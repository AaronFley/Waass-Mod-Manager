"""界面关掉之后，后台进程会不会自己退出。

用户双击 exe 打开界面，关掉窗口后如果进程还留着，任务管理器里会躺一个
看不见的 Python 进程，下次双击还会撞上「已经在运行」。所以做了个握手：
页面 pagehide → 报告一声 → 6 秒内没有新页面接手就退出。

这里测两种情形：
    A. 关窗（只报一声，没有新页面）→ 应该在 6 秒左右退出
    B. 刷新（报一声之后马上又加载了页面）→ 不能误杀

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

    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
