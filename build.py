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

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
SEP = ";" if sys.platform == "win32" else ":"

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

    exe_name = f"Waass-{APP_VERSION}"
    icon = ROOT / "web" / "static" / "icon.ico"
    if not icon.is_file():
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], cwd=str(ROOT))

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--windowed",                       # 不要控制台黑框
        "--name", exe_name,
        "--add-data", f"web{SEP}web",
        "--version-file", str(version_file(APP_VERSION, APP_NAME, exe_name + ".exe")),
        # 只排掉确定用不上的科学计算库；tkinter 要留着 ——
        # 设置里的「浏览选择…」靠它弹系统的选目录框
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
    if exe.is_file():
        mb = exe.stat().st_size / 1024 / 1024
        print(f"\n完成：{exe}（{mb:.1f} MB）")
        print("双击就能运行，没有黑框。数据会放在 exe 同目录的 data/ 里。")
        return 0
    print("没找到产物，看看上面的报错")
    return 1


if __name__ == "__main__":
    sys.exit(main())
