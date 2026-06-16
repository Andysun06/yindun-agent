# -*- coding: utf-8 -*-
"""
隐盾 — 新建对话弹窗
现代化、圆角、主题自适应的自定义对话框。
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QLineEdit, QPushButton, QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QMouseEvent


class NewSessionDialog(QWidget):
    """新建对话弹窗 - 圆角胶囊风, 浅/深色主题自适应"""

    created = Signal(str)  # 空字符串表示用默认命名

    # 浅 / 深色双主题样式 (与主界面 22px 外壳 / 18px 输入框同体系)
    _QSS = {
        "light": """
            QFrame { background: #ffffff; border: 1px solid rgba(0,0,0,10); border-radius: 16px; }
            QLabel { background: transparent; border: none; }
            QLabel#nsdTitle { font-size: 14px; font-weight: 700; color: #1a1a2e; }
            QLabel#nsdHint  { font-size: 11px; color: #6b7280; }
            QLineEdit#nsdInput {
                background: #f8f9fc; border: 1.5px solid #e0e3ea; border-radius: 12px;
                padding: 8px 12px; font-size: 13px; color: #1e293b;
            }
            QLineEdit#nsdInput:focus { border-color: #07c160; background: #ffffff; }
            QPushButton#nsdCancel {
                background: transparent; border: 1px solid #e0e3ea; border-radius: 12px;
                padding: 6px 18px; font-size: 12px; color: #64748b; font-weight: 600;
            }
            QPushButton#nsdCancel:hover { background: #f1f5f9; color: #334155; }
            QPushButton#nsdCreate {
                background: #07c160; color: white; border: none; border-radius: 12px;
                padding: 6px 22px; font-size: 12px; font-weight: 700;
            }
            QPushButton#nsdCreate:hover { background: #06ad56; }
            QPushButton#nsdCreate:pressed { background: #059a4c; }
        """,
        "dark": """
            QFrame { background: #2a2a3e; border: 1px solid rgba(255,255,255,10); border-radius: 16px; }
            QLabel { background: transparent; border: none; }
            QLabel#nsdTitle { font-size: 14px; font-weight: 700; color: #e0e0e0; }
            QLabel#nsdHint  { font-size: 11px; color: #888; }
            QLineEdit#nsdInput {
                background: #1a1a2e; border: 1.5px solid #3a3a50; border-radius: 12px;
                padding: 8px 12px; font-size: 13px; color: #e0e0e0;
            }
            QLineEdit#nsdInput:focus { border-color: #3b82f6; background: #20203a; }
            QPushButton#nsdCancel {
                background: transparent; border: 1px solid #3a3a50; border-radius: 12px;
                padding: 6px 18px; font-size: 12px; color: #aaa; font-weight: 600;
            }
            QPushButton#nsdCancel:hover { background: #303050; color: #fff; }
            QPushButton#nsdCreate {
                background: #3b82f6; color: white; border: none; border-radius: 12px;
                padding: 6px 22px; font-size: 12px; font-weight: 700;
            }
            QPushButton#nsdCreate:hover { background: #2563eb; }
            QPushButton#nsdCreate:pressed { background: #1d4ed8; }
        """,
    }

    def __init__(self, parent=None, dark: bool = False):
        super().__init__(parent)
        self.setFixedSize(360, 220)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowModality(Qt.WindowModal)
        self._dark = dark
        self._drag_pos = None

        # 圆角卡片
        self.card = QFrame(self)
        self.card.setGeometry(10, 10, 340, 200)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        self.card.setGraphicsEffect(shadow)

        # 内容
        main = QVBoxLayout(self.card)
        main.setContentsMargins(22, 18, 22, 18)
        main.setSpacing(10)

        title = QLabel("✨ 新建对话")
        title.setObjectName("nsdTitle")
        main.addWidget(title)

        hint = QLabel("给这次对话起个名字 · 留空将使用自动命名")
        hint.setObjectName("nsdHint")
        main.addWidget(hint)

        self.name_in = QLineEdit()
        self.name_in.setObjectName("nsdInput")
        self.name_in.setPlaceholderText("例如: 项目复盘 / 周报草稿 / 论文速记…")
        self.name_in.setClearButtonEnabled(True)
        self.name_in.returnPressed.connect(self._on_create)
        main.addWidget(self.name_in)
        main.addStretch(1)

        self._build_buttons(main)
        self._update_shadow_color()
        self._apply_theme()

    def _build_buttons(self, parent_layout):
        """构造底部 [取消] [创建] 按钮行"""
        row = QHBoxLayout()
        row.setSpacing(10)
        cancel = QPushButton("取消")
        cancel.setObjectName("nsdCancel")
        cancel.setCursor(QCursor(Qt.PointingHandCursor))
        cancel.clicked.connect(self.close)
        row.addWidget(cancel)
        row.addStretch(1)
        create = QPushButton("✓ 创建")
        create.setObjectName("nsdCreate")
        create.setCursor(QCursor(Qt.PointingHandCursor))
        create.setDefault(True)
        create.clicked.connect(self._on_create)
        row.addWidget(create)
        parent_layout.addLayout(row)

    # -------- 主题 --------
    def set_dark(self, dark: bool):
        if self._dark == dark:
            return
        self._dark = dark
        self._update_shadow_color()
        self._apply_theme()

    def _update_shadow_color(self):
        eff = self.card.graphicsEffect()
        if isinstance(eff, QGraphicsDropShadowEffect):
            eff.setColor(QColor(59, 130, 246, 32) if self._dark else QColor(7, 193, 96, 28))

    def _apply_theme(self):
        self.setStyleSheet(self._QSS["dark" if self._dark else "light"])

    # -------- 业务 --------
    def _on_create(self):
        self.created.emit(self.name_in.text().strip())
        self.close()

    # -------- 拖拽 / 焦点 --------
    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e: QMouseEvent):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e: QMouseEvent):
        self._drag_pos = None

    def showEvent(self, e):
        super().showEvent(e)
        self.name_in.setFocus()
