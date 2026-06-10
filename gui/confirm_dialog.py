# -*- coding: utf-8 -*-
"""
隐盾 V2.0 — 🚨 高危操作审计拦截舱
当大模型企图调用本地物理工具执行跨目录越界写盘、删除等高危操作时，
触发该独立安全对话框，熔断挂起后台线程，强行等待人类安全员做出二次审批决策
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QPushButton, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor

class ConfirmDialog(QWidget):
    """高危越界操作二次人工确认审计对话框"""
    # 建立一条安全的布尔型审批信号管道 (True代表批准放行，False代表驳回拦截)
    confirmed = Signal(bool)

    def __init__(self, tool_name, target_path, parent=None):
        super().__init__(parent)
        # 固定温馨小巧的警告窗尺寸
        self.setFixedSize(350, 200)
        # 强行抹去Windows系统自带的死板白框，永远置顶并作为独立模态窗口弹出
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 1. 组装一层带有安全防线的半透明外壳大卡片
        self.card = QFrame(self)
        self.card.setGeometry(10, 10, 330, 180)
        # 注入高级微带血红的高危警告卡片皮肤
        self.card.setStyleSheet("""
            QFrame {
                background-color: #ffffff;
                border: 1px solid rgba(220, 38, 38, 40);
                border-radius: 18px;
            }
        """)
        
        # 🌟 视觉升级：为拦截弹窗注入物理级扩散阴影，使其在桌面上浮现时极具立体冲击感
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setColor(QColor(220, 38, 38, 30)) # 淡红色防御类微光阴影
        shadow.setOffset(0, 4)
        self.card.setGraphicsEffect(shadow)
        
        # 2. 卡片内部主纵向安全布局
        main_layout = QVBoxLayout(self.card)
        main_layout.setContentsMargins(20, 16, 20, 16)
        main_layout.setSpacing(10)
        
        # 头部红色硬核盾牌警告标签
        title_lbl = QLabel("🚨 隐盾安全决策网关拦截")
        title_lbl.setStyleSheet("""
            font-size: 14px; 
            font-weight: 700; 
            color: #dc2626; 
            background: transparent; 
            border: none;
        """)
        main_layout.addWidget(title_lbl)
        
        # 核心审计上下文详情展示区
        msg_lbl = QLabel(
            f"安全内核检测到 Agent 正在暗中调用物理机械臂工具，试图切入默认沙箱外的敏感绝对路径，系统已依法熔断拦截。\n\n"
            f"📌 申请工具: {tool_name}\n"
            f"📌 越界目标: {target_path}"
        )
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("""
            font-size: 11px; 
            color: #475569; 
            line-height: 1.5; 
            background: transparent; 
            border: none;
        """)
        main_layout.addWidget(msg_lbl, 1)
        
        # 3. 底部双轨人工审批控制按钮行
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        
        # 🟥 驳回拦截按钮 — 采用温和红灰色交互皮肤
        deny_btn = QPushButton("🟥 驳回拦截")
        deny_btn.setCursor(QCursor(Qt.PointingHandCursor))
        deny_btn.setStyleSheet("""
            QPushButton {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
                color: #64748b;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #fee2e2;
                border-color: #fca5a5;
                color: #dc2626;
            }
        """)
        deny_btn.clicked.connect(lambda: self._decide(False))
        
        # 🟩 授权批准执行按钮 — 采用高亮科技绿皮肤
        approve_btn = QPushButton("🟩 授权执行")
        approve_btn.setCursor(QCursor(Qt.PointingHandCursor))
        approve_btn.setStyleSheet("""
            QPushButton {
                background-color: #07c160;
                border: none;
                border-radius: 8px;
                padding: 6px 14px;
                font-size: 12px;
                color: white;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #06ad56;
            }
            QPushButton:pressed {
                background-color: #059a4c;
            }
        """)
        approve_btn.clicked.connect(lambda: self._decide(True))
        
        btn_layout.addWidget(deny_btn)
        btn_layout.addWidget(approve_btn)
        main_layout.addLayout(btn_layout)
        
    def _decide(self, approved):
        """发射审批决策结果并销毁弹窗窗口"""
        self.confirmed.emit(approved)
        self.close()