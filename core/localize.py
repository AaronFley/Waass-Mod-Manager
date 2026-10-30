"""汉化：把 mod 里残留的英文找出来，交给 AI / 人翻，再安全写回去。

现实是什么（2026-10-25 用 18 个真实 mod 逐字节核对过）
------------------------------------------------------
1. **en.w3strings 大多是加密的**：索引表用语言相关的 XOR 掩码打散，文本块也
   不总是明文。读不出原文，就翻不了 —— 我们不去猜密钥。
2. **cn.w3strings / zh.w3strings 通常是明文**，而且里面经常混着没翻的英文
   （作者直接拿英文填了中文槽，或者只翻了标题没翻选项）。
   这才是真正有活干的地方。
3. **菜单 xml 基本全是 localization key**，字面英文极少（18 个 mod 合计 0 条），
   所以改 xml 这条路效益很低，只作为兜底保留。

于是判定改成「看内容，不看文件名」：
- 一个语言文件只要**能读出足够多的文字**，就是可编辑的；
- 里面**含中文的条目**算已完成；
- 里面**还是英文的条目**就是待翻任务。

写回只做「等长替换」：改文本块里的字节，偏移/长度/哈希全不动，文件大小一模一样。
代价是译文不能比原文长（一个汉字 3 字节），放不下的会被跳过并如实告诉你。

导出格式设计成「人能看懂、AI 能一次翻完、程序能校验」的样子：
    {"kind":"waass-translate","items":[{"id","mod","file","kind","where","src","dst","max"}]}
用户把 dst 填上再导回来即可。
"""
from __future__ import annotations

import json
import re
import shutil
import time
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

from . import i18n, w3strings
from .paths import DATA_DIR, game_layout

JOBS_DIR = DATA_DIR / "translate"
BACKUP_DIR = DATA_DIR / "localize_backups"

# 认为「已经是中文」的判断
_CJK = re.compile(r"[\u4e00-\u9fff]")
# localization key 的样子：全 ASCII、下划线连字符、没有空格
_KEYISH = re.compile(r"^[A-Za-z0-9_.\-]+$")
# 常见需要跳过的东西
_SKIP_ATTR = {"id", "varId", "value", "displayType", "tags", "type", "actionType"}
_TEXT_ATTR = ("displayName", "title", "text", "label", "name", "tooltip")

# w3strings 里的富文本标签，翻译时要原样保留
_TAG = re.compile(r"<[^>]*>")

# 只处理这两类语言文件：玩家用中文界面，其它语种的残留翻译了也没人看
_CN_LANGS = ("cn", "zh")


# ---------------- 哪条该翻 ----------------
def is_todo(text: str) -> bool:
    """这条文本是不是「还没翻的英文」—— 是的话就该进任务包。

    判据刻意保守：只认确定是给人看的英文，宁可漏掉几条，也不要塞一堆
    文件路径、格式符、别的语种进翻译列表让人（或 AI）去猜。
    """
    if not text:
        return False
    plain = _TAG.sub("", text).strip()
    if len(plain) < 3:
        return False
    if _CJK.search(plain):
        return False                       # 已经有中文了
    # 阿拉伯/俄/日韩这类：非 ASCII 字符占比高，肯定不是待翻的英文
    ascii_chars = sum(1 for c in plain if c.isascii())
    if ascii_chars / max(len(plain), 1) < 0.85:
        return False
    letters = sum(1 for c in plain if c.isascii() and c.isalpha())
    if letters < 2:
        return False
    if letters / max(len(plain), 1) < 0.4:
        return False                       # 数字/符号为主
    low = plain.lower()
    if low.endswith((".xml", ".ws", ".bundle", ".w3strings", ".dds", ".xbm", ".png")):
        return False
    if ("\\" in plain or "/" in plain) and " " not in plain:
        return False                       # 资源路径
    # 界面文字里几乎不会出现这些符号；出现就说明是从二进制里切出来的碎片
    if any(c in plain for c in '}~^`|{}[]'):
        return False
    return True


def budget(length: int) -> int:
    """这个字节预算大概能装几个汉字（UTF-8 一个汉字 3 字节）。"""
    return max(0, length // 3)


# ---------------- 分类：哪些算「菜单页」，哪些别碰 ----------------
# 用户 2026-10-28 的要求：默认不改模组名，只汉化菜单页。
# 理由不是审美，是安全 —— 模组名经常被脚本用来认自己、做匹配、拼路径，
# 改了名字出现的问题（菜单项对不上、设置不生效）极难归因到「汉化」头上。
CAT_MENU = "menu"      # 菜单项、分组名、署名行
CAT_MOD = "mod_name"   # 模组自己的显示名
CAT_ITEM = "item"      # 物品名（护甲 / 武器 / 套装）

class _CatText(Mapping):
    """类别名 / 提示：取值时才过 i18n.t()。

    语言在运行时可切换，模块顶层不许调 t()；但 server.py 直接消费这两个
    映射，所以做成「键固定、值在读取时翻译」的只读映射，用法和 dict 一致。
    """

    def __init__(self, table: dict):
        self._table = table

    def __getitem__(self, k):
        return i18n.t(self._table[k])

    def __iter__(self):
        return iter(self._table)

    def __len__(self):
        return len(self._table)


CAT_LABEL = _CatText({
    CAT_MENU: "loc.cat_menu", CAT_MOD: "loc.cat_mod_name", CAT_ITEM: "loc.cat_item",
})
CAT_HINT = _CatText({
    CAT_MENU: "loc.cat_hint_menu",
    CAT_MOD: "loc.cat_hint_mod",
    CAT_ITEM: "loc.cat_hint_item",
})
CAT_DEFAULT = (CAT_MENU,)

_POSSESSIVE = re.compile(r"['’]s\s")
_MENU_HINT = re.compile(
    r"<font|<br|\b(auto|enable|allow|unequip|equip|show|hide|toggle|set|use|display"
    r"|disable|switch|animate|force|ignore|mod by)\b", re.I)

# 单个词但确实是菜单/开关的词。判「这是不是分类名」时，命中这里的一律不算。
# 少一个词顶多是少翻一句菜单（用户勾上还能翻），多一个词会把分类名当待翻，
# 于是又冒出「还差 1 字节」这种没法办的条目 —— 所以这里宁可列全一点。
_MENU_WORDS = {
    "enable", "disable", "enabled", "disabled", "on", "off", "default", "other",
    "others", "none", "all", "auto", "yes", "no", "ok", "debug", "general",
    "reset", "apply", "save", "cancel", "close", "back", "next", "settings",
    "options", "presets", "comments", "logging", "language", "version", "info",
    "notes", "warning", "test", "unlock", "locked", "show", "hide", "force",
    "custom", "always", "never", "mod", "mods", "true", "false", "empty",
}

# 装备 / 物品的分类名（复数居多）。只用来认「这是分类名不是菜单句子」。
_ITEM_WORDS = {
    "hoods", "capes", "cloaks", "masks", "scarves", "gloves", "boots",
    "trousers", "armour", "armor", "swords", "shields", "accessories",
    "gauntlets", "crossbows", "daggers", "sets", "wraps",
}


def _flat(s: str) -> str:
    """只留字母数字小写，用来比名字（'Hood Slots' == 'hood_slots'）。"""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _is_category_word(t: str) -> bool:
    """单个词、首字母大写、不是菜单词表里的词 → 当分类名（物品名）看待。

    这条是为了 `Hoods`（护甲分类）这种：它长得像菜单项，但其实是个分类名，
    而且 5 个字节根本放不下「兜帽」（6 字节），列成待翻只会给用户出难题。
    """
    if not t or " " in t or "\t" in t:
        return False
    if not t[0].isupper():
        return False
    low = t.lower()
    if low in _MENU_WORDS:
        return False
    return low in _ITEM_WORDS or (low.endswith("s") and len(low) >= 4)


def _is_name_with_version(flat: str, names: set[str]) -> bool:
    """「模组名 + 版本号」也是模组名，不是菜单句子。

    语言表里常把名字和版本各占一条：`Mod Settings Menu Fix` / `Mod Settings Menu Fix 1.5.0`。
    改名字没意义（脚本靠它认自己），所以这两条都不该进待翻。
    2026-10-29 用户实测：只认了裸名字，带版本那条被当成菜单英文，面板一直提示
    「还有英文待翻」，而游戏里菜单其实是全中文的。
    """
    for nm in names:
        if not nm or not flat.startswith(nm):
            continue
        rest = flat[len(nm):]
        if rest and re.fullmatch(r"\d{1,6}[a-z]?", rest):
            return True
    return False


def classify(src: str, idx: int = -1, mod_display: str = "", folder: str = "") -> str:
    """判定这条文本属于哪一类。

    判断依据只有两条硬信号，其余一律归到「菜单页」—— 宁可多翻一条菜单，
    也不要把物品名误当菜单翻译（后者会真的出问题，前者最多是翻错一句）。
    """
    t = (src or "").strip()
    if not t:
        return CAT_MENU

    flat = _flat(t)
    names = {_flat(mod_display), _flat(folder)}
    for p in ("mod_", "mod", "dlc_", "dlc"):
        if folder and folder.lower().startswith(p):
            names.add(_flat(folder[len(p):]))
    names.discard("")

    # 语言表第 0 条几乎总是「模组名」
    if idx == 0 or flat in names or _is_name_with_version(flat, names):
        return CAT_MOD

    # 所有格 + 没有任何菜单特征 → 物品名（Deadly Ronin's Kimono）
    if _POSSESSIVE.search(t) and not _MENU_HINT.search(t):
        return CAT_ITEM

    # 单个分类词（Hoods / Capes）→ 物品名，不列进待翻
    if _is_category_word(t):
        return CAT_ITEM

    return CAT_MENU


def xml_takeover(mod_dir: Path, menu_root: Path, mod, done: int) -> dict:
    """菜单 XML 是否已经用中文字面「接管」了界面文字。

    背景（2026-10-28 用户实测反馈）：
    有人手汉化时根本不碰语言表，而是直接改菜单 XML ——
    `<Var displayName="启用模组" tags="customNames;nonLocalizedName"/>`。
    这样界面上显示的是 XML 里的中文字面，语言表那 36 条英文就成了**过期残留**
    （它们原本是 displayName 的值，被中文替换后就没人再引用了）。

    旧逻辑只认语言表，于是把这种模组照旧报成「还有 36 条英文没翻」。

    判定条件（两条都要满足，避免把「只翻了前几行」的模组误判成已完成）：
      1. XML 里至少有 1 条中文字面 displayName —— 说明确实有人改过 XML；
      2. 语言表里已翻的条目数 >= XML 里仍以 key 形式引用的**不同 key 数** ——
         说明这些 key 都对应得上中文，剩下的英文确实没人引用。

    用「不同 key 数」而不是「引用次数」：`njs_all` 这种同一个 key 可能被
    好几个 Preset 用到，按次数算会虚高，把真该翻的模组判成已完成。

    返回 {"on": bool, "cjk": 中文字面数, "keys": 不同 key 数}
    """
    cjk = 0
    keys: set[str] = set()
    for x in _menu_files(mod_dir, menu_root, mod):
        if x["name"].lower().endswith(".disabled"):
            continue
        got = extract_xml(x["path"])
        cjk += got.get("cjk", 0)
        keys.update(got.get("key_names") or [])
    on = bool(cjk) and done >= len(keys)
    return {"on": on, "cjk": cjk, "keys": len(keys)}


# ---------------- 现状盘点 ----------------
def inspect(root: Path, mods: list) -> list[dict]:
    """每个 mod 的汉化现状：哪些语言文件能改、还差几条英文。"""
    lay = game_layout(root)
    out: list[dict] = []

    for m in mods:
        d = lay["mods"] / m.folder
        if not d.is_dir():
            continue

        w3 = w3strings.scan_mod(d)
        files: list[dict] = []
        todo = 0
        done = 0
        samples: list[str] = []
        locked = 0

        for f in w3:
            row = {"lang": f["lang"], "rel": f["rel"], "size": f["size"],
                   "ok": f["ok"], "reason": f.get("reason", ""),
                   "todo": 0, "done": 0}
            if not f["ok"] or f["lang"] not in _CN_LANGS:
                # 加密的读不了；其它语种（ar/de/ru…）翻了也没人看，别浪费时间
                if not f["ok"]:
                    locked += 1
                files.append(row)
                continue
            try:
                texts = w3strings.extract(f["path"])
            except OSError:
                texts = []
            n_todo = n_done = 0
            for k, t in enumerate(texts):
                s = t["text"]
                if _CJK.search(s):
                    n_done += 1
                elif is_todo(s) and classify(s, k, m.name, m.folder) == CAT_MENU:
                    # 分类和任务包共用同一套判断：模组名（含「名字 + 版本号」）与物品名
                    # 本来就不翻，数进 todo 只会让人以为还有活干 —— 面板和任务包
                    # 各说一套是 1.2.3 里的真实误报来源。
                    n_todo += 1
                    if len(samples) < 3:
                        samples.append(_TAG.sub("", s).strip())
            row["todo"] = n_todo
            row["done"] = n_done
            todo += n_todo
            done += n_done
            files.append(row)

        # 菜单 xml 里的字面英文（绝大多数 mod 是 0 条，保留兜底）
        xmls = _menu_files(d, lay["menu_xml"], m)
        text_units = 0
        key_only = 0
        for x in xmls:
            got = extract_xml(x["path"])
            x["units"] = len(got["items"])
            x["keyed"] = got["keyed"]
            text_units += len(got["items"])
            key_only += got["keyed"]
            if len(samples) < 3:
                samples.extend(i["src"] for i in got["items"][:2])

        if not w3 and not xmls:
            continue

        # 菜单 XML 里已经写成中文了？那语言表里这些英文就是**被取代的旧文本**，
        # 界面上根本不显示，不该再算「待翻」。
        # 必须和 build_jobs() 用同一个判定 —— 面板说「已汉化」而任务包还在
        # 让它翻同一批句子，是 1.2.3 就吃过一次的亏（面板和任务包各说一套）。
        take = xml_takeover(d, lay["menu_xml"], m, done)
        stale_n = 0
        if take["on"]:
            for f in files:
                if f["todo"]:
                    f["stale"] = f["todo"]
                    f["todo"] = 0
                    stale_n += f["stale"]
            todo -= stale_n
        took_over = take["on"] and stale_n > 0

        readable = [f for f in files if f["ok"] and f["lang"] in _CN_LANGS]
        if took_over:
            why = i18n.t("loc.why_takeover", n=stale_n)
        elif todo or text_units:
            why = (i18n.t("loc.why_todo", n=todo) if todo
                   else i18n.t("loc.why_xml", n=text_units))
        elif not readable:
            why = (i18n.t("loc.why_encrypted")
                   if any(f["lang"] in _CN_LANGS for f in files)
                   else i18n.t("loc.why_no_cn"))
        elif done:
            why = i18n.t("loc.why_done")
        else:
            why = i18n.t("loc.why_nothing")

        out.append({
            "key": m.key,
            "name": m.name,
            "folder": m.folder,
            "enabled": m.enabled,
            "files": files,
            "todo": todo,
            "done": done,
            "locked": locked,
            "stale": stale_n,
            "takeover": take["on"],
            "has_cn": any(f["lang"] in ("cn", "zh") for f in files),
            "menus": xmls,
            "xml_units": text_units,
            "xml_keyed": key_only,
            "ready": bool(todo or text_units),
            "why": why,
            "samples": samples,
        })

    out.sort(key=lambda x: (not x["ready"], -x["todo"], x["name"].lower()))
    return out


def script_text(mod_dir: Path) -> dict:
    """第三条通道：菜单文字直接写在 `.ws` 里，按游戏语言分支返回。

    AutoLoot 就是这样：`modAutoLootLoc.ws` 里 941 个中文字符，
    `if (lang == "ZH") return "…"` 分支给出中文，最后兜底英文。
    这类模组的语言表里没有中文，但游戏里显示得完全正常 ——
    只看语言表会把它们误报成「缺中文表」。

    只读识别，**不改 .ws**：改源码和改语言表不是一个风险量级。
    """
    base = Path(mod_dir) / "content" / "scripts" / "local"
    cjk = 0
    files = 0
    if base.is_dir():
        for p in base.rglob("*.ws"):
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            n = len(_CJK.findall(t))
            if n:
                cjk += n
                files += 1
    return {"cjk": cjk, "files": files}


def catalog(root: Path, mod_keys: list[str] | None = None) -> list[dict]:
    """按模组列出「能勾什么、翻不了的是为什么」。汉化面板的勾选列表用它。

    每个模组给出三类内容的条数。界面默认只勾「菜单页」那一列，
    模组名和物品名摆出来但默认不勾 —— 用户想翻可以自己勾上。
    """
    from .scanner import scan as do_scan

    res = do_scan(root)
    mods = res.mods
    if mod_keys:
        wanted = set(mod_keys)
        mods = [m for m in mods if m.key in wanted]

    rows = {r["key"]: r for r in inspect(root, mods)}
    jobs = build_jobs(root, mod_keys, cats=None)
    all_items = jobs["items"]
    stale = jobs.get("stale") or {}
    lay = game_layout(root)

    by: dict[str, dict] = {}
    for it in all_items:
        cat = it.get("cat", CAT_MENU)
        slot = by.setdefault(it["mod"], {CAT_MENU: 0, CAT_MOD: 0, CAT_ITEM: 0})
        slot[cat] = slot.get(cat, 0) + 1

    out: list[dict] = []
    for m in mods:
        row = rows.get(m.key)
        if not row:
            continue
        cnt = by.get(m.key) or {CAT_MENU: 0, CAT_MOD: 0, CAT_ITEM: 0}
        sc = script_text(lay["mods"] / m.folder) if not row["has_cn"] else {"cjk": 0, "files": 0}
        blocked = ""
        note = ""
        n_stale = row.get("stale") or stale.get(m.key, 0)
        if not row["has_cn"]:
            if sc["cjk"]:
                # 有中文，只是不在语言表里 —— 别误报成「缺中文」
                blocked = i18n.t("loc.blocked_in_script")
                note = i18n.t("loc.note_in_script")
            elif not (cnt.get(CAT_MENU, 0) + cnt.get(CAT_MOD, 0) + cnt.get(CAT_ITEM, 0)):
                blocked = i18n.t("loc.why_nothing")
                note = i18n.t("loc.note_no_todo")
            else:
                blocked = i18n.t("loc.blocked_no_cn")
        elif not any(f["ok"] and f["lang"] in _CN_LANGS for f in row["files"]):
            blocked = i18n.t("loc.blocked_encrypted")
            note = i18n.t("loc.note_encrypted")
        elif n_stale and not cnt.get(CAT_MENU, 0):
            # 有人手改过菜单 XML，界面文字已经是中文；语言表里剩下的是残留
            blocked = i18n.t("loc.blocked_takeover")
            note = i18n.t("loc.note_takeover", n=n_stale)
        out.append({
            "key": m.key,
            "name": m.name,
            "folder": m.folder,
            "enabled": m.enabled,
            "counts": cnt,
            "menu": cnt.get(CAT_MENU, 0),
            "docs": cnt.get(CAT_MOD, 0) + cnt.get(CAT_ITEM, 0),
            "stale": n_stale,
            "files": [f for f in row["files"] if f["lang"] in _CN_LANGS],
            "samples": row["samples"],
            "why": row["why"],
            "blocked": blocked,
            "note": note,
            "script_cjk": sc["cjk"],
            "ready": cnt.get(CAT_MENU, 0) > 0,
        })

    out.sort(key=lambda x: (not x["ready"], not x["enabled"], -x["menu"], x["name"].lower()))
    return out


def _menu_files(mod_dir: Path, menu_root: Path, mod) -> list[dict]:
    """这个 mod 相关的菜单 xml（游戏目录里的 + mod 自带的）。"""
    out: list[dict] = []
    seen: set[str] = set()

    for name in list(mod.menu_xmls) + list(mod.disabled_menu_xmls):
        p = menu_root / name
        if p.is_file() and str(p).lower() not in seen:
            seen.add(str(p).lower())
            out.append({"path": str(p), "name": name, "where": "game", "disabled": False})

    bundled = mod_dir / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
    if bundled.is_dir():
        for p in bundled.glob("*.xml*"):
            if str(p).lower() in seen:
                continue
            seen.add(str(p).lower())
            out.append({"path": str(p), "name": p.name, "where": "mod",
                        "disabled": p.name.lower().endswith(".disabled")})
    return out


# ---------------- 菜单 xml ----------------
def extract_xml(path: Path | str) -> dict:
    """把 xml 里可直接翻译的字面文本挑出来。

    displayName="LightRewrite_Enabled" 这种是 key，不在此列（翻了也没用）。
    """
    p = Path(path)
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"items": [], "keyed": 0, "cjk": 0, "key_names": []}

    # 用正则扫属性，比 ElementTree 更宽容（这些 xml 有时带非标准写法）
    items: list[dict] = []
    cjk = 0
    key_names: set[str] = set()
    seen: set[str] = set()
    for mt in re.finditer(r'(\w+)\s*=\s*"([^"]*)"', raw):
        attr, val = mt.group(1), mt.group(2)
        if attr not in _TEXT_ATTR or attr in _SKIP_ATTR:
            continue
        v = val.strip()
        if not v:
            continue
        if _CJK.search(v):
            # 已经是中文了 —— 这条不用翻，但它是「有人手工汉化过」的证据，
            # xml_takeover() 靠这个数判断语言表里的英文还算不算数。
            cjk += 1
            continue
        # Mods.xxx / njs_xxx 都是指向语言表的 key。Mods.* 这类带点的以前
        # 直接跳过，结果 xml_takeover 少算了「还在靠语言表的行」，判定会偏松。
        if _KEYISH.match(v) and " " not in v:
            key_names.add(v)
            continue
        if not any(c.isalpha() for c in v):
            continue
        if v in seen:
            continue
        seen.add(v)
        items.append({
            "src": v,
            "attr": attr,
            "where": _line_of(raw, mt.start()),
            "max": 0,                      # xml 不限长度
        })
    return {"items": items, "keyed": len(key_names),
            "cjk": cjk, "key_names": sorted(key_names)}


def _line_of(text: str, pos: int) -> str:
    line = text.count("\n", 0, pos) + 1
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    snippet = text[start:end if end > 0 else len(text)].strip()
    if len(snippet) > 90:
        snippet = snippet[:87] + "…"
    return i18n.t("loc.line_no", line=line, snippet=snippet)


def apply_xml(path: Path | str, mapping: dict[str, str],
              backup_dir: Path | None = None) -> dict:
    """替换 xml 里的字面文本。属性值本身是转义过的，要小心处理。"""
    p = Path(path)
    raw = p.read_text(encoding="utf-8", errors="ignore")
    changed: list[dict] = []

    def sub(mt: re.Match) -> str:
        attr, val = mt.group(1), mt.group(2)
        if attr not in _TEXT_ATTR:
            return mt.group(0)
        v = val.strip()
        dst = mapping.get(v)
        if not dst:
            return mt.group(0)
        dst = str(dst).strip()
        if not dst or dst == v:
            return mt.group(0)
        changed.append({"text": v, "dst": dst})
        safe = (dst.replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
        return f'{attr}="{safe}"'

    new = re.sub(r'(\w+)\s*=\s*"([^"]*)"', sub, raw)
    result = {"file": str(p), "changed": len(changed), "backup": ""}
    if not changed:
        return result

    if backup_dir is not None:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{p.name}.{stamp}.bak"
        dest.write_text(raw, encoding="utf-8")
        result["backup"] = str(dest)

    p.write_text(new, encoding="utf-8")
    result["items"] = changed
    return result


def xml_wellformed(path: Path | str) -> bool:
    try:
        ET.parse(str(path))
        return True
    except (ET.ParseError, OSError):
        return False


# ---------------- 任务包 ----------------
def build_jobs(root: Path, mod_keys: list[str] | None = None,
               include_w3: bool = True,
               cats: tuple[str, ...] | None = None) -> dict:
    """生成翻译任务。返回 {"file": 存放路径, "items": [...]}

    `cats` 是允许进任务的内容类别，默认只有「菜单页」——
    模组名和物品名默认不进来（见 `classify()` 的说明）。
    传 `cats=None` 表示全都要，主要是给面板统计用。
    """
    from .scanner import scan as do_scan

    res = do_scan(root)
    mods = res.mods
    if mod_keys:
        wanted = set(mod_keys)
        mods = [m for m in mods if m.key in wanted]

    lay = game_layout(root)
    items: list[dict] = []
    stale: dict[str, int] = {}          # mod_key -> 被菜单中文字面取代的英文条目数
    n = 0

    for m in mods:
        d = lay["mods"] / m.folder
        if not d.is_dir():
            continue

        for x in _menu_files(d, lay["menu_xml"], m):
            if x["name"].lower().endswith(".disabled"):
                continue
            got = extract_xml(x["path"])
            for it in got["items"]:
                n += 1
                items.append({
                    "id": f"u{n}",
                    "mod": m.key,
                    "mod_name": m.name,
                    "file": x["path"],
                    "kind": "menu_xml",
                    "cat": CAT_MENU,
                    "lang": "",
                    "where": it["where"],
                    "src": it["src"],
                    "max": 0,
                    "dst": "",
                })

        if include_w3:
            # 只挑「能读出文字」的语言文件（基本都是 cn/zh 这类明文），
            # 把里面还是英文的条目做成任务，改的是这个文件本身 —— 不加新文件。
            readable: list[tuple[dict, list]] = []
            done = 0
            for f in w3strings.scan_mod(d):
                if not f["ok"] or f["lang"] not in _CN_LANGS:
                    continue
                try:
                    texts = w3strings.extract(f["path"])
                except OSError:
                    continue
                readable.append((f, texts))
                done += sum(1 for t in texts if _CJK.search(t["text"]))

            # 有人手改过菜单 XML、把界面文字写成中文了？那语言表里的英文
            # 就是过期残留（原本是 displayName 的值，已经被替换掉了）。
            take = xml_takeover(d, lay["menu_xml"], m, done)

            for f, texts in readable:
                for k, t in enumerate(texts):
                    s = t["text"]
                    if _CJK.search(s) or not is_todo(s):
                        continue
                    if take["on"]:
                        stale[m.key] = stale.get(m.key, 0) + 1
                        continue
                    n += 1
                    items.append({
                        "id": f"u{n}",
                        "mod": m.key,
                        "mod_name": m.name,
                        "file": f["path"],
                        "kind": "w3strings",
                        "cat": classify(s, k, m.name, m.folder),
                        "idx": k,
                        "lang": f["lang"],
                        "where": f"{f['rel']} @{t['off']}",
                        "src": s,
                        "max": t["length"],     # 等长替换的硬上限（字节）
                        "room": budget(t["length"]),
                        "dst": "",
                    })

    if cats is not None:
        allow = set(cats)
        items = [i for i in items if i.get("cat", CAT_MENU) in allow]

    payload = {
        "kind": "waass-translate",
        "version": 1,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "game_root": str(root),
        "note": i18n.t("loc.job_note"),
        "items": items,
    }

    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    out = JOBS_DIR / f"translate-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    payload["file"] = str(out)
    payload["count"] = len(items)
    payload["stale"] = stale            # 界面拿它说「这 N 条是过期残留，不用翻」
    return payload


def load_jobs(path: Path | str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(i18n.t("loc.file_missing", path=p))
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(i18n.t("loc.bad_json", err=e)) from None
    if not isinstance(d, dict) or d.get("kind") != "waass-translate":
        raise ValueError(i18n.t("loc.bad_kind"))
    return d


def apply_jobs(data: dict) -> dict:
    """按文件分组，把译文写回去。"""
    from . import safety

    items = [it for it in (data.get("items") or []) if str(it.get("dst") or "").strip()]
    if not items:
        return {"ok": False, "error": i18n.t("loc.no_translations"), "changed": 0}

    by_file: dict[str, list[dict]] = {}
    for it in items:
        by_file.setdefault(it["file"], []).append(it)

    results: list[dict] = []
    total = 0
    skipped: list[dict] = []

    for path, rows in by_file.items():
        p = Path(path)
        if not p.is_file():
            # 目标语言文件还不存在 → 用英文那份做模板复制一份
            tpl = rows[0].get("template") or ""
            if tpl and Path(tpl).is_file():
                safety.authorize_path(p)
                Path(p).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(tpl, p)
            else:
                results.append({"file": path,
                                "error": i18n.t("loc.file_and_template_missing")})
                continue
        safety.authorize_path(p)
        mapping = {r["src"]: r["dst"] for r in rows}
        try:
            if rows[0]["kind"] == "menu_xml":
                r = apply_xml(p, mapping, BACKUP_DIR)
            else:
                r = w3strings.apply(p, mapping, BACKUP_DIR)
        except Exception as e:                       # noqa: BLE001 单个文件失败不影响其它
            results.append({"file": path, "error": f"{type(e).__name__}: {e}"})
            continue
        total += r.get("changed", 0)
        skipped.extend([dict(s, file=p.name) for s in r.get("skipped", [])])
        results.append({k: v for k, v in r.items() if k != "items"})

    # 备份会随每次汉化长大，留最近 20 份够了
    try:
        from . import storage
        storage.prune_backups(20)
    except Exception:                                # noqa: BLE001
        pass

    return {"ok": True, "changed": total, "files": results,
            "skipped": skipped[:30], "skipped_count": len(skipped)}
