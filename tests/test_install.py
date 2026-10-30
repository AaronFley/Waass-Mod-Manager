"""端到端：用用户真实下载的 mod 包，装进一个假游戏目录。

真包只读，游戏目录是临时造的，绝不碰真实安装。
跑法：
    python tests/test_install.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DL = Path(r"D:\download")
Z_LOADFIX = DL / "W3LoadFix_13881_1.0.8_2026-10-19T17-54Z_Ukn0RyUyK (1).zip"
Z_LIGHT = DL / "Light_Rewrite_12144_0.26.2_2026-10-23T03-19Z_864tMl89U.zip"
Z_UIRE = DL / "UI_Remastered_Edition_14578_1.1.2_2026-10-24T01-45Z_lPL5EVlrG.zip"

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def make_game(base: Path) -> tuple[Path, Path]:
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    pc = game / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
    pc.mkdir(parents=True)
    (pc / "gameplay.xml").write_text("<x/>", encoding="utf-8")
    (game / "mods").mkdir()
    (game / "dlc").mkdir()
    (game / "mods" / "modSwordsOnHip" / "content").mkdir(parents=True)
    (game / "mods" / "modSwordsOnHip" / "content" / "a.bundle").write_bytes(b"1")

    cfg = base / "cfg"
    cfg.mkdir()
    (cfg / "mods.settings").write_text(
        "[modSwordsOnHip]\nEnabled=1\nPriority=110\n", encoding="utf-8")
    return game, cfg


def main() -> int:
    missing = [p for p in (Z_LOADFIX, Z_LIGHT, Z_UIRE) if not p.is_file()]
    if missing:
        print("缺少样本包，跳过：", [p.name for p in missing])
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        game, cfg = make_game(base)
        os.environ.update({
            "WAASS_CONFIG_DIR": str(cfg),
            "WAASS_GAME_DIR": str(game),
            "WAASS_DATA_DIR": str(base / "appdata"),
            "WAASS_TEST_MODE": "1",
            "WAASS_SANDBOX_DIR": str(base),
        })

        from core import install, registry, safety

        print("\n[0] 文件名解析（Nexus ID / 版本）")
        i = registry.parse_archive_name(Z_LIGHT.name)
        check("Light_Rewrite ID=12144", i["nexus_id"] == 12144, str(i))
        check("版本 0.26.2", i["version"] == "0.26.2", str(i))
        i2 = registry.parse_archive_name(Z_LOADFIX.name)
        check("带 (1) 后缀也认", i2["nexus_id"] == 13881 and i2["version"] == "1.0.8", str(i2))

        # 老 mod 的包名（名字-ID-版本-时间戳），版本段数不定
        legacy = {
            "Hoods-4242-3-7-1698765432.zip": ("Hoods", 4242, "3.7"),
            "All Quest Markers-13087-1-7-3-1698765432.7z": ("All Quest Markers", 13087, "1.7.3"),
            "UI Remastered - Next Gen-14578-1-1-2-1698765432.zip":
                ("UI Remastered Next Gen", 14578, "1.1.2"),
            "Blood and Steel-9674-3-02-1698765432.zip": ("Blood and Steel", 9674, "3.02"),
            "Swords-13583-3-1-1698765432-999888.7z": ("Swords", 13583, "3.1"),
            "Better Swords 2-9876-1-0-1698765432.zip": ("Better Swords 2", 9876, "1.0"),
            "Plain Name-1234-1698765432.zip": ("Plain Name", 1234, ""),
        }
        for fn, (nm, nid, ver) in legacy.items():
            g = registry.parse_archive_name(fn)
            check(f"老格式 {fn[:26]}", (g["name"], g["nexus_id"], g["version"]) == (nm, nid, ver),
                  str(g))

        # 认不出来就必须老实说不知道，不能瞎猜一个 ID 出来
        for fn in ("modHoods.zip", "my backup.7z", "Vesemir-2-1-1698765432.zip",
                   "download (1).zip"):
            g = registry.parse_archive_name(fn)
            check(f"不瞎猜 {fn}", g["nexus_id"] is None, str(g))

        print("\n[1] 预览三个真包")
        for z, expect_mod in ((Z_LIGHT, "modLightRewrite"),
                              (Z_UIRE, "mod0000_UIRemasteredEdition"),
                              (Z_LOADFIX, None)):
            pv = install.preview(z, game)
            if expect_mod:
                check(f"{pv['name'][:20]} 认出 {expect_mod}", expect_mod in pv["mods"], str(pv["mods"]))
            else:
                check(f"{pv['name'][:20]} 标为高危", pv["risk"] == "high", pv["risk"])
        check("预览没有往游戏里写东西", not (game / "mods" / "modLightRewrite").exists())

        print("\n[2] 装 Light_Rewrite")
        r = install.install(Z_LIGHT, game)
        check("返回包 id", bool(r["package_id"]))
        check("mod 文件夹就位", (game / "mods" / "modLightRewrite").is_dir())
        check("dlc 就位", (game / "dlc" / "dlclightrewrite").is_dir())
        check("菜单 xml 就位", (game / "bin" / "config" / "r4game" /
                              "user_config_matrix" / "pc" / "LightRewrite.xml").is_file())
        check("原有 modSwordsOnHip 没被动",
              (game / "mods" / "modSwordsOnHip" / "content" / "a.bundle").is_file())
        pkg_lr = r["package_id"]

        print("\n[3] 卸载进隔离区")
        u = install.uninstall(pkg_lr, game)
        check("游戏里的 mod 没了", not (game / "mods" / "modLightRewrite").exists())
        check("dlc 没了", not (game / "dlc" / "dlclightrewrite").exists())
        check("隔离区有文件", (Path(u["quarantine"]) / "mods" / "modLightRewrite").is_dir())
        check("文件没被删（还能数出内容）",
              any((Path(u["quarantine"]) / "mods" / "modLightRewrite").rglob("*")))
        check("档案状态=removed", registry.get(pkg_lr)["state"] == "removed")

        print("\n[4] 从隔离区恢复（等于重装）")
        install.restore(pkg_lr, game)
        check("mod 回来了", (game / "mods" / "modLightRewrite").is_dir())
        check("dlc 回来了", (game / "dlc" / "dlclightrewrite").is_dir())
        check("档案状态=active", registry.get(pkg_lr)["state"] == "active")

        print("\n[5] 高危包必须二次确认")
        try:
            install.install(Z_LOADFIX, game)
            check("未确认时应拒绝", False, "居然直接装了")
        except install.NeedsConfirm as e:
            check("已拦下并说明原因", "游戏程序目录" in str(e))
        check("拒绝后没写 bin", not (game / "bin" / "x64_dx12" / "w3loadfix.dll").exists())

        print("\n[6] 确认后装高危包")
        r2 = install.install(Z_LOADFIX, game, allow_risk=True)
        check("dll 就位", (game / "bin" / "x64_dx12" / "w3loadfix.dll").is_file())
        check("dsound.dll 就位", (game / "bin" / "x64_dx12" / "dsound.dll").is_file())
        check("witcher3.exe 还在", (game / "bin" / "x64_dx12" / "witcher3.exe").is_file())
        check("菜单 xml 就位", (game / "bin" / "config" / "r4game" /
                              "user_config_matrix" / "pc" / "w3loadfix.xml").is_file())

        print("\n[7] 覆盖安装（同名冲突）")
        try:
            install.install(Z_UIRE, game)
            install.install(Z_UIRE, game)
            check("重复装应提示冲突", False, "没提示")
        except install.NeedsConfirm as e:
            check("冲突被拦下", "已经有" in str(e), str(e))
        r3 = install.install(Z_UIRE, game, on_conflict="replace")
        check("覆盖装成功", (game / "mods" / "mod0000_UIRemasteredEdition").is_dir())
        check("旧版进了隔离区",
              (Path(r3["package_id"]) and
               (Path(registry.get(r3["package_id"])["quarantine"]) / "mods" /
                "mod0000_UIRemasteredEdition").is_dir()))

        print("\n[8] 撤销上一步（覆盖安装）")
        pkg_ui = r3["package_id"]
        ok, msg = safety.undo_last()
        check("撤销成功", ok, msg)
        check("旧版被放回游戏目录",
              (game / "mods" / "mod0000_UIRemasteredEdition").is_dir())
        check("撤销后档案里不再挂着这份新装",
              registry.get(pkg_ui) is None)

        print("\n[9] 档案总览")
        groups = install.list_packages()
        check("有 3 个包登记在册",
              len(groups["active"]) + len(groups["removed"]) == 3,
              f"active={len(groups['active'])} removed={len(groups['removed'])}")
        check("都能对上 Nexus ID",
              all(p["nexus_id"] in (12144, 13881, 14578)
                  for p in groups["active"] + groups["removed"]))

        print("\n[10] 写保护仍然有效")
        from core import guard
        check("真实游戏目录不可写",
              not guard.is_writable(r"D:\game\steam\steamapps\common\The Witcher 3\mods"))
        check("沙盒内可写", guard.is_writable(game / "mods"))

        # [11] 「保留原包」开关已随 1.2.4 移除（用户要求），这里只留一条反向断言：
        # 装完不该再往 data/archives 里塞任何东西。
        print("\n[11] 不再复制原包")
        check("安装后 data/archives 不存在或为空",
              not (base / "appdata" / "archives").is_dir()
              or not any((base / "appdata" / "archives").iterdir()))

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：" + "、".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
