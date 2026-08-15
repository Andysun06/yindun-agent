with open(r'd:\yindun\yindun-agent\yindun\gui\workflow_panel.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Replace _activate_current_step tree section
old_activate = """        # 在树中找到对应 item，选中并高亮 + 激活按钮 + 填充详情
        if target_id:
            self._current_step_id = target_id
            self._current_step_name = target_name or ""
            for i in range(self._steps_tree.topLevelItemCount()):
                item = self._steps_tree.topLevelItem(i)
                if item.data(0, Qt.UserRole) == target_id:
                    self._steps_tree.setCurrentItem(item)
                    # 填充详情
                    for step in status['steps']:
                        if step['step_id'] == target_id:
                            self._detail_text.setText(
                                json.dumps(step, ensure_ascii=False, indent=2)
                            )
                            break
                    break
            # 统一设置按钮
            self._set_btn_state(target_status)"""

new_activate = """        # 找到对应卡片，高亮 + 激活按钮 + 填充详情
        if target_id:
            self._current_step_id = target_id
            self._current_step_name = target_name or ""
            for step in status['steps']:
                if step['step_id'] == target_id:
                    self._show_step_detail(step)
                    self._highlight_step_card(target_id)
                    break
            # 统一设置按钮
            self._set_btn_state(target_status)"""

if old_activate in content:
    content = content.replace(old_activate, new_activate)
    print("OK: activate_current_step replaced")
else:
    print("WARN: activate_current_step not found")
    # Debug
    idx = content.find("# 在树中找到对应 item")
    print(f"Found at: {idx}")

# 2. Replace _update_progress to add banner update
old_update_start = "    def _update_progress(self, status: dict):"
lines = content.split('\n')
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if line.strip() == old_update_start.strip():
        start_idx = i
        break

if start_idx is not None:
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith('    def '):
            end_idx = i
            break

    new_update_lines = [
        '    def _update_progress(self, status: dict):',
        '        progress = status[\'progress\']',
        '        self._progress_bar.setValue(int(progress[\'percentage\']))',
        '        self._banner_progress.setText(f"{int(progress[\'percentage\'])}%")',
        '',
        '        current = status.get(\'current_step\')',
        '        if status[\'is_complete\']:',
        '            self._banner_title.setText("🎉 工作流已完成！")',
        '            self._banner_subtitle.setText(f"共 {progress[\'total\']} 个步骤，全部执行完毕")',
        '        elif status[\'has_failed\']:',
        '            self._banner_title.setText("⚠️ 工作流有步骤失败")',
        '            self._banner_subtitle.setText(f"已完成 {progress[\'completed\']}/{progress[\'total\']}，有 {progress[\'failed\']} 个失败")',
        '        elif current:',
        '            s = current[\'status\']',
        '            if s == \'pending\':',
        '                hint = f"⏳ 等待执行：{current[\'name\']}"',
        '            elif s == \'waiting_approval\':',
        '                hint = f"🔒 等待审批：{current[\'name\']}"',
        '            elif s == \'executing\':',
        '                hint = f"🔄 正在执行：{current[\'name\']}"',
        '            elif s == \'approved\':',
        '                hint = f"✅ 已审批，等待执行：{current[\'name\']}"',
        '            else:',
        '                hint = f"当前步骤：{current[\'name\']}"',
        '            self._banner_title.setText(f"🚀 {hint}")',
        '            self._banner_subtitle.setText(f"进度：{progress[\'completed\']}/{progress[\'total\']} 步骤")',
        '        else:',
        '            self._banner_title.setText("📋 工作流已就绪")',
        '            self._banner_subtitle.setText(f"进度：{progress[\'completed\']}/{progress[\'total\']} 步骤")',
    ]
    content = '\n'.join(lines[:start_idx] + new_update_lines + lines[end_idx:])
    print(f"OK: _update_progress replaced ({start_idx+1} to {end_idx})")

# 3. Replace _populate_steps
lines = content.split('\n')
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if 'def _populate_steps' in line:
        start_idx = i
        break

if start_idx is not None:
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith('    def ') and '_populate_steps' not in lines[i]:
            end_idx = i
            break

    new_populate_lines = [
        '    def _clear_steps_container(self):',
        '        while self._steps_inner_layout.count() > 0:',
        '            item = self._steps_inner_layout.takeAt(0)',
        '            if item and item.widget():',
        '                item.widget().deleteLater()',
        '        self._step_cards = {}',
        '',
        '    def _create_step_card(self, index: int, step: dict) -> QFrame:',
        '        status_val = step[\'status\']',
        '        status_enum = StepStatus(status_val)',
        '        icon = STATUS_ICONS.get(status_enum, "❓")',
        '        color = STATUS_COLORS.get(status_enum, "#94a3b8")',
        '        status_text = STATUS_TEXT.get(status_val, status_val)',
        '        step_id = step[\'step_id\']',
        '',
        '        approval_type = step.get(\'approval_type\', \'auto\')',
        '        if approval_type == \'manual\':',
        '            approval_text = \'🔒 人工审批\'',
        '            approval_color = "#f59e0b"',
        '        else:',
        '            approval_text = \'⚡ 自动\'',
        '            approval_color = "#06b6d4"',
        '',
        '        card = QFrame()',
        '        card.setObjectName(f"stepCard_{step_id}")',
        '        card.setCursor(QCursor(Qt.PointingHandCursor))',
        '',
        '        card_layout = QHBoxLayout(card)',
        '        card_layout.setContentsMargins(12, 10, 12, 10)',
        '        card_layout.setSpacing(10)',
        '',
        '        circle = QLabel(f"{index + 1}")',
        '        circle.setObjectName("stepCircle")',
        '        circle.setAlignment(Qt.AlignCenter)',
        '        circle.setFixedSize(QSize(32, 32))',
        '        circle.setStyleSheet(f"""',
        '            QLabel#stepCircle {{',
        '                background-color: {color};',
        '                color: white;',
        '                border-radius: 16px;',
        '                font-weight: bold;',
        '                font-size: 13px;',
        '            }}',
        '        """)',
        '        card_layout.addWidget(circle)',
        '',
        '        info_col = QVBoxLayout()',
        '        info_col.setSpacing(3)',
        '',
        '        name_row = QHBoxLayout()',
        '        name_row.setSpacing(6)',
        '        icon_lbl = QLabel(icon)',
        '        icon_lbl.setFixedWidth(20)',
        '        icon_lbl.setStyleSheet("font-size: 14px;")',
        '        name_row.addWidget(icon_lbl)',
        '        name_lbl = QLabel(step[\'name\'])',
        '        name_lbl.setObjectName("stepNameLabel")',
        '        name_lbl.setStyleSheet("font-size: 14px; font-weight: bold; color: #1e293b;")',
        '        name_row.addWidget(name_lbl)',
        '        name_row.addStretch()',
        '        info_col.addLayout(name_row)',
        '',
        '        if step.get(\'description\'):',
        '            desc_lbl = QLabel(step[\'description\'])',
        '            desc_lbl.setWordWrap(True)',
        '            desc_lbl.setStyleSheet("font-size: 12px; color: #64748b;")',
        '            info_col.addWidget(desc_lbl)',
        '',
        '        card_layout.addLayout(info_col, 1)',
        '',
        '        right_col = QVBoxLayout()',
        '        right_col.setSpacing(4)',
        '',
        '        status_badge = QLabel(status_text)',
        '        status_badge.setAlignment(Qt.AlignCenter)',
        '        status_badge.setFixedHeight(22)',
        '        status_badge.setStyleSheet(f"""',
        '            background-color: {color}; color: white;',
        '            border-radius: 11px; padding: 0 10px;',
        '            font-size: 11px; font-weight: bold;',
        '        """)',
        '        right_col.addWidget(status_badge)',
        '',
        '        approval_badge = QLabel(approval_text)',
        '        approval_badge.setAlignment(Qt.AlignCenter)',
        '        approval_badge.setFixedHeight(20)',
        '        approval_badge.setStyleSheet(f"""',
        '            background-color: {approval_color}; color: white;',
        '            border-radius: 10px; padding: 0 8px;',
        '            font-size: 10px; font-weight: bold;',
        '        """)',
        '        right_col.addWidget(approval_badge)',
        '        right_col.addStretch()',
        '',
        '        card_layout.addLayout(right_col)',
        '',
        '        card._step_id = step_id',
        '        card._step_data = step',
        '        card.mousePressEvent = lambda event, sid=step_id: self._on_step_card_clicked(sid)',
        '',
        '        return card',
        '',
        '    def _create_connector(self) -> QFrame:',
        '        connector = QFrame()',
        '        connector.setFixedHeight(16)',
        '        layout = QVBoxLayout(connector)',
        '        layout.setContentsMargins(26, 0, 0, 0)',
        '        line = QFrame()',
        '        line.setFixedWidth(2)',
        '        line.setFixedHeight(14)',
        '        line.setStyleSheet("background-color: #cbd5e1;")',
        '        line_layout = QHBoxLayout()',
        '        line_layout.setContentsMargins(0, 0, 0, 0)',
        '        line_layout.addWidget(line)',
        '        layout.addLayout(line_layout)',
        '        return connector',
        '',
        '    def _populate_steps(self, status: dict):',
        '        self._current_steps = status[\'steps\']',
        '        self._clear_steps_container()',
        '',
        '        card_style = """',
        '            QFrame#stepCard {',
        '                background-color: #f8fafc;',
        '                border: 1px solid #e2e8f0;',
        '                border-radius: 10px;',
        '            }',
        '            QFrame#stepCard:hover {',
        '                background-color: #eff6ff;',
        '                border: 2px solid #3b82f6;',
        '            }',
        '        """',
        '',
        '        steps = status[\'steps\']',
        '        for i, step in enumerate(steps):',
        '            card = self._create_step_card(i, step)',
        '            card.setObjectName("stepCard")',
        '            card.setStyleSheet(card_style)',
        '            self._steps_inner_layout.addWidget(card)',
        '            self._step_cards[step[\'step_id\']] = card',
        '',
        '            if i < len(steps) - 1:',
        '                connector = self._create_connector()',
        '                self._steps_inner_layout.addWidget(connector)',
        '',
        '        self._steps_inner_layout.addStretch()',
    ]
    content = '\n'.join(lines[:start_idx] + new_populate_lines + lines[end_idx:])
    print(f"OK: _populate_steps replaced ({start_idx+1} to {end_idx})")

# 4. Replace _on_step_clicked
lines = content.split('\n')
start_idx = None
end_idx = None

for i, line in enumerate(lines):
    if 'def _on_step_clicked' in line or 'def _on_step_card_clicked' in line:
        start_idx = i
        break

if start_idx is not None:
    for i in range(start_idx + 1, len(lines)):
        if lines[i].startswith('    def ') and '_step_clicked' not in lines[i] and '_step_card' not in lines[i]:
            end_idx = i
            break
    if end_idx is None:
        end_idx = len(lines) - 1

    new_click_lines = [
        '    def _on_step_card_clicked(self, step_id: str):',
        '        for step in self._current_steps:',
        '            if step[\'step_id\'] == step_id:',
        '                self._show_step_detail(step)',
        '                self._highlight_step_card(step_id)',
        '                self._current_step_id = step_id',
        '                self._current_step_name = step[\'name\']',
        '                status = StepStatus(step[\'status\'])',
        '                self._set_btn_state(status)',
        '                break',
        '',
        '    def _highlight_step_card(self, step_id: str):',
        '        card_style_selected = """',
        '            QFrame#stepCard {',
        '                background-color: #dbeafe;',
        '                border: 2px solid #3b82f6;',
        '                border-radius: 10px;',
        '            }',
        '        """',
        '        card_style_normal = """',
        '            QFrame#stepCard {',
        '                background-color: #f8fafc;',
        '                border: 1px solid #e2e8f0;',
        '                border-radius: 10px;',
        '            }',
        '            QFrame#stepCard:hover {',
        '                background-color: #eff6ff;',
        '                border: 2px solid #3b82f6;',
        '            }',
        '        """',
        '        for sid, card in self._step_cards.items():',
        '            if sid == step_id:',
        '                card.setStyleSheet(card_style_selected)',
        '            else:',
        '                card.setStyleSheet(card_style_normal)',
        '',
        '    def _show_step_detail(self, step: dict):',
        '        while self._detail_layout.count() > 0:',
        '            item = self._detail_layout.takeAt(0)',
        '            if item and item.widget():',
        '                item.widget().deleteLater()',
        '',
        '        status_val = step[\'status\']',
        '        status_enum = StepStatus(status_val)',
        '        icon = STATUS_ICONS.get(status_enum, "❓")',
        '        color = STATUS_COLORS.get(status_enum, "#94a3b8")',
        '        status_text = STATUS_TEXT.get(status_val, status_val)',
        '',
        '        title_row = QHBoxLayout()',
        '        title_row.setSpacing(8)',
        '        title_icon = QLabel(icon)',
        '        title_icon.setStyleSheet("font-size: 20px;")',
        '        title_row.addWidget(title_icon)',
        '        title_lbl = QLabel(f"<b style=\'font-size:16px;color:#1e293b;\'>{step[\'name\']}</b>")',
        '        title_row.addWidget(title_lbl)',
        '        title_row.addStretch()',
        '',
        '        status_badge = QLabel(status_text)',
        '        status_badge.setAlignment(Qt.AlignCenter)',
        '        status_badge.setFixedHeight(26)',
        '        status_badge.setStyleSheet(f"""',
        '            background-color: {color}; color: white;',
        '            border-radius: 13px; padding: 0 12px;',
        '            font-size: 12px; font-weight: bold;',
        '        """)',
        '        title_row.addWidget(status_badge)',
        '',
        '        title_widget = QWidget()',
        '        title_widget.setLayout(title_row)',
        '        self._detail_layout.addWidget(title_widget)',
        '',
        '        sep = QFrame()',
        '        sep.setFrameShape(QFrame.HLine)',
        '        sep.setStyleSheet("background-color: #e2e8f0; max-height: 1px;")',
        '        self._detail_layout.addWidget(sep)',
        '',
        '        info_box = QFrame()',
        '        info_box.setStyleSheet("background-color: #f8fafc; border-radius: 8px; padding: 8px;")',
        '        info_layout = QVBoxLayout(info_box)',
        '        info_layout.setContentsMargins(12, 10, 12, 10)',
        '        info_layout.setSpacing(6)',
        '',
        '        info_rows = []',
        '        info_rows.append(("📋 步骤名称", step[\'name\']))',
        '        if step.get(\'description\'):',
        '            info_rows.append(("📄 步骤说明", step[\'description\']))',
        '',
        '        approval_type = step.get(\'approval_type\', \'auto\')',
        '        if approval_type == \'manual\':',
        '            info_rows.append(("🔐 审批方式", "人工审批（需要人工确认）"))',
        '        else:',
        '            info_rows.append(("⚡ 审批方式", "自动审批（系统自动通过）"))',
        '',
        '        info_rows.append(("🏷️ 状态", status_text))',
        '',
        '        for label_text, value_text in info_rows:',
        '            row = QHBoxLayout()',
        '            row.setSpacing(8)',
        '            lbl = QLabel(label_text)',
        '            lbl.setStyleSheet("color: #64748b; font-size: 12px;")',
        '            lbl.setFixedWidth(90)',
        '            row.addWidget(lbl)',
        '            val = QLabel(value_text)',
        '            val.setStyleSheet("color: #1e293b; font-size: 12px; font-weight: 500;")',
        '            val.setWordWrap(True)',
        '            val.setTextInteractionFlags(Qt.TextSelectableByMouse)',
        '            row.addWidget(val, 1)',
        '            info_layout.addLayout(row)',
        '',
        '        self._detail_layout.addWidget(info_box)',
        '',
        '        if step.get(\'tool_name\'):',
        '            tool_box = QFrame()',
        '            tool_box.setStyleSheet("background-color: #eff6ff; border-radius: 8px; padding: 8px;")',
        '            tool_layout = QVBoxLayout(tool_box)',
        '            tool_layout.setContentsMargins(12, 10, 12, 10)',
        '            tool_layout.setSpacing(6)',
        '',
        '            tool_title = QLabel("🔧 使用的工具")',
        '            tool_title.setStyleSheet("color: #1e40af; font-size: 13px; font-weight: bold;")',
        '            tool_layout.addWidget(tool_title)',
        '',
        '            tool_name_lbl = QLabel(f"工具名称：{step[\'tool_name\']}")',
        '            tool_name_lbl.setStyleSheet("color: #1e293b; font-size: 12px;")',
        '            tool_layout.addWidget(tool_name_lbl)',
        '',
        '            if step.get(\'tool_args\'):',
        '                args_text = json.dumps(step[\'tool_args\'], ensure_ascii=False, indent=2)',
        '                args_lbl = QLabel(f"参数：\\n{args_text}")',
        '                args_lbl.setStyleSheet("color: #475569; font-size: 12px; font-family: Consolas, monospace;")',
        '                args_lbl.setWordWrap(True)',
        '                args_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)',
        '                tool_layout.addWidget(args_lbl)',
        '',
        '            self._detail_layout.addWidget(tool_box)',
        '',
        '        if step.get(\'result\'):',
        '            result = step[\'result\']',
        '            result_box = QFrame()',
        '            result_box.setStyleSheet("background-color: #f0fdf4; border-radius: 8px; padding: 8px;")',
        '            result_layout = QVBoxLayout(result_box)',
        '            result_layout.setContentsMargins(12, 10, 12, 10)',
        '            result_layout.setSpacing(6)',
        '',
        '            result_title = QLabel("📊 执行结果")',
        '            result_title.setStyleSheet("color: #166534; font-size: 13px; font-weight: bold;")',
        '            result_layout.addWidget(result_title)',
        '',
        '            if isinstance(result, dict):',
        '                if result.get(\'ok\'):',
        '                    ok_lbl = QLabel("✅ 执行成功")',
        '                    ok_lbl.setStyleSheet("color: #22c55e; font-size: 12px; font-weight: bold;")',
        '                    result_layout.addWidget(ok_lbl)',
        '                else:',
        '                    fail_lbl = QLabel("❌ 执行失败")',
        '                    fail_lbl.setStyleSheet("color: #ef4444; font-size: 12px; font-weight: bold;")',
        '                    result_layout.addWidget(fail_lbl)',
        '',
        '                for key, val in result.items():',
        '                    if key == \'ok\':',
        '                        continue',
        '                    row = QHBoxLayout()',
        '                    row.setSpacing(8)',
        '                    k_lbl = QLabel(f"{key}:")',
        '                    k_lbl.setStyleSheet("color: #64748b; font-size: 11px;")',
        '                    k_lbl.setFixedWidth(70)',
        '                    row.addWidget(k_lbl)',
        '                    v_lbl = QLabel(str(val))',
        '                    v_lbl.setStyleSheet("color: #1e293b; font-size: 12px;")',
        '                    v_lbl.setWordWrap(True)',
        '                    v_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)',
        '                    row.addWidget(v_lbl, 1)',
        '                    result_layout.addLayout(row)',
        '            else:',
        '                result_lbl = QLabel(str(result))',
        '                result_lbl.setStyleSheet("color: #166534; font-size: 12px;")',
        '                result_lbl.setWordWrap(True)',
        '                result_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)',
        '                result_layout.addWidget(result_lbl)',
        '',
        '            self._detail_layout.addWidget(result_box)',
        '',
        '        if step.get(\'reviewer\') or step.get(\'review_comment\'):',
        '            review_box = QFrame()',
        '            review_box.setStyleSheet("background-color: #fffbeb; border-radius: 8px; padding: 8px;")',
        '            review_layout = QVBoxLayout(review_box)',
        '            review_layout.setContentsMargins(12, 10, 12, 10)',
        '            review_layout.setSpacing(6)',
        '',
        '            review_title = QLabel("✍️ 审批信息")',
        '            review_title.setStyleSheet("color: #92400e; font-size: 13px; font-weight: bold;")',
        '            review_layout.addWidget(review_title)',
        '',
        '            if step.get(\'reviewer\'):',
        '                r_lbl = QLabel(f"审批人：{step[\'reviewer\']}")',
        '                r_lbl.setStyleSheet("color: #1e293b; font-size: 12px;")',
        '                review_layout.addWidget(r_lbl)',
        '',
        '            if step.get(\'review_comment\'):',
        '                c_lbl = QLabel(f"审批意见：{step[\'review_comment\']}")',
        '                c_lbl.setStyleSheet("color: #475569; font-size: 12px;")',
        '                c_lbl.setWordWrap(True)',
        '                c_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)',
        '                review_layout.addWidget(c_lbl)',
        '',
        '            self._detail_layout.addWidget(review_box)',
        '',
        '        time_box = QFrame()',
        '        time_box.setStyleSheet("background-color: #f1f5f9; border-radius: 8px; padding: 8px;")',
        '        time_layout = QVBoxLayout(time_box)',
        '        time_layout.setContentsMargins(12, 10, 12, 10)',
        '        time_layout.setSpacing(6)',
        '',
        '        time_title = QLabel("⏰ 时间信息")',
        '        time_title.setStyleSheet("color: #475569; font-size: 13px; font-weight: bold;")',
        '        time_layout.addWidget(time_title)',
        '',
        '        if step.get(\'started_at\'):',
        '            s_lbl = QLabel(f"开始时间：{step[\'started_at\']}")',
        '            s_lbl.setStyleSheet("color: #1e293b; font-size: 12px;")',
        '            time_layout.addWidget(s_lbl)',
        '',
        '        if step.get(\'completed_at\'):',
        '            c_lbl = QLabel(f"完成时间：{step[\'completed_at\']}")',
        '            c_lbl.setStyleSheet("color: #1e293b; font-size: 12px;")',
        '            time_layout.addWidget(c_lbl)',
        '',
        '        self._detail_layout.addWidget(time_box)',
        '        self._detail_layout.addStretch()',
    ]
    content = '\n'.join(lines[:start_idx] + new_click_lines + lines[end_idx:])
    print(f"OK: _on_step_clicked replaced ({start_idx+1} to {end_idx})")

# 5. Add _step_cards initialization in __init__
old_init = "        self._current_step_id = None"
new_init = "        self._step_cards = {}\n        self._current_step_id = None"
content = content.replace(old_init, new_init, 1)

# Write the modified file
with open(r'd:\yindun\yindun-agent\yindun\gui\workflow_panel.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("File written successfully")
