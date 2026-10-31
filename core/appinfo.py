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
import urllib.error
import urllib.request
from pathlib import Path

from core import i18n

APP_NAME = "Waass Mod Manager"
APP_VERSION = "1.3.0"
APP_DATE = "2026-10-31"
APP_REPO = "https://github.com/AaronFley/Waass-Mod-Manager"

# 默认更新源。用户没在 config.json 里填 update_feed 时用它，
# 所以发布版开箱就能查更新，不需要任何配置。
DEFAULT_FEED = ("https://api.github.com/repos/AaronFley/"
                "Waass-Mod-Manager/releases/latest")
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
    """更新源：用户填的优先；没填就用内置默认；`update_check: false` 表示不查。"""
    from .paths import load_config
    cfg = load_config()
    if cfg.get("update_check") is False:
        return ""
    custom = str(cfg.get("update_feed") or "").strip()
    return custom or DEFAULT_FEED


def check() -> dict:
    """查程序自己的新版本。任何失败都返回人能看懂的一句话，不抛异常。"""
    from .track import compare
    feed = _feed()
    if not feed:
        return {
            "ok": False, "configured": False,
            "message": i18n.t("upd.no_feed"),
            "current": APP_VERSION,
        }
    req = urllib.request.Request(feed, headers={
        "User-Agent": f"Waass/{APP_VERSION}",
        "Accept": "application/vnd.github+json, application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read(400_000)
    except urllib.error.HTTPError as e:
        # GitHub 在没有正式 Release 时 /releases/latest 返回 404 —— 这不是故障
        if e.code == 404:
            return {"ok": False, "configured": True, "current": APP_VERSION,
                    "message": i18n.t("upd.feed_not_found")}
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": i18n.t("upd.feed_unreachable", err=f"HTTP {e.code}")}
    except Exception as e:                           # noqa: BLE001 网络问题不该崩界面
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": i18n.t("upd.feed_unreachable", err=type(e).__name__)}

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": i18n.t("upd.feed_bad_json")}

    # GitHub Release 与自制 JSON 两种格式都认
    latest = str(data.get("tag_name") or data.get("version") or "").strip()
    url = str(data.get("html_url") or data.get("url") or "").strip()
    notes = str(data.get("body") or data.get("notes") or "").strip()
    if not latest:
        return {"ok": False, "configured": True, "current": APP_VERSION,
                "message": i18n.t("upd.feed_no_version")}

    c = compare(latest, APP_VERSION)
    return {
        "ok": True, "configured": True,
        "current": APP_VERSION, "latest": latest,
        "newer": c == 1, "url": url, "notes": notes[:600],
        "message": i18n.t("upd.new_version", ver=latest) if c == 1 else i18n.t("upd.up_to_date"),
    }
