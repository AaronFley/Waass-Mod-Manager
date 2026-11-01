"""把程序打包成一个 exe（需要 PyInstaller）。

用法：
    python build.py

产物在 dist/Waass-<版本>.exe。双击就能用，没有控制台黑框：
界面由系统自带的 Edge/Chrome 内核开一个独立窗口。

数据（配置、快照、隔离区、缩略图）放在 exe 同目录的 data/ 里；
如果 exe 放在没有写权限的位置（比如 Program Files），会自动改用
%LOCALAPPDATA%\\WaassModManager。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 中文输出要在英文版 Windows（cp1252 控制台）上也打得出来：
# 这些脚本会在 import core 之前就 print，包入口的自动切换还没生效。
from core.console import enable_utf8                             # noqa: E402
enable_utf8()
SEP = ";" if sys.platform == "win32" else ":"

# 诊断规则表必须跟着 exe 走。少带它，程序照常启动、照常“能解析格式”，但四条
# 规则全灭、说不出原因，而且一声不响 —— 线上没人看得出来。
# 目标目录写 `core`：core/diagnose.py 的 rules_path() 先找 __file__ 旁边那份
# （打包后 __file__ 是 <_MEIPASS>/core/diagnose.py），找不到才退回
# RES_DIR/core/，两条路在 exe 里指的都是 <_MEIPASS>/core/。
RULES_REL = Path("core") / "diagnose_rules.json"
RULES_DEST = "core"

VERSION_TEMPLATE = """VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v4},
    prodvers={v4},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', 'Waass'),
        StringStruct('FileDescription', '{name}'),
        StringStruct('FileVersion', '{ver}'),
        StringStruct('InternalName', 'Waass'),
        StringStruct('LegalCopyright', 'MIT License'),
        StringStruct('OriginalFilename', '{exe}'),
        StringStruct('ProductName', '{name}'),
        StringStruct('ProductVersion', '{ver}')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def version_file(ver: str, name: str, exe: str) -> Path:
    parts = [int(x) for x in (ver.split(".") + ["0", "0", "0"])[:4]]
    body = VERSION_TEMPLATE.format(v4=tuple(parts), ver=ver, name=name, exe=exe)
    out = ROOT / "build" / "version_info.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


def check_rules_source() -> bool:
    """打包前先确认规则表在、能解析 —— 别等打了五分钟才发现带的是个坏文件。"""
    src = ROOT / RULES_REL
    if not src.is_file():
        print(f"缺规则表：{src}")
        print("  没有它，exe 里的诊断只会解析格式、说不出原因，而且不报错。")
        return False
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"规则表读不动：{src} → {type(exc).__name__}: {exc}")
        return False
    n = len(data.get("rules") or []) if isinstance(data, dict) else 0
    if not n:
        print(f"规则表里没有 rules：{src}")
        return False
    print(f"规则表：{src}（{src.stat().st_size} 字节，{n} 条规则）")
    return True


def check_rules_packed(build_name: str) -> bool:
    """打包后核对规则表真的进了 exe。

    只能查构建目录里的 Analysis 清单：exe 内部的解包目录要等它跑起来才有，
    而 exe 自己的 `--check` 自检（core 侧，不归这里改）不问规则表。清单里
    有这一条，才算「带上了」。
    """
    toc = ROOT / "build" / build_name / "Analysis-00.toc"
    try:
        blocks = ast.literal_eval(toc.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError, SyntaxError) as exc:
        print(f"  规则表无法核对：读不动 {toc}（{type(exc).__name__}）")
        return False
    want = f"{RULES_DEST}/diagnose_rules.json".lower()
    for block in blocks:
        if not isinstance(block, list):
            continue
        for entry in block:
            if not (isinstance(entry, tuple) and len(entry) >= 3 and entry[2] == "DATA"):
                continue
            dest, src = str(entry[0]).replace("\\", "/"), Path(entry[1])
            if dest.lower() != want:
                continue
            if not src.is_file():
                print(f"  规则表条目指向的文件不存在：{src}")
                return False
            print(f"  规则表已打进 exe：{dest} ← {src.name}（{src.stat().st_size} 字节）")
            return True
    print(f"  规则表没进 exe：Analysis 清单里没有 {RULES_DEST}/diagnose_rules.json")
    return False


def main() -> int:
    # 别只找 pyinstaller.exe：Python 升级（版本目录换名）或 pip 装到
    # .../Scripts 时，exe 既不在 PATH 也不在 python.exe 旁边，但 `-m PyInstaller`
    # 照样能用。判断「装没装」唯一可靠的办法是看模块能不能导入。
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("没装 PyInstaller。先执行：python -m pip install pyinstaller")
        return 1

    from core.appinfo import APP_NAME, APP_VERSION

    # 构建机自己有没有对话框能力，先亮出来 —— 打完之后 exe 里有没有，
    # 由下面的 smoke() 证明，这一步只是让人看到前提。
    try:
        from core import picker
        print(f"构建机选择框后端：{picker.probe() or '无（exe 会缺这个功能）'}")
    except Exception as exc:                        # noqa: BLE001
        print(f"选择框后端探测失败：{type(exc).__name__}: {exc}")

    exe_name = f"Waass-{APP_VERSION}"
    icon = ROOT / "web" / "static" / "icon.ico"
    if not icon.is_file():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], cwd=str(ROOT))

    if not check_rules_source():
        return 1

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                       # 不要控制台黑框
        "--name", exe_name,
        "--add-data", f"web{SEP}web",
        "--add-data", f"core/diagnose_rules.json{SEP}core",   # 诊断规则表，与上面同理
        "--version-file", str(version_file(APP_VERSION, APP_NAME, exe_name + ".exe")),
        # 只排掉确定用不上的科学计算库。
        # 注意这里**没有**任何「要留着 tkinter」的说法：1.2.9 就是信了这句话，
        # 而打包机上的 Python 根本没带 Tcl/Tk，结果 exe 的选目录功能是坏的。
        # 选目录现在走 Windows 自己的 COM 对话框（core/picker.py），不依赖它。
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "pandas",
        "--exclude-module", "matplotlib",
        str(ROOT / "run.py"),
    ]
    if icon.is_file():
        i = args.index("--name")
        args[i:i] = ["--icon", str(icon)]

    print(f"开始打包 {exe_name}…（第一次会比较慢）")
    r = subprocess.run(args, cwd=str(ROOT))
    if r.returncode:
        print("打包失败")
        return r.returncode

    exe = ROOT / "dist" / f"{exe_name}.exe"
    if not exe.is_file():
        print("没找到产物，看看上面的报错")
        return 1

    mb = exe.stat().st_size / 1024 / 1024
    print(f"\n完成：{exe}（{mb:.1f} MB）")

    # 先核对规则表在不在包里，再问 exe 自己的能力 —— 两条都过才许发布。
    if not check_rules_packed(exe_name):
        print("  这份 exe 不要发布。")
        return 1

    # 打包成功 ≠ 功能齐全。必须问 exe 自己一句。
    if not smoke(exe):
        return 1
    print("双击就能运行，没有黑框。数据会放在 exe 同目录的 data/ 里。")
    return 0


def smoke(exe: Path) -> bool:
    """跑一遍 exe 的能力自检（--check），缺东西就别让人发出去。"""
    out = ROOT / "build" / "execheck.json"
    out.unlink(missing_ok=True)
    print("\n打包后自检：问 exe 自己「你现在能干什么」……")
    try:
        r = subprocess.run([str(exe), "--check", "--check-out", str(out)],
                           cwd=str(ROOT), timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  起不来：{type(exc).__name__}: {exc}")
        return False
    try:
        rep = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"  没拿到自检结果（退出码 {r.returncode}）")
        return False

    d = rep.get("detail") or {}
    print(f"  版本 {d.get('version')} / picker={d.get('picker')} / "
          f"frozen={d.get('frozen')}")
    if d.get("web", {}).get("missing"):
        print(f"  缺界面文件：{', '.join(d['web']['missing'])}")
    if d.get("modules", {}).get("broken"):
        for k, v in d["modules"]["broken"].items():
            print(f"  模块导入失败：{k} → {v}")
    if rep.get("ok"):
        print("  通过：界面资源、核心模块、选择框后端都在。")
        return True
    print(f"  失败：{', '.join(rep.get('fatal') or ['unknown'])}")
    print("  这份 exe 不要发布。")
    return False


if __name__ == "__main__":
    sys.exit(main())
