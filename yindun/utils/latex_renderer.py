# -*- coding: utf-8 -*-
"""
LaTeX 公式渲染工具
支持 $...$（行内公式）和 $$...$$（独立公式块）
依赖 matplotlib 内置 mathtext 引擎，无需完整 LaTeX 环境
"""
import re
import hashlib
import os
from io import BytesIO
from PySide6.QtWidgets import QLabel, QHBoxLayout
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QGuiApplication


# LaTeX 行内公式：$...$，单行内匹配
INLINE_PATTERN = re.compile(r'(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)')
# LaTeX 独立公式块：$$...$$
BLOCK_PATTERN = re.compile(r'\$\$(.+?)\$\$', re.DOTALL)
# 公式块内的占位符（用于混合文本中标注公式位置）
LATEX_PLACEHOLDER_PREFIX = '\x00LATEX:'
BLOCK_PLACEHOLDER_PREFIX = '\x00LATEX_BLOCK:'


def _get_latex_cache_dir():
    """获取公式图片缓存目录"""
    cache_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'cache', 'latex')
    os.makedirs(cache_dir, exist_ok=True)
    return cache_dir


def _render_formula_to_pixmap(formula: str, fontsize=14, is_block=False) -> QPixmap:
    """
    使用 matplotlib mathtext 将 LaTeX 公式渲染为 QPixmap
    is_block=True 时增大字号并居中对齐
    """
    import matplotlib
    matplotlib.use('Agg')  # 无头模式，不弹出窗口
    import matplotlib.pyplot as plt
    import matplotlib.mathtext as mathtext

    # 字号：行内公式 14，块公式 16
    size = fontsize + (2 if is_block else 0)
    dpi = 150  # 高分辨率渲染

    try:
        # 使用 matplotlib mathtext 渲染
        fig = plt.figure(figsize=(4, 0.6 if is_block else 0.4), dpi=dpi)
        fig.text(0, 0.5, f'${formula}$', fontsize=size, math_fontfamily='cm')
        fig.tight_layout(pad=0.1)

        buf = BytesIO()
        fig.savefig(buf, format='png', dpi=dpi, bbox_inches='tight',
                    facecolor='none', transparent=True)
        buf.seek(0)
        plt.close(fig)

        pixmap = QPixmap()
        pixmap.loadFromData(buf.read())
        buf.close()

        # 清除透明背景
        if not pixmap.isNull():
            img = pixmap.toImage()
            for x in range(img.width()):
                for y in range(img.height()):
                    pixel = img.pixelColor(x, y)
                    if pixel.alpha() == 0:
                        img.setPixelColor(x, y, pixel)
            pixmap = QPixmap.fromImage(img)

        return pixmap
    except Exception:
        # 渲染失败时返回 None，调用方降级为纯文本
        try:
            plt.close()
        except Exception:
            pass
        return QPixmap()


def extract_formulas(text: str):
    """
    从文本中提取所有 LaTeX 公式，返回混合列表
    返回：[('text', '普通文本'), ('inline', '$f(x)$'), ('block', '$$...$$'), ...]
    """
    parts = []
    remaining = text

    # 优先处理块公式（$$...$$），因为行内公式匹配 $ 时会与块公式冲突
    while True:
        match = BLOCK_PATTERN.search(remaining)
        if not match:
            break
        before = remaining[:match.start()]
        if before:
            parts.append(('text', before))
        parts.append(('block', match.group(1).strip()))
        remaining = remaining[match.end():]

    # 处理剩余文本中的行内公式
    while True:
        match = INLINE_PATTERN.search(remaining)
        if not match:
            break
        before = remaining[:match.start()]
        if before:
            parts.append(('text', before))
        parts.append(('inline', match.group(1).strip()))
        remaining = remaining[match.end():]

    if remaining:
        parts.append(('text', remaining))

    return parts


def render_latex_label(formula: str, is_block=False, dark_mode=False) -> QLabel:
    """
    将 LaTeX 公式渲染为 QLabel（嵌入图片）
    渲染失败时返回 None
    """
    pixmap = _render_formula_to_pixmap(formula, fontsize=14, is_block=is_block)
    if pixmap.isNull():
        return None

    label = QLabel()
    label.setPixmap(pixmap)
    label.setAlignment(Qt.AlignCenter if is_block else Qt.AlignVCenter)

    # 设置背景色匹配气泡
    if dark_mode:
        label.setStyleSheet(
            "QLabel { background-color: #2a2a3e; border-radius: 4px; padding: 2px; }"
            if is_block else
            "QLabel { background-color: transparent; padding: 0 1px; }"
        )
    else:
        label.setStyleSheet(
            "QLabel { background-color: #f0f4ff; border-radius: 4px; padding: 2px; }"
            if is_block else
            "QLabel { background-color: transparent; padding: 0 1px; }"
        )

    return label
