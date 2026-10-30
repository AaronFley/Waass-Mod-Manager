"""一键汉化的任务：后台翻译、进度可轮询、结果可改、一次写入一次撤销。

为什么要有这一层
----------------
翻译是网络请求，一批 36 条要 8 秒上下，慢网更久。同步接口会把界面卡住
（更糟：`/api/progress` 也一起卡住，进度条永远不动）。所以翻译放到后台线程，
界面每半秒问一次进度。

任务只活在内存里，进程退出就没了 —— 这是故意的。真正要长期留住的是
**译文记忆**（`data/translate/memory.json`）和**原始文件备份**
（`data/localize/orig/`），前者保证重开程序不用重翻，后者保证随时能还原。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from . import guard, i18n, localize, localize_backup as LB, safety, translate

_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}
KEEP_JOBS = 4          # 内存里留最近几个任务，够界面上翻看


def _new_id() -> str:
    return "job-%d" % int(time.time() * 1000)


def _trim() -> None:
    if len(_JOBS) <= KEEP_JOBS:
        return
    olds = sorted(_JOBS.values(), key=lambda j: j["ts"])[: len(_JOBS) - KEEP_JOBS]
    for j in olds:
        if j["status"] in ("running",):
            continue
        _JOBS.pop(j["id"], None)


# ---------------- 翻译 ----------------

def start(root: Path, keys: list[str], cats=None) -> dict:
    """建任务并立刻开始翻译。返回任务的快照（不含逐条结果）。"""
    cats = tuple(cats or localize.CAT_DEFAULT)
    unknown = [c for c in cats if c not in localize.CAT_LABEL]
    if unknown:
        raise ValueError(i18n.t("loc.bad_cat", names=i18n.join(unknown)))

    jobs = localize.build_jobs(root, list(keys), cats=cats)
    items = jobs["items"]
    if not items:
        raise ValueError(i18n.t("loc.no_menu_text"))

    job = {
        "id": _new_id(),
        "ts": time.time(),
        "status": "running",
        "keys": list(keys),
        "cats": list(cats),
        "total": len(items),
        "done": 0,
        "items": [],
        "error": "",
        "started": time.strftime("%H:%M:%S"),
        "engine": "",
    }
    job["_stop"] = threading.Event()
    job["_lock"] = threading.Lock()
    _JOBS[job["id"]] = job

    def work() -> None:
        try:
            rows = translate.run(
                items,
                on_progress=lambda n, _t, row: _push(job, n, row),
                stop=job["_stop"].is_set,
            )
            with job["_lock"]:
                job["items"] = rows
                job["done"] = len(rows)
                cancelled = any(r.get("status") == "cancel" for r in rows)
                job["status"] = "cancelled" if cancelled else "done"
                for r in rows:
                    if r.get("engine") and r["engine"] not in ("memory", "glossary", "dup"):
                        job["engine"] = job["engine"] or r["engine"]
        except Exception as e:                       # noqa: BLE001 后台线程不能把进程带崩
            with job["_lock"]:
                job["status"] = "failed"
                job["error"] = "%s: %s" % (type(e).__name__, e)

    threading.Thread(target=work, daemon=True).start()
    _trim()
    return snapshot(job["id"])


def _push(job: dict, n: int, row: dict) -> None:
    with job["_lock"]:
        job["items"].append(row)
        job["done"] = n


def cancel(job_id: str) -> dict:
    job = _JOBS.get(job_id)
    if not job:
        raise KeyError(i18n.t("loc.job_gone"))
    job["_stop"].set()
    return snapshot(job_id)


def snapshot(job_id: str, with_items: bool = True) -> dict:
    job = _JOBS.get(job_id)
    if not job:
        raise KeyError(i18n.t("loc.job_gone"))
    with job["_lock"]:
        rows = list(job["items"])
        out = {
            "id": job["id"],
            "status": job["status"],
            "total": job["total"],
            "done": job["done"],
            "error": job["error"],
            "engine": job["engine"],
            "cats": job["cats"],
            "counts": _counts(rows),
        }
        if with_items:
            out["items"] = [_public(r) for r in rows]
    return out


def _counts(rows: list[dict]) -> dict:
    c = {"ok": 0, "long": 0, "fail": 0, "cancel": 0, "pending": 0}
    for r in rows:
        s = str(r.get("status") or "")
        if s.startswith("ok"):
            c["ok"] += 1
        elif s in c:
            c[s] += 1
        else:
            c["pending"] += 1
    return c


def _public(row: dict) -> dict:
    """给界面的一行。字段名尽量短，但别丢「为什么不行」。"""
    return {
        "id": row.get("id", ""),
        "mod": row.get("mod", ""),
        "mod_name": row.get("mod_name", ""),
        "file": row.get("file", ""),
        "cat": row.get("cat", localize.CAT_MENU),
        "src": row.get("src", ""),
        "dst": row.get("dst", ""),
        "max": row.get("max", 0),
        "used": row.get("used", 0),
        "status": row.get("status", ""),
        "engine": row.get("engine", ""),
        "why": row.get("why", ""),
        "shortened": bool(row.get("shortened")),
    }


# ---------------- 写回 ----------------

def write(job_id: str, edits: list[dict] | None = None,
          root: Path | None = None, only: list[str] | None = None) -> dict:
    """把任务里的译文写进游戏目录。

    `edits` 是用户在表里改过的条目 `[{id, dst}]`，先覆盖再写。
    `only` 限定只写这些 id（「只重写这一条」用）。

    整个过程：一次备份 → 逐文件等长替换 → 一次记账。
    还原点是 `data/localize/orig` 里的原件，随时能一键退回去。
    """
    job = _JOBS.get(job_id)
    if not job:
        raise KeyError(i18n.t("loc.job_gone"))
    if job["status"] == "running":
        raise ValueError(i18n.t("loc.still_running"))

    with (job.get("_lock") or _LOCK):
        rows = job["items"]

    byid = {r.get("id"): r for r in rows}
    for e in (edits or []):
        r = byid.get(e.get("id"))
        if r is None:
            continue
        dst = str(e.get("dst") or "").strip()
        r["dst"] = dst
        # 手改过的以用户为准：长度对不对由下面的写入环节如实回报，
        # 不在这里替用户改字。
        if dst:
            r["used"] = len(dst.encode("utf-8"))
            r["status"] = "ok" if (not r.get("max") or r["used"] <= r["max"]) else "long"

    want = set(only) if only else None
    todo = []
    for r in rows:
        if want is not None and r.get("id") not in want:
            continue
        if not str(r.get("dst") or "").strip():
            continue
        if str(r.get("status") or "").startswith("cancel"):
            continue
        todo.append(r)

    if not todo:
        return {"ok": False, "error": i18n.t("loc.nothing_to_write"),
                "changed": 0, "files": [], "skipped": []}

    if root is not None:
        safety.authorize_game(Path(root))
    targets = sorted({str(r["file"]) for r in todo})
    safety.assert_writable(*[Path(t) for t in targets])

    # 1. 备份（永久原件 + 这一批的快照）
    token = LB.prepare([Path(t) for t in targets], root)

    # 2. 逐文件写
    by_file: dict[str, list[dict]] = {}
    for r in todo:
        by_file.setdefault(str(r["file"]), []).append(r)

    files: list[dict] = []
    skipped: list[dict] = []
    changed = 0
    written_paths: list[str] = []
    for path, group in by_file.items():
        p = Path(path)
        kind = group[0].get("kind") or "w3strings"
        mapping = {}
        for r in group:
            src = str(r.get("src") or "")
            dst = str(r.get("dst") or "").strip()
            if src and dst and dst != src:
                mapping[src] = dst
        if not mapping:
            continue
        try:
            if kind == "menu_xml":
                res = localize.apply_xml(p, mapping, localize.BACKUP_DIR)
            else:
                res = localize.w3strings.apply(p, mapping, localize.BACKUP_DIR)
        except Exception as e:                       # noqa: BLE001 单个文件失败不连坐
            files.append({"file": str(p), "error": "%s: %s" % (type(e).__name__, e)})
            continue
        n = res.get("changed", 0)
        changed += n
        if n:
            written_paths.append(str(p))
        for s in res.get("skipped", []):
            skipped.append(dict(s, file=p.name))
        files.append({k: v for k, v in res.items() if k != "items"})

    # 3. 记账（只有真改了东西才记）
    entry = None
    if written_paths:
        keep = [f for f in token["files"] if f["path"] in written_paths]
        n_mods = len({r["mod"] for r in todo if str(r["file"]) in written_paths})
        entry = LB.commit({"files": keep, "ts": token["ts"]},
                           label=i18n.tn("loc.op_localize_one", "loc.op_localize_many",
                                         n_mods, n=n_mods),
                           engine=job.get("engine", ""), count=changed, root=root)

    return {
        "ok": changed > 0,
        "changed": changed,
        "files": files,
        "skipped": skipped[:30],
        "skipped_count": len(skipped),
        "record": entry["id"] if entry else "",
        "error": "" if changed else i18n.t("loc.wrote_nothing"),
    }


# ---------------- 还原 ----------------

def history(limit: int = 12) -> list[dict]:
    return LB.records(limit)


def restore(record_id: str | None = None, mode: str = "orig",
            force: bool = False, root: Path | None = None) -> dict:
    if root is not None:
        safety.authorize_game(Path(root))
    return LB.restore(record_id, mode=mode, force=force, root=root)


def state_of(paths, root: Path | None = None) -> list[dict]:
    return [LB.state(Path(p), root) for p in paths]
