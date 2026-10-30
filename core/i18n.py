"""界面文案的中英双语支持。

两种语言共用一套键，各自一个 JSON：web/i18n/zh.json、web/i18n/en.json。
后端用 t() 取句子；前端拿到的是同一份表（服务端渲染首页时内联进 <script>），
所以两边文案永远同源，不会出现「界面英文、提示弹中文」。

三条纪律，改文案时别破：
1. 键名一律「命名空间.用途」，如 install.preview_title。不要复用语义会漂的短键。
2. 一句话一条，**不许用字符串拼接**。中英语序不同，`"已禁用 " + n + " 个"`
   拼成英文必然是病句。需要插变量就用 {name} 占位，交给 t() 替换。
3. 模块导入阶段不许调 t()（那时语言还没定下来），只能在函数体里调。
   `LABEL = t("x")` 这种写在模块顶层的，语言一切换就永远冻在启动时的那个。

语言的三种取值：`auto`（跟随系统，默认）、`zh`、`en`。存在 data/config.json 的
`lang` 字段里；字段不存在按 auto 处理 —— 老用户的 config.json 里没有这一项。
"""

from __future__ import annotations

import json
import os

from core.paths import CONFIG_FILE, RES_DIR, load_config, save_config

SUPPORTED = ("zh", "en")
FALLBACK = "zh"
# <html lang> 用规范写法，不是我们内部的短码。英文只写 en，别写 en-US ——
# 用户可能在英国/澳洲，写死区域反而会被某些输入法/断词规则当真。
HTML_LANG = {"zh": "zh-CN", "en": "en"}
CATALOG_DIR = RES_DIR / "web" / "i18n"

# 当前语言 + 已载入的键表。单进程单用户，全局状态够用；
# 每个请求进来读一次 i18n.current()，不要缓存到别处。
_state: dict = {"lang": "", "cat": {}}
_cache: dict = {}


def _read(lang: str) -> dict:
    """读一份键表。下划线开头的键是元信息，不参与查询。"""
    if lang in _cache:
        return _cache[lang]
    cat: dict = {}
    try:
        raw = json.loads((CATALOG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    if isinstance(raw, dict):
        cat = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, str)}
    _cache[lang] = cat
    return cat


def system_lang() -> str:
    """系统界面语言是不是中文。

    只分两类：中文 → zh，其它一切 → en。俄语、波兰语、日语用户拿到的也是
    英文界面 —— 巫师 3 玩家遍全球，英文是唯一说得过去的兜底（而且他们宁可用
    英文，也不想看一堆看不懂的方块字）。

    Windows 上问内核拿 LANGID，比读环境变量可靠：中文系统的 LANG 可能是空的。
    """
    try:
        import ctypes

        langid = int(ctypes.windll.kernel32.GetUserDefaultUILanguage())
        return "zh" if (langid & 0x3FF) == 0x04 else "en"
    except Exception:                                  # noqa: BLE001
        pass
    for key in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = (os.environ.get(key) or "").strip().lower()
        if v:
            return "zh" if v.startswith("zh") else "en"
    return "en"


def resolve(pref) -> str:
    """把配置里的值（可能是空的、也可能是别人手改的乱码）落成 zh / en。"""
    p = str(pref or "").strip().lower()
    if p in SUPPORTED:
        return p
    return system_lang()


def set_lang(pref) -> str:
    lang = resolve(pref)
    if lang != _state["lang"]:
        _state["lang"] = lang
        _state["cat"] = _read(lang)
    return lang


def current() -> str:
    if not _state["lang"]:
        set_lang(load_config().get("lang"))
    return _state["lang"]


def _cat() -> dict:
    if not _state["lang"]:
        current()
    return _state["cat"]


def t(_key: str, **params) -> str:
    """取当前语言的一句话。

    找不到不抛异常，直接把键名还回去 —— 界面上出现 `install.preview_title`
    比整页崩掉好，而且一眼能看出漏了哪条，测试也会抓。
    形参带下划线前缀：占位符里 `key` / `n` 是最常见的名字，调用方
    `i18n.t("act.op_priority", key=k)` 这种写法不该撞上内部参数名。
    """
    s = _cat().get(_key)
    if s is None:
        s = _read(FALLBACK).get(_key)
    if s is None:
        return _key
    for k, v in params.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def tn(_one: str, _many: str, _n, **params) -> str:
    """带单复数的句子。中文两份内容一样，英文按 n 选。

    只处理「1 和其它」这一档 —— 英文复数规则里这一档覆盖了本程序全部场景
    （可数个模组 / 个文件 / 处）。真要严格，得引 gettext，不值当。
    形参带下划线前缀，理由同 t()：调用方允许再传一个 n= 占位符。
    """
    key = _one if int(_n) == 1 else _many
    # 调用方可能把数量又当占位符传了一遍（tn(..., 3, n=3)）—— 以它为准，
    # 没传才注入内部的 _n，不然 t() 会收到两个 n。
    params.setdefault("n", _n)
    return t(key, **params)


def join(parts) -> str:
    """按当前语言把几个名字连成一串：中文顿号，英文逗号加空格。

    模组名列表、冲突对象列表这类「名字，名字，名字」都用它，
    别在业务代码里写死 `、` —— 英文界面会冒出全角顿号。
    """
    return ("、" if current() == "zh" else ", ").join(parts)


def quote(s: str) -> str:
    """按当前语言给名字加引号：中文『』，英文直引号。"""
    return f"『{s}』" if current() == "zh" else f"'{s}'"


def strings() -> dict:
    """当前语言的整份键表，服务端渲染首页时内联给前端。"""
    return dict(_cat())


def bootstrap() -> str:
    """启动时按 config.json 定语言。返回落定后的语言。"""
    return set_lang(load_config().get("lang"))


def save_pref(pref: str) -> str:
    """写回 config.json 并立即生效。

    只动 lang 一个键，其余原样保留 —— 这文件里还有 game_root、merger_exe、
    update_feed 等用户配置，整份重写等于把它们抹掉。
    """
    lang = resolve(pref)
    cfg = load_config()
    cfg["lang"] = lang if str(pref or "").strip().lower() in SUPPORTED else "auto"
    if CONFIG_FILE.is_file() or cfg:
        save_config(cfg)
    set_lang(cfg["lang"])
    return lang


def reload() -> None:
    """丢掉缓存重新读盘。改完 JSON 不用重启进程（开发时方便）。"""
    _cache.clear()
    lang = _state["lang"]
    _state["lang"] = ""
    set_lang(lang or load_config().get("lang"))
