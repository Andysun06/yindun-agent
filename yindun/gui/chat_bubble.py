# -*- coding: utf-8 -*-
"""
隐盾 V2.1.0 — 💬 纯原生流式气泡组件
彻底抛弃 QTextBrowser 网页标签，改用原生布局与自适应物理卡片
升级：
1. 引入 QTextDocument 理想宽度计算矩阵，锁死 75% 阈值前的绝对不换行算法
2. 全面重构时间戳色彩矩阵，实现绿卡与白卡的高对比度现代化视觉
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextDocument

class ChatBubble(QWidget):
    """单条对话的高级原生弹性气泡组件 (全新升级自适应不提前换行与高对比度色彩)"""
    def __init__(self, role, text, timestamp, window_width=420, dark_mode=False):
        super().__init__()
        self._is_user = (role == "user")
        self._dark_mode = dark_mode

        # 1. 建立整行水平弹性卡片流布局
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 5, 14, 5)
        row_layout.setSpacing(0)

        # 2. 创建承载气泡的物理外壳卡片
        self.bubble_card = QFrame()

        # 3. 精准锁死 75% 最大宽度红线
        max_bubble_width = int(window_width * 0.75) - 35

        # 4. 创建气泡内部的纵向文本排版系统
        card_layout = QVBoxLayout(self.bubble_card)
        card_layout.setContentsMargins(12, 9, 12, 9)
        card_layout.setSpacing(5)

        # 注入标准文本标签
        self.text_label = QLabel()
        self.text_label.setTextFormat(Qt.MarkdownText)
        self.text_label.setText(text)
        self.text_label.setWordWrap(True)

        # 引入 QTextDocument 虚拟渲染舱
        doc = QTextDocument()
        doc.setDefaultFont(self.text_label.font())
        doc.setMarkdown(text)
        ideal_width = int(doc.idealWidth()) + 12

        if ideal_width < max_bubble_width:
            self.text_label.setFixedWidth(ideal_width)
        else:
            self.text_label.setFixedWidth(max_bubble_width)

        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_layout.addWidget(self.text_label)

        # 注入时间戳微型标签
        self.time_label = QLabel(timestamp)

        # 应用角色对应的皮肤与对齐策略
        self._apply_style()

        # 5. 装配外层水平流向
        if self._is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(self.bubble_card)
        else:
            row_layout.addWidget(self.bubble_card)
            row_layout.addStretch(1)

    def set_dark_mode(self, dark: bool):
        """切换深色/浅色主题。AI 气泡需要重绘, 用户气泡保持绿色不动。"""
        if self._dark_mode == dark:
            return
        self._dark_mode = dark
        if not self._is_user:
            self._apply_style()

    def _apply_style(self):
        # 先清除旧阴影, 避免重复叠加
        if self.bubble_card.graphicsEffect() is not None:
            self.bubble_card.setGraphicsEffect(None)

        if self._is_user:
            # 用户气泡：绿色背景 + 黑色文字（深色模式下也保持一致）
            self.text_label.setStyleSheet("""
                QLabel { color: #000000; font-size: 13px; font-weight: 500; border: none; background: transparent; }
            """)
            self.time_label.setStyleSheet("""
                QLabel { font-size: 10px; color: #2c5a16; font-weight: 600; border: none; background: transparent; }
            """)
            self.time_label.setAlignment(Qt.AlignRight)
            self.bubble_card.setStyleSheet("""
                QFrame {
                    background-color: #95ec69;
                    border: 1px solid rgba(7, 193, 96, 30);
                    border-radius: 12px;
                    border-bottom-right-radius: 2px;
                }
            """)
        else:
            # AI 气泡：深色/浅色自适应
            if self._dark_mode:
                self.text_label.setStyleSheet("""
                    QLabel { color: #e0e0e0; font-size: 13px; border: none; background: transparent; }
                """)
                self.time_label.setStyleSheet("""
                    QLabel { font-size: 10px; color: #888; font-weight: 500; border: none; background: transparent; }
                """)
                self.bubble_card.setStyleSheet("""
                    QFrame {
                        background-color: #2a2a3e;
                        border: 1px solid #3a3a50;
                        border-radius: 12px;
                        border-bottom-left-radius: 2px;
                    }
                """)
            else:
                self.text_label.setStyleSheet("""
                    QLabel { color: #1e293b; font-size: 13px; border: none; background: transparent; }
                """)
                self.time_label.setStyleSheet("""
                    QLabel { font-size: 10px; color: #576574; font-weight: 500; border: none; background: transparent; }
                """)
                self.bubble_card.setStyleSheet("""
                    QFrame {
                        background-color: #ffffff;
                        border: 1px solid #e5e7eb;
                        border-radius: 12px;
                        border-bottom-left-radius: 2px;
                    }
                """)
                # 视觉阴影升级 (仅浅色模式)
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(8)
                shadow.setColor(QColor(0, 0, 0, 12))
                shadow.setOffset(0, 2)
                self.bubble_card.setGraphicsEffect(shadow)
            self.time_label.setAlignment(Qt.AlignLeft)


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