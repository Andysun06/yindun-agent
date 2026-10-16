# -*- coding: utf-8 -*-
"""隐盾 · AI 权限档位（四档，单一事实来源）

═══ 设计（为什么是这四档，以及底线在哪）═══

| 档位 | 只读 | 写盘/创建 | 删除 | 执行命令 |
|---|---|---|---|---|
| **计划模式** plan | ✅ 自动（沙箱内） | ⛔ 直接拒绝 | ⛔ 直接拒绝 | ⛔ 直接拒绝 |
| **变更前确认** ask | ✅ 自动 | 🙋 人工确认 | 🙋 人工确认 | 🙋 人工确认 |
| **自主模式** auto | ✅ 自动 | ✅ 自动（沙箱内） | 🙋 人工确认 | 🙋 人工确认 |
| **完全访问** full | ✅ 自动 | ✅ 自动（沙箱内） | ✅ 自动（沙箱内） | 🙋 人工确认 |

★ **硬底线（任何档位都不放开）**：
  1. **执行命令永远需要人工确认** —— 命令执行是策略层控制 + 进程容器，不是 OS 级隔离，
     自动放行的风险量级与文件操作不同。想少打扰就少用命令，而不是把这条关掉。
  2. **沙箱边界、脱敏网关、审计链** 与档位无关，任何档位都不可关闭或放宽；
  3. 控制面下发的规则**只能在本档位基础上继续收紧**（deny 覆盖 confirm 覆盖 approve）。

★ **立即生效**：档位取值每次调用时从环境变量读取（`permission_mode()`），
  因此设置页一改就作用于下一次工具调用，**不需要重启**。

★ **向后兼容**：旧版中文档位名（彻底审计 / 安全只读 / 完全控制）与旧数值/空值都能解析：
  彻底审计、安全只读 → plan（只读分析）；完全控制 → ask（变更前确认）。
"""
from __future__ import annotations

import os
from typing import Dict, Optional

# ── 环境变量：设置页写入、工具函数每次调用读取（→ 立即生效）──
PERMISSION_ENV = "YINDUN_PERMISSION"
LEGACY_ENV = "PERMISSION_LEVEL"          # 旧变量（中文档位名），仍可解析

MODES = ("plan", "ask", "auto", "full")

LABELS: Dict[str, str] = {
    "plan": "计划模式",
    "ask": "变更前确认",
    "auto": "自主模式",
    "full": "完全访问",
}
SHORT: Dict[str, str] = {
    "plan": "计划",
    "ask": "确认",
    "auto": "自主",
    "full": "完全",
}
HINTS: Dict[str, str] = {
    "plan": "只读分析、给出计划；不写盘、不删除、不执行命令",
    "ask": "读操作自动放行；写盘 / 删除 / 执行命令前逐次人工确认（默认，最稳）",
    "auto": "沙箱内的创建与写入自动放行；删除与执行命令仍逐次确认",
    "full": "沙箱内的创建 / 写入 / 删除自动放行；执行命令仍逐次确认（底线不可越）",
}

# 旧中文档位名 → 新档位
_LEGACY_MAP = {
    "彻底审计": "plan",
    "安全只读": "plan",
    "完全控制": "ask",
}

DEFAULT_MODE = "ask"

# 操作类别 → 各档位的基础判定（"approve" 自动放行 / "confirm" 人工确认 / "deny" 直接拒绝）
MODE_POLICY: Dict[str, Dict[str, str]] = {
    "plan": {"read": "approve", "write": "deny",    "delete": "deny",    "execute": "deny"},
    "ask":  {"read": "approve", "write": "confirm", "delete": "confirm", "execute": "confirm"},
    "auto": {"read": "approve", "write": "approve", "delete": "confirm", "execute": "confirm"},
    "full": {"read": "approve", "write": "approve", "delete": "approve", "execute": "confirm"},
}

# 判定强度：用于"只收紧"的取更严运算（控制面规则 / 插件规则都只能往上加）
_RANK = {"approve": 0, "confirm": 1, "deny": 2}


def resolve_mode(value: Optional[str]) -> str:
    """把任意来源的档位值解析成四档之一（含旧中文档位名与空值兜底）。"""
    text = str(value or "").strip()
    if text in MODES:
        return text
    for legacy, mode in _LEGACY_MAP.items():
        if legacy in text:
            return mode
    lowered = text.lower()
    if lowered in MODES:
        return lowered
    return DEFAULT_MODE


def permission_mode() -> str:
    """当前生效档位。**每次调用都读环境变量** —— 设置一改，下一次工具调用即生效。"""
    value = os.environ.get(PERMISSION_ENV)
    if value:
        return resolve_mode(value)
    return resolve_mode(os.environ.get(LEGACY_ENV))


def stricter(a: str, b: str) -> str:
    """取两个判定中更严的那个（approve < confirm < deny）。"""
    return a if _RANK.get(a, 1) >= _RANK.get(b, 1) else b


def base_decision(operation: str, mode: Optional[str] = None) -> str:
    """档位对某类操作的基础判定。operation ∈ read/write/delete/execute。"""
    table = MODE_POLICY.get(mode or permission_mode(), MODE_POLICY[DEFAULT_MODE])
    return table.get(operation, "confirm")


def gate_reason(operation: str, tool_display: str = "") -> str:
    """工具入口的**硬闸**：只在"计划模式"下拦下变更类操作，返回拦截话术；放行则返回空串。

    为什么只在这里拦 plan：其余三档的差别是"要不要人工确认"，那是审批层的事
    （`policy_manager.check`）。工具内部再判一次会造成两套真相。
    """
    if permission_mode() != "plan" or operation == "read":
        return ""
    what = {"write": "写盘 / 创建 / 修改文件", "delete": "删除文件", "execute": "执行命令"}.get(operation, "变更操作")
    name = f"（{tool_display}）" if tool_display else ""
    return (f"❌ 权限拦截：当前为【计划模式】{name}，本档位只做只读分析、不产生任何变更——"
            f"因此{what}已被拒绝。若确实需要动手，请在设置页把权限档位切到「变更前确认」或更高。")
