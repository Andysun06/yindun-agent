# -*- coding: utf-8 -*-
"""
隐盾 V2.0 — 🚨 高危操作审计拦截舱
当大模型企图调用本地物理工具执行跨目录越界写盘、删除等高危操作时，
触发该独立安全对话框，熔断挂起后台线程，强行等待人类安全员做出二次审批决策

V3.4 修复（盲签问题）：
  旧实现只展示"申请工具 + 目标路径"，对 run_local_command 而言人类看不到
  即将执行的命令、对写文件工具看不到即将写入的内容。审批人实际上是在
  不知情的情况下签字——与命令白名单的绕过风险叠加即构成"点一下授权 = 放行任意操作"。
  现在把工具入参渲染进弹窗，命令/写入内容以等宽字体完整展示（长内容可滚动）。
"""
import json

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
                               QPushButton, QGraphicsDropShadowEffect, QPlainTextEdit)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QFont

# 单个字段的最大展示字符数（超出截断并提示，避免弹窗被超长内容撑爆）
_MAX_DETAIL_CHARS = 800


def _format_tool_detail(tool_args) -> str:
    """把工具入参渲染成人类可审阅的文本（审批人必须看到"要做什么"）。

    优先展示最具决策价值的主载荷字段，其余字段以 JSON 兜底展示。
    """
    if not tool_args or not isinstance(tool_args, dict):
        return ""
    blocks = []

    def _clip(text: str) -> str:
        text = str(text)
        if len(text) > _MAX_DETAIL_CHARS:
            return text[:_MAX_DETAIL_CHARS] + f"\n…（已截断，共 {len(text)} 字）"
        return text

    if tool_args.get("command"):
        blocks.append(("即将执行的命令", _clip(tool_args["command"])))
    if tool_args.get("content") not in (None, ""):
        blocks.append((f"即将写入的内容（共 {len(str(tool_args['content']))} 字）",
                       _clip(tool_args["content"])))
    if tool_args.get("new_content") not in (None, ""):
        blocks.append((f"替换后的内容（共 {len(str(tool_args['new_content']))} 字）",
                       _clip(tool_args["new_content"])))
    if tool_args.get("old_content") not in (None, ""):
        blocks.append((f"将被替换的旧内容（共 {len(str(tool_args['old_content']))} 字）",
                       _clip(tool_args["old_content"])))
    if tool_args.get("filename"):
        blocks.append(("目标文件", _clip(tool_args["filename"])))

    if not blocks:
        # 其他工具：完整展示参数（过滤掉已展示的与无语义的占位字段）
        payload = {k: v for k, v in tool_args.items()
                   if k not in ("target_directory",) and v not in (None, "")}
        if not payload:
            return ""
        blocks.append(("工具参数", _clip(json.dumps(payload, ensure_ascii=False, indent=2))))

    return "\n\n".join(f"【{title}】\n{body}" for title, body in blocks)


class ConfirmDialog(QWidget):
    """高危越界操作二次人工确认审计对话框"""
    # 建立一条安全的布尔型审批信号管道 (True代表批准放行，False代表驳回拦截)
    confirmed = Signal(bool)

    def __init__(self, tool_name, target_path, tool_args=None, parent=None):
        super().__init__(parent)
        # 固定温馨小巧的警告窗尺寸
        self.setFixedSize(460, 360)
        # 强行抹去Windows系统自带的死板白框，永远置顶并作为独立模态窗口弹出
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # 1. 组装一层带有安全防线的半透明外壳大卡片
        self.card = QFrame(self)
        self.card.setGeometry(10, 10, 440, 340)
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
        main_layout.setSpacing(8)

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
            f"安全内核检测到 Agent 请求执行高危操作，已熔断挂起后台线程，"
            f"等待人工安全审批。\n\n"
            f"📌 申请工具: {tool_name}\n"
            f"📌 目标路径: {target_path}"
        )
        msg_lbl.setWordWrap(True)
        msg_lbl.setStyleSheet("""
            font-size: 11px;
            color: #475569;
            line-height: 1.5;
            background: transparent;
            border: none;
        """)
        main_layout.addWidget(msg_lbl)

        # ★ 待审内容详情：审批人必须看到"到底要执行什么/写什么"才能签字
        detail_text = _format_tool_detail(tool_args)
        if detail_text:
            hint_lbl = QLabel("⚠️ 请核对以下内容后再决定是否授权：")
            hint_lbl.setStyleSheet("""
                font-size: 11px;
                font-weight: 600;
                color: #b91c1c;
                background: transparent;
                border: none;
            """)
            main_layout.addWidget(hint_lbl)

            self.detail_view = QPlainTextEdit(detail_text)
            self.detail_view.setReadOnly(True)
            self.detail_view.setFixedHeight(120)
            self.detail_view.setFont(QFont("Consolas", 9))
            self.detail_view.setStyleSheet("""
                QPlainTextEdit {
                    background-color: #fff7f7;
                    border: 1px solid #fecaca;
                    border-radius: 8px;
                    padding: 6px;
                    color: #334155;
                    font-size: 11px;
                }
            """)
            main_layout.addWidget(self.detail_view)

        main_layout.addStretch(1)

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
