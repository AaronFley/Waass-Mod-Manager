"""控制台编码：让中文日志在英文版 Windows 上也能打印出来。

Windows 控制台默认用本地代码页 —— 英文系统是 cp1252。往 stdout 写中文会抛
`UnicodeEncodeError`，而 `print` 就在启动路径上，一崩就是整个程序起不来。

本机是中文系统（cp936，中文能编），所以这个错**在开发机上永远看不到**；
GitHub Actions 的 windows-latest 用的是英文镜像，第一次跑 CI 就把它照出来了：
28 套测试里 20 多套在第一条中文输出就崩，而失败原因看起来像「测试全挂了」，
很容易被误判成逻辑问题。

做法两步：

1. 先把控制台代码页切成 UTF-8（65001），Windows Terminal 能正常显示中文；
2. 再把标准流重设为 `utf-8` + `errors="replace"` —— 万一某个老终端仍不支持，
   最多把一个字打成问号，**绝不让程序死在一句日志上**。

没有控制台时不做事：打包成 `--windowed` 的 exe 里 `sys.stdout is None`
（run.py 的 `_silence_streams()` 会把它们接到 devnull）。
"""
from __future__ import annotations

import ctypes
import os
import sys

_DONE = False


def enable_utf8() -> None:
    """切成 UTF-8；可重复调用，只生效一次。"""
    global _DONE
    if _DONE:
        return
    _DONE = True

    if os.name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            k32.SetConsoleOutputCP(65001)
            k32.SetConsoleCP(65001)
        except Exception:                           # noqa: BLE001 没有控制台 / 老系统
            pass

    for name in ("stdout", "stderr", "stdin"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass                                    # 已经关掉 / 不是文本流


def can_print(text: str) -> bool:
    """这段字在当前标准流上能不能原样打出来（给「要不要降级成英文」用）。"""
    enc = getattr(getattr(sys, "stdout", None), "encoding", None) or "ascii"
    try:
        text.encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False
