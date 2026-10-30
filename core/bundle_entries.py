"""POTATO70 资源包（.bundle）里的文件清单 —— 只读名称表，不解压、不写盘。

为什么要读这个
--------------
巫师3 的界面 mod 各自带一个 `content/blob0.bundle`。**包文件名全都一样**，
所以按文件比永远「全撞」，没有参考价值；真正打架的是包**内部**的资源路径，
比如两个 mod 都提供 `gameplay\\gui_new\\swf\\inventory\\panel_inventory.redswf`，
游戏只会加载其中一份，另一份的界面改动整体消失。

2026-10-28 的实例：UI Remastered(66MB) / MoreSkillSlots(17MB) / Hoods(2.3MB) /
HoodSlots(1MB) 四份 blob0.bundle，脚本零重叠、却因为抢同一个面板文件，
HoodSlots 的三个槽位框整片不见。

格式（实测，见 witcher3/mods/panel-and-bundle-notes.md §3）
----------------------------------------------------------
```
0x00  "POTATO70"
0x08  u32 条目数 / u32 未知 / u32 名称表字节数
0x14  u16 版本 / u16 名称表起始（通常 0x20）
0x20  名称表，每条固定 304 字节：
        272 字节 = 256 名称槽（NUL 结尾，cp1252）+ 16 字节名称哈希
         32 字节 = offset u64 / 原始大小 / 磁盘大小 / CRC32 / 压缩字 / 保留×2
```

**只读前 0x20 + 名称表**，不碰数据区。66MB 的包实际只读几百 KB。

压缩字（0 原样 / 1 zlib / 5 LZ4 块）在这里完全不关心 —— 那要解压才用得上，
我们只要名字。这也是为什么本模块不需要 LZ4 实现。

只读承诺
--------
- 只以 `rb` 打开，只 `seek/read`，从不写。
- 名称表字节数有上限（`MAX_TABLE`），损坏文件不会让程序去读几个 G。
- 任何异常都返回空结果，不抛给界面。
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

MAGIC = b"POTATO70"
REC = 304                 # 每条记录的固定长度
NAME_SLOT = 272           # 名称槽 + 哈希（真正要读的部分）
HEADER = 0x20             # 名称表起始偏移（版本较老的包也在这里）

MAX_TABLE = 64 << 20      # 名称表最多读 64MB，正常包远小于此
MAX_ENTRIES = 60000       # 单个包最多收这么多条目

# (路径, mtime, 大小) → 名称元组
_cache: dict[tuple, tuple[str, ...]] = {}
_cache_max = 400


def read_names(path: Path | str) -> tuple[str, ...]:
    """列出一个 bundle 内部的资源路径（小写、反斜杠分隔）。

    读不到就返回空元组 —— 这不是错误，可能只是包损坏或者是别的格式。
    """
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return ()
    key = (str(p).lower(), int(st.st_mtime), st.st_size)
    hit = _cache.get(key)
    if hit is not None:
        return hit

    names: tuple[str, ...] = ()
    try:
        names = _read(p, st.st_size)
    except (OSError, struct.error, ValueError):
        names = ()

    if len(_cache) >= _cache_max:
        _cache.clear()
    _cache[key] = names
    return names


def _read(p: Path, size: int) -> tuple[str, ...]:
    if size < HEADER + REC:
        return ()
    with open(p, "rb") as f:
        head = f.read(HEADER)
        if head[:8] != MAGIC:
            return ()
        names_size = struct.unpack_from("<I", head, 0x10)[0]
        # 名称表不可能比文件本身还大；也不可能超过上限
        if names_size < REC or names_size > MAX_TABLE or names_size > size:
            return ()
        f.seek(HEADER)
        table = f.read(names_size)

    out: list[str] = []
    seen: set[str] = set()
    n = min(len(table) // REC, MAX_ENTRIES)
    for i in range(n):
        base = i * REC
        slot = table[base:base + NAME_SLOT]
        end = slot.find(b"\x00")
        if end <= 0:
            continue
        try:
            name = slot[:end].decode("cp1252")
        except UnicodeDecodeError:
            name = slot[:end].decode("cp1252", "replace")
        name = name.replace("/", "\\").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def scan_mod(mod_dir: Path | str, limit_files: int = 24) -> dict[str, tuple[str, ...]]:
    """一个 mod 里所有 bundle 的条目。返回 {包内相对路径: 条目}。"""
    d = Path(mod_dir)
    out: dict[str, tuple[str, ...]] = {}
    content = d / "content"
    if not content.is_dir():
        return out
    n = 0
    for cur, _dirs, files in os.walk(content):
        for f in files:
            if not f.lower().endswith(".bundle"):
                continue
            full = Path(cur) / f
            try:
                rel = str(full.relative_to(d)).replace("\\", "/")
            except ValueError:
                continue
            out[rel] = read_names(full)
            n += 1
            if n >= limit_files:
                return out
    return out


def classify(name: str) -> str:
    """把资源路径归成三类，用来判断「这一撞到底多要紧」。

    ui    —— 界面。抢了就是某一方的界面改动整片消失，最值得说。
    data  —— 物品/任务/技能定义。抢了会让某一方的数值改动失效。
    asset —— 贴图、模型、声音。材质类 mod 之间撞是常态，不主动报。
    """
    low = name.lower()
    if low.endswith((".redswf", ".swf", ".gfx", ".redflash")):
        return "ui"
    if low.endswith((".xml", ".reddlc", ".w2quest", ".w2phase")) or "\\definitions\\" in low:
        return "data"
    return "asset"


def drop_cache() -> None:
    _cache.clear()
