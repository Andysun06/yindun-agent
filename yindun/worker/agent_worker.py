# -*- coding: utf-8 -*-
"""
隐盾 V3.0 — 🧠 原生 Tool Calling Agent
彻底抛弃正则解析与结构化字段提取，全面迁移到 LangChain 原生工具调用协议。

核心架构：
1. llm 已经在 main_window.py 中通过 bind_tools(tools_list) 绑定了所有可用工具
2. ReAct 循环：llm.invoke(messages) -> 检查 tool_calls -> 执行工具 -> 追加 ToolMessage -> 循环
3. 路径解析：在工具执行前独立完成，从 tool_call args 中提取 target_directory
4. 权限熔断：跨目录操作触发 need_confirm 信号等待人工审批
5. 隐私脱敏：PrivacyEngine 在输入/输出层面统一处理
"""
import os
import re
import threading
import concurrent.futures
from PySide6.QtCore import QObject, Signal
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from yindun.core.privacy_engine import PrivacyEngine
from yindun.core.memory_manager import SummarizableChatHistory

# ──────────────────────────────────────────────
# Agent 系统提示词：告诉模型它能做什么，以及工具使用规范
# ──────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "你是「隐盾」，一个专注于本地文件操作与代码分析的中文智能助手。\n\n"
    "【你的能力】\n"
    "你可以通过调用工具完成以下任务：\n"
    "1. 列出目录下的文件和子目录（list_local_files）\n"
    "2. 创建新文件并写入内容（create_local_file）\n"
    "3. 读取已有文件的内容（read_local_file）\n"
    "4. 修改已有文件的内容（modify_local_file）\n"
    "5. 删除指定文件（delete_local_file）\n"
    "6. 执行系统命令（run_local_command）\n"
    "7. 递归分析整个项目的结构和关键代码（analyze_project）\n"
    "8. 在文件中搜索特定内容（search_in_files）\n\n"
    "【工具使用规范】\n"
    "1. 当用户请求涉及文件操作、目录查询、项目分析、命令执行等任务时，优先调用工具，不要凭空回答。\n"
    "2. 工具参数中的 target_directory 表示目标路径，可以是绝对路径（如 E:\\qwen、C:\\Users\\test），也可以是中文描述（如桌面、E盘、D盘的文档目录）。\n"
    "3. ★★★ 关键规则：如果用户消息中包含完整的绝对路径（如 E:\\xxx\\yyy），你必须把完整路径原样填入 target_directory，绝对不要留空或填'当前沙箱目录'，否则系统会分析错位置！\n"
    "4. 如果用户提到文件名但没指定目录，target_directory 留空表示使用当前工作目录。\n"
    "5. 支持多步推理：例如「先列出文件，再读取关键文件，最后分析项目结构」，可以连续调用多个工具。\n"
    "6. 工具返回结果后，请根据结果和用户的原始需求给出清晰、专业、易懂的中文总结。\n\n"
    "【回答要求】\n"
    "1. 使用中文回答\n"
    "2. 不要输出工具调用过程的内部细节，只输出最终给用户的结果\n"
    "3. 如果需要调用工具，请直接在 tool_calls 中生成调用，不要在文本中描述\n"
    "4. 如果工具执行失败或返回错误信息，将错误信息友好地翻译给用户并给出建议\n"
)


class Worker(QObject):
    """Agent Worker：基于 LangChain 原生 Tool Calling 协议"""
    finished = Signal(str)
    error = Signal(str)
    status = Signal(str)
    need_confirm = Signal(dict)
    intermediate_result = Signal(str)  # 思考过程中的中间结果，用于渐进输出

    # ──────────────────────────────────────────
    # 取消执行机制
    # ──────────────────────────────────────────
    def __init__(self):
        super().__init__()
        self.user_input = ""
        self.messages_snapshot: list[dict] = []
        self.think_mode = "快速回答"
        self.privacy_shield = True
        self.llm = None
        self.tools_map = {}
        self.sandbox_path = os.path.abspath(".")
        self._approved = threading.Event()
        self._approved_val = None
        self._cancel_requested = threading.Event()
        self.think_depth = 3  # 1~10，深度思考模式专用，默认为3（对应6轮推理）
        self.result_messages: list[dict] = []
        self.tool_call_count = 0

    def approve(self, ok):
        """人工审批回调：ok=True 表示批准，ok=False 表示驳回"""
        self._approved_val = ok
        self._approved.set()

    def cancel(self):
        """取消正在进行的推理：由主线程调用，触发后会在下一轮 ReAct 循环开始时安全退出"""
        self._cancel_requested.set()

    def _invoke_llm_with_cancel_check(self, messages, check_interval=0.5):
        """
        可中断的 LLM 调用：使用线程池包装 llm.invoke()，
        每隔 check_interval 秒检查取消标志，实现即时响应取消请求。
        """
        if self._cancel_requested.is_set():
            raise KeyboardInterrupt("用户取消")

        # 使用线程池执行 llm.invoke()，主线程定期检查取消标志
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.llm.invoke, messages)
            while not future.done():
                if self._cancel_requested.is_set():
                    # 用户点击取消，立即返回
                    self.status.emit("[已取消] 推理已被用户中断")
                    raise KeyboardInterrupt("用户取消")
                # 等待一小段时间再检查（避免频繁轮询）
                threading.Event().wait(check_interval)
            return future.result()

    # ──────────────────────────────────────────
    # 主入口：根据模式执行不同逻辑
    # ──────────────────────────────────────────
    def run(self):
        try:
            memory = SummarizableChatHistory.from_dict_list(
                self.messages_snapshot, max_tokens=5000
            )
            engine = PrivacyEngine()

            # 隐私脱敏：用户输入可能包含敏感信息
            ai_input, box = (
                engine.anonymize(self.user_input)
                if self.privacy_shield
                else (self.user_input, {})
            )

            # 检查是否为音频文件请求（音频文件已作为上下文注入，不需要工具调用）
            _AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")
            _path_match = re.search(r'([A-Za-z]:[\\/][^\s]+)', self.user_input)
            _is_audio_request = False
            if _path_match:
                _resolved_path = os.path.abspath(os.path.normpath(_path_match.group(1)))
                if os.path.exists(_resolved_path):
                    _is_audio_request = any(_resolved_path.lower().endswith(ext) for ext in _AUDIO_EXTS)

            # 简单问答或音频文件请求直接跳过工具调用，最快响应
            if self._is_simple_question(ai_input) or _is_audio_request:
                self.status.emit("[快速回答] 直接回答问题...")
                reply = self._clean(self._direct_answer(ai_input, memory))
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.result_messages = memory.to_dict_list()
                self.finished.emit(reply)
                return

            # ──────────────────────────────────────────
            # ★★★ 关键修复：当用户输入包含完整路径和分析关键词时，
            #     强制调用 analyze_project 工具，不依赖 LLM 的工具调用决策
            #     （LLM 在快速模式下经常偷懒不调用工具而直接编造回答）
            #     注意：音频文件已在前面被拦截，不会到达这里
            # ──────────────────────────────────────────
            forced_tool_result = None
            forced_tool_name = None
            _path_match = re.search(r'([A-Za-z]:[\\/][^\s]+)', self.user_input)
            _has_analyze = any(w in self.user_input for w in ["分析", "项目结构", "代码", "项目"])
            _has_list = any(w in self.user_input for w in ["列出", "查看目录", "有什么文件", "目录下", "列出文件"])
            _has_read = "读取" in self.user_input or "读文件" in self.user_input or "文件内容" in self.user_input

            if _path_match and (_has_analyze or _has_list or _has_read):
                _resolved_path = os.path.abspath(os.path.normpath(_path_match.group(1)))
                if os.path.exists(_resolved_path):
                    # 直接执行工具（绕开 LLM 的工具调用决策，保证路径正确）
                    if "analyze_project" in self.tools_map:
                        _tool_obj = self.tools_map["analyze_project"]
                        self.status.emit(f"[工具执行] 扫描项目目录: {_resolved_path}")
                        try:
                            if _has_analyze or os.path.isdir(_resolved_path):
                                _args = {"target_directory": _resolved_path, "max_depth": self.think_depth}
                                forced_tool_result = str(_tool_obj.invoke(_args))
                                forced_tool_name = "analyze_project"
                            elif _has_list and os.path.isdir(_resolved_path):
                                _list_obj = self.tools_map.get("list_local_files")
                                if _list_obj:
                                    _args = {"target_directory": _resolved_path}
                                    forced_tool_result = str(_list_obj.invoke(_args))
                                    forced_tool_name = "list_local_files"
                        except Exception as _e:
                            forced_tool_result = f"[工具执行出错] {_e}"
                    self.tool_call_count += 1

            # 进入 ReAct 循环（快速模式和深度模式都走这个循环，区别在于最大轮次）
            # 如果已经强制执行了工具，把结果作为上下文注入，让 LLM 做总结
            if "深度" in self.think_mode:
                max_rounds = self.think_depth * 2  # depth 3 → 6轮, depth 1 → 2轮, depth 10 → 20轮
            else:
                max_rounds = 3

            final_reply = self._run_react_loop(
                ai_input, box, memory, engine, max_rounds,
                forced_tool_result=forced_tool_result,
                forced_tool_name=forced_tool_name
            )

            if self.privacy_shield and box:
                final_reply = engine.deanonymize(final_reply, box)

            # 注：ReAct 循环内部已经通过 update_with_full_chain 保存完整工具调用链
            # 这里只需要把 final_reply 的 de-anonymized 版本 emit 给用户
            self.finished.emit(final_reply)

        except KeyboardInterrupt:
            # 用户主动取消，正常结束（不是错误）
            self.status.emit("[已取消] 推理已被用户中断")
            self.finished.emit("推理已取消。")
        except Exception as e:
            self.result_messages = self.messages_snapshot
            self.error.emit(f"{type(e).__name__}: {e}")

    # ──────────────────────────────────────────
    # 简单问答检测
    # ──────────────────────────────────────────
    def _is_simple_question(self, text: str) -> bool:
        """检测用户是否在问一个不涉及工具的简单问题"""
        keywords = ["是什么", "怎么样", "如何", "为什么", "怎么", "能否",
                    "是否", "吗", "呢", "?", "？", "解释", "说明", "介绍",
                    "hi", "hello", "你好", "嗨"]
        tool_keywords = ["创建", "读取", "修改", "删除", "列出", "查看",
                         "运行", "执行", "分析", "项目", "文件", "目录",
                         "写", "新建", "保存", "命令", "脚本", "启动", "搜索"]
        return (any(w in text for w in keywords)
                and not any(w in text for w in tool_keywords))

    # ──────────────────────────────────────────
    # 核心：ReAct 循环 — 推理 -> 工具调用 -> 观察 -> 总结
    # ──────────────────────────────────────────
    def _run_react_loop(self, ai_input, box, memory, engine, max_rounds: int,
                         forced_tool_result=None, forced_tool_name=None) -> str:
        """
        ReAct 循环（LangChain 原生 Tool Calling）
        每轮：
          1. llm.invoke(messages) 让模型决定回答或调用工具
          2. 如果 tool_calls 非空，依次执行每个工具
          3. 将 ToolMessage 追加到消息列表
          4. 循环直到模型停止调用工具，或达到最大轮次

        参数：
          forced_tool_result/forced_tool_name: 在进入循环前已强制执行的工具结果
                                          （用于快速模式下 LLM 不肯调用工具的问题
        """
        # 快速模式：禁止写操作工具
        _WRITE_TOOLS = {"create_local_file", "modify_local_file", "delete_local_file", "run_local_command"}
        is_quick_mode = "深度" not in self.think_mode
        # 最低思考轮数：确保深度思考不会过早退出（未达此轮数时强制继续分析）
        min_rounds = max(2, int(self.think_depth * 0.8)) if "深度" in self.think_mode else 0

        # 初始消息列表：系统提示 + 历史上下文 + 当前用户输入
        messages = [SystemMessage(content=_SYSTEM_PROMPT)]
        context_msgs = memory.get_context_messages(self.llm)
        messages.extend(context_msgs)
        human_msg = HumanMessage(content=ai_input)
        messages.append(human_msg)

        # ──────────────────────────────────────────
        # ★★★ 如果有强制工具结果，注入到消息列表
        #     这是修复：快速模式下 LLM 不肯调用工具时，在这里直接注入真实结果
        # ──────────────────────────────────────────
        has_executed_tool = False  # 追踪是否执行过工具，用于决定是否强制总结
        if forced_tool_result and forced_tool_name:
            # 注入模拟的 AI 消息 + 工具结果消息（作为上下文
            messages.append(AIMessage(content="正在为您分析项目..."))
            messages.append(ToolMessage(
                content=f"[{forced_tool_name} 执行结果]\n{forced_tool_result}",
                tool_call_id="forced_tool_001"
            ))
            last_tool_result = forced_tool_result[:2000]
            has_executed_tool = True
            self.status.emit(f"[工具执行完成] 已扫描目录，准备总结...")
            # 渐进输出：把工具扫描结果先发给用户看
            self.intermediate_result.emit("正在为您整理扫描到的项目结构和关键文件...")
        else:
            last_tool_result = ""

        # 记录本轮产生的所有中间消息（排除初始系统/上下文），用于后续持久化
        round_start_idx = len(messages) - 1  # 从 HumanMessage 开始记录
        round_count = 0
        consecutive_failures = 0

        while round_count < max_rounds:
            # 取消检测
            if self._cancel_requested.is_set():
                self.status.emit("[已取消] 推理已被用户中断")
                return "推理已取消。"

            round_count += 1
            self.status.emit(f"[思考 {round_count}/{max_rounds}] 模型判断中...")

            # 让模型决定：回答或调用工具
            ai_msg = self._invoke_llm_with_cancel_check(messages)
            messages.append(ai_msg)

            # 检查模型是否请求工具调用
            tool_calls = getattr(ai_msg, "tool_calls", None)
            if not tool_calls:
                # 没有工具调用，模型给出了文本回答
                content = getattr(ai_msg, "content", "")

                # 渐进输出：把当前轮次的回答显示给用户
                if content and isinstance(content, str) and content.strip():
                    self.intermediate_result.emit(self._clean(content))

                if has_executed_tool and round_count < min_rounds:
                    # 未达到最低思考轮数，强制继续深入分析
                    self.status.emit(f"[深化 {round_count}/{min_rounds}] 未达最低思考轮数，强制继续分析...")
                    deepen_prompt = (
                        "\n\n请基于以上工具执行结果，继续深入分析："
                        "1. 重点解读关键文件的具体实现逻辑\n"
                        "2. 总结项目的核心功能、技术亮点和潜在风险\n"
                        "3. 如果有遗漏的重要文件，请调用工具继续读取分析"
                    )
                    messages.append(HumanMessage(content=deepen_prompt))
                    has_executed_tool = False  # 重置标记，允许下一轮继续强制
                    continue  # 不返回，继续下一轮推理

                if has_executed_tool:
                    # 达到最低轮数，追加总结轮次
                    self.status.emit("[总结] 工具执行完毕，正在生成最终总结...")
                    summary_prompt = "\n\n请基于以上工具执行结果，给用户提供一个详细的总结报告。"
                    messages.append(HumanMessage(content=summary_prompt))
                    final_msg = self._invoke_llm_with_cancel_check(messages)
                    content = getattr(final_msg, "content", "")
                    # 渐进输出：把总结也先发给用户
                    if content and isinstance(content, str) and content.strip():
                        self.intermediate_result.emit(self._clean(content))

                if content and isinstance(content, str) and content.strip():
                    final_reply = self._clean(content)
                    # 保存完整工具调用链（从本轮的 HumanMessage 之后到结束）
                    chain = messages[round_start_idx:]
                    memory.update_with_full_chain(chain)
                    self.result_messages = memory.to_dict_list()
                    return final_reply
                # 模型返回空内容，尝试直接回答
                self.status.emit("[模型] 未给出明确回答，重新生成...")
                reply_msg = self._direct_answer(ai_input, memory)
                final_reply = self._clean(reply_msg)
                chain = messages[round_start_idx:]
                memory.update_with_full_chain(chain)
                self.result_messages = memory.to_dict_list()
                return final_reply

            # 执行每个工具调用
            self.status.emit(f"[思考 {round_count}/{max_rounds}] 准备调用 {len(tool_calls)} 个工具...")
            all_success = True
            round_had_failure = False

            for tc in tool_calls:
                # 取消检测
                if self._cancel_requested.is_set():
                    self.status.emit("[已取消] 推理已被用户中断")
                    return "推理已取消。"

                tool_name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                tool_args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                tool_call_id = tc.get("id", "") if isinstance(tc, dict) else getattr(tc, "id", "")

                # 快速模式：拦截写操作工具
                if is_quick_mode and tool_name in _WRITE_TOOLS:
                    display = self._map_tool_display_name(tool_name)
                    tool_result = (
                        f"⚠️ [快速模式限制] 当前为快速模式，不支持写操作（{display}）。"
                        f"请切换到『深度思考』模式后重试。"
                    )
                    tool_msg = ToolMessage(content=tool_result, tool_call_id=tool_call_id)
                    messages.append(tool_msg)
                    round_had_failure = True
                    all_success = False
                    continue

                # 还原隐私脱敏的参数
                if self.privacy_shield and box:
                    tool_args = {
                        k: (engine.deanonymize(v, box) if isinstance(v, str) else v)
                        for k, v in tool_args.items()
                    }

                # 1. 路径解析：从工具参数中提取并净化路径
                target_path = self._resolve_target_path(tool_args)

                # 2. 敏感操作检测与人工审批
                tool_display_name = self._map_tool_display_name(tool_name)
                is_sensitive = self._is_sens(target_path)

                if is_sensitive:
                    self.status.emit(
                        f"[思考 {round_count}/{max_rounds}] 🚨 检测到跨目录操作 "
                        f"{tool_display_name}，等待人工审批..."
                    )
                    self.need_confirm.emit({
                        "name": tool_display_name,
                        "args": tool_args,
                        "path": target_path
                    })
                    self._approved.clear()
                    self._approved.wait()
                    if not self._approved_val:
                        # 用户驳回
                        tool_result = f"已驳回：敏感操作被人工拦截（尝试在 {target_path} 执行 {tool_display_name}）"
                        tool_msg = ToolMessage(content=tool_result, tool_call_id=tool_call_id)
                        messages.append(tool_msg)
                        all_success = False
                        continue

                # 3. 执行工具
                self.status.emit(
                    f"[思考 {round_count}/{max_rounds}] 执行工具: {tool_display_name}（{target_path}）"
                )
                try:
                    self.tool_call_count += 1

                    tool_result = self._execute_tool(tool_name, tool_args)
                    has_executed_tool = True  # 标记已执行过工具
                    last_tool_result = str(tool_result)[:2000]

                    # 脱敏工具返回内容
                    if self.privacy_shield and box:
                        if isinstance(tool_result, str):
                            tool_result = engine.anonymize(tool_result)[0]

                except Exception as e:
                    tool_result = f"[工具执行错误] {type(e).__name__}: {e}"
                    round_had_failure = True
                    all_success = False

                # 4. 将工具执行结果追加到消息列表
                tool_msg = ToolMessage(content=str(tool_result), tool_call_id=tool_call_id)
                messages.append(tool_msg)

            # 连续失败检测
            if round_had_failure:
                consecutive_failures += 1
                if consecutive_failures >= 2:
                    self.status.emit("[放弃] 连续 2 轮工具调用失败，已放弃")
                    final_reply = (
                        f"抱歉，我尝试了 {round_count} 轮推理，但工具调用连续失败。"
                        f"建议：\n"
                        f"1. 检查路径是否正确\n"
                        f"2. 如果涉及写操作，请切换到深度思考模式\n"
                        f"3. 用更具体的表述告诉我你想做什么"
                    )
                    chain = messages[round_start_idx:]
                    memory.update_with_full_chain(chain)
                    self.result_messages = memory.to_dict_list()
                    return final_reply
            else:
                consecutive_failures = 0
                # 渐进输出：工具执行完成后，向用户展示当前思考状态
                if has_executed_tool:
                    self.intermediate_result.emit(
                        f"[第 {round_count} 轮] 已完成工具检索，正在结合工具结果深入分析中..."
                    )

            # 一轮工具执行完毕，继续循环让模型基于结果生成下一轮决策

        # 达到最大轮次，让模型总结
        self.status.emit("[总结] 已达最大推理轮次，正在生成最终回答...")
        final_msg = self._invoke_llm_with_cancel_check(messages)
        messages.append(final_msg)
        content = getattr(final_msg, "content", "")
        final_reply = self._clean(content) if content and isinstance(content, str) else last_tool_result
        # 保存完整消息链
        chain = messages[round_start_idx:]
        memory.update_with_full_chain(chain)
        self.result_messages = memory.to_dict_list()
        return final_reply

    # ──────────────────────────────────────────
    # 工具执行层：统一处理所有工具的调用
    # ──────────────────────────────────────────
    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """根据工具名调用对应的工具对象，返回工具执行结果"""

        # ──────────────────────────────────────────
        # ★ 关键修复：始终以用户原始输入中的路径为准，
        #   模型可能传错路径（如多一层目录），不能信任模型的 target_directory
        # ──────────────────────────────────────────
        user_path = self._det(self.user_input)
        effective_args = dict(args)
        
        # 如果用户输入中包含明确路径，强制覆盖模型传的任何路径
        if user_path:
            effective_args["target_directory"] = user_path
        else:
            # 用户没给路径，才使用模型传的或回退到默认
            if not effective_args.get("target_directory") or effective_args.get("target_directory") in ("", "当前沙箱目录"):
                effective_args["target_directory"] = self.sandbox_path

        # 对 analyze_project：根据 thinking_depth 动态调整 max_depth 和采样行数
        if tool_name == "analyze_project":
            d = self.think_depth
            effective_args["max_depth"] = d  # 1~10，对应1~10层递归
            # 注意：代码采样行数由 analyze_project 内部 _summarize_code_files 控制
            # 该函数硬编码"前 30 行"，通过调整 max_depth 间接影响采样范围

        # 优先使用 tools_map 中已有的工具对象
        if tool_name in self.tools_map:
            tool_obj = self.tools_map[tool_name]
            # LangChain Tool 对象支持直接 invoke(args_dict)
            if isinstance(tool_obj, BaseTool):
                return str(tool_obj.invoke(effective_args))
            return str(tool_obj(effective_args) if callable(tool_obj) else str(tool_obj))

        # 通过 tools_map 中的键做模糊匹配
        for key, tool_obj in self.tools_map.items():
            if tool_name.lower() == key.lower() or tool_name.lower() in key.lower():
                if isinstance(tool_obj, BaseTool):
                    return str(tool_obj.invoke(effective_args))
                return str(tool_obj(effective_args) if callable(tool_obj) else str(tool_obj))

        # 工具不存在，返回错误信息
        return f"[错误] 工具 '{tool_name}' 未找到。可用工具: {list(self.tools_map.keys())}"

    def _map_tool_display_name(self, tool_name: str) -> str:
        """将工具名映射为更易懂的中文名（用于状态显示和审批提示）"""
        name_map = {
            "list_local_files": "列出目录文件",
            "create_local_file": "创建文件",
            "read_local_file": "读取文件",
            "modify_local_file": "修改文件",
            "delete_local_file": "删除文件",
            "run_local_command": "执行命令",
            "analyze_project": "分析项目",
            "search_in_files": "文件搜索",
        }
        return name_map.get(tool_name, tool_name)

    # ──────────────────────────────────────────
    # 路径解析：从工具参数中提取并净化目标路径
    # ──────────────────────────────────────────
    def _resolve_target_path(self, args: dict) -> str:
        """
        从工具参数中解析目标路径：
        1. ★★★ 优先从用户原始输入中提取完整绝对路径（最可靠）
        2. 其次使用 args 中的 target_directory（模型可能传错，仅作参考）
        3. 如果是中文描述（如"桌面"、"E盘"），转换为实际路径
        4. 默认返回当前沙箱目录
        """
        # ★★★ 优先从用户输入中提取完整绝对路径（模型可能传错路径）
        user_input_path = self._det(self.user_input)
        if user_input_path:
            return user_input_path

        # 从工具参数中提取
        target_dir = ""
        if isinstance(args, dict):
            target_dir = args.get("target_directory", "") or args.get("directory", "") or args.get("path", "") or ""

        if not target_dir or target_dir == "当前沙箱目录":
            return self.sandbox_path

        # 用 _dyn 解析中文描述
        resolved = self._dyn(target_dir)
        if resolved and resolved != self.sandbox_path:
            return resolved

        return self.sandbox_path

    @staticmethod
    def _det(text):
        """从用户输入文本中解析目标路径（支持多种中文描述格式）。"""
        if not text:
            return None

        # ★★★ 优先匹配完整的绝对路径（如 E:\xxx\yyy 或 E:/xxx/yyy）
        # 使用 [^\s]+ 匹配所有非空白字符，这是最可靠的方式
        path_match = re.search(r"([A-Za-z]:[\\/][^\s]+)", text)
        if path_match:
            return os.path.abspath(os.path.normpath(path_match.group(1)))

        # 匹配 "在桌面"、"到桌面"、"桌面的"、"桌面目录"、"桌面文件夹" 等
        if re.search(r"(?:在|到|从)\s*桌面|桌面(?:上|中|下|的|目录|文件夹)", text):
            return os.path.join(os.path.expanduser("~"), "Desktop")

        # 匹配 "E盘"、"D盘的文档"、"C:\Users" 等
        drive_match = re.search(r"([A-Za-z])\s*盘", text)
        if drive_match:
            d = drive_match.group(1).upper()
            after_drive = text[drive_match.end():]
            after_drive = re.sub(r"^(?:的|这个|那个|该|此)\s*", "", after_drive)
            cn_suffix = r"(?:文件夹内|文件夹中|文件夹里|文件夹下|文件夹|目录下|目录里|目录中|目录)"
            m_en = re.search(r"([a-zA-Z0-9_\-]{1,30})" + cn_suffix, after_drive)
            if m_en:
                return os.path.abspath(f"{d}:\\{m_en.group(1)}")
            m_cn = re.search(r"([\u4e00-\u9fa5]{1,8})" + cn_suffix, after_drive)
            if m_cn:
                name = m_cn.group(1)
                if name not in ("项目", "工作", "这个", "那个"):
                    return os.path.abspath(f"{d}:\\{name}")
            return os.path.abspath(f"{d}:\\")

        return None

    @staticmethod
    def _dyn(desc):
        """解析中文路径描述为实际路径"""
        if not desc or not isinstance(desc, str):
            return os.path.abspath(".")
        if "项目根目录" in desc or "当前沙箱" in desc or "当前目录" in desc:
            return os.path.abspath(".")
        if "桌面" in desc:
            return os.path.join(os.path.expanduser("~"), "Desktop")
        p = desc.strip()
        if "盘" in p:
            parts = p.split("盘")
            p = parts[0].upper() + ":\\" + (parts[1] if len(parts) > 1 else "")
        for s in ["内", "中", "目录", "文件夹", "根目录", "下"]:
            if p.endswith(s):
                p = p[:-len(s)]
        try:
            p_normalized = p.strip().replace("/", "\\")
            # 如果看起来像合法路径（包含盘符或相对路径），返回绝对路径
            if re.match(r"^[A-Za-z]:", p_normalized) or os.path.exists(p_normalized):
                return os.path.abspath(os.path.normpath(p_normalized))
            # 否则返回当前目录
            return os.path.abspath(".")
        except Exception:
            return os.path.abspath(".")

    @staticmethod
    def _is_sens(path):
        """检测路径是否为跨目录操作（敏感操作）"""
        if not path or not isinstance(path, str):
            return True
        allowed = [
            os.path.abspath(".").lower(),
            os.path.join(os.path.expanduser("~"), "Desktop").lower()
        ]
        return path.lower() not in allowed

    # ──────────────────────────────────────────
    # 直接回答：不调用工具的纯问答模式
    # ──────────────────────────────────────────
    def _direct_answer(self, ai_input: str, memory) -> str:
        """让模型直接回答用户问题（不调用工具）"""
        messages = [SystemMessage(
            content="你是一个专业的中文智能助手，代号「隐盾」。请用中文直接、完整地回答用户的问题。\n\n"
                    "回答要求：\n"
                    "1. 回答必须完整，不要中途中断或省略关键步骤\n"
                    "2. 如果问题要求证明、解释、推理，请给出完整的推理过程\n"
                    "3. 如果是代码问题，给出完整的代码示例和解释\n"
                    "4. 如果是数学/逻辑问题，给出完整的推导过程\n"
                    "5. 使用清晰的标题、列表和分段，方便阅读\n"
                    "6. 使用 Markdown 格式增强可读性（代码块、加粗、列表）\n"
        )]
        messages.extend(memory.get_context_messages(self.llm))
        messages.append(HumanMessage(content=ai_input))
        resp = self._invoke_llm_with_cancel_check(messages)
        content = getattr(resp, "content", "")
        return content if isinstance(content, str) else str(content)

    @staticmethod
    def _clean(text):
        """清理模型输出中的工具调用残留标签"""
        if not text:
            return ""
        if not isinstance(text, str):
            return str(text)
        t = re.sub(r"(?i)(?:brtc|portun|tool_call)?\s*\{.*\}\s*</tool_call>?", "", text)
        return re.sub(r"(?i)</?tool_call>", "", t).strip()
