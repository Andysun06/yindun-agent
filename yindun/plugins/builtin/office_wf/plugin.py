# -*- coding: utf-8 -*-
"""内置插件 · 办公工作流模板包（workflow_template）

说明（为什么这两个模板值得存在）：
  · 内核自带三个模板，其中"周报生成"含一个演示步骤（人工审核，返回模拟结果）；
    本插件补的"本周工作总结"用真实步骤替换它，全链路不再有演示环节。
  · "代码安全体检（增强版）"在内核模板基础上补了"生成修复建议 + 导出报告"两步。

安全约束（由内核执行，插件无法绕过，见 core/workflow.py 的 register_external_template）：
  · 模板 id 不能覆盖内核模板或其它插件的模板；
  · 每个 tool_name 必须是引擎已注册的执行器，否则整条模板被拒绝；
  · 高危工具（写盘 / 删除 / 执行命令 / 导出文件）的步骤**强制人工审批**。
    下面"导出报告"一步故意声明 approval=auto，用来演示内核会把它纠正为人工审批——
    插件只能让流程更自动化，不能降低审批强度。
"""
from __future__ import annotations

PLUGIN_ID = "office_wf"
PLUGIN_DATA_DIR = ""

_TEMPLATES = [
    {
        "id": "ext_office_code_audit",
        "name": "代码安全体检（增强版）",
        "description": "分析项目 → 安全扫描 → 风险定级 → 生成修复建议 → 汇总报告 → 导出。"
                       "全部为真实步骤；最后一步的审批由内核强制。",
        "path_var": "project_path",
        "steps": [
            {"name": "分析项目结构", "description": "读取目标目录的代码结构与关键文件",
             "tool_name": "analyze_project", "tool_args": {"target": "{{project_path}}"},
             "approval": "auto"},
            {"name": "安全扫描", "description": "按 OWASP Top 10 规则检查常见漏洞",
             "tool_name": "security_scan", "tool_args": {"rules": "owasp_top10"},
             "approval": "auto"},
            {"name": "风险定级", "description": "对发现的漏洞做风险定级与优先级排序",
             "tool_name": "risk_assessment", "tool_args": {}, "approval": "auto"},
            {"name": "生成修复建议", "description": "针对每个问题给出具体修复做法",
             "tool_name": "generate_fix", "tool_args": {}, "approval": "manual"},
            {"name": "汇总安全报告", "description": "输出完整的安全评估报告正文",
             "tool_name": "generate_report", "tool_args": {"type": "security_audit"},
             "approval": "manual"},
            {"name": "导出报告", "description": "把报告写成文件（高危步骤：审批由内核强制）",
             "tool_name": "export_file", "tool_args": {"format": "md"},
             "approval": "auto"},
        ],
    },
    {
        "id": "ext_office_weekly_real",
        "name": "本周工作总结（无演示步骤）",
        "description": "收集本周记录 → 整理结构 → 生成周报草稿 → 导出。"
                       "与内核自带周报模板的区别：不含演示步骤。",
        "path_var": "",
        "steps": [
            {"name": "收集工作记录", "description": "从审计日志中收集最近的工具调用与会话记录",
             "tool_name": "query_history", "tool_args": {"time_range": "this_week"},
             "approval": "auto"},
            {"name": "整理周报结构", "description": "按工作模块归类，形成周报骨架",
             "tool_name": "organize_content", "tool_args": {"structure": "weekly"},
             "approval": "auto"},
            {"name": "生成周报草稿", "description": "生成含成果、问题、下周计划的初稿",
             "tool_name": "generate_report", "tool_args": {"type": "weekly_report"},
             "approval": "manual"},
            {"name": "导出最终版本", "description": "审核通过后导出（高危步骤：审批由内核强制）",
             "tool_name": "export_file", "tool_args": {"format": "docx"},
             "approval": "manual"},
        ],
    },
]


def workflow_template(context):
    """返回本插件贡献的模板列表（内核会逐条校验后注册）。"""
    return {"templates": [dict(item) for item in _TEMPLATES]}
