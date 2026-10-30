"""脚本文件的两项属性：注解挂在哪个函数上、编码合不合规。

为什么要看注解
--------------
5.x 之后好 mod 都用注解（`@wrapMethod` / `@replaceMethod`）而不是整份覆盖文件。
注解型 mod 之间**文件名不撞**，所以按文件名比永远是「无冲突」—— 但它们可能
包同一个函数。`@wrapMethod` 是链式叠加（能共存，顺序影响结果），
`@replaceMethod` 是整段替换（后生效的那份会把前一份的改动连同包装一起丢掉）。

2026-10-28 的实例：MoreSkillSlots 与 AutoLoot 各带一份 `playerWitcher.ws`，
撞的是文件；而真正决定「谁的 GetAutoLootManager 还在」的，是最后生效的
那一份。注解型 mod 之间没有这种惨案，但同名的包装顺序仍会改变行为。

看编码
------
5.00 把文本资源从 UTF-16 改成 UTF-8。4.x 时代的 `.ws` 原样拷进来会编译失败，
报错只指向文件名，用户看不出问题在编码上。这里提前标出来。

只读承诺
--------
只 `open(..., "rb")` 读前若干字节 / 全文，不写、不改、不缓存到磁盘。
单文件超过 `MAX_BYTES` 只查编码，不扫注解（防止某个整合包里塞了巨型文件）。
"""
from __future__ import annotations

import codecs
import re
from pathlib import Path

# 单文件扫注解的上限。超过就只查编码。
MAX_BYTES = 4 << 20

# @wrapMethod( CR4MapMenu ) / @replaceMethod( Some.Class )
_ANNO = re.compile(
    r"@(wrapMethod|replaceMethod)\s*\(\s*([A-Za-z_][\w.]*)\s*\)")
# 下一行（可能带修饰词）的 function 声明
_FUNC = re.compile(
    r"^\s*(?:(?:private|protected|public|event|timer|quest|storyscene"
    r"|exec|latent|static|final|import)\s+)*function\s+([A-Za-z_]\w*)")

_ENCODINGS = {
    "utf-8": "utf-8",
    "utf-8-bom": "utf-8-bom",
    "utf-16-le": "utf-16-le",
    "utf-16-be": "utf-16-be",
    "other": "other",
}

_cache: dict[tuple, dict] = {}
_cache_max = 1200


def encoding(path: Path | str) -> str:
    """判断文本编码。返回值里只有 `utf-8` 是 5.x 合规的。

    BOM 也算不合规：引擎能读，但同一份文件被别的工具改写时容易出岔子，
    而且项目里所有原版脚本都是无 BOM。
    """
    p = Path(path)
    try:
        with open(p, "rb") as f:
            head = f.read(4096)
    except OSError:
        return "other"
    if head[:2] == b"\xff\xfe":
        return "utf-16-le"
    if head[:2] == b"\xfe\xff":
        return "utf-16-be"
    if head[:3] == b"\xef\xbb\xbf":
        return "utf-8-bom"
    # 没有 BOM 的 UTF-16：ASCII 文本存成 "x\0x\0" 的样子，NUL 占一半。
    # 有些工具转码时会把 BOM 去掉，光看前两字节会误判成 UTF-8。
    # 用比例而不是绝对个数 —— 短文件用绝对阈值判定不出来。
    probe = head[:64]
    if len(probe) >= 8 and probe.count(0) * 10 >= len(probe) * 4:
        return "utf-16-le" if probe[1:2] == b"\x00" else "utf-16-be"
    # 末尾可能被切断在多字节字符中间，用增量解码器避免误判
    try:
        dec = codecs.getincrementaldecoder("utf-8")()
        dec.decode(head, False)
    except UnicodeDecodeError:
        return "other"
    return "utf-8"


def annotations(path: Path | str, limit: int = 400) -> list[tuple[str, str, str]]:
    """抽出脚本里的注解目标：[(wrap|replace, 类名, 函数名)]。

    只认 `@wrapMethod` 与 `@replaceMethod` —— `@addMethod` / `@addField` 是
    往类上加新东西，撞名概率极低，加进来只会制造噪音。
    """
    p = Path(path)
    try:
        if p.stat().st_size > MAX_BYTES:
            return []
        text = _read_text(p)
    except OSError:
        return []
    if not text:
        return []

    out: list[tuple[str, str, str]] = []
    pending: tuple[str, str] | None = None
    for line in text.splitlines():
        mt = _ANNO.search(line)
        if mt:
            pending = (mt.group(1), mt.group(2).split(".")[-1])
            continue
        if pending is None:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue                      # 注解和函数之间允许空行/注释
        fn = _FUNC.match(line)
        if fn:
            out.append((pending[0], pending[1], fn.group(1)))
            if len(out) >= limit:
                break
        pending = None                    # 下一行不是函数声明，这个注解不是挂函数的
    return out


def _read_text(p: Path) -> str:
    """按实际编码读文本。UTF-16 的文件也能扫，虽然它本来就不合规。"""
    raw = p.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    return raw.decode("utf-8-sig", "replace")


def scan(path: Path | str) -> dict:
    """一个脚本文件的两项属性。结果按 (路径, mtime, 大小) 缓存。"""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return {"enc": "other", "anno": []}
    key = (str(p).lower(), int(st.st_mtime), st.st_size)
    hit = _cache.get(key)
    if hit is not None:
        return hit
    val = {"enc": encoding(p), "anno": annotations(p)}
    if len(_cache) >= _cache_max:
        _cache.clear()
    _cache[key] = val
    return val


def drop_cache() -> None:
    _cache.clear()
