# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget, QListWidgetItem


class SessionSelectorPage(QWidget):
    """Independent session picker page used by MainWindow."""

    new_session_requested = Signal()
    open_session_requested = Signal(str)
    delete_session_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionPage")

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 14)
        root.setSpacing(8)

        self.session_title = QLabel("选择对话")
        self.session_title.setObjectName("titleText")
        root.addWidget(self.session_title)

        self.session_hint = QLabel("管理历史对话记录")
        self.session_hint.setStyleSheet("color:#6b7280;font-size:11px;")
        root.addWidget(self.session_hint)

        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
        self.session_list.setStyleSheet("""
            QListWidget#sessionList {
                background: #f8f9fc; border: 1px solid #e5e7eb;
                border-radius: 10px; padding: 4px; outline: none;
            }
            QListWidget#sessionList::item {
                padding: 8px 10px; border-radius: 8px; color: #334155;
                font-size: 12px; border: none;
            }
            QListWidget#sessionList::item:selected {
                background: #e8f5e9; color: #1b5e20; font-weight: 600;
            }
            QListWidget#sessionList::item:hover {
                background: #f1f5f9;
            }
        """)
        self.session_list.itemDoubleClicked.connect(lambda _: self._emit_open_current())
        root.addWidget(self.session_list, 1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.new_session_btn = QPushButton("新建对话")
        self.open_session_btn = QPushButton("进入对话")
        self.delete_session_btn = QPushButton("删除对话")

        for btn in (self.new_session_btn, self.open_session_btn, self.delete_session_btn):
            btn.setObjectName("sessionBtn")
            btn.setCursor(Qt.PointingHandCursor)

        self.new_session_btn.clicked.connect(self.new_session_requested.emit)
        self.open_session_btn.clicked.connect(self._emit_open_current)
        self.delete_session_btn.clicked.connect(self._emit_delete_current)
        row.addWidget(self.new_session_btn)
        row.addWidget(self.open_session_btn)
        row.addWidget(self.delete_session_btn)
        root.addLayout(row)

    def selected_session_id(self):
        item = self.session_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def render_sessions(self, sessions, current_session_id):
        self.session_list.clear()
        for session in sessions:
            title = session.get("title", "未命名对话")
            msg_count = len(session.get("messages", []))
            updated = session.get("updated_at", "")[:19].replace("T", " ")
            label = f"{title}  |  {msg_count} 条消息  |  {updated or '未开始'}"

            item = QListWidgetItem(label)
            sid = session.get("id")
            item.setData(Qt.UserRole, sid)
            if sid == current_session_id:
                item.setText("● " + item.text())
            self.session_list.addItem(item)

    def _emit_open_current(self):
        sid = self.selected_session_id()
        if sid:
            self.open_session_requested.emit(sid)

    def _emit_delete_current(self):
        sid = self.selected_session_id()
        if sid:
            self.delete_session_requested.emit(sid)
