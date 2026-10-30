"""游戏目录探测：Steam 库 / GOG 注册表 / Epic 清单 / 配置 / 兜底。

只测解析和拼接逻辑 —— 注册表读不到就自然落空，不会因为 CI 上没有 Steam
而失败。真正的注册表读取走的是同一批纯函数，所以这里覆盖到的部分就是
最可能写错的部分。

跑法：
    python tests/test_paths_detect.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_game(base: Path, name: str = "The Witcher 3") -> Path:
    g = base / name
    (g / "bin" / "x64_dx12").mkdir(parents=True, exist_ok=True)
    (g / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    return g


def main() -> int:
    fails = []

    def check(name, cond, extra=""):
        print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"  {extra}"))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["WAASS_DATA_DIR"] = str(base / "appdata")
        os.environ["WAASS_TEST_MODE"] = "1"
        os.environ["WAASS_SANDBOX_DIR"] = str(base)
        os.environ.pop("WAASS_GAME_DIR", None)

        from core import paths

        print("\n[1] libraryfolders.vdf 解析")
        vdf = (
            '"libraryfolders"\n{\n'
            '  "0"\n  {\n    "path"    "C:\\\\Program Files (x86)\\\\Steam"\n  }\n'
            '  "1"\n  {\n    "path"    "D:\\\\SteamLibrary"\n    "label"   ""\n  }\n'
            '  "2"\n  {\n    "path"    ""\n  }\n'
            '}\n'
        )
        got = paths._parse_library_paths(vdf)
        check("解析出 2 个库（空 path 丢弃）", len(got) == 2, got)
        check("反斜杠还原成单层", str(got[0]) == r"C:\Program Files (x86)\Steam", got[0])
        check("第二个库", str(got[1]) == r"D:\SteamLibrary", got[1])

        print("\n[2] 老格式 libraryfolders.vdf（值是路径字符串，没有 path 键）")
        old = '"libraryfolders"\n{\n  "TimeNextStatsReport"  "123"\n  "1"  "E:\\\\Games\\\\Steam"\n}\n'
        check("老格式没有 path 键 → 解析为空，不误报",
              paths._parse_library_paths(old) == [], paths._parse_library_paths(old))

        print("\n[3] Steam 库根 → 游戏目录拼接")
        lib = base / "SteamLib"
        want = make_game(lib / "steamapps" / "common")
        orig_steam = paths._steam_roots
        paths._steam_roots = lambda: [lib]
        try:
            cands = paths._probe_candidates()
            check("拼出 steamapps/common/The Witcher 3", want in cands, cands[:3])
            check("候选都指向游戏目录本身", all(not str(c).endswith("common") for c in cands), cands[:3])
        finally:
            paths._steam_roots = orig_steam

        print("\n[4] Epic 清单（.item 是 JSON，只信 InstallLocation）")
        ep_base = base / "ProgramData"
        man = ep_base / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
        man.mkdir(parents=True)
        epic_game = make_game(base / "EpicGames", "witcher3")
        (man / "abc.item").write_text(json.dumps({
            "DisplayName": "The Witcher 3",          # 名字对但路径是错的，不能信
            "InstallLocation": "Z:\\nope",
        }), encoding="utf-8")
        (man / "def.item").write_text(json.dumps({
            "DisplayName": "猎魔人3",                 # 名字本地化了也能认
            "InstallLocation": str(epic_game),
        }), encoding="utf-8")
        (man / "broken.item").write_text("{ 这不是 JSON", encoding="utf-8")

        orig_pd = os.environ.get("PROGRAMDATA")
        os.environ["PROGRAMDATA"] = str(ep_base)
        try:
            got = paths._epic_roots()
            check("坏清单不炸、好清单照读", len(got) == 2, got)
            check("拿到的是 InstallLocation", epic_game in got, got)
        finally:
            if orig_pd is None:
                os.environ.pop("PROGRAMDATA", None)
            else:
                os.environ["PROGRAMDATA"] = orig_pd

        print("\n[5] 探测优先级：配置 > 注册表 > 兜底")
        # [5] 起要把「注册表探测」和「硬编码兜底」都掐掉，否则这台机器上真的
        # 装了游戏，断言「找不到」就会假失败 —— 测试要测逻辑，不能测本机装没装。
        cfg_game = make_game(base / "CfgGame")
        orig_probe, orig_cands = paths._probe_candidates, paths._CANDIDATES
        paths._probe_candidates = lambda: []
        paths._CANDIDATES = []
        try:
            paths.forget_detected()
            check("什么都没有时返回 None", paths.detect_game_root(refresh=True) is None)

            paths.save_config({"game_root": str(cfg_game)})
            check("配置生效", paths.detect_game_root(refresh=True) == cfg_game)
            check("save_config 会清缓存", paths.detect_game_root() == cfg_game)

            print("\n[6] 缓存会自己失效（游戏被卸掉/挪走）")
            check("命中缓存", paths.detect_game_root() == cfg_game)
            (cfg_game / "bin" / "x64_dx12" / "witcher3.exe").unlink()
            check("exe 没了 → 立刻返回 None，不返回死路径",
                  paths.detect_game_root() is None)

            print("\n[8] 注册表/清单全部抛异常时不能崩")
            def boom():
                raise OSError("模拟注册表被锁")
            paths._probe_candidates = orig_probe
            origs = (paths._steam_roots, paths._gog_roots, paths._epic_roots)
            paths._steam_roots = boom
            paths._gog_roots = boom
            paths._epic_roots = boom
            try:
                check("_probe_candidates 返回空表而不是抛",
                      paths._probe_candidates() == [], "抛异常了")
                paths.forget_detected()
                check("detect_game_root 仍能返回 None", paths.detect_game_root(refresh=True) is None)
            finally:
                paths._steam_roots, paths._gog_roots, paths._epic_roots = origs
        finally:
            paths._probe_candidates = orig_probe
            paths._CANDIDATES = orig_cands

        print("\n[7] 不再带作者本机路径")
        for c in paths._CANDIDATES:
            check(f"没有个人路径 {c}", "game\\steam" not in c.lower() and "AI claw" not in c)

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：" + "、".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
