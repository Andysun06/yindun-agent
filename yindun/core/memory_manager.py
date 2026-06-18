# -*- coding: utf-8 -*-
"""
隐盾 V2.2.0 — 🧠 智能记忆管理器
基于 LangChain ChatMessageHistory 封装，提供：
1. max_tokens 阈值控制
2. 上下文自动摘要机制（超出阈值时压缩早期对话为摘要）
3. 与 chat_sessions.json 兼容的序列化/反序列化
"""
from typing import Optional, Callable

from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.language_models import BaseChatModel


# ── Token 估算 ─────────────────────────────────────────────
def _estimate_tokens(text: str) -> int:
    """估算文本 token 数。优先使用 tiktoken，失败则用字符数/2.5 近似。"""
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # 中英文混合粗略估算：每个汉字约 1.5 token，英文单词约 1.3 token
        # 保守取字符数/2.5
        return max(1, len(text) // 2)


def _count_message_tokens(msg: BaseMessage) -> int:
    """估算单条消息的 token 数（内容 + 角色标记开销约 4 token）"""
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    return _estimate_tokens(content) + 4


class SummarizableChatHistory(BaseChatMessageHistory):
    """
    可摘要的聊天历史管理器，继承 LangChain BaseChatMessageHistory。

    核心特性：
    - 内嵌 max_tokens 阈值，超出后自动触发 LLM 摘要压缩早期消息
    - 摘要以 SystemMessage 形式装在消息列表首部
    - 支持 to_dict_list / from_dict_list 与 chat_sessions.json 互通
    """

    # ── 摘要提示词模板 ──────────────────────────────────────
    SUMMARY_PROMPT = (
        "请用一段中文简要总结以下对话的核心内容和关键结论（控制在 200 字以内），"
        "只输出总结文本，不要附加任何额外说明：\n\n"
    )

    def __init__(self, max_tokens: int = 5000):
        super().__init__()
        self._messages: list[BaseMessage] = []
        self.max_tokens = max_tokens
        self._summary: str = ""

    # ── BaseChatMessageHistory 接口 ────────────────────────
    @property
    def messages(self) -> list[BaseMessage]:
        return self._messages

    def add_message(self, message: BaseMessage) -> None:
        self._messages.append(message)

    def clear(self) -> None:
        self._messages.clear()
        self._summary = ""

    # ── 便捷方法 ───────────────────────────────────────────
    def add_user_message(self, content: str) -> None:
        self.add_message(HumanMessage(content=content))

    def add_ai_message(self, content: str) -> None:
        self.add_message(AIMessage(content=content))

    # ── Token 统计 ─────────────────────────────────────────
    def total_tokens(self) -> int:
        """计算当前所有消息的 token 总数"""
        base = sum(_count_message_tokens(m) for m in self._messages)
        if self._summary:
            base += _estimate_tokens(self._summary) + 4
        return base

    def is_over_threshold(self) -> bool:
        return self.total_tokens() > self.max_tokens

    # ── 摘要压缩 ───────────────────────────────────────────
    def summarize(self, llm: BaseChatModel) -> str:
        """
        调用 LLM 将现有消息汇总为一段摘要文本。
        返回摘要字符串，同时内部更新 _summary 字段。
        """
        if not self._messages:
            return ""

        # 构建完整的对话文本
        conversation_text = "\n".join(
            f"{'用户' if isinstance(m, HumanMessage) else 'AI'}: {m.content}"
            for m in self._messages
        )
        prompt = self.SUMMARY_PROMPT + conversation_text

        try:
            resp = llm.invoke(prompt)
            self._summary = resp.content.strip() if hasattr(resp, 'content') else str(resp).strip()
        except Exception:
            # LLM 不可用时，取前 500 字作为退化摘要
            self._summary = conversation_text[:500] + "…"

        return self._summary

    # ── 获取 LLM 上下文消息（核心方法）─────────────────────
    def get_context_messages(self, llm: Optional[BaseChatModel] = None) -> list[BaseMessage]:
        """
        返回传给 LLM 的完整上下文消息列表。
        若 token 超阈值且提供了 llm，自动触发摘要压缩：
        - 生成摘要后清空早期消息，保留最近若干条
        - 摘要以 SystemMessage 形式置于列表首部
        """
        if not self._messages:
            return []

        need_summary = self.is_over_threshold()

        if need_summary and llm is not None:
            # 保留最近 20% 的消息（最少 4 条）
            keep_count = max(4, len(self._messages) // 5)
            old_msgs = self._messages[:-keep_count]
            recent_msgs = self._messages[-keep_count:]

            # 生成摘要
            saved = self._messages[:]
            self._messages = old_msgs
            self.summarize(llm)
            self._messages = saved  # 恢复完整历史

            # 构建上下文：SystemMessage(摘要) + 保留的最近消息
            context = [SystemMessage(content=f"[对话历史摘要]\n{self._summary}")]
            context.extend(recent_msgs)
            return context

        # 未超阈值 → 返回完整消息列表
        return list(self._messages)

    def update_with_context_result(self, user_input: str, ai_response: str):
        """
        在 LLM 回复后更新记忆：
        - 如果是摘要后的回复，清空旧消息并以 [摘要 + 最近N条 + 本轮对话] 重建
        - 否则直接追加
        """
        need_summary = self.is_over_threshold()

        if need_summary:
            keep_count = max(4, len(self._messages) // 5)
            recent = self._messages[-keep_count:]
            # 重建：摘要不存储为独立消息，保留在 _summary 中
            self._messages = list(recent)
            self._messages.append(HumanMessage(content=user_input))
            self._messages.append(AIMessage(content=ai_response))
        else:
            self._messages.append(HumanMessage(content=user_input))
            self._messages.append(AIMessage(content=ai_response))

    def update_with_full_chain(self, messages: list):
        """
        在 ReAct 循环完成后保存完整消息链。
        messages 应包含：[HumanMessage, ...(AIMessage with tool_calls + ToolMessage)*..., AIMessage final]
        """
        need_summary = self.is_over_threshold()
        if need_summary:
            keep_count = max(4, len(self._messages) // 5)
            self._messages = list(self._messages[-keep_count:])
        self._messages.extend(messages)

    # ── 序列化（兼容 chat_sessions.json）────────────────────
    def to_dict_list(self) -> list[dict]:
        """导出为 chat_sessions.json 兼容的消息列表。支持 Human/AIMessage/ToolMessage。"""
        from langchain_core.messages import ToolMessage
        result = []
        if self._summary:
            result.append({"role": "system", "content": f"[SUMMARY]{self._summary}"})
        for m in self._messages:
            if isinstance(m, HumanMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                result.append({"role": "user", "content": content})
            elif isinstance(m, ToolMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                result.append({"role": "tool", "content": content, "tool_call_id": getattr(m, "tool_call_id", "")})
            elif isinstance(m, AIMessage):
                content = m.content if isinstance(m.content, str) else str(m.content)
                msg_dict = {"role": "assistant", "content": content}
                tcs = getattr(m, "tool_calls", None)
                if tcs:
                    msg_dict["tool_calls"] = [
                        {"name": tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", ""),
                         "args": tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {}),
                         "id": tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")}
                        for tc in tcs
                    ]
                result.append(msg_dict)
        return result

    @classmethod
    def from_dict_list(cls, messages: list[dict], max_tokens: int = 5000) -> "SummarizableChatHistory":
        """从 chat_sessions.json 的消息列表恢复实例。支持 Human/AIMessage/ToolMessage。"""
        from langchain_core.messages import ToolMessage
        instance = cls(max_tokens=max_tokens)
        for item in messages:
            if not isinstance(item, dict):
                continue
            role = item.get("role", "")
            content = item.get("content", "")
            if role == "system" and content.startswith("[SUMMARY]"):
                instance._summary = content[len("[SUMMARY]"):]
            elif role == "user":
                instance.add_user_message(content)
            elif role == "tool":
                tc_id = item.get("tool_call_id", "")
                instance._messages.append(ToolMessage(content=content, tool_call_id=tc_id))
            elif role == "assistant":
                ai_msg = AIMessage(content=content)
                tool_calls = item.get("tool_calls")
                if tool_calls:
                    ai_msg.tool_calls = tool_calls
                instance._messages.append(ai_msg)
        return instance
