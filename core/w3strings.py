"""w3strings 的文本读取与安全替换 —— 只做「等长替换」。

为什么只做等长替换
------------------
.w3strings 的结构是「头部 + 索引表 + 文本块」，索引里记着每条的偏移和长度。
想让中文比英文长，就必须重算整张索引表；而重制版的多数文件还带语言相关的
加密（Dynamic 16-bit shift XOR），没有官方编码器的情况下重写索引风险极高 ——
一旦算错，游戏读这个文件会直接崩，或者整段文字变乱码。

等长替换只改文本块里的字节，偏移、长度、哈希全都保持原样，文件大小一模一样。
引擎看到的是一份结构完全合法的文件，只是文字变了。这是唯一能保证「不会把游戏搞坏」
的做法，代价是译文不能比原文长（差额用空格补齐）。

对于确实是明文的文件，这个办法够用：英文 17 字节的 "Switchable Cloaks"，
中文「可切换披风」15 字节，装得下。

对于检测不到足够的可读文本（说明是加密的），直接拒绝，不猜、不硬改。
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from core import i18n

# 被认为是「文本」的最小长度；再短就都是格式字段了
MIN_LEN = 4
MAX_LEN = 4000

_CTRL = set(range(0x00, 0x20)) | {0x7F}


def _looks_text(seg: bytes, nul_after: bool = True) -> bool:
    """判断一段字节是不是「真的文字」。

    这个函数被加密文件坑过：加密后的字节偶尔也能被 UTF-8 解出来，看着像
    `# y-R` 这样的碎片。所以判据要收紧：
      - 必须是一段独立字节（后面紧跟 0x00 或到文件末尾），二进制字段几乎不会满足；
      - 字母占比要高，并且要有长度 ≥3 的单词（或者含中文这类非 ASCII）。
    """
    if not (MIN_LEN <= len(seg) <= MAX_LEN):
        return False
    if not nul_after:
        return False
    try:
        s = seg.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not s.strip():
        return False
    if any(ch in "\ufffd\x00" for ch in s):
        return False
    printable = sum(1 for ch in s if ch.isprintable() or ch in "\n\t")
    if printable / max(len(s), 1) < 0.95:
        return False

    letters = sum(1 for ch in s if ch.isalpha())
    if letters / max(len(s), 1) < 0.5:
        return False
    if not any(ch.isalpha() for ch in s):
        return False

    ascii_only = all(ord(c) < 128 for c in s)
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in s)
    if ascii_only:
        words = [w for w in s.replace("\n", " ").split(" ") if w]
        # 正常文本里的单词大小写是规整的（全小写 / 全大写 / 首字母大写）。
        # 像 "yXh" 这种大小写乱跳的，基本都是加密数据解出来的碎片。
        good = [w for w in words
                if w.isalpha() and (w.islower() or w.isupper() or w.istitle())]
        long_good = [w for w in good if len(w) >= 4]
        mid_good = [w for w in good if len(w) >= 3]
        if not long_good and len(mid_good) < 2:
            return False
    elif not has_cjk and len(s) < 6:
        # 非中文的其它语言（俄/日/韩等）短串容易误判，要求长一点
        return False

    low = s.lower()
    if low.endswith((".xml", ".ws", ".bundle", ".w3strings", ".dds", ".xbm")):
        return False
    if ("\\" in s or "/" in s) and " " not in s:
        return False
    if " " not in s and "_" in s and ascii_only:
        return False
    return True


def extract(path: Path | str) -> list[dict]:
    """扫出文件里所有可读文本片段。

    返回 [{off, length, text, quality}]，length 是原始字节数（写回时必须等长）。
    """
    raw = Path(path).read_bytes()
    out: list[dict] = []
    i, n = 0, len(raw)
    while i < n:
        b = raw[i]
        if b == 0 or b in _CTRL:
            i += 1
            continue
        j = i
        while j < n and raw[j] != 0 and raw[j] not in _CTRL:
            j += 1
        seg = raw[i:j]
        nul_after = (j >= n) or raw[j] == 0
        if _looks_text(seg, nul_after):
            out.append({
                "off": i,
                "length": len(seg),
                "text": seg.decode("utf-8"),
            })
        i = j
    return out


def version(path: Path | str) -> int | None:
    """读头部版本号。162 / 163 / 164 —— 重制版 v5.00 是 164。

    读不出版本说明这不是 w3strings：返回 None。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        return None
    if len(head) < 8 or head[:4] != b"RTSW":
        return None
    return int.from_bytes(head[4:8], "little")


def game_baseline(root: Path | str) -> int | None:
    """游戏本体的语言表版本，作为「mod 表是否过旧」的基准。

    优先读 `content/content0/cn.w3strings`（玩家用中文界面），
    读不到就退回 en。这个文件是游戏自带的，不受 mod 影响。
    """
    base = Path(root) / "content" / "content0"
    for name in ("cn.w3strings", "en.w3strings"):
        v = version(base / name)
        if v is not None:
            return v
    return None


def probe(path: Path | str) -> dict:
    """判断这个文件能不能安全地做等长替换。"""
    p = Path(path)
    try:
        size = p.stat().st_size
        magic = p.read_bytes()[:4]
    except OSError as e:
        return {"ok": False, "reason": i18n.t("ws.read_failed", err=e)}

    if magic != b"RTSW":
        return {"ok": False, "reason": i18n.t("ws.not_w3strings")}

    texts = extract(p)
    chars = sum(len(t["text"]) for t in texts)
    if not texts:
        return {"ok": False, "reason": i18n.t("ws.encrypted"),
                "texts": 0, "size": size}
    # 判定「明文」的门槛：至少 3 条、总字数 24 以上。
    # 加密文件偶尔也能解出几个假碎片，卡在这里。
    if len(texts) < 3 or chars < 24:
        return {"ok": False,
                "reason": i18n.t("ws.too_few"),
                "texts": len(texts), "size": size}
    return {"ok": True, "reason": "", "texts": len(texts),
            "chars": chars, "size": size}


def fits(dst: str, length: int) -> bool:
    return len(dst.encode("utf-8")) <= length


def apply(path: Path | str, mapping: dict[str, str],
          backup_dir: Path | None = None) -> dict:
    """把 mapping（原文 → 译文）写回文件。返回详细结果。

    绝不改变文件长度。放不下的译文会被跳过并报告，不会截断。
    """
    p = Path(path)
    info = probe(p)
    if not info["ok"]:
        raise ValueError(i18n.t("ws.unsafe", reason=info["reason"]))

    raw = bytearray(p.read_bytes())
    spans = extract(p)

    changed: list[dict] = []
    skipped: list[dict] = []
    missing: list[str] = []

    for item in spans:
        src = item["text"]
        dst = mapping.get(src)
        if not dst:
            if src.strip() and len(src) >= 6:
                missing.append(src)
            continue
        dst = str(dst).strip()
        if not dst or dst == src:
            continue
        b = dst.encode("utf-8")
        if len(b) > item["length"]:
            skipped.append({
                "text": src, "dst": dst,
                "need": len(b), "room": item["length"],
            })
            continue
        raw[item["off"]:item["off"] + item["length"]] = b + b" " * (item["length"] - len(b))
        changed.append({"text": src, "dst": dst, "room": item["length"], "used": len(b)})

    result = {
        "file": str(p),
        "changed": len(changed),
        "skipped": skipped,
        "missing": missing[:40],
        "size_before": len(p.read_bytes()),
        "backup": "",
    }

    if not changed:
        result["size_after"] = result["size_before"]
        return result

    # 备份（放在程序自己的目录里，不污染游戏目录）
    if backup_dir is not None:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{p.stem}.{stamp}{p.suffix}"
        shutil.copy2(p, dest)
        result["backup"] = str(dest)

    # 长度必须一致，这是安全底线
    if len(raw) != result["size_before"]:
        raise RuntimeError(i18n.t("ws.length_changed"))

    p.write_bytes(raw)
    result["size_after"] = len(raw)
    return result


def scan_mod(mod_dir: Path) -> list[dict]:
    """列出一个 mod 里所有 w3strings 文件及其可编辑性。"""
    out: list[dict] = []
    content = Path(mod_dir) / "content"
    if not content.is_dir():
        return out
    for f in content.rglob("*.w3strings"):
        try:
            size = f.stat().st_size
        except OSError:
            continue
        info = probe(f)
        out.append({
            "path": str(f),
            "rel": str(f.relative_to(mod_dir)).replace("\\", "/"),
            "lang": f.stem.lower(),
            "size": size,
            "ver": version(f),
            "ok": info["ok"],
            "reason": info.get("reason", ""),
            "texts": info.get("texts", 0),
        })
    out.sort(key=lambda x: x["rel"])
    return out
