"""被合并过的游戏配置：原始基线 + 每个包装了什么。

两个文件：

- `data/baseline/<相对路径>` —— **第一次动这个文件之前**的内容，永不被覆盖。
  它代表「没有这些 mod 时游戏长什么样」，是最后的安全网。
- `data/config_patches.json` —— 哪个包、改了哪个文件的哪些键。
  卸载就照这份反向撤销，而不是整份还原。

为什么不只用 journal 快照
------------------------
journal 只保留最近 30 次操作，且按「操作」组织。这里的记录按「文件」组织，
要回答的是「这个键是谁加的、原来是什么值」，卸载时必须能一条条查。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from . import config_merge
from .paths import DATA_DIR

BASELINE_DIR = DATA_DIR / "baseline"
PATCHES = DATA_DIR / "config_patches.json"


# ---------------- 基线 ----------------
def _baseline_path(rel: str) -> Path:
    rel = str(rel).replace("\\", "/").lstrip("/")
    return BASELINE_DIR / Path(rel)


def has_baseline(rel: str) -> bool:
    return _baseline_path(rel).is_file()


def ensure_baseline(rel: str, path: str | Path) -> bool:
    """第一次动这个文件之前存一份原样。已经有基线就什么都不做。

    返回 True 表示这次真的新建了基线。
    """
    dest = _baseline_path(rel)
    if dest.is_file():
        return False
    src = Path(path)
    if not src.is_file():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(src.read_bytes())
    return True


def read_baseline(rel: str) -> tuple[str, str] | None:
    """读基线文本，返回 (内容, 编码)。没有基线返回 None。"""
    p = _baseline_path(rel)
    if not p.is_file():
        return None
    try:
        return config_merge.load_text(p)
    except OSError:
        return None


def baseline_info(rel: str) -> dict | None:
    p = _baseline_path(rel)
    if not p.is_file():
        return None
    st = p.stat()
    return {"rel": rel, "path": str(p), "size": st.st_size,
            "saved_at": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}


# ---------------- 补丁账本 ----------------
def load() -> dict:
    if PATCHES.is_file():
        try:
            return json.loads(PATCHES.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return {"packages": {}}


def save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PATCHES.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def record(pkg_id: str, name: str, rel: str,
           changes: Iterable[config_merge.Change]) -> dict | None:
    """记下这个包改了哪些键。changes 为空则不记。"""
    items = [c.as_dict() for c in changes]
    if not items:
        return None
    data = load()
    pkg = data["packages"].setdefault(pkg_id, {"name": name, "files": {}})
    pkg["name"] = name
    pkg["files"][rel] = {
        "format": "ini",
        "changes": items,
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "baseline": str(_baseline_path(rel)) if has_baseline(rel) else "",
    }
    save(data)
    return pkg["files"][rel]


def for_pkg(pkg_id: str) -> dict[str, dict]:
    return load()["packages"].get(pkg_id, {}).get("files", {})


def packages_for_file(rel: str) -> list[dict]:
    """哪些包动过这个文件（用于提示「两个 mod 抢同一个键」）。"""
    out = []
    for pid, pkg in load()["packages"].items():
        if rel in pkg.get("files", {}):
            out.append({"id": pid, "name": pkg.get("name", pid),
                        "changes": pkg["files"][rel].get("changes", [])})
    return out


def drop(pkg_id: str) -> None:
    data = load()
    if data["packages"].pop(pkg_id, None) is not None:
        save(data)


def all_records() -> list[dict]:
    out = []
    for pid, pkg in load()["packages"].items():
        for rel, info in pkg.get("files", {}).items():
            out.append({"package_id": pid, "name": pkg.get("name", ""),
                        "rel": rel, "changes": len(info.get("changes", []))})
    return out


def reset() -> None:
    """测试用：清空账本（不动基线文件）。"""
    save({"packages": {}})
