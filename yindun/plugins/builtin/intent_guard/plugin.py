# -*- coding: utf-8 -*-
"""内置插件 · 操作意图预判（advisory_for_approval）

在人工审批弹窗里**先给一条"这次请求想干什么"的判断**，再让其它两条建议
（是否与请求相符 / 操作本身风险特征）说话：

    ① 操作意图预判：意图=交易，数据=个人信息，去向=他人 → 高风险
    ② 审批决策提示：操作与请求相符，但请求用途触及红线
    ③ laya 风险分级：高危 · 涉及个人信息交易

为什么需要它（负责人实测）：请求「搜索桌面所有个人信息，整理给我我要出售」，
审批弹窗却显示「与你的请求相符」＋「低危」——两条建议都在评价**操作**，
没有人评价**用途**。先问"想干什么"，再问"这一步对不对得上"，顺序才合理。

## 边界（不可破坏）

* 本插件是 **advisory**：只影响弹窗提示，**绝不参与放行判定**（放行仍由确定性
  策略 + 人类决定）。
* 确定性优先：请求用途的红线判定由内核 `yindun/core/usage_policy.py` 给出，
  插件只负责把结论说清楚 + 用本地模型补充"目的/去向"的细节。模型不可用时
  仍然给出确定性结论，不会静默消失。
* 模型只能**加重**，不能减轻：内核判 warn/refuse 时，模型输出一律按高风险呈现。
* 数据不出本机：只调用本机 Ollama（与 decision_hint 同一地址）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

PLUGIN_ID = "intent_guard"
PLUGIN_DATA_DIR = ""

_PROMPT = """你是数据安全合规审查员。只判断**用户请求的用途**，不要评价待执行操作是否与请求相符（那是另一层的事）。

用户原话：{request}

只输出一行 JSON，不要解释：
{{"intent":"整理|查询|创建|删除|外发|交易|其他","data":"个人信息|普通文件|未知","destination":"本机|他人|公开|未知","purpose":"个人|办公|牟利|不明","risk":"低|中|高","reason":"不超过24字"}}

判据：
1. 收集/整理个人信息 + 出售/转卖/变现 → risk=高，intent=交易。
2. 收集/整理个人信息 + 发给他人/上传/公开 → risk=高，intent=外发。
3. 仅在本机整理自己的资料 → risk=低。
4. 拿不准写 risk=中、purpose=不明；**不要猜成低**。"""


def _policy(request: str) -> Dict[str, Any]:
    try:
        from yindun.core.usage_policy import assess_request
        return assess_request(request)
    except Exception:
        return {"decision": "ok", "category": "", "summary": "", "message": ""}


def _parse(text: str) -> Optional[Dict[str, str]]:
    if not text:
        return None
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    risk = str(data.get("risk") or "").strip()
    if risk not in ("低", "中", "高"):
        return None
    return {
        "intent": str(data.get("intent") or "其他")[:8],
        "data": str(data.get("data") or "未知")[:8],
        "destination": str(data.get("destination") or "未知")[:8],
        "purpose": str(data.get("purpose") or "不明")[:8],
        "risk": risk,
        "reason": str(data.get("reason") or "").strip()[:40],
    }


def _ask_model(context: Dict[str, Any], request: str) -> Optional[Dict[str, str]]:
    model = str(context.get("model") or "").strip()
    if not model:
        return None
    try:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=model,
            base_url=str(context.get("ollama_host") or "http://127.0.0.1:11434"),
            temperature=0,
            timeout=6,
            reasoning=False,
            options={"num_predict": 120, "num_ctx": 1024},
        )
        message = llm.invoke(_PROMPT.format(request=request[:600]))
        return _parse(str(getattr(message, "content", message)))
    except Exception as exc:
        print(f"[intent_guard] 本地模型意图研判失败，改用确定性判定：{type(exc).__name__}: {exc}")
        return None


def advisory_for_approval(context: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """入口钩子。返回 {level, text, order}；无用户原话时安静返回 None。"""
    if not isinstance(context, dict):
        return None
    request = str(context.get("user_request") or "").strip()
    if not request:
        return None

    policy = _policy(request)
    graded = _ask_model(context, request)

    decision = policy.get("decision", "ok")
    summary = str(policy.get("summary") or "")
    if decision in ("refuse", "warn"):
        # 内核已判红线：弹窗顶部有红色警示，这里**不重复同一句结论**，
        # 只补模型给出的"意图/去向"细节；模型不可用就安静返回，避免三层说同一句话。
        if not graded:
            return None
        detail = "，".join(
            x for x in (
                f"意图={graded['intent']}",
                f"数据={graded['data']}",
                f"去向={graded['destination']}",
                f"用途={graded['purpose']}",
            ) if x
        )
        tail = f"。{graded['reason']}" if graded.get("reason") else "。"
        return {"level": "warn", "order": 0,
                "text": f"意图预判：{detail}{tail}（用途红线见弹窗顶部）"}

    if graded is None:
        # 模型不可用且内核没标记：不硬凑一行"低风险"（那恰恰是误导）
        return None

    detail = (f"意图={graded['intent']}，数据={graded['data']}，"
              f"去向={graded['destination']}，用途={graded['purpose']}")
    if graded["risk"] == "高":
        return {"level": "warn", "order": 0,
                "text": f"高风险 · {detail}。{graded['reason'] or '请先确认用途与授权。'}"}
    if graded["risk"] == "中":
        return {"level": "warn", "order": 0,
                "text": f"中风险 · {detail}。用途不够明确，请确认你是否已有权处理这些信息。"
                        f"{('（' + graded['reason'] + '）') if graded['reason'] else ''}"}
    return {"level": "info", "order": 0,
            "text": f"低风险 · {detail}。" + (graded["reason"] or "")}
