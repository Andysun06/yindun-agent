# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - Independent Settings Panel Component
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QScrollArea, QGroupBox, QFormLayout, QComboBox, QCheckBox, QSlider
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

class SettingsPanel(QWidget):
    # Define two data pipelines to communicate with MainWindow safely
    settings_saved = Signal(dict)
    cancel_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 14)
        layout.setSpacing(8)
        
        # Header bar with back button
        hdr = QHBoxLayout()
        st = QLabel("⚙️ 隐盾设置")
        st.setObjectName("settingsTitle")
        hdr.addWidget(st)
        hdr.addStretch()
        
        back = QPushButton("← 返回对话")
        back.setObjectName("settingsCancel")
        back.setCursor(QCursor(Qt.PointingHandCursor))
        back.clicked.connect(self.cancel_clicked.emit)
        hdr.addWidget(back)
        layout.addLayout(hdr)
        
        # Scroll area for form elements
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        sc = QWidget()
        sc.setStyleSheet("background:transparent;")
        form_layout = QVBoxLayout(sc)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(6)
        
        # Group 1: LLM Engine Config
        g1 = QGroupBox("算力底座")
        f1 = QFormLayout(g1)
        f1.setSpacing(8)
        self.setting_model = QComboBox()
        self.setting_model.setObjectName("settingCombo")
        self.setting_model.addItems(["qwen2.5:1.5b", "qwen2.5:7b"])
        f1.addRow("模型选择:", self.setting_model)
        
        self.setting_privacy = QCheckBox("激活数据隐私隔离网关")
        self.setting_privacy.setObjectName("settingCheck")
        f1.addRow(self.setting_privacy)
        form_layout.addWidget(g1)
        
        # Group 2: Security Policy Config
        g2 = QGroupBox("安全策略")
        f2 = QFormLayout(g2)
        f2.setSpacing(8)
        self.setting_perm = QComboBox()
        self.setting_perm.setObjectName("settingCombo")
        self.setting_perm.addItems(["完全控制 (读/写/列表)", "安全只读 (仅列表/读取)", "彻底审计 (禁用所有操作)"])
        f2.addRow("操作权限:", self.setting_perm)
        self.setting_policy = QComboBox()
        self.setting_policy.setObjectName("settingCombo")
        self.setting_policy.addItems(["无需提示", "切换到敏感目录需提示", "每次切换目录都提示"])
        f2.addRow("目录审计:", self.setting_policy)
        form_layout.addWidget(g2)
        
        # Group 3: UI Preference Config
        g3 = QGroupBox("界面偏好")
        f3 = QFormLayout(g3)
        f3.setSpacing(8)
        
        self.setting_opacity = QSlider(Qt.Horizontal)
        self.setting_opacity.setObjectName("settingSlider")
        self.setting_opacity.setRange(50, 100)
        self.opacity_txt_label = QLabel("100%")
        self.opacity_txt_label.setStyleSheet("font-size: 12px; color: #555; font-weight: bold;")
        self.setting_opacity.valueChanged.connect(lambda v: self.opacity_txt_label.setText(f"{v}%"))
        
        slider_layout = QHBoxLayout()
        slider_layout.addWidget(self.setting_opacity)
        slider_layout.addWidget(self.opacity_txt_label)
        f3.addRow("窗口不透明度:", slider_layout)
        
        self.setting_topmost = QCheckBox("窗口始终置顶")
        self.setting_topmost.setObjectName("settingCheck")
        f3.addRow(self.setting_topmost)
        form_layout.addWidget(g3)
        form_layout.addStretch()
        
        # Save button
        save = QPushButton("💾 保存并应用")
        save.setObjectName("settingsSave")
        save.setCursor(QCursor(Qt.PointingHandCursor))
        save.clicked.connect(self._on_save_clicked)
        form_layout.addWidget(save, 0, Qt.AlignRight)
        
        scroll.setWidget(sc)
        layout.addWidget(scroll, 1)

    def load_settings_to_ui(self, settings_dict):
        """Map config dictionary data onto UI control states"""
        idx = self.setting_model.findText(settings_dict["model"])
        if idx >= 0: self.setting_model.setCurrentIndex(idx)
        
        self.setting_privacy.setChecked(settings_dict["privacy"])
        
        idx = self.setting_perm.findText(settings_dict["permission"])
        if idx >= 0: self.setting_perm.setCurrentIndex(idx)
        
        idx = self.setting_policy.findText(settings_dict["policy"])
        if idx >= 0: self.setting_policy.setCurrentIndex(idx)
        
        self.setting_opacity.setValue(settings_dict["opacity"])
        self.opacity_txt_label.setText(f"{settings_dict['opacity']}%")
        self.setting_topmost.setChecked(settings_dict["topmost"])

    def _on_save_clicked(self):
        """Pack UI data into a dictionary and emit out via signal"""
        payload = {
            "model": self.setting_model.currentText(),
            "privacy": self.setting_privacy.isChecked(),
            "permission": self.setting_perm.currentText(),
            "policy": self.setting_policy.currentText(),
            "opacity": self.setting_opacity.value(),
            "topmost": self.setting_topmost.isChecked()
        }
        self.settings_saved.emit(payload)