# -*- coding: utf-8 -*-
"""
隐盾 V2.5.0 — 🧠 异步大脑驱动舱
专门处理本地大模型连接、脱敏网关推演与物理工具调度的后台子线程，保护主界面永不卡死
⭐ 升级 V2.5.0：思考模式实现完整的 ReAct 循环（推理 → 行动 → 观察 → 调整），
  最大化发挥 7B 模型工具调用能力；Quick 模式保持单次快速 invoke 向后兼容。
"""
import os
import re
import threading
from PySide6.QtCore import QObject, Signal
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from yindun.core.privacy_engine import PrivacyEngine
from yindun.core.memory_manager import SummarizableChatHistory

# ──────────────────────────────────────────────
# 意图识别系统提示词：让模型像填表一样输出结构化结果
# 增强版：支持完整通用 agent 能力（文件读写、代码修改、命令执行、问答）
# ──────────────────────────────────────────────
_INTENT_PROMPT = (
    "你是一个专业的中文通用智能助手，代号「隐盾」。你的任务是分析用户请求，"
    "提取意图和参数，并严格按照以下格式输出。\n"
    "\n"
    "【输出格式】每行一个字段，KEY 用英文大写，VALUE 用中文或路径：\n"
    "INTENT: [create_file | read_file | modify_file | delete_file | list_files | analyze_project | run_command | answer]\n"
    "FILENAME: [文件名（含后缀，如 笔记.txt），不需要时留空]\n"
    "CONTENT: [要写入/替换的文本内容，不需要时留空]\n"
    "DIRECTORY: [目标目录，如 E:/qwen 或 D盘的文档文件夹，默认填 当前沙箱目录]\n"
    "COMMAND: [要执行的命令，不需要时留空]\n"
    "FOCUS: [分析重点（仅analyze_project用），如'架构'、'依赖'，不需要时留空]\n"
    "ANSWER: [如果 INTENT=answer，直接回答用户；其他情况留空]\n"
    "REASONING: [你的推理过程，必须简要说明为什么判断为此意图]\n"
    "\n"
    "【可用工具与意图映射】\n"
    "- list_files：列出目录下的文件和文件夹\n"
    "- read_file：读取单个文件内容（用于分析、总结、理解单个代码文件）\n"
    "- analyze_project：递归分析整个项目结构（识别项目类型、关键文件、文件树、代码采样）\n"
    "- create_file：创建新文件（覆盖式写入）\n"
    "- modify_file：修改已有文件（替换或追加内容）\n"
    "- delete_file：删除文件\n"
    "- run_command：执行系统命令（如 python 脚本、pip 安装等）\n"
    "- answer：直接回答问题（不需要调用工具）\n"
    "\n"
    "【意图判断规则】\n"
    "- create_file：用户要求创建/保存/写/新建/生成文件（如：创建文件、写一个脚本、保存到...）\n"
    "- read_file：用户明确指定了具体文件名，或要求分析文件内容、提取数据、列成表格、查看具体信息、进一步分析、深入分析（如：读取文件、分析资源、列成表格、有哪些资源、再进一步分析、深入分析代码）\n"
    "- analyze_project：用户要求分析/理解/总结整个项目、代码库、项目结构、技术栈（如：分析这个项目、总结项目代码、这个项目是做什么的、技术栈是什么）\n"
    "- modify_file：用户要求修改/编辑/更新/修复已有文件（如：修改代码、修复bug、更新文件内容...）\n"
    "- delete_file：用户要求删除/移除/清理文件（如：删除文件、清理掉...）\n"
    "- list_files：用户要求列出/查看目录内容（如：有什么文件、目录下有哪些、列出...）\n"
    "- run_command：用户要求运行/执行命令、脚本、程序（如：运行脚本、执行测试、安装依赖...）\n"
    "- answer：其他所有情况（闲聊、问答、代码生成但不保存、解释说明等）\n"
    "\n"
    "【DIRECTORY 路径解析规则】\n"
    "1. 如果用户明确给出完整路径（如 E:/qwen、C:\\Users\\test），直接使用\n"
    "2. 如果用户说\"当前目录\"、\"当前沙箱\"、\"项目根目录\"、\"这里\"，填 当前沙箱目录\n"
    "3. 如果用户说\"桌面\"，填 桌面\n"
    "4. 如果用户说\"E盘\"、\"D盘\"等，填 盘符根目录（如 E:\\）\n"
    "5. 如果用户说\"E盘的qwen文件夹\"、\"D盘的文档目录\"，提取目录名（如 E:\\qwen、D:\\文档）\n"
    "6. 如果用户没有指定目录，默认填 当前沙箱目录\n"
    "\n"
    "【FILENAME 提取规则】\n"
    "1. 必须包含后缀名（如 .txt、.py、.md）\n"
    "2. 如果用户没说文件名，留空\n"
    "3. 如果用户说\"一个文件\"、\"文件\"，但没说具体名字，留空\n"
    "\n"
    "【CONTENT 提取规则】\n"
    "1. create_file：提取用户要求写入的新文件内容\n"
    "2. modify_file：提取用户要求替换或追加的新内容\n"
    "3. 其他意图：留空\n"
    "\n"
    "【COMMAND 提取规则】\n"
    "1. 只有 INTENT=run_command 时才填写\n"
    "2. 提取用户要求执行的具体命令\n"
    "3. 如果用户说\"运行test.py\"，COMMAND填\"python test.py\"\n"
    "\n"
    "【强制要求】\n"
    "- 严格按7行格式输出，不要输出任何其他文字\n"
    "- REASONING 字段必须填写，简要说明推理过程\n"
    "- 不要用 Markdown 格式，不要用代码块包裹\n"
    "- 不要输出解释性文字，直接输出7行字段\n"
    "- DIRECTORY 必须是具体路径或 当前沙箱目录，不能留空\n"
    "\n"
    "【示例学习】\n"
    "示例1：用户说「在E盘的qwen文件夹创建一个1.txt文件」\n"
    "INTENT: create_file\n"
    "FILENAME: 1.txt\n"
    "CONTENT: \n"
    "DIRECTORY: E:\\qwen\n"
    "COMMAND: \n"
    "ANSWER: \n"
    "REASONING: 用户提到\"创建\"和\"文件\"，意图为create_file；目录是E盘的qwen文件夹\n"
    "\n"
    "示例2：用户说「帮我读取main.py文件的内容」\n"
    "INTENT: read_file\n"
    "FILENAME: main.py\n"
    "CONTENT: \n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: \n"
    "ANSWER: \n"
    "REASONING: 用户要求读取文件内容，意图为read_file\n"
    "\n"
    "示例3：用户说「帮我修改config.py，把timeout改成30」\n"
    "INTENT: modify_file\n"
    "FILENAME: config.py\n"
    "CONTENT: timeout = 30\n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: \n"
    "ANSWER: \n"
    "REASONING: 用户要求修改已有文件，意图为modify_file\n"
    "\n"
    "示例4：用户说「运行test.py脚本」\n"
    "INTENT: run_command\n"
    "FILENAME: \n"
    "CONTENT: \n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: python test.py\n"
    "ANSWER: \n"
    "REASONING: 用户要求执行脚本，意图为run_command\n"
    "\n"
    "示例5：用户说「帮我分析一下这个目录下的项目结构」\n"
    "INTENT: list_files\n"
    "FILENAME: \n"
    "CONTENT: \n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: \n"
    "ANSWER: \n"
    "REASONING: 用户要求查看目录内容，意图为list_files\n"
    "\n"
    "示例6：用户说「Python中如何读取文件」\n"
    "INTENT: answer\n"
    "FILENAME: \n"
    "CONTENT: \n"
    "DIRECTORY: \n"
    "COMMAND: \n"
    "ANSWER: 在Python中读取文件可以使用open()函数，例如：\nwith open('file.txt', 'r', encoding='utf-8') as f:\n    content = f.read()\nprint(content)\n"
    "REASONING: 用户询问编程知识，不需要调用工具，意图为answer\n"
    "\n"
    "示例7：用户说「帮我分析main.py文件的代码逻辑」\n"
    "INTENT: read_file\n"
    "FILENAME: main.py\n"
    "CONTENT: \n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: \n"
    "ANSWER: \n"
    "REASONING: 用户要求分析文件内容，需要先读取文件，意图为read_file\n"
    "\n"
    "示例8：用户说「帮我分析一下这个项目是做什么的」\n"
    "INTENT: analyze_project\n"
    "FILENAME: \n"
    "CONTENT: \n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: \n"
    "FOCUS: \n"
    "ANSWER: \n"
    "REASONING: 用户要求分析整个项目（不是单个文件），意图为analyze_project\n"
    "\n"
    "示例9：用户说「总结下这个项目的技术栈」\n"
    "INTENT: analyze_project\n"
    "FILENAME: \n"
    "CONTENT: \n"
    "DIRECTORY: 当前沙箱目录\n"
    "COMMAND: \n"
    "FOCUS: 技术栈\n"
    "ANSWER: \n"
    "REASONING: 用户要求总结项目技术栈，意图为analyze_project，重点是技术栈\n"
)

# 结构化字段解析：匹配 KEY: VALUE 格式（允许中文冒号和英文冒号）
_INTENT_FIELD_RE = re.compile(r"^(INTENT|FILENAME|CONTENT|DIRECTORY|COMMAND|FOCUS|ANSWER|REASONING)\s*[:：]\s*(.*?)\s*$", re.IGNORECASE)

# 目录路径的模型辅助正则（用于解析 DIRECTORY 字段，支持各种用户描述）
_DIR_PATH_RE = re.compile(r"([A-Za-z]):[\\/]?[\w\u4e00-\u9fa5.\-/\\]*")


class Worker(QObject):
    finished = Signal(str)
    error = Signal(str)
    status = Signal(str)
    need_confirm = Signal(dict)

    def __init__(self):
        super().__init__()
        self.user_input = ""
        self.messages_snapshot: list[dict] = []
        self.think_mode = "快速"
        self.privacy_shield = True
        self.llm = None
        self.tools_map = {}
        self.sandbox_path = os.path.abspath(".")
        self._approved = threading.Event()
        self._approved_val = None
        self.result_messages: list[dict] = []
        self.tool_call_count = 0

    def approve(self, ok):
        self._approved_val = ok
        self._approved.set()

    def run(self):
        try:
            memory = SummarizableChatHistory.from_dict_list(
                self.messages_snapshot, max_tokens=5000
            )
            engine = PrivacyEngine()
            ai_input, box = (
                engine.anonymize(self.user_input)
                if self.privacy_shield
                else (self.user_input, {})
            )

            if "深度" in self.think_mode:
                self._run_react_loop(ai_input, box, memory, engine)
            else:
                self._run_quick_mode(ai_input, box, memory, engine)

            self.result_messages = memory.to_dict_list()

        except Exception as e:
            self.result_messages = self.messages_snapshot
            self.error.emit(f"{type(e).__name__}: {e}")

    # ──────────────────────────────────────────
    # 意图识别核心：让模型先做结构化分类与参数提取
    # ──────────────────────────────────────────
    def _parse_structured_output(self, text: str) -> dict:
        """
        解析模型输出的结构化字段（INTENT / FILENAME / CONTENT / DIRECTORY / ANSWER）。
        支持：多行 KEY: VALUE 格式、中文/英文冒号、字段顺序不固定。
        """
        if not text:
            return {}

        result = {}
        # 清理引号（模型可能输出 "E:/qwen" 这种带引号的内容）
        clean = text.strip().strip("```")

        for line in clean.split("\n"):
            line = line.strip()
            if not line:
                continue
            # 逐行尝试匹配 KEY: VALUE 格式
            m = _INTENT_FIELD_RE.match(line)
            if m:
                key = m.group(1).upper()
                val = m.group(2).strip()
                # 去除两端的引号（如果有）
                if (val.startswith('"') and val.endswith('"')) or \
                   (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                # 去除两端的中文引号
                if val.startswith("「") and val.endswith("」"):
                    val = val[1:-1]
                result[key] = val

        # 清理空值（只保留有内容的字段）
        return {k: v for k, v in result.items() if v and v.strip() not in
                ("无", "空", "none", "None", "N/A", "n/a", "不适用", "否", "-")}

    def _recognize_intent(self, ai_input: str, memory) -> dict:
        """
        调用模型识别用户意图，返回结构化字典。
        支持最多2次重试：如果第一次输出格式不对，就引导模型用标准格式再输出。
        """
        messages = [SystemMessage(content=_INTENT_PROMPT)]
        messages.extend(memory.get_context_messages(self.llm))
        messages.append(HumanMessage(content=f"用户请求：{ai_input}\n\n请按上面的格式输出。"))

        # 最多尝试2次（第一次主调用，第二次格式修正）
        best_guess = {"INTENT": "answer", "ANSWER": ""}

        for attempt in range(2):
            self.status.emit(f"[思考] 识别用户意图（第{attempt+1}次）...")
            resp = self.llm.invoke(messages)
            raw = resp.content if hasattr(resp, "content") else str(resp)
            parsed = self._parse_structured_output(raw)

            # 校验 INTENT 是否合法
            valid_intents = {"create_file", "read_file", "modify_file", "delete_file", "list_files", "analyze_project", "run_command", "answer"}
            if parsed.get("INTENT") in valid_intents:
                return parsed

            # 第一次格式不对，给一个明确的格式示例让模型重试
            if attempt == 0:
                # 提取可能的自由文本作为 ANSWER 兜底
                best_guess = {"INTENT": "answer", "ANSWER": raw.strip()}
                messages.append(AIMessage(content=raw))
                messages.append(HumanMessage(
                    content="格式不规范，请严格按以下7行格式重写，不要加其他文字：\n\n"
                            "INTENT: answer\n"
                            "FILENAME: \n"
                            "CONTENT: \n"
                            "DIRECTORY: 当前沙箱目录\n"
                            "COMMAND: \n"
                            "ANSWER: 在这里直接回答用户的问题\n"
                            "REASONING: 简要说明推理过程"
                ))
            # 第二次：尝试从原始文本中启发式推断意图
            else:
                return self._heuristic_infer_intent(ai_input, raw)

        return best_guess

    def _heuristic_infer_intent(self, ai_input: str, raw_text: str) -> dict:
        """
        当模型输出格式完全不规范时，用关键词启发式推断意图，避免整个任务失败。
        """
        if any(w in ai_input for w in ["创建", "新建", "保存", "写文件", "生成文件", "写入"]):
            return {"INTENT": "create_file"}
        if any(w in ai_input for w in ["读取", "查看文件", "读文件", "读一下", "看看文件", "打开文件", 
                                        "分析资源", "资源列表", "列成表格", "哪些资源", "有哪些", 
                                        "详细信息", "具体信息", "数据结构", "内容是什么",
                                        "再进一步分析", "深入分析", "详细分析", "代码逻辑", "代码实现", "核心逻辑"]):
            return {"INTENT": "read_file"}
        if any(w in ai_input for w in ["分析项目", "总结项目", "项目是", "技术栈", "项目结构", "代码库", "代码结构"]):
            return {"INTENT": "analyze_project"}
        if any(w in ai_input for w in ["修改", "编辑", "更新", "修复", "改一下"]):
            return {"INTENT": "modify_file"}
        if any(w in ai_input for w in ["删除", "移除", "清理", "删掉"]):
            return {"INTENT": "delete_file"}
        if any(w in ai_input for w in ["列出", "目录", "有什么", "哪些文件", "里面有"]):
            return {"INTENT": "list_files"}
        if any(w in ai_input for w in ["运行", "执行", "启动", "跑一下"]):
            return {"INTENT": "run_command"}
        return {"INTENT": "answer", "ANSWER": raw_text.strip()[:500]}

    # ──────────────────────────────────────────
    # ReAct 循环（思考模式）—— 新版：先识别意图，再执行
    # ──────────────────────────────────────────
    def _run_react_loop(self, ai_input, box, memory, engine):
        """
        思考模式核心逻辑（已针对 7B 模型优化）：
        - 第一步：让模型输出结构化意图分类和参数
        - 第二步：代码根据意图可靠地执行操作（调用工具 / 直接回答）
        - 第三步：工具执行后，让模型基于结果给出最终回答
        - 任何异常都会自动降级：格式解析失败 → 启发式推断 → 直接把模型输出当回答
        """
        tool_names = list(self.tools_map.keys())

        # ── 第一步：意图识别与参数提取
        self.status.emit("[思考 1/3] 正在分析用户意图...")
        intent_info = self._recognize_intent(ai_input, memory)
        intent = intent_info.get("INTENT", "answer").strip().lower()

        # 解析工具参数（同时从 DIRECTORY 字段 和 用户原始输入推断路径）
        filename = intent_info.get("FILENAME", "").strip()
        content = intent_info.get("CONTENT", "").strip()
        directory_raw = intent_info.get("DIRECTORY", "").strip()
        command = intent_info.get("COMMAND", "").strip()
        focus = intent_info.get("FOCUS", "").strip()
        answer_text = intent_info.get("ANSWER", "").strip()

        # 路径解析：优先用 DIRECTORY 字段，其次从用户原始输入推断
        rp = self._resolve_path(self.user_input, {"target_directory": directory_raw})

        # ── 第二步：根据意图执行相应操作
        # 情况 1：不需要工具调用，直接回答
        if intent == "answer" or intent not in ("create_file", "read_file", "modify_file", "delete_file", "list_files", "analyze_project", "run_command"):
            self.status.emit("[思考 2/3] 生成完整回答...")
            # 不再依赖意图识别阶段的 ANSWER 字段（易截断），直接用专用问答流程
            reply = self._clean(self._direct_answer(ai_input, memory))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 2：create_file —— 需要 filename
        if intent == "create_file":
            if not filename:
                self.status.emit("[思考] 模型识别为创建文件但未给出文件名，重新询问...")
                reply = self._direct_answer(ai_input, memory)
                reply = self._clean(reply)
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return

            # 确保 filename 不包含路径
            filename_clean = os.path.basename(filename)
            is_sensitive = self._is_sens(rp)
            args_map = {"filename": filename_clean, "content": content}
            tool_name = "create_local_file"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录写操作，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试在 {rp} 创建文件）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            # ── 第三步：基于工具结果润色回答
            self.status.emit("[思考 3/3] 整理执行结果...")
            final = self.llm.invoke(
                f"你是专业的文件操作助手「隐盾」。请根据以下信息用中文给用户一个清晰的回复：\n\n"
                f"用户请求：{ai_input}\n"
                f"目标路径：{rp}\n"
                f"执行的操作：{tool_name}\n"
                f"操作参数：{args_map}\n"
                f"工具执行结果：{tool_result}\n\n"
                f"请用简洁友好的语言总结操作结果，包括：\n"
                f"1. 是否成功\n"
                f"2. 具体做了什么\n"
                f"3. 如果有错误，简要说明原因\n"
                f"不要使用技术术语，用日常语言表达。"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 3：read_file —— 需要 filename
        if intent == "read_file":
            if not filename:
                # 尝试从对话历史中提取之前提到的关键文件名
                history_text = ""
                try:
                    history_messages = memory.get_context_messages(self.llm)
                    for msg in history_messages:
                        if hasattr(msg, 'content'):
                            history_text += str(msg.content) + " "
                except Exception:
                    pass
                
                # 从历史中匹配 .js/.json/.py 等代码文件
                file_pattern = re.compile(r'([a-zA-Z0-9_\-]+\.(?:js|json|py|html|css|xml|yml|yaml|toml|cfg|ini))', re.IGNORECASE)
                matched_files = file_pattern.findall(history_text)
                
                if matched_files:
                    # 取第一个匹配到的文件（通常是最重要的）
                    filename = matched_files[0]
                    self.status.emit(f"[思考] 从对话历史中提取到关键文件：{filename}")
                else:
                    self.status.emit("[思考] 模型识别为读取文件但未给出文件名，且历史中没有找到关键文件，直接回答...")
                    reply = self._direct_answer(ai_input, memory)
                    reply = self._clean(reply)
                    if self.privacy_shield and box:
                        reply = engine.deanonymize(reply, box)
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            filename_clean = os.path.basename(filename)
            is_sensitive = self._is_sens(rp)
            args_map = {"filename": filename_clean}
            tool_name = "read_local_file"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录读取操作，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试读取 {rp}）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            self.status.emit("[思考 3/3] 整理执行结果...")
            # 增强分析能力：让模型根据用户请求进行深度分析
            final = self.llm.invoke(
                f"你是专业的代码分析助手「隐盾」。请根据以下信息对文件内容进行深度分析，并给出专业的回复：\n\n"
                f"用户请求：{ai_input}\n"
                f"目标路径：{rp}\n"
                f"执行的操作：{tool_name}\n"
                f"文件内容：\n{tool_result}\n\n"
                f"分析要求：\n"
                f"1. 如果用户要求列出资源/数据，请提取数据并以表格形式展示\n"
                f"2. 如果用户要求分析代码，请解释代码的功能、逻辑结构、关键变量和函数\n"
                f"3. 如果用户要求深入分析，请从多个维度（数据结构、算法逻辑、业务流程）进行分析\n"
                f"4. 如果文件是游戏配置（如 data.js），请提取游戏资源类型、属性、初始值等\n"
                f"5. 输出时尽量使用清晰的标题、列表和表格，方便阅读\n"
                f"6. 使用中文，语言通俗易懂，避免过于技术化的术语"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 4：modify_file —— 需要 filename 和 content
        if intent == "modify_file":
            if not filename:
                self.status.emit("[思考] 模型识别为修改文件但未给出文件名，直接回答...")
                reply = self._direct_answer(ai_input, memory)
                reply = self._clean(reply)
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return

            filename_clean = os.path.basename(filename)
            is_sensitive = self._is_sens(rp)
            args_map = {"filename": filename_clean, "old_content": "", "new_content": content}
            tool_name = "modify_local_file"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录修改操作，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试修改 {rp}）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            self.status.emit("[思考 3/3] 整理执行结果...")
            final = self.llm.invoke(
                f"你是专业的文件操作助手「隐盾」。请根据以下信息用中文给用户一个清晰的回复：\n\n"
                f"用户请求：{ai_input}\n"
                f"目标路径：{rp}\n"
                f"执行的操作：{tool_name}\n"
                f"操作参数：{args_map}\n"
                f"工具执行结果：{tool_result}\n\n"
                f"请用简洁友好的语言总结操作结果，包括：\n"
                f"1. 是否成功\n"
                f"2. 具体做了什么\n"
                f"3. 如果有错误，简要说明原因\n"
                f"不要使用技术术语，用日常语言表达。"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 5：delete_file —— 需要 filename
        if intent == "delete_file":
            if not filename:
                self.status.emit("[思考] 模型识别为删除文件但未给出文件名，直接回答...")
                reply = self._direct_answer(ai_input, memory)
                reply = self._clean(reply)
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return

            filename_clean = os.path.basename(filename)
            is_sensitive = self._is_sens(rp)
            args_map = {"filename": filename_clean}
            tool_name = "delete_local_file"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录删除操作，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试在 {rp} 删除文件）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            self.status.emit("[思考 3/3] 整理执行结果...")
            final = self.llm.invoke(
                f"你是专业的文件操作助手「隐盾」。请根据以下信息用中文给用户一个清晰的回复：\n\n"
                f"用户请求：{ai_input}\n"
                f"目标路径：{rp}\n"
                f"执行的操作：{tool_name}\n"
                f"操作参数：{args_map}\n"
                f"工具执行结果：{tool_result}\n\n"
                f"请用简洁友好的语言总结操作结果，包括：\n"
                f"1. 是否成功\n"
                f"2. 具体做了什么\n"
                f"3. 如果有错误，简要说明原因\n"
                f"不要使用技术术语，用日常语言表达。"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 4：list_files
        if intent == "list_files":
            is_sensitive = self._is_sens(rp)
            args_map = {"target_directory": directory_raw or "当前沙箱目录"}
            tool_name = "list_local_files"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录查看操作，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试查看 {rp}）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            self.status.emit("[思考 3/3] 整理执行结果...")
            final = self.llm.invoke(
                f"你是专业的文件操作助手「隐盾」。请根据以下信息用中文给用户一个清晰的回复：\n\n"
                f"用户请求：{ai_input}\n"
                f"目标路径：{rp}\n"
                f"执行的操作：{tool_name}\n"
                f"工具执行结果：{tool_result}\n\n"
                f"请用清晰友好的语言列出目录内容，格式要求：\n"
                f"1. 先说明目录路径\n"
                f"2. 用列表形式列出文件和子目录\n"
                f"3. 如果目录为空，说明是空目录\n"
                f"4. 如果有错误，简要说明原因\n"
                f"不要使用技术术语，用日常语言表达。"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 5：analyze_project —— 递归分析整个项目
        if intent == "analyze_project":
            is_sensitive = self._is_sens(rp)
            args_map = {"target_directory": rp, "max_depth": 3, "focus": focus}
            tool_name = "analyze_project"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录分析操作，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试分析 {rp}）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            self.status.emit("[思考 3/3] 整理分析结果...")
            # 项目分析：让模型深度解读工具返回的报告，给出专业总结
            user_focus = f"\n\n用户特别关注的重点：{focus}" if focus else ""
            final = self.llm.invoke(
                f"你是一个资深的软件架构师「隐盾」。请根据以下项目分析报告，"
                f"用中文给用户一个**专业、深入、易懂**的项目解读：\n\n"
                f"用户请求：{ai_input}{user_focus}\n"
                f"项目路径：{rp}\n"
                f"分析报告：\n{tool_result}\n\n"
                f"请按以下结构输出解读：\n"
                f"1. 📌 项目概述：这是一个什么项目？解决什么问题？\n"
                f"2. 🛠️ 技术栈：使用的主要技术、框架、语言\n"
                f"3. 📂 模块划分：主要的目录/模块及其职责\n"
                f"4. 🚀 入口与启动：如何运行这个项目？主要入口文件是什么？\n"
                f"5. 💡 核心特点：值得注意的设计模式、技术亮点\n"
                f"6. ⚠️ 潜在问题（可选）：发现的代码异味、安全隐患等\n\n"
                f"要求：\n"
                f"- 用通俗易懂的语言，避免堆砌术语\n"
                f"- 如果用户关注了重点（如'架构'、'技术栈'），在该部分详细展开\n"
                f"- 如果信息不足，请明确说明，并建议如何进一步获取信息"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 情况 7：run_command —— 需要 command
        if intent == "run_command":
            if not command:
                self.status.emit("[思考] 模型识别为执行命令但未给出命令，直接回答...")
                reply = self._direct_answer(ai_input, memory)
                reply = self._clean(reply)
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return

            is_sensitive = self._is_sens(rp)
            args_map = {"command": command}
            tool_name = "run_local_command"

            if is_sensitive:
                self.status.emit("[思考 2/3] 🚨 检测到跨目录命令执行，等待人工审批...")
                self.need_confirm.emit({"name": tool_name, "args": args_map, "path": rp})
                self._approved.clear()
                self._approved.wait()
                if not self._approved_val:
                    reply = f"已驳回：敏感操作被人工拦截（尝试在 {rp} 执行命令）。"
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return

            self.status.emit(f"[思考 2/3] 执行工具: {tool_name}（{rp}）")
            os.environ["SANDBOX_PATH"] = rp
            try:
                self.tool_call_count += 1
                tool_result = self.tools_map[tool_name].invoke(args_map)
            except Exception as e:
                tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

            self.status.emit("[思考 3/3] 整理执行结果...")
            final = self.llm.invoke(
                f"你是专业的命令执行助手「隐盾」。请根据以下信息用中文给用户一个清晰的回复：\n\n"
                f"用户请求：{ai_input}\n"
                f"目标路径：{rp}\n"
                f"执行的操作：{tool_name}\n"
                f"执行的命令：{command}\n"
                f"工具执行结果：{tool_result}\n\n"
                f"请用简洁友好的语言总结命令执行结果，包括：\n"
                f"1. 是否成功\n"
                f"2. 命令输出的关键信息\n"
                f"3. 如果有错误，简要说明原因\n"
                f"不要使用技术术语，用日常语言表达。"
            )
            reply = self._clean(final.content if hasattr(final, "content") else str(final))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 兜底：任何未覆盖的情况，直接回答
        reply = self._direct_answer(ai_input, memory)
        reply = self._clean(reply)
        if self.privacy_shield and box:
            reply = engine.deanonymize(reply, box)
        memory.update_with_context_result(self.user_input, reply)
        self.finished.emit(reply)

    def _direct_answer(self, ai_input: str, memory) -> str:
        """让模型直接回答用户问题（不调用工具），用于意图为 answer 或 解析失败时的兜底。"""
        messages = [SystemMessage(
            content="你是一个专业的中文智能助手，代号「隐盾」。请用中文直接、完整地回答用户的问题。\n\n"
                    "回答要求：\n"
                    "1. 回答必须完整，不要中途中断或省略关键步骤\n"
                    "2. 如果问题要求证明、解释、推理，请给出完整的推理过程，每一步都清晰说明\n"
                    "3. 如果是代码问题，给出完整的代码示例和解释\n"
                    "4. 如果是数学/逻辑问题，给出完整的推导过程\n"
                    "5. 答案长度以回答完整为准，不要为了简洁而省略\n"
                    "6. 直接输出答案，不要用 Markdown 代码块包裹"
        )]
        messages.extend(memory.get_context_messages(self.llm))
        messages.append(HumanMessage(content=ai_input))
        resp = self.llm.invoke(messages)
        return resp.content if hasattr(resp, "content") else str(resp)

    # ──────────────────────────────────────────
    # 快速模式（单次 invoke，向后兼容）
    # ──────────────────────────────────────────
    def _run_quick_mode(self, ai_input, box, memory, engine):
        """
        快速模式：先让模型输出结构化意图，
        - 如果意图是 answer：直接返回模型回答（最快）
        - 如果是其他意图：执行工具（比思考模式少一步润色，更快）
        """
        self.status.emit("[快速模式] 识别用户意图...")
        intent_info = self._recognize_intent(ai_input, memory)
        intent = intent_info.get("INTENT", "answer").strip().lower()
        filename = intent_info.get("FILENAME", "").strip()
        content = intent_info.get("CONTENT", "").strip()
        command = intent_info.get("COMMAND", "").strip()
        focus = intent_info.get("FOCUS", "").strip()
        answer_text = intent_info.get("ANSWER", "").strip()

        # 不需要工具的情况：直接用专用问答接口获取完整回答
        if intent not in ("create_file", "read_file", "modify_file", "delete_file", "list_files", "analyze_project", "run_command"):
            reply = self._clean(self._direct_answer(ai_input, memory))
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)
            memory.update_with_context_result(self.user_input, reply)
            self.finished.emit(reply)
            return

        # 需要工具的情况：解析路径 + 调用工具 + 返回结果
        rp = self._resolve_path(self.user_input, {"target_directory": intent_info.get("DIRECTORY", "")})
        is_sensitive = self._is_sens(rp)

        # 根据意图确定工具名和参数
        tool_name = ""
        ca = {}
        if intent == "create_file":
            tool_name = "create_local_file"
            if not filename:
                reply = "文件名不明确，请告诉我要创建什么文件。"
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return
            ca = {"filename": os.path.basename(filename), "content": content}
        elif intent == "read_file":
            tool_name = "read_local_file"
            if not filename:
                # 尝试从对话历史中提取之前提到的关键文件名
                history_text = ""
                try:
                    history_messages = memory.get_context_messages(self.llm)
                    for msg in history_messages:
                        if hasattr(msg, 'content'):
                            history_text += str(msg.content) + " "
                except Exception:
                    pass
                file_pattern = re.compile(r'([a-zA-Z0-9_\-]+\.(?:js|json|py|html|css|xml|yml|yaml|toml|cfg|ini))', re.IGNORECASE)
                matched_files = file_pattern.findall(history_text)
                if matched_files:
                    filename = matched_files[0]
                else:
                    reply = "文件名不明确，请告诉我要读取什么文件。"
                    if self.privacy_shield and box:
                        reply = engine.deanonymize(reply, box)
                    memory.update_with_context_result(self.user_input, reply)
                    self.finished.emit(reply)
                    return
            ca = {"filename": os.path.basename(filename)}
        elif intent == "modify_file":
            tool_name = "modify_local_file"
            if not filename:
                reply = "文件名不明确，请告诉我要修改什么文件。"
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return
            ca = {"filename": os.path.basename(filename), "old_content": "", "new_content": content}
        elif intent == "delete_file":
            tool_name = "delete_local_file"
            if not filename:
                reply = "文件名不明确，请告诉我要删除什么文件。"
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return
            ca = {"filename": os.path.basename(filename)}
        elif intent == "run_command":
            tool_name = "run_local_command"
            if not command:
                reply = "命令不明确，请告诉我要执行什么命令。"
                if self.privacy_shield and box:
                    reply = engine.deanonymize(reply, box)
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return
            ca = {"command": command}
        elif intent == "analyze_project":
            tool_name = "analyze_project"
            ca = {"target_directory": rp, "max_depth": 3, "focus": focus}
        else:  # list_files
            tool_name = "list_local_files"
            ca = {"target_directory": intent_info.get("DIRECTORY", "当前沙箱目录")}

        # 还原脱敏参数
        if self.privacy_shield and box:
            ca = {k: (engine.deanonymize(v, box) if isinstance(v, str) else v) for k, v in ca.items()}

        # 高危操作二次审批
        if is_sensitive:
            self.status.emit("🚨 等待人工合规审批...")
            self.need_confirm.emit({"name": tool_name, "args": ca, "path": rp})
            self._approved.clear()
            self._approved.wait()
            if not self._approved_val:
                reply = f"已驳回：敏感操作被人工拦截（尝试在 {rp} 执行 {tool_name}）。"
                memory.update_with_context_result(self.user_input, reply)
                self.finished.emit(reply)
                return

        # 执行工具
        self.status.emit(f"[快速模式] 执行工具: {tool_name}（{rp}）")
        os.environ["SANDBOX_PATH"] = rp
        try:
            self.tool_call_count += 1
            tool_result = self.tools_map[tool_name].invoke(ca)
        except Exception as e:
            tool_result = f"[工具执行错误] {type(e).__name__}: {e}"

        # analyze_project 需要让模型解读报告（即使快速模式也要做），其余工具直接返回结果
        if intent == "analyze_project":
            user_focus = f"\n\n用户关注：{focus}" if focus else ""
            self.status.emit("[快速模式] 生成项目解读...")
            final = self.llm.invoke(
                f"你是一个资深的软件架构师「隐盾」。请根据以下项目分析报告，"
                f"用中文给用户一个**专业、简洁、易懂**的项目解读：\n\n"
                f"用户请求：{ai_input}{user_focus}\n"
                f"项目路径：{rp}\n"
                f"分析报告（文件结构、关键文件、代码采样）：\n{tool_result}\n\n"
                f"请按以下结构输出：\n"
                f"📌 项目概述：这是一个什么项目？做什么的？\n"
                f"🛠️ 技术栈：使用的主要技术、框架、语言\n"
                f"📂 模块划分：主要目录/模块及其职责\n"
                f"🚀 入口与启动：主要入口文件是什么？\n"
                f"💡 核心特点：值得注意的设计模式或技术亮点\n"
                f"⚠️  潜在问题：代码中可能存在的问题或改进建议\n"
            )
            reply = self._clean(final)
        elif intent == "read_file":
            user_focus = f"\n\n用户关注：{focus}" if focus else ""
            self.status.emit("[快速模式] 分析文件内容...")
            final = self.llm.invoke(
                f"你是专业的代码分析助手「隐盾」。请根据以下文件内容进行深度分析：\n\n"
                f"用户请求：{ai_input}{user_focus}\n"
                f"文件：{filename}（位于 {rp}）\n"
                f"文件内容：\n{tool_result}\n\n"
                f"分析要求：\n"
                f"1. 若文件是游戏配置（data.js等），请提取资源类型、属性、初始值并用表格展示\n"
                f"2. 若文件是代码文件，请解释关键函数、变量和逻辑流程\n"
                f"3. 输出使用清晰的标题、列表和表格，语言通俗易懂\n"
                f"4. 使用中文回答\n"
            )
            reply = self._clean(final)
        else:
            reply = f"操作完成：\n{tool_result}\n\n目标路径：{rp}"

        if self.privacy_shield and box:
            reply = engine.deanonymize(reply, box)
        memory.update_with_context_result(self.user_input, reply)
        self.finished.emit(reply)

    # ──────────────────────────────────────────
    # 工具辅助：路径解析与安全检查
    # ──────────────────────────────────────────
    def _resolve_path(self, text, args):
        det = self._det(text)
        if det:
            return det
        desc = args.get("target_directory", "当前沙箱目录")
        return self._dyn(desc) if desc and desc not in ["当前沙箱目录", "项目根目录"] else self.sandbox_path

    @staticmethod
    def _det(text):
        """从用户输入文本中解析目标路径（支持多种中文描述格式）。"""
        # 只有当"桌面"作为目录指示词时才返回桌面目录，而不是路径中包含"桌面"两字就返回
        # 匹配模式："在桌面"、"到桌面"、"从桌面"、"桌面的"、"桌面目录"、"桌面文件夹"等
        if re.search(r"(?:在|到|从)\s*桌面|桌面(?:上|中|下|的|目录|文件夹)", text):
            return os.path.join(os.path.expanduser("~"), "Desktop")

        # 中文指示词（必须完整匹配才算命中，避免"文档"因含"文"字被误判）
        # ── 双字/多字词优先级最高
        _INDICATORS_MULTI = (
            "这个", "那个", "该个",
            "目录", "文件夹",
            "下的", "里面", "里头",
            "创建", "新建", "保存", "写入", "生成", "删除", "移除", "清理",
            "项目", "帮我", "分析", "查看", "看看", "写出",
        )
        # 强单字指示词：在段内也能拦停（几乎不可能出现在合法目录名里）
        _INDICATORS_SINGLE_STRONG = {"该", "此", "下", "个"}
        # 弱单字指示词：仅在"作为独立段"时视为边界（"我的文档"合法，所以"的"不拦段内）
        _INDICATORS_SINGLE_WEAK = {"的", "中", "内", "里", "文件"}

        def _is_indicator_segment(seg):
            """整个段是否就是一个指示词（用于"独立段才是边界"的判断）。"""
            s = seg.strip()
            if not s:
                return True
            for ind in _INDICATORS_MULTI:
                if s == ind:
                    return True
            if s in _INDICATORS_SINGLE_STRONG or s in _INDICATORS_SINGLE_WEAK:
                return True
            return False

        def _safe_segment(seg):
            """一个路径段是否是合法的目录/文件名。"""
            if not seg:
                return False
            # 纯英数/点/下划线/横杠 —— 肯定合法
            if re.fullmatch(r"[\w.\-]+", seg):
                return True
            # 段本身就是一个指示词 → 不合法
            if _is_indicator_segment(seg):
                return False
            # 段以指示词结尾/开头 → 通常是污染（如 "新建 文件夹"）
            for ind in _INDICATORS_MULTI:
                if seg.startswith(ind) or seg.endswith(ind):
                    return False
            return True

        def _splitext_is_file(name):
            _, ext = os.path.splitext(name)
            return bool(ext) and len(ext) >= 3 and ext[1:].isalnum()

        # ── 1. 处理完整路径格式：E:/xxx 或 E:\\xxx
        #    宽松匹配：先抓到以盘符开头的一长串字符，再按分隔符逐段清洗边界指示词
        loose_pattern = re.compile(
            r"([A-Za-z]):[/\\]{1,3}[\w\u4e00-\u9fa5.\-/\\]+",
            re.IGNORECASE,
        )
        m = loose_pattern.search(text)
        if m:
            raw = m.group(0)
            # 规范化分隔符
            raw_norm = raw.replace("\\", "/").replace("//", "/")
            # 去掉盘符前缀，逐段处理
            drive_letter = raw[0].upper()
            body = raw_norm[3:]  # 去掉 "X:/"

            # 按 "/" 切分，逐段从左到右接受；段内再从左到右扫描多字词指示词，
            # 命中就停；段本身是指示词也停
            segments = body.split("/")
            accepted = []
            stopped = False
            for seg in segments:
                if stopped:
                    break
                # 段本身就是独立指示词段 → 停止（如 "的"、"下"）
                if _is_indicator_segment(seg):
                    stopped = True
                    continue
                # 段内从左到右扫描多字词指示词 + 强单字指示词（如 "这个"、"目录"、"该"、"下"）
                cleaned = ""
                hit = False
                i = 0
                while i < len(seg):
                    matched_len = 0
                    # 优先匹配多字词（从长到短）
                    for ind in sorted(_INDICATORS_MULTI, key=len, reverse=True):
                        if seg[i:i + len(ind)] == ind:
                            matched_len = len(ind)
                            break
                    # 再匹配强单字指示词
                    if not matched_len and seg[i] in _INDICATORS_SINGLE_STRONG:
                        matched_len = 1
                    if matched_len:
                        hit = True
                        break
                    cleaned += seg[i]
                    i += 1
                if cleaned:
                    if _safe_segment(cleaned):
                        accepted.append(cleaned)
                    else:
                        stopped = True
                if hit:
                    stopped = True

            if not accepted:
                return os.path.abspath(f"{drive_letter}:\\")

            # 组装最终路径
            final_path = f"{drive_letter}:/" + "/".join(accepted)
            # basename 有扩展名 → 降级为父目录（但保留 .js/.json 等配置文件，因为这些常被直接引用）
            base = os.path.basename(final_path.replace("/", "\\"))
            if _splitext_is_file(base):
                ext = os.path.splitext(base)[1].lower()
                # 保留配置/数据文件（.js/.json/.xml/.yml 等），这些常被直接引用
                if ext not in {".js", ".json", ".xml", ".yml", ".yaml", ".toml", ".cfg", ".ini"}:
                    parent = os.path.dirname(final_path)
                    if len(parent) > 2:
                        final_path = parent

            return os.path.abspath(os.path.normpath(final_path.replace("/", "\\")))

        # ── 2. 处理中文描述格式（E盘...qwen文件夹/目录）
        drive_match = re.search(r"([A-Za-z])\s*盘", text)
        if drive_match:
            d = drive_match.group(1).upper()
            after_drive = text[drive_match.end():]

            # 先跳过 "的/这个/那个/该" 等前面的指示词
            after_drive = re.sub(r"^(?:的|这个|那个|该|此)\s*", "", after_drive)

            # 在 "盘" 之后寻找形如 "xx文件夹" / "xx目录" 的中文描述
            cn_suffix = r"(?:文件夹内|文件夹中|文件夹里|文件夹下|文件夹|目录下|目录里|目录中|目录)"
            # 英文/数字名目录
            m_en = re.search(r"([a-zA-Z0-9_\-]{1,30})" + cn_suffix, after_drive)
            if m_en:
                return os.path.abspath(f"{d}:\\{m_en.group(1)}")
            # 中文名目录（1-8 个中文字，且不能是纯指示词）
            m_cn = re.search(r"([\u4e00-\u9fa5]{1,8})" + cn_suffix, after_drive)
            if m_cn:
                name = m_cn.group(1)
                if not _is_indicator_segment(name):
                    return os.path.abspath(f"{d}:\\{name}")
            return os.path.abspath(f"{d}:\\")

        return None

    @staticmethod
    def _dyn(desc):
        if not desc or "项目根目录" in desc or "当前沙箱" in desc:
            return os.path.abspath(".")
        if "桌面" in desc:
            return os.path.join(os.path.expanduser("~"), "Desktop")
        p = desc.strip()
        if "盘" in p:
            parts = p.split("盘")
            p = parts[0].upper() + ":\\" + (parts[1] if len(parts) > 1 else "")
        for s in ["内", "中", "目录", "文件夹", "根目录"]:
            if p.endswith(s):
                p = p[:-len(s)]
        try:
            return os.path.abspath(os.path.normpath(p.strip()))
        except Exception:
            return os.path.abspath(".")

    @staticmethod
    def _is_sens(path):
        return path.lower() not in (
            os.path.abspath(".").lower(),
            os.path.join(os.path.expanduser("~"), "Desktop").lower()
        )

    @staticmethod
    def _clean(text):
        if not text:
            return ""
        t = re.sub(r"(?i)(?:brtc|portun|tool_call)?\s*\{.*\}\s*</tool_call>?", "", text)
        return re.sub(r"(?i)</?tool_call>", "", t).strip()

