"""汉化判定规则的沙盒测试（1.2.1 新增）。

为什么单独一个文件
------------------
1.2.0 只认语言表，于是把「已经手改菜单 XML 汉化好」的模组照旧报成「还有 36 条英文」。
1.2.1 加了两条规则：

  1. 菜单 XML 里已经用中文字面写死的行 → 语言表里对应的英文算「过期残留」，不计入待翻；
  2. 单个分类词（Hoods / Capes）→ 判成物品名，也不算待翻。

这两条都属于「宁可少列，不要列错」的判断，判错了用户会白翻一堆没用的东西，
或者盯着一个永远写不进去的 5 字节词发愁。所以单独钉一套测试。

语言表读写不在这里测（另有两个文件专门测），这里把 w3strings 的读取打桩，
只测「规则对不对」。XML 是真文件、真解析。

跑法：
    python tests/test_localize_detect.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="waass-locdet-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_SANDBOX)
os.environ["WAASS_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WAASS_CONFIG_DIR"] = str(_SANDBOX / "cfg")

from core import localize, w3strings          # noqa: E402

PASS = FAIL = 0

GAME = _SANDBOX / "game"
MENU = GAME / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def make_mod(folder: str) -> Path:
    d = GAME / "mods" / folder / "content"
    d.mkdir(parents=True, exist_ok=True)
    (d / "cn.w3strings").write_bytes(b"RTSW")
    return d


def make_menu(name: str, body: str) -> Path:
    MENU.mkdir(parents=True, exist_ok=True)
    p = MENU / name
    p.write_text('<?xml version="1.0" encoding="UTF-8"?>\n<UserConfig>\n'
                 + body + "\n</UserConfig>\n", encoding="utf-8")
    return p


# 桩：语言表内容由测试指定，避免为了造二进制去写一个编码器
_TEXTS: dict[str, list[str]] = {}


def _scan_mod(mod_dir: Path) -> list[dict]:
    p = mod_dir / "content" / "cn.w3strings"
    if not p.is_file():
        return []
    return [{"lang": "cn", "rel": str(p.relative_to(GAME)).replace("\\", "/"),
             "path": str(p), "size": 100, "ok": True, "reason": ""}]


def _extract(path) -> list[dict]:
    out = []
    for i, t in enumerate(_TEXTS.get(str(path), [])):
        out.append({"off": i * 10, "length": len(t.encode("utf-8")), "text": t})
    return out


w3strings.scan_mod = _scan_mod          # type: ignore[assignment]
w3strings.extract = _extract            # type: ignore[assignment]


def texts_of(folder: str, items: list[str]) -> None:
    _TEXTS[str(GAME / "mods" / folder / "content" / "cn.w3strings")] = items


def items_of(jobs: dict, key: str = "") -> list[dict]:
    return [i for i in jobs["items"] if not key or i["mod"] == key]


def main() -> int:
    # ---------- 1. 手改菜单 XML 的模组 ----------
    print("[1] 菜单 XML 已经写成中文 → 语言表英文算过期")
    make_mod("modCloak")
    make_menu("Cloak.xml", """
  <Group displayName="Mods.priler_cloak.section_general">
    <Var id="Enabled" displayName="启用模组" displayType="TOGGLE"/>
    <Var id="Hoods" displayName="允许兜帽" displayType="TOGGLE"/>
    <Var id="Anim" displayName="播放动画" displayType="TOGGLE"/>
    <Preset id="0" displayName="njs_default"/>
    <Preset id="1" displayName="njs_all"/>
  </Group>""")
    # 语言表：2 条已翻（对应两个 key）+ 6 条英文（原本是 displayName，已被中文取代）
    texts_of("modCloak", ["可切换披风", "默认",
                          "Unequip during FIST FIGHT", "Animate cloaks/capes",
                          "Allow toggling while horse riding", "Other",
                          "Auto equip force no animation", "Mod by PRILER / Other"])
    got = localize.extract_xml(MENU / "Cloak.xml")
    check("XML 里数出 3 条中文字面", got["cjk"] == 3, str(got["cjk"]))
    check("XML 里数出 3 个不同 key", got["keyed"] == 3, str(got.get("key_names")))
    check("中文字面不进待翻", not got["items"], str(got["items"]))

    take = localize.xml_takeover(GAME / "mods" / "modCloak", MENU,
                                 type("M", (), {"menu_xmls": ["Cloak.xml"],
                                                "disabled_menu_xmls": []})(), done=2)
    check("2 条已翻 >= 3 个 key？不成立，不该判接管", take["on"] is False, str(take))

    take2 = localize.xml_takeover(GAME / "mods" / "modCloak", MENU,
                                  type("M", (), {"menu_xmls": ["Cloak.xml"],
                                                 "disabled_menu_xmls": []})(), done=3)
    check("3 条已翻 >= 3 个 key → 判为已接管", take2["on"] is True, str(take2))

    _TEXTS[str(GAME / "mods" / "modCloak" / "content" / "cn.w3strings")] = \
        ["可切换披风", "默认", "仅披风",
         "Unequip during FIST FIGHT", "Animate cloaks/capes",
         "Allow toggling while horse riding", "Other",
         "Auto equip force no animation", "Mod by PRILER / Other"]
    jobs = localize.build_jobs(GAME, None, cats=None)
    check("这 6 条英文不再进任务表", not items_of(jobs, "modCloak"),
          str([i["src"] for i in items_of(jobs, "modCloak")]))
    check("但计入 stale，信息没丢", jobs["stale"].get("modCloak") == 6, str(jobs["stale"]))

    rows = localize.catalog(GAME, None)
    row = next((r for r in rows if r["key"] == "modCloak"), None)
    check("体检里标成「已手工汉化」", bool(row and "手工汉化" in row["blocked"]),
          str(row and row["blocked"]))
    check("体检里带上过期条数", bool(row and row["stale"] == 6), str(row and row["stale"]))
    check("这种模组不需要勾选", bool(row and row["ready"] is False))

    # ---------- 2. 只改了一部分，不能算完成 ----------
    print("[2] XML 只写了几个中文、key 还一大把 → 不能判接管")
    make_mod("modHalf")
    make_menu("Half.xml", """
  <Group displayName="Mods.half.a">
    <Var id="A" displayName="已翻一条" displayType="TOGGLE"/>
    <Var id="B" displayName="Mods.half.b" displayType="TOGGLE"/>
    <Var id="C" displayName="Mods.half.c" displayType="TOGGLE"/>
    <Var id="D" displayName="Mods.half.d" displayType="TOGGLE"/>
    <Var id="E" displayName="Mods.half.e" displayType="TOGGLE"/>
  </Group>""")
    texts_of("modHalf", ["已翻", "Unequip during FIST FIGHT", "Animate cloaks"])
    take = localize.xml_takeover(GAME / "mods" / "modHalf", MENU,
                                 type("M", (), {"menu_xmls": ["Half.xml"],
                                                "disabled_menu_xmls": []})(), done=1)
    check("5 个 key 只翻了 1 条 → 不判接管", take["on"] is False, str(take))

    jobs = localize.build_jobs(GAME, ["modHalf"], cats=None)
    check("这种模组的英文照旧进任务表", len(items_of(jobs, "modHalf")) == 2,
          str([i["src"] for i in items_of(jobs, "modHalf")]))

    # ---------- 3. 没有 XML 的模组不受影响 ----------
    print("[3] 没有菜单 XML → 照旧按语言表算")
    make_mod("modPlain")
    texts_of("modPlain", ["Auto unequip during cutscenes", "Show in combat"])
    jobs = localize.build_jobs(GAME, ["modPlain"], cats=None)
    check("两条英文都进任务表", len(items_of(jobs, "modPlain")) == 2,
          str([i["src"] for i in items_of(jobs, "modPlain")]))
    check("没有 stale", not jobs["stale"].get("modPlain"), str(jobs["stale"]))

    # ---------- 4. 分类词判定 ----------
    print("[4] 单个分类词不算待翻")
    cases = [
        ("Hoods", localize.CAT_ITEM, "护甲分类"),
        ("Capes", localize.CAT_ITEM, "护甲分类"),
        ("Scarves", localize.CAT_ITEM, "护甲分类"),
        ("Enable", localize.CAT_MENU, "开关词"),
        ("Disabled", localize.CAT_MENU, "开关词"),
        ("Settings", localize.CAT_MENU, "菜单词，别被复数骗了"),
        ("Options", localize.CAT_MENU, "菜单词"),
        ("Comments", localize.CAT_MENU, "菜单词"),
        ("Other", localize.CAT_MENU, "菜单词"),
        ("Debug", localize.CAT_MENU, "菜单词"),
        ("Auto unequip in combat", localize.CAT_MENU, "有空格 = 句子"),
        ("Deadly Ronin's Kimono", localize.CAT_ITEM, "所有格 = 物品"),
    ]
    for src, want, why in cases:
        got = localize.classify(src, 3, "Some Mod", "mod_some")
        check(f"{src!r} → {want}（{why}）", got == want, f"实际 {got}")

    print("[5] 语言表第 0 条仍然算模组名")
    got = localize.classify("Hoods", 0, "Hood Slots", "mod_HoodSlots")
    check("第 0 条 = 模组名", got == localize.CAT_MOD, got)
    got = localize.classify("Hoods", 5, "Hoods", "mod_Hoods")
    check("和模组名同名 = 模组名", got == localize.CAT_MOD, got)

    print("[5b] 「模组名 + 版本号」也是模组名（1.2.4）")
    # 真实案例：Mod Settings Menu Fix 的 cn.w3strings 第 0/1 条就是这两行，
    # 第 1 条曾被当成菜单英文，面板一直说「还有英文待翻」。
    for src, want_mod, why in [
        ("Mod Settings Menu Fix 1.5.0", True, "名字 + 版本号"),
        ("Mod Settings Menu Fix", True, "裸名字"),
        ("Mod Settings Menu Fix and more", False, "后面还有别的词"),
        ("Enable Mod Settings Menu Fix", False, "前面还有动词，是句子"),
    ]:
        got = localize.classify(src, 2, "Mod Settings Menu Fix", "modModSettingsMenuFix")
        ok = (got == localize.CAT_MOD) == want_mod
        check(f"{src!r} → {'模组名' if want_mod else '非模组名'}（{why}）", ok, f"实际 {got}")

    print("[6] 面板统计与任务包必须用同一套判定（1.2.6）")
    # 真实反馈：hoodsSwitchableCloak 的菜单 XML 早就手写成中文了，
    # 任务包那边正确地把 36 条英文归成「过期残留」，但面板还在说
    # 「36 条菜单文字还是英文」—— 因为 inspect() 根本没看 xml_takeover()。
    make_mod("modHoodsCloak")
    make_menu("Cloak.xml", """
  <Group displayName="Mods.priler_cloak.section_general">
    <Var id="Enabled" displayName="启用模组" displayType="TOGGLE"/>
    <Var id="Hoods" displayName="允许兜帽" displayType="TOGGLE"/>
    <Var id="Anim" displayName="播放动画" displayType="TOGGLE"/>
    <Preset id="0" displayName="njs_default"/>
    <Preset id="1" displayName="njs_all"/>
  </Group>""")
    texts_of("modHoodsCloak", ["可切换披风", "默认", "常规",
                               "Unequip during FIST FIGHT", "Animate cloaks/capes",
                               "Allow toggling while horse riding",
                               "Mod by PRILER / Auto Equip"])
    fake = type("M", (), {"key": "modHoodsCloak", "name": "hoods Switchable Cloak",
                          "folder": "modHoodsCloak", "enabled": True,
                          "priority": None, "menu_xmls": ["Cloak.xml"],
                          "disabled_menu_xmls": []})()
    row = localize.inspect(GAME, [fake])[0]
    check("待翻归零", row["todo"] == 0, str(row["todo"]))
    check("改记成过期残留", row["stale"] == 4, str(row["stale"]))
    check("不再算 ready（勾不上）", row["ready"] is False, str(row["ready"]))
    check("why 说清楚了", "过期残留" in row["why"], row["why"])

    print("[7] 菜单文件归属：文件夹名比文件名多一个限定词（1.2.6）")
    # mod_hoodsSwitchableCloak 配的是 SwitchableCloak.xml。
    # 老逻辑只认「文件夹名是文件名的前缀」，于是这个 xml 被当成孤儿，
    # 里面的中文也就白翻了 —— 用户那边表现成「我汉化过了它还说待翻」。
    from core import scanner
    (GAME / "bin" / "x64_dx12").mkdir(parents=True, exist_ok=True)
    (GAME / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (GAME / "dlc").mkdir(parents=True, exist_ok=True)
    make_mod("mod_hoodsSwitchableCloak")
    make_menu("SwitchableCloak.xml", """
  <Group displayName="Mods.priler_switchable_cloak.priler_section_general">
    <Var id="Enabled" displayName="启用模组" displayType="TOGGLE"/>
  </Group>""")
    texts_of("mod_hoodsSwitchableCloak", [])
    res = scanner.scan(GAME)
    hit = next((m for m in res.mods if m.key == "mod_hoodsSwitchableCloak"), None)
    check("菜单归到了这个模组",
          hit is not None and "SwitchableCloak.xml" in hit.menu_xmls,
          str(hit.menu_xmls if hit else "没扫到"))
    check("没被当成孤儿", "SwitchableCloak.xml" not in res.orphan_menu, str(res.orphan_menu))

    print()
    print("通过 %d / 失败 %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
