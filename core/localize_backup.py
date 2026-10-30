"""汉化专用的备份与还原。

为什么不直接用 `safety.Operation` 的快照
----------------------------------------
两者目标不同，别混：

| | Operation 快照 | 这里 |
|---|---|---|
| 服务于 | 撤销「上一次写操作」 | 一键还原到「从没汉化过」 |
| 保留 | 最近 30 份，自动清理 | 永久，除非用户手动删 |
| 出问题的时点 | 通常是刚才那一步 | 可能是三个月后进游戏才发现 |

用户在 2026-10-28 明确要求「所有汉化的模组必须做好备份和还原，汉化完出 bug
能一键还原」。一个三个月前被自动清掉的还原点等于没有，所以这套备份独立存，
不进 `storage` 的清理列表。

目录结构（全部在 `data/localize/` 下）
--------------------------------------
```
orig/<相对游戏目录的路径>          原始副本。第一次见到就存，之后永不覆盖。
history/<批次号>/<同样的相对路径>   每次汉化前的一份，留最近 20 批。
index.json                        历次记录，含前后指纹。
```

为什么两套都要
--------------
`orig` 回答「它本来长什么样」—— 一键还原用这个。
`history` 回答「上一次汉化之前长什么样」—— 连续汉化两次时，
第二次出错只想退掉第二次，这时 `orig` 会把第一次的成果一起抹掉。

指纹校验
--------
还原前比对当前文件的 sha256 与记录里的 `sha_after`：
- 一致 → 是汉化之后没动过，直接还原
- 不一致 → 模组更新过（或用户自己改过），先提示，由调用方决定要不要硬还原。
  不能默默覆盖 —— 那会把模组的新内容换成旧备份。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import i18n, safety
from .paths import DATA_DIR, detect_game_root

BASE = DATA_DIR / "localize"
ORIG = BASE / "orig"
HIST = BASE / "history"
INDEX = BASE / "index.json"
KEEP_HISTORY = 20          # 保留最近多少批


# ---------------- 基础 ----------------

def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    got = detect_game_root()
    if not got:
        raise FileNotFoundError(i18n.t("loc.no_game_root"))
    return Path(got)


def rel_of(path: Path, root: Path | None = None) -> str:
    """语言表相对游戏目录的路径。用这个当 key。

    不用内容哈希当 key：内容会随汉化而变，我们要的是「同一个文件」。
    """
    p = Path(path).resolve()
    try:
        return str(p.relative_to(_root(root).resolve())).replace("\\", "/")
    except ValueError:
        # 不在游戏目录里（测试沙盒、或用户手动指定的外部文件）：
        # 用盘符外的完整路径转成的扁平名字，仍然稳定可辨。
        drive = p.drive.rstrip(":") or "x"
        tail = str(p).replace("\\", "/").lstrip("/").replace(":", "")
        return "%s/%s" % (drive, tail)


def _load() -> list[dict]:
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _save(items: list[dict]) -> None:
    BASE.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- 存 ----------------

def keep_original(path: Path, root: Path | None = None) -> str:
    """把这个文件的「原始样子」存起来。已经有了就不动。

    只在**第一次**改动前调用。第二次汉化时不能覆盖 —— 那时文件里已经是译文了，
    再存一次就把唯一的原件换成译文，等于把还原点毁掉。
    """
    p = Path(path)
    rel = rel_of(p, root)
    dest = ORIG / rel
    if not dest.is_file() and p.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dest)
    return rel


def has_original(path: Path, root: Path | None = None) -> bool:
    return (ORIG / rel_of(Path(path), root)).is_file()


def original_of(path: Path, root: Path | None = None) -> Path | None:
    """拿到原始副本的路径（可能已被用户删掉）。"""
    dest = ORIG / rel_of(Path(path), root)
    return dest if dest.is_file() else None


def prepare(paths, root: Path | None = None) -> dict:
    """批量写入前的准备。返回 token，写入完成后交给 `commit()`。

    这一步不写游戏目录，只读 + 往 `data/localize/` 复制，
    所以再出什么错也不会伤到游戏。
    """
    files = []
    for p in paths:
        p = Path(p)
        files.append({
            "rel": keep_original(p, root),
            "path": str(p),
            "sha_before": _sha(p) if p.is_file() else "",
            "size_before": p.stat().st_size if p.is_file() else 0,
        })
    return {"files": files, "ts": time.time()}


def _batch_id() -> str:
    """批次号。秒级时间戳不够用 —— 同一秒里连续汉化两个模组会撞号，
    结果就是按 id 还原时挑中了别人那一条。撞了就加后缀。"""
    base = datetime.now().strftime("%Y%m%d-%H%M%S")
    have = {e.get("id") for e in _load()}
    if base not in have:
        return base
    for n in range(2, 100):
        cand = "%s-%d" % (base, n)
        if cand not in have:
            return cand
    return "%s-%d" % (base, time.time_ns())


def commit(token: dict, label: str, engine: str = "", count: int = 0,
           root: Path | None = None) -> dict:
    """写入成功后调用：把这批的「汉化前」存进 history，并记进 index。"""
    bid = _batch_id()
    hdir = HIST / bid
    files = []
    for f in token.get("files", []):
        p = Path(f["path"])
        hist_rel = ""
        if p.is_file():
            dest = hdir / f["rel"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
            hist_rel = f["rel"]
        files.append({
            "rel": f["rel"],
            "path": str(p),
            "sha_before": f["sha_before"],
            "sha_after": _sha(p) if p.is_file() else "",
            "size_before": f.get("size_before", 0),
            "size_after": p.stat().st_size if p.is_file() else 0,
            "hist": hist_rel,
        })

    entry = {
        "id": bid,
        "ts": time.time(),
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "label": label,
        "engine": engine,
        "count": count,
        "files": files,
    }
    items = _load()
    items.append(entry)
    _save(items)
    _prune()
    return entry


def _prune() -> None:
    """history 只留最近 KEEP_HISTORY 批。orig 永远不删。"""
    items = _load()
    if len(items) <= KEEP_HISTORY:
        return
    keep = {e["id"] for e in items[-KEEP_HISTORY:]}
    for e in items[:-KEEP_HISTORY]:
        d = HIST / e["id"]
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
    _save(items[-KEEP_HISTORY:])
    # 兜底：index 之外残留的目录也清掉（比如上次清理到一半被打断）
    if HIST.is_dir():
        for d in HIST.iterdir():
            if d.is_dir() and d.name not in keep:
                shutil.rmtree(d, ignore_errors=True)


# ---------------- 查 ----------------

def records(limit: int = 20) -> list[dict]:
    """历次汉化记录，新的在前。只给界面看，不含内部路径细节。"""
    out = []
    for e in reversed(_load()):
        out.append({
            "id": e["id"],
            "time": e.get("time", ""),
            "label": e.get("label", ""),
            "engine": e.get("engine", ""),
            "count": e.get("count", 0),
            "files": [{"rel": f["rel"], "path": f["path"]} for f in e.get("files", [])],
        })
        if len(out) >= limit:
            break
    return out


def state(path: Path, root: Path | None = None) -> dict:
    """这个文件现在处于什么状态。界面据此决定按钮怎么显示。"""
    p = Path(path)
    rel = rel_of(p, root)
    orig = ORIG / rel
    info = {"path": str(p), "rel": rel, "has_original": orig.is_file(),
            "localized": False, "drifted": False}

    items = _load()
    last = None
    for e in reversed(items):
        for f in e.get("files", []):
            if f["rel"] == rel:
                last = f
                break
        if last:
            break
    info["last"] = {"time": last.get("time", ""), "sha_after": last.get("sha_after", "")} if last else None

    if orig.is_file() and p.is_file():
        cur = _sha(p)
        info["localized"] = cur != _sha(orig)
        if last and last.get("sha_after") and cur != last["sha_after"]:
            # 汉化之后文件又变了：模组更新，或用户自己动过
            info["drifted"] = True
    return info


def check(record_id: str | None = None) -> dict:
    """还原前的预检：哪些文件已经和记录对不上。"""
    items = _load()
    if not items:
        return {"ok": False, "why": i18n.t("loc.no_records"), "drifted": []}
    entry = None
    if record_id:
        for e in items:
            if e["id"] == record_id:
                entry = e
                break
        if not entry:
            return {"ok": False, "why": i18n.t("loc.record_missing", id=record_id),
                    "drifted": []}
    else:
        entry = items[-1]

    drifted = []
    for f in entry.get("files", []):
        p = Path(f["path"])
        if not p.is_file():
            drifted.append({"path": str(p), "rel": f["rel"],
                            "why": i18n.t("loc.file_gone"), "code": "file_gone"})
            continue
        cur = _sha(p)
        if f.get("sha_after") and cur != f["sha_after"]:
            drifted.append({"path": str(p), "rel": f["rel"],
                            "why": i18n.t("loc.drifted_after"),
                            "code": "changed_again"})
    return {"ok": True, "entry": entry, "drifted": drifted,
            "files": len(entry.get("files", []))}


# ---------------- 还原 ----------------

def restore(record_id: str | None = None, mode: str = "orig",
            force: bool = False, root: Path | None = None) -> dict:
    """还原语言表。

    mode:
      - `orig`   还原到「从没汉化过」的原始副本（默认，一键还原用这个）
      - `hist`   只退掉指定这一批汉化，保留更早的汉化成果

    force:
      - False（默认）遇到「汉化之后又被改过」的文件就跳过并如实报告
      - True  仍然覆盖。调用方必须在界面上先问过用户。

    动游戏目录之前一次性校验：游戏没在跑 + 所有目标都有写入授权。
    任何一条不过就整批不动 —— 不做「改了一半」。
    """
    items = _load()
    if not items:
        return {"ok": False, "why": i18n.t("loc.no_records"),
                "restored": [], "skipped": []}

    entry = None
    if record_id:
        for e in items:
            if e["id"] == record_id:
                entry = e
                break
    else:
        entry = items[-1]
    if not entry:
        return {"ok": False, "why": i18n.t("loc.record_missing", id=record_id),
                "restored": [], "skipped": []}

    src_base = ORIG if mode == "orig" else (HIST / entry["id"])

    plan: list[tuple[dict, Path]] = []
    skipped: list[dict] = []
    for f in entry.get("files", []):
        p = Path(f["path"])
        rel = f["rel"]
        src = src_base / rel
        if not src.is_file():
            skipped.append({"path": str(p), "rel": rel,
                            "why": i18n.t("loc.no_orig_backup") if mode == "orig"
                            else i18n.t("loc.hist_pruned"),
                            "code": "no_backup" if mode == "orig"
                            else "backup_pruned"})
            continue
        want = f.get("sha_after") if mode == "hist" else None
        if p.is_file() and not force:
            cur = _sha(p)
            if want and cur != want:
                skipped.append({"path": str(p), "rel": rel,
                                "why": i18n.t("loc.drifted_skip"),
                                "code": "changed_again"})
                continue
            if mode == "orig" and cur == _sha(src):
                skipped.append({"path": str(p), "rel": rel,
                                "why": i18n.t("loc.already_orig"),
                                "code": "already_original"})
                continue
        plan.append((f, src))

    if not plan:
        return {"ok": True, "restored": [], "skipped": skipped,
                "why": i18n.t("loc.nothing_to_restore")}

    safety.assert_writable(*[Path(f["path"]) for f, _ in plan])

    op = safety.Operation(i18n.t("loc.op_restore"))
    done = []
    for f, src in plan:
        p = Path(f["path"])
        op.backup(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, p)
        done.append({"path": str(p), "rel": f["rel"], "size": p.stat().st_size})
    op.commit()

    return {"ok": True, "mode": mode, "record": entry["id"],
            "restored": done, "skipped": skipped, "op": op.id,
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


def drop_all(confirm: bool = False) -> dict:
    """删掉全部原件与记录。只有用户明确要求时才该调用。

    故意要求显式 confirm：这是唯一一个「删了真没办法」的操作。
    """
    if not confirm:
        return {"ok": False, "why": i18n.t("loc.drop_needs_confirm")}
    removed = []
    for d in (ORIG, HIST):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(str(d))
    if INDEX.is_file():
        INDEX.unlink()
        removed.append(str(INDEX))
    return {"ok": True, "removed": removed}
