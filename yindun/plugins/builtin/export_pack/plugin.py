# -*- coding: utf-8 -*-
"""内置插件 · 导出格式扩展（export_renderer）

提供两种插件渲染的导出格式：
  · audit    → markdown  ：审计报告（已脱敏）渲染成 Markdown 表格
  · workflow → html_card ：工作流执行记录渲染成带样式的卡片式 HTML

★ 格式命名约定：内核已占用的格式名（audit 的 json/html、workflow 的 md/html/json/docx）
  不允许插件占用——宿主会在加载时直接拒绝，避免"内核兜底"变成"内核被顶替"。
  插件用自己的格式名（如 html_card），并在 manifest 的 format_labels 里给中文标签。

契约：`export_renderer(context)`，context = {"kind", "format", "payload", "generated_at"}
  · kind    ：audit | workflow
  · payload ：内核已脱敏的数据（audit=审计报告结构；workflow=实例状态 + 报告正文）
  · 返回 {"text", "ext", "suggested_name"}；返回 None 表示本插件不处理该请求。

内核兜底：内核自带的导出格式始终可用。本插件停用后，导出菜单里只是少这两个选项，
不会出现"点了导出没反应"。
"""
from __future__ import annotations

import html as _html

PLUGIN_ID = "export_pack"
PLUGIN_DATA_DIR = ""

_MAX_ROWS = 500


def _cell(value, limit: int = 120) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    text = text.replace("|", "\\|")
    return text[:limit]


def _audit_markdown(payload: dict) -> dict:
    stats = payload.get("stats") if isinstance(payload.get("stats"), dict) else {}
    entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    chain_valid = bool(payload.get("chain_valid", stats.get("chain_valid", False)))
    generated_at = str(payload.get("generated_at") or "")

    lines = [
        "# 隐盾安全审计报告",
        "",
        f"- 生成时间：{generated_at}",
        f"- 事件总数：{stats.get('total_entries', len(entries))}",
        f"- 审计链完整性：{'有效' if chain_valid else '无效（需人工核查）'}",
        "",
    ]
    by_type = stats.get("by_type") if isinstance(stats.get("by_type"), dict) else {}
    if by_type:
        lines += ["## 事件类型分布", "", "| 类型 | 数量 |", "| --- | --- |"]
        for key, value in sorted(by_type.items(), key=lambda kv: -int(kv[1] or 0)):
            lines.append(f"| {_cell(key, 40)} | {int(value or 0)} |")
        lines.append("")
    by_severity = stats.get("by_severity") if isinstance(stats.get("by_severity"), dict) else {}
    if by_severity:
        lines += ["## 严重度分布", "", "| 严重度 | 数量 |", "| --- | --- |"]
        for key, value in sorted(by_severity.items(), key=lambda kv: -int(kv[1] or 0)):
            lines.append(f"| {_cell(key, 40)} | {int(value or 0)} |")
        lines.append("")

    lines += ["## 事件明细（最近 %d 条，均为脱敏预览）" % min(len(entries), _MAX_ROWS), "",
              "| 时间 | 类型 | 严重度 | 说明 | 摘要 |", "| --- | --- | --- | --- | --- |"]
    for item in list(entries)[-_MAX_ROWS:]:
        if not isinstance(item, dict):
            continue
        lines.append("| {} | {} | {} | {} | {} |".format(
            _cell(item.get("timestamp") or item.get("time"), 32),
            _cell(item.get("event_type") or item.get("type"), 32),
            _cell(item.get("severity"), 16),
            _cell(item.get("message"), 120),
            _cell((item.get("details") or {}).get("preview") if isinstance(item.get("details"), dict)
                  else item.get("preview"), 120),
        ))
    lines += ["", "> 本报告由「导出格式扩展」插件渲染；数据在内核侧已于写入时脱敏。", ""]
    return {"text": "\n".join(lines), "ext": "md",
            "suggested_name": "audit_report_plugin.md"}


def _workflow_html(payload: dict) -> dict:
    status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
    instance_id = str(payload.get("instance_id") or status.get("instance_id") or "")
    template_name = str(status.get("template_name") or payload.get("title") or "工作流报告")
    progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
    steps = status.get("steps") if isinstance(status.get("steps"), list) else []
    body = str(payload.get("report_markdown") or payload.get("body") or "")
    source_plugin = str(status.get("source_plugin") or "")

    rows = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        demo = "（演示）" if step.get("demo") else ""
        forced = " · 审批由内核强制" if step.get("forced_approval") else ""
        rows.append(
            "<tr><td>{}</td><td>{}</td><td>{}{}</td><td>{}</td></tr>".format(
                _html.escape(str(step.get("name") or "")),
                _html.escape(str(step.get("status") or "")),
                _html.escape(str(step.get("tool_name") or "")),
                _html.escape(demo + forced),
                _html.escape(str(step.get("result") or step.get("error") or "")[:200]),
            )
        )
    table = ("<table><thead><tr><th>步骤</th><th>状态</th><th>工具</th><th>结果摘要</th></tr></thead>"
             "<tbody>{}</tbody></table>".format("".join(rows))) if rows else "<p>（无步骤记录）</p>"

    badges = []
    if source_plugin:
        badges.append(f"模板来源：插件 {source_plugin}")
    if progress:
        badges.append(f"进度：{progress.get('completed', 0)}/{progress.get('total', 0)} 步")
    document = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>{title}</title>
<style>
 :root {{ color-scheme: light; }}
 body {{ font-family: "Microsoft YaHei UI", -apple-system, "Segoe UI", sans-serif;
        margin: 0; padding: 32px; background: #f7f7f8; color: #18181b; line-height: 1.65; }}
 .card {{ max-width: 900px; margin: 0 auto; background: #fff; border: 1px solid #e4e4e7;
         border-radius: 6px; padding: 28px 32px; }}
 h1 {{ font-size: 22px; margin: 0 0 4px; }}
 h2 {{ font-size: 15px; margin: 28px 0 10px; color: #3f3f46; }}
 .meta {{ color: #71717a; font-size: 12px; margin-bottom: 18px; }}
 table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
 th, td {{ border: 1px solid #e4e4e7; padding: 8px 10px; text-align: left; vertical-align: top; }}
 th {{ background: #fafafa; font-weight: 600; color: #52525b; }}
 pre {{ white-space: pre-wrap; word-break: break-word; font-size: 13px;
       background: #fafafa; border: 1px solid #e4e4e7; border-radius: 4px; padding: 14px; }}
 .note {{ color: #71717a; font-size: 12px; margin-top: 22px; }}
</style></head><body><div class="card">
 <h1>{title}</h1>
 <div class="meta">实例：{instance} ｜ {badges} ｜ 生成时间：{generated_at}</div>
 <h2>执行步骤</h2>
 {table}
 <h2>报告正文</h2>
 <pre>{body}</pre>
 <div class="note">本报告由「导出格式扩展」插件渲染；正文在落盘前已由内核统一脱敏。</div>
</div></body></html>
""".format(
        title=_html.escape(template_name),
        instance=_html.escape(instance_id or "—"),
        badges=_html.escape(" ｜ ".join(badges) or "—"),
        generated_at=_html.escape(str(payload.get("generated_at") or "")),
        table=table,
        body=_html.escape(body),
    )
    return {"text": document, "ext": "html",
            "suggested_name": f"{template_name or 'workflow'}_plugin.html"}


def export_renderer(context):
    """钩子入口（函数名必须与 manifest 里声明的钩子同名，宿主按名字查找）。"""
    ctx = context if isinstance(context, dict) else {}
    kind = str(ctx.get("kind") or "").lower()
    fmt = str(ctx.get("format") or "").lower()
    payload = ctx.get("payload") if isinstance(ctx.get("payload"), dict) else {}
    if kind == "audit" and fmt == "markdown":
        return _audit_markdown(payload)
    if kind == "workflow" and fmt == "html_card":
        return _workflow_html(payload)
    return None


# 便于人工阅读/单测时按语义名调用；宿主只认钩子同名函数
render = export_renderer
