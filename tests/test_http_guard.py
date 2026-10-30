"""HTTP 层的第一道闸门：Host 校验、跨站拒绝、鉴权、路径穿越。

这里只测「不该通的路通不过」，不测业务 —— 业务在各自的 core 测试里。
跑法：
    python tests/test_http_guard.py

为什么单独有这一块：token 是直接写进首页 HTML 的，而首页不鉴权。恶意网页
把自己的域名 rebind 到 127.0.0.1 之后，浏览器会以「同源」身份请求 / 拿走
token，之后 install / uninstall 全通。Host 校验是这条链唯一便宜的断点。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    fails = []

    def check(name, cond, extra=""):
        print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"  {extra}"))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        game = base / "game"
        (game / "bin" / "x64_dx12").mkdir(parents=True)
        (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
        (game / "mods" / "modAlpha").mkdir(parents=True)
        (game / "dlc").mkdir()
        cfg = base / "cfg"
        cfg.mkdir()
        (cfg / "mods.settings").write_text(
            "[modAlpha]\nEnabled=1\nPriority=10\n", encoding="utf-8")

        # 环境必须在 import server 之前设好：DATA_DIR 是模块级常量
        os.environ["WAASS_CONFIG_DIR"] = str(cfg)
        os.environ["WAASS_GAME_DIR"] = str(game)
        os.environ["WAASS_DATA_DIR"] = str(base / "appdata")
        os.environ["WAASS_TEST_MODE"] = "1"
        os.environ["WAASS_SANDBOX_DIR"] = str(base)

        import server

        httpd = server.serve(0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

        def probe(path="/api/health", method="GET", host=None, site=None,
                  token=False, body=None):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
            h = {}
            if host is not None:
                h["Host"] = host
            if site:
                h["Sec-Fetch-Site"] = site
            if token:
                h["X-Waass-Token"] = server.TOKEN
            raw = b""
            if body is not None:
                raw = json.dumps(body).encode()
                h["Content-Type"] = "application/json"
            c.request(method, path, body=raw or None, headers=h)
            r = c.getresponse()
            payload = r.read()
            try:
                data = json.loads(payload)
            except Exception:                        # noqa: BLE001 非 JSON 也算合法返回
                data = {}
            return r.status, data, payload

        local = f"127.0.0.1:{port}"

        try:
            print("\n[1] 正常来源必须照常能用（别把自家界面挡了）")
            st, _, _ = probe(host=local)
            check("/api/health 200", st == 200, st)
            st, _, _ = probe(host=f"localhost:{port}")
            check("localhost 也放行", st == 200, st)
            st, _, page = probe(path="/", host=local)
            check("首页 200 且把 token 写进去了",
                  st == 200 and server.TOKEN.encode() in page, st)

            print("\n[2] DNS rebinding：陌生 Host 一律 403")
            st, data, _ = probe(host="evil.example.com")
            check("/api/health 403", st == 403, st)
            check("返回 JSON 而不是 HTML", data.get("error") == "forbidden", data)
            st, _, _ = probe(path="/", host="evil.example.com")
            check("首页也挡住（token 不能下发）", st == 403, st)
            st, _, _ = probe(path="/", host="127.0.0.1.nip.io")
            check("长得像本机的域名也挡住", st == 403, st)

            print("\n[3] 跨站发起一律 403")
            st, _, _ = probe(host=local, site="cross-site")
            check("cross-site 403", st == 403, st)
            st, _, _ = probe(host=local, site="same-origin")
            check("same-origin 放行", st == 200, st)
            st, _, _ = probe(host=local)
            check("不带 Sec-Fetch-Site（curl/脚本）放行", st == 200, st)

            print("\n[4] 鉴权没被 Host 校验顶掉")
            st, data, _ = probe("/api/state", host=local)
            check("无 token 401", st == 401, st)
            st, _, _ = probe("/api/state", host=local, token=True)
            check("带 token 200", st == 200, st)

            print("\n[5] 关窗招呼（sendBeacon）不能被误伤")
            # sendBeacon 带不了自定义头，只能靠 query 里的 token；
            # 它一定会带 Host 和 Sec-Fetch-Site: same-origin。
            st, _, _ = probe(f"/api/bye?t={server.TOKEN}", method="POST",
                             host=local, site="same-origin")
            check("bye 204", st == 204, st)
            server.LIFECYCLE["bye_at"] = 0.0          # 别真的触发退出

            print("\n[6] 路径穿越与目录逃逸")
            st, _, _ = probe("/static/../server.py", host=local)
            check("静态资源穿越 404", st == 404, st)
            st, _, _ = probe("/thumbs/../../server.py", host=local)
            check("封面目录穿越 404", st == 404, st)
            for key in ("..", r"..\..", "../..", "..%5c.."):
                st, _, _ = probe("/api/open-folder", method="POST", host=local,
                                 token=True, body={"key": key})
                check(f"open-folder 拒绝 {key!r}", st == 404, st)

            print("\n[7] 服务端版本号跟着 APP_VERSION 走")
            from core import appinfo
            check("server_version 不含 0.1",
                  "0.1" not in server.Handler.server_version,
                  server.Handler.server_version)
            check("等于 APP_VERSION",
                  server.Handler.server_version == f"Waass/{appinfo.APP_VERSION}",
                  server.Handler.server_version)
        finally:
            httpd.shutdown()

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：" + "、".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
