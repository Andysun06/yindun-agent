# -*- coding: utf-8 -*-
"""
隐盾 V2.2 — 聊天气泡组件

1. 微信式宽度自适应：短内容气泡窄，长内容自动换行到 75% 窗口宽度
2. 深色/浅色主题自适应
3. 底部元信息行（左侧：模式+耗时，右侧：时间戳）
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextDocument


class ChatBubble(QWidget):
    def __init__(self, role, text, timestamp, window_width=420, dark_mode=False, meta_info=""):
        super().__init__()
        self._is_user = (role == "user")
        self._dark_mode = dark_mode
        self._raw_text = text
        self._window_width = window_width
        self._max_bubble_width = max(160, int(window_width * 0.75) - 35)
        self._cached_ideal_width = 0  # 缓存，文本不变时跳过 QTextDocument 重建

        # 外层布局
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 5, 14, 5)
        row_layout.setSpacing(0)

        self.bubble_card = QFrame()
        card_layout = QVBoxLayout(self.bubble_card)
        card_layout.setContentsMargins(12, 9, 12, 9)
        card_layout.setSpacing(3)

        # 核心文本标签
        self.text_label = QLabel()
        self.text_label.setWordWrap(True)
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        if self._is_user:
            self.text_label.setTextFormat(Qt.PlainText)
            self.text_label.setText(text)
        else:
            self.text_label.setTextFormat(Qt.MarkdownText)
            self.text_label.setText(text)

        ideal = self._compute_ideal_width()
        self.text_label.setFixedWidth(min(ideal, self._max_bubble_width))
        card_layout.addWidget(self.text_label)

        # 底部元信息行（AI 气泡始终创建，用户气泡只显示时间）
        self._meta_label = None
        self._time_label = None
        if not self._is_user:
            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(0, 2, 0, 0)
            self._meta_label = QLabel(meta_info)
            self._meta_label.setStyleSheet(
                "font-size:10px; color:#6e80ff; font-weight:500; "
                "border:none; background:transparent;"
            )
            meta_row.addWidget(self._meta_label)
            meta_row.addStretch(1)
            self._time_label = QLabel(timestamp)
            self._time_label.setStyleSheet(
                f"font-size:10px; color:{'#888' if self._dark_mode else '#576574'}; font-weight:500; "
                "border:none; background:transparent;"
            )
            meta_row.addWidget(self._time_label)
            card_layout.addLayout(meta_row)
        else:
            time_row = QHBoxLayout()
            time_row.setContentsMargins(0, 2, 0, 0)
            time_row.addStretch(1)
            self._time_label = QLabel(timestamp)
            self._time_label.setStyleSheet(
                "font-size:10px; color:#2c5a16; font-weight:500; "
                "border:none; background:transparent;"
            )
            time_row.addWidget(self._time_label)
            card_layout.addLayout(time_row)

        self._apply_style()

        if self._is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(self.bubble_card)
        else:
            row_layout.addWidget(self.bubble_card)
            row_layout.addStretch(1)

    # ── 宽度计算 ──
    def _compute_ideal_width(self) -> int:
        if self._cached_ideal_width > 0:
            return self._cached_ideal_width
        doc = QTextDocument()
        doc.setDefaultFont(self.text_label.font())
        if self._is_user:
            doc.setPlainText(self.text_label.text())
        else:
            doc.setMarkdown(self.text_label.text())
        self._cached_ideal_width = max(80, int(doc.idealWidth()) + 8)
        return self._cached_ideal_width

    # ── 样式 ──
    def _apply_style(self):
        if self._is_user:
            self.bubble_card.setStyleSheet(
                "QFrame { background-color:#95ec69; border:1px solid rgba(7,193,96,30); "
                "border-radius:12px; border-bottom-right-radius:2px; }"
            )
            self.text_label.setStyleSheet(
                "QLabel { color:#000000; font-size:13px; font-weight:500; "
                "border:none; background:transparent; }"
            )
        else:
            if self._dark_mode:
                self.bubble_card.setStyleSheet(
                    "QFrame { background-color:#2a2a3e; border:1px solid #3a3a50; "
                    "border-radius:12px; border-bottom-left-radius:2px; }"
                )
                self.text_label.setStyleSheet(
                    "QLabel { color:#e0e0e0; font-size:13px; "
                    "border:none; background:transparent; }"
                )
            else:
                self.bubble_card.setStyleSheet(
                    "QFrame { background-color:#ffffff; border:1px solid #e5e7eb; "
                    "border-radius:12px; border-bottom-left-radius:2px; }"
                )
                self.text_label.setStyleSheet(
                    "QLabel { color:#1e293b; font-size:13px; "
                    "border:none; background:transparent; }"
                )
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(8)
                shadow.setColor(QColor(0, 0, 0, 12))
                shadow.setOffset(0, 2)
                self.bubble_card.setGraphicsEffect(shadow)

    # ── 主题切换 ──
    def set_dark_mode(self, dark: bool):
        if self._is_user or self._dark_mode == dark:
            return
        self._dark_mode = dark
        self._cached_ideal_width = 0
        self.text_label.setTextFormat(Qt.MarkdownText)
        self.text_label.setText(self._raw_text)
        if self._time_label:
            self._time_label.setStyleSheet(
                f"font-size:10px; color:{'#888' if dark else '#576574'}; font-weight:500; "
                "border:none; background:transparent;"
            )
        self._apply_style()

    # ── 流式更新 ──
    def update_text(self, new_text, new_meta_info=None):
        self._raw_text = new_text
        self._cached_ideal_width = 0
        if self._is_user:
            self.text_label.setTextFormat(Qt.PlainText)
            self.text_label.setText(new_text)
        else:
            self.text_label.setTextFormat(Qt.MarkdownText)
            self.text_label.setText(new_text)
        self.text_label.setFixedWidth(min(self._compute_ideal_width(), self._max_bubble_width))
        if new_meta_info is not None and self._meta_label is not None:
            self._meta_label.setText(new_meta_info)

    # ── 窗口 resize ──
    def update_width(self, new_window_width):
        self._window_width = new_window_width
        self._max_bubble_width = max(160, int(new_window_width * 0.75) - 35)
        self.text_label.setFixedWidth(min(self._compute_ideal_width(), self._max_bubble_width))


class StatusBanner(QWidget):
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
        if self._dark_mode == dark: return
        self._dark_mode = dark
        self._apply_style()

    def _apply_style(self):
        self.lbl.setStyleSheet(
            f"font-size:11px; color:{'#888' if self._dark_mode else '#94a3b8'}; "
            "background:transparent; border:none;"
        )