"""控制台编码：中文日志在英文版 Windows 上也要打得出来。

抓这个 bug 的经过：GitHub Actions 的 windows-latest 是英文镜像，控制台代码页
cp1252。28 套测试里有 20 多套在第一条中文输出上抛 `UnicodeEncodeError` 全挂，
看起来像「测试集体失败」，其实一行逻辑都没错——本机是中文系统（cp936），
永远看不见。

测四件事：
    1. 对照组：cp1252 下直接 print 中文确实会崩（证明这个测试有意义）
    2. 调过 enable_utf8() 之后，同样环境能打印中文
    3. 只 import core（不手动调）就够了 —— 包入口负责，靠人记着调迟早漏
    4. 重复调用不出错，且不会把已经好的流弄坏

跑法：
    python tests/test_console.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 这个测试自己也要在 cp1252 下活得下来（它正是被那样跑到的）
from core.console import can_print, enable_utf8                 # noqa: E402

enable_utf8()

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  OK   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}   {detail}")


def run_child(code: str, encoding: str = "cp1252") -> subprocess.CompletedProcess:
    """在「英文版 Windows 控制台」里跑一段代码。"""
    env = dict(os.environ, PYTHONIOENCODING=encoding, PYTHONUTF8="0")
    return subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, timeout=60)


def main() -> int:
    print("\n[1] 对照组：不做任何处理，cp1252 下打印中文确实会崩")
    r = run_child("print('中文')")
    check("退出码非 0", r.returncode != 0, f"rc={r.returncode}")
    check("报的是编码错", b"UnicodeEncodeError" in r.stderr,
          r.stderr.decode("utf-8", "replace")[-200:])

    print("\n[2] 调过 enable_utf8() 之后可以打印")
    r = run_child("import sys; sys.path.insert(0, '.')\n"
                  "from core.console import enable_utf8\n"
                  "enable_utf8()\n"
                  "print('中文没问题：启用 / 禁用')")
    check("退出码 0", r.returncode == 0, r.stderr.decode("utf-8", "replace")[-200:])
    out = r.stdout.decode("utf-8", "replace")
    check("中文原样打出来了", "中文没问题：启用 / 禁用" in out, repr(out[-80:]))

    print("\n[3] 只 import core 就够（包入口负责，不靠人记）")
    r = run_child("import sys; sys.path.insert(0, '.')\n"
                  "import core\n"
                  "print('包入口已接管')")
    check("退出码 0", r.returncode == 0, r.stderr.decode("utf-8", "replace")[-200:])
    check("中文原样打出来了", "包入口已接管" in r.stdout.decode("utf-8", "replace"),
          repr(r.stdout[-80:]))

    print("\n[4] 本进程内：重复调用不出错，流仍是可用的")
    enable_utf8()
    enable_utf8()
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    check("标准输出是 utf-8", enc.replace("_", "-") in ("utf-8", "utf8", "cp65001"), enc)
    check("can_print 认得中文", can_print("中文") is True)
    check("can_print 认得英文", can_print("ascii") is True)
    sys.stdout.write("")
    check("写完不炸", True)

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
