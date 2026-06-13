# -*- coding: utf-8 -*-
# Yindun Security Agent V3.1.4 - Independent Control Dock Component (3D Rounded Edition)
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLineEdit, QLabel, QGraphicsDropShadowEffect
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor, QColor

class SegmentedModeSwitch(QFrame):
    """高级原子级分段滑动开关 (已升级完美全圆角胶囊拨杆与立体物理阴影)"""
    mode_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(110, 28)
        self._dark_mode = False
        self._is_think = False # False 代表左侧快速，True 代表右侧思考
        
        self.setObjectName("modeSwitchBase")
        
        # 1. 🌟 视觉升级：为整个模式切换胶囊注入细腻、轻量的高斯模糊物理下沉微阴影
        self.shadow_effect = QGraphicsDropShadowEffect(self)
        self.shadow_effect.setBlurRadius(8)
        self.shadow_effect.setColor(QColor(0, 0, 0, 18))
        self.shadow_effect.setOffset(0, 2)
        self.setGraphicsEffect(self.shadow_effect)
        
        # 内部双轨水平排版布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        
        self.fast_btn = QPushButton("快速")
        self.fast_btn.setCheckable(True)
        self.fast_btn.setChecked(True)
        self.fast_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.fast_btn.clicked.connect(self._set_fast_mode)
        
        self.think_btn = QPushButton("思考")
        self.think_btn.setCheckable(True)
        self.think_btn.setChecked(False)
        self.think_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.think_btn.clicked.connect(self._set_think_mode)
        
        layout.addWidget(self.fast_btn)
        layout.addWidget(self.think_btn)
        
        self.update_style()

    def get_mode(self) -> str:
        return "👁️ 深度自检" if self._is_think else "⚡ 快速响应"

    def set_dark_theme(self, dark: bool):
        self._dark_mode = dark
        self.update_style()

    def _set_fast_mode(self):
        if self._is_think:
            self._is_think = False
            self.fast_btn.setChecked(True)
            self.think_btn.setChecked(False)
            self.update_style()
            self.mode_changed.emit(self.get_mode())

    def _set_think_mode(self):
        if not self._is_think:
            self._is_think = True
            self.fast_btn.setChecked(False)
            self.think_btn.setChecked(True)
            self.update_style()
            self.mode_changed.emit(self.get_mode())

    def update_style(self):
        """流式动态渲染多态色板调性 QSS"""
        # 🌟 优化核心：将 checked（激活）状态的圆角强推至 12px，并利用微带深色的 border-bottom 营造拟物悬浮卡片质感
        if self._dark_mode:
            base_qss = "QFrame#modeSwitchBase { background: #2a2a3e; border: 1.5px solid #3a3a50; border-radius: 14px; }"
            btn_qss = """
                QPushButton { background: transparent; border: none; border-radius: 12px; color: #888899; font-size: 11px; font-weight: 600; }
                QPushButton:hover { color: #ffffff; }
                QPushButton:checked { background: #3b82f6; color: #ffffff; border: 1px solid #2563eb; border-bottom: 2px solid #1d4ed8; border-radius: 12px; font-weight: 700; }
            """
            self.shadow_effect.setColor(QColor(0, 0, 0, 45)) # 深色模式加深阴影突显
        else:
            base_qss = "QFrame#modeSwitchBase { background: #e2e8f0; border: 1px solid rgba(0,0,0,8); border-radius: 14px; }"
            btn_qss = """
                QPushButton { background: transparent; border: none; border-radius: 12px; color: #64748b; font-size: 11px; font-weight: 600; }
                QPushButton:hover { color: #07c160; }
                QPushButton:checked { background: #ffffff; color: #07c160; border: 1px solid #cbd5e1; border-bottom: 2px solid #b2c2d3; border-radius: 12px; font-weight: 700; }
            """
            self.shadow_effect.setColor(QColor(0, 0, 0, 18))
        self.setStyleSheet(base_qss + btn_qss)


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