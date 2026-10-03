# -*- coding: utf-8 -*-
"""内置插件 · 审批决策提示

做一件事：人工审批弹窗出现时，让**本地模型**看一眼
「用户刚才说了什么」和「Agent 现在要做什么」，给出一句提示——
是否相符、有无越权或破坏性风险，供人类在几秒内判断放行还是拦截。

为什么放在插件里：
  · 它需要本地模型算力，不是每个用户都装；内核保持精简，按需启用；
  · 它**只是建议**：判断依据是概率模型的输出，绝不允许参与审批放行判定
    （放行与否仍由确定性策略 + 人类决定，见开发记忆 §5 安全不变量）。

运行时若模型不可用/输出不可解析，一律安静返回 None（不打扰审批流程）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional

_PROMPT = """你是涉密办公场景的安全审批助手。请判断：Agent 请求执行的操作，与用户的原话请求是否相符？是否存在越权、超范围或破坏性风险？

只输出一行 JSON，不要任何解释文字：
{{"verdict": "相符|存疑|不符", "reason": "不超过30字的理由"}}

用户原话：{request}

待执行操作：工具={tool}；目标={path}；参数={args}"""


def _verdict_from_text(text: str) -> Optional[str]:
    """从自由文本里判断结论。7B 模型常写成 "不符|越权" 这类带后缀的形式，
    所以先按"不符 → 存疑 → 相符"的顺序做关键词判定（注意"不符合"含"符合"，必须先判"不符"）。"""
    if not text:
        return None
    if any(k in text for k in ("不符", "不符合", "不匹配", "不一致", "越权", "无关", "不相关")):
        return "不符"
    if any(k in text for k in ("存疑", "可疑", "不确定", "不确定", "有风险", "需确认", "谨慎")):
        return "存疑"
    if any(k in text for k in ("相符", "符合", "匹配", "一致", "相关")):
        return "相符"
    return None


def _parse(text: str) -> Optional[Dict[str, str]]:
    """从模型输出里宽松地取出 verdict / reason（模型常加解释、代码块或后缀）。"""
    if not text:
        return None
    verdict = None
    reason = ""
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
        except Exception:
            data = None
        if isinstance(data, dict):
            verdict = _verdict_from_text(str(data.get("verdict", ""))) or _verdict_from_text(str(data.get("reason", "")))
            reason = str(data.get("reason", "")).strip()
    if verdict is None:
        verdict = _verdict_from_text(text)
    if verdict is None:
        return None
    if not reason:
        # 没有结构化理由时，截取模型输出里最有信息量的一段
        cleaned = re.sub(r"\{|\}|\"verdict\"|\"reason\"|:", " ", text).strip()
        reason = cleaned[:40] or "模型未给出理由"
    return {"verdict": verdict, "reason": reason[:60]}


def advisory_for_approval(context: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """入口钩子：返回 {"level": "info"|"warn", "text": str} 或 None。"""
    model = str(context.get("model") or "").strip()
    if not model:
        return None

    request = str(context.get("user_request") or "").strip()[:600]
    if not request:
        # 没有用户原话时无法判断"是否相符"，只提示操作本身的性质
        request = "（本轮没有可对照的用户原话）"

    args = context.get("args") or {}
    brief_args = json.dumps(
        {k: (str(v)[:200] if not isinstance(v, (int, float, bool)) else v)
         for k, v in list(args.items())[:6]},
        ensure_ascii=False,
    )

    try:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=model,
            base_url=str(context.get("ollama_host") or "http://127.0.0.1:11434"),
            temperature=0,
            timeout=8,
            options={"num_predict": 64, "num_ctx": 2048},
        )
        message = llm.invoke(_PROMPT.format(
            request=request,
            tool=str(context.get("tool") or context.get("tool_name") or "未知工具"),
            path=str(context.get("path") or "-"),
            args=brief_args,
        ))
        content = getattr(message, "content", message)
        parsed = _parse(str(content))
    except Exception as exc:
        print(f"[decision_hint] 本地模型判断失败，本次不给出提示：{type(exc).__name__}: {exc}")
        return None

    if not parsed:
        return None

    verdict, reason = parsed["verdict"], parsed["reason"]
    if verdict == "相符":
        return {"level": "info", "text": f"本地模型判断：与你的请求相符。{reason}".strip()}
    mark = "存疑" if verdict == "存疑" else "不符"
    return {"level": "warn", "text": f"本地模型判断：与你的请求{mark}。{reason}".strip()}
