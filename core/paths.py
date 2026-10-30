"""路径探测：游戏根目录、配置文件目录、本程序数据目录。

所有操作都是只读的，不会创建或修改游戏目录里的任何东西。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import winreg
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))


def _writable(d: Path) -> bool:
    """这个目录能不能写。决定数据放哪，所以要先试一下再承诺。"""
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".waass-write-test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def dir_writable(d: Path) -> bool:
    """这个目录能不能往里新建文件 —— 只问权限，**不真的写**。

    为什么要问：Steam 默认把游戏装在 `C:\\Program Files (x86)`，那里的
    目录对普通用户只读。程序自己（`data/`）能写，游戏目录不能写，于是
    用户点「安装」「启用」时才会撞上 WinError 5，而那时他已经等了半天。

    实现：用 CreateFileW 以「允许新建文件」的权限打开目录本身，带
    FILE_FLAG_BACKUP_SEMANTICS（否则打不开目录）。系统会替我们把 ACL 算一遍，
    拿得到句柄就是能写。全程不创建、不修改任何文件 —— 游戏目录一根汗毛都不动。
    """
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
        ]
        FILE_ADD_FILE = 0x0002
        FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
        OPEN_EXISTING = 3
        INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
        share = 0x1 | 0x2 | 0x4          # 允许别人同时读/写/删，别挡着游戏
        h = k32.CreateFileW(str(d), FILE_ADD_FILE, share, None,
                            OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, None)
        if not h or h == INVALID_HANDLE_VALUE:
            return False
        k32.CloseHandle(h)
        return True
    except Exception:                       # noqa: BLE001 问不出来就当作能写
        return True


def _data_home() -> Path:
    """打包成 exe 之后，数据不能放在临时解包目录里（退出就没了）。

    优先进程旁边的 data/（便携，拷走就能用）；exe 在 Program Files 之类
    不可写的位置时，退到 %LOCALAPPDATA%\\WaassModManager。
    """
    exe_dir = Path(sys.executable).resolve().parent
    if _writable(exe_dir / "data"):
        return exe_dir
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home()
    return base / "WaassModManager"


if FROZEN:
    # 只读资源（web/）在 PyInstaller 的解包目录里
    RES_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    APP_DIR = _data_home()
else:
    APP_DIR = Path(__file__).resolve().parent.parent
    RES_DIR = APP_DIR

DATA_DIR = Path(os.environ.get("WAASS_DATA_DIR") or (APP_DIR / "data"))  # 本程序自己的数据
CONFIG_FILE = DATA_DIR / "config.json"

ENV_GAME = "WAASS_GAME_DIR"

# 常见安装位置。这里只放**公开的默认位置** —— 任何作者本机的自定义路径都
# 不该出现，那是别人机器上永远不成立的假设（1.2.5 之前这里第一条就是
# D:\game\steam\...，"自动识别"在作者机器上自然一次命中，别人的机器则全落空）。
_CANDIDATES = [
    r"C:\Program Files (x86)\Steam\steamapps\common\The Witcher 3",
    r"C:\Program Files\Steam\steamapps\common\The Witcher 3",
    r"D:\SteamLibrary\steamapps\common\The Witcher 3",
    r"E:\SteamLibrary\steamapps\common\The Witcher 3",
    r"C:\Program Files (x86)\GOG Galaxy\Games\The Witcher 3",
    r"C:\Program Files (x86)\GOG Galaxy\Games\The Witcher 3 GOTY",
    r"D:\GOG Games\The Witcher 3",
]

_STEAM_COMMON = Path("steamapps") / "common"
_STEAM_GAME_DIRS = ("The Witcher 3", "The Witcher 3 GOTY")
_GOG_IDS = ("1495134320",)          # 巫师3 的 GOG gameID（次世代版）

# 探测会读注册表和几个清单文件，比纯 is_dir 贵得多。而它每次 /api/state 都会
# 被问到，所以缓存一下；目录被删/被挪时靠 is_game_root 复验兜回来。
_DETECT = {"at": 0.0, "root": None}
_DETECT_TTL = 20.0


def _parse_library_paths(vdf: str) -> list[Path]:
    """从 libraryfolders.vdf 抓出每个 Steam 库根。

    不做 VDF 解析器：这文件只有一层结构，"path" 是唯一用得上的字段，新旧两种
    写法（老格式的 "1" 块、新格式的 "0" 块）在这里长得一样。正则抓它最省事，
    也不会因为某个无关字段缺失就整份解析失败。
    """
    roots: list[Path] = []
    for m in re.finditer(r'"path"\s+"((?:[^"\\]|\\.)*)"', vdf):
        raw = m.group(1).replace("\\\\", "\\").replace('\\"', '"').strip()
        if raw:
            roots.append(Path(raw))
    return roots


def _steam_roots() -> list[Path]:
    """Steam 本体 + 所有库目录。多盘、多库是这个游戏的常态，只认默认路径等于没探测。"""
    roots: list[Path] = []
    for hive, sub in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                roots.append(Path(winreg.QueryValueEx(k, "SteamPath")[0]))
            break
        except OSError:
            continue

    # 库目录清单：<Steam>\steamapps\libraryfolders.vdf
    for base in list(roots):
        vdf = base / "steamapps" / "libraryfolders.vdf"
        try:
            roots += _parse_library_paths(vdf.read_text("utf-8", errors="replace"))
        except OSError:
            continue
    return roots


def _gog_roots() -> list[Path]:
    """GOG 版：Galaxy 把安装路径写进注册表，读它比猜路径准。

    32 位程序写的是 WOW6432Node；两种视图都试一遍，OpenKey 失败就下一个。
    """
    out: list[Path] = []
    for sub, access in ((r"SOFTWARE\WOW6432Node\GOG.com\Games", 0),
                        (r"SOFTWARE\GOG.com\Games", winreg.KEY_WOW64_32KEY),
                        (r"SOFTWARE\GOG.com\Games", 0)):
        for gid in _GOG_IDS:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub + "\\" + gid,
                                    0, access) as k:
                    out.append(Path(winreg.QueryValueEx(k, "path")[0]))
            except OSError:
                continue
    return out


def _epic_roots() -> list[Path]:
    """Epic 版：每个已装游戏一个 .item 清单（JSON）。

    不认 AppName —— 那字段可能是本地化过的名字；只取 InstallLocation，
    最后统一过 is_game_root()，认不出就自然落空。
    """
    base = Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData")
    man = base / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"
    out: list[Path] = []
    try:
        files = list(man.glob("*.item"))
    except OSError:
        return out
    for f in files:
        try:
            data = json.loads(f.read_text("utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        loc = str(data.get("InstallLocation") or "").strip()
        if loc:
            out.append(Path(loc))
    return out


def _dedupe(items: list[Path]) -> list[Path]:
    """去重。Steam 的库清单里会把自己也列一条，注册表又给一条，大小写还未必一致。"""
    seen: set[str] = set()
    out: list[Path] = []
    for p in items:
        key = os.path.normcase(str(p)).rstrip("\\/")
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _probe_candidates() -> list[Path]:
    """按「越准越前」列出注册表/库清单里找到的游戏目录候选。

    每一项都可能抛（注册表被锁、清单是坏 JSON、路径不存在），逐个吞掉：
    探测失败只该让下一个候选上场，不该把启动流程搞崩。
    """
    out: list[Path] = []

    try:
        for lib in _steam_roots():
            for name in _STEAM_GAME_DIRS:
                out.append(lib / _STEAM_COMMON / name)
    except OSError:
        pass

    try:
        out += _gog_roots()
    except OSError:
        pass

    try:
        out += _epic_roots()
    except OSError:
        pass
    return _dedupe(out)


def detect_game_root(refresh: bool = False) -> Path | None:
    """按 环境变量 → 配置 → 注册表/库清单 → 常见路径 的顺序找一个可用的游戏目录。

    refresh=True 跳过缓存，用于用户点了「重新查找」或者刚改完配置。
    """
    now = time.time()
    if not refresh and now - _DETECT["at"] < _DETECT_TTL:
        cached = _DETECT["root"]
        if cached is None or is_game_root(cached):
            return cached

    found: Path | None = None

    env = os.environ.get(ENV_GAME)
    if env and is_game_root(env):
        found = Path(env)

    if found is None:
        cfg = load_config()
        if cfg.get("game_root") and is_game_root(cfg["game_root"]):
            found = Path(cfg["game_root"])

    if found is None:
        for c in _probe_candidates():
            if is_game_root(c):
                found = c
                break

    if found is None:
        for c in _CANDIDATES:
            if is_game_root(c):
                found = Path(c)
                break

    _DETECT["at"] = now
    _DETECT["root"] = found
    return found


def forget_detected() -> None:
    """丢掉探测缓存。改完配置、用户点「重新查找」时调用。"""
    _DETECT["at"] = 0.0
    _DETECT["root"] = None


def _exe_of(root: Path) -> Path | None:
    for rel in ("bin/x64_dx12/witcher3.exe", "bin/x64/witcher3.exe"):
        p = root / rel
        if p.is_file():
            return p
    return None


def is_game_root(path: str | Path) -> bool:
    """判断一个目录是不是巫师3的安装根目录。"""
    p = Path(path)
    return p.is_dir() and _exe_of(p) is not None


def documents_dir() -> Path:
    """当前用户的「文档」目录（可能被重定向到别的盘）。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            val, _ = winreg.QueryValueEx(k, "Personal")
            p = Path(os.path.expandvars(val))
            if p.is_dir():
                return p
    except OSError:
        pass
    home = Path.home() / "Documents"
    return home if home.is_dir() else Path.home()


def user_config_dir(root: Path | None = None) -> Path:
    """mods.settings 所在的用户配置目录，通常是 文档/The Witcher 3。

    少数整合包会把它放在游戏目录下，两种都认。
    环境变量 WAASS_CONFIG_DIR 可以强制指定（测试和特殊安装用它）。
    """
    override = os.environ.get("WAASS_CONFIG_DIR")
    if override:
        return Path(override)
    cands = [documents_dir() / "The Witcher 3"]
    if root is not None:
        cands += [root / "The Witcher 3", root]
    for c in cands:
        if (c / "mods.settings").is_file():
            return c
    return cands[0]


def load_config() -> dict:
    if CONFIG_FILE.is_file():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_FILE)
    forget_detected()          # 换了游戏目录，下一次要立刻按新配置找


def game_layout(root: Path) -> dict:
    """游戏里与本程序相关的那几个目录。"""
    return {
        "root": root,
        "mods": root / "mods",
        "dlc": root / "dlc",
        "menu_xml": root / "bin" / "config" / "r4game" / "user_config_matrix" / "pc",
        "bin_x64": root / "bin" / "x64_dx12",
        "user_config": user_config_dir(root),
        "exe": _exe_of(root),
    }
