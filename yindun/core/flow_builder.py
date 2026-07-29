# -*- coding: utf-8 -*-
"""
隐盾数据流构建器 — 基于审计日志构建有向数据流图
================================================
从 AuditLog 的 tool_call / tool_result 事件链中提取数据流向，
构建 DAG 有向图，支持按会话查询、按路径聚合、检测数据泄露风险。

核心概念：
  - 数据流入（data_in）：工具读取的源（文件路径、目录）
  - 数据流出（data_out）：工具写入的目标（文件路径）
  - 数据敏感度（data_sensitivity）：流经数据的最高分级

使用示例：
    builder = FlowBuilder()
    graph = builder.build(session_entries)
    summary = builder.summarize_flows(graph)
"""

import os
import json
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict


@dataclass
class FlowEdge:
    """数据流的一条边"""
    source: str          # 数据来源（文件路径或 "user_input"）
    target: str          # 数据目标（文件路径或 "user_output"）
    tool: str           # 使用的工具
    timestamp: str
    session_id: str = ""
    sensitivity: str = "公开"   # 绝密 | 机密 | 内部 | 公开
    data_size: int = 0         # 数据大小（字节）
    success: bool = True

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "tool": self.tool,
            "timestamp": self.timestamp,
            "sensitivity": self.sensitivity,
            "data_size": self.data_size,
            "success": self.success
        }


@dataclass
class FlowGraph:
    """有向数据流图"""
    session_id: str
    edges: List[FlowEdge] = field(default_factory=list)
    nodes: Set[str] = field(default_factory=set)
    total_flows: int = 0

    # 聚合统计
    tool_distribution: Dict[str, int] = field(default_factory=dict)
    sensitivity_distribution: Dict[str, int] = field(default_factory=dict)
    source_distribution: Dict[str, int] = field(default_factory=dict)     # 源 → 次数
    target_distribution: Dict[str, int] = field(default_factory=dict)     # 目标 → 次数

    # 风险指标
    leak_risks: List[dict] = field(default_factory=list)    # 疑似泄露路径

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "total_flows": self.total_flows,
            "nodes_count": len(self.nodes),
            "edges": [e.to_dict() for e in self.edges],
            "tool_distribution": self.tool_distribution,
            "sensitivity_distribution": self.sensitivity_distribution,
            "source_distribution": self.source_distribution,
            "target_distribution": self.target_distribution,
            "leak_risks": self.leak_risks
        }

    def summary(self) -> str:
        """生成数据流摘要"""
        lines = [
            "=" * 50,
            f"数据流向图: {self.session_id}",
            f"总流数: {self.total_flows}, 节点数: {len(self.nodes)}",
            "-" * 50,
        ]
        if self.tool_distribution:
            lines.append("工具分布: " + ", ".join(
                f"{k}({v})" for k, v in self.tool_distribution.items()))
        if self.sensitivity_distribution:
            lines.append("敏感度: " + ", ".join(
                f"{k}({v})" for k, v in sorted(
                    self.sensitivity_distribution.items(),
                    key=lambda x: {"绝密": 4, "机密": 3, "内部": 2, "公开": 1}.get(x[0], 0),
                    reverse=True)))
        if self.leak_risks:
            lines.append(f"\n⚠ 发现 {len(self.leak_risks)} 条疑似数据泄露路径:")
            for risk in self.leak_risks:
                lines.append(f"  {risk['source']} → {risk['target']} ({risk['reason']})")
        else:
            lines.append("\n✓ 未发现数据泄露风险")
        lines.append("=" * 50)
        return "\n".join(lines)


class FlowBuilder:
    """
    数据流构建器

    用法：
        builder = FlowBuilder()
        graph = builder.build(audit_entries)
        print(graph.summary())
    """

    # 操作方向映射
    READ_TOOLS = {"read_local_file", "search_in_files", "analyze_project",
                   "list_local_files", "read_attachment_chunk",
                   "search_knowledge_base"}
    WRITE_TOOLS = {"create_local_file", "modify_local_file", "replace_in_file"}
    DELETE_TOOLS = {"delete_local_file"}
    EXEC_TOOLS = {"run_local_command"}

    def build(self, entries: list, session_id: str = "",
              classify_path_fn=None) -> FlowGraph:
        """
        从审计日志条目构建数据流图。

        参数：
            entries: AuditEntry dict 列表
            session_id: 会话 ID
            classify_path_fn: 路径分级函数 (path) → severity_str

        返回：FlowGraph
        """
        graph = FlowGraph(session_id=session_id)

        # 第一步：提取所有 tool_call 和 tool_result 对
        tool_calls: Dict[str, dict] = {}  # tool_call_id → {tool_call entry details}

        for entry in entries:
            # 统一处理 dict 和对象
            etype = entry.get("event_type") if isinstance(entry, dict) else getattr(entry, "event_type", "")
            details = entry.get("details", {}) if isinstance(entry, dict) else getattr(entry, "details", {})
            ts = entry.get("timestamp", "") if isinstance(entry, dict) else getattr(entry, "timestamp", "")

            if etype == "tool_call":
                tool_name = details.get("tool_name", "")
                target_path = details.get("target_path", "")
                tool_args = details.get("tool_args", {})
                tool_calls[tool_name] = {
                    "tool_name": tool_name,
                    "target_path": target_path,
                    "args": tool_args,
                    "timestamp": ts
                }

            elif etype == "tool_result":
                tool_name = details.get("tool_name", "")
                success = details.get("success", True)
                result = details.get("result", "")

                # 查找对应的 tool_call
                if tool_name in tool_calls:
                    call_info = tool_calls.pop(tool_name)
                    edge = self._build_edge_from_call(
                        call_info, success, ts, result, classify_path_fn)
                    if edge:
                        graph.edges.append(edge)
                        graph.nodes.add(edge.source)
                        graph.nodes.add(edge.target)

        # 聚合统计
        graph.total_flows = len(graph.edges)
        for edge in graph.edges:
            graph.tool_distribution[edge.tool] = \
                graph.tool_distribution.get(edge.tool, 0) + 1
            graph.sensitivity_distribution[edge.sensitivity] = \
                graph.sensitivity_distribution.get(edge.sensitivity, 0) + 1
            graph.source_distribution[self._shorten_path(edge.source)] = \
                graph.source_distribution.get(self._shorten_path(edge.source), 0) + 1
            graph.target_distribution[self._shorten_path(edge.target)] = \
                graph.target_distribution.get(self._shorten_path(edge.target), 0) + 1

        # 泄露风险检测
        graph.leak_risks = self._detect_leakage(graph)

        return graph

    def _build_edge_from_call(self, call_info: dict, success: bool,
                               timestamp: str, result: str,
                               classify_path_fn) -> Optional[FlowEdge]:
        """从单个工具调用构建流边"""
        tool_name = call_info.get("tool_name", "")
        target_path = call_info.get("target_path", "")

        # 读取操作：source = target_path, target = "user_output"
        if tool_name in self.READ_TOOLS:
            sensitivity = classify_path_fn(target_path) if classify_path_fn else "公开"
            return FlowEdge(
                source=target_path or "unknown",
                target="user_output",
                tool=tool_name,
                timestamp=timestamp,
                sensitivity=sensitivity,
                data_size=len(result) if result else 0,
                success=success
            )

        # 写入操作：source = "user_input", target = target_path
        elif tool_name in self.WRITE_TOOLS:
            sensitivity = classify_path_fn(target_path) if classify_path_fn else "公开"
            return FlowEdge(
                source="user_input",
                target=target_path,
                tool=tool_name,
                timestamp=timestamp,
                sensitivity=sensitivity,
                data_size=len(result) if result else 0,
                success=success
            )

        # 执行操作：source = target_path(命令路径), target = "user_output"
        elif tool_name in self.EXEC_TOOLS:
            return FlowEdge(
                source=target_path or "user_input",
                target="user_output",
                tool=tool_name,
                timestamp=timestamp,
                sensitivity="内部",
                success=success
            )

        return None

    def _detect_leakage(self, graph: FlowGraph) -> List[dict]:
        """
        检测数据泄露风险：
        - 从高敏感路径流向低安全目标（如 绝密文件 → user_output）
        - 从内部源写入外部/公开目标
        """
        risks = []
        severity_order = {"绝密": 4, "机密": 3, "内部": 2, "公开": 1}

        for edge in graph.edges:
            source_sens = severity_order.get(edge.sensitivity, 1)
            if edge.target == "user_output" and source_sens >= 3:
                risks.append({
                    "source": edge.source,
                    "target": edge.target,
                    "tool": edge.tool,
                    "sensitivity": edge.sensitivity,
                    "reason": f"高敏感数据({edge.sensitivity})流向用户输出"
                })
            elif edge.target not in ("user_output", "user_input"):
                target_sens = severity_order.get(
                    self._path_to_sensitivity(edge.target), 1)
                if source_sens > target_sens:
                    risks.append({
                        "source": edge.source,
                        "target": edge.target,
                        "tool": edge.tool,
                        "sensitivity": edge.sensitivity,
                        "reason": f"数据降级: {edge.sensitivity}→{self._path_to_sensitivity(edge.target)}"
                    })

        return risks

    def summarize_flows(self, graphs: List[FlowGraph]) -> dict:
        """聚合多个会话的数据流"""
        summary = {
            "total_sessions": len(graphs),
            "total_flows": sum(g.total_flows for g in graphs),
            "total_nodes": sum(len(g.nodes) for g in graphs),
            "total_leak_risks": sum(len(g.leak_risks) for g in graphs),
            "tool_usage": {},
            "sensitivity_breakdown": {},
            "top_sources": {},
            "top_targets": {}
        }
        for g in graphs:
            for tool, count in g.tool_distribution.items():
                summary["tool_usage"][tool] = summary["tool_usage"].get(tool, 0) + count
            for sens, count in g.sensitivity_distribution.items():
                summary["sensitivity_breakdown"][sens] = \
                    summary["sensitivity_breakdown"].get(sens, 0) + count
            for src, count in g.source_distribution.items():
                summary["top_sources"][src] = summary["top_sources"].get(src, 0) + count
            for tgt, count in g.target_distribution.items():
                summary["top_targets"][tgt] = summary["top_targets"].get(tgt, 0) + count

        # 取 Top 10
        summary["top_sources"] = dict(
            sorted(summary["top_sources"].items(), key=lambda x: -x[1])[:10])
        summary["top_targets"] = dict(
            sorted(summary["top_targets"].items(), key=lambda x: -x[1])[:10])
        return summary

    # ── 工具函数 ────────────────────────────

    @staticmethod
    def _shorten_path(path: str, max_len: int = 40) -> str:
        """缩短路径显示"""
        if len(path) <= max_len:
            return path
        return "..." + path[-(max_len - 3):]

    @staticmethod
    def _path_to_sensitivity(path: str) -> str:
        """简单路径→敏感度估计"""
        sensitive_kw = {
            ".ssh": "绝密", ".aws": "绝密", "id_rsa": "绝密",
            ".env": "机密", "credentials": "机密", "config": "机密",
            ".git-credentials": "机密",
        }
        normalized = path.lower().replace("\\", "/")
        for kw, level in sensitive_kw.items():
            if kw in normalized:
                return level
        return "公开"
