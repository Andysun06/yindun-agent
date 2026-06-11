# -*- coding: utf-8 -*-
# Yindun Security Agent V2.1.0 - MainWindow Framework (Extreme Adaptive & Responsive Edition)
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
from yindun.gui.styles import GLOBAL_QSS                                
from yindun.worker.agent_worker import Worker                          
from yindun.utils.document_parser import extract_file_text              
from yindun.gui.confirm_dialog import ConfirmDialog                     
from yindun.gui.settings_panel import SettingsPanel  
from yindun.gui.chat_display import ChatDisplay       
from yindun.gui.status_bar import AgentStatusBar     
from yindun.gui.control_dock import ControlDock       
from yindun.gui.session_selector import SessionSelectorPage

COLLAPSED_H = 46  # 🌟 优化：极致折叠挂件高度，刚好容纳一根微型闪发控制条
EXPANDED_W, EXPANDED_H = 420, 640

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("隐盾 V2.1.0")
        self.setObjectName("mainWindow")
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.resize(EXPANDED_W, EXPANDED_H)
        self._position_bottom_right()
        
        # 🌟 核心修复：开启主窗体的全局高级鼠标轨迹追踪，激活无边框自由拉伸机制
        self.setMouseTracking(True)
        
        # 核心业务内存与状态锁阵列
        self.llm = None
        self.tools_map = {}
        self.llm_ready = False
        self.is_busy = False
        self.attached_file = None
        
        # 无边框像素级物理拖拽缩放算力参数
        self._drag_pos = None
        self._resize_edge = None
        self._edge_px = 8  # 提高到 8 像素边缘触发红线，让鼠标更容易抓取
        self._resize_start_geo = QRect()
        self._resize_start_pos = QPoint()
        self._collapsed = False
        self._normal_h = EXPANDED_H
        
        # 全局安全隔离配置树
        self._config_file = Path(__file__).resolve().parents[2] / "global_config.json"
        self._settings = {
            "model": "qwen2.5:7b", "privacy": True, "permission": "完全控制 (读/写/列表)",
            "policy": "切换到敏感目录需提示", "opacity": 100, "topmost": True,
            "custom_models": {}
        }
        self._load_global_config() 

        self._sessions_file = Path(__file__).resolve().parents[2] / "chat_sessions.json"
        self._sessions = {}
        self._current_session_id = None
        self._load_sessions_store()
        
        # 前端原子积木装配火控流
        self._build_ui()
        self._apply_opacity()
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
                    os.environ["PERMISSION_LEVEL"] = self._settings["permission"]
        except: pass

    def _save_global_config(self):
        try:
            with self._config_file.open("w", encoding="utf-8") as f:
                json.dump(self._settings, f, ensure_ascii=False, indent=2)
        except: pass

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        
        self.container = QFrame()
        self.container.setObjectName("container")
        # 🌟 核心修复：外壳容器同步强推鼠标追踪，杜绝子控件拦截拉伸信号
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

        # 🌟 2. 核心重构：全新极简闪发折叠控制舱 (折叠模式下独立渲染，外层完全全透明)
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
        
        # 🌟 新增：独立高级白色展开按钮，具备完美的鼠标移入浮现、移出静默穿透特性
        self.mini_expand_btn = QPushButton("展开 ↩")
        self.mini_expand_btn.setFixedSize(50, 26)
        self.mini_expand_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.mini_expand_btn.setStyleSheet("""
            QPushButton { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 10px; color: #475569; font-size: 11px; font-weight: 600; }
            QPushButton:hover { background: #f1f5f9; border-color: #07c160; color: #07c160; }
        """)
        self.mini_expand_btn.clicked.connect(self._toggle_collapse)
        self.mini_expand_btn.hide() # 初始默认隐藏透明
        
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
        self.mini_dock.setVisible(False) # 初始未折叠时隐藏

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
        
        # 挂载原子砖块一：会话切换页 (默认不强制抢占，由自适应分流控制)
        self.session_page = SessionSelectorPage()
        self.session_page.setMinimumWidth(160)
        self.session_page.setMaximumWidth(260)
        self.session_page.new_session_requested.connect(self._new_session)
        self.session_page.open_session_requested.connect(self._switch_to_session)
        self.session_page.delete_session_requested.connect(self._delete_session_by_id)
        self.workspace_layout.addWidget(self.session_page)
        
        # 挂载原子砖块二：独立右侧对话综合体
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
        
        self.chat_display.add_status_banner("🛡️ 隐盾解耦版自适应双轨视舱配置完成")
        self._refresh_session_list()
        
        # 🌟 核心修复：初始拼装完成后，强制调用一次自适应布局分流，解决一打开就紧凑两列的Bug
        self._update_responsive_layout()
        self._stack.setCurrentIndex(self._workspace_index)

    def _update_responsive_layout(self):
        """🌟 响应式动态路由算法：全自动监控拉伸宽度，划分左侧列表与右侧对话显隐"""
        if self._collapsed: return
        
        # 自由横向拉宽突破 450px 阈值，双轨同时浮现，形成工作台侧边栏布局
        if self.width() >= 450:
            self.session_page.setVisible(True)
            self.chat_container.setVisible(True)
        else:
            # 小于 450px 窄屏模式下，强制关闭左侧列表，保证大厅呼吸空间
            self.session_page.setVisible(False)
            self.chat_container.setVisible(True)

    def _handle_mini_submit(self):
        """🌟 闪发互锁逻辑：迷你框回车触发时，应用瞬间打破折叠回归正常大厅，并投递提问"""
        text = self.mini_input.text().strip()
        if text:
            if self._collapsed:
                self._toggle_collapse() # 自动弹回展开状态
            self._on_user_submit(text)
            self.mini_input.clear()

    def enterEvent(self, event):
        """🌟 悬浮感知：鼠标指针移入软件空间内，如果处于折叠模式，浮现白色展开按钮"""
        super().enterEvent(event)
        if self._collapsed:
            self.mini_expand_btn.show()

    def leaveEvent(self, event):
        """🌟 悬浮感知：鼠标指针离开软件物理范围，白色展开按钮瞬间隐形，恢复透明穿透"""
        super().leaveEvent(event)
        if self._collapsed:
            self.mini_expand_btn.hide()
        if not self._resize_edge:
            self.setCursor(Qt.ArrowCursor)

    def _toggle_collapse(self):
        """🌟 极致折叠策略重构：在展开/极致全透闪发框之间互锁切换"""
        if self._collapsed:
            # 准备解冻回归正常大视舱
            self.mini_dock.hide()
            self.title_bar.show()
            self._collapsible.show()
            # 恢复外部大卡片的白底圆角工业样式
            self.container.setStyleSheet("QFrame#container { background: white; border: 1px solid rgba(0,0,0,18); border-radius: 22px; }")
            self._collapse_btn.setText("▸")
            QTimer.singleShot(10, self._do_expand)
            self._collapsed = False
        else:
            # 物理冻结，切入闪发微型挂件模式
            self._normal_h = self.height()
            self.title_bar.hide()
            self._collapsible.hide()
            self.mini_dock.show()
            # 🌟 核心升级：强制将大容器背景及边框全部“全透明化”，抹除输入条以外的所有痕迹
            self.container.setStyleSheet("QFrame#container { background: transparent; border: none; }")
            self._collapse_btn.setText("▾")
            QTimer.singleShot(10, self._do_shrink)
            self._collapsed = True
            self.mini_expand_btn.hide() # 初始置为安全隐藏

    def _do_shrink(self):
        g = self.geometry()
        self.setGeometry(g.x(), g.y() + g.height() - COLLAPSED_H, g.width(), COLLAPSED_H)

    def _do_expand(self):
        g = self.geometry()
        self.setGeometry(g.x(), g.y() - (self._normal_h - g.height()), g.width(), self._normal_h)
        # 展平后 20ms 微小缓冲后重算自适应宽度，确保多轨列表恢复精确
        QTimer.singleShot(20, self._update_responsive_layout)

    def _on_user_submit(self, text):
        if not self._current_session_id:
            self._open_session_selector()
            return
        if not self.llm_ready or self.is_busy: return
        self.is_busy = True
        self.control_dock.clear_input_field()
        self.control_dock.toggle_busy_lock(True, "正在调度隐盾核心引擎...")
        
        display_text = f"📎 附件: {self.attached_file['name']}\n{text}" if self.attached_file else text
        self.chat_display.add_message_bubble("user", display_text, self.width())
        # ⭐ 不在主线程写入会话，交由 Worker 统一管理本轮对话的记忆写入
        
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
        # ⭐ 传递当前会话的消息快照（dict list），替代旧的 self.messages.copy()
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
        # ⭐ 从 Worker 读取最新的消息列表（含摘要标记），写入当前会话
        self._apply_worker_messages()
        self._cleanup_session()

    def _on_error_caught(self, e):
        self.status_bar.stop_thinking()
        self.chat_display.add_message_bubble("assistant", f"⚠️ 算力中断: {e}", self.width())
        self._apply_worker_messages()
        self._cleanup_session()

    def _apply_worker_messages(self):
        """⭐ 将 Worker 处理后的消息列表同步回当前会话存储"""
        if not hasattr(self.worker, 'result_messages') or not self.worker.result_messages:
            return
        sid = self._current_session_id
        if sid not in self._sessions:
            return
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
                    if isinstance(msg, dict) and msg.get("role") in {"user", "assistant"} and isinstance(msg.get("content"), str):
                        safe_messages.append({"role": msg["role"], "content": msg["content"]})
                sessions[sid] = {"id": sid, "title": title, "created_at": str(item.get("created_at", "")), "updated_at": str(item.get("updated_at", "")), "messages": safe_messages}
            self._sessions = sessions
            sid = payload.get("current_session_id")
            self._current_session_id = sid if sid in self._sessions else None
        except:
            self._sessions, self._current_session_id = {}, None

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
        self._persist_sessions_store()
        self._refresh_session_list()

    def _switch_to_session(self, sid):
        session = self._sessions.get(sid)
        if session is None: return
        self._current_session_id = sid
        self.chat_display.clear_messages()
        # ⭐ 从会话消息列表渲染气泡（跳过 system 摘要标记消息）
        for m in session.get("messages", []):
            role = m.get("role", "")
            content = m.get("content", "")
            if role == "system":
                # 摘要消息以状态条方式展示
                if content.startswith("[SUMMARY]"):
                    summary_text = content[len("[SUMMARY]"):]
                    self.chat_display.add_status_banner(f"📝 历史摘要: {summary_text[:80]}…")
                continue
            if role in ("user", "assistant"):
                self.chat_display.add_message_bubble(role, content, self.width())
        self._persist_sessions_store()
        self._refresh_session_list()
        
        self._update_responsive_layout()
        self._stack.setCurrentIndex(self._workspace_index)
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
        if s: 
            dlg.move(s.availableGeometry().right() - 370, s.availableGeometry().bottom() - 230)
        dlg.confirmed.connect(lambda ok: self.worker.approve(ok))
        dlg.show()

    def _minimize(self): self.showMinimized()

    def _open_settings(self):
        self.settings_panel.load_settings_to_ui(self._settings)
        self._stack.setCurrentIndex(self._settings_index)

    def _open_session_selector(self):
        """💬 智能分流按键：如果是宽轨模式直接无视，窄轨模式下独立切回/切出列表层"""
        self._refresh_session_list()
        self._stack.setCurrentIndex(self._workspace_index)
        if self.width() >= 450:
            self.session_page.setVisible(True)
            self.chat_container.setVisible(True)
        else:
            if self.session_page.isVisible():
                self.session_page.setVisible(False)
                self.chat_container.setVisible(True)
            else:
                self.session_page.setVisible(True)
                self.chat_container.setVisible(False)

    def _handle_settings_saved(self, new_settings):
        old_model = self._settings["model"]
        self._settings.update(new_settings)
        self._save_global_config() 
        os.environ["PERMISSION_LEVEL"] = self._settings["permission"]
        self._apply_opacity()
        
        f = self.windowFlags()
        if self._settings["topmost"]: self.setWindowFlags(f | Qt.WindowStaysOnTopHint)
        else: self.setWindowFlags(f & ~Qt.WindowStaysOnTopHint)
        self.show()
        
        if self._settings["model"] != old_model:
            self.llm_ready = False
            self.control_dock.toggle_busy_lock(True, "正在热切换本地算力...")
            self._init_llm_async()
            
        self.chat_display.add_status_banner("⚙️ 全局安全隔离策略已同步完成物理更新")
        self._stack.setCurrentIndex(self._workspace_index)

    def _apply_opacity(self): self.setWindowOpacity(self._settings["opacity"] / 100.0)

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
        # 🌟 核心修复：即使不按下按键，单纯移动鼠标到边界时，即时转换拉伸箭头
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

    def mouseReleaseEvent(self, event): 
        self._drag_pos, self._resize_edge = None, None

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
                        base_model = ChatOpenAI(
                            model=c_info["model_id"],
                            openai_api_base=c_info["base_url"],
                            openai_api_key=c_info["api_key"]
                        )
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
            self.chat_display.add_status_banner("❌ 算力内核连通中断，请检查设置面板的网络参数或本地算力状态")
            self.control_dock.update_placeholder_text("算力内核离线")
            return
        self.llm = llm
        self.tools_map = tm
        self.llm_ready = True
        self.control_dock.toggle_busy_lock(False)
        self.control_dock.force_input_focus()
        self.chat_display.add_status_banner(f"✅ 安全算力联通成功 [{self._settings['model']}]")