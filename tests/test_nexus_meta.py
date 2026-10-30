"""Nexus 取资料模块的测试：解析、缓存、过期判断、失败降级、封面路径。

不联网 —— 把底层请求换成一个假的，专门验「拿到数据之后怎么处理」。
最后有一项真的连一次网（连不上就跳过，不算失败）。
跑法：
    python tests/test_nexus_meta.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="waass-nexus-")
    base = Path(tmp)
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods").mkdir()
    os.environ.update({
        "WAASS_DATA_DIR": str(base / "appdata"),
        "WAASS_CONFIG_DIR": str(base / "cfg"),
        "WAASS_GAME_DIR": str(game),
        "WAASS_TEST_MODE": "1",
        "WAASS_SANDBOX_DIR": str(base),
        "WAASS_EXTRA_ARCHIVE_DIRS": "",
    })

    from core import nexus_meta as nx

    # 把请求换掉，测试不碰网
    calls: list[str] = []
    fake: dict = {"data": None, "raise": None}

    def fake_gql(query: str, timeout: float = 12.0) -> dict:
        calls.append(query)
        if fake["raise"]:
            raise nx.NexusError(fake["raise"])
        return fake["data"]

    nx._gql = fake_gql  # type: ignore[assignment]

    print("\n[1] 一条 GraphQL 结果 → 我们的字段")
    node = {
        "modId": 13087, "name": "All Quest Markers Plus", "author": "alx713",
        "version": "1.7.3", "summary": "干活用的", "category": "User Interface",
        "pictureUrl": "https://img/p.jpg", "thumbnailUrl": "https://img/t.jpg",
        "downloads": 52083, "endorsements": 521,
        "updatedAt": "2026-10-23T15:07:10Z", "adultContent": False,
    }
    rec = nx._norm(node)
    check("ID 转成整数", rec["mod_id"] == 13087)
    check("作者", rec["author"] == "alx713")
    check("版本", rec["version"] == "1.7.3")
    check("封面取大图", rec["picture"].endswith("p.jpg"))
    check("缩略图单独存", rec["thumb"].endswith("t.jpg"))
    check("页面地址拼好", rec["url"] == "https://www.nexusmods.com/witcher3/mods/13087")
    check("成人标记", rec["adult"] is False)

    print("\n[2] 批量取一批 ID")
    fake["data"] = {"data": {"legacyMods": {"nodes": [
        node,
        {"modId": 4242, "name": "Hoods", "author": "ArdCarraigh", "version": "3.7"},
    ]}}}
    got = nx.fetch(["13087", "4242", "  4242  "])
    check("拿到两条", len(got) == 2, str(list(got)))
    check("重复的 ID 只问一次", calls and calls[-1].count("modId:4242") == 1, calls[-1][:80])
    check("写进了缓存文件", (base / "appdata" / "nexus" / "13087.json").is_file())

    print("\n[3] 脏输入不会变成请求")
    calls.clear()
    fake["data"] = {"data": {"legacyMods": {"nodes": []}}}
    check("空名单不请求", nx.fetch([]) == {} and not calls)
    check("非数字被丢掉", nx.fetch(["abc", "", None]) == {} and not calls)
    long_ids = [str(1000 + i) for i in range(90)]
    nx.fetch(long_ids)
    check("一次最多问 %d 个" % nx.MAX_IDS,
          calls and calls[-1].count("modId:") == nx.MAX_IDS, str(calls[-1].count("modId:")))

    print("\n[4] 网络出问题时安静返回空")
    fake["raise"] = "连不上"
    check("fetch 返回空", nx.fetch(["9999"]) == {})
    nx._state["fail_until"] = 0.0          # 清掉冷却，后面的用例还要用
    fake["raise"] = None
    fake["data"] = {"data": {"legacyMods": {"nodes": []}}}

    print("\n[5] 新鲜度与过期")
    check("刚写的是新的", nx.is_fresh("13087") is True)
    p = base / "appdata" / "nexus" / "13087.json"
    d = json.loads(p.read_text(encoding="utf-8"))
    old = (datetime.now().astimezone() - timedelta(days=nx.TTL_DAYS + 1))
    d["fetched_at"] = old.isoformat(timespec="seconds")
    p.write_text(json.dumps(d), encoding="utf-8")
    check("超过 %d 天算旧" % nx.TTL_DAYS, nx.is_fresh("13087") is False)
    check("没有记录的算旧", nx.is_fresh("77777") is False)
    d["fetched_at"] = "不是时间"
    p.write_text(json.dumps(d), encoding="utf-8")
    check("时间戳坏了当作旧", nx.is_fresh("13087") is False)

    print("\n[6] refresh 只问过期的")
    calls.clear()
    fake["data"] = {"data": {"legacyMods": {"nodes": [node]}}}
    out = nx.refresh(["13087", "4242"], with_cover=True)
    check("只问了过期的那一个", out["requested"] == 1, str(out))
    check("拿到了", out["got"] == 1, str(out))

    print("\n[7] 缓存读不回来也不能崩")
    p.write_text("{坏掉的 json", encoding="utf-8")
    check("坏文件当作没有", nx.cached("13087") is None)
    check("全量读也跳过坏的", isinstance(nx.cached_all(), dict))

    print("\n[8] 按名字搜候选")
    calls.clear()
    fake["data"] = {"data": {"mods": {"nodes": [node]}}}
    res = nx.search("smooth ui")
    check("搜到了", res["ok"] and len(res["items"]) == 1, str(res)[:120])
    check("只在本游戏里搜", 'gameId:{value:"952"' in calls[-1], calls[-1][:120])
    check("按下载量排", "downloads:{direction:DESC}" in calls[-1])
    check("带了网页搜索的兜底链接", "search/" in res["search_page"])
    check("空关键词不请求", nx.search("  ")["ok"] is False)
    fake["raise"] = "断网了"
    bad = nx.search("hoods")
    nx._state["fail_until"] = 0.0
    fake["raise"] = None
    check("搜不动时给出错误而不是抛异常", bad["ok"] is False and bad["error"], str(bad)[:80])

    print("\n[9] 封面路径")
    cov = base / "appdata" / "thumbs"
    cov.mkdir(parents=True, exist_ok=True)
    check("没有图时是空的", nx.cover_rel("55555") == "")
    (cov / "nexus_55555.png").write_bytes(b"\x89PNG" + b"0" * 100)
    check("认得 png", nx.cover_rel("55555") == "thumbs/nexus_55555.png")
    (cov / "nexus_55555.jpg").write_bytes(b"\xff\xd8" + b"0" * 100)
    check("jpg 优先", nx.cover_rel("55555") == "thumbs/nexus_55555.jpg")

    print("\n[10] 删掉某个 mod 的资料")
    (base / "appdata" / "nexus" / "4242.json").write_text(
        json.dumps({"mod_id": 4242, "name": "Hoods"}), encoding="utf-8")
    check("删掉了", nx.forget("4242") is True)
    check("再读没有了", nx.cached("4242") is None)

    print("\n[11] 真连一次网（连不上就跳过）")
    class _Real:
        def __getattr__(self, k):
            raise AttributeError(k)
    # 换回真实现
    import importlib
    real = importlib.reload(nx)
    try:
        live = real.fetch(["13087", "4242"])
        if live:
            one = live.get("13087") or {}
            check("线上拿到了名字", bool(one.get("name")), str(one)[:80])
            check("线上拿到了作者", bool(one.get("author")), str(one)[:80])
        else:
            print("  skip 没连上（离线环境正常，不算失败）")
    except Exception as e:                                  # noqa: BLE001
        print(f"  skip 请求出错：{e}")

    print()
    if fails:
        print(f"{len(fails)} 项失败：" + "、".join(fails))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
