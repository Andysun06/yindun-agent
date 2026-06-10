# -*- coding: utf-8 -*-
# Yindun Security Agent V2.0 - MainWindow Framework (100% Component-Driven)
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QStackedLayout,
    QFrame, QLabel, QPushButton, QSizeGrip, QFileDialog,
    QInputDialog, QMessageBox
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QPoint, QRect
from PySide6.QtGui import QColor, QPalette, QFont, QCursor, QMouseEvent

# Core backend communication libraries
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage
from core.file_tools import list_local_files, create_local_file, delete_local_file

# 🌟 Cross-module architectural integration: Import all decoupled atomic bricks
from gui.styles import GLOBAL_QSS                                
from threads.agent_worker import Worker                          
from utils.document_parser import extract_file_text              
from gui.confirm_dialog import ConfirmDialog                     
from gui.settings_panel import SettingsPanel  
from gui.chat_display import ChatDisplay       # Imported display canvas brick
from gui.status_bar import AgentStatusBar     # Imported animation status bar brick
from gui.control_dock import ControlDock       # Imported dashboard control dock brick
from gui.session_selector import SessionSelectorPage

COLLAPSED_H = 52
EXPANDED_W, EXPANDED_H = 420, 640

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("隐盾 V2.0")
        self.setObjectName("mainWindow")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.resize(EXPANDED_W, EXPANDED_H)
        self._position_bottom_right()
        
        # Central business memory matrix
        self.messages = []
        self.llm = None
        self.tools_map = {}
        self.llm_ready = False
        self.is_busy = False
        self.attached_file = None
        
        # Borderless dragging and resizing parameters
        self._drag_pos = None
        self._resize_edge = None
        self._edge_px = 6
        self._resize_start_geo = QRect()
        self._resize_start_pos = QPoint()
        self._collapsed = False
        self._normal_h = EXPANDED_H
        
        # Global configuration properties
        self._settings = {
            "model": "qwen2.5:7b", "privacy": True, "permission": "完全控制 (读/写/列表)",
            "policy": "切换到敏感目录需提示", "opacity": 100, "topmost": True
        }

        self._sessions_file = Path(__file__).resolve().parents[1] / "chat_sessions.json"
        self._sessions = {}
        self._current_session_id = None
        self._load_sessions_store()
        
        # Assembly layout setup
        self._build_ui()
        self._apply_opacity()
        self._init_llm_async()

    def _position_bottom_right(self):
        s = QApplication.primaryScreen()
        if s: 
            g = s.availableGeometry()
            self.move(g.right() - self.width() - 14, g.bottom() - self.height() - 14)

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        
        self.container = QFrame()
        self.container.setObjectName("container")
        cl = QVBoxLayout(self.container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)

        # 1. Custom TitleBar segment
        self.title_bar = QFrame()
        self.title_bar.setObjectName("titleBar")
        self.title_bar.setFixedHeight(40)
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

        self._collapsible = QWidget()
        self._stack = QStackedLayout(self._collapsible)
        self._stack.setContentsMargins(0, 0, 0, 0)

        # 2. Session selector page (independent component)
        self.session_page = SessionSelectorPage()
        self.session_page.new_session_requested.connect(self._new_session)
        self.session_page.open_session_requested.connect(self._switch_to_session)
        self.session_page.delete_session_requested.connect(self._delete_session_by_id)

        self._stack.addWidget(self.session_page)
        self._session_index = 0

        # 3. 🌟 Assembly of the main chat page using isolated atomic bricks
        chat_page = QWidget()
        cp = QVBoxLayout(chat_page)
        cp.setContentsMargins(0, 0, 0, 0)
        cp.setSpacing(0)
        
        # Instantiate and stack the messaging board brick
        self.chat_display = ChatDisplay()
        cp.addWidget(self.chat_display, 1)
        
        # Instantiate and stack the animated status bar brick
        self.status_bar = AgentStatusBar()
        cp.addWidget(self.status_bar)
        
        # Instantiate and stack the input console brick
        self.control_dock = ControlDock()
        # Secure pipeline wiring: Connect child signals to local handler methods
        self.control_dock.send_triggered.connect(self._on_user_submit)
        self.control_dock.file_requested.connect(self._on_file_pick_request)
        cp.addWidget(self.control_dock)
        
        self._stack.addWidget(chat_page)
        self._chat_index = 1

        # 4. Integrated Settings Panel brick
        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_saved.connect(self._handle_settings_saved)
        self.settings_panel.cancel_clicked.connect(lambda: self._stack.setCurrentIndex(self._chat_index))
        self._stack.addWidget(self.settings_panel)
        self._settings_index = 2
        
        cl.addWidget(self._collapsible, 1)

        # Window resize drag node
        gr = QHBoxLayout()
        gr.setContentsMargins(0, 0, 4, 4)
        gr.addStretch()
        grip = QSizeGrip(self)
        grip.setFixedSize(14, 14)
        grip.setStyleSheet("QSizeGrip{background:transparent;}")
        gr.addWidget(grip)
        cl.addLayout(gr)
        
        outer.addWidget(self.container)
        
        # Trigger welcome note on launch
        self.chat_display.add_status_banner("🛡️ 隐盾解耦版全原子流式框架已成功挂载运行")
        self._refresh_session_list()
        self._stack.setCurrentIndex(self._session_index)

    def _on_user_submit(self, text):
        """Handler intercepting text stream emissions from ControlDock"""
        if not self._current_session_id:
            self._open_session_selector()
            return
        if not self.llm_ready or self.is_busy: return
        self.is_busy = True
        self.control_dock.clear_input_field()
        self.control_dock.toggle_busy_lock(True, "正在调度隐盾核心引擎...")
        
        display_text = f"📎 附件: {self.attached_file['name']}\n{text}" if self.attached_file else text
        self.chat_display.add_message_bubble("user", display_text, self.width())
        self._append_to_current_session("user", display_text)
        
        full_context = text
        if self.attached_file:
            f = self.attached_file
            full_context = f"[离线附件环境上下文：{f['name']}]\n{f['text']}\n\n[人类当前实时提问]：{text}"
            self.attached_file = None
            self.control_dock.update_file_button_text("📎 挂载文件")
            
        self._start_worker(full_context)

    def _on_file_pick_request(self):
        """Handler capturing file mounting interactions from ControlDock"""
        path, _ = QFileDialog.getOpenFileName(self, "挂载本地文件", "", "办公文件 (*.pdf *.docx *.xlsx *.txt *.md *.csv);;所有文件 (*)")
        if path:
            fname = os.path.basename(path)
            self.attached_file = {"name": fname, "text": extract_file_text(path)}
            self.control_dock.update_file_button_text(f"📎 {fname[:10]}...")
            self.chat_display.add_status_banner(f"🔒 离线机密附件就绪：{fname}")

    def _start_worker(self, user_input):
        """Deploy and ignite asynchronous inference worker thread loops"""
        self.status_bar.start_thinking("隐盾大脑研判中")
        self.worker = Worker()
        self.worker.user_input = user_input
        self.worker.history = self.messages.copy()
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
        self._append_to_current_session("assistant", r)
        self._cleanup_session()

    def _on_error_caught(self, e):
        self.status_bar.stop_thinking()
        self.chat_display.add_message_bubble("assistant", f"⚠️ 算力中断: {e}", self.width())
        self._cleanup_session()

    def _cleanup_session(self):
        self.is_busy = False
        self.control_dock.toggle_busy_lock(False)
        self.control_dock.force_input_focus()

    def _load_sessions_store(self):
        if not self._sessions_file.exists():
            return
        try:
            with self._sessions_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                return

            sessions = {}
            for item in payload.get("sessions", []):
                if not isinstance(item, dict):
                    continue
                sid = item.get("id")
                title = item.get("title")
                messages = item.get("messages", [])
                if not isinstance(sid, str) or not sid or not isinstance(title, str):
                    continue
                safe_messages = []
                for msg in messages if isinstance(messages, list) else []:
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role")
                    content = msg.get("content")
                    if role in {"user", "assistant"} and isinstance(content, str):
                        safe_messages.append({"role": role, "content": content})
                sessions[sid] = {
                    "id": sid,
                    "title": title,
                    "created_at": str(item.get("created_at", "")),
                    "updated_at": str(item.get("updated_at", "")),
                    "messages": safe_messages,
                }

            self._sessions = sessions
            sid = payload.get("current_session_id")
            self._current_session_id = sid if sid in self._sessions else None
        except Exception:
            self._sessions = {}
            self._current_session_id = None

    def _persist_sessions_store(self):
        try:
            payload = {
                "version": 1,
                "current_session_id": self._current_session_id,
                "sessions": list(self._sessions.values()),
            }
            with self._sessions_file.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _refresh_session_list(self):
        all_sessions = list(self._sessions.values())
        all_sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        self.session_page.render_sessions(all_sessions, self._current_session_id)

    def _new_session(self):
        title, ok = QInputDialog.getText(self, "新建对话", "请输入对话名称：")
        if not ok:
            return
        title = (title or "").strip() or f"新对话 {datetime.now().strftime('%m-%d %H:%M')}"
        now = datetime.now().isoformat(timespec="seconds")
        sid = uuid4().hex
        self._sessions[sid] = {
            "id": sid,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": [],
        }
        self._persist_sessions_store()
        self._refresh_session_list()
        self._switch_to_session(sid)

    def _open_selected_session(self):
        sid = self.session_page.selected_session_id()
        if sid in self._sessions:
            self._switch_to_session(sid)

    def _delete_selected_session(self):
        sid = self.session_page.selected_session_id()
        if sid is None:
            return
        self._delete_session_by_id(sid)

    def _delete_session_by_id(self, sid):
        if sid not in self._sessions:
            return
        title = self._sessions[sid].get("title", "未命名对话")
        reply = QMessageBox.question(self, "删除对话", f"确定删除对话“{title}”吗？\n该操作不可恢复。")
        if reply != QMessageBox.StandardButton.Yes:
            return
        del self._sessions[sid]
        if self._current_session_id == sid:
            self._current_session_id = None
            self.messages = []
            self.chat_display.clear_messages()
        self._persist_sessions_store()
        self._refresh_session_list()

    def _switch_to_session(self, sid):
        session = self._sessions.get(sid)
        if session is None:
            return
        self._current_session_id = sid
        self.messages = [(m["role"], m["content"]) for m in session.get("messages", [])]
        self.chat_display.clear_messages()
        for role, content in self.messages:
            self.chat_display.add_message_bubble(role, content, self.width())
        self._persist_sessions_store()
        self._refresh_session_list()
        self._stack.setCurrentIndex(self._chat_index)
        self.status_bar.set_static_text(f"当前对话：{session.get('title', '未命名对话')}")

    def _append_to_current_session(self, role, content):
        self.messages.append((role, content))
        sid = self._current_session_id
        if sid not in self._sessions:
            return
        session = self._sessions[sid]
        session.setdefault("messages", []).append({"role": role, "content": content})
        session["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self._persist_sessions_store()

    def _on_intercept_confirm(self, info):
        """Trigger pop-up auditing block dialog when tool path intrusion occurs"""
        self.status_bar.set_static_text("🚨 等待人工合规审批...")
        dlg = ConfirmDialog(info["name"], info["path"], self)
        s = QApplication.primaryScreen()
        if s: 
            dlg.move(s.availableGeometry().right() - 370, s.availableGeometry().bottom() - 230)
        dlg.confirmed.connect(lambda ok: self.worker.approve(ok))
        dlg.show()

    def _minimize(self): 
        self.showMinimized()

    def _toggle_collapse(self):
        if self._collapsed:
            self._collapsible.show()
            self._collapse_btn.setText("▸")
            QTimer.singleShot(10, self._do_expand)
            self._collapsed = False
        else:
            self._normal_h = self.height()
            self._collapsible.hide()
            self._collapse_btn.setText("▾")
            QTimer.singleShot(10, self._do_shrink)
            self._collapsed = True

    def _do_shrink(self):
        g = self.geometry()
        self.setGeometry(g.x(), g.y() + g.height() - COLLAPSED_H, g.width(), COLLAPSED_H)

    def _do_expand(self):
        g = self.geometry()
        self.setGeometry(g.x(), g.y() - (self._normal_h - g.height()), g.width(), self._normal_h)

    def _open_settings(self):
        self.settings_panel.load_settings_to_ui(self._settings)
        self._stack.setCurrentIndex(self._settings_index)

    def _open_session_selector(self):
        self._refresh_session_list()
        self._stack.setCurrentIndex(self._session_index)

    def _handle_settings_saved(self, new_settings):
        old_model = self._settings["model"]
        self._settings.update(new_settings)
        os.environ["PERMISSION_LEVEL"] = self._settings["permission"]
        self._apply_opacity()
        
        f = self.windowFlags()
        if self._settings["topmost"]: 
            self.setWindowFlags(f | Qt.WindowStaysOnTopHint)
        else: 
            self.setWindowFlags(f & ~Qt.WindowStaysOnTopHint)
        self.show()
        
        if self._settings["model"] != old_model:
            self.llm_ready = False
            self.control_dock.toggle_busy_lock(True, "正在热切换本地算力...")
            self._init_llm_async()
            
        self.chat_display.add_status_banner("⚙️ 全局安全隔离策略已同步完成物理更新")
        self._stack.setCurrentIndex(self._chat_index)

    def _apply_opacity(self): 
        self.setWindowOpacity(self._settings["opacity"] / 100.0)

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
            if pos.y() < 40:
                self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                event.accept()

    def mouseMoveEvent(self, event):
        if self._resize_edge and event.buttons() & Qt.LeftButton:
            self._do_resize(event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()
            return
        if not self._collapsed: 
            self._update_cursor(self._detect_edge(event.position().toPoint()))

    def mouseReleaseEvent(self, event): 
        self._drag_pos = None
        self._resize_edge = None

    def leaveEvent(self, event):
        if not self._resize_edge: self.setCursor(Qt.ArrowCursor)

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
        if not self._collapsed: self._normal_h = self.height()

    def _init_llm_async(self):
        self.status_bar.set_static_text("🔄 正在连接离线算力内核...")
        self.control_dock.toggle_busy_lock(True, "正在注入算力...")
        mn = self._settings["model"]
        
        def do_init():
            for i in range(3):
                try:
                    m = ChatOllama(model=mn, base_url="http://127.0.0.1:11434")
                    m.invoke("ping")
                    return m, {t.name: t for t in [list_local_files, create_local_file, delete_local_file]}, None
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
            return
        self.llm = llm
        self.tools_map = tm
        self.llm_ready = True
        self.control_dock.toggle_busy_lock(False)
        self.control_dock.force_input_focus()
        self.chat_display.add_status_banner(f"✅ 安全算力联通成功 [{self._settings['model']}]")