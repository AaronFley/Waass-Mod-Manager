"""认出模组的 Nexus ID —— 只用本地证据，宁可空着也不猜。

为什么要单独一个模块：ID 是整条 Nexus 链的起点（作者、封面、最新版本、更新提醒
全靠它）。2026-10-30 的事故是：ID 只从 RMMR 存档和 readme 链接来，用户一清理
RMMR、又用了全新的 data/，21 个模组一个 ID 都没有，整个「检查更新」对新用户是死的。

现在的来源按可信度排：

1. `stored`   本模块自己记过的（`data/nexus_ids.json`）—— 认一次永久有效，
              下载目录里的原包删了也不影响
2. `registry` 本程序的安装档案（装的时候从包名解析出的 ID，铁证）
3. `package`  下载目录里的原包文件名（`Hoods 4242 3.7 …zip`，铁证）
4. readme     由 scanner 负责，不在这里
5. 联网搜      不在这里 —— 只准备候选让人点，绝不自动绑

**为什么 3 也要打"铁证"标签**：包名里的 ID 是 Nexus 自己生成的，不是猜的。
唯一需要判断的是「这个包属于哪个模组」，所以有两道闸：名字得对得上，且不能
出现并列第一。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .paths import DATA_DIR

IDS_FILE = DATA_DIR / "nexus_ids.json"

# 包清单缓存：downloads.scan() 要 30ms 上下，扫描一次会被调很多次，别重复扫
_PKG_TTL = 60.0
_pkg_cache: dict = {"at": 0.0, "items": []}

# 名字里带这些词，说明是翻译版或二改版。除非模组自己的名字里也有，
# 否则不能拿来当它的身份 —— 「Seamless Adaptive HUD Chinese」不是
# 「Seamless Adaptive HUD」。
# 按**词**比对而不是子串：`Convention` 里含 `cn` 两个字母，子串匹配会误伤。
_LANG_WORDS = {
    "chinese", "russian", "polish", "french", "german", "spanish", "italian",
    "japanese", "korean", "portuguese", "turkish", "czech", "hungarian",
    "ukrainian", "dutch", "traduction", "traduccion", "italiana",
    "simplified", "traditional", "hans", "hant",
    "cn", "ru", "pl", "fr",
}
# 注意两件事：
# 1. 绝不能收录 "remaster" —— 重制版的本体 mod 大多带这个词
#    （UI Remastered Edition / SeamlessAdaptiveHUDRemastered），收了就把本体
#    当翻译版扣分，全认不出。
# 2. 两个字母的词只留 cn/ru/pl/fr。`it`、`de`、`es` 在英文标题里是正常词
#    （"Everything is on it"），收进来会误伤。

# 包装词：作者给自家 mod 起的漂亮名字里常见，但对「是哪一个 mod」没有区分度。
# 只用来算「多出来的词」和「差几个词」，**不参与覆盖率**——否则
# `AutoLoot Enhanced` 对 `SmartAutoLoot Remaster` 会因为多了 enhanced / smart /
# remaster 三个词拿到负数，连候选都进不去（2026-10-30 实测就是这么空着的）。
# 刻意不收 `remaster`：它标记的是「哪个版本线」，恰恰是要分辨的东西。
_FILLER = {
    "enhanced", "remake", "redux", "rework", "reworked", "revamp",
    "fix", "fixed", "patch", "patched", "update", "updated", "upd",
    "improved", "improvement", "optimized", "rebalanced", "tweak", "tweaked",
    "ultimate", "definitive", "complete", "extended", "expanded", "advanced",
    "lite", "plus", "pro", "premium", "new", "next", "gen", "ng", "ngplus",
    "version", "ver", "beta", "alpha", "final", "standalone", "separate",
    "addon", "pack", "bundle", "edition", "collection",
    "remaster", "remastered", "full", "minimal",
    "the", "and", "for", "with", "of", "to", "your", "my", "you", "want",
    "v",
}

# 判「是不是同一个 mod」时，`remaster` 不能当包装词丢掉：重制版有时真的
# 另开了一个页面。只有在算「多出来的词扣多少分」时才把它抹平。
_FILLER_BIND = _FILLER - {"remaster", "remastered"}

# 低于这个分不必看了：名字只是碰巧有几个字母相同
_MIN_SCORE = 40
# 自动绑定要过这条线。比候选门槛高得多：列出几个来让人挑是好事，
# 猜错一个就绑定才是灾难（「检查更新」会一直报别人的版本）。
_BIND_SCORE = 62


def _stem(word: str) -> str:
    """去复数尾巴。`Cloaks` 和 `Cloak` 是同一个词，不该算两个。"""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


# 包名里常见 `V1.1.7` / `r2` / `x64` 这类版本前缀。它们是数字的一部分，
# 但 `V1` 不是纯数字，`isdigit()` 拦不住，会当成一个「多出来的词」扣分
# （`SmartAutoLoot Remaster V1.1.7` → 多算一个词，AutoLoot 直接被扣到候选线以下）。
_VER_PREFIX = re.compile(r"^[vrx]\d{1,4}$")


def tokenize(name: str) -> list[str]:
    """`mod_SwordsOnHip` / `Swords On Hip` / `Swords-On-Hip` → [sword, on, hip]

    必须先拆驼峰：文件夹名是 `modSwordsOnHip`（连写），Nexus 上的标题是
    `Swords On Hip`（分词）。按字符序列比会一个字都对不上 —— 词序一变更糟，
    `mod_hoodsSwitchableCloak` 和 `Switchable Cloaks - Hoods` 就是这种情况。
    """
    s = re.sub(r"^~", "", name or "")
    s = re.sub(r"^mod[_\-\s]*", "", s, flags=re.I)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", s)      # 驼峰
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)   # NGPlus 这种
    # 纯数字是包名里的版本号（`ResponsiveMovement 1.7.5`），不是名字的一部分。
    # 留着会让「多了几个词」的惩罚凭空变重。
    return [_stem(t) for t in re.split(r"[^A-Za-z0-9]+", s.lower())
            if t and not t.isdigit() and not _VER_PREFIX.match(t)]


def normalize(name: str) -> str:
    """文件夹名/包名 → 只留字母数字，去掉 mod 前缀。

    `mod_SwordsOnHip` / `Swords On Hip` / `SwordsOnHip-1.0` 都归一成
    `swordsonhip`，这样不同来源的名字才能比。
    """
    s = (name or "").lower()
    s = re.sub(r"^~", "", s)
    s = re.sub(r"^mod[_\-\s]*", "", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def _word_score(name: str, pkg_name: str) -> int:
    """按词袋比。词序无关，正好治文件夹名和标题词序相反的情况。

    两条规矩都很要紧：
    - **覆盖率**：模组名的词至少六成被覆盖才算数。否则「UIRE Always Quest
      Marks」会因为共用一个 `quest` 就认成「All Quest Markers On Map」。
    - **每多一个词扣 20 分**：`Swords On Hip` 和 `Dynamic Swords (and
      Crossbow) On Hip - REMASTERED` 词全中，但那是另一个 mod；真正的本体
      `Swords On Hip When Cloaked` 只多两个词。少扣分它才排得上去。
    """
    a, b = tokenize(name), tokenize(pkg_name)
    if not a or not b:
        return 0
    sa, sb = set(a), set(b)
    if sa == sb:
        return 100
    inter = sa & sb
    if not inter:
        return 0
    cover = len(inter) / len(sa)
    if cover < 0.6:
        return 0
    # 多出来的词里，包装词不算数：`SmartAutoLoot Remaster V1.1.7` 相对
    # `AutoLoot` 真正多出来的只有 smart，不该按两个词扣分。
    extra = len({w for w in sb - sa if w not in _FILLER})
    return max(0, int(100 * cover) - 20 * extra)


def _word_diff(name: str, pkg_name: str) -> int | None:
    """两个名字差几个词。没有共同的词就返回 None（根本不是一回事）。

    这是「是不是同一个 mod」的判据，比分数可靠：
    - `Hoods` ↔ `Hoods`           差 0 → 强证据
    - `Hoods` ↔ `HoodSlots`       差 1
    - `RoninArmor` ↔ `dlcRoninArmor` 差 1（多了个 dlc 前缀，也算）
    - `Swords On Hip` ↔ `Swords On Hip When Cloaked` 差 2 → 弱，得让人确认
    """
    if normalize(name) == normalize(pkg_name):
        return 0
    a, b = tokenize(name), tokenize(pkg_name)
    if not a or not b:
        return None
    sa, sb = set(a), set(b)
    if not (sa & sb):
        # 全小写的连写名拆不出词（`roninarmor` 没有驼峰，也没有分隔符），
        # 词袋这条路走不通。退回字符级：整串被包住也算差 1。
        mn, pn = normalize(name), normalize(pkg_name)
        if mn and pn and (mn in pn or pn in mn):
            return 1
        return None
    return len({w for w in (sa ^ sb) if w not in _FILLER_BIND})


def _score(name: str, version: str, pkg: dict) -> int:
    """这个包是「这个模组」的可能性，0 表示不像。

    两条路各算一次，取高的：
    - 字符级：连写变体、只差分隔符的名字（完全相等给满分）
    - 词级：词序不同、多了几个词的标题

    版本号吻合是**加分项不是必要条件** —— 不同模组恰好同版本的情况太多，
    单凭版本相同就绑定会认错。
    """
    mn = normalize(name)
    pn = normalize(pkg.get("name") or "")
    if not mn or not pn:
        return 0

    if mn == pn:
        char_score = 100
    elif mn in pn:
        char_score = 70 - min(len(pn) - len(mn), 25)
    elif pn in mn:
        char_score = 50 - min(len(mn) - len(pn), 25)
    else:
        char_score = 0

    score = max(char_score, _word_score(name, pkg.get("name") or ""))
    if not score:
        return 0

    pkg_ver = (pkg.get("version") or "").strip()
    if version and pkg_ver and version == pkg_ver:
        score += 30
    if set(tokenize(pkg.get("name") or "")) & _LANG_WORDS and not set(tokenize(name)) & _LANG_WORDS:
        score -= 45                        # 翻译版/二改版，基本不可能是它本体
    return max(score, 0)


def search_queries(name: str) -> list[str]:
    """给 Nexus 搜的几组关键词，从严到宽。

    只给一组全词是不够的：Nexus 的 `nameStemmed MATCHES` 要求**几个词基本都
    命中**，词越多越容易一条都搜不到。实测（2026-10-30）：

        autoloot            12 条
        auto loot            6 条
        auto loot enhanced   0 条     ← 原先只发这一组，所以永远空着

    `AutoLoot Enhanced` 这种「功能词 + 形容词」的写法很常见，形容词 Nexus 上
    根本不在标题里。所以按「全词 → 去包装词 → 前两个词 → 第一个词」逐档放宽，
    谁先搜到东西就用谁。
    """
    words = tokenize(name)
    while len(words) > 1 and words[-1] in _LANG_WORDS:
        words.pop()
    if not words:
        return []
    core = [w for w in words if w not in _FILLER] or list(words)
    out = [" ".join(words)]
    if core != words:
        out.append(" ".join(core))
    if len(core) > 2:
        out.append(" ".join(core[:2]))
    # 最后一档只留一个词，专治 `UIRE Travel` 这种「缩写 + 通用词」：
    # 两词一起搜不到，单搜缩写反而能翻出本源。
    if len(core) > 1 and len(core[0]) >= 3 and core[0] not in _FILLER:
        out.append(core[0])
    seen: set[str] = set()
    final: list[str] = []
    for q in out:
        if q and q not in seen:
            seen.add(q)
            final.append(q)
    return final


def search_terms(name: str) -> str:
    """最严的那一组关键词（保留旧接口：调用点只想发一次请求时用它）。"""
    qs = search_queries(name)
    return qs[0] if qs else (name or "").strip()


def packages() -> list[dict]:
    """下载目录里带 Nexus ID 的原包（带 60 秒缓存）。"""
    now = time.time()
    if _pkg_cache["at"] and now - _pkg_cache["at"] < _PKG_TTL:
        return _pkg_cache["items"]
    items: list[dict] = []
    try:
        from . import downloads
        for p in downloads.scan():
            if p.get("nexus_id"):
                items.append({"name": p.get("name") or "",
                              "version": p.get("version") or "",
                              "nexus_id": str(p["nexus_id"])})
    except Exception:                       # noqa: BLE001 下载目录读不了就当没有
        items = []
    _pkg_cache["items"] = items
    _pkg_cache["at"] = now
    return items


def drop_cache() -> None:
    _pkg_cache["at"] = 0.0
    _pkg_cache["items"] = []


# ---------------- 记下来的结果 ----------------

def _load() -> dict:
    try:
        data = json.loads(IDS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and v.get("id")}


def stored(folder: str) -> dict | None:
    return _load().get((folder or "").lower())


def remember(folder: str, nexus_id, src: str) -> None:
    """认出来的 ID 立刻落盘。下载目录里的原包早晚会被清掉，认一次就该记住。"""
    key = (folder or "").lower()
    nid = str(nexus_id or "").strip()
    if not key or not nid.isdigit():
        return
    data = _load()
    if data.get(key, {}).get("id") == nid:
        return
    data[key] = {"id": nid, "src": src, "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = IDS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(IDS_FILE)
    except OSError:
        pass                                # 记不住只是下次要重算，不该让扫描失败


def forget(folder: str) -> bool:
    key = (folder or "").lower()
    data = _load()
    if key not in data:
        return False
    data.pop(key)
    try:
        IDS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True


# ---------------- 主入口 ----------------

def guess(folder: str, name: str, version: str = "") -> tuple[str, str]:
    """返回 (nexus_id, 来源)；认不出返回 ("", "")。

    只在**唯一且够像**的时候给答案。同名并列（比如 AutoLoot 有三个）一律不放行，
    让界面去问人 —— 绑错 ID 会让「检查更新」指向另一个模组，比空着更糟。
    """
    hit = stored(folder)
    if hit:
        return str(hit["id"]), "stored"

    # 显示名可能是中文（自己起的/汉化过的），归一化后什么都不剩 ——
    # 那种情况退回文件夹名，它一定是英文的。
    label = name if normalize(name) else (folder or "")

    # 安装档案：本程序装的时候从包名解析过，最可信
    try:
        from . import registry
        for p in registry.load()["packages"]:
            nid = str(p.get("nexus_id") or "").strip()
            if not nid.isdigit():
                continue
            folders = [str(x) for x in (p.get("mods") or [])]
            folders += [Path(str(x)).name for x in (p.get("paths") or [])]
            if (folder or "").lower() in {f.lower().lstrip("~") for f in folders}:
                remember(folder, nid, "registry")
                return nid, "registry"
    except Exception:                       # noqa: BLE001 档案坏了不该影响扫描
        pass

    # 下载目录里的原包：先算「差几个词」，再算分
    rows: list[tuple[int, int, str]] = []       # (差几个词, 分数, ID)
    for pkg in packages():
        # 翻译版/二改版**从不**自动绑定，哪怕版本号一字不差。
        # 只有 `Hoods - Chinese` 一个包时，绑上去会让「检查更新」一直拿
        # 中文补丁的版本号跟本体比。候选列表里仍然会列出来，人自己判断。
        pkg_words = set(tokenize(pkg.get("name") or ""))
        if pkg_words & _LANG_WORDS and not set(tokenize(label)) & _LANG_WORDS:
            continue
        s = _score(label, version, pkg)
        diff = _word_diff(label, pkg.get("name") or "")
        if diff is None:
            continue
        if s >= _MIN_SCORE or diff <= 1:
            rows.append((diff, s, pkg["nexus_id"]))

    # 只认「差 0～1 个词」的候选，且必须**有且只有一个**。
    # 差 2 个词以上一律不猜：`Swords On Hip` 既可能是 `Swords On Hip When
    # Cloaked`，也可能是 `Dynamic Swords (and Crossbow) On Hip`，两个都不是
    # 对方 —— 这种时候猜错就是灾难，交给人在界面上点。
    if rows:
        near_diff = min(r[0] for r in rows)
        if near_diff <= 1:
            near = {r[2] for r in rows if r[0] == near_diff}
            if len(near) == 1:
                nid = next(iter(near))
                top = max(r[1] for r in rows if r[2] == nid)
                if top >= _BIND_SCORE:
                    remember(folder, nid, "package")
                    return nid, "package"
    return "", ""


def candidates(name: str, version: str = "", limit: int = 5) -> list[dict]:
    """给界面用：这个模组可能是哪几个（含分数），用于「还有 N 个没认出」列表。

    只读下载目录，不联网。联网候选在 nexus_meta.search 那一侧。
    """
    rows: dict[str, dict] = {}
    label = name if normalize(name) else ""
    if not label:
        return []
    for pkg in packages():
        s = _score(label, version, pkg)
        if s < _MIN_SCORE:
            continue
        nid = pkg["nexus_id"]
        if s > rows.get(nid, {}).get("score", 0):
            rows[nid] = {"nexus_id": nid, "score": s,
                         "pkg_name": pkg.get("name") or "",
                         "pkg_version": pkg.get("version") or ""}
    return sorted(rows.values(), key=lambda r: -r["score"])[:limit]


def merge_candidates(local: list[dict], remote: list[dict], limit: int = 5) -> list[dict]:
    """把「下载目录里的包」和「Nexus 上搜到的」合成一张候选表。

    两条路各有盲区，合起来才够用：
    - 本地包名是**你这台机器上的铁证**（`SmartAutoLoot_Remaster_13035.zip`
      就在你硬盘里），但包被删了就没了
    - 联网搜索什么都能搜到，但同名模组一大堆（AutoLoot 就有好几个作者的不同版本），
      排在前面的未必是你装的
    同一个 ID 两边都命中时合成一条，标 `both`。
    """
    rows: dict[str, dict] = {}
    for it in local or []:
        nid = str(it.get("nexus_id") or "")
        if not nid:
            continue
        rows[nid] = {"mod_id": nid, "score": it.get("score") or 0,
                     "name": it.get("pkg_name") or "", "author": "",
                     "version": it.get("pkg_version") or "",
                     "downloads": 0, "url": "", "src": "local"}
    for it in remote or []:
        nid = str(it.get("mod_id") or "")
        if not nid:
            continue
        cur = rows.get(nid)
        if cur:
            cur["score"] = max(cur["score"], it.get("score") or 0)
            cur["src"] = "both"
            cur["name"] = it.get("name") or cur["name"]
            cur["author"] = it.get("author") or ""
            cur["url"] = it.get("url") or ""
            cur["downloads"] = it.get("downloads") or 0
        else:
            rows[nid] = {"mod_id": nid, "score": it.get("score") or 0,
                         "name": it.get("name") or "", "author": it.get("author") or "",
                         "version": it.get("version") or "",
                         "downloads": it.get("downloads") or 0,
                         "url": it.get("url") or "", "src": "nexus"}
    # 排序先看证据等级再看分数：你自己硬盘里的原包名字对上了，比 Nexus 上
    # 搜到的「下载量最高的同名 mod」更可能是你装的那个。
    # 反例就在眼前：AutoLoot 的正主 `SmartAutoLoot Remaster` 分数只有 46，
    # 不这么排会被 `Auto Loot Herbs`（13228）压到第三位，人一眼就跳过去了。
    _rank = {"both": 2, "local": 1, "nexus": 0}
    out = sorted(rows.values(),
                 key=lambda r: (-_rank.get(r["src"], 0), -r["score"], -r["downloads"]))
    return out[:limit]


def stats() -> dict:
    return {"stored": len(_load()), "packages": len(packages())}


# ---------------- 联网候选（只备菜，不替人下决定） ----------------
# 本地证据认不出的模组（原包被删了、readme 里没链接），只能靠名字去 Nexus 搜。
# 搜到的结果**绝不自动绑定**：Nexus 上同名/近名的模组太多（AutoLoot 就有三个
# 不同作者的版本），绑错了「检查更新」会指向别人，比空着更糟。
# 这里只负责把候选存下来，等人在界面上点一下确认。

GUESS_FILE = DATA_DIR / "nexus_guesses.json"
GUESS_TTL = 3 * 86400          # 搜过一次三天内不重复搜，别拿 Nexus 当靶子


def _load_guesses() -> dict:
    try:
        data = json.loads(GUESS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def cached_guess(folder: str) -> dict | None:
    """之前搜过的候选。过期了返回 None（调用方决定要不要重搜）。"""
    rec = _load_guesses().get((folder or "").lower())
    if not isinstance(rec, dict):
        return None
    try:
        at = float(rec.get("at") or 0)
    except (TypeError, ValueError):
        at = 0.0
    if not at or time.time() - at > GUESS_TTL:
        return None
    return rec


def save_guess(folder: str, items: list[dict]) -> None:
    key = (folder or "").lower()
    if not key:
        return
    data = _load_guesses()
    data[key] = {"at": time.time(), "items": items[:5]}
    try:
        GUESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = GUESS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(GUESS_FILE)
    except OSError:
        pass


def shortlist(hits: list[dict], name: str, version: str = "", limit: int = 3) -> list[dict]:
    """从 Nexus 搜索结果里挑出「可能是它」的那几个，按像的程度排。

    用和本地包名完全同一套打分 —— 两处口径不一致的话，本地认不出、联网又认成
    另一个，人就彻底糊涂了。
    """
    rows: dict[str, dict] = {}
    for h in hits or []:
        nid = str(h.get("mod_id") or "").strip()
        if not nid.isdigit():
            continue
        s = _score(name, version, {"name": h.get("name") or "",
                                   "version": h.get("version") or ""})
        if s < _MIN_SCORE:
            continue
        if s > rows.get(nid, {}).get("score", 0):
            rows[nid] = {"mod_id": nid, "score": s,
                         "name": h.get("name") or "",
                         "author": h.get("author") or "",
                         "version": h.get("version") or "",
                         "downloads": h.get("downloads") or 0,
                         "url": h.get("url") or ""}
    return sorted(rows.values(), key=lambda r: (-r["score"], -r["downloads"]))[:limit]

