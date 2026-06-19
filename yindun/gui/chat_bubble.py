# -*- coding: utf-8 -*-
"""
隐盾 V2.1.0 — 💬 纯原生流式气泡组件
彻底抛弃 QTextBrowser 网页标签，改用原生布局与自适应物理卡片
升级：
1. 引入 QTextDocument 理想宽度计算矩阵，锁死 75% 阈值前的绝对不换行算法
2. 全面重构时间戳色彩矩阵，实现绿卡与白卡的高对比度现代化视觉
3. 支持 LaTeX 公式渲染（$...$ 行内 / $$...$$ 独立公式）
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextDocument

from yindun.utils.latex_renderer import extract_formulas, render_latex_label


class ChatBubble(QWidget):
    """单条对话的高级原生弹性气泡组件 (升级：支持 LaTeX 公式渲染)"""
    def __init__(self, role, text, timestamp, window_width=420, dark_mode=False, meta_info=""):
        super().__init__()
        self._is_user = (role == "user")
        self._dark_mode = dark_mode
        self._meta_info = meta_info  # 如 "快速   1m36s"
        # 缓存原始 markdown 文本
        self._raw_text = text
        self._original_text = text
        self._current_indent = 4
        self._window_width = window_width
        self._max_bubble_width = int(window_width * 0.75) - 35
        # 记录所有内容片段标签（含 LaTeX 图片），用于样式更新
        self._content_labels = []  # [{'widget': QLabel, 'type': 'text'|'latex'}]

        # 1. 建立整行水平弹性卡片流布局
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 5, 14, 5)
        row_layout.setSpacing(0)

        # 2. 创建承载气泡的物理外壳卡片
        self.bubble_card = QFrame()

        # 3. 创建气泡内部的纵向文本排版系统
        self.card_layout = QVBoxLayout(self.bubble_card)
        self.card_layout.setContentsMargins(12, 9, 12, 9)
        self.card_layout.setSpacing(5)

        # 注入内容片段（文本 + LaTeX 图片）
        self._current_indent = 2 if self._max_bubble_width < 320 else 4
        self._build_content(self._raw_text)

        # 注入时间戳微型标签
        self.time_label = QLabel(timestamp)

        # AI 气泡底部信息栏（模式 + 耗时）
        self.meta_label = None
        if not self._is_user and self._meta_info:
            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(0, 2, 0, 0)
            self.meta_label = QLabel(self._meta_info)
            self.meta_label.setAlignment(Qt.AlignRight)
            meta_row.addWidget(self.meta_label)
            self.card_layout.addLayout(meta_row)

        # 应用角色对应的皮肤与对齐策略
        self._apply_style()

        # 4. 装配外层水平流向
        if self._is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(self.bubble_card)
        else:
            row_layout.addWidget(self.bubble_card)
            row_layout.addStretch(1)

    # ─────────────────────────── 内容构建 ───────────────────────────

    def _build_content(self, text):
        """从文本中提取 LaTeX 公式并构建内容片段"""
        # 清除旧内容
        for entry in self._content_labels:
            w = entry['widget']
            self.card_layout.removeWidget(w)
            w.deleteLater()
        self._content_labels.clear()

        # 提取公式片段
        parts = extract_formulas(text)
        for kind, content in parts:
            if kind == 'text':
                # 普通文本片段 → QLabel + Markdown
                label = QLabel()
                label.setTextFormat(Qt.MarkdownText)
                label.setWordWrap(True)
                label.setText(self._adjust_indent(content, self._current_indent))
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setFixedWidth(self._max_bubble_width)
                self.card_layout.addWidget(label)
                self._content_labels.append({'widget': label, 'type': 'text'})
            else:
                # LaTeX 公式片段 → QLabel + 渲染图片
                is_block = (kind == 'block')
                latex_label = render_latex_label(content, is_block=is_block, dark_mode=self._dark_mode)
                if latex_label is None:
                    # 渲染失败，降级为纯文本显示
                    label = QLabel()
                    label.setWordWrap(True)
                    label.setFixedWidth(self._max_bubble_width)
                    display_text = f"📐 {content}"
                    label.setText(display_text)
                    self.card_layout.addWidget(label)
                    self._content_labels.append({'widget': label, 'type': 'text'})
                else:
                    self.card_layout.addWidget(latex_label)
                    self._content_labels.append({'widget': latex_label, 'type': 'latex'})
                    # LaTeX 块公式下方加一点间距
                    if is_block:
                        spacer = QWidget()
                        spacer.setFixedHeight(4)
                        self.card_layout.addWidget(spacer)

        # 计算理想宽度（基于所有文本片段）
        ideal = self._compute_ideal_width()
        return ideal

    def _compute_ideal_width(self):
        """计算所有文本片段的理想宽度（忽略 LaTeX 图片，以文本宽度为主）"""
        max_text_width = 0
        for entry in self._content_labels:
            if entry['type'] == 'text':
                doc = QTextDocument()
                doc.setDefaultFont(entry['widget'].font())
                doc.setMarkdown(entry['widget'].text())
                w = int(doc.idealWidth()) + 12
                if w > max_text_width:
                    max_text_width = w
        self._ideal_width = max_text_width
        return max_text_width

    # ─────────────────────────── 样式应用 ───────────────────────────

    def _text_style(self):
        """获取当前主题下文本标签的样式表（子类气泡共享）"""
        if self._is_user:
            return "color: #000000; font-size: 13px; font-weight: 500; border: none; background: transparent;"
        elif self._dark_mode:
            return "color: #e0e0e0; font-size: 13px; border: none; background: transparent;"
        else:
            return "color: #1e293b; font-size: 13px; border: none; background: transparent;"

    def _card_bg(self):
        """获取当前主题下气泡背景"""
        if self._is_user:
            return "#95ec69", "rgba(7, 193, 96, 30)"
        elif self._dark_mode:
            return "#2a2a3e", "#3a3a50"
        else:
            return "#ffffff", "#e5e7eb"

    def _apply_style(self):
        # 先清除旧阴影
        if self.bubble_card.graphicsEffect() is not None:
            self.bubble_card.setGraphicsEffect(None)

        bg, border = self._card_bg()
        self.bubble_card.setStyleSheet(
            f"QFrame {{ background-color: {bg}; border: 1px solid {border}; "
            f"border-radius: 12px; {'border-bottom-right-radius: 2px;' if self._is_user else 'border-bottom-left-radius: 2px;'} }}"
        )

        # 同步所有内容片段的样式
        for entry in self._content_labels:
            w = entry['widget']
            if entry['type'] == 'text':
                w.setStyleSheet(self._text_style())
            elif entry['type'] == 'latex':
                # LaTeX 图片背景随主题
                if self._dark_mode:
                    w.setStyleSheet(
                        "QLabel { background-color: #2a2a3e; border-radius: 4px; padding: 2px; }"
                    )
                else:
                    w.setStyleSheet(
                        "QLabel { background-color: #f0f4ff; border-radius: 4px; padding: 2px; }"
                    )

        # 时间戳
        if self._is_user:
            ts_color = "#2c5a16"
            ts_align = Qt.AlignRight
        else:
            ts_color = "#888" if self._dark_mode else "#576574"
            ts_align = Qt.AlignLeft
        self.time_label.setStyleSheet(
            f"font-size: 10px; color: {ts_color}; font-weight: 500; border: none; background: transparent;"
        )
        self.time_label.setAlignment(ts_align)

        # 元信息栏
        if self.meta_label:
            meta_color = "#6e80ff"
            self.meta_label.setStyleSheet(
                f"font-size: 10px; color: {meta_color}; font-weight: 500; border: none; background: transparent;"
            )

        # 浅色模式 AI 气泡加阴影
        if not self._is_user and not self._dark_mode:
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(8)
            shadow.setColor(QColor(0, 0, 0, 12))
            shadow.setOffset(0, 2)
            self.bubble_card.setGraphicsEffect(shadow)

    def set_dark_mode(self, dark: bool):
        """切换深色/浅色主题。AI 气泡需要重绘，用户气泡保持绿色不动。"""
        if self._dark_mode == dark:
            return
        self._dark_mode = dark
        if not self._is_user:
            self._apply_style()

    # ─────────────────────────── 动态更新 ───────────────────────────

    def update_text(self, new_text, new_meta_info=None):
        """动态更新气泡内容（用于思考过程的渐进输出）"""
        self._raw_text = new_text
        self._original_text = new_text
        self._build_content(new_text)

        if new_meta_info is not None and self.meta_label is not None:
            self.meta_label.setText(new_meta_info)

    def update_width(self, new_window_width):
        """当窗口宽度变化时，更新所有内容片段的宽度"""
        self._window_width = new_window_width
        self._max_bubble_width = int(new_window_width * 0.75) - 35

        # 重新调整缩进
        target_indent = 2 if self._max_bubble_width < 320 else 4
        if target_indent != self._current_indent:
            self._current_indent = target_indent
            # 重建内容以应用新缩进
            self._build_content(self._original_text)

        # 更新文本片段宽度
        for entry in self._content_labels:
            if entry['type'] == 'text':
                entry['widget'].setFixedWidth(self._max_bubble_width)

    # ─────────────────────────── 工具 ───────────────────────────

    def _adjust_indent(self, text, target_indent):
        """根据目标缩进级别调整文本中的代码块缩进"""
        if target_indent == 4:
            return text
        lines = text.split('\n')
        result = []
        for line in lines:
            if line.startswith('    '):
                result.append(line[2:])
            elif line.startswith('  '):
                result.append(line)
            else:
                result.append(line)
        return '\n'.join(result)


class StatusBanner(QWidget):
    """常驻大厅中心的系统通知与环境状态条"""
    def __init__(self, text, dark_mode=False):
        super().__init__()
        self._dark_mode = dark_mode

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        self.lbl = QLabel(text)
        self.lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl)

        self._apply_style()

    def set_dark_mode(self, dark: bool):
        if self._dark_mode == dark:
            return
        self._dark_mode = dark
        self._apply_style()

    def _apply_style(self):
        color = '#888' if self._dark_mode else '#94a3b8'
        self.lbl.setStyleSheet(
            f"font-size: 11px; color: {color}; background: transparent; border: none;"
        )
