"""存储占用统计与清理。

原则
----
1. **能再生的才敢批量删**。封面、浏览器缓存、废弃文件 —— 删了不影响使用。
2. **安全网只提醒、不替你删**。快照、隔离区、保留的原包 —— 删了就找不回来，
   所以必须你亲手点，并且写清楚后果。
3. **核心数据永远不进清理列表**。配置、追踪台账、中文名、分组、安装档案 ——
   这些是本程序的大脑，任何一键清理都不会碰。
4. 删除范围硬编码在下面，不接受外部传路径。传进来的只能是一个名单 id。

另外顺手做两件"别让垃圾长大"的事：
    * 浏览器内核的磁盘缓存限制到 16 MB（见 run.py 的启动参数）；
    * 快照目录只留最近 30 份，写操作时自动清更早的（见 safety.py）。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import guard, i18n
from .paths import DATA_DIR

# 这些是本程序的大脑，永远不删，只在界面上显示体积
CORE_FILES = [
    "config.json", "track.json", "names.json", "library.json",
    "installed.json", "legacy.json", "integrity.json", "journal.json",
    # 哪个 mod 改过哪个游戏配置的哪些键。删了卸载就只会傻删整份文件。
    "config_patches.json", "ui.json",
]


def _size(path: Path) -> tuple[int, int]:
    """(字节, 文件数)。一个不存在的路径算 0。"""
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    if not path.is_dir():
        return 0, 0
    total = count = 0
    stack = [str(path)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            stack.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            count += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, count


# ---------------------------------------------------------------- 分类定义
# risk: safe   = 删了立刻能再生，无感
#       caution= 删了少点方便（封面要重新搬、快照没了不能撤销）
#       danger = 删了就永久没了（隔离区里的 mod、保留的原包）
# title/desc 存的是 i18n 键（stor.*，见 _work/i18n/backend-a.json），
# 在 report()/clean() 出口处用 i18n.t() 落成当前语言的文案。
GROUPS = [
    {
        "id": "webview",
        "title": "stor.webview_title",
        "desc": "stor.webview_desc",
        "paths": ["webview", "webview-profile"],
        "risk": "safe",
    },
    {
        "id": "dead",
        "title": "stor.dead_title",
        "desc": "stor.dead_desc",
        "paths": ["nexus_cache.json", "updates.json", "server.log",
                  "nul", "crash.log"],
        "risk": "safe",
    },
    {
        "id": "thumbs",
        "title": "stor.thumbs_title",
        "desc": "stor.thumbs_desc",
        "paths": ["thumbs"],
        "risk": "caution",
    },
    {
        "id": "nexus",
        "title": "stor.nexus_title",
        "desc": "stor.nexus_desc",
        "paths": ["nexus"],
        "risk": "safe",
    },
    {
        "id": "jobs",
        "title": "stor.jobs_title",
        "desc": "stor.jobs_desc",
        "paths": ["translate", "names", "names.json"],
        "risk": "caution",
    },
    {
        "id": "snapshots",
        "title": "stor.snapshots_title",
        "desc": "stor.snapshots_desc",
        "paths": ["snapshots"],
        "risk": "caution",
    },
    {
        "id": "localize_backups",
        "title": "stor.localize_backups_title",
        "desc": "stor.localize_backups_desc",
        "paths": ["localize_backups"],
        "risk": "caution",
    },
    {
        "id": "quarantine",
        "title": "stor.quarantine_title",
        "desc": "stor.quarantine_desc",
        "paths": ["quarantine"],
        "risk": "danger",
    },
    {
        "id": "archives",
        "title": "stor.archives_title",
        "desc": "stor.archives_desc",
        "paths": ["archives"],
        "risk": "danger",
    },
]

_BY_ID = {g["id"]: g for g in GROUPS}

# 核心数据（只统计、不清理）
# baseline/ 是「第一次改游戏配置之前的原样」，删了就没法把配置整份还原回去，
# 所以和存档一样只统计、不进清理清单。
#
# localize/ 同理，而且更重要：里面的 orig/ 是**汉化前的原件**，是「一键还原成英文」
# 唯一的数据源。它和快照不一样 —— 快照只留最近 30 份会自动淘汰，原件一旦写进去
# 就永不覆盖、永不清理。所以这个目录绝不出现在清理清单里，只报体积。
# （history/ 会被 localize_backup 自己按批次轮转，不需要用户动手。）
KEEP_DIRS = ["saves", "baseline", "localize"]


def _abs(rel: str) -> Path:
    return (DATA_DIR / rel) if rel else DATA_DIR


def _files_in(p: Path, limit: int = 40) -> list[str]:
    """给界面看的明细（最多 40 条）。"""
    if not p.is_dir():
        return []
    out = []
    try:
        for e in sorted(os.scandir(p), key=lambda x: x.name.lower()):
            if e.is_file(follow_symlinks=False):
                out.append(e.name)
            if len(out) >= limit:
                break
    except OSError:
        pass
    return out


def _unaccounted(groups: list[dict], core_bytes: int) -> dict:
    """data 里不属于任何一类的占用。只统计、列名字，不提供删除按钮。"""
    known = set(CORE_FILES) | set(KEEP_DIRS)
    for g in GROUPS:
        known.update(g["paths"])
    names: list[str] = []
    b = f = 0
    try:
        entries = list(os.scandir(DATA_DIR))
    except OSError:
        entries = []
    for e in entries:
        if e.name in known:
            continue
        db, df = _size(Path(e.path))
        if not db:
            continue
        b += db
        f += df
        names.append(e.name)
    return {
        "id": "other", "title": i18n.t("stor.other_title"),
        "desc": i18n.t("stor.other_desc"),
        "risk": "caution", "bytes": b, "files": f,
        "detail": names[:8], "paths": [], "actionable": False,
    }


def report() -> dict:
    """当前占用。只读，不删任何东西。"""
    groups = []
    for g in GROUPS:
        b = f = 0
        detail: list[str] = []
        found = False
        for rel in g["paths"]:
            p = _abs(rel)
            db, df = _size(p)
            if db or df:
                found = True
                b += db
                f += df
                if p.is_dir():
                    detail += _files_in(p)
        if not found:
            continue
        groups.append({
            "id": g["id"],
            "title": i18n.t(g["title"]), "desc": i18n.t(g["desc"]),
            "risk": g["risk"], "bytes": b, "files": f,
            "detail": detail[:8], "actionable": True,
            "paths": [str(_abs(r)) for r in g["paths"] if _abs(r).exists()],
        })

    core = []
    core_bytes = 0
    for name in CORE_FILES:
        p = DATA_DIR / name
        b, f = _size(p)
        if b:
            core_bytes += b
            core.append({"name": name, "bytes": b})
    for d in KEEP_DIRS:
        b, f = _size(DATA_DIR / d)
        if b:
            core_bytes += b
            core.append({"name": d + "/", "bytes": b})

    total_bytes, total_files = _size(DATA_DIR)
    safe_bytes = sum(x["bytes"] for x in groups if x["risk"] == "safe")

    # 剩下没被归类的，如实列出来 —— 万一以后 data 里多了奇怪的东西，
    # 至少不用对着「总占用」和「明细」对不上账。
    other = _unaccounted(groups, core_bytes)
    if other["bytes"] > 100_000:
        groups.append(other)

    return {
        "data_dir": str(DATA_DIR),
        "groups": groups,
        "core": core,
        "core_bytes": core_bytes,
        "total_bytes": total_bytes,
        "total_files": total_files,
        "safe_bytes": safe_bytes,
        "reclaim": sum(x["bytes"] for x in groups),
    }


def _inside_data(p: Path) -> bool:
    """只能删 data 目录里面的东西，且不能是 data 本身。"""
    try:
        rp = p.resolve()
    except OSError:
        return False
    try:
        rp.relative_to(DATA_DIR.resolve())
    except (ValueError, OSError):
        return False
    return rp != DATA_DIR.resolve()


def _remove(p: Path) -> int:
    """删一个文件或目录，返回释放的字节数。失败不抛，返回 0。"""
    if not _inside_data(p):
        return 0
    before, _ = _size(p)
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.is_file():
            p.unlink()
    except OSError:
        return 0
    return before if not p.exists() else 0


def clean(ids: list[str] | None = None, only_safe: bool = False) -> dict:
    """按名单清理。only_safe=True 时只处理 risk=safe 的那几类。

    返回每一类释放了多少，以及总数。任何异常都不会让整个清理失败。
    """
    guard.allow(DATA_DIR)                    # 测试模式下这会强制要求沙盒
    want = set(ids or [])
    freed = 0
    rows = []
    for g in GROUPS:
        if only_safe and g["risk"] != "safe":
            continue
        if want and g["id"] not in want:
            continue
        got = 0
        for rel in g["paths"]:
            p = _abs(rel)
            if not p.exists():
                continue
            try:
                guard.assert_can_write(p, "清理缓存")
            except guard.WriteDenied:
                continue
            got += _remove(p)
        if got:
            rows.append({"id": g["id"], "title": i18n.t(g["title"]), "bytes": got})
            freed += got
    return {"ok": True, "freed": freed, "rows": rows}


# 界面窗口关掉之后就没有保留价值的那些（保留 Local Storage / Preferences /
# 登录态之类，这样你的主题、上次看的视图不会每次重置）
BROWSER_TRIM = [
    "webview/BrowserMetrics",
    "webview/Crashpad",
    "webview/GPUPersistentCache",
    "webview/SmartScreen",
    "webview/Safe Browsing",
    "webview/Nurturing",
    "webview/Default/Cache",
    "webview/Default/Code Cache",
    "webview/Default/GPUCache",
    "webview/Default/DawnGraphiteCache",
    "webview/Default/DawnWebGPUCache",
    "webview/Default/Service Worker",
    "webview/Default/Session Storage",
    "webview/Default/blob_storage",
    "webview-profile",
]


def trim_browser_cache() -> int:
    """清掉界面窗口的临时缓存。启动前和退出时各调一次。

    浏览器内核每开一次都会写几 MB 指标和缓存文件（实测一次 17 MB，其中
    BrowserMetrics 一项就 8 MB）。这些下次启动全部会重建，没有留的价值。
    删的时候窗口已经关了或者还没开，不会影响正在用的页面。
    """
    freed = 0
    try:
        guard.allow(DATA_DIR)
    except guard.WriteDenied:
        return 0
    for rel in BROWSER_TRIM:
        p = DATA_DIR / rel
        if not p.exists():
            continue
        try:
            guard.assert_can_write(p, "清理窗口缓存")
        except guard.WriteDenied:
            continue
        freed += _remove(p)
    return freed


def prune_snapshots(keep: int = 30) -> int:
    """快照只留最近 N 份。写操作前调用，返回删了几个目录。

    快照目录名是时间戳（2026-10-25T19-30-00），按名字排序即按时间排序。
    """
    snap = DATA_DIR / "snapshots"
    if not snap.is_dir():
        return 0
    try:
        dirs = sorted([e for e in os.scandir(snap) if e.is_dir(follow_symlinks=False)],
                      key=lambda x: x.name)
    except OSError:
        return 0
    extra = dirs[:-keep] if keep > 0 and len(dirs) > keep else []
    removed = 0
    for e in extra:
        try:
            shutil.rmtree(e.path, ignore_errors=True)
            removed += 1
        except OSError:
            continue
    return removed


def prune_backups(keep: int = 20) -> int:
    """汉化备份只留最近 N 个文件。"""
    d = DATA_DIR / "localize_backups"
    if not d.is_dir():
        return 0
    try:
        files = sorted([e for e in os.scandir(d) if e.is_file(follow_symlinks=False)],
                       key=lambda x: x.stat().st_mtime)
    except OSError:
        return 0
    removed = 0
    for e in (files[:-keep] if keep > 0 and len(files) > keep else []):
        try:
            Path(e.path).unlink()
            removed += 1
        except OSError:
            continue
    return removed
