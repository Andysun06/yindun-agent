# -*- coding: utf-8 -*-
"""
隐盾 V2.1.0 — 💬 纯原生流式气泡组件
彻底抛弃 QTextBrowser 网页标签，改用原生布局与自适应物理卡片
升级：
1. 引入 QTextDocument 理想宽度计算矩阵，锁死 75% 阈值前的绝对不换行算法
2. 全面重构时间戳色彩矩阵，实现绿卡与白卡的高对比度现代化视觉
"""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QGraphicsDropShadowEffect
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QTextDocument

class ChatBubble(QWidget):
    """单条对话的高级原生弹性气泡组件 (全新升级自适应不提前换行与高对比度色彩)"""
    def __init__(self, role, text, timestamp, window_width=420, dark_mode=False, meta_info=""):
        super().__init__()
        self._is_user = (role == "user")
        self._dark_mode = dark_mode
        self._meta_info = meta_info  # 如 "快速   1m36s"
        # 缓存原始 markdown 文本，避免 update_width 重复读取 label 文本
        self._raw_text = text
        # 缓存原始缩进版本（用于动态调整缩进）
        self._original_text = text
        # 当前使用的缩进级别
        self._current_indent = 4

        # 1. 建立整行水平弹性卡片流布局
        row_layout = QHBoxLayout(self)
        row_layout.setContentsMargins(14, 5, 14, 5)
        row_layout.setSpacing(0)

        # 2. 创建承载气泡的物理外壳卡片
        self.bubble_card = QFrame()

        # 3. 精准锁死 75% 最大宽度红线
        max_bubble_width = int(window_width * 0.75) - 35

        # 4. 创建气泡内部的纵向文本排版系统
        card_layout = QVBoxLayout(self.bubble_card)
        card_layout.setContentsMargins(12, 9, 12, 9)
        card_layout.setSpacing(5)

        # 注入标准文本标签
        self.text_label = QLabel()
        self.text_label.setTextFormat(Qt.MarkdownText)
        # 根据宽度决定初始缩进
        self._current_indent = 2 if max_bubble_width < 320 else 4
        self.text_label.setText(self._adjust_indent(self._raw_text, self._current_indent))
        self.text_label.setWordWrap(True)

        # 计算理想宽度
        ideal_width = self._compute_ideal_width()

        if ideal_width < max_bubble_width:
            self.text_label.setFixedWidth(ideal_width)
        else:
            self.text_label.setFixedWidth(max_bubble_width)

        self.text_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_layout.addWidget(self.text_label)

        # 注入时间戳微型标签
        self.time_label = QLabel(timestamp)

        # AI 气泡底部信息栏（模式 + 耗时）
        self.meta_label = None
        if not self._is_user and self._meta_info:
            meta_row = QHBoxLayout()
            meta_row.setContentsMargins(0, 2, 0, 0)
            self.meta_label = QLabel(self._meta_info)
            self.meta_label.setAlignment(Qt.AlignRight)
            meta_row.addWidget(self.meta_label)
            card_layout.addLayout(meta_row)

        # 应用角色对应的皮肤与对齐策略
        self._apply_style()

        # 5. 装配外层水平流向
        if self._is_user:
            row_layout.addStretch(1)
            row_layout.addWidget(self.bubble_card)
        else:
            row_layout.addWidget(self.bubble_card)
            row_layout.addStretch(1)

    def set_dark_mode(self, dark: bool):
        """切换深色/浅色主题。AI 气泡需要重绘, 用户气泡保持绿色不动。"""
        if self._dark_mode == dark:
            return
        self._dark_mode = dark
        if not self._is_user:
            self._apply_style()

    def _apply_style(self):
        # 先清除旧阴影, 避免重复叠加
        if self.bubble_card.graphicsEffect() is not None:
            self.bubble_card.setGraphicsEffect(None)

        if self._is_user:
            # 用户气泡：绿色背景 + 黑色文字（深色模式下也保持一致）
            self.text_label.setStyleSheet("""
                QLabel { color: #000000; font-size: 13px; font-weight: 500; border: none; background: transparent; }
            """)
            self.time_label.setStyleSheet("""
                QLabel { font-size: 10px; color: #2c5a16; font-weight: 600; border: none; background: transparent; }
            """)
            self.time_label.setAlignment(Qt.AlignRight)
            self.bubble_card.setStyleSheet("""
                QFrame {
                    background-color: #95ec69;
                    border: 1px solid rgba(7, 193, 96, 30);
                    border-radius: 12px;
                    border-bottom-right-radius: 2px;
                }
            """)
        else:
            # AI 气泡：深色/浅色自适应
            if self._dark_mode:
                self.text_label.setStyleSheet("""
                    QLabel { color: #e0e0e0; font-size: 13px; border: none; background: transparent; }
                """)
                self.time_label.setStyleSheet("""
                    QLabel { font-size: 10px; color: #888; font-weight: 500; border: none; background: transparent; }
                """)
                if self.meta_label:
                    self.meta_label.setStyleSheet("""
                        QLabel { font-size: 10px; color: #6e80ff; font-weight: 500; border: none; background: transparent; }
                    """)
                self.bubble_card.setStyleSheet("""
                    QFrame {
                        background-color: #2a2a3e;
                        border: 1px solid #3a3a50;
                        border-radius: 12px;
                        border-bottom-left-radius: 2px;
                    }
                """)
            else:
                self.text_label.setStyleSheet("""
                    QLabel { color: #1e293b; font-size: 13px; border: none; background: transparent; }
                """)
                self.time_label.setStyleSheet("""
                    QLabel { font-size: 10px; color: #576574; font-weight: 500; border: none; background: transparent; }
                """)
                if self.meta_label:
                    self.meta_label.setStyleSheet("""
                        QLabel { font-size: 10px; color: #6e80ff; font-weight: 500; border: none; background: transparent; }
                    """)
                self.bubble_card.setStyleSheet("""
                    QFrame {
                        background-color: #ffffff;
                        border: 1px solid #e5e7eb;
                        border-radius: 12px;
                        border-bottom-left-radius: 2px;
                    }
                """)
                # 视觉阴影升级 (仅浅色模式)
                shadow = QGraphicsDropShadowEffect(self)
                shadow.setBlurRadius(8)
                shadow.setColor(QColor(0, 0, 0, 12))
                shadow.setOffset(0, 2)
                self.bubble_card.setGraphicsEffect(shadow)
            self.time_label.setAlignment(Qt.AlignLeft)

    def update_text(self, new_text, new_meta_info=None):
        """动态更新气泡内容（用于思考过程的渐进输出）
        保持同一个气泡 widget，只更新文本和宽度。"""
        self._raw_text = new_text
        self._original_text = new_text
        self.text_label.setText(self._adjust_indent(new_text, self._current_indent))

        # 重新计算理想宽度，更新气泡宽度
        ideal_width = self._compute_ideal_width()
        parent = self.parent()
        window_width = 420
        if parent is not None:
            window_width = parent.width()
        max_bubble_width = int(window_width * 0.75) - 35

        if ideal_width < max_bubble_width:
            self.text_label.setFixedWidth(ideal_width)
        else:
            self.text_label.setFixedWidth(max_bubble_width)

        # 可选更新元信息（最终答案时用到）
        if new_meta_info is not None and self.meta_label is not None:
            self.meta_label.setText(new_meta_info)

    def update_width(self, new_window_width):
        """当窗口宽度变化时，更新气泡文本标签的宽度（保持 75% 最大宽度约束）"""
        max_bubble_width = int(new_window_width * 0.75) - 35

        # 根据宽度动态调整缩进：窄气泡用2空格缩进，宽气泡用4空格缩进
        target_indent = 2 if max_bubble_width < 320 else 4
        if target_indent != self._current_indent:
            self._current_indent = target_indent
            # 更新文本标签内容（重新应用缩进）
            self.text_label.setText(self._adjust_indent(self._original_text, self._current_indent))
            # 重新计算理想宽度（因为文本内容变了）
            self._compute_ideal_width()

        # 使用缓存的原始文本和预计算的理想宽度
        ideal_width = self._ideal_width if hasattr(self, '_ideal_width') else self._compute_ideal_width()

        if ideal_width < max_bubble_width:
            self.text_label.setFixedWidth(ideal_width)
        else:
            self.text_label.setFixedWidth(max_bubble_width)

    def _compute_ideal_width(self):
        """使用 QTextDocument 计算文本理想宽度（结果会被缓存）"""
        doc = QTextDocument()
        doc.setDefaultFont(self.text_label.font())
        doc.setMarkdown(self.text_label.text())
        self._ideal_width = int(doc.idealWidth()) + 12
        return self._ideal_width

    def _adjust_indent(self, text, target_indent):
        """根据目标缩进级别调整文本中的代码块缩进
        target_indent: 2 或 4（空格数）
        """
        if target_indent == 4:
            return text
        lines = text.split('\n')
        result = []
        for line in lines:
            if line.startswith('    '):
                result.append(line[2:])
            elif line.startswith('  '):
                result.append(line)
            else:
                result.append(line)
        return '\n'.join(result)


class StatusBanner(QWidget):
    """常驻大厅中心的系统通知与环境状态条"""
    def __init__(self, text, dark_mode=False):
        super().__init__()
        self._dark_mode = dark_mode

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)

        self.lbl = QLabel(text)
        self.lbl.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl)

        self._apply_style()

    def set_dark_mode(self, dark: bool):
        if self._dark_mode == dark:
            return
        self._dark_mode = dark
        self._apply_style()

    def _apply_style(self):
        color = '#888' if self._dark_mode else '#94a3b8'
        self.lbl.setStyleSheet(
            f"font-size: 11px; color: {color}; background: transparent; border: none;"
        )