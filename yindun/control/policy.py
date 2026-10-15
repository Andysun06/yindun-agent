# -*- coding: utf-8 -*-
"""隐盾控制面 · 策略包：签名、白名单校验与"只收紧"合并。

═══ 为什么不是"信任控制面" ═══

控制面能下发策略，但**端点绝不假设控制面行为良好**——两条独立防线：

  ① **键白名单 + 降级键检测**：策略包里出现脱敏豁免 / 审批放行 / 白名单放行之类
     的键（无论名字怎么变），或任何不在白名单里的键，**整包拒绝**并记告警。
     这与 `plugins/host.py` 的 `FORBIDDEN_HOOKS` 是同一个决策：判定权不外放。
  ② **单调合并**：即便包是通过白名单的合法键，合并也只是"收紧"——
     敏感路径取**并集**、审批下限取**更严**、允许集取**交集**、严格模式取**或**。
     合并函数的输出**永远**不会比输入更宽松（有单调性测试锁住）。

  合起来的结果是：**就算控制面主机被完全攻破，它能做到的极限也只是"让端点管得更严"**，
  放行不了任何一次脱敏豁免或审批跳过。这正是"控制面只能收紧"这句话的技术含义。

签名用 HMAC-SHA256（密钥＝端点登记时下发的令牌），用于证明"包来自控制面且未被中间人改动"；
它**不**替代上面两条——签名只解决来源与完整性，不解决"控制面自己乱来"。
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any, Dict, List, Optional, Tuple

SIGN_ALGO = "hmac-sha256"

# ── 允许下发的键（白名单，之外一律拒绝）──
POLICY_KEYS = {
    "version",        # int，策略版本号（用于端点展示与幂等）
    "issued_at",      # ISO 时间
    "sensitive_paths",     # list[str] 追加为敏感路径（并集）
    "approval_floor",      # "confirm" | "deny"（提升审批下限）
    "allowed_models",      # list[str] 允许使用的模型（交集）
    "allowed_plugins",     # list[str] 允许启用的插件 id（交集）
    "require_strict_mode",  # bool 强制严格模式（或）
    "note",           # str 备注（展示用，不参与判定）
}

# ── 绝不允许下发的键（含近义写法，防"改个名字就绕过"）──
FORBIDDEN_KEYS = {
    "anonymize", "anonymize_rules", "anonymization", "exempt", "exemption", "exemptions",
    "bypass", "bypass_rules", "approve", "approval", "auto_approve", "allow", "allowed",
    "whitelist", "allowlist_commands", "allowed_commands", "skip_approval", "skip_review",
    "disable_guard", "disable_audit", "no_mask", "unmask", "plaintext", "disable_privacy",
    "approval_rules", "sandbox_root", "allow_script_exec",
}

APPROVAL_RANK = {"approve": 0, "confirm": 1, "deny": 2}


class PolicyError(ValueError):
    """策略包不合法 / 降级尝试（调用方应拒绝整包并记告警）。"""


def canonical_bytes(body: Dict[str, Any]) -> bytes:
    """规范化序列化（键排序、紧凑、UTF-8）——签名与验签必须用同一套。"""
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sign(bundle: Dict[str, Any], secret: str) -> str:
    """对策略包体签名（签名值本身不参与签名）。"""
    body = {k: v for k, v in bundle.items() if k != "sig"}
    return hmac.new(secret.encode("utf-8"), canonical_bytes(body), hashlib.sha256).hexdigest()


def validate_keys(bundle: Dict[str, Any]) -> None:
    """白名单 + 降级键检测。不通过即抛 PolicyError。"""
    if not isinstance(bundle, dict):
        raise PolicyError("策略包必须是 JSON 对象")
    forbidden = sorted(set(bundle) & FORBIDDEN_KEYS)
    if forbidden:
        raise PolicyError("策略包含不允许下发的键（脱敏/审批判定权归内核，控制面无权下发）："
                          + "、".join(forbidden))
    unknown = sorted(set(bundle) - POLICY_KEYS - {"sig"})
    if unknown:
        raise PolicyError("策略包含未知键（拒绝，避免未来语义被悄悄利用）：" + "、".join(unknown))


def _check_types(bundle: Dict[str, Any]) -> None:
    if "version" in bundle and (not isinstance(bundle["version"], int) or isinstance(bundle["version"], bool)):
        raise PolicyError("version 必须是整数")
    if "sensitive_paths" in bundle:
        paths = bundle["sensitive_paths"]
        if not isinstance(paths, list) or len(paths) > 200:
            raise PolicyError("sensitive_paths 必须是长度 ≤200 的数组")
        for p in paths:
            if not isinstance(p, str) or not p.strip() or len(p) > 260:
                raise PolicyError("sensitive_paths 元素必须是非空短字符串")
            if "\x00" in p:
                raise PolicyError("sensitive_paths 含空字节")
    if "approval_floor" in bundle and bundle["approval_floor"] not in APPROVAL_RANK:
        raise PolicyError("approval_floor 只能是 approve/confirm/deny")
    for key in ("allowed_models", "allowed_plugins"):
        if key in bundle:
            seq = bundle[key]
            if not isinstance(seq, list) or len(seq) > 200:
                raise PolicyError(f"{key} 必须是长度 ≤200 的数组")
            if any(not isinstance(x, str) or not x.strip() or len(x) > 96 for x in seq):
                raise PolicyError(f"{key} 元素必须是非空短字符串")
    if "require_strict_mode" in bundle and not isinstance(bundle["require_strict_mode"], bool):
        raise PolicyError("require_strict_mode 必须是布尔值")
    if "note" in bundle and (not isinstance(bundle["note"], str) or len(bundle["note"]) > 500):
        raise PolicyError("note 必须是 ≤500 字符的字符串")


def verify_bundle(bundle: Dict[str, Any], secret: str) -> Tuple[bool, str]:
    """校验签名与键合法性。返回 (是否通过, 原因)。"""
    try:
        validate_keys(bundle)
        _check_types(bundle)
    except PolicyError as exc:
        return False, str(exc)
    sig = bundle.get("sig", "")
    if not isinstance(sig, str) or not sig:
        return False, "策略包缺少签名"
    if not hmac.compare_digest(sig, sign(bundle, secret)):
        return False, "策略包签名不匹配（可能被中间人改动，或令牌不一致）"
    return True, "ok"


def empty_local() -> Dict[str, Any]:
    """端点本地策略的初始形态（未施加任何限制）。"""
    return {"sensitive_paths": [], "approval_floor": None,
            "allowed_models": None, "allowed_plugins": None, "require_strict_mode": False,
            "policy_version": 0}


def _intersect(a: Optional[List[str]], b: Optional[List[str]]) -> Optional[List[str]]:
    if a is None:
        return list(b) if b is not None else None
    if b is None:
        return list(a)
    return [x for x in a if x in set(b)]


def merge_tighten(local: Dict[str, Any], remote: Dict[str, Any]) -> Dict[str, Any]:
    """把远端策略合并进本地策略：**结果只可能更严**。

    单调性（tests/test_control_plane.py 逐条锁住）：
      · sensitive_paths 只增不减（并集）
      · approval_floor 只升不降（取更严）
      · allowed_models / allowed_plugins 只缩不扩（交集）
      · require_strict_mode 只会由 False 变 True
      · 幂等：同一包合并两次结果相同
    """
    validate_keys(remote)
    _check_types(remote)
    out = dict(local)

    # 并集：路径只增不减
    merged_paths = list(out.get("sensitive_paths") or [])
    for p in remote.get("sensitive_paths", []) or []:
        if p not in merged_paths:
            merged_paths.append(p)
    out["sensitive_paths"] = merged_paths

    # 取更严：审批下限只能升
    local_floor = out.get("approval_floor")
    remote_floor = remote.get("approval_floor")
    if remote_floor is not None:
        if local_floor is None or APPROVAL_RANK[remote_floor] > APPROVAL_RANK[local_floor]:
            out["approval_floor"] = remote_floor

    # 交集：允许集只能缩
    out["allowed_models"] = _intersect(out.get("allowed_models"),
                                       remote.get("allowed_models"))
    out["allowed_plugins"] = _intersect(out.get("allowed_plugins"),
                                        remote.get("allowed_plugins"))

    # 或：严格模式只可能被打开
    out["require_strict_mode"] = bool(out.get("require_strict_mode")) or bool(
        remote.get("require_strict_mode"))

    # 版本只增（用于端点判断"是否收到过更新的策略"）。
    # 同时写 version：合并结果会被存进端点配置并随上报回传（"我应用了哪一版"），
    # 界面据此算策略漂移；只写 policy_version 会让漂移永远显示成 v0。
    out["policy_version"] = max(int(out.get("policy_version", 0)), int(remote.get("version", 0)))
    out["version"] = out["policy_version"]
    return out


def is_tightening_only(local: Dict[str, Any], merged: Dict[str, Any]) -> bool:
    """自检：断言合并结果确实没有变松（供测试与端点启动自检调用）。"""
    if set(merged["sensitive_paths"]) < set(local.get("sensitive_paths") or []):
        return False
    lf, mf = local.get("approval_floor"), merged.get("approval_floor")
    if lf is not None and (mf is None or APPROVAL_RANK[mf] < APPROVAL_RANK[lf]):
        return False
    for key in ("allowed_models", "allowed_plugins"):
        lv, mv = local.get(key), merged.get(key)
        if lv is not None and mv is not None and set(mv) - set(lv):
            return False
    if local.get("require_strict_mode") and not merged.get("require_strict_mode"):
        return False
    return True


def describe(bundle: Dict[str, Any]) -> Dict[str, str]:
    """给界面用的中文摘要（键 → 人话）。"""
    out: Dict[str, str] = {}
    if bundle.get("sensitive_paths"):
        out["敏感路径"] = f"追加 {len(bundle['sensitive_paths'])} 条"
    if bundle.get("approval_floor"):
        out["审批下限"] = {"approve": "自动放行", "confirm": "必须人工确认", "deny": "一律拒绝"}[
            bundle["approval_floor"]]
    if bundle.get("allowed_models") is not None:
        out["允许模型"] = "、".join(bundle["allowed_models"]) or "（空＝全部禁用）"
    if bundle.get("allowed_plugins") is not None:
        out["允许插件"] = "、".join(bundle["allowed_plugins"]) or "（空＝全部禁用）"
    if bundle.get("require_strict_mode"):
        out["严格模式"] = "强制开启（禁用解释器）"
    if bundle.get("note"):
        out["备注"] = str(bundle["note"])
    return out
