# -*- coding: utf-8 -*-
# 隐盾 V3.2.0 - 数据看板（可从底部向上滑出的抽屉）
import time
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGridLayout, QProgressBar, QSizePolicy
)
from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import QCursor


class DataDashboard(QFrame):
    """底部可向上滑出的数据统计看板

    折叠态：仅显示 "数据看板 ^"
    展开态：上下文独占一行（进度条），下方 4 项 2 列网格
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dataDashboard")
        self.setMinimumHeight(0)

        # 统计计数器
        self._context_tokens = 0
        self._max_tokens = 8192
        self._model_calls = 0
        self._tool_calls = 0
        self._current_model = "未连接"

        # 展开/折叠状态
        self._expanded = False
        self._collapsed_h = 26
        self._expanded_h = 220

        self.setFixedHeight(self._collapsed_h)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── 折叠态栏：标题 + 展开按钮 ──
        self._bar = QFrame()
        self._bar.setObjectName("dashboardBar")
        bar_layout = QHBoxLayout(self._bar)
        bar_layout.setContentsMargins(12, 0, 8, 0)
        bar_layout.setSpacing(8)

        self._bar_label = QLabel("数据看板")
        self._bar_label.setObjectName("dashboardBarLabel")
        bar_layout.addWidget(self._bar_label)
        bar_layout.addStretch()

        self._toggle_btn = QPushButton("^")
        self._toggle_btn.setObjectName("dashboardToggle")
        self._toggle_btn.setFixedSize(22, 22)
        self._toggle_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._toggle_btn.clicked.connect(self.toggle)
        bar_layout.addWidget(self._toggle_btn)

        main_layout.addWidget(self._bar)

        # ── 展开态面板 ──
        self._panel = QFrame()
        self._panel.setObjectName("dashboardPanel")
        self._panel.setVisible(False)
        self._panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(10, 8, 10, 10)
        panel_layout.setSpacing(10)

        # 第一行：上下文进度条（独占一行）
        ctx_row = QFrame()
        ctx_row.setObjectName("ctxRow")
        ctx_layout = QHBoxLayout(ctx_row)
        ctx_layout.setContentsMargins(0, 0, 0, 0)
        ctx_layout.setSpacing(8)

        ctx_icon_label = QLabel("🧠")
        ctx_icon_label.setObjectName("ctxIcon")
        ctx_layout.addWidget(ctx_icon_label)

        ctx_text_label = QLabel("上下文")
        ctx_text_label.setObjectName("ctxText")
        ctx_layout.addWidget(ctx_text_label)

        self._ctx_progress = QProgressBar()
        self._ctx_progress.setObjectName("ctxProgress")
        self._ctx_progress.setMinimum(0)
        self._ctx_progress.setMaximum(100)
        self._ctx_progress.setValue(0)
        self._ctx_progress.setTextVisible(False)
        self._ctx_progress.setFixedHeight(10)
        self._ctx_progress.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        ctx_layout.addWidget(self._ctx_progress, 1)

        self._ctx_pct_label = QLabel("0%")
        self._ctx_pct_label.setObjectName("ctxPct")
        self._ctx_pct_label.setMinimumWidth(36)
        ctx_layout.addWidget(self._ctx_pct_label)

        panel_layout.addWidget(ctx_row)

        # 第二部分：4 项 2×2 网格
        grid = QGridLayout()
        grid.setSpacing(6)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)

        self._lbl_model = self._make_stat("🤖", "模型调用", "0")
        self._lbl_tool = self._make_stat("🛠️", "对话次数", "0")
        self._lbl_model_name = self._make_stat("📦", "当前模型", "-")
        self._lbl_avg_latency = self._make_stat("⚡", "平均响应", "-")

        grid.addWidget(self._lbl_model["card"], 0, 0)
        grid.addWidget(self._lbl_tool["card"], 0, 1)
        grid.addWidget(self._lbl_model_name["card"], 1, 0)
        grid.addWidget(self._lbl_avg_latency["card"], 1, 1)
        panel_layout.addLayout(grid)

        main_layout.addWidget(self._panel)

        # 动画
        self._anim = QPropertyAnimation(self, b"maximumHeight", self)
        self._anim.setDuration(280)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

        self._bar.mousePressEvent = self._on_bar_clicked

        self.set_dark_mode(False)

    def _make_stat(self, icon, label, value):
        """创建统计卡片"""
        card = QFrame()
        card.setObjectName("statCard")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(1)
        lbl_top = QLabel(f"{icon} {label}")
        lbl_top.setObjectName("statLabel")
        lbl_val = QLabel(value)
        lbl_val.setObjectName("statValue")
        cl.addWidget(lbl_top)
        cl.addWidget(lbl_val)
        return {"card": card, "value": lbl_val}

    def _on_bar_clicked(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle()

    def toggle(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self._panel.setVisible(True)
        self.setMaximumHeight(16777215)
        self._anim.stop()
        self._anim.setStartValue(self.height())
        self._anim.setEndValue(self._expanded_h)
        self._anim.start()
        self._toggle_btn.setText("v")
        self.updateGeometry()

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self._anim.stop()
        self._anim.setStartValue(self.height())
        self._anim.setEndValue(self._collapsed_h)
        self._anim.finished.connect(self._on_collapse_finished)
        self._anim.start()
        self._toggle_btn.setText("^")
        self.updateGeometry()

    def _on_collapse_finished(self):
        if not self._expanded:
            self._panel.setVisible(False)
            self.setMaximumHeight(self._collapsed_h)
        try:
            self._anim.finished.disconnect(self._on_collapse_finished)
        except Exception:
            pass

    # ── 公共 API ──
    def update_context_tokens(self, n: int):
        self._context_tokens = n
        pct = min(100, int(n / self._max_tokens * 100))
        self._ctx_progress.setValue(pct)
        self._ctx_pct_label.setText(f"{pct}%")

    def set_max_tokens(self, n: int):
        self._max_tokens = n
        self.update_context_tokens(self._context_tokens)

    def update_model_calls(self, n: int):
        self._model_calls = n
        self._lbl_model["value"].setText(str(n))

    def update_tool_calls(self, n: int):
        self._tool_calls = n
        self._lbl_tool["value"].setText(str(n))

    def update_current_model(self, name: str):
        self._current_model = name
        self._lbl_model_name["value"].setText(name)

    def update_avg_latency(self, ms: float):
        self._lbl_avg_latency["value"].setText(f"{ms:.0f} ms")

    def inc_model_call(self):
        self.update_model_calls(self._model_calls + 1)

    def inc_tool_call(self):
        self.update_tool_calls(self._tool_calls + 1)

    def set_dark_mode(self, dark: bool):
        c = {
            "bg": "#252538" if dark else "#f8fafc",
            "border": "rgba(255,255,255,8)" if dark else "#e2e8f0",
            "text": "#d4d4e0" if dark else "#334155",
            "toggle_border": "rgba(255,255,255,20)" if dark else "#cbd5e1",
            "pct": "#4ade80" if dark else "#16a34a",
            "progress_bg": "rgba(255,255,255,10)" if dark else "#e2e8f0",
            "card_border": "rgba(255,255,255,12)" if dark else "#e2e8f0",
            "stat_label": "#9aa0b0" if dark else "#64748b",
            "stat_value": "#d4d4e0" if dark else "#1e293b",
        }
        self.setStyleSheet(f"""
            QFrame#dataDashboard {{ background: {c['bg']}; border-top: 1px solid {c['border']}; }}
            QFrame#dashboardBar {{ background: transparent; }}
            QLabel#dashboardBarLabel {{ color: {c['text']}; font-size: 11px; font-weight: 600; }}
            QPushButton#dashboardToggle {{
                background: transparent; color: {c['text']};
                border: 1px solid {c['toggle_border']};
                border-radius: 11px; font-size: 11px; font-weight: bold;
            }}
            QPushButton#dashboardToggle:hover {{ color: #07c160; border-color: #07c160; }}
            QFrame#dashboardPanel {{ background: transparent; }}
            QFrame#ctxRow {{ background: transparent; }}
            QLabel#ctxIcon {{ color: {c['text']}; font-size: 12px; }}
            QLabel#ctxText {{ color: {c['text']}; font-size: 11px; font-weight: 500; min-width: 42px; }}
            QLabel#ctxPct {{ color: {c['pct']}; font-size: 11px; font-weight: bold; }}
            QProgressBar#ctxProgress {{
                background: {c['progress_bg']};
                border: none; border-radius: 5px;
            }}
            QProgressBar#ctxProgress::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #4ade80,stop:1 #22c55e);
                border-radius: 5px;
            }}
            QFrame#statCard {{ background: transparent; border: 1px solid {c['card_border']}; border-radius: 6px; }}
            QLabel#statLabel {{ color: {c['stat_label']}; font-size: 9px; }}
            QLabel#statValue {{ color: {c['stat_value']}; font-size: 12px; font-weight: bold; }}
        """)
