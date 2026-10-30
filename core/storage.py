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

from . import guard
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
GROUPS = [
    {
        "id": "webview",
        "title": "界面窗口的缓存",
        "desc": "开界面用的浏览器内核存下的临时文件。删掉没有任何影响，"
                "下次启动会自己重建（界面偏好会重置一次）。",
        "paths": ["webview", "webview-profile"],
        "risk": "safe",
    },
    {
        "id": "dead",
        "title": "不再使用的旧文件",
        "desc": "早期版本留下的、现在完全不读的缓存和日志。留着只是占地方。",
        "paths": ["nexus_cache.json", "updates.json", "server.log",
                  "nul", "crash.log"],
        "risk": "safe",
    },
    {
        "id": "thumbs",
        "title": "模组封面图",
        "desc": "卡片上显示的封面。删掉不影响任何功能，只是卡片会变成纯色底；"
                "有 Nexus ID 的会在下次打开时自动重新取，没有 ID 的可以在设置里"
                "重新导入一次旧资料。",
        "paths": ["thumbs"],
        "risk": "caution",
    },
    {
        "id": "nexus",
        "title": "从 Nexus 查到的资料",
        "desc": "作者、简介、最新版本号的缓存（每个 mod 一个小文件）。"
                "删掉不影响任何功能，下次打开会重新查一次（只查有 ID 的）。",
        "paths": ["nexus"],
        "risk": "safe",
    },
    {
        "id": "jobs",
        "title": "汉化中间文件（含旧版中文名数据）",
        "desc": "导出给 AI 的待翻译清单、导回来的结果。做完的可以清，"
                "不影响已经写进游戏或已保存的译文。中文名那套功能 1.2.5 已下线，"
                "names.json / names/ 里的旧数据也可以一起清掉。",
        "paths": ["translate", "names", "names.json"],
        "risk": "caution",
    },
    {
        "id": "snapshots",
        "title": "写操作快照",
        "desc": "每次改 mods.settings 或菜单文件前，把原文件存一份。"
                "这是「撤销」的依据，清掉之后就撤不回更早的操作了。"
                "程序会自动只保留最近 30 份。",
        "paths": ["snapshots"],
        "risk": "caution",
    },
    {
        "id": "localize_backups",
        "title": "汉化中间文件（旧版）",
        "desc": "1.1 及更早版本导出待翻译清单时留下的备份。新版的原件备份不在"
                "这里，已经归到「汉化原件」里保护起来了，清这一项不会影响还原。",
        "paths": ["localize_backups"],
        "risk": "caution",
    },
    {
        "id": "quarantine",
        "title": "隔离区（卸载的模组）",
        "desc": "卸载 mod 时没有真删，而是搬到这里，方便反悔。确认不再需要就能永久删除。"
                "删掉以后这些 mod 的文件就彻底没了。",
        "paths": ["quarantine"],
        "risk": "danger",
    },
    {
        "id": "archives",
        "title": "旧版留下的原包副本",
        "desc": "1.2.4 之前的「安装后复制一份原包」存下来的副本。这个功能已经去掉，"
                "这里只用来清理以前留下的文件 —— 删掉不影响已装好的 mod。",
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
        "id": "other", "title": "其他文件",
        "desc": "数据目录里不属于上面任何一类的东西。程序不会动它们，"
                "如果认不出来源，可以直接去数据目录看一眼。",
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
            "id": g["id"], "title": g["title"], "desc": g["desc"],
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
            rows.append({"id": g["id"], "title": g["title"], "bytes": got})
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
