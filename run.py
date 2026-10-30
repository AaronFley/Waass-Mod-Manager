"""Waass Mod Manager 启动入口。

用法：
    python run.py                 正常启动（有内置窗口就用窗口，否则开浏览器）
    python run.py --browser       强制用默认浏览器
    python run.py --serve-only    只起服务，不开界面（调试用）
    python run.py --port 8765     指定端口

关于那个「正在你的所有设备上同步你的浏览数据」弹窗
--------------------------------------------------
界面是用 Edge / Chrome 的 --app 模式开的独立窗口。第一次给一个全新的
用户数据目录时，浏览器会当成「你刚装了浏览器」，顺手用 Windows 登录的
微软账号做一次隐式登录，然后弹同步提示。这不是本程序的行为，但看着很
像，而且重复出现。

处理办法：
  * 专用且干净的 profile 目录（data/webview），预置 First Run 标记；
  * --disable-sync 关掉数据同步；
  * --disable-features 关掉隐式登录 / 登录拦截 / 同步推广；
  * 限制磁盘缓存，别让它悄悄长到几百 MB。
"""
from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 打包成无控制台窗口的程序后，stdout 是 None，任何 print 都会炸
def _silence_streams() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            try:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
            except OSError:
                pass


_silence_streams()

from core import i18n                                               # noqa: E402
from server import STATE, mark_app_window, start_background          # noqa: E402

MUTEX_NAME = "Local\\WaassModManager_SingleInstance"
ERROR_ALREADY_EXISTS = 183
_mutex_handle = None


def single_instance() -> bool:
    """靠命名内核对象做单实例，进程退出自动释放，不会留垃圾文件。

    开发/演示时用 WAASS_MULTI=1 跳过，方便同时跑正式实例和隔离实例做对比。
    """
    if os.environ.get("WAASS_MULTI") == "1":
        return True
    global _mutex_handle
    k32 = ctypes.windll.kernel32
    _mutex_handle = k32.CreateMutexW(None, False, MUTEX_NAME)
    return k32.GetLastError() != ERROR_ALREADY_EXISTS


BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

# 一次性合并写，浏览器只认最后一个同名参数
_QUIET_FEATURES = ",".join([
    "msEdgeIdentityFeatures",     # Edge 的账号身份功能（隐式登录的来源）
    "msImplicitSignin",           # 用 Windows 账号自动登录
    "msEdgeSyncPromo",            # 同步推广提示
    "EdgeSyncPromo",
    "SigninInterception",         # 登录拦截浮层
    "msEdgeFirstRunExperience",
])


def _prepare_profile() -> Path:
    """专门给界面窗口用的 profile，和你的日常浏览器完全隔离。"""
    from core.paths import DATA_DIR
    from core import storage

    # 上次如果不是正常关闭，浏览器内核可能留下了几 MB 指标文件，先扫干净
    try:
        storage.trim_browser_cache()
    except Exception:                                  # noqa: BLE001 清不掉不影响启动
        pass

    profile = DATA_DIR / "webview"
    try:
        profile.mkdir(parents=True, exist_ok=True)
        mark = profile / "First Run"
        if not mark.is_file():
            mark.write_text("", encoding="utf-8")
    except OSError:
        pass
    return profile


def open_app_window(url: str) -> bool:
    """用浏览器内核开一个无地址栏的独立窗口，观感接近原生应用，且零依赖。"""
    import subprocess

    profile = _prepare_profile()
    for exe in BROWSERS:
        if not Path(exe).is_file():
            continue
        args = [
            exe,
            f"--app={url}",
            f"--user-data-dir={profile}",
            "--window-size=1280,820",
            "--no-first-run",
            "--no-default-browser-check",
            "--no-service-autorun",
            "--disable-sync",
            "--disable-background-networking",
            "--disable-component-update",
            "--disable-breakpad",
            "--disable-session-crashed-bubble",
            "--disable-application-cache",
            "--disable-gpu-shader-disk-cache",
            f"--disable-features={_QUIET_FEATURES}",
            "--disk-cache-size=16777216",      # 缓存上限 16 MB
            "--media-cache-size=1048576",
        ]
        try:
            subprocess.Popen(args, close_fds=True)
            return True
        except OSError:
            continue
    return False


def alert(msg: str, title: str = "Waass Mod Manager", icon: int = 0x40) -> None:
    """无控制台窗口时，至少有办法把话说到人眼前。"""
    try:
        ctypes.windll.user32.MessageBoxW(None, msg, title, icon | 0x40000)  # 置顶
    except Exception:                                  # noqa: BLE001
        print(msg)


def _crash_hook() -> None:
    """未处理的异常：写到 data/crash.log（只保留最近一次），并且告诉用户。"""
    def hook(exc_type, exc, tb):
        from core.paths import DATA_DIR
        import traceback
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "crash.log").write_text(text, encoding="utf-8")
        except OSError:
            pass
        alert(i18n.t("run.crash", what=f"{exc_type.__name__}: {exc}"),
              icon=0x10)
        os._exit(1)
    sys.excepthook = hook


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--browser", action="store_true")
    ap.add_argument("--serve-only", action="store_true")
    args = ap.parse_args()

    _crash_hook()

    if not single_instance():
        alert(i18n.t("run.already_running"), icon=0x30)
        return 1

    try:
        httpd, port = start_background(args.port, watch_close=not args.serve_only)
    except OSError as exc:
        alert(i18n.t("run.server_start_failed", err=exc), icon=0x10)
        return 1

    url = f"http://127.0.0.1:{port}/"

    # 扫描放后台，界面先出来，别让人对着白屏等
    threading.Thread(target=STATE.refresh, daemon=True).start()

    print(f"Waass Mod Manager 已启动：{url}")

    if args.serve_only:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    if not args.browser:
        try:
            import webview                        # pywebview（装了就用它）
            for _ in range(120):
                if STATE.scan_result is not None:
                    break
                time.sleep(0.1)
            webview.create_window(
                "Waass Mod Manager", url,
                width=1280, height=820, min_size=(980, 620),
                # 1.2.3 起默认主题是深色（#1E1E1E），这里必须跟着深色走。
                # 主题偏好存在浏览器 profile 的 localStorage 里，服务端读不到，
                # 所以没法真「跟随设置」—— 深色是默认值，取它最不容易闪得刺眼。
                background_color="#1E1E1E",
            )
            webview.start()
            return 0
        except ImportError:
            pass
        except Exception as exc:                   # 窗口起不来就退回浏览器
            print(f"内置窗口启动失败（{exc}），改用浏览器内核")

    opened = open_app_window(url) if not args.browser else False
    if not opened:
        webbrowser.open(url)
    else:
        # 独立窗口：允许「按窗口判退出」这条慢路径（浏览器标签页模式不能用）
        mark_app_window()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
