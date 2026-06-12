# -*- coding: utf-8 -*-
# Yindun Security Agent V2.1.0 - Independent Settings Panel Component
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QScrollArea, QGroupBox, QFormLayout, QComboBox, QCheckBox, QRadioButton, QButtonGroup
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCursor

# 🌟 核心跨模块导入：引入我们上一步刚做好的自定义模型资产表单配置舱
from yindun.gui.custom_model_dialog import CustomModelDialog

class SettingsPanel(QWidget):
    # 定义安全、高内聚的数据驱动信号互锁管道，分别同步最新配置及取消切页行为
    settings_saved = Signal(dict)
    cancel_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.custom_models = {} # 初始化本地外部大模型算力资产临时树缓存
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 14)
        layout.setSpacing(8)
        
        # 头部导航操作区（带扁平化返回指针）
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
        
        # 🌟 核心重构：将原本单一写死的下拉框改造为支持“资产动态追加”的组合水平流布局
        model_container = QHBoxLayout()
        self.setting_model = QComboBox()
        self.setting_model.setObjectName("settingCombo")
        model_container.addWidget(self.setting_model, 1) # 锁死弹性比例
        
        self.add_model_btn = QPushButton("➕ 新增外部")
        self.add_model_btn.setObjectName("addModelBtn")
        self.add_model_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_model_btn.clicked.connect(self._popup_custom_model_dialog)
        model_container.addWidget(self.add_model_btn)
        
        f1.addRow("模型选择:", model_container)
        
        self.setting_privacy = QCheckBox("激活数据隐私隔离网关")
        self.setting_privacy.setObjectName("settingCheck")
        f1.addRow(self.setting_privacy)
        form_layout.addWidget(g1)
        
        # 界面偏好
        g3 = QGroupBox("界面偏好")
        f3 = QFormLayout(g3)
        f3.setSpacing(8)
        
        self.setting_theme_light = QRadioButton("白色")
        self.setting_theme_dark = QRadioButton("黑色")
        self.setting_theme_light.setChecked(True)
        theme_group = QButtonGroup(self)
        theme_group.addButton(self.setting_theme_light)
        theme_group.addButton(self.setting_theme_dark)
        theme_container = QHBoxLayout()
        theme_container.addWidget(self.setting_theme_light)
        theme_container.addWidget(self.setting_theme_dark)
        theme_container.addStretch()
        f3.addRow("背景颜色:", theme_container)
        
        self.setting_topmost = QCheckBox("窗口始终置顶")
        self.setting_topmost.setObjectName("settingCheck")
        f3.addRow(self.setting_topmost)
        form_layout.addWidget(g3)
        form_layout.addStretch()
        
        # 底部下发磁盘落地并即时应用配置实体键
        save = QPushButton("确认")
        save.setObjectName("settingsSave")
        save.setCursor(QCursor(Qt.PointingHandCursor))
        save.clicked.connect(self._on_save_clicked)
        form_layout.addWidget(save, 0, Qt.AlignRight)
        
        scroll.setWidget(sc)
        layout.addWidget(scroll, 1)

    def load_settings_to_ui(self, settings_dict):
        """将外界传入的数据驱动Payload映射并刷新至界面各表单状态中"""
        # 1. 动态对齐：清空老旧下拉菜单，重新注入本地经典预设
        self.setting_model.clear()
        self.setting_model.addItems(["qwen2.5:1.5b", "qwen2.5:7b"])
        
        # 2. 从隐盾物理全局配置中释放出注册的所有自定义大模型名字
        self.custom_models = settings_dict.get("custom_models", {})
        if self.custom_models:
            self.setting_model.addItems(list(self.custom_models.keys()))
            
        idx = self.setting_model.findText(settings_dict["model"])
        if idx >= 0: self.setting_model.setCurrentIndex(idx)
        
        self.setting_privacy.setChecked(settings_dict["privacy"])
        if settings_dict.get("dark_mode", False):
            self.setting_theme_dark.setChecked(True)
        else:
            self.setting_theme_light.setChecked(True)
        self.setting_topmost.setChecked(settings_dict["topmost"])

    def _popup_custom_model_dialog(self):
        """物理唤醒资产配置子舱，并让其吸附于当前设置大卡片偏中位置弹出"""
        dlg = CustomModelDialog(self)
        if self.window():
            dlg.move(self.window().geometry().x() + 30, self.window().geometry().y() + 150)
        dlg.saved.connect(self._handle_new_custom_model)
        dlg.show()

    def _handle_new_custom_model(self, model_info):
        """阻断信号捕获，将新模型的资产详情及密钥追加注册进入主字典，并刷新下拉选择"""
        nick = model_info["nickname"]
        self.custom_models[nick] = {
            "base_url": model_info["base_url"],
            "api_key": model_info["api_key"],
            "model_id": model_info["model_id"]
        }
        self.setting_model.addItem(nick)
        self.setting_model.setCurrentText(nick)

    def _on_save_clicked(self):
        """收集当前界面更改的所有核心配置包，向外发射安全 Payload 以驱动主连廊完成更新"""
        payload = {
            "model": self.setting_model.currentText(),
            "privacy": self.setting_privacy.isChecked(),
            "dark_mode": self.setting_theme_dark.isChecked(),
            "topmost": self.setting_topmost.isChecked(),
            "custom_models": self.custom_models
        }
        self.settings_saved.emit(payload)