"""汉化备份与还原的沙盒测试。

全部在临时目录里的假游戏目录上跑，绝不碰真实游戏。
按项目红线：WAASS_TEST_MODE=1 + WAASS_SANDBOX_DIR，沙盒外一律拒绝写。
"""
from __future__ import annotations

import os
import hashlib
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 环境必须在 import core.* 之前设好：paths/guard 在导入时就读取了。
_SANDBOX = Path(tempfile.mkdtemp(prefix="waass-loc-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_SANDBOX)
os.environ["WAASS_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WAASS_CONFIG_DIR"] = str(_SANDBOX / "cfg")

from core import guard, localize_backup as LB, safety      # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def make_game(root: Path) -> Path:
    """造一个最小的假游戏目录：只有 mods/ 下的两个语言表。"""
    for name in ("modA", "modB"):
        d = root / "mods" / name / "content"
        d.mkdir(parents=True, exist_ok=True)
        (d / "cn.w3strings").write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + name.encode())
    (root / "bin" / "x64" / "witcher3.exe").parent.mkdir(parents=True, exist_ok=True)
    return root


def main() -> int:
    game = make_game(_SANDBOX / "game")
    safety.authorize_game(game)

    a = game / "mods" / "modA" / "content" / "cn.w3strings"
    b = game / "mods" / "modB" / "content" / "cn.w3strings"
    orig_a = a.read_bytes()

    print("[1] 存原件")
    rel = LB.keep_original(a, game)
    check("相对路径按游戏目录算", rel == "mods/modA/content/cn.w3strings", rel)
    check("原件已存下", LB.has_original(a, game))
    check("原件内容正确", LB.original_of(a, game).read_bytes() == orig_a)

    print("\n[2] 第二次不覆盖原件（关键：否则还原点被毁）")
    a.write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + "改过的译文".encode())
    LB.keep_original(a, game)
    check("原件仍是原始的", LB.original_of(a, game).read_bytes() == orig_a,
          repr(LB.original_of(a, game).read_bytes()[-12:]))

    print("\n[3] prepare + commit")
    a.write_bytes(orig_a)                              # 回到原位，模拟第一次汉化
    tok = LB.prepare([a, b], game)
    check("token 记了两个文件", len(tok["files"]) == 2, str(len(tok["files"])))
    a.write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + "兜帽".encode())
    b.write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + "披风".encode())
    entry = LB.commit(tok, "汉化 2 个模组", engine="google", count=2, root=game)
    check("记录里文件数是 2", len(entry["files"]) == 2)
    check("history 存了一份", (LB.HIST / entry["id"] / "mods/modA/content/cn.w3strings").is_file())
    check("sha_before 记的是汉化前的内容",
          entry["files"][0]["sha_before"] == hashlib.sha256(orig_a).hexdigest())
    check("sha_after 记的是汉化后的内容",
          entry["files"][0]["sha_after"] == hashlib.sha256(a.read_bytes()).hexdigest())
    check("大小前后都记了下来",
          entry["files"][0]["size_before"] == len(orig_a)
          and entry["files"][0]["size_after"] == a.stat().st_size,
          "%s / %s" % (entry["files"][0]["size_before"], entry["files"][0]["size_after"]))

    print("\n[4] state 反映当前状态")
    st = LB.state(a, game)
    check("判定为已汉化", st["localized"] is True)
    check("有原件", st["has_original"] is True)
    check("没有漂移", st["drifted"] is False, str(st))

    print("\n[5] 记录列表")
    recs = LB.records()
    check("有一条记录", len(recs) == 1, str(len(recs)))
    check("带时间和数量", bool(recs[0]["time"]) and recs[0]["count"] == 2)

    print("\n[6] 还原到原始")
    res = LB.restore(mode="orig", root=game)
    check("还原成功", res["ok"] is True, str(res.get("why")))
    check("a 回到原文", a.read_bytes() == orig_a)
    check("b 回到原文", b.read_bytes() == b"RTSW" + b"\xa4\x00\x00\x00" + b"modB")

    print("\n[7] 已经是原始文件时不重复动")
    res2 = LB.restore(mode="orig", root=game)
    check("没有要还原的文件", res2["restored"] == [], str(res2))
    check("如实说明原因", any("已经是原始" in s["why"] for s in res2["skipped"]), str(res2["skipped"]))

    print("\n[8] 漂移检测：汉化之后模组更新过")
    tok2 = LB.prepare([a], game)
    a.write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + "新译文".encode())
    e2 = LB.commit(tok2, "再汉化一次", engine="google", count=1, root=game)
    a.write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + "模组更新过的内容".encode())
    chk = LB.check(e2["id"])
    check("检出了漂移", len(chk["drifted"]) == 1, str(chk["drifted"]))
    res3 = LB.restore(e2["id"], mode="hist", force=False, root=game)
    check("默认不硬覆盖", res3["restored"] == [], str(res3["restored"]))
    check("说明是漂移挡住的", any("又被改过" in s["why"] for s in res3["skipped"]), str(res3["skipped"]))
    check("文件确实没被动", b"\xe6\xa8\xa1\xe7\xbb\x84" in a.read_bytes())

    res4 = LB.restore(e2["id"], mode="hist", force=True, root=game)
    check("force 才覆盖", len(res4["restored"]) == 1, str(res4["restored"]))
    check("hist 还原到第二次汉化之后", a.read_bytes() == b"RTSW" + b"\xa4\x00\x00\x00" + "新译文".encode(),
          repr(a.read_bytes()[-12:]))

    print("\n[9] 还原本身也可撤销（走 Operation）")
    ok, msg = safety.undo_last()
    check("撤销成功", ok is True, msg)
    check("撤销后回到漂移前的内容", a.read_bytes() == b"RTSW" + b"\xa4\x00\x00\x00" + "模组更新过的内容".encode(),
          repr(a.read_bytes()[-16:]))

    print("\n[10] 不存在的记录")
    bad = LB.restore("19990101-000000", root=game)
    check("明确报错而不是瞎还原", bad["ok"] is False and "找不到" in bad["why"], str(bad.get("why")))

    print("\n[11] history 只留 20 批，orig 永不删")
    for i in range(4):
        t = LB.prepare([a], game)
        a.write_bytes(b"RTSW" + b"\xa4\x00\x00\x00" + ("第%d次" % i).encode())
        LB.commit(t, "第 %d 次" % i, engine="google", count=1, root=game)
    dirs = [d for d in LB.HIST.iterdir() if d.is_dir()]
    check("history 目录数不超过 20", len(dirs) <= 20, str(len(dirs)))
    check("orig 还在", (LB.ORIG / "mods/modA/content/cn.w3strings").is_file())

    print("\n[12] 清空必须显式确认")
    r = LB.drop_all()
    check("不给确认就拒绝", r["ok"] is False)
    check("拒绝时什么都没删", (LB.ORIG / "mods/modA/content/cn.w3strings").is_file())
    r2 = LB.drop_all(confirm=True)
    check("确认后才清", r2["ok"] is True and not LB.ORIG.exists())

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_SANDBOX, ignore_errors=True)
    sys.exit(code)
