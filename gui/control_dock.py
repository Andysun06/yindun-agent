# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - Independent Control Dock Component
from PySide6.QtWidgets import QFrame, QVBoxLayout, QHBoxLayout, QComboBox, QPushButton, QLineEdit
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QCursor

class ControlDock(QFrame):
    """Independent bottom dashboard managing user modes, inputs, and attachment file signals"""
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
        
        # Row 1: Multimodal config tool bar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)
        
        self.mode_combo = QComboBox()
        self.mode_combo.setObjectName("modeSelector")
        self.mode_combo.addItems(["⚡ 快速响应", "👁️ 深度自检"])
        self.mode_combo.setCursor(QCursor(Qt.PointingHandCursor))
        toolbar.addWidget(self.mode_combo)
        
        self.file_btn = QPushButton("📎 挂载文件")
        self.file_btn.setObjectName("fileBtn")
        self.file_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.file_btn.clicked.connect(self.file_requested.emit)
        toolbar.addWidget(self.file_btn)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        
        # Row 2: Prompt line input and launch grid
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

    def _handle_send(self):
        """Extract input text stream and relay safely to upper controllers"""
        text = self.input_line.text().strip()
        if text:
            self.send_triggered.emit(text)

    def get_current_mode(self):
        return self.mode_combo.currentText()

    def clear_input_field(self):
        self.input_line.clear()

    def update_file_button_text(self, text):
        self.file_btn.setText(text)

    def force_input_focus(self):
        self.input_line.setFocus()

    def update_placeholder_text(self, text):
        self.input_line.setPlaceholderText(text)

    def toggle_busy_lock(self, is_busy, customized_placeholder=""):
        """Lock controls during active LLM inference cycles to guarantee single-track runs"""
        self.send_btn.setEnabled(not is_busy)
        if is_busy:
            self.input_line.setPlaceholderText(customized_placeholder if customized_placeholder else "正在处理机密网关数据...")
        else:
            self.input_line.setPlaceholderText("请输入涉密指令...")