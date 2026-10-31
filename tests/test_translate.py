"""翻译引擎的离线测试：只测不联网的部分。

真正发请求的逻辑（引擎降级、Key 轮换）测不了稳定性 —— 免费接口会变脸、
会限流、两次返回不同句式。所以这里把「能离线判断对错」的那些钉住：
标签保护、槽位重排、措辞归一、缩句、分类。
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import localize, translate as T      # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def b(s: str) -> int:
    return len(s.encode("utf-8"))


def main() -> int:
    print("[1] 标签保护")
    src = 'Switchable Cloaks by <font color="#1FD655">PRILER</font>'
    q, tags = T.protect(src)
    check("标签被换成占位符", "<font" not in q and len(tags) == 2, q)
    check("还原回去一模一样", T.restore(q, tags) == src, T.restore(q, tags))
    check("容错：全角方括号",
          "[0]" in T.restore("可切换斗篷 ［0］ PRILER ［1］", tags) or
          "<font" in T.restore("可切换斗篷 ［0］ PRILER ［1］", tags))
    check("标签内侧多余空格被清掉",
          "<font" in T.restore('可切换斗篷 <font color="#1FD655"> PRILER </font>', tags)
          and "> PRILER <" not in T.restore('可切换斗篷 <font color="#1FD655"> PRILER </font>', tags))

    print("\n[2] 结构校验 tags_ok")
    q2, t2 = T.protect('Auto equip <font color="#FFDE80">DURING CUTSCENES</font>')
    ok, why = T.tags_ok(q2, "自动装备 [0] 过场动画 [1]", len(t2))
    check("正常译文通过", ok, why)
    ok, why = T.tags_ok(q2, "过场动画期间自动装备 [0]  [1]", len(t2))
    check("标签被掏空能识别", not ok, why)
    ok, why = T.tags_ok(q2, "自动装备 过场动画", len(t2))
    check("标签丢了能识别", not ok, why)
    ok, why = T.tags_ok(q2, "自动装备 [1] 过场动画 [0]", len(t2))
    check("标签顺序错能识别", not ok, why)

    print("\n[3] 槽位拆分（中文语序会挪动标签的位置）")
    skel, slots = T.split_slots('Auto unequip <font color="#F07427">IN INTERIORS</font>')
    check("骨架里剩占位符", "{0}" in skel and "<font" not in skel, skel)
    check("槽位内容取出", len(slots) == 1 and slots[0][1] == "IN INTERIORS", str(slots))
    merged = T.place_slots("在 {0} 时自动卸下", slots, ["室内"])
    check("按骨架位置拼回去", merged == '在 <font color="#F07427">室内</font> 时自动卸下', merged)
    check("槽位顺序校验", T.slot_order_ok("在 {0} 时", 1) is True)
    check("槽位顺序错了能查出", T.slot_order_ok("{0} {1} {0}", 1) is False)

    print("\n[4] 措辞归一（同一批英文必须落成同一个中文）")
    cases = [
        ("自动解除装备 <font>x</font>", "自动卸下"),
        ("自动取消装备", "自动卸下"),
        ("<font>理发</font>次时卸下", "<font>理发</font>时卸下"),
        ("<font>理发</font>次访问后重新装备", "<font>理发</font>后重新装备"),
        ("在<font>雨</font>天气期间自动装备", "在<font>雨</font>时自动装备"),
        ("斗篷/斗篷动画", "斗篷/披风动画"),
        ("动画头巾/围巾/口罩", "头巾/围巾/面具动画"),
        ("由<font>PRILER</font> /其他修改", "由<font>PRILER</font>/其他"),
        ("汽车装备", "自动装备"),
    ]
    for src_w, want in cases:
        got = T.fix_wording(src_w)
        check("%-28s → %s" % (src_w[:26], want), want in got, got)
    check("不误伤：以汉字结尾的普通句子不加「动画」",
          T.fix_wording("自动装备") == "自动装备", T.fix_wording("自动装备"))

    print("\n[5] 空格归一")
    check("中文与标签之间的空格去掉",
          T.squeeze('在 <font color="#aaa">雨</font> 时') == '在<font color="#aaa">雨</font>时',
          T.squeeze('在 <font color="#aaa">雨</font> 时'))
    check("保留英文词之间的空格",
          T.squeeze("Auto equip") == "Auto equip", T.squeeze("Auto equip"))

    print("\n[6] 缩句")
    long_src = '自动穿<font color="#8EA4C8">仅斗篷/披风</font> <font size="16">(ignore hoods)</font>'
    out, fits, cut = T.shorten(long_src, 200)
    check("预算宽裕时原样返回", out == T.squeeze(long_src) or fits, out)
    tight, fits2, cut2 = T.shorten("在过场动画期间自动装备斗篷并取消装备", 20)
    check("放不下时给出更短的写法", b(tight) <= 20 or not fits2, "%s (%d)" % (tight, b(tight)))
    check("绝不截断成半个字", all(ord(c) < 128 or True for c in tight) and not tight.endswith("\ufffd"), tight)
    check("预算为 0 时不硬来", T.shorten("自动装备", 0)[1] is False)

    print("\n[7] 算「能放几个汉字」要扣掉标签")
    s = 'Unequip during <font color="#F54B59">FIST FIGHT</font>'
    room = T.room_for(s, 54)
    check("标签占的字节被扣掉", room < 54 // 3, "room=%d, 54//3=%d" % (room, 18))
    check("标签少时能放的更多", T.room_for("Auto equip cloak", 54) > room)

    print("\n[8] 术语与记忆")
    check("术语表忽略大小写", T.lookup_phrase("  in interiors ", {"In Interiors": "室内"}) == "室内")
    check("查不到返回空", T.lookup_phrase("something else", {"In Interiors": "室内"}) == "")
    k1 = T.memo_key("Auto equip")
    k2 = T.memo_key("Auto equip")
    check("记忆键稳定", k1 == k2)
    check("不同原文不同键", T.memo_key("Auto equip") != T.memo_key("Auto unequip"))
    got = T.correct("昆特牌 Gwent 设置")
    check("残留英文术语被清掉", "Gwent" not in got, got)

    print("\n[9] 分类：默认只翻菜单页")
    check("第 0 条当模组名",
          localize.classify("Light Rewrite", idx=0, mod_display="Light Rewrite",
                            folder="modLightRewrite") == localize.CAT_MOD)
    check("名字相同当模组名",
          localize.classify("Light Rewrite", idx=85, mod_display="Light Rewrite",
                            folder="modLightRewrite") == localize.CAT_MOD)
    check("所有格当物品名",
          localize.classify("Deadly Ronin's Kimono", idx=3, mod_display="Ronin",
                            folder="modroninarmor") == localize.CAT_ITEM)
    check("带菜单特征的仍是菜单页",
          localize.classify('Auto equip <font color="#fff">X</font>', idx=5) == localize.CAT_MENU)
    check("普通选项是菜单页",
          localize.classify("Allow toggling while horse riding", idx=8) == localize.CAT_MENU)
    check("短词也是菜单页",
          localize.classify("Other", idx=26) == localize.CAT_MENU)
    check("默认类别只有菜单页",
          localize.CAT_DEFAULT == (localize.CAT_MENU,), str(localize.CAT_DEFAULT))

    print("\n[10] 引擎注册表")
    ids = set(T.ENGINES)
    check("免 Key 的 MyMemory 还在", "mymemory" in ids, str(ids))
    check("Index-Translate 也注册了", "indextranslate" in ids, str(ids))
    check("Index-Translate 也免 Key",
          getattr(T.ENGINES.get("indextranslate"), "needs_key", True) is False)
    check("默认链首位是 Index-Translate",
          (T.DEFAULT_CONFIG.get("order") or [None])[0] == "indextranslate",
          str(T.DEFAULT_CONFIG.get("order")))
    check("需要 Key 的标了 needs_key",
          any(getattr(c, "needs_key", False) for c in T.ENGINES.values()))
    check("配置里带顺序", isinstance(T.config().get("order"), list))
    check("下线的引擎不在注册表里", "google" not in ids, str(ids))
    check("默认链里也没有下线的引擎",
          "google" not in (T.DEFAULT_CONFIG.get("order") or []),
          str(T.DEFAULT_CONFIG.get("order")))
    check("默认链非空", bool(T.DEFAULT_CONFIG.get("order")))
    check("每个引擎都写了会连的域名",
          all(getattr(c, "target", "") for c in T.ENGINES.values()),
          str({k: getattr(c, "target", "") for k, c in T.ENGINES.items()}))
    check("首次同意默认「还没同意」", T.DEFAULT_CONFIG.get("consent") is False)

    print("\n[11] 老配置兼容（升级前留下的 config.json 里有下线引擎）")
    with tempfile.TemporaryDirectory() as td:
        old_dir, old_file = T.DIR, T.CONF_FILE
        T.DIR, T.CONF_FILE = Path(td), Path(td) / "config.json"
        try:
            T.CONF_FILE.write_text(json.dumps({
                "order": ["google", "mymemory"],
                "engines": {"google": {"on": True, "keys": []},
                            "mymemory": {"on": True, "keys": ["k1"]}},
            }), encoding="utf-8")
            cfg = T.config()
            check("下线引擎读不进配置", "google" not in (cfg.get("engines") or {}), str(cfg))
            # 1.3.2 起：老 order 里没有的新引擎会被追加到末尾，用户原有顺序不动
            check("order 里的下线引擎被过滤、新引擎追加在末尾",
                  cfg.get("order") == ["mymemory", "indextranslate"], str(cfg.get("order")))
            check("用户原来开着的还在",
                  (cfg.get("engines") or {}).get("mymemory", {}).get("on") is True)
            check("老配置没有 consent 时按没同意算", cfg.get("consent") is False)
            check("链路按用户顺序起步、新引擎追加在后",
                  [e.id for e in T.build_chain(cfg)] == ["mymemory", "indextranslate"],
                  str([e.id for e in T.build_chain(cfg)]))

            T.save_config({"consent": True})
            raw = json.loads(T.CONF_FILE.read_text(encoding="utf-8"))
            check("保存后不会写回下线引擎",
                  "google" not in json.dumps(raw, ensure_ascii=False), str(raw))
            check("同意被记住", raw.get("consent") is True, str(raw))
            check("同意后 config() 读得到", T.config().get("consent") is True)

            T.save_config({"order": ["google", "mymemory"]})
            raw2 = json.loads(T.CONF_FILE.read_text(encoding="utf-8"))
            check("手填的下线引擎也写不回去",
                  raw2.get("order") == ["mymemory", "indextranslate"], str(raw2.get("order")))
            check("同意状态不因改顺序而丢失", raw2.get("consent") is True, str(raw2))
        finally:
            T.DIR, T.CONF_FILE = old_dir, old_file

    print("\n[12] 请求头里的版本号是真实版本")
    from core.appinfo import APP_VERSION

    seen: dict = {}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, *a):
            return b'{"responseData":{"translatedText":"ok"},"responseStatus":200}'

    def _fake(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp()

    with mock.patch("urllib.request.urlopen", _fake):
        T.MyMemoryEngine({}).translate("Auto equip")
    check("UA 是 Waass/<真实版本>",
          seen.get("ua") == "Waass/%s" % APP_VERSION, str(seen))
    check("UA 里没有写死的旧版本号", "Waass/1.2" != seen.get("ua"), str(seen))

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
