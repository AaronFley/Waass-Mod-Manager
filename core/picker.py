"""选文件 / 选目录：只用系统原生对话框，不把 tkinter 当必需品。

为什么（1.3.0 的教训）
----------------------
打包时 PyInstaller 只打「构建用的那个 Python 里装了的」模块。开发机的 CPython
没有编 Tcl/Tk，于是 `import tkinter` 在源码环境跑得通、打包出来却缺模块：
exe 里点「浏览」直接 500，而测试全绿 —— 因为测试也在源码环境里跑。
所以选目录改走 Windows 自己的 COM 对话框（IFileOpenDialog），和解释器带不带
Tcl/Tk 无关；tkinter 只剩最后一道兜底。

原生对话框的两个好处
--------------------
* 能把主界面窗口设成 owner，弹框必定盖在主界面上、跟着主界面一起最小化；
  用户点了按钮不会「什么都没发生」（Tk 的独立窗口会被 Windows 压在后面）。
* 过滤器、最近位置、图标都是系统样式，比手绘的稳。

选文件用 GetOpenFileNameW，选目录用 IFileOpenDialog（Vista 起的那套，
带地址栏和搜索框）；老 API SHBrowseForFolder 留作第二兜底。
"""
from __future__ import annotations

import ctypes
import sys
import uuid
from pathlib import Path

from core import i18n

# ---- GetOpenFileNameW 的常量（winuser.h / commdlg.h） ----
OFN_NOCHANGEDIR = 0x00000008
OFN_PATHMUSTEXIST = 0x00000800
OFN_FILEMUSTEXIST = 0x00001000
OFN_EXPLORER = 0x00080000
OFN_DONTADDTORECENT = 0x02000000

BIF_RETURNONLYFSDIRS = 0x0001
BIF_EDITBOX = 0x0010
BIF_NEWDIALOGSTYLE = 0x0040

# ---- IFileOpenDialog 的常量 ----
HRESULT = ctypes.c_long
_S_OK, _S_FALSE = 0, 1
_CLSCTX_INPROC_SERVER = 0x1
_COINIT_APARTMENTTHREADED = 0x2
_COINIT_DISABLE_OLE1DDE = 0x4
_FOS_PICKFOLDERS = 0x20
_FOS_FORCEFILESYSTEM = 0x40
_FOS_PATHMUSTEXIST = 0x800
_SIGDN_FILESYSPATH = 0x80058000
_HR_CANCELLED = 0x800704C7          # HRESULT_FROM_WIN32(ERROR_CANCELLED)

# kind -> (标题, [(说明, 通配符)])
PRESETS = {
    "json": ("pick.json_title", [("pick.json_filter", "*.json"), ("pick.all_files", "*.*")]),
    "archive": ("pick.archive_title", [("pick.archive_filter", "*.zip;*.7z;*.rar"),
                                       ("pick.all_files", "*.*")]),
    "exe": ("pick.exe_title",
            [("Script Merger", "*.exe"), ("pick.all_files", "*.*")]),
    "file": ("pick.file_title", [("pick.all_files", "*.*")]),
}


class PickError(OSError):
    """选择框开不出来。

    `code` 给界面判断（前端不许拿译文匹配），`detail` 只进日志。
    """

    def __init__(self, code: str = "picker_unavailable", detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


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


# ---------------- COM 小工具 ----------------

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    u = uuid.UUID(text)
    return _GUID(u.fields[0], u.fields[1], u.fields[2],
                 (ctypes.c_ubyte * 8)(*u.bytes[8:]))


_CLSID_FILE_OPEN_DIALOG = _guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")
_IID_IFILE_OPEN_DIALOG = _guid("D57C7288-D4AD-4768-BE02-9D969532D960")
_IID_ISHELL_ITEM = _guid("43826D1E-E718-42EE-BC55-A1E261C37BFE")

# IFileDialog 的 vtable 序号（IUnknown 3 个 + IModalWindow 1 个 + 自身）
_V_SHOW = 3
_V_SET_OPTIONS = 9
_V_GET_OPTIONS = 10
_V_SET_FOLDER = 12
_V_SET_TITLE = 17
_V_GET_RESULT = 20
_V_RELEASE = 2
# IShellItem：IUnknown(0-2) BindToHandler GetParent GetDisplayName GetAttributes Compare
_SI_GET_DISPLAY_NAME = 5


def _vt(ptr, index: int, restype, *argtypes):
    """按 vtable 序号取方法。ctypes 没有 COM 支持，只能自己走指针。"""
    table = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_void_p))[0]
    fn = ctypes.cast(table, ctypes.POINTER(ctypes.c_void_p))[index]
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(fn)


def _vista(title: str, initial: str = "", show: bool = True):
    """IFileOpenDialog 选目录。

    返回 (后端标记, 路径)；**None 表示这套 API 在这儿用不了**（要让调用方
    去试下一个兜底）。用户取消返回 (标记, "") —— 和「用不了」必须分开，
    否则一取消就被当成"没有原生框"再弹一个 Tk 框。
    """
    if sys.platform != "win32":
        return None
    from ctypes import wintypes

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p,
                                       wintypes.DWORD, ctypes.POINTER(_GUID),
                                       ctypes.POINTER(ctypes.c_void_p)]
    ole32.CoCreateInstance.restype = HRESULT
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
    if hr not in (_S_OK, _S_FALSE):
        # 这个线程的 COM 已经被按别的模式初始化过（RPC_E_CHANGED_MODE），
        # 单线程的对话框在这儿开不了，交给下一个兜底。
        return None
    try:
        dlg = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_FILE_OPEN_DIALOG), None,
                                    _CLSCTX_INPROC_SERVER,
                                    ctypes.byref(_IID_IFILE_OPEN_DIALOG),
                                    ctypes.byref(dlg))
        if hr < 0 or not dlg:
            return None
        try:
            opts = wintypes.DWORD(0)
            flags = _FOS_PICKFOLDERS | _FOS_FORCEFILESYSTEM | _FOS_PATHMUSTEXIST
            if _vt(dlg, _V_GET_OPTIONS, HRESULT,
                   ctypes.POINTER(wintypes.DWORD))(dlg, ctypes.byref(opts)) >= 0:
                flags |= opts.value
            _vt(dlg, _V_SET_OPTIONS, HRESULT, wintypes.DWORD)(dlg, wintypes.DWORD(flags))
            if title:
                _vt(dlg, _V_SET_TITLE, HRESULT, wintypes.LPCWSTR)(dlg, title)
            if initial:
                item0 = ctypes.c_void_p()
                if ctypes.windll.shell32.SHCreateItemFromParsingName(
                        wintypes.LPCWSTR(initial), None,
                        ctypes.byref(_IID_ISHELL_ITEM),
                        ctypes.byref(item0)) >= 0 and item0:
                    try:
                        _vt(dlg, _V_SET_FOLDER, HRESULT, ctypes.c_void_p)(dlg, item0)
                    finally:
                        _vt(item0, _V_RELEASE, ctypes.c_ulong)(item0)
            if not show:
                return ("vista", "")
            hr = _vt(dlg, _V_SHOW, HRESULT, wintypes.HWND)(dlg, wintypes.HWND(_owner_hwnd()))
            if (hr & 0xFFFFFFFF) == _HR_CANCELLED:
                return ("vista", "")
            if hr < 0:
                return None
            item = ctypes.c_void_p()
            hr = _vt(dlg, _V_GET_RESULT, HRESULT,
                     ctypes.POINTER(ctypes.c_void_p))(dlg, ctypes.byref(item))
            if hr < 0 or not item:
                return ("vista", "")
            try:
                raw = wintypes.LPWSTR()
                hr = _vt(item, _SI_GET_DISPLAY_NAME, HRESULT, ctypes.c_int,
                         ctypes.POINTER(wintypes.LPWSTR))(item, _SIGDN_FILESYSPATH,
                                                          ctypes.byref(raw))
                if hr < 0 or not raw.value:
                    return ("vista", "")
                path = str(raw.value)
                ole32.CoTaskMemFree(ctypes.cast(raw, ctypes.c_void_p))
                return ("vista", path)
            finally:
                _vt(item, _V_RELEASE, ctypes.c_ulong)(item)
        finally:
            _vt(dlg, _V_RELEASE, ctypes.c_ulong)(dlg)
    finally:
        try:
            ole32.CoUninitialize()
        except OSError:
            pass


def _shbrowse_dir(title: str = ""):
    """老式 SHBrowseForFolder。第二兜底：有的系统上新对话框被策略禁用。"""
    if sys.platform != "win32":
        return None
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

    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = HRESULT
    hr = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED | _COINIT_DISABLE_OLE1DDE)
    if hr not in (_S_OK, _S_FALSE):
        return None
    try:
        shell = ctypes.windll.shell32
        display = ctypes.create_unicode_buffer(260)
        bi = BROWSEINFOW()
        bi.hwndOwner = wintypes.HWND(_owner_hwnd())
        bi.pszDisplayName = ctypes.cast(display, wintypes.LPWSTR)
        bi.lpszTitle = title or i18n.t("pick.dir_title")
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
            ole32.CoTaskMemFree(ctypes.c_void_p(pidl))
    finally:
        try:
            ole32.CoUninitialize()
        except OSError:
            pass


def _native_file(kind: str) -> str | None:
    """成功返回路径；用户取消返回 ''；这套 API 不可用返回 None。"""
    if sys.platform != "win32":
        return None
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

    title_key, groups = PRESETS.get(kind, PRESETS["file"])
    title = i18n.t(title_key)
    # 过滤器是「说明\0通配符\0」重复，最后再来一个 \0 收尾
    filt = "".join(f"{i18n.t(label)} ({pat.replace(';', ', ')})\0{pat}\0" for label, pat in groups) + "\0"

    buf = ctypes.create_unicode_buffer(32768)
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.hwndOwner = wintypes.HWND(_owner_hwnd())
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


def _tk_pick(kind: str) -> str:
    """最后兜底：tkinter。源码运行通常有，打包版可能没有（见模块开头）。"""
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
            return filedialog.askdirectory(title=i18n.t("pick.dir_title")) or ""
        title_key, groups = PRESETS.get(kind, PRESETS["file"])
        title = i18n.t(title_key)
        types = [(i18n.t(label), pat) for label, pat in groups]
        return filedialog.askopenfilename(title=title, filetypes=types) or ""
    finally:
        root.destroy()


def probe() -> str:
    """不弹窗，只回答「这台机器 / 这份 exe 里哪条路能用」。给打包自检调用。

    返回 "vista" / "shbrowse" / "tk"，都不可用返回空串。
    """
    if sys.platform == "win32":
        try:
            if _vista("", show=False):
                return "vista"
        except Exception:                        # noqa: BLE001
            pass
        try:
            import tkinter  # noqa: F401
            return "tk"
        except Exception:                        # noqa: BLE001
            return ""
    try:
        import tkinter  # noqa: F401
        return "tk"
    except Exception:                            # noqa: BLE001
        return ""


def pick(kind: str = "file") -> str:
    """kind: dir / json / archive / file。返回路径，取消返回空字符串。

    顺序：原生框 → 老式文件夹框（仅 dir）→ tkinter。三条都不可用时抛
    PickError，让界面说「直接粘贴路径」，而不是把 ImportError 甩给用户。
    """
    if kind == "dir":
        title = i18n.t("pick.dir_title")
        if sys.platform == "win32":
            try:
                got = _vista(title, _initial_dir(kind))
                if got is not None:
                    return got[1]
            except Exception:                    # noqa: BLE001 换下一个兜底
                pass
            try:
                res = _shbrowse_dir(title)
                if res is not None:
                    return str(res)
            except Exception:                    # noqa: BLE001
                pass
    elif sys.platform == "win32":
        try:
            res = _native_file(kind)
            if res is not None:
                return str(res)
        except Exception:                        # noqa: BLE001 退回 Tk
            pass

    try:
        return _tk_pick(kind)
    except PickError:
        raise
    except Exception as exc:                     # noqa: BLE001 没有 Tk / 没有 GUI
        raise PickError("picker_unavailable",
                        f"no dialog backend: {type(exc).__name__}: {exc}") from exc
