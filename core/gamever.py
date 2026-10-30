"""游戏版本：读出玩家在主菜单里看到的那个版本号（如 `5.01c`）。

为什么不读文件版本
------------------
`bin/x64_dx12/witcher3.exe` 的 PE 文件版本是 `5.0.16.41566`，和主菜单角标上的
`5.01c` 对不上 —— 玩家看到的是后者。而游戏把这份字符串**直接内嵌在 exe 里**
（多处，形如 `v 5.01c\\0`），所以只能去文件里找。

代价
----
exe 有 90 MB，整读一遍约 20~50 ms（文件在系统缓存里时更快）。窗口很短，
用不上就白花这点时间，所以按 (路径, 修改时间, 大小) 缓存 —— 游戏不更新就一直命中。

读不到怎么办
------------
回退到 PE 文件版本（`5.0.16.41566`）。格式和主菜单不一样，但足够看出游戏
打过补丁、也能和 mod 声称的 gameVersion 对账。两者都读不到就返回空。
"""
from __future__ import annotations

import ctypes
import re
import sys
from pathlib import Path

# exe 里那份字面量：`v 5.01c\0`。前面那个 "v " 是关键 —— 单靠 `5.01c`
# 会匹配到别的数字串（内存地址、版本字段碎片）。
_PAT = re.compile(rb"v (\d+\.\d+[0-9]{0,2}[a-z]?)\x00")

_CHUNK = 4 << 20
_OVERLAP = 64                     # 防止匹配跨在两块边界上

# {exe 路径: (mtime, size, 结果)}
_cache: dict[str, tuple[float, int, dict]] = {}

_EXE_NAMES = (
    "bin/x64_dx12/witcher3.exe",   # 重制版只有这个（DX12-only 构建）
    "bin/x64/witcher3.exe",        # 4.x 时代的路径，留着不吃亏
)


def exe_path(root) -> Path | None:
    """游戏根目录下真正在用的那个 exe。"""
    if not root:
        return None
    base = Path(root)
    for rel in _EXE_NAMES:
        p = base / rel
        if p.is_file():
            return p
    return None


def _scan_exe(p: Path) -> str:
    """在 exe 里找 `v X.YZ<字母>`。找不到返回空串。"""
    try:
        with p.open("rb") as f:
            tail = b""
            while True:
                chunk = f.read(_CHUNK)
                if not chunk:
                    return ""
                buf = tail + chunk
                m = _PAT.search(buf)
                if m:
                    return m.group(1).decode("ascii", "ignore")
                tail = buf[-_OVERLAP:]
    except OSError:
        return ""


def _pe_version(p: Path) -> str:
    """PE 资源里的文件版本，形如 5.0.16.41566。读不到返回空串。"""
    if not sys.platform.startswith("win"):
        return ""
    try:
        from ctypes import (POINTER, Structure, WinDLL, byref, c_void_p,
                            create_string_buffer, wintypes)

        class _FixedFileInfo(Structure):
            _fields_ = [
                ("dwSignature", wintypes.DWORD), ("dwStrucVersion", wintypes.DWORD),
                ("dwFileVersionMS", wintypes.DWORD), ("dwFileVersionLS", wintypes.DWORD),
                ("dwProductVersionMS", wintypes.DWORD), ("dwProductVersionLS", wintypes.DWORD),
                ("dwFileFlagsMask", wintypes.DWORD), ("dwFileFlags", wintypes.DWORD),
                ("dwFileOS", wintypes.DWORD), ("dwFileType", wintypes.DWORD),
                ("dwFileSubtype", wintypes.DWORD), ("dwFileDateMS", wintypes.DWORD),
                ("dwFileDateLS", wintypes.DWORD),
            ]

        ver = WinDLL("version")
        size = ver.GetFileVersionInfoSizeW(str(p), None)
        if not size:
            return ""
        buf = create_string_buffer(size)
        if not ver.GetFileVersionInfoW(str(p), 0, size, buf):
            return ""
        ptr = c_void_p()
        n = wintypes.UINT()
        if not ver.VerQueryValueW(buf, "\\", byref(ptr), byref(n)):
            return ""
        fi = ctypes.cast(ptr, POINTER(_FixedFileInfo)).contents
        ms, ls = fi.dwFileVersionMS, fi.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:                                # noqa: BLE001 读不到就当没有
        return ""


def detect(root) -> dict:
    """返回 {"version": "5.01c", "src": "exe"|"pe"|"", "exe": 路径}。"""
    p = exe_path(root)
    if p is None:
        return {"version": "", "src": "", "exe": ""}
    try:
        st = p.stat()
        key = str(p).lower()
        hit = _cache.get(key)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
    except OSError:
        return {"version": "", "src": "", "exe": str(p)}

    text, src = _scan_exe(p), "exe"
    if not text:
        text, src = _pe_version(p), "pe"
    out = {"version": text, "src": src if text else "", "exe": str(p)}
    _cache[str(p).lower()] = (st.st_mtime, st.st_size, out)
    return out
