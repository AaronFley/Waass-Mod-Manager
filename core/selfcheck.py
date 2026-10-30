"""启动自检：每次开程序先自己检查一遍，有事就在界面顶部说清楚。

检查什么
--------
1. **程序是不是刚被更新过** —— `data/meta.json` 里记着上一次运行的版本。
   对不上就说明用户换了新的 exe。这时候最要紧的不是「欢迎新版本」，
   而是确认「他原来的数据还读得出来」：模组列表、安装档案、Nexus ID、
   汉化备份，一个都不能丢。
2. **数据格式能不能被这一版程序读懂**（交给 compat 判定 + 迁移）。
3. **关键文件真的能解析**（json 坏没坏）、**data 目录真的能写**。
4. **更新源配得对不对** —— 联网查「有没有新版本」由 `/api/app/check` 干，
   那一步要等网络，不能卡启动；这里只检查配置本身合不合法。

为什么消息只给 code 不给句子
--------------------------
界面是双语的，而且用户随时能切。这里把「发生了什么」编码成
`{"code": "schema_migrated", "params": {"from": 2, "to": 3}}`，
前端用当前语言渲染 —— 切语言时连已经显示过的提醒都会跟着变。
（1.2.8 的教训：前端绝不拿译文去匹配后端返回值。）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import compat, guard
from .paths import CONFIG_FILE, DATA_DIR, dir_writable

CHECK_FILE = DATA_DIR / "selfcheck.json"
KEEP_HISTORY = 10

# 进程内的最近一次结果（/api/state 会被频繁拉取，别每次都读盘）
_MEM: dict | None = None

# 自检时会真的解析一遍的文件（顺序就是界面上报错的顺序）
_CHECK_FILES = ("config.json", "ui.json", "installed.json", "track.json",
                "nexus_ids.json", "localize/config.json")


def _is_url(v: str) -> bool:
    return v.startswith("http://") or v.startswith("https://")


def _json_ok(p: Path) -> bool:
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return True
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False


def _data_checks() -> tuple[dict, list[dict]]:
    """逐项体检。返回 (明细, 问题列表)。问题列表只装 code + params。"""
    detail: dict = {"files": {}, "writable": False, "config_readable": False}
    items: list[dict] = []

    for name in _CHECK_FILES:
        p = DATA_DIR / name
        if not p.is_file():
            detail["files"][name] = "missing"
            continue
        ok = _json_ok(p)
        detail["files"][name] = "ok" if ok else "bad"
        if not ok:
            items.append({"code": "bad_json", "level": "error",
                          "params": {"file": name}})

    # config 能读出来才有「上次配置的游戏目录」这一说
    cfg = {}
    if CONFIG_FILE.is_file():
        try:
            cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            detail["config_readable"] = isinstance(cfg, dict)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            cfg = {}

    # 配过的游戏目录还在不在（不在不是错误：用户可能把游戏挪了，
    # 程序会重新探测，这里只是提前说一声，省得他以为是扫描坏了）
    root = str((cfg or {}).get("game_root") or "").strip()
    if not root:
        # 从没保存过配置也能查到：探测结果是缓存的，后面扫描本来就要用，
        # 这里先问一次不额外花时间。查不到就当没有，绝不能因此报错。
        try:
            from .paths import detect_game_root
            found = detect_game_root()
            root = str(found) if found else ""
        except Exception:                       # noqa: BLE001
            root = ""
    if root:
        exists = Path(root).is_dir()
        detail["game_root"] = {"path": root, "exists": exists, "writable": None}
        if not exists:
            items.append({"code": "game_gone", "level": "warn",
                          "params": {"path": root}})
        else:
            # 游戏目录能不能写。Steam 默认装在 Program Files (x86)，那里对普通
            # 用户只读；不提前说，用户点「安装/启用/汉化」时才撞 WinError 5。
            # 只问权限不落文件，见 paths.dir_writable 的说明。
            target = Path(root) / "mods"
            ok = dir_writable(target if target.is_dir() else Path(root))
            detail["game_root"]["writable"] = ok
            if not ok:
                items.append({"code": "game_readonly", "level": "warn",
                              "params": {"path": root}})

    # 更新源配得对不对（有配但不像个网址 = 一定连不上）
    feed = str((cfg or {}).get("update_feed") or "").strip()
    detail["feed"] = bool(feed)
    if feed and not _is_url(feed):
        items.append({"code": "feed_bad", "level": "warn", "params": {}})

    # data 目录真的能写吗：写一个临时文件再删掉。只读盘 / 权限问题
    # 之前只会在用户安装 mod 的那一刻才暴露，那时已经点了半天。
    probe = DATA_DIR / ".selfcheck.tmp"
    try:
        guard.allow(DATA_DIR)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        detail["writable"] = True
    except Exception as exc:                        # noqa: BLE001
        detail["writable"] = False
        # 只读模式（数据来自更新版本）本来就不许写，不算故障
        if not guard.readonly():
            items.append({"code": "data_unwritable", "level": "error",
                          "params": {"why": type(exc).__name__}})

    return detail, items


def _level(items: list[dict]) -> str:
    rank = {"error": 3, "warn": 2, "info": 1}
    best = "ok"
    score = 0
    for it in items:
        s = rank.get(it.get("level", ""), 0)
        if s > score:
            score, best = s, it["level"]
    return best


def run(app_version: str, compat_report: dict | None = None) -> dict:
    """跑一次自检，落盘，返回报告。

    调用顺序很重要：compat.prepare() → selfcheck.run()。
    前者负责迁移并写 schema，后者才读得到「迁移过没有」和「上次是哪个版本」。
    """
    rep = compat_report or compat.prepare(app_version)
    prev = str((compat.read_meta() or {}).get("app") or "")

    detail, items = _data_checks()
    kind = rep.get("kind") or "ok"

    updated = bool(prev) and prev != app_version
    # 「首次运行」只认真正的第一次：data/ 干净。老用户升上来（有数据、没版本号）
    # 不算 —— 他更关心「数据已经迁过来了」，而不是「欢迎使用」。
    first_run = (not prev) and kind == "fresh" and not rep.get("migrated")

    # 迁移留下的痕迹（隔离了坏文件、备份失败…）转成界面能说的问题
    notes = list(rep.get("notes") or [])
    quarantined = [n.split(":", 1)[1].split("->")[0]
                   for n in notes if n.startswith("quarantined:")]
    for name in quarantined:
        items.append({"code": "file_quarantined", "level": "warn",
                      "params": {"file": name}})
    if "backup_failed" in notes:
        items.append({"code": "backup_failed", "level": "error", "params": {}})
    if any(n.startswith("no_step_for") or "_failed" in n for n in notes
           if not n.startswith("quarantined:")):
        items.append({"code": "compat_step_failed", "level": "error", "params": {}})

    # ---- 版本与数据之间的那几句话，是整个模块的重点 ----
    if rep.get("readonly"):
        items.insert(0, {"code": "downgrade", "level": "error", "params": {
            "app_schema": rep.get("to") or compat.SCHEMA,
            "data_schema": rep.get("from") or 0}})
    elif rep.get("migrated"):
        items.insert(0, {"code": "schema_migrated", "level": "info", "params": {
            "from": rep.get("from") or 0, "to": rep.get("to") or compat.SCHEMA,
            "backup": rep.get("backup") or ""}})

    if updated and not rep.get("readonly"):
        # 「刚被更新过」→ 必须明确回答「旧数据还正常吗」，不能只说一句欢迎。
        # 降级时不走这里：只读那条已经说得更准确，再叠一条只会让人以为
        # 数据被改坏了。
        bad = [i for i in items if i["level"] == "error"]
        ok_n = sum(1 for v in detail["files"].values() if v == "ok")
        if bad:
            items.insert(0, {"code": "updated_data_issue", "level": "error",
                             "params": {"from": prev, "to": app_version,
                                        "n": len(bad)}})
        else:
            items.insert(0, {"code": "updated_data_ok", "level": "info",
                             "params": {"from": prev, "to": app_version,
                                        "n": ok_n}})
    elif first_run:
        items.append({"code": "first_run", "level": "info",
                      "params": {"version": app_version}})

    report = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "app": app_version,
        "prev_app": prev,
        "updated": updated,
        "first_run": first_run,
        "kind": kind,
        "app_schema": compat.SCHEMA,
        # 数据的**实际**版本（不是迁移目标）：降级时它是 9，界面要如实说
        # 「这份数据是第 9 版，本程序只认到第 3 版」。
        "data_schema": compat.state()["from"] or compat.SCHEMA,
        "readonly": bool(rep.get("readonly")),
        "backup": rep.get("backup") or "",
        "detail": detail,
        "items": items,
        "level": _level(items),
        "notes": notes[:20],
    }

    # 记住「这一版跑过了」。放在最后：万一自检中途崩，下次还会认为刚更新，
    # 重新校验一遍 —— 宁可多检查一次，也不要漏掉一次数据核对。
    compat.touch(app_version)

    _save(report)
    return report


def _save(report: dict) -> None:
    global _MEM
    old = {}
    try:
        old = json.loads(CHECK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        old = {}
    hist = old.get("history") if isinstance(old.get("history"), list) else []
    hist.append({"at": report["at"], "app": report["app"],
                 "level": report["level"], "kind": report["kind"]})
    out = dict(report)
    out["history"] = hist[-KEEP_HISTORY:]
    # 先放内存：只读模式下写不了盘（guard 会拒），但**这次运行的自检结论
    # 必须能显示**——否则用户看到的就是「数据来自更新版本」却一个字都没说。
    _MEM = out
    try:
        guard.allow(DATA_DIR)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        guard.assert_can_write(CHECK_FILE, "自检记录")
        tmp = CHECK_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CHECK_FILE)
    except Exception:                               # noqa: BLE001 记录失败不影响使用
        pass


def cached() -> dict:
    """读最近一次自检结果。读不到就返回空壳，界面按「没自检过」处理。

    进程内缓存：`/api/state` 会被频繁拉取，自检结果在一次运行里不会变
    （除非用户手动点「重新自检」，那时 run() 会覆盖缓存）。
    """
    global _MEM
    if _MEM is not None:
        return _MEM
    try:
        raw = json.loads(CHECK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    _MEM = raw if isinstance(raw, dict) else {}
    return _MEM


def summary() -> dict:
    """给界面的那份。只带渲染需要的字段，别把整份体检明细塞进首页。"""
    rep = cached()
    if not rep:
        return {"level": "ok", "items": [], "updated": False, "readonly": False}
    return {
        "at": rep.get("at") or "",
        "app": rep.get("app") or "",
        "prev_app": rep.get("prev_app") or "",
        "updated": bool(rep.get("updated")),
        "first_run": bool(rep.get("first_run")),
        "kind": rep.get("kind") or "ok",
        "app_schema": rep.get("app_schema") or compat.SCHEMA,
        "data_schema": rep.get("data_schema") or compat.SCHEMA,
        "readonly": bool(rep.get("readonly")),
        "backup": rep.get("backup") or "",
        "level": rep.get("level") or "ok",
        "items": rep.get("items") or [],
    }
