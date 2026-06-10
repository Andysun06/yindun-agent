# -*- coding: utf-8 -*-
"""
隐盾 V2.0 — 💬 纯原生流式气泡组件
彻底抛弃 QTextBrowser 网页标签，改用原生布局与自适应物理卡片
实现 75% 黄金分割线单行不提前换行算法
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

class ChatBubble(QWidget):
    """单条对话的高级原生弹性气泡组件"""
    def __init__(self, role, text, timestamp, window_width=420):
        super().__init__()
        is_user = (role == "user")
        
        # 1. 建立整行水平弹性卡片流布局
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 5, 14, 5)
        row_layout.setSpacing(0)
        
        # 2. 创建承载气泡的物理外壳卡片
        self.bubble_card = QFrame()
        
        # 3. 🎯 核心算法：精准锁死 75% 最大宽度红线 (窗口宽 420px * 0.75 = 315px)
        # 减去气泡自身的内边距，文字标签的最大宽度死锁在 280px
        # 这样文字在没达到 280px 宽以前，绝对保持单行横向延伸；一旦达到 280px，才会柔和换行
        max_bubble_width = int(window_width * 0.75) - 35
        
        # 4. 创建气泡内部的纵向文本排版系统
        card_layout = QVBoxLayout(self.bubble_card)
        card_layout.setContentsMargins(12, 9, 12, 9)
        card_layout.setSpacing(3)
        
        # 注入标准文本标签
        self.text_label = QLabel(text)
        self.text_label.setWordWrap(True) # 激活自适应换行
        self.text_label.setMaximumWidth(max_bubble_width) # 强行拦截换行边界
        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse) # 支持鼠标划选机密文本
        card_layout.addWidget(self.text_label)
        
        # 注入时间戳微型标签
        self.time_label = QLabel(timestamp)
        self.time_label.setStyleSheet("font-size: 10px; color: #b0b8c9; border: none; background: transparent;")
        
        # 5. 根据发送者角色，像素级渲染高定皮肤与对齐策略
        if is_user:
            self.text_label.setStyleSheet("color: #000000; font-size: 13px; font-weight: 500; border: none; background: transparent;")
            self.time_label.setAlignment(Qt.AlignRight)
            card_layout.addWidget(self.time_label)
            
            # 现代化科技绿微弧度卡片样式 (圆角错落：左下角圆，右下角尖)
            self.bubble_card.setStyleSheet("""
                QFrame {
                    background-color: #95ec69;
                    border: 1px solid rgba(7, 193, 96, 30);
                    border-radius: 12px;
                    border-bottom-right-radius: 2px;
                }
            """)
            # 用户气泡推向右侧
            row_layout.addStretch(1)
            row_layout.addWidget(self.bubble_card)
        else:
            self.text_label.setStyleSheet("color: #1e293b; font-size: 13px; line-height: 1.5; border: none; background: transparent;")
            self.time_label.setAlignment(Qt.AlignLeft)
            card_layout.addWidget(self.time_label)
            
            # 极简高贵纯白卡片样式 (圆角错落：左下角尖，右下角圆)
            self.bubble_card.setStyleSheet("""
                QFrame {
                    background-color: #ffffff;
                    border: 1px solid #e5e7eb;
                    border-radius: 12px;
                    border-bottom-left-radius: 2px;
                }
            """)
            
            # 🌟 视觉升级：为 AI 的答复卡片注入极其细腻的、带有半透明高斯模糊的物理微阴影
            shadow = QGraphicsDropShadowEffect(self)
            shadow.setBlurRadius(8)
            shadow.setColor(QColor(0, 0, 0, 12))
            shadow.setOffset(0, 2)
            self.bubble_card.setGraphicsEffect(shadow)
            
            # AI 气泡推向左侧
            row_layout.addWidget(self.bubble_card)
            row_layout.addStretch(1)


class StatusBanner(QWidget):
    """常驻大厅中心的系统通知与环境状态条"""
    def __init__(self, text):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("""
            font-size: 11px; 
            color: #94a3b8; 
            background: transparent; 
            border: none;
        """)
        layout.addWidget(lbl)