"""程序自身的信息与更新检查（检查的是 Waass 这个程序，不是 mod）。

更新源怎么配
------------
在 data/config.json 里加一行，二选一：

    "update_feed": "https://api.github.com/repos/你/waass/releases/latest"
        走 GitHub Release（认 tag_name / html_url / body）

    "update_feed": "https://你的域名/waass/version.json"
        自己放个 JSON：{"version": "1.2.0", "url": "下载页", "notes": "改了什么"}

没配就没有更新源，界面上会如实写「本地开发版」，不会假装「已是最新」。
"""
from __future__ import annotations

import json
import platform
import sys
import urllib.request
from pathlib import Path

APP_NAME = "Waass Mod Manager"
APP_VERSION = "1.2.6"
APP_DATE = "2026-10-30"
APP_REPO = ""                     # 发布后填 GitHub 地址
LICENSE = "MIT"


def info(game_root: Path | None = None, data_dir: Path | None = None,
         counts: dict | None = None) -> dict:
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "date": APP_DATE,
        "license": LICENSE,
        "repo": APP_REPO,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "os": f"{platform.system()} {platform.release()}",
        "frozen": bool(getattr(sys, "frozen", False)),
        "data_dir": str(data_dir or ""),
        "game_root": str(game_root or ""),
        "counts": counts or {},
        "feed": bool(_feed()),
    }


def _feed() -> str:
    from .paths import load_config
    return str(load_config().get("update_feed") or "").strip()


def check() -> dict:
    """查程序自己的新版本。任何失败都返回人能看懂的一句话，不抛异常。"""
    from .track import compare
    feed = _feed()
    if not feed:
        return {
            "ok": False, "configured": False,
            "message": "这是本地开发版，还没有配置更新源。发布到 GitHub 后，"
                       "在 config.json 里填 update_feed 就能自动检查。",
            "current": APP_VERSION,
        }
    req = urllib.request.Request(feed, headers={
        "User-Agent": f"Waass/{APP_VERSION}",
        "Accept": "application/vnd.github+json, application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read(400_000)
    except Exception as e:                           # noqa: BLE001 网络问题不该崩界面
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": f"连不上更新源：{type(e).__name__}"}

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": "更新源返回的不是 JSON"}

    # GitHub Release 与自制 JSON 两种格式都认
    latest = str(data.get("tag_name") or data.get("version") or "").strip()
    url = str(data.get("html_url") or data.get("url") or "").strip()
    notes = str(data.get("body") or data.get("notes") or "").strip()
    if not latest:
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": "更新源里没有版本号"}

    c = compare(latest, APP_VERSION)
    return {
        "ok": True, "configured": True,
        "current": APP_VERSION, "latest": latest,
        "newer": c == 1, "url": url, "notes": notes[:600],
        "message": ("有新版本 " + latest) if c == 1 else "已经是最新版本",
    }
