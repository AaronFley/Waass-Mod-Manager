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
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from core import i18n

APP_NAME = "Waass Mod Manager"
APP_VERSION = "1.3.4"
APP_DATE = "2026-11-04"
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


def check(force: bool = False) -> dict:
    """查程序自己的新版本。任何失败都返回人能看懂的一句话，不抛异常。

    缓存
    ----
    结果存 `data/update.json`，24 小时内直接复用；过期后才真正联网，
    并带上上次的 `ETag` 走条件请求 —— 没变化时服务器只回 304，
    不重复下载正文。

    自动检查（`force=False`）遇到网络问题就用缓存里的旧结论，界面不报错；
    手动检查（`force=True`）必须给一句实话，因为用户是点了按钮在等结果。
    """
    feed = _feed()
    if not feed:
        return {
            "ok": False, "configured": False,
            "message": i18n.t("upd.no_feed"),
            "current": APP_VERSION,
        }

    cache = _load_cache()
    fresh = bool(cache.get("latest")) and \
        time.time() - float(cache.get("ts") or 0) < CACHE_TTL
    if fresh and not force:
        return _from_cache(cache, cached=True)

    headers = {
        "User-Agent": f"Waass/{APP_VERSION}",
        "Accept": "application/vnd.github+json, application/json",
    }
    etag = str(cache.get("etag") or "")
    if etag:
        headers["If-None-Match"] = etag
    req = urllib.request.Request(feed, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read(400_000)
            new_etag = r.headers.get("ETag") or etag
    except urllib.error.HTTPError as e:
        # 304：更新源说「跟你上次问的一样」，直接沿用缓存，不重写正文
        if e.code == 304 and cache.get("latest"):
            cache["ts"] = time.time()
            cache["checked_at"] = _now()
            _save_cache(cache)
            return _from_cache(cache, cached=True)
        # GitHub 在没有正式 Release 时 /releases/latest 返回 404 —— 不是故障
        if e.code == 404:
            return _fail("upd.feed_not_found", cache, manual=force)
        return _fail("upd.feed_unreachable", cache, manual=force, err=f"HTTP {e.code}")
    except Exception as e:                           # noqa: BLE001 网络问题不该崩界面
        return _fail("upd.feed_unreachable", cache, manual=force, err=type(e).__name__)

    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return _fail("upd.feed_bad_json", cache, manual=force)

    # GitHub Release 与自制 JSON 两种格式都认
    latest = str(data.get("tag_name") or data.get("version") or "").strip()
    url = str(data.get("html_url") or data.get("url") or "").strip()
    notes = str(data.get("body") or data.get("notes") or "").strip()
    if not latest:
        return _fail("upd.feed_no_version", cache, manual=force)

    entry = {
        "latest": latest, "url": url, "notes": _clean_notes(notes),
        "etag": new_etag, "ts": time.time(), "checked_at": _now(),
    }
    _save_cache(entry)
    return _from_cache(entry)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------

CACHE_NAME = "update.json"
CACHE_TTL = 24 * 3600           # 一天问一次就够了，更新没那么快


def _cache_file() -> Path:
    # 延迟导入：DATA_DIR 由环境变量决定，测试里换目录后才能生效
    from .paths import DATA_DIR
    return DATA_DIR / CACHE_NAME


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _load_cache() -> dict:
    try:
        raw = json.loads(_cache_file().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(entry: dict) -> None:
    p = _cache_file()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass                     # 只读或磁盘满：写不进缓存不影响这次的结果


def _from_cache(entry: dict, cached: bool = False, stale: bool = False) -> dict:
    """把缓存条目还原成接口结果。`newer` 现算 —— 程序升级后缓存可能还是旧的。"""
    from .track import compare
    latest = str(entry.get("latest") or "")
    newer = bool(latest) and compare(latest, APP_VERSION) == 1
    return {
        "ok": True, "configured": True,
        "current": APP_VERSION, "latest": latest,
        "newer": newer,
        "url": str(entry.get("url") or ""),
        "notes": _clean_notes(str(entry.get("notes") or "")),
        "cached": cached, "stale": stale,
        "checked_at": str(entry.get("checked_at") or ""),
        "message": (i18n.t("upd.new_version", ver=latest) if newer
                    else i18n.t("upd.up_to_date")),
    }


def _clean_notes(raw: str) -> str:
    """更新说明是给界面看的纯文本：去掉 markdown 的结构符号，压掉多余空行。

    Release 正文的排版是给网页用的，直接塞进小框里会是一堆 `##`、表格竖线和星号。
    """
    keep: list[str] = []
    for ln in raw.splitlines():
        t = ln.strip()
        if not t:
            keep.append("")
            continue
        if t.startswith("> "):                  # 引用块：留文字，去符号
            keep.append(t[2:].rstrip())
            continue
        if t.startswith(("#", "![", "|", "---", "***")):
            continue
        keep.append(ln.rstrip())
    out = "\n".join(keep).replace("**", "").replace("`", "")
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    return out.strip()[:600]


def _fail(key: str, cache: dict, manual: bool = False, **kw) -> dict:
    """联网失败。自动检查退回上一次的结论（标 stale），手动检查如实报错。

    手动检查不能拿旧结论充数：用户是点了按钮在等结果，一句「已是最新」
    在断网时是骗人的。
    """
    if cache.get("latest") and not manual:
        out = _from_cache(cache, cached=True, stale=True)
        out["error"] = i18n.t(key, **kw)
        return out
    return {"ok": False, "configured": True, "current": APP_VERSION,
            "message": i18n.t(key, **kw)}

