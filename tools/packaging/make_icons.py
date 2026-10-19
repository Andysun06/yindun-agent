# -*- coding: utf-8 -*-
"""生成两个 exe 的定制图标（隐盾客户端 / 控制端）。

为什么要有这个脚本（负责人反馈）：
  「我们的产品的 exe 图标能不能定制一个，比如 🛡️，然后控制端就换个颜色之类的，
    不要用 python 默认的图标」——PyInstaller 不传 `icon=` 时用的是**它自带的默认图标**
  （一个通用的"py"样式图标），和产品毫无关系，看起来就像没做完。

做法（不引外部素材、不联网）：用 Pillow 直接画**盾牌 + 对勾**（与 Web 界面里那枚
品牌盾牌是同一套形状语言：`M12 3l7 3v5.5c0 4.3-2.9 7.9-7 9-4.1-1.1-7-4.7-7-9V6l7-3z`
+ 中间的对勾），导出多尺寸 .ico（16/24/32/48/64/128/256）——Windows 在任务栏、
资源管理器、Alt+Tab 各处会挑不同尺寸，小尺寸必须单独渲染才不糊。

两个配色（同为盾牌，一眼能分清是哪个程序）：
  · 客户端 `yindun-client.ico`：品牌绿（深绿盾 + 白勾）
  · 控制端 `yindun-control.ico`：管理蓝（深蓝盾 + 白勾）

用法：python tools/packaging/make_icons.py   （产物落在 assets/icons/，随仓库提交）
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "assets" / "icons"

# 配色：盾牌主体（上浅下深，做一个很轻的渐变感）、描边、对勾
PALETTES = {
    "yindun-client": {
        "label": "客户端（品牌绿）",
        "top": (46, 168, 110),        # #2EA86E
        "bottom": (14, 106, 68),      # #0E6A44
        "edge": (8, 74, 48),          # #084A30
        "mark": (255, 255, 255),
    },
    "yindun-control": {
        "label": "控制端（管理蓝）",
        "top": (59, 130, 246),        # #3B82F6
        "bottom": (29, 78, 216),      # #1D4ED8
        "edge": (20, 52, 150),        # #143496
        "mark": (255, 255, 255),
    },
}

SIZES = (256, 128, 64, 48, 32, 24, 16)
SUPERSAMPLE = 4          # 先按 4 倍画再缩小：Windows 小尺寸下边缘才不毛糙


def shield_points(size: int) -> list:
    """盾牌轮廓（归一化坐标 → 像素）：上沿平直、两侧内收、底部收成圆角尖。"""
    def x(ratio: float) -> float:
        return size * ratio

    def y(ratio: float) -> float:
        return size * ratio

    return [
        (x(0.50), y(0.055)),                     # 顶部中点
        (x(0.885), y(0.205)),                    # 右上肩
        (x(0.885), y(0.470)),                    # 右腰
        (x(0.790), y(0.735)),                    # 右下收
        (x(0.610), y(0.885)),                    # 右下弧
        (x(0.500), y(0.945)),                    # 底部尖（圆一点）
        (x(0.390), y(0.885)),
        (x(0.210), y(0.735)),
        (x(0.115), y(0.470)),
        (x(0.115), y(0.205)),                    # 左上肩
    ]


def draw_shield(size: int, palette: dict) -> Image.Image:
    """画一枚盾牌（带对勾），返回该尺寸的 RGBA 图。"""
    big = size * SUPERSAMPLE
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    points = [(px * SUPERSAMPLE, py * SUPERSAMPLE) for px, py in shield_points(size)]

    # 主体：先用底色填充整枚盾牌，再从上到下叠加一层"上浅下深"的渐变遮罩
    draw.polygon(points, fill=palette["bottom"] + (255,))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).polygon(points, fill=255)
    gradient = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(gradient)
    top_rgb, bottom_rgb = palette["top"], palette["bottom"]
    for row in range(big):
        ratio = row / max(1, big - 1)
        color = tuple(int(top_rgb[i] + (bottom_rgb[i] - top_rgb[i]) * ratio) for i in range(3))
        gdraw.line([(0, row), (big, row)], fill=color + (255,))
    img = Image.composite(gradient, img, mask)

    # 描边：让图标在浅色与深色背景上都立得住
    draw = ImageDraw.Draw(img)
    draw.line(points + [points[0]], fill=palette["edge"] + (255,),
              width=max(1, int(big * 0.035)), joint="curve")

    # 对勾（与界面里那枚盾牌内的勾形一致：起笔中间偏左、下探、再上扬到右上）
    stroke = max(2, int(big * 0.095))
    draw.line(
        [(0.335 * big, 0.505 * big), (0.455 * big, 0.635 * big), (0.690 * big, 0.360 * big)],
        fill=palette["mark"] + (255,), width=stroke, joint="curve",
    )
    for center in ((0.335, 0.505), (0.455, 0.635), (0.690, 0.360)):
        radius = stroke / 2
        draw.ellipse(
            [center[0] * big - radius, center[1] * big - radius,
             center[0] * big + radius, center[1] * big + radius],
            fill=palette["mark"] + (255,),
        )

    return img.resize((size, size), Image.LANCZOS)


def build(name: str, palette: dict) -> Path:
    frames = [draw_shield(size, palette) for size in SIZES]
    base = max(SIZES)
    base_frame = frames[SIZES.index(base)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ico_path = OUT_DIR / f"{name}.ico"
    png_path = OUT_DIR / f"{name}.png"
    base_frame.save(png_path)                                   # README / 文档用
    base_frame.save(ico_path, format="ICO",
                    sizes=[(size, size) for size in SIZES])
    print(f"  ✅ {ico_path.relative_to(ROOT)}（{palette['label']}，"
          f"{len(SIZES)} 个尺寸：{'/'.join(str(s) for s in SIZES)}）")
    return ico_path


def main() -> int:
    print("生成 exe 图标：")
    for name, palette in PALETTES.items():
        build(name, palette)
    print("提示：spec 里的 `icon=` 指向这些文件；改图标后必须重建 exe 才会生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
