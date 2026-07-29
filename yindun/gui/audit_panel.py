# -*- coding: utf-8 -*-
"""
隐盾审计日志查看面板 - P0 第4项
核心能力：①哈希审计链可视化 ②全链路数据流向 ③隐私敏感事件告警
"""
import json
import os
from datetime import datetime
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QTextEdit, QComboBox,
    QLineEdit, QSplitter, QTreeWidget, QTreeWidgetItem,
    QFileDialog, QProgressBar, QHeaderView, QWidget,
    QGroupBox, QGridLayout, QSizePolicy, QScrollArea, QDialog
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QBrush, QCursor
from yindun.core.audit_log import AuditLog, AuditEventType, AuditSeverity
from yindun.core.health_scanner import HealthScanner, ScanReport
from yindun.core.behavior_analyzer import BehaviorAnalyzer
from yindun.core.flow_builder import FlowBuilder


class AuditPanel(QFrame):
    back_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("auditPanel")
        self._audit_log = AuditLog()
        self._current_filters = {}
        self._dark_mode = False
        self._init_ui()
        self._refresh_data()
        self._start_refresh_timer()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        top_bar = QFrame()
        top_bar.setObjectName("auditTopBar")
        top_bar.setFixedHeight(44)
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(12, 0, 12, 0)
        tb_layout.setSpacing(8)

        back_btn = QPushButton("← 返回")
        back_btn.setObjectName("auditBackBtn")
        back_btn.setCursor(QCursor(Qt.PointingHandCursor))
        back_btn.clicked.connect(self.back_requested)
        tb_layout.addWidget(back_btn)

        title_label = QLabel("🛡️ 审计日志")
        title_label.setObjectName("auditTitle")
        tb_layout.addWidget(title_label)
        tb_layout.addStretch()

        export_btn = QPushButton("导出报告")
        export_btn.setObjectName("auditExportBtn")
        export_btn.setCursor(QCursor(Qt.PointingHandCursor))
        export_btn.clicked.connect(self._export_report)
        tb_layout.addWidget(export_btn)

        refresh_btn = QPushButton("🔄")
        refresh_btn.setObjectName("auditRefreshBtn")
        refresh_btn.setFixedSize(28, 28)
        refresh_btn.setCursor(QCursor(Qt.PointingHandCursor))
        refresh_btn.clicked.connect(self._refresh_data)
        tb_layout.addWidget(refresh_btn)

        main_layout.addWidget(top_bar)

        filter_bar = QFrame()
        filter_bar.setObjectName("auditFilterBar")
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(12, 8, 12, 8)
        fb_layout.setSpacing(10)

        event_type_combo = QComboBox()
        event_type_combo.addItem("全部事件类型")
        for et in AuditEventType:
            event_type_combo.addItem(et.value)
        event_type_combo.currentTextChanged.connect(self._on_filter_change)
        event_type_combo.setObjectName("auditEventTypeCombo")
        fb_layout.addWidget(event_type_combo)

        severity_combo = QComboBox()
        severity_combo.addItem("全部严重程度")
        for sv in AuditSeverity:
            severity_combo.addItem(sv.value)
        severity_combo.currentTextChanged.connect(self._on_filter_change)
        severity_combo.setObjectName("auditSeverityCombo")
        fb_layout.addWidget(severity_combo)

        search_input = QLineEdit()
        search_input.setPlaceholderText("搜索日志...")
        search_input.textChanged.connect(self._on_filter_change)
        search_input.setObjectName("auditSearchInput")
        fb_layout.addWidget(search_input, 1)

        clear_btn = QPushButton("清除筛选")
        clear_btn.setObjectName("auditClearBtn")
        clear_btn.setCursor(QCursor(Qt.PointingHandCursor))
        clear_btn.clicked.connect(self._clear_filters)
        fb_layout.addWidget(clear_btn)

        main_layout.addWidget(filter_bar)

        self._alert_bar = QFrame()
        self._alert_bar.setObjectName("auditAlertBar")
        self._alert_bar.setFixedHeight(36)
        alert_layout = QHBoxLayout(self._alert_bar)
        alert_layout.setContentsMargins(12, 0, 12, 0)
        alert_layout.setSpacing(8)

        self._alert_icon = QLabel("🚨")
        self._alert_icon.setObjectName("auditAlertIcon")
        alert_layout.addWidget(self._alert_icon)

        self._alert_label = QLabel("")
        self._alert_label.setObjectName("auditAlertLabel")
        alert_layout.addWidget(self._alert_label)
        alert_layout.addStretch()

        self._alert_count = QLabel("")
        self._alert_count.setObjectName("auditAlertCount")
        alert_layout.addWidget(self._alert_count)

        main_layout.addWidget(self._alert_bar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("auditSplitter")

        left_panel = QFrame()
        left_panel.setObjectName("auditLeftPanel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)

        stats_group = QGroupBox("统计概览")
        stats_group.setObjectName("auditStatsGroup")
        stats_layout = QGridLayout(stats_group)

        self._stat_labels = {}
        stat_items = [
            ("total", "总日志数", "0"),
            ("tools", "工具调用", "0"),
            ("privacy", "隐私事件", "0"),
            ("chain", "链条状态", "验证中")
        ]
        for i, (key, label, value) in enumerate(stat_items):
            lbl = QLabel(label)
            lbl.setObjectName("auditStatLabel")
            val = QLabel(value)
            val.setObjectName("auditStatValue")
            stats_layout.addWidget(lbl, i // 2, (i % 2) * 2)
            stats_layout.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self._stat_labels[key] = val

        left_layout.addWidget(stats_group)

        chain_group = QGroupBox("哈希审计链")
        chain_group.setObjectName("auditChainGroup")
        chain_layout = QVBoxLayout(chain_group)

        self._chain_tree = QTreeWidget()
        self._chain_tree.setObjectName("auditChainTree")
        self._chain_tree.setColumnCount(2)
        self._chain_tree.setHeaderLabels(["区块", "哈希值"])
        self._chain_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._chain_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        chain_layout.addWidget(self._chain_tree)

        left_layout.addWidget(chain_group)

        flow_group = QGroupBox("数据流向")
        flow_group.setObjectName("auditFlowGroup")
        flow_layout = QVBoxLayout(flow_group)

        self._flow_tree = QTreeWidget()
        self._flow_tree.setObjectName("auditFlowTree")
        self._flow_tree.setColumnCount(2)
        self._flow_tree.setHeaderLabels(["节点", "状态"])
        self._flow_tree.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._flow_tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        flow_layout.addWidget(self._flow_tree)

        left_layout.addWidget(flow_group)

        # ★ 新增：健康体检 & 行为画像入口 ──────────────────
        actions_group = QGroupBox("安全工具")
        actions_group.setObjectName("auditActionsGroup")
        act_layout = QVBoxLayout(actions_group)

        health_btn = QPushButton("🔍 隐私健康体检")
        health_btn.setObjectName("auditHealthBtn")
        health_btn.setCursor(QCursor(Qt.PointingHandCursor))
        health_btn.clicked.connect(self._run_health_scan)
        act_layout.addWidget(health_btn)

        behavior_btn = QPushButton("📊 模型行为画像")
        behavior_btn.setObjectName("auditBehaviorBtn")
        behavior_btn.setCursor(QCursor(Qt.PointingHandCursor))
        behavior_btn.clicked.connect(self._run_behavior_analysis)
        act_layout.addWidget(behavior_btn)

        left_layout.addWidget(actions_group)

        left_layout.addStretch()
        splitter.addWidget(left_panel)
        splitter.setStretchFactor(0, 1)

        right_panel = QFrame()
        right_panel.setObjectName("auditRightPanel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        tabs_group = QGroupBox("日志列表")
        tabs_group.setObjectName("auditTabsGroup")
        tabs_layout = QVBoxLayout(tabs_group)

        self._log_table = QTableWidget()
        self._log_table.setObjectName("auditLogTable")
        self._log_table.setColumnCount(5)
        self._log_table.setHorizontalHeaderLabels(["时间", "类型", "级别", "消息", "会话ID"])
        self._log_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self._log_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._log_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._log_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self._log_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self._log_table.setAlternatingRowColors(True)
        self._log_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._log_table.doubleClicked.connect(self._on_log_double_click)
        tabs_layout.addWidget(self._log_table)

        right_layout.addWidget(tabs_group)

        detail_group = QGroupBox("详细信息")
        detail_group.setObjectName("auditDetailGroup")
        detail_layout = QVBoxLayout(detail_group)

        self._detail_text = QTextEdit()
        self._detail_text.setObjectName("auditDetailText")
        self._detail_text.setReadOnly(True)
        detail_layout.addWidget(self._detail_text)

        right_layout.addWidget(detail_group)

        right_layout.addStretch()
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 2)

        main_layout.addWidget(splitter, 1)

        bottom_bar = QFrame()
        bottom_bar.setObjectName("auditBottomBar")
        bb_layout = QHBoxLayout(bottom_bar)
        bb_layout.setContentsMargins(12, 6, 12, 6)
        bb_layout.setSpacing(8)

        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("auditStatusLabel")
        bb_layout.addWidget(self._status_label)
        bb_layout.addStretch()

        self._chain_valid_label = QLabel("")
        self._chain_valid_label.setObjectName("auditChainValidLabel")
        bb_layout.addWidget(self._chain_valid_label)

        main_layout.addWidget(bottom_bar)

        self.set_dark_mode(False)

    def _start_refresh_timer(self):
        self._timer = QTimer()
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self._refresh_data)
        self._timer.start()

    def _refresh_data(self):
        stats = self._audit_log.get_stats()
        self._stat_labels["total"].setText(str(stats["total_entries"]))
        self._stat_labels["tools"].setText(str(stats["by_type"].get("tool_call", 0)))
        self._stat_labels["privacy"].setText(str(stats["by_type"].get("privacy_sensitive", 0)))

        chain_valid = stats["chain_valid"]
        self._stat_labels["chain"].setText("✅ 有效" if chain_valid else "❌ 无效")
        self._chain_valid_label.setText(
            f"哈希链: {'✅ 完整性验证通过' if chain_valid else '❌ 链条已被篡改'}"
        )

        self._populate_chain_tree()
        self._populate_flow_tree()
        self._update_alert_bar()
        self._populate_log_table()

    def _update_alert_bar(self):
        stats = self._audit_log.get_stats()
        critical_count = stats["by_severity"].get("critical", 0)
        security_count = stats["by_severity"].get("security", 0)
        total_alerts = critical_count + security_count

        if total_alerts > 0:
            self._alert_bar.setVisible(True)
            if critical_count > 0:
                self._alert_label.setText(f"检测到 {critical_count} 个严重安全事件！")
                self._alert_label.setStyleSheet("color: #ef4444; font-weight: bold;")
            else:
                self._alert_label.setText(f"检测到 {security_count} 个隐私敏感事件")
                self._alert_label.setStyleSheet("color: #f59e0b; font-weight: 600;")
            self._alert_count.setText(f"共 {total_alerts} 条")
        else:
            self._alert_bar.setVisible(False)

    def _populate_flow_tree(self):
        self._flow_tree.clear()
        entries = self._audit_log.get_entries()
        
        flow_data = {
            "user_input": {"count": 0, "status": "未活跃"},
            "privacy_filter": {"count": 0, "status": "未检测"},
            "llm_process": {"count": 0, "status": "未调用"},
            "tool_exec": {"count": 0, "status": "未执行"},
            "result_output": {"count": 0, "status": "无输出"}
        }

        for entry in entries:
            if entry.event_type == "llm_input":
                flow_data["user_input"]["count"] += 1
                flow_data["user_input"]["status"] = "活跃"
                if entry.details.get("has_privacy", False):
                    flow_data["privacy_filter"]["count"] += 1
                    flow_data["privacy_filter"]["status"] = "检测到敏感数据"
                else:
                    flow_data["privacy_filter"]["status"] = "安全"
            elif entry.event_type == "llm_output":
                flow_data["llm_process"]["count"] += 1
                flow_data["llm_process"]["status"] = "已响应"
                flow_data["result_output"]["count"] += 1
                flow_data["result_output"]["status"] = "已生成"
            elif entry.event_type == "tool_call":
                flow_data["tool_exec"]["count"] += 1
                flow_data["tool_exec"]["status"] = "执行中"
            elif entry.event_type == "tool_result":
                flow_data["tool_exec"]["status"] = "完成" if entry.details.get("success") else "失败"

        flow_nodes = [
            ("👤 用户输入", "user_input"),
            ("🛡️ 隐私过滤", "privacy_filter"),
            ("🧠 LLM处理", "llm_process"),
            ("⚙️ 工具执行", "tool_exec"),
            ("📤 结果输出", "result_output")
        ]

        for label, key in flow_nodes:
            data = flow_data[key]
            status_color = "#4ade80" if "安全" in data["status"] or "活跃" in data["status"] or "完成" in data["status"] or "已" in data["status"] else "#f59e0b"
            if "失败" in data["status"]:
                status_color = "#ef4444"
            elif "检测到" in data["status"]:
                status_color = "#f59e0b"

            item = QTreeWidgetItem([label, f"{data['status']} ({data['count']})"])
            item.setBackground(1, QBrush(QColor(status_color)))
            item.setForeground(1, QBrush(QColor("#ffffff")))
            self._flow_tree.addTopLevelItem(item)

    def _populate_chain_tree(self):
        self._chain_tree.clear()
        entries = self._audit_log.get_entries()
        for i, entry in enumerate(entries[-20:]):
            root = QTreeWidgetItem([f"#{len(entries) - 20 + i}", entry.entry_hash[:16] + "..."])
            root.setToolTip(1, entry.entry_hash)
            root.addChild(QTreeWidgetItem(["时间", entry.timestamp]))
            root.addChild(QTreeWidgetItem(["类型", entry.event_type]))
            root.addChild(QTreeWidgetItem(["级别", entry.severity]))
            root.addChild(QTreeWidgetItem(["前哈希", entry.previous_hash[:16] + "..."]))
            self._chain_tree.addTopLevelItem(root)
            root.setExpanded(False)

    def _populate_log_table(self):
        self._log_table.setRowCount(0)
        entries = self._audit_log.get_entries(self._current_filters)
        entries.reverse()

        for entry in entries[:100]:
            row = self._log_table.rowCount()
            self._log_table.insertRow(row)

            time_item = QTableWidgetItem(entry.timestamp)
            time_item.setData(Qt.UserRole, entry)

            type_item = QTableWidgetItem(entry.event_type)
            type_color = self._get_event_type_color(entry.event_type)
            type_item.setBackground(QBrush(QColor(type_color)))
            type_item.setForeground(QBrush(QColor("#ffffff")))
            type_item.setFont(QFont("", -1, QFont.Bold))

            severity_item = QTableWidgetItem(entry.severity)
            severity_color = self._get_severity_color(entry.severity)
            severity_item.setBackground(QBrush(QColor(severity_color)))
            severity_item.setForeground(QBrush(QColor("#ffffff")))

            msg_item = QTableWidgetItem(entry.message)
            session_item = QTableWidgetItem(entry.session_id or "-")

            for item in [time_item, type_item, severity_item, msg_item, session_item]:
                item.setTextAlignment(Qt.AlignVCenter)

            self._log_table.setItem(row, 0, time_item)
            self._log_table.setItem(row, 1, type_item)
            self._log_table.setItem(row, 2, severity_item)
            self._log_table.setItem(row, 3, msg_item)
            self._log_table.setItem(row, 4, session_item)

    def _get_event_type_color(self, event_type):
        colors = {
            "tool_call": "#3b82f6",
            "tool_result": "#10b981",
            "llm_input": "#8b5cf6",
            "llm_output": "#f59e0b",
            "privacy_sensitive": "#ef4444",
            "access_control": "#6366f1",
            "session_start": "#14b8a6",
            "session_end": "#94a3b8"
        }
        return colors.get(event_type, "#64748b")

    def _get_severity_color(self, severity):
        colors = {
            "info": "#3b82f6",
            "warning": "#f59e0b",
            "critical": "#ef4444",
            "security": "#6366f1"
        }
        return colors.get(severity, "#64748b")

    def _on_filter_change(self, *args):
        event_type = self.findChild(QComboBox, "auditEventTypeCombo").currentText()
        severity = self.findChild(QComboBox, "auditSeverityCombo").currentText()
        search = self.findChild(QLineEdit, "auditSearchInput").text().strip()

        self._current_filters = {}
        if event_type != "全部事件类型":
            self._current_filters["event_type"] = event_type
        if severity != "全部严重程度":
            self._current_filters["severity"] = severity

        self._populate_log_table()

        if search:
            for row in range(self._log_table.rowCount()):
                visible = False
                for col in range(self._log_table.columnCount()):
                    item = self._log_table.item(row, col)
                    if item and search.lower() in item.text().lower():
                        visible = True
                        break
                self._log_table.setRowHidden(row, not visible)

    def _clear_filters(self):
        self.findChild(QComboBox, "auditEventTypeCombo").setCurrentText("全部事件类型")
        self.findChild(QComboBox, "auditSeverityCombo").setCurrentText("全部严重程度")
        self.findChild(QLineEdit, "auditSearchInput").clear()
        self._current_filters = {}
        self._populate_log_table()

    def _on_log_double_click(self, index):
        row = index.row()
        item = self._log_table.item(row, 0)
        if item:
            entry = item.data(Qt.UserRole)
            if entry:
                detail = json.dumps(entry.to_dict(), ensure_ascii=False, indent=2)
                self._detail_text.setText(detail)

    def _export_report(self):
        format_dialog = QDialog(self)
        format_dialog.setWindowTitle("选择报告格式")
        format_dialog.setFixedSize(300, 120)
        
        layout = QVBoxLayout(format_dialog)
        
        label = QLabel("请选择导出格式：")
        layout.addWidget(label)
        
        combo = QComboBox()
        combo.addItems(["JSON", "HTML"])
        layout.addWidget(combo)
        
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)
        
        ok_btn.clicked.connect(format_dialog.accept)
        cancel_btn.clicked.connect(format_dialog.reject)
        
        if format_dialog.exec() != QDialog.Accepted:
            return
        
        format_str = combo.currentText()
        format = "json" if format_str == "JSON" else "html"
        
        filepath = QFileDialog.getSaveFileName(
            self, "保存审计报告",
            f"audit_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{format}",
            f"{format.upper()}文件 (*.{format});;所有文件 (*)"
        )
        if filepath[0]:
            try:
                content = self._audit_log.export_report(format, self._current_filters)
                with open(filepath[0], 'w', encoding='utf-8') as f:
                    f.write(content)
                self._status_label.setText(f"报告已导出: {os.path.basename(filepath[0])}")
            except Exception as e:
                self._status_label.setText(f"导出失败: {str(e)}")

    def set_dark_mode(self, dark):
        self._dark_mode = dark
        c = {
            "bg": "#1e1e2e" if dark else "#ffffff",
            "panel_bg": "#252538" if dark else "#f8fafc",
            "border": "rgba(255,255,255,8)" if dark else "#e2e8f0",
            "text": "#d4d4e0" if dark else "#334155",
            "title_text": "#ffffff" if dark else "#1e293b",
            "btn_bg": "#33334a" if dark else "#f1f5f9",
            "btn_text": "#d4d4e0" if dark else "#475569",
            "btn_hover": "#07c160" if dark else "#07c160",
            "group_title": "#9aa0b0" if dark else "#64748b",
            "table_header": "#2a2a3e" if dark else "#f1f5f9",
            "table_row1": "#252538" if dark else "#ffffff",
            "table_row2": "#2a2a3e" if dark else "#f8fafc",
            "detail_bg": "#1e1e2e" if dark else "#f1f5f9",
            "detail_text": "#94a3b8" if dark else "#64748b",
            "stat_label": "#9aa0b0" if dark else "#64748b",
            "stat_value": "#d4d4e0" if dark else "#1e293b",
            "success": "#4ade80",
            "error": "#f87171"
        }

        self.setStyleSheet(f"""
            QFrame#auditPanel {{ background: {c['bg']}; }}
            QFrame#auditTopBar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #07c160,stop:1 #06ad56); }}
            QPushButton#auditBackBtn {{ 
                background: rgba(255,255,255,0.2); color: white; border: none; 
                border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 600; 
            }}
            QPushButton#auditBackBtn:hover {{ background: rgba(255,255,255,0.3); }}
            QLabel#auditTitle {{ color: white; font-size: 14px; font-weight: bold; }}
            QPushButton#auditExportBtn {{ 
                background: rgba(255,255,255,0.2); color: white; border: none; 
                border-radius: 8px; padding: 4px 12px; font-size: 12px; font-weight: 600; 
            }}
            QPushButton#auditExportBtn:hover {{ background: rgba(255,255,255,0.3); }}
            QPushButton#auditRefreshBtn {{ 
                background: rgba(255,255,255,0.2); color: white; border: none; 
                border-radius: 8px; font-size: 12px; font-weight: bold; 
            }}
            QPushButton#auditRefreshBtn:hover {{ background: rgba(255,255,255,0.3); }}
            QFrame#auditFilterBar {{ background: {c['panel_bg']}; border-bottom: 1px solid {c['border']}; }}
            QComboBox#auditEventTypeCombo, QComboBox#auditSeverityCombo {{
                background: {c['btn_bg']}; color: {c['btn_text']}; border: 1px solid {c['border']};
                border-radius: 6px; padding: 4px 8px; font-size: 11px; min-width: 120px;
            }}
            QLineEdit#auditSearchInput {{
                background: {c['btn_bg']}; color: {c['text']}; border: 1px solid {c['border']};
                border-radius: 6px; padding: 4px 10px; font-size: 11px;
            }}
            QPushButton#auditClearBtn {{
                background: {c['btn_bg']}; color: {c['btn_text']}; border: 1px solid {c['border']};
                border-radius: 6px; padding: 4px 10px; font-size: 11px; font-weight: 500;
            }}
            QPushButton#auditClearBtn:hover {{ background: {c['btn_hover']}; color: white; }}
            QPushButton#auditHealthBtn, QPushButton#auditBehaviorBtn {{
                background: transparent; color: {c['btn_text']}; border: 1px solid {c['border']};
                border-radius: 6px; padding: 6px 10px; font-size: 11px; font-weight: 500; text-align: left;
            }}
            QPushButton#auditHealthBtn:hover, QPushButton#auditBehaviorBtn:hover {{
                background: {c['btn_hover']}; color: white; border-color: {c['btn_hover']};
            }}
            QFrame#auditLeftPanel, QFrame#auditRightPanel {{ background: {c['panel_bg']}; }}
            QSplitter#auditSplitter {{ border: none; }}
            QFrame#auditAlertBar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #fef3c7,stop:1 #fde68a); border-bottom: 1px solid #f59e0b; }}
            QLabel#auditAlertIcon {{ font-size: 16px; }}
            QLabel#auditAlertLabel {{ color: #92400e; font-size: 12px; font-weight: 600; }}
            QLabel#auditAlertCount {{ color: #f59e0b; font-size: 12px; font-weight: bold; }}
            QGroupBox#auditStatsGroup, QGroupBox#auditChainGroup, QGroupBox#auditFlowGroup, QGroupBox#auditActionsGroup, QGroupBox#auditTabsGroup, QGroupBox#auditDetailGroup {{
                background: transparent; border: 1px solid {c['border']}; border-radius: 8px;
                font-size: 11px; font-weight: 600; color: {c['group_title']};
            }}
            QLabel#auditStatLabel {{ color: {c['stat_label']}; font-size: 10px; }}
            QLabel#auditStatValue {{ color: {c['stat_value']}; font-size: 14px; font-weight: bold; }}
            QTreeWidget#auditChainTree {{
                background: {c['btn_bg']}; color: {c['text']}; border: none;
                alternate-background-color: {c['table_row2']};
            }}
            QTreeWidget#auditChainTree::item:selected {{ background: #07c160; color: white; }}
            QTreeWidget#auditChainTree QHeaderView::section {{
                background: {c['table_header']}; color: {c['group_title']}; font-size: 10px;
            }}
            QTreeWidget#auditFlowTree {{
                background: {c['btn_bg']}; color: {c['text']}; border: none;
                alternate-background-color: {c['table_row2']};
            }}
            QTreeWidget#auditFlowTree::item:selected {{ background: #07c160; color: white; }}
            QTreeWidget#auditFlowTree QHeaderView::section {{
                background: {c['table_header']}; color: {c['group_title']}; font-size: 10px;
            }}
            QTableWidget#auditLogTable {{
                background: {c['btn_bg']}; color: {c['text']}; border: none;
                alternate-background-color: {c['table_row2']}; gridline-color: {c['border']};
            }}
            QTableWidget#auditLogTable::item:selected {{ background: #07c160; color: white; }}
            QTableWidget#auditLogTable QHeaderView::section {{
                background: {c['table_header']}; color: {c['group_title']}; font-size: 10px; font-weight: 600;
            }}
            QTextEdit#auditDetailText {{
                background: {c['detail_bg']}; color: {c['detail_text']}; border: 1px solid {c['border']};
                border-radius: 6px; font-family: Consolas, monospace; font-size: 11px;
            }}
            QFrame#auditBottomBar {{ background: {c['panel_bg']}; border-top: 1px solid {c['border']}; }}
            QLabel#auditStatusLabel {{ color: {c['stat_label']}; font-size: 11px; }}
            QLabel#auditChainValidLabel {{ color: {c['success']}; font-size: 11px; font-weight: 600; }}
        """)

    # ── 隐私健康体检 ──────────────────────────

    def _run_health_scan(self):
        """打开隐私健康体检对话框，扫描指定目录"""
        from PySide6.QtWidgets import QProgressDialog
        from threading import Thread

        dir_path = QFileDialog.getExistingDirectory(self, "选择要扫描的目录", os.path.expanduser("~"))
        if not dir_path:
            return

        self._status_label.setText("正在扫描...")
        progress = QProgressDialog("正在扫描目录...", "取消", 0, 0, self)
        progress.setWindowTitle("隐私健康体检")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        report = [None]

        def scan_worker():
            scanner = HealthScanner()
            report[0] = scanner.scan(dir_path, depth=3)

        def on_finished():
            progress.close()
            self._status_label.setText("就绪")
            if report[0]:
                self._show_scan_report(report[0])

        thread = Thread(target=scan_worker, daemon=True)
        thread.start()

        # 轮询等待
        def check_thread():
            if thread.is_alive():
                QTimer.singleShot(200, check_thread)
            else:
                on_finished()

        QTimer.singleShot(100, check_thread)

    def _show_scan_report(self, report: ScanReport):
        """显示扫描报告弹窗"""
        dlg = QDialog(self)
        dlg.setWindowTitle("隐私健康体检报告")
        dlg.resize(600, 480)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)

        # 摘要
        summary = QTextEdit()
        summary.setReadOnly(True)
        summary.setPlainText(report.summary())
        summary.setStyleSheet("""
            QTextEdit { font-family: Consolas, monospace; font-size: 12px;
                       background: #1e1e2e; color: #d4d4e0; border: 1px solid #333; border-radius: 8px; }
        """)
        layout.addWidget(summary)

        # 导出按钮
        btn_row = QHBoxLayout()
        export_json_btn = QPushButton("导出 JSON")
        export_json_btn.clicked.connect(lambda: self._save_report_json(report))
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.close)
        btn_row.addStretch()
        btn_row.addWidget(export_json_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()

    def _save_report_json(self, report: ScanReport):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存报告", "health_report.json", "JSON文件 (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(report.to_json())
            self._status_label.setText(f"报告已保存: {os.path.basename(path)}")

    # ── 模型行为画像 ──────────────────────────

    def _run_behavior_analysis(self):
        """显示当前会话的行为画像"""
        entries = self._audit_log.get_entries()
        if not entries:
            self._status_label.setText("暂无日志数据")
            return

        # 按 session_id 分组
        sessions = {}
        for e in entries:
            sid = getattr(e, "session_id", "") if hasattr(e, "session_id") else ""
            if not sid:
                continue
            sessions.setdefault(sid, []).append(e)

        if not sessions:
            self._status_label.setText("无会话数据")
            return

        analyzer = BehaviorAnalyzer()

        dlg = QDialog(self)
        dlg.setWindowTitle("模型行为画像")
        dlg.resize(660, 520)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(16, 16, 16, 16)

        text = QTextEdit()
        text.setReadOnly(True)
        lines = []

        for sid, session_entries in list(sessions.items())[-5:]:  # 最近5个会话
            profile = analyzer.build_profile(session_entries, sid)
            anomalies = analyzer.detect_anomalies(session_entries, sid)
            lines.append(profile.summary())
            if anomalies:
                lines.append(f"\n异常记录: {len(anomalies)} 条")
                for a in anomalies:
                    lines.append(f"  [{a.severity}] {a.anomaly_type}: {a.description}")
            lines.append("")

        text.setPlainText("\n".join(lines))
        text.setStyleSheet("""
            QTextEdit { font-family: Consolas, monospace; font-size: 12px;
                       background: #1e1e2e; color: #d4d4e0; border: 1px solid #333; border-radius: 8px; }
        """)
        layout.addWidget(text)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.close)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        dlg.exec()

    # ── 增强数据流向（集成 FlowBuilder）──────────

    def _populate_flow_tree(self):
        self._flow_tree.clear()
        entries = self._audit_log.get_entries()

        # ★ 使用 FlowBuilder 构建有向数据流图
        builder = FlowBuilder()
        # 将 AuditEntry 对象转为 dict
        entry_dicts = [
            {
                "event_type": getattr(e, "event_type", ""),
                "details": getattr(e, "details", {}),
                "timestamp": getattr(e, "timestamp", ""),
                "session_id": getattr(e, "session_id", "")
            }
            for e in entries
        ]
        graph = builder.build(entry_dicts, session_id="all")

        # 数据流概览
        overview_item = QTreeWidgetItem(["📊 数据流概览", f"{graph.total_flows} 条流, {len(graph.nodes)} 个节点"])
        overview_item.setBackground(1, QBrush(QColor("#07c160")))
        overview_item.setForeground(1, QBrush(QColor("#ffffff")))
        self._flow_tree.addTopLevelItem(overview_item)

        # 工具分布
        for tool, count in sorted(graph.tool_distribution.items(), key=lambda x: -x[1])[:8]:
            item = QTreeWidgetItem([f"  ⚙️ {tool}", f"{count} 次"])
            self._flow_tree.addTopLevelItem(item)

        # 敏感度分布
        for sens, count in sorted(graph.sensitivity_distribution.items(),
                                   key=lambda x: {"绝密": 4, "机密": 3, "内部": 2, "公开": 1}.get(x[0], 0),
                                   reverse=True):
            color = {"绝密": "#ef4444", "机密": "#f59e0b", "内部": "#3b82f6", "公开": "#22c55e"}.get(sens, "#94a3b8")
            item = QTreeWidgetItem([f"  🔒 {sens}", f"{count} 条"])
            item.setBackground(1, QBrush(QColor(color)))
            item.setForeground(1, QBrush(QColor("#ffffff")))
            self._flow_tree.addTopLevelItem(item)

        # 泄露风险
        if graph.leak_risks:
            risk_header = QTreeWidgetItem([f"⚠️ 泄露风险", f"{len(graph.leak_risks)} 条"])
            risk_header.setBackground(1, QBrush(QColor("#ef4444")))
            risk_header.setForeground(1, QBrush(QColor("#ffffff")))
            self._flow_tree.addTopLevelItem(risk_header)
            for risk in graph.leak_risks[:5]:
                item = QTreeWidgetItem([f"  {risk['source']}", risk['reason']])
                item.setForeground(0, QBrush(QColor("#fca5a5")))
                self._flow_tree.addTopLevelItem(item)

        # 回退：保留原流程节点（兼容旧视图）
        flow_data = {
            "user_input": {"count": 0, "status": "未活跃"},
            "privacy_filter": {"count": 0, "status": "未检测"},
            "llm_process": {"count": 0, "status": "未调用"},
            "tool_exec": {"count": 0, "status": "未执行"},
            "result_output": {"count": 0, "status": "无输出"}
        }
        for entry in entries:
            if hasattr(entry, "event_type"):
                if entry.event_type == "llm_input":
                    flow_data["user_input"]["count"] += 1
                    flow_data["user_input"]["status"] = "活跃"
                    if hasattr(entry, "details") and entry.details.get("has_privacy", False):
                        flow_data["privacy_filter"]["count"] += 1
                        flow_data["privacy_filter"]["status"] = "检测到敏感数据"
                    else:
                        flow_data["privacy_filter"]["status"] = "安全"
                elif entry.event_type == "llm_output":
                    flow_data["llm_process"]["count"] += 1
                    flow_data["llm_process"]["status"] = "已响应"
                    flow_data["result_output"]["count"] += 1
                    flow_data["result_output"]["status"] = "已生成"
                elif entry.event_type == "tool_call":
                    flow_data["tool_exec"]["count"] += 1
                    flow_data["tool_exec"]["status"] = "执行中"
                elif entry.event_type == "tool_result":
                    success = entry.details.get("success") if hasattr(entry, "details") else True
                    flow_data["tool_exec"]["status"] = "完成" if success else "失败"

        process_sep = QTreeWidgetItem(["── 处理流程 ──", ""])
        self._flow_tree.addTopLevelItem(process_sep)

        flow_nodes = [
            ("👤 用户输入", "user_input"),
            ("🛡️ 隐私过滤", "privacy_filter"),
            ("🧠 LLM处理", "llm_process"),
            ("⚙️ 工具执行", "tool_exec"),
            ("📤 结果输出", "result_output")
        ]
        for label, key in flow_nodes:
            data = flow_data[key]
            color_map = {"安全": "#4ade80", "活跃": "#22c55e", "完成": "#4ade80",
                         "已响应": "#22c55e", "已生成": "#4ade80"}
            fallback = "#f59e0b"
            if "失败" in data["status"]:
                fallback = "#ef4444"
            status_color = color_map.get(data["status"].split("(")[0], fallback)

            item = QTreeWidgetItem([label, f"{data['status']} ({data['count']})"])
            item.setBackground(1, QBrush(QColor(status_color)))
            item.setForeground(1, QBrush(QColor("#ffffff")))
            self._flow_tree.addTopLevelItem(item)