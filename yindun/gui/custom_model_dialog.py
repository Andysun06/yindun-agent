# -*- coding: utf-8 -*-
"""
隐盾 V2.1.0 — 🔌 外部算力资产配置舱
独立无边框高定表单弹窗，负责收集外部 OpenAI/DeepSeek 兼容流的密钥与网关地址
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QLineEdit, QPushButton, QFormLayout, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor

class CustomModelDialog(QWidget):
    """外部大模型 API 配置对话框"""
    # 建立安全的数据承载管道，保存时向外发射配置字典
    saved = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(360, 260)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 1. 组装物理圆角大卡片外壳
        self.card = QFrame(self)
        self.card.setGeometry(10, 10, 340, 240)
        self.card.setStyleSheet("""
            QFrame { background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 18px; }
        """)
        
        # 注入细腻的高斯模糊物理微阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(7, 193, 96, 25)) # 科技绿微光阴影
        shadow.setOffset(0, 4)
        self.card.setGraphicsEffect(shadow)
        
        # 2. 内部主布局
        main_layout = QVBoxLayout(self.card)
        main_layout.setContentsMargins(18, 14, 18, 14)
        main_layout.setSpacing(8)
        
        title_lbl = QLabel("🔌 接入自定义外部算力模型")
        title_lbl.setStyleSheet("font-size: 13px; font-weight: 700; color: #1e293b; border: none; background: transparent;")
        main_layout.addWidget(title_lbl)
        
        # 3. 核心表单区域
        form = QFormLayout()
        form.setSpacing(6)
        
        # 统一样式函数
        def make_input(holder):
            inp = QLineEdit()
            inp.setPlaceholderText(holder)
            inp.setStyleSheet("""
                QLineEdit { border: 1px solid #cbd5e1; border-radius: 8px; padding: 4px 8px; font-size: 11px; }
                QLineEdit:focus { border-color: #07c160; }
            """)
            return inp

        self.name_in = make_input("例如: DeepSeek-V3")
        self.url_in = make_input("基础API地址，例如: https://api.deepseek.com/v1")
        self.key_in = make_input("输入合规的 API Secret Key")
        self.key_in.setEchoMode(QLineEdit.Password) # 密码隐藏保护密钥
        self.id_in = make_input("对应的核心模型标识，例如: deepseek-chat")
        
        form.addRow("显示名称:", self.name_in)
        form.addRow("Base URL:", self.url_in)
        form.addRow("API Key:", self.key_in)
        form.addRow("模型标识:", self.id_in)
        main_layout.addLayout(form)
        
        # 4. 底部双轨控制按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.setCursor(QCursor(Qt.PointingHandCursor))
        cancel_btn.setStyleSheet("""
            QPushButton { background-color: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 5px; font-size: 11px; color: #64748b; }
            QPushButton:hover { background-color: #f1f5f9; }
        """)
        cancel_btn.clicked.connect(self.close)
        
        save_btn = QPushButton("💾 验证并添加")
        save_btn.setCursor(QCursor(Qt.PointingHandCursor))
        save_btn.setStyleSheet("""
            QPushButton { background-color: #07c160; border: none; border-radius: 6px; padding: 5px; font-size: 11px; color: white; font-weight: 600; }
            QPushButton:hover { background-color: #06ad56; }
        """)
        save_btn.clicked.connect(self._on_save)
        
        btn_layout.addWidget(cancel_btn)
        btn_layout.addWidget(save_btn)
        main_layout.addLayout(btn_layout)
        
    def _on_save(self):
        """校验并向外层发射资产Payload"""
        name = self.name_in.text().strip()
        url = self.url_in.text().strip()
        key = self.key_in.text().strip()
        mid = self.id_in.text().strip()
        
        if name and url and key and mid:
            self.saved.emit({
                "nickname": name,
                "base_url": url,
                "api_key": key,
                "model_id": mid
            })
            self.close()