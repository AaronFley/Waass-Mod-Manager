"""旧资料固化 + 存储清理的测试。

重点验证两件事：
1. 资料只从外部管理器搬一次，之后删掉它也能照常用（这是「独立运行」的证明）；
2. 清理只碰 data 目录里白名单内的东西，核心数据和危险项不会误删。

全部在临时目录里跑。
跑法：
    python tests/test_legacy_storage.py
"""
from __future__ import annotations

import json
import os
import shutil
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


def make_rmmr(game: Path) -> Path:
    d = game / "RMMR Mod Manager"
    (d / "thumbnails").mkdir(parents=True)
    (d / "thumbnails" / "swords.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 500)
    (d / "installed.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        "<mods>\n"
        '  <mod name="Swords On Hip" enabled="true" priority="110">\n'
        "    <data>modSwordsOnHip</data>\n"
        "    <dlc>dlcSwordsOnHip</dlc>\n"
        '    <menu path="D:\\game\\bin\\config\\r4game\\user_config_matrix\\pc\\modSwordsOnHip.xml"/>\n'
        "  </mod>\n"
        "</mods>\n", encoding="utf-8")
    (d / "mod_metadata.json").write_text(json.dumps({
        "Swords On Hip": {
            "nexus_mod_id": 13357,
            "author": "Gervant",
            "category": "Gameplay Changes",
            "installed_version": "1.2",
            "remote_version": "1.3",
            "last_checked": 1761177600,
            "thumbnail_path": "thumbnails/swords.png",
        }
    }), encoding="utf-8")
    return d


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="waass-ls-")
    base = Path(tmp)
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods" / "modSwordsOnHip").mkdir(parents=True)
    (game / "mods" / "modSwordsOnHip" / "content").mkdir()
    data = base / "appdata"
    data.mkdir()
    cfg = base / "cfg"
    cfg.mkdir()

    os.environ.update({
        "WAASS_DATA_DIR": str(data),
        "WAASS_CONFIG_DIR": str(cfg),
        "WAASS_GAME_DIR": str(game),
        "WAASS_TEST_MODE": "1",
        "WAASS_SANDBOX_DIR": str(base),
        "WAASS_EXTRA_ARCHIVE_DIRS": "",
    })

    from core import guard, legacy, scanner, storage

    rmmr = make_rmmr(game)

    print("\n[1] 第一次扫描：从外部管理器搬一次")
    res = scanner.scan(game)
    s = legacy.summary()
    check("写下了固化时间", s["done"] is True, str(s))
    check("搬到了 1 条资料", s["count"] == 1, str(s))
    check("扫描结果带上了 Nexus ID",
          any(m.nexus_id == "13357" for m in res.mods),
          str([(m.key, m.nexus_id) for m in res.mods]))
    check("显示名用了它记录的名字",
          any(m.name == "Swords On Hip" for m in res.mods))
    check("封面复制到了自己的目录",
          (data / "thumbs" / "swords.png").is_file())
    # 1.0.10 起不再往界面报「已搬 N 条」：那是一次性事件，导入入口也撤了。
    check("搬家不再打扰界面（没有 legacy_done 提示）",
          not any(w.code == "legacy_done" for w in res.warnings),
          str([w.code for w in res.warnings]))

    print("\n[2] 第二次扫描：不重复搬")
    at = s["at"]
    res2 = scanner.scan(game)
    check("固化时间没变", legacy.summary()["at"] == at)
    check("不再重复提示",
          not any(w.code == "legacy_done" for w in res2.warnings))

    print("\n[3] 把外部管理器整个删掉，程序照常工作")
    shutil.rmtree(rmmr)
    res3 = scanner.scan(game)
    ids = [m.nexus_id for m in res3.mods]
    names = [m.name for m in res3.mods]
    check("ID 还在", "13357" in ids, str(ids))
    check("名字还在", "Swords On Hip" in names, str(names))
    check("作者还在", any(m.author == "Gervant" for m in res3.mods))
    check("封面还在", any(m.thumbnail.endswith("swords.png") for m in res3.mods),
          str([m.thumbnail for m in res3.mods]))
    check("latest 版本按旧记录填上",
          any(m.latest_version == "1.3" for m in res3.mods))

    print("\n[4] 版本来源标成「历史记录」而不是别人的名字")
    from core import track
    from core.models import ModRecord
    m = ModRecord(key="modSwordsOnHip", name="Swords On Hip", folder="modSwordsOnHip")
    track.drop_cache()
    track.apply([m])
    check("本地版本来自历史记录", m.version == "1.2" and m.version_src == "legacy",
          f"{m.version}/{m.version_src}")
    check("来源中文名是「历史记录」", track.version_src_cn("legacy") == "历史记录")

    print("\n[5] 强制重导 / 清空存档")
    make_rmmr(game)
    out = legacy.ensure_migrated(game, force=True)
    check("强制重导后有 1 条", out["count"] == 1)
    # 1.0.10 起界面上的「导入 / 清空」入口已经撤掉，只保留内部能力：
    # 清空＝直接写一份空存档，语义和原来的 forget() 一样。
    legacy.save(legacy._empty())
    check("清空后没有固化时间", legacy.summary()["done"] is False)
    check("清空后扫描仍能跑（只是没有旧资料）",
          isinstance(scanner.scan(game).mods, list))

    print("\n[6] 存储统计")
    # 造点占用
    (data / "webview" / "Cache").mkdir(parents=True, exist_ok=True)
    (data / "webview" / "Cache" / "blob.bin").write_bytes(b"a" * 200_000)
    (data / "quarantine" / "pkg1").mkdir(parents=True, exist_ok=True)
    (data / "quarantine" / "pkg1" / "old.pak").write_bytes(b"q" * 120_000)
    (data / "archives").mkdir(parents=True, exist_ok=True)
    (data / "archives" / "keep.zip").write_bytes(b"z" * 80_000)
    (data / "config.json").write_text('{"game_root": "x"}', encoding="utf-8")
    (data / "dead.json").write_text("{}", encoding="utf-8")
    (data / "updates.json").write_text("{}", encoding="utf-8")
    (data / "nexus_cache.json").write_text("{}", encoding="utf-8")

    rep = storage.report()
    ids = {g["id"]: g for g in rep["groups"]}
    check("识别到浏览器缓存", "webview" in ids, str(list(ids)))
    check("识别到隔离区", ids.get("quarantine", {}).get("bytes") == 120_000,
          str(ids.get("quarantine")))
    check("识别到保留原包", ids.get("archives", {}).get("bytes") == 80_000)
    check("识别到废弃缓存", "dead" in ids)
    check("总占用大于各部分之和", rep["total_bytes"] >= 400_000, str(rep["total_bytes"]))
    check("核心数据里有 config.json",
          any(c["name"] == "config.json" for c in rep["core"]), str(rep["core"]))

    print("\n[7] 一键清理只动「随便删」的那一类")
    r = storage.clean(only_safe=True)
    check("确实释放了空间", r["freed"] >= 200_000, str(r["freed"]))
    check("浏览器缓存被清", not (data / "webview").exists())
    check("废弃缓存被清", not (data / "updates.json").exists())
    check("隔离区没被动", (data / "quarantine" / "pkg1" / "old.pak").is_file())
    check("保留原包没被动", (data / "archives" / "keep.zip").is_file())
    check("配置没被动", (data / "config.json").is_file())

    print("\n[8] 单独清理危险项（需要显式点名）")
    r2 = storage.clean(["quarantine"])
    check("隔离区被清", not (data / "quarantine").exists(), str(r2))
    check("保留原包仍然在", (data / "archives" / "keep.zip").is_file())

    print("\n[9] 越界保护：data 外面的东西一个字节都不碰")
    outside = base / "outside.txt"
    outside.write_text("do not touch", encoding="utf-8")
    got = storage._remove(outside)                      # noqa: SLF001 故意走内部函数
    check("拒绝删 data 外的文件", got == 0 and outside.is_file())
    got2 = storage._remove(data)                        # 连 data 目录本身也不行
    check("拒绝删 data 目录本身", got2 == 0 and data.is_dir())

    print("\n[10] 快照自动只留最近 N 份")
    for i in range(5):
        d = data / "snapshots" / f"2026-10-2{i}T10-00-00"
        d.mkdir(parents=True, exist_ok=True)
        (d / "f.txt").write_text("x", encoding="utf-8")
    removed = storage.prune_snapshots(2)
    left = sorted(p.name for p in (data / "snapshots").iterdir())
    check("删掉了 3 个旧快照", removed == 3, str(removed))
    check("留下的是最新的 2 个",
          left == ["2026-10-23T10-00-00", "2026-10-24T10-00-00"], str(left))

    print("\n[11] 只读模式下清理会拒绝")
    os.environ["WAASS_READONLY"] = "1"
    try:
        (data / "webview" / "Cache").mkdir(parents=True, exist_ok=True)
        (data / "webview" / "Cache" / "b.bin").write_bytes(b"b" * 1000)
        storage.clean(only_safe=True)
        check("只读时没有删东西", (data / "webview" / "Cache" / "b.bin").is_file())
    finally:
        os.environ.pop("WAASS_READONLY", None)
    guard.deny_all()

    shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("全部通过" if not fails else f"失败 {len(fails)} 项：{fails}"))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
