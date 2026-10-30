"""界面关掉之后，后台进程会不会自己退出。

用户双击 exe 打开界面，关掉窗口后如果进程还留着，任务管理器里会躺一个
看不见的 Python 进程，下次双击还会撞上「已经在运行」。所以做了个握手：
页面 pagehide → 报告一声 → 6 秒内没有新页面接手就退出。

这里测两种情形：
    A. 关窗（只报一声，没有新页面）→ 应该在 6 秒左右退出
    B. 刷新（报一声之后马上又加载了页面）→ 不能误杀

还有一组纯函数检查（1.2.7 加的）：第二条退出判据「窗口没了」。
那一枪（pagehide 的 beacon）实测会丢 —— 关窗瞬间渲染进程被收掉，包发不出去，
进程就永久留在后台。所以补了「直接问系统还有没有窗口」，这里只测判定逻辑，
真窗口留给冒烟验。

跑法：
    python tests/test_lifecycle.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

CHILD = r'''
import os, sys, time, urllib.request
sys.path.insert(0, r"{root}")
os.environ["WAASS_MULTI"] = "1"
os.environ["WAASS_DATA_DIR"] = r"{data}"
os.environ["WAASS_CONFIG_DIR"] = r"{data}"
from server import start_background, TOKEN

httpd, port = start_background(0, watch_close=True)


def hit(path, method="GET"):
    data = b"" if method == "POST" else None
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                                 method=method, data=data)
    try:
        urllib.request.urlopen(req, timeout=3).read()
        return True
    except Exception as exc:
        print("hit failed:", exc, flush=True)
        return False


mode = sys.argv[1]
hit("/api/bye?t=" + TOKEN, "POST")
if mode == "stay":
    time.sleep(0.3)
    hit("/")                       # 模拟刷新：新页面立刻加载
    time.sleep(9)
    print("STAY_OK", flush=True)
    sys.exit(0)

time.sleep(15)
print("STILL_ALIVE", flush=True)
sys.exit(3)
'''

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def run_child(mode: str, data: Path) -> tuple[int, float, str]:
    code = CHILD.format(root=ROOT, data=data)
    t0 = time.time()
    p = subprocess.run([sys.executable, "-c", code, mode],
                       capture_output=True, text=True, timeout=40)
    return p.returncode, time.time() - t0, (p.stdout or "") + (p.stderr or "")


def unit_checks() -> None:
    """退出判据的纯函数检查：不启服务、不看真窗口。"""
    print("\n[U] 退出判据（窗口兜底）")
    sys.path.insert(0, str(ROOT))
    import os
    os.environ.setdefault("WAASS_MULTI", "1")
    from core import winwatch
    from server import exit_reason

    NOW = 1000.0

    def life(**kw):
        base = {"bye_at": 0.0, "seen_page": True, "first_page": NOW - 100,
                "last_seen": NOW - 100, "app_window": True}
        base.update(kw)
        return base

    check("刚说再见还在宽限期内，不退",
          exit_reason(NOW, life(bye_at=NOW - 4), 1) == "")
    check("过了宽限期就按 bye 退",
          exit_reason(NOW, life(bye_at=NOW - 10), 1) == "bye")
    check("窗口没了、也安静够了 → 退",
          exit_reason(NOW, life(), 0) == "no-window")
    check("窗口还在 → 不退", exit_reason(NOW, life(), 1) == "")
    check("查不了窗口（-1）→ 只认 bye", exit_reason(NOW, life(), -1) == "")
    check("刚还有请求，先别急着退",
          exit_reason(NOW, life(last_seen=NOW - 2), 0) == "")
    check("页面才加载 5 秒，窗口可能还没弹出来",
          exit_reason(NOW, life(first_page=NOW - 5), 0) == "")
    check("从没加载过页面 → 不退",
          exit_reason(NOW, life(seen_page=False), 0) == "")
    check("窗口叫起来一小时没人加载页面 → 当作窗口没起来，退",
          exit_reason(NOW, life(seen_page=False, first_page=0, last_seen=0,
                                started_at=NOW - 3600), 0) == "no-page")
    check("窗口刚叫起来、还没加载完 → 不退",
          exit_reason(NOW, life(seen_page=False, first_page=0, last_seen=0,
                                started_at=NOW - 5), 0) == "")
    check("浏览器标签页模式（不是 --app 窗口）→ 不看窗口",
          exit_reason(NOW, life(app_window=False), 0) == "")

    n = winwatch.count()
    check("winwatch 查询不抛异常、返回整数", isinstance(n, int) and n >= -1, repr(n))
    t = winwatch.titles()
    check("titles() 要么是名单要么 None", t is None or isinstance(t, list), repr(type(t)))


def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="waass-life-"))

    print("\n[A] 关窗之后自己退出")
    code, took, out = run_child("die", base / "a")
    check("进程按预期结束了", code == 0, f"exit={code} out={out[-300:]}")
    check("在 6 秒宽限之后、15 秒之内退出", 5.0 <= took <= 12.0, f"{took:.1f}s")
    check("没有落到「还活着」的分支", "STILL_ALIVE" not in out, out[-200:])

    print("\n[B] 刷新页面不能误杀")
    code, took, out = run_child("stay", base / "b")
    check("页面还在时进程没退", "STAY_OK" in out and code == 0,
          f"exit={code} out={out[-300:]}")
    check("足足活过了宽限期", took >= 9.0, f"{took:.1f}s")

    unit_checks()

    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
