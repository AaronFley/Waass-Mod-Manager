"""窗口还在不在 —— 退出判据的兜底。

为什么需要它
------------
1.2.6 之前，进程退出只有一条路：页面 `pagehide` 时用 `sendBeacon` 打一声招呼，
服务端等 6 秒没有新页面接手就退出。实测（2026-10-30）出了问题：窗口关了，
进程在后台活了四十多分钟。原因是那一枪不可靠 —— 关窗瞬间渲染进程可能被直接
收掉，或者浏览器内核已经在拆网络服务，beacon 的包根本没发出去。

这里不依赖页面：直接问系统「还有没有标题对得上的可见窗口」。
只在 Windows 上工作；其它平台返回 -1 表示「不知道」，调用方必须按「不知道」
处理，不能当成「窗口没了」。
"""
from __future__ import annotations

import sys

# 界面窗口的标题，来自 web/templates/index.html 的 <title>。
# 用包含匹配且不分大小写，外壳偶尔会在标题后缀上加东西。
TITLE_HINT = "waass mod manager"


def supported() -> bool:
    return sys.platform.startswith("win")


def titles() -> list[str] | None:
    """所有可见窗口的标题。查不了就返回 None（和「真没有窗口」区分开）。"""
    if not supported():
        return None
    try:
        import ctypes
        import ctypes.wintypes as wt

        u32 = ctypes.windll.user32
        out: list[str] = []
        proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def _cb(hwnd, _lparam):                       # noqa: ANN001 ctypes 回调
            if u32.IsWindowVisible(hwnd):
                n = u32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    u32.GetWindowTextW(hwnd, buf, n + 1)
                    out.append(buf.value)
            return True

        if not u32.EnumWindows(proc(_cb), 0):
            return None
        return out
    except Exception:                                 # noqa: BLE001 查不到就当不知道
        return None


def count(part: str = TITLE_HINT) -> int:
    """标题含 part（不分大小写）的可见窗口数。 -1 = 这个平台查不了。

    注意最小化的窗口仍然是「可见」的（IsWindowVisible 返回真），
    所以最小化不会让人误判成退出。
    """
    t = titles()
    if t is None:
        return -1
    needle = part.lower()
    return sum(1 for x in t if needle in x.lower())
