"""
生成 PWA 图标
==============

用脚本画图标而不是手工做图，理由是图标会在多处复用（PWA 图标、favicon、
后续可能的小程序）。改配色或调整造型时重跑一次即可，不会出现
"某处的图标还是旧版"这种问题。

用法（用后端的 venv，那里有 PIL）::

    ..\\backend\\.venv\\Scripts\\python.exe scripts\\generate_icons.py

产出（写入 frontend/public/icons/）：
    icon-192.png            普通图标
    icon-512.png            普通图标（高分屏）
    icon-maskable-512.png   可裁剪图标（Android 自适应图标）
    favicon-32.png          浏览器标签页
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

FRONTEND_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = FRONTEND_DIR / 'public' / 'icons'

#: 主色（与前端 `--c-primary` 一致）
PRIMARY = (29, 78, 216)
PRIMARY_DARK = (30, 58, 138)
WHITE = (255, 255, 255)

#: Android 自适应图标要求前景元素落在中间 80% 的安全区内，
#: 超出部分可能被系统裁掉。maskable 版本因此把图形整体缩小。
MASKABLE_SAFE_RATIO = 0.72


def _draw_road(draw: ImageDraw.ImageDraw, size: int, scale: float = 1.0) -> None:
    """画一个简化的高速路图形：两条边缘线 + 中间虚线。

    :param scale: 图形整体缩放。1.0 时占满画布，maskable 版本传入更小的值。
    """
    unit = size * scale
    offset = (size - unit) / 2

    def px(ratio: float) -> float:
        return offset + unit * ratio

    line_width = max(2, int(unit * 0.055))

    # 道路两侧的边缘线（略带透视，下宽上窄）
    left_top, left_bottom = px(0.38), px(0.24)
    right_top, right_bottom = px(0.62), px(0.76)
    draw.line([(left_top, px(0.16)), (left_bottom, px(0.88))], fill=WHITE, width=line_width)
    draw.line([(right_top, px(0.16)), (right_bottom, px(0.88))], fill=WHITE, width=line_width)

    # 中间的虚线：由下往上逐渐变短，强化"远去"的感觉
    center_x = px(0.5)
    dash_y = px(0.86)
    dash_len = unit * 0.075
    gap = unit * 0.055
    shrink = 0.86
    while dash_y > px(0.18):
        draw.line(
            [(center_x, dash_y), (center_x, dash_y - dash_len)],
            fill=WHITE,
            width=max(2, int(line_width * 0.75)),
        )
        dash_y -= dash_len + gap
        dash_len *= shrink
        gap *= shrink


def _render(size: int, *, maskable: bool = False, rounded: bool = True) -> Image.Image:
    """渲染一个图标。

    :param maskable: 是否生成 Android 自适应图标版本。该版本**不留圆角**
        （由系统裁剪），且图形缩小到安全区内。
    """
    # 以 4 倍分辨率绘制再缩小，得到平滑边缘 —— PIL 的 draw 没有抗锯齿
    supersample = 4
    canvas_size = size * supersample
    image = Image.new('RGB', (canvas_size, canvas_size), PRIMARY)

    # 竖向渐变底色
    gradient = Image.new('RGB', (1, canvas_size))
    for y in range(canvas_size):
        ratio = y / max(canvas_size - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(
                int(PRIMARY[i] + (PRIMARY_DARK[i] - PRIMARY[i]) * ratio)
                for i in range(3)
            ),
        )
    image = gradient.resize((canvas_size, canvas_size))

    draw = ImageDraw.Draw(image)
    _draw_road(draw, canvas_size, scale=MASKABLE_SAFE_RATIO if maskable else 1.0)

    image = image.resize((size, size), Image.LANCZOS)

    # 普通图标切圆角；maskable 版本交给系统处理，不做裁剪
    if rounded and not maskable:
        mask = Image.new('L', (size, size), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(0, 0), (size - 1, size - 1)], radius=int(size * 0.22), fill=255
        )
        result = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        result.paste(image, (0, 0), mask)
        return result

    return image.convert('RGBA')


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    outputs = [
        ('icon-192.png', 192, False),
        ('icon-512.png', 512, False),
        ('icon-maskable-512.png', 512, True),
        ('favicon-32.png', 32, False),
    ]

    for name, size, maskable in outputs:
        target = OUTPUT_DIR / name
        _render(size, maskable=maskable).save(target)
        print(f'  {name:<28} {size}x{size}  {target.stat().st_size / 1024:.1f} KB')

    print(f'\n图标已写入 {OUTPUT_DIR}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
