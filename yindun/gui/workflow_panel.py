# -*- coding: utf-8 -*-
"""
隐盾协同工作流编排面板 - P1 第8项
核心能力：模板化多步骤任务编排、强制审批链、上下文传递、进度可视化
"""
import json
import os
from datetime import datetime
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QTableWidget, QTableWidgetItem, QTextEdit, QComboBox,
    QSplitter, QTreeWidget, QTreeWidgetItem,
    QProgressBar, QHeaderView, QWidget,
    QGroupBox, QGridLayout, QSizePolicy, QDialog, QListWidget,
    QListWidgetItem, QInputDialog, QMessageBox, QTabWidget,
    QScrollArea, QSpacerItem
)
from PySide6.QtCore import Qt, Signal, QTimer, QRect, QPoint, QSize
from PySide6.QtGui import QColor, QFont, QBrush, QCursor, QPainter, QPen, QPixmap, QPaintEvent
from yindun.core.workflow import (
    WorkflowEngine, WorkflowStep, StepStatus, ApprovalType,
    get_workflow_engine
)


STATUS_ICONS = {
    StepStatus.PENDING: "⏳",
    StepStatus.WAITING_APPROVAL: "🔒",
    StepStatus.APPROVED: "✅",
    StepStatus.EXECUTING: "🔄",
    StepStatus.COMPLETED: "✅",
    StepStatus.FAILED: "❌",
    StepStatus.SKIPPED: "⏭",
}

class StyledTemplateDialog(QDialog):
    """自定义新建工作流对话框 - 模板选择 + 自定义命名一步完成"""
    def __init__(self, parent=None, templates=None):
        super().__init__(parent)
        self.setWindowTitle("新建工作流")
        self.setFixedSize(400, 460)
        self.setObjectName("wfStyledDialog")
        self._selected_id = None
        self._custom_name = ""
        self._templates = templates or []
        self._initialized = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title_lbl = QLabel("✨ 新建工作流")
        title_lbl.setStyleSheet("color: #1e293b; font-size: 15px; font-weight: bold;")
        layout.addWidget(title_lbl)

        step1_lbl = QLabel("① 选择模板：")
        step1_lbl.setStyleSheet("color: #475569; font-size: 12px; font-weight: 600;")
        layout.addWidget(step1_lbl)

        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget {
                background: #ffffff; border: 1px solid #e2e8f0;
                border-radius: 6px; font-size: 13px; color: #1e293b;
            }
            QListWidget::item {
                padding: 8px 10px; color: #1e293b; border-bottom: 1px solid #f1f5f9;
            }
            QListWidget::item:selected {
                background: #8b5cf6; color: #ffffff;
            }
            QListWidget::item:hover {
                background: #ede9fe; color: #1e293b;
            }
            QListWidget::item:selected:hover {
                background: #7c3aed; color: #ffffff;
            }
        """)
        self._list.setCursor(QCursor(Qt.PointingHandCursor))
        self._list.setMinimumHeight(180)
        for t in self._templates:
            item = QListWidgetItem(f"  📋 {t['name']}")
            item.setData(Qt.UserRole, t['id'])
            item.setData(Qt.UserRole + 1, t['name'])
            item.setToolTip(t.get('description', ''))
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        layout.addWidget(self._list)

        self._desc_lbl = QLabel("")
        self._desc_lbl.setStyleSheet("color: #64748b; font-size: 11px; padding: 0 2px;")
        self._desc_lbl.setWordWrap(True)
        layout.addWidget(self._desc_lbl)

        step2_lbl = QLabel("② 工作流名称（可修改）：")
        step2_lbl.setStyleSheet("color: #475569; font-size: 12px; font-weight: 600; margin-top: 4px;")
        layout.addWidget(step2_lbl)

        self._name_edit = QLineEdit()
        self._name_edit.setStyleSheet("""
            QLineEdit {
                background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px;
                padding: 8px 10px; font-size: 13px; color: #1e293b;
            }
            QLineEdit:focus { border: 1px solid #8b5cf6; }
        """)
        self._name_edit.setPlaceholderText("请输入工作流名称，留空使用模板默认名")
        layout.addWidget(self._name_edit)

        tip_lbl = QLabel("💡 切换模板会自动填入默认名称，可自由修改")
        tip_lbl.setStyleSheet("color: #94a3b8; font-size: 11px; padding: 0 2px;")
        layout.addWidget(tip_lbl)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#475569;border:none;border-radius:6px;"
            "padding:7px 18px;font-weight:600;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        self._ok_btn = QPushButton("✅ 创建")
        self._ok_btn.setStyleSheet(
            "QPushButton{background:#8b5cf6;color:white;border:none;border-radius:6px;"
            "padding:7px 20px;font-weight:600;}"
            "QPushButton:hover{background:#7c3aed;}"
        )
        self._ok_btn.clicked.connect(self._accept_selected)
        btn_row.addWidget(self._ok_btn)

        layout.addLayout(btn_row)
        self._list.itemDoubleClicked.connect(lambda _i: self._accept_selected())

        # 所有控件创建完成后再连接信号和初始化
        self._list.itemSelectionChanged.connect(self._on_selection_changed)
        self._on_selection_changed()
        self._initialized = True

    def _on_selection_changed(self):
        # 初始化期间不响应信号
        if not self._initialized and not hasattr(self, '_ok_btn'):
            return
        item = self._list.currentItem()
        if item:
            tid = item.data(Qt.UserRole)
            default_name = item.data(Qt.UserRole + 1) or ""
            for t in self._templates:
                if t['id'] == tid:
                    self._desc_lbl.setText(f"📝 {t.get('description', '')}")
                    break
            # 如果名称输入框当前是空的或等于旧模板默认名，则自动更新为新模板默认名
            current_text = self._name_edit.text().strip() if hasattr(self, '_name_edit') else ""
            if not current_text:
                self._name_edit.setText(default_name)
            else:
                # 判断当前文本是不是上一个模板的默认名
                prev_default = ""
                for t in self._templates:
                    if t['name'] == current_text:
                        prev_default = t['name']
                        break
                if prev_default:
                    self._name_edit.setText(default_name)
            self._name_edit.selectAll()
            self._name_edit.setFocus()
            self._ok_btn.setEnabled(True)
        else:
            self._desc_lbl.setText("")
            self._ok_btn.setEnabled(False)

    def _accept_selected(self):
        item = self._list.currentItem()
        if not item and self._list.count() > 0:
            self._list.setCurrentRow(0)
            item = self._list.item(0)
        if item:
            self._selected_id = item.data(Qt.UserRole)
            self._custom_name = self._name_edit.text().strip()
            self.accept()

    def get_selected_id(self):
        return self._selected_id

    def get_custom_name(self):
        return self._custom_name


class StyledCommentDialog(QDialog):
    """自定义审批意见输入对话框"""
    def __init__(self, parent=None, title="审批", prompt="请输入审批意见："):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setFixedSize(380, 200)
        self.setObjectName("wfStyledDialog")
        self._comment = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title_lbl = QLabel(f"📝 {title}")
        title_lbl.setStyleSheet("color: #1e293b; font-size: 14px; font-weight: bold;")
        layout.addWidget(title_lbl)

        prompt_lbl = QLabel(prompt)
        prompt_lbl.setStyleSheet("color: #475569; font-size: 12px;")
        prompt_lbl.setWordWrap(True)
        layout.addWidget(prompt_lbl)

        self._input = QLineEdit()
        self._input.setStyleSheet(
            "QLineEdit{background:#ffffff;color:#1e293b;border:1px solid #e2e8f0;"
            "border-radius:6px;padding:8px 10px;font-size:13px;}"
            "QLineEdit:focus{border:1px solid #8b5cf6;}"
        )
        self._input.setPlaceholderText("审批意见（可选）")
        layout.addWidget(self._input)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#475569;border:none;border-radius:6px;"
            "padding:6px 16px;font-weight:600;}"
            "QPushButton:hover{background:#e2e8f0;}"
        )
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        ok_btn = QPushButton("确定")
        ok_btn.setStyleSheet(
            "QPushButton{background:#3b82f6;color:white;border:none;border-radius:6px;"
            "padding:6px 16px;font-weight:600;}"
            "QPushButton:hover{background:#2563eb;}"
        )
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(ok_btn)

        layout.addLayout(btn_row)

    def get_comment(self):
        return self._input.text().strip()


class StyledConfirmDialog(QMessageBox):
    """自定义确认对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            "QMessageBox{background:#ffffff;color:#1e293b;}"
            "QLabel{color:#1e293b;font-size:13px;min-width:280px;}"
            "QPushButton{background:#ef4444;color:white;border:none;border-radius:6px;"
            "padding:6px 18px;font-weight:600;min-width:80px;}"
            "QPushButton:hover{background:#dc2626;}"
            "QPushButton#qt_msgbox_buttonbox{background:#64748b;}"
        )


STATUS_TEXT = {
    "pending": "待执行",
    "waiting_approval": "等待审批",
    "approved": "已审批",
    "executing": "执行中",
    "completed": "已完成",
    "failed": "失败",
    "skipped": "已跳过",
}

STATUS_COLORS = {
    StepStatus.PENDING: "#94a3b8",
    StepStatus.WAITING_APPROVAL: "#f59e0b",
    StepStatus.APPROVED: "#3b82f6",
    StepStatus.EXECUTING: "#06b6d4",
    StepStatus.COMPLETED: "#22c55e",
    StepStatus.FAILED: "#ef4444",
    StepStatus.SKIPPED: "#64748b",
}


class WorkflowPanel(QFrame):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("workflowPanel")
        self._engine = get_workflow_engine()
        self._current_instance_id = None
        self._dark_mode = False
        self._init_ui()
        self._refresh_templates()
        self._start_refresh_timer()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 记录当前激活的 step_id（刷新时自动恢复选中）
        self._step_cards = {}
        self._current_step_id = None
        self._current_step_name = ""

        top_bar = QFrame()
        top_bar.setObjectName("wfTopBar")
        top_bar.setFixedHeight(44)
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(12, 0, 12, 0)
        tb_layout.setSpacing(8)

        back_btn = QPushButton("← 返回")
        back_btn.setObjectName("wfBackBtn")
        back_btn.setCursor(QCursor(Qt.PointingHandCursor))
        back_btn.clicked.connect(self.back_requested.emit)
        tb_layout.addWidget(back_btn)

        title = QLabel("🔀 协同工作流编排")
        title.setObjectName("wfTitle")
        tb_layout.addWidget(title)
        tb_layout.addStretch()

        self._new_wf_btn = QPushButton("➕ 新建工作流")
        self._new_wf_btn.setObjectName("wfNewBtn")
        self._new_wf_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._new_wf_btn.clicked.connect(self._on_new_workflow)
        tb_layout.addWidget(self._new_wf_btn)

        self._refresh_btn = QPushButton("🔄 刷新")
        self._refresh_btn.setObjectName("wfRefreshBtn")
        self._refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._refresh_btn.clicked.connect(self._refresh_all)
        tb_layout.addWidget(self._refresh_btn)

        main_layout.addWidget(top_bar)

        # ============ 顶部工作流总览横幅 ============
        self._summary_banner = QFrame()
        self._summary_banner.setObjectName("wfSummaryBanner")
        self._summary_banner.setFixedHeight(72)
        self._summary_banner.setStyleSheet("""
            QFrame#wfSummaryBanner {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #7c3aed, stop:1 #06b6d4);
                border-radius: 10px;
            }
        """)
        sb_layout = QHBoxLayout(self._summary_banner)
        sb_layout.setContentsMargins(18, 10, 18, 10)
        sb_layout.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.setSpacing(2)
        self._banner_title = QLabel("🚀 请选择一个工作流模板开始")
        self._banner_title.setStyleSheet("color: white; font-size: 15px; font-weight: bold;")
        left_col.addWidget(self._banner_title)
        self._banner_subtitle = QLabel("点击左侧模板或右上角「➕ 新建工作流」")
        self._banner_subtitle.setStyleSheet("color: rgba(255,255,255,0.85); font-size: 12px;")
        left_col.addWidget(self._banner_subtitle)
        sb_layout.addLayout(left_col, 3)

        right_col = QVBoxLayout()
        right_col.setSpacing(2)
        self._banner_progress = QLabel("0%")
        self._banner_progress.setStyleSheet("color: white; font-size: 22px; font-weight: bold;")
        self._banner_progress.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right_col.addWidget(self._banner_progress)
        self._banner_hint = QLabel("总进度")
        self._banner_hint.setStyleSheet("color: rgba(255,255,255,0.8); font-size: 11px;")
        self._banner_hint.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        right_col.addWidget(self._banner_hint)
        sb_layout.addLayout(right_col, 1)

        main_layout.addWidget(self._summary_banner)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("wfSplitter")

        left_panel = QFrame()
        left_panel.setObjectName("wfLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(4)

        templates_group = QGroupBox("📋 可用模板")
        tg_layout = QVBoxLayout(templates_group)
        self._template_list = QListWidget()
        self._template_list.setObjectName("wfTemplateList")
        self._template_list.itemClicked.connect(self._on_template_selected)
        tg_layout.addWidget(self._template_list)
        left_layout.addWidget(templates_group)

        instances_group = QGroupBox("🗂️ 已创建的工作流")
        ig_layout = QVBoxLayout(instances_group)
        self._instances_list = QListWidget()
        self._instances_list.setObjectName("wfInstancesList")
        self._instances_list.itemClicked.connect(self._on_instance_selected)
        ig_layout.addWidget(self._instances_list)
        left_layout.addWidget(instances_group, 1)

        progress_group = QGroupBox("📊 执行进度")
        pg_layout = QVBoxLayout(progress_group)
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("wfProgressBar")
        self._progress_bar.setValue(0)
        self._progress_bar.setFormat("%v%")
        pg_layout.addWidget(self._progress_bar)
        self._progress_label = QLabel("选择模板以开始")
        self._progress_label.setObjectName("wfProgressLabel")
        pg_layout.addWidget(self._progress_label)
        left_layout.addWidget(progress_group)

        left_layout.addStretch()
        splitter.addWidget(left_panel)
        splitter.setStretchFactor(0, 1)

        center_panel = QFrame()
        center_panel.setObjectName("wfCenterPanel")
        center_layout = QVBoxLayout(center_panel)
        center_layout.setContentsMargins(4, 4, 4, 4)
        center_layout.setSpacing(4)

        steps_group = QGroupBox("🔗 步骤流程编排")
        sg_layout = QVBoxLayout(steps_group)
        sg_layout.setSpacing(6)

        self._steps_scroll = QScrollArea()
        self._steps_scroll.setWidgetResizable(True)
        self._steps_scroll.setObjectName("wfStepsScroll")
        self._steps_container = QWidget()
        self._steps_container.setObjectName("wfStepsContainer")
        self._steps_inner_layout = QVBoxLayout(self._steps_container)
        self._steps_inner_layout.setContentsMargins(8, 8, 8, 8)
        self._steps_inner_layout.setSpacing(0)
        self._steps_inner_layout.addStretch()
        self._steps_scroll.setWidget(self._steps_container)
        sg_layout.addWidget(self._steps_scroll, 1)

        btn_row = QHBoxLayout()
        self._execute_btn = QPushButton("▶ 执行步骤")
        self._execute_btn.setObjectName("wfExecuteBtn")
        self._execute_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._execute_btn.clicked.connect(self._on_execute_step)
        self._execute_btn.setEnabled(False)
        btn_row.addWidget(self._execute_btn)

        self._approve_btn = QPushButton("✅ 审批通过")
        self._approve_btn.setObjectName("wfApproveBtn")
        self._approve_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._approve_btn.clicked.connect(self._on_approve_step)
        self._approve_btn.setEnabled(False)
        btn_row.addWidget(self._approve_btn)

        self._reject_btn = QPushButton("❌ 拒绝")
        self._reject_btn.setObjectName("wfRejectBtn")
        self._reject_btn.setCursor(QCursor(Qt.PointingHandCursor))
        self._reject_btn.clicked.connect(self._on_reject_step)
        self._reject_btn.setEnabled(False)
        btn_row.addWidget(self._reject_btn)

        btn_row.addStretch()
        sg_layout.addLayout(btn_row)
        center_layout.addWidget(steps_group, 1)

        splitter.addWidget(center_panel)
        splitter.setStretchFactor(1, 3)

        # ============ 右侧：格式化步骤详情 ============
        right_panel = QFrame()
        right_panel.setObjectName("wfRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)
        right_layout.setSpacing(4)

        detail_group = QGroupBox("📝 步骤详情")
        dg_layout = QVBoxLayout(detail_group)
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setObjectName("wfDetailScroll")
        self._detail_widget = QWidget()
        self._detail_widget.setObjectName("wfDetailWidget")
        self._detail_layout = QVBoxLayout(self._detail_widget)
        self._detail_layout.setContentsMargins(12, 12, 12, 12)
        self._detail_layout.setSpacing(8)
        self._detail_layout.addStretch()
        self._detail_scroll.setWidget(self._detail_widget)
        dg_layout.addWidget(self._detail_scroll, 1)
        right_layout.addWidget(detail_group, 1)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(2, 2)

        main_layout.addWidget(splitter, 1)

        bottom_bar = QFrame()
        bottom_bar.setObjectName("wfBottomBar")
        bottom_bar.setFixedHeight(32)
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(12, 0, 12, 0)
        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("wfStatusLabel")
        bb_layout.addWidget(self._status_label)
        bb_layout.addStretch()
        main_layout.addWidget(bottom_bar)

        # 步骤卡片点击通过每个卡片的点击信号来处理

        # 调试：确保所有按钮一定是可用状态（防止样式覆盖或意外置disabled）
        for btn in [self._new_wf_btn, self._refresh_btn, self._execute_btn,
                    self._approve_btn, self._reject_btn]:
            btn.setEnabled(True)
            btn.setCursor(QCursor(Qt.PointingHandCursor))
            btn.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        self.set_dark_mode(False)

    def _start_refresh_timer(self):
        self._timer = QTimer()
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._refresh_current)
        self._timer.start()

    def _refresh_templates(self):
        self._template_list.clear()
        templates = self._engine.list_templates()
        for t in templates:
            item = QListWidgetItem(f"  📋 {t['name']}")
            item.setData(Qt.UserRole, t['id'])
            item.setToolTip(t['description'])
            self._template_list.addItem(item)

    def _on_template_selected(self, item):
        template_id = item.data(Qt.UserRole)
        template_name = item.text().strip().replace("📋 ", "").strip()
        instance = self._engine.create_instance(template_id)
        if instance:
            self._current_instance_id = instance.template_id
            self._refresh_current()
            self._status_label.setText(
                f"✅ 已创建工作流: {instance.name}"
            )
            self._status_label.setStyleSheet("color: #22c55e; font-weight: bold;")

    def _refresh_current(self):
        if not self._current_instance_id:
            # 仍然刷新实例列表，确保空状态下左侧列表正确
            self._refresh_instances()
            return

        status = self._engine.get_workflow_status(self._current_instance_id)
        if "error" in status:
            return

        self._populate_steps(status)
        self._update_progress(status)
        # 关键修复：刷新后自动激活当前可执行步骤，避免3秒一次的定时器让按钮反复变灰
        self._activate_current_step(status)
        # 同时刷新左侧实例列表（进度变化时可见）
        self._refresh_instances()

    def _activate_current_step(self, status: dict):
        """找到当前可操作步骤，自动选中并启用对应按钮"""
        current_step_dict = status.get("current_step")
        target_id = None
        target_name = None
        target_status = None

        # 1. 优先：如果 _current_step_id 仍然存在且状态合法，保持用户的选择
        if self._current_step_id:
            for step in status['steps']:
                if step['step_id'] == self._current_step_id:
                    target_id = step['step_id']
                    target_name = step['name']
                    target_status = StepStatus(step['status'])
                    break

        # 2. 否则使用 engine 返回的 current_step（下一步可执行/待审批）
        if target_id is None and current_step_dict:
            target_id = current_step_dict['step_id']
            target_name = current_step_dict['name']
            target_status = StepStatus(current_step_dict['status'])

        # 3. 再否则取第一个 PENDING / WAITING_APPROVAL / APPROVED
        if target_id is None:
            for step in status['steps']:
                s = StepStatus(step['status'])
                if s in (StepStatus.PENDING, StepStatus.WAITING_APPROVAL,
                         StepStatus.APPROVED, StepStatus.EXECUTING):
                    target_id = step['step_id']
                    target_name = step['name']
                    target_status = s
                    break

        if target_id:
            self._current_step_id = target_id
            self._current_step_name = target_name or ""
            self._highlight_step_card(target_id)
            self._show_step_detail(target_id)
            self._set_btn_state(target_status)
        else:
            # 没有可操作步骤，按钮全灰
            self._execute_btn.setEnabled(False)
            self._approve_btn.setEnabled(False)
            self._reject_btn.setEnabled(False)

    def _set_btn_state(self, step_status: StepStatus):
        """根据步骤状态统一设置三个按钮的启用/禁用状态"""
        if step_status is None:
            self._execute_btn.setEnabled(False)
            self._approve_btn.setEnabled(False)
            self._reject_btn.setEnabled(False)
            return
        # PENDING / APPROVED / WAITING_APPROVAL 都可点击执行
        # (WAITING_APPROVAL 时执行会提示用户先审批，避免用户困惑)
        self._execute_btn.setEnabled(
            step_status in (StepStatus.PENDING, StepStatus.APPROVED,
                            StepStatus.WAITING_APPROVAL)
        )
        # 仅 WAITING_APPROVAL：显示审批/拒绝按钮
        self._approve_btn.setEnabled(step_status == StepStatus.WAITING_APPROVAL)
        self._reject_btn.setEnabled(step_status == StepStatus.WAITING_APPROVAL)

    def _clear_steps_container(self):
        """清空步骤容器"""
        while self._steps_inner_layout.count() > 0:
            item = self._steps_inner_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()
        self._step_cards = {}

    def _create_step_card(self, index: int, step: dict) -> QFrame:
        """创建一个步骤卡片"""
        status_val = step['status']
        status_enum = StepStatus(status_val)
        icon = STATUS_ICONS.get(status_enum, "❓")
        color = STATUS_COLORS.get(status_enum, "#94a3b8")
        status_text = STATUS_TEXT.get(status_val, status_val)
        step_id = step['step_id']

        approval_type = step.get('approval_type', 'auto')
        if approval_type == 'manual':
            approval_text = '🔒 人工审批'
            approval_color = "#f59e0b"
        else:
            approval_text = '⚡ 自动'
            approval_color = "#06b6d4"

        card = QFrame()
        card.setObjectName(f"stepCard_{step_id}")
        card.setCursor(QCursor(Qt.PointingHandCursor))

        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(10)

        # 左侧步骤号圆形
        circle = QLabel(f"{index + 1}")
        circle.setObjectName("stepCircle")
        circle.setAlignment(Qt.AlignCenter)
        circle.setFixedSize(QSize(32, 32))
        circle.setStyleSheet(f"""
            QLabel#stepCircle {{
                background-color: {color};
                color: white;
                border-radius: 16px;
                font-weight: bold;
                font-size: 13px;
            }}
        """)
        card_layout.addWidget(circle)

        # 中间信息区
        info_col = QVBoxLayout()
        info_col.setSpacing(2)

        name_row = QHBoxLayout()
        name_row.setSpacing(6)
        icon_lbl = QLabel(icon)
        icon_lbl.setFixedWidth(20)
        icon_lbl.setStyleSheet("font-size: 14px;")
        name_row.addWidget(icon_lbl)
        name_lbl = QLabel(step['name'])
        name_lbl.setObjectName("stepNameLabel")
        name_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b;")
        name_row.addWidget(name_lbl)
        name_row.addStretch()
        info_col.addLayout(name_row)

        if step.get('description'):
            desc_lbl = QLabel(step['description'])
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet("font-size: 12px; color: #64748b;")
            info_col.addWidget(desc_lbl)

        card_layout.addLayout(info_col, 1)

        # 右侧状态 & 审批标签
        right_col = QVBoxLayout()
        right_col.setSpacing(4)

        status_badge = QLabel(status_text)
        status_badge.setAlignment(Qt.AlignCenter)
        status_badge.setFixedHeight(22)
        status_badge.setStyleSheet(f"""
            background-color: {color}; color: white;
            border-radius: 11px; padding: 0 10px;
            font-size: 11px; font-weight: bold;
        """)
        right_col.addWidget(status_badge)

        approval_badge = QLabel(approval_text)
        approval_badge.setAlignment(Qt.AlignCenter)
        approval_badge.setFixedHeight(20)
        approval_badge.setStyleSheet(f"""
            background-color: {approval_color}; color: white;
            border-radius: 10px; padding: 0 8px;
            font-size: 10px; font-weight: bold;
        """)
        right_col.addWidget(approval_badge)
        right_col.addStretch()

        card_layout.addLayout(right_col)

        # 存储 step_id 用于点击处理
        card._step_id = step_id
        card._step_data = step

        # 点击事件
        card.mousePressEvent = lambda event, sid=step_id: self._on_step_card_clicked(sid)

        return card

    def _create_connector(self, is_last: bool = False) -> QFrame:
        """创建步骤之间的连接线"""
        connector = QFrame()
        connector.setFixedHeight(20)
        layout = QVBoxLayout(connector)
        layout.setContentsMargins(26, 0, 0, 0)
        layout.setSpacing(0)

        line = QFrame()
        line.setFixedWidth(2)
        line.setFixedHeight(16)
        line.setStyleSheet("background-color: #cbd5e1;")
        line_layout = QHBoxLayout()
        line_layout.setContentsMargins(0, 0, 0, 0)
        line_layout.addWidget(line)
        layout.addLayout(line_layout)
        return connector

    def _populate_steps(self, status: dict):
        """以可视化时间线方式填充步骤卡片"""
        self._current_steps = status['steps']
        self._clear_steps_container()

        card_style = """
            QFrame#stepCard {
                background-color: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 10px;
            }
            QFrame#stepCard:hover {
                background-color: #eff6ff;
                border: 2px solid #3b82f6;
            }
        """

        steps = status['steps']
        for i, step in enumerate(steps):
            card = self._create_step_card(i, step)
            card.setObjectName("stepCard")
            card.setStyleSheet(card_style)
            self._steps_inner_layout.addWidget(card)
            self._step_cards[step['step_id']] = card

            if i < len(steps) - 1:
                connector = self._create_connector()
                self._steps_inner_layout.addWidget(connector)

        # 末尾添加弹性空间
        self._steps_inner_layout.addStretch()

    def _update_progress(self, status: dict):
        progress = status['progress']
        self._progress_bar.setValue(int(progress['percentage']))

        if status['is_complete']:
            self._progress_label.setText(f"✅ 工作流完成！ ({progress['completed']}/{progress['total']})")
        elif status['has_failed']:
            self._progress_label.setText(f"❌ 有步骤失败 ({progress['completed']}/{progress['total']})")
        elif status['current_step']:
            current = status['current_step']
            s = current['status']
            # 修复 pending 语义错误：pending 不是"正在处理"，而是"待执行"
            if s == 'pending':
                step_txt = f"⏳ 待执行: {current['name']}"
            elif s == 'waiting_approval':
                step_txt = f"🔒 等待审批: {current['name']}"
            elif s == 'executing':
                step_txt = f"🔄 正在执行: {current['name']}"
            elif s == 'approved':
                step_txt = f"✅ 已审批: {current['name']}"
            else:
                step_txt = f"{current['name']} [{s}]"
            self._progress_label.setText(
                f"{step_txt}  ({progress['completed']}/{progress['total']})"
            )
        else:
            self._progress_label.setText(f"等待开始 ({progress['completed']}/{progress['total']})")

        if status['is_complete']:
            self._banner_title.setText("🎉 工作流已完成")
            self._banner_subtitle.setText("所有步骤均已执行完毕")
        elif status['has_failed']:
            self._banner_title.setText("⚠️ 工作流有步骤失败")
            self._banner_subtitle.setText("请查看失败步骤并修复")
        elif status['current_step']:
            current = status['current_step']
            self._banner_title.setText(f"🚀 {current['name']}")
            self._banner_subtitle.setText(f"状态: {STATUS_TEXT.get(current['status'], current['status'])}")
        else:
            self._banner_title.setText("📋 工作流待开始")
            self._banner_subtitle.setText("选择步骤或点击执行开始")
        self._banner_progress.setText(f"{int(progress['percentage'])}%")

    def _on_step_card_clicked(self, step_id: str):
        self._current_step_id = step_id
        for step in self._current_steps:
            if step['step_id'] == step_id:
                self._current_step_name = step['name']
                status = StepStatus(step['status'])
                self._set_btn_state(status)
                break
        self._highlight_step_card(step_id)
        self._show_step_detail(step_id)

    def _highlight_step_card(self, step_id: str):
        for sid, card in self._step_cards.items():
            if sid == step_id:
                card.setStyleSheet("""
                    QFrame#stepCard {
                        background-color: #eff6ff;
                        border: 2px solid #3b82f6;
                        border-radius: 10px;
                    }
                """)
            else:
                card.setStyleSheet("""
                    QFrame#stepCard {
                        background-color: #f8fafc;
                        border: 1px solid #e2e8f0;
                        border-radius: 10px;
                    }
                    QFrame#stepCard:hover {
                        background-color: #eff6ff;
                        border: 2px solid #3b82f6;
                    }
                """)

    def _show_step_detail(self, step_id: str):
        while self._detail_layout.count() > 0:
            item = self._detail_layout.takeAt(0)
            if item and item.widget():
                item.widget().deleteLater()

        if not hasattr(self, '_current_steps') or not self._current_steps:
            return

        step_data = None
        for step in self._current_steps:
            if step['step_id'] == step_id:
                step_data = step
                break

        if not step_data:
            return

        status_val = step_data['status']
        status_enum = StepStatus(status_val)
        color = STATUS_COLORS.get(status_enum, "#94a3b8")
        status_text = STATUS_TEXT.get(status_val, status_val)
        icon = STATUS_ICONS.get(status_enum, "❓")
        step_name = step_data.get('name', '未知步骤')

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_lbl = QLabel(f"{icon} {step_name}")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        title_row.addWidget(title_lbl)
        title_row.addStretch()
        status_badge = QLabel(status_text)
        status_badge.setAlignment(Qt.AlignCenter)
        status_badge.setFixedHeight(24)
        status_badge.setStyleSheet(f"background-color: {color}; color: white; border-radius: 12px; padding: 0 12px; font-size: 12px; font-weight: bold;")
        title_row.addWidget(status_badge)
        title_widget = QWidget()
        title_widget.setLayout(title_row)
        self._detail_layout.addWidget(title_widget)

        basic_box = QGroupBox("📋 基本信息")
        basic_layout = QGridLayout(basic_box)
        basic_layout.setHorizontalSpacing(12)
        basic_layout.setVerticalSpacing(6)
        basic_layout.setContentsMargins(10, 14, 10, 10)

        name_lbl = QLabel("名称")
        name_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        name_val = QLabel(step_name)
        name_val.setStyleSheet("color: #1e293b; font-size: 13px; font-weight: 600;")
        basic_layout.addWidget(name_lbl, 0, 0)
        basic_layout.addWidget(name_val, 0, 1)

        desc_text = step_data.get('description', '')
        desc_lbl = QLabel("描述")
        desc_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        desc_val = QLabel(desc_text if desc_text else '无')
        desc_val.setWordWrap(True)
        desc_val.setStyleSheet("color: #1e293b; font-size: 12px;")
        basic_layout.addWidget(desc_lbl, 1, 0)
        basic_layout.addWidget(desc_val, 1, 1)

        approval_type = step_data.get('approval_type', 'auto')
        approval_text = '🔒 人工审批' if approval_type == 'manual' else '⚡ 自动'
        approval_lbl = QLabel("审批方式")
        approval_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        approval_val = QLabel(approval_text)
        approval_val.setStyleSheet("color: #1e293b; font-size: 12px;")
        basic_layout.addWidget(approval_lbl, 2, 0)
        basic_layout.addWidget(approval_val, 2, 1)

        status_lbl = QLabel("状态")
        status_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
        status_val_lbl = QLabel(status_text)
        status_val_lbl.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: bold;")
        basic_layout.addWidget(status_lbl, 3, 0)
        basic_layout.addWidget(status_val_lbl, 3, 1)

        self._detail_layout.addWidget(basic_box)

        tool_name = step_data.get('tool_name', '')
        tool_args = step_data.get('tool_args', {})
        if tool_name or tool_args:
            tool_box = QGroupBox("🔧 工具信息")
            tool_layout = QGridLayout(tool_box)
            tool_layout.setHorizontalSpacing(12)
            tool_layout.setVerticalSpacing(6)
            tool_layout.setContentsMargins(10, 14, 10, 10)

            tname_lbl = QLabel("工具名称")
            tname_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
            tname_val = QLabel(tool_name or '无')
            tname_val.setStyleSheet("color: #1e293b; font-size: 13px; font-weight: 600;")
            tool_layout.addWidget(tname_lbl, 0, 0)
            tool_layout.addWidget(tname_val, 0, 1)

            if tool_args:
                targs_lbl = QLabel("工具参数")
                targs_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
                if isinstance(tool_args, dict):
                    args_text = '\n'.join(f'{k}: {v}' for k, v in tool_args.items())
                else:
                    args_text = str(tool_args)
                targs_val = QLabel(args_text)
                targs_val.setStyleSheet("color: #334155; font-family: Consolas, 'Microsoft YaHei UI', monospace; font-size: 12px;")
                targs_val.setWordWrap(True)
                tool_layout.addWidget(targs_lbl, 1, 0, alignment=Qt.AlignTop)
                tool_layout.addWidget(targs_val, 1, 1)

            self._detail_layout.addWidget(tool_box)

        result = step_data.get('result')
        error = step_data.get('error')
        if result or error or status_enum in (StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.EXECUTING):
            result_box = QGroupBox("📊 执行结果")
            result_layout = QVBoxLayout(result_box)
            result_layout.setContentsMargins(10, 14, 10, 10)
            result_layout.setSpacing(6)

            if error:
                err_lbl = QLabel(f"❌ 错误: {error}")
                err_lbl.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: 600;")
                err_lbl.setWordWrap(True)
                result_layout.addWidget(err_lbl)

            if result:
                result_text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
                result_lbl = QLabel(result_text)
                result_lbl.setStyleSheet("color: #334155; font-family: Consolas, 'Microsoft YaHei UI', monospace; font-size: 12px;")
                result_lbl.setWordWrap(True)
                result_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
                result_layout.addWidget(result_lbl)

            if not result and not error:
                pending_lbl = QLabel("⏳ 等待执行...")
                pending_lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
                result_layout.addWidget(pending_lbl)

            self._detail_layout.addWidget(result_box)

        reviewer = step_data.get('reviewer')
        review_comment = step_data.get('review_comment')
        if reviewer or review_comment or status_enum == StepStatus.WAITING_APPROVAL or status_enum == StepStatus.APPROVED:
            review_box = QGroupBox("🔍 审批信息")
            review_layout = QGridLayout(review_box)
            review_layout.setHorizontalSpacing(12)
            review_layout.setVerticalSpacing(6)
            review_layout.setContentsMargins(10, 14, 10, 10)

            if reviewer:
                rv_lbl = QLabel("审批人")
                rv_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
                rv_val = QLabel(reviewer)
                rv_val.setStyleSheet("color: #1e293b; font-size: 12px;")
                review_layout.addWidget(rv_lbl, 0, 0)
                review_layout.addWidget(rv_val, 0, 1)

            if review_comment:
                rc_lbl = QLabel("审批意见")
                rc_lbl.setStyleSheet("color: #64748b; font-size: 12px;")
                rc_val = QLabel(review_comment)
                rc_val.setWordWrap(True)
                rc_val.setStyleSheet("color: #1e293b; font-size: 12px;")
                review_layout.addWidget(rc_lbl, 1, 0)
                review_layout.addWidget(rc_val, 1, 1)

            if not reviewer and not review_comment:
                if status_enum == StepStatus.WAITING_APPROVAL:
                    waiting_lbl = QLabel("⏳ 等待人工审批...")
                    waiting_lbl.setStyleSheet("color: #f59e0b; font-size: 12px;")
                    review_layout.addWidget(waiting_lbl, 0, 0, 1, 2)
                elif approval_type == 'auto':
                    auto_lbl = QLabel("⚡ 自动审批（无需人工干预）")
                    auto_lbl.setStyleSheet("color: #06b6d4; font-size: 12px;")
                    review_layout.addWidget(auto_lbl, 0, 0, 1, 2)

            self._detail_layout.addWidget(review_box)

        time_box = QGroupBox("⏰ 时间信息")
        time_layout = QGridLayout(time_box)
        time_layout.setHorizontalSpacing(12)
        time_layout.setVerticalSpacing(6)
        time_layout.setContentsMargins(10, 14, 10, 10)

        created_at = step_data.get('created_at', '')
        updated_at = step_data.get('updated_at', '')
        started_at = step_data.get('started_at', '')
        completed_at = step_data.get('completed_at', '')

        row = 0
        if created_at:
            clbl = QLabel("创建时间")
            clbl.setStyleSheet("color: #64748b; font-size: 12px;")
            cval = QLabel(str(created_at))
            cval.setStyleSheet("color: #1e293b; font-size: 12px;")
            time_layout.addWidget(clbl, row, 0)
            time_layout.addWidget(cval, row, 1)
            row += 1

        if started_at:
            slbl = QLabel("开始时间")
            slbl.setStyleSheet("color: #64748b; font-size: 12px;")
            sval = QLabel(str(started_at))
            sval.setStyleSheet("color: #1e293b; font-size: 12px;")
            time_layout.addWidget(slbl, row, 0)
            time_layout.addWidget(sval, row, 1)
            row += 1

        if completed_at:
            complbl = QLabel("完成时间")
            complbl.setStyleSheet("color: #64748b; font-size: 12px;")
            compval = QLabel(str(completed_at))
            compval.setStyleSheet("color: #1e293b; font-size: 12px;")
            time_layout.addWidget(complbl, row, 0)
            time_layout.addWidget(compval, row, 1)
            row += 1

        if updated_at:
            ulbl = QLabel("更新时间")
            ulbl.setStyleSheet("color: #64748b; font-size: 12px;")
            uval = QLabel(str(updated_at))
            uval.setStyleSheet("color: #1e293b; font-size: 12px;")
            time_layout.addWidget(ulbl, row, 0)
            time_layout.addWidget(uval, row, 1)
            row += 1

        if row == 0:
            no_time = QLabel("暂无时间信息")
            no_time.setStyleSheet("color: #94a3b8; font-size: 12px;")
            time_layout.addWidget(no_time, 0, 0, 1, 2)

        self._detail_layout.addWidget(time_box)
        self._detail_layout.addStretch()

    def _on_execute_step(self):
        if not self._current_instance_id or not hasattr(self, '_current_step_id'):
            return

        step_name = getattr(self, '_current_step_name', '')

        result = self._engine.execute_step(
            self._current_instance_id, self._current_step_id
        )

        if result.get("needs_approval"):
            self._status_label.setText(f"⏳ {step_name} 需要人工审批")
        elif result["success"]:
            self._status_label.setText(f"✅ {step_name} 执行成功")
        else:
            self._status_label.setText(f"❌ {step_name} 执行失败: {result['error']}")

        self._refresh_current()

    def _on_approve_step(self):
        if not self._current_instance_id or not hasattr(self, '_current_step_id'):
            return

        dlg = StyledCommentDialog(
            self, title="审批通过",
            prompt=f"为步骤「{self._current_step_name}」添加审批意见："
        )
        if dlg.exec() != QDialog.Accepted:
            return

        comment = dlg.get_comment()
        self._engine.approve_step(
            self._current_instance_id, self._current_step_id,
            reviewer="user", comment=comment
        )
        self._status_label.setText(f"✅ 已审批通过: {self._current_step_name}")
        self._refresh_current()

    def _on_reject_step(self):
        if not self._current_instance_id or not hasattr(self, '_current_step_id'):
            return

        dlg = StyledConfirmDialog(self)
        dlg.setWindowTitle("拒绝审批")
        dlg.setText(f"<b style='color:#1e293b;'>确定要拒绝步骤「{self._current_step_name}」吗？</b>")
        dlg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        dlg.setButtonText(QMessageBox.Yes, "拒绝")
        dlg.setButtonText(QMessageBox.No, "取消")
        dlg.setDefaultButton(QMessageBox.No)
        reply = dlg.exec()
        if reply == QMessageBox.Yes:
            self._engine.reject_step(
                self._current_instance_id, self._current_step_id,
                reviewer="user", comment="人工拒绝"
            )
            self._status_label.setText(f"❌ 已拒绝: {self._current_step_name}")
            self._refresh_current()

    def _on_new_workflow(self):
        """新建工作流 - 单步对话框：选模板 + 改名字 一起完成"""
        try:
            templates = self._engine.list_templates()
            if not templates:
                QMessageBox.warning(self, "提示", "没有可用的工作流模板")
                return

            dlg = StyledTemplateDialog(self, templates=templates)
            if dlg.exec() == QDialog.Accepted:
                tid = dlg.get_selected_id()
                cname = dlg.get_custom_name()
                if tid:
                    self._on_template_id_selected(tid, custom_name=cname)
                else:
                    self._status_label.setText("⚠️ 未选择任何模板")
                    self._status_label.setStyleSheet("color: #f59e0b; font-weight: bold;")
            else:
                self._status_label.setText("已取消新建工作流")
                self._status_label.setStyleSheet("color: #64748b;")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            self._status_label.setText(f"错误: {e}")
            self._status_label.setStyleSheet("color: #ef4444; font-weight: bold;")
            try:
                QMessageBox.critical(self, "新建工作流失败",
                                     f"发生错误：{e}\n\n{tb}")
            except:
                pass

    def _on_template_id_selected(self, template_id: str, custom_name: str = ""):
        instance = self._engine.create_instance(template_id, custom_name=custom_name)
        if instance:
            self._current_instance_id = instance.template_id
            self._refresh_current()
            # 更明显的成功反馈
            self._status_label.setText(
                f"✅ 已创建工作流: {instance.name} (实例ID: {instance.template_id})"
            )
            self._status_label.setStyleSheet("color: #22c55e; font-weight: bold;")
            # 自动聚焦到第一个可执行步骤
            self._activate_current_step(
                self._engine.get_workflow_status(self._current_instance_id)
            )
        else:
            self._status_label.setText(f"❌ 创建失败: {template_id}")
            self._status_label.setStyleSheet("color: #ef4444;")

    def _refresh_all(self):
        self._refresh_templates()
        self._refresh_instances()
        self._refresh_current()

    def _refresh_instances(self):
        """刷新左侧已创建实例列表"""
        self._instances_list.clear()
        if not hasattr(self._engine, '_instances') or not self._engine._instances:
            empty = QListWidgetItem("  （尚未创建任何工作流）")
            empty.setFlags(Qt.NoItemFlags)
            self._instances_list.addItem(empty)
            return

        # 最新创建的显示在顶部
        for iid, instance in reversed(list(self._engine._instances.items())):
            status = self._engine.get_workflow_status(iid)
            progress = status.get('progress', {})
            pct = progress.get('percentage', 0)
            total = progress.get('total', 0)
            done = progress.get('completed', 0)
            failed = progress.get('failed', 0)

            if status.get('is_complete'):
                icon, tag = "✅", "已完成"
            elif failed > 0:
                icon, tag = "❌", "失败"
            elif status.get('current_step') and status['current_step']['status'] == 'waiting_approval':
                icon, tag = "🔒", "待审批"
            elif status.get('current_step') and status['current_step']['status'] == 'executing':
                icon, tag = "🔄", "执行中"
            elif pct > 0:
                icon, tag = "▶️", "进行中"
            else:
                icon, tag = "⏸️", "未开始"

            item_txt = f"  {icon} {instance.name}\n    {tag} {done}/{total} ({pct:.0f}%) [{iid[:6]}…]"
            item = QListWidgetItem(item_txt)
            item.setData(Qt.UserRole, iid)
            if iid == self._current_instance_id:
                item.setSelected(True)
            self._instances_list.addItem(item)

    def _on_instance_selected(self, item):
        """点击实例列表切换当前工作流"""
        iid = item.data(Qt.UserRole)
        if not iid:
            return
        if iid == self._current_instance_id:
            return
        # 检查实例是否仍存在
        if not hasattr(self._engine, '_instances') or iid not in self._engine._instances:
            self._status_label.setText("⚠️ 该实例已不存在")
            return
        self._current_instance_id = iid
        instance = self._engine._instances.get(iid)
        if instance:
            self._status_label.setText(
                f"已切换到: {instance.name} (实例ID: {iid})"
            )
            self._status_label.setStyleSheet("color: #06b6d4; font-weight: bold;")
        self._refresh_current()

    def set_dark_mode(self, dark):
        self._dark_mode = dark
        c = {
            "bg": "#1e1e2e" if dark else "#ffffff",
            "panel_bg": "#252538" if dark else "#f8fafc",
            "border": "rgba(255,255,255,8)" if dark else "#e2e8f0",
            "text": "#e2e8f0" if dark else "#1e293b",
            "btn_bg": "#2a2a3e" if dark else "#f1f5f9",
            "btn_text": "#cbd5e1" if dark else "#475569",
            "btn_hover": "#3a3a4e" if dark else "#e2e8f0",
            "group_title": "#9aa0b0" if dark else "#64748b",
            "progress_bg": "#2a2a3e" if dark else "#e2e8f0",
        }

        self.setStyleSheet(f"""
            QFrame#workflowPanel {{ background: {c['bg']}; }}
            QFrame#wfTopBar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8b5cf6,stop:1 #7c3aed); }}
            QPushButton#wfBackBtn {{
                background: rgba(255,255,255,0.2); color: white; border: none;
                border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 600;
            }}
            QPushButton#wfBackBtn:hover {{ background: rgba(255,255,255,0.3); }}
            QLabel#wfTitle {{ color: white; font-size: 14px; font-weight: bold; }}
            QPushButton#wfNewBtn, QPushButton#wfRefreshBtn {{
                background: rgba(255,255,255,0.2); color: white; border: none;
                border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 600;
            }}
            QPushButton#wfNewBtn:hover, QPushButton#wfRefreshBtn:hover {{ background: rgba(255,255,255,0.3); }}
            QFrame#wfLeftPanel, QFrame#wfCenterPanel, QFrame#wfRightPanel {{ background: {c['panel_bg']}; }}
            QSplitter#wfSplitter {{ border: none; }}
            QGroupBox {{
                background: transparent; border: 1px solid {c['border']}; border-radius: 8px;
                font-size: 11px; font-weight: 600; color: {c['group_title']};
                margin-top: 12px; padding-top: 8px;
            }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; }}
            QListWidget#wfTemplateList {{
                background: {c['btn_bg']}; color: {c['text']}; border: none;
                border-radius: 6px; font-size: 12px; outline: 0;
            }}
            QListWidget#wfTemplateList::item {{
                padding: 8px; border-bottom: 1px solid {c['border']};
                color: {c['text']};
            }}
            QListWidget#wfTemplateList::item:selected {{
                background: #8b5cf6; color: white;
            }}
            QListWidget#wfTemplateList::item:hover {{
                background: {c['border']};
            }}
            QListWidget#wfInstancesList {{
                background: {c['btn_bg']}; color: {c['text']}; border: none;
                border-radius: 6px; font-size: 11px; outline: 0;
            }}
            QListWidget#wfInstancesList::item {{
                padding: 6px; border-bottom: 1px solid {c['border']};
                color: {c['text']};
            }}
            QListWidget#wfInstancesList::item:selected {{
                background: #06b6d4; color: white;
            }}
            QListWidget#wfInstancesList::item:hover {{
                background: {c['border']};
            }}
            QProgressBar#wfProgressBar {{
                background: {c['progress_bg']}; border: none; border-radius: 6px;
                height: 20px; text-align: center; color: {c['text']}; font-weight: bold;
            }}
            QProgressBar#wfProgressBar::chunk {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #8b5cf6,stop:1 #7c3aed);
                border-radius: 6px;
            }}
            QLabel#wfProgressLabel {{ color: {c['group_title']}; font-size: 11px; }}
            QPushButton#wfExecuteBtn {{
                background: #22c55e; color: white; border: none;
                border-radius: 6px; padding: 6px 14px; font-weight: 600;
            }}
            QPushButton#wfExecuteBtn:hover {{ background: #16a34a; }}
            QPushButton#wfExecuteBtn:disabled {{ background: {c['progress_bg']}; color: {c['group_title']}; }}
            QPushButton#wfApproveBtn {{
                background: #3b82f6; color: white; border: none;
                border-radius: 6px; padding: 6px 14px; font-weight: 600;
            }}
            QPushButton#wfApproveBtn:hover {{ background: #2563eb; }}
            QPushButton#wfApproveBtn:disabled {{ background: {c['progress_bg']}; color: {c['group_title']}; }}
            QPushButton#wfRejectBtn {{
                background: #ef4444; color: white; border: none;
                border-radius: 6px; padding: 6px 14px; font-weight: 600;
            }}
            QPushButton#wfRejectBtn:hover {{ background: #dc2626; }}
            QPushButton#wfRejectBtn:disabled {{ background: {c['progress_bg']}; color: {c['group_title']}; }}
            QFrame#wfBottomBar {{ background: {c['panel_bg']}; border-top: 1px solid {c['border']}; }}
            QLabel#wfStatusLabel {{ color: {c['group_title']}; font-size: 11px; }}
        """)
