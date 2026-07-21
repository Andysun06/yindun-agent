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
    QMessageBox, QLineEdit, QMenu
)
from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QPoint, QRect
from PySide6.QtGui import QColor, QPalette, QFont, QCursor, QMouseEvent

# 核心后端多算力通信隔离舱
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, AIMessage
from yindun.core.file_tools import list_local_files, create_local_file, delete_local_file, read_local_file, modify_local_file, run_local_command, analyze_project, search_in_files

# 跨模块总线架构集成：动态引入所有的原子功能积木件
from yindun.gui.styles import GLOBAL_QSS, DARK_QSS                                
from yindun.worker.agent_worker import Worker                          
from yindun.utils.document_parser import extract_file_text              
from yindun.gui.confirm_dialog import ConfirmDialog                     
from yindun.gui.settings_panel import SettingsPanel, detect_ollama_models, get_first_available_model
from yindun.gui.chat_display import ChatDisplay       
from yindun.gui.status_bar import AgentStatusBar     
from yindun.gui.control_dock import ControlDock       
from yindun.gui.session_selector import SessionSelectorPage
from yindun.gui.new_session_dialog import NewSessionDialog
from yindun.gui.data_dashboard import DataDashboard

COLLAPSED_H = 44  # 极致折叠挂件高度
EXPANDED_W, EXPANDED_H = 420, 640


class _AudioTranscriberThread(QThread):
    """
    后台音频转写线程：避免转写大音频时 GUI 卡死
    """
    finished = Signal(str)  # 转写完成，返回文本内容

    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def run(self):
        # 在后台线程执行转写，不阻塞 GUI
        from yindun.utils.document_parser import extract_file_text
        text = extract_file_text(self.filepath)
        self.finished.emit(text)


class MainWindow(QWidget):
    # 后台 Ollama 模型检测完成后通知设置面板刷新
    model_list_updated = Signal(list)

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
        self.attached_files = []  # 多文件挂载列表 [{"name": str, "text": str}]
        
        # 全局安全隔离配置树
        self._config_file = Path(__file__).resolve().parents[2] / "global_config.json"
        self._settings = {
            "model": "qwen2.5:7b", "privacy": True, "dark_mode": False, "topmost": True,
            "custom_models": {}, "thinking_depth": 3,
            "ollama_models_cache": []   # 缓存的 Ollama 模型列表，用于零阻塞启动
        }
        self._load_global_config()

        # 检查保存的模型是否仍然可用（仅当缓存非空时才校验，缓存空时信任已保存的配置）
        saved_model = self._settings.get("model", "")
        custom_models = self._settings.get("custom_models", {})
        cached_models = self._settings.get("ollama_models_cache", [])
        if cached_models and saved_model not in cached_models and saved_model not in custom_models:
            self._settings["model"] = cached_models[0]
            self._save_global_config()
        
        os.environ["PERMISSION_LEVEL"] = self._settings.get("permission", "完全控制 (读/写/列表)")

        # 根据配置设置窗口标志
        flags = Qt.FramelessWindowHint
        if self._settings.get("topmost", True):
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self._restore_window_geometry()
        
        # 开启主窗体的全局高级鼠标轨迹追踪，激活无边框自由拉伸机制
        self.setMouseTracking(True)
        
        # 无边框像素级物理拖拽缩放算力参数
        self._drag_pos = None
        self._resize_edge = None
        self._edge_px = 22        # 单边检测宽度
        self._corner_px = 32      # 角落检测宽度（更大，便于命中四角）
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

    def _restore_window_geometry(self):
        """恢复上次关闭时的窗口位置和大小，若不可用则回退到右下角默认"""
        geo = self._settings.get("window_geometry")
        if geo and len(geo) == 4:
            r = QRect(*geo)
            for screen in QApplication.screens():
                if screen.availableGeometry().intersects(r):
                    self.setGeometry(r)
                    return
        self.resize(EXPANDED_W, EXPANDED_H)
        self._position_bottom_right()

    def closeEvent(self, event):
        """关闭时保存窗口位置，下次启动时恢复"""
        if not self._collapsed:
            g = self.geometry()
            self._settings["window_geometry"] = [g.x(), g.y(), g.width(), g.height()]
            self._save_global_config()
        super().closeEvent(event)

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
        
        for txt, nm, slot in [("➕", "titleBtnNew", self._new_session), ("💬", "titleBtn", self._open_session_selector), ("⚙", "titleBtn", self._open_settings), ("—", "titleBtn", self._minimize),
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
        self.chat_display.file_dropped.connect(self._mount_files)
        self.control_dock.manage_requested.connect(self._show_file_manager)
        self.control_dock.stop_requested.connect(self._on_stop_requested)

        self.data_dashboard = DataDashboard()
        self._dashboard_dark = False

        cc_layout.addWidget(self.chat_display, 1)
        cc_layout.addWidget(self.status_bar)
        cc_layout.addWidget(self.control_dock)
        cc_layout.addWidget(self.data_dashboard)
        self.workspace_layout.addWidget(self.chat_container)
        
        self._stack.addWidget(self.workspace_page)
        self._workspace_index = 0

        # 4. 独立的高级安全参数配置面板舱
        self.settings_panel = SettingsPanel()
        self.settings_panel.settings_saved.connect(self._handle_settings_saved)
        self.settings_panel.cancel_clicked.connect(self._on_settings_cancel)
        self.model_list_updated.connect(self.settings_panel.refresh_ollama_models)
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
            # 应用启动后，用聊天区域实际可用宽度更新所有气泡的宽度
            self.chat_display.update_all_bubbles_width(self._get_chat_available_width())
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
        # 更新气泡宽度（使用聊天区域实际可用宽度）
        self.chat_display.update_all_bubbles_width(self._get_chat_available_width())

    def _get_chat_available_width(self):
        """计算聊天区域实际可用宽度（窗口宽度 - 会话页面宽度 - 边距）"""
        total_width = self.width()
        session_width = 0
        if self.session_page.isVisible() and not self._session_only:
            session_width = self.session_page.width()
        # 减去外层边距和容器边框
        available = total_width - session_width - 32
        return max(200, available)

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
        self.data_dashboard.set_dark_mode(dark)
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
        
        # 显示用户消息（含附件名摘要）
        if self.attached_files:
            names = ", ".join(f["name"] for f in self.attached_files)
            display_text = f"📎 附件: {names}\n{text}"
        else:
            display_text = text
        self.chat_display.add_message_bubble("user", display_text, self.width())

        full_context = text
        if self.attached_files:
            # 检查是否有音频还在转写中
            if any(not f.get("text", "").strip() for f in self.attached_files):
                self._pending_audio_request = text
                self.chat_display.add_assistant_message("⌛ 正在解析音频，请稍候...")
                self.is_busy = True
                self.control_dock.toggle_busy_lock(True)
                return
            full_context = self._build_attachment_context(text)
            self._clear_attachments()

        self._start_worker(full_context)

    def _on_file_pick_request(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "挂载本地文件", "",
            "办公文件 (*.pdf *.docx *.xlsx *.txt *.md *.csv);;"
            "音频文件 (*.mp3 *.wav *.flac *.m4a *.aac *.ogg *.opus *.wma);;"
            "所有文件 (*)"
        )
        if paths:
            self._mount_files(paths)

    def _mount_files(self, paths):
        """批量挂载本地文件（支持多文件拖放/多选）"""
        _AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")
        for path in paths:
            fname = os.path.basename(path)
            if path.lower().endswith(_AUDIO_EXTS):
                self.chat_display.add_status_banner(f"🎙️ 正在转写音频：{fname}（请稍候...）")
                self._attached_file_pending = fname
                self.attached_files.append({"name": fname, "text": ""})
                self._audio_transcriber = _AudioTranscriberThread(path)
                self._audio_transcriber.finished.connect(self._on_audio_transcribe_done)
                self._audio_transcriber.start()
            else:
                self.attached_files.append({"name": fname, "text": extract_file_text(path)})
                self.chat_display.add_status_banner(f"🔒 离线机密附件就绪：{fname}")
        self.control_dock.update_file_count(len(self.attached_files))

    def _on_audio_transcribe_done(self, text):
        """音频转写完成回调：在 attached_files 列表中回填文本"""
        if not (hasattr(self, "_attached_file_pending") and self._attached_file_pending):
            return
        fname = self._attached_file_pending
        self._attached_file_pending = None
        for f in self.attached_files:
            if f["name"] == fname:
                f["text"] = text
                break
        self.chat_display.add_status_banner(f"🔒 音频转写完成：{fname}")
        # 检查是否有等待中的请求
        if hasattr(self, "_pending_audio_request") and self._pending_audio_request:
            user_input = self._pending_audio_request
            self._pending_audio_request = None
            full_context = self._build_attachment_context(user_input)
            self._clear_attachments()
            self._start_worker(full_context)

    # ──────────────────────────────────────────
    # 附件管理
    # ──────────────────────────────────────────
    _MAX_DOC_CHARS = 12000

    def _build_attachment_context(self, user_text: str) -> str:
        """拼接所有附件上下文 + 用户提问"""
        contexts = []
        for f in self.attached_files:
            t = (f['text'] or '')[:self._MAX_DOC_CHARS]
            if len(f['text'] or '') > self._MAX_DOC_CHARS:
                t += f"\n\n[注：文档较长，已截断前 {self._MAX_DOC_CHARS} 字符，如需分析后续内容请分段提问]"
            contexts.append(f"[离线附件环境上下文：{f['name']}]\n{t}")
        return "\n\n".join(contexts) + f"\n\n[人类当前实时提问]：{user_text}"

    def _clear_attachments(self):
        self.attached_files = []
        self.control_dock.update_file_count(0)

    def _show_file_manager(self):
        """弹出文件管理菜单"""
        if not self.attached_files:
            return
        dark = self._settings.get("dark_mode", False)
        accent = "#3b82f6" if dark else "#07c160"
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{ background: {'#2a2a3e' if dark else '#fff'}; border: 1px solid {'#3a3a50' if dark else '#e0e3ea'};
                      border-radius: 10px; padding: 6px; }}
            QMenu::item {{ padding: 8px 24px; border-radius: 6px; color: {'#e0e0e0' if dark else '#1a1a2e'}; }}
            QMenu::item:selected {{ background: {accent}; color: white; }}
            QMenu::separator {{ height: 1px; background: {'#3a3a50' if dark else '#e0e3ea'}; margin: 4px 8px; }}
            QMenu::item:disabled {{ color: #888; }}
        """)
        title = menu.addAction("🗂️ 已挂载附件")
        title.setEnabled(False)
        menu.addSeparator()
        for i, f in enumerate(self.attached_files):
            icon = "⏳" if not f.get("text", "").strip() else "📄"
            act = menu.addAction(f"{icon}  {f['name']}")
            act.triggered.connect(lambda _, idx=i: self._remove_file(idx))
        menu.addSeparator()
        menu.addAction("🗑️  清空全部附件").triggered.connect(
            lambda: (self.chat_display.add_status_banner(f"🗑️ 已清空 {len(self.attached_files)} 个附件"),
                     self._clear_attachments()))
        menu.addSeparator()
        menu.addAction("📎  挂载新文件...").triggered.connect(self._on_file_pick_request)
        btn = self.control_dock.manage_btn
        menu.exec(btn.mapToGlobal(QPoint(0, btn.height() + 4)))

    def _remove_file(self, idx):
        """删除指定索引的附件"""
        if 0 <= idx < len(self.attached_files):
            name = self.attached_files.pop(idx)["name"]
            self.chat_display.add_status_banner(f"✕ 已移除附件：{name}")
            self.control_dock.update_file_count(len(self.attached_files))

    def _start_worker(self, user_input):
        self.status_bar.start_thinking("隐盾大脑研判中")
        self._request_start_time = datetime.now()
        self.worker = Worker()
        self.worker.user_input = user_input
        sid = self._current_session_id
        self.worker.messages_snapshot = list(self._sessions.get(sid, {}).get("messages", []))
        self.worker.think_mode = self.control_dock.get_current_mode()
        self.worker.privacy_shield = self._settings["privacy"]
        self.worker.think_depth = self._settings.get("thinking_depth", 3)
        self.worker.llm = self.llm
        self.worker.tools_map = self.tools_map
        self.worker.sandbox_path = os.environ.get("SANDBOX_PATH", os.path.abspath("."))
        
        self.thread = QThread()
        self.worker.moveToThread(self.thread)
        
        self.worker.finished.connect(self._on_reply_received)
        self.worker.error.connect(self._on_error_caught)
        self.worker.status.connect(self.status_bar.set_static_text)
        self.worker.need_confirm.connect(self._on_intercept_confirm)
        self.worker.intermediate_result.connect(self._on_intermediate_result)
        self.status_bar.cancel_requested.connect(lambda: self.worker.cancel() if self.worker else None)
        # 数据看板：每次发起请求 +1 次模型调用
        self.data_dashboard.inc_model_call()
        
        # 立即创建占位气泡，让用户看到"正在思考"（后续会被渐进更新覆盖）
        self.chat_display.add_message_bubble("assistant", "⌛ 正在为您分析，请稍候...", self.width(), meta_info="")
        
        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.thread.start()

    def _on_stop_requested(self):
        """停止按钮：立即取消生成并重置 UI，不等待 worker 线程"""
        if self.worker and self.is_busy:
            self.worker.cancel()
            # 断开旧 worker 信号，防止 stale 回复污染 UI
            try:
                self.worker.finished.disconnect(self._on_reply_received)
                self.worker.error.disconnect(self._on_error_caught)
            except Exception:
                pass
            # 立即重置 UI 状态
            self.is_busy = False
            self.control_dock.toggle_busy_lock(False)
            self.control_dock.force_input_focus()
            self.status_bar.stop_thinking()
            self.status_bar.set_static_text("⏹ 已取消生成")

    def _on_intermediate_result(self, text):
        """渐进输出：收到模型的中间结果时，更新最后一个 AI 气泡"""
        self.chat_display.update_last_assistant_bubble(text, meta_info=None)

    def _on_reply_received(self, r):
        self.status_bar.stop_thinking()
        # 计算 meta info：模式 + 耗时
        meta = ""
        try:
            mode = getattr(self.worker, 'think_mode', '')
            elapsed = int((datetime.now() - self._request_start_time).total_seconds())
            m, s = divmod(elapsed, 60)
            time_str = f"{m}m{s:02d}s" if m > 0 else f"{s}s"
            meta = f"{mode}   {time_str}"
        except Exception:
            pass
        # 最终回答：更新已存在的 AI 气泡（带元信息），而不是新建一个
        updated = self.chat_display.update_last_assistant_bubble(r, meta_info=meta)
        if not updated:
            # 兜底：如果没有可更新的气泡，就新建一个
            self.chat_display.add_message_bubble("assistant", r, self.width(), meta_info=meta)
        self._apply_worker_messages()
        self._refresh_dashboard()
        self._cleanup_session()

    def _refresh_dashboard(self):
        """根据当前 worker 的统计刷新数据看板"""
        try:
            tool_count = getattr(self.worker, "tool_call_count", 0)
            self.data_dashboard.update_tool_calls(tool_count)
            token_est = sum(len(str(m.get("content", ""))) for m in self.worker.result_messages) // 2
            self.data_dashboard.update_context_tokens(token_est)
        except Exception:
            pass

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
        dlg = NewSessionDialog(self, dark=self._settings.get("dark_mode", False))
        if self.window():
            dlg.move(self.window().geometry().x() + 30, self.window().geometry().y() + 200)
        dlg.created.connect(self._handle_new_session)
        dlg.show()

    def _handle_new_session(self, title: str):
        # 空字符串走自动命名
        title = title.strip() or f"新对话 {datetime.now().strftime('%m-%d %H:%M')}"
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

        # 切换会话后，用聊天区域实际可用宽度更新所有气泡的宽度（确保位置正确）
        # 使用延迟调用确保所有气泡都已经添加完成
        QTimer.singleShot(0, lambda: self.chat_display.update_all_bubbles_width(self._get_chat_available_width()))

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
        """💬 双模导航:
        - 在设置页: 智能回到最近一次使用的对话 (与 ← 返回对话等价)
        - 在工作台: 在'当前对话'和'会话选择器'之间双向切换
        """
        # 1) 来自设置页 → 走智能返回路径, 不会进入选择器
        if self._stack.currentIndex() == self._settings_index:
            self._on_settings_cancel()
            return

        # 2) 在工作台 → 执行切换
        self._stack.setCurrentIndex(self._workspace_index)
        self._refresh_session_list()

        if self._session_only:
            # 当前在选择器, 回到对话
            if self._current_session_id and self._current_session_id in self._sessions:
                self._exit_session_only()
            else:
                # 没有当前会话, 尝试最近一次使用的
                latest = self._get_most_recent_session_id()
                if latest:
                    self._switch_to_session(latest)  # 内部会退出 _session_only
                # else: 真的没有历史, 保持在选择器让用户新建
        else:
            # 当前在对话中, 进入全屏选择器
            self._show_session_only()

    def _on_settings_cancel(self):
        """⚡ 从设置返回对话时, 自动定位到最近一次使用的对话, 无需再次手动选择"""
        self._stack.setCurrentIndex(self._workspace_index)
        # 优先保持当前会话; 若没有则取最近更新过的会话
        if not self._current_session_id or self._current_session_id not in self._sessions:
            latest = self._get_most_recent_session_id()
            if latest:
                self._switch_to_session(latest)
            else:
                # 没有任何历史对话, 退回到会话选择器让用户新建
                self._show_session_only()

    def _get_most_recent_session_id(self) -> str | None:
        if not self._sessions:
            return None
        return max(
            self._sessions.values(),
            key=lambda s: s.get("updated_at", ""),
        ).get("id")

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
        c = self._corner_px
        # 先检测角落（用更大的检测区 c），再检测单边
        in_left = x < c
        in_right = x > w - c
        in_top = y < c
        in_bottom = y > h - c
        if in_left and in_top: return "top-left"
        if in_right and in_top: return "top-right"
        if in_left and in_bottom: return "bottom-left"
        if in_right and in_bottom: return "bottom-right"
        # 再检测单边（用较小的检测区 e）
        if x < e: return "left"
        if x > w - e: return "right"
        if y < e: return "top"
        if y > h - e: return "bottom"
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
        # 主动更新所有气泡的宽度（折叠态下也要同步）
        self.chat_display.update_all_bubbles_width(self._get_chat_available_width())

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
        # 主动更新所有气泡的宽度（使用聊天区域实际可用宽度）
        if not self._collapsed:
            self.chat_display.update_all_bubbles_width(self._get_chat_available_width())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._collapsed:
            self._normal_w = self.width()
            self._normal_h = self.height()
            self._update_responsive_layout()

    def _init_llm_async(self):
        self.status_bar.set_static_text("🔄 正在连接离线算力内核...")
        self.control_dock.toggle_busy_lock(True, "正在注入算力...")
        self.control_dock.mode_switch.setEnabled(True)
        mn = self._settings["model"]
        custom_models = self._settings.get("custom_models", {})

        tools_list = [list_local_files, create_local_file, delete_local_file, read_local_file, modify_local_file, run_local_command, analyze_project, search_in_files]

        def do_init():
            import json as _json
            from pathlib import Path as _Path
            # 在后台线程中检测 Ollama 模型列表并更新缓存
            _fresh = detect_ollama_models()
            _cfg = _Path(__file__).resolve().parents[2] / "global_config.json"
            try:
                with _cfg.open("r", encoding="utf-8") as _f:
                    _saved = _json.load(_f)
            except Exception:
                _saved = {}
            if _saved.get("ollama_models_cache") != _fresh:
                _saved["ollama_models_cache"] = _fresh
                try:
                    with _cfg.open("w", encoding="utf-8") as _f:
                        _json.dump(_saved, _f, ensure_ascii=False, indent=2)
                except Exception:
                    pass
            try:
                if mn in custom_models:
                    from langchain_openai import ChatOpenAI
                    c_info = custom_models[mn]
                    base_model = ChatOpenAI(
                        model=c_info["model_id"], openai_api_base=c_info["base_url"],
                        openai_api_key=c_info["api_key"]
                    )
                else:
                    base_model = ChatOllama(
                        model=mn,
                        base_url="http://127.0.0.1:11434",
                        timeout=60,
                        options={
                            "num_ctx": 16384,
                            "num_predict": 4096,
                            "temperature": 0.7,
                            "top_p": 0.9,
                        },
                        keep_alive=300,
                    )
                m_bound = base_model.bind_tools(tools_list)
                return m_bound, {t.name: t for t in tools_list}, _fresh, None
            except Exception as e:
                return None, {}, [], f"模型初始化失败: {str(e)}"

        class IW(QObject):
            done = Signal(object, object, list, object)
            def __init__(s, fn): super().__init__(); s.fn = fn
            def run(s): s.done.emit(*s.fn())

        self._it = QThread()
        self._iw = IW(do_init)
        self._iw.moveToThread(self._it)
        self._it.started.connect(self._iw.run)
        self._iw.done.connect(self._on_llm_ready)
        self._iw.done.connect(self._it.quit)
        self._it.start()

    def _on_llm_ready(self, llm, tm, fresh_models, err):
        self.status_bar.stop_thinking()
        # 更新 settings 中的缓存
        if fresh_models:
            self._settings["ollama_models_cache"] = fresh_models
            # 通知设置面板刷新模型列表（如果设置页已打开）
            self.model_list_updated.emit(fresh_models)
        if err:
            self.chat_display.add_status_banner(f"❌ {err}")
            self.control_dock.update_placeholder_text("算力内核离线")
            self.control_dock.toggle_busy_lock(False)
            return
        self.llm = llm
        self.tools_map = tm
        self.llm_ready = True
        self.control_dock.toggle_busy_lock(False)
        self.control_dock.force_input_focus()
        self.chat_display.add_status_banner(f"✅ 安全算力联通成功 [{self._settings['model']}]")
        self.data_dashboard.update_current_model(self._settings["model"])