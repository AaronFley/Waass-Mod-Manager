"""从 Relapse's Mod Manager Redux (RMMR) 的存档里吸收已有资料。

动机：你已经在 RMMR 里维护了 19 个 mod 的 Nexus ID、作者、分类、缩略图。
重装一遍太浪费，所以首次扫描时直接读它的记录（只读，不改它），
把 Nexus ID 和封面图搬过来。之后就以本程序自己的数据为准。
"""
from __future__ import annotations

import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

RMMR_DIRNAME = "RMMR Mod Manager"


def find_rmmr(game_root: Path) -> Path | None:
    """在游戏目录及其同级找 RMMR 的安装目录。"""
    cands = [game_root / RMMR_DIRNAME]
    parent = game_root.parent
    if parent.is_dir():
        cands.append(parent / RMMR_DIRNAME)
    for c in cands:
        if (c / "installed.xml").is_file():
            return c
    return None


def _truthy(v: str | None) -> bool:
    return str(v).strip().lower() in ("true", "1", "yes")


def load_installed(rmmr: Path) -> dict[str, dict]:
    """node 索引：{内部文件夹名(小写): {name, dlc[], menu[], bin[]}}"""
    out: dict[str, dict] = {}
    f = rmmr / "installed.xml"
    if not f.is_file():
        return out
    try:
        tree = ET.parse(f)
    except ET.ParseError:
        return out

    for mod in tree.getroot().iter("mod"):
        name = mod.get("name") or ""
        entry = {
            "name": name,
            "enabled": _truthy(mod.get("enabled")),
            "priority": mod.get("priority") if mod.get("priority") not in (None, "Not Set") else None,
            "data": [], "dlc": [], "menu": [], "bin": [], "ini": [],
        }
        for child in mod:
            tag = child.tag
            if tag in ("data", "dlc"):
                if child.text and child.text.strip():
                    entry[tag].append(child.text.strip())
            elif tag == "menu":
                p = child.get("path")
                if p:
                    entry["menu"].append(p)
            elif tag == "bin":
                if child.text and child.text.strip():
                    entry["bin"].append(child.text.strip())
            elif tag == "ini":
                p = child.get("path")
                if p:
                    entry["ini"].append(p)
        for d in entry["data"]:
            out[d.lower()] = entry
    return out


def load_metadata(rmmr: Path) -> dict[str, dict]:
    """{显示名: Nexus 元数据}"""
    f = rmmr / "mod_metadata.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def copy_thumbnails(rmmr: Path, meta: dict, dest: Path) -> dict[str, str]:
    """把 RMMR 缓存的缩略图复制到本程序目录，返回 {显示名: 相对路径}。"""
    dest.mkdir(parents=True, exist_ok=True)
    out: dict[str, str] = {}
    for name, info in meta.items():
        rel = (info or {}).get("thumbnail_path") or ""
        if not rel:
            continue
        src = rmmr / rel
        if not src.is_file():
            continue
        target = dest / src.name
        try:
            if not target.is_file() or target.stat().st_size != src.stat().st_size:
                shutil.copy2(src, target)
            out[name] = f"thumbs/{target.name}"
        except OSError:
            continue
    return out


def build_index(game_root: Path, thumbs_dest: Path) -> dict:
    """一次性读取 RMMR 资料，返回可直接给扫描器用的索引。"""
    rmmr = find_rmmr(game_root)
    if rmmr is None:
        return {"found": False, "dir": "", "by_folder": {}, "meta": {}, "thumbs": {}}
    by_folder = load_installed(rmmr)
    meta = load_metadata(rmmr)
    thumbs = copy_thumbnails(rmmr, meta, thumbs_dest)
    return {
        "found": True,
        "dir": str(rmmr),
        "by_folder": by_folder,
        "meta": meta,
        "thumbs": thumbs,
    }
