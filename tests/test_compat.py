"""数据兼容 + 启动自检的沙盒测试。

规矩同其它测试：一律在临时目录里造假数据，不碰真实 data/。
本文件在 import core 之前就把环境变量钉好 —— core.paths 的 DATA_DIR
在 import 那一刻就绑死了，晚一步设就写进用户真实目录。
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_BOX = Path(tempfile.mkdtemp(prefix="waass-compat-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_BOX)
os.environ["WAASS_DATA_DIR"] = str(_BOX / "data")
os.environ["WAASS_CONFIG_DIR"] = str(_BOX / "cfg")
os.environ.pop("WAASS_READONLY", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import compat, guard, selfcheck      # noqa: E402

DATA = _BOX / "data"
PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


def reset(data: dict | None = None, files: dict | None = None) -> None:
    """清空沙盒 data/，重建成指定状态。

    selfcheck 有进程内缓存、guard 的只读开关在环境变量里 —— 两个都要复位，
    否则上一个场景会污染下一个。
    """
    shutil.rmtree(DATA, ignore_errors=True)
    DATA.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        p = DATA / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    if data is not None:
        (DATA / "meta.json").write_text(json.dumps(data), encoding="utf-8")
    selfcheck._MEM = None
    os.environ.pop(guard.ENV_READONLY, None)


def main() -> int:
    print("[1] 全新安装")
    reset()
    st = compat.state()
    check("识别为全新（fresh）", st["kind"] == "fresh", st["kind"])
    rep = selfcheck.run("1.2.9")
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    check("写了 meta 且 schema=3", meta.get("schema") == compat.SCHEMA, str(meta))
    check("自检报首次运行", any(i["code"] == "first_run" for i in rep["items"]),
          str([i["code"] for i in rep["items"]]))
    check("没有产生备份目录", not (DATA / "_backup").exists())

    print("[2] 老用户：有数据但没有版本号（1.2.8 升上来）")
    reset(files={
        "config.json": json.dumps({"lang": "zh"}),
        "installed.json": '{"packages": []}',
        "track.json": '{"mods": {}}',
    })
    st = compat.state()
    check("识别为 legacy（按第 2 版处理）", st["kind"] == "legacy", str(st))
    rep = selfcheck.run("1.2.9")
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    check("迁移后 schema=3", meta.get("schema") == compat.SCHEMA, str(meta))
    check("自检报「数据已迁移」",
          any(i["code"] == "schema_migrated" for i in rep["items"]),
          str([i["code"] for i in rep["items"]]))
    backups = list((DATA / "_backup").glob("pre-schema2-*"))
    check("迁移前留了备份", len(backups) == 1, str(backups))
    if backups:
        check("备份里有 config.json", (backups[0] / "config.json").is_file())
    check("老用户不报「首次运行」",
          not any(i["code"] == "first_run" for i in rep["items"]))
    check("用户原来那份 config 没被改写",
          (DATA / "config.json").read_text(encoding="utf-8") == json.dumps({"lang": "zh"}))

    print("[3] 坏文件：改名保留，绝不删除")
    reset(files={
        "config.json": '{"lang": "zh"}',
        "track.json": '{"mods": {',          # 断电写了一半
    })
    selfcheck.run("1.2.9")
    broken = list(DATA.glob("track.json.broken-*"))
    check("坏文件被改名保留", len(broken) == 1, str(broken))
    check("内容原样还在", broken and broken[0].read_text(encoding="utf-8") == '{"mods": {')
    check("原文件名已不存在", not (DATA / "track.json").exists())
    rep = json.loads((DATA / "selfcheck.json").read_text(encoding="utf-8"))
    check("自检报了被隔离的文件",
          any(i["code"] == "file_quarantined" for i in rep["items"]),
          str([i["code"] for i in rep["items"]]))

    print("[4] 幂等：同一版本再启动一次，不该重复迁移")
    before = len(list((DATA / "_backup").glob("*")))
    rep2 = selfcheck.run("1.2.9")
    after = len(list((DATA / "_backup").glob("*")))
    check("没有新增备份", before == after, f"{before}→{after}")
    check("kind 变成 ok", rep2["kind"] == "ok", rep2["kind"])
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    check("history 没被重复写", len(meta.get("history") or []) == 1,
          str(meta.get("history")))

    print("[5] 降级：数据来自更新版本的程序 → 只读")
    reset(data={"schema": 9, "app": "2.0.0"})
    rep3 = selfcheck.run("1.2.9")
    check("kind=downgrade", rep3["kind"] == "downgrade", rep3["kind"])
    check("进只读", rep3["readonly"] and guard.readonly())
    check("界面会收到 error 提示",
          any(i["code"] == "downgrade" and i["level"] == "error" for i in rep3["items"]),
          str(rep3["items"]))
    meta = json.loads((DATA / "meta.json").read_text(encoding="utf-8"))
    check("没有改写 meta（仍是第 9 版）", meta.get("schema") == 9, str(meta))
    check("只读下自检结论仍然能显示", bool(selfcheck.summary().get("items")))

    print("[6] 刚换过 exe：要明确回答「原来的数据还正常吗」")
    reset(data={"schema": 3, "app": "1.2.8"},
          files={"config.json": '{"lang": "zh"}', "installed.json": '{"packages": []}'})
    rep4 = selfcheck.run("1.2.9")
    codes = [i["code"] for i in rep4["items"]]
    check("报「已更新」", rep4["updated"], str(rep4))
    check("明确说数据核对正常", "updated_data_ok" in codes, str(codes))
    check("带上前后版本号",
          next((i["params"] for i in rep4["items"]
                if i["code"] == "updated_data_ok"), {}).get("from") == "1.2.8")

    print("[7] 刚换过 exe 但数据读不出来 → 报问题而不是报平安")
    reset(data={"schema": 3, "app": "1.2.8"},
          files={"config.json": '{"lang": "zh"}', "installed.json": "not json"})
    rep5 = selfcheck.run("1.2.9")
    codes = [i["code"] for i in rep5["items"]]
    check("不报平安", "updated_data_ok" not in codes, str(codes))
    check("报「更新后数据有问题」", "updated_data_issue" in codes, str(codes))
    check("同时点名坏文件", any(c == "bad_json" for c in codes), str(codes))
    check("整体等级是 error", rep5["level"] == "error", rep5["level"])

    print("[8] data 目录写不进去要提前说，而不是等用户装 mod 时才发现")
    reset(data={"schema": 3, "app": "1.2.9"},
          files={"config.json": '{"lang": "zh"}'})
    real = DATA / ".selfcheck.tmp"
    # 造一个同名目录，让写文件必然失败（比改权限可靠，且跨平台）
    (DATA / ".selfcheck.tmp").mkdir()
    rep6 = selfcheck.run("1.2.9")
    codes = [i["code"] for i in rep6["items"]]
    check("报数据目录不可写", "data_unwritable" in codes, str(codes))
    shutil.rmtree(real, ignore_errors=True)

    print("[9] 游戏目录写不进去（装在 Program Files）要提前说")
    game = _BOX / "game"
    (game / "bin" / "x64").mkdir(parents=True, exist_ok=True)
    (game / "bin" / "x64" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods").mkdir(exist_ok=True)
    reset(data={"schema": 3, "app": "1.2.9"},
          files={"config.json": json.dumps({"game_root": str(game)})})
    real_probe = selfcheck.dir_writable
    selfcheck.dir_writable = lambda _p: False        # 假装 ACL 不让写
    try:
        rep7 = selfcheck.run("1.2.9")
    finally:
        selfcheck.dir_writable = real_probe
    codes = [i["code"] for i in rep7["items"]]
    check("报游戏目录不可写", "game_readonly" in codes, str(codes))
    check("是警告（黄条）不是错误", rep7["level"] == "warn", rep7["level"])
    check("明细里如实记 writable=False",
          rep7["detail"].get("game_root", {}).get("writable") is False,
          str(rep7["detail"].get("game_root")))

    reset(data={"schema": 3, "app": "1.2.9"},
          files={"config.json": json.dumps({"game_root": str(game)})})
    rep8 = selfcheck.run("1.2.9")
    check("能写的时候不打扰", "game_readonly" not in [i["code"] for i in rep8["items"]])

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
