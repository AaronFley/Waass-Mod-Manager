"""把用户粘进来的一段游戏报错，就地判断「大概哪一类、指向哪个文件、下一步动哪里」。

为什么输入是「一段文本」而不是「一个日志文件」
----------------------------------------------
巫师 3 不写独立的脚本错误日志。报错只出现在启动时那个 "Script Compilation
Errors" 窗口里，窗口自带一个 Copy to Clipboard。用户手上能拿到的就是一段
文本 —— 可能很脏、可能被截断、可能混着中文说明。所以这里的一切容错都是必须
的：宁可少认一行，也不要因为一行怪格式把整段放弃。

本阶段只做四条规则（其余一律说「我不认识」，绝不硬套一个原因）
--------------------------------------------------------------
  syntax_broken        花括号被改坏 —— 唯一能指到「就这一个文件」的一类
  merged_unregistered  合订本没登记 / 被停用 / 顺序不对
  merged_stale_ref     合订本里还引用着已经不在的类型
  ghost_folder         报错点名的模组文件夹在 mods/ 下不存在
判据全部来自 core.conflicts.index() 导出的**数据**（谁提供哪个脚本、合订本登记
状态、mods/ 下有哪些文件夹），不看 analyze() 渲染好的中文句子。

三条硬约束（改这个文件之前先读一遍）
------------------------------------
1. **不上传**：不 import socket / urllib / requests，一个字节都不出这台机器。
2. **不写盘**：不 import install / guard / snapshot，除了读文件不做任何写入。
   「规则表坏了要写日志」这条也按「不写盘」办：原因放进返回值的 notes 与
   stderr，界面上能看见，比落一个用户永远找不到的日志文件有用。
3. **判断只看数据**：evidence 里的句子是给人看的，切一次语言就换一套；任何
   规则都不许拿这些句子做匹配，否则换语言就全断（这正是不能读 analyze()
   的 title/detail 的原因）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

from . import conflicts, i18n, merger
from .paths import RES_DIR

RULES_NAME = "diagnose_rules.json"


def _warn(msg: str) -> None:
    """往 stderr 报一句，但不许因为 stderr 不存在而崩。

    打包成 --windowed 的 exe 之后 sys.stderr 是 None，直接 print 会抛
    AttributeError —— 那会把「规则表坏了就静默降级」变成「一进诊断就崩」，
    也就是本来就轻的故障被放大成致命故障。宁可丢这句提示，也不能崩。
    """
    if sys.stderr is not None:
        print(msg, file=sys.stderr)

# 规则表读不到时的最小骨架：只做结构解析 + 通用三步走，一条关键词都没有。
# 这不是「另一套规则」，是「没有规则」时的空壳 —— 宁可什么都不判，也不许
# 因为一个坏 JSON 把整个程序带崩。
_MINIMAL: dict = {
    "version": 0,
    "limits": {
        "max_text_bytes": 204800,
        "max_results": 200,
        "max_line_chars": 4000,
        "ident_scan_bytes": 50331648,
        "ident_scan_files": 20000,
        "ident_scan_file_bytes": 8388608,
    },
    "patterns": {
        "kind": "^(Error|Warning|Info)\\b",
        "owner": "\\[([^\\[\\]]{1,100})\\]",
        "where": "([^\\s()\\[\\]:]{1,300}\\.ws)\\s*\\(\\s*(\\d{1,7})",
        "unknown_type": "Unknown\\s+(?:type|class)\\s+'([^']{1,120})'",
    },
    "owner_skip": ["^content\\d*$", "^dlc\\d*$"],
    "merged": {"default_bracket": "mod0000_mergedfiles"},
    "rules": [],
    "unknown": {
        "code": "unknown",
        "verdict": "unknown",
        "action": "restore_recent",
        "text": {
            "zh": {
                "not_mine": "这段不在我认识的范围内 —— 我不敢猜是哪个模组的问题。",
                "steps": "可以按三步走：先看第一条 Error 行指的是哪个文件；再把最近装过"
                         "或卸过的那个撤回去；改完点 Retry 让游戏重新编译，不用把游戏"
                         "关掉重开。",
                "no_index": "另外，这次没读到你的模组列表，所以只能就文字本身说话。",
            },
            "en": {
                "not_mine": "This one is outside what I recognise — I won't guess "
                            "which mod is at fault.",
                "steps": "Try three steps: look at the first Error line to see which "
                         "file it names; undo the last mod you installed or removed; "
                         "then hit Retry so the game recompiles.",
                "no_index": "Also, I have no mod list this time, so I'm going on the "
                            "text alone.",
            },
        },
    },
}

ACTIONS = ("open_conflicts", "restore_recent", "retry_game", "none")
VERDICTS = ("certain", "likely", "partial", "unknown")

_INDEX_TTL = 60.0        # 索引重复算一遍的代价不小，同一次会话里够用
_IDENT_TTL = 180.0       # 「这个类型有没有人提供」的答案，三分钟内不会变
_RULE_CACHE: dict = {"key": None, "data": None}
_index_cache: dict[str, tuple] = {}
_ident_cache: dict[str, dict] = {}


# ---------------------------------------------------------------- 规则表

def rules_path() -> Path:
    """规则表在哪。开发时在 core/ 旁边，打包后跟资源目录走。"""
    here = Path(__file__).with_name(RULES_NAME)
    if here.is_file():
        return here
    return Path(RES_DIR) / "core" / RULES_NAME


def _load_rules() -> tuple[dict, str]:
    """读规则表。返回 (规则, 问题说明)。读不到不抛异常，退回最小骨架。"""
    path = rules_path()
    try:
        st = path.stat()
    except OSError:
        return dict(_MINIMAL), f"rules file missing: {path}"
    key = (str(path), st.st_mtime, st.st_size)
    if _RULE_CACHE["key"] == key and _RULE_CACHE["data"] is not None:
        return _RULE_CACHE["data"], ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("rules file is not an object")
        data = _merge(_MINIMAL, raw)
        for name in data["patterns"]:
            re.compile(data["patterns"][name])
    except (OSError, ValueError, re.error) as exc:
        why = f"rules file unusable: {type(exc).__name__}: {exc}"
        _warn(f"[diagnose] {why}")
        return dict(_MINIMAL), why
    _RULE_CACHE["key"] = key
    _RULE_CACHE["data"] = data
    return data, ""


def _merge(base: dict, over: dict) -> dict:
    """浅合并两层：缺项用最小骨架兜底，坏项不许把整张表带下水。"""
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            merged = dict(base[k])
            merged.update(v)
            out[k] = merged
        elif k in ("rules", "owner_skip") and not isinstance(v, list):
            continue
        else:
            out[k] = v
    if not isinstance(out.get("rules"), list):
        out["rules"] = []
    return out


def _pats(rules: dict) -> dict:
    return {k: re.compile(v) for k, v in (rules.get("patterns") or {}).items()}


def _limits(rules: dict) -> dict:
    lim = dict(_MINIMAL["limits"])
    lim.update(rules.get("limits") or {})
    return lim


# ---------------------------------------------------------------- 入口

def diagnose(text, root=None, mods=None) -> dict:
    """一段报错原文 → 结构化结论。

    root / mods 来自扫描结果（Scanner 的那一份）。拿不到就只按文字本身说话，
    需要本地数据才能判断的规则一律降级成 partial，并写清缺什么。
    """
    rules, why = _load_rules()
    lim = _limits(rules)
    notes = [why] if why else []
    if not rules.get("rules"):
        notes.append("no rules loaded: structural parse only")

    body, truncated = _clip(text, int(lim["max_text_bytes"]))
    lines = [ln.strip() for ln in body.splitlines()]
    lines = [ln for ln in lines if ln]

    idx, index_why = _index(root, mods)
    if index_why:
        notes.append(index_why)

    pats = _pats(rules)
    stats = {"lines": len(lines), "errors": 0, "warnings": 0, "results": 0}
    results: list[dict] = []
    capped = False

    for ln in lines:
        if len(ln) > int(lim["max_line_chars"]):
            # 一行几万字符的乱码（贴错了、或者把二进制粘进来）：不拿去跑正则，
            # 也不当成错误行。宽字符集上的正则是 O(n²)，一行就够卡住整个界面。
            continue
        p = _parse(ln, pats)
        if p["kind"] == "warning":
            # Warning 行是噪音（几乎每次大堆脚本错误都跟着一条），不当结论。
            stats["warnings"] += 1
            continue
        if not _is_error_line(p, rules):
            continue
        stats["errors"] += 1
        if len(results) >= int(lim["max_results"]):
            capped = True
            continue
        results.append(_judge(p, idx, rules, lim))

    stats["results"] = len(results)
    unknown = (not results) or all(r["code"] == "unknown" for r in results)
    return {
        "ok": True,
        "unknown": unknown,
        "truncated": truncated,
        "stats": stats,
        "results": results,
        "lang": i18n.current(),
        "capped": capped,
        "notes": notes,
    }


def drop_cache() -> None:
    """丢掉索引 / 类型搜索的缓存（测试与重新扫描后调）。"""
    _index_cache.clear()
    _ident_cache.clear()
    _RULE_CACHE["key"] = None
    _RULE_CACHE["data"] = None


# ---------------------------------------------------------------- 解析

def _clip(text, limit: int) -> tuple[str, bool]:
    """按 200KB 截断。尽量切在行边界上，别把最后一行切成半句。"""
    if not isinstance(text, str):
        text = ""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text, False
    cut = raw[:limit]
    nl = cut.rfind(b"\n")
    if nl > limit // 2:
        cut = cut[:nl]
    return cut.decode("utf-8", "ignore"), True


def _parse(line: str, pats: dict) -> dict:
    """引擎的行格式：`Error [归属]相对路径(行号): 消息`。

    真实文本很脏，样本里就有 `ws(14:` 少右括号的写法，所以每一段都单独容错：
    少了前缀、少了右括号、少了冒号，能救回来就救回来。
    """
    s = (line or "").strip()
    out = {"raw": s, "kind": "", "owner": "", "rel": "", "line": None, "msg": s}

    m = pats["kind"].match(s)
    if m:
        out["kind"] = m.group(1).lower()

    owner_m = None
    head = s[:100]
    if "owner" in pats:
        owner_m = pats["owner"].search(head)
    if owner_m:
        name = (owner_m.group(1) or "").strip()
        # 归属是个文件夹名：带路径分隔符或空格的多半是消息里的方括号，不是归属
        if name and not any(c in name for c in "\\/: ") and len(name) <= 100:
            out["owner"] = name

    msg = s
    if "where" in pats:
        mw = pats["where"].search(s)
        if mw:
            out["rel"] = (mw.group(1) or "").strip()
            try:
                out["line"] = int(mw.group(2))
            except (TypeError, ValueError):
                out["line"] = None
            msg = s[mw.end():]
        elif owner_m:
            msg = s[owner_m.end():]
    elif owner_m:
        msg = s[owner_m.end():]

    msg = msg.lstrip(")").lstrip(":").strip()
    out["msg"] = msg or s
    return out


def _is_error_line(p: dict, rules: dict) -> bool:
    """这算不算一条「要说点什么」的错误行。Warning / 纯说明行直接放过。"""
    if p["kind"] == "error":
        return True
    if p["kind"]:
        return False                      # warning / info
    if _hit_keywords(p["raw"], rules):
        return True
    return bool(p["rel"])                 # 丢了 Error 前缀、但带着 .ws(行号)


def _hit_keywords(text: str, rules: dict, only: dict | None = None) -> bool:
    low = (text or "").lower()
    pool = [only] if only is not None else (rules.get("rules") or [])
    for rule in pool:
        for kw in rule.get("keywords") or []:
            if str(kw).lower() in low:
                return True
    return False


# ---------------------------------------------------------------- 判断

def _judge(p: dict, idx: dict | None, rules: dict, lim: dict) -> dict:
    for rule in rules.get("rules") or []:
        fn = _HANDLERS.get(str(rule.get("code") or ""))
        if fn is None:
            continue
        try:
            hit = fn(p, idx, rules, lim, rule)
        except Exception as exc:          # noqa: BLE001 一条规则出错不许拖死整段
            _warn(f"[diagnose] rule {rule.get('code')} failed: "
                  f"{type(exc).__name__}: {exc}")
            hit = None
        if hit is not None:
            return hit
    return _unknown_result(p, idx, rules)


def _h_syntax(p: dict, idx: dict | None, rules: dict, lim: dict, rule: dict) -> dict | None:
    if not _hit_keywords(p["raw"], rules, only=rule):
        return None
    rel = _norm(p["rel"])
    if not rel:
        # 认得出是语法坏了，但这段里没文件 —— 按「只能确定到这一步」说话
        return _result(p, rule, "partial", None, [
            p["raw"],
            _say(rule, "what_bare"),
            _say(rule, "nofile"),
            _say(rule, "next"),
        ])
    ev = [p["raw"], _say(rule, "what", rel=rel)]
    prov = _providers(idx, rel)
    if prov:
        ev.append(_say(rule, "who", names=_names(prov)))
    elif p["owner"] and p["owner"].lower().startswith("content"):
        ev.append(_say(rule, "content0", owner=p["owner"]))
    elif p["owner"]:
        ev.append(_say(rule, "owner", owner=p["owner"]))
    ev.append(_say(rule, "next"))
    return _result(p, rule, "certain", rel, ev)


def _h_merged_stale(p: dict, idx: dict | None, rules: dict, lim: dict, rule: dict) -> dict | None:
    if not _is_merged(p["owner"], idx, rules):
        return None
    pat = _pats(rules).get("unknown_type")
    m = pat.search(p["msg"]) if pat else None
    if not m:
        return None
    ident = (m.group(1) or "").strip()
    if not ident:
        return None
    rel = _norm(p["rel"])
    name = _merged_name(idx, rules)
    head = [p["raw"], _say(rule, "what", name=name)]

    if idx is None:
        return _result(p, rule, "partial", rel, head + [_say(rule, "no_index")])

    hits = _idents_found(idx, [ident], lim).get(ident.lower())
    if hits is None:
        return _result(p, rule, "partial", rel, head + [_say(rule, "over_budget")])
    if not hits:
        st = idx.get("merged") or {}
        tail = [] if st.get("exists") else [_say(rule, "no_folder")]
        return _result(p, rule, "likely", rel,
                       head + [_say(rule, "missing", ident=ident),
                               _say(rule, "gone"), _say(rule, "next_gone")] + tail)

    live = [f for f in hits if _mod_enabled(idx, f)]
    if live:
        return _result(p, rule, "partial", rel,
                       head + [_say(rule, "found_elsewhere", names=_names(live))])
    return _result(p, rule, "likely", rel,
                   head + [_say(rule, "disabled_only", names=_names(hits)),
                           _say(rule, "next_disabled")])


def _h_merged_unregistered(p: dict, idx: dict | None, rules: dict, lim: dict,
                           rule: dict) -> dict | None:
    if not _is_merged(p["owner"], idx, rules):
        return None
    rel = _norm(p["rel"])
    name = _merged_name(idx, rules)
    head = [p["raw"], _say(rule, "what", name=name)]

    if idx is None:
        return _result(p, rule, "partial", rel, head + [_say(rule, "no_index")])

    st = idx.get("merged") or {}
    if not st.get("exists"):
        # 合订本文件夹都没有，却报合订本里的文件 —— 这段报错是旧的
        return _result(p, rule, "partial", rel, head + [_say(rule, "no_folder")])
    if st.get("ok"):
        return None                       # 登记没问题、顺序也对 → 真不认识，交给 unknown

    if not st.get("entry"):
        why = _say(rule, "no_entry")
    elif not st.get("enabled"):
        why = _say(rule, "disabled")
    else:
        why = _say(rule, "not_min", priority=st.get("priority"),
                   min_other=st.get("min_other"))
    return _result(p, rule, "likely", rel, head + [why, _say(rule, "next")])


def _h_ghost(p: dict, idx: dict | None, rules: dict, lim: dict, rule: dict) -> dict | None:
    owner = (p["owner"] or "").strip()
    if not owner or _is_merged(owner, idx, rules):
        return None
    for pat in rules.get("owner_skip") or []:
        try:
            if re.match(str(pat), owner, re.IGNORECASE):
                return None
        except re.error:
            continue
    low = owner.lower()
    rel = _norm(p["rel"])

    if idx is None:
        return _result(p, rule, "partial", rel,
                       [p["raw"], _say(rule, "what_bare", owner=owner),
                        _say(rule, "no_index")])

    disk = {str(f).lower() for f in (idx.get("disk_folders") or [])}
    disk |= {d.lstrip("~") for d in disk}       # 停用的模组文件夹带 `~` 前缀
    keys = {str(m.get("key") or "").lower() for m in (idx.get("mods") or [])}
    if low and (low in disk or low in keys):
        return None
    return _result(p, rule, "certain", rel,
                   [p["raw"], _say(rule, "what", owner=owner),
                    _say(rule, "why"), _say(rule, "next")])


def _unknown_result(p: dict, idx: dict | None, rules: dict) -> dict:
    blk = rules.get("unknown") or _MINIMAL["unknown"]
    ev = [p["raw"], _say(blk, "not_mine"), _say(blk, "steps")]
    if idx is None:
        ev.append(_say(blk, "no_index"))
    return {
        "raw": p["raw"],
        "code": "unknown",
        "verdict": "unknown",
        "owner": p["owner"] or None,
        "rel": _norm(p["rel"]),
        "evidence": [e for e in ev if e],
        "action": {"code": _action(blk)},
        "line": p["line"],
    }


_HANDLERS = {
    "syntax_broken": _h_syntax,
    "merged_stale_ref": _h_merged_stale,
    "merged_unregistered": _h_merged_unregistered,
    "ghost_folder": _h_ghost,
}


def _result(p: dict, rule: dict, verdict: str, rel: str | None, ev: list) -> dict:
    if verdict not in VERDICTS:
        verdict = "unknown"
    return {
        "raw": p["raw"],
        "code": str(rule.get("code") or "unknown"),
        "verdict": verdict,
        "owner": p["owner"] or None,
        "rel": rel or None,
        "evidence": [e for e in ev if e],
        "action": {"code": _action(rule)},
        "line": p["line"],
    }


def _action(block: dict) -> str:
    code = str(block.get("action") or "none").strip()
    return code if code in ACTIONS else "none"


def _say(block: dict, key: str, **kw) -> str:
    """取一句当前语言的话。文案全在规则表里 —— 代码里不写死给人看的句子。"""
    texts = block.get("text") or {}
    table = texts.get(i18n.current()) or texts.get("zh") or {}
    s = table.get(key) or ""
    for k, v in kw.items():
        s = s.replace("{" + k + "}", str(v))
    return s


def _names(keys: list) -> str:
    return i18n.join(i18n.quote(str(k)) for k in keys)


def _norm(rel: str) -> str | None:
    """路径归一化：唯一入口是 merger._norm_rel，重写一份必然对不上。"""
    if not rel:
        return None
    try:
        return merger._norm_rel(rel)
    except Exception:                     # noqa: BLE001 归一化失败就不指路，不猜
        return None


# ---------------------------------------------------------------- 数据

def _index(root, mods) -> tuple[dict | None, str]:
    """冲突检测的索引（带短缓存）。拿不到时返回 (None, 缺什么)。"""
    if root is None:
        return None, "no game root: rules needing local data are degraded to partial"
    key = str(root).lower()
    now = time.time()
    hit = _index_cache.get(key)
    if hit and now - hit[0] < _INDEX_TTL:
        return hit[1], ""
    try:
        idx = conflicts.index(Path(root), list(mods or []))
    except Exception as exc:              # noqa: BLE001 索引坏了不许把诊断也拖死
        why = f"conflict index failed: {type(exc).__name__}: {exc}"
        _warn(f"[diagnose] {why}")
        return None, why
    if len(_index_cache) >= 8:
        _index_cache.clear()
    _index_cache[key] = (now, idx)
    return idx, ""


def _providers(idx: dict | None, rel: str | None) -> list[str]:
    if not idx or not rel:
        return []
    keys = (idx.get("scripts") or {}).get(rel) or []
    return sorted(set(keys), key=str.lower)


def _mod_enabled(idx: dict, folder: str) -> bool:
    low = str(folder).lower()
    for m in idx.get("mods") or []:
        f = str(m.get("folder") or "").lower()
        if f == low or f.lstrip("~") == low.lstrip("~"):
            return bool(m.get("enabled"))
    return False


def _merged_name(idx: dict | None, rules: dict) -> str:
    if idx:
        st = idx.get("merged") or {}
        if st.get("name"):
            return str(st["name"])
        for n in idx.get("merged_names") or []:
            return str(n)
    return str((rules.get("merged") or {}).get("default_bracket") or "")


def _is_merged(owner: str, idx: dict | None, rules: dict) -> bool:
    """方括号里那个名字是不是「合并产物」（而不是一个普通模组文件夹）。"""
    low = (owner or "").strip().lower()
    if not low:
        return False
    names = {str((rules.get("merged") or {}).get("default_bracket") or "").lower()}
    if idx:
        names |= {str(n).lower() for n in (idx.get("merged_names") or [])}
        st = idx.get("merged") or {}
        if st.get("name"):
            names.add(str(st["name"]).lower())
    names.discard("")
    return low in names


def _idents_found(idx: dict, names: list[str], lim: dict) -> dict:
    """这些类型名在你的 mods 里有没有人提供。

    返回 {小写名: [提供它的文件夹名, ...] 或 None}。**None 表示没扫完**
    （到上限了），调用方必须当「判断不了」处理，绝不能当成「没有」——
    「找不到提供者」被说成「缺前置模组」是这条路线上最容易犯的错。
    """
    mods_dir = Path(str(idx.get("mods_dir") or ""))
    out: dict = {}
    if not mods_dir.is_dir():
        return {str(n).lower(): [] for n in names if n}

    merged = {str(n).lower() for n in (idx.get("merged_names") or [])}
    now = time.time()
    cache = _ident_cache.setdefault(str(mods_dir).lower(), {})
    todo: list[str] = []
    for raw in names:
        low = str(raw).lower()
        hit = cache.get(low)
        if hit and now - hit[0] < _IDENT_TTL:
            out[low] = hit[1]
        else:
            todo.append(low)

    if todo:
        found, complete = _sweep(mods_dir, merged, todo, lim)
        for low in todo:
            if found[low] or complete:
                if len(cache) >= 400:
                    cache.clear()
                cache[low] = (now, found[low])
                out[low] = found[low]
            else:
                out[low] = None
    return out


def _sweep(mods_dir: Path, skip: set, todo: list, lim: dict) -> tuple[dict, bool]:
    """在 mods/*/content/scripts 的 .ws 里找这几个名字。

    跳过合并产物文件夹 —— 报错说的那个类型本来就写在合订本里，
    不跳过的话每一个都会「找到」，这条规则就永远不成立。
    """
    found: dict = {k: [] for k in todo}
    left = set(todo)
    budget = int(lim["ident_scan_bytes"])
    files = 0
    try:
        entries = sorted(mods_dir.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return found, True
    for d in entries:
        if not d.is_dir() or d.name.lower() in skip:
            continue
        scripts = d / "content" / "scripts"
        if not scripts.is_dir():
            continue
        for cur, _dirs, names in os.walk(scripts):
            for f in names:
                if not f.lower().endswith(".ws"):
                    continue
                files += 1
                if files > int(lim["ident_scan_files"]):
                    return found, False
                p = Path(cur) / f
                try:
                    size = p.stat().st_size
                except OSError:
                    continue
                if size > int(lim["ident_scan_file_bytes"]):
                    continue
                if size > budget:
                    return found, False
                budget -= size
                try:
                    with open(p, "rb") as fh:
                        text = fh.read().decode("utf-8", "ignore").lower()
                except OSError:
                    continue
                for low in list(left):
                    if low and low in text:
                        found[low].append(d.name)
                        left.discard(low)
                if not left:
                    return found, True
    return found, True
