"""从 Nexus 官网取 mod 资料：作者、封面、最新版本、简介、下载量。

为什么不走官方 API
------------------
官方 API 要 Personal Key，免费账号体验差，Key 还会过期。这里用的是 Nexus
**新版网页前端自己在用的** 公开查询接口：

    POST https://api-router.nexusmods.com/graphql

它只读、只返回公开数据、不需要登录、不需要 Key。官网的 HTML 页面有 Cloudflare
人机校验（程序访问一律 403 "Just a moment..."），但这个接口没有，所以用它。

边界（这几条是硬规则）
----------------------
* **只读**。不下载 mod 文件、不碰账号、请求里不带任何身份信息。
* **不在扫描时联网**。请求只发生在三个时刻：装新包时（可在设置里关）、
  启动后补齐缺失资料（每次启动最多一轮）、你手动点「刷新」时。不做轮询。
* 缓存 7 天（`data/nexus/<id>.json`），没过期就不重复请求。
* 任何失败都静默返回空 —— 断网、接口变了、被限流，界面照常用，只是显示
  「未记录」，绝不阻塞安装和扫描。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

from .paths import DATA_DIR

GAME_ID = 952                     # Nexus 上 The Witcher 3 的固定编号
GQL = "https://api-router.nexusmods.com/graphql"
PAGE = "https://www.nexusmods.com/witcher3/mods/{id}"
SITE = "https://www.nexusmods.com/witcher3"

CACHE_DIR = DATA_DIR / "nexus"
COVER_DIR = DATA_DIR / "thumbs"

TTL_DAYS = 7                      # 资料多久算旧
MIN_GAP = 0.8                     # 两次请求之间至少隔这么久，别把人网站当靶子
COOL_DOWN = 300                   # 失败后 5 分钟内不再试，避免反复撞墙
MAX_IDS = 60                      # 一次批量最多问这么多个
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0")

_lock = threading.Lock()
_state = {"last": 0.0, "fail_until": 0.0, "ok_at": 0.0, "last_error": ""}

MOD_FIELDS = ("modId name author version summary category pictureUrl thumbnailUrl "
              "downloads endorsements updatedAt adultContent")

# 文件清单里挑「主文件」用得上的字段（category 是枚举，不能带子选择）
FILE_FIELDS = "name version date primary category"

# 这些分类不算「这个 mod 的最新版」：
#   OPTIONAL / MISCELLANEOUS —— 可选的汉化、兼容补丁，版本号自己走
#   OLD_VERSION / ARCHIVED   —— 作者归档的旧版本
# 混进来就会出现「主文件 1.9.2，却报说最新是 1.10 的德语翻译包」这种结论。
MAIN_CATS = ("MAIN",)


class NexusError(RuntimeError):
    pass


# ---------------- 底层请求 ----------------
def _gql(query: str, timeout: float = 12.0) -> dict:
    with _lock:
        gap = MIN_GAP - (time.time() - _state["last"])
        if gap > 0:
            time.sleep(gap)
        if time.time() < _state["fail_until"]:
            raise NexusError("刚刚请求失败过，稍后再试")
        _state["last"] = time.time()

    body = json.dumps({"query": query}).encode("utf-8")
    req = urllib.request.Request(
        GQL, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": UA,
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        with _lock:
            _state["fail_until"] = time.time() + COOL_DOWN
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
        raise NexusError(f"连不上 Nexus：{exc}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise NexusError("Nexus 返回的不是 JSON") from exc

    if isinstance(data, dict) and data.get("errors") and not data.get("data"):
        msg = str((data["errors"][0] or {}).get("message", ""))[:160]
        raise NexusError(f"Nexus 说：{msg}")
    if not isinstance(data, dict):
        raise NexusError("Nexus 返回格式意外")
    with _lock:
        _state["ok_at"] = time.time()
        _state["last_error"] = ""
    return data


# ---------------- 数据整形 ----------------
def _norm(node: dict) -> dict:
    """GraphQL 的一条结果 → 我们存的样子。"""
    try:
        mod_id = int(node.get("modId") or 0)
    except (TypeError, ValueError):
        mod_id = 0
    if not mod_id:
        raise NexusError("结果里没有 modId")
    return {
        "mod_id": mod_id,
        "name": str(node.get("name") or "").strip(),
        "author": str(node.get("author") or "").strip(),
        "version": str(node.get("version") or "").strip(),
        "summary": _clip(str(node.get("summary") or "").strip(), 600),
        "category": str(node.get("category") or "").strip(),
        "picture": str(node.get("pictureUrl") or "").strip(),
        "thumb": str(node.get("thumbnailUrl") or "").strip(),
        "downloads": _int(node.get("downloads")),
        "endorsements": _int(node.get("endorsements")),
        "updated_at": str(node.get("updatedAt") or "").strip(),
        "adult": bool(node.get("adultContent")),
        "url": PAGE.format(id=mod_id),
    }


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# ---------------- 按 ID 查 ----------------
def fetch(ids, timeout: float = 12.0, with_files: bool = True) -> dict[str, dict]:
    """批量取资料。返回 {id字符串: 资料}；失败返回 {}，不抛给调用方。

    `with_files=True` 时会顺带查每个 mod 的文件清单，把主文件版本写进
    `main_version` —— 这才是「最新版」的准信。每个 mod 多一次请求，
    但结果缓存 7 天，只在真的需要刷新时才付这个成本。
    """
    want = []
    for x in ids or []:
        s = str(x or "").strip()
        if s.isdigit() and s not in want:
            want.append(s)
    if not want:
        return {}
    want = want[:MAX_IDS]
    parts = ",".join("{gameId:%d,modId:%s}" % (GAME_ID, i) for i in want)
    query = ("{legacyMods(ids:[%s]){nodes{%s}}}" % (parts, MOD_FIELDS))
    try:
        data = _gql(query, timeout=timeout)
    except NexusError:
        return {}
    nodes = (((data.get("data") or {}).get("legacyMods") or {}).get("nodes") or [])
    out: dict[str, dict] = {}
    for node in nodes:
        try:
            rec = _norm(node)
        except NexusError:
            continue
        out[str(rec["mod_id"])] = rec
    if out and with_files:
        for key, rec in out.items():
            f = mod_files(key, timeout)
            if f:
                rec.update(f)
    if out:
        save(out)
    return out


# ---------------- 文件清单：取「主文件版本」 ----------------
def _pick_main(files: list[dict]) -> dict:
    """从文件清单里挑出代表「这个 mod 最新版」的那一个。

    这一步是 1.0.9 的核心修正。mod 级的 `version` 字段是作者手填的展示值，
    经常落后于他自己上传的主文件（实测 17 个 mod 里有 3 个差 1～2 个热修版），
    拿它去比就会出现「本地 1.9.2.1 比 Nexus 的 1.9.2 还高」这种看着像坏了的结果。

    挑选顺序：
    1. 只看 MAIN 分类 —— 用户真正会装的那份
    2. MAIN 有多个版本时取版本号最大的（不是取上传时间最新的：作者重传旧文件
       会刷新时间，但版本号不变）
    3. 没有 MAIN 才退回 primary=1，再退回全部文件

    返回 {} 表示这个 mod 一个文件都没有（很少见，但别 assumed）。
    """
    from .version import vkey

    if not files:
        return {}
    main = [f for f in files if str(f.get("category") or "").upper() in MAIN_CATS]
    pool = main or [f for f in files if f.get("primary")] or files
    best = max(pool, key=lambda f: (vkey(f.get("version")), f.get("date") or 0))
    return {
        "main_version": str(best.get("version") or "").strip(),
        "main_file": str(best.get("name") or "").strip(),
        "main_date": int(best.get("date") or 0),
        "main_files": len(files),
    }


def mod_files(mod_id, timeout: float = 12.0) -> dict:
    """取一个 mod 的文件清单，算出主文件版本。失败一律返回 {}，不抛。"""
    try:
        mid = int(str(mod_id).strip())
    except (TypeError, ValueError):
        return {}
    query = ("{r:modFiles(modId:%d, gameId:%d){%s}}"
             % (mid, GAME_ID, FILE_FIELDS))
    try:
        data = _gql(query, timeout=timeout)
    except NexusError:
        return {}
    files = ((data.get("data") or {}).get("r") or [])
    if not isinstance(files, list):
        return {}
    out = _pick_main([f for f in files if isinstance(f, dict)])
    if out:
        out["files_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return out


# ---------------- 按名字搜 ----------------
def search(text: str, count: int = 8, timeout: float = 12.0) -> dict:
    """按名字找候选。Nexus 的匹配是词干级的，会带出翻译版和相似 mod，
    所以这里只当「候选清单」用，不替你决定是哪一个。"""
    q = (text or "").strip()
    if not q:
        return {"ok": False, "error": "没有关键词", "items": []}
    q = q.replace('"', " ").replace("\\", " ")[:80]
    query = (
        '{mods(filter:{op:AND,filter:[{gameId:{value:"%d",op:EQUALS}},'
        '{nameStemmed:{value:"%s",op:MATCHES}}]},'
        'sort:{downloads:{direction:DESC}},count:%d){nodes{%s}}}'
        % (GAME_ID, q, max(1, min(count, 20)), MOD_FIELDS))
    try:
        data = _gql(query, timeout=timeout)
    except NexusError as exc:
        return {"ok": False, "error": str(exc), "items": []}
    nodes = (((data.get("data") or {}).get("mods") or {}).get("nodes") or [])
    items = []
    for node in nodes:
        try:
            items.append(_norm(node))
        except NexusError:
            continue
    return {"ok": True, "query": q, "items": items,
            "search_page": f"{SITE}/search/?gsearch={urllib.parse.quote(q)}"}


def search_multi(queries, count: int = 8, timeout: float = 12.0) -> dict:
    """按从严到宽的顺序试几组关键词，第一组有结果就收工。

    `identify.search_queries()` 产出这些档位。为什么要多档：这个接口的
    `nameStemmed MATCHES` 是「几个词基本都得命中」，`auto loot enhanced`
    一条都没有，放宽到 `auto loot` 就有六条。只发一组全词 = 一大批模组
    永远看不到候选。

    代价是可能多发几次请求，所以：只要某次网络就报错（`ok` 为假）立刻停，
    不拿断网去撞后面几档；命中的组会写回 `tried` 方便排查。
    """
    tried: list[str] = []
    last: dict = {"ok": False, "error": "没有关键词", "items": []}
    for q in queries or []:
        r = search(q, count=count, timeout=timeout)
        tried.append(q)
        if r.get("items"):
            r = dict(r)
            r["tried"] = tried
            return r
        last = r
        if not r.get("ok"):
            break
    last = dict(last)
    last["tried"] = tried
    return last


# ---------------- 缓存 ----------------

def _path(mod_id) -> Path:
    return CACHE_DIR / f"{str(mod_id).strip()}.json"


def cached(mod_id) -> dict | None:
    """纯本地读，不联网。读不到就 None。"""
    p = _path(mod_id)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("mod_id") else None


def cached_all() -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not CACHE_DIR.is_dir():
        return out
    for p in CACHE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("mod_id"):
            out[str(data["mod_id"])] = data
    return out


def is_fresh(mod_id, days: int = TTL_DAYS) -> bool:
    data = cached(mod_id)
    if not data or not data.get("fetched_at"):
        return False
    try:
        at = datetime.fromisoformat(str(data["fetched_at"]))
    except ValueError:
        return False
    return (datetime.now().astimezone() - at) < timedelta(days=days)


def save(entries: dict) -> int:
    """把抓到的资料写进缓存。返回写了几条。"""
    if not entries:
        return 0
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return 0
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    n = 0
    for key, rec in entries.items():
        if not isinstance(rec, dict) or not rec.get("mod_id"):
            continue
        prev = cached(key) or {}
        item = dict(rec)
        item["fetched_at"] = now
        # 封面本地已有就保留，别每次清掉
        if prev.get("cover") and not _cover_path(key).is_file():
            item.pop("cover", None)
        elif prev.get("cover"):
            item["cover"] = prev["cover"]
        try:
            tmp = _path(key).with_suffix(".json.tmp")
            tmp.write_text(json.dumps(item, ensure_ascii=False, indent=1),
                           encoding="utf-8")
            tmp.replace(_path(key))
            n += 1
        except OSError:
            continue
    return n


def forget(mod_id) -> bool:
    """删掉某个 mod 的资料（含封面）。"""
    ok = False
    for p in (_path(mod_id), _cover_path(mod_id)):
        try:
            if p.is_file():
                p.unlink()
                ok = True
        except OSError:
            pass
    return ok


# ---------------- 封面 ----------------
def _cover_path(mod_id) -> Path:
    for ext in (".jpg", ".png", ".webp", ".jpeg", ".gif"):
        p = COVER_DIR / f"nexus_{mod_id}{ext}"
        if p.is_file():
            return p
    return COVER_DIR / f"nexus_{mod_id}.jpg"


def cover_rel(mod_id) -> str:
    """相对 data/ 的封面路径，给界面用；没有就空字符串。"""
    p = COVER_DIR / f"nexus_{mod_id}.jpg"
    if p.is_file():
        return f"thumbs/{p.name}"
    for ext in (".png", ".webp", ".jpeg", ".gif"):
        p = COVER_DIR / f"nexus_{mod_id}{ext}"
        if p.is_file():
            return f"thumbs/{p.name}"
    return ""


def download_cover(mod_id, url: str = "", timeout: float = 15.0) -> str:
    """取缩略图存到 data/thumbs/。返回相对路径，失败返回空。"""
    url = (url or "").strip()
    if not url:
        rec = cached(mod_id) or {}
        url = str(rec.get("thumb") or rec.get("picture") or "")
    if not url.startswith("http"):
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            blob = r.read(4_000_000)
            ctype = (r.headers.get("Content-Type") or "").lower()
    except (urllib.error.URLError, TimeoutError, OSError):
        return ""
    if not blob or len(blob) < 200:
        return ""
    ext = ".jpg"
    if "png" in ctype:
        ext = ".png"
    elif "webp" in ctype:
        ext = ".webp"
    elif blob[:4] == b"\x89PNG":
        ext = ".png"
    try:
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        dst = COVER_DIR / f"nexus_{mod_id}{ext}"
        tmp = dst.with_suffix(dst.suffix + ".tmp")
        tmp.write_bytes(blob)
        tmp.replace(dst)
        # 同名的旧扩展名清掉，免得一个 mod 两张图
        for other in (".jpg", ".png", ".webp", ".jpeg"):
            p = COVER_DIR / f"nexus_{mod_id}{other}"
            if p != dst and p.is_file():
                try:
                    p.unlink()
                except OSError:
                    pass
    except OSError:
        return ""
    rec = cached(mod_id) or {}
    rec["mod_id"] = rec.get("mod_id") or _int(mod_id)
    rec["cover"] = f"thumbs/{dst.name}"
    rec.setdefault("fetched_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    save({str(mod_id): rec})
    return f"thumbs/{dst.name}"


# ---------------- 给上层用的门面 ----------------
def refresh(ids, with_cover: bool = True, force: bool = False,
            timeout: float = 12.0, days: int = TTL_DAYS) -> dict:
    """取一批 mod 的资料，顺便下封面。过期的不重复取（force=True 除外）。

    `days` 是「多少天内算新」。默认 7 天（手动刷新时够用），
    启动自动同步传 1 —— 那样每天第一次开程序会真的查一遍有没有新版本，
    同一天里再开几次就直接命中缓存，不会把 Nexus 当靶子打。
    """
    todo = []
    fill = []                     # 资料还新、但缺「主文件版本」的老缓存
    for x in ids or []:
        s = str(x or "").strip()
        if not s.isdigit():
            continue
        if force or not is_fresh(s, days):
            todo.append(s)
        elif not (cached(s) or {}).get("main_version"):
            fill.append(s)
    got = fetch(todo, timeout=timeout) if todo else {}
    for key in fill:
        rec = cached(key) or {}
        f = mod_files(key, timeout)
        if f and rec:
            rec.update(f)
            save({key: rec})
            got.setdefault(key, rec)
    covers = 0
    if with_cover:
        for key, rec in got.items():
            if cover_rel(key):
                continue
            if download_cover(key, rec.get("thumb") or rec.get("picture")):
                covers += 1
    return {"ok": bool(got) or not todo, "requested": len(todo),
            "got": len(got), "covers": covers,
            "error": "" if got or not todo else (_state.get("last_error") or "没取到资料")}


def status() -> dict:
    with _lock:
        return {
            "endpoint": GQL,
            "game_id": GAME_ID,
            "cached": len(cached_all()),
            "last_ok": (datetime.fromtimestamp(_state["ok_at"]).isoformat(timespec="seconds")
                        if _state["ok_at"] else ""),
            "last_error": _state["last_error"],
            "ttl_days": TTL_DAYS,
            "cooling": time.time() < _state["fail_until"],
        }
