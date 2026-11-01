"""列表视图卡片不被压缩的静态断言。

要证明的是：
1. `.mods-grid[data-view="list"] .card` 里有一条「不许压缩」的声明
   （`flex: 0 0 auto` / `flex: none` / `flex-shrink: 0` 都算）；
2. 滚动行为仍在 `.mods-grid` 上（纵向可滚）；
3. 网格视图的 `grid-auto-rows: max-content` 没被顺手删掉；
4. 模组名仍是单行 + 省略号，不会被换行把行高撑开。

为什么是静态断言：这里跑的是纯 Python 环境，没有浏览器可用。
真正压扁卡片的是 CSS 规则本身，规则没了就一定复发，所以盯住规则就够了。

跑法：
    python tests/test_ui_list_scroll.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 下面每条输出都是中文标题；英文版 Windows 的 cp1252 控制台上第一句 print 就会崩
# （CI 抓到过）。这里不导入任何业务模块，所以没有别处帮忙切码页，必须自己切。
from core.console import enable_utf8                              # noqa: E402

enable_utf8()

CSS = ROOT / "web" / "static" / "style.css"

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def rule_body(css: str, selector: str) -> str:
    """取出某个选择器的声明块内容。找不到就返回空串。

    只处理平铺的 CSS（本项目没有嵌套规则），按第一个 `{` 到下一个 `}` 取。
    """
    for m in re.finditer(re.escape(selector) + r"\s*\{", css):
        start = m.end()
        end = css.find("}", start)
        if end > start:
            return css[start:end]
    return ""


def main() -> int:
    if not CSS.is_file():
        print(f"找不到 {CSS}")
        return 1
    css = CSS.read_text(encoding="utf-8")

    print("列表视图的卡片不压缩")
    body = rule_body(css, '.mods-grid[data-view="list"] .card')
    check("选择器存在", bool(body))
    no_shrink = re.search(
        r"flex\s*:\s*(0\s+0\s+auto|none)\b|flex-shrink\s*:\s*0\b", body
    )
    check(
        "卡片写了「不许压缩」（flex: 0 0 auto / flex: none / flex-shrink: 0）",
        bool(no_shrink),
        body.strip()[:200],
    )

    print("滚动还在容器上")
    grid = rule_body(css, ".mods-grid")
    check(".mods-grid 纵向可滚", bool(re.search(r"overflow-y\s*:\s*auto", grid)), grid.strip())
    check("列表视图没把自己变成纵向滚动容器", "overflow-y" not in rule_body(css, '.mods-grid[data-view="list"]'))

    print("网格视图没被顺手改坏")
    gridview = rule_body(css, '.mods-grid[data-view="grid"]')
    check("grid-auto-rows: max-content 仍在", bool(re.search(r"grid-auto-rows\s*:\s*max-content", gridview)), gridview.strip())
    check("网格视图没有跟着加不压缩声明", not re.search(r"flex-shrink\s*:\s*0|flex\s*:\s*0\s+0", gridview))

    print("长模组名仍是单行省略号")
    name = rule_body(css, ".card-name")
    check("不换行", bool(re.search(r"white-space\s*:\s*nowrap", name)), name.strip())
    check("省略号", bool(re.search(r"text-overflow\s*:\s*ellipsis", name)), name.strip())
    check("溢出隐藏", bool(re.search(r"overflow\s*:\s*hidden", name)), name.strip())

    print()
    if fails:
        print(f"{len(fails)} 项不通过：" + "、".join(fails))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
