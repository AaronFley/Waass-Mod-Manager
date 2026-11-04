"""Script Merger 感知的沙盒测试：造假的 Merger 台账，不碰真实游戏目录。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import conflicts, merger              # noqa: E402
from core.scanner import scan                   # noqa: E402

PASS = FAIL = 0
# make_mod 自己会补 content/scripts 前缀，这里只给相对这部分之后的路径
SCRIPT_REL = "game/player/playerWitcher.ws"
# 断言用的完整相对路径（全小写，和检测器内部一致）
SCRIPT = "content/scripts/game/player/playerwitcher.ws"


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


def make_mod(root: Path, folder: str, scripts: list[str] = ()) -> Path:
    d = root / "mods" / folder
    content = d / "content"
    content.mkdir(parents=True, exist_ok=True)
    (content / "x.bundle").write_bytes(b"BND")
    for rel in scripts:
        p = content / "scripts" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("// script\n", encoding="utf-8")
    return d


def write_inventory(root: Path, entries: list[tuple[str, list[str], str]]) -> None:
    """entries: [(相对路径, [源 mod], 产物名)]"""
    d = root / "Merger"
    d.mkdir(parents=True, exist_ok=True)
    (d / "WitcherScriptMerger.exe").write_bytes(b"MZ")
    rows = []
    for rel, sources, prod in entries:
        inc = "".join(f'      <IncludedMod Hash="00" ModIoId="0">{s}</IncludedMod>\n'
                      for s in sources)
        rows.append(
            "  <Merge>\n"
            f"    <RelativePath>{rel}</RelativePath>\n"
            f"{inc}"
            f"    <MergedModName>{prod}</MergedModName>\n"
            "  </Merge>\n")
    (d / "MergeInventory.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<MergeInventory xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
        + "".join(rows) + "</MergeInventory>\n", encoding="utf-8")


def fresh_game(base: Path, name: str) -> Path:
    game = base / name
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods").mkdir()
    (game / "dlc").mkdir()
    return game


def settings(cfg: Path, names: list[str]) -> None:
    cfg.mkdir(parents=True, exist_ok=True)
    body = "".join(f"[{n}]\nEnabled=1\nPriority={10 * (i + 1)}\n\n"
                   for i, n in enumerate(names))
    (cfg / "mods.settings").write_text(body, encoding="utf-8")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        os.environ["WAASS_DATA_DIR"] = str(base / "appdata")
        from core import i18n
        i18n.set_lang("zh")   # 断言的是中文文案，别跟着真实 config.json 的 lang 走

        # ---------- 1. 没装 Script Merger ----------
        print("[1] 没有 Merger 目录时行为不变")
        g1 = fresh_game(base, "g1")
        make_mod(g1, "modA", [SCRIPT_REL])
        make_mod(g1, "modB", [SCRIPT_REL])
        settings(base / "c1", ["modA", "modB"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c1")
        os.environ["WAASS_GAME_DIR"] = str(g1)
        inv = merger.inventory(g1)
        check("found=False", inv["found"] is False, str(inv))
        rep = conflicts.analyze(g1, scan(g1).mods, force=True)
        so = [i for i in rep["items"] if i["code"] == "script_overlap"]
        check("仍然报待合并", len(so) == 1, str([i["code"] for i in rep["items"]]))
        check("stats.merger.found 为 False", rep["stats"]["merger"]["found"] is False)
        check("exe_path 返回 None", merger.exe_path(g1) is None)

        # ---------- 2. 已合并的两方 ----------
        print("[2] 两方已合并 → 降级为「无需处理」")
        g2 = fresh_game(base, "g2")
        make_mod(g2, "modAutoLoot", [SCRIPT_REL])
        make_mod(g2, "modMoreSkillSlots", [SCRIPT_REL])
        make_mod(g2, "mod0000_MergedFiles", [SCRIPT_REL])
        write_inventory(g2, [("game\\player\\playerWitcher.ws",
                              ["modMoreSkillSlots", "modAutoLoot"],
                              "mod0000_MergedFiles")])
        settings(base / "c2", ["modAutoLoot", "modMoreSkillSlots"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c2")
        os.environ["WAASS_GAME_DIR"] = str(g2)
        rep = conflicts.analyze(g2, scan(g2).mods, force=True)
        codes = [i["code"] for i in rep["items"]]
        check("不再报 script_overlap", "script_overlap" not in codes, str(codes))
        check("报 script_merged", "script_merged" in codes, str(codes))
        ok = [i for i in rep["items"] if i["code"] == "script_merged"]
        check("等级是 ok（绿色）", ok and ok[0]["level"] == "ok",
              str(ok[0]["level"] if ok else None))
        check("三方都点名", ok and len(ok[0]["mods"]) == 3,
              str(ok[0]["mods"] if ok else []))
        check("待合并计数为 0", rep["stats"]["merge"] == 0, str(rep["stats"]))
        check("已合并组计数为 1", rep["stats"]["merged_groups"] == 1)
        check("merger.found 为 True", rep["stats"]["merger"]["found"] is True)
        check("读到产物名", rep["stats"]["merger"]["merged_name"] == "mod0000_MergedFiles")

        # ---------- 3. 三方只合并了两方 ----------
        print("[3] 有第三方没合并 → 仍报待合并")
        g3 = fresh_game(base, "g3")
        make_mod(g3, "modAutoLoot", [SCRIPT_REL])
        make_mod(g3, "modMoreSkillSlots", [SCRIPT_REL])
        make_mod(g3, "mod0000_MergedFiles", [SCRIPT_REL])
        make_mod(g3, "modLateComer", [SCRIPT_REL])          # 后装的，没进合并
        write_inventory(g3, [("game\\player\\playerWitcher.ws",
                              ["modMoreSkillSlots", "modAutoLoot"],
                              "mod0000_MergedFiles")])
        settings(base / "c3", ["modAutoLoot", "modMoreSkillSlots", "modLateComer"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c3")
        os.environ["WAASS_GAME_DIR"] = str(g3)
        rep = conflicts.analyze(g3, scan(g3).mods, force=True)
        so = [i for i in rep["items"] if i["code"] == "script_overlap"]
        check("报一条待合并", len(so) == 1, str([i["code"] for i in rep["items"]]))
        check("参与者是未合并方 + 产物", so and set(so[0]["mods"]) ==
              {"modLateComer", "mod0000_MergedFiles"},
              str(so[0]["mods"] if so else []))

        # ---------- 4. 台账引用了已删掉的 mod ----------
        print("[4] 合并记录失效检测")
        g4 = fresh_game(base, "g4")
        make_mod(g4, "modAutoLoot", [SCRIPT_REL])
        make_mod(g4, "mod0000_MergedFiles", [SCRIPT_REL])
        write_inventory(g4, [("game\\player\\playerWitcher.ws",
                              ["modMoreSkillSlots", "modAutoLoot"],   # 前者已卸载
                              "mod0000_MergedFiles")])
        settings(base / "c4", ["modAutoLoot"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c4")
        os.environ["WAASS_GAME_DIR"] = str(g4)
        rep = conflicts.analyze(g4, scan(g4).mods, force=True)
        stale = [i for i in rep["items"] if i["code"] == "merge_stale"]
        check("报合并记录失效", len(stale) == 1,
              str([i["code"] for i in rep["items"]]))
        check("等级是 warn", stale and stale[0]["level"] == "warn")
        check("点名被删的 mod", stale and "modMoreSkillSlots" in stale[0]["detail"],
              stale[0]["detail"][:80] if stale else "")
        check("stats.merger.stale 计数", rep["stats"]["merger"]["stale"] == 1)

        # ---------- 5. 产物是空壳 ----------
        print("[5] 合并产物是空壳")
        g5 = fresh_game(base, "g5")
        make_mod(g5, "modAutoLoot", [SCRIPT_REL])
        (g5 / "mods" / "mod0000_MergedFiles" / "content").mkdir(parents=True)
        write_inventory(g5, [("game\\player\\playerWitcher.ws",
                              ["modAutoLoot"], "mod0000_MergedFiles")])
        settings(base / "c5", ["modAutoLoot"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c5")
        os.environ["WAASS_GAME_DIR"] = str(g5)
        rep = conflicts.analyze(g5, scan(g5).mods, force=True)
        check("报空壳", "merge_empty" in [i["code"] for i in rep["items"]],
              str([i["code"] for i in rep["items"]]))

        # ---------- 6. 产物有文件但台账没记录 ----------
        print("[6] 产物存在但台账没记录")
        g6 = fresh_game(base, "g6")
        make_mod(g6, "modA", [SCRIPT_REL])
        make_mod(g6, "modB", [SCRIPT_REL])
        make_mod(g6, "mod0000_MergedFiles", [SCRIPT_REL])
        write_inventory(g6, [])
        settings(base / "c6", ["modA", "modB"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c6")
        os.environ["WAASS_GAME_DIR"] = str(g6)
        rep = conflicts.analyze(g6, scan(g6).mods, force=True)
        so = [i for i in rep["items"] if i["code"] == "script_overlap"]
        check("保守报待合并", len(so) == 1,
              str([i["code"] for i in rep["items"]]))
        check("提示台账没记录", so and "合并记录里没有它" in so[0]["detail"],
              so[0]["detail"][:90] if so else "")

        # ---------- 7. 台账解析本身 ----------
        print("[7] 台账解析")
        inv = merger.inventory(g2)
        check("读到一个合并条目", inv["count"] == 1, str(inv["count"]))
        check("路径归一化带 content/scripts 前缀", SCRIPT in inv["merges"],
              str(list(inv["merges"])))
        check("来源两个 mod", sorted(inv["merges"][SCRIPT]["sources"]) ==
              ["modAutoLoot", "modMoreSkillSlots"],
              str(inv["merges"][SCRIPT]["sources"]))
        check("产物名不含路径残留", inv["merges"][SCRIPT]["out"] == "mod0000_MergedFiles")
        check("product_names 小写", "mod0000_mergedfiles" in merger.product_names(inv))

        # 坏文件不能炸
        (g2 / "Merger" / "MergeInventory.xml").write_text("<broken", encoding="utf-8")
        bad = merger.inventory(g2)
        check("坏台账不抛异常", isinstance(bad, dict) and bad["count"] == 0,
              str(bad.get("count")))

        # ---------- 8. 合并产物不进 mod 列表 ----------
        print("[8] 合订本从界面列表隐藏，冲突判定保留")
        g8 = fresh_game(base, "g8")
        make_mod(g8, "modAutoLoot", [SCRIPT_REL])
        make_mod(g8, "modMoreSkillSlots", [SCRIPT_REL])
        make_mod(g8, "mod0000_MergedFiles", [SCRIPT_REL])
        write_inventory(g8, [("game\\player\\playerWitcher.ws",
                              ["modMoreSkillSlots", "modAutoLoot"],
                              "mod0000_MergedFiles")])
        settings(base / "c8", ["modAutoLoot", "modMoreSkillSlots"])
        os.environ["WAASS_CONFIG_DIR"] = str(base / "c8")
        os.environ["WAASS_GAME_DIR"] = str(g8)
        res8 = scan(g8)
        by = {m.folder: m for m in res8.mods}
        check("产物打上 is_merged", by["mod0000_MergedFiles"].is_merged is True)
        check("来源 mod 不打标记", by["modAutoLoot"].is_merged is False)
        d8 = res8.to_dict()
        folders = [m["folder"] for m in d8["mods"]]
        check("to_dict 里没有产物", "mod0000_MergedFiles" not in folders, str(folders))
        check("to_dict 里有来源", "modAutoLoot" in folders)
        from core import install as _inst
        unk = _inst.unknown_mods(g8)
        check("unknown_mods 不报产物", "mod0000_MergedFiles" not in unk, str(unk))
        check("unknown_mods 照报来源", "modAutoLoot" in unk, str(unk))
        rep8 = conflicts.analyze(g8, res8.mods, force=True)
        check("冲突判定仍认已合并",
              any(i["code"] == "script_merged" for i in rep8["items"]),
              str([i["code"] for i in rep8["items"]]))

        # ---------- 9. 合订本必须登记且排最前（2026-10-27 事故） ----------
        print("[9] 合订本未登记 → 报警 + 自动补位")
        from core.settings_ini import SettingsFile            # noqa: PLC0415

        g9 = fresh_game(base, "g9")
        make_mod(g9, "modAutoLoot", [SCRIPT_REL])
        make_mod(g9, "modMoreSkillSlots", [SCRIPT_REL])
        make_mod(g9, "mod0000_MergedFiles", [SCRIPT_REL])
        write_inventory(g9, [("game\\player\\playerWitcher.ws",
                              ["modMoreSkillSlots", "modAutoLoot"],
                              "mod0000_MergedFiles")])
        cfg9 = base / "c9"
        settings(cfg9, ["modAutoLoot", "modMoreSkillSlots"])   # 故意不给合订本条目
        os.environ["WAASS_CONFIG_DIR"] = str(cfg9)
        os.environ["WAASS_GAME_DIR"] = str(g9)
        cfg_file = cfg9 / "mods.settings"

        inv9 = merger.inventory(g9)
        st9 = SettingsFile.load(cfg_file)
        ms = merger.merged_state(g9, inv9, st9)
        check("认出合订本在硬盘上", ms["exists"] is True and ms["name"] == "mod0000_MergedFiles")
        check("发现没登记", ms["entry"] is False)
        check("判定为不生效", ms["ok"] is False)
        check("给出原因", "没在 mods.settings 里登记" in ms["problem"], ms["problem"])

        # 扫描只报不改
        before = cfg_file.read_bytes()
        res9 = scan(g9)
        d9 = res9.to_dict()
        check("扫描下发 merged 段", d9["merged"]["exists"] is True and d9["merged"]["ok"] is False)
        check("扫描警告里有一条", any(w["code"] == "merge_unregistered" for w in d9["warnings"]),
              str([w["code"] for w in d9["warnings"]]))
        check("扫描没动 mods.settings", cfg_file.read_bytes() == before)
        rep9 = conflicts.analyze(g9, res9.mods, force=True)
        check("冲突面板也报", any(i["code"] == "merge_unregistered" for i in rep9["items"]),
              str([i["code"] for i in rep9["items"]]))
        check("stats.merger.registered 为 False",
              rep9["stats"]["merger"]["registered"] is False)

        # 修复：补条目，且比所有 mod 更靠前
        r = merger.ensure_registered(st9, inv9, g9)
        check("修复时报告 changed", r["changed"] is True)
        sec = st9.get("mod0000_MergedFiles")
        check("条目补上了", sec is not None)
        check("Priority 比所有 mod 小", sec and sec["priority"] < 10, str(sec))
        check("是启用状态", sec and sec["enabled"] is True)
        st9.save()

        st9b = SettingsFile.load(cfg_file)
        ms2 = merger.merged_state(g9, inv9, st9b)
        check("复检变为生效", ms2["ok"] is True and ms2["is_min"] is True, str(ms2))
        check("合规后再调不动文件", merger.ensure_registered(st9b, inv9, g9)["changed"] is False)

        # 被人挤到后面 / 被禁用，都要能被纠回来
        st9b.get("mod0000_MergedFiles")["priority"] = 999
        check("顺序被挤后能纠回", merger.ensure_registered(st9b, inv9, g9)["changed"] is True)
        check("纠正后仍最小", st9b.get("mod0000_MergedFiles")["priority"] < 10,
              str(st9b.get("mod0000_MergedFiles")))
        st9b.get("mod0000_MergedFiles")["enabled"] = False
        merger.ensure_registered(st9b, inv9, g9)
        check("被禁用后能重新启用", st9b.get("mod0000_MergedFiles")["enabled"] is True)

        # 没有合订本时，这条规矩不适用
        g9b = fresh_game(base, "g9b")
        make_mod(g9b, "modA", [SCRIPT_REL])
        check("没有合订本 → exists False",
              merger.merged_state(g9b, merger.inventory(g9b),
                                  SettingsFile.load(cfg_file))["exists"] is False)
        check("没有合订本 → 不去创建条目",
              merger.ensure_registered(SettingsFile.load(cfg_file),
                                       merger.inventory(g9b), g9b)["changed"] is False)

        # ---- 自动发现：Script Merger 不装在游戏目录里也要找得到 ----
        # 它是个绿色程序，教程教的就是「解压到任意目录」。1.3.x 只认
        # <游戏根目录>/Merger/，新用户照教程解压到桌面就一直「没检测到」。
        with tempfile.TemporaryDirectory() as disc_tmp:
            disc = Path(disc_tmp)
            # 把 home 指到空目录，免得测试机上真实桌面里真的有合并器
            old_home = os.environ.get("USERPROFILE")
            os.environ["USERPROFILE"] = str(disc / "home")
            try:
                g10 = fresh_game(disc, "game")

                merger.forget_discovery()
                check("没装时不乱认", merger.exe_path(g10) is None,
                      str(merger.exe_path(g10)))

                own = g10 / "WitcherScriptMerger" / "WitcherScriptMerger.exe"
                own.parent.mkdir(parents=True, exist_ok=True)
                own.write_bytes(b"MZ")
                merger.forget_discovery()
                check("游戏目录里的自定义文件夹能找到", merger.exe_path(g10) == own,
                      str(merger.exe_path(g10)))

                own.unlink()
                side = disc / "Tools" / "Merger" / "ScriptMerger.exe"
                side.parent.mkdir(parents=True, exist_ok=True)
                side.write_bytes(b"MZ")
                merger.forget_discovery()
                check("和游戏并排的工具目录能找到", merger.exe_path(g10) == side,
                      str(merger.exe_path(g10)))

                # 同一个目录里躺着一个名字也含 merger 的无关程序，不能顶掉正品
                (side.parent / "NotAMergerApp.exe").write_bytes(b"MZ")
                merger.forget_discovery()
                check("干扰程序不盖过正品", merger.exe_path(g10) == side,
                      str(merger.exe_path(g10)))

                # 台账跟着 exe 走，而不是死认 <游戏根>/Merger
                (side.parent / "MergeInventory.xml").write_text("<Merges/>", encoding="utf-8")
                check("台账目录跟着 exe 走", merger.merger_dir(g10) == side.parent,
                      str(merger.merger_dir(g10)))
            finally:
                if old_home is None:
                    os.environ.pop("USERPROFILE", None)
                else:
                    os.environ["USERPROFILE"] = old_home
                merger.forget_discovery()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
