"""游戏版本读取的沙盒测试（1.2.4 新增）。

为什么单独一个文件
------------------
侧栏要显示「游戏版本 5.01c」，这个号只能从 `witcher3.exe` 里抠出来 ——
PE 文件版本是 `5.0.16.41566`，和主菜单角标对不上。抠字符串这件事有两个
容易翻车的地方：

  1. 字符串可能跨在两次读取的边界上（按块读 90 MB，`v 5.01c` 正好被切开
     就会漏掉，而漏掉的表现是「标签不显示」，没人会怀疑到读法上）；
  2. 抠不到时不能瞎猜 —— 宁可空着，也不显示一个对不上号的版本。

所以这里造假的 exe 文件，把边界、无标记、目录结构三种情况都钉住。

跑法：
    python tests/test_gamever.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_SANDBOX = Path(tempfile.mkdtemp(prefix="waass-gamever-"))
os.environ["WAASS_TEST_MODE"] = "1"
os.environ["WAASS_SANDBOX_DIR"] = str(_SANDBOX)
os.environ["WAASS_DATA_DIR"] = str(_SANDBOX / "appdata")
os.environ["WAASS_CONFIG_DIR"] = str(_SANDBOX / "cfg")

from core import gamever  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}" + (f"   {extra}" if extra else ""))


def make_game(base: Path, exe_rel: str, payload: bytes) -> Path:
    p = base / exe_rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(payload)
    return p


def main() -> int:
    print("[1] 常规位置：bin/x64_dx12/witcher3.exe")
    g1 = _SANDBOX / "g1"
    blob = b"MZ" + b"\x00" * 4096 + b"v 5.01c\x00" + b"\x00" * 64
    make_game(g1, "bin/x64_dx12/witcher3.exe", blob)
    got = gamever.detect(g1)
    check("读到 5.01c", got["version"] == "5.01c", str(got))
    check("来源标注是 exe", got["src"] == "exe", str(got))

    print("[2] 字符串正好跨在读取边界上")
    # 把标记放到 _CHUNK 末尾前 3 个字节，前一块只剩 "v 5."，后一块才是 "01c"
    g2 = _SANDBOX / "g2"
    pad = gamever._CHUNK - 3 - len(b"MZ")
    make_game(g2, "bin/x64_dx12/witcher3.exe", b"MZ" + b"\x00" * pad + b"v 5.01c\x00")
    got2 = gamever.detect(g2)
    check("跨边界也能读到", got2["version"] == "5.01c", str(got2))

    print("[3] 4.x 时代的老路径 bin/x64/witcher3.exe")
    g3 = _SANDBOX / "g3"
    make_game(g3, "bin/x64/witcher3.exe", b"MZ\x00v 4.04\x00" + b"\x00" * 32)
    got3 = gamever.detect(g3)
    check("老路径也能找到", got3["version"] == "4.04", str(got3))

    print("[4] 抠不到就别瞎猜")
    g4 = _SANDBOX / "g4"
    make_game(g4, "bin/x64_dx12/witcher3.exe", b"MZ" + b"\x00" * 2048)
    got4 = gamever.detect(g4)
    check("没有版本标记时返回空", got4["version"] == "", str(got4))
    check("来源也空", got4["src"] == "", str(got4))

    print("[5] 没有 exe 的目录（游戏目录没设/挪走了）")
    got5 = gamever.detect(_SANDBOX / "nope")
    check("不抛异常、返回空", got5 == {"version": "", "src": "", "exe": ""}, str(got5))
    check("root 为空也不炸", gamever.detect(None)["version"] == "")

    print("[6] 缓存按 (路径, 修改时间, 大小) 失效")
    g6 = _SANDBOX / "g6"
    p6 = make_game(g6, "bin/x64_dx12/witcher3.exe", b"MZ\x00v 5.00c\x00")
    first = gamever.detect(g6)
    check("先读到 5.00c", first["version"] == "5.00c", str(first))
    p6.write_bytes(b"MZ\x00v 5.01c\x00\x00\x00")
    os.utime(p6, (os.path.getatime(p6), os.path.getmtime(p6) + 5))
    again = gamever.detect(g6)
    check("游戏更新后缓存自动失效", again["version"] == "5.01c", str(again))

    print()
    print("通过 %d / 失败 %d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
