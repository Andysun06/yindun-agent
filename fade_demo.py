# -*- coding: utf-8 -*-
"""
Qt for Python (PySide6) 淡出淡入切换演示
==========================================
纯教学参考，不影响原项目。

核心原理：
  - 两个 QWidget 平级父子关系，通过 setVisible 切换
  - QStackedLayout 托管子页面几何（自动填满容器）
  - 动画期间：两个页面同时 setVisible(True)，各自独立做 opacity 动画
  - 动画结束：隐藏旧页，QStackedLayout.setCurrentWidget 切到新页

关键点（避免 QPainter 冲突）：
  - QGraphicsOpacityEffect 不施加于 QStackedLayout 当前活跃的子控件上
  - 动画期间两个页面都手动 setVisible(True)，脱离 QStackedLayout 控制
  - 动画结束后 setCurrentWidget 恢复 QStackedLayout 托管

运行：python fade_demo.py
"""

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QStackedLayout,
    QLabel, QPushButton, QFrame, QGraphicsOpacityEffect
)
from PySide6.QtCore import (
    Qt, QPropertyAnimation, QEasingCurve
)


class CrossFadePage(QFrame):
    """带文字标签的独立页面"""

    def __init__(self, name: str, color: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"QFrame {{ background: {color}; border-radius: 12px; }}")
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        label = QLabel(f"{name}", self)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet(
            "color: white; font-size: 22px; font-weight: bold; background: transparent;"
        )
        layout.addWidget(label)


class DemoWindow(QWidget):
    """
    主窗口布局：
      ┌──────────────────────────┐
      │     pages_container      │
      │  [QStackedLayout]        │
      │   ├─ page_a (红)         │
      │   └─ page_b (蓝)         │
      ├──────────────────────────┤
      │   [切A]  [切B]           │
      └──────────────────────────┘

    切换流程：
      1. 取消 QStackedLayout 托管：page_a.show(), page_b.show()（两个同时可见）
      2. page_a 做 opacity 1→0 淡出、page_b 做 opacity 0→1 淡入（并行）
      3. 动画结束：page_a.hide()，QStackedLayout.setCurrentWidget(page_b)
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("淡出淡入动画演示")
        self.resize(500, 360)
        self.setStyleSheet("QWidget { background: #1a1a2e; }")

        # ── 主体布局 ──
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)

        # ── 页面容器 ──
        self.pages_container = QWidget()
        self.pages_container.setMinimumHeight(240)
        self._stack = QStackedLayout(self.pages_container)
        outer.addWidget(self.pages_container, 1)

        self.page_a = CrossFadePage("🅰 工作区", "#e74c3c")
        self.page_b = CrossFadePage("🅱 设置面板", "#3498db")
        self._stack.addWidget(self.page_a)
        self._stack.addWidget(self.page_b)
        self._stack.setCurrentWidget(self.page_a)

        # 动画锁
        self._busy = False

        # ── 底部按钮栏 ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(12)

        for page, text, color, hover in [
            (self.page_a, "切换到页面 A", "#e74c3c", "#c0392b"),
            (self.page_b, "切换到页面 B", "#3498db", "#2980b9"),
        ]:
            btn = QPushButton(text)
            btn.setStyleSheet(f"""
                QPushButton {{ background: {color}; color: white; border: none;
                              border-radius: 8px; padding: 10px 20px; font-weight: bold; }}
                QPushButton:hover {{ background: {hover}; }}
            """)
            btn.clicked.connect(lambda checked, p=page: self.crossfade_to(p))
            btn_row.addStretch()
            btn_row.addWidget(btn)

        btn_row.addStretch()
        outer.addLayout(btn_row)

    def crossfade_to(self, target: CrossFadePage):
        """
        并行淡出旧页 + 淡入新页（同时动画，视觉上有交错过渡感）
        """
        old = self._stack.currentWidget()
        if target is old or self._busy:
            return
        self._busy = True

        # ── 第一步：两个页面都脱离 QStackedLayout，手动 setVisible ──
        old.show()
        target.show()
        target.raise_()  # 新页在上层

        # ── 第二步：并行启动两个动画 ──
        done = [False, False]  # [old_done, target_done]

        def try_finalize():
            if done[0] and done[1]:
                old.hide()
                old.setGraphicsEffect(None)
                target.setGraphicsEffect(None)
                self._stack.setCurrentWidget(target)  # 恢复 QStackedLayout 托管
                self._busy = False

        # 旧页淡出
        out_ef = QGraphicsOpacityEffect(old)
        old.setGraphicsEffect(out_ef)
        out_anim = QPropertyAnimation(out_ef, b"opacity")
        out_anim.setDuration(250)
        out_anim.setStartValue(1.0)
        out_anim.setEndValue(0.0)
        out_anim.setEasingCurve(QEasingCurve.OutCubic)
        out_anim.finished.connect(lambda: [setitem(done, 0, True), try_finalize()])
        out_anim.start()

        # 新页淡入
        in_ef = QGraphicsOpacityEffect(target)
        target.setGraphicsEffect(in_ef)
        in_anim = QPropertyAnimation(in_ef, b"opacity")
        in_anim.setDuration(250)
        in_anim.setStartValue(0.0)
        in_anim.setEndValue(1.0)
        in_anim.setEasingCurve(QEasingCurve.OutCubic)
        in_anim.finished.connect(lambda: [setitem(done, 1, True), try_finalize()])
        in_anim.start()


def setitem(lst, idx, val):
    lst[idx] = val


if __name__ == "__main__":
    app = QApplication([])
    demo = DemoWindow()
    demo.show()
    app.exec()
