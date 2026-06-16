# -*- coding: utf-8 -*-
# Yindun Security Agent V3.1.4 - Independent Settings Panel Component (Real-time Adaptive)
import subprocess
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QScrollArea, QGroupBox, QFormLayout, QComboBox, QRadioButton, QButtonGroup, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

# 核心跨模块导入：引入自定义模型资产表单配置舱
from yindun.gui.custom_model_dialog import CustomModelDialog

def detect_ollama_models():
    """检测本地 Ollama 已安装的模型列表"""
    try:
        result = subprocess.run(
            ["ollama", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return [m["name"] for m in data.get("models", [])]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return []

def get_first_available_model():
    """获取第一个可用的模型，如果没有则返回 None"""
    models = detect_ollama_models()
    return models[0] if models else None

class SlidingSwitch(QFrame):
    """高级原子级左右滑动开关组件"""
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 22)
        self._checked = False
        self.setObjectName("switchBase")
        
        # 组装内部滑动小圆球
        self.handle = QFrame(self)
        self.handle.setObjectName("switchHandle")
        self.handle.setFixedSize(16, 16)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        
        self._update_position()
        self.update_style()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, emit_signal=True):
        if self._checked != checked:
            self._checked = checked
            self._update_position()
            self.update_style()
            if emit_signal:
                self.toggled.emit(self._checked)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)

    def _update_position(self):
        # 拨动至右侧或左侧
        if self._checked:
            self.handle.move(26, 3)
        else:
            self.handle.move(4, 3)

    def update_style(self):
        base_bg = "#07c160" if self._checked else "#cbd5e1"
        self.setStyleSheet(f"""
            QFrame#switchBase {{ background-color: {base_bg}; border-radius: 11px; border: none; }}
            QFrame#switchHandle {{ background-color: #ffffff; border-radius: 8px; border: none; }}
        """)


class SettingsPanel(QWidget):
    # 定义高内聚的数据驱动信号管道，数据改变时即时同步向外发射
    settings_saved = Signal(dict)
    cancel_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.custom_models = {} 
        self._is_loading = False # 互锁状态旗标，防止初始化加载数据时循环触发即时保存
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 14)
        layout.setSpacing(8)
        
        # 头部导航操作区
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
        
        # 弹性表单长区域视舱
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        sc = QWidget()
        sc.setStyleSheet("background:transparent;")
        form_layout = QVBoxLayout(sc)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(6)
        
        # 舱区 1：多算力引擎组合配置
        g1 = QGroupBox("算力底座")
        f1 = QFormLayout(g1)
        f1.setSpacing(8)
        
        model_container = QHBoxLayout()
        self.setting_model = QComboBox()
        self.setting_model.setObjectName("settingCombo")
        # 🌟 即时生效：模型下拉选择切换时立刻驱动更新
        self.setting_model.currentTextChanged.connect(self._trigger_immediate_save)
        model_container.addWidget(self.setting_model, 1)
        
        self.add_model_btn = QPushButton("➕ 新增外部")
        self.add_model_btn.setObjectName("addModelBtn")
        self.add_model_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_model_btn.clicked.connect(self._popup_custom_model_dialog)
        model_container.addWidget(self.add_model_btn)
        f1.addRow("模型选择:", model_container)
        
        # 🌟 核心升级：换入左右滑动开关，并完美靠右挂载表单
        self.setting_privacy = SlidingSwitch()
        self.setting_privacy.toggled.connect(self._trigger_immediate_save)
        f1.addRow("隐私隔离网关:", self.setting_privacy)
        form_layout.addWidget(g1)
        
        # 舱区 2：界面偏好自定义
        g3 = QGroupBox("界面偏好")
        f3 = QFormLayout(g3)
        f3.setSpacing(8)
        
        self.setting_theme_light = QRadioButton("白色")
        self.setting_theme_dark = QRadioButton("黑色")
        self.setting_theme_light.setChecked(True)
        
        theme_group = QButtonGroup(self)
        theme_group.addButton(self.setting_theme_light)
        theme_group.addButton(self.setting_theme_dark)
        # 🌟 即时生效：点击任意主题色单选框时立刻应用深色/浅色皮肤
        theme_group.buttonClicked.connect(self._trigger_immediate_save)
        
        theme_container = QHBoxLayout()
        theme_container.addWidget(self.setting_theme_light)
        theme_container.addWidget(self.setting_theme_dark)
        theme_container.addStretch()
        f3.addRow("背景颜色:", theme_container)
        
        # 🌟 核心升级：窗口置顶换入高定制滑动开关
        self.setting_topmost = SlidingSwitch()
        self.setting_topmost.toggled.connect(self._trigger_immediate_save)
        f3.addRow("窗口始终置顶:", self.setting_topmost)
        form_layout.addWidget(g3)
        
        # 🌟 终极破局：完全干掉底部的“保存/保存设置”确认按钮，留出极简现代的表单尾部
        form_layout.addStretch()
        
        scroll.setWidget(sc)
        layout.addWidget(scroll, 1)

    def load_settings_to_ui(self, settings_dict):
        """将连廊传入的数据驱动Payload安全反序列化至界面状态中"""
        self._is_loading = True
        
        self.setting_model.clear()
        
        ollama_models = detect_ollama_models()
        if ollama_models:
            self.setting_model.addItems(ollama_models)
        
        self.custom_models = settings_dict.get("custom_models", {})
        if self.custom_models:
            self.setting_model.addItems(list(self.custom_models.keys()))
        
        if not ollama_models and not self.custom_models:
            self.setting_model.addItems(["qwen2.5:7b-instruct", "qwen2.5:7b"])
        
        idx = self.setting_model.findText(settings_dict["model"])
        if idx >= 0:
            self.setting_model.setCurrentIndex(idx)
        
        # 静默装填滑动开关状态，不抛出变化信号
        self.setting_privacy.setChecked(settings_dict["privacy"], emit_signal=False)
        
        if settings_dict.get("dark_mode", False):
            self.setting_theme_dark.setChecked(True)
        else:
            self.setting_theme_light.setChecked(True)
            
        self.setting_topmost.setChecked(settings_dict["topmost"], emit_signal=False)
        
        self._is_loading = False # 释放互锁，主逻辑恢复即时存盘状态

    def _popup_custom_model_dialog(self):
        dlg = CustomModelDialog(self)
        if self.window():
            dlg.move(self.window().geometry().x() + 30, self.window().geometry().y() + 150)
        dlg.saved.connect(self._handle_new_custom_model)
        dlg.show()

    def _handle_new_custom_model(self, model_info):
        nick = model_info["nickname"]
        self.custom_models[nick] = {
            "base_url": model_info["base_url"],
            "api_key": model_info["api_key"],
            "model_id": model_info["model_id"]
        }
        self.setting_model.addItem(nick)
        self.setting_model.setCurrentText(nick)
        # 🌟 即时生效：新增外部算力模型完成时，自动触发一次全局保存
        self._trigger_immediate_save()

    def _trigger_immediate_save(self):
        """🌟 核心即时保存网关：只要表单状态发生任何位移，立刻打包数据同步给大脑大管家"""
        if self._is_loading: 
            return
            
        payload = {
            "model": self.setting_model.currentText(),
            "privacy": self.setting_privacy.isChecked(),
            "dark_mode": self.setting_theme_dark.isChecked(),
            "topmost": self.setting_topmost.isChecked(),
            "custom_models": self.custom_models
        }
        self.settings_saved.emit(payload)