"""冷启动自检：空 data/ + 假游戏目录 + 无网络配置，跑一遍所有只读接口。

这不是单元测试，是「别人第一次双击拿到的东西能不能跑」的模拟。
会自己建临时目录，跑完删掉，不碰真实 data/、不碰真实游戏目录。

    python tools/coldstart.py
    python tools/coldstart.py --no-game     # 再模拟一次「机器上没装游戏」
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def build_fake_game(base: Path) -> Path:
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ\x00v 5.01c\x00")
    (game / "mods" / "modTestMod").mkdir(parents=True)
    (game / "mods" / "modTestMod" / "content").mkdir()
    (game / "dlc" / "dlcTest").mkdir(parents=True)
    cfg = base / "cfg"
    cfg.mkdir()
    (cfg / "mods.settings").write_text(
        "[modTestMod]\nEnabled=1\nPriority=10\n", encoding="utf-8")
    return game


GET_ROUTES = [
    "/api/health", "/api/state", "/api/about", "/api/journal", "/api/packages",
    "/api/saves", "/api/archives", "/api/settings", "/api/conflicts",
    "/api/library", "/api/storage", "/api/track", "/api/merger/state",
    "/api/nexus/status", "/api/localize", "/api/localize/history",
    "/", "/static/app.js", "/static/style.css",
]
# 故意不探 /api/localize/panel：它会顺手生成一份待翻译任务包（写盘），
# 不是纯只读接口。这里要证明的是「只是打开看一眼，硬盘上不留东西」。


def main() -> int:
    no_game = "--no-game" in sys.argv
    base = Path(tempfile.mkdtemp(prefix="waass-cold-"))
    fails = []
    httpd = None
    try:
        game = None if no_game else build_fake_game(base)
        data = base / "data"          # 故意不创建：让程序自己去建
        os.environ["WAASS_DATA_DIR"] = str(data)
        os.environ["WAASS_CONFIG_DIR"] = str(base / "cfg")
        os.environ["WAASS_TEST_MODE"] = "1"
        os.environ["WAASS_SANDBOX_DIR"] = str(base)
        os.environ.pop("WAASS_GAME_DIR", None)
        if game:
            os.environ["WAASS_GAME_DIR"] = str(game)
        os.environ.pop("WAASS_READONLY", None)

        if no_game:
            # 光是「不设 WAASS_GAME_DIR」不够：开发机上真的装着游戏，注册表和
            # Steam 库会被探测到，于是又扫了一遍真实目录。要模拟新电脑，得把
            # 探测和兜底一起掐掉 —— 这才是「他电脑上什么都没有」的样子。
            from core import paths as _p
            _p._CANDIDATES = []
            _p._probe_candidates = lambda: []
            _p.forget_detected()

        print(f"数据目录: {data}（不存在）")
        print(f"游戏目录: {game or '（故意不给）'}")
        print(f"配置目录: {base / 'cfg'}\n")

        # 1.2.9：真实启动的第一步就是自检（run.py 在起服务之前跑）。这里照做 ——
        # 否则冷启动验的是「没有自检的那个程序」，和发出去的 exe 不是一回事。
        from core import appinfo, selfcheck
        sc = selfcheck.run(appinfo.APP_VERSION)
        print(f"[自检] kind={sc['kind']} level={sc['level']} "
              f"items={[i['code'] for i in sc['items']]}")
        if sc["level"] == "error":
            fails.append(f"自检报错：{sc['items']}")

        import server
        from core.paths import detect_game_root

        root = detect_game_root(refresh=True)
        print(f"[探测] game_root = {root}")
        if game and root != game:
            fails.append("给了 WAASS_GAME_DIR 却没认出来")

        print("\n[扫描]")
        server.STATE.refresh()
        res = server.STATE.scan_result
        if res is None:
            if game:
                fails.append(f"有游戏却扫不出东西：{server.STATE.last_error}")
            print("  无游戏 → 空结果（正确）")
        else:
            print(f"  模组 {len(res.mods)} 个，警告 {len(getattr(res, 'warnings', []) or [])} 条")

        httpd = server.serve(0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        print("\n[接口]")
        for path in GET_ROUTES:
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            try:
                c.request("GET", path, headers={
                    "Host": f"127.0.0.1:{port}", "X-Waass-Token": server.TOKEN})
                r = c.getresponse()
                body = r.read()
                # 没游戏时汉化那几个接口会明确回 400（「还没扫描到 mod」），
                # 这是对的 —— 它们本来就没有东西可列。别把它当服务坏了。
                ok = r.status == 200 or (game is None and r.status == 400)
                note = ""
                if not ok:
                    note = body[:120].decode("utf-8", "replace")
                    fails.append(f"{path} → {r.status} {note}")
                print(f"  {'OK  ' if ok else 'FAIL'} {r.status} {path} {' ' + note if note else ''}")
            except Exception as exc:                 # noqa: BLE001
                fails.append(f"{path} 抛异常 {type(exc).__name__}: {exc}")
                print(f"  FAIL {path} {exc}")
            finally:
                c.close()

        print("\n[落地文件] 程序在 data/ 里自己建了什么：")
        # 只读访问不该写任何东西 —— 这既是「别人拿到是干净的」，
        # 也是我们自己的规矩：没点过写操作，就别在硬盘上留痕。
        #
        # 唯一的例外是 1.2.9 起的两份「程序自己的账」：meta.json 记数据格式版本、
        # selfcheck.json 记这次启动的自检结论。它们不是用户数据，每次启动都要写。
        ALLOWED = {"meta.json", "selfcheck.json"}
        if data.is_dir():
            landed = sorted(e.name for e in data.iterdir())
            print("  " + ("、".join(landed) if landed else "（空目录）"))
            extra = [n for n in landed if n not in ALLOWED]
            if extra:
                fails.append(f"只读访问却写了文件：{extra}")
            else:
                print("  只有版本记录与自检记录（允许）")
        else:
            print("  没有创建 data/（正确：什么都没点，就不该落盘）")

        print("\n[无残留] 检查程序有没有往游戏目录里写东西：")
        if game:
            unexpected = []
            for p in game.rglob("*"):
                rel = p.relative_to(game)
                if rel.parts[0] in ("mods", "dlc", "bin") and p.is_file():
                    if not str(rel).lower().startswith(("bin\\x64_dx12\\witcher3.exe",
                                                       "mods\\", "dlc\\")):
                        unexpected.append(str(rel))
            print("  未写入任何多余文件" if not unexpected else f"  多出来: {unexpected}")
            if unexpected:
                fails.append(f"往游戏目录写了东西: {unexpected}")
        else:
            print("  跳过（没有游戏目录）")
    finally:
        if httpd is not None:
            httpd.shutdown()
        shutil.rmtree(base, ignore_errors=True)

    print("\n" + ("冷启动通过" if not fails
                  else f"{len(fails)} 项不合格：\n  - " + "\n  - ".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
