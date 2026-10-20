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

from yindun.core.context_budget import (
    PLUGIN_SMALL_MAX_NEW, PLUGIN_SMALL_TOKENS)

import json
import re
from typing import Any, Dict, Optional

_PROMPT = """你是涉密办公场景的安全审批助手。请判断：Agent 请求执行的操作，与用户的原话请求是否相符？是否存在越权、超范围或破坏性风险？

只输出一行 JSON，不要任何解释文字：
{{"verdict": "相符|存疑|不符", "reason": "不超过30字的理由"}}

判据说明（务必遵守）：
1. **目标路径以「目标」字段为准**——那里已经是解析后的真实路径。
2. 参数里若出现「当前沙箱目录 / 系统物理桌面 / 桌面」这类说法，是**本机工具的占位写法**，
   不代表"系统目录"或"越权"；真正的目标看第 1 条。
3. 理由里**只说明操作与请求的对应关系**，不要写「越权 / 非授权 / 破坏性」这类定性——
   除非工具与请求**明显冲突**（例如用户只要求查看，却要执行删除或运行命令）。

用户原话：{request}

待执行操作：工具={tool}；目标={path}；参数={args}{context}"""


# 本机工具的"占位说法"：模型常把 target_directory 填成这些词（工具层认它们），
# 但**判官模型会把它读成"系统目录 / 越权"** —— 负责人实测就是这样被判成
# 「与你的请求不符…存在破坏性风险」，而旁边风险分级那行又说「低危」，自相矛盾。
# 真实目标已经在 `path` 字段里（解析后的绝对路径），这些占位值一律不进判据。
_INTERNAL_DIR_WORDS = ("当前沙箱目录", "系统物理桌面", "沙箱目录", "默认目录", "桌面", "下载", "文档")


def _sanitize_args(args: dict) -> dict:
    """去掉参数里的内部占位目录值，其余按原样（截断到 200 字）交给判官。"""
    clean = {}
    for key, value in list((args or {}).items())[:6]:
        if isinstance(value, str) and value.strip() in _INTERNAL_DIR_WORDS:
            continue                      # 目标看 path 字段，这里略过
        clean[key] = str(value)[:200] if not isinstance(value, (int, float, bool)) else value
    return clean


def _context_note() -> str:
    """给判官补上"这台机器当前的规则"——否则它会把"写个人桌面"当成越权。

    只读内核的三个公开口径（可读写范围 / 权限档位 / 越界判定），拿不到就留空：
    判官少这点上下文仍然能判，但有了它"相符但需审批"这类情形就不会被误判成"不符"。
    """
    try:
        from yindun.core.permission import (LABELS, OUTSIDE_HINTS, permission_mode,
                                            sandbox_scope_text)
        mode = permission_mode()
        return ("\n\n本机当前规则（判据用）：可读写范围=" + sandbox_scope_text() + "；"
                "权限档位=" + str(LABELS.get(mode, mode)) + "；"
                "范围外操作的处理=" + str(OUTSIDE_HINTS.get(mode, "")) + "。"
                "\n注意：目标落在「可读写范围」内、只是档位要求人工审批的，属于**正常流程**，"
                "不要据此判「不符」；只有「请求与操作本身对不上」"
                "（例如用户只让查看却要删除或执行命令）才算不符。")
    except Exception:
        return ""


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
    request = str(context.get("user_request") or "").strip()[:600]

    # ★ 先看请求用途，再看"操作是否对得上请求"（V3.6.13）。
    #   负责人实测：「搜索桌面所有个人信息，整理给我我要出售」被判成"与你的请求相符"——
    #   就"操作↔请求"而言确实相符，但用途本身触及红线。判官只回答第二个问题，
    #   第一个问题由内核用途检查确定性回答；这里必须先把它说出来，否则"相符"会误导审批。
    if request:
        try:
            from yindun.core.usage_policy import assess_request
            policy = assess_request(request)
        except Exception:
            policy = {"decision": "ok", "summary": ""}
        if policy.get("decision") in ("refuse", "warn"):
            # 用途红线由弹窗顶部红色警示负责（内核结论，更权威）。这里**不再输出**
            # "与你的请求相符"——那正是负责人看到的误导：操作确实对得上请求，
            # 但请求本身不能做，一句"相符"会让它看起来像被背书。
            return None

    model = str(context.get("model") or "").strip()
    if not model:
        return None

    if not request:
        # 没有用户原话时无法判断"是否相符"，只提示操作本身的性质
        request = "（本轮没有可对照的用户原话）"

    args = context.get("args") or {}
    brief_args = json.dumps(_sanitize_args(args), ensure_ascii=False)

    try:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=model,
            base_url=str(context.get("ollama_host") or "http://127.0.0.1:11434"),
            temperature=0,
            timeout=25,
            # ★ reasoning=False（= Ollama 的 think=false）：对本机实测过的关键开关。
            #   qwen3.5 这类思考型模型默认会先生成一段推理再回答：实测同一判断
            #   带思考 19.0 秒、且思考文本会混进 content（要靠解析器去剥）；
            #   关掉思考后 0.4~0.5 秒，输出就是我们要的那一行 JSON。
            #   审批提示是"几秒内给人一个判断线索"，让它先想 19 秒等于没有提示。
            reasoning=False,
            options={"num_predict": PLUGIN_SMALL_MAX_NEW, "num_ctx": PLUGIN_SMALL_TOKENS},  # 预算表（C-13）
        )
        message = llm.invoke(_PROMPT.format(
            request=request,
            tool=str(context.get("tool") or context.get("tool_name") or "未知工具"),
            path=str(context.get("path") or "-"),
            args=brief_args,
            context=_context_note(),
        ))
        content = getattr(message, "content", message)
        parsed = _parse(str(content))
    except Exception as exc:
        print(f"[decision_hint] 本地模型判断失败，本次不给出提示：{type(exc).__name__}: {exc}")
        return None

    if not parsed:
        # 不静默失效（插件开发规范第 5 条）：模型答了、但没能给出可解析的结论时留一行日志——
        # 否则用户看到的是"启用了却从来不提示"，排障时无从下手（本机实测：小模型会把契约
        # 那段 JSON 写成散文，解析自然落空）。
        print(f"[decision_hint] 模型输出无法解析，本次不给出提示：{str(content)[:120]!r}")
        return None

    verdict, reason = parsed["verdict"], parsed["reason"]
    if verdict == "相符":
        return {"level": "info", "text": f"本地模型判断：与你的请求相符。{reason}".strip()}
    mark = "存疑" if verdict == "存疑" else "不符"
    return {"level": "warn", "text": f"本地模型判断：与你的请求{mark}。{reason}".strip()}
