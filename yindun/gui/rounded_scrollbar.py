# -*- coding: utf-8 -*-
"""
隐盾 — 自定义圆角滚动条
Qt QSS 对 QScrollBar::handle 的 border-radius 支持不完整，
本类重写 paintEvent，用 QPainterPath 绘制真正的胶囊形把手。
"""
from PySide6.QtWidgets import QScrollBar
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QPainter, QPainterPath


class RoundedScrollBar(QScrollBar):
    """自绘胶囊形圆角滚动条，支持浅/深色主题。"""

    def __init__(self, parent=None, dark: bool = False):
        super().__init__(Qt.Vertical, parent)
        self._dark = dark
        self._hover = False
        self.setMouseTracking(True)
        self.setPageStep(20)
        self.setSingleStep(15)

    def set_dark(self, dark: bool):
        self._dark = dark
        self.update()

    def _handle_color(self) -> QColor:
        if self._dark:
            base, hot = QColor(255, 255, 255, 36), QColor(255, 255, 255, 60)
        else:
            base, hot = QColor(0, 0, 0, 32), QColor(0, 0, 0, 64)
        return hot if self._hover else base

    def _handle_rect(self) -> QRectF:
        if self.maximum() <= self.minimum():
            return QRectF()
        margin = 10
        usable = self.height() - margin * 2
        ratio = (self.value() - self.minimum()) / (self.maximum() - self.minimum())
        page_ratio = self.pageStep() / (self.maximum() - self.minimum() + self.pageStep())
        h = max(36, min(int(usable * page_ratio), usable))
        y = margin + int((usable - h) * ratio)
        return QRectF(1, y, self.width() - 2, h)

    def enterEvent(self, e):
        self._hover = True; self.update(); super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False; self.update(); super().leaveEvent(e)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self._handle_rect()
        if rect.isValid():
            path = QPainterPath()
            path.addRoundedRect(rect, rect.width() / 2, rect.width() / 2)
            p.fillPath(path, self._handle_color())
        p.end()
