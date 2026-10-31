"""Nexus 台账测试：ID 解析、版本比较、多路版本来源、手填/清除、程序关于。

全部在临时目录里跑，不联网、不碰真实游戏目录。
跑法：
    python tests/test_track.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import json
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="waass-track-")
    base = Path(tmp)
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods").mkdir()
    data = base / "appdata"
    os.environ.update({
        "WAASS_DATA_DIR": str(data),
        "WAASS_CONFIG_DIR": str(base / "cfg"),
        "WAASS_GAME_DIR": str(game),
        "WAASS_TEST_MODE": "1",
        "WAASS_SANDBOX_DIR": str(base),
        "WAASS_EXTRA_ARCHIVE_DIRS": "",
    })

    from core import appinfo, track
    from core.models import ModRecord

    print("\n[1] 从各种写法里认 Nexus ID")
    cases = {
        "13087": "13087",
        "  4242  ": "4242",
        "https://www.nexusmods.com/witcher3/mods/12144": "12144",
        "https://www.nexusmods.com/witcher3/mods/14578?tab=files&file_id=99": "14578",
        "nexusmods.com/witcher3/mods/9674/": "9674",
        "mods/10172": "10172",
        "ID 是 13357": "13357",
    }
    for raw, want in cases.items():
        try:
            got = track.parse_ref(raw)
        except ValueError as e:
            got = f"<拒绝 {e}>"
        check(f"{raw[:34]!r} → {want}", got == want, str(got))

    print("\n[2] 认不出来的要拒绝，别瞎猜")
    for bad in ("", "abc", "我把地址弄丢了"):
        try:
            track.parse_ref(bad)
            check(f"{bad!r} 应被拒绝", False)
        except ValueError:
            check(f"{bad!r} 被拒绝", True)

    print("\n[3] 版本比较")
    check("1.2 与 1.2.0 相等", track.compare("1.2", "1.2.0") == 0)
    check("1.9.2.1 比 1.9.2 新", track.compare("1.9.2.1", "1.9.2") == 1)
    check("v3.02 比 3.1 新", track.compare("v3.02", "3.1") == 1)
    check("0.3.2 比 0.3 新", track.compare("0.3.2", "0.3") == 1)
    check("8 与 8 相等", track.compare("8", "8") == 0)
    check("5.01.00 和 5.1 数字上相等", track.compare("5.1", "5.01.00") == 0)
    check("空字符串看不出", track.compare("", "1.0") == -9)
    check("不是版本号看不出", track.compare("beta", "1.0") == -9)

    print("\n[4] 版本来源：手填 > 安装档案 > mod 自带")
    mods = [
        ModRecord(key="modA", name="A", folder="modA", version="2.0"),   # 只有自带文件
        ModRecord(key="modB", name="B", folder="modB", version="1.0"),
        ModRecord(key="modC", name="C", folder="modC"),
    ]
    # 安装档案里记着 B 是 3.1.4
    data.mkdir(parents=True, exist_ok=True)
    (data / "installed.json").write_text(json.dumps({"packages": [
        {"id": "x", "name": "B", "state": "active", "version": "3.1.4",
         "mods": ["modB"], "paths": ["mods/modB"]},
    ]}), encoding="utf-8")
    track.apply(mods)
    a, b, c = mods
    check("A 用 mod 自带的版本", (a.version, a.version_src) == ("2.0", "folder"),
          f"{a.version}/{a.version_src}")
    check("B 用安装档案的版本", (b.version, b.version_src) == ("3.1.4", "manifest"),
          f"{b.version}/{b.version_src}")
    check("C 没有版本来源", (c.version, c.track_state) == ("", "unknown"),
          f"{c.version}/{c.track_state}")
    check("没有 ID 就没有页面地址", c.nexus_url == "")

    print("\n[5] 手填 ID 与版本，并且手填优先")
    track.set_manual("modC", "https://www.nexusmods.com/witcher3/mods/4321")
    track.set_manual("modC", version="0.9")
    track.apply(mods)
    check("ID 记上了", c.nexus_id == "4321", str(c.nexus_id))
    check("页面地址拼对", c.nexus_url == "https://www.nexusmods.com/witcher3/mods/4321",
          c.nexus_url)
    check("来源标成手填", (c.id_src, c.version_src) == ("manual", "manual"))
    check("手填版本覆盖其它来源", c.version == "0.9", c.version)

    print("\n[6] Nexus 查到的最新版本参与比较")
    # 模拟「已经从 Nexus 查到 C(4321) 的最新版是 1.0」——不再靠外部赋值，
    # 走真实的缓存文件，跟程序里发生的事一样。
    nd = data / "nexus"
    nd.mkdir(parents=True, exist_ok=True)
    (nd / "4321.json").write_text(json.dumps({
        "mod_id": 4321, "name": "C 的官方标题", "author": "someone",
        "version": "1.0", "category": "Gameplay", "summary": "简介",
        "downloads": 1234, "updated_at": "2026-10-24T12:00:00Z",
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }), encoding="utf-8")
    track.drop_cache()
    track.apply(mods)
    check("0.9 < 1.0 → 可更新", c.update_available and c.track_state == "new",
          f"{c.version}/{c.track_state}")
    check("最新版标成来自 Nexus", (c.latest_version, c.latest_src) == ("1.0", "nexus"),
          f"{c.latest_version}/{c.latest_src}")
    check("作者补上了", c.author == "someone", c.author)
    check("官方标题也记下了", c.name_nexus == "C 的官方标题", c.name_nexus)
    track.set_manual("modC", version="1.0")
    track.apply(mods)
    check("1.0 == 1.0 → 已最新", not c.update_available and c.track_state == "current",
          f"{c.version}/{c.track_state}")

    print("\n[6b] Nexus 资料比旧记录优先")
    # 旧记录说线上是 0.5，Nexus 说 1.0 —— 应该信新的
    from core import legacy
    store = legacy.load()
    store.setdefault("by_name", {})["c"] = {
        "name": "C", "nexus_id": "4321", "remote_version": "0.5",
        "installed_version": "0.2", "last_checked": "2025-01-01"}
    legacy.save(store)
    track.drop_cache()
    track.apply(mods)
    check("挑新的那份", (c.latest_version, c.latest_src) == ("1.0", "nexus"),
          f"{c.latest_version}/{c.latest_src}")

    print("\n[6c] 没有 Nexus 资料时退回旧记录")
    (nd / "4321.json").unlink()
    track.drop_cache()
    track.apply(mods)
    check("用旧记录", (c.latest_version, c.latest_src) == ("0.5", "legacy"),
          f"{c.latest_version}/{c.latest_src}")
    # 恢复 Nexus 那份，后面的用例继续用
    (nd / "4321.json").write_text(json.dumps({
        "mod_id": 4321, "name": "C 的官方标题", "author": "someone",
        "version": "1.0", "updated_at": "2026-10-24T12:00:00Z",
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }), encoding="utf-8")
    track.drop_cache()
    track.apply(mods)

    print("\n[7] 只填版本不填 ID 时，ID 不会被清掉")
    before = c.nexus_id
    track.set_manual("modC", version="1.0.1")
    track.apply(mods)
    check("ID 还在", c.nexus_id == before, str(c.nexus_id))
    check("版本更新了", c.version == "1.0.1", c.version)

    print("\n[8] 清单与统计")
    out = track.rows(mods)
    st = out["stats"]
    check("总数 3", st["total"] == 3, str(st))
    check("有页面 1 个", st["linked"] == 1, str(st["linked"]))
    check("缺 ID 2 个", st["missing"] == 2, str(st["missing"]))
    check("清单带搜索链接", out["rows"][0]["search"].startswith(
        "https://www.nexusmods.com/witcher3/search/"))
    # 1.0.9 起本地高于线上不再算「已最新」，单列 ahead 一档（见 test_version.py）。
    # 这里 C 是 1.0.1、线上 1.0，所以排序是 ahead → 没有 Nexus 页的 A/B。
    check("本地更高的排在最前、没有页面的在后",
          [r["state"] for r in out["rows"]] == ["ahead", "unchecked", "unchecked"],
          str([r["state"] for r in out["rows"]]))
    check("ahead 不算能更新", st["new"] == 0 and st["ahead"] == 1, str(st))

    print("\n[9] 清除")
    track.clear_manual("modC")
    track.apply(mods)
    check("清除后 ID 空", not c.nexus_id, str(c.nexus_id))
    check("清除后版本回到来源推断", c.version_src != "manual", c.version_src)

    print("\n[9b] 更新之后本地版本要跟着变（1.2.4）")
    from core import registry as _reg
    p1 = _reg.parse_archive_name("Hoods 4242 3.7 2026-10-16T13-25Z M3vl4EMry.zip")
    check("空格分隔的包名也认得出来", (p1["nexus_id"], p1["version"]) == (4242, "3.7"), str(p1))
    p2 = _reg.parse_archive_name(
        "Light_Rewrite_12144_0.27.1_2026-10-24T18-32Z_Ukn0RyUwT.zip")
    check("下划线格式没被改坏", (p2["nexus_id"], p2["version"]) == (12144, "0.27.1"), str(p2))
    p3 = _reg.parse_archive_name("Hoods-4242-3-7-1698765432.zip")
    check("老式短横线格式仍然认", (p3["nexus_id"], p3["version"]) == (4242, "3.7"), str(p3))
    p4 = _reg.parse_archive_name("随便一个包.zip")
    check("认不出的不瞎猜", (p4["nexus_id"], p4["version"]) == (None, ""), str(p4))

    # 造一个下载目录：三个「刚下完、还没被安装档案记住」的包
    dl = base / "dl"
    dl.mkdir()
    for name in ("Hoods 4242 3.7 2026-10-16T13-25Z M3vl4EMry.zip",
                 "Light Rewrite 12144 0.28.0 2026-10-29T12-23Z oDUKYVijL.zip",
                 "MoreSkillSlots 12345 1.1.3 2026-10-01T00-00Z aaaaaaaaa.zip"):
        (dl / name).write_bytes(b"PK\x03\x04fake")
    os.environ["WAASS_EXTRA_ARCHIVE_DIRS"] = str(dl)

    past = datetime.now().timestamp() - 3600      # 安装档案是一小时前的快照
    (data / "installed.json").write_text(json.dumps({"packages": [
        {"id": "d", "name": "D", "state": "active", "version": "3.6", "ts": past,
         "mods": ["modD"], "paths": ["mods/modD"]},
        {"id": "e", "name": "E", "state": "active", "version": "0.27.1", "ts": past,
         "mods": ["modE"], "paths": ["mods/modE"]},
        {"id": "g", "name": "G", "state": "active", "version": "1.1.3", "ts": past,
         "mods": ["modG"], "paths": ["mods/modG"]},
    ]}), encoding="utf-8")
    track.drop_cache()

    fresh = [
        ModRecord(key="modD", name="D", folder="modD", nexus_id="4242"),
        ModRecord(key="modE", name="E", folder="modE", nexus_id="12144"),
        # REDkit 包的 info.json 写着 1.1.3，是磁盘上的真相
        ModRecord(key="modF", name="F", folder="modF", version="1.1.3",
                  version_file="info"),
        # 有的作者从不更新 info.json 的 version（实测有个写着 0.1 而实际是 1.1.3）
        ModRecord(key="modG", name="G", folder="modG", nexus_id="12345",
                  version="0.1", version_file="info"),
    ]
    track.apply(fresh)
    d, e, f, g = fresh
    check("装完又下了新包 → 用新包的版本", (d.version, d.version_src) == ("3.7", "archive"),
          f"{d.version}/{d.version_src}")
    check("同一件事的下划线包名同理", (e.version, e.version_src) == ("0.28.0", "archive"),
          f"{e.version}/{e.version_src}")
    check("mod 自带 info.json 优先于没时间戳的旧记录",
          (f.version, f.version_src) == ("1.1.3", "info"), f"{f.version}/{f.version_src}")
    check("info.json 明显偏低时不采信，退回安装档案/下载包",
          (g.version, g.version_src) == ("1.1.3", "archive"), f"{g.version}/{g.version_src}")
    os.environ.pop("WAASS_EXTRA_ARCHIVE_DIRS", None)
    (data / "installed.json").unlink()
    track.drop_cache()

    print("\n[10] 关于程序")
    ab = appinfo.info(game, data, {"mods": 3})
    check("有版本号", bool(ab["version"]), str(ab))
    check("许可证是 MIT", ab["license"] == "MIT")
    check("仓库地址已填", ab["repo"].startswith("https://github.com/"), ab["repo"])
    # 1.3.0 起内置了默认更新源，所以「没配」这条只剩关掉检查的情况。
    # 别在这里断言联网结果 —— CI 可能没有外网。
    (data / "config.json").write_text(
        json.dumps({"update_check": False}), encoding="utf-8")
    chk = appinfo.check()
    check("关掉检查时如实说没源", chk["ok"] is False and chk["configured"] is False,
          str(chk))
    check("提示里说明检查已关闭", "关闭" in chk["message"], chk["message"])

    # 本地放一个 JSON 更新源，验证真能比对
    feed = base / "version.json"
    feed.write_text(json.dumps({"version": "9.9.9", "url": "https://example.com/dl"}),
                    encoding="utf-8")
    (data / "config.json").write_text(
        json.dumps({"update_feed": feed.as_uri()}), encoding="utf-8")
    chk2 = appinfo.check()
    check("指向本地更新源时能判出新版", chk2.get("newer") is True, str(chk2))
    check("拿到了下载页", chk2.get("url") == "https://example.com/dl", str(chk2))

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：" + "、".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
