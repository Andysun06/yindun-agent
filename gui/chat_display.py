# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - Independent Chat Display Component
from PySide6.QtWidgets import QScrollArea, QWidget, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
from gui.chat_bubble import ChatBubble, StatusBanner
from datetime import datetime

class ChatDisplay(QScrollArea):
    """Independent scrollable message board component"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
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

    def add_message_bubble(self, role, text, current_window_width):
        """Instantiate and inject a native chat bubble into the flow layout"""
        timestamp = datetime.now().strftime("%H:%M")
        bubble = ChatBubble(role, text, timestamp, window_width=current_window_width)
        
        # Always insert above the bottom stretch anchor spring
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, bubble)
        QTimer.singleShot(40, self.auto_scroll_to_bottom)

    def add_status_banner(self, text):
        """Inject a centered system environment notice banner"""
        banner = StatusBanner(text)
        self.chat_layout.insertWidget(self.chat_layout.count() - 1, banner)
        QTimer.singleShot(40, self.auto_scroll_to_bottom)

    def auto_scroll_to_bottom(self):
        """Smoothly force the scrollbar slider to track the lowest limit"""
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def clear_messages(self):
        """Remove all message widgets while keeping the bottom stretch anchor."""
        while self.chat_layout.count() > 1:
            item = self.chat_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()