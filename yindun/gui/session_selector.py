# -*- coding: utf-8 -*-
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QListWidget, QListWidgetItem)


class SessionItemWidget(QWidget):
    """会话列表项控件：标题 + 元信息，支持自动换行、行高自适应"""

    def __init__(self, title, meta, is_current=False, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(2)

        # 标题（可加粗，自动换行，当前会话加 ● 前缀）
        self.title_label = QLabel(("● " if is_current else "") + title)
        self.title_label.setWordWrap(True)
        self.title_label.setObjectName("titleLabel")
        lay.addWidget(self.title_label)

        # 元信息（次要灰色小字，自动换行）
        self.meta_label = QLabel(meta)
        self.meta_label.setWordWrap(True)
        self.meta_label.setObjectName("metaLabel")
        lay.addWidget(self.meta_label)

    def set_dark_mode(self, dark: bool):
        """跟随主题设置文字配色"""
        self.title_label.setStyleSheet(
            f"color:{'#e0e6f0' if dark else '#1f2937'};font-size:13px;font-weight:600;"
        )
        self.meta_label.setStyleSheet(
            f"color:{'#7a7f94' if dark else '#94a3b8'};font-size:11px;"
        )


class SessionSelectorPage(QWidget):
    """Independent session picker page used by MainWindow."""

    new_session_requested = Signal()
    open_session_requested = Signal(str)
    delete_session_requested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sessionPage")
        self._dark_mode = False
        self._items = []  # 保存 (item, widget) 便于尺寸刷新

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 14, 12, 14)
        root.setSpacing(8)

        self.session_title = QLabel("选择对话")
        self.session_title.setObjectName("titleText")
        root.addWidget(self.session_title)

        self.session_hint = QLabel("管理历史对话记录")
        self.session_hint.setObjectName("sessionHint")
        root.addWidget(self.session_hint)

        self.session_list = QListWidget()
        self.session_list.setObjectName("sessionList")
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

        self._apply_theme(enabled=False)

    def set_dark_mode(self, dark: bool):
        self._dark_mode = dark
        self._apply_theme(enabled=dark)
        for _, widget in self._items:
            widget.set_dark_mode(dark)

    def _apply_theme(self, enabled: bool):
        dark = enabled
        # 提示文字
        self.session_hint.setStyleSheet(
            f"color:{'#888' if dark else '#6b7280'};font-size:11px;"
        )
        # 列表样式
        self.session_list.setStyleSheet(f"""
            QListWidget#sessionList {{
                background: {'#222240' if dark else '#f8f9fc'};
                border: 1px solid {'#3a3a50' if dark else '#e5e7eb'};
                border-radius: 10px; padding: 4px; outline: none;
            }}
            QListWidget#sessionList::item {{
                padding: 6px 8px; border-radius: 8px;
                color: {'#ccc' if dark else '#334155'};
                font-size: 12px; border: none;
            }}
            QListWidget#sessionList::item:selected {{
                background: {'#1a3a5c' if dark else '#e8f5e9'};
                color: {'#60a5fa' if dark else '#1b5e20'};
            }}
            QListWidget#sessionList::item:hover {{
                background: {'#303050' if dark else '#f1f5f9'};
            }}
        """)

    def resizeEvent(self, event):
        """列表宽度变化后刷新各项行高，保证换行文本不被纵向裁切"""
        super().resizeEvent(event)
        for item, _ in self._items:
            self._sync_item_size(item)

    def _sync_item_size(self, item):
        """依据控件当前 sizeHint 同步对应项的行高"""
        widget = self.session_list.itemWidget(item)
        if widget is not None:
            item.setSizeHint(QSize(widget.sizeHint().width(), widget.sizeHint().height()))

    def selected_session_id(self):
        item = self.session_list.currentItem()
        if item is None:
            return None
        return item.data(Qt.UserRole)

    def render_sessions(self, sessions, current_session_id):
        self.session_list.clear()
        self._items = []
        for session in sessions:
            title = session.get("title", "未命名对话")
            msg_count = len(session.get("messages", []))
            updated = session.get("updated_at", "")[:19].replace("T", " ")
            meta = f"{msg_count} 条消息  ·  {updated or '未开始'}"

            sid = session.get("id")
            is_current = sid == current_session_id

            item = QListWidgetItem()
            item.setData(Qt.UserRole, sid)
            self.session_list.addItem(item)

            widget = SessionItemWidget(title, meta, is_current=is_current)
            widget.set_dark_mode(self._dark_mode)
            self.session_list.setItemWidget(item, widget)
            self._items.append((item, widget))
            self._sync_item_size(item)

    def _emit_open_current(self):
        sid = self.selected_session_id()
        if sid:
            self.open_session_requested.emit(sid)

    def _emit_delete_current(self):
        sid = self.selected_session_id()
        if sid:
            self.delete_session_requested.emit(sid)