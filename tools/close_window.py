"""给指定标题的窗口发 WM_CLOSE，用来验证「关窗即退出」。"""
import ctypes
import ctypes.wintypes as wt
import sys
import time

# 中文输出要在英文版 Windows（cp1252 控制台）上也打得出来。
# 这个脚本不 import core，自己切一次，别让一句 print 把工具打崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

u = ctypes.windll.user32
u.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]

target = (sys.argv[1] if len(sys.argv) > 1 else "Waass").lower()
found = []


def cb(hwnd, _):
    if not u.IsWindowVisible(hwnd):
        return True
    n = u.GetWindowTextLengthW(hwnd)
    if not n:
        return True
    b = ctypes.create_unicode_buffer(n + 1)
    u.GetWindowTextW(hwnd, b, n + 1)
    if target in b.value.lower():
        found.append((hwnd, b.value))
    return True


u.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
for hwnd, title in found:
    print("关闭:", title)
    u.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
if not found:
    print("没找到标题含 %r 的窗口" % target)
    sys.exit(1)
