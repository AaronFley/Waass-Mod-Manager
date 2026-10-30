"""汉化模块测试：造假的 w3strings / 菜单 xml，验证「等长替换」不会破坏文件。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import localize, w3strings          # noqa: E402

PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


def fake_w3strings(path: Path, texts: list[str]) -> None:
    """按「头部 + 每条 [长度][文本][NUL]」的样子造一个明文文件。"""
    body = bytearray(b"RTSW" + (162).to_bytes(4, "little") + b"\x00\x00\x01\xe8")
    offset = 0
    for t in texts:
        b = t.encode("utf-8")
        body += bytes([len(b)]) + b + b"\x00\x00\x00"
        offset += len(b) + 4
    body += b"\x00" * 3
    path.write_bytes(bytes(body))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        f = base / "en.w3strings"
        fake_w3strings(f, [
            "Switchable Cloaks",
            "Auto equip",
            "Hoods only",
            "Disable",
            "Show in menu",
        ])
        size0 = f.stat().st_size

        print("[1] 识别明文")
        info = w3strings.probe(f)
        check("判定为可编辑", info["ok"], str(info))
        check("提取到 5 条", info.get("texts") == 5, str(info))

        print("[2] 提取内容")
        texts = [t["text"] for t in w3strings.extract(f)]
        check("原文正确", texts[:2] == ["Switchable Cloaks", "Auto equip"], str(texts))

        print("[3] 等长替换")
        r = w3strings.apply(f, {
            "Switchable Cloaks": "可切换披风",
            "Auto equip": "自动装",          # 10 字节 → 9 字节，放得下
            "Hoods only": "只兜帽",
            "Disable": "关",
            "Show in menu": "这个译名实在是太长了放不进去的",   # 故意超长
        }, backup_dir=base / "bak")
        check("改了 4 条", r["changed"] == 4, str(r["changed"]))
        check("超长的被跳过 1 条", len(r["skipped"]) == 1, str(r["skipped"]))
        check("文件长度没变", f.stat().st_size == size0,
              f"{size0} -> {f.stat().st_size}")
        check("备份生成了", bool(r["backup"]) and Path(r["backup"]).is_file())
        after = [t["text"] for t in w3strings.extract(f)]
        check("译文写进去了", any(t.strip() == "可切换披风" for t in after), str(after))
        check("超长那条保持原样", "Show in menu" in after, str(after))

        print("[4] 加密文件要拒绝")
        enc = base / "locked.w3strings"
        enc.write_bytes(b"RTSW" + (162).to_bytes(4, "little")
                        + bytes(range(1, 40)).replace(b"\x01", b"")[:20])
        info2 = w3strings.probe(enc)
        check("判定为不可编辑", not info2["ok"], str(info2))
        try:
            w3strings.apply(enc, {"anything": "中"})
            check("加密文件不写入", False, "居然写成功了")
        except Exception:
            check("加密文件不写入", True)

        print("[5] 非 w3strings 文件")
        bad = base / "x.txt"
        bad.write_text("hello", encoding="utf-8")
        check("拒绝非 RTSW 文件", not w3strings.probe(bad)["ok"])

        print("[6] 菜单 xml")
        xml = base / "menu.xml"
        xml.write_text(
            '<?xml version="1.0"?>\n<UserConfig>\n'
            '  <Group id="g" displayName="Mods.Hoods">\n'
            '    <Var id="Enabled" displayName="Enable Hoods" displayType="TOGGLE"/>\n'
            '    <Var id="Mode" displayName="Hood_Mode_Key" displayType="OPTIONS"/>\n'
            '    <Option id="0" displayName="Left side"/>\n'
            '  </Group>\n</UserConfig>\n', encoding="utf-8")
        got = localize.extract_xml(xml)
        srcs = [i["src"] for i in got["items"]]
        check("提取到 2 条字面文本", len(srcs) == 2, str(srcs))
        check("key 型被单独计数", got["keyed"] >= 1, str(got["keyed"]))
        check("Mods. 前缀不误伤", "Mods.Hoods" not in srcs, str(srcs))

        r2 = localize.apply_xml(xml, {"Enable Hoods": "启用兜帽",
                                      "Left side": "左侧"})
        txt = xml.read_text(encoding="utf-8")
        check("xml 里写入了中文", "启用兜帽" in txt and "左侧" in txt, txt[:200])
        check("xml 仍然可解析", localize.xml_wellformed(xml))
        check("key 没被动", "Hood_Mode_Key" in txt)

        print("[7] 译文里的引号要转义")
        xml2 = base / "m2.xml"
        xml2.write_text('<UserConfig><Var displayName="Test Value"/></UserConfig>',
                        encoding="utf-8")
        localize.apply_xml(xml2, {"Test Value": 'a "quoted" <b>'})
        check("转义正确", "&quot;" in xml2.read_text(encoding="utf-8")
              and localize.xml_wellformed(xml2), xml2.read_text(encoding="utf-8"))

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
