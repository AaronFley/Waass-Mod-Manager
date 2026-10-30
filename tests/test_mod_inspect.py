"""1.1.1 新增的三个只读探针：bundle 条目、脚本注解与编码、语言表版本。

全部在临时目录里造文件，不碰真实游戏目录，也不需要真的巫师3 安装。
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import bundle_entries, scripts_meta, w3strings   # noqa: E402

PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


# ---------------- 造一个最小可解析的 POTATO70 ----------------

def make_bundle(path: Path, names: list[str], magic: bytes = b"POTATO70",
                names_size: int | None = None) -> None:
    table = bytearray()
    for n in names:
        raw = n.encode("cp1252")
        assert len(raw) < 255
        slot = raw + b"\x00" * (bundle_entries.NAME_SLOT - len(raw))
        table += slot
        table += struct.pack("<QIIIIII", 0, 0, 0, 0, 0, 0, 0)
    size = len(table) if names_size is None else names_size
    head = magic + struct.pack("<III", len(names), 0, size) + struct.pack("<HH", 1, 0x20)
    head = head + b"\x00" * (bundle_entries.HEADER - len(head))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + bytes(table))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        print("[1] bundle 名称表")
        b = base / "modA" / "content" / "blob0.bundle"
        make_bundle(b, [
            "gameplay\\gui_new\\swf\\inventory\\panel_inventory.redswf",
            "gameplay\\gui_new\\swf\\HUD\\Hud_Main.redswf",
            "items\\definitions\\items.xml",
            "characters\\models\\foo.w2mesh",
        ])
        got = bundle_entries.read_names(b)
        check("条目数正确", len(got) == 4, str(got))
        check("路径转成小写反斜杠", all(n == n.lower() and "/" not in n for n in got), str(got[:1]))
        check("能认出界面文件",
              bundle_entries.classify("a\\b\\panel_inventory.redswf") == "ui")
        check("能认出定义文件",
              bundle_entries.classify("items\\definitions\\items.xml") == "data")
        check("贴图归为 asset",
              bundle_entries.classify("a\\b\\x.xbm") == "asset")

        print("[2] 异常输入一律返回空，不抛异常")
        bad = base / "bad.bundle"
        bad.write_bytes(b"NOTABUNDLE" + b"\x00" * 64)
        check("魔数不对 → 空", bundle_entries.read_names(bad) == ())

        broken = base / "broken.bundle"
        make_bundle(broken, ["a\\b.redswf"], names_size=50_000_000)
        check("名称表大小离谱 → 空（不会去读 50MB）",
              bundle_entries.read_names(broken) == ())

        tiny = base / "tiny.bundle"
        tiny.write_bytes(b"POTATO70")
        check("文件太短 → 空", bundle_entries.read_names(tiny) == ())

        check("不存在的文件 → 空", bundle_entries.read_names(base / "nope.bundle") == ())

        print("[3] 缓存按 mtime 失效")
        n1 = bundle_entries.read_names(b)
        n2 = bundle_entries.read_names(b)
        check("两次读的是同一份结果", n1 is n2)
        import time
        time.sleep(0.05)
        make_bundle(b, ["only\\one.redswf"])
        n3 = bundle_entries.read_names(b)
        check("内容变了缓存跟着变", len(n3) == 1 and n3[0] == "only\\one.redswf", str(n3))

        print("[4] scan_mod 汇总一个 mod 的多个包")
        mod = base / "modScan"
        make_bundle(mod / "content" / "blob0.bundle", ["a\\one.redswf"])
        make_bundle(mod / "content" / "bundles" / "extra.bundle", ["a\\two.xml"])
        packs = bundle_entries.scan_mod(mod)
        check("两个包都扫到", len(packs) == 2, str(list(packs)))
        check("相对路径带 content/",
              all(k.startswith("content/") for k in packs), str(list(packs)))
        check("没有 content 目录 → 空",
              bundle_entries.scan_mod(base / "nothing") == {})

        print("[5] 脚本注解抽取")
        ws = base / "local" / "demo.ws"
        ws.parent.mkdir(parents=True)
        ws.write_text(
            "// 注释里的 @wrapMethod( Fake ) function bogus 不算\n"
            "@wrapMethod( CR4MapMenu )\n"
            "function OnStaticMapPinUsed( pinTag : name, areaNameId : int )\n"
            "{\n"
            "    wrappedMethod( pinTag, areaNameId );\n"
            "}\n"
            "\n"
            "@replaceMethod( CR4InventoryMenu )\n"
            "private function OnConfigUI( )\n"
            "{\n"
            "}\n"
            "\n"
            "@addField( CR4HudModule ) private var m_x : bool;\n"
            "\n"
            "@wrapMethod( CR4Hud )\n"
            "private event function OnTick( dt : float )\n"
            "{\n"
            "}\n",
            encoding="utf-8")
        annos = scripts_meta.annotations(ws)
        kinds = [(k, c, f) for k, c, f in annos]
        check("抽到 3 条（addField 不算）", len(annos) == 3, str(kinds))
        check("wrap + 类名 + 函数名",
              ("wrapMethod", "CR4MapMenu", "OnStaticMapPinUsed") in kinds, str(kinds))
        check("replace 也能抽到",
              ("replaceMethod", "CR4InventoryMenu", "OnConfigUI") in kinds, str(kinds))
        check("带 private / event 修饰词照样认",
              ("wrapMethod", "CR4Hud", "OnTick") in kinds, str(kinds))
        check("注释里的假注解不误报",
              not any("bogus" in f for _k, _c, f in annos), str(kinds))

        print("[6] 注解后跟的不是函数就别硬凑")
        ws2 = base / "local" / "demo2.ws"
        ws2.write_text("@wrapMethod( SomeClass )\nvar x : int;\n", encoding="utf-8")
        check("注解挂字段 → 空", scripts_meta.annotations(ws2) == [])

        print("[7] 编码判定")
        u8 = base / "u8.ws"
        u8.write_text("// 中文注释也正常\n", encoding="utf-8")
        check("UTF-8 → utf-8", scripts_meta.encoding(u8) == "utf-8")

        u8b = base / "u8b.ws"
        u8b.write_bytes(b"\xef\xbb\xbf// bom\n")
        check("带 BOM → utf-8-bom", scripts_meta.encoding(u8b) == "utf-8-bom")

        u16 = base / "u16.ws"
        u16.write_bytes("// hi\n".encode("utf-16-le"))
        check("无 BOM 的 UTF-16LE 也能认出来",
              scripts_meta.encoding(u16) == "utf-16-le", scripts_meta.encoding(u16))

        u16b = base / "u16b.ws"
        u16b.write_bytes("// hi\n".encode("utf-16"))
        check("带 BOM 的 UTF-16 → utf-16-le", scripts_meta.encoding(u16b) == "utf-16-le")

        binjunk = base / "junk.ws"
        binjunk.write_bytes(b"\x80\x81\x82\x83" * 20)
        check("乱字节 → other", scripts_meta.encoding(binjunk) == "other")

        print("[8] 语言表版本")
        w3 = base / "cn.w3strings"
        w3.write_bytes(b"RTSW" + struct.pack("<I", 164) + b"\x00" * 40)
        check("读出 164", w3strings.version(w3) == 164)

        older = base / "zh.w3strings"
        older.write_bytes(b"RTSW" + struct.pack("<I", 162) + b"\x00" * 40)
        check("读出 162", w3strings.version(older) == 162)

        check("不是 w3strings → None",
              w3strings.version(base / "u8.ws") is None)
        check("文件不存在 → None",
              w3strings.version(base / "none.w3strings") is None)

        print("[9] 游戏基准版本")
        game = base / "game"
        c0 = game / "content" / "content0"
        c0.mkdir(parents=True)
        (c0 / "cn.w3strings").write_bytes(b"RTSW" + struct.pack("<I", 164) + b"\x00" * 40)
        check("优先取 cn 表", w3strings.game_baseline(game) == 164)

        (c0 / "cn.w3strings").unlink()
        (c0 / "en.w3strings").write_bytes(b"RTSW" + struct.pack("<I", 163) + b"\x00" * 40)
        check("没有 cn 就退回 en", w3strings.game_baseline(game) == 163)

        check("整个都没有 → None",
              w3strings.game_baseline(base / "empty") is None)

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
