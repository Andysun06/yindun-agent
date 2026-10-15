# -*- coding: utf-8 -*-
"""隐盾控制面 · 把（已验签且只收紧过的）策略落到本地内核。

═══ 为什么单独一个模块 ═══

控制面策略只有**真的改变本地判定行为**才算数——"存着不下发"或"下发不生效"都会让
运维产生虚假的安全感。本模块负责这段映射，并且把边界写死：

| 策略项 | 落到内核哪里 | 效果 |
|---|---|---|
| `sensitive_paths` | `PolicyManager` 审批规则（插在队首） | 命中即 `confirm`（floor=deny 时 `deny`） |
| `approval_floor` | `PolicyManager` 队尾**兜底规则** | `confirm`＝处处人工确认；`deny`＝全面熔断 |
| `require_strict_mode` | 设置 `allow_script_exec=False` | 禁用解释器，仅留只读命令 |
| `allowed_plugins` | 插件宿主停用不在列表内的插件 | 只缩不扩 |
| `allowed_models` | 当前模型不在允许集内则回退 | 只缩不扩 |

★ 三条不变量：
  1. **只收紧**：本模块只写 confirm / deny 与"更严"的设置，没有任何把规则放松的路径；
  2. **幂等**：重复应用同一策略，规则条数不增长（由 `set_control_rules` 整体替换保证）；
  3. **可辨认**：所有写入的规则都带 `source="control"` 与策略版本号，设置页能列出
     "哪些规则来自控制面"，也可以用 `clear_control_rules()` 一次性撤销。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

FLOOR_POLICY = {"confirm": "confirm", "deny": "deny"}


def build_floor_rule(policy: Dict[str, Any]) -> Optional[dict]:
    """把 approval_floor 变成一条"队尾兜底规则"。

    · `confirm` → 任何未被前置规则命中的操作都要人工确认（含只读白名单，即冻结自动放行）
    · `deny`    → 全面熔断：一切操作直接拒绝（企业应急开关）
    """
    floor = str((policy or {}).get("approval_floor") or "").strip().lower()
    if floor not in FLOOR_POLICY:
        return None
    return {"path_pattern": "*", "operations": "*", "policy": FLOOR_POLICY[floor], "_floor": True}


def apply_policy_to_kernel(policy: Dict[str, Any], policy_manager: Any) -> Dict[str, Any]:
    """把策略写进本地策略引擎（`PolicyManager` 单例）。返回落地摘要，供界面与审计展示。

    本函数**只碰策略引擎**；严格模式、插件、模型的副作用由调用方（应用服务层）处理，
    这样内核侧改动面最小，也便于单测。
    """
    if policy_manager is None:
        return {"ok": False, "error": "策略引擎不可用"}
    version = int(policy.get("version") or 0)
    paths: List[str] = [str(p) for p in (policy.get("sensitive_paths") or []) if str(p).strip()]
    floor_rule = build_floor_rule(policy)
    floor = str((policy or {}).get("approval_floor") or "")
    # 敏感路径默认 confirm；floor=deny 时直接 deny（更严）
    rules = [{"path_pattern": p, "policy": "deny" if floor == "deny" else "confirm"} for p in paths]
    if floor_rule:
        rules.append(floor_rule)
    count = policy_manager.set_control_rules(rules=rules, sensitive_paths=paths,
                                             policy_version=version)
    return {
        "ok": True, "policy_version": version, "rules": count,
        "sensitive_paths": paths,
        "floor": floor,
        "strict_mode": bool(policy.get("require_strict_mode")),
        "allowed_models": policy.get("allowed_models"),
        "allowed_plugins": policy.get("allowed_plugins"),
    }


def describe_local_effect(policy: Dict[str, Any]) -> List[str]:
    """给界面用的人话摘要：这份策略在这台机器上究竟改变了什么。"""
    lines: List[str] = []
    paths = policy.get("sensitive_paths") or []
    floor = str(policy.get("approval_floor") or "")
    if paths:
        act = "直接拒绝" if floor == "deny" else "必须人工审批"
        lines.append(f"{len(paths)} 条敏感路径：命中即{act}")
    if floor == "confirm":
        lines.append("审批下限：所有操作都要人工确认（含只读操作，冻结自动放行）")
    elif floor == "deny":
        lines.append("审批下限：全面熔断——一切操作直接拒绝（应急开关）")
    if policy.get("require_strict_mode"):
        lines.append("严格模式：禁用解释器，只保留只读命令")
    if policy.get("allowed_models") is not None:
        allowed = policy.get("allowed_models") or []
        lines.append("允许模型：" + ("、".join(allowed) if allowed else "（空＝全部禁用）"))
    if policy.get("allowed_plugins") is not None:
        allowed = policy.get("allowed_plugins") or []
        lines.append("允许插件：" + ("、".join(allowed) if allowed else "（空＝全部停用）"))
    return lines
