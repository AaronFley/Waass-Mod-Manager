"""在临时沙盒里测写操作。绝不碰真实游戏目录。

跑法：
    python tests/test_actions.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_sandbox(base: Path) -> tuple[Path, Path]:
    """造一个最小可用的假游戏目录 + 假配置目录。"""
    game = base / "game"
    (game / "bin" / "x64_dx12").mkdir(parents=True)
    (game / "bin" / "x64_dx12" / "witcher3.exe").write_bytes(b"MZ")
    (game / "mods").mkdir()
    (game / "dlc").mkdir()

    for name in ("modAlpha", "modBeta", "~modGamma"):
        d = game / "mods" / name
        (d / "content" / "scripts").mkdir(parents=True)
        (d / "content" / "scripts" / "x.ws").write_text("// test", encoding="utf-8")

    cfg = base / "cfg"
    cfg.mkdir()
    (cfg / "mods.settings").write_text(
        "[modAlpha]\nEnabled=1\nPriority=10\n\n[modBeta]\nEnabled=1\nPriority=20\n",
        encoding="utf-8")
    return game, cfg


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main() -> int:
    fails = []

    def check(name, cond, extra=""):
        print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"  {extra}"))
        if not cond:
            fails.append(name)

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        game, cfg = make_sandbox(base)
        # 三道隔离：配置目录、游戏目录、程序数据目录
        os.environ["WAASS_CONFIG_DIR"] = str(cfg)
        os.environ["WAASS_GAME_DIR"] = str(game)
        os.environ["WAASS_DATA_DIR"] = str(base / "appdata")
        # 写保护闸门：开测试模式，唯一允许写的范围就是这个临时目录
        os.environ["WAASS_TEST_MODE"] = "1"
        os.environ["WAASS_SANDBOX_DIR"] = str(base)

        from core import actions, guard, safety
        from core.paths import game_layout
        from core.settings_ini import SettingsFile

        lay = game_layout(game)
        settings_path = lay["user_config"] / "mods.settings"

        print("\n[1] 禁用 modBeta（只改文件夹名，不碰 mods.settings）")
        before = read(settings_path)
        actions.set_enabled(game, "modBeta", False)
        check("文件夹加了 ~", (game / "mods" / "~modBeta").is_dir())
        check("原文件夹不在了", not (game / "mods" / "modBeta").exists())
        check("mods.settings 一个字节都没动", read(settings_path) == before)
        s = SettingsFile.load(settings_path)
        check("Priority 原样留着（重新启用后顺序不变）", s.get("modBeta")["priority"] == 20)
        check("文件仍是纯 INI", read(settings_path).startswith("["))

        print("\n[2] 撤消")
        ok, msg = safety.undo_last()
        check("撤销成功", ok, msg)
        check("文件夹改回来了", (game / "mods" / "modBeta").is_dir())
        check("~ 版本不在了", not (game / "mods" / "~modBeta").exists())

        print("\n[3] 启用 ~ 前缀的 modGamma（把文件夹名改回来）")
        r = actions.set_enabled(game, "modGamma", True)
        check("文件夹去掉 ~", (game / "mods" / "modGamma").is_dir())
        check("确实改名了一次", r["renamed"] is True, str(r))
        # mods.settings 里本来就没有 modGamma 的条目，启用时也不该凭空补一条
        check("mods.settings 没有多出条目",
              SettingsFile.load(settings_path).get("modGamma") is None)
        ok, msg = safety.undo_last()
        check("撤销后 ~ 回来", (game / "mods" / "~modGamma").is_dir())
        check("撤销后条目消失", SettingsFile.load(settings_path).get("modGamma") is None)

        print("\n[4] 调顺序 + 重排")
        actions.set_priority(game, "modAlpha", 55)
        check("modAlpha 优先级 55", SettingsFile.load(settings_path).get("modAlpha")["priority"] == 55)
        actions.reorder(game, ["modAlpha", "modBeta", "modGamma"])
        s = SettingsFile.load(settings_path)
        check("重排后 10/20/30",
              [s.get(k)["priority"] for k in ("modAlpha", "modBeta", "modGamma")] == [10, 20, 30])

        print("\n[4b] 重排时保住合订本（2026-10-27 事故)")
        merged_dir = game / "mods" / "mod0000_MergedFiles" / "content" / "scripts"
        merged_dir.mkdir(parents=True)
        (merged_dir / "x.ws").write_text("// merged", encoding="utf-8")
        mdir = game / "Merger"
        mdir.mkdir()
        (mdir / "MergeInventory.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<MergeInventory>\n  <Merge>\n'
            '    <RelativePath>game\\player\\x.ws</RelativePath>\n'
            '    <IncludedMod Hash="00" ModIoId="0">modAlpha</IncludedMod>\n'
            '    <MergedModName>mod0000_MergedFiles</MergedModName>\n  </Merge>\n'
            '</MergeInventory>\n', encoding="utf-8")
        check("起步时没有合订本条目",
              SettingsFile.load(settings_path).get("mod0000_MergedFiles") is None)
        actions.reorder(game, ["modAlpha", "modBeta", "modGamma"])
        s1 = SettingsFile.load(settings_path)
        sec = s1.get("mod0000_MergedFiles")
        check("重排后自动补上合订本条目", sec is not None)
        others = [x["priority"] for x in s1.sections
                  if x["name"] != "mod0000_MergedFiles"]
        check("合订本比所有 mod 靠前", bool(sec) and sec["priority"] < min(others),
              f"{sec} vs {others}")
        check("用户顺序没被改坏",
              [s1.get(k)["priority"] for k in ("modAlpha", "modBeta", "modGamma")]
              == [10, 20, 30])

        print("\n[5] 游戏运行时拒绝写入")
        orig = safety.game_running
        safety.game_running = lambda: True
        try:
            actions.set_enabled(game, "modAlpha", False)
            check("应抛 GameRunning", False)
        except safety.GameRunning:
            check("已拒绝", True)
        finally:
            safety.game_running = orig

        print("\n[6] 输出格式严格性")
        text = read(settings_path)
        check("无分号注释", ";" not in text)
        check("无空 Priority 行", "Priority=None" not in text)
        check("段间恰好一个空行", "\n\n\n" not in text)

        print("\n[7] 写保护闸门（沙盒外一律拒绝）")
        outside = Path(os.environ["TEMP"]) / "waass_should_never_exist.txt"
        try:
            guard.assert_can_write(outside, "越界测试")
            check("应拒绝沙盒外的路径", False, "居然放行了")
        except guard.WriteDenied:
            check("沙盒外被拒绝", True)
        check("拒绝后文件确实没生成", not outside.exists())

        real_cfg = Path(r"D:\1\The Witcher 3\mods.settings")
        check("真实配置路径不可写", not guard.is_writable(real_cfg))
        check("游戏 mods 目录不可写",
              not guard.is_writable(r"D:\game\steam\steamapps\common\The Witcher 3\mods"))

        print("\n[7b] 批量开关：一次操作、一次撤销")
        before_j = len(safety.load_journal())
        before_s = read(settings_path)
        r = actions.set_enabled_many(game, ["modAlpha", "modBeta"], False)
        check("返回改动数 2", r["count"] == 2, str(r))
        check("两个都改了名", r["renamed"] == 2, str(r["renamed"]))
        check("两个文件夹都带 ~",
              (game / "mods" / "~modAlpha").is_dir() and (game / "mods" / "~modBeta").is_dir())
        j = safety.load_journal()
        check("日志只多出一条", len(j) == before_j + 1, f"{before_j} -> {len(j)}")
        check("mods.settings 一个字节都没动", read(settings_path) == before_s)
        s = SettingsFile.load(settings_path)
        check("合订本仍然最靠前",
              s.get("mod0000_MergedFiles")["priority"]
              < min(x["priority"] for x in s.sections if x["name"] != "mod0000_MergedFiles"))
        ok, msg = safety.undo_last()
        check("一次撤销就全回来", ok, msg)
        check("两个文件夹都改回原名",
              (game / "mods" / "modAlpha").is_dir() and (game / "mods" / "modBeta").is_dir())

        print("\n[7c] 批量开关的前置校验（要么全改，要么一个都不改）")
        try:
            actions.set_enabled_many(game, ["modAlpha", "modDoesNotExist"], False)
            check("应抛 FileNotFoundError", False)
        except FileNotFoundError:
            check("已拒绝", True)
        s = SettingsFile.load(settings_path)
        check("同批里合法的那个也没被改", s.get("modAlpha")["enabled"] is True)

        print("\n[7d] 批量启用 ~ 前缀（改名 + 去重）")
        r = actions.set_enabled_many(game, ["modGamma", "modGamma"], True)
        check("重复的 key 只算一次", r["count"] == 1, str(r))
        check("改名计数 1", r["renamed"] == 1, str(r["renamed"]))
        check("文件夹去掉 ~", (game / "mods" / "modGamma").is_dir())
        ok, msg = safety.undo_last()
        check("撤销后 ~ 回来", ok and (game / "mods" / "~modGamma").is_dir(), msg)

        print("\n[7e] 重排只动指定那几个（已禁用的号不能跟着变）")
        # 模拟界面上的情况：已禁用的模组排在最后，它们不该被重排波及。
        s = SettingsFile.load(settings_path)
        s.set_state("modDelta", priority=500, enabled=False)
        s.save()
        actions.reorder(game, ["modBeta", "modAlpha"],
                        prios=[s.get("modAlpha")["priority"], s.get("modBeta")["priority"]])
        s2 = SettingsFile.load(settings_path)
        check("modBeta 拿到原来的小号", s2.get("modBeta")["priority"] < s2.get("modAlpha")["priority"],
              f'{s2.get("modBeta")["priority"]} vs {s2.get("modAlpha")["priority"]}')
        check("没进重排的 modDelta 一个数字都没动",
              s2.get("modDelta")["priority"] == 500, str(s2.get("modDelta")["priority"]))
        check("合订本还是最靠前",
              s2.get("mod0000_MergedFiles")["priority"]
              < min(x["priority"] for x in s2.sections if x["name"] != "mod0000_MergedFiles"))

        for bad, why in (([10], "长度不一致"), ([10, 10], "号重复"), ([10, -5], "负数")):
            try:
                actions.reorder(game, ["modAlpha", "modBeta"], prios=bad)
                check(f"应拒绝（{why}）", False)
            except ValueError:
                check(f"已拒绝：{why}", True)

        print("\n[7f] 重排里出现不存在的 mod：必须报错，不能偷偷写幽灵条目")
        # 真实场景：另一个窗口刚把某个 mod 删了，这边还拖着它的卡片在排序。
        # set_state() 默认 create=True，不校验就会凭空写出一条 [modGhost]，
        # 游戏里变成「找不到模组」，而界面还回 ok。
        before = settings_path.read_bytes()
        for bad in (["modGhost"], ["modAlpha", "modNope"]):
            try:
                actions.reorder(game, bad)
                check(f"应拒绝 {bad}", False)
            except ValueError as e:
                check(f"已拒绝 {bad} → {e}", True)
        check("mods.settings 一个字节都没动", settings_path.read_bytes() == before)

        print("\n[8] 只读开关")
        os.environ["WAASS_READONLY"] = "1"
        try:
            guard.assert_can_write(settings_path, "只读测试")
            check("只读模式下应拒绝沙盒内路径", False)
        except guard.WriteDenied:
            check("只读模式生效", True)
        finally:
            os.environ.pop("WAASS_READONLY", None)

    print("\n" + ("全部通过" if not fails else f"{len(fails)} 项失败：" + "、".join(fails)))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
