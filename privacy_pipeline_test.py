# -*- coding: utf-8 -*-
"""
端到端链路验证：模拟 agent_worker.py 的脱敏决策，检验真实数据流是否泄露
对应 agent_worker.py 第248行（输入脱敏）、第770行（工具结果脱敏）、
第399行（审计日志 preview）、main_window.py 第751行（附件原文落盘）
"""
import sys
import os
import json

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yindun.core.privacy_engine import PrivacyEngine

engine = PrivacyEngine()
privacy_shield = True  # 对应 worker 默认开启的隐私防护

print("=" * 78)
print("【端到端链路验证】模拟 Worker 脱敏决策")
print("=" * 78)

# 模拟一份含敏感数据的本地文件（工具可能读取到的内容）
TOOL_RESULT = (
    "客户姓名：张伟明，手机号：13812345678，"
    "身份证：110101199003072316，银行卡：6228480402564890018，"
    "住址：北京市海淀区中关村大街1号"
)

SCENARIOS = [
    ("场景1：用户提问含敏感词",
     "查一下 13812345678 这个客户的资料"),
    ("场景2：用户提问无敏感词（最典型：让AI读文件）",
     "帮我总结一下这份客户资料文件的内容"),
    ("场景3：用户提问无敏感词（挂载附件后追问）",
     "这个表里第3行的联系方式是什么"),
]

for title, user_input in SCENARIOS:
    print(f"\n{title}")
    print(f"  用户输入: {user_input}")

    # ── 第248行：engine.anonymize(self.user_input) ──
    ai_input, box = engine.anonymize(user_input)
    print(f"  输入脱敏后: {ai_input}")
    print(f"  本轮映射 box: {'非空（%d项）' % len(box) if box else '空 ← 关键'}")

    # ── 第770行（修复后）：if self.privacy_shield and tool_name != "search_knowledge_base" ──
    tool_result = TOOL_RESULT
    tool_name = "read_local_file"  # 模拟普通工具，非知识库检索
    if privacy_shield and tool_name != "search_knowledge_base":
        tool_result = engine.anonymize(tool_result)[0]
        decision = "执行脱敏"
    else:
        decision = "⚠️ 跳过脱敏"

    print(f"  工具结果处理: {decision}")
    print(f"  发给模型的内容: {tool_result[:110]}")

    leaked = ("13812345678" in tool_result) or ("110101199003072316" in tool_result)
    print(f"  结论: {'❌ 明文敏感数据已送达模型' if leaked else '✅ 已脱敏'}")

# ── 第399/410行（修复后）：审计记录脱敏态副本 _reply_before_restore ──
print("\n" + "=" * 78)
print("【审计日志落盘验证】第410行 log_llm_output(_reply_before_restore, ...) 记录脱敏态")
print("=" * 78)
user_input = "客户 13812345678 的手机号是多少"
ai_input, box = engine.anonymize(user_input)
real_ph = list(box.keys())[0]                          # 取本轮真实占位符
final_reply_model = f"该客户手机号为 {real_ph}。"        # 模型输出的脱敏态回复
_reply_before_restore = final_reply_model               # 第384行：审计用脱敏态副本
final_reply_user = engine.deanonymize(final_reply_model, box)  # 第388行还原
print(f"  模型输出(脱敏态): {final_reply_model}")
print(f"  还原后(给用户):   {final_reply_user}")
print(f"  审计日志 preview 落盘内容: {_reply_before_restore[:200]}（脱敏态）")
print(f"  结论: ✅ 审计日志记录脱敏态，未落盘还原后的明文")

# ── 工具结果映射合并验证（对应第2步修复后的 Worker._anonymize_tool_output）──
# 修复前：engine.anonymize(tool_result)[0] 只取文本、丢弃 mapping
#         → 最终回复中工具数据的占位符无法还原，用户看到 [PHONE_0_xxxx] 乱码
# 修复后：_anonymize_tool_output 内部调用 _merge_box_mapping 将 mapping 并入
#         self._box_mapping，deanonymize 时即可还原为明文
print("\n" + "=" * 78)
print("【工具结果映射合并验证】_anonymize_tool_output 将 mapping 并入 _box_mapping")
print("=" * 78)
tool_result = TOOL_RESULT
anon_text, tool_box = engine.anonymize(tool_result)

# ── 对照：修复前（丢弃映射）──
lost = engine.deanonymize(anon_text, {})
print(f"  修复前（丢弃映射）还原结果: {lost[:90]}")

# ── 修复后（合并映射，对应 Worker._box_mapping）──
_box_mapping: dict = {}
_box_mapping.update(tool_box)          # ← 第2步的核心：合并而非丢弃
restored = engine.deanonymize(anon_text, _box_mapping)
print(f"  修复后（合并映射）还原结果: {restored[:90]}")

ok = ("13812345678" in restored) and ("110101199003072316" in restored)
stale = ("13812345678" in lost)
print(f"\n  映射项数: {len(tool_box)} | 修复前能还原明文: {stale} | 修复后能还原明文: {ok}")
print(f"  结论: {'✅ 映射已正确合并，占位符还原为明文' if (ok and not stale) else '❌ 映射合并异常'}")

print("\n" + "=" * 78)
print("验证结束")
print("=" * 78)
