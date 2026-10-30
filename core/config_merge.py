"""游戏自带配置文件的「合并写入」。

为什么需要它
------------
有些 mod 会往游戏自己的 `bin/config/` 里塞 `.ini`：

| 包 | 目标 | 里面写的 |
| --- | --- | --- |
| Debug Console | `bin/config/base/general.ini` | `DBGConsoleOn=true` |
| RemasteredBloom | `bin/config/base/visuals.ini` | `BloomBeforeTonemap=true` |
| 各类按键 mod | `bin/config/r4game/legacy/base/input_*.ini` | 按键绑定 |
| 优化类 mod | `bin/config/platform/pc/rendering.ini` | 画质参数 |

这些文件是**游戏自己的运行参数**，不是 mod 的私有物。整份替换会出三种事：

1. 冲掉游戏原有的键 —— mod 作者只按自己那版游戏写文件；
2. 冲掉你自己改过的设置（按键、画质）；
3. 把版本字段改旧 —— Debug Console 包里是 `ConfigVersion=5`，
   而你游戏里是 `7`。降级会让游戏判定配置过期，甚至重置设置。

所以这类文件按「节 + 键」合并：只把 mod 真正写了的东西打进去，
卸载时反向撤销，文件本身保留。

本模块是纯函数，不认路径、不碰磁盘权限 —— 落盘由 core/install.py 负责，
那里才有写保护闸门。

两条硬规则
----------
1. **只加和改，从不按「包里没写」删键。**
   多数 mod 附带的是片段文件（只有自己那几行），少数附的是完整文件。
   两种都绝不能因为「没有这个键」就当游戏该删它。
2. **还原时如果当前值不等于当初写进去的值，就别动。**
   说明有第三方（另一个 mod 或你手工）改过，硬还原会吃掉别人的改动。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from core import i18n

# 只处理这些：配置语义、按行可合并
INI_SUFFIXES = {".ini", ".cfg"}

# 游戏自己管理、mod 不该碰的键。Debug Console 的包里写的是 ConfigVersion=5，
# 而你游戏里是 7 —— 合并进去等于把配置格式降级，游戏可能判定过期并重置设置。
# 这类键一律跳过，并在预览里说明。
PROTECTED_KEYS = {"configversion"}

_SECTION_RE = re.compile(r"^\s*\[(?P<name>[^\]]*)\]\s*$")


def supported(rel_or_name: str) -> bool:
    """这个文件能不能按行合并。目前只有 .ini / .cfg。"""
    return Path(str(rel_or_name)).suffix.lower() in INI_SUFFIXES


# ---------------- 文本读写 ----------------
def load_text(path: str | Path) -> tuple[str, str]:
    """读成文本，返回 (内容, 编码)。编码原样记住，写回时用同一个。"""
    p = Path(path)
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk", "cp1252"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1"), "latin-1"


def save_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode(encoding, errors="strict"))


# ---------------- 行模型 ----------------
def _split_eol(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


@dataclass
class Line:
    raw: str                  # 不含行尾
    eol: str
    section: str = ""         # 小写节名，用于比较；"" = 第一个节头之前
    key: str = ""             # 小写键名；"" = 非键值行
    value: str = ""
    section_raw: str = ""     # 节名的原始写法，插新节时必须照抄（大小写敏感）

    @property
    def is_kv(self) -> bool:
        return bool(self.key)


def parse(text: str) -> list[Line]:
    """切成行，标出每行属于哪个节、是不是键值行。注释和空行原样留着。"""
    out: list[Line] = []
    section = ""
    section_raw = ""
    for raw_line in text.splitlines(keepends=True):
        body, eol = _split_eol(raw_line)
        stripped = body.strip()
        m = _SECTION_RE.match(body)
        if m:
            section_raw = m.group("name").strip()
            section = section_raw.lower()
            out.append(Line(body, eol, section, section_raw=section_raw))
            continue
        if not stripped or stripped[0] in ";#":
            out.append(Line(body, eol, section, section_raw=section_raw))
            continue
        if "=" in stripped:
            k, v = stripped.split("=", 1)
            key = k.strip().lower()
            out.append(Line(body, eol, section, key, v.strip(), section_raw))
            continue
        out.append(Line(body, eol, section, section_raw=section_raw))
    return out


def _join(lines: list[Line], default_eol: str = "\r\n") -> str:
    """拼回文本。最后一行原本没有换行符的话，也不给它补上。"""
    parts: list[str] = []
    last = len(lines) - 1
    for i, ln in enumerate(lines):
        eol = ln.eol
        if not eol and i < last:
            eol = default_eol
        parts.append(ln.raw + eol)
    return "".join(parts)


# ---------------- 变更集 ----------------
@dataclass
class Change:
    """一条改动。op 只有 add（原来没有这个键）、mod（有但值不同）、skip（不合并）。"""

    op: str                 # add / mod / skip
    section: str            # 小写
    key: str                # 小写
    value: str              # mod 想要的值
    old: str = ""           # 安装前的值（mod 才有）
    line: str = ""          # mod 包里的原始行，插入时照着写，保留大小写
    why: str = ""           # op == skip 时的原因，给界面看
    section_raw: str = ""   # 节名原始写法，插新节时照抄（ini 节名大小写敏感）

    def as_dict(self) -> dict:
        return {"op": self.op, "section": self.section, "key": self.key,
                "value": self.value, "old": self.old, "line": self.line,
                "why": self.why, "section_raw": self.section_raw}

    @classmethod
    def from_dict(cls, d: dict) -> "Change":
        return cls(d.get("op", "add"), d.get("section", ""), d.get("key", ""),
                   d.get("value", ""), d.get("old", ""), d.get("line", ""),
                   d.get("why", ""), d.get("section_raw", ""))


def _key_map(lines: list[Line]) -> dict[tuple[str, str], Line]:
    """(节, 键) → 行。同名键以最后一行为准 —— ini 里后出现的生效。"""
    m: dict[tuple[str, str], Line] = {}
    for ln in lines:
        if ln.is_kv:
            m[(ln.section, ln.key)] = ln
    return m


def diff(current_text: str, patch_text: str) -> list[Change]:
    """算出「包里的文件相对当前文件改了什么」。

    只返回包里写了的键：包里没有的键一律当作「与本次无关」，不产生删除项。
    游戏自管的键（PROTECTED_KEYS）标成 skip，交给界面解释为什么没写入。
    """
    cur = _key_map(parse(current_text))
    changes: list[Change] = []
    for k, ln in _key_map(parse(patch_text)).items():
        section, key = k
        old_ln = cur.get(k)
        if key in PROTECTED_KEYS:
            if old_ln is not None and _norm(old_ln.value) != _norm(ln.value):
                changes.append(Change(
                    "skip", section, key, ln.value, old_ln.value, ln.raw.strip(),
                    i18n.t("cm.skip_protected", pv=ln.value, cv=old_ln.value)))
            continue
        if old_ln is None:
            changes.append(Change("add", section, key, ln.value, "",
                                  ln.raw.strip(), "", ln.section_raw))
        elif _norm(old_ln.value) != _norm(ln.value):
            changes.append(Change("mod", section, key, ln.value,
                                  old_ln.value, ln.raw.strip(), "", ln.section_raw))
    changes.sort(key=lambda c: (c.op, c.section, c.key))
    return changes


def _norm(v: str) -> str:
    """比较用：去空白、统一布尔与数字写法，避免 true/True 被当成改动。"""
    s = v.strip()
    low = s.lower()
    if low in ("true", "false"):
        return low
    try:
        f = float(s)
        return repr(int(f)) if f == int(f) else repr(f)
    except (TypeError, ValueError):
        return s


def _replace_value(raw: str, value: str) -> str:
    """把 `键 = 旧值` 里的值换掉，保留缩进、键的写法、等号周围的空格。"""
    i = raw.find("=")
    if i < 0:
        return raw
    left, rest = raw[: i + 1], raw[i + 1:]
    stripped = rest.lstrip()
    pad = rest[: len(rest) - len(stripped)]
    return left + pad + value


def _default_eol(lines: list[Line]) -> str:
    for ln in lines:
        if ln.eol:
            return ln.eol
    return "\r\n"


def _insert_for_section(lines: list[Line], section: str, new: list[Line],
                        eol: str) -> None:
    """把新键插到该节最后一行的后面；节不存在就在文件末尾另起一节。"""
    idx = -1
    for i, ln in enumerate(lines):
        if ln.is_kv and ln.section == section:
            idx = i
    if idx < 0:
        # 没有这个节（或节里没键）：找节头位置，没有就追加到末尾
        head = -1
        for i, ln in enumerate(lines):
            if ln.section == section and not ln.is_kv:
                head = i
        if head >= 0:
            idx = head
        else:
            raw_name = new[0].section_raw or section if new else section
            if lines and lines[-1].raw.strip():
                lines.append(Line("", eol, section, section_raw=raw_name))
            if section:
                lines.append(Line(f"[{raw_name}]", eol, section, section_raw=raw_name))
            idx = len(lines) - 1
    for off, ln in enumerate(new, start=1):
        lines.insert(idx + off, ln)


def apply(current_text: str, changes: list[Change]) -> tuple[str, int]:
    """把变更集打到当前文件上。返回 (新内容, 实际改动条数)。"""
    live = [c for c in changes if c.op != "skip"]
    if not live:
        return current_text, 0
    lines = parse(current_text)
    eol = _default_eol(lines)
    want = {(c.section, c.key): c for c in live}
    hit: set[tuple[str, str]] = set()
    touched = 0

    for ln in lines:
        if not ln.is_kv:
            continue
        k = (ln.section, ln.key)
        c = want.get(k)
        if c is None:
            continue
        hit.add(k)
        if c.op == "mod":
            if _norm(ln.value) == _norm(c.value):
                continue
            ln.raw = _replace_value(ln.raw, c.value)
            ln.value = c.value
            touched += 1
        # op == add 但当前已有：说明别人先加了，视为一致，不再动

    pending: dict[str, list[Line]] = {}
    for c in live:
        k = (c.section, c.key)
        if c.op != "add" or k in hit:
            continue
        body = c.line or f"{c.key}={c.value}"
        pending.setdefault(c.section, []).append(
            Line(body, eol, c.section, c.key, c.value, c.section_raw))
        touched += 1

    for section, new in pending.items():
        _insert_for_section(lines, section, new, eol)

    return _join(lines, eol), touched


def _drop_empty_sections(lines: list[Line], sections: set[str]) -> list[Line]:
    """撤掉那些「一个键都不剩、且全是 mod 加的」节的节头。

    不这么做，卸载后文件里会留下一个空的 `[AutoLoot]` —— 游戏读不坏，
    但看着像没卸干净，而且下次装回来会变成两个同名节。
    """
    if not sections:
        return lines
    has_kv: dict[str, int] = {}
    for ln in lines:
        if ln.is_kv:
            has_kv[ln.section] = has_kv.get(ln.section, 0) + 1
    out: list[Line] = []
    for ln in lines:
        is_head = bool(_SECTION_RE.match(ln.raw)) if not ln.is_kv else False
        if is_head and ln.section in sections and not has_kv.get(ln.section):
            if out and not out[-1].raw.strip():        # 顺手吃掉前面的空行
                out.pop()
            continue
        out.append(ln)
    return out


def revert(current_text: str, changes: list[Change], *,
           force: bool = False) -> tuple[str, int, list[dict]]:
    """撤销变更集。返回 (新内容, 撤销条数, 冲突列表)。

    - add 型：把这一行删掉（因为原来是游戏没有的）
    - mod 型：把值还原成安装前的
    - 当前值已经不等于当初写进去的值 → 视为被第三方改过，跳过并记冲突
      （除非 force=True）
    """
    if not changes:
        return current_text, 0, []
    lines = parse(current_text)
    eol = _default_eol(lines)
    by_key = {(c.section, c.key): c for c in changes if c.op != "skip"}
    out: list[Line] = []
    conflicts: list[dict] = []
    done = 0

    for ln in lines:
        if not ln.is_kv:
            out.append(ln)
            continue
        c = by_key.get((ln.section, ln.key))
        if c is None:
            out.append(ln)
            continue
        if _norm(ln.value) != _norm(c.value) and not force:
            conflicts.append({"section": c.section, "key": c.key,
                              "expected": c.value, "found": ln.value,
                              "why": i18n.t("cm.conflict")})
            out.append(ln)
            continue
        if c.op == "add":
            done += 1
            continue
        ln.raw = _replace_value(ln.raw, c.old)
        ln.value = c.old
        out.append(ln)
        done += 1

    # 整个节都是 mod 加进去的、撤完一个键都不剩 → 节头也别留着
    added_sections: set[str] = set()
    for c in changes:
        if c.op == "add" and c.section:
            added_sections.add(c.section)
    for c in changes:
        if c.op == "mod" and c.section:
            added_sections.discard(c.section)     # 这节原来就有，不能删
    out = _drop_empty_sections(out, added_sections)

    return _join(out, eol), done, conflicts


# ---------------- 给人看的摘要 ----------------
def summarize(changes: list[Change]) -> dict:
    add = [c for c in changes if c.op == "add"]
    mod = [c for c in changes if c.op == "mod"]
    skip = [c for c in changes if c.op == "skip"]

    def label(c: Change) -> str:
        return f"{c.section + '/' if c.section else ''}{c.key}"

    return {
        "add": len(add), "mod": len(mod), "skip": len(skip),
        "total": len(add) + len(mod),
        "added": [label(c) for c in add][:12],
        "modified": [label(c) for c in mod][:12],
        "skipped": [{"key": label(c), "why": c.why} for c in skip][:6],
        "text": _text(add, mod),
    }


def _text(add: list[Change], mod: list[Change]) -> str:
    if add and mod:
        return i18n.t("cm.summary_both", n=len(add), m=len(mod))
    if add:
        return i18n.t("cm.summary_add", n=len(add))
    if mod:
        return i18n.t("cm.summary_mod", n=len(mod))
    return i18n.t("cm.summary_none")
