"""数据兼容：换新版本的程序，老数据要能直接接着用。

用户的使用方式应该只有一句话
----------------------------
**把新的 Waass.exe 覆盖旧的，双击就行。** 不需要导出、导入、搬文件夹。

所以本模块负责三件事：
1. 认出 data/ 是哪一版程序写下的（`data/meta.json` 里的 `schema`）；
2. 需要升级时，先整份备份核心数据，再按版本逐级迁移（幂等，跑两次结果一样）；
3. 数据来自**更新**版本的程序（用户把 exe 换回旧版）时，一切写入都停掉，
   进只读模式 —— 旧程序读不懂新字段，硬写会把新数据改坏。

不丢数据的底线
--------------
* 迁移只做「补齐 / 改名 / 重建索引」这类可逆动作，不改用户看得懂的取值；
* 认不出的文件一律**改名保留**（`x.json.broken-20261030-2130`），绝不删除；
* 任何一步失败都保留备份路径，用户能原样搬回去。

schema 版本
-----------
1   1.0.x 早期（RMMR 导入阶段）
2   1.2.8 及以前：全部数据是若干 json + 目录，没有版本号
3   1.2.9 起：`meta.json` 登记 schema；自检结果落 `selfcheck.json`
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import guard
from .paths import CONFIG_FILE, DATA_DIR

SCHEMA = 3
META_FILE = DATA_DIR / "meta.json"

# 每个版本对应一句给人看的话（界面按 code 翻译，这里只是开发时的说明）
SCHEMA_NOTES = {
    1: "1.0.x early layout",
    2: "1.2.8 and earlier: json files, no schema marker",
    3: "1.2.9+: meta.json + selfcheck.json",
}

# 迁移时关心的数据文件。列出来是为了「哪些文件该跟着备份 / 该体检」有唯一出处。
CORE_FILES = (
    "config.json",
    "ui.json",
    "installed.json",        # 安装档案（registry.py）
    "track.json",            # 模组跟进 / 忽略
    "nexus_ids.json",        # 手工绑定的 Nexus ID
    "nexus_guesses.json",    # 候选缓存
    "legacy.json",           # RMMR 导入记录
    "library.json",
    "integrity.json",
    "config_patches.json",
    "journal.json",          # 写操作日志（可撤销的凭据）
    "names.json",
)

# 迁移时要确保存在的目录（老用户升级时建，全新安装不建 —— 少一层空目录）
CORE_DIRS = ("snapshots", "localize", "translate", "nexus", "thumbs", "saves")


# ---------------- 读 meta ----------------

def read_meta() -> dict:
    try:
        raw = json.loads(META_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    try:
        raw["schema"] = int(raw.get("schema") or 0)
    except (TypeError, ValueError):
        raw["schema"] = 0
    return raw


def _write_meta(meta: dict) -> None:
    guard.allow(DATA_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    guard.assert_can_write(META_FILE, "数据版本记录")
    tmp = META_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(META_FILE)


def _has_any_data() -> bool:
    """判断 data/ 里有没有「用户的」东西。

    只要不是全空就算有 —— 一个 config.json 就说明用过。空目录（比如刚 mkdir
    出来的 webview）不算。
    """
    if CONFIG_FILE.is_file():
        return True
    for name in CORE_FILES:
        if (DATA_DIR / name).is_file():
            return True
    return False


# ---------------- 状态判定 ----------------

def state() -> dict:
    """现在该走哪条路。

    kind:
      fresh       全新安装，data/ 干干净净
      legacy      有数据但没版本号（1.2.8 及以前装过），按 schema 2 处理
      ok          版本正好
      upgrade     数据比程序旧，需要迁移
      downgrade   数据比程序新（用户把 exe 换回旧版），只读
    """
    meta = read_meta()
    marked = meta.get("schema") or 0
    if not marked:
        return {"kind": "legacy" if _has_any_data() else "fresh",
                "from": 2 if _has_any_data() else 0, "to": SCHEMA,
                "app": meta.get("app") or ""}
    if marked < SCHEMA:
        return {"kind": "upgrade", "from": marked, "to": SCHEMA,
                "app": meta.get("app") or ""}
    if marked == SCHEMA:
        return {"kind": "ok", "from": marked, "to": SCHEMA,
                "app": meta.get("app") or ""}
    return {"kind": "downgrade", "from": marked, "to": SCHEMA,
            "app": meta.get("app") or ""}


# ---------------- 备份 ----------------

def _backup_dir(from_schema: int) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return DATA_DIR / "_backup" / f"pre-schema{from_schema}-{stamp}"


def _backup(from_schema: int) -> tuple[str, list[str]]:
    """把核心文件复制一份。目录整体复制太慢（webview 有几百 MB），只挑数据。"""
    dst = _backup_dir(from_schema)
    copied: list[str] = []
    guard.allow(DATA_DIR)
    try:
        dst.mkdir(parents=True, exist_ok=True)
        for name in CORE_FILES:
            src = DATA_DIR / name
            if not src.is_file():
                continue
            guard.assert_can_write(dst / name, "迁移前备份")
            shutil.copy2(src, dst / name)
            copied.append(name)
        # 迁移会碰到的目录：只备份其中「小且重要」的索引，不搬整棵
        for sub in ("localize", "snapshots"):
            d = DATA_DIR / sub
            if d.is_dir():
                target = dst / sub
                target.mkdir(parents=True, exist_ok=True)
                for p in list(d.iterdir())[:200]:
                    if p.is_file():
                        guard.assert_can_write(target / p.name, "迁移前备份")
                        shutil.copy2(p, target / p.name)
                        copied.append(f"{sub}/{p.name}")
        (dst / "README.txt").write_text(
            "迁移前的数据备份，由 Waass 在升级时自动生成。\n"
            "需要回退：把这里的文件复制回 data/ 覆盖即可。\n",
            encoding="utf-8")
    except Exception as exc:                        # noqa: BLE001 备份失败要传出去，不能静默
        return "", [f"{type(exc).__name__}: {exc}"]
    return str(dst), copied


# ---------------- 迁移 ----------------

def _quarantine_broken(notes: list[str]) -> list[str]:
    """读不出来的 json 改名保留。

    坏文件的典型来源：磁盘写满、断电写了一半、用户手改漏了括号。
    直接删等于替他做决定；改名之后程序会当成「没有这份记录」重新生成，
    而原件还在，出问题能捞回来。
    """
    broken: list[str] = []
    for name in CORE_FILES:
        p = DATA_DIR / name
        if not p.is_file():
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            stamp = time.strftime("%Y%m%d-%H%M%S")
            dst = p.with_name(f"{p.name}.broken-{stamp}")
            try:
                guard.assert_can_write(dst, "隔离损坏的数据文件")
                p.replace(dst)
                broken.append(name)
                notes.append(f"quarantined:{name}->{dst.name}")
            except Exception as exc:                # noqa: BLE001
                notes.append(f"quarantine_failed:{name}:{type(exc).__name__}")
    return broken


def _ensure_dirs(notes: list[str]) -> None:
    guard.allow(DATA_DIR)
    for sub in CORE_DIRS:
        d = DATA_DIR / sub
        if d.is_dir():
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            notes.append(f"mkdir_failed:{sub}:{type(exc).__name__}")


def _migrate_2_to_3(notes: list[str]) -> None:
    """1.2.8 → 1.2.9。

    这一版**没有改任何数据格式**：所有文件原样能读。做的只是三件事：
    把老用户的常用目录补齐、把读不出来的文件隔离掉、登记版本号。
    以后真有格式变化，在这里加 3→4，历史步骤不用动。
    """
    _ensure_dirs(notes)
    _quarantine_broken(notes)


_STEPS = {2: _migrate_2_to_3}


def prepare(app_version: str) -> dict:
    """启动时调用一次。返回给人看的报告（全部是 code + params，不装译文）。

    {
      "kind": "fresh|legacy|ok|upgrade|downgrade",
      "from": 2, "to": 3,
      "backup": "路径" or "",
      "migrated": True/False,
      "readonly": True/False,
      "notes": ["quarantined:track.json->track.json.broken-...", ...]
    }
    """
    st = state()
    out = {
        "kind": st["kind"], "from": st["from"], "to": SCHEMA,
        "backup": "", "migrated": False, "readonly": False, "notes": [],
    }
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%S")

    if st["kind"] == "downgrade":
        # 旧程序不能碰新数据。只读由 guard 统一拦（界面也会说明原因）。
        os.environ[guard.ENV_READONLY] = "1"
        out["readonly"] = True
        return out

    if st["kind"] in ("fresh", "ok"):
        # 什么都不用改，只补 meta（老逻辑没有 meta 就写一份，幂等）
        # 注意：**不写 meta["app"]** —— 那个字段的语义是「上一次运行的程序版本」，
        # 由 touch() 独占维护。这里写会让自检永远认为「版本没变」。
        meta = read_meta() or {}
        meta.setdefault("first_seen", now_iso)
        meta["schema"] = SCHEMA
        meta["last_seen"] = now_iso
        try:
            _write_meta(meta)
        except Exception as exc:                    # noqa: BLE001
            out["notes"].append(f"meta_write_failed:{type(exc).__name__}")
        return out

    # legacy / upgrade：备份 → 逐级迁移 → 登记
    guard.allow(DATA_DIR)
    backup, copied = _backup(st["from"])
    if backup:
        out["backup"] = backup
        out["notes"].append(f"backup_files:{len(copied)}")
    else:
        # 备份失败就不动数据。宁可这次不迁移，也不能裸着改。
        out["notes"].append("backup_failed")
        out["notes"].extend(copied[:3])
        return out

    cur = st["from"]
    while cur < SCHEMA:
        step = _STEPS.get(cur)
        if step is None:
            out["notes"].append(f"no_step_for:{cur}")
            break
        try:
            step(out["notes"])
        except Exception as exc:                    # noqa: BLE001
            out["notes"].append(f"step_{cur}_failed:{type(exc).__name__}")
            break
        cur += 1

    meta = read_meta() or {}
    meta.setdefault("first_seen", now_iso)
    meta["schema"] = cur
    meta["last_seen"] = now_iso
    hist = meta.get("history")
    if not isinstance(hist, list):
        hist = []
    hist.append({"schema": cur, "app": app_version, "at": now_iso})
    meta["history"] = hist[-20:]
    try:
        _write_meta(meta)
    except Exception as exc:                        # noqa: BLE001
        out["notes"].append(f"meta_write_failed:{type(exc).__name__}")

    out["migrated"] = cur > st["from"]
    out["from"] = st["from"]
    out["to"] = cur
    return out


def touch(app_version: str) -> str:
    """记下「这一版跑过了」，返回上一次记录的程序版本（自检用它判断刚被更新）。

    返回空串表示第一次运行（没有记录）。
    """
    meta = read_meta() or {}
    prev = str(meta.get("app") or "")
    if prev == app_version:
        # 同一版重复启动不刷 history，只更新 last_seen
        meta["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            _write_meta(meta)
        except Exception:                           # noqa: BLE001
            pass
        return prev
    meta["app"] = app_version
    meta["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    meta.setdefault("schema", SCHEMA)
    meta.setdefault("first_seen", meta["last_seen"])
    try:
        _write_meta(meta)
    except Exception:                               # noqa: BLE001
        pass
    return prev


def dump() -> dict:
    """给设置页 / 自检面板看的现状。"""
    st = state()
    meta = read_meta()
    return {
        "app_schema": SCHEMA,
        "data_schema": st["from"],
        "kind": st["kind"],
        "last_app": meta.get("app") or "",
        "first_seen": meta.get("first_seen") or "",
        "last_seen": meta.get("last_seen") or "",
        "meta_file": str(META_FILE),
        "notes": {k: v for k, v in sorted(SCHEMA_NOTES.items())},
    }
