# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - Independent Status Bar Component
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QTimer, Qt, Signal

class AgentStatusBar(QLabel):
    """Independent status banner featuring built-in thinking text animations"""
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusLabel")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(20)

        # Internal animation parameters
        self._thinking = False
        self._think_timer = QTimer(self)
        self._think_dots = 0
        self._think_base = ""
        self._think_timer.timeout.connect(self._tick_think)
        self.setCursor(Qt.ArrowCursor)

    def start_thinking(self, text=""):
        """Activate ticking animation loop with specific text"""
        self._thinking = True
        self._think_base = text
        self._think_dots = 0
        self.setText(f"🧠 {text}（点击此处取消）")
        self._think_timer.start(400)
        self.setCursor(Qt.PointingHandCursor)

    def set_static_text(self, text):
        """Set an instant text message and halt active timers"""
        self._thinking = False
        self._think_timer.stop()
        self.setText(text)
        self.setCursor(Qt.ArrowCursor)

    def stop_thinking(self):
        """Terminate all animation updates and wipe display clear"""
        self._thinking = False
        self._think_timer.stop()
        self.setText("")
        self.setCursor(Qt.ArrowCursor)

    def _tick_think(self):
        """Periodically increment dot sequences to generate breathing effect"""
        self._think_dots = (self._think_dots + 1) % 4
        self.setText(f"🧠 {self._think_base}（点击此处取消）{'.' * self._think_dots}")

    def mousePressEvent(self, event):
        """If clicked while thinking, emit cancel signal"""
        if self._thinking and event.button() == Qt.LeftButton:
            self.cancel_requested.emit()
        super().mousePressEvent(event)