"""安装档案：记住每个包装了什么、从哪来、现在什么状态。

为什么需要它
------------
"卸载"要知道删哪些东西。一个 Nexus 包经常是 mod 文件夹 + dlc 文件夹 +
菜单 xml 三件套，散在不同目录，靠猜必错。

同时从文件名里把 Nexus ID 和版本号读出来 —— Nexus 的下载文件名是固定格式：

    Light_Rewrite_12144_0.26.2_2026-10-23T03-19Z_864tMl89U.zip
                  ↑ID   ↑版本    ↑文件时间      ↑token

装上就有 ID 和版本，M4 检查更新不用再问用户。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime
from pathlib import Path

from .paths import DATA_DIR

REGISTRY = DATA_DIR / "installed.json"

# Nexus 文件名：名字_数字ID_版本_ISO时间_token.扩展名
#
# 分隔符有两种，都要认：
#     Light_Rewrite_12144_0.27.1_2026-10-24T18-32Z_Ukn0RyUwT.zip     ← 下划线
#     Light Rewrite 12144 0.28.0 2026-10-29T12-23Z oDUKYVijL.zip     ← 空格
# 2026-10-29 的事故：只认下划线，用户新下的三个包一个都没被认出来，
# 版本号停在旧包上，界面一直说「有更新」。分隔符放宽到「下划线或空格」。
_SEP = r"[_ ]"
_NEXUS_RE = re.compile(
    rf"^(?P<name>.+?){_SEP}(?P<id>\d{{3,8}}){_SEP}(?P<ver>[^_ ]+){_SEP}"
    rf"(?P<date>\d{{4}}-\d{{2}}-\d{{2}}T[\d\-]+Z){_SEP}(?P<token>[A-Za-z0-9]+)"
    rf"(?:\s*\(\d+\))?\.(?P<ext>zip|7z|rar|tar|gz)$",
    re.IGNORECASE,
)

# 老 mod 的包名长这样（大量 mod 至今还是这个格式）：
#     Hoods-4242-3-7-1698765432.zip
#     All Quest Markers-13087-1-7-3-1698765432.7z
#     UI Remastered - Next Gen-14578-1-1-2-1698765432.zip
# 也就是「名字-ID-版本(1~4 段)-Unix 时间戳」。版本段数不定，还用 - 和 _ 混着，
# 所以不能靠一条正则硬啃，改成从右往左数：时间戳 → 版本 → ID → 剩下的是名字。
_TS_RE = re.compile(r"^\d{9,13}$")
_NUM_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _parse_legacy_name(base: str) -> dict | None:
    p = Path(base)
    stem = re.sub(r"\s*\(\d+\)\s*$", "", p.stem)      # 重复下载的 " (1)"
    parts = [x for x in re.split(r"[-_]+", stem) if x]
    if len(parts) < 3:
        return None

    ts_i = -1
    for j in range(len(parts) - 1, 1, -1):
        if _TS_RE.match(parts[j]):
            ts_i = j
            break
    if ts_i < 2:
        return None

    run: list[str] = []
    k = ts_i - 1
    while k >= 0 and _NUM_RE.match(parts[k]) and len(run) < 5:
        run.insert(0, parts[k])
        k -= 1
    if not run:
        return None

    id_part, ver_parts = run[0], run[1:]
    if not (3 <= len(id_part) <= 6) or not id_part.isdigit():
        return None                        # 长度不像 Nexus ID，宁可不认

    name = " ".join(parts[:k + 1])
    name = re.sub(r"\s+", " ", name).strip(" -_")
    if len(name) < 2:
        return None
    return {
        "name": name,
        "nexus_id": int(id_part),
        "version": ".".join(ver_parts),
        "packed_at": parts[ts_i],
        "raw": p.name,
    }


def parse_archive_name(filename: str) -> dict:
    """从文件名尽量读出 (name, nexus_id, version)。读不出就返回空字段。"""
    base = Path(filename).name
    m = _NEXUS_RE.match(base)
    if m:
        return {
            "name": m.group("name").replace("_", " ").strip(),
            "nexus_id": int(m.group("id")),
            "version": m.group("ver"),
            "packed_at": m.group("date"),
            "raw": base,
        }
    old = _parse_legacy_name(base)
    if old:
        return old
    # 退一步：试着只找 "名字_数字_版本"（同样兼容空格分隔）
    m2 = re.match(rf"^(?P<name>.+?){_SEP}(?P<id>\d{{3,8}}){_SEP}(?P<ver>[\d.]+[a-z]?)",
                  base, re.I)
    if m2:
        return {
            "name": m2.group("name").replace("_", " ").strip(),
            "nexus_id": int(m2.group("id")),
            "version": m2.group("ver"),
            "packed_at": "",
            "raw": base,
        }
    return {"name": Path(base).stem, "nexus_id": None, "version": "", "packed_at": "", "raw": base}


def load() -> dict:
    if REGISTRY.is_file():
        try:
            data = json.loads(REGISTRY.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "packages" in data:
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return {"packages": []}


def save(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(REGISTRY)


def _new_id(name: str) -> str:
    """秒级时间戳 + 短随机串。只用时间戳的话，同一秒内覆盖安装两次会撞 id，
    后果是 update/get 拿到错误的那条记录。"""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-")[:40] or "package"
    return f"{stamp}_{slug}_{uuid.uuid4().hex[:6]}"


def make_id(name: str) -> str:
    return _new_id(name)


def add(entry: dict) -> dict:
    """登记一个安装包。同名旧包若还在，先标成 superseded（旧版不退场）。"""
    data = load()
    for p in data["packages"]:
        if p["state"] == "active" and p["name"].lower() == entry["name"].lower() \
                and p["version"] != entry.get("version", ""):
            p["state"] = "superseded"
            p["superseded_at"] = datetime.now().isoformat(timespec="seconds")
    data["packages"].append(entry)
    save(data)
    return entry


def new_entry(*, name: str, archive: str, nexus_id, version: str, paths: list[str],
              mods: list[str], dlc: list[str], menu_xmls: list[str],
              files: list[str], risk: str,
              op: str, mods_settings_delta: list[str] | None = None,
              quarantine: str = "", merged: list[str] | None = None) -> dict:
    return {
        "id": _new_id(name),
        "name": name,
        "archive": archive,
        "nexus_id": nexus_id,
        "version": version,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "ts": time.time(),
        "state": "active",          # active / removed / superseded
        "risk": risk,
        "paths": paths,             # 相对游戏根目录的全部写入目标
        "mods": mods,
        "dlc": dlc,
        "menu_xmls": menu_xmls,
        "files": files,
        # 游戏自带、被本包按行合并过的配置文件。这些文件属于游戏，
        # 卸载时只撤掉本包加/改的那几行，不搬走整个文件。
        "merged": merged or [],
        "mods_settings": mods_settings_delta or [],
        "op": op,                   # 对应的 journal 记录
        "quarantine": quarantine,   # 卸载后的隔离区子目录名
    }


def get(pkg_id: str) -> dict | None:
    for p in load()["packages"]:
        if p["id"] == pkg_id:
            return p
    return None


def update(pkg_id: str, **fields) -> dict | None:
    data = load()
    for p in data["packages"]:
        if p["id"] == pkg_id:
            p.update(fields)
            save(data)
            return p
    return None


def remove(pkg_id: str) -> bool:
    data = load()
    before = len(data["packages"])
    data["packages"] = [p for p in data["packages"] if p["id"] != pkg_id]
    save(data)
    return len(data["packages"]) != before


def prune_by_op(op_id: str) -> int:
    """撤销某次 journal 操作后，把对应的档案条目一并清掉，免得留下指向空处的记录。"""
    data = load()
    before = len(data["packages"])
    data["packages"] = [p for p in data["packages"] if p.get("op") != op_id]
    if len(data["packages"]) != before:
        save(data)
    return before - len(data["packages"])


def by_state(state: str = "active") -> list[dict]:
    return [p for p in load()["packages"] if p["state"] == state]


def find_by_mod(mod_key: str) -> dict | None:
    """反查：这个 mod 文件夹是哪个包装的。"""
    low = mod_key.lower().lstrip("~")
    for p in load()["packages"]:
        if any(m.lower().lstrip("~") == low for m in p.get("mods", [])):
            return p
        if any(Path(x).name.lower().lstrip("~") == low for x in p.get("paths", [])):
            return p
    return None
