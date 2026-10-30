"""版本号比较：一套规则，三处共用。

为什么单独拎出来
----------------
原来 track.py 和 nexus.py 各写了一份「抠出数字再比大小」，规则还不完全一样。
版本号判错一位的后果不是崩溃，是**界面安静地说错话**：把落后一个热修的
mod 说成「已是最新」，或者把最新的说成「本地比线上还新」。这类错没人会
怀疑到比对逻辑头上，所以规则只留这一份。

规则
----
* 只认数字段：`v3.02` → (3, 2, 0, 0)、`1.9.2.1` → (1, 9, 2, 1)
* 最多取 4 段，不够补 0 —— 所以 `1.2` 和 `1.2.0` 相等
* 带 alpha / beta / rc / pre / dev 后缀的，同号情况下算更旧：
  `0.3beta` < `0.3`（Nexus 上作者就是这么用的）
* 抠不出数字 → 空元组，调用方按「看不出来」处理，不许瞎猜
"""
from __future__ import annotations

import re

_PRE = re.compile(r"alpha|beta|rc|pre|dev", re.I)


def vkey(s: str) -> tuple:
    """版本字符串 → 可比较的元组。认不出来返回 ()。"""
    text = str(s or "")
    nums = re.findall(r"\d+", text)
    if not nums:
        return ()
    out = [int(x) for x in nums[:4]]
    while len(out) < 4:
        out.append(0)
    # 第 5 位是「正式版标记」：普通版 1，预发布 0。同号时正式版更大。
    out.append(0 if _PRE.search(text) else 1)
    return tuple(out)


def compare(remote: str, local: str) -> int:
    """线上 > 本地 → 1；相等 → 0；线上 < 本地 → -1；看不出来 → -9。"""
    a, b = vkey(remote), vkey(local)
    if not a or not b:
        return -9
    if a > b:
        return 1
    if a == b:
        return 0
    return -1


def newer(remote: str, local: str) -> bool | None:
    """True 有更新 / False 没有 / None 缺一边的版本号，判断不了。"""
    c = compare(remote, local)
    if c == -9:
        return None
    return c == 1
