"""机器翻译：引擎链、术语表、翻译记忆。

为什么不是「调一个接口就完事」
------------------------------
2026-10-28 把能想到的免 Key 端点全测了一遍，活着的只有 MyMemory 和
Google gtx（LibreTranslate 的公共镜像全是 502/403/301，微软的 edge
免 Key 通道已经 404）。只绑一个，它一挂整条链路就废，用户看到的又是
「汉化失败」。

2026-10-31 把 Google 那个免 Key 的内部翻译端点摘了：它没有公开文档、
没有配额承诺、随时可能被关，却要把用户的菜单文字发出去。
「能翻」抵不上「没经过用户同意就外发」和「哪天突然全挂」这两件事。

同一天接入 B站 Index-Translate（有公开的 OpenAI 兼容接口、匿名可用），
默认排在 MyMemory 前面：模型出的语序比通用记忆库顺，短菜单句尤其明显。
百度留作用户自配。

更要紧的是：**HTTP 200 不等于翻译成功**。实测三种典型失败：

1. 原样退回 —— MyMemory 对 `Mod by <font…>PRILER</font> / Auto Unequip`
   直接回 `MOD BY <font…>PRILER</font>/AUTO UNEQUIP`，大写英文原封不动；
2. 标签挪位 —— 同一引擎把 `Switchable Cloaks by <font…>PRILER</font>`
   翻成 `PRILER可切换斗<font…></font>篷`，颜色应用到一个空串上，
   游戏里就是一个空的染色符号；
3. 短词硬翻 —— `Other` → `其他种族`（没有上下文，纯猜）。

前两种必须判失败并换引擎；第三种只能靠术语表兜。

设计约束
--------
- 只用标准库（urllib），跟 nexus.py 一样，打包体积和依赖都不增加；
- 不发任何游戏内数据，只发待翻的那几句菜单文字；
- 发之前要用户点头（`consent`），同意一次就记住，见 server 的 /api/localize/consent；
- 记忆库和术语表都在 data/translate/ 下，纯本地文件，用户可删可改。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .appinfo import APP_VERSION      # appinfo 只依赖 i18n / paths，不会反向引本模块
from .paths import DATA_DIR

DIR = DATA_DIR / "translate"
CONF_FILE = DIR / "config.json"
MEMO_FILE = DIR / "memory.json"
GLOS_FILE = DIR / "glossary.json"

# 富文本标签。w3strings 里的界面文字常带 <font color="#F54B59">，必须原样保住，
# 否则游戏里的红字/字号效果全丢。
TAG = re.compile(r"<[^<>]{1,200}>")
_CJK = re.compile(r"[\u4e00-\u9fff]")
# 判断「译文其实没翻」时用：只留 ASCII 字母数字，中文一律清掉
_ALNUM = re.compile(r"[^a-z0-9]+")

TIMEOUT = 12
FAILS_BEFORE_SKIP = 3          # 一个引擎连续失败几次就本轮让位

# 巫师3 的固定译名。来源：witcher3/guide 的定名基准（游戏内简中日志优先）。
# 免费引擎经常把 Sign 译成「标志」、Gwent 留着不翻，所以这张表是必需品不是装饰。
DEFAULT_GLOSSARY = {
    "Witcher": "猎魔人",
    "Sign": "法印",
    "Signs": "法印",
    "Gwent": "昆特牌",
    "Mutagen": "突变",
    "Roach": "萝卜",
    "Novigrad": "诺维格瑞",
    "Skellige": "史凯利格",
    "Velen": "威伦",
    "Toussaint": "陶森特",
    "Yennefer": "叶奈法",
    "Triss": "特莉丝",
    "Ciri": "希里",
    "Geralt": "杰洛特",
    "Quen": "昆恩",
    "Igni": "伊格尼",
    "Aard": "阿尔德",
    "Yrden": "亚登",
    "Axii": "阿克西",

    # —— 菜单惯用短语 ——
    # 这些词单独拎出来翻译时，通用引擎没有语境，必错或者译得又长又怪。
    # 实测（2026-10-28，Google）：
    #   IN INTERIORS   → 在室内装饰     （要 6 个汉字，其实是 2 个）
    #   in interiors   → 在室内设计     （换个大小写还是一样错）
    #   IN ANY STATE   → 在任何州       （state 当成美国的州）
    #   FIST FIGHT     → 拳打脚踢       （对，但 4 个字太占地方）
    # 这里给的是「游戏菜单里就该这么写」的短译法。
    "In combat": "战斗中",
    "After combat": "战斗后",
    "In interiors": "室内",
    "Leaving interiors": "离开室内",
    "During cutscenes": "过场动画",
    "After cutscenes": "过场动画后",
    "In any state": "任意状态",
    "Swimming": "游泳",
    "Horse riding": "骑马",
    "Fist fight": "拳斗",
    "Barber": "理发",
    # 天气词要给「能直接接在『时』前面」的形态。译成「雨」会得到
    # 「在雨天气期间自动装备」→ 修完变成「在雨时」，不通顺。
    "Rain": "下雨",
    "Storm": "风暴",
    "Snow": "下雪",
    "Cold": "寒冷",
    "Wind": "刮风",
    "Comments": "提示",
    "Other": "其他",
    "Hoods": "兜帽",
    "Cloaks": "斗篷",
    "Capes": "披风",
    "Cloaks/Capes": "斗篷/披风",
    "Only cloaks/capes": "仅斗篷/披风",
    "Only hoods/scarfs": "仅兜帽/围巾",
}

DEFAULT_CONFIG = {
    # 顺序 = 尝试顺序。启动时探测一次，不通的自动往后排。
    "order": ["indextranslate", "mymemory"],
    "engines": {
        "indextranslate": {"on": True},
        "mymemory": {"on": True, "keys": [], "email": ""},
        "baidu": {"on": False, "appid": "", "keys": []},
    },
    # 用户有没有同意过「把待翻的界面文字发到上面这些服务」。
    # 必须在 server 侧拦，不能只靠前端藏按钮 —— 接口是可以直接调的。
    "consent": False,
}


# ---------------- 配置 / 记忆 / 术语 ----------------
def _read_json(p: Path, fallback: dict) -> dict:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else dict(fallback)
    except (OSError, json.JSONDecodeError):
        return dict(fallback)


def _write_json(p: Path, data: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _merge_order(order: list[str]) -> list[str]:
    """把默认链里、用户 order 还没有的引擎追加到末尾。

    升级前的老配置 `order` 只有 `["mymemory"]`，光靠「过滤下线引擎」永远补不进
    新引擎 —— 用户在设置里看不到它，链路里也轮不到它，等于没接。
    已存在的顺序一律不动（用户自己排过序就按他的来），只往后追加；
    这次追加只作用于本次读取，不强制写盘，用户下次保存时才落进文件。
    """
    out = list(order)
    for eid in DEFAULT_CONFIG["order"]:
        if eid in ENGINES and eid not in out:
            out.append(eid)
    return out


def config() -> dict:
    d = _read_json(CONF_FILE, DEFAULT_CONFIG)
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULT_CONFIG.items()}
    for k, v in d.items():
        if k == "engines" and isinstance(v, dict):
            for eid, ecfg in v.items():
                if eid not in ENGINES:
                    # 老配置里可能留着已经下线的引擎（1.3.1 之前的 google）。
                    # 这里直接丢掉：读进来一次，下次保存就不会再写回去。
                    continue
                base = dict(out["engines"].get(eid) or {"on": False, "keys": []})
                base.update(ecfg)
                out["engines"][eid] = base
        elif k == "order" and isinstance(v, list):
            out["order"] = [str(x) for x in v if str(x) in ENGINES]
        else:
            out[k] = v
    out["order"] = _merge_order(out["order"])
    return out


def save_config(patch: dict) -> dict:
    cur = config()
    for k, v in (patch or {}).items():
        if k == "engines" and isinstance(v, dict):
            for eid, ecfg in v.items():
                if eid not in ENGINES:
                    continue
                base = dict(cur["engines"].get(eid) or {"on": False, "keys": []})
                if isinstance(ecfg, dict):
                    base.update(ecfg)
                    cur["engines"][eid] = base
        elif k == "order" and isinstance(v, list):
            # 保存时同样补齐：否则每个用户手动保存一次，刚补进来的引擎又丢了。
            # 想停用它请关 `on`，而不是从 order 里删 —— 删了下次读还会补回来。
            cur["order"] = _merge_order([str(x) for x in v if str(x) in ENGINES])
        elif k == "consent":
            cur["consent"] = bool(v)
    _write_json(CONF_FILE, cur)
    return cur


def glossary() -> dict:
    d = _read_json(GLOS_FILE, DEFAULT_GLOSSARY)
    return d or dict(DEFAULT_GLOSSARY)


def save_glossary(d: dict) -> dict:
    clean = {str(k): str(v) for k, v in (d or {}).items() if str(k).strip() and str(v).strip()}
    _write_json(GLOS_FILE, clean)
    return clean


def memory() -> dict:
    return _read_json(MEMO_FILE, {})


def save_memory(d: dict) -> None:
    # 上限保护：无限长下去会变成几 MB 的 json，每次启动都要读
    items = list(d.items())
    if len(items) > 8000:
        items = items[-8000:]
    _write_json(MEMO_FILE, dict(items))


def memo_key(src: str) -> str:
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]


# ---------------- 标签保护 ----------------
def protect(text: str) -> tuple[str, list[str]]:
    """把 <font …> 这类标签换成 [0] [1]，翻完再换回来。

    实测如果不做这一步，MyMemory 会把标签挪到别的词上，
    游戏里就是颜色标错位置或者干脆标在空串上。
    """
    tags: list[str] = []

    def sub(m: re.Match) -> str:
        tags.append(m.group(0))
        return " [%d] " % (len(tags) - 1)

    return TAG.sub(sub, text), tags


def _variants(i: int) -> tuple[str, ...]:
    """引擎可能把 [0] 改成全角，三种形态都认。"""
    return ("[%d]" % i, "［%d］" % i, "〔%d〕" % i, "【%d】" % i, "[ %d ]" % i)


def find_token(s: str, i: int, start: int = 0) -> int:
    best = -1
    for v in _variants(i):
        p = s.find(v, start)
        if p >= 0 and (best < 0 or p < best):
            best = p
    return best


def restore(q: str, tags: list[str]) -> str:
    out = q
    for i, t in enumerate(tags):
        for v in _variants(i):
            out = out.replace(v, t)
    # 两个引擎都会在标签内侧吐多余空格：<font …> PRILER </font>
    out = re.sub(r"(<[^/!][^<>]*>)\s+", r"\1", out)      # 开标签之后
    out = re.sub(r"\s+(</[^<>]*>)", r"\1", out)          # 闭标签之前
    # protect() 给占位符两侧各留了一个空格（方便引擎把它当整词），
    # 还原后这些空格会留下来 —— `by [0] PRILER [1]` 变成 `by  <font>…`。
    # 译文里连续空格没有意义（菜单文字不会靠空格排版），收成一个。
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out.strip()


def tags_ok(q_src: str, q_dst: str, n: int) -> tuple[bool, str]:
    """标签是否还在、顺序对不对、有没有被掏空。

    掏空是实测到的真实失败：原文 `Auto equip [0]DURING CUTSCENES[1]`，
    译文 `过场动画期间自动装备[0][1]` —— 标签都在，但中间空了，
    游戏里就是一段没有颜色的空文本。
    """
    if n == 0:
        return True, ""
    pos = -1
    ps: list[int] = []
    pd: list[int] = []
    for i in range(n):
        a = find_token(q_src, i, pos + 1)
        b = find_token(q_dst, i, pos + 1)
        if b < 0:
            return False, "标签丢失或顺序被打乱"
        ps.append(a if a >= 0 else 0)
        pd.append(b)
        pos = b
    for i in range(n - 1):
        between_src = q_src[ps[i]:ps[i + 1]]
        between_dst = q_dst[pd[i]:pd[i + 1]]
        # 原文两标签之间有内容，译文对应位置却是空的 → 标签被掏空
        if len(TAG.sub("", between_src).strip(" []0123456789")) > 0 and \
           len(re.sub(r"[\[\]0-9\s]", "", between_dst)) == 0:
            return False, "标签里的内容被挪到了标签外面"
    return True, ""


# ---------------- 标签槽位 ----------------
# 为什么不能只把 <font> 和 </font> 各换成一个占位符
# --------------------------------------------------
# 那样必然在「语序变化」时失败。实测：
#   原文  Auto unequip <font color="#F07427">IN INTERIORS</font>
#   保护  Auto unequip [0] IN INTERIORS [1]
#   译文  在室内 [1] 中自动卸下 [0]
# 英文语序里标签对在句尾，中文语序要挪到句中，单符号占位符被引擎当成
# 两个无关的数字重排了，还原出来是 `在室内</font>中自动卸下<font …>` —— 惨不忍睹。
#
# 所以把**整个标签对**当成一个槽位：
#   骨架  Auto unequip {0}
#   译文  {0}时自动卸下        ← 位置由引擎按中文语序决定，这才是对的
#   槽位  IN INTERIORS → 室内
#   合并  <font color="#F07427">室内</font>时自动卸下
SLOT = re.compile(r"(<font\b[^>]*>)([^<>]*)(</font>)", re.I)
_SLOT_TOKEN = re.compile(r"[\{｛［\[]\s*(\d+)\s*[\}｝］\]]")


def split_slots(text: str) -> tuple[str, list[tuple[str, str, str]]]:
    """把成对的 <font>…</font> 换成 {0} {1}，返回 (骨架, [(开标签, 内容, 闭标签)])。"""
    slots: list[tuple[str, str, str]] = []

    def rep(m: re.Match) -> str:
        slots.append((m.group(1), m.group(2), m.group(3)))
        return "{%d}" % (len(slots) - 1)

    return SLOT.sub(rep, text), slots


def slot_order_ok(dst: str, n: int) -> bool:
    """骨架译文里的 {0} {1} 必须齐全且顺序不变 —— 顺序一乱，内容就张冠李戴。"""
    seen = [int(x) for x in _SLOT_TOKEN.findall(dst)]
    return seen == list(range(n))


def place_slots(dst: str, slots: list[tuple[str, str, str]],
                contents: list[str]) -> str:
    """把槽位内容塞回骨架，开闭标签沿用原文的。"""
    out = dst
    for i, (op, body, cl) in enumerate(slots):
        tok = re.compile(r"[\{｛［\[]\s*%d\s*[\}｝］\]]" % i)
        text = contents[i] if i < len(contents) and contents[i] else body
        out = tok.sub(lambda _m, op=op, cl=cl, text=text: op + text + cl, out)
    return out


# ---------------- 质量判定 ----------------
def _squash(s: str) -> str:
    return _ALNUM.sub("", s.lower())


def looks_untranslated(src: str, dst: str) -> bool:
    """译文其实就是原文（大小写/标点不同）→ 等于没翻。

    MyMemory 对没把握的句子会直接退回大写英文，HTTP 却是 200。
    """
    a = _squash(TAG.sub("", src))
    b = _squash(TAG.sub("", dst))
    return bool(a) and a == b


def lookup_phrase(text: str, table: dict | None = None) -> str:
    """整词查术语表（忽略大小写与首尾标点）。短语命中就不必问网络了。"""
    table = table if table is not None else glossary()
    k = text.strip().strip(".!:;,，。；：").lower()
    if not k:
        return ""
    for en, zh in table.items():
        if en.strip().lower() == k:
            return zh
    return ""


# 骨架里出现这些错译时按规则改回来。只修「确凿且高发」的：
#   Auto equip → 汽车装备（auto 被当成汽车，Google 和 MyMemory 都会犯）
#   Mod by X / Y → 由 X 改装 / Y、按 X 修改 / Y（原文是「作者」，不是动作）
_FIX_WORDS = [
    (r"汽车装备", "自动装备"),
    (r"汽车穿|汽车装卸", "自动穿"),
    (r"汽车卸下|汽车卸装", "自动卸下"),
    (r"^汽车", "自动"),
    (r"[由按]\s*(<font\b[^>]*>[^<]*</font>)\s*[改装修改]{2}", r"作者 \1"),
    (r"^修改为\s*", "作者 "),

    # —— 同一句英文必须落成同一个中文 ——
    # 逐条独立翻的必然结果：同一个 "Auto unequip" 会产出「自动卸下」「自动解除装备」
    # 「自动取消装备」三种写法，同一个 "Equip back" 会产出「装备回来」「恢复装备」
    # 「返回装备」。单看每条都对，摆在一个菜单里就是明摆着的机翻。
    (r"自动解除装备|自动取消装备|自动解除|自动卸装", "自动卸下"),
    (r"自动取消", "自动卸下"),
    (r"恢复装备|返回装备|装备回来|穿回来", "重新装备"),

    # "visits" 被直译进来变成「次」「访问」：Unequip during {BARBER} visits
    # → 「理发次时卸下」。中文不这么说话。
    (r"(?<=[^\s])次访问后", "后"),
    (r"(?<=[^\s])次访问时", "时"),
    (r"(?<=[^\s])次访问", ""),
    (r"(?<=[^\s])次时", "时"),
    (r"(?<=[^\s])次后", "后"),

    # "during X weather" → 「在X天气期间」。中文说「下雨时」。
    # 注意先做空格归一（fix_wording 里 squeeze 在最前），所以这里不用管空格。
    (r"天气期间", "时"),
    (r"天气天气", "天气"),
    (r"(?<=[\u4e00-\u9fff>])期间", "时"),

    # Unequip 的其它直译形态。菜单里统一说「卸下」。
    (r"取消装备|解除装备|卸下装备", "卸下"),

    # "Mod by X / Other" → 「由X /其他修改」：斜杠后面是菜单分组名，
    # 那个「修改」是引擎把 "Mod by" 拆错了留下的尾巴。
    (r"/\s*([\u4e00-\u9fff]{2,6})(?:修改|更改)$", r"/\1"),

    # 名词口径统一：同一个模组里 斗篷/斗篷 和 斗篷/披风 不能混着来。
    (r"斗篷/斗篷", "斗篷/披风"),
    (r"口罩", "面具"),
]

# "Animate cloaks/capes" 被翻成「动画斗篷/斗篷」—— 中文该说「斗篷/披风动画」。
# 这个只能在整句层面判断（有前缀才挪到后缀），不能写成通用替换，
# 否则任何以汉字结尾的句子都会被追加一个「动画」。
_ANIMATE = re.compile(r"^动画(?=[\u4e00-\u9fff])")


def fix_wording(dst: str) -> str:
    """按规则把「逐条翻出来的各说各话」收敛成一套说法。

    先做空格归一：中文不用空格分词，而引擎常吐出
    `在 <font>理发</font> 次访问期间`——空格夹在中间会让后面所有
    「紧邻 次」这类判定失效。同一个源句两次翻译的句式也会变，
    所以判定不能依赖某一个具体句式。
    """
    out = squeeze(dst)
    for pat, rep in _FIX_WORDS:
        out = re.sub(pat, rep, out)
    if _ANIMATE.match(out):
        out = _ANIMATE.sub("", out) + "动画"
    return out


def correct(dst: str, table: dict | None = None) -> str:
    """术语后校正：译文里残留的英文术语换成标准译名。

    只做这一个方向 —— 把中文错译改回来（比如「标志」→「法印」）太容易误伤，
    不做。

    「中英混杂」是常态（`由 PRILER 修改`、`昆特牌 Gwent 设置`）。
    标准译名已经在句子里的时候不重复插入，直接把多余的英文词删掉。
    """
    table = table if table is not None else glossary()
    out = dst
    for en, zh in table.items():
        if not en or not zh:
            continue
        pat = r"\b%s\b" % re.escape(en)
        if not re.search(pat, out, re.I):
            continue
        if zh in out:
            out = re.sub(pat + r"[ \t]*", "", out, flags=re.I)
        else:
            out = re.sub(pat, zh, out, flags=re.I)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


# ---------------- 超长时缩句 ----------------
# 等长写入是硬约束：译文一个字节都不能多。实测 34 条菜单文字里有 15 条
# 机器译文超出预算，多数只差 2~8 字节（1~3 个汉字）。
#
# 机器翻译天然写「完整句式」——"在拳战期间取消装备"，而菜单项需要「电报体」——
# "拳斗时卸下"。这组规则就是把前者压成后者。只在超预算时才用，
# 压过的句子一定不如原译自然，所以结果会标注「已缩短」，用户能看到并改。
_SHORTEN = [
    (r"在\s*(.+?)\s*期间", r"\1时"),
    (r"(.+?)\s*期间", r"\1时"),
    (r"的时候", "时"),
    (r"自动取消装备|自动解除装备", "自动卸下"),
    (r"取消装备|解除装备|卸下装备", "卸下"),
    (r"自动装备", "自动穿"),
    (r"重新装备|装备回来", "再穿"),
    (r"强制无动画", "无动画"),
    (r"允许", "可"),
    (r"访问时", "时"),
    (r"当\s*(.+?)\s*时", r"\1时"),
    (r"\s+/\s+", "/"),                  # " / " → "/"，白扔 2 字节
]

# 还是超长时，把「括号补充说明」整段拿掉。
# 这些是 <font size="17">(better turn on)</font> 这类"更好用"的提示，
# 去掉不影响功能，但会让用户少一条信息 —— 所以结果里标注「已省略说明」。
_PAREN_NOTE = re.compile(r"<font\s+size=[\"']?\d+[\"']?>\s*[（(][^）)]*[）)]\s*</font>")

# 中文之间、中文与标签之间的空格一律不要 —— 中文不用空格分词，
# 每个空格都是白扔的字节。这一步是无损的，先做它。
_CJK_CLASS = "\u4e00-\u9fff"


def squeeze(dst: str) -> str:
    out = re.sub(r"(?<=[%s])\s+(?=<)" % _CJK_CLASS, "", dst)
    out = re.sub(r"(?<=>)\s+(?=[%s])" % _CJK_CLASS, "", out)
    out = re.sub(r"(?<=[%s])\s+(?=[%s])" % (_CJK_CLASS, _CJK_CLASS), "", out)
    out = re.sub(r">\s+<", "><", out)        # 两个标签之间的空白，中文里没有意义
    out = re.sub(r"\s+/\s+", "/", out)
    out = re.sub(r"(?<=>)\s+/", "/", out)    # 「作者 <font>PRILER</font> /其他」→ 去掉斜杠前的空格
    return out.strip()


def shorten(dst: str, budget_bytes: int) -> tuple[str, bool, bool]:
    """把译文压到预算内。返回 (文本, 是否放得下, 是否省略过说明)。

    顺序：无损清空格 → 逐轮套缩句规则 → 实在不行才删括号说明。
    标签里的内容是原文的一部分（颜色标记的文字），挖出来保护，不参与替换。
    """
    if budget_bytes <= 0:
        return dst, False, False
    q, tags = protect(dst)
    out = q
    for _ in range(6):
        cand = squeeze(restore(out, tags))
        if len(cand.encode("utf-8")) <= budget_bytes:
            return cand, True, False
        before = out
        for pat, rep in _SHORTEN:
            out = re.sub(pat, rep, out)
        if out == before:
            break
    # 最后一招：拿掉括号补充说明。
    # 必须先 restore 再删 —— 此时标签还是 [0] [1] 占位符，正则匹配不到 <font size=…>
    cand = squeeze(_PAREN_NOTE.sub("", restore(out, tags)))
    if len(cand.encode("utf-8")) <= budget_bytes:
        return cand, True, True
    return cand, len(cand.encode("utf-8")) <= budget_bytes, True


# ---------------- 引擎 ----------------
class EngineError(RuntimeError):
    """这个引擎这次没给出可用译文。调用方应该换下一个。"""


class Engine:
    id = ""
    label = ""
    needs_key = False
    # 实际会连的域名。首次同意那张说明要把它原样给用户看，
    # 所以写在这里，而不是让界面自己猜。
    target = ""

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}
        self.fails = 0           # 连续失败次数，到阈值本轮让位
        self.used: dict[str, int] = {}   # 每个 Key 用过几次，用来轮换

    def ready(self) -> tuple[bool, str]:
        return True, ""

    def translate(self, text: str, key: str | None = None) -> str:
        raise NotImplementedError

    # 子类用同一个 urllib 封装，统一超时与错误
    def _send(self, req):
        """GET / POST 共用：一个超时、一套错误文案，别让两种请求各说各话。"""
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise EngineError("接口返回 %s" % e.code) from None
        except urllib.error.URLError as e:
            raise EngineError("连不上（%s）" % (getattr(e, "reason", "") or "网络不通")) from None
        except (TimeoutError, OSError) as e:
            raise EngineError("超时（%s）" % e) from None
        except json.JSONDecodeError:
            raise EngineError("返回的不是 JSON") from None

    def _get(self, url: str):
        req = urllib.request.Request(url, headers={
            # 版本号从 appinfo 取，别写死 —— 之前写死成 1.2，服务方看到的一直是旧版本
            "User-Agent": f"Waass/{APP_VERSION}",
            "Accept": "application/json,text/plain,*/*",
        })
        return self._send(req)

    def _post(self, url: str, payload: dict):
        """JSON POST。

        刻意**不带** Authorization：见过一个端点（Index-Translate）只要收到鉴权头，
        哪怕是占位符，也直接 412 拒掉。匿名能用的接口就老老实实匿名，
        也不要伪装成浏览器的 UA —— 服务方该看到的版本信息如实给。
        """
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "User-Agent": f"Waass/{APP_VERSION}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        return self._send(req)


class MyMemoryEngine(Engine):
    id = "mymemory"
    label = "MyMemory"
    target = "mymemory.translated.net"

    def ready(self) -> tuple[bool, str]:
        return True, ""      # 免 Key；填邮箱只是提额度

    def translate(self, text: str, key: str | None = None) -> str:
        # 单次查询上限 500 字节，超过接口直接报错，先说清楚
        if len(text.encode("utf-8")) > 480:
            raise EngineError("这句太长（超过接口 500 字节上限）")
        params = {"q": text, "langpair": "en|zh-CN"}
        email = str(self.cfg.get("email") or "").strip()
        if email:
            params["de"] = email
        d = self._get("https://api.mymemory.translated.net/get?" + urllib.parse.urlencode(params))
        status = d.get("responseStatus")
        data = d.get("responseData") or {}
        out = str(data.get("translatedText") or "").strip()
        if str(status) not in ("200", "None") and not out:
            raise EngineError("接口报错（%s）" % status)
        if not out:
            raise EngineError("返回了空译文")
        return out


_IT_URL = "https://index-translate.bilibili.com/v1/chat/completions"
# 模型名必须写全。实测传 "index-translate" 直接 400「未知模型」。
# 同一端点还挂着 2B / 35B-A3B / Index-Nailong-9B，菜单短句用 9B 就够，
# 换别的只是多一种变量，没有收益。
_IT_MODEL = "Index-Translate-9B"

# LLM 不是翻译 API，它需要一个「只给译文」的指令；占位符要单独交代，
# 否则它会把 [0] {0} 当成正文改写掉，标签结构直接崩。
_IT_PROMPT = (
    "把下面这条游戏菜单文字翻译成简体中文。\n"
    "只输出译文本身：不要解释、不要加引号、不要附上原文。\n"
    "原文里的 [0]、{0} 是占位符，必须原样保留，位置按中文语序安排。\n"
    "原文："
)

# 模型「多说一句」的几种开场白。中英文都认 —— 它有时整段答英文。
_PREFIX = re.compile(
    r"^\s*(?:译文|翻译|中文译文|简体中文|中文|译名|翻译结果|输出|"
    r"translation|translated text|chinese|output)\s*[:：]\s*", re.I)
# 中英文引号、直角引号、书名号：它包哪一层都见过
_QUOTES = "\"'“”‘’「」『』《》"


def _tidy_reply(reply: str, src: str = "") -> str:
    """把模型的回复收成一行纯译文；收不出可用的就抛 EngineError。

    实测同一句原文，它这样回过三种（都是 HTTP 200、finish_reason: stop）：

        "译文： 自动装备时强制不播放动画"        ← 引号 + 前缀
        译文：\\n自动装备时强制不播放动画          ← 前缀自己占一行
        自动装备时强制不播放动画\\n\\n说明：……     ← 正文后面跟一段解释

    规则：去首尾引号 → 去掉「译文：」类前缀 → 多行取第一行有内容的。
    取第一行而不是拼起来，是因为第二行起基本都是解释，拼进来会把译文撑长，
    等长写入时必然超预算。代价是模型真把一句译文断成两行时只剩前半句 ——
    菜单文字本来就该是一行，这个取舍划算。

    清洗后为空、或者清洗后还是原文（见 `looks_untranslated`），都算这次没翻成：
    空串和英文原样写进语言表，用户看到的就是「翻了但没翻」，不如换下一个引擎。
    """
    out = ""
    for line in str(reply or "").splitlines():
        t = line.strip().strip(_QUOTES).strip()
        t = _PREFIX.sub("", t).strip().strip(_QUOTES).strip()
        if t:
            out = t
            break
    if not out:
        raise EngineError("返回了空译文")
    if src and looks_untranslated(src, out):
        raise EngineError("把原文原样退回了（等于没翻）")
    return out


class IndexTranslateEngine(Engine):
    """B站 Index-Translate：OpenAI 兼容的 chat/completions，匿名、免 Key。

    实测踩出来的三个坑（都已处理，改之前先读一遍）：

    1. 模型名 —— 必须 `Index-Translate-9B`，只写 `index-translate` → 400「未知模型」；
    2. 鉴权头 —— 带任何 `Authorization`（哪怕占位符）→ 412「request was banned」，
       所以 `_post()` 里根本不设这个头，匿名请求；
    3. 返回形态 —— 它是 LLM，会把「译文：」或一句解释一起吐出来，必须清洗，
       见 `_tidy_reply`。

    端点没有配额承诺，也没有文档承诺长期存在，所以只是链上的第一站不是唯一一站：
    它挂了，`_ask()` 会自动落到 MyMemory。
    """
    id = "indextranslate"
    label = "Index-Translate（B站）"
    needs_key = False
    target = "index-translate.bilibili.com"

    def ready(self) -> tuple[bool, str]:
        return True, ""      # 匿名可用，不需要用户填任何东西

    def translate(self, text: str, key: str | None = None) -> str:
        d = self._post(_IT_URL, {
            "model": _IT_MODEL,
            "messages": [{"role": "user", "content": _IT_PROMPT + text}],
            # 不关思考会先吐一段推理过程：既慢，又会混进要清洗的正文里
            "chat_template_kwargs": {"enable_thinking": False},
        })
        try:
            raw = d["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise EngineError("返回结构看不懂") from None
        return _tidy_reply(str(raw), text)


class BaiduEngine(Engine):
    """百度翻译开放平台。国内最稳，免费额度够翻几万条；要用户自己申请 appid 和密钥。"""
    id = "baidu"
    label = "百度翻译"
    needs_key = True
    target = "fanyi-api.baidu.com"

    def ready(self) -> tuple[bool, str]:
        if not str(self.cfg.get("appid") or "").strip():
            return False, "还没填 appid"
        if not self.cfg.get("keys"):
            return False, "还没填密钥"
        return True, ""

    def translate(self, text: str, key: str | None = None) -> str:
        appid = str(self.cfg.get("appid") or "").strip()
        secret = (key or (self.cfg.get("keys") or [""])[0]) or ""
        if not appid or not secret:
            raise EngineError("没配 appid / 密钥")
        salt = str(int(time.time() * 1000) % 100000)
        sign = hashlib.md5((appid + text + salt + secret).encode("utf-8")).hexdigest()
        url = "https://fanyi-api.baidu.com/api/trans/vip/translate?" + urllib.parse.urlencode({
            "q": text, "from": "en", "to": "zh", "appid": appid, "salt": salt, "sign": sign,
        })
        d = self._get(url)
        if d.get("error_code"):
            raise EngineError("接口报错 %s" % d.get("error_code"))
        try:
            out = "".join(x["dst"] for x in d["trans_result"]).strip()
        except (KeyError, TypeError):
            raise EngineError("返回结构看不懂") from None
        if not out:
            raise EngineError("返回了空译文")
        return out


ENGINES: dict[str, type[Engine]] = {
    "indextranslate": IndexTranslateEngine,
    "mymemory": MyMemoryEngine,
    "baidu": BaiduEngine,
}


def build_chain(cfg: dict | None = None) -> list[Engine]:
    cfg = cfg or config()
    out: list[Engine] = []
    for eid in cfg.get("order") or []:
        cls = ENGINES.get(eid)
        if not cls:
            continue
        ecfg = (cfg.get("engines") or {}).get(eid) or {}
        if not ecfg.get("on", True):
            continue
        eng = cls(ecfg)
        ok, _why = eng.ready()
        if ok:
            out.append(eng)
    return out


def targets(cfg: dict | None = None) -> list[dict]:
    """这次真会发请求的那几个服务，给「首次同意」那张说明用。

    列的是 build_chain() 的结果，不是整张注册表 —— 没开、没配 Key 的引擎
    不会收到任何东西，写进说明里只会让人以为发得比实际多。
    """
    return [{"id": e.id, "label": e.label, "target": e.target}
            for e in build_chain(cfg)]


def probe(cfg: dict | None = None, text: str = "Auto equip force no animation") -> list[dict]:
    """「测一下」：每个引擎发一句真实菜单文字，回报延迟和译文。"""
    cfg = cfg or config()
    rows: list[dict] = []
    for eid, cls in ENGINES.items():
        ecfg = (cfg.get("engines") or {}).get(eid) or {}
        eng = cls(ecfg)
        ok, why = eng.ready()
        if not ok:
            rows.append({"id": eid, "label": eng.label, "ok": False, "ms": 0,
                         "dst": "", "why": why})
            continue
        t0 = time.time()
        try:
            dst = eng.translate(text)
            rows.append({"id": eid, "label": eng.label, "ok": True,
                         "ms": int((time.time() - t0) * 1000), "dst": dst, "why": ""})
        except EngineError as e:
            rows.append({"id": eid, "label": eng.label, "ok": False,
                         "ms": int((time.time() - t0) * 1000), "dst": "", "why": str(e)})
    return rows


# ---------------- 引擎链执行 ----------------
def _ask(text: str, engines: list[Engine], memo: dict,
         allow_memo: bool = True) -> tuple[str, str, str]:
    """问引擎链要一句译文。返回 (译文, 引擎, 失败原因)。

    只看这一句：网络、超时、原样退回。标签结构由调用方负责校验 ——
    骨架和槽位内容要校验的东西不一样。
    """
    if allow_memo and memo is not None:
        hit = memo.get(memo_key(text))
        if hit and hit.get("dst"):
            return hit["dst"], "memory", ""

    last_why = "没有可用的翻译引擎（检查网络，或到设置里配一个）"
    tried = 0
    for eng in engines:
        if eng.fails >= FAILS_BEFORE_SKIP:
            continue
        tried += 1
        key = None
        keys = eng.cfg.get("keys") or []
        if keys:
            key = min(keys, key=lambda k: eng.used.get(k, 0))    # 最久没用过的
        try:
            raw = eng.translate(text, key=key)
        except EngineError as e:
            eng.fails += 1
            last_why = "%s：%s" % (eng.label, e)
            continue
        except Exception as e:                       # noqa: BLE001 引擎不该把主流程带崩
            eng.fails += 1
            last_why = "%s：%s" % (eng.label, e)
            continue

        eng.fails = 0
        if key:
            eng.used[key] = eng.used.get(key, 0) + 1
        raw = (raw or "").strip()
        if not raw:
            last_why = "%s 返回了空译文" % eng.label
            continue
        if looks_untranslated(text, raw):
            last_why = "%s 把原文原样退回了（等于没翻）" % eng.label
            continue
        if allow_memo and memo is not None:
            memo[memo_key(text)] = {"dst": raw, "engine": eng.id, "ts": int(time.time())}
        return raw, eng.id, ""

    if tried == 0:
        last_why = "所有引擎这次都不可用（连续失败已跳过），稍后再试"
    return "", "", last_why


def translate_one(text: str, engines: list[Engine], memo: dict,
                  glos: dict) -> dict:
    """翻一句。返回 {dst, engine, status, why}。

    status: ok / fail。标签坏了、原样退回都算这条没成 ——
    宁可空着让用户看到「没翻」，也不要写进去一句错的。
    """
    src = text

    # 整句就是术语表里的词，不用问网络
    hit = lookup_phrase(src, glos)
    if hit:
        return {"dst": hit, "engine": "glossary", "status": "ok", "why": ""}

    skel, slots = split_slots(src)

    if not slots:
        # 没有成对 tag，只有可能游离的 <br> 之类：整体翻，标签单独保护
        q_src, tags = protect(src)
        dst, eng, why = _ask(q_src, engines, memo)
        if not dst:
            return {"dst": "", "engine": "", "status": "fail", "why": why}
        ok, tag_why = tags_ok(q_src, dst, len(tags))
        if not ok:
            return {"dst": "", "engine": "", "status": "fail",
                    "why": "译文%s" % tag_why}
        return {"dst": correct(fix_wording(restore(dst, tags)), glos), "engine": eng,
                "status": "ok", "why": ""}

    # 有标签对：翻骨架 → 校验槽位顺序 → 逐个翻槽位内容 → 拼回去
    skel_dst, eng, why = _ask(skel, engines, memo)
    if not skel_dst:
        return {"dst": "", "engine": "", "status": "fail", "why": why}
    if not slot_order_ok(skel_dst, len(slots)):
        return {"dst": "", "engine": "", "status": "fail",
                "why": "%s 把标签的位置弄乱了（中文语序重排后对不上）" % (eng or "引擎")}

    # 骨架里有 by → 紧随其后的槽位是作者名，保留原文。
    # "作者 PRILER" 比 "作者 普里勒" 好，用户找 mod 时也是按英文名找。
    keep: set[int] = set()
    mb = re.search(r"\bby\b", skel, re.I)
    if mb:
        toks = _SLOT_TOKEN.findall(skel[mb.end():])
        if toks:
            keep.add(int(toks[0]))

    contents: list[str] = []
    for i, (_op, body, _cl) in enumerate(slots):
        plain = body.strip()
        if not plain or _CJK.search(plain) or i in keep:
            contents.append(body)          # 空 / 已经是中文 / 作者名，原样留着
            continue
        hit = lookup_phrase(plain, glos)
        if hit:
            contents.append(hit)           # 短语表命中，比引擎准也更短
            continue
        one, _e, _w = _ask(plain, engines, memo)
        contents.append(one or body)       # 槽位翻不出来就用原文，至少不串位

    merged = fix_wording(place_slots(skel_dst, slots, contents))
    return {"dst": correct(merged, glos), "engine": eng, "status": "ok", "why": ""}


def room_for(src: str, max_bytes: int) -> int:
    """这条译文最多能放几个汉字。

    必须扣掉标签占的字节 —— 之前按 max//3 算是错的：
    `Unequip during <font color="#F54B59">FIST FIGHT</font>` 预算 54，
    看着能放 18 个汉字，实际标签就占了 29 字节，只剩 25 字节 = 8 个汉字。
    """
    tag_bytes = sum(len(t.encode("utf-8")) for t in TAG.findall(src))
    return max(0, (max_bytes - tag_bytes) // 3)


def run(items: list[dict], on_progress=None, stop=None) -> list[dict]:
    """批量翻。items 每条要有 id / src / max(可选)。

    同一句原文只请求一次 —— Hood Slots 的 cn 和 zh 两个表里都有同一个 "Hoods"，
    没必要翻两遍。
    """
    engines = build_chain()
    memo = memory()
    glos = glossary()
    for e in engines:
        e.fails = 0
        e.used = {}

    out: list[dict] = []
    seen: dict[str, dict] = {}
    total = len(items)

    for idx, it in enumerate(items):
        if stop is not None and stop():
            out.append({**it, "dst": "", "engine": "", "status": "cancel", "why": "已取消"})
            continue
        src = str(it.get("src") or "")
        if src in seen:
            got = dict(seen[src])
            got["status"] = got["status"] + "-dup" if got["status"] == "ok" else got["status"]
        else:
            got = translate_one(src, engines, memo, glos)
            if got["status"] == "ok" and got["engine"] not in ("memory", "glossary"):
                memo[memo_key(src)] = {"dst": got["dst"], "engine": got["engine"],
                                       "ts": int(time.time())}
            seen[src] = got

        row = {**it, "dst": got["dst"], "engine": got["engine"],
               "status": got["status"], "why": got["why"]}

        # 长度检查：等长写入的硬上限。放不下的先自动缩句，
        # 缩完仍然放不下就如实标出来 —— 绝不截断，截断就是一屏乱码。
        mx = it.get("max") or 0
        if row["dst"] and mx:
            row["room"] = room_for(src, mx)
            used = len(row["dst"].encode("utf-8"))
            if used > mx:
                short, fits, cut_note = shorten(row["dst"], mx)
                used = len(short.encode("utf-8"))
                row["dst"] = short
                row["shortened"] = True
                if fits:
                    row["why"] = "已缩写才放进原文长度" + ("，省略了括号里的说明" if cut_note else "")
                else:
                    row["status"] = "long"
                    row["why"] = ("还差 %d 字节（约 %d 个汉字），请在表里改短"
                                  % (used - mx, (used - mx + 2) // 3))
            row["used"] = used
        out.append(row)

        if on_progress is not None:
            on_progress(idx + 1, total, row)

    if memo:
        save_memory(memo)
    return out
