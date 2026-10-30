"""用户自己的分组与标签。

为什么不复用 Nexus 的分类：Nexus 只有「这个 mod 在 N 网上被归到哪一类」，
而实际用起来更关心「这个 mod 属于我这套整合包的哪一块」「它是不是必备」。
两套东西并存：分类是只读的元数据，分组和标签归用户。

存储：data/library.json。只存 key，不存路径 —— 用户改文件夹名也不会错位。
"""
from __future__ import annotations

import re
import time
import uuid
from datetime import datetime

from .paths import DATA_DIR, load_config, save_config

LIB_FILE = DATA_DIR / "library.json"

_EMPTY = {"groups": [], "tags": {}, "updated": ""}


def _load() -> dict:
    import json
    if LIB_FILE.is_file():
        try:
            d = json.loads(LIB_FILE.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                d.setdefault("groups", [])
                d.setdefault("tags", {})
                return d
        except (OSError, json.JSONDecodeError):
            pass
    return {"groups": [], "tags": {}, "updated": ""}


def _save(d: dict) -> None:
    import json
    d["updated"] = datetime.now().isoformat(timespec="seconds")
    try:
        LIB_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = LIB_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(LIB_FILE)
    except OSError:
        pass


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:40]


# ---------------- 分组 ----------------
def groups() -> list[dict]:
    return _load()["groups"]


def create_group(name: str, members: list[str] | None = None) -> dict:
    name = _clean(name)
    if not name:
        raise ValueError("分组名不能为空")
    d = _load()
    if any(g["name"] == name for g in d["groups"]):
        raise ValueError(f"已经有叫「{name}」的分组了")
    g = {
        "id": "g_" + uuid.uuid4().hex[:8],
        "name": name,
        "members": list(dict.fromkeys(members or [])),
        "created": time.time(),
    }
    d["groups"].append(g)
    _save(d)
    return g


def rename_group(gid: str, name: str) -> dict:
    name = _clean(name)
    if not name:
        raise ValueError("分组名不能为空")
    d = _load()
    for g in d["groups"]:
        if g["id"] == gid:
            g["name"] = name
            _save(d)
            return g
    raise KeyError("找不到这个分组")


def delete_group(gid: str) -> dict:
    d = _load()
    before = len(d["groups"])
    d["groups"] = [g for g in d["groups"] if g["id"] != gid]
    _save(d)
    return {"ok": len(d["groups"]) != before}


def set_member(gid: str, key: str, on: bool) -> dict:
    d = _load()
    for g in d["groups"]:
        if g["id"] != gid:
            continue
        m = set(g.get("members") or [])
        if on:
            m.add(key)
        else:
            m.discard(key)
        # 一个 mod 只归一个分组，避免出现"同时在两个组里"的歧义
        if on:
            for other in d["groups"]:
                if other["id"] != gid and key in (other.get("members") or []):
                    other["members"] = [x for x in other["members"] if x != key]
        g["members"] = sorted(m)
        _save(d)
        return {"ok": True, "group": g}
    raise KeyError("找不到这个分组")


def members_of(gid: str) -> list[str]:
    for g in groups():
        if g["id"] == gid:
            return list(g.get("members") or [])
    return []


def group_of(key: str) -> dict | None:
    for g in groups():
        if key in (g.get("members") or []):
            return {"id": g["id"], "name": g["name"]}
    return None


# ---------------- 标签 ----------------
def all_tags() -> list[dict]:
    """所有出现过的标签 + 用量。"""
    counts: dict[str, int] = {}
    for tags in _load()["tags"].values():
        for t in tags:
            counts[t] = counts.get(t, 0) + 1
    return [{"name": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]


def tags_of(key: str) -> list[str]:
    return list(_load()["tags"].get(key) or [])


def set_tags(key: str, tags: list[str]) -> list[str]:
    d = _load()
    clean: list[str] = []
    for t in tags:
        t = _clean(t)
        if t and t not in clean:
            clean.append(t)
    clean = clean[:12]
    if clean:
        d["tags"][key] = clean
    else:
        d["tags"].pop(key, None)
    _save(d)
    return clean


def add_tag(key: str, tag: str) -> list[str]:
    return set_tags(key, tags_of(key) + [tag])


def remove_tag(key: str, tag: str) -> list[str]:
    return set_tags(key, [t for t in tags_of(key) if t != tag])


def index() -> dict:
    """给界面用的一份总索引。"""
    d = _load()
    by_key: dict[str, dict] = {}
    for g in d["groups"]:
        for k in (g.get("members") or []):
            by_key.setdefault(k, {})["group"] = {"id": g["id"], "name": g["name"]}
    for k, tags in d["tags"].items():
        by_key.setdefault(k, {})["tags"] = list(tags)
    return {
        "groups": [{"id": g["id"], "name": g["name"],
                    "count": len(g.get("members") or [])} for g in d["groups"]],
        "tags": all_tags(),
        "by_key": by_key,
        "updated": d.get("updated", ""),
    }


def auto_group_by_category(mods: list, name: str = "按分类整理") -> dict:
    """一键按现有分类生成分组 —— 给不知道该怎么分的人一个起点。"""
    from .scanner import CAT_CN

    buckets: dict[str, list[str]] = {}
    for m in mods:
        label = CAT_CN.get(m.category or "", "未分类")
        buckets.setdefault(label, []).append(m.key)

    d = _load()
    made = 0
    for label, keys in sorted(buckets.items(), key=lambda x: -len(x[1])):
        if any(g["name"] == label for g in d["groups"]):
            continue
        d["groups"].append({
            "id": "g_" + uuid.uuid4().hex[:8],
            "name": label,
            "members": keys,
            "created": time.time(),
        })
        made += 1
    _save(d)
    return {"ok": True, "created": made, "groups": groups()}
