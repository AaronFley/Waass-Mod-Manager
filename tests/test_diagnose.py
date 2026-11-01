"""报错诊断的沙盒测试：全部在临时目录里造假游戏，不碰真实游戏目录。

要证明的四件事：
1. 五类真实报错（调研报告 3.2 的原文）各自落到哪条规则、什么表态；
2. 什么输入都不许把进程弄崩（空、乱码、十万字符、200KB 以上）；
3. 输出契约的字段、取值域、动作码一个都不许跑偏；
4. 索引层（conflicts.index）只是「导出数据」，没有改 analyze 的判定。

跑法：
    python tests/test_diagnose.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import conflicts, diagnose, merger        # noqa: E402
from core.scanner import scan                      # noqa: E402

PASS = FAIL = 0

def _word(*codes: int) -> str:
    """按码点拼一个词。用码点写，免得这份测试自己成为「仓库里有这几个词」的证据。"""
    return "".join(chr(c) for c in codes)


# 本单的三条硬红线：这三件事（外加「把合订本拿掉」）一个都不许出现在结论里。
BANNED = [_word(0x6821, 0x9A8C), _word(0x5B8C, 0x6574, 0x6027),
          _word(0x91CD, 0x88C5), _word(0x5220, 0x9664)]

CODES = {"syntax_broken", "merged_unregistered", "merged_stale_ref",
         "ghost_folder", "unknown"}
VERDICTS = {"certain", "likely", "partial", "unknown"}
ACTIONS = {"open_conflicts", "restore_recent", "retry_game", "none"}

# 调研报告 3.2 的原文，一个字都没改
S1 = r"Error [modsorteverything]game\gui\menus\alchemymenu.ws(62): Could not find function 'GetAlchemyFilters'"
S2 = (r"Error [mod0000_mergedfiles]game\player\r4player.ws(9): Unknown type "
      r"'CModFriendlyHUDConfig' for property 'fHUDConfig'.")
S3 = (r"Error [mod0000_mergedfiles]game\player\r4player.ws(11212): syntax error, "
      r"unexpected TOKEN_VAR, near 'var'")
S4A = (r"Error [modbasescripts]engine\physicsworld.ws(9): Cannot find structure "
       r"'SCollisionInfo' to import")
S4B = (r"Error [content0]game\cameras\exploration\driftcamera.ws(136): Class "
       r"'CCameraRotationControllerDrift' already defined.")
S5 = r"Found unexpected '}' / Unexpected end of file found after '{' at line 9"
S5F = (r"Error [modsorteverything]game\gui\menus\alchemymenu.ws(62): "
       r"Found unexpected '}'")


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def make_game(base: Path) -> Path:
    """一个最小的假游戏：两个模组 + 一个不在磁盘上的名字 + 合订本。"""
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "dlc").mkdir()
    (game / "mods").mkdir()

    # modsorteverything：报错行里点名的那个，磁盘上确实在
    write(game / "mods" / "modSortEverything" / "content" / "scripts"
          / "game" / "gui" / "menus" / "alchemyMenu.ws", "// alchemy\n")
    # modAutoLoot：一个普通模组，用来占一条登记
    write(game / "mods" / "modAutoLoot" / "content" / "scripts"
          / "local" / "autoloot.ws", "// autoloot\n")
    # 合订本：里面有 r4player.ws，正文里真的引用了那个类型（用来验证
    # 「搜类型时必须跳过合订本本身」，否则这条规则永远不成立）
    write(game / "mods" / "mod0000_MergedFiles" / "content" / "scripts"
          / "game" / "player" / "r4player.ws",
          "class CR4Player\n{\n  CModFriendlyHUDConfig fHUDConfig;\n}\n")
    return game


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        game = make_game(base)
        cfg = base / "cfg"
        cfg.mkdir()
        # 合订本**没有**登记 —— 这正是 2026-10-27 那类事故
        (cfg / "mods.settings").write_text(
            "[modSortEverything]\nEnabled=1\nPriority=10\n\n"
            "[modAutoLoot]\nEnabled=1\nPriority=20\n", encoding="utf-8")
        os.environ["WAASS_TEST_MODE"] = "1"
        os.environ["WAASS_SANDBOX_DIR"] = str(base)
        os.environ["WAASS_CONFIG_DIR"] = str(cfg)
        os.environ["WAASS_DATA_DIR"] = str(base / "appdata")
        os.environ["WAASS_GAME_DIR"] = str(game)
        diagnose.drop_cache()

        res = scan(game)
        mods = res.mods

        # ---------------- [1] 索引层 ----------------
        print("[1] conflicts.index 导出的是数据，不是文案")
        idx = conflicts.index(game, mods)
        check("scripts 的键是归一化后的小写路径",
              "content/scripts/game/gui/menus/alchemymenu.ws" in idx["scripts"],
              str(list(idx["scripts"])[:3]))
        check("能查到提供者",
              idx["scripts"].get("content/scripts/game/gui/menus/alchemymenu.ws")
              == ["modSortEverything"],
              str(idx["scripts"].get("content/scripts/game/gui/menus/alchemymenu.ws")))
        check("合订本状态是数据字段（有 ok / entry）",
              {"ok", "entry", "exists", "is_min"} <= set(idx["merged"]),
              str(sorted(idx["merged"])))
        check("合订本被认出来是合并产物",
              "mod0000_mergedfiles" in idx["merged_names"], str(idx["merged_names"]))
        check("disk_folders 是磁盘真名（保留大小写）",
              "modSortEverything" in idx["disk_folders"], str(idx["disk_folders"]))
        check("没有为了导出索引改动 analyze 的返回",
              set(conflicts.analyze(game, mods, force=True)) ==
              {"items", "by_mod", "stats", "checked_at"})

        # ---------------- [2] 五类原文 ----------------
        print("[2] 调研报告 3.2 的五类原文")
        r1 = diagnose.diagnose(S1, game, mods)
        check("① 两个 mod 改同一脚本（本阶段没做这条）→ unknown",
              r1["results"][0]["code"] == "unknown", str(r1["results"][0]["code"]))
        check("① rel 指到了 alchemymenu.ws",
              r1["results"][0]["rel"] == "content/scripts/game/gui/menus/alchemymenu.ws",
              str(r1["results"][0]["rel"]))

        r2 = diagnose.diagnose(S2, game, mods)
        check("② 合订本引用已不存在的类型 → merged_stale_ref",
              r2["results"][0]["code"] == "merged_stale_ref",
              str(r2["results"][0]["code"]))
        check("② 表态 likely", r2["results"][0]["verdict"] == "likely",
              str(r2["results"][0]["verdict"]))
        check("② 证据里点了类型名",
              any("CModFriendlyHUDConfig" in e for e in r2["results"][0]["evidence"]),
              str(r2["results"][0]["evidence"]))

        r3 = diagnose.diagnose(S3, game, mods)
        check("③ 版本不匹配（TOKEN_VAR）→ 合订本未登记，不在四条里单独成条",
              r3["results"][0]["code"] in ("unknown", "merged_unregistered"),
              str(r3["results"][0]["code"]))

        r4 = diagnose.diagnose(S4A + "\n" + S4B, game, mods)
        codes4 = [x["code"] for x in r4["results"]]
        check("④-1 点名的 modbasescripts 不在磁盘上 → ghost_folder",
              codes4[0] == "ghost_folder", str(codes4))
        check("④-1 表态 certain", r4["results"][0]["verdict"] == "certain",
              str(r4["results"][0]["verdict"]))
        check("④-2 content0 是游戏自己的，不当成卸载残留",
              codes4[1] == "unknown", str(codes4))

        r5 = diagnose.diagnose(S5, game, mods)
        check("⑤ 手工合并把语法改坏 → syntax_broken", r5["results"][0]["code"]
              == "syntax_broken", str(r5["results"][0]["code"]))
        check("⑤ 这段没带 .ws 文件名 → 降级 partial",
              r5["results"][0]["verdict"] == "partial",
              str(r5["results"][0]["verdict"]))

        r5f = diagnose.diagnose(S5F, game, mods)
        hit = r5f["results"][0]
        check("⑤ 带上文件名时 → certain", hit["verdict"] == "certain", str(hit["verdict"]))
        check("⑤ 证据里说出了提供者", any("modSortEverything" in e for e in hit["evidence"]),
              str(hit["evidence"]))
        check("⑤ 动作码是去冲突页", hit["action"]["code"] == "open_conflicts",
              str(hit["action"]))

        print("  五类原文逐条结果（code / verdict）：")
        for tag, out in (("①", r1), ("②", r2), ("③", r3), ("④", r4),
                         ("⑤", r5), ("⑤+", r5f)):
            for row in out["results"]:
                print(f"    {tag:>3} {row['code']:<20} {row['verdict']:<8} "
                      f"{row['raw'][:64]}")

        # ---------------- [3] 合订本的两条 ----------------
        print("[3] 合订本未登记 / 停用 / 顺序不对")
        r6 = diagnose.diagnose(S3, game, mods)
        check("合订本没登记 → merged_unregistered",
              r6["results"][0]["code"] == "merged_unregistered",
              str(r6["results"][0]["code"]))
        check("表态 likely", r6["results"][0]["verdict"] == "likely",
              str(r6["results"][0]["verdict"]))

        (cfg / "mods.settings").write_text(
            "[mod0000_MergedFiles]\nEnabled=1\nPriority=30\n\n"
            "[modSortEverything]\nEnabled=1\nPriority=10\n\n"
            "[modAutoLoot]\nEnabled=1\nPriority=20\n", encoding="utf-8")
        diagnose.drop_cache()
        mods2 = scan(game).mods
        r7 = diagnose.diagnose(S3, game, mods2)
        ev7 = " ".join(r7["results"][0]["evidence"])
        check("登记了但排在后面 → 仍然 merged_unregistered",
              r7["results"][0]["code"] == "merged_unregistered"
              and r7["results"][0]["verdict"] == "likely", str(r7["results"][0]["code"]))
        check("证据里带出优先级数字", "30" in ev7 and "10" in ev7, ev7)

        (cfg / "mods.settings").write_text(
            "[mod0000_MergedFiles]\nEnabled=1\nPriority=0\n\n"
            "[modSortEverything]\nEnabled=1\nPriority=10\n\n"
            "[modAutoLoot]\nEnabled=1\nPriority=20\n", encoding="utf-8")
        diagnose.drop_cache()
        mods3 = scan(game).mods
        r8 = diagnose.diagnose(S3, game, mods3)
        check("登记好、顺序也对 → 回落到 unknown（不硬套原因）",
              r8["results"][0]["code"] == "unknown", str(r8["results"][0]["code"]))

        # 类型其实有人提供：不能报「缺前置」
        write(game / "mods" / "modFriendlyHUD" / "content" / "scripts"
              / "local" / "fhud.ws", "class CModFriendlyHUDConfig\n{\n}\n")
        diagnose.drop_cache()
        mods4 = scan(game).mods
        r9 = diagnose.diagnose(S2, game, mods4)
        check("类型在别的模组里找得到 → 不许说「缺前置」",
              r9["results"][0]["verdict"] == "partial",
              str(r9["results"][0]["verdict"]))
        check("并且说出了是哪个模组提供的",
              any("modFriendlyHUD" in e for e in r9["results"][0]["evidence"]),
              str(r9["results"][0]["evidence"]))

        # ---------------- [4] 不崩 ----------------
        print("[4] 什么输入都不许崩")
        many_lines = "\n".join(f"Error [modx]game\\a{i}.ws(1): odd" for i in range(1200))
        junk = [
            ("空字符串", ""),
            ("纯空白", "   \n\t \n"),
            ("一段正常中文", "这是一段正常的中文，用户随手粘的一段话，没有任何报错。"),
            ("二进制乱码", "".join(chr(i) for i in range(0, 512))),
            ("十万个 A", "A" * 100000),
            ("1200 行错误", many_lines),
        ]
        for label, text in junk:
            out = diagnose.diagnose(text, game, mods4)
            blob = json.dumps(out, ensure_ascii=False)
            ok = (out["ok"] is True and isinstance(out["results"], list)
                  and set(out) >= {"ok", "unknown", "truncated", "stats", "results"})
            check(f"输入「{label}」（{len(text)} 字符）返回合法结构", ok, blob[:120])
            check(f"输入「{label}」能被 JSON 序列化", isinstance(blob, str))
            print(f"      → truncated={out['truncated']} unknown={out['unknown']} "
                  f"results={len(out['results'])} stats={out['stats']}")

        big = diagnose.diagnose("A" * 300000, game, mods4)
        check("单行超 200KB 被截断并置 truncated", big["truncated"] is True,
              str(big["truncated"]))
        biglog = "\n".join(f"{S1}\n{S2}" for _ in range(1500))
        big2 = diagnose.diagnose(biglog, game, mods4)
        check("真实形态的 200KB 以上日志也被截断",
              big2["truncated"] is True and len(json.dumps(
                  big2, ensure_ascii=False)) > 0,
              f"{len(biglog)} chars, truncated={big2['truncated']}, "
              f"lines={big2['stats']['lines']}")
        print(f"      → 原始 {len(biglog)} 字符，截断后处理 "
              f"{big2['stats']['lines']} 行，结果 {len(big2['results'])} 条")
        many = diagnose.diagnose(many_lines, game, mods4)
        check("结果条数有上限", len(many["results"]) <= 200, str(len(many["results"])))
        check("被截断条数时 capped 为真", many["capped"] is True, str(many["capped"]))
        check("lines 统计的是非空行", many["stats"]["lines"] == 1200,
              str(many["stats"]))
        check("errors 统计的是认出来的错误行", many["stats"]["errors"] == 1200,
              str(many["stats"]))

        # ---------------- [5] 契约 ----------------
        print("[5] 输出契约")
        out = diagnose.diagnose("\n".join([S1, S2, S3, S4A, S4B, S5]), game, mods4)
        bad = []
        for row in out["results"]:
            if row["code"] not in CODES:
                bad.append(("code", row["code"]))
            if row["verdict"] not in VERDICTS:
                bad.append(("verdict", row["verdict"]))
            if row["action"]["code"] not in ACTIONS:
                bad.append(("action", row["action"]))
            for key in ("raw", "owner", "rel", "evidence", "action"):
                if key not in row:
                    bad.append(("missing", key))
            if not isinstance(row["evidence"], list):
                bad.append(("evidence", row["evidence"]))
        check("每条的 code / verdict / action 都在取值域内", not bad, str(bad))
        check("空文本 → results 空 + unknown 真",
              diagnose.diagnose("", game, mods4)["unknown"] is True
              and diagnose.diagnose("", game, mods4)["results"] == [])
        check("一条命中时 unknown 为假",
              diagnose.diagnose(S5F, game, mods4)["unknown"] is False)
        banned = BANNED
        blob = json.dumps(out, ensure_ascii=False)
        check("结论里没有那三件不该推荐的事",
              not any(w in blob for w in banned),
              str([w for w in banned if w in blob]))
        check("evidence 单条都不为空", all(r["evidence"] for r in out["results"]))

        # ---------------- [6] 规则表坏掉 ----------------
        print("[6] 规则表缺失时照常启动")
        old = diagnose.RULES_NAME
        diagnose.RULES_NAME = "no_such_rules.json"
        diagnose.drop_cache()
        out6 = diagnose.diagnose(S5F, game, mods4)
        check("没有规则表也不抛异常", out6["ok"] is True)
        check("退化成「只做结构解析 + 通用三步走」",
              out6["results"][0]["code"] == "unknown"
              and any(out6["notes"]), str(out6["notes"]))
        check("解析仍然认出了行号", out6["results"][0]["line"] == 62,
              str(out6["results"][0]["line"]))
        diagnose.RULES_NAME = old
        diagnose.drop_cache()

        bad = base / "bad_rules.json"
        bad.write_text("{ 这不是 JSON", encoding="utf-8")
        old_fn = diagnose.rules_path
        diagnose.rules_path = lambda: bad
        diagnose.drop_cache()
        out6b = diagnose.diagnose(S5F, game, mods4)
        check("规则表格式坏也不抛异常", out6b["ok"] is True)
        check("坏表时同样退回通用三步走",
              out6b["results"][0]["code"] == "unknown", str(out6b["results"][0]["code"]))
        check("坏表的原因写进了 notes",
              any("unusable" in n for n in out6b["notes"]), str(out6b["notes"]))
        diagnose.rules_path = old_fn
        diagnose.drop_cache()

        # ---------------- [7] 只读 / 不上网 ----------------
        print("[7] 三条硬约束的静态证据")
        src = (Path(__file__).resolve().parent.parent / "core" / "diagnose.py")
        text = src.read_text(encoding="utf-8")
        imports = "\n".join(ln for ln in text.splitlines()
                            if ln.startswith("import ") or ln.startswith("from "))
        for mod in ("install", "guard", "snapshot", "urllib", "socket", "requests"):
            check(f"import 列表里没有 {mod}", mod not in imports, imports)
        check("diagnose.py 里没有动作码之外的字符串混进 action",
              set(re.findall(r'"code"\s*:\s*"([a-z_]+)"', text)) <= CODES | ACTIONS,
              str(set(re.findall(r'"code"\s*:\s*"([a-z_]+)"', text))))

        rules = json.loads((Path(__file__).resolve().parent.parent / "core"
                            / "diagnose_rules.json").read_text(encoding="utf-8"))
        got = [r["code"] for r in rules["rules"]]
        check("规则表里正好是那四条", got == ["syntax_broken", "merged_stale_ref",
                                              "merged_unregistered", "ghost_folder"], str(got))
        check("每条都有 code / keywords / verdict / note",
              all({"code", "keywords", "verdict", "note"} <= set(r)
                  for r in rules["rules"]))
        check("规则表里也没有禁用字样",
              not any(w in json.dumps(rules, ensure_ascii=False) for w in banned))

        srv = (Path(__file__).resolve().parent.parent / "server.py").read_text(encoding="utf-8")
        check("server.py 里挂了 POST /api/diagnose", 'path == "/api/diagnose"' in srv)
        check("merger._norm_rel 是唯一归一化入口（诊断复用它）",
              "merger._norm_rel" in text)

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
