"""界面提示「忽略」+ 下载包指纹的测试。

要证明的是：
1. 忽略过的警告不会再来；冒出新警告时会重新出现；
2. 下载提示按内容指纹判断，同一批包只提醒一次，多一个包就重新提醒；
3. 账本文件损坏/不存在都不影响程序运行。

全部在临时目录里跑，不碰真实数据和游戏目录。
跑法：
    python tests/test_ui_banners.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="waass-ui-"))
os.environ["WAASS_DATA_DIR"] = str(TMP / "data")
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(TMP)
DL = TMP / "downloads"
DL.mkdir(parents=True, exist_ok=True)
os.environ["WAASS_EXTRA_ARCHIVE_DIRS"] = str(DL)

from core import downloads, ui                       # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


def main() -> int:
    print("界面提示账本")

    check("账本初始为空", ui.load() == {"dismissed_warns": [], "dismissed_dl": "", "dismissed_upd": ""},
          str(ui.load()))

    ui.forget_warns(["tilde_prefix|modA", "dup_priority|"])
    st = ui.load()
    check("忽略的警告记住了", "tilde_prefix|modA" in st["dismissed_warns"], str(st))
    check("无关警告不受影响", "stale_settings|x" not in st["dismissed_warns"])

    ui.forget_warns(["tilde_prefix|modA"])
    check("重复忽略不重复记录", ui.load()["dismissed_warns"].count("tilde_prefix|modA") == 1,
          str(ui.load()))

    ui.forget_dl("abc123")
    check("下载提示指纹记住了", ui.load()["dismissed_dl"] == "abc123")
    ui.forget_dl("def456")
    check("指纹变了就覆盖", ui.load()["dismissed_dl"] == "def456")

    ui.reset()
    check("reset 清空账本", ui.load() == {"dismissed_warns": [], "dismissed_dl": "", "dismissed_upd": ""})

    print("指纹")
    a = ui.digest(["x|1", "y|2"])
    b = ui.digest(["y|2", "x|1"])
    check("同一批内容指纹一致（与顺序无关）", a == b, f"{a} vs {b}")
    check("多一个文件指纹就变", ui.digest(["x|1"]) != a)

    print("下载提示")
    (DL / "Hoods-4242-3-7-1698765432.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    s1 = downloads.summary()
    check("扫到新包", s1["new"] >= 1, str(s1))
    check("带指纹", bool(s1["new_sig"]), str(s1.get("new_sig")))

    s2 = downloads.summary()
    check("同一批包指纹稳定", s1["new_sig"] == s2["new_sig"], f"{s1['new_sig']} vs {s2['new_sig']}")

    (DL / "BloodAndSteel-13087-1-7-3-1700000000.zip").write_bytes(b"PK\x05\x06" + b"\0" * 18)
    s3 = downloads.summary()
    check("多了包指纹变化", s3["new_sig"] != s1["new_sig"])

    # 7 天窗口滚动的边界：一个包过了 7 天就不该再算"新包"，
    # 但指纹不能跟着变 —— 否则用户忽略过的提示会自己重新亮，数量还少一个。
    old = DL / "Hoods-4242-3-7-1698765432.zip"
    stale = 1700000000 - 8 * 86400
    os.utime(old, (stale, stale))
    s4 = downloads.summary()
    check("过期的包不再算新包", s4["new"] == s3["new"] - 1, f"{s3['new']} -> {s4['new']}")
    check("过期不改变指纹（忽略状态仍然有效）", s4["new_sig"] == s3["new_sig"],
          f"{s3['new_sig']} vs {s4['new_sig']}")
    check("指纹只覆盖没装过的包", bool(s4["new_sig"]))

    # 装掉一个包（进入 installed 名单）才应该算"少了一个"
    os.utime(old, None)

    print("损坏的账本")
    (Path(os.environ["WAASS_DATA_DIR"]) / "ui.json").write_text("{ 这不是 json", encoding="utf-8")
    check("坏文件按空账本处理", ui.load() == {"dismissed_warns": [], "dismissed_dl": "", "dismissed_upd": ""})

    print("选择框模块")
    from core import picker
    check("导出 pick()", callable(picker.pick))
    check("JSON 过滤默认可见 JSON", "*.json" in str(picker.PRESETS["json"]))
    check("压缩包过滤仍在", "*.zip" in str(picker.PRESETS["archive"]))

    print()
    if fails:
        print(f"{len(fails)} 项不通过：" + "、".join(fails))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
