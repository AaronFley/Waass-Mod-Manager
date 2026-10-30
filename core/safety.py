"""安全层：任何写操作都要经过这里。

规则：
1. 游戏在跑 → 一律拒绝写
2. 写之前把原文件快照到 data/snapshots/<时间戳>/
3. 每次操作记进 data/journal.json，可回滚
4. 卸载不是删除，是搬到 data/quarantine/
"""
from __future__ import annotations

import ctypes
import json
import shutil
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from . import guard
from . import i18n
from .paths import DATA_DIR, game_layout

SNAP_DIR = DATA_DIR / "snapshots"
JOURNAL = DATA_DIR / "journal.json"
QUARANTINE = DATA_DIR / "quarantine"
MAX_JOURNAL = 200


class GameRunning(RuntimeError):
    pass


# ---------------- 进程检测 ----------------
_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260
_INVALID = ctypes.c_void_p(-1).value


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * _MAX_PATH),
    ]


def processes() -> list[str]:
    """当前所有进程名（小写）。用 Toolhelp 快照，比 tasklist 快一个数量级。"""
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == _INVALID:
        return []
    out: list[str] = []
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        ok = k32.Process32First(snap, ctypes.byref(entry))
        while ok:
            name = entry.szExeFile.decode("mbcs", errors="ignore").lower()
            if name:
                out.append(name)
            ok = k32.Process32Next(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def game_running() -> bool:
    names = set(processes())
    return bool(names & {"witcher3.exe", "witcher3_x64.exe"})


def authorize_game(root: Path) -> None:
    """只放开这 4 个位置，其它一律不许写。

    目录：mods/、dlc/、bin/config/r4game/user_config_matrix/pc/
    文件：mods.settings（单独放行，不连带开放整个文档目录 —— 里面有存档）
    """
    lay = game_layout(root)
    guard.allow(lay["mods"], lay["dlc"], lay["menu_xml"])
    guard.allow_file(lay["user_config"] / "mods.settings")


def authorize_rels(root: Path, rels) -> list[Path]:
    """按「这次到底要动哪些路径」放开所需子目录，而不是猜。

    为什么需要它
    ------------
    有些 mod 会往 `bin/config/base/` 塞配置文件（典型：SmartAutoLoot 的
    `autoloot.ini`）。这类文件既不属于 mods/、也不属于菜单目录，只靠
    `authorize_game()` 放开的四处永远写不进去 —— 安装会直接报「没有写入授权」。
    2026-10-26 就是这样装不上 SmartAutoLoot 的。

    规则：
      - 非 bin/ 的路径不在这里处理（mods/、dlc/ 已由 authorize_game 放开）
      - `bin/config/**`  → 只放开 bin/config 这一层（.ini/.xml，不含可执行文件）
      - 其它 `bin/**`    → 放开整个 bin（dll/exe，安装前会单独警告是高风险）
    """
    authorize_game(root)
    root = Path(root)
    added: list[Path] = []
    for rel in rels:
        r = str(rel or "").replace("\\", "/").lstrip("/").lower()
        if not r.startswith("bin/"):
            continue
        target = (root / "bin" / "config") if r.startswith("bin/config/") else (root / "bin")
        added += guard.allow(target)
    return added


def authorize_path(p) -> None:
    """只给单个已知文件放行（汉化改写 mod 内部文件时用）。

    仍然要求它落在已授权目录里，否则照样拒绝 —— 不是万能钥匙。
    """
    from pathlib import Path as _P
    guard.assert_can_write(_P(p), i18n.t("safe.what_single"))


def assert_writable(*paths) -> None:
    """写之前的双重闸门：游戏没在跑 + 每个目标路径都有授权。

    故意要求把目标路径一起传进来 —— 逼调用方明确说出它要动哪些文件，
    而不是"感觉安全"就往下写。
    """
    if game_running():
        raise GameRunning(i18n.t("safe.game_running"))
    if paths:
        guard.assert_can_write_many([p for p in paths if p is not None], i18n.t("safe.what_precheck"))


# ---------------- 快照与日志 ----------------
def now_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + f"{int(time.time() * 1000) % 1000:03d}"


def load_journal() -> list[dict]:
    if JOURNAL.is_file():
        try:
            return json.loads(JOURNAL.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
    return []


def save_journal(items: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text(json.dumps(items[-MAX_JOURNAL:], ensure_ascii=False, indent=1),
                       encoding="utf-8")


class Operation:
    """一次写操作的凭据：负责快照、记账、以及必要时的回滚。"""

    def __init__(self, label: str):
        self.id = _now_id()
        self.label = label
        self.dir = SNAP_DIR / self.id
        self.files: list[dict] = []     # {path, backup, existed}
        self.renames: list[dict] = []   # {from, to}
        self.created: list[str] = []    # 这次操作新建的文件/目录

    # --- 快照 ---
    def backup(self, path: Path) -> None:
        """把即将被修改的文件存一份。文件不存在时也会记录，方便回滚时删除。"""
        p = Path(path)
        guard.assert_can_write(p, i18n.t("safe.what_snapshot", label=self.label))
        existed = p.is_file()
        rec = {"path": str(p), "existed": existed, "backup": ""}
        if existed:
            self.dir.mkdir(parents=True, exist_ok=True)
            dest = self.dir / f"{len(self.files):03d}_{p.name}"
            shutil.copy2(p, dest)
            rec["backup"] = str(dest)
        self.files.append(rec)

    def record_rename(self, src: Path, dst: Path) -> None:
        guard.assert_can_write(src, i18n.t("safe.what_rename_src"))
        guard.assert_can_write(dst, i18n.t("safe.what_rename_dst"))
        self.renames.append({"from": str(src), "to": str(dst)})

    def record_create(self, path: Path) -> None:
        guard.assert_can_write(path, i18n.t("safe.what_create"))
        self.created.append(str(path))

    # --- 提交 ---
    def commit(self) -> dict:
        entry = {
            "id": self.id,
            "ts": time.time(),
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "label": self.label,
            "files": self.files,
            "renames": self.renames,
            "created": self.created,
        }
        items = load_journal()
        items.append(entry)
        save_journal(items)
        # 快照会随每次写操作长大。留最近 30 份足够了，更早的自动清掉，
        # 免得用了半年之后 data 目录悄悄变成几百 MB。
        try:
            from . import storage
            storage.prune_snapshots(30)
        except Exception:                            # noqa: BLE001 清不掉不影响这次操作
            pass
        return entry


def _revert(created: list[str], files: list[dict], renames: list[dict],
            may_write, errors: list[str]) -> None:
    """撤销三件事，顺序要紧：

    1. 先删掉这次新建的东西 —— 否则下一步"把移走的放回来"会因为目标已存在而跳过
    2. 再恢复被覆盖的文件内容
    3. 最后把改名搬回去（旧文件夹从隔离区回到游戏目录）
    """
    for c in reversed(created):
        p = Path(c)
        if not may_write(p):
            continue
        try:
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.is_file():
                p.unlink()
        except OSError as e:
            errors.append(f"{p.name}: {e}")

    for f in reversed(files):
        p = Path(f["path"])
        if not may_write(p):
            continue
        try:
            if f.get("backup"):
                p.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f["backup"], p)
            elif not f.get("existed") and p.is_file():
                p.unlink()
        except OSError as e:
            errors.append(f"{p.name}: {e}")

    for r in reversed(renames):
        src, dst = Path(r["to"]), Path(r["from"])
        if not (may_write(src) and may_write(dst)):
            continue
        try:
            if src.exists() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        except OSError as e:
            errors.append(f"{src.name}: {e}")


def rollback_op(op: "Operation") -> None:
    """装到一半失败时的紧急回滚：不写 journal，尽力恢复原样。"""
    errors: list[str] = []
    _revert(op.created, op.files, op.renames, lambda p: True, errors)
    if errors:
        raise RuntimeError(i18n.t("safe.rollback_dirty", errs="；".join(errors)))


def _authorize_for_undo(entry: dict) -> None:
    """按 journal 记录补授权。只放开这条记录里出现过的路径，别的一概不动。"""
    for f in entry.get("files") or []:
        if f.get("path"):
            try:
                guard.allow_file(f["path"])
            except guard.WriteDenied:
                pass
    for c in entry.get("created") or []:
        try:
            guard.allow_file(c)
        except guard.WriteDenied:
            pass
    for r in entry.get("renames") or []:
        for k in ("from", "to"):
            v = r.get(k)
            if not v:
                continue
            try:
                guard.allow(Path(v).parent)
            except guard.WriteDenied:
                pass


def undo_last() -> tuple[bool, str]:
    """回滚最后一次操作。返回 (是否成功, 说明)。"""
    items = load_journal()
    if not items:
        return False, i18n.t("safe.undo_none")
    entry = items.pop()

    # 白名单是进程内的，冷启动时是空的。用户装完关掉程序、第二天打开再点
    # 「撤销上一步」，会被 guard 全部拦掉，表现为「点了没反应，还提示没有写入授权」。
    # 授权依据就是这条 journal 本身 —— 它记录的正是当初用户批准过的路径。
    _authorize_for_undo(entry)

    errors: list[str] = []
    blocked: list[str] = []

    def may_write(p: Path) -> bool:
        if guard.is_writable(p):
            return True
        blocked.append(str(p))
        return False

    _revert(entry.get("created", []), entry.get("files", []),
            entry.get("renames", []), may_write, errors)

    # 档案里对应的条目也要撤掉，否则下次点它只会报"找不到文件"
    try:
        from . import registry
        registry.prune_by_op(entry.get("id", ""))
    except Exception:                                  # 档案坏了也不能影响回滚
        pass

    # 没撤干净就别把条目丢掉 —— 否则用户点了一次失败，日志里那条也没了，
    # 连「刚才到底改了什么」都查不回来。
    if blocked or errors:
        items.append(entry)
    save_journal(items)
    if blocked:
        return False, i18n.tn("safe.undo_blocked_one", "safe.undo_blocked_many",
                              len(blocked), paths="；".join(blocked[:3]))
    if errors:
        return False, i18n.t("safe.undo_errors", errs="；".join(errors))
    return True, entry.get("label", "")


def journal_summary(limit: int = 20) -> list[dict]:
    items = load_journal()
    return [
        {"id": i["id"], "time": i.get("time", ""), "label": i.get("label", "")}
        for i in items[-limit:][::-1]
    ]
