"""清理功能的边界测试 —— 专盯「绝不能删」的那几个目录。

背景（2026-10-27）：
1.2.0 加了一键汉化，汉化前的原件存在 data/localize/orig/，是「还原成英文」
唯一的数据源。它和快照不同 —— 快照只留 30 份会自动淘汰，原件永不覆盖、
永不清理。所以这个目录必须彻底不在清理清单里，连「风险=谨慎」那一档都不能出现。

这个文件只验证边界，不验证清理本身能不能腾出空间（那是 test_legacy_storage 的事）。

跑法：
    python tests/test_storage_safety.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="waass-stg-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_SANDBOX)
os.environ["WAASS_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WAASS_CONFIG_DIR"] = str(_SANDBOX / "cfg")

from core import storage                     # noqa: E402
from core.paths import DATA_DIR              # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def seed() -> None:
    """造一份典型的数据目录：核心文件 + 各清理项 + 汉化原件。"""
    (DATA_DIR / "localize" / "orig" / "mods" / "modA" / "content").mkdir(parents=True)
    (DATA_DIR / "localize" / "orig" / "mods" / "modA" / "content" /
     "cn.w3strings").write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + b"x" * 400)
    hist = DATA_DIR / "localize" / "history" / "20261027-101010" / "mods" / "modA" / "content"
    hist.mkdir(parents=True)
    (hist / "cn.w3strings").write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + b"y" * 400)
    (DATA_DIR / "localize" / "index.json").write_text("[]", encoding="utf-8")

    for rel in ("thumbs", "snapshots", "webview", "localize_backups", "quarantine"):
        d = DATA_DIR / rel
        d.mkdir(parents=True, exist_ok=True)
        (d / "junk.bin").write_bytes(b"z" * 2048)

    (DATA_DIR / "config.json").write_text('{"game_root": "X:\\\\game"}', encoding="utf-8")
    (DATA_DIR / "track.json").write_text("{}", encoding="utf-8")


def main() -> int:
    seed()
    orig = DATA_DIR / "localize" / "orig" / "mods" / "modA" / "content" / "cn.w3strings"

    print("[1] 清单里不能出现汉化原件")
    check("localize 在 KEEP_DIRS 里", "localize" in storage.KEEP_DIRS, str(storage.KEEP_DIRS))
    ids = [g["id"] for g in storage.GROUPS]
    rels = [p for g in storage.GROUPS for p in g["paths"]]
    check("没有任何清理项指向 localize", not any(r.split("/")[0] == "localize"
                                              and r != "localize_backups" for r in rels),
          str(rels))
    check("旧版备份项还在（1.1 的目录要能清）", "localize_backups" in ids, str(ids))

    print("[2] report 只统计、不提供删除")
    rep = storage.report()
    # 注意：这里必须比「data 目录下的第一段名字 == localize」，不能用子串匹配
    # —— localize_backups 是旧版目录，本来就该留在清单里。
    def _top(p: str) -> str:
        try:
            return Path(p).relative_to(DATA_DIR).parts[0]
        except (ValueError, IndexError):
            return ""
    localize_rows = [g for g in rep["groups"]
                     if any(_top(p) == "localize" for p in g.get("paths", []))]
    check("报告里 localize 不是可清理项", not localize_rows, str(localize_rows))
    core_names = [c["name"] for c in rep["core"]]
    check("原件体积计入核心数据", "localize/" in core_names, str(core_names))
    check("原件体积大于 0", any(c["name"] == "localize/" and c["bytes"] > 0
                              for c in rep["core"]), str(rep["core"]))
    check("没被算进「其他文件」", "localize" not in
          [g for g in rep["groups"] if g["id"] == "other"][0]["detail"]
          if any(g["id"] == "other" for g in rep["groups"]) else True)

    print("[3] 全量清理（连危险项一起）之后原件还在")
    before = orig.read_bytes()
    storage.clean(ids=None, only_safe=False)          # 不传 ids = 全部走一遍
    check("一键清理可再生的 → 原件还在", orig.is_file())
    storage.clean(ids=[g["id"] for g in storage.GROUPS], only_safe=False)
    check("点名清理所有清单项 → 原件还在", orig.is_file())
    check("原件内容一个字节没变", orig.is_file() and orig.read_bytes() == before)
    check("history 也还在", (DATA_DIR / "localize" / "history").is_dir())
    check("index.json 也还在", (DATA_DIR / "localize" / "index.json").is_file())

    print("[4] 清单项确实被清掉了（证明清理本身有效，不是全程空转）")
    check("thumbs 被清掉", not (DATA_DIR / "thumbs" / "junk.bin").exists())
    check("旧版备份被清掉", not (DATA_DIR / "localize_backups" / "junk.bin").exists())
    check("隔离区被清掉", not (DATA_DIR / "quarantine" / "junk.bin").exists())

    print("[5] 核心文件不被碰")
    check("config.json 还在", (DATA_DIR / "config.json").is_file())
    check("track.json 还在", (DATA_DIR / "track.json").is_file())

    print("[6] 传一个不存在的 id 不会误伤")
    got = storage.clean(ids=["localize", "localize/orig"], only_safe=False)
    check("未知 id 释放 0 字节", got["freed"] == 0, str(got))
    check("原件依然在", orig.is_file())

    print()
    print("通过 %d / 失败 %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
