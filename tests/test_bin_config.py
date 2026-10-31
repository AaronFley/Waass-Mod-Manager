"""带 `bin/config/` 配置文件的包装不上 —— 回归测试。

2026-10-26 的真实故障：SmartAutoLoot 这个包除了 mod 文件夹和菜单 xml，还会往
`bin/config/base/autoloot.ini` 写一份游戏配置。这类文件被归为 `bin_config`，
而安装时只按 `bin_binaries`（dll/exe）决定要不要放开 `bin/` 目录，
所以 `bin/config/base/` 永远拿不到写权限，安装必然报「没有写入授权」。

这个测试要证明：
1. `bin_config` 会被识别并报给界面；
2. 装得进去，且只放开 `bin/config` 这一层（没有连带放开整个 bin）；
3. 装得进去也卸得出来（卸载同样过闸门），恢复也正常；
4. 真要写 dll 的包仍然需要二次确认，风险提示没被这次改动削弱。

包是现场造在临时目录里的，不依赖用户下载过什么。
跑法：
    python tests/test_bin_config.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def make_pkg(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return path


def make_game(base: Path) -> tuple[Path, Path]:
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    base_cfg = game / "bin" / "config" / "base"
    base_cfg.mkdir(parents=True)
    (base_cfg / "autoloot.ini").write_bytes(b"OLD-CONTENT")
    pc = game / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
    pc.mkdir(parents=True)
    (pc / "gameplay.xml").write_text("<x/>", encoding="utf-8")
    (game / "mods").mkdir()
    (game / "dlc").mkdir()

    cfg = base / "cfg"
    cfg.mkdir()
    (cfg / "mods.settings").write_text("[modSwordsOnHip]\nEnabled=1\n", encoding="utf-8")
    return game, cfg


AUTOLOOT = b"[AutoLoot]\nenabled=1\nradius=15\n"


def main() -> int:
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

        from core import guard, i18n, install, safety
        # 断言里写的是中文文案，必须自己钉死语言：程序默认跟随系统，
        # 英文版 Windows（CI 就是）会挑英文，这里就会假失败。
        i18n.set_lang("zh")

        # ---- 造包 ----
        pkg_cfg = make_pkg(base / "SmartAutoLoot_13035_1.1.7_1698765432.zip", {
            "mods/modAutoLoot/content/blob0.bundle": b"MOD",
            "bin/config/base/autoloot.ini": AUTOLOOT,
            "bin/config/r4game/user_config_matrix/pc/modAutoLoot.xml": b"<x/>",
        })
        pkg_dll = make_pkg(base / "SomePlugin_9999_1.0_1698765432.zip", {
            "mods/modPlugin/content/blob0.bundle": b"MOD",
            "bin/x64_dx12/d3d11.dll": b"MZ-FAKE",
        })

        print("[1] 识别")
        pv = install.preview(pkg_cfg, game)
        check("认出配置文件", pv["bin_configs"] == ["bin/config/base/autoloot.ini"],
              str(pv.get("bin_configs")))
        check("不算高危（不是 dll）", pv["risk"] == "normal", pv["risk"])
        check("清单里写了「游戏配置文件」", "游戏配置文件" in pv["summary"], pv["summary"])
        check("菜单文件照旧单独归类", pv["menu_xmls"] == ["modAutoLoot.xml"], str(pv["menu_xmls"]))
        check("预览没动过游戏目录里的旧配置",
              (game / "bin" / "config" / "base" / "autoloot.ini").read_bytes() == b"OLD-CONTENT")

        print("[2] 安装（这次故障的场景）")
        res = install.install(pkg_cfg, game, on_conflict="replace")
        check("装成功了", res.get("ok") is True, str(res)[:200])
        target = game / "bin" / "config" / "base" / "autoloot.ini"
        # 1.0.5 起这类文件走合并：游戏里原有的内容保留，只把 mod 的键并进去
        got = target.read_bytes()
        check("mod 的配置键写进去了", b"[AutoLoot]" in got and b"radius=15" in got, repr(got))
        check("游戏里原有的内容没被冲掉", b"OLD-CONTENT" in got, repr(got))
        check("mod 文件夹也装了", (game / "mods" / "modAutoLoot").is_dir())
        check("菜单 xml 也装了",
              (game / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
               / "modAutoLoot.xml").is_file())
        q = Path(install.QUARANTINE) / res["package_id"]
        check("配置文件没有被整份搬进隔离区（它是游戏自己的文件）",
              not (q / "bin" / "config" / "base" / "autoloot.ini").exists(), str(q))
        check("档案里标了这是合并文件",
              "bin/config/base/autoloot.ini" in (res.get("merges") and
                                                 [m["dst"] for m in res["merges"]] or []),
              str(res.get("merges")))

        print("[3] 授权范围没有被放大")
        check("bin/config 可写", guard.is_writable(game / "bin" / "config" / "base" / "autoloot.ini"))
        check("整个 bin 没有连带放开（dll 仍写不了）",
              not guard.is_writable(game / "bin" / "x64_dx12" / "d3d11.dll"))
        check("mods 照旧可写", guard.is_writable(game / "mods" / "modX" / "a.bundle"))

        print("[4] 卸载也要过闸门，并且能搬走")
        pkg_id = res["package_id"]
        before = len(guard.denied_hits())
        un = install.uninstall(pkg_id, game)
        check("卸载成功", un.get("ok") is True, str(un)[:200])
        check("配置文件还在游戏目录（没被删）", target.exists())
        check("只撤掉了自己加的那几行",
              b"[AutoLoot]" not in target.read_bytes(), repr(target.read_bytes()))
        check("游戏原有的内容完好在原处", b"OLD-CONTENT" in target.read_bytes(),
              repr(target.read_bytes()))
        check("mod 文件夹移走了", not (game / "mods" / "modAutoLoot").exists())
        check("卸载没有触发写保护拦截", len(guard.denied_hits()) == before,
              str(guard.denied_hits()[before:]))

        print("[5] 恢复")
        rs = install.restore(pkg_id, game)
        check("恢复成功", rs.get("ok") is True, str(rs)[:200])
        got2 = target.read_bytes()
        check("配置键回来了", b"[AutoLoot]" in got2 and b"radius=15" in got2, repr(got2))
        check("只出现一次", got2.count(b"[AutoLoot]") == 1, repr(got2))

        print("[6] 要写 dll 的包仍然需要二次确认")
        pv2 = install.preview(pkg_dll, game)
        check("dll 包标为高危", pv2["risk"] == "high", pv2["risk"])
        try:
            install.install(pkg_dll, game, on_conflict="replace")
            check("不给 allow_risk 时被拒", False, "居然装进去了")
        except install.NeedsConfirm:
            check("不给 allow_risk 时被拒", True)
        check("被拒之后 dll 没落地",
              not (game / "bin" / "x64_dx12" / "d3d11.dll").exists())

        print("[7] 写保护对沙盒外依然有效")
        check("真实游戏目录不可写",
              not guard.is_writable(r"D:\game\steam\steamapps\common\The Witcher 3\mods"))
        check("真实 autoloot.ini 不可写",
              not guard.is_writable(
                  r"D:\game\steam\steamapps\common\The Witcher 3\bin\config\base\autoloot.ini"))

        print()
        if fails:
            print(f"{len(fails)} 项不通过：" + "、".join(fails))
            return 1
        print("全部通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
