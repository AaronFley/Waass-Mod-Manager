"""选择框：三条后端路径的降级是否干净。

背景（1.2.9 教训）：打包机的 CPython 没有 Tcl/Tk，`import tkinter` 在源码
环境跑得通，打进 exe 就缺模块 —— 用户点「浏览」直接 500，而测试全绿。
现在选目录走 Windows 自己的 COM 对话框，tkinter 只是最后兜底；三条都不通
时必须抛出带 code 的 PickError，让界面说「粘贴路径」，而不是把 ImportError
原样甩给用户。

跑法：python tests/test_picker.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

_BOX = tempfile.mkdtemp(prefix="waass-picker-")
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = _BOX
os.environ["WAASS_DATA_DIR"] = str(Path(_BOX) / "data")

from core import execheck, picker      # noqa: E402

PASS = FAIL = 0


def check(label: str, ok: bool, extra: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {label}")
    else:
        FAIL += 1
        print(f"  [--] {label} {extra}")


def main() -> int:
    print("[1] 模块本身")
    check("pick 可调用", callable(picker.pick))
    check("PickError 是 OSError（服务端按 OSError 兜底）",
          issubclass(picker.PickError, OSError))
    check("JSON 过滤器还在", "*.json" in str(picker.PRESETS["json"]))
    check("压缩包过滤器还在", "*.zip" in str(picker.PRESETS["archive"]))

    print("[2] 这台机器至少有后端能用")
    backend = picker.probe()
    check("probe() 有结果", bool(backend), backend or "(空)")
    check("报告的后端是已知值", backend in ("vista", "shbrowse", "tk"), backend)

    print("[3] 原生目录框：只建对象、不弹窗（probe 用的就是这条）")
    if sys.platform == "win32":
        got = picker._vista("", show=False)
        check("IFileOpenDialog 能创建并设好选项", got == ("vista", ""), repr(got))
        check("不弹窗时路径为空", not (got or ("", ""))[1])
    else:
        check("非 Windows 直接返回 None（交给 Tk）",
              picker._vista("", show=False) is None)

    print("[4] 三条路都不通：说人话，不是 ImportError")
    real_vista = picker._vista
    real_sh = picker._shbrowse_dir
    real_tk = sys.modules.get("tkinter", "MISSING")
    try:
        picker._vista = lambda *a, **k: None            # 原生框不可用
        picker._shbrowse_dir = lambda *a, **k: None     # 老式框也不可用
        sys.modules["tkinter"] = None                   # 模拟打包机没装 Tk
        raised = None
        try:
            picker.pick("dir")
        except picker.PickError as exc:
            raised = exc
        except Exception as exc:                        # noqa: BLE001
            raised = exc
        check("抛的是 PickError", isinstance(raised, picker.PickError),
              type(raised).__name__ if raised else "(没抛)")
        check("code 是 picker_unavailable",
              getattr(raised, "code", "") == "picker_unavailable",
              str(getattr(raised, "code", "")))
        check("不再冒出 No module named 'tkinter'",
              "No module named" not in str(raised), str(raised))

        print("[5] 原生框可用时不该碰 Tk（哪怕 Tk 也装着）")
        picker._vista = lambda *a, **k: ("vista", r"D:\SteamLibrary\The Witcher 3")
        check("直接用原生框的结果",
              picker.pick("dir") == r"D:\SteamLibrary\The Witcher 3", picker.pick("dir"))
        picker._vista = lambda *a, **k: ("vista", "")   # 用户取消
        check("取消返回空串，不后退去弹 Tk", picker.pick("dir") == "")
    finally:
        picker._vista = real_vista
        picker._shbrowse_dir = real_sh
        if real_tk == "MISSING":
            sys.modules.pop("tkinter", None)
        else:
            sys.modules["tkinter"] = real_tk

    print("[6] 打包后自检能跑，且认得出后端")
    rep = execheck.run()
    check("返回结构齐全", set(rep) == {"ok", "fatal", "detail"}, str(list(rep)))
    check("界面资源都在", not rep["detail"]["web"]["missing"],
          str(rep["detail"]["web"]))
    check("核心模块都能导入", not rep["detail"]["modules"]["broken"],
          str(rep["detail"]["modules"]))
    check("记录了 picker 后端", rep["detail"]["picker"] in ("vista", "shbrowse", "tk"),
          rep["detail"]["picker"])
    check("源码环境自检通过", rep["ok"] is True, str(rep["fatal"]))

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
