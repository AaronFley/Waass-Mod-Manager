"""Nexus 台账：这个 mod 在 Nexus 上是哪一个、本地装的是哪个版本。

为什么不走 API
--------------
Nexus 的 API 要 Personal Key，免费账号还拿不到直链；而且每查一次就是一次外网
请求，断网、Key 过期、限流都会让界面显示一堆看不清真假的状态。所以这里改成
纯本地：把「ID / 页面地址 / 本地版本 / 上次记到的版本」都存下来，想看最新的
就点按钮跳到网页自己看。

ID 从哪来（优先级从高到低）
1. data/track.json —— 你手填或手改的，永远以你填的为准
2. data/legacy.json —— 从旧管理器一次性搬过来的存档（已固化，不依赖它本体）
3. mod 目录里的说明文件 —— 里面写着 nexusmods 链接
4. 下载目录 —— 文件名自带 ID，能反查出这个 ID 属于哪个 mod

本地版本从哪来
--------------
五份来源，每份都只是「某个版本曾经在这里」的证据，**取版本号最高的那个**：

1. 你手填的（永远优先，不再比较）
2. mod 自带的 `content/info.json` —— REDkit 打包的 mod 都带。注意有的作者从不更新
   这个字段（实测有个 mod 写着 0.1 而实际装的是 1.1.3），所以只当证据之一
3. data/installed.json —— 用本程序安装时从文件名解析出的版本。它是快照：
   之后同一个 ID 又下过新包，这条就过期，不参与比较
4. data/legacy.json 里记着的旧版本
5. 下载目录里同一个 ID 的最新压缩包 —— 推定：你多半就是拿那个文件装的
6. mod 自带的 version.txt / modinfo

为什么取最高而不是「按可信度取第一个」：更新之后，写文件的来源就变了 ——
REDkit 包换成新 info.json、重新下载的包换了文件名、安装档案却还停在旧值。
按可信度取第一个会让更新过的 mod 一直挂着旧版本号（2026-10-29 用户报的就是这个）。

「线上最新」只有一份来源：旧管理器上次检查时记下的版本，会带上那个时间点。
它是旧数据，界面上必须写清楚，不能假装是实时结果。
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path

from . import i18n
from .paths import DATA_DIR

TRACK = DATA_DIR / "track.json"

SITE = "https://www.nexusmods.com/witcher3"
PAGE = SITE + "/mods/{id}"

# 下载目录与存档都有点 I/O，缓存一会儿，别在每次刷新时重复扫
_CACHE_TTL = 30
_conf: dict = {"at": 0.0, "legacy": {}, "arch": {}, "nx": {}}

_VERSION_SRC_CN = {
    "manual": "trk.src_manual",
    "info": "trk.src_info",
    "manifest": "trk.src_manifest",
    "legacy": "trk.src_legacy",
    "rmmr": "trk.src_legacy",
    "archive": "trk.src_archive",
    "folder": "trk.src_folder",
    "": "",
}

# 安装档案是「装那一刻」的快照。若同一个 Nexus ID 之后又下过包，
# 快照就过期了 —— 判它过期的宽限（秒）。下载在前、安装在后，正常不会踩到。
_PKG_GRACE = 60

_ID_SRC_CN = {
    "manual": "trk.id_manual",
    "legacy": "trk.id_legacy",
    "readme": "trk.id_readme",
    "package": "trk.id_package",
    "search": "trk.id_search",
    "": "",
}


# ---------------- 手填的那一份 ----------------
def _load_all() -> dict:
    """整个 track.json。里面有 manual（手填的 ID/版本）和 ignored（不在 Nexus 上的）两栏。"""
    try:
        data = json.loads(TRACK.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = TRACK.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(TRACK)


def load_manual() -> dict:
    manual = _load_all().get("manual")
    return manual if isinstance(manual, dict) else {}


def save_manual(manual: dict) -> None:
    # 不能直接覆盖整份文件 —— 里面还有 ignored，一覆盖就被冲掉了
    data = _load_all()
    data["manual"] = manual
    _save_all(data)


def load_ignored() -> set[str]:
    """标成「这个不在 Nexus 上」的模组（自制、移植、合订本）。

    没有这个标记的话，「还有 N 个没对上 Nexus」会永远挂着那几个自制 mod，
    提示条从此再也消不掉 —— 时间一长用户就学会无视所有提示了。
    """
    raw = _load_all().get("ignored")
    return {str(x) for x in raw if x} if isinstance(raw, list) else set()


def set_ignored(key: str, on: bool = True) -> dict:
    key = (key or "").strip()
    if not key:
        raise ValueError(i18n.t("trk.no_key"))
    cur = load_ignored()
    if on:
        cur.add(key)
    else:
        cur.discard(key)
    data = _load_all()
    data["ignored"] = sorted(cur)
    _save_all(data)
    drop_cache()
    return {"key": key, "ignored": on, "count": len(cur)}


def parse_ref(text: str) -> str:
    """从「13087」「witcher3/mods/13087」「完整网址」里抽出 ID。"""
    s = (text or "").strip()
    if not s:
        raise ValueError(i18n.t("trk.ref_empty"))
    if s.isdigit():
        if 2 <= len(s) <= 8:
            return s
        raise ValueError(i18n.t("trk.ref_bad_number"))
    m = re.search(r"/mods?/(\d{2,8})", s)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{3,8})\b", s)
    if m:
        return m.group(1)
    raise ValueError(i18n.t("trk.ref_not_found"))


def search_url(name: str) -> str:
    from urllib.parse import quote
    return f"{SITE}/search/?gsearch={quote(name or '')}&gsearchtype=mods"


def set_manual(key: str, ref: str = "", version: str = "") -> dict:
    """记下某个 mod 的 Nexus ID / 本地版本。ref 为空表示只改版本。"""
    key = (key or "").strip()
    if not key:
        raise ValueError(i18n.t("trk.no_key"))
    manual = load_manual()
    cur = dict(manual.get(key) or {})
    if ref.strip():
        cur["nexus_id"] = parse_ref(ref)
    if version.strip():
        cur["version"] = version.strip()
    elif "version" in cur and not version.strip():
        cur.pop("version", None)
    cur["at"] = datetime.now().isoformat(timespec="seconds")
    manual[key] = cur
    save_manual(manual)
    drop_cache()
    return {"key": key, **cur}


def clear_manual(key: str) -> dict:
    manual = load_manual()
    manual.pop(key, None)
    save_manual(manual)
    drop_cache()
    return {"ok": True, "key": key}


# ---------------- 版本比较 ----------------
# 规则只有一份，在 core/version.py。这里保留同名入口，别处还按 track.compare 调。
from .version import compare, vkey  # noqa: E402,F401


# ---------------- 周边资料 ----------------
def _legacy_meta() -> dict:
    """自己存档里按显示名索引的那一份：{小写显示名: 旧资料}。"""
    from . import legacy
    return {str(k).strip().lower(): v
            for k, v in (legacy.load().get("by_name") or {}).items()
            if isinstance(v, dict)}


def _archive_versions() -> dict:
    """{nexus_id(str): {"version","file","mtime"}}，同一个 ID 取最新的那个包。"""
    from . import downloads
    out: dict[str, dict] = {}
    try:
        items = downloads.scan(limit=downloads.HARD_LIMIT)
    except Exception:                                # noqa: BLE001 扫不动就算了
        return out
    for it in items:
        nid = it.get("nexus_id")
        ver = (it.get("version") or "").strip()
        if not nid or not ver:
            continue
        k = str(nid)
        cur = out.get(k)
        if cur is None or it["mtime"] > cur["mtime"]:
            out[k] = {"version": ver, "file": it["file"], "mtime": it["mtime"]}
    return out


def _cached() -> dict:
    now = time.time()
    if now - _conf["at"] < _CACHE_TTL and _conf["at"]:
        return _conf
    from . import nexus_meta
    _conf["legacy"] = _legacy_meta()
    _conf["arch"] = _archive_versions()
    _conf["nx"] = nexus_meta.cached_all()
    _conf["at"] = now
    return _conf


def drop_cache() -> None:
    _conf["at"] = 0.0


def _registry_index() -> dict:
    """{mod 文件夹名(小写): (版本, 安装时间戳)}，来自本程序的安装档案。"""
    from . import registry
    out: dict[str, tuple[str, float]] = {}
    try:
        pkgs = registry.load()["packages"]
    except Exception:                                # noqa: BLE001
        return out
    for p in pkgs:
        ver = (p.get("version") or "").strip()
        if not ver:
            continue
        try:
            ts = float(p.get("ts") or 0)
        except (TypeError, ValueError):
            ts = 0.0
        if not ts:
            # 老记录没有 ts，只有 installed_at
            try:
                ts = datetime.fromisoformat(str(p.get("installed_at"))).timestamp()
            except (TypeError, ValueError):
                ts = 0.0
        for folder in (p.get("mods") or []) + [Path(x).name for x in (p.get("paths") or [])]:
            f = str(folder).lower().lstrip("~")
            if f:
                out.setdefault(f, (ver, ts))
    return out


# ---------------- 主入口 ----------------
def apply(mods: list) -> int:
    """把 ID / 页面地址 / 版本 / 是否落后，全部贴到记录上。返回有变化的条数。"""
    from . import nexus_meta
    cached = _cached()
    store, arch, nx = cached["legacy"], cached["arch"], cached["nx"]
    reg = _registry_index()
    manual = load_manual()
    touched = 0

    for m in mods:
        mine = manual.get(m.key) or {}

        # 每次重算，避免换了 ID 之后还挂着上一个 mod 的旧值
        m.latest_version = ""
        m.latest_at = ""
        m.latest_src = ""
        m.latest_file = ""
        m.page_version = ""
        m.name_nexus = ""
        m.summary = ""
        m.downloads = 0

        # 1. Nexus ID
        # 记住「扫描那一刻」的 ID：这样同一条记录反复 apply（比如刚清掉手填的 ID）
        # 不会把上一轮贴上去的 ID 当成扫描结果留下来。
        if not hasattr(m, "_scan_id"):
            m._scan_id = m.nexus_id or ""
        nid = str(mine.get("nexus_id") or m._scan_id or "").strip()
        if mine.get("nexus_id"):
            m.id_src = "manual"
        m.nexus_id = nid or None
        m.nexus_url = PAGE.format(id=nid) if nid else ""
        if not nid:
            m.id_src = ""

        # 2. 从 Nexus 抓回来的资料 —— 这是最准的一份，优先用
        nxc = (nx.get(nid) or {}) if nid else {}
        if nxc:
            if nxc.get("author"):
                m.author = str(nxc["author"])
            if nxc.get("category"):
                m.category = str(nxc["category"])
            m.name_nexus = str(nxc.get("name") or "")
            m.summary = str(nxc.get("summary") or "")
            m.downloads = int(nxc.get("downloads") or 0)
            # 「最新版」认主文件版本，不认 mod 页面上那个作者手填的展示值：
            # 后者经常落后于他自己传的主文件（实测 3/17 差 1～2 个热修版）。
            m.page_version = str(nxc.get("version") or "").strip()
            main = str(nxc.get("main_version") or "").strip()
            if main:
                m.latest_version = main
                m.latest_at = str(nxc.get("updated_at") or "")
                m.latest_src = "nexus"
                m.latest_file = str(nxc.get("main_file") or "")
            elif m.page_version:
                m.latest_version = m.page_version
                m.latest_at = str(nxc.get("updated_at") or "")
                m.latest_src = "nexus"
            cover = nexus_meta.cover_rel(nid)
            if cover:
                m.thumbnail = cover

        # 3. 旧管理器上次看到的最新版（只在没抓到新资料时兜底）
        meta = store.get((m.name or "").strip().lower()) or {}
        if not m.latest_src and meta.get("remote_version"):
            m.latest_version = str(meta["remote_version"]).strip()
            m.latest_at = str(meta.get("last_checked") or "")
            m.latest_src = "legacy"

        # 4. 名字：如果现在显示的还只是文件夹名，就换成 Nexus 官方标题
        if m.name_nexus and (m.name or "").replace(" ", "").lower() == \
                (m.folder or "").lstrip("~").replace(" ", "").lower():
            m.name = m.name_nexus

        # 5. 本地版本
        #
        # 2026-10-29 用户实测：更新了 3 个 mod，界面还说「有更新」。修在这一段：
        #   a) 新下载的包名用空格分隔，解析不出来 → 版本停在旧包（registry.py 已修）
        #   b) 安装档案是「装那一刻」的快照，之后又下了新包就过期了
        #   c) 每份来源都只是「某个版本曾经在这里」的证据，谁最新谁最接近磁盘现状
        #
        # 所以不再按「来源优先级取第一个」，改成**取版本号最高的那个**，
        # 只有版本相同（或都比不出来）时才用来源优先级决定标注是谁提供的。
        #
        # 已知取舍：只下载没安装的新包也会被算成「已装」，可能把一次真更新藏起来。
        # 反过来（拿旧快照当已装）会让更新过的 mod 一直挂在「有更新」里 ——
        # 后者是用户实际报过的症状，所以选前者。
        ver, src = "", ""
        if mine.get("version"):
            ver, src = str(mine["version"]), "manual"
        else:
            cand: list[tuple[int, str, str]] = []
            if m.version_file == "info" and m.version:
                cand.append((0, m.version, "info"))
            reg_hit = reg.get(m.key.lower())
            if reg_hit:
                rv, rt = reg_hit
                newer_pkg = bool(nid and nid in arch
                                 and arch[nid]["mtime"] > rt + _PKG_GRACE)
                if not newer_pkg:
                    cand.append((1, rv, "manifest"))
            if str(meta.get("installed_version") or "").strip():
                cand.append((2, str(meta["installed_version"]).strip(), "legacy"))
            if nid and nid in arch:
                cand.append((3, arch[nid]["version"], "archive"))
            if m.version:
                cand.append((4, m.version, "folder"))

            best: tuple[int, str, str, tuple] | None = None
            for pr, v, s in cand:
                k = vkey(v)
                if best is None:
                    best = (pr, v, s, k)
                    continue
                bk = best[3]
                if k and bk and k > bk:
                    best = (pr, v, s, k)
                elif k and bk and k == bk and pr < best[0]:
                    best = (pr, v, s, k)
                elif not k and not bk and pr < best[0]:
                    best = (pr, v, s, k)
            if best:
                ver, src = best[1], best[2]
        m.version, m.version_src = ver, src

        # 4. 落后了吗
        if not ver:
            m.update_available = False
            m.track_state = "unknown"                # 本地版本不知道，不敢下结论
        elif not m.latest_version:
            m.update_available = False
            m.track_state = "unchecked"              # 本地有，线上没记录
        else:
            c = compare(m.latest_version, ver)
            if c == 1:
                m.update_available, m.track_state = True, "new"
            elif c == 0:
                m.update_available, m.track_state = False, "current"
            elif c == -1:
                # 本地比线上还高。绝大多数情况不是「你超前」，而是两边版本号的
                # 来源不同（下载包文件名 / 旧管理器记录 / Nexus 页面手填值），
                # 单独一档说清楚，别混进「已是最新」里让人看不懂。
                m.update_available, m.track_state = False, "ahead"
            else:
                m.update_available, m.track_state = False, "unchecked"
        touched += 1
    return touched


def version_src_cn(src: str) -> str:
    key = _VERSION_SRC_CN.get(src or "", "")
    return i18n.t(key) if key else ""


def rows(mods: list) -> dict:
    """给界面用的清单。"""
    out = []
    ignored = load_ignored()
    for m in mods:
        id_key = _ID_SRC_CN.get(m.id_src or "", "")
        # latest_code 是给前端比较用的稳定值（track.js 原来拿中文「刚查到」做匹配）
        if m.latest_src == "nexus":
            latest_cn, latest_code = i18n.t("trk.latest_fresh"), "fresh"
        elif m.latest_src == "legacy":
            latest_cn, latest_code = i18n.t("trk.latest_legacy"), "recorded"
        else:
            latest_cn, latest_code = "", ""
        out.append({
            "key": m.key,
            "name": m.name,
            "name_cn": m.name_cn,
            "name_nexus": m.name_nexus or "",
            "author": m.author,
            "folder": m.folder,
            "enabled": m.enabled,
            "nexus_id": m.nexus_id or "",
            "url": m.nexus_url or "",
            "id_src": m.id_src or "",
            "id_cn": i18n.t(id_key) if id_key else "",
            "version": m.version or "",
            "version_cn": version_src_cn(m.version_src),
            "latest": m.latest_version or "",
            "latest_at": (m.latest_at or "")[:10],
            "latest_cn": latest_cn,
            "latest_code": latest_code,
            "latest_file": m.latest_file or "",
            "latest_page": m.page_version or "",
            "state": m.track_state or "unknown",
            "summary": m.summary or "",
            "downloads": m.downloads or 0,
            "cover": m.thumbnail or "",
            "search": search_url(m.name),
            # 「这个不在 Nexus 上」：自制/移植/合订本，标了之后不再提醒。
            "ignored": m.key in ignored,
        })
    order = {"new": 0, "ahead": 1, "unknown": 2, "unchecked": 3, "current": 4}
    out.sort(key=lambda r: (order.get(r["state"], 5), r["name"].lower()))
    stats = {
        "total": len(out),
        "linked": sum(1 for r in out if r["nexus_id"]),
        "missing": sum(1 for r in out if not r["nexus_id"]),
        # 真正「还该处理」的缺失数：手动标成不在 Nexus 上的不算
        "missing_active": sum(1 for r in out if not r["nexus_id"] and not r["ignored"]),
        "new": sum(1 for r in out if r["state"] == "new"),
        "ahead": sum(1 for r in out if r["state"] == "ahead"),
        "current": sum(1 for r in out if r["state"] == "current"),
        "unknown": sum(1 for r in out if r["state"] == "unknown"),
        "unchecked": sum(1 for r in out if r["state"] == "unchecked"),
        "no_version": sum(1 for r in out if not r["version"]),
        "with_meta": sum(1 for r in out if r["latest_code"] == "fresh"),
        "with_cover": sum(1 for r in out if r["cover"]),
        "record_at": max((r["latest_at"] for r in out if r["latest_at"]), default=""),
    }
    return {"rows": out, "stats": stats}
