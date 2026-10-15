# -*- coding: utf-8 -*-
"""隐盾控制面 · 审计链的**外部锚定**与连续性校验。

═══ 这个模块能证明什么、不能证明什么（先讲清楚，再写代码）═══

端点侧的审计链是 `HMAC_κ(前一条哈希 ‖ 本条内容)`，密钥 κ 由 `core/audit_log` 的
密钥管理器持有，**主密钥经 DPAPI 绑定端点当前 Windows 用户**。控制面：

  ✅ 能做的：
     · **批内连续性**：相邻条目的 `previous_hash` 必须等于前一条的 `entry_hash`；
       本批首条的 `previous_hash` 必须等于服务端记录的链头 —— 中间少一条就会断链。
     · **单调性**：总条数只能增不能减（减少 = 尾部截断）。
     · **同长度改写**：条数不变时链头必须一致（不一致 = 同长度替换）。
     · **增量自洽**：批内条数必须与"总条数增量"吻合，且最后一条的哈希等于声明的链头。
     · **外部锚定**：服务端**逐次保存** (条数, 链头, legacy 前缀) 这条时间序列。
       端点侧重算整条链属于它自己的权利，但**改不出服务端历史里那份旧链头** ——
       这正是"审计链头外部锚定"的价值：把本地可重写的证据变成远端只增不改的证据。

  ❌ 做不到的（必须如实声明，不能吹）：
     · **不能验证 HMAC 真伪**：没有 κ，服务端无法重算哈希。所以控制面证明的是
       "从第一次上报起，这条链**自洽且未被删改**"，不是"这条链的内容是真的"。
     · **不能覆盖首次上报之前的历史**：端点可以在首次上报前伪造整条链，服务端只从
       锚定时刻起有效。→ 部署时应尽早让端点上报（越早锚定，可被伪造的窗口越小）。
     · **沉默不等于安全**：端点不上报时，控制面只能显示"失联"，无法判断它是停了还是被拆了。
       运维侧应以"最后上报时间"作为告警维度（界面已按此呈现）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .models import EndpointReport, is_high_severity

# 告警代码 → (中文说明, 是否高危)
ALERT_CODES = {
    "BROKEN_LINK":      ("批内链断裂（相邻条目哈希不衔接）—— 中间条目被删或被改", True),
    "HEAD_MISMATCH":    ("链头与上次锚定不一致（条数未变）—— 同长度替换", True),
    "TRUNCATED":        ("总条数少于上次锚定 —— 尾部被截断", True),
    "REWRITTEN":        ("增量与链头不自洽 —— 尾部被重写", True),
    "GAP":              ("上报起始序号大于上次锚定的下一条 —— 中间有未上报区间", True),
    "ANCHOR_TAMPERED":  ("端点自报锚点不可信（本地锚点校验失败）", True),
    "LEGACY_SHRINK":    ("legacy 历史前缀变短 —— 有条目被抹掉了 HMAC 标记", True),
    "FIRST_REPORT":     ("首次锚定：控制面自此开始留痕（此前历史无法追认）", False),
    "RETRY_OVERLAP":    ("重复上报了已锚定的区间（端点重试，属正常现象）", False),
    "HIGH_EVENT":       ("端点上报了高风险事件（需人工查看）", True),
}


@dataclass
class ChainSnapshot:
    """服务端为每个端点保存的锚定快照（只增不改的"外部证据"）。"""
    total_count: int = 0
    head_hash: str = ""
    legacy_prefix: int = 0
    first_seen_at: str = ""
    last_report_at: str = ""

    def to_row(self) -> dict:
        return {"total_count": self.total_count, "head_hash": self.head_hash,
                "legacy_prefix": self.legacy_prefix,
                "first_seen_at": self.first_seen_at, "last_report_at": self.last_report_at}


@dataclass
class Alert:
    code: str
    detail: str
    severity: str = "高"

    def label(self) -> str:
        return ALERT_CODES.get(self.code, ("未知告警", True))[0]


@dataclass
class VerifyResult:
    ok: bool = True
    alerts: List[Alert] = field(default_factory=list)
    new_snapshot: Optional[ChainSnapshot] = None
    accepted_events: int = 0
    accepted_from_seq: int = 0

    def alert_codes(self) -> List[str]:
        return [a.code for a in self.alerts]


def verify_report(prev: Optional[ChainSnapshot], report: EndpointReport) -> VerifyResult:
    """校验一次上报并产出新的锚定快照。

    规则刻意"宁严勿宽"：任何一处不自洽都记为告警，但**不丢弃数据**——
    事件仍然入库（带告警标记），因为控制面宁可留下可疑证据，也不该替用户删证据。
    """
    claim = report.chain
    events = report.events
    result = VerifyResult()
    # 首次锚定时间：端点已登记但从未上报时，快照里的 first_seen_at 是空串，
    # 此时必须取本次上报时间（否则界面上"首次锚定"永远显示为空）。
    first_seen = prev.first_seen_at if (prev and prev.first_seen_at) else report.reported_at

    # ── 首报：只做锚定，不判告警（此前历史无从追认，如实标注）──
    if prev is None or prev.total_count == 0 and not prev.head_hash:
        result.alerts.append(Alert("FIRST_REPORT",
                                   f"首次锚定 {claim.total_count} 条，链头 {claim.head_hash[:16] or '(空)'}",
                                   severity="低"))
        result.new_snapshot = ChainSnapshot(claim.total_count, claim.head_hash,
                                            claim.legacy_prefix, first_seen, report.reported_at)
        result.accepted_events = len(events)
        result.accepted_from_seq = claim.first_seq
        return result

    # ── 1. 单调性：条数不得减少 ──
    if claim.total_count < prev.total_count:
        result.ok = False
        result.alerts.append(Alert("TRUNCATED",
                                   f"本次 {claim.total_count} 条 < 上次锚定 {prev.total_count} 条"))
    # ── 2. legacy 前缀不得变短 ──
    if claim.legacy_prefix < prev.legacy_prefix:
        result.ok = False
        result.alerts.append(Alert("LEGACY_SHRINK",
                                   f"legacy 前缀 {claim.legacy_prefix} < 上次 {prev.legacy_prefix}"))
    # ── 3. 条数未变时必须同链头 ──
    if claim.total_count == prev.total_count and claim.head_hash != prev.head_hash:
        result.ok = False
        result.alerts.append(Alert("HEAD_MISMATCH",
                                   f"条数同为 {claim.total_count}，链头却由 {prev.head_hash[:12]} 变为 "
                                   f"{claim.head_hash[:12]}"))
    # ── 4. 端点自报锚点不可信 ──
    if claim.anchor_state == "tampered":
        result.ok = False
        result.alerts.append(Alert("ANCHOR_TAMPERED", "端点本地锚点校验失败（链可能被整体替换）"))

    # ── 5. 增量自洽：本批应恰好从"上次锚定 + 1"条开始 ──
    expected_first = prev.total_count + 1
    if events:
        if claim.first_seq == 0:
            result.alerts.append(Alert("GAP", "上报未声明起始序号，无法核对增量区间"))
        elif claim.first_seq > expected_first:
            result.ok = False
            result.alerts.append(Alert("GAP",
                                       f"起始序号 {claim.first_seq} > 期望 {expected_first}，"
                                       f"中间 {claim.first_seq - expected_first} 条未上报"))
        elif claim.first_seq < expected_first:
            # 重复上报（端点重试/上次回执丢失）：不是安全问题，按"正常现象"记录
            result.alerts.append(Alert("RETRY_OVERLAP",
                                       f"起始序号 {claim.first_seq} < 期望 {expected_first}（重复上报，已幂等处理）",
                                       severity="低"))

    # ── 6. 批内连续性 ──
    cursor = prev.head_hash if (events and claim.first_seq == expected_first) else None
    for index, event in enumerate(events):
        if cursor is not None:
            if event.previous_hash != cursor:
                result.ok = False
                result.alerts.append(Alert("BROKEN_LINK",
                                           f"批内第 {index + 1} 条的 previous_hash 与上一条 entry_hash 不衔接"))
                cursor = None      # 断链后不再逐条比对，避免刷屏
        cursor = event.entry_hash or cursor

    # ── 7. 链头自洽：批末条哈希必须等于声明的链头 ──
    if events and claim.head_hash:
        if events[-1].entry_hash != claim.head_hash:
            result.ok = False
            result.alerts.append(Alert("REWRITTEN",
                                       f"批末条哈希 {events[-1].entry_hash[:12]} ≠ 声明链头 "
                                       f"{claim.head_hash[:12]}"))
        # 条数与增量必须吻合（无缺口时）
        elif claim.first_seq == expected_first and claim.total_count != claim.first_seq + len(events) - 1:
            result.ok = False
            result.alerts.append(Alert("REWRITTEN",
                                       f"条数声明 {claim.total_count} 与批区间 "
                                       f"[{claim.first_seq}, {claim.first_seq + len(events) - 1}] 不吻合"))

    # ── 8. 高危事件本身也进告警流（运维要在同一个视图里看到"链有问题"和"有高危操作"）──
    for event in events:
        if is_high_severity(event.severity):
            result.alerts.append(Alert("HIGH_EVENT",
                                       f"{event.event_type} · {event.message[:80]}", severity="高"))

    new_count = max(claim.total_count, prev.total_count)
    new_head = claim.head_hash if claim.total_count >= prev.total_count else prev.head_hash
    new_legacy = max(claim.legacy_prefix, prev.legacy_prefix)
    result.new_snapshot = ChainSnapshot(new_count, new_head, new_legacy, first_seen, report.reported_at)
    result.accepted_events = len(events)
    result.accepted_from_seq = claim.first_seq
    return result


def snapshot_verdict(snapshot: Optional[ChainSnapshot]) -> str:
    """给界面用的结论文案（不含技术黑话，运维一眼能懂）。"""
    if snapshot is None or (snapshot.total_count == 0 and not snapshot.head_hash):
        return "未上报"
    if not snapshot.head_hash:
        return "空链"
    return "已锚定"
