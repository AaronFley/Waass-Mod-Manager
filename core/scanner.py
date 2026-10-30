"""只读扫描器：把游戏目录里的 mod 现状完整读出来。

硬约束：只读。这个模块不写游戏目录里的任何文件。
写操作全部在 actions.py 里，并且必须先过 safety.py 的快照。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import i18n, identify, legacy, merger
from .models import ModRecord, ScanResult, Warning
from .paths import DATA_DIR, game_layout
from .settings_ini import SettingsFile

_NEXUS_RE = re.compile(r"nexusmods\.com/(?:[a-z0-9]+/)?mods/(\d+)", re.I)
_LEADING_NUM_RE = re.compile(r"^(\d+)")
_README_GLOB = ("readme*", "README*", "*.txt", "*.md")

# 游戏本体自带的菜单文件，永远不算 mod，也不报"孤儿"
VANILLA_MENU_XML = {
    "audio.xml", "display.xml", "gameplay.xml", "gamma.xml", "graphics.xml",
    "hdr.xml", "hidden.xml", "hud.xml", "input.xml", "localization.xml",
}

# Nexus 英文分类 → 中文分组名（前端有一份同样的，改动时记得同步）
CAT_CN = {
    "User Interface": "界面", "Visuals and Graphics": "画面", "Combat": "战斗",
    "Gameplay Changes": "玩法", "Items": "装备", "Inventory": "装备",
    "Armour": "装备", "Weapons": "装备", "Bug Fixes": "修复",
    "Performance": "优化", "Quests and Adventures": "任务", "Immersion": "沉浸",
    "Audio": "音效", "Miscellaneous": "杂项", "Utilities": "工具",
    "Environment": "环境", "Models and Textures": "模型贴图",
    "Magic Spells and Enchantments": "法印",
    "Alchemy, Food, Farming and Housing": "炼金", "Characters": "角色",
    "Locations": "地点", "Buildings": "建筑", "Skills and Perks": "技能",
    "Overhauls": "大修",
}


def _dir_size(path: Path, limit: int = 400_000) -> tuple[int, int]:
    """返回 (总字节数, 文件数)。上限只防极端情况，正常 mod 走在上限内。"""
    total = 0
    count = 0
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
        if count > limit:
            break
    return total, count


def _find_nexus_id(mod_dir: Path) -> str | None:
    """从 mod 目录里散落的 readme / 说明文件猜 Nexus ID。"""
    try:
        entries = list(os.scandir(mod_dir))
    except OSError:
        return None
    for e in entries:
        if not e.is_file(follow_symlinks=False):
            continue
        low = e.name.lower()
        if not (low.startswith("readme") or low.endswith((".txt", ".md", ".url"))):
            continue
        try:
            if e.stat(follow_symlinks=False).st_size > 2_000_000:
                continue
            raw = Path(e.path).read_bytes()
        except OSError:
            continue
        m = _NEXUS_RE.search(raw.decode("utf-8", errors="ignore"))
        if m:
            return m.group(1)
    return None


_VER_OK = re.compile(r"^\d+(?:\.\d+){0,3}[a-z]?$", re.I)


def _version_from_info(p: Path) -> str:
    """从 mod 自带的 info.json 里读 version。

    只认长得像版本号的值 —— 有的包里这个字段是 "n/a" 或一串说明文字，
    拿它去比大小会得出「本地比线上新」这种更离谱的结论。
    """
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    v = str(data.get("version") or "").strip()
    return v if _VER_OK.match(v) else ""


def _collect(mod_dir: Path) -> dict:
    """看一个 mod 文件夹里有什么。"""
    info = {
        "has_scripts": False,
        "w3strings": [],
        "bundled_menu_xmls": [],
        "version": "",
        "version_file": "",
    }
    scripts = mod_dir / "content" / "scripts"
    info["has_scripts"] = scripts.is_dir()

    # 本地化文件：只在 content 下找，避免挖进贴图目录
    content = mod_dir / "content"
    if content.is_dir():
        for p in content.rglob("*.w3strings"):
            info["w3strings"].append(str(p.relative_to(mod_dir)).replace("\\", "/"))
            if len(info["w3strings"]) >= 60:
                break

    menu = mod_dir / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
    if menu.is_dir():
        for p in menu.glob("*.xml"):
            info["bundled_menu_xmls"].append(p.name)

    # 版本号：三条来源，可信度从高到低
    #   1. content/info.json —— REDkit 打包的 mod 必带，作者自己写的版本，
    #      跟着文件一起被覆盖，所以更新之后它是**磁盘上的真相**；
    #   2. 根目录 version.txt / VERSION / modinfo.*（老 mod 才这么干）；
    #   3. 都没有就空着，交给 track.py 从下载包/安装档案里推定。
    info["version"] = ""
    info["version_file"] = ""
    for cand in ("content/info.json", "info.json"):
        f = mod_dir / cand
        if f.is_file():
            v = _version_from_info(f)
            if v:
                info["version"] = v
                info["version_file"] = "info"
                break
    if not info["version"]:
        for cand in ("version.txt", "VERSION", "modinfo.txt", "modinfo.json"):
            f = mod_dir / cand
            if f.is_file():
                try:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    break
                m = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", txt)
                if m:
                    info["version"] = m.group(1)
                    info["version_file"] = "txt"
                break
    return info


def _strip_prefix(folder: str) -> str:
    """mod0000_UIRemasteredEdition -> UIRemasteredEdition（只用于展示，不改磁盘）。"""
    name = folder.lstrip("~")
    name = re.sub(r"^mod[_\-]?", "", name)
    return name or folder


def scan(game_root: Path, data_dir: Path = DATA_DIR) -> ScanResult:
    lay = game_layout(game_root)
    res = ScanResult(
        game_root=str(game_root),
        user_config=str(lay["user_config"]),
        scanned_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )

    settings = SettingsFile.load(lay["user_config"] / "mods.settings")
    res.settings_entries = len(settings)

    # 旧管理器留下的静态资料只搬一次，之后全用自己 data/legacy.json 里的存档。
    # 1.0.10 起不再往界面报「已搬 N 条」：那是一次性事件，导入入口也撤了，
    # 再说一遍只会让人以为还要做什么。
    store = legacy.ensure_migrated(game_root)
    by_folder: dict[str, dict] = store.get("by_folder", {})

    # ---------- 1. mods/ ----------
    mods_root = lay["mods"]
    mod_dirs: list[Path] = []
    if mods_root.is_dir():
        for e in os.scandir(mods_root):
            if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                mod_dirs.append(Path(e.path))

    records: list[ModRecord] = []
    for d in sorted(mod_dirs, key=lambda p: p.name.lower()):
        folder = d.name
        base = folder.lstrip("~")
        sec = settings.get(base)
        disabled_by_prefix = folder.startswith("~")
        enabled = (not disabled_by_prefix) and (sec["enabled"] if sec else True)
        priority = sec["priority"] if sec else None

        rec = ModRecord(
            key=base,
            name=_strip_prefix(folder),
            folder=folder,
            enabled=enabled,
            priority=priority,
        )

        # 自己的存档优先（里面是上次搬过来的名字、分类、封面）
        saved = by_folder.get(base.lower())
        display_name = None
        if saved:
            display_name = saved.get("name")
            rec.dlc_dirs = list(saved.get("dlc") or [])
            rec.menu_xmls = list(saved.get("menu") or [])
            rec.nexus_id = str(saved.get("nexus_id") or "").strip() or None
            rec.id_src = "legacy" if rec.nexus_id else ""
            rec.author = str(saved.get("author") or "")
            rec.category = str(saved.get("category") or "")
            rec.latest_version = str(saved.get("remote_version") or "")
            rec.latest_at = str(saved.get("last_checked") or "")
            rec.thumbnail = str(saved.get("thumbnail") or "")
            if saved:
                rec.source = "legacy"

        info = _collect(d)
        rec.has_scripts = info["has_scripts"]
        rec.w3strings = info["w3strings"]
        rec.bundled_menu_xmls = info["bundled_menu_xmls"]
        rec.version = info["version"]
        rec.version_file = info["version_file"]

        size, count = _dir_size(d)
        rec.size_bytes = size
        rec.file_count = count
        try:
            rec.mtime = d.stat().st_mtime
        except OSError:
            pass

        if not rec.nexus_id:
            # 先问「本地证据」：自己记过的 > 安装档案 > 下载目录里的原包名。
            # 都不成立才去 readme 里找链接 —— 顺序不能反，readme 是作者手写的，
            # 有时指的是同系列的另一个 mod。
            nid, src = identify.guess(folder, rec.name, rec.version)
            if nid:
                rec.nexus_id, rec.id_src = nid, src
            else:
                rec.nexus_id = _find_nexus_id(d)
                rec.id_src = "readme" if rec.nexus_id else ""
                if rec.nexus_id:
                    identify.remember(folder, rec.nexus_id, "readme")

        if display_name:
            rec.name = display_name

        # 1.2.6 起 `~` 前缀就是本程序自己的禁用方式（`actions.set_enabled`
        # 禁用时改名、启用时改回来），不再是「别人留下的老办法」。
        # 这里以前会报一条「建议改成 mods.settings 里的 Enabled=0」，
        # 现在已经自相矛盾，删掉。
        records.append(rec)

    # ---------- 2. dlc/ ----------
    dlc_root = lay["dlc"]
    disk_dlc: dict[str, bool] = {}      # 裸名 -> 是否启用（~ 前缀 = 停用）
    if dlc_root.is_dir():
        for e in os.scandir(dlc_root):
            if not e.is_dir(follow_symlinks=False) or e.name.startswith("."):
                continue
            if e.name.startswith("~"):
                disk_dlc[e.name[1:]] = False
            else:
                disk_dlc[e.name] = True

    known = {d.lower() for r in records for d in r.dlc_dirs}
    by_mod_name = {r.key.lower(): r for r in records}

    for bare in sorted(disk_dlc, key=str.lower):
        if bare.lower() in known:
            continue
        stem = re.sub(r"^dlc[_\-]?", "", bare).lower()
        hit = by_mod_name.get("mod" + stem) or by_mod_name.get(stem)
        if hit is None:
            for k, r in by_mod_name.items():
                core = k.replace("mod", "")
                if stem and core and (core in stem or stem in core):
                    hit = r
                    break
        if hit is not None:
            hit.dlc_dirs.append(bare)
        else:
            res.orphan_dlc.append(bare)

    for r in records:
        # 只保留磁盘上真实存在的 dlc，并如实反映其启用状态
        keep = [d for d in r.dlc_dirs if d in disk_dlc]
        r.dlc_dirs = keep
        r.has_dlc = bool(keep)
        # DLC 的 `~` 前缀同样是正常的停用手段（游戏只认 dlc/ 下不带 `~` 的目录），
        # 状态照实反映即可，不必再报一条「你用了老办法」。
        if any(not disk_dlc[d] for d in keep):
            r.enabled = False

    # ---------- 3. 菜单 xml ----------
    # 三种状态：正常 .xml / 被改名停用 .xml.disabled / 游戏自带（忽略）
    menu_root = lay["menu_xml"]
    disk_menu: dict[str, bool] = {}     # 裸文件名 -> 是否启用
    if menu_root.is_dir():
        for p in menu_root.iterdir():
            if not p.is_file():
                continue
            n = p.name
            if n.lower() in VANILLA_MENU_XML:
                continue
            if n.lower().endswith(".xml"):
                disk_menu[n] = True
            elif n.lower().endswith(".xml.disabled"):
                disk_menu[n[: -len(".disabled")]] = False

    claimed = {m.lower() for r in records for m in r.menu_xmls}
    for xml, active in disk_menu.items():
        if xml.lower() in claimed:
            continue
        low = xml.lower()
        # 比之前必须先去掉 `.xml`：`mod_HoodSlots` ↔ `HoodSlots.xml` 靠的是
        # 「前缀相同」，带上后缀也能中；但反方向（`SwitchableCloak.xml` ↔
        # `mod_hoodsSwitchableCloak`）带后缀就永远比不上了。
        flat = low[:-4] if low.endswith(".xml") else low
        flat = flat.replace("_", "").replace("-", "")

        # 归属用「覆盖率」而不是「谁包含谁」。谁包含谁有两个方向的假阳性：
        #   `modCloak` ← `SwitchableCloak.xml`（`cloak` 是子串，5/15 = 33%）
        #   `mod_hoodsSwitchableCloak` ← `SwitchableCloak.xml`（15/21 = 71%）
        # 短的那一方至少占长的 50% 才算同一个东西，然后取最贴近的。
        def _cover(r) -> float:
            stem = r.key.lower().replace("mod", "").replace("_", "").replace("-", "")
            if len(stem) < 4:
                return 0.0
            if stem == flat:
                return 1.0
            short, longer = (stem, flat) if len(stem) <= len(flat) else (flat, stem)
            if short not in longer:
                return 0.0
            return len(short) / len(longer)

        scored = sorted(((_cover(r), r) for r in records), key=lambda x: -x[0])
        hit = scored[0][1] if scored and scored[0][0] >= 0.5 else None
        if hit is not None:
            hit.menu_xmls.append(xml)
        elif not active:
            pass  # 停用的陌生 xml 不吵人
        else:
            res.orphan_menu.append(xml)

    for r in records:
        on_disk = {k.lower(): v for k, v in disk_menu.items()}
        keep, off = [], []
        for m in r.menu_xmls:
            st = on_disk.get(m.lower())
            if st is None:
                continue
            (keep if st else off).append(m)
        r.menu_xmls, r.disabled_menu_xmls = keep, off

    res.mods = records

    # ---------- 3.5 认出合并产物 ----------
    # Script Merger 的合订本（mod0000_MergedFiles 之类）在磁盘上和普通 mod
    # 长得一样，但它不是用户装的 mod。打上标记后：
    #   - 界面列表不再显示它（用户去 Script Merger 里看合并结果）；
    #   - 冲突检测仍然用完整的 records —— 合订本是判定「已合并」的裁判。
    inv = merger.inventory(game_root)
    if inv.get("found"):
        prod_names = merger.product_names(inv)
        for r in records:
            if r.folder.lower() in prod_names:
                r.is_merged = True

    # ---------- 3.6 合订本必须登记且排最前 ----------
    # 2026-10-27 的事故：合订本文件夹在、台账在，但 mods.settings 里没条目，
    # 它就被排到所有已登记 mod 后面，源 mod 反把合并结果盖掉。这里只报不改
    # （扫描永远只读）；真正补条目发生在用户点保存顺序/启停的时候。
    mstate = merger.merged_state(game_root, inv, settings)
    res.merged = {**mstate, "detail": merger.detail(game_root, inv)}
    if mstate["exists"] and not mstate["ok"]:
        res.warnings.append(Warning(
            "warn", "merge_unregistered",
            i18n.t("scan.merge_unregistered",
                   name=mstate['name'], problem=mstate['problem']),
            target=mstate["name"]))

    # ---------- 4. 一致性检查 ----------
    disk_keys = {r.key.lower() for r in records}
    for sec in settings.sections:
        if sec["name"].lower() not in disk_keys:
            res.warnings.append(Warning(
                "warn", "stale_settings",
                i18n.t("scan.stale_settings", name=sec['name']),
                target=sec["name"]))

    dup: dict[int, list[str]] = {}
    for r in records:
        if r.priority is not None:
            dup.setdefault(r.priority, []).append(r.key)
    for prio, names in dup.items():
        if len(names) > 1:
            res.warnings.append(Warning(
                "warn", "dup_priority",
                i18n.t("scan.dup_priority", prio=prio,
                       n=len(names), names="、".join(names))))

    return res
