# -*- coding: utf-8 -*-
"""
隐盾对话记忆优化 - 功能验证测试
验证项：
1. 跨轮脱敏映射累积与还原（改动A核心）
2. token 估算优化（改动D）
3. 工具调用对配平（改动D）
4. 摘要二次脱敏（改动B）
5. memory_manager 序列化回归
"""
import os
import sys
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from yindun.core.memory_manager import SummarizableChatHistory, _estimate_tokens
from yindun.core.privacy_engine import PrivacyEngine


def test_01_estimate_tokens():
    """测试1：token 估算优化"""
    # 纯中文：每字约1 token
    cn = "你好世界，这是我的手机号。"
    cn_tokens = _estimate_tokens(cn)
    cn_len = len(cn)
    assert cn_tokens >= cn_len * 0.5, f"中文估算过低: {cn_tokens}"
    # 纯英文：约4字符1 token
    en = "hello world this is a test message"  # 36字符
    en_tokens = _estimate_tokens(en)
    assert en_tokens <= 12, f"英文估算过高: {en_tokens}（期望<=12，即36//4+1）"
    # 空文本
    assert _estimate_tokens("") == 0, "空文本应为0"
    print(f"✅ 测试1通过: token估算优化正常（中文'{cn_tokens}'tokens, 英文'{en_tokens}'tokens）")


def test_02_tool_pair_balance():
    """测试2：工具调用对配平"""
    h = HumanMessage(content="问题")
    # 一轮工具调用：AI(tool_calls) -> ToolMessage -> AI(final)
    ai_tool = AIMessage(content="", tool_calls=[{"name": "read", "args": {}, "id": "call_1"}])
    tool_msg = ToolMessage(content="结果", tool_call_id="call_1")
    ai_final = AIMessage(content="回答")
    history = SummarizableChatHistory()
    history._messages = [h, ai_tool, tool_msg, ai_final]

    # 只保留 tool_msg 和 ai_final——tool_msg 的请求 ai_tool 应被补回
    recent_ids = {id(tool_msg), id(ai_final)}
    balanced = SummarizableChatHistory._ensure_tool_pair_ids(recent_ids, history._messages)
    assert id(ai_tool) in balanced, "ToolMessage 的请求应被补回"
    # 只保留 ai_tool 和 ai_final——ai_tool 的响应 tool_msg 应被补回
    recent_ids2 = {id(ai_tool), id(ai_final)}
    balanced2 = SummarizableChatHistory._ensure_tool_pair_ids(recent_ids2, history._messages)
    assert id(tool_msg) in balanced2, "tool_call 的响应应被补回"
    print(f"✅ 测试2通过: 工具调用对配平正常（补回 {len(balanced)} 条中的 2 条配对）")


def test_03_tool_pair_get_context():
    """测试3：get_context_messages 触发摘要时保持工具对完整"""
    history = SummarizableChatHistory(max_tokens=100)
    # 填充大量消息，超过阈值
    for i in range(30):
        history._messages.append(HumanMessage(content=f"这是第{i}轮问题，包含一些较长的中文内容用于撑大token预算。"))
        ai_tool = AIMessage(content="", tool_calls=[{"name": f"tool_{i}", "args": {}, "id": f"call_{i}"}])
        history._messages.append(ai_tool)
        history._messages.append(ToolMessage(content="工具结果内容", tool_call_id=f"call_{i}"))
        history._messages.append(AIMessage(content=f"这是第{i}轮回答。"))

    assert history.is_over_threshold(), "消息应已超阈值"

    # 使用假 llm（返回固定摘要）验证
    class FakeLLM:
        def invoke(self, prompt):
            return AIMessage(content="这是一段对话摘要内容。")

    context = history.get_context_messages(FakeLLM())
    assert context[0].content.startswith("[对话历史摘要]"), "摘要应置于首部"
    # 校验所有 ToolMessage 都有对应请求，所有带 tool_calls 的 AIMessage 都有响应
    req_ids = set()
    for m in context:
        if isinstance(m, AIMessage):
            for tc in (getattr(m, "tool_calls", None) or []):
                tc_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")
                if tc_id:
                    req_ids.add(tc_id)
    res_ids = set()
    for m in context:
        if isinstance(m, ToolMessage):
            tcid = getattr(m, "tool_call_id", "")
            if tcid:
                res_ids.add(tcid)
    # 每个响应必须有请求；每个请求必须有响应
    assert res_ids.issubset(req_ids), f"存在无请求的 ToolMessage: {res_ids - req_ids}"
    assert req_ids.issubset(res_ids), f"存在无响应的 tool_call: {req_ids - res_ids}"
    print(f"✅ 测试3通过: 摘要上下文工具对完整（{len(req_ids)} 对，全部成对）")


def test_04_summary_second_anonymization():
    """测试4：摘要二次脱敏"""
    history = SummarizableChatHistory()
    history._messages = [HumanMessage(content="我的手机号是13800138000")]

    class FakeLLM:
        def invoke(self, prompt):
            # 模拟 LLM 摘要时"漏脱敏"，把真实手机号带进摘要
            return AIMessage(content="用户手机号是13800138000，邮箱是test@qq.com。")

    summary = history.summarize(FakeLLM())
    # 二次脱敏后摘要不应含真实手机号/邮箱
    assert "13800138000" not in summary, f"摘要含真实手机号: {summary}"
    assert "test@qq.com" not in summary, f"摘要含真实邮箱: {summary}"
    assert "[PHONE_" in summary or "[EMAIL_" in summary, f"摘要应有占位符: {summary}"
    print(f"✅ 测试4通过: 摘要二次脱敏正常（摘要={summary}）")


def test_05_cross_turn_mapping_restore():
    """测试5：跨轮脱敏映射累积与还原（改动A核心）"""
    engine = PrivacyEngine()
    # 第1轮：用户输入含手机号
    anon1, box1 = engine.anonymize("我的电话是13800138000")
    # 占位符为含随机 nonce 的新格式 [PHONE_0_abcd]（防占位符劫持），
    # 因此【不要硬编码 [PHONE_0]】，须从映射表取实际占位符
    ph = next(iter(box1))
    assert ph.startswith("[PHONE_"), f"未生成手机号占位符: {anon1}"
    assert len(ph.split("_")) == 3, f"应为含 nonce 的新格式占位符: {ph}"
    # 模拟 worker 累积映射
    box_mapping = {}
    merged1 = {**box_mapping, **box1}
    reply1 = f"好的，已记录你的电话{ph}"
    restored1 = engine.deanonymize(reply1, merged1)
    assert "13800138000" in restored1, f"第1轮还原失败: {restored1}"
    box_mapping.update(box1)  # 累积

    # 第2轮：新的 worker 从会话恢复 box_mapping
    box_mapping_restore = dict(box_mapping)
    # 第2轮 LLM 引用了历史占位符（来自历史消息）
    reply2 = f"你之前说电话是{ph}，对吗？"
    restored2 = engine.deanonymize(reply2, box_mapping_restore)
    assert "13800138000" in restored2, f"跨轮还原失败: {restored2}"
    print(f"✅ 测试5通过: 跨轮映射累积与还原正常（占位符 {ph}，第2轮成功还原历史占位符）")


def test_06_memory_serialization_regression():
    """测试6：memory_manager 序列化回归（不影响现有会话）"""
    history = SummarizableChatHistory()
    history.add_user_message("你好")
    history.add_ai_message("你好，有什么可以帮你？")
    ai_tool = AIMessage(content="", tool_calls=[{"name": "list_local_files", "args": {"target_directory": "E:\\"}, "id": "call_x"}])
    history._messages.append(ai_tool)
    history._messages.append(ToolMessage(content="文件列表", tool_call_id="call_x"))
    history._messages.append(AIMessage(content="完成"))

    dict_list = history.to_dict_list()
    assert dict_list[0]["role"] == "user"
    assert any(m.get("tool_calls") for m in dict_list if m.get("role") == "assistant"), "应序列化 tool_calls"
    assert any(m.get("role") == "tool" for m in dict_list), "应序列化 ToolMessage"

    restored = SummarizableChatHistory.from_dict_list(dict_list)
    assert len(restored._messages) == len(history._messages), "序列化往返消息数应一致"
    print(f"✅ 测试6通过: 序列化往返正常（{len(restored._messages)} 条）")


if __name__ == "__main__":
    print("=" * 60)
    print("隐盾对话记忆优化 - 功能验证测试")
    print("=" * 60)
    print()
    tests = [
        test_01_estimate_tokens,
        test_02_tool_pair_balance,
        test_03_tool_pair_get_context,
        test_04_summary_second_anonymization,
        test_05_cross_turn_mapping_restore,
        test_06_memory_serialization_regression,
    ]
    passed = 0
    failed = 0
    for i, test in enumerate(tests, 1):
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            import traceback
            print(f"❌ 测试{i:02d}失败 [{test.__name__}]: {type(e).__name__}: {e}")
            traceback.print_exc()

    print()
    print("=" * 60)
    print(f"测试结果: {passed}/{len(tests)} 通过, {failed} 失败")
    print("=" * 60)
    sys.exit(0 if failed == 0 else 1)
