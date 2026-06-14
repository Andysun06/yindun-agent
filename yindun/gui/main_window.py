# -*- coding: utf-8 -*-
# Yindun Security Agent V3.1.4 - MainWindow Framework (Extreme Adaptive & Responsive Edition)
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from yindun.core.memory_manager import SummarizableChatHistory

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QStackedLayout,
    QFrame, QLabel, QPushButton, QSizeGrip, QFileDialog,
    QInputDialog, QMessageBox, QLineEdit
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QPoint, QRect
from PySide6.QtGui import QColor, QPalette, QFont, QCursor, QMouseEvent

# 核心后端多算力通信隔离舱
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage
from yindun.core.file_tools import list_local_files, create_local_file, delete_local_file

# 跨模块总线架构集成：动态引入所有的原子功能积木件
from yindun.gui.styles import GLOBAL_QSS, DARK_QSS                                
from yindun.worker.agent_worker import Worker                          
from yindun.utils.document_parser import extract_file_text              
from yindun.gui.confirm_dialog import ConfirmDialog                     
from yindun.gui.settings_panel import SettingsPanel  
from yindun.gui.chat_display import ChatDisplay       
from yindun.gui.status_bar import AgentStatusBar     
from yindun.gui.control_dock import ControlDock       
from yindun.gui.session_selector import SessionSelectorPage

COLLAPSED_H = 44  # 极致折叠挂件高度
EXPANDED_W, EXPANDED_H = 420, 640

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("隐盾 V3.1.4demo")
        self.setObjectName("mainWindow")
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 核心业务内存与状态锁阵列
        self.llm = None
        self.tools_map = {}
        self.llm_ready = False
        self.is_busy = False
        self.attached_file = None
        
        # 全局安全隔离配置树
        self._config_file = Path(__file__).resolve().parents[2] / "global_config.json"
        self._settings = {
            "model": "qwen2.5:7b", "privacy": True, "dark_mode": False, "topmost": True,
            "custom_models": {}
        }
        self._load_global_config()
        os.environ["PERMISSION_LEVEL"] = self._settings.get("permission", "完全控制 (读/写/列表)")

        # 根据配置设置窗口标志
        flags = Qt.FramelessWindowHint
        if self._settings.get("topmost", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.resize(EXPANDED_W, EXPANDED_H)
        self._position_bottom_right()
        
        # 开启主窗体的全局高级鼠标轨迹追踪，激活无边框自由拉伸机制
        self.setMouseTracking(True)
        
        # 无边框像素级物理拖拽缩放算力参数
        self._drag_pos = None
        self._resize_edge = None
        self._edge_px = 14
        self._resize_start_geo = QRect()
        self._resize_start_pos = QPoint()
        self._collapsed = False
        self._normal_w = EXPANDED_W
        self._normal_h = EXPANDED_H
        self._session_only = False
        self._saved_geo = None
        self._collapsed_w = EXPANDED_W  # 折叠态宽度记忆（本次会话内记忆，重启重置）

        self._sessions_file = Path(__file__).resolve().parents[2] / "chat_sessions.json"
        self._sessions = {}
        self._current_session_id = None
        self._load_sessions_store()
        
        # 前端原子积木装配火控流
        self._build_ui()
        self._apply_theme()
        self._init_llm_async()

    def _position_bottom_right(self):
        s = QApplication.primaryScreen()
        if s: 
            g = s.availableGeometry()
            self.move(g.right() - self.width() - 14, g.bottom() - self.height() - 14)

    def _load_global_config(self):
        if not self._config_file.exists(): return
        try:
            with self._config_file.open("r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    self._settings.update(saved)
        except: pass

    def _save_global_config(self):
        try:
            with self._config_file.open("w", encoding="utf-8") as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
        except: pass

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8) # 四周预留 8px 拉伸红线抓取区
        outer.setSpacing(0)
        
        self.container = QFrame()
        self.container.setObjectName("container")
        self.container.setMouseTracking(True)
        
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # 1. 顶部定制化无边框控制标题栏
        self.title_bar = QFrame()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(40)
        self.title_bar.setMouseTracking(True)
        tb = QHBoxLayout(self.title_bar)
        tb.setContentsMargins(14, 0, 10, 0)
        
        ico = QLabel("🛡️")
        ico.setObjectName("titleIcon")
        tb.addWidget(ico)
        tb.addSpacing(2)
        
        ttl = QLabel("隐盾安全智能体")
        ttl.setObjectName("titleText")
        tb.addWidget(ttl)
        tb.addStretch()
        
        for txt, nm, slot in [("💬", "titleBtn", self._open_session_selector), ("⚙", "titleBtn", self._open_settings), ("—", "titleBtn", self._minimize),
                              ("▸", "titleBtn", self._toggle_collapse), ("×", "closeBtn", self.close)]:
            b = QPushButton(txt)
            b.setObjectName(nm)
            b.setCursor(QCursor(Qt.PointingHandCursor))
            b.clicked.connect(slot)
            b.setFixedSize(24, 24)
            if txt == "▸": self._collapse_btn = b
            tb.addWidget(b)
        cl.addWidget(self.title_bar)

        # 2. 全极简闪发折叠控制舱
        self.mini_dock = QFrame()
        self.mini_dock.setObjectName("miniDock")
        self.mini_dock.setFixedHeight(44)
        self.mini_dock.setMouseTracking(True)
        self.mini_dock.setStyleSheet("""
            QFrame#miniDock { background: #ffffff; border: 1.5px solid #e0e3ea; border-radius: 18px; }
        """)
        md_layout = QHBoxLayout(self.mini_dock)
        md_layout.setContentsMargins(8, 2, 8, 2)
        md_layout.setSpacing(6)
        
        self.mini_expand_btn = QPushButton("展开 ↩")
        self.mini_expand_btn.setFixedSize(50, 26)
        self.mini_expand_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.mini_expand_btn.setStyleSheet("""
            QPushButton { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; color: #475569; font-size: 11px; font-weight: 600; }
            QPushButton:hover { background: #f1f5f9; border-color: #07c160; color: #07c160; }
        """)
        self.mini_expand_btn.clicked.connect(self._toggle_collapse)
        self.mini_expand_btn.hide()
        
        self.mini_input = QLineEdit()
        self.mini_input.setPlaceholderText("闪发模式：输入指令直接回车并自动展开...")
        self.mini_input.setStyleSheet("""
            QLineEdit { background: transparent; border: none; padding: 4px 4px; font-size: 12px; color: #222; }
        """)
        self.mini_input.returnPressed.connect(self._handle_mini_submit)
        
        self.mini_send_btn = QPushButton("➤")
        self.mini_send_btn.setStyleSheet("""
            QPushButton { background: #07c160; color: white; border: none; border-radius: 14px; font-size: 11px; font-weight: bold; min-width: 28px; max-width: 28px; min-height: 28px; max-height: 28px; }
            QPushButton:hover { background: #06ad56; }
        """)
        self.mini_send_btn.clicked.connect(self._handle_mini_submit)
        
        md_layout.addWidget(self.mini_expand_btn)
        md_layout.addWidget(self.mini_input, 1)
        md_layout.addWidget(self.mini_send_btn)
        cl.addWidget(self.mini_dock)
        self.mini_dock.setVisible(False)

        # 核心弹性展开总画布
        self._collapsible = QWidget()
        self._collapsible.setMouseTracking(True)
        self._stack = QStackedLayout(self._collapsible)
        self._stack.setContentsMargins(0, 0, 0, 0)

        # 3. 建立自适应多轨画布 (Workspace Page)
        self.workspace_page = QWidget()
        self.workspace_page.setMouseTracking(True)
        self.workspace_layout = QHBoxLayout(self.workspace_page)
        self.workspace_layout.setContentsMargins(0, 0, 0, 0)
        self.workspace_layout.setSpacing(0)
        
        self.session_page = SessionSelectorPage()
        self.session_page.setMinimumWidth(160)
        self.session_page.setMaximumWidth(260)
        self.session_page.new_session_requested.connect(self._new_session)
        self.session_page.open_session_requested.connect(self._switch_to_session)
        self.session_page.delete_session_requested.connect(self._delete_session_by_id)
        self.workspace_layout.addWidget(self.session_page)
        
        self.chat_container = QWidget()
        self.chat_container.setMouseTracking(True)
        cc_layout = QVBoxLayout(self.chat_container)
        cc_layout.setContentsMargins(0, 0, 0, 0)
        cc_layout.setSpacing(0)
        
        self.chat_display = ChatDisplay()
        self.status_bar = AgentStatusBar()
        self.control_dock = ControlDock()
        self.control_dock.send_triggered.connect(self._on_user_submit)
        self.control_dock.file_requested.connect(self._on_file_pick_request)
        
        cc_layout.addWidget(self.chat_display, 1)
        cc_layout.addWidget(self.status_bar)
        cc_layout.addWidget(self.control_dock)
        self.workspace_layout.addWidget(self.chat_container)
        
        self._stack.addWidget(self.workspace_page)
        self._workspace_index = 0

        # 4. 独立的高级安全参数配置面板舱
        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_saved.connect(self._handle_settings_saved)
        self.settings_panel.cancel_clicked.connect(lambda: self._stack.setCurrentIndex(self._workspace_index))
        self._stack.addWidget(self.settings_panel)
        self._settings_index = 1
        
        cl.addWidget(self._collapsible, 1)

        # 底置无边界物理拉伸抓手
        gr = QHBoxLayout()
        gr.setContentsMargins(0, 0, 4, 4)
        gr.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("QSizeGrip{background:transparent;}")
        gr.addWidget(grip)
        cl.addLayout(gr)
        
        outer.addWidget(self.container)
        
        self.chat_display.add_status_banner("隐盾 V3.1.4demo - 请选择或创建对话")
        self._refresh_session_list()
        
        if self._current_session_id:
            self._update_responsive_layout()
        else:
            self._show_session_only()
        self._stack.setCurrentIndex(self._workspace_index)

        # 为关键子控件安装事件过滤器（确保边缘鼠标事件可达主窗口，修复边缘拖拽拉伸功能）
        for w in [self.container, self.title_bar, self.mini_dock, self.chat_container,
                  self.chat_display, self.status_bar, self.control_dock, self.session_page]:
            if w is not None:
                w.installEventFilter(self)
                w.setMouseTracking(True)

    def _update_responsive_layout(self):
        """响应式分流：拉宽突破 600px 浮现双轨工作台"""
        if self._collapsed or self._session_only:
            return
        if self.width() >= 600:
            self.session_page.setVisible(True)
            self.chat_container.setVisible(True)
        else:
            self.session_page.setVisible(False)
            self.chat_container.setVisible(True)

    def _handle_mini_submit(self):
        text = self.mini_input.text().strip()
        if text:
            if self._collapsed:
                self._toggle_collapse()
            self._on_user_submit(text)
            self.mini_input.clear()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._collapsed:
            self.mini_expand_btn.show()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._collapsed:
            self.mini_expand_btn.hide()
        if not self._resize_edge:
            self.setCursor(Qt.ArrowCursor)

    def _toggle_collapse(self):
        if self._collapsed:
            self.mini_dock.hide()
            self.title_bar.show()
            self._collapsible.show()
            self._collapsed = False
            self._apply_theme()
            self._collapse_btn.setText("▸")
            # 恢复折叠前保存的完整几何（位置 + 大小）
            if self._saved_geo is not None:
                self.setGeometry(self._saved_geo)
            QTimer.singleShot(20, self._update_responsive_layout)
        else:
            # 保存完整几何，用于展开时精确还原
            self._saved_geo = self.geometry()
            self._normal_w = self.width()
            self._normal_h = self.height()
            self.title_bar.hide()
            self._collapsible.hide()
            self.mini_dock.show()
            self.container.setStyleSheet("QFrame#container { background: transparent; border: none; }")
            self._collapse_btn.setText("▾")
            QTimer.singleShot(10, self._do_shrink)
            self._collapsed = True
            self.mini_expand_btn.hide()

    def _do_shrink(self):
        g = self.geometry()
        total_collapsed_h = COLLAPSED_H + 16
        # 折叠态宽度使用记忆值（本次会话内保持，重启重置）
        collapsed_w = max(280, self._collapsed_w)
        # 保持底部对齐（以让折叠后的窗口与原窗口底部对齐）
        new_x = g.x() + (g.width() - collapsed_w) // 2
        new_y = g.y() + g.height() - total_collapsed_h
        self.setGeometry(new_x, new_y, collapsed_w, total_collapsed_h)

    def _show_session_only(self):
        self._session_only = True
        self.session_page.setVisible(True)
        self.session_page.setMaximumWidth(9999)
        self.chat_container.setVisible(False)

    def _exit_session_only(self):
        self._session_only = False
        self.session_page.setMaximumWidth(260)
        self.chat_container.setVisible(True)
        self._update_responsive_layout()

    def _apply_theme(self):
        dark = self._settings.get("dark_mode", False)
        app = QApplication.instance()
        if app:
            app.setStyleSheet(DARK_QSS if dark else GLOBAL_QSS)
        bg = "#1a1a2e" if dark else "#f5f6f8"
        self.workspace_page.setStyleSheet(f"background: {bg};")
        self.chat_display.set_dark_mode(dark)
        self.control_dock.set_dark_mode(dark)
        self.session_page.set_dark_mode(dark)
        if not self._collapsed:
            container_bg = "#1e1e2e" if dark else "white"
            container_border = "rgba(255,255,255,12)" if dark else "rgba(0,0,0,18)"
            self.container.setStyleSheet(
                f"QFrame#container {{ background: {container_bg}; border: 1px solid {container_border}; border-radius: 22px; }}"
            )

    def _on_user_submit(self, text):
        if not self._current_session_id or self._session_only: return
        if not self.llm_ready or self.is_busy: return
        self.is_busy = True
        self.control_dock.clear_input_field()
        self.control_dock.toggle_busy_lock(True, "正在调度隐盾核心引擎...")
        
        display_text = f"📎 附件: {self.attached_file['name']}\n{text}" if self.attached_file else text
        self.chat_display.add_message_bubble("user", display_text, self.width())
        
        full_context = text
        if self.attached_file:
            f = self.attached_file
            full_context = f"[离线附件环境上下文：{f['name']}]\n{f['text']}\n\n[人类当前实时提问]：{text}"
            self.attached_file = None
            self.control_dock.update_file_button_text("📎 挂载文件")
            
        self._start_worker(full_context)

    def _on_file_pick_request(self):
        path, _ = QFileDialog.getOpenFileName(self, "挂载本地文件", "", "办公文件 (*.pdf *.docx *.xlsx *.txt *.md *.csv);;所有文件 (*)")
        if path:
            fname = os.path.basename(path)
            self.attached_file = {"name": fname, "text": extract_file_text(path)}
            self.control_dock.update_file_button_text(f"📎 {fname[:10]}...")
            self.chat_display.add_status_banner(f"🔒 离线机密附件就绪：{fname}")

    def _start_worker(self, user_input):
        self.status_bar.start_thinking("隐盾大脑研判中")
        self.worker = Worker()
        self.worker.user_input = user_input
        sid = self._current_session_id
        self.worker.messages_snapshot = list(self._sessions.get(sid, {}).get("messages", []))
        self.worker.think_mode = self.control_dock.get_current_mode()
        self.worker.privacy_shield = self._settings["privacy"]
        self.worker.llm = self.llm
        self.worker.tools_map = self.tools_map
        self.worker.sandbox_path = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
        
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        
        self.worker.finished.connect(self._on_reply_received)
        self.worker.error.connect(self._on_error_caught)
        self.worker.status.connect(self.status_bar.set_static_text)
        self.worker.need_confirm.connect(self._on_intercept_confirm)
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.start()

    def _on_reply_received(self, r):
        self.status_bar.stop_thinking()
        self.chat_display.add_message_bubble("assistant", r, self.width())
        self._apply_worker_messages()
        self._cleanup_session()

    def _on_error_caught(self, e):
        self.status_bar.stop_thinking()
        self.chat_display.add_message_bubble("assistant", f"⚠️ 算力中断: {e}", self.width())
        self._apply_worker_messages()
        self._cleanup_session()

    def _apply_worker_messages(self):
        if not hasattr(self.worker, 'result_messages') or not self.worker.result_messages: return
        sid = self._current_session_id
        if sid not in self._sessions: return
        self._sessions[sid]["messages"] = self.worker.result_messages
        self._sessions[sid]["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._persist_sessions_store()

    def _cleanup_session(self):
        self.is_busy = False
        self.control_dock.toggle_busy_lock(False)
        self.control_dock.force_input_focus()

    def _load_sessions_store(self):
        if not self._sessions_file.exists(): return
        try:
            with self._sessions_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict): return
            sessions = {}
            for item in payload.get("sessions", []):
                if not isinstance(item, dict): continue
                sid, title, messages = item.get("id"), item.get("title"), item.get("messages", [])
                if not sid or not isinstance(title, str): continue
                safe_messages = []
                for msg in messages if isinstance(messages, list) else []:
                    if isinstance(msg, dict) and msg.get("role") in {"user", "assistant", "system"} and isinstance(msg.get("content"), str):
                        safe_messages.append({"role": msg["role"], "content": msg["content"]})
                sessions[sid] = {"id": sid, "title": title, "created_at": str(item.get("created_at", "")), "updated_at": str(item.get("updated_at", "")), "messages": safe_messages}
            self._sessions = sessions
            sid = payload.get("current_session_id")
            self._current_session_id = sid if sid in self._sessions else None
        except: self._sessions, self._current_session_id = {}, None

    def _persist_sessions_store(self):
        try:
            payload = {"version": 1, "current_session_id": self._current_session_id, "sessions": list(self._sessions.values())}
            with self._sessions_file.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except: pass

    def _refresh_session_list(self):
        all_sessions = list(self._sessions.values())
        all_sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        self.session_page.render_sessions(all_sessions, self._current_session_id)

    def _new_session(self):
        title, ok = QInputDialog.getText(self, "新建对话", "请输入对话名称：")
        if not ok: return
        title = (title or "").strip() or f"新对话 {datetime.now().strftime('%m-%d %H:%M')}"
        now = datetime.now().isoformat(timespec="seconds")
        sid = uuid4().hex
        self._sessions[sid] = {"id": sid, "title": title, "created_at": now, "updated_at": now, "messages": []}
        self._persist_sessions_store()
        self._refresh_session_list()
        self._switch_to_session(sid)

    def _delete_session_by_id(self, sid):
        if sid not in self._sessions: return
        title = self._sessions[sid].get("title", "未命名对话")
        reply = QMessageBox.question(self, "删除对话", f"确定删除对话“{title}”吗？\n该操作不可恢复。")
        if reply != QMessageBox.StandardButton.Yes: return
        del self._sessions[sid]
        if self._current_session_id == sid:
            self._current_session_id = None
            self.chat_display.clear_messages()
            self._show_session_only()
        self._persist_sessions_store()
        self._refresh_session_list()

    def _switch_to_session(self, sid):
        session = self._sessions.get(sid)
        if session is None: return
        self._current_session_id = sid
        self.chat_display.clear_messages()
        for m in session.get("messages", []):
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                if content.startswith("[SUMMARY]"):
                    summary_text = content[len("[SUMMARY]"):]
                    self.chat_display.add_status_banner(f"历史摘要: {summary_text[:80]}…")
                continue
            if role in ("user", "assistant"):
                self.chat_display.add_message_bubble(role, content, self.width())
        self._persist_sessions_store()
        self._refresh_session_list()
        
        self._session_only = False
        self.session_page.setMaximumWidth(260)
        self.chat_container.setVisible(True)
        self._update_responsive_layout()
        self.status_bar.set_static_text(f"当前对话：{session.get('title', '未命名对话')}")

    def _append_to_current_session(self, role, content):
        sid = self._current_session_id
        if sid not in self._sessions: return
        session = self._sessions[sid]
        session.setdefault("messages", []).append({"role": role, "content": content})
        session["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._persist_sessions_store()

    def _on_intercept_confirm(self, info):
        self.status_bar.set_static_text("🚨 等待人工合规审批...")
        dlg = ConfirmDialog(info["name"], info["path"], self)
        s = QApplication.primaryScreen()
        if s: dlg.move(s.availableGeometry().right() - 370, s.availableGeometry().bottom() - 230)
        dlg.confirmed.connect(lambda ok: self.worker.approve(ok))
        dlg.show()

    def _minimize(self): self.showMinimized()

    def _open_settings(self):
        # 从会话选择模式切到设置时, 先恢复正常双栏布局, 避免设置页显示不完整
        if self._session_only:
            self._exit_session_only()
        self.settings_panel.load_settings_to_ui(self._settings)
        self._stack.setCurrentIndex(self._settings_index)

    def _open_session_selector(self):
        # ⚠️ 关键修复: 无论当前在哪个 stack 页, 都要先切回 workspace,
        # 否则在设置页点 💬 时, 会话列表会被设置页挡住, 表现为"无响应"
        self._stack.setCurrentIndex(self._workspace_index)
        self._refresh_session_list()
        if not self._session_only:
            self._show_session_only()

    def _handle_settings_saved(self, new_settings):
        """🌟 核心优化：即时数据驱动。
        ⚠️ 重要：所有会重建原生窗口的操作 (setWindowFlags / 全局 setStyleSheet)
        必须延迟到下一个事件循环执行，否则会打断当前 QComboBox 下拉框的事件链，
        导致后续下拉无响应。"""
        old_model = self._settings["model"]
        old_dark = self._settings.get("dark_mode", False)
        old_topmost = self._settings.get("topmost", True)
        self._settings.update(new_settings)
        self._save_global_config()

        # 把"重型操作"全部延后到下一个事件循环, 让本次控件事件自然结束
        def _apply_heavy_updates():
            if self._settings.get("dark_mode", False) != old_dark:
                self._apply_theme()
            # 仅在置顶状态真正翻转时才改 windowFlags, 避免无谓的原生窗口重建
            if self._settings.get("topmost", True) != old_topmost:
                f = self.windowFlags()
                if self._settings["topmost"]:
                    self.setWindowFlags(f | Qt.WindowStaysOnTopHint)
                else:
                    self.setWindowFlags(f & ~Qt.WindowStaysOnTopHint)
                self.show()

        QTimer.singleShot(0, _apply_heavy_updates)

        if self._settings["model"] != old_model:
            self.llm_ready = False
            self.control_dock.toggle_busy_lock(True, "正在热切换本地算力...")
            self._init_llm_async()

        self.chat_display.add_status_banner("配置已同步完成")
        # ❌ 彻底摘除原本的强制跳转：self._stack.setCurrentIndex(self._workspace_index)

    def _detect_edge(self, pos):
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()
        e = self._edge_px
        l, r, t, b = x < e, x > w - e, y < e, y > h - e
        if not (l or r or t or b): return None
        if t and l: return "top-left"
        if t and r: return "top-right"
        if b and l: return "bottom-left"
        if b and r: return "bottom-right"
        if t: return "top"
        if b: return "bottom"
        if l: return "left"
        if r: return "right"
        return None

    def _update_cursor(self, edge):
        c = {
            "top": Qt.SizeVerCursor, "bottom": Qt.SizeVerCursor, "left": Qt.SizeHorCursor, "right": Qt.SizeHorCursor,
            "top-left": Qt.SizeFDiagCursor, "bottom-right": Qt.SizeFDiagCursor, "top-right": Qt.SizeBDiagCursor, "bottom-left": Qt.SizeBDiagCursor
        }
        self.setCursor(c.get(edge, Qt.ArrowCursor))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint()
            edge = self._detect_edge(pos)
            if edge and not self._collapsed:
                self._resize_edge = edge
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
                event.accept()
                return
            if pos.y() >= 8 and pos.y() < 48 and pos.x() >= 8 and pos.x() < self.width() - 8:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            if not self._collapsed:
                edge = self._detect_edge(event.position().toPoint())
                self._update_cursor(edge)
            event.accept()
            return
        if self._resize_edge and event.buttons() & Qt.LeftButton:
            self._do_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return

    def mouseReleaseEvent(self, event): self._drag_pos, self._resize_edge = None, None

    def eventFilter(self, obj, event):
        """🌟 事件过滤器。统一处理展开态 / 折叠态下的边缘缩放和窗口拖动。
        折叠态：只允许左右边缘调整宽度，不允许上下调整高度；mini_dock 任意区域可拖动。
        展开态：四边+四角完整缩放；title_bar 区域可拖动。
        """
        et = event.type()

        # ── 左键按下：检测边缘 → 进入 resize 模式；或检测拖动区域 → 进入 drag 模式 ──
        if et == event.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            local_pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edge = self._detect_edge(local_pos)

            # 折叠态下：只允许 left/right 边缘（宽度调整），不允许 top/bottom
            if self._collapsed:
                if edge and edge in ("left", "right", "top-left", "top-right",
                                      "bottom-left", "bottom-right"):
                    # 折叠态 resize 只调整宽度（高度锁定）
                    self._resize_edge = edge
                    self._resize_start_pos = event.globalPosition().toPoint()
                    self._resize_start_geo = self.geometry()
                    self._collapsed_resize = True
                    return True
                # 折叠态：mini_dock 任意区域可拖动窗口
                if obj in (self.mini_dock, self.container):
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    return True
            else:
                # 展开态：完整边缘缩放
                if edge:
                    self._resize_edge = edge
                    self._resize_start_pos = event.globalPosition().toPoint()
                    self._resize_start_geo = self.geometry()
                    self._collapsed_resize = False
                    return True
                # 展开态：标题栏拖动（避开边缘区）
                if (local_pos.y() >= self._edge_px and local_pos.y() < 48
                        and local_pos.x() >= self._edge_px and local_pos.x() < self.width() - self._edge_px
                        and obj == self.title_bar):
                    self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                    return True

        # ── 鼠标移动：边缘悬停更新光标 / 拖拽时执行 resize 或 drag ──
        elif et == event.Type.MouseMove:
            # 正在 resize
            if self._resize_edge and event.buttons() & Qt.LeftButton:
                if getattr(self, '_collapsed_resize', False):
                    self._do_collapsed_resize(event.globalPosition().toPoint())
                else:
                    self._do_resize(event.globalPosition().toPoint())
                return True
            # 正在拖动窗口
            if self._drag_pos and event.buttons() & Qt.LeftButton:
                self.move(event.globalPosition().toPoint() - self._drag_pos)
                return True
            # 悬停时更新光标（折叠态只处理左右边缘）
            local_pos = self.mapFromGlobal(event.globalPosition().toPoint())
            edge = self._detect_edge(local_pos)
            if self._collapsed:
                if edge in ("left", "right", "top-left", "top-right",
                            "bottom-left", "bottom-right"):
                    self._update_cursor(edge)
                else:
                    self.unsetCursor()
            else:
                self._update_cursor(edge)

        # ── 左键释放：结束 resize / 拖动 ──
        elif et == event.Type.MouseButtonRelease and event.button() == Qt.LeftButton:
            if self._resize_edge or self._drag_pos:
                self._drag_pos = None
                self._resize_edge = None
                self._collapsed_resize = False
                self.unsetCursor()
                return True

        return super().eventFilter(obj, event)

    def _do_collapsed_resize(self, gpos):
        """折叠态下的 resize：只调整宽度（左右边缘），高度保持不变；同时记忆宽度。"""
        d = gpos - self._resize_start_pos
        g = self._resize_start_geo
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        e = self._resize_edge
        min_w = 280

        if "right" in e:
            w = max(min_w, g.width() + d.x())
        if "left" in e:
            nw = max(min_w, g.width() - d.x())
            x += g.width() - nw
            w = nw
        # 忽略 top/bottom 方向的调整（折叠态高度锁定）
        self.setGeometry(x, y, w, h)
        # 记忆本次会话的折叠态宽度（下次折叠时使用；重启重置）
        self._collapsed_w = w

    def _do_resize(self, gpos):
        d = gpos - self._resize_start_pos
        g = self._resize_start_geo
        x, y, w, h = g.x(), g.y(), g.width(), g.height()
        e = self._resize_edge
        if "right" in e: w = max(340, g.width() + d.x())
        if "bottom" in e: h = max(200, g.height() + d.y())
        if "left" in e: 
            nw = max(340, g.width() - d.x())
            x += g.width() - nw
            w = nw
        if "top" in e: 
            nh = max(200, g.height() - d.y())
            y += g.height() - nh
            h = nh
        self.setGeometry(x, y, w, h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._collapsed: 
            self._normal_w = self.width()
            self._normal_h = self.height()
            self._update_responsive_layout()

    def _init_llm_async(self):
        self.status_bar.set_static_text("🔄 正在连接离线算力内核...")
        self.control_dock.toggle_busy_lock(True, "正在注入算力...")
        mn = self._settings["model"]
        custom_models = self._settings.get("custom_models", {})
        
        tools_list = [list_local_files, create_local_file, delete_local_file]
        
        def do_init():
            for i in range(3):
                try:
                    if mn in custom_models:
                        from langchain_openai import ChatOpenAI
                        c_info = custom_models[mn]
                        base_model = ChatOpenAI(model=c_info["model_id"], openai_api_base=c_info["base_url"], openai_api_key=c_info["api_key"])
                    else:
                        base_model = ChatOllama(model=mn, base_url="http://127.0.0.1:11434")
                    m_bound = base_model.bind_tools(tools_list)
                    try: m_bound.invoke("hi")
                    except: pass
                    return m_bound, {t.name: t for t in tools_list}, None
                except:
                    if i < 2: time.sleep(1)
                    else: return None, {}, traceback.format_exc()
                    
        class IW(QObject):
            done = Signal(object, object, object)
            def __init__(s, fn): super().__init__(); s.fn = fn
            def run(s): s.done.emit(*s.fn())
            
        self._it = QThread()
        self._iw = IW(do_init)
        self._iw.moveToThread(self._it)
        self._it.started.connect(self._iw.run)
        self._iw.done.connect(self._on_llm_ready)
        self._iw.done.connect(self._it.quit)
        self._it.start()

    def _on_llm_ready(self, llm, tm, err):
        self.status_bar.stop_thinking()
        if err:
            self.chat_display.add_status_banner("❌ 算力集群离线，请开启 Ollama 后台进程并拉起模型")
            self.control_dock.update_placeholder_text("算力内核离线")
            self.control_dock.toggle_busy_lock(False)
            return
        self.llm = llm
        self.tools_map = tm
        self.llm_ready = True
        self.control_dock.toggle_busy_lock(False)
        self.control_dock.force_input_focus()
        self.chat_display.add_status_banner(f"✅ 安全算力联通成功 [{self._settings['model']}]")