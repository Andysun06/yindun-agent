# -*- coding: utf-8 -*-
"""
隐盾模型行为画像分析器
=======================
基于 AuditLog 的日志数据，分析每个会话/模型的工具调用行为模式，
识别异常行为并生成画像报告。

检测规则：
  - 高频操作检测（短时间内大量写入/删除）
  - 敏感路径访问检测（访问密钥/配置目录）
  - 跨目录跳跃检测（在同一会话中访问多个不相关目录）
  - 非工作时间操作检测
  - 工具调用序列异常检测

使用示例：
    from yindun.core.audit_log import AuditLog
    from yindun.core.behavior_analyzer import BehaviorAnalyzer

    analyzer = BehaviorAnalyzer()
    profile = analyzer.build_profile("session_abc")
    anomalies = analyzer.detect_anomalies("session_abc")
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import Counter, defaultdict


@dataclass
class BehaviorProfile:
    """会话行为画像"""
    session_id: str
    model_name: str = ""
    time_range: Tuple[str, str] = ("", "")

    # 操作统计
    total_tool_calls: int = 0
    tool_call_breakdown: Dict[str, int] = field(default_factory=dict)       # tool_name → count
    operation_breakdown: Dict[str, int] = field(default_factory=dict)       # read/write/delete/execute → count

    # 路径统计
    unique_paths: int = 0
    accessed_paths: List[str] = field(default_factory=list)
    sensitive_paths_accessed: List[str] = field(default_factory=list)       # 访问的敏感路径
    path_jumps: int = 0                                                      # 跨路径跳跃次数

    # 风险指标
    risk_score: int = 0
    high_freq_operations: List[dict] = field(default_factory=list)          # 高频操作
    risk_factors: List[str] = field(default_factory=list)                    # 风险因子描述

    # LLM 统计
    total_llm_calls: int = 0
    avg_input_length: int = 0
    avg_output_length: int = 0

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "model_name": self.model_name,
            "time_range": list(self.time_range),
            "total_tool_calls": self.total_tool_calls,
            "tool_call_breakdown": self.tool_call_breakdown,
            "operation_breakdown": self.operation_breakdown,
            "unique_paths": self.unique_paths,
            "sensitive_paths_accessed": self.sensitive_paths_accessed[:50],
            "path_jumps": self.path_jumps,
            "risk_score": self.risk_score,
            "high_freq_operations": self.high_freq_operations[:20],
            "risk_factors": self.risk_factors,
            "total_llm_calls": self.total_llm_calls,
            "avg_input_length": self.avg_input_length,
            "avg_output_length": self.avg_output_length
        }

    def summary(self) -> str:
        """生成画像摘要"""
        lines = [
            "=" * 50,
            f"模型行为画像: {self.session_id}",
            f"模型: {self.model_name}",
            f"时间: {self.time_range[0]} ~ {self.time_range[1]}",
            "-" * 50,
            f"工具调用: {self.total_tool_calls} 次",
            f"  {self.tool_call_breakdown}",
            f"访问路径: {self.unique_paths} 个",
            f"敏感路径: {len(self.sensitive_paths_accessed)} 个",
            f"风险评估: {self.risk_score}/100",
            "-" * 50,
        ]
        if self.risk_factors:
            lines.append("风险因子:")
            for f in self.risk_factors:
                lines.append(f"  ⚠ {f}")
        else:
            lines.append("✓ 未检测到异常行为")
        lines.append("=" * 50)
        return "\n".join(lines)


@dataclass
class AnomalyRecord:
    """异常记录"""
    session_id: str
    timestamp: str
    anomaly_type: str       # "high_frequency" | "sensitive_access" | "path_hopping" | "off_hours"
    severity: str           # "low" | "medium" | "high" | "critical"
    description: str
    details: dict = field(default_factory=dict)


class BehaviorAnalyzer:
    """
    模型行为画像分析器

    用法：
        analyzer = BehaviorAnalyzer()
        # 从 AuditLog 获取条目
        entries = audit_log.get_entries({"session_id": session_id})
        profile = analyzer.build_profile(entries, session_id)
        anomalies = analyzer.detect_anomalies(entries, session_id)
    """

    # 高风险文件路径模式
    SENSITIVE_PATHS = {
        ".ssh", ".aws", ".config/gcloud", ".env", "credentials",
        "id_rsa", "id_ed25519", "id_ecdsa", "authorized_keys",
        "config.ini", "application.yml", ".git-credentials", ".npmrc",
        "/etc/", "/var/", "C:\\Windows\\", "C:\\Windows\\System32\\",
        "AppData\\Roaming", "AppData\\Local"
    }

    # 高频操作阈值
    HIGH_FREQ_THRESHOLD = 10  # 短时间内超过10次同类型操作
    HIGH_FREQ_WINDOW_SEC = 60  # 60秒窗口

    # 路径跳跃阈值
    PATH_JUMP_THRESHOLD = 5   # 跳跃超过5个不相关目录

    # 非工作时间（北京时间 23:00 - 06:00）
    OFF_HOURS_START = 23
    OFF_HOURS_END = 6

    def __init__(self):
        pass

    def build_profile(self, entries: list, session_id: str,
                      model_name: str = "") -> BehaviorProfile:
        """
        基于审计日志条目构建行为画像。

        参数：
            entries: AuditEntry 列表（已按 session_id 过滤）
            session_id: 会话 ID
            model_name: 模型名称
        """
        profile = BehaviorProfile(
            session_id=session_id,
            model_name=model_name
        )

        if not entries:
            return profile

        # 时间范围
        profile.time_range = (entries[0].timestamp, entries[-1].timestamp)

        # 工具调用统计
        tool_counter = Counter()
        op_counter = Counter()
        paths_set: Set[str] = set()
        sensitive_paths: List[str] = []
        tool_times: List[Tuple[str, str]] = []           # [(tool_name, timestamp), ...]
        prev_paths: List[str] = []
        llm_inputs = 0
        total_input_chars = 0
        total_output_chars = 0

        for entry in entries:
            details = entry.details if hasattr(entry, "details") else {}

            if hasattr(entry, "event_type") and hasattr(entry, "tool_name"):
                pass

        # 实际处理 entries（基于 dict 格式，因为 AuditEntry 对象可能不完整）
        for entry in entries:
            etype = entry.get("event_type") if isinstance(entry, dict) else getattr(entry, "event_type", "")
            details = entry.get("details", {}) if isinstance(entry, dict) else getattr(entry, "details", {})
            timestamp = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")

            if etype == "tool_call":
                tool_name = details.get("tool_name", "unknown")
                target_path = details.get("target_path", "")
                profile.total_tool_calls += 1
                tool_counter[tool_name] += 1

                # 操作分类
                if tool_name in ("read_local_file", "search_in_files",
                                 "analyze_project", "list_local_files"):
                    op_counter["read"] += 1
                elif tool_name in ("create_local_file", "modify_local_file",
                                   "replace_in_file"):
                    op_counter["write"] += 1
                elif tool_name == "delete_local_file":
                    op_counter["delete"] += 1
                elif tool_name == "run_local_command":
                    op_counter["execute"] += 1

                # 路径追踪
                if target_path:
                    paths_set.add(target_path)
                    tool_times.append((tool_name, timestamp))
                    if self._is_sensitive(target_path):
                        sensitive_paths.append(target_path)

                    # 路径跳跃检测
                    if prev_paths and target_path not in prev_paths:
                        # 检查是否跨不相关目录
                        last_dir = prev_paths[-1].replace("\\", "/").rsplit("/", 1)[0]
                        cur_dir = target_path.replace("\\", "/").rsplit("/", 1)[0]
                        if last_dir != cur_dir:
                            profile.path_jumps += 1
                    prev_paths.append(target_path)

            elif etype == "llm_input":
                llm_inputs += 1
                total_input_chars += details.get("content_length", 0)
                profile.total_llm_calls += 1

            elif etype == "llm_output":
                total_output_chars += details.get("content_length", 0)

        # 填充画像
        profile.tool_call_breakdown = dict(tool_counter)
        profile.operation_breakdown = dict(op_counter)
        profile.unique_paths = len(paths_set)
        profile.accessed_paths = list(paths_set)
        profile.sensitive_paths_accessed = sensitive_paths
        if llm_inputs > 0:
            profile.avg_input_length = total_input_chars // llm_inputs
        if profile.total_llm_calls > 0:
            profile.avg_output_length = total_output_chars // max(profile.total_llm_calls, 1)

        # 风险评估
        profile = self._assess_risk(profile, tool_times)

        return profile

    def detect_anomalies(self, entries: list, session_id: str) -> List[AnomalyRecord]:
        """
        检测异常行为。

        返回：AnomalyRecord 列表
        """
        anomalies: List[AnomalyRecord] = []

        if not entries:
            return anomalies

        tool_entries = []
        for entry in entries:
            etype = entry.get("event_type") if isinstance(entry, dict) else getattr(entry, "event_type", "")
            if etype == "tool_call":
                tool_entries.append(entry)

        if not tool_entries:
            return anomalies

        # 1. 高频操作检测
        freq_anomalies = self._detect_high_frequency(tool_entries, session_id)
        anomalies.extend(freq_anomalies)

        # 2. 敏感路径访问检测
        for entry in tool_entries:
            details = entry.get("details", {}) if isinstance(entry, dict) else getattr(entry, "details", {})
            target_path = details.get("target_path", "")
            tool_name = details.get("tool_name", "")
            ts = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")
            if self._is_sensitive(target_path):
                anomalies.append(AnomalyRecord(
                    session_id=session_id,
                    timestamp=ts,
                    anomaly_type="sensitive_access",
                    severity="medium" if os.path.basename(target_path) in (".env", "id_rsa") else "low",
                    description=f"访问敏感路径: {target_path}",
                    details={"tool": tool_name, "path": target_path}
                ))

        # 3. 路径跳跃检测
        paths = []
        for entry in tool_entries:
            details = entry.get("details", {}) if isinstance(entry, dict) else getattr(entry, "details", {})
            tp = details.get("target_path", "")
            if tp and (not paths or tp != paths[-1]):
                paths.append(tp)

        unique_dirs = set()
        for p in paths:
            d = p.replace("\\", "/").rsplit("/", 1)[0]
            unique_dirs.add(d)

        if len(unique_dirs) > self.PATH_JUMP_THRESHOLD:
            anomalies.append(AnomalyRecord(
                session_id=session_id,
                timestamp=entries[-1].get("timestamp", "") if isinstance(entries[-1], dict) else "",
                anomaly_type="path_hopping",
                severity="medium",
                description=f"跨 {len(unique_dirs)} 个不相关目录操作，可能存在遍历风险",
                details={"directories": list(unique_dirs)}
            ))

        # 4. 非工作时间操作
        for entry in tool_entries:
            ts = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")
            hour = self._get_hour(ts)
            if hour is not None and (hour >= self.OFF_HOURS_START or hour < self.OFF_HOURS_END):
                anomalies.append(AnomalyRecord(
                    session_id=session_id,
                    timestamp=ts,
                    anomaly_type="off_hours",
                    severity="low",
                    description=f"非工作时间操作（{hour}:00）",
                    details={"hour": hour}
                ))
                break  # 同类只报告一次

        return anomalies

    # ── 内部方法 ────────────────────────────

    def _is_sensitive(self, path: str) -> bool:
        """检查路径是否包含敏感关键词"""
        normalized = path.replace("\\", "/").lower()
        for kw in self.SENSITIVE_PATHS:
            if kw.lower() in normalized:
                return True
        return False

    def _detect_high_frequency(self, tool_entries: list,
                                session_id: str) -> List[AnomalyRecord]:
        """检测高频操作"""
        anomalies = []

        if len(tool_entries) < self.HIGH_FREQ_THRESHOLD:
            return anomalies

        # 按时间窗口统计操作频次
        windows = defaultdict(Counter)
        for entry in tool_entries:
            details = entry.get("details", {}) if isinstance(entry, dict) else getattr(entry, "details", {})
            tool_name = details.get("tool_name", "unknown")
            ts = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")

            try:
                dt = datetime.fromisoformat(ts)
                window_key = dt.strftime("%Y-%m-%d %H:%M")
                windows[window_key][tool_name] += 1
            except (ValueError, TypeError):
                continue

        for window_key, counter in windows.items():
            for tool_name, count in counter.items():
                if count >= self.HIGH_FREQ_THRESHOLD:
                    severity = "critical" if count >= 30 else "high" if count >= 20 else "medium"
                    anomalies.append(AnomalyRecord(
                        session_id=session_id,
                        timestamp=window_key,
                        anomaly_type="high_frequency",
                        severity=severity,
                        description=f"高频操作: {tool_name} x{count}（1分钟内）",
                        details={"tool": tool_name, "count": count, "window": window_key}
                    ))

        return anomalies

    def _get_hour(self, timestamp: str) -> Optional[int]:
        try:
            return datetime.fromisoformat(timestamp).hour
        except (ValueError, TypeError):
            return None

    def _assess_risk(self, profile: BehaviorProfile,
                     tool_times: List[Tuple[str, str]]) -> BehaviorProfile:
        """
        风险评估（0-100分）
        """
        score = 0
        factors = []

        # 敏感路径访问
        if profile.sensitive_paths_accessed:
            count = len(profile.sensitive_paths_accessed)
            add = min(count * 10, 30)
            score += add
            factors.append(f"访问了 {count} 个敏感路径 (+{add})")

        # 路径跳跃
        if profile.path_jumps > 3:
            add = min(profile.path_jumps * 5, 20)
            score += add
            factors.append(f"跨目录跳跃 {profile.path_jumps} 次 (+{add})")

        # 删除/执行操作
        delete_count = profile.operation_breakdown.get("delete", 0)
        exec_count = profile.operation_breakdown.get("execute", 0)
        if delete_count > 0:
            add = min(delete_count * 10, 20)
            score += add
            factors.append(f"删除了 {delete_count} 个文件 (+{add})")
        if exec_count > 0:
            add = min(exec_count * 15, 30)
            score += add
            factors.append(f"执行了 {exec_count} 次命令 (+{add})")

        # 高频写入
        write_count = profile.operation_breakdown.get("write", 0)
        if write_count > 20:
            add = min((write_count - 20) // 2, 15)
            score += add
            factors.append(f"写入操作 {write_count} 次（偏高）(+{add})")

        profile.risk_score = min(score, 100)
        profile.risk_factors = factors
        return profile
