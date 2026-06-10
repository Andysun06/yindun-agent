# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - Independent Status Bar Component
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import QTimer, Qt

class AgentStatusBar(QLabel):
    """Independent status banner featuring built-in thinking text animations"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusLabel")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedHeight(20)
        
        # Internal animation parameters
        self._think_timer = QTimer(self)
        self._think_dots = 0
        self._think_base = ""
        self._think_timer.timeout.connect(self._tick_think)

    def start_thinking(self, text=""):
        """Activate ticking animation loop with specific text"""
        self._think_base = text
        self._think_dots = 0
        self.setText(text)
        self._think_timer.start(400)

    def set_static_text(self, text):
        """Set an instant text message and halt active timers"""
        self._think_timer.stop()
        self.setText(text)

    def stop_thinking(self):
        """Terminate all animation updates and wipe display clear"""
        self._think_timer.stop()
        self.setText("")

    def _tick_think(self):
        """Periodically increment dot sequences to generate breathing effect"""
        self._think_dots = (self._think_dots + 1) % 4
        self.setText(f"🧠 {self._think_base}{'.' * self._think_dots}")