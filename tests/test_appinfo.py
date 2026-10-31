"""程序自身更新检查的测试：缓存、条件请求、失败时的口径。

全部在本机临时目录与一个本地 HTTP 服务里跑，不访问外网。
跑法：
    python tests/test_appinfo.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


class Feed:
    """一个会说 ETag 的最小更新源。"""

    def __init__(self) -> None:
        self.version = "9.9.9"
        self.url = "https://github.com/AaronFley/Waass-Mod-Manager/releases/tag/v9.9.9"
        self.notes = ("# Waass 1.0.0\n\n**· 新增若干功能**\n· 修复若干问题\n"
                      "| 文件 | 大小 |\n| --- | --- |\n> 提示一句")
        self.etag = '"v1"'
        self.hits = 0
        self.not_modified = 0
        self.conditional = 0
        self.offline = False


def serve(feed: Feed) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):                      # noqa: N802  http.server 约定
            if feed.offline:
                self.send_error(503, "offline")
                return
            feed.hits += 1
            if self.headers.get("If-None-Match"):
                feed.conditional += 1
                if self.headers.get("If-None-Match") == feed.etag:
                    feed.not_modified += 1
                    self.send_response(304)
                    self.send_header("ETag", feed.etag)
                    self.end_headers()
                    return
            body = json.dumps({"version": feed.version, "url": feed.url,
                               "notes": feed.notes}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("ETag", feed.etag)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):             # 别把测试输出弄脏
            pass

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="waass-upd-"))
    data = tmp / "appdata"
    data.mkdir()
    os.environ.update({
        "WAASS_DATA_DIR": str(data),
        "WAASS_CONFIG_DIR": str(tmp / "cfg"),
        "WAASS_TEST_MODE": "1",
    })

    from core import appinfo, i18n
    i18n.set_lang("zh")

    feed = Feed()
    srv = serve(feed)
    port = srv.server_address[1]
    cfg = data / "config.json"

    def write_cfg(obj: dict) -> None:
        cfg.write_text(json.dumps(obj), encoding="utf-8")

    write_cfg({"update_feed": f"http://127.0.0.1:{port}/latest"})

    print("[1] 首次检查与缓存")
    r1 = appinfo.check()
    check("能判出新版本", r1.get("newer") is True, str(r1))
    check("拿到发布页地址", r1.get("url") == feed.url, str(r1))
    check("拿到更新说明", "新增" in (r1.get("notes") or ""), str(r1))
    check("缓存文件已落盘", (data / "update.json").is_file())
    check("首次请求数 1", feed.hits == 1, str(feed.hits))

    hits = feed.hits
    r2 = appinfo.check()
    check("24 小时内不重复联网", feed.hits == hits, str(feed.hits))
    check("复用结果时标 cached", r2.get("cached") is True, str(r2))
    check("缓存结果同样判有新版本", r2.get("newer") is True, str(r2))

    print("\n[2] 手动检查不吃缓存")
    r3 = appinfo.check(force=True)
    check("手动检查会真去问一次", feed.hits == hits + 1, str(feed.hits))
    check("带上了 If-None-Match", feed.conditional >= 1, str(feed.conditional))
    check("服务器回 304 时结论不变", r3.get("newer") is True and r3.get("latest") == "9.9.9",
          str(r3))
    check("304 不等于重新下载", feed.not_modified >= 1, str(feed.not_modified))

    print("\n[3] 缓存过期后走条件请求")
    cache_file = data / "update.json"
    entry = json.loads(cache_file.read_text(encoding="utf-8"))
    check("缓存里记了 ETag", bool(entry.get("etag")), str(entry))
    entry["ts"] = 0
    cache_file.write_text(json.dumps(entry), encoding="utf-8")
    before, cond = feed.hits, feed.conditional
    r4 = appinfo.check()
    check("过期后重新联网", feed.hits == before + 1, str(feed.hits))
    check("过期后是条件请求", feed.conditional == cond + 1, str(feed.conditional))
    check("304 之后结论仍可用", r4.get("newer") is True, str(r4))
    fresh = json.loads(cache_file.read_text(encoding="utf-8"))
    check("时间戳已刷新", float(fresh.get("ts") or 0) > 0, str(fresh.get("ts")))

    print("\n[4] 线上版本低于本地时不提示")
    feed.version = "1.0.0"
    feed.etag = '"v2"'
    r5 = appinfo.check(force=True)
    check("更旧的版本不算更新", r5.get("newer") is False, str(r5))
    check("仍如实给出线上版本号", r5.get("latest") == "1.0.0", str(r5))

    print("\n[5] 联网失败的口径")
    feed.offline = True
    r6 = appinfo.check(force=True)
    check("手动检查失败要说实话", r6.get("ok") is False and bool(r6.get("message")), str(r6))
    # 让缓存过期，否则自动检查会直接吃缓存、根本不联网
    stale = json.loads(cache_file.read_text(encoding="utf-8"))
    stale["ts"] = 0
    cache_file.write_text(json.dumps(stale), encoding="utf-8")
    r7 = appinfo.check()
    check("自动检查退回上次结论", r7.get("ok") is True, str(r7))
    check("退回的结果标了 stale", r7.get("stale") is True, str(r7))
    check("退回时仍记得线上版本", r7.get("latest") == "1.0.0", str(r7))

    print("\n[6] 没有缓存又断网")
    cache_file.unlink()
    r8 = appinfo.check()
    check("没缓存就如实报失败", r8.get("ok") is False, str(r8))

    print("\n[7] 关掉检查")
    write_cfg({"update_check": False})
    before_off = feed.hits
    r9 = appinfo.check(force=True)
    check("关掉后标 configured=False", r9.get("configured") is False, str(r9))
    check("关掉后不再联网", feed.hits == before_off, str(feed.hits))

    srv.shutdown()

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：" + "、".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
