# -*- coding: utf-8 -*-
"""非 OpenAI-Chat 协议的轻量适配器（V3.6.21）。

背景：模型接入页一直有四个「接口格式」选项，但内核只真正支持
OpenAI 兼容 Chat Completions，另外三个在界面上是置灰的——"宁可明说不支持"。
本模块把其中三种补成**真能用**：

  · ollama_native —— 由 llm_factory 直接用 ChatOllama 构建（不在这里）；
  · anthropic     —— Anthropic Messages API（`/v1/messages`）；
  · responses     —— OpenAI Responses API（`/responses`）。

设计约束：
  · **零新增依赖**：只用标准库 urllib（langchain-anthropic / openai 的新协议
    都不在依赖树里，也不想为了两个格式把 exe 再撑大）；
  · 只实现 Worker 真正用到的最小面：`bind_tools()` + `invoke()`。
    没有 `stream()` 时 Worker 会自动回落整段 invoke（见 `_invoke_llm_streaming`），
    因此这里不假装支持流式；
  · 工具调用双向翻译：LangChain 的 AIMessage.tool_calls / ToolMessage
    ↔ 目标协议的 tool_use / tool_result（Anthropic）或 function_call /
    function_call_output（Responses）；
  · 出错时抛出**可读**异常（带 HTTP 状态与上游 message 片段），由既有错误链路
    展示给用户，不做静默降级。

诚实声明：这两个适配器的协议一致性由本地 mock 服务回归（tests/test_chat_adapters.py），
真实厂商端点需要各自的有效 Key 才能端到端验证——没有 Key 的格式不宣称"已验证"。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage,
)


# ── 公共 HTTP ────────────────────────────────────────────────
def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any],
               timeout: float = 90.0) -> Dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            pass
        raise RuntimeError(_readable_http_error(exc.code, body)) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"连接失败：{getattr(exc, 'reason', exc)}") from exc
    try:
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"上游返回的不是 JSON（前 200 字）：{raw[:200]}") from exc


def _readable_http_error(status: int, body: str) -> str:
    """把 HTTP 错误翻译成用户能照着做的一句话（保留上游原文片段）。"""
    detail = body
    try:
        obj = json.loads(body)
        err = obj.get("error") if isinstance(obj, dict) else None
        if isinstance(err, dict):
            detail = str(err.get("message") or err.get("code") or body)
        elif isinstance(err, str):
            detail = err
        elif isinstance(obj, dict) and obj.get("message"):
            detail = str(obj["message"])
    except Exception:
        pass
    if status == 401:
        return f"密钥无效或未授权（401）：{detail[:200]}"
    if status == 403:
        return f"没有访问权限（403）：{detail[:200]}"
    if status == 404:
        return f"接口地址或模型不存在（404）：{detail[:200]}"
    if status == 429:
        return f"上游限流/额度不足（429），请稍后重试或换模型：{detail[:200]}"
    return f"上游返回 {status}：{detail[:200]}"


def _tool_schema(tool: Any) -> Dict[str, Any]:
    """从 LangChain 工具取 JSON Schema（优先 args_schema，回落 tool.args）。"""
    schema: Dict[str, Any] = {}
    try:
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is not None:
            if hasattr(args_schema, "model_json_schema"):
                schema = args_schema.model_json_schema()
            elif hasattr(args_schema, "schema"):
                schema = args_schema.schema()
    except Exception:
        schema = {}
    if not schema:
        schema = {"type": "object", "properties": dict(getattr(tool, "args", {}) or {})}
    schema.pop("title", None)
    return schema


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return "" if content is None else str(content)


def _base_of(base_url: str) -> str:
    """去掉用户可能粘贴的完整端点后缀，返回基础地址。"""
    url = str(base_url or "").strip().rstrip("/")
    for suffix in ("/v1/messages", "/messages", "/v1/responses", "/responses"):
        if url.lower().endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


# ── Anthropic Messages ─────────────────────────────────────
class AnthropicChatModel:
    """Anthropic Messages API 的最小 LangChain 兼容封装（非流式）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 max_tokens: int = 4096, timeout: float = 90.0,
                 api_version: str = "2023-06-01") -> None:
        self.base_url = _base_of(base_url)
        self.api_key = str(api_key or "")
        self.model = str(model or "")
        self.max_tokens = max(256, int(max_tokens or 4096))
        self.timeout = timeout
        self.api_version = api_version
        self._tools: List[Any] = []

    def bind_tools(self, tools: Sequence[Any]) -> "AnthropicChatModel":
        self._tools = list(tools or [])
        return self

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/v1/messages"

    def _convert(self, messages: Sequence[BaseMessage]) -> Tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        out: List[Dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                text = _text_of(msg.content)
                if text:
                    system_parts.append(text)
                continue
            if isinstance(msg, ToolMessage):
                out.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": str(getattr(msg, "tool_call_id", "") or ""),
                    "content": _text_of(msg.content),
                }]})
                continue
            if isinstance(msg, AIMessage):
                blocks: List[Dict[str, Any]] = []
                text = _text_of(msg.content)
                if text:
                    blocks.append({"type": "text", "text": text})
                for call in (getattr(msg, "tool_calls", None) or []):
                    blocks.append({
                        "type": "tool_use",
                        "id": str(call.get("id") or ""),
                        "name": str(call.get("name") or ""),
                        "input": dict(call.get("args") or {}),
                    })
                if blocks:
                    out.append({"role": "assistant", "content": blocks})
                continue
            if isinstance(msg, HumanMessage):
                out.append({"role": "user",
                            "content": [{"type": "text", "text": _text_of(msg.content)}]})
                continue
            # 其它消息类型（少见）：按用户文本处理，不丢内容
            text = _text_of(getattr(msg, "content", ""))
            if text:
                out.append({"role": "user", "content": [{"type": "text", "text": text}]})
        return "\n\n".join(system_parts), out

    def invoke(self, messages: Sequence[BaseMessage], **_: Any) -> AIMessage:
        system, converted = self._convert(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if self._tools:
            payload["tools"] = [{
                "name": str(getattr(t, "name", "") or ""),
                "description": str(getattr(t, "description", "") or ""),
                "input_schema": _tool_schema(t),
            } for t in self._tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": self.api_version}
        data = _post_json(self.endpoint, headers, payload, timeout=self.timeout)
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for block in (data.get("content") or []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "name": str(block.get("name") or ""),
                    "args": dict(block.get("input") or {}),
                    "id": str(block.get("id") or ""),
                })
        return AIMessage(content="".join(text_parts), tool_calls=tool_calls,
                         response_metadata={"stop_reason": data.get("stop_reason")})


# ── OpenAI Responses ───────────────────────────────────────
class ResponsesChatModel:
    """OpenAI Responses API 的最小 LangChain 兼容封装（非流式）。"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 max_tokens: int = 4096, timeout: float = 90.0) -> None:
        self.base_url = _base_of(base_url)
        self.api_key = str(api_key or "")
        self.model = str(model or "")
        self.max_tokens = max(256, int(max_tokens or 4096))
        self.timeout = timeout
        self._tools: List[Any] = []

    def bind_tools(self, tools: Sequence[Any]) -> "ResponsesChatModel":
        self._tools = list(tools or [])
        return self

    @property
    def endpoint(self) -> str:
        return f"{self.base_url}/responses"

    def _convert(self, messages: Sequence[BaseMessage]) -> Tuple[str, List[Dict[str, Any]]]:
        instructions: List[str] = []
        items: List[Dict[str, Any]] = []
        for msg in messages:
            if isinstance(msg, SystemMessage):
                text = _text_of(msg.content)
                if text:
                    instructions.append(text)
                continue
            if isinstance(msg, ToolMessage):
                items.append({
                    "type": "function_call_output",
                    "call_id": str(getattr(msg, "tool_call_id", "") or ""),
                    "output": _text_of(msg.content),
                })
                continue
            if isinstance(msg, AIMessage):
                text = _text_of(msg.content)
                if text:
                    items.append({"role": "assistant",
                                  "content": [{"type": "output_text", "text": text}]})
                for call in (getattr(msg, "tool_calls", None) or []):
                    items.append({
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(call.get("name") or ""),
                        "arguments": json.dumps(dict(call.get("args") or {}), ensure_ascii=False),
                    })
                continue
            if isinstance(msg, HumanMessage):
                items.append({"role": "user",
                              "content": [{"type": "input_text", "text": _text_of(msg.content)}]})
                continue
            text = _text_of(getattr(msg, "content", ""))
            if text:
                items.append({"role": "user",
                              "content": [{"type": "input_text", "text": text}]})
        return "\n\n".join(instructions), items

    def invoke(self, messages: Sequence[BaseMessage], **_: Any) -> AIMessage:
        instructions, items = self._convert(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": items,
            "max_output_tokens": self.max_tokens,
        }
        if instructions:
            payload["instructions"] = instructions
        if self._tools:
            payload["tools"] = [{
                "type": "function",
                "name": str(getattr(t, "name", "") or ""),
                "description": str(getattr(t, "description", "") or ""),
                "parameters": _tool_schema(t),
            } for t in self._tools]
        headers = {"Authorization": f"Bearer {self.api_key}"}
        data = _post_json(self.endpoint, headers, payload, timeout=self.timeout)
        text_parts: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        for item in (data.get("output") or []):
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for block in (item.get("content") or []):
                    if isinstance(block, dict) and block.get("type") == "output_text":
                        text_parts.append(str(block.get("text") or ""))
            elif item.get("type") == "function_call":
                try:
                    args = json.loads(item.get("arguments") or "{}")
                except Exception:
                    args = {}
                tool_calls.append({
                    "name": str(item.get("name") or ""),
                    "args": dict(args) if isinstance(args, dict) else {},
                    "id": str(item.get("call_id") or item.get("id") or ""),
                })
        return AIMessage(content="".join(text_parts), tool_calls=tool_calls,
                         response_metadata={"status": data.get("status")})
