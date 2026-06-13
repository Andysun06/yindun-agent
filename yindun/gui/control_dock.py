# -*- coding: utf-8 -*-
# Yindun Security Agent V3.1.4 - Independent Control Dock Component (3D Rounded Edition)
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel, QGraphicsDropShadowEffect, QWidget
from PySide6.QtCore import Signal, Qt, QPropertyAnimation, QEasingCurve, Property
from PySide6.QtGui import QCursor, QColor, QPainter, QFont

class SegmentedModeSwitch(QWidget):
    """分段滑动式模式开关（紧凑低调风格，与整体 UI 统一）"""
    mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(110, 28)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        self._dark_mode = False
        self._is_think = False  # False=快速（左）, True=思考（右）

        # 几何参数
        self._radius = 14          # 背景圆角
        self._thumb_pad = 3        # 滑块与背景间距
        self._thumb_w = (self.width() - self._thumb_pad * 2) / 2 - 1
        self._thumb_h = self.height() - self._thumb_pad * 2

        # 动画
        self._thumb_x = float(self._thumb_pad + 1)
        self._anim = QPropertyAnimation(self, b"thumbX", self)
        self._anim.setDuration(200)
        self._anim.setEasingCurve(QEasingCurve.InOutCubic)

        # 轻微下沉阴影（低调）
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setBlurRadius(6)
        self._shadow.setColor(QColor(0, 0, 0, 14))
        self._shadow.setOffset(0, 1)
        self.setGraphicsEffect(self._shadow)

    # ── 动画属性 ───────────────────────────────────────────────
    def _get_thumb_x(self):
        return self._thumb_x

    def _set_thumb_x(self, v):
        self._thumb_x = v
        self.update()

    thumbX = Property(float, _get_thumb_x, _set_thumb_x)

    # ── 对外 API ───────────────────────────────────────────────
    def get_mode(self) -> str:
        return "👁️ 深度自检" if self._is_think else "⚡ 快速响应"

    def set_dark_theme(self, dark: bool):
        if self._dark_mode != dark:
            self._dark_mode = dark
            self._shadow.setColor(QColor(0, 0, 0, 28) if dark else QColor(0, 0, 0, 14))
            self.update()

    # ── 交互事件 ───────────────────────────────────────────────
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._set_mode(event.position().x() > self.width() / 2)
            event.accept()

    def enterEvent(self, event):
        super().enterEvent(event)

    def leaveEvent(self, event):
        super().leaveEvent(event)

    def _set_mode(self, is_think):
        if self._is_think == is_think:
            return
        self._is_think = is_think
        target_x = self.width() - self._thumb_w - self._thumb_pad - 1 if is_think else float(self._thumb_pad + 1)
        self._anim.stop()
        self._anim.setStartValue(self._thumb_x)
        self._anim.setEndValue(target_x)
        self._anim.start()
        self.mode_changed.emit(self.get_mode())

    # ── 自绘核心 ───────────────────────────────────────────────
    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)

        w, h = self.width(), self.height()

        # 配色：严格跟随全局 QSS 风格
        if self._dark_mode:
            base_bg = QColor(42, 42, 62)
            base_border = QColor(62, 62, 88)
            inactive_text = QColor(136, 136, 153)
            # 选中滑块：深色模式用系统强调色蓝
            thumb_color = QColor(59, 130, 246)
            thumb_border = QColor(37, 99, 235)
            active_text = QColor(255, 255, 255)
        else:
            base_bg = QColor(226, 232, 240)       # 与原 #e2e8f0 一致
            base_border = QColor(203, 213, 225)   # 与原 #cbd5e1 一致
            inactive_text = QColor(100, 116, 139) # 与原 #64748b 一致
            # 选中滑块：浅色模式用白底 + 强调色文字，与原先 QPushButton:checked 风格一致
            thumb_color = QColor(255, 255, 255)
            thumb_border = QColor(203, 213, 225)
            active_text = QColor(7, 193, 96)      # 与原 #07c160 绿一致

        # 1. 胶囊背景
        p.setPen(base_border)
        p.setBrush(base_bg)
        p.drawRoundedRect(0, 0, w, h, self._radius, self._radius)

        # 2. 背景文字（未选中的那一边）
        font = QFont()
        font.setPointSize(9)
        font.setWeight(QFont.DemiBold)
        font.setFamily("Microsoft YaHei")
        p.setFont(font)

        half_w = w / 2
        p.setPen(inactive_text)
        p.drawText(0, 0, half_w, h, Qt.AlignCenter, "快速")
        p.drawText(half_w, 0, half_w, h, Qt.AlignCenter, "思考")

        # 3. 滑块（圆角矩形，低调无多余装饰）
        tx = int(self._thumb_x)
        ty = self._thumb_pad
        tw = int(self._thumb_w)
        th = int(self._thumb_h)
        p.setPen(thumb_border)
        p.setBrush(thumb_color)
        p.drawRoundedRect(tx, ty, tw, th, 12, 12)

        # 4. 在滑块区域重绘选中文字
        p.setPen(active_text)
        label = "思考" if self._is_think else "快速"
        p.drawText(tx, ty, tw, th, Qt.AlignCenter, label)

        p.end()


class ControlDock(QFrame):
    """下置复合多模态控制台底座"""
    send_triggered = Signal(str)
    file_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlDock")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 8, 14, 12)
        layout.setSpacing(8)
        
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        
        self.mode_switch = SegmentedModeSwitch()
        toolbar.addWidget(self.mode_switch)
        
        self.file_btn = QPushButton("📎 挂载文件")
        self.file_btn.setObjectName("fileBtn")
        self.file_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.file_btn.clicked.connect(self.file_requested.emit)
        toolbar.addWidget(self.file_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        inp_row = QHBoxLayout()
        inp_row.setSpacing(8)
        
        self.input_line = QLineEdit()
        self.input_line.setObjectName("inputLine")
        self.input_line.setPlaceholderText("请输入涉密指令...")
        self.input_line.returnPressed.connect(self._handle_send)
        inp_row.addWidget(self.input_line, 1)
        
        self.send_btn = QPushButton("➤")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.send_btn.clicked.connect(self._handle_send)
        inp_row.addWidget(self.send_btn)
        layout.addLayout(inp_row)
        
        self.set_dark_mode(False)

    def _handle_send(self):
        text = self.input_line.text().strip()
        if text:
            self.send_triggered.emit(text)

    def get_current_mode(self):
        return self.mode_switch.get_mode()

    def clear_input_field(self):
        self.input_line.clear()

    def update_file_button_text(self, text):
        self.file_btn.setText(text)

    def force_input_focus(self):
        self.input_line.setFocus()

    def update_placeholder_text(self, text):
        self.input_line.setPlaceholderText(text)

    def toggle_busy_lock(self, is_busy, customized_placeholder=""):
        self.send_btn.setEnabled(not is_busy)
        self.mode_switch.setEnabled(not is_busy)
        if is_busy:
            self.input_line.setPlaceholderText(customized_placeholder if customized_placeholder else "正在处理机密网关数据...")
        else:
            self.input_line.setPlaceholderText("请输入涉密指令...")

    def set_dark_mode(self, dark: bool):
        self.mode_switch.set_dark_theme(dark)
        if dark:
            self.send_btn.setStyleSheet("""
                QPushButton#sendBtn {
                    background: #3b82f6; color: white; border: none; border-radius: 16px;
                    font-size: 15px; font-weight: bold;
                    min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
                }
                QPushButton#sendBtn:hover { background: #2563eb; }
                QPushButton#sendBtn:pressed { background: #1d4ed8; }
                QPushButton#sendBtn:disabled { background: #3a3a50; }
            """)
        else:
            self.send_btn.setStyleSheet("""
                QPushButton#sendBtn {
                    background: #07c160; color: white; border: none; border-radius: 16px;
                    font-size: 15px; font-weight: bold;
                    min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
                }
                QPushButton#sendBtn:hover { background: #06ad56; }
                QPushButton#sendBtn:pressed { background: #059a4c; }
                QPushButton#sendBtn:disabled { background: #c5cde0; }
            """)