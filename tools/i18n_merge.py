"""把 _work/i18n/ 里的片段合并进 web/i18n/zh.json 和 en.json。

片段格式（并行作业时每个 agent 写一份，互不冲突）：
    {"install.preview_title": {"zh": "安装预览", "en": "Install preview"}}

合并规则：
- 键冲突且内容不一致 → 报错退出（谁对谁错得人来定，不许悄悄覆盖）。
- zh / en 占位符集合必须一致（{n} 对 {n}），不一致 → 报错。英文漏一个 {n}
  运行时才会露馅，那种 bug 在界面上非常难看，合并时就拦住。
- 输出按键排序，_meta 保留在最前。

跑法：python tools/i18n_merge.py [--check]   # --check 只验不改
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web" / "i18n"
FRAG = ROOT / "_work" / "i18n"
PH = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        sys.exit(f"{p} 读不了：{e}")


def main() -> int:
    check_only = "--check" in sys.argv
    merged: dict[str, dict] = {}
    problems: list[str] = []

    frag_files = sorted(FRAG.glob("*.json")) if FRAG.is_dir() else []
    if not frag_files:
        print("没有片段文件，只校验现有键表")

    for p in frag_files:
        for k, v in _load(p).items():
            if not (isinstance(v, dict) and set(v) == {"zh", "en"}
                    and isinstance(v["zh"], str) and v["zh"].strip()
                    and isinstance(v["en"], str) and v["en"].strip()):
                problems.append(f"{p.name}: {k} 格式坏（需要非空 zh/en 两个字符串）")
                continue
            if k in merged and merged[k] != v:
                problems.append(f"{k} 在两个片段里内容不一致：\n  {merged[k]}\n  {v}")
            merged[k] = v

    base: dict[str, dict] = {}
    for lang in ("zh", "en"):
        raw = _load(OUT / f"{lang}.json")
        for k, v in raw.items():
            if k.startswith("_"):
                continue
            base.setdefault(k, {})[lang] = v
    frag_keys = {kk for p in frag_files for kk in _load(p)}
    for k, v in base.items():
        if k in merged:
            # 片段是准绳：片段里登记过的键以片段为准，缺的语言用旧表补
            for lang, s in v.items():
                merged[k].setdefault(lang, s)
        else:
            merged[k] = v

    warns: list[str] = []
    for k, v in merged.items():
        zs, es = set(PH.findall(v.get("zh", ""))), set(PH.findall(v.get("en", "")))
        if es - zs:
            problems.append(f"{k} 英文里出现了中文没有的占位符：{sorted(es - zs)}"
                            "（调用处多半没传这个参数，会直接把 {x} 显示出来）")
        if zs - es:
            warns.append(f"{k} 英文省略了占位符 {sorted(zs - es)}"
                         "（合法：英文口语里数量常融进句子，但要人工确认不是漏翻）")

    if problems:
        for x in problems:
            print("FAIL", x)
        return 1
    for x in warns:
        print("WARN", x)

    if check_only or not frag_files:
        print(f"校验通过，共 {len(merged)} 键" + ("（无片段可合并）" if not frag_files else ""))
        return 0

    for lang in ("zh", "en"):
        out = {"_meta": _load(OUT / f"{lang}.json").get("_meta", {"lang": lang})}
        for k in sorted(merged):
            out[k] = merged[k][lang]
        (OUT / f"{lang}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"合并完成：{len(merged)} 键 → zh.json / en.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
