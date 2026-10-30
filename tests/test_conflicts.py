"""冲突检测的沙盒测试：全部在临时目录里造假游戏，不碰真实游戏目录。"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import bundle_entries, conflicts      # noqa: E402
from core.scanner import scan                   # noqa: E402

PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


def make_bundle(path: Path, names: list[str]) -> None:
    """手搓一个只有名称表的最小 POTATO70，够解析就行（不解压任何内容）。"""
    table = bytearray()
    for n in names:
        raw = n.encode("cp1252")
        table += raw + b"\x00" * (bundle_entries.NAME_SLOT - len(raw))
        table += struct.pack("<QIIIIII", 0, 0, 0, 0, 0, 0, 0)
    head = (b"POTATO70" + struct.pack("<III", len(names), 0, len(table))
            + struct.pack("<HH", 1, 0x20))
    head += b"\x00" * (bundle_entries.HEADER - len(head))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + bytes(table))


def make_mod(root: Path, folder: str, scripts: list[str] = (), menu: list[str] = (),
             bundle: str = "x.bundle", bundle_names: list[str] | None = None,
             script_body: str = "// script\n", bodies: dict[str, bytes] | None = None,
             w3cn: int | None = None) -> Path:
    d = root / "mods" / folder
    content = d / "content"
    content.mkdir(parents=True)
    if bundle:
        if bundle_names is None:
            (content / bundle).write_bytes(b"BND")
        else:
            make_bundle(content / bundle, bundle_names)
    for rel in scripts:
        p = content / "scripts" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if bodies and rel in bodies:
            p.write_bytes(bodies[rel])
        else:
            p.write_text(script_body, encoding="utf-8")
    if w3cn is not None:
        (content / "cn.w3strings").write_bytes(b"RTSW" + struct.pack("<I", w3cn) + b"\x00" * 16)
    if menu:
        pc = d / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
        pc.mkdir(parents=True, exist_ok=True)
        for m in menu:
            (pc / m).write_text("<xml/>", encoding="utf-8")
    return d


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        game = base / "game"
        (game / "bin" / "x64_dx12").mkdir(parents=True)
        (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
        (game / "mods").mkdir()
        (game / "dlc").mkdir()
        cfg = base / "cfg"
        cfg.mkdir()
        (cfg / "mods.settings").write_text(
            "[modA]\nEnabled=1\nPriority=10\n\n"
            "[modB]\nEnabled=1\nPriority=10\n\n"
            "[modC]\nEnabled=1\nPriority=30\n", encoding="utf-8")
        os.environ["WAASS_CONFIG_DIR"] = str(cfg)
        os.environ["WAASS_GAME_DIR"] = str(game)
        os.environ["WAASS_DATA_DIR"] = str(base / "appdata")

        # A 和 B 抢同一个脚本；A 和 C 抢同一个菜单 xml
        make_mod(game, "modA", scripts=["local/foo.ws", "game/bar.ws"], menu=["modA.xml"])
        make_mod(game, "modB", scripts=["local/foo.ws"])
        make_mod(game, "modC", menu=["modA.xml"])
        make_mod(game, "modBroken", bundle="")

        res = scan(game)
        rep = conflicts.analyze(game, res.mods, force=True)
        codes = {i["code"] for i in rep["items"]}

        print("[1] 脚本同名")
        so = [i for i in rep["items"] if i["code"] == "script_overlap"]
        check("检出脚本冲突", len(so) == 1, str(codes))
        check("冲突文件是 local/foo.ws", so and "content/scripts/local/foo.ws" in so[0]["files"],
              str(so[0]["files"] if so else []))
        check("两侧都记到 by_mod",
              so and {"modA", "modB"} <= {m for m in rep["by_mod"]})

        print("[2] 菜单 xml 同名")
        mx = [i for i in rep["items"] if i["code"] == "menu_xml_clash"]
        check("检出菜单冲突", len(mx) == 1, str(codes))
        check("点名两个 mod", mx and set(mx[0]["mods"]) == {"modA", "modC"},
              str(mx[0]["mods"] if mx else []))

        print("[3] Priority 重复")
        dp = [i for i in rep["items"] if i["code"] == "dup_priority"]
        check("检出优先级冲突", len(dp) == 1)

        print("[4] 结构异常")
        check("空 mod 被发现", "empty_mod" in codes or "no_payload" in codes, str(codes))

        print("[5] 缓存")
        rep2 = conflicts.analyze(game, res.mods)
        check("命中缓存返回同一对象", rep2 is rep)

        print("[6] 输出规模")
        check("脚本文件数统计正确", rep["stats"]["script_files"] == 3,
              str(rep["stats"]))

        # ============ 第二批：1.1.1 新增的四类检测 ============
        # 另起一个假游戏，免得影响上面 [6] 的文件数统计。
        game2 = base / "g2"
        (game2 / "bin" / "x64_dx12").mkdir(parents=True)
        (game2 / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
        (game2 / "mods").mkdir()
        (game2 / "dlc").mkdir()
        c0 = game2 / "content" / "content0"
        c0.mkdir(parents=True)
        (c0 / "cn.w3strings").write_bytes(b"RTSW" + struct.pack("<I", 164) + b"\x00" * 16)
        cfg2 = base / "cfg2"
        cfg2.mkdir()
        (cfg2 / "mods.settings").write_text(
            "[modD]\nEnabled=1\nPriority=10\n\n"
            "[modE]\nEnabled=1\nPriority=20\n\n"
            "[modF]\nEnabled=1\nPriority=30\n", encoding="utf-8")
        os.environ["WAASS_CONFIG_DIR"] = str(cfg2)

        swf = "gameplay\\gui_new\\swf\\inventory\\panel_inventory.redswf"
        wrap = ("@wrapMethod(CR4InventoryMenu)\n"
                "function OnConfigUI()\n{\n    // mine\n}\n")
        # D 和 E 抢同一个界面资源、又都 wrap 同一个函数；D 的中文表还是 v162
        make_mod(game2, "modD", scripts=["local/d.ws"], bundle="blob0.bundle",
                 bundle_names=[swf, "items\\definitions\\items.xml"],
                 script_body=wrap, w3cn=162)
        make_mod(game2, "modE", scripts=["local/e.ws"], bundle="blob0.bundle",
                 bundle_names=[swf], script_body=wrap)
        # F 是 4.x 时代的老包：脚本是 UTF-16
        make_mod(game2, "modF", scripts=["local/f.ws"],
                 bodies={"local/f.ws": "// old\n".encode("utf-16-le")})
        # G 用 ~ 前缀停用，包里也有同一个界面资源 —— 必须完全不出现在结果里
        make_mod(game2, "~modG", bundle="blob0.bundle", bundle_names=[swf])

        res2 = scan(game2)
        rep3 = conflicts.analyze(game2, res2.mods, force=True)
        codes3 = {i["code"] for i in rep3["items"]}
        st3 = rep3["stats"]

        print("[7] 资源包内部的同名界面资源")
        bu = [i for i in rep3["items"] if i["code"] == "bundle_ui"]
        check("检出界面资源撞车", len(bu) == 1, str(codes3))
        check("只点名启用的 D 和 E",
              bu and set(bu[0]["mods"]) == {"modD", "modE"},
              str(bu[0]["mods"] if bu else []))
        check("文件是 panel_inventory.redswf",
              bu and bu[0]["files"] == ["panel_inventory.redswf"],
              str(bu[0]["files"] if bu else []))
        check("停用的 ~modG 不算在内",
              bool(bu) and all("modG" not in k for k in bu[0]["mods"]),
              str(bu[0]["mods"] if bu else []))
        check("物品定义只有一方提供，不报 bundle_data", "bundle_data" not in codes3,
              str(codes3))

        print("[8] 注解挂在同一个函数上")
        aw = [i for i in rep3["items"] if i["code"] == "anno_wrap"]
        check("检出注解叠加", len(aw) == 1, str(codes3))
        check("级别是 info（可共存，不是错）", aw and aw[0]["level"] == "info",
              str(aw[0]["level"] if aw else []))
        check("点名 CR4InventoryMenu.OnConfigUI",
              aw and "OnConfigUI" in aw[0]["files"][0],
              str(aw[0]["files"] if aw else []))
        check("没误报 @replaceMethod", "anno_replace" not in codes3, str(codes3))

        print("[9] 脚本编码")
        we = [i for i in rep3["items"] if i["code"] == "ws_encoding"]
        check("检出非 UTF-8 脚本", len(we) == 1, str(codes3))
        check("只点名 modF", we and set(we[0]["mods"]) == {"modF"},
              str(we[0]["mods"] if we else []))

        print("[10] 语言表版本")
        wst = [i for i in rep3["items"] if i["code"] == "w3strings_stale"]
        check("检出语言表落后", len(wst) == 1, str(codes3))
        check("只点名 modD（v162 对 v164）", wst and set(wst[0]["mods"]) == {"modD"},
              str(wst[0]["mods"] if wst else []))
        check("stats 读到游戏本体版本 164", st3["w3_base"] == 164, str(st3["w3_base"]))

        print("[11] 统计口径")
        check("界面撞车计入 stats", st3["bundle_ui"] >= 1, str(st3["bundle_ui"]))
        check("停用模组单独计数", st3["disabled"] == 1, str(st3["disabled"]))
        check("bundle_mods 数的是带包的启用模组（D/E/F）", st3["bundle_mods"] == 3,
              str(st3["bundle_mods"]))

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
