"""Index-Translate 引擎的离线测试（不联网）。

要钉住的是三条实测踩出来的坑，而不是「能不能翻」：
1. 模型名必须是 `Index-Translate-9B`；
2. 请求里**不能**有 Authorization（带了就 412）；
3. 回复必须清洗：引号、`译文：`前缀、后面的解释段落都不能进语言表。

所有写盘都落在临时目录里；出网一律用 mock 拦掉，`urlopen` 换成桩函数，
断言连请求体、请求头都逐项检查 —— 只断言「没抛异常」是不够的。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

# 目录隔离要在 import core.* 之前做完：DATA_DIR 是 import 时求值的常量。
# 只设 WAASS_SANDBOX_DIR 不够 —— 配置会写进真实 data/，这里三个一起设。
_TMP = Path(tempfile.mkdtemp(prefix="waass-index-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_TMP)
os.environ["WAASS_DATA_DIR"] = str(_TMP / "data")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import translate as T                    # noqa: E402
from core.appinfo import APP_VERSION               # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


class _Resp:
    """够 urlopen 用的最小响应对象：with 一下，read() 拿字节。"""

    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, *a):
        return self._body


def ok_reply(text: str) -> bytes:
    """OpenAI 兼容格式的正常回复。"""
    return json.dumps({
        "choices": [{"message": {"role": "assistant", "content": text},
                     "finish_reason": "stop"}],
        "model": "Index-Translate-9B",
    }, ensure_ascii=False).encode("utf-8")


def main() -> int:
    print("[1] 注册表与默认链")
    check("新引擎在注册表里", T.ENGINES.get("indextranslate") is T.IndexTranslateEngine,
          str(sorted(T.ENGINES)))
    check("默认链首位是它", (T.DEFAULT_CONFIG.get("order") or [None])[0] == "indextranslate",
          str(T.DEFAULT_CONFIG.get("order")))
    check("默认是开着的",
          (T.DEFAULT_CONFIG["engines"].get("indextranslate") or {}).get("on") is True,
          str(T.DEFAULT_CONFIG["engines"]))
    eng = T.IndexTranslateEngine({})
    check("不需要 Key", eng.needs_key is False)
    check("ready() 直接可用", eng.ready() == (True, ""), str(eng.ready()))
    check("label 与域名对得上",
          (eng.label, eng.target) == ("Index-Translate（B站）", "index-translate.bilibili.com"),
          "%s / %s" % (eng.label, eng.target))
    ids = [e.id for e in T.build_chain(T.DEFAULT_CONFIG)]
    check("链里排第一（MyMemory 在后）", ids[:2] == ["indextranslate", "mymemory"], str(ids))
    tgt = T.targets(T.DEFAULT_CONFIG)
    check("首次同意清单自动带上它",
          tgt and tgt[0]["target"] == "index-translate.bilibili.com", str(tgt))

    print("\n[2] 请求体 / 请求头")
    seen: dict = {}

    hits: list[str] = []

    def _capture(req, timeout=None):
        hits.append(req.full_url)
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["body"] = json.loads((req.data or b"{}").decode("utf-8"))
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        seen["timeout"] = timeout
        return _Resp(ok_reply("自动装备时强制不播放动画"))

    with mock.patch("urllib.request.urlopen", _capture):
        got = T.IndexTranslateEngine({}).translate("Auto equip force no animation")

    check("打到 chat/completions", seen.get("url") == T._IT_URL, str(seen.get("url")))
    check("用的是 POST", seen.get("method") == "POST", str(seen.get("method")))
    check("模型名写全了", seen["body"].get("model") == "Index-Translate-9B",
          str(seen["body"].get("model")))
    check("关掉了 thinking",
          (seen["body"].get("chat_template_kwargs") or {}).get("enable_thinking") is False,
          str(seen["body"].get("chat_template_kwargs")))
    msgs = seen["body"].get("messages") or []
    check("只有一个 user 消息",
          len(msgs) == 1 and msgs[0].get("role") == "user", str(msgs))
    check("原文进了 prompt", "Auto equip force no animation" in str(msgs[0].get("content")),
          str(msgs[0].get("content"))[:120])
    check("没有 Authorization 头", "authorization" not in seen["headers"],
          str(sorted(seen["headers"])))
    check("UA 如实写版本、不伪装浏览器",
          seen["headers"].get("user-agent") == "Waass/%s" % APP_VERSION,
          str(seen["headers"].get("user-agent")))
    check("Content-Type 是 json",
          seen["headers"].get("content-type") == "application/json",
          str(seen["headers"].get("content-type")))
    check("超时沿用模块里的约定", seen.get("timeout") == T.TIMEOUT, str(seen.get("timeout")))
    check("正常回复原样返回", got == "自动装备时强制不播放动画", got)
    check("只发了一次请求", len(hits) == 1, str(hits))

    print("\n[3] 清洗")
    cases = [
        ('"译文： 自动装备时强制不播放动画"', "引号 + 前缀"),
        ("译文：\n自动装备时强制不播放动画", "前缀单独一行"),
        ("自动装备时强制不播放动画\n\n说明：这句是菜单里的开关文案。", "正文后跟解释"),
        ('“自动装备时强制不播放动画”', "中文引号"),
        ("Translation: 自动装备时强制不播放动画", "英文前缀"),
        ("  自动装备时强制不播放动画  \n", "首尾空白"),
    ]
    for raw, why in cases:
        try:
            got = T._tidy_reply(raw, "Auto equip force no animation")
        except T.EngineError as e:
            got = "<EngineError %s>" % e
        check("%-14s → 只留译文" % why, got == "自动装备时强制不播放动画", got)

    check("译文里的标点不动",
          T._tidy_reply("斗篷/披风动画。", "Animate cloaks") == "斗篷/披风动画。",
          T._tidy_reply("斗篷/披风动画。", "Animate cloaks"))
    check("占位符原样留着",
          T._tidy_reply("{0}时自动卸下", "Auto unequip {0}") == "{0}时自动卸下")

    for raw, why, src in [
        ("", "空回复", "Auto equip"),
        ("   \n\n ", "只有空白", "Auto equip"),
        ("译文：", "只有前缀", "Auto equip"),
        ("Auto equip force no animation", "英文原样退回", "Auto equip force no animation"),
        ("AUTO EQUIP FORCE NO ANIMATION!", "只改大小写和标点也算没翻",
         "Auto equip force no animation"),
    ]:
        try:
            got = T._tidy_reply(raw, src)
            check("%-16s 抛 EngineError" % why, False, "返回了 %r" % got)
        except T.EngineError as e:
            check("%-16s 抛 EngineError" % why, True)
            if why == "英文原样退回":
                check("  原因说得清楚", "原样" in str(e), str(e))

    print("\n[4] 失败路径都要变成 EngineError（不能把主流程带崩）")

    def _http_err(code):
        def f(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, code, "boom", {}, None)
        return f

    for code, why in ((400, "未知模型"), (412, "鉴权头被 ban"), (500, "服务端错")):
        with mock.patch("urllib.request.urlopen", _http_err(code)):
            try:
                T.IndexTranslateEngine({}).translate("Auto equip")
                check("HTTP %d 抛 EngineError" % code, False, "没抛")
            except T.EngineError as e:
                check("HTTP %d（%s）→ EngineError" % (code, why), str(code) in str(e), str(e))

    def _no_net(req, timeout=None):
        raise urllib.error.URLError("测试里不许出网")

    with mock.patch("urllib.request.urlopen", _no_net):
        try:
            T.IndexTranslateEngine({}).translate("Auto equip")
            check("连不上 → EngineError", False, "没抛")
        except T.EngineError as e:
            check("连不上 → EngineError", "连不上" in str(e), str(e))

    for body, why in (
        (b'{"choices": []}', "choices 是空的"),
        (b'{"choices": [{"message": {}}]}', "没有 content"),
        (b'not json at all', "返回的不是 JSON"),
        (ok_reply(""), "content 是空串"),
        (ok_reply("Auto equip force no animation"), "模型原样退回英文"),
    ):
        with mock.patch("urllib.request.urlopen", lambda req, timeout=None, _b=body: _Resp(_b)):
            try:
                T.IndexTranslateEngine({}).translate("Auto equip force no animation")
                check("%s → EngineError" % why, False, "没抛")
            except T.EngineError:
                check("%s → EngineError" % why, True)

    print("\n[5] 链上降级：它挂了要能落到 MyMemory")
    calls: list[str] = []

    def _two(req, timeout=None):
        calls.append(req.full_url)
        if "bilibili" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, 412, "banned", {}, None)
        return _Resp(b'{"responseData":{"translatedText":"\xe8\x87\xaa\xe5\x8a\xa8\xe8\xa3\x85\xe5\xa4\x87"},'
                     b'"responseStatus":200}')

    with mock.patch("urllib.request.urlopen", _two):
        dst, want, _why = T._ask("Auto equip", T.build_chain(T.DEFAULT_CONFIG), {})

    check("第一条就失败时换了下一个", len([c for c in calls if "bilibili" in c]) == 1, str(calls))
    check("最后由 MyMemory 给出结果", want == "mymemory" and dst == "自动装备",
          "%s / %s" % (dst, want))

    print("\n[6] 老配置迁移（升级前 order 里没有新引擎）")
    old_dir, old_file = T.DIR, T.CONF_FILE
    T.DIR, T.CONF_FILE = _TMP / "translate", _TMP / "translate" / "config.json"
    try:
        T.DIR.mkdir(parents=True, exist_ok=True)
        T.CONF_FILE.write_text(json.dumps({
            "order": ["mymemory"],
            "engines": {"mymemory": {"on": True, "keys": [], "email": ""}},
            "consent": True,
        }), encoding="utf-8")
        cfg = T.config()
        check("老顺序没被打乱", cfg["order"] == ["mymemory", "indextranslate"],
              str(cfg["order"]))
        check("新引擎自动补成默认开启",
              (cfg["engines"].get("indextranslate") or {}).get("on") is True,
              str(cfg["engines"].get("indextranslate")))
        check("consent 等用户设置不受影响", cfg.get("consent") is True, str(cfg))
        check("迁移不改文件（不强制落盘）",
              json.loads(T.CONF_FILE.read_text(encoding="utf-8"))["order"] == ["mymemory"],
              T.CONF_FILE.read_text(encoding="utf-8"))
        cfg2 = T.config()
        check("反复读不会重复追加", cfg2["order"] == ["mymemory", "indextranslate"],
              str(cfg2["order"]))

        T.save_config({"consent": True})
        saved = json.loads(T.CONF_FILE.read_text(encoding="utf-8"))
        check("用户一保存就落进文件",
              saved["order"] == ["mymemory", "indextranslate"], str(saved["order"]))
        check("保存后仍能读回同样的顺序",
              T.config()["order"] == ["mymemory", "indextranslate"],
              str(T.config()["order"]))
    finally:
        T.DIR, T.CONF_FILE = old_dir, old_file

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
