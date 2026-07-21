# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - Independent Chat Display Component
from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QTimer, Signal
from yindun.gui.chat_bubble import ChatBubble, StatusBanner
from yindun.gui.rounded_scrollbar import RoundedScrollBar
from datetime import datetime
class ChatDisplay(QScrollArea):
    """Independent scrollable message board component"""
    file_dropped = Signal(list)  # 拖放文件时发出，参数为本地文件路径列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setAcceptDrops(True)  # 启用拖放接收
        self._dark_mode = False
        # 节流：避免拖动边缘时频繁更新所有气泡（卡顿优化）
        self._update_timer = QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(60)  # 60ms 节流窗口
        self._update_timer.timeout.connect(self._do_update_all_bubbles_width)
        self._pending_width = 0

        # 替换为自定义胶囊形圆角滚动条 (Qt QSS border-radius 不够圆)
        self.setVerticalScrollBar(RoundedScrollBar(self, dark=False))
        self.verticalScrollBar().setFixedWidth(10)

        # Inner canvas with custom styling
        self.inner_canvas = QWidget()
        self.inner_canvas.setStyleSheet("background: #f5f6f8; border: none;")

        # Vertical flow layout for bubbles
        self.chat_layout = QVBoxLayout(self.inner_canvas)
        self.chat_layout.setContentsMargins(0, 10, 0, 10)
        self.chat_layout.setSpacing(4)

        # Base anchor spring to push messages from bottom to top
        self.chat_layout.addStretch(1)

        self.setWidget(self.inner_canvas)

    def set_dark_mode(self, dark: bool):
        """切换聊天区域深色/浅色主题, 同步所有历史气泡"""
        if self._dark_mode == dark:
            return
        self._dark_mode = dark
        self.inner_canvas.setStyleSheet(
            f"background: {'#1a1a2e' if dark else '#f5f6f8'}; border: none;"
        )
        # 同步滚动条主题
        sb = self.verticalScrollBar()
        if isinstance(sb, RoundedScrollBar):
            sb.set_dark(dark)
        # 同步所有历史气泡和状态条的主题
        for i in range(self.chat_layout.count() - 1):  # 跳过末尾 stretch
            item = self.chat_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, (ChatBubble, StatusBanner)):
                w.set_dark_mode(dark)

    def add_message_bubble(self, role, text, current_window_width, meta_info=""):
        """Instantiate and inject a native chat bubble into the flow layout"""
        timestamp = datetime.now().strftime("%H:%M")
        bubble = ChatBubble(role, text, timestamp, window_width=current_window_width,
                           dark_mode=self._dark_mode, meta_info=meta_info)
        
        # Always insert above the bottom stretch anchor spring
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        QTimer.singleShot(40, self.auto_scroll_to_bottom)

    def update_last_assistant_bubble(self, text, meta_info=None):
        """更新最后一个 AI 气泡的内容（用于思考过程的渐进输出）
        如果没有 AI 气泡则返回 False"""
        # 从后往前找，跳过底部的 stretch，找到第一个 AI 气泡
        for i in range(self.chat_layout.count() - 1, -1, -1):
            item = self.chat_layout.itemAt(i)
            w = item.widget() if item else None
            if isinstance(w, ChatBubble) and not w._is_user:
                w.update_text(text, meta_info)
                QTimer.singleShot(40, self.auto_scroll_to_bottom)
                return True
        return False

    def add_status_banner(self, text):
        """Inject a centered system environment notice banner"""
        banner = StatusBanner(text, dark_mode=self._dark_mode)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, banner)
        QTimer.singleShot(40, self.auto_scroll_to_bottom)

    def auto_scroll_to_bottom(self):
        """仅当用户已在底部时才自动滚动，避免强制拉回（翻阅历史时不受干扰）"""
        sb = self.verticalScrollBar()
        if sb.value() >= sb.maximum() - 20:
            sb.setValue(sb.maximum())

    def clear_messages(self):
        """Remove all message widgets while keeping the bottom stretch anchor."""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def update_all_bubbles_width(self, available_width):
        """当窗口宽度变化时，更新所有气泡的宽度（保持 75% 最大宽度约束）
        使用节流：60ms 内的多次调用只会执行最后一次
        available_width: 聊天区域实际可用宽度（不是窗口总宽度）
        """
        self._pending_width = available_width
        # 重启定时器，60ms 内只执行一次实际更新
        self._update_timer.start()

    def _do_update_all_bubbles_width(self):
        """实际执行气泡宽度更新（节流后的真实操作）"""
        available_width = self._pending_width
        if available_width <= 0:
            return
        # 确保 inner_canvas 的宽度与可用宽度同步
        vp_width = self.viewport().width()
        if vp_width > 0:
            self.inner_canvas.setFixedWidth(vp_width)
        for i in range(self.chat_layout.count()):
            item = self.chat_layout.itemAt(i)
            if item:
                widget = item.widget()
                if isinstance(widget, ChatBubble):
                    widget.update_width(available_width)

    # ──────────────────────────────────────────
    # 拖放文件支持
    # ──────────────────────────────────────────
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        """放下：收集所有本地文件路径，发出 file_dropped 信号"""
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.file_dropped.emit(paths)
            event.acceptProposedAction()
        else:
            event.ignore()