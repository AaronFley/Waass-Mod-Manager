"""认出模组的 Nexus ID：只用本地证据，宁可空着也不猜。

这块是全项目最容易「看起来在工作、其实在瞎猜」的地方 —— 绑错 ID 会让
「检查更新」一直报别人的版本，比没有 ID 还糟。所以测试重点不是「能认出多少」，
而是**该沉默的时候必须沉默**。

跑法：
    python tests/test_identify.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="waass-identify-")
os.environ["WAASS_DATA_DIR"] = str(Path(_TMP) / "data")   # 必须在 import core 之前
os.environ["WAASS_TEST_MODE"] = "1"

from core import identify  # noqa: E402


def pkg(name: str, nexus_id: str, version: str = "") -> dict:
    return {"name": name, "version": version, "nexus_id": nexus_id}


def main() -> int:
    fails = []

    def check(name, cond, extra=""):
        print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"  {extra}"))
        if not cond:
            fails.append(name)

    real_packages = identify.packages
    try:
        print("\n[1] 名字拆词（文件夹是连写、Nexus 标题是分词）")
        check("去 mod 前缀", identify.normalize("mod_SwordsOnHip") == "swordsonhip",
              identify.normalize("mod_SwordsOnHip"))
        check("驼峰拆开", identify.tokenize("modSwordsOnHip") == ["sword", "on", "hip"],
              identify.tokenize("modSwordsOnHip"))
        check("复数归并", identify.tokenize("Switchable Cloaks - Hoods")
              == ["switchable", "cloak", "hood"], identify.tokenize("Switchable Cloaks - Hoods"))
        check("版本号不算名字的一部分",
              identify.tokenize("ResponsiveMovement 1.7.5") == ["responsive", "movement"],
              identify.tokenize("ResponsiveMovement 1.7.5"))
        check("搜索词也拆好", identify.search_terms("modSwordsOnHip") == "sword on hip",
              identify.search_terms("modSwordsOnHip"))

        print("\n[2] 差几个词（这比分数可靠）")
        check("同名差 0", identify._word_diff("Hoods", "Hoods") == 0)
        check("Hoods / HoodSlots 差 1", identify._word_diff("Hoods", "HoodSlots") == 1)
        check("RoninArmor / dlcRoninArmor 差 1",
              identify._word_diff("RoninArmor", "dlcRoninArmor") == 1)
        check("多两个词差 2",
              identify._word_diff("Swords On Hip", "Swords On Hip When Cloaked") == 2)
        check("毫无关系 → None", identify._word_diff("AutoLoot", "Light Rewrite") is None)

        print("\n[3] 唯一且对得上 → 自动绑")
        identify.packages = lambda: [pkg("Hoods", "4242", "3.7"),
                                     pkg("HoodSlots", "14254", "1.4")]
        check("Hoods 差 0 + 版本吻合", identify.guess("modHoods", "Hoods", "3.7") == ("4242", "package"))
        check("HoodSlots 差 0", identify.guess("mod_HoodSlots", "Hood Slots", "1.4")
              == ("14254", "package"))
        check("认出来就记住（下次不用再算）", (identify.stored("modHoods") or {}).get("id") == "4242")

        print("\n[4] 有歧义就闭嘴（宁可空着）")
        identify.packages = lambda: [pkg("Swords On Hip When Cloaked", "9974", "1.0"),
                                     pkg("Dynamic Swords (and Crossbow) On Hip", "13583", "1.2")]
        check("两个都差 2 个以上 → 不绑",
              identify.guess("modSwordsOnHip", "SwordsOnHip", "") == ("", ""))
        check("但候选里能看到（给人挑）",
              {c["nexus_id"] for c in identify.candidates("SwordsOnHip")} >= {"9974", "13583"},
              identify.candidates("SwordsOnHip"))

        print("\n[5] 同名别家版本 / 翻译版不自动绑")
        identify.packages = lambda: [pkg("SmartAutoLoot Remaster V1.1.7", "13035", "1.1.7")]
        check("名字差得远（Smart 前缀）→ 不绑",
              identify.guess("modAutoLoot", "AutoLoot", "") == ("", ""))
        identify.packages = lambda: [pkg("Hoods - Chinese", "8888", "3.7")]
        check("只有中文翻译版时，不拿它当本体",
              identify.guess("modHoods2", "Hoods", "3.7") == ("", ""))
        identify.packages = lambda: [pkg("Hoods - Chinese", "8888", "3.7"),
                                     pkg("Hoods", "4242", "3.7")]
        check("本体和翻译版同时存在 → 选本体",
              identify.guess("modHoods3", "Hoods", "3.7") == ("4242", "package"))

        print("\n[6] 安装档案优先于下载目录")
        identify.packages = lambda: [pkg("Hoods", "1111", "3.7")]
        real_registry_load = None
        try:
            from core import registry
            real_registry_load = registry.load
            registry.load = lambda: {"packages": [
                {"nexus_id": 4242, "version": "3.7", "mods": ["modFromArch"],
                 "paths": [], "ts": 0}]}
            check("装过的话以档案为准",
                  identify.guess("modFromArch", "SomethingElse", "") == ("4242", "registry"))
        finally:
            if real_registry_load is not None:
                registry.load = real_registry_load

        print("\n[7] 下载目录读不出来时不能把扫描搞崩")
        def boom():
            raise OSError("模拟下载目录不存在")
        identify.packages = real_packages      # 先把上面几个假包换回真的
        identify.drop_cache()
        real_scan = None
        try:
            from core import downloads
            real_scan = downloads.scan
            downloads.scan = boom
            identify.drop_cache()
            check("packages() 返回空表", identify.packages() == [])
            check("guess() 返回空而不是抛",
                  identify.guess("modNobody", "Nobody", "") == ("", ""))
            check("candidates() 返回空表", identify.candidates("Nobody") == [])
        finally:
            if real_scan is not None:
                downloads.scan = real_scan
            identify.drop_cache()

        print("\n[8] 记住 / 忘掉")
        identify.remember("modX", "12345", "package")
        check("记下来了", (identify.stored("modX") or {}).get("id") == "12345")
        identify.packages = lambda: []
        check("包全删了也还记得", identify.guess("modX", "X", "") == ("12345", "stored"))
        check("forget 之后回到空", identify.forget("modX") and not identify.stored("modX"))
        identify.remember("modBad", "abc", "package")
        check("非数字 ID 不记", identify.stored("modBad") is None)

        print("\n[9] 联网候选筛选")
        hits = [
            {"mod_id": 4242, "name": "Hoods", "author": "A", "version": "3.7", "downloads": 900},
            {"mod_id": 8732, "name": "Cat Hood Fix", "author": "B", "version": "1.0", "downloads": 50},
            {"mod_id": 8888, "name": "Hoods - Chinese", "author": "C", "version": "3.7", "downloads": 10},
        ]
        got = identify.shortlist(hits, "Hoods", "3.7")
        ids = [x["mod_id"] for x in got]
        check("正主排第一", ids and ids[0] == "4242", got)
        check("翻译版排在正主之后", "8888" not in ids or ids.index("8888") > ids.index("4242"), ids)
        check("候选里最高分也只可能来自正主",
              max(got, key=lambda x: x["score"])["mod_id"] == "4242", got)

        print("\n[10] 两条来源合并")
        local = [{"nexus_id": "13035", "score": 53, "pkg_name": "SmartAutoLoot Remaster",
                  "pkg_version": "1.1.7"}]
        remote = [{"mod_id": "11199", "score": 80, "name": "Just Auto Loot", "author": "Teamy",
                   "version": "2.0", "downloads": 500, "url": "u"},
                  {"mod_id": "13035", "score": 40, "name": "SmartAutoLoot Remaster",
                   "author": "X", "version": "1.1.7", "downloads": 900, "url": "u2"}]
        merged = identify.merge_candidates(local, remote)
        by_id = {m["mod_id"]: m for m in merged}
        check("两路都命中的合成一条并标 both", by_id["13035"]["src"] == "both",
              by_id.get("13035"))
        check("取两边较高的分", by_id["13035"]["score"] == 53, by_id["13035"]["score"])
        # 排序先看证据等级：两路都对上的（both）一定比你只从 Nexus 搜到的高。
        # 反例是 AutoLoot —— 正主分数只有 46，按纯分数排会被高下载量的同名 mod 压下去。
        check("两路都对上的排第一", merged[0]["mod_id"] == "13035", [m["mod_id"] for m in merged])
        check("只搜到的排在后面", [m["mod_id"] for m in merged] == ["13035", "11199"],
              [m["mod_id"] for m in merged])
        check("合并后不重复", len(merged) == len({m["mod_id"] for m in merged}))

        print("\n[11] 搜索关键词要能逐档放宽")
        qs = identify.search_queries("AutoLoot Enhanced")
        check("第一档是全词", qs and qs[0] == "auto loot enhanced", qs)
        # Nexus 那边是多词 AND：`auto loot enhanced` 一条都搜不到，`auto loot` 才有。
        # 只发一档等于这批模组永远空着候选。
        check("去掉包装词后还有一档", "auto loot" in qs, qs)
        check("档位不重复", len(qs) == len(set(qs)), qs)
        check("单词模组也能用", identify.search_queries("Hoods")[0] == "hood",
              identify.search_queries("Hoods"))
        check("包装词不会被单独拿去搜", all(q.strip() not in identify._FILLER for q in qs), qs)

        print("\n[12] 包装词、版本前缀不该算成「多出来的词」")
        toks = identify.tokenize("SmartAutoLoot Remaster V1.1.7")
        check("V1.1.7 不是一个词", "v1" not in toks, toks)
        # 真事：AutoLoot Enhanced ↔ SmartAutoLoot Remaster。enhanced 是包装词不扣分，
        # 但 smart/remaster 是真的名字差异 —— 所以给候选、不自动绑。
        check("enhanced 不算差异",
              identify._word_diff("AutoLoot Enhanced", "SmartAutoLoot Remaster") == 2,
              identify._word_diff("AutoLoot Enhanced", "SmartAutoLoot Remaster"))
        check("remaster 仍算差异（重制版可能另开页面）",
              identify._word_diff("Hoods", "Hoods Remastered") == 1,
              identify._word_diff("Hoods", "Hoods Remastered"))
        check("泛词不再把分数扣到候选线以下",
              identify._word_score("AutoLoot Enhanced", "SmartAutoLoot Remaster")
              >= identify._MIN_SCORE,
              identify._word_score("AutoLoot Enhanced", "SmartAutoLoot Remaster"))

    finally:
        identify.packages = real_packages

    print(f"\n失败的 {len(fails)} 项")
    for f in fails:
        print("   ×", f)
    if not fails:
        print("全部通过")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
