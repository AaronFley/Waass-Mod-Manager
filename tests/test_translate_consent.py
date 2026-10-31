"""首次同意 + 任务启动的接口级测试（不联网）。

要钉住的两件事：
1. 全新数据目录下 POST /api/localize/run 只回 need_consent，这一趟一个字都不发出去；
2. POST /api/localize/consent 之后再 run，任务能正常建起来。

所有写盘都落在 WAASS_SANDBOX_DIR 里；出网一律用 mock 拦掉 ——
测试不只断言「没人调 urlopen」，还把 urlopen 换成必然失败的桩，
就算真有人绕过断言发请求，也不会打到真实服务上。
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="waass-consent-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_SANDBOX)
os.environ["WAASS_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WAASS_CONFIG_DIR"] = str(_SANDBOX / "cfg")

import server                                            # noqa: E402
from core import i18n, localize_job as LJ, translate     # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def fake_w3(path: Path, texts: list[str]) -> None:
    """造一份最小的 w3strings：只要扫描器认得出来、能提出条目就够了。"""
    body = bytearray(b"RTSW" + (162).to_bytes(4, "little") + b"\x00\x00\x01\xe8")
    for t in texts:
        b = t.encode("utf-8")
        body += bytes([len(b)]) + b + b"\x00\x00\x00"
    body += b"\x00" * 3
    path.write_bytes(bytes(body))


def post(port: int, path: str, body: dict) -> tuple[int, dict]:
    """用 http.client 而不是 urllib —— 这样 mock 掉 urllib.request.urlopen
    也不会把测试自己的请求一起拦了。"""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        conn.request("POST", path, body=json.dumps(body).encode("utf-8"), headers={
            "X-Waass-Token": server.TOKEN,
            "Content-Type": "application/json",
        })
        r = conn.getresponse()
        raw = r.read().decode("utf-8")
        return r.status, json.loads(raw or "{}")
    finally:
        conn.close()


def no_net(req, timeout=None):
    raise urllib.error.URLError("测试里不许出网")


def main() -> int:
    game = _SANDBOX / "game"
    content = game / "mods" / "modA" / "content"
    content.mkdir(parents=True)
    (game / "bin" / "x64").mkdir(parents=True)
    (game / "bin" / "x64" / "witcher3.exe").write_bytes(b"MZ")
    # 语言表要够大：扫描器对太小的表会当成加密文件跳过
    fake_w3(content / "cn.w3strings",
            ["Auto equip", "Hoods only", "Show in menu",
             "Auto unequip", "Animate cloaks", "Allow toggling"])

    i18n.bootstrap()
    cfg_file = translate.CONF_FILE
    check("测试数据落在沙盒里",
          str(_SANDBOX).lower() in str(cfg_file).lower(), str(cfg_file))

    with mock.patch.object(server, "detect_game_root", lambda: game), \
            mock.patch("urllib.request.urlopen", side_effect=no_net) as net:
        httpd = server.serve(0)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            print("[1] 没同意过：run 只回 need_consent")
            check("起始状态是没同意", translate.config().get("consent") is False)

            net.reset_mock()
            code, d = post(port, "/api/localize/run",
                           {"mods": ["modA"], "cats": ["menu"]})
            calls = net.call_count

            check("HTTP 200（不是错误）", code == 200, str(code))
            check("回的是 need_consent", d.get("need_consent") is True, str(d))
            check("只回这两个字段",
                  set(d) == {"need_consent", "engines"}, str(sorted(d)))
            ids = [e.get("id") for e in (d.get("engines") or [])]
            check("列的是真会用的引擎", ids == ["indextranslate", "mymemory"],
                  str(d.get("engines")))
            check("带上了会连的域名",
                  {e.get("id"): e.get("target") for e in (d.get("engines") or [])} == {
                      "indextranslate": "index-translate.bilibili.com",
                      "mymemory": "mymemory.translated.net",
                  }, str(d.get("engines")))
            check("全程没有出网", calls == 0, "urlopen 被调了 %d 次" % calls)
            check("没建任务", not (d.get("id") or d.get("items")), str(d))
            check("也没写配置文件", not cfg_file.exists(), str(cfg_file))

            print("\n[2] 点「同意并继续」")
            code, c = post(port, "/api/localize/consent", {})
            check("HTTP 200", code == 200, str(code))
            check("回来了 ok + consent", c.get("ok") is True and c.get("consent") is True, str(c))
            check("配置文件写出来了", cfg_file.exists(), str(cfg_file))
            raw = json.loads(cfg_file.read_text(encoding="utf-8"))
            check("consent 落盘", raw.get("consent") is True, str(raw))
            check("新引擎也进了落盘的 order",
                  raw.get("order") == ["indextranslate", "mymemory"], str(raw.get("order")))
            check("配置里没有下线引擎",
                  "google" not in json.dumps(raw, ensure_ascii=False), str(raw))

            print("\n[3] 同意之后再 run")
            net.reset_mock()
            code, job = post(port, "/api/localize/run",
                             {"mods": ["modA"], "cats": ["menu"]})
            check("HTTP 200", code == 200, str(code))
            check("这次没有 need_consent", not job.get("need_consent"), str(job))
            check("拿到带 id 的任务快照", bool(job.get("id")), str(job))
            check("状态是 running", job.get("status") == "running", str(job))
            check("有待翻条目", (job.get("total") or 0) > 0, str(job.get("total")))

            # 后台线程是异步的，等它真的开始干活再断言「引擎被调过」
            jid = str(job.get("id") or "")
            deadline = time.time() + 10
            snap: dict = {}
            while jid and time.time() < deadline:
                snap = LJ.snapshot(jid)
                if snap.get("status") != "running":
                    break
                time.sleep(0.05)
            check("后台任务确实跑起来了（引擎被调用）",
                  net.call_count > 0 or bool(snap.get("done")),
                  "calls=%d done=%s" % (net.call_count, snap.get("done")))

            print("\n[4] 关掉模态那条路（不发 consent 直接再 run）")
            translate.save_config({"consent": False})
            net.reset_mock()
            code, d2 = post(port, "/api/localize/run",
                            {"mods": ["modA"], "cats": ["menu"]})
            check("仍然只回 need_consent", d2.get("need_consent") is True, str(d2))
            check("仍然不出网", net.call_count == 0, str(net.call_count))
        finally:
            httpd.shutdown()

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
