"""选文件 / 选目录：优先用系统原生对话框。

为什么不用 tkinter
------------------
tkinter 的对话框是一个独立的 Tk 顶层窗口，进程又是在后台被调起来的；
Windows 的前台焦点规则会把这种窗口压在别的窗口下面 —— 用户点了按钮，
看起来什么都没发生，实际弹框藏在主界面背后。

原生对话框可以把主界面窗口设成 owner（GetOpenFileNameW 的 hwndOwner），
这样它必定盖在主界面上、也跟着主界面一起最小化，符合直觉。
另外原生框的过滤器、最近位置、图标都是系统样式，比 Tk 手绘的稳。

非 Windows（或调用失败）退回 tkinter，功能不至于没有。
"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

# ---- GetOpenFileNameW 的常量（winuser.h / commdlg.h） ----
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
OFN_DONTADDTORECENT = 0x02000000

BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040

# kind -> (标题, [(说明, 通配符)])
PRESETS = {
    "json": ("选择译文 JSON 文件", [("译文 JSON", "*.json"), ("所有文件", "*.*")]),
    "archive": ("选择 mod 压缩包", [("mod 压缩包", "*.zip;*.7z;*.rar"), ("所有文件", "*.*")]),
    "exe": ("选择 Script Merger 的主程序",
            [("Script Merger", "*.exe"), ("所有文件", "*.*")]),
    "file": ("选择文件", [("所有文件", "*.*")]),
}


def _u32():
    return ctypes.windll.user32


def _owner_hwnd() -> int:
    """主界面窗口。找不到就返回 0（对话框仍然能弹，只是没有 owner）。"""
    u32 = _u32()
    h = u32.FindWindowW(None, "Waass Mod Manager")
    if h:
        return int(h)
    found: list[int] = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _lparam):                      # noqa: ANN001
        if not u32.IsWindowVisible(hwnd):
            return True
        n = u32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u32.GetWindowTextW(hwnd, buf, n + 1)
        if "Waass" in buf.value:
            found.append(int(hwnd))
        return True

    try:
        u32.EnumWindows(_cb, 0)
    except OSError:
        return 0
    return found[0] if found else 0


def _initial_dir(kind: str) -> str:
    """尽量落在用户上次该用的地方，少点几次。"""
    if kind == "json":
        try:
            from .paths import DATA_DIR
            d = DATA_DIR / "translate"
            if d.is_dir():
                return str(d)
        except Exception:                        # noqa: BLE001
            pass
    if kind == "archive":
        try:
            from . import downloads
            for d in downloads.dirs():
                if Path(d["path"]).is_dir():
                    return d["path"]
        except Exception:                        # noqa: BLE001
            pass
    for guess in (Path.home() / "Downloads", Path.home() / "Desktop", Path.home()):
        if guess.is_dir():
            return str(guess)
    return str(Path.home())


def _native_file(kind: str) -> str | None:
    """成功返回路径；用户取消返回 ''；这套 API 不可用返回 None。"""
    from ctypes import wintypes

    class OPENFILENAMEW(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD),
            ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE),
            ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR),
            ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD),
            ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD),
            ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD),
            ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR),
            ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD),
            ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR),
            ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p),
            ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p),
            ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    title, groups = PRESETS.get(kind, PRESETS["file"])
    # 过滤器是「说明\0通配符\0」重复，最后再来一个 \0 收尾
    filt = "".join(f"{label} ({pat.replace(';', ', ')})\0{pat}\0" for label, pat in groups) + "\0"

    buf = ctypes.create_unicode_buffer(32768)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = _owner_hwnd()
    ofn.lpstrFilter = filt            # ctypes 会自己留住这个字符串
    ofn.lpstrFile = ctypes.cast(buf, wintypes.LPWSTR)
    ofn.nMaxFile = len(buf)
    ofn.lpstrInitialDir = _initial_dir(kind)
    ofn.lpstrTitle = title
    ofn.Flags = (OFN_EXPLORER | OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST
                 | OFN_NOCHANGEDIR | OFN_DONTADDTORECENT)

    comdlg = ctypes.windll.comdlg32
    comdlg.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    comdlg.GetOpenFileNameW.restype = wintypes.BOOL
    ok = comdlg.GetOpenFileNameW(ctypes.byref(ofn))
    if ok:
        return buf.value
    err = comdlg.CommDlgExtendedError()
    if err:                                     # 0 = 用户取消，非 0 才是真出错
        raise OSError(f"系统文件选择框报错（代码 {err}）")
    return ""


def _native_dir(title: str = "选择文件夹") -> str | None:
    """老式 SHBrowseForFolder：只在有独立消息循环的线程里可靠。
    这里保留实现但不默认启用（见 pick 里的说明）。"""
    from ctypes import wintypes

    class BROWSEINFOW(ctypes.Structure):
        _fields_ = [
            ("hwndOwner", wintypes.HWND),
            ("pidlRoot", ctypes.c_void_p),
            ("pszDisplayName", wintypes.LPWSTR),
            ("lpszTitle", wintypes.LPCWSTR),
            ("ulFlags", wintypes.UINT),
            ("lpfn", ctypes.c_void_p),
            ("lParam", wintypes.LPARAM),
            ("iImage", ctypes.c_int),
        ]

    try:
        ctypes.windll.ole32.CoInitialize(None)
    except OSError:
        pass

    shell = ctypes.windll.shell32
    display = ctypes.create_unicode_buffer(260)
    bi = BROWSEINFOW()
    bi.hwndOwner = _owner_hwnd()
    bi.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
    bi.lpszTitle = title
    bi.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_EDITBOX

    shell.SHBrowseForFolderW.argtypes = [ctypes.POINTER(BROWSEINFOW)]
    shell.SHBrowseForFolderW.restype = ctypes.c_void_p
    pidl = shell.SHBrowseForFolderW(ctypes.byref(bi))
    if not pidl:
        return ""
    out = ctypes.create_unicode_buffer(32768)
    try:
        if shell.SHGetPathFromIDListW(ctypes.c_void_p(pidl), out):
            return out.value
        return ""
    finally:
        ctypes.windll.ole32.CoTaskMemFree(ctypes.c_void_p(pidl))


def _tk_pick(kind: str) -> str:
    """兜底：没有原生对话框时用 Tk。"""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    try:
        if kind == "dir":
            return filedialog.askdirectory(title="选择文件夹") or ""
        title, groups = PRESETS.get(kind, PRESETS["file"])
        types = [(label, pat) for label, pat in groups]
        return filedialog.askopenfilename(title=title, filetypes=types) or ""
    finally:
        root.destroy()


def pick(kind: str = "file") -> str:
    """kind: dir / json / archive / file。返回路径，取消返回空字符串。

    选目录仍然交给 Tk：从 HTTP 请求线程调老式文件夹框（SHBrowseForFolder）
    实测会卡半分钟才返回，Tk 的目录框反而稳。选文件走原生框 —— 它才是
    「点了没反应」的那个，原生框带 owner 就压不到后台去了。
    """
    if kind != "dir" and sys.platform == "win32":
        try:
            res = _native_file(kind)
            if res is not None:
                return str(res)
        except Exception:                        # noqa: BLE001 退回 Tk
            pass
    return _tk_pick(kind)
