"""存档备份：装 mod 之前先把进度存一份。

巫师3 存档在 <文档>/The Witcher 3/gamesaves/*.sav。mod 装错、读档崩溃这类事故
里，最不可挽回的就是存档，所以默认在首次安装前自动做一次全量。

全量  = 打包 gamesaves 下所有文件
增量  = 只打包自上次备份以来新增或改动过的文件（省时间，适合每天开机点一下）
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

from . import guard
from . import i18n
from .paths import DATA_DIR, user_config_dir

SAVE_DIR = DATA_DIR / "saves"
INDEX = SAVE_DIR / "index.json"


def gamesaves_dir(root: Path | None = None) -> Path:
    return user_config_dir(root) / "gamesaves"


def _load_index() -> dict:
    if INDEX.is_file():
        try:
            return json.loads(INDEX.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"backups": []}


def _save_index(data: dict) -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _scan(src: Path) -> list[tuple[str, float, int]]:
    out: list[tuple[str, float, int]] = []
    if not src.is_dir():
        return out
    for p in src.rglob("*"):
        if p.is_file():
            st = p.stat()
            out.append((str(p.relative_to(src)).replace("\\", "/"),
                        st.st_mtime, st.st_size))
    return out


def backup(root: Path | None = None, mode: str = "full", note: str = "") -> dict:
    """备份存档。mode = "full" 全量 / "incremental" 增量。"""
    src = gamesaves_dir(root)
    if not src.is_dir():
        raise FileNotFoundError(i18n.t("sav.dir_missing", path=src))

    guard.allow(DATA_DIR)

    files = _scan(src)
    if not files:
        raise FileNotFoundError(i18n.t("sav.empty"))

    index = _load_index()
    prev_files: dict[str, dict] = {}
    if mode == "incremental" and index["backups"]:
        last = index["backups"][-1]
        prev_files = {f["rel"]: f for f in last.get("files", [])}

    picked = []
    for rel, mtime, size in files:
        old = prev_files.get(rel)
        if mode == "incremental" and old and old["size"] == size \
                and abs(old["mtime"] - mtime) < 1e-6:
            continue
        picked.append((rel, mtime, size))

    if not picked:
        return {"ok": True, "empty": True, "mode": mode,
                "message": i18n.t("sav.no_new")}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = SAVE_DIR / f"saves-{stamp}-{mode}.zip"
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel, _, _ in picked:
            zf.write(src / rel, rel)

    entry = {
        "id": f"saves-{stamp}-{mode}",
        "file": target.name,
        "mode": mode,
        "time": datetime.now().isoformat(timespec="seconds"),
        "note": note,
        "count": len(picked),
        "bytes": target.stat().st_size,
        "source": str(src),
        "files": [{"rel": rel, "mtime": mt, "size": sz} for rel, mt, sz in picked],
    }
    index["backups"].append(entry)
    _save_index(index)
    return {"ok": True, "id": entry["id"], "count": entry["count"],
            "bytes": entry["bytes"], "mode": mode, "file": str(target)}


def list_backups() -> list[dict]:
    items = _load_index()["backups"]
    return [
        {"id": b["id"], "time": b["time"], "mode": b["mode"], "count": b["count"],
         "bytes": b["bytes"], "note": b.get("note", "")}
        for b in items[::-1]
    ]


def restore_backup(backup_id: str, root: Path | None = None) -> dict:
    """把某次备份解回存档目录。解之前先把当前存档另存一份，防止越修越糟。"""
    index = _load_index()
    entry = next((b for b in index["backups"] if b["id"] == backup_id), None)
    if entry is None:
        raise FileNotFoundError(i18n.t("sav.not_found", id=backup_id))

    archive = SAVE_DIR / entry["file"]
    if not archive.is_file():
        raise FileNotFoundError(i18n.t("sav.file_missing", path=archive))

    dest = gamesaves_dir(root)
    guard.allow(DATA_DIR, dest)

    safety_copy = {}
    if dest.is_dir() and any(dest.iterdir()):
        safety_copy = backup(root, mode="full", note=i18n.t("sav.restore_note", id=backup_id))

    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(dest)

    return {"ok": True, "id": backup_id, "restored": entry["count"],
            "safety_copy": safety_copy.get("id", ""), "to": str(dest)}
