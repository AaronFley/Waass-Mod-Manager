"""看看某个进程开了哪些窗口 —— 排查「是不是弹了个多余的框」。

用法：
    python tools/inspect_windows.py Waass
    python tools/inspect_windows.py msedge

输出每个可见顶层窗口的 标题 / 类名 / 位置尺寸 / 所属进程。
界面用的是 Edge 的 --app 窗口，正常情况下只应该有一个 1280×820 的框；
如果多出一个小窗口，多半是浏览器的某个提示（登录、同步、崩溃恢复）。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

# 中文输出要在英文版 Windows（cp1252 控制台）上也打得出来。
# 这个脚本不 import core，自己切一次，别让一句 print 把工具打崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

user32 = ctypes.windll.user32

EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def title_of(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def class_of(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def rect_of(hwnd) -> RECT:
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    want = sys.argv[1].lower()

    import os
    import subprocess

    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True)
    text = (out.stdout or b"").decode("gbk", errors="ignore")
    pids = set()
    for line in text.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if parts and want in parts[0].lower():
            try:
                pids.add(int(parts[1]))
            except (IndexError, ValueError):
                continue

    rows: list[tuple] = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = pid_of(hwnd)
        if pid not in pids:
            return True
        t = title_of(hwnd)
        if not t:
            return True
        r = rect_of(hwnd)
        rows.append((pid, class_of(hwnd), t, r.right - r.left, r.bottom - r.top,
                     r.left, r.top))
        return True

    user32.EnumWindows(EnumWindowsProc(cb), 0)

    print(f"进程 {sorted(pids) or '（没找到）'}")
    if not rows:
        print("没有可见窗口。")
        return 0
    for pid, cls, t, w, h, x, y in rows:
        print(f"  [{pid}] {w}x{h} @({x},{y})  {cls}  「{t}」")
    print(f"共 {len(rows)} 个可见窗口")
    return 0


if __name__ == "__main__":
    sys.exit(main())
