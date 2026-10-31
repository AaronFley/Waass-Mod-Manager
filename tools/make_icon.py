"""生成程序图标：一个紫底白 W 的圆角方块。

为什么自己画
------------
打包成 exe 之后没有图标，任务栏和资源管理器里就是一个白纸，很难当成
正经软件。这里用纯标准库（zlib + 手写 PNG/ICO 容器）画一张，不引第三方库，
也不往仓库里塞一堆二进制。

为什么要有这么多档
------------------
Windows 任务栏、Alt+Tab、资源管理器、文件属性里各自要不同尺寸的图标。
只给一张大图的话，系统只能自己缩，缩出来就是糊的 —— 所以这里按需出十档，
每个像素尺寸都单独按目标大小画（而不是缩放），小尺寸还把笔画加粗一点，
这样 16/32 像素下也能看清是个 W。

ICO 里的存储格式也有讲究：256×256 用 PNG 压缩（Vista 以后都认），
小于 256 的用老式 BMP(DIB) 条目 —— 小尺寸塞 PNG 在部分 Windows 组件里
支持不完整，会退回用大图缩，同样是糊。

跑法：
    python tools/make_icon.py
产物（都在 web/static/）：
    icon.ico                                    给 exe 用（十档）
    icon.png  →  256px                          页面主图标
    icon-16/32/48/64.png                        给浏览器按需挑，避免缩放
"""
from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 中文输出要在英文版 Windows（cp1252 控制台）上也打得出来。
# 这个脚本不 import core，自己切一次，别让一句 print 把工具打崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass
OUT_DIR = ROOT / "web" / "static"
OUT_ICO = OUT_DIR / "icon.ico"

BG = (79, 70, 184)          # --accent
FG = (255, 255, 255)

# W 的四个折点（画布坐标 0~256）
# 2026-10-28 调大：原来 x 62..194 / y 84..176，只占画面的 52% × 36%，
# 缩到 16px 之后 W 只有中间 6 像素高，四周全是紫底 —— 任务栏上看着就是一小团糊。
W_PTS = [(50, 66), (94, 190), (128, 116), (162, 190), (206, 66)]
W_W = 156.0                 # W 的横向跨度，用来按比例推笔画粗细
STROKE_RATIO = 0.082        # 大尺寸下笔画半宽占 W 跨度的比例

# 小尺寸单独给一个笔画半宽下限。
# 原因：抗锯齿是按像素覆盖率算的，尺寸越小、边缘占的比重越大，
# 同样"比例正确"的笔画在小图上会被灰边吞掉，看着就发虚。
# 16~24 这几档是任务栏真正用的尺寸，优先保它们。
STROKE_HALF = {16: 1.10, 20: 1.32, 24: 1.55, 32: 1.95, 40: 2.35, 48: 2.70}

# 小于这个尺寸时，W 的笔画不再做羽化，直接按覆盖率二分（0.5 以上算实心）。
# 16px 上一条斜线只占 2 个像素，四档灰阶叠上去就成了"糊"；
# 切成硬边反而干净 —— 小图标宁可有锯齿，也不能没有轮廓。
HARD_EDGE_MAX = 24
HARD_EDGE_AT = 0.46

ICO_DIB = [16, 20, 24, 32, 40, 48, 64, 96, 128]   # 用 BMP 条目
ICO_PNG = [256]                                    # 唯一用 PNG 的一档
PNG_OUT = {16: "icon-16.png", 20: "icon-20.png", 32: "icon-32.png",
           40: "icon-40.png", 48: "icon-48.png", 64: "icon-64.png",
           256: "icon.png"}


def half_width(size: int) -> float:
    """该尺寸下 W 的笔画半宽（像素）。等比与下限取大，保证随尺寸单调变粗。"""
    return max(STROKE_RATIO * W_W * size / 256.0, STROKE_HALF.get(size, 0.0))


def _seg_dist(px: float, py: float, a, b) -> float:
    ax, ay = a
    bx, by = b
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    den = vx * vx + vy * vy
    t = 0.0 if den == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / den))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def _rrect_dist(px: float, py: float, size: float, radius: float) -> float:
    hw = hh = size / 2
    qx = abs(px - hw) - (hw - radius)
    qy = abs(py - hh) - (hh - radius)
    return math.hypot(max(qx, 0), max(qy, 0)) + min(max(qx, qy), 0) - radius


def render(size: int) -> bytes:
    """画出 size×size 的 RGBA 像素。用超采样算覆盖率，边缘才干净。"""
    ss = 4 if size <= 96 else 2          # 大图用 2 倍就够，省点时间
    n = ss * ss
    k = size / 256.0
    radius = min(56 * k, size * 0.26)
    pts = [(x * k, y * k) for x, y in W_PTS]
    half = half_width(size)

    offs = [(i + 0.5) / ss for i in range(ss)]
    px = bytearray()
    for y in range(size):
        for x in range(size):
            inside = 0
            stroke = 0
            for dy in offs:
                cy = y + dy
                for dx in offs:
                    cx = x + dx
                    if _rrect_dist(cx, cy, size, radius) > 0:
                        continue
                    inside += 1
                    if min(_seg_dist(cx, cy, pts[i], pts[i + 1])
                           for i in range(len(pts) - 1)) <= half:
                        stroke += 1
            a_box = inside / n
            w = stroke / n
            if size <= HARD_EDGE_MAX and w > 0:
                w = 1.0 if w >= HARD_EDGE_AT else 0.0
            r = BG[0] + (FG[0] - BG[0]) * w
            g = BG[1] + (FG[1] - BG[1]) * w
            b = BG[2] + (FG[2] - BG[2]) * w
            px += bytes((int(round(r)), int(round(g)), int(round(b)),
                         int(round(255 * a_box))))
    return bytes(px)


def png_bytes(size: int, rgba: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgba[y * size * 4:(y + 1) * size * 4] for y in range(size))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return (struct.pack(">I", len(body)) + tag + body
                + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def dib_bytes(size: int, rgba: bytes) -> bytes:
    """老式 32 位 BMP 条目：头 + 自下而上的 BGRA 像素 + 全零 AND 掩码。"""
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    xor = bytearray()
    for y in range(size - 1, -1, -1):
        row = rgba[y * size * 4:(y + 1) * size * 4]
        for i in range(0, len(row), 4):
            xor += bytes((row[i + 2], row[i + 1], row[i], row[i + 3]))
    mask_stride = ((size + 31) // 32) * 4
    return hdr + bytes(xor) + b"\x00" * (mask_stride * size)


def ico_bytes(entries: list[tuple[int, bytes]]) -> bytes:
    count = len(entries)
    head = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    index = b""
    body = b""
    for size, data in entries:
        dim = 0 if size >= 256 else size
        index += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32,
                             len(data), offset + len(body))
        body += data
    return head + index + body


def main() -> int:
    # 1.2.3 起图标来自设计稿（waass-icon/，深色底 + 金色 W）。这个脚本画的是
    # 旧的紫底白 W，直接跑会把它覆盖回去。留一道闸：已有 icon.ico 就不动，
    # 真要用旧版请显式加 --force。
    if OUT_ICO.exists() and "--force" not in sys.argv:
        print(f"{OUT_ICO.name} 已存在（1.2.3 起用的是设计稿图标）。"
              f"确实要用脚本重画请加 --force。")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cache: dict[int, bytes] = {}

    def pix(size: int) -> bytes:
        if size not in cache:
            cache[size] = render(size)
        return cache[size]

    entries: list[tuple[int, bytes]] = []
    for s in ICO_DIB:
        entries.append((s, dib_bytes(s, pix(s))))
    for s in ICO_PNG:
        entries.append((s, png_bytes(s, pix(s))))
    OUT_ICO.write_bytes(ico_bytes(entries))

    for s, name in PNG_OUT.items():
        (OUT_DIR / name).write_bytes(png_bytes(s, pix(s)))

    print(f"写好了 {OUT_ICO.name}：{len(entries)} 档 "
          f"({OUT_ICO.stat().st_size // 1024} KB)")
    print("写好了 " + "、".join(PNG_OUT.values()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
