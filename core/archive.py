"""压缩包：解压到暂存区，识别巫师3 结构，生成写入预览。

设计原则
--------
1. 绝不解压到游戏目录。所有解压都进临时暂存区，检查清楚再谈写入。
2. 不信任压缩包内容：路径穿越、绝对路径、盘符、符号链接一律拒绝。
3. 只读：本模块不写游戏目录，只产出"打算写什么"的清单（InstallPlan）。

支持的格式：zip 用标准库；7z/rar 依次找 Bandizip / 7-Zip / UnRAR。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import i18n

# ---------------- 解压工具 ----------------

_BANDIZIP = [
    r"C:\Program Files\Bandizip\bz.exe",
    r"C:\Program Files (x86)\Bandizip\bz.exe",
]
_SEVENZIP = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]
_UNRAR = [
    r"C:\Program Files\WinRAR\UnRAR.exe",
    r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
]

SUPPORTED_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".tgz"}


def _first_existing(cands: list[str]) -> Path | None:
    for c in cands:
        if Path(c).is_file():
            return Path(c)
    return None


def tools_report() -> dict:
    """当前机器能用哪些解压工具，供界面提示用。"""
    return {
        "zip": True,
        "bandizip": str(_first_existing(_BANDIZIP) or ""),
        "7zip": str(_first_existing(_SEVENZIP) or ""),
        "unrar": str(_first_existing(_UNRAR) or ""),
    }


def can_handle(suffix: str) -> bool:
    suffix = suffix.lower()
    if suffix not in SUPPORTED_EXT:
        return False
    if suffix in {".zip", ".tar", ".gz", ".bz2", ".xz", ".tgz"}:
        return True
    return bool(_first_existing(_BANDIZIP) or _first_existing(_SEVENZIP))


def _long(p: Path | str) -> str:
    """Windows 长路径前缀，mod 里的路径经常超 260 字符。"""
    s = os.path.abspath(str(p))
    if os.name == "nt" and len(s) > 240 and not s.startswith("\\\\?\\"):
        return "\\\\?\\" + s
    return s


# ---------------- 安全解压 ----------------

def _check_member(name: str) -> bool:
    """压缩包里的相对路径是否安全。"""
    if not name or name.endswith("/"):
        return True
    norm = name.replace("\\", "/")
    if norm.startswith("/") or ":" in norm.split("/")[0]:
        return False                                  # 绝对路径或盘符
    parts = [p for p in norm.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return False                                  # 路径穿越
    return True


def _extract_zip(archive: Path, dest: Path) -> None:
    skipped: list[str] = []
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if not _check_member(name):
                skipped.append(name)
                continue
            # 符号链接：zip 里权限位最高位为 0xA 时是链接，直接跳过
            if (info.external_attr >> 28) == 0xA:
                skipped.append(name)
                continue
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(_long(target), "wb") as out:
                shutil.copyfileobj(src, out, 1 << 20)
    if skipped:
        raise ArchiveError(i18n.tn(
            "arc.unsafe_one", "arc.unsafe_many", len(skipped),
            names=str(skipped[:3])))


class ArchiveError(RuntimeError):
    pass


def _extract_external(archive: Path, dest: Path) -> None:
    bz = _first_existing(_BANDIZIP)
    if bz:
        cmd = [str(bz), "x", "-y", f"-o:{dest}", str(archive)]
    else:
        sz = _first_existing(_SEVENZIP)
        if sz:
            cmd = [str(sz), "x", "-y", f"-o{dest}", str(archive)]
        else:
            un = _first_existing(_UNRAR)
            if not un or archive.suffix.lower() != ".rar":
                raise ArchiveError(
                    i18n.t("arc.no_tool", suffix=archive.suffix)
                )
            cmd = [str(un), "x", "-y", str(archive), str(dest) + os.sep]

    # Windows 下隐藏子进程窗口
    si = None
    if os.name == "nt":
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    proc = subprocess.run(
        cmd, capture_output=True, text=True, errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), startupinfo=si,
    )
    if proc.returncode not in (0, 1):                  # UnRAR 用 1 表示"有警告但成功"
        raise ArchiveError(i18n.t("arc.extract_failed", suffix=archive.suffix,
                                  detail=(proc.stderr or proc.stdout)[:300]))


def extract(archive: str | Path, dest: str | Path | None = None) -> tuple[Path, bool]:
    """把压缩包解到暂存目录。返回 (暂存目录, 是否为临时目录)。"""
    src = Path(archive)
    if not src.is_file():
        raise ArchiveError(i18n.t("arc.file_not_found", path=src))
    if not can_handle(src.suffix):
        raise ArchiveError(i18n.t("arc.unsupported", suffix=src.suffix))

    temp = dest is None
    out = Path(tempfile.mkdtemp(prefix="waass_stage_")) if temp else Path(dest)
    out.mkdir(parents=True, exist_ok=True)

    if src.suffix.lower() == ".zip":
        _extract_zip(src, out)
    else:
        _extract_external(src, out)
    return out, temp


# ---------------- 结构识别 ----------------

GAME_TOP = {"mods", "dlc", "bin", "content", "registry", "r4game"}
IGNORE_NAMES = {
    "readme.txt", "readme.md", "readme", "license.txt", "license", "changelog.txt",
    "说明.txt", "安装说明.txt", "安装说明.txt", "thumbs.db", "desktop.ini",
}
JUNK_DIRS = {"__macosx", ".ds_store", "__MACOSX"}


@dataclass
class PlanEntry:
    src: str            # 相对暂存目录
    dst: str            # 相对游戏根目录
    kind: str           # mod_folder / dlc_folder / menu_xml / bin_config / bin_binary / settings
    name: str = ""      # 目标文件夹或文件名
    conflict: str = ""  # "" / "replace"（游戏里已存在同名）
    size: int = 0       # 单文件大小；文件夹记 0
    mergeable: bool = False   # 已有同名配置文件，但可以按行合并，不算冲突


@dataclass
class InstallPlan:
    staging: str
    wrapper: str = ""                      # 被剥掉的包装层路径
    entries: list[PlanEntry] = field(default_factory=list)
    mod_folders: list[str] = field(default_factory=list)
    dlc_folders: list[str] = field(default_factory=list)
    menu_xmls: list[str] = field(default_factory=list)
    bin_configs: list[str] = field(default_factory=list)    # bin/config/ 下的 .ini/.xml，非菜单
    bin_binaries: list[str] = field(default_factory=list)   # dll / ini 之类，高危
    has_settings: bool = False
    ignored: list[str] = field(default_factory=list)
    warnings: list[str | dict] = field(default_factory=list)

    @property
    def risk(self) -> str:
        """high = 会写进游戏二进制目录，出错游戏起不来。"""
        return "high" if self.bin_binaries else "normal"

    @property
    def conflicts(self) -> list[PlanEntry]:
        return [e for e in self.entries if e.conflict == "replace"]

    @property
    def ok(self) -> bool:
        return bool(self.entries)

    @property
    def summary(self) -> str:
        bits = []
        if self.mod_folders:
            bits.append(i18n.tn("arc.sum_mods_one", "arc.sum_mods_many",
                                len(self.mod_folders)))
        if self.dlc_folders:
            bits.append(i18n.tn("arc.sum_dlc_one", "arc.sum_dlc_many",
                                len(self.dlc_folders)))
        if self.menu_xmls:
            bits.append(i18n.tn("arc.sum_menu_one", "arc.sum_menu_many",
                                len(self.menu_xmls)))
        if self.bin_configs:
            bits.append(i18n.tn("arc.sum_configs_one", "arc.sum_configs_many",
                                len(self.bin_configs)))
        if self.bin_binaries:
            bits.append(i18n.tn("arc.sum_bin_one", "arc.sum_bin_many",
                                len(self.bin_binaries)))
        if self.has_settings:
            bits.append("mods.settings")
        return "、".join(bits) or i18n.t("arc.sum_none")


def _subdirs(p: Path) -> list[Path]:
    try:
        return sorted(
            (c for c in p.iterdir() if c.is_dir() and c.name.lower() not in JUNK_DIRS),
            key=lambda c: c.name.lower(),
        )
    except OSError:
        return []


# mod 本体内部才有的东西：看到这些就说明这一层已经是 mod 本身，不是包装层
MOD_INNER = {"content", "scripts", "mod.info", "info.json", "mod.json", "readme.txt"}


def _is_mod_payload(p: Path) -> bool:
    """这一层目录看起来是不是一个 mod / dlc 的本体。"""
    try:
        names = {c.name.lower() for c in p.iterdir()}
    except OSError:
        return False
    return bool(names & MOD_INNER)


def _looks_like_game_root(p: Path) -> bool:
    subs = _subdirs(p)
    names = {c.name.lower() for c in subs}
    if names & {"mods", "dlc"}:
        return True
    if (p / "bin" / "config").is_dir():
        return True
    if any(n.startswith("mod") or n.startswith("dlc") for n in names if len(n) > 4):
        return True
    # 只有一层、但里面是 mod 本体：说明这一层就是内容根
    return len(subs) == 1 and _is_mod_payload(subs[0])


def _find_content_root(staging: Path) -> tuple[Path, str]:
    """往深处剥包装层，找到真正的内容根。最多剥 4 层。"""
    cur, wrapper = staging, ""
    for _ in range(5):
        if _looks_like_game_root(cur):
            return cur, wrapper
        subs = _subdirs(cur)
        files = [f for f in cur.iterdir() if f.is_file()] if cur.is_dir() else []
        # 只有一个子目录、没有其它文件、且子目录不是 mod 本体 → 认为是包装层
        if len(subs) == 1 and not files and not _is_mod_payload(subs[0]):
            wrapper = str(subs[0].relative_to(staging))
            cur = subs[0]
            continue
        break
    return cur, wrapper


def _is_mod_name(name: str) -> bool:
    low = name.lower().lstrip("~")
    return low.startswith("mod") and len(low) > 3


def _is_dlc_name(name: str) -> bool:
    low = name.lower().lstrip("~")
    return low.startswith("dlc") and len(low) > 3


def _norm_mod_name(name: str) -> tuple[str, str]:
    """返回 (可用文件夹名, 提示)。巫师3 只认 mod 开头的文件夹。"""
    if name.startswith("~"):
        return name, i18n.t("arc.disabled_hint")
    if _is_mod_name(name):
        return name, ""
    return "mod_" + name, i18n.t("arc.rename_hint", name=name, fixed="mod_" + name)


def build_plan(staging: str | Path, game_root: str | Path | None = None) -> InstallPlan:
    """扫暂存区，算出要往游戏里写什么。纯读，不写任何东西。"""
    st = Path(staging)
    plan = InstallPlan(staging=str(st))
    root, wrapper = _find_content_root(st)
    plan.wrapper = wrapper

    game = Path(game_root) if game_root else None

    def add(src: Path, dst_rel: str, kind: str, name: str = "") -> None:
        entry = PlanEntry(
            src=str(src.relative_to(st)).replace("\\", "/"),
            dst=dst_rel.replace("\\", "/"), kind=kind, name=name,
        )
        if game is not None:
            target = game / entry.dst
            if target.exists():
                # bin/config 下的 .ini 走合并：游戏里本来就有这个文件，
                # 不是「冲突」而是「要往里面加几行」。整份替换会冲掉游戏
                # 自己的键（Debug Console 甚至会把你游戏的 ConfigVersion
                # 从 7 降回 5）。所以这里不当冲突，交给 install 合并。
                from .config_merge import supported as _merge_ok
                if kind == "bin_config" and target.is_file() and _merge_ok(entry.dst):
                    entry.mergeable = True
                else:
                    entry.conflict = "replace"
        plan.entries.append(entry)

    # 1. 明确带 mods/ dlc/ 的游戏根结构
    mods_dir = root / "mods"
    if mods_dir.is_dir():
        for d in _subdirs(mods_dir):
            fixed, hint = _norm_mod_name(d.name)
            if hint:
                plan.warnings.append(hint)
            add(d, f"mods/{fixed}", "mod_folder", fixed)
            plan.mod_folders.append(fixed)

    dlc_dir = root / "dlc"
    if dlc_dir.is_dir():
        for d in _subdirs(dlc_dir):
            add(d, f"dlc/{d.name}", "dlc_folder", d.name)
            plan.dlc_folders.append(d.name)

    bin_dir = root / "bin"
    menu_pc = root / "bin" / "config" / "r4game" / "user_config_matrix" / "pc"
    if bin_dir.is_dir():
        for f in bin_dir.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(root)).replace("\\", "/")
            if menu_pc.is_dir() and menu_pc in f.parents:
                add(f, rel, "menu_xml", f.name)
                plan.menu_xmls.append(f.name)
            elif "bin/config/" in rel.lower():
                add(f, rel, "bin_config", f.name)
                plan.bin_configs.append(rel)
            else:
                # dll / ini / exe：写进游戏二进制目录，出事游戏直接起不来
                add(f, rel, "bin_binary", f.name)
                plan.bin_binaries.append(rel)

    if plan.bin_binaries:
        # 跨端契约：前端不再按中文文本匹配这条警告，改认 code。
        # 其余 warnings 仍是纯字符串，只有这一条是 {"code", "message"} dict。
        plan.warnings.append({
            "code": "inside_game_bin",
            "message": i18n.tn(
                "arc.bin_one", "arc.bin_many", len(plan.bin_binaries),
                name=Path(plan.bin_binaries[0]).name),
        })

    # 2. 松散放着的 modXXX / dlcXXX 目录
    if not mods_dir.is_dir() or not dlc_dir.is_dir():
        for d in _subdirs(root):
            if d.name.lower() in GAME_TOP:
                continue
            if _is_mod_name(d.name):
                fixed, hint = _norm_mod_name(d.name)
                if hint:
                    plan.warnings.append(hint)
                add(d, f"mods/{fixed}", "mod_folder", fixed)
                plan.mod_folders.append(fixed)
            elif _is_dlc_name(d.name):
                add(d, f"dlc/{d.name}", "dlc_folder", d.name)
                plan.dlc_folders.append(d.name)
            elif _is_mod_payload(d):
                # 名字不规范（不以 mod 开头），但确实是 mod 本体
                fixed, hint = _norm_mod_name(d.name)
                if hint:
                    plan.warnings.append(hint)
                add(d, f"mods/{fixed}", "mod_folder", fixed)
                plan.mod_folders.append(fixed)

    # 3. mods.settings
    settings = root / "mods.settings"
    if settings.is_file():
        plan.has_settings = True
        add(settings, "mods.settings", "settings")
        plan.warnings.append(i18n.t("arc.settings_note"))

    # 4. 认不出来的东西
    for f in root.iterdir() if root.is_dir() else []:
        if f.is_file():
            low = f.name.lower()
            if low in IGNORE_NAMES or low.startswith("readme"):
                plan.ignored.append(f.name)
            elif low == "mods.settings":
                continue
            elif f.suffix.lower() in SUPPORTED_EXT:
                plan.ignored.append(i18n.t("arc.nested_archive", name=f.name))
            else:
                plan.ignored.append(f.name)

    if not plan.entries:
        plan.warnings.append(i18n.t("arc.no_structure"))

    # 去重：同一目标只留一条
    seen: dict[str, PlanEntry] = {}
    for e in plan.entries:
        seen.setdefault(e.dst.lower(), e)
    plan.entries = list(seen.values())
    return plan


def cleanup(staging: str | Path) -> None:
    """删掉临时暂存目录。只在确实是临时目录时才调用。"""
    p = Path(staging)
    if p.is_dir() and p.name.startswith("waass_stage_"):
        shutil.rmtree(_long(p), ignore_errors=True)
