"""别的管理器留下的资料，搬进本程序自己的存档（data/legacy.json）。

这一层现在的角色
----------------
不是「导入功能」，而是**一次性的数据种子**：第一次扫描时如果本机有旧管理器的
存档，就把 Nexus ID / 作者 / 分类 / 封面复制一份进来；搬完之后这层只是读
data/legacy.json 给扫描器用。

1.0.10 起界面上不再有「旧资料存档」入口 —— 已经搬完的东西没有理由再「导入一次」，
而重复导入只会把用户后来手工追认的 ID 覆盖回旧值。想重新搬就删 legacy.json。

搬什么（都是静态资料，不会过期）
    * Nexus ID / 作者 / Nexus 分类
    * 显示名、它记录的 DLC 与菜单文件清单
    * 封面图（复制到 data/thumbs/）
    * 它最后一次看到的「线上版本」（会标上时间，明确是旧数据）

不搬什么
    * 启用状态、加载顺序 —— 这两个以磁盘上的 mods.settings 为唯一事实
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import rmmr_import
from .paths import DATA_DIR

FILE = DATA_DIR / "legacy.json"
VERSION = 1

# 只搬这些字段，别把别的管理器的内部结构整包抄进来
_FIELDS = {
    "nexus_mod_id": "nexus_id",
    "author": "author",
    "category": "category",
    "installed_version": "installed_version",
    "remote_version": "remote_version",
    "last_checked": "last_checked",
}


def _empty() -> dict:
    return {"version": VERSION, "migrated_at": "", "source": "",
            "source_dir": "", "count": 0, "by_folder": {}, "by_name": {}}


def load() -> dict:
    if not FILE.is_file():
        return _empty()
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    out = _empty()
    out.update({k: v for k, v in data.items() if k in out})
    out["by_folder"] = data.get("by_folder") if isinstance(data.get("by_folder"), dict) else {}
    out["by_name"] = data.get("by_name") if isinstance(data.get("by_name"), dict) else {}
    return out


def save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(FILE)


def index() -> dict:
    """扫描器要的东西：{by_folder, by_name}。已经开始搬过就直接给存档。"""
    return load()


def ensure_migrated(game_root: Path | None, force: bool = False) -> dict:
    """没有存档时从旧管理器搬一次。只读它，不动它。

    `force` 只给内部/测试用 —— 界面上已经没有「重新导入」按钮了（1.0.10）。
    """
    data = load()
    if data["migrated_at"] and not force:
        return data
    if game_root is None:
        return data

    rmmr = rmmr_import.find_rmmr(Path(game_root))
    if rmmr is None:
        return data                       # 没装过就不写标记，以后装回来还能自动搬

    by_folder: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    meta = rmmr_import.load_metadata(rmmr)
    thumbs = rmmr_import.copy_thumbnails(rmmr, meta, DATA_DIR / "thumbs")
    installed = rmmr_import.load_installed(rmmr)

    for folder, entry in installed.items():
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        info = meta.get(name) or {}
        rec: dict = {"name": name}
        for src, dst in _FIELDS.items():
            val = info.get(src)
            if val not in (None, ""):
                rec[dst] = str(val).strip()
        rec["nexus_id"] = rec.get("nexus_id", "").strip()
        if thumbs.get(name):
            rec["thumbnail"] = thumbs[name]
        if entry.get("dlc"):
            rec["dlc"] = list(entry["dlc"])
        if entry.get("menu"):
            rec["menu"] = [Path(p).name for p in entry["menu"]]
        by_folder[folder.lower()] = rec
        if name:
            by_name[name.strip().lower()] = {
                k: rec[k] for k in
                ("nexus_id", "author", "category", "installed_version",
                 "remote_version", "last_checked", "thumbnail") if rec.get(k)
            }

    data = {
        "version": VERSION,
        "migrated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": "RMMR",
        "source_dir": str(rmmr),
        "count": len(by_folder),
        "by_folder": by_folder,
        "by_name": by_name,
    }
    save(data)
    return data


def summary() -> dict:
    """给内部诊断用（设置界面 1.0.10 起不再展示）。"""
    data = load()
    return {
        "done": bool(data["migrated_at"]),
        "at": (data["migrated_at"] or "")[:19].replace("T", " "),
        "source": data["source"],
        "source_dir": data["source_dir"],
        "count": int(data["count"] or len(data["by_folder"])),
        "file": str(FILE),
    }
