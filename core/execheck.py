"""打包后的能力自检：证明这份 exe 真的有它宣称的功能。

为什么需要它
------------
1.2.9 发布的 exe 里点「浏览选择文件夹」会返回 500：打包机上的 CPython 没有
Tcl/Tk，`import tkinter` 在源码环境跑得通，打进 exe 就缺模块。当时「构建
成功」「27 套测试全绿」「exe 能启动并扫到模组」三条都通过了 —— 没有一条能
发现这个洞。

所以构建完必须问 exe 自己一句：「你现在到底能干什么？」而不是问源码。

用法（build.py 自动调用）
------------------------
    Waass-<版本>.exe --check --check-out build/execheck.json

`--windowed` 的 exe 没有 stdout，所以结论写文件、用退出码表达成败：
0 = 关键能力齐全；1 = 有关键能力缺失（不该发布）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import appinfo

# 少一个界面就整页白屏，一个都不能缺
REQUIRED_WEB = (
    "templates/index.html",
    "static/app.js",
    "static/style.css",
    "static/settings.js",
    "i18n/zh.json",
    "i18n/en.json",
)

# 这些模块缺了对应功能直接不可用（不是"降级"）
REQUIRED_MODULES = (
    "core.picker", "core.scanner", "core.localize", "core.compat",
    "core.selfcheck", "core.nexus_meta", "core.merger", "core.storage",
)


def _web_dir() -> Path:
    from .paths import RES_DIR
    return RES_DIR / "web"


def run() -> dict:
    """返回 {"ok": bool, "fatal": [...], "detail": {...}}。绝不抛异常。"""
    fatal: list[str] = []
    detail: dict = {"version": appinfo.APP_VERSION,
                    "frozen": bool(getattr(sys, "frozen", False)),
                    "python": f"{sys.version_info.major}.{sys.version_info.minor}"}

    # 1) 界面资源
    web = _web_dir()
    missing = [n for n in REQUIRED_WEB if not (web / n).is_file()]
    detail["web"] = {"dir": str(web), "missing": missing}
    if missing:
        fatal.append("web_assets")

    # 2) 模块能不能真的导入（PyInstaller 只打它分析到的 import）
    broken: dict[str, str] = {}
    for name in REQUIRED_MODULES:
        try:
            __import__(name)
        except Exception as exc:                    # noqa: BLE001
            broken[name] = f"{type(exc).__name__}: {exc}"
    detail["modules"] = {"broken": broken}
    if broken:
        fatal.append("modules_import_failed")

    # 3) 选择框：曾经就是这里出的问题
    try:
        from . import picker
        backend = picker.probe()
    except Exception as exc:                        # noqa: BLE001
        backend = ""
        detail["picker_error"] = f"{type(exc).__name__}: {exc}"
    detail["picker"] = backend or "none"
    if not backend:
        fatal.append("no_picker_backend")

    # 4) 数据目录能不能落（只算信息，不写盘：打包机上可能是只读位置）
    try:
        from .paths import DATA_DIR
        detail["data_dir"] = str(DATA_DIR)
    except Exception as exc:                        # noqa: BLE001
        detail["data_dir"] = ""
        detail["data_dir_error"] = f"{type(exc).__name__}: {exc}"

    return {"ok": not fatal, "fatal": fatal, "detail": detail}


def write_report(path: Path) -> int:
    """给 --check 用：结论写文件，返回退出码。"""
    try:
        rep = run()
    except Exception as exc:                        # noqa: BLE001 自检自己崩也得说清楚
        rep = {"ok": False, "fatal": ["check_crashed"],
               "detail": {"error": f"{type(exc).__name__}: {exc}"}}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        return 1
    return 0 if rep.get("ok") else 1
