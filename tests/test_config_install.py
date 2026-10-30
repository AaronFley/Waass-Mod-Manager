"""合并安装的端到端：真实包 + 沙盒游戏目录。

用 Debug Console 这个真实包（它就往游戏自带的 general.ini 里加一行），
验证「只加该加的那行、不碰 ConfigVersion、卸载只撤自己那行」。
游戏目录是临时造的，真实游戏目录全程只读。

跑法：
    python tests/test_config_install.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DL = Path(r"D:\download")
Z_DEBUG = DL / "Debug Console (Next-Gen)-7172-1-0-1671042002.zip"

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


GENERAL_BASE = "[General]\r\nConfigVersion=7\r\n"
VISUALS_BASE = ("[Visuals]\r\nGamma=1\r\nMovieFramerate=30.0\r\n"
                "MovieUbersampling=true\r\n")


def make_game(base: Path) -> tuple[Path, Path]:
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    base_cfg = game / "bin" / "config" / "base"
    base_cfg.mkdir(parents=True)
    (base_cfg / "general.ini").write_bytes(GENERAL_BASE.encode())
    (base_cfg / "visuals.ini").write_bytes(VISUALS_BASE.encode())
    (base_cfg / "rendering.ini").write_bytes(b"[Rendering]\r\nTextureMemoryBudget=1200\r\n")
    pc = game / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
    pc.mkdir(parents=True)
    (game / "mods").mkdir()
    (game / "dlc").mkdir()

    cfg = base / "cfg"
    cfg.mkdir()
    (cfg / "mods.settings").write_text("", encoding="utf-8")
    data = base / "appdata"
    data.mkdir()
    (data / "config.json").write_text(json.dumps({"nexus_auto_meta": False}), encoding="utf-8")
    return game, cfg


def read(p: Path) -> str:
    return p.read_bytes().decode("utf-8-sig", "replace")


def make_fake_pkg(path: Path, name: str, files: dict[str, str]) -> Path:
    """现场造一个包（不用下载），用来测「两个 mod 抢同一个键」。"""
    with zipfile.ZipFile(path, "w") as z:
        for rel, text in files.items():
            z.writestr(rel, text)
    return path


def main() -> int:
    if not Z_DEBUG.is_file():
        print("缺少样本包，跳过：", Z_DEBUG.name)
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

        from core import config_patches, install, registry

        general = game / "bin" / "config" / "base" / "general.ini"

        print("\n[1] 预览：认出要合并、并拦下 ConfigVersion")
        pv = install.preview(Z_DEBUG, game)
        check("包认得出来", pv["ok"], str(pv.get("warnings")))
        check("没有算成冲突", pv["conflicts"] == [], str(pv["conflicts"]))
        m = {m["dst"]: m for m in pv["merges"]}
        check("general.ini 走合并", "bin/config/base/general.ini" in m, str(list(m)))
        mm = m.get("bin/config/base/general.ini", {})
        check("新增 1 项", mm.get("add") == 1, str(mm))
        check("ConfigVersion 被跳过", mm.get("skip") == 1, str(mm))

        print("\n[2] 安装：只加 DBGConsoleOn，不动 ConfigVersion")
        r = install.install(Z_DEBUG, game)
        check("安装成功", r["ok"], str(r))
        text = read(general)
        check("ConfigVersion 还是 7", "ConfigVersion=7" in text, repr(text))
        check("没有降成 5", "ConfigVersion=5" not in text, repr(text))
        check("DBGConsoleOn 已写入", "DBGConsoleOn=true" in text, repr(text))
        check("没有丢原节头", "[General]" in text, repr(text))

        pkg = registry.get(r["package_id"])
        check("档案记了 merged",
              "bin/config/base/general.ini" in (pkg.get("merged") or []), str(pkg.get("merged")))
        check("merged 不在 paths 里",
              "bin/config/base/general.ini" not in pkg["paths"], str(pkg["paths"]))
        check("隔离区里没有搬走这个文件",
              not (install.QUARANTINE / r["package_id"] / "bin/config/base/general.ini").exists())

        print("\n[3] 基线：改之前的原样留了一份")
        bl = config_patches.read_baseline("bin/config/base/general.ini")
        check("有基线", bl is not None)
        check("基线是安装前的内容", bl and bl[0] == GENERAL_BASE, repr(bl[0] if bl else None))

        print("\n[4] 卸载：只撤掉自己加的那行")
        u = install.uninstall(r["package_id"], game)
        check("卸载成功", u["ok"], str(u))
        text = read(general)
        check("DBGConsoleOn 已撤掉", "DBGConsoleOn" not in text, repr(text))
        check("ConfigVersion 仍是 7", "ConfigVersion=7" in text, repr(text))
        check("文件还在（没被删）", general.is_file())
        check("报告了撤销记录", bool(u["reverted"]), str(u["reverted"]))

        print("\n[5] 恢复：改动重新打上，不重复")
        rs = install.restore(r["package_id"], game)
        check("恢复成功", rs["ok"], str(rs))
        text = read(general)
        check("DBGConsoleOn 回来了", "DBGConsoleOn=true" in text, repr(text))
        check("只出现一次", text.count("DBGConsoleOn") == 1, repr(text))
        check("ConfigVersion 还是 7", "ConfigVersion=7" in text, repr(text))
        install.uninstall(r["package_id"], game)

        print("\n[6] 两个包抢同一个键：后装的生效，卸掉后回到前一个的值")
        a = make_fake_pkg(base / "A-1001-1-0-1700000001.zip", "A", {
            "bin/config/base/rendering.ini": "[Rendering]\r\nTextureMemoryBudget=3000\r\n",
        })
        b = make_fake_pkg(base / "B-1002-1-0-1700000002.zip", "B", {
            "bin/config/base/rendering.ini": "[Rendering]\r\nTextureMemoryBudget=5000\r\n",
        })
        rendering = game / "bin" / "config" / "base" / "rendering.ini"
        ra = install.install(a, game)
        check("A 写入 3000", "TextureMemoryBudget=3000" in read(rendering), read(rendering))
        rb = install.install(b, game)
        check("B 覆盖成 5000", "TextureMemoryBudget=5000" in read(rendering), read(rendering))
        install.uninstall(rb["package_id"], game)
        txt = read(rendering)
        check("卸掉 B 回到 A 的 3000", "TextureMemoryBudget=3000" in txt, repr(txt))
        install.uninstall(ra["package_id"], game)
        txt = read(rendering)
        check("卸掉 A 回到游戏的 1200", "TextureMemoryBudget=1200" in txt, repr(txt))

        print("\n[7] 手工改过就不硬还原")
        c = make_fake_pkg(base / "C-1003-1-0-1700000003.zip", "C", {
            "bin/config/base/rendering.ini": "[Rendering]\r\nTextureMemoryBudget=2000\r\n",
        })
        rc = install.install(c, game)
        hand = read(rendering).replace("TextureMemoryBudget=2000", "TextureMemoryBudget=7777")
        rendering.write_bytes(hand.encode())
        uc = install.uninstall(rc["package_id"], game)
        txt = read(rendering)
        check("你改的 7777 保住了", "TextureMemoryBudget=7777" in txt, repr(txt))
        check("报出冲突", bool(uc["merge_conflicts"]), str(uc["merge_conflicts"]))

        print("\n[8] 沙盒外的路径一律拒绝")
        from core import guard, safety
        outside = base.parent / "outside.ini"
        try:
            safety.assert_writable(outside)
            check("沙盒外被拒绝", False, "居然没报错")
        except Exception as e:                      # noqa: BLE001
            check("沙盒外被拒绝", "WriteDenied" in type(e).__name__ or "授权" in str(e), str(e))

    print()
    if fails:
        print(f"{len(fails)} 项失败：")
        for f in fails:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
