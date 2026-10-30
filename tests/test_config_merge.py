"""合并写入的边界测试。纯函数层，不碰任何真实目录。

这里每一条都对应一个真实会踩到的情况，别删。
跑法：
    python tests/test_config_merge.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.config_merge import Change, apply, diff, parse, revert, summarize  # noqa: E402

fails: list[str] = []


def check(name: str, cond: bool, extra: str = "") -> None:
    print(("  OK   " if cond else "  FAIL ") + name + ("" if cond else f"   {extra}"))
    if not cond:
        fails.append(name)


# ---------- 真实案例：Debug Console 的 general.ini ----------
print("== 案例 1：Debug Console 的 general.ini（ConfigVersion 必须不动）")
CUR = "[General]\r\nConfigVersion=7\r\n"
PKG = "[General]\r\nConfigVersion=5\r\nDBGConsoleOn=true\r\n"
ch = diff(CUR, PKG)
ops = {(c.op, c.key) for c in ch}
check("ConfigVersion 被标为跳过", ("skip", "configversion") in ops, str(ops))
check("DBGConsoleOn 记为新增", ("add", "dbgconsoleon") in ops, str(ops))
new, n = apply(CUR, ch)
check("只写入 1 条", n == 1, str(n))
check("ConfigVersion 仍是 7", "ConfigVersion=7" in new, new)
check("DBGConsoleOn 已加上", "DBGConsoleOn=true" in new, new)
check("没有出现 5", "ConfigVersion=5" not in new, new)

back, m, conf = revert(new, ch)
check("撤销后回到原样", back == CUR, repr(back))
check("撤销 1 条", m == 1, str(m))
check("无冲突", not conf, str(conf))

# ---------- 包内是完整文件，也不能删游戏的键 ----------
print("\n== 案例 2：包内文件缺了游戏的键，不能当删除处理")
CUR2 = "[Visuals]\r\nGamma=1\r\nMovieFramerate=30.0\r\nMovieUbersampling=true\r\n"
PKG2 = "[Visuals]\r\nGamma=1\r\nBloomBeforeTonemap=true\r\n"
ch2 = diff(CUR2, PKG2)
check("只产生 1 条新增", len([c for c in ch2 if c.op != "skip"]) == 1,
      str([(c.op, c.key) for c in ch2]))
check("没有删除项", all(c.op in ("add", "mod", "skip") for c in ch2))
new2, _ = apply(CUR2, ch2)
check("MovieUbersampling 保住了", "MovieUbersampling=true" in new2, new2)
check("MovieFramerate 保住了", "MovieFramerate=30.0" in new2, new2)
check("新增项已写入", "BloomBeforeTonemap=true" in new2, new2)

# ---------- 修改已有键 ----------
print("\n== 案例 3：改已有键的值")
CUR3 = "[Rendering]\r\nTextureMemoryBudget = 1200\r\nMaxTextureSize=4096\r\n"
PKG3 = "[Rendering]\r\nTextureMemoryBudget=3000\r\n"
ch3 = diff(CUR3, PKG3)
check("识别为修改", len(ch3) == 1 and ch3[0].op == "mod", str(ch3))
new3, _ = apply(CUR3, ch3)
check("值已换成 3000", "TextureMemoryBudget = 3000" in new3, repr(new3))
check("等号旁空格保留", "TextureMemoryBudget = 3000" in new3, repr(new3))
check("其它键没动", "MaxTextureSize=4096" in new3)
back3, _, _ = revert(new3, ch3)
check("撤销回到 1200", "TextureMemoryBudget = 1200" in back3, repr(back3))

# ---------- 撤销时被第三方改过 ----------
print("\n== 案例 4：撤销时发现值被别人改过，不硬还原")
CUR4 = "[A]\r\nX=1\r\n"
PKG4 = "[A]\r\nX=2\r\n"
ch4 = diff(CUR4, PKG4)
applied, _ = apply(CUR4, ch4)
tampered = applied.replace("X=2", "X=9")
back4, cnt4, conf4 = revert(tampered, ch4)
check("跳过不撤销", cnt4 == 0, str(cnt4))
check("报告冲突", len(conf4) == 1 and conf4[0]["found"] == "9", str(conf4))
check("值保持 9 没被动", "X=9" in back4, repr(back4))
forced, cnt5, _ = revert(tampered, ch4, force=True)
check("强制撤销才回到 1", "X=1" in forced and cnt5 == 1, repr(forced))

# ---------- CRLF / BOM / 结尾无换行 ----------
print("\n== 案例 5：行尾符与 BOM")
CUR5 = "[S]\r\nA=1\r\nB=2\r\n"
PKG5 = "[S]\r\nC=3\r\n"
new5, _ = apply(CUR5, diff(CUR5, PKG5))
check("新增行用 CRLF", "C=3\r\n" in new5, repr(new5))
check("没有混入裸 LF", "\n" not in new5.replace("\r\n", ""), repr(new5))

CUR5b = "[S]\nA=1\n"
new5b, _ = apply(CUR5b, diff(CUR5b, "[S]\nB=2\n"))
check("原本是 LF 就还是 LF", "B=2\n" in new5b and "\r" not in new5b, repr(new5b))

CUR5c = "[S]\r\nA=1"          # 结尾没有换行
new5c, _ = apply(CUR5c, diff(CUR5c, "[S]\r\nB=2\r\n"))
check("结尾不额外补空行", new5c.endswith("B=2\r\n") or not new5c.endswith("\r\n\r\n"),
      repr(new5c))

# ---------- 重复键 / 值里带等号 / 节名带斜杠 ----------
print("\n== 案例 6：畸形写法")
CUR6 = "[Rendering/RT/PathTracer]\r\nPTNumBounces=2\r\n"
PKG6 = "[Rendering/RT/PathTracer]\r\nPTNumBounces=1\r\n"
new6, _ = apply(CUR6, diff(CUR6, PKG6))
check("带斜杠的节名认得出", "PTNumBounces=1" in new6, repr(new6))

CUR7 = "[S]\r\nA=1\r\nA=2\r\n"
PKG7 = "[S]\r\nA=5\r\n"
ch7 = diff(CUR7, PKG7)
check("重复键只算一条改动", len(ch7) == 1, str(ch7))
new7, _ = apply(CUR7, ch7)
check("重复键两行都改到", new7.count("A=5") == 2, repr(new7))

CUR8 = "[S]\r\nUrl=https://a.b/?x=1&y=2\r\n"
PKG8 = "[S]\r\nUrl=https://a.b/?x=9&y=2\r\n"
new8, _ = apply(CUR8, diff(CUR8, PKG8))
check("值里的 = 不影响切分", "x=9" in new8, repr(new8))

# ---------- 没有节头的键 ----------
print("\n== 案例 7：没有节头")
CUR9 = "A=1\r\n"
PKG9 = "B=2\r\n"
new9, _ = apply(CUR9, diff(CUR9, PKG9))
check("键加在后面", "A=1" in new9 and "B=2" in new9, repr(new9))
check("没凭空造出节头", "[S]" not in new9 and new9.count("[") == 0, repr(new9))

# ---------- 新节插入 ----------
print("\n== 案例 8：要写进一个原本不存在的节")
CUR10 = "[S]\r\nA=1\r\n"
PKG10 = "[T]\r\nB=2\r\n"
new10, _ = apply(CUR10, diff(CUR10, PKG10))
check("新节被追加", "[T]" in new10 and "B=2" in new10, repr(new10))
check("原节完好", "[S]" in new10 and "A=1" in new10, repr(new10))

# ---------- 删除自己新建的节 ----------
print("\n== 案例 8b：整节都是 mod 加的，撤销时连节头一起清掉")
CUR8b = "[S]\r\nA=1\r\n"
PKG8b = "[S]\r\nA=1\r\n[AutoLoot]\r\nradius=15\r\n"
ch8b = diff(CUR8b, PKG8b)
new8b, _ = apply(CUR8b, ch8b)
check("节被加上", "[AutoLoot]" in new8b and "radius=15" in new8b, repr(new8b))
back8b, _, _ = revert(new8b, ch8b)
check("撤销后不留空节头", "AutoLoot" not in back8b, repr(back8b))
check("原节完好", "[S]" in back8b and "A=1" in back8b, repr(back8b))
check("撤销后和原文件一致", back8b == CUR8b, repr(back8b))

# 节原本就有、只是被改了值：节头必须留着
CUR8c = "[AutoLoot]\r\nradius=15\r\n"
PKG8c = "[AutoLoot]\r\nradius=40\r\n"
ch8c = diff(CUR8c, PKG8c)
new8c, _ = apply(CUR8c, ch8c)
back8c, _, _ = revert(new8c, ch8c)
check("原有节撤销后节头还在", "[AutoLoot]" in back8c, repr(back8c))
check("原有节撤销后值回原样", "radius=15" in back8c, repr(back8c))

# ---------- 数值与布尔等价 ----------
print("\n== 案例 9：1 和 1.0 不算改动")
CUR11 = "[S]\r\nGamma=1\r\nFlag=true\r\n"
PKG11 = "[S]\r\nGamma=1.0\r\nFlag=True\r\n"
ch11 = diff(CUR11, PKG11)
check("没有变化", len(ch11) == 0, str([(c.op, c.key) for c in ch11]))

# ---------- 幂等 ----------
print("\n== 案例 10：重复安装同一份变更不会叠加")
ch12 = diff(CUR, PKG)
once, _ = apply(CUR, ch12)
twice, n12 = apply(once, ch12)
check("第二次写入 0 条", n12 == 0, str(n12))
check("内容不再变化", once == twice, repr(twice))

# ---------- 注释与空行保留 ----------
print("\n== 案例 11：注释和空行原样保留")
CUR13 = "; 这是注释\r\n[General]\r\n\r\n; 另一个注释\r\nConfigVersion=7\r\n"
PKG13 = "[General]\r\nDBGConsoleOn=true\r\n"
new13, _ = apply(CUR13, diff(CUR13, PKG13))
check("注释都在", new13.count(";") == 2, repr(new13))
check("空行还在", "\r\n\r\n" in new13, repr(new13))
check("ConfigVersion 没动", "ConfigVersion=7" in new13, repr(new13))

# ---------- 摘要 ----------
print("\n== 案例 12：给界面看的摘要")
s = summarize(diff(CUR, PKG))
check("摘要数字对", s["add"] == 1 and s["mod"] == 0 and s["skip"] == 1, str(s))
check("摘要文字可读", "新增 1 项" in s["text"], s["text"])
check("跳过项带原因", bool(s["skipped"]) and "游戏自己管理" in s["skipped"][0]["why"],
      str(s["skipped"]))

# ---------- 空输入 ----------
print("\n== 案例 13：空文件 / 空变更")
check("空 vs 空", diff("", "") == [], str(diff("", "")))
_got = apply("", diff("", "[A]\nB=1\n"))[0].replace("\r\n", "\n").strip()
check("空文件加键", _got == "[A]\nB=1", repr(_got))
check("空变更不动文件", apply(CUR, [])[0] == CUR)
check("空撤销不动文件", revert(CUR, [])[0] == CUR)

# ---------- 解析器本身 ----------
print("\n== 案例 14：解析器")
ls = parse("[A]\r\nK=V\r\n;c\r\n\r\n[B]\r\nX=1\r\n")
check("行数对", len(ls) == 6, str(len(ls)))
check("节归属正确", [l.section for l in ls] == ["a", "a", "a", "a", "b", "b"],
      str([l.section for l in ls]))
check("键值行标对", [l.is_kv for l in ls] == [False, True, False, False, False, True],
      str([l.is_kv for l in ls]))

print()
if fails:
    print(f"{len(fails)} 项失败：")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("全部通过")
