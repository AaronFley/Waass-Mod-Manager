"""下载目录管理：让用户自己指定「我平时往哪儿存 mod 包」。

设计要点：
- 默认带上系统真实的下载目录（从注册表读，用户可能把它挪到 D 盘），
  另外附带几个常见的下载路径，能读到就列出来。
- 用户可以在设置里添加任意目录，也可以把默认的关掉。
- 扫描只往下走两层，避免有人在下载目录里放整个游戏备份时把程序卡死。
- 全程只读：这里不删、不改、不移动用户的任何文件。
"""
from __future__ import annotations

import os
import time
import winreg
from pathlib import Path

from . import i18n
from .paths import load_config, save_config
from . import ui as _ui

ENV_MORE = "WAASS_EXTRA_ARCHIVE_DIRS"

# 注册表里的「下载」文件夹 GUID
_DOWNLOADS_GUID = "{374DE290-123F-4565-9164-39C4925E467B}"

# 常见的下载落点，只作候补
_CANDIDATES = (
    "D:/download",
    "D:/downloads",
    "D:/Downloads",
    "C:/download",
    "C:/Downloads",
    "E:/download",
    "E:/Downloads",
)

ARCHIVE_EXT = {".zip", ".7z", ".rar"}

# 扫到这么多个包就停手，防止目录里堆着几千个文件
HARD_LIMIT = 400


def _reg_downloads() -> Path | None:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            val, _ = winreg.QueryValueEx(k, _DOWNLOADS_GUID)
            p = Path(os.path.expandvars(val))
            if p.is_dir():
                return p
    except OSError:
        pass
    return None


def default_downloads() -> Path | None:
    """系统的下载目录。"""
    p = _reg_downloads()
    if p is not None:
        return p
    cand = Path.home() / "Downloads"
    return cand if cand.is_dir() else None


def _norm(p: str | Path) -> Path:
    try:
        return Path(str(p)).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(str(p))


def _cfg_dirs() -> list:
    cfg = load_config()
    v = cfg.get("archive_dirs")
    return v if isinstance(v, list) else []


def _cfg_disabled() -> set[str]:
    cfg = load_config()
    v = cfg.get("archive_dirs_off") or []
    return {str(_norm(x)).lower() for x in v if x}


def dirs() -> list[dict]:
    """当前生效的扫描目录，带来源标注。"""
    out: list[dict] = []
    seen: set[str] = set()

    def add(p: Path, kind: str) -> None:
        key = str(p).lower()
        if key in seen:
            return
        seen.add(key)
        out.append({
            "path": str(p),
            "name": p.name or str(p),
            "kind": kind,                 # system / custom / suggested
            "exists": p.is_dir(),
        })

    off = _cfg_disabled()
    d = default_downloads()
    if d is not None and str(d).lower() not in off:
        add(d, "system")

    for cand in _CANDIDATES:
        p = _norm(cand)
        if p.is_dir() and str(p).lower() not in off:
            add(p, "suggested")

    for raw in _cfg_dirs():
        try:
            add(_norm(raw), "custom")
        except (OSError, ValueError):
            continue

    for raw in (os.environ.get(ENV_MORE) or "").split(os.pathsep):
        if raw.strip():
            p = _norm(raw.strip())
            if p.is_dir():
                add(p, "custom")

    return out


def add_dir(path: str | Path) -> dict:
    p = _norm(path)
    if not p.is_dir():
        raise FileNotFoundError(i18n.t("dl.dir_missing", path=p))
    cfg = load_config()
    lst = [str(_norm(x)) for x in _cfg_dirs()]
    if str(p) not in lst:
        lst.append(str(p))
    cfg["archive_dirs"] = lst
    # 如果之前被关掉过，加回来时一并解除
    off = [x for x in (cfg.get("archive_dirs_off") or [])
           if str(_norm(x)).lower() != str(p).lower()]
    cfg["archive_dirs_off"] = off
    save_config(cfg)
    return {"ok": True, "dirs": dirs()}


def remove_dir(path: str | Path) -> dict:
    """移除一个目录。系统的下载目录是「关掉」而不是删除，随时可恢复。"""
    p = _norm(path)
    cfg = load_config()
    lst = []
    removed_custom = False
    for x in _cfg_dirs():
        if str(_norm(x)).lower() == str(p).lower():
            removed_custom = True
        else:
            lst.append(str(_norm(x)))
    cfg["archive_dirs"] = lst

    d = default_downloads()
    is_system = d is not None and str(d).lower() == str(p).lower()
    off = [str(_norm(x)) for x in (cfg.get("archive_dirs_off") or [])]
    if not removed_custom and not is_system:
        # 常见候选目录：也记进"不要再列"
        off.append(str(p))
    if off:
        cfg["archive_dirs_off"] = list(dict.fromkeys(off))
    save_config(cfg)
    return {"ok": True, "dirs": dirs(), "system": is_system}


def reset_dirs() -> dict:
    cfg = load_config()
    cfg.pop("archive_dirs", None)
    cfg.pop("archive_dirs_off", None)
    save_config(cfg)
    return {"ok": True, "dirs": dirs()}


def _walk(root: Path, depth: int = 2) -> list[Path]:
    """浅层遍历：根目录一层 + 一层子目录。mod 包一般就放在这两层里。"""
    found: list[Path] = []
    try:
        entries = list(os.scandir(root))
    except OSError:
        return found
    for e in entries:
        try:
            if e.is_file(follow_symlinks=False):
                if Path(e.name).suffix.lower() in ARCHIVE_EXT:
                    found.append(Path(e.path))
            elif depth > 1 and e.is_dir(follow_symlinks=False):
                if e.name.startswith((".", "$", "System", "Windows")):
                    continue
                found.extend(_walk(Path(e.path), depth - 1))
        except OSError:
            continue
        if len(found) >= HARD_LIMIT:
            break
    return found


def scan(limit: int = 240) -> list[dict]:
    """扫所有目录里的压缩包。只返回元信息，不读内容。"""
    from . import registry

    pkgs = registry.load()["packages"]
    known_files = {Path(p.get("archive", "")).name for p in pkgs if p.get("archive")}
    known_ids = {(p.get("nexus_id"), p.get("version")) for p in pkgs
                 if p.get("nexus_id")}

    out: list[dict] = []
    seen: set[str] = set()
    for d in dirs():
        root = Path(d["path"])
        if not root.is_dir():
            continue
        for p in _walk(root):
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            try:
                st = p.stat()
            except OSError:
                continue
            info = registry.parse_archive_name(p.name)
            installed = (p.name in known_files
                         or (info["nexus_id"] and (info["nexus_id"], info["version"]) in known_ids))
            out.append({
                "path": str(p),
                "file": p.name,
                "dir": str(root),
                "dir_name": d["name"],
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "name": info["name"],
                "nexus_id": info["nexus_id"],
                "version": info["version"],
                "known": installed,
            })

    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def new_count(days: int = 7) -> int:
    """最近几天下的、还没装过的包有几个。

    只看最近几天，否则一个常年不清空的下载目录会让提示一直亮着，变成噪音。
    """
    cutoff = time.time() - days * 86400
    try:
        return sum(1 for it in scan(limit=HARD_LIMIT)
                   if not it["known"] and it["mtime"] >= cutoff)
    except Exception:            # 扫描失败不该影响主界面
        return 0


def summary() -> dict:
    ds = dirs()
    counts = {}
    fresh = 0                  # 7 天内、还没装过的：这个数字用于界面播报
    uninstalled: list[str] = []  # 全部还没装过的：这个集合用于判「是不是同一批」
    cutoff = time.time() - 7 * 86400
    try:
        items = scan(limit=HARD_LIMIT)
    except Exception:
        items = []
    for it in items:
        counts[it["dir"]] = counts.get(it["dir"], 0) + 1
        if not it["known"]:
            uninstalled.append(it["path"])
            if it["mtime"] >= cutoff:
                fresh += 1
    for d in ds:
        d["archives"] = counts.get(d["path"], 0)
    return {
        "dirs": ds,
        "total": len(items),
        "new": fresh,
        # 指纹只看"还没装过的包有哪些"，**故意不带修改时间、也不带 7 天窗口**。
        # 带上 mtime 或时间窗口的话，包一旦满 7 天就会被踢出集合，指纹随之变化，
        # 用户明明忽略过的提示会自己重新亮起来（数量还少了一个），非常费解。
        # 现在只有"真的多了一个包/少了一个包"才变。
        "new_sig": _ui.digest(uninstalled) if uninstalled else "",
        "scanned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
