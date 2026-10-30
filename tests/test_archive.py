"""压缩包结构识别测试：造各种真实世界里出现过的包形态。"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.archive import ArchiveError, build_plan, extract, tools_report  # noqa: E402

ok = 0
fail = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global ok, fail
    if cond:
        ok += 1
        print(f"  ok   {label}")
    else:
        fail += 1
        print(f"  FAIL {label}  {detail}")


def make_zip(path: Path, files: dict[str, bytes | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def main() -> int:
    from core import i18n
    i18n.set_lang("zh")   # 断言的是中文文案，别跟着真实 config.json 的 lang 走
    base = Path(tempfile.mkdtemp(prefix="waass_zip_"))
    print("解压工具:", tools_report())

    # 1. 直接就是 modXXX/
    z1 = make_zip(base / "a.zip", {
        "modBetterRoads/content/scripts/foo.ws": "-- script",
        "modBetterRoads/readme.txt": "hi",
    })
    st, _ = extract(z1)
    p = build_plan(st)
    check("裸 mod 目录 → 进 mods/", p.mod_folders == ["modBetterRoads"], str(p.mod_folders))
    check("dst 正确", p.entries[0].dst == "mods/modBetterRoads", p.entries[0].dst)

    # 2. 带包装层的标准结构
    z2 = make_zip(base / "b.zip", {
        "MyMod-v1.2/mods/modFoo/content/x.bundle": b"x",
        "MyMod-v1.2/dlc/dlcFoo/content/y.bundle": b"y",
        "MyMod-v1.2/bin/config/r4game/user_config_matrix/pc/foo.xml": "<xml/>",
        "MyMod-v1.2/readme.md": "doc",
    })
    st, _ = extract(z2)
    p = build_plan(st)
    check("剥掉包装层", p.wrapper.lower().startswith("mymod"), p.wrapper)
    check("认出 mod 文件夹", p.mod_folders == ["modFoo"], str(p.mod_folders))
    check("认出 dlc", p.dlc_folders == ["dlcFoo"], str(p.dlc_folders))
    check("认出菜单 xml", p.menu_xmls == ["foo.xml"], str(p.menu_xmls))
    check("menu xml 落点", any(e.dst.endswith("pc/foo.xml") for e in p.entries),
          str([e.dst for e in p.entries]))

    # 3. 大小写混用 Mods /
    z3 = make_zip(base / "c.zip", {"Mods/modBar/content/a.bundle": b"a"})
    st, _ = extract(z3)
    p = build_plan(st)
    check("Mods 大小写也认", p.mod_folders == ["modBar"], str(p.mod_folders))

    # 4. 不带 mod 前缀的文件夹 → 自动改名并警告
    z4 = make_zip(base / "d.zip", {"BetterRoads/content/a.bundle": b"a"})
    st, _ = extract(z4)
    p = build_plan(st)
    check("无前缀自动改名", p.mod_folders == ["mod_BetterRoads"], str(p.mod_folders))
    check("有改名警告", any("不以 mod 开头" in w for w in p.warnings), str(p.warnings))

    # 5. 路径穿越必须被拦
    z5 = base / "e.zip"
    with zipfile.ZipFile(z5, "w") as zf:
        zf.writestr("../evil.txt", b"pwn")
        zf.writestr("modOk/content/a.bundle", b"a")
    try:
        st, _ = extract(z5)
        escaped = (Path(st).parent.parent / "evil.txt").exists()
        check("路径穿越被拦", not escaped, "文件逃到暂存区外面了")
    except ArchiveError as exc:
        check("路径穿越被拦", True)
        print(f"       → {exc}")

    # 6. 带 mods.settings
    z6 = make_zip(base / "f.zip", {
        "modX/content/a.bundle": b"a",
        "mods.settings": "[modX]\nEnabled=1\n",
    })
    st, _ = extract(z6)
    p = build_plan(st)
    check("认出 mods.settings", p.has_settings, str(p.has_settings))

    # 7. 认不出来的包
    z7 = make_zip(base / "g.zip", {"random.txt": "x", "data.bin": b"y"})
    st, _ = extract(z7)
    p = build_plan(st)
    check("无结构时给提示", not p.ok and p.warnings, str(p.warnings[:1]))

    # 8. 冲突标注
    fake_game = base / "game"
    (fake_game / "mods" / "modFoo").mkdir(parents=True, exist_ok=True)
    st, _ = extract(z2)
    p = build_plan(st, fake_game)
    foo = next(e for e in p.entries if e.name == "modFoo")
    check("已存在同名 → 标冲突", foo.conflict == "replace", foo.conflict)

    print(f"\n通过 {ok}，失败 {fail}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
