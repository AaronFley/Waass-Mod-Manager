"""一键汉化「写入 + 还原」链路的沙盒测试。

翻译要联网，这里不测网络：直接往任务表里放已经翻好的结果，
专测最容易出事的那一段 —— 把译文写进游戏目录，以及能不能干净地退回来。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="waass-locjob-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_SANDBOX)
os.environ["WAASS_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WAASS_CONFIG_DIR"] = str(_SANDBOX / "cfg")

from core import localize, localize_backup as LB, localize_job as LJ, safety   # noqa: E402

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ok   %s" % name)
    else:
        FAIL += 1
        print("  FAIL %s  %s" % (name, detail))


def fake_w3(path: Path, texts: list[str]) -> None:
    body = bytearray(b"RTSW" + (162).to_bytes(4, "little") + b"\x00\x00\x01\xe8")
    for t in texts:
        b = t.encode("utf-8")
        body += bytes([len(b)]) + b + b"\x00\x00\x00"
    body += b"\x00" * 3
    path.write_bytes(bytes(body))


def make_job(path: Path, rows: list[dict]) -> str:
    """直接塞一个「翻译已完成」的任务，跳过网络。"""
    jid = LJ._new_id() + "-test"
    LJ._JOBS[jid] = {
        "id": jid, "ts": 0, "status": "done", "keys": ["modA"], "cats": ["menu"],
        "total": len(rows), "done": len(rows), "items": rows, "error": "",
        "started": "", "engine": "google",
    }
    return jid


def main() -> int:
    game = _SANDBOX / "game"
    content = game / "mods" / "modA" / "content"
    content.mkdir(parents=True)
    (game / "bin" / "x64").mkdir(parents=True)
    (game / "bin" / "x64" / "witcher3.exe").write_bytes(b"MZ")
    w3 = content / "cn.w3strings"
    # 注意：probe 的门槛是「至少 3 条、总字数 24 以上」，太小的表会被当成
    # 加密文件拒绝写入。这里造 6 条足够的量，模拟真实语言表。
    fake_w3(w3, ["Auto equip", "Hoods only", "Show in menu",
                 "Auto unequip", "Animate cloaks", "Allow toggling"])
    size0 = w3.stat().st_size
    raw0 = w3.read_bytes()
    safety.authorize_game(game)

    print("[1] 写入")
    rows = [
        {"id": "u1", "mod": "modA", "mod_name": "A", "file": str(w3), "kind": "w3strings",
         "cat": "menu", "src": "Auto equip", "dst": "自动装", "max": 10, "status": "ok"},
        {"id": "u2", "mod": "modA", "mod_name": "A", "file": str(w3), "kind": "w3strings",
         "cat": "menu", "src": "Hoods only", "dst": "仅兜帽", "max": 10, "status": "ok"},
        {"id": "u3", "mod": "modA", "mod_name": "A", "file": str(w3), "kind": "w3strings",
         "cat": "menu", "src": "Show in menu", "dst": "", "max": 12, "status": "long"},
    ]
    jid = make_job(w3, rows)
    res = LJ.write(jid, root=game)
    check("写入成功", res["ok"] is True, str(res.get("error")))
    check("改了 2 条", res["changed"] == 2, str(res["changed"]))
    check("没填译文的没被碰", not res.get("skipped"), str(res.get("skipped")))
    txt = w3.read_bytes()
    check("文件长度没变（等长替换）", len(txt) == size0, "%d -> %d" % (size0, len(txt)))
    got = [s["text"] for s in localize.w3strings.extract(w3)]
    check("译文进文件了", any("自动装" in t for t in got), str(got))
    check("没填译文那条保持原样", any("Show in menu" in t for t in got), str(got))

    print("\n[2] 备份与记录")
    check("原始件已存", LB.has_original(w3, game))
    check("原件是没汉化过的", LB.original_of(w3, game).read_bytes() == raw0)
    check("有记录", bool(res.get("record")), str(res))
    recs = LJ.history()
    check("记录里能看到这一批", len(recs) == 1 and recs[0]["count"] == 2, str(recs))
    st = LB.state(w3, game)
    check("状态是已汉化", st["localized"] is True)

    print("\n[3] 手动改译文的覆盖")
    rows2 = [{"id": "v1", "mod": "modA", "mod_name": "A", "file": str(w3), "kind": "w3strings",
              "cat": "menu", "src": "Show in menu", "dst": "", "max": 12, "status": "long"}]
    jid2 = make_job(w3, rows2)
    r2 = LJ.write(jid2, edits=[{"id": "v1", "dst": "显示在菜单里"}], root=game)
    check("手改后按用户长度判定", r2["changed"] == 0,
          "12 字节放不下 18 字节，应被跳过：%s" % r2.get("skipped"))
    check("如实说明放不下", any("放不下" in str(s.get("need", "")) or s.get("need") for s in r2["skipped"]),
          str(r2.get("skipped")))
    r3 = LJ.write(jid2, edits=[{"id": "v1", "dst": "菜单显示"}], root=game)
    check("改短后写进去了", r3["changed"] == 1, str(r3))

    print("\n[4] only 限定单条重写")
    rows3 = [
        {"id": "w1", "mod": "modA", "mod_name": "A", "file": str(w3), "kind": "w3strings",
         "cat": "menu", "src": "菜单显示", "dst": "在菜单里", "max": 12, "status": "ok"},
        {"id": "w2", "mod": "modA", "mod_name": "A", "file": str(w3), "kind": "w3strings",
         "cat": "menu", "src": "自动装", "dst": "自己装", "max": 9, "status": "ok"},
    ]
    jid3 = make_job(w3, rows3)
    r4 = LJ.write(jid3, root=game, only=["w1"])
    got4 = [s["text"] for s in localize.w3strings.extract(w3)]
    check("只写了指定那条", any("在菜单里" in t for t in got4), str(got4))
    check("另一条没动", any("自动装" in t for t in got4), str(got4))

    print("\n[5] 一键还原")
    res5 = LJ.restore(mode="orig", root=game)
    check("还原成功", res5["ok"] is True, str(res5.get("why")))
    check("文件回到最初", w3.read_bytes()[:4] == b"RTSW" and
          "Auto equip" in w3.read_bytes().decode("utf-8", "ignore"))
    after = w3.read_bytes().decode("utf-8", "ignore")
    check("译文全没了", "自动装" not in after and "在菜单里" not in after and "Auto equip" in after, after)
    check("还原本身可撤销（走 Operation）", safety.undo_last()[0] is True)

    print("\n[6] 边界")
    empty = make_job(w3, [{"id": "x", "file": str(w3), "src": "a", "dst": "", "status": "long"}])
    r6 = LJ.write(empty, root=game)
    check("没有可写内容时明确报错", r6["ok"] is False and r6["changed"] == 0, str(r6))
    try:
        LJ.write("不存在", root=game)
        check("不存在的任务应报错", False)
    except KeyError:
        check("不存在的任务明确报错", True)
    try:
        LJ.start(game, ["modA"], cats=["不存在的类别"])
        check("陌生类别应报错", False)
    except ValueError:
        check("陌生类别明确报错", True)

    print("\n结果：%d 通过 / %d 失败" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_SANDBOX, ignore_errors=True)
    sys.exit(code)
