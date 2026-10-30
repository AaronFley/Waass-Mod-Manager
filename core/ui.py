"""界面提示的「已读」账本。

为什么需要
----------
界面上的警告提示（扫描发现几处需要注意）本质上只是状态播报：
状态不变，它们就一直在，每次开软件都弹一遍，看起来像有没处理完的错误。

所以给它们记一笔：用户按过「忽略」之后，同样的内容不再出现；
一旦内容真的变了（冒出一条新警告），才重新提醒。

账本放在 data/ui.json —— 和 config.json 同级的一个小文件。
删掉它没有任何副作用，只是提示会重新出现。

注：账本里的 dismissed_dl（「下载目录有新包」那条）1.0.7 起前端不再用了，
先留着不动，将来想恢复那个提示不用重写记账逻辑。
"""
from __future__ import annotations

import hashlib
import json

from .paths import DATA_DIR

FILE = DATA_DIR / "ui.json"
KEEP_MAX = 200          # 最多记这么多条，防止文件无限长大


def _blank() -> dict:
    return {"dismissed_warns": [], "dismissed_dl": "", "dismissed_upd": ""}


def load() -> dict:
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _blank()
    if not isinstance(raw, dict):
        return _blank()
    out = _blank()
    warns = raw.get("dismissed_warns")
    if isinstance(warns, list):
        out["dismissed_warns"] = [str(x) for x in warns if isinstance(x, str)]
    for k in ("dismissed_dl", "dismissed_upd"):
        v = raw.get(k)
        if isinstance(v, str):
            out[k] = v
    return out


def _save(state: dict) -> None:
    try:
        FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(FILE)
    except OSError:
        pass


def forget_warns(keys: list[str]) -> dict:
    st = load()
    changed = False
    for k in keys:
        if k and k not in st["dismissed_warns"]:
            st["dismissed_warns"].append(k)
            changed = True
    if changed:
        st["dismissed_warns"] = st["dismissed_warns"][-KEEP_MAX:]
        _save(st)
    return st


def forget_dl(sig: str) -> dict:
    st = load()
    if sig and st["dismissed_dl"] != sig:
        st["dismissed_dl"] = sig
        _save(st)
    return st


def forget_upd(sig: str) -> dict:
    """「有 N 个模组可更新」那条提示的已读记账。

    指纹只由「可更新的模组名单」决定，不含时间 —— 同一批更新忽略过就不再出现；
    等真的又冒出一个新版本（名单变了），指纹变，提示自己回来。
    """
    st = load()
    if sig and st["dismissed_upd"] != sig:
        st["dismissed_upd"] = sig
        _save(st)
    return st


def reset() -> None:
    _save(_blank())


def digest(items: list[str]) -> str:
    """一串字符串 → 短指纹，用来判断「内容变了没有」。"""
    h = hashlib.sha1("\n".join(sorted(items)).encode("utf-8")).hexdigest()
    return h[:16]
