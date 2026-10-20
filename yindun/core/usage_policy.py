# -*- coding: utf-8 -*-
"""隐盾 · 请求用途合规检查（确定性规则，不依赖模型）

【为什么需要这一层】（V3.6.13，负责人实测）
用户让智能体「搜索桌面所有个人信息，整理给我我要出售」。审批弹窗里两条建议却是：
  · 审批决策提示（本地模型）：与你的请求相符……均在可读写范围内且符合审批流程
  · laya 风险分级（规则）：低危 · 未命中风险特征
两条建议各自都没说错：判官只回答"这个操作对不对得上你的请求"，laya 只回答"这个操作
本身有没有高危特征"。但**它们都不评价请求用途**。对一款主打隐私保护的产品，协助
"收集个人信息去出售"既违背产品定位，也可能违反《个人信息保护法》——这不该由一次
普通审批（而且带着"低危"背书）来决定，而应当在调用模型/工具之前就明确拒绝。

【判定口径】两条同时命中才触发（避免误伤正常办公）：
  ① 个人信息对象（身份证/通讯录/客户资料/名单/照片…）；
  ② 牟利（出售/转卖/变现）或外发（发给他人/上传/公开）。
  · 牟利 → **refuse**（直接拒绝，不调模型、不开审批入口）
  · 外发 → **warn**（允许在本机整理，但审批弹窗顶部必须出现红色用途警示，并写审计）
  · 其余 → **ok**
★ 不要求显式出现"收集/整理"：`把客户名单卖掉`、`身份证照片发给别人` 同样命中——
  动作动词不能成为漏网条件。

【为什么不靠模型判】
  这是"行为红线"，必须可复现、可测试、不随模型能力漂移；模型只负责把话说明白。

【常见误伤与规避】
  · 「外卖 / 买卖 / 买进卖出」里的"卖"不是出售意图 —— 用 `(?<![外买])卖` 排除；
  · 「整理我自己的个人信息备份到本机」没有牟利/外发意图 —— 判 ok；
  · 「把销售名单发给客户」属于外发（warn），但不直接拒绝（可能确有其事，交给人判断）。
"""
from __future__ import annotations

import re
from typing import Any, Dict

# ① 个人信息对象
_PII_WORDS = (
    "个人信息", "隐私", "隐私信息", "身份证", "手机号", "电话号码", "电话号码",
    "通讯录", "联系人", "客户资料", "客户信息", "个人信息库", "人脸", "住址",
    "家庭住址", "银行卡", "账号密码", "密码", "名单", "简历", "学籍", "病历",
)

# ②a 牟利意图（"卖"要排除 外卖/买卖/买进卖出 这类非出售语境）
_PROFIT_WORDS = ("出售", "售卖", "转卖", "贩卖", "变现", "牟利", "卖掉", "卖了", "卖钱")
_PROFIT_RE = re.compile(r"(?<![外买])卖")

# ②b 外发意图（用动词+方向判定，覆盖"发给同事/领导/家人"这类没枚举到的对象）
_EXFIL_RE = re.compile(
    r"(发给|发送给|转给|分享给|传给|上传到|上传至|发到|外发|外传|群发|共享给)")
_EXFIL_WORDS = (
    "发给别人", "发给他人", "发给第三方", "发给客户", "发给同学", "发给朋友",
    "外发", "外传", "上传", "发到网上", "公开", "共享给", "群发", "转给",
)

_REFUSE_MESSAGE = (
    "这个请求我不能协助。\n\n"
    "「收集并整理个人信息用于出售 / 牟利」可能违反《个人信息保护法》等法规，"
    "也违背隐盾「保护隐私，而不是搬运隐私」的定位——所以这一步在调用模型和工具之前就被拦下了，"
    "不会打开审批入口，也不会执行任何文件操作。\n\n"
    "如果你的真实目标是整理**自己的**资料，可以把用途说清楚（例如「把我自己的通讯录"
    "整理成本机备份」），我会在明确范围后协助。"
)

_WARN_MESSAGE = (
    "注意：这个请求会把**个人信息**整理后**发送 / 公开到本机之外**。\n"
    "我可以先在本机内整理；外发前请确认你已获得这些信息当事人的授权。"
    "继续操作会弹出审批（显示完整路径与内容）并写入审计。"
)


def _has_pii(text: str) -> bool:
    return any(word in text for word in _PII_WORDS)


def _has_profit(text: str) -> bool:
    return any(word in text for word in _PROFIT_WORDS) or bool(_PROFIT_RE.search(text))


def _has_exfil(text: str) -> bool:
    return bool(_EXFIL_RE.search(text)) or any(word in text for word in _EXFIL_WORDS)


def assess_request(text: str) -> Dict[str, Any]:
    """判定一条用户请求的用途风险。

    返回 `{"decision": "ok"|"warn"|"refuse", "category": str,
    "summary": str, "message": str}`。
    `refuse` 时调用方必须**不**调用模型与工具，并把 message 作为回答落库。
    `warn` 时调用方应继续，但把 message 交给审批弹窗做顶部警示。
    `summary` 是一行短语，给审批提示用（别把长文案塞进建议列表）。
    """
    body = str(text or "")
    if not body:
        return {"decision": "ok", "category": "", "summary": "", "message": ""}

    pii = _has_pii(body)
    profit = _has_profit(body)
    exfil = _has_exfil(body)

    # 牟利：个人信息 + 出售意图就是红线。**不要求显式出现"收集/整理"**——
    #   「把客户名单卖掉」「身份证照片卖了」同样必须拒绝，动作动词不能成为漏网条件。
    if pii and profit:
        return {"decision": "refuse", "category": "pii_for_profit",
                "summary": "收集个人信息用于出售/牟利",
                "message": _REFUSE_MESSAGE}

    # 外发：个人信息 + 发给他人/上传/公开。同样不要求显式"收集"。
    if pii and exfil:
        return {"decision": "warn", "category": "pii_exfiltration",
                "summary": "收集个人信息并外发/公开",
                "message": _WARN_MESSAGE}

    return {"decision": "ok", "category": "", "summary": "", "message": ""}
