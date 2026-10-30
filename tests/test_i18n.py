"""双语键表的完整性测试。

跑法：python tests/test_i18n.py

查四件事：
1. zh / en 两份键表键集合完全一致 —— 缺一个，英文界面就会露出键名。
2. 没有空值。
3. 英文没有中文没有的占位符（调用处没传那个参数，会把 {x} 原样显示出来）。
4. 英文值里没有中文残留 —— 有就是漏翻或复制错了行。

5、6 两件是运行时行为：缺键返回键名不抛异常；两种语言都能取到 admin 最
常用的那条（真去读 JSON，而不只是查表）。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import os
os.environ.setdefault("WAASS_DATA_DIR", str(Path(__file__).parent / "_tmp_i18n"))

from core import i18n                                     # noqa: E402

PH = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
CJK = re.compile(r"[\u4e00-\u9fff]")
DIR = ROOT / "web" / "i18n"
fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  OK   " if ok else "  FAIL ") + name + ("" if ok else f"   {detail}"))
    if not ok:
        fails.append(name)


def main() -> int:
    zh = json.loads((DIR / "zh.json").read_text(encoding="utf-8"))
    en = json.loads((DIR / "en.json").read_text(encoding="utf-8"))
    zk = {k for k in zh if not k.startswith("_")}
    ek = {k for k in en if not k.startswith("_")}

    check("两份键表键集合一致", zk == ek,
          f"只缺在 zh：{sorted(zk - ek)[:8]}；只缺在 en：{sorted(ek - zk)[:8]}")
    empty = [k for k, d in (("zh", zh), ("en", en)) for k, v in d.items()
             if not k.startswith("_") and not str(v).strip()]
    check("没有空值", not empty, str(empty[:8]))

    bad_ph = [k for k in zk if set(PH.findall(str(en.get(k, "")))) - set(PH.findall(str(zh[k])))]
    check("英文没有中文没有的占位符", not bad_ph, str(bad_ph[:8]))

    cjk_in_en = [k for k in ek if CJK.search(str(en[k]))]
    check("英文值里没有中文残留", not cjk_in_en, str(cjk_in_en[:8]))

    zh_fmt = {k: v for k, v in zh.items() if not k.startswith("_")}
    en_fmt = {k: v for k, v in en.items() if not k.startswith("_")}
    i18n._cache["zh"] = zh_fmt
    i18n._cache["en"] = en_fmt

    i18n.set_lang("zh")
    check("缺键返回键名不抛异常", i18n.t("no.such.key") == "no.such.key")
    check("占位符替换（中文）", i18n.t("ui.pick_selected", n=3) == "已选 3 个")

    i18n.set_lang("en")
    check("切换后取到英文", i18n.t("ui.import") == "Import archive")
    check("切换后缺键同样兜底", i18n.t("no.such.key") == "no.such.key")

    # 单复数对：每个 *_one 必须有对应 *_many，且 tn(1)/tn(2) 各取对的一边
    ones = [k[:-4] for k in zk if k.endswith("_one")]
    missing_many = [k for k in ones if k + "_many" not in zk]
    check("单复数键成对（共 %d 对）" % len(ones), not missing_many, str(missing_many[:6]))
    i18n.set_lang("zh")
    # tn 会把 {n} 格式化掉，所以期望值 = 模板里 {n} 换成数字后的样子
    def exp(raw: str, n: int) -> str:
        return raw.replace("{n}", str(n))
    ok_pair = all(i18n.tn(k + "_one", k + "_many", 1) == exp(zh_fmt[k + "_one"], 1)
                  and i18n.tn(k + "_one", k + "_many", 2) == exp(zh_fmt[k + "_many"], 2)
                  for k in ones)
    check("中文单复数两边取值正确", ok_pair)

    check("两种语言键数都在 900 以上", len(zk) >= 900 and len(ek) >= 900,
          f"zh={len(zk)} en={len(ek)}")

    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
