"""体检工具：让你自己核实管理器到底动没动你的游戏。

用法
----
    python tools/doctor.py            看当前状态
    python tools/doctor.py snapshot   给游戏关键文件记一份指纹
    python tools/doctor.py check      跟上次指纹比对，列出被改过的文件

指纹只覆盖"管理器有可能碰到的"文件：mods.settings、菜单 xml、mods/ 与 dlc/
的目录清单。不哈希几 GB 的 bundle，运行很快。
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import guard  # noqa: E402
from core.paths import DATA_DIR, detect_game_root, game_layout  # noqa: E402

MANIFEST = DATA_DIR / "integrity.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect(root: Path) -> dict:
    """采集当前状态。全部只读。"""
    lay = game_layout(root)
    files: dict[str, dict] = {}

    for p in [lay["user_config"] / "mods.settings"]:
        if p.is_file():
            st = p.stat()
            files[str(p)] = {
                "sha256": sha256(p),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            }

    if lay["menu_xml"].is_dir():
        for p in lay["menu_xml"].glob("*.xml*"):
            st = p.stat()
            files[str(p)] = {
                "sha256": sha256(p),
                "size": st.st_size,
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            }

    def names(d: Path) -> list[str]:
        if not d.is_dir():
            return []
        return sorted(c.name for c in d.iterdir())

    return {
        "taken_at": datetime.now().isoformat(timespec="seconds"),
        "game_root": str(root),
        "files": files,
        "mods_dirs": names(lay["mods"]),
        "dlc_dirs": names(lay["dlc"]),
    }


def status() -> int:
    print("=" * 62)
    print("Waass 体检")
    print("=" * 62)

    d = guard.describe()
    print(f"\n写保护闸门")
    print(f"  测试模式      : {'开（只许写沙盒）' if d['test_mode'] else '关（正常模式）'}")
    print(f"  只读模式      : {'开' if d['readonly'] else '关'}")
    print(f"  已授权可写    : {d['allowed'] or '无 —— 任何写入都会被拒绝'}")
    print(f"  被拦截次数    : {d['denied_count']}")

    root = detect_game_root()
    print(f"\n游戏目录")
    if root is None:
        print("  未找到（需要手动指定）")
    else:
        lay = game_layout(root)
        print(f"  根目录        : {root}")
        print(f"  配置目录      : {lay['user_config']}")
        print(f"  mod 数量      : {len([c for c in lay['mods'].iterdir()]) if lay['mods'].is_dir() else 0}")
        print(f"  指纹记录      : {'有，' + json.loads(MANIFEST.read_text(encoding='utf-8'))['taken_at'] if MANIFEST.is_file() else '还没有（跑 snapshot 生成）'}")

    from core.archive import tools_report

    t = tools_report()
    print(f"\n解压能力")
    print(f"  zip           : 内置")
    print(f"  Bandizip      : {t['bandizip'] or '未安装'}")
    print(f"  7-Zip         : {t['7zip'] or '未安装'}")
    print(f"  UnRAR         : {t['unrar'] or '未安装'}")

    from core import safety

    print(f"\n游戏进程        : {'正在运行（禁止写入）' if safety.game_running() else '未运行'}")
    print(f"操作日志        : {len(safety.load_journal())} 条")
    print()
    return 0


def snapshot() -> int:
    root = detect_game_root()
    if root is None:
        print("没找到游戏目录，无法记录指纹")
        return 1
    guard.allow(DATA_DIR)
    data = collect(root)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已记录 {len(data['files'])} 个文件的指纹 → {MANIFEST}")
    print("之后随时跑 `python tools/doctor.py check` 对比。")
    return 0


def check() -> int:
    if not MANIFEST.is_file():
        print("还没有指纹记录，先跑 `python tools/doctor.py snapshot`")
        return 1
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    root = Path(old["game_root"])
    if not root.is_dir():
        print(f"游戏目录不见了：{root}")
        return 1
    new = collect(root)

    changed, added, removed = [], [], []
    for path, info in new["files"].items():
        prev = old["files"].get(path)
        if prev is None:
            added.append(path)
        elif prev["sha256"] != info["sha256"]:
            changed.append((path, prev["mtime"], info["mtime"]))
    for path in old["files"]:
        if path not in new["files"]:
            removed.append(path)

    for label, key in (("mods/ 目录", "mods_dirs"), ("dlc/ 目录", "dlc_dirs")):
        before, after = set(old[key]), set(new[key])
        if before != after:
            print(f"\n{label} 有变化")
            for n in sorted(after - before):
                print(f"  + {n}")
            for n in sorted(before - after):
                print(f"  - {n}")

    print(f"\n对比基准：{old['taken_at']}")
    if not (changed or added or removed):
        print("关键文件全部一致，没有任何改动。")
        return 0

    for p, t1, t2 in changed:
        print(f"  改过  {p}\n         {t1} → {t2}")
    for p in added:
        print(f"  新增  {p}")
    for p in removed:
        print(f"  删除  {p}")
    return 0


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "snapshot":
        return snapshot()
    if cmd == "check":
        return check()
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
