# -*- coding: utf-8 -*-
"""
隐盾审计日志模块 - 全链路审计黑匣子
基于哈希链的不可篡改审计日志系统
"""
import json
import hashlib
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional
from enum import Enum


class AuditEventType(Enum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    PRIVACY_SENSITIVE = "privacy_sensitive"
    ACCESS_CONTROL = "access_control"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


class AuditSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    SECURITY = "security"


class AuditEntry:
    def __init__(self, event_type: AuditEventType, severity: AuditSeverity,
                 message: str, details: dict = None, session_id: str = None):
        self.timestamp = datetime.now().isoformat()
        self.event_type = event_type.value
        self.severity = severity.value
        self.message = message
        self.details = details or {}
        self.session_id = session_id
        self.previous_hash = ""
        self.entry_hash = ""

    def compute_hash(self, previous_hash: str = "") -> str:
        self.previous_hash = previous_hash
        data = json.dumps({
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "session_id": self.session_id,
            "previous_hash": previous_hash
        }, ensure_ascii=False, sort_keys=True)
        self.entry_hash = hashlib.sha256(data.encode('utf-8')).hexdigest()
        return self.entry_hash

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "session_id": self.session_id,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AuditEntry':
        entry = cls(
            event_type=AuditEventType(data.get("event_type")),
            severity=AuditSeverity(data.get("severity")),
            message=data.get("message", ""),
            details=data.get("details", {}),
            session_id=data.get("session_id")
        )
        entry.timestamp = data.get("timestamp", "")
        entry.previous_hash = data.get("previous_hash", "")
        entry.entry_hash = data.get("entry_hash", "")
        return entry


class AuditLog:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._entries: List[AuditEntry] = []
        self._storage_path = Path(__file__).resolve().parents[2] / "audit_logs"
        self._storage_path.mkdir(exist_ok=True)
        self._current_session_id = None
        self._load_logs()
        self._initialized = True

    def _load_logs(self):
        log_file = self._storage_path / "audit_chain.json"
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for entry_data in data:
                        entry = AuditEntry.from_dict(entry_data)
                        self._entries.append(entry)
            except Exception:
                pass

    def _save_logs(self):
        log_file = self._storage_path / "audit_chain.json"
        try:
            with open(log_file, 'w', encoding='utf-8') as f:
                json.dump([e.to_dict() for e in self._entries], f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def set_session_id(self, session_id: str):
        self._current_session_id = session_id

    def add_entry(self, event_type: AuditEventType, severity: AuditSeverity,
                  message: str, details: dict = None):
        entry = AuditEntry(
            event_type=event_type,
            severity=severity,
            message=message,
            details=details,
            session_id=self._current_session_id
        )
        prev_hash = self._entries[-1].entry_hash if self._entries else ""
        entry.compute_hash(prev_hash)
        self._entries.append(entry)
        self._save_logs()
        return entry

    def log_tool_call(self, tool_name: str, tool_args: dict, target_path: str = ""):
        return self.add_entry(
            AuditEventType.TOOL_CALL,
            AuditSeverity.INFO,
            f"工具调用: {tool_name}",
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "target_path": target_path
            }
        )

    def log_tool_result(self, tool_name: str, success: bool, result: str = ""):
        return self.add_entry(
            AuditEventType.TOOL_RESULT,
            AuditSeverity.WARNING if not success else AuditSeverity.INFO,
            f"工具执行{'成功' if success else '失败'}: {tool_name}",
            {
                "tool_name": tool_name,
                "success": success,
                "result": result[:500] if result else ""
            }
        )

    def log_llm_input(self, content: str, has_privacy: bool = False):
        return self.add_entry(
            AuditEventType.LLM_INPUT,
            AuditSeverity.INFO,
            f"LLM输入{'(含隐私脱敏)' if has_privacy else ''}",
            {
                "content_length": len(content),
                "has_privacy": has_privacy
            }
        )

    def log_llm_output(self, content: str, token_count: int = 0):
        return self.add_entry(
            AuditEventType.LLM_OUTPUT,
            AuditSeverity.INFO,
            "LLM输出",
            {
                "content_length": len(content),
                "token_count": token_count
            }
        )

    def log_privacy_sensitive(self, entity_type: str, entity_value: str, action: str = "detected"):
        return self.add_entry(
            AuditEventType.PRIVACY_SENSITIVE,
            AuditSeverity.CRITICAL if action == "leak" else AuditSeverity.SECURITY,
            f"隐私敏感事件: {entity_type} {action}",
            {
                "entity_type": entity_type,
                "entity_value": entity_value,
                "action": action
            }
        )

    def log_access_control(self, action: str, path: str, approved: bool):
        return self.add_entry(
            AuditEventType.ACCESS_CONTROL,
            AuditSeverity.CRITICAL if not approved else AuditSeverity.SECURITY,
            f"访问控制{'通过' if approved else '拒绝'}: {action} {path}",
            {
                "action": action,
                "path": path,
                "approved": approved
            }
        )

    def log_session_start(self, session_id: str):
        self.set_session_id(session_id)
        return self.add_entry(
            AuditEventType.SESSION_START,
            AuditSeverity.INFO,
            f"会话开始: {session_id}",
            {"session_id": session_id}
        )

    def log_session_end(self):
        entry = self.add_entry(
            AuditEventType.SESSION_END,
            AuditSeverity.INFO,
            f"会话结束: {self._current_session_id}",
            {"session_id": self._current_session_id}
        )
        self._current_session_id = None
        return entry

    def verify_chain(self) -> bool:
        for i, entry in enumerate(self._entries):
            if i == 0:
                expected_hash = hashlib.sha256(
                    json.dumps({
                        "timestamp": entry.timestamp,
                        "event_type": entry.event_type,
                        "severity": entry.severity,
                        "message": entry.message,
                        "details": entry.details,
                        "session_id": entry.session_id,
                        "previous_hash": ""
                    }, ensure_ascii=False, sort_keys=True).encode('utf-8')
                ).hexdigest()
            else:
                prev_entry = self._entries[i - 1]
                expected_hash = hashlib.sha256(
                    json.dumps({
                        "timestamp": entry.timestamp,
                        "event_type": entry.event_type,
                        "severity": entry.severity,
                        "message": entry.message,
                        "details": entry.details,
                        "session_id": entry.session_id,
                        "previous_hash": prev_entry.entry_hash
                    }, ensure_ascii=False, sort_keys=True).encode('utf-8')
                ).hexdigest()
            if entry.entry_hash != expected_hash:
                return False
        return True

    def get_entries(self, filters: dict = None) -> List[AuditEntry]:
        result = self._entries[:]
        if filters:
            if "event_type" in filters:
                result = [e for e in result if e.event_type == filters["event_type"]]
            if "severity" in filters:
                result = [e for e in result if e.severity == filters["severity"]]
            if "session_id" in filters:
                result = [e for e in result if e.session_id == filters["session_id"]]
            if "start_time" in filters:
                result = [e for e in result if e.timestamp >= filters["start_time"]]
            if "end_time" in filters:
                result = [e for e in result if e.timestamp <= filters["end_time"]]
        return result

    def get_stats(self) -> dict:
        stats = {
            "total_entries": len(self._entries),
            "by_type": {},
            "by_severity": {},
            "by_session": {},
            "chain_valid": self.verify_chain()
        }
        for entry in self._entries:
            stats["by_type"][entry.event_type] = stats["by_type"].get(entry.event_type, 0) + 1
            stats["by_severity"][entry.severity] = stats["by_severity"].get(entry.severity, 0) + 1
            if entry.session_id:
                stats["by_session"][entry.session_id] = stats["by_session"].get(entry.session_id, 0) + 1
        return stats

    def export_report(self, format: str = "json", filters: dict = None) -> str:
        entries = self.get_entries(filters)
        if format == "json":
            report = {
                "generated_at": datetime.now().isoformat(),
                "chain_valid": self.verify_chain(),
                "stats": self.get_stats(),
                "entries": [e.to_dict() for e in entries]
            }
            return json.dumps(report, ensure_ascii=False, indent=2)
        elif format == "html":
            stats = self.get_stats()
            html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>隐盾审计报告</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f8fafc; }}
        .header {{ background: linear-gradient(135deg, #07c160, #06ad56); color: white; padding: 24px; border-radius: 12px; margin-bottom: 20px; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .header p {{ opacity: 0.9; margin: 8px 0 0; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }}
        .stat-card {{ background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .stat-card .label {{ color: #64748b; font-size: 12px; }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; color: #1e293b; }}
        .stat-card.valid .value {{ color: #07c160; }}
        .stat-card.invalid .value {{ color: #ef4444; }}
        .table-container {{ background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
        th {{ background: #f1f5f9; font-weight: 600; color: #475569; font-size: 12px; }}
        td {{ font-size: 13px; color: #334155; }}
        .severity {{ padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
        .severity-info {{ background: #dbeafe; color: #1e40af; }}
        .severity-warning {{ background: #fef3c7; color: #d97706; }}
        .severity-critical {{ background: #fee2e2; color: #dc2626; }}
        .severity-security {{ background: #e0e7ff; color: #6366f1; }}
        .event-type {{ padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; background: #f1f5f9; color: #475569; }}
        .details {{ max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: monospace; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🛡️ 隐盾安全审计报告</h1>
        <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <div class="stats-grid">
        <div class="stat-card"><div class="label">总日志数</div><div class="value">{stats['total_entries']}</div></div>
        <div class="stat-card valid"><div class="label">链条完整性</div><div class="value">{'✅ 有效' if stats['chain_valid'] else '❌ 无效'}</div></div>
        <div class="stat-card"><div class="label">工具调用</div><div class="value">{stats['by_type'].get('tool_call', 0)}</div></div>
        <div class="stat-card"><div class="label">隐私事件</div><div class="value">{stats['by_type'].get('privacy_sensitive', 0)}</div></div>
    </div>
    <div class="table-container">
        <table>
            <thead>
                <tr><th>时间</th><th>事件类型</th><th>严重程度</th><th>消息</th><th>详情</th></tr>
            </thead>
            <tbody>
"""
            for entry in entries:
                severity_class = f"severity-{entry.severity}"
                html += f"""<tr>
                    <td>{entry.timestamp}</td>
                    <td><span class="event-type">{entry.event_type}</span></td>
                    <td><span class="severity {severity_class}">{entry.severity}</span></td>
                    <td>{entry.message}</td>
                    <td class="details">{json.dumps(entry.details, ensure_ascii=False)}</td>
                </tr>"""
            html += """</tbody></table></div></body></html>"""
            return html
        return ""

    def save_report(self, format: str = "json", filename: str = None) -> str:
        content = self.export_report(format)
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"audit_report_{timestamp}.{format}"
        filepath = self._storage_path / filename
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        return str(filepath)