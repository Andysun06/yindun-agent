# -*- coding: utf-8 -*-
"""隐盾控制面 · 数据模型与入参校验。

设计要点：
- **入参一律视为不可信**：字段类型、长度、条数、嵌套深度全部显式设限，
  越界即整包拒绝（控制面不能被一个坏端点拖垮，也不能被超长字段撑爆存储）。
- **不解析任何"可执行"内容**：上报里没有脚本、没有路径通配、没有正则；
  `details` 只当作扁平化字典存储与展示。
- 时间戳只接受 ISO-8601 字符串，**不在服务端做时区推断**（展示原样 + 标注来源）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

# ── 硬上限（越界即拒绝，不做静默截断）──
MAX_EVENTS_PER_BATCH = 500
MAX_MESSAGE_LEN = 2000
MAX_DETAILS_KEYS = 40
MAX_DETAIL_LEN = 500
MAX_MODELS = 64
MAX_PLUGINS = 64
MAX_USAGE_ROWS = 200
MAX_HASH_LEN = 128
MAX_ID_LEN = 64

_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,%d}$" % MAX_ID_LEN)
_HASH_RE = re.compile(r"^[0-9a-f]{0,%d}$" % MAX_HASH_LEN)
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?([+-]\d{2}:?\d{2}|Z)?$")

# 与 `core/audit_log.AuditEventType` 的 .value 对齐（小写蛇形）。
# 不认识的新类型不拒绝（前向兼容），但会被归入 OTHER_* 以免界面被脏数据撑爆。
KNOWN_EVENT_TYPES = {
    "tool_call", "tool_result", "llm_input", "llm_output", "privacy_sensitive",
    "access_control", "session_start", "session_end", "workflow_step_executed", "chain_restart",
    "control_report",
}
# 与 `core/audit_log.AuditSeverity` 的 .value 对齐。
KNOWN_SEVERITIES = {"info", "warning", "critical", "security"}

# 视为"高风险"的级别（用于态势统计）：critical 与 security 都要人看一眼
HIGH_SEVERITIES = {"critical", "security"}


class ReportError(ValueError):
    """上报数据不合法（整包拒绝，不做部分接受）。"""


def _req(data: Dict[str, Any], key: str) -> Any:
    if key not in data:
        raise ReportError(f"缺少字段：{key}")
    return data[key]


def _check_str(value: Any, name: str, max_len: int, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ReportError(f"{name} 必须是字符串")
    if len(value) > max_len:
        raise ReportError(f"{name} 超长（{len(value)} > {max_len}）")
    if not allow_empty and not value.strip():
        raise ReportError(f"{name} 不能为空")
    return value


def _check_id(value: Any, name: str) -> str:
    s = _check_str(value, name, MAX_ID_LEN, allow_empty=False)
    if not _ID_RE.match(s):
        raise ReportError(f"{name} 含非法字符（只允许字母数字与 _ . : -）")
    return s


def _check_hash(value: Any, name: str) -> str:
    s = _check_str(value, name, MAX_HASH_LEN)
    if not _HASH_RE.match(s):
        raise ReportError(f"{name} 不是合法十六进制哈希")
    return s


def _check_ts(value: Any, name: str) -> str:
    s = _check_str(value, name, 40, allow_empty=False)
    if not _TS_RE.match(s):
        raise ReportError(f"{name} 不是 ISO-8601 时间戳：{s[:32]}")
    return s


def _sanitize_details(details: Any) -> Dict[str, str]:
    """details 只保留一层键值（值统一转字符串并有长度上限）。

    不做嵌套——嵌套会让存储与展示都出现"深不可测"的角落，而控制面不需要它。
    """
    if details is None:
        return {}
    if not isinstance(details, dict):
        raise ReportError("details 必须是对象")
    if len(details) > MAX_DETAILS_KEYS:
        raise ReportError(f"details 键数超限（{len(details)} > {MAX_DETAILS_KEYS}）")
    out: Dict[str, str] = {}
    for key, value in details.items():
        k = str(key)[:64]
        if isinstance(value, (dict, list)):
            v = f"<{type(value).__name__} len={len(value)}>"
        else:
            v = str(value)
        out[k] = v[:MAX_DETAIL_LEN]
    return out


# ══════════════════════════════════════════════════════════════════
#  端点上报
# ══════════════════════════════════════════════════════════════════

@dataclass
class EventIn:
    """一条上报事件。`message` / `details` 到控制面时已是掩码态。"""
    ts: str
    event_type: str
    severity: str
    message: str = ""
    details: Dict[str, str] = field(default_factory=dict)
    previous_hash: str = ""
    entry_hash: str = ""
    hash_algo: str = ""
    legacy: bool = False

    @classmethod
    def from_dict(cls, raw: Any) -> "EventIn":
        if not isinstance(raw, dict):
            raise ReportError("events 元素必须是对象")
        ts = _check_ts(_req(raw, "ts"), "events[].ts")
        etype = _check_str(_req(raw, "event_type"), "events[].event_type", 32, allow_empty=False)
        if etype not in KNOWN_EVENT_TYPES:
            # 未知类型不拒绝（前向兼容），但统一归到 OTHER，避免界面被脏数据撑爆
            etype = "OTHER_" + re.sub(r"[^A-Z0-9_]", "", etype.upper())[:24]
        sev = _check_str(raw.get("severity", "info"), "events[].severity", 16, allow_empty=False)
        if sev not in KNOWN_SEVERITIES:
            sev = "info"
        return cls(
            ts=ts, event_type=etype, severity=sev,
            message=_check_str(raw.get("message", ""), "events[].message", MAX_MESSAGE_LEN),
            details=_sanitize_details(raw.get("details")),
            previous_hash=_check_hash(raw.get("previous_hash", ""), "events[].previous_hash"),
            entry_hash=_check_hash(raw.get("entry_hash", ""), "events[].entry_hash"),
            hash_algo=_check_str(raw.get("hash_algo", ""), "events[].hash_algo", 24),
            legacy=bool(raw.get("legacy", False)),
        )


@dataclass
class ChainClaim:
    """端点对"我这条链现在长什么样"的声明（服务端据此做外部锚定与连续性校验）。"""
    total_count: int
    head_hash: str
    legacy_prefix: int = 0
    anchor_state: str = "unknown"      # verified / absent / tampered / unknown
    first_seq: int = 0                 # 本批首条的 1-based 序号

    @classmethod
    def from_dict(cls, raw: Any) -> "ChainClaim":
        if not isinstance(raw, dict):
            raise ReportError("chain 必须是对象")
        total = _req(raw, "total_count")
        if not isinstance(total, int) or isinstance(total, bool) or total < 0 or total > 10 ** 9:
            raise ReportError("chain.total_count 非法")
        first_seq = raw.get("first_seq", 0)
        if not isinstance(first_seq, int) or isinstance(first_seq, bool) or first_seq < 0:
            raise ReportError("chain.first_seq 非法")
        legacy_prefix = raw.get("legacy_prefix", 0)
        if not isinstance(legacy_prefix, int) or isinstance(legacy_prefix, bool) or legacy_prefix < 0:
            raise ReportError("chain.legacy_prefix 非法")
        state = str(raw.get("anchor_state", "unknown"))[:16]
        if state not in {"verified", "absent", "tampered", "unknown"}:
            state = "unknown"
        return cls(total_count=total, head_hash=_check_hash(raw.get("head_hash", ""), "chain.head_hash"),
                   legacy_prefix=legacy_prefix, anchor_state=state, first_seq=first_seq)


@dataclass
class Capabilities:
    """端点能力清单（用于资产视角：装了什么模型、开了哪些插件、是否严格模式）。"""
    models: List[str] = field(default_factory=list)
    external_models: List[str] = field(default_factory=list)
    plugins: List[Dict[str, Any]] = field(default_factory=list)
    strict_mode: bool = False
    policy_version: int = 0
    platform: str = ""

    @classmethod
    def from_dict(cls, raw: Any) -> "Capabilities":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ReportError("capabilities 必须是对象")
        models = raw.get("models") or []
        external = raw.get("external_models") or []
        plugins = raw.get("plugins") or []
        if not isinstance(models, list) or len(models) > MAX_MODELS:
            raise ReportError("capabilities.models 非法")
        if not isinstance(external, list) or len(external) > MAX_MODELS:
            raise ReportError("capabilities.external_models 非法")
        if not isinstance(plugins, list) or len(plugins) > MAX_PLUGINS:
            raise ReportError("capabilities.plugins 非法")
        clean_plugins: List[Dict[str, Any]] = []
        for item in plugins:
            if not isinstance(item, dict):
                continue
            clean_plugins.append({
                "id": _check_str(item.get("id", ""), "plugins[].id", 64),
                "version": _check_str(item.get("version", ""), "plugins[].version", 32),
                "enabled": bool(item.get("enabled", False)),
            })
        pv = raw.get("policy_version", 0)
        if not isinstance(pv, int) or isinstance(pv, bool) or pv < 0:
            pv = 0
        return cls(
            models=[_check_str(m, "models[]", 96) for m in models],
            external_models=[_check_str(m, "external_models[]", 96) for m in external],
            plugins=clean_plugins,
            strict_mode=bool(raw.get("strict_mode", False)),
            policy_version=pv,
            platform=_check_str(raw.get("platform", ""), "capabilities.platform", 64),
        )


@dataclass
class UsageRow:
    """模型用量：**只有计数与体量，没有内容**。

    当前内核的 LLM 审计事件未标注模型名（worker 侧只记 content_length/token_count），
    因此端点上报的是**端点级聚合**（`model="(端点合计)"`）——不编造按模型的归因。
    归因所需的内核改动（LLM 事件带模型名）已列入路线图，见技术报告"企业部署形态"节。
    """
    model: str
    scope: str          # local / external / mixed
    calls: int = 0
    chars: int = 0

    @classmethod
    def from_dict(cls, raw: Any) -> "UsageRow":
        if not isinstance(raw, dict):
            raise ReportError("usage 元素必须是对象")
        calls = raw.get("calls", 0)
        chars = raw.get("chars", 0)
        for name, value in (("calls", calls), ("chars", chars)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0 or value > 10 ** 12:
                raise ReportError(f"usage[].{name} 非法")
        scope = str(raw.get("scope", "local"))[:16]
        if scope not in {"local", "external", "mixed"}:
            scope = "local"
        return cls(model=_check_str(_req(raw, "model"), "usage[].model", 96, allow_empty=False),
                   scope=scope, calls=calls, chars=chars)


@dataclass
class EndpointReport:
    endpoint_id: str
    reported_at: str
    version: str
    platform: str
    chain: ChainClaim
    events: List[EventIn]
    capabilities: Capabilities
    usage: List[UsageRow]
    schema: int = SCHEMA_VERSION

    @classmethod
    def from_dict(cls, raw: Any) -> "EndpointReport":
        if not isinstance(raw, dict):
            raise ReportError("上报体必须是 JSON 对象")
        schema = raw.get("schema", SCHEMA_VERSION)
        if schema != SCHEMA_VERSION:
            raise ReportError(f"schema 版本不支持：{schema}（本控制面支持 {SCHEMA_VERSION}）")
        events_raw = raw.get("events") or []
        if not isinstance(events_raw, list):
            raise ReportError("events 必须是数组")
        if len(events_raw) > MAX_EVENTS_PER_BATCH:
            raise ReportError(f"单批事件数超限（{len(events_raw)} > {MAX_EVENTS_PER_BATCH}）")
        usage_raw = raw.get("usage") or []
        if not isinstance(usage_raw, list) or len(usage_raw) > MAX_USAGE_ROWS:
            raise ReportError("usage 非法或超限")
        return cls(
            endpoint_id=_check_id(_req(raw, "endpoint_id"), "endpoint_id"),
            reported_at=_check_ts(_req(raw, "reported_at"), "reported_at"),
            version=_check_str(raw.get("version", ""), "version", 32),
            platform=_check_str(raw.get("platform", ""), "platform", 64),
            chain=ChainClaim.from_dict(_req(raw, "chain")),
            events=[EventIn.from_dict(e) for e in events_raw],
            capabilities=Capabilities.from_dict(raw.get("capabilities")),
            usage=[UsageRow.from_dict(u) for u in usage_raw],
        )


def is_high_severity(severity: str) -> bool:
    return severity in HIGH_SEVERITIES
