# -*- coding: utf-8 -*-
# Yindun Security Agent V3.1.4 - Independent Settings Panel Component (Real-time Adaptive)
import os
import subprocess
import json
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QGroupBox, QFormLayout, QComboBox, QRadioButton, QButtonGroup, QFrame, QSlider,
    QListWidget, QListWidgetItem, QFileDialog, QProgressBar
)
from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtGui import QCursor

# 核心跨模块导入：引入自定义模型资产表单配置舱
from yindun.gui.custom_model_dialog import CustomModelDialog

def detect_ollama_models():
    """检测本地 Ollama 已安装的模型列表"""
    try:
        result = subprocess.run(
            ["ollama", "list", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return [m["name"] for m in data.get("models", [])]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return []

def get_first_available_model():
    """获取第一个可用的模型，如果没有则返回 None"""
    models = detect_ollama_models()
    return models[0] if models else None

class SlidingSwitch(QFrame):
    """高级原子级左右滑动开关组件"""
    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(46, 22)
        self._checked = False
        self.setObjectName("switchBase")
        
        # 组装内部滑动小圆球
        self.handle = QFrame(self)
        self.handle.setObjectName("switchHandle")
        self.handle.setFixedSize(16, 16)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        
        self._update_position()
        self.update_style()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, emit_signal=True):
        if self._checked != checked:
            self._checked = checked
            self._update_position()
            self.update_style()
            if emit_signal:
                self.toggled.emit(self._checked)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)

    def _update_position(self):
        # 拨动至右侧或左侧
        if self._checked:
            self.handle.move(26, 3)
        else:
            self.handle.move(4, 3)

    def update_style(self):
        base_bg = "#07c160" if self._checked else "#cbd5e1"
        self.setStyleSheet(f"""
            QFrame#switchBase {{ background-color: {base_bg}; border-radius: 11px; border: none; }}
            QFrame#switchHandle {{ background-color: #ffffff; border-radius: 8px; border: none; }}
        """)


class SettingsPanel(QWidget):
    # 定义高内聚的数据驱动信号管道，数据改变时即时同步向外发射
    settings_saved = Signal(dict)
    cancel_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsPage")
        self.custom_models = {} 
        self._is_loading = False # 互锁状态旗标，防止初始化加载数据时循环触发即时保存
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 14)
        layout.setSpacing(8)
        
        # 头部导航操作区
        hdr = QHBoxLayout()
        st = QLabel("⚙️ 隐盾设置")
        st.setObjectName("settingsTitle")
        hdr.addWidget(st)
        hdr.addStretch()
        
        back = QPushButton("← 返回对话")
        back.setObjectName("settingsCancel")
        back.setCursor(QCursor(Qt.PointingHandCursor))
        back.clicked.connect(self.cancel_clicked.emit)
        hdr.addWidget(back)
        layout.addLayout(hdr)
        
        # 弹性表单长区域视舱
        scroll = QScrollArea()
        scroll.setObjectName("settingsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        
        sc = QWidget()
        sc.setStyleSheet("background:transparent;")
        form_layout = QVBoxLayout(sc)
        form_layout.setContentsMargins(0, 0, 8, 0)
        form_layout.setSpacing(6)
        
        # 舱区 1：多算力引擎组合配置
        g1 = QGroupBox("算力底座")
        f1 = QFormLayout(g1)
        f1.setSpacing(8)
        
        model_container = QHBoxLayout()
        self.setting_model = QComboBox()
        self.setting_model.setObjectName("settingCombo")
        # 🌟 即时生效：模型下拉选择切换时立刻驱动更新
        self.setting_model.currentTextChanged.connect(self._trigger_immediate_save)
        model_container.addWidget(self.setting_model, 1)
        
        self.add_model_btn = QPushButton("➕ 新增外部")
        self.add_model_btn.setObjectName("addModelBtn")
        self.add_model_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.add_model_btn.clicked.connect(self._popup_custom_model_dialog)
        model_container.addWidget(self.add_model_btn)
        f1.addRow("模型选择:", model_container)
        
        # 🌟 核心升级：换入左右滑动开关，并完美靠右挂载表单
        self.setting_privacy = SlidingSwitch()
        self.setting_privacy.toggled.connect(self._trigger_immediate_save)
        f1.addRow("隐私隔离网关:", self.setting_privacy)

        # 🌟 思考深度滑动条（仅影响深度模式，拖动释放后生效）
        depth_container = QHBoxLayout()
        self.setting_depth_slider = QSlider(Qt.Horizontal)
        self.setting_depth_slider.setRange(1, 10)
        self.setting_depth_slider.setFixedWidth(140)
        self.setting_depth_slider.setTickPosition(QSlider.TicksBelow)
        self.setting_depth_slider.setTickInterval(1)
        self.setting_depth_slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 4px;
                background: #3a3a4a;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -5px 0;
                background: #6e80ff;
                border-radius: 7px;
            }
            QSlider::add-page:horizontal {
                background: #3a3a4a;
                border-radius: 2px;
            }
            QSlider::sub-page:horizontal {
                background: #6e80ff;
                border-radius: 2px;
            }
            QSlider::tick:below {
                color: #666;
            }
        """)
        # 拖动释放后才保存（避免频繁触发）
        self.setting_depth_slider.sliderReleased.connect(self._trigger_immediate_save)
        self.setting_depth_label = QLabel("3")
        self.setting_depth_label.setFixedWidth(16)
        self.setting_depth_slider.valueChanged.connect(
            lambda v: self.setting_depth_label.setText(str(v))
        )
        depth_container.addWidget(self.setting_depth_slider)
        depth_container.addWidget(self.setting_depth_label)
        depth_container.addStretch()
        f1.addRow("深度思考强度:", depth_container)
        form_layout.addWidget(g1)

        # 舱区 2：界面偏好自定义
        g3 = QGroupBox("界面偏好")
        f3 = QFormLayout(g3)
        f3.setSpacing(8)
        
        self.setting_theme_light = QRadioButton("白色")
        self.setting_theme_dark = QRadioButton("黑色")
        self.setting_theme_light.setChecked(True)
        
        theme_group = QButtonGroup(self)
        theme_group.addButton(self.setting_theme_light)
        theme_group.addButton(self.setting_theme_dark)
        # 🌟 即时生效：点击任意主题色单选框时立刻应用深色/浅色皮肤
        theme_group.buttonClicked.connect(self._trigger_immediate_save)
        
        theme_container = QHBoxLayout()
        theme_container.addWidget(self.setting_theme_light)
        theme_container.addWidget(self.setting_theme_dark)
        theme_container.addStretch()
        f3.addRow("背景颜色:", theme_container)
        
        # 🌟 核心升级：窗口置顶换入高定制滑动开关
        self.setting_topmost = SlidingSwitch()
        self.setting_topmost.toggled.connect(self._trigger_immediate_save)
        f3.addRow("窗口始终置顶:", self.setting_topmost)
        form_layout.addWidget(g3)

        # 🌟 终极破局：完全干掉底部的“保存/保存设置”确认按钮，留出极简现代的表单尾部
        form_layout.addStretch()

        # ──────────────────────────────────────────
        # 舱区 3：本地知识库管理（脱敏 RAG）
        # ──────────────────────────────────────────
        g_kb = QGroupBox("📚 本地知识库（脱敏 RAG）")
        g_kb.setStyleSheet("""
            QGroupBox {
                font-weight: bold; padding-top: 14px;
                border: 1px solid #3a3a4a; border-radius: 8px;
                margin-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 10px; padding: 0 4px;
            }
        """)
        kb_layout = QVBoxLayout(g_kb)
        kb_layout.setSpacing(6)

        # 状态行
        self.kb_status_label = QLabel("检测中...")
        self.kb_status_label.setWordWrap(True)
        self.kb_status_label.setStyleSheet("color: #888; font-size: 12px;")
        kb_layout.addWidget(self.kb_status_label)

        # 操作按钮行
        kb_btn_row = QHBoxLayout()
        self.kb_add_btn = QPushButton("➕ 添加文档")
        self.kb_add_btn.setObjectName("addModelBtn")
        self.kb_add_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.kb_add_btn.clicked.connect(self._kb_add_documents)
        kb_btn_row.addWidget(self.kb_add_btn)

        self.kb_refresh_btn = QPushButton("🔄 刷新")
        self.kb_refresh_btn.setObjectName("addModelBtn")
        self.kb_refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.kb_refresh_btn.clicked.connect(self._kb_refresh)
        kb_btn_row.addWidget(self.kb_refresh_btn)

        self.kb_del_btn = QPushButton("🗑 删除选中")
        self.kb_del_btn.setObjectName("addModelBtn")
        self.kb_del_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self.kb_del_btn.clicked.connect(self._kb_delete_selected)
        kb_btn_row.addWidget(self.kb_del_btn)
        kb_btn_row.addStretch()
        kb_layout.addLayout(kb_btn_row)

        # 进度条
        self.kb_progress = QProgressBar()
        self.kb_progress.setVisible(False)
        self.kb_progress.setTextVisible(True)
        self.kb_progress.setStyleSheet("""
            QProgressBar { border: 1px solid #3a3a4a; border-radius: 4px; text-align: center; height: 18px; }
            QProgressBar::chunk { background: #6e80ff; border-radius: 3px; }
        """)
        kb_layout.addWidget(self.kb_progress)

        # 文档列表
        self.kb_list = QListWidget()
        self.kb_list.setStyleSheet("""
            QListWidget { background: #1e1e2a; border: 1px solid #3a3a4a; border-radius: 6px; padding: 4px; }
            QListWidget::item { padding: 6px 8px; border-bottom: 1px solid #2a2a3a; }
            QListWidget::item:selected { background: #6e80ff; color: white; }
        """)
        self.kb_list.setMinimumHeight(120)
        self.kb_list.setMaximumHeight(200)
        kb_layout.addWidget(self.kb_list)

        form_layout.addWidget(g_kb)

        # 知识库回调对象（由 main_window 注入 worker 后设置）
        self._kb_worker = None
        # 标记是否为首次检测（首次只做轻量依赖检查，不阻塞 UI）
        self._kb_first_check = True

        # 初始检测（延迟到界面显示后，避免阻塞启动）
        from PySide6.QtCore import QTimer
        QTimer.singleShot(800, self._kb_refresh)
        
        scroll.setWidget(sc)
        layout.addWidget(scroll, 1)

    # ──────────────────────────────────────────
    # 知识库管理方法（供 GUI 操作）
    # ──────────────────────────────────────────
    def set_kb_worker(self, worker):
        """由 main_window 注入 worker 实例，用于调用知识库管理接口。"""
        self._kb_worker = worker

    def _kb_refresh(self):
        """刷新知识库状态和文档列表。"""
        # 首次检测只做轻量依赖检查，不连 Ollama（避免阻塞 UI 启动）
        if self._kb_first_check:
            self._kb_first_check = False
            try:
                from yindun.core.knowledge_base import KnowledgeBase
                kb = KnowledgeBase()
                if not kb.is_available():
                    self.kb_status_label.setText(
                        "❌ 知识库不可用：请运行 pip install chromadb langchain-chroma langchain-ollama langchain-text-splitters"
                    )
                    self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
                    self.kb_add_btn.setEnabled(False)
                    self.kb_del_btn.setEnabled(False)
                    return
                # 依赖就绪，提示用户点刷新检测 Ollama
                self.kb_status_label.setText("✅ 依赖已就绪，点击「刷新」检测知识库状态")
                self.kb_status_label.setStyleSheet("color: #6e80ff; font-size: 12px;")
                self.kb_add_btn.setEnabled(False)
                self.kb_del_btn.setEnabled(False)
                return
            except Exception as e:
                self.kb_status_label.setText(f"❌ 检测失败: {type(e).__name__}: {e}")
                self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
                return

        if self._kb_worker is None:
            # worker 还没创建（用户还没发过消息），直接用 KnowledgeBase 检测依赖状态
            try:
                from yindun.core.knowledge_base import KnowledgeBase
                kb = KnowledgeBase()
                available = kb.is_available()
                if not available:
                    self.kb_status_label.setText(
                        "❌ 知识库不可用：请运行 pip install chromadb langchain-chroma langchain-ollama langchain-text-splitters"
                    )
                    self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
                    self.kb_add_btn.setEnabled(False)
                    self.kb_del_btn.setEnabled(False)
                    return
                # 检查 Ollama
                import requests
                try:
                    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
                    r = requests.get(f"{ollama_host}/api/tags", timeout=2)
                    if r.status_code != 200:
                        raise Exception("ollama not running")
                    models = [m["name"] for m in r.json().get("models", [])]
                    if not any("nomic-embed-text" in m for m in models):
                        self.kb_status_label.setText(
                            "⚠️ 缺少 embedding 模型，请运行: ollama pull nomic-embed-text"
                        )
                        self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
                        self.kb_add_btn.setEnabled(False)
                        self.kb_del_btn.setEnabled(False)
                        return
                except Exception:
                    self.kb_status_label.setText("⚠️ Ollama 服务未启动，请先启动 Ollama")
                    self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
                    self.kb_add_btn.setEnabled(False)
                    self.kb_del_btn.setEnabled(False)
                    return
                # 依赖就绪，用临时 kb 实例拉取文档列表
                docs = kb.list_documents()
                stats = kb.get_stats()
                total_chunks = stats.get("total_chunks", 0)
                self.kb_list.clear()
                for d in docs:
                    from datetime import datetime
                    ts = d.get("added_at", 0)
                    time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "未知"
                    item_text = f"📄 {d['file']}  |  {d['chunks']} 片段  |  {time_str}"
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.UserRole, d["file"])
                    self.kb_list.addItem(item)
                self.kb_status_label.setText(
                    f"✅ 知识库就绪  |  文档 {len(docs)} 篇  |  总片段 {total_chunks}  |  模型: {stats.get('embed_model', 'N/A')}"
                )
                self.kb_status_label.setStyleSheet("color: #10b981; font-size: 12px;")
                self.kb_add_btn.setEnabled(True)
                self.kb_del_btn.setEnabled(True)
                # 临时 kb 实例用于添加/删除操作
                self._kb_standalone = kb
            except Exception as e:
                self.kb_status_label.setText(f"❌ 检测失败: {type(e).__name__}: {e}")
                self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
            return
        try:
            status = self._kb_worker.get_knowledge_base_status()
            if not status.get("available"):
                self.kb_status_label.setText(
                    "❌ 知识库不可用：请运行 pip install chromadb langchain-chroma langchain-ollama langchain-text-splitters"
                )
                self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
                self.kb_list.clear()
                self.kb_add_btn.setEnabled(False)
                self.kb_del_btn.setEnabled(False)
                return
            if not status.get("ollama_running"):
                self.kb_status_label.setText("⚠️ Ollama 服务未启动，请先启动 Ollama")
                self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
                self.kb_add_btn.setEnabled(False)
                self.kb_del_btn.setEnabled(False)
                return
            if not status.get("has_embed_model"):
                self.kb_status_label.setText(
                    "⚠️ 缺少 embedding 模型，请运行: ollama pull nomic-embed-text"
                )
                self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
                self.kb_add_btn.setEnabled(False)
                self.kb_del_btn.setEnabled(False)
                return

            # 依赖就绪，拉取文档列表
            self.kb_add_btn.setEnabled(True)
            self.kb_del_btn.setEnabled(True)
            result = self._kb_worker.list_knowledge_base()
            if result.get("available"):
                docs = result.get("documents", [])
                total_chunks = result.get("total_chunks", 0)
                self.kb_list.clear()
                for d in docs:
                    from datetime import datetime
                    ts = d.get("added_at", 0)
                    time_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "未知"
                    item_text = f"📄 {d['file']}  |  {d['chunks']} 片段  |  {time_str}"
                    item = QListWidgetItem(item_text)
                    item.setData(Qt.UserRole, d["file"])
                    self.kb_list.addItem(item)
                self.kb_status_label.setText(
                    f"✅ 知识库就绪  |  文档 {len(docs)} 篇  |  总片段 {total_chunks}  |  模型: {result.get('embed_model', 'N/A')}"
                )
                self.kb_status_label.setStyleSheet("color: #10b981; font-size: 12px;")
            else:
                err = result.get("error", "未知错误")
                self.kb_status_label.setText(f"❌ 获取列表失败: {err}")
                self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
        except Exception as e:
            self.kb_status_label.setText(f"❌ 刷新失败: {type(e).__name__}: {e}")
            self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")

    def _kb_add_documents(self):
        """打开文件选择对话框，选文档入库。"""
        if self._kb_worker is None and not hasattr(self, '_kb_standalone'):
            self.kb_status_label.setText("⚠️ 知识库未就绪，请先刷新")
            self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
            return
        from PySide6.QtWidgets import QFileDialog
        file_paths, _ = QFileDialog.getOpenFileNames(
            self, "选择要入库的文档", "",
            "支持的文档 (*.pdf *.docx *.doc *.xlsx *.xls *.txt *.md *.csv);;所有文件 (*)"
        )
        if not file_paths:
            return

        # 在后台线程入库，避免阻塞 UI
        self.kb_add_btn.setEnabled(False)
        self.kb_progress.setVisible(True)
        self.kb_progress.setValue(0)
        self.kb_progress.setFormat("准备入库...")

        self._kb_ingest_thread = _KBIngestThread(
            self._kb_worker if self._kb_worker is not None else self._kb_standalone,
            file_paths,
            use_worker=(self._kb_worker is not None)
        )
        self._kb_ingest_thread.progress.connect(self._on_kb_progress)
        self._kb_ingest_thread.finished.connect(self._on_kb_ingest_finished)
        self._kb_ingest_thread.start()

    def _on_kb_progress(self, current, total, fname, status, detail):
        """入库进度回调（主线程执行）。"""
        pct = int(current / total * 100) if total > 0 else 0
        if status == "processing":
            self.kb_progress.setFormat(f"入库中 [{current}/{total}]: {fname}")
        elif status == "success":
            self.kb_progress.setFormat(f"✅ [{current}/{total}] {fname}: {detail}")
        elif status == "failed":
            self.kb_progress.setFormat(f"❌ [{current}/{total}] {fname}: {detail}")
        self.kb_progress.setValue(pct)

    def _on_kb_ingest_finished(self, result):
        """入库完成回调。"""
        self.kb_progress.setVisible(False)
        self.kb_add_btn.setEnabled(True)
        if result.get("available") is False:
            self.kb_status_label.setText(f"❌ {result.get('error', '入库失败')}")
            self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
            return
        total = result.get("total", 0)
        success = result.get("success", 0)
        failed = result.get("failed", 0)
        chunks = result.get("total_chunks", 0)
        msg = f"入库完成: 成功 {success}/{total}, 失败 {failed}, 新增 {chunks} 片段"
        self.kb_status_label.setText(msg)
        self.kb_status_label.setStyleSheet(
            "color: #10b981;" if failed == 0 else "color: #f59e0b; font-size: 12px;"
        )
        self._kb_refresh()

    def _kb_delete_selected(self):
        """删除选中的文档。"""
        if self._kb_worker is None and not hasattr(self, '_kb_standalone'):
            self.kb_status_label.setText("⚠️ 知识库未就绪，请先刷新")
            self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
            return
        items = self.kb_list.selectedItems()
        if not items:
            self.kb_status_label.setText("⚠️ 请先在列表中选择要删除的文档")
            self.kb_status_label.setStyleSheet("color: #f59e0b; font-size: 12px;")
            return
        kb = self._kb_worker if self._kb_worker is not None else self._kb_standalone
        for item in items:
            file_name = item.data(Qt.UserRole)
            if self._kb_worker is not None:
                result = kb.remove_from_knowledge_base(file_name)
                if not result.get("success"):
                    self.kb_status_label.setText(f"❌ {result.get('message', '删除失败')}")
                    self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
                    self._kb_refresh()
                    return
            else:
                try:
                    kb.remove_document(file_name)
                except Exception as e:
                    self.kb_status_label.setText(f"❌ 删除失败: {e}")
                    self.kb_status_label.setStyleSheet("color: #ef4444; font-size: 12px;")
                    self._kb_refresh()
                    return
        self._kb_refresh()

    def refresh_ollama_models(self, models: list):
        """供 MainWindow 后台检测完成后调用，刷新下拉列表中的 Ollama 模型"""
        if self._is_loading:
            return
        # 清除可能残留的占位默认项
        for item in ["qwen2.5:7b-instruct", "qwen2.5:7b"]:
            idx = self.setting_model.findText(item)
            if idx >= 0 and item not in self.custom_models:
                self.setting_model.removeItem(idx)
        # 在 custom_models 之前插入新的 Ollama 模型
        for i, m in enumerate(models):
            self.setting_model.insertItem(i, m)

    def load_settings_to_ui(self, settings_dict):
        """将连廊传入的数据驱动Payload安全反序列化至界面状态中"""
        self._is_loading = True

        self.setting_model.clear()

        # 优先使用缓存的模型列表（零阻塞），后台检测完成后会通过 refresh_ollama_models 更新
        cached = settings_dict.get("ollama_models_cache", [])
        ollama_models = cached if cached else detect_ollama_models()
        if ollama_models:
            self.setting_model.addItems(ollama_models)

        self.custom_models = settings_dict.get("custom_models", {})
        if self.custom_models:
            self.setting_model.addItems(list(self.custom_models.keys()))

        if not ollama_models and not self.custom_models:
            self.setting_model.addItems(["qwen2.5:7b-instruct", "qwen2.5:7b"])

        idx = self.setting_model.findText(settings_dict["model"])
        if idx >= 0:
            self.setting_model.setCurrentIndex(idx)

        # 静默装填滑动开关状态，不抛出变化信号
        self.setting_privacy.setChecked(settings_dict["privacy"], emit_signal=False)

        if settings_dict.get("dark_mode", False):
            self.setting_theme_dark.setChecked(True)
        else:
            self.setting_theme_light.setChecked(True)

        self._is_loading = False
            
        self.setting_topmost.setChecked(settings_dict["topmost"], emit_signal=False)

        depth = settings_dict.get("thinking_depth", 3)
        self.setting_depth_slider.setValue(depth)
        self.setting_depth_label.setText(str(depth))
        
        self._is_loading = False # 释放互锁，主逻辑恢复即时存盘状态

    def _popup_custom_model_dialog(self):
        dlg = CustomModelDialog(self)
        if self.window():
            dlg.move(self.window().geometry().x() + 30, self.window().geometry().y() + 150)
        dlg.saved.connect(self._handle_new_custom_model)
        dlg.show()

    def _handle_new_custom_model(self, model_info):
        nick = model_info["nickname"]
        self.custom_models[nick] = {
            "base_url": model_info["base_url"],
            "api_key": model_info["api_key"],
            "model_id": model_info["model_id"]
        }
        self.setting_model.addItem(nick)
        self.setting_model.setCurrentText(nick)
        # 🌟 即时生效：新增外部算力模型完成时，自动触发一次全局保存
        self._trigger_immediate_save()

    def _trigger_immediate_save(self):
        """🌟 核心即时保存网关：只要表单状态发生任何位移，立刻打包数据同步给大脑大管家"""
        if self._is_loading: 
            return
            
        payload = {
            "model": self.setting_model.currentText(),
            "privacy": self.setting_privacy.isChecked(),
            "dark_mode": self.setting_theme_dark.isChecked(),
            "topmost": self.setting_topmost.isChecked(),
            "custom_models": self.custom_models,
            "thinking_depth": self.setting_depth_slider.value()
        }
        self.settings_saved.emit(payload)


class _KBIngestThread(QThread):
    """后台入库线程，避免阻塞 UI。"""
    progress = Signal(int, int, str, str, str)  # current, total, fname, status, detail
    finished = Signal(dict)

    def __init__(self, kb_handler, file_paths, use_worker=True):
        super().__init__()
        self._handler = kb_handler
        self._file_paths = file_paths
        self._use_worker = use_worker

    def run(self):
        try:
            if self._use_worker:
                # 通过 Worker 管理接口
                result = self._handler.add_to_knowledge_base(
                    self._file_paths,
                    progress_callback=lambda c, t, f, s, d: self.progress.emit(c, t, f, s, d)
                )
                self.finished.emit(result)
            else:
                # 直接用 KnowledgeBase 实例
                result = self._handler.add_documents(
                    self._file_paths,
                    progress_callback=lambda c, t, f, s, d: self.progress.emit(c, t, f, s, d)
                )
                self.finished.emit(result)
        except Exception as e:
            self.finished.emit({"error": f"{type(e).__name__}: {e}", "available": False})