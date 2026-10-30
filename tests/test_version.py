"""版本比对的测试：规则、主文件挑选、「本地更高」的判定。

背景：1.0.8 之前「最新版」取的是 Nexus 页面上作者手填的 `version`，
它在 17 个 mod 里有 3 个落后于作者自己上传的主文件（差 1～2 个热修版）。
结果是界面把「已是最新」显示成「本地比线上还高」，看着像坏了。

这个文件把三件事钉住：
1. 版本字符串怎么比（一套规则，三处共用）
2. 从文件清单里挑哪个才算「最新版」
3. 本地高于线上时归到 ahead 这一档，不混进 current

跑法：
    python tests/test_version.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="waass-ver-"))
    game = tmp / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods").mkdir()
    data = tmp / "appdata"
    data.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "WAASS_DATA_DIR": str(data),
        "WAASS_CONFIG_DIR": str(tmp / "cfg"),
        "WAASS_GAME_DIR": str(game),
        "WAASS_TEST_MODE": "1",
        "WAASS_SANDBOX_DIR": str(tmp),
        "WAASS_EXTRA_ARCHIVE_DIRS": "",
    })

    from core import nexus_meta, track
    from core.version import compare, newer, vkey
    from core.models import ModRecord

    print("\n[1] 版本号规则")
    check("1.2 与 1.2.0 相等", compare("1.2", "1.2.0") == 0)
    check("1.9.2.1 比 1.9.2 新", compare("1.9.2.1", "1.9.2") == 1)
    check("2.11.0 比 2.10.1 新（不是按字符串比）", compare("2.11.0", "2.10.1") == 1)
    check("v3.02 比 3.1 新", compare("v3.02", "3.1") == 1)
    check("5.01.00 与 5.1 相等", compare("5.1", "5.01.00") == 0)
    check("8 与 8 相等", compare("8", "8") == 0)
    check("0.3beta 比 0.3 旧（预发布算更旧）", compare("0.3beta", "0.3") == -1)
    check("0.3.2 比 0.3 新", compare("0.3.2", "0.3") == 1)
    check("空字符串看不出 → -9", compare("", "1.0") == -9)
    check("beta 抠不出数字 → -9", compare("beta", "1.0") == -9)
    check("vkey 补齐到 4 段", vkey("1.9") == (1, 9, 0, 0, 1))
    check("newer() 判断不了返回 None", newer("", "1.0") is None)
    check("newer() 有新版返回 True", newer("1.9.2.1", "1.9.2") is True)

    print("\n[2] 从文件清单里挑「最新版」")
    # 真实数据：Switchable Cloaks（10172），页面写 1.9.2，主文件是 1.9.2.1
    files = [
        {"name": "Localization", "version": "1.10", "category": "MISCELLANEOUS",
         "primary": 0, "date": 1759_000_000},
        {"name": "Cloaks REMASTERED", "version": "1.9.2", "category": "OLD_VERSION",
         "primary": 0, "date": 1758_000_000},
        {"name": "Cloaks REMASTERED", "version": "1.9.2.1", "category": "MAIN",
         "primary": 0, "date": 1758_500_000},
        {"name": "Cloaks NEXT-GEN", "version": "1.9.2.1", "category": "MAIN",
         "primary": 1, "date": 1759_500_000},
    ]
    got = nexus_meta._pick_main(files)
    check("取 MAIN 而不是版本号更大的方言包（1.10）",
          got.get("main_version") == "1.9.2.1", str(got))
    check("记下了主文件名", got.get("main_file") == "Cloaks NEXT-GEN", str(got))
    check("文件总数也对", got.get("main_files") == 4, str(got))

    # 作者重传了旧版本 → 时间变新，但版本号没变，不能因此说旧版是最新
    files2 = [
        {"name": "old", "version": "1.4", "category": "MAIN", "primary": 0,
         "date": 1759_900_000},
        {"name": "new", "version": "1.5", "category": "MAIN", "primary": 1,
         "date": 1758_000_000},
    ]
    got2 = nexus_meta._pick_main(files2)
    check("重传的旧文件不抢「最新」", got2.get("main_version") == "1.5", str(got2))

    # 没有 MAIN 分类时退回 primary，再退回全部
    files3 = [
        {"name": "patch", "version": "2.1", "category": "OPTIONAL", "primary": 0},
        {"name": "patch2", "version": "2.0", "category": "OPTIONAL", "primary": 0},
    ]
    check("没有 MAIN 就退回全部文件", nexus_meta._pick_main(files3).get("main_version") == "2.1")
    check("一个文件都没有 → 空", nexus_meta._pick_main([]) == {})

    print("\n[3] 本地版本 vs 主文件版本")
    cache_dir = data / "nexus"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def put(mid: str, page: str, main: str) -> None:
        (cache_dir / f"{mid}.json").write_text(json.dumps({
            "mod_id": int(mid), "name": f"Mod {mid}", "version": page,
            "main_version": main, "main_file": "main.7z",
            "author": "x", "fetched_at": "2026-10-27T00:00:00+08:00",
        }, ensure_ascii=False), encoding="utf-8")

    put("1001", "1.9.2", "1.9.2.1")     # 页面旧、主文件新，本地跟着主文件
    put("1002", "1.9.2", "1.9.2.1")     # 同上，但本地是旧的 → 应该报能更新
    put("1003", "1.0", "1.0")           # 本地自己改高了 → ahead
    put("1004", "1.1", "")              # 没有主文件版本，退回页面值

    mods = [
        ModRecord(key="modA", name="A", folder="modA", nexus_id="1001", version="1.9.2.1"),
        ModRecord(key="modB", name="B", folder="modB", nexus_id="1002", version="1.9.2"),
        ModRecord(key="modC", name="C", folder="modC", nexus_id="1003", version="1.0.9"),
        ModRecord(key="modD", name="D", folder="modD", nexus_id="1004", version="1.1"),
    ]
    track.drop_cache()
    track.apply(mods)
    by = {m.key: m for m in mods}

    check("A 本地=主文件版本，判为已最新", by["modA"].track_state == "current",
          by["modA"].track_state)
    check("A 不会因为页面写成 1.9.2 就报落后", by["modA"].update_available is False)
    check("A 记下了页面值备查", by["modA"].page_version == "1.9.2", by["modA"].page_version)
    check("A 用的是主文件版本", by["modA"].latest_version == "1.9.2.1",
          by["modA"].latest_version)

    check("B 落后一个热修 → 报能更新", by["modB"].update_available is True)
    check("B 最新版取 1.9.2.1", by["modB"].latest_version == "1.9.2.1",
          by["modB"].latest_version)

    check("C 本地比线上高 → ahead，不混进 current", by["modC"].track_state == "ahead",
          by["modC"].track_state)
    check("C 不算「可更新」", by["modC"].update_available is False)

    check("D 没有主文件版本时退回页面值", by["modD"].latest_version == "1.1",
          by["modD"].latest_version)
    check("D 判为已最新", by["modD"].track_state == "current", by["modD"].track_state)

    rows = track.rows(mods)
    st = rows["stats"]
    check("统计里 ahead 单独一档", st.get("ahead") == 1, str(st))
    check("统计里 current 是 2", st.get("current") == 2, str(st))
    check("行里带出 latest_page", any(r.get("latest_page") == "1.9.2" for r in rows["rows"]))
    check("ahead 排在「能更新」之后、未知之前",
          [r["key"] for r in rows["rows"]][:3] == ["modB", "modC", "modA"],
          str([r["key"] for r in rows["rows"]]))

    print("\n" + ("全部通过" if not fails else f"失败的 {len(fails)} 项"))
    for f in fails:
        print("   ×", f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
