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
import time
import concurrent.futures
from PySide6.QtCore import QObject, Signal
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from yindun.core.privacy_engine import PrivacyEngine
from yindun.core.memory_manager import SummarizableChatHistory
from yindun.core.audit_log import AuditLog
from yindun.core.policy_manager import PolicyManager

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
    "8. 在文件中搜索特定内容（search_in_files）\n"
    "9. ★★★ 读取长附件文档的指定片段（read_attachment_chunk）——按题号/关键词/字符区间检索附件原文，详见下方【长文档分块检索规范】\n"
    "10. ★★★ 知识库语义检索（search_knowledge_base）——在已入库的本地知识库中进行语义检索，详见下方【知识库检索规范】\n\n"
    "【工具使用规范】\n"
    "1. 当用户请求涉及文件操作、目录查询、项目分析、命令执行等任务时，优先调用工具，不要凭空回答。\n"
    "2. 工具参数中的 target_directory 表示目标路径，可以是绝对路径（如 E:\\qwen、C:\\Users\\test），也可以是中文描述（如桌面、E盘、D盘的文档目录）。\n"
    "3. ★★★ 关键规则：如果用户消息中包含完整的绝对路径（如 E:\\xxx\\yyy），你必须把完整路径原样填入 target_directory，绝对不要留空或填'当前沙箱目录'，否则系统会分析错位置！\n"
    "4. 如果用户提到文件名但没指定目录，target_directory 留空表示使用当前工作目录。\n"
    "5. 支持多步推理：例如「先列出文件，再读取关键文件，最后分析项目结构」，可以连续调用多个工具。\n"
    "6. 工具返回结果后，请根据结果和用户的原始需求给出清晰、专业、易懂的中文总结。\n\n"
    "【附件文档处理规范】\n"
    "当用户消息中出现 [离线附件环境上下文：xxx] 标记时，说明已挂载离线文档/音频转写文本。你必须按以下流程处理：\n"
    "1. 类型识别：先判断附件类型（PDF/Word/Excel/TXT/音频转写），并推断文档主题。\n"
    "2. 结构化提取（非音频）：\n"
    "   - 提取标题、章节层级、关键术语表\n"
    "   - 表格数据用 Markdown 表格还原，保留行列对应关系\n"
    "   - 列表/编号项保持原序\n"
    "3. 音频转写处理：\n"
    "   - 推断说话人角色（如'会议主持/参会者A/参会者B'），用 [说话人]: 文本 格式重组\n"
    "   - 识别问答环节、决议结论、待办事项\n"
    "   - 如转写文本无标点或断句混乱，先做句子边界恢复再分析\n"
    "4. 多维度分析（按用户提问意图选择）：\n"
    "   - 摘要：3句话核心 + 要点列表\n"
    "   - 关键信息抽取：人名/机构/日期/金额/条款编号/数据指标\n"
    "   - 风险扫描：标注敏感数据（手机号/身份证/财务数字/密钥）出现位置\n"
    "   - 对比问答：基于文档内容回答，引用原文片段为证，标注'见第X段'\n"
    "5. 输出格式：先给【附件概览】（类型/主题/字数/敏感项计数），再给用户问题的回答。\n"
    "6. 局限告知：若文档含表格/图片/公式导致解析缺失，明确告知用户'该部分未解析到，建议补充原文'，不要编造内容。\n"
    "   ★★★ OCR 相关规则（禁止编造'OCR 失败'话术）：\n"
    "   - 若附件开头出现 [本文档部分页面通过 OCR 识别，可能存在识别误差] 标记，说明 OCR 已成功执行并提取到文字，可以正常引用 OCR 识别出的内容作答，必要时在引用处提示'（OCR 识别，可能存在误差）'。\n"
    "   - 仅当某页明确标注 [本页为扫描图片/空白页，OCR 未识别到文字内容] 时，才说明该页 OCR 确实未识别到文字；此时应说'该页未识别到文字内容'，不要说'OCR 技术失败/未通过 OCR 识别/OCR 未能识别'等夸大话术。\n"
    "   - 严禁把 [本文档部分页面通过 OCR 识别] 这一成功标记曲解为失败信号，严禁在附件概览中写'主题：未知（因前 N 页均未通过 OCR 识别出文字）'。\n"
    "   - 若附件标注 [OCR 引擎未安装]，提示用户运行 `pip install rapidocr-onnxruntime` 后重新挂载。\n"
    "7. ★★★ 跨轮次附件上下文联系（关键规则）：\n"
    "   - 当本轮用户消息中没有 [离线附件环境上下文：xxx] 标记，但用户提问明显是在追问上一篇文档的内容时（例如出现'上面''刚才''这个文档''那个文件''文中''上文''文档里''它''这份'等指代词，或问题主题与上一轮附件主题高度相关），你必须主动回溯对话历史中的 [离线附件环境上下文：xxx] 标记块，从中提取附件原文作为本轮回答依据。\n"
    "   - 回答时无需再次输出【附件概览】，直接基于历史附件原文作答，并在结尾用一行注明'（参考：历史挂载文档《文件名》）'。\n"
    "   - 如果历史附件原文已不在上下文窗口内（被摘要压缩），需明确告知用户：'该文档原文已超出记忆窗口，请重新挂载《文件名》后再提问'，不要凭空编造文档内容。\n"
    "   - 如果用户追问涉及历史多个附件，需指明本次回答引用的是哪一份文档。\n"
    "   - 禁止以'您没有上传文件''请上传文件后再提问'为由拒绝基于历史附件的追问。\n"
    "8. ★★★ 题号定位规范（试卷/题库场景强制执行）：\n"
    "   当附件为试卷/题库/练习册/考试文档，且用户问的是某一道具体题目时（如'第5题''题目12''第3题选什么'），你必须按以下流程回答，禁止跳过定位步骤直接作答：\n"
    "   步骤 A：在当前上下文中查找 [Q<题号>] 锚点标记（系统会在每道题前自动注入 [Q5]、[Q12] 等锚点）。\n"
    "   步骤 B：若找到 [Q<题号>] 锚点，先输出一行定位标记：\n"
    "     【定位】Q<题号> 原文：「此处粘贴该题完整题干原文，含选项」\n"
    "   步骤 C：再输出答案与解析：\n"
    "     【答案】<选项或答案>\n"
    "     【解析】<详细解析>\n"
    "   步骤 D：若上下文中没有 [Q<题号>] 锚点（可能题目在截断之外，或文档未自动识别到题号），必须调用 read_attachment_chunk 工具检索：\n"
    "     read_attachment_chunk(file='附件文件名', question='题号')\n"
    "     工具返回该题完整原文后，再按步骤 B、C 输出。\n"
    "   步骤 E：若工具也未找到该题号（返回未命中提示），明确告知用户'未在文档中定位到第 N 题，请确认题号或文档是否正确'，禁止凭空编造题干和答案。\n"
    "   补充说明：\n"
    "   - 若用户问的是'第5题到第8题'，需对每道题分别执行定位流程，不能只答第一题。\n"
    "   - 若用户问的是'含某关键词的题目'（如'三角函数那道题'），用 keyword 参数调用工具：\n"
    "     read_attachment_chunk(file='附件文件名', keyword='三角函数')\n"
    "   - 禁止仅凭题号数字猜测题干内容，必须以原文片段为准。\n"
    "9. ★★★ 长文档分块检索规范：\n"
    "   当附件上下文末尾出现【长文档分块提示】时，说明文档已截断，后续内容需通过工具检索：\n"
    "   - 工具 read_attachment_chunk 支持三种检索方式（按需任选）：\n"
    "     a. 题号检索：read_attachment_chunk(file='文件名', question='5')  → 返回第5题完整题干+前后各1道题\n"
    "     b. 关键词检索：read_attachment_chunk(file='文件名', keyword='三角函数')  → 返回首次出现该词的片段（前后各3000字符）\n"
    "     c. 字符区间检索：read_attachment_chunk(file='文件名', char_start=30000)  → 从第30000字符继续读取6000字符\n"
    "   - 当用户问的题目/内容不在已展示的前30000字符中，必须主动调用工具检索，不要回答'文档中未找到'。\n"
    "   - 工具可多次调用：例如用户问'第35题和第60题'，需分别调用两次工具检索两道题。\n"
    "   - char_length 参数可调整单次读取长度（500~12000），默认6000。\n\n"
    "【知识库检索规范】\n"
    "当用户询问的内容可能来自已入库的文档（如合同、报表、员工信息、规章等），优先使用 search_knowledge_base 工具进行语义检索。\n"
    "1. 适用场景：\n"
    "   - 用户问及合同条款、金额、当事人、日期等信息\n"
    "   - 用户问及员工联系方式、薪资、部门等信息\n"
    "   - 跨多份文档对比信息（如'哪份合同金额最高'）\n"
    "   - 语义近似检索（如问'提前终止'，文档写的是'解除协议'）\n"
    "2. 调用方式：search_knowledge_base(query='用户的实际问题', top_k=4)\n"
    "   - query 用自然语言描述，不要只填关键词\n"
    "   - top_k 默认4，问题复杂时可增至6~8\n"
    "3. 与 read_attachment_chunk 的区别：\n"
    "   - search_knowledge_base：跨所有入库文档的语义检索，适合'找相关内容'\n"
    "   - read_attachment_chunk：单文档精确字符定位，适合'读某文档第N段'\n"
    "   - 若不确定用哪个，优先用 search_knowledge_base\n"
    "4. 返回内容已脱敏（敏感信息以 [KB_NAME_0]/[KB_PHONE_0] 等占位符显示，前缀 KB_ 表示来自知识库），你应基于占位符文本正常作答，占位符会在最终展示时自动还原给用户，无需你处理。\n\n"
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
        # ★★★ 附件全文快照：由 main_window._start_worker 注入
        # 结构：{文件名: 全文文本}，供 read_attachment_chunk 工具检索
        # 跨轮次保留：main_window 会把所有历史轮次挂载过的附件累积到 session，再传给 worker
        self.attachment_fulltext: dict = {}
        # 当前 session id（由 main_window 注入，用于回写附件快照）
        self.session_id = None
        # ★ 知识库实例（脱敏 RAG）：懒加载，首次调用 search_knowledge_base 时初始化
        self._kb_instance = None
        # ★ 知识库检索返回的全局映射表：供输出层 deanonymize 合并使用
        # 每次 ReAct 循环开始时清空，结束时用后即焚
        self._kb_mapping: dict = {}
        # ★ 跨轮脱敏映射表：累积所有历史轮次的脱敏映射，
        # 保证历史消息里的占位符（如 [PHONE_0]）在后续轮次也能正确还原
        self._box_mapping: dict = {}
        # 由 main_window 注入的上轮映射快照（从会话数据恢复，供本轮合并还原）
        self._box_mapping_restore: dict = {}

    def approve(self, ok):
        """人工审批回调：ok=True 表示批准，ok=False 表示驳回"""
        self._approved_val = ok
        self._approved.set()

    def cancel(self):
        """取消正在进行的推理：置取消标志，并把审批视为拒绝（_approved_val=False），
        同时 set 审批事件，唤醒所有阻塞在等待人工审批上的线程，避免永久挂起。"""
        self._cancel_requested.set()
        self._approved_val = False
        self._approved.set()

    def _request_approval(self, name, args, path, timeout: float = 60.0) -> bool:
        """统一的敏感操作人工审批等待。

        先 clear 审批事件再 emit need_confirm（修正 clear/emit 顺序，消除
        GUI 先响应导致批准被误清的竞态）；随后用联合等待（审批事件 / 取消事件
        / 超时）保证任何情形都能被唤醒返回，且不会被取消/超时永久卡死。

        返回 True=批准；False=驳回/取消/超时（默认 timeout 60s）。
        """
        self._approved.clear()
        self.need_confirm.emit({"name": name, "args": args, "path": path})
        deadline = time.monotonic() + timeout
        approved = None
        while approved is None:
            # 取消请求优先：cancel() 置标志并 set 审批事件
            if self._cancel_requested.is_set():
                approved = False
                break
            if self._approved.wait(timeout=0.2):  # 0.2s 轮询取消标志
                approved = bool(self._approved_val)
                break
            if time.monotonic() >= deadline:
                approved = False  # 超时视为驳回，避免永久阻塞
                break
        self._approved_val = approved
        return approved

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
            # ★ 每轮对话开始时清空知识库映射表（上轮的已用后即焚）
            self._kb_mapping = {}
            # ★ 恢复跨轮脱敏映射表（从会话数据恢复，保证历史占位符可还原）
            if self._box_mapping_restore:
                self._box_mapping = dict(self._box_mapping_restore)
                self._box_mapping_restore.clear()
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
            # ★ 审计：记录用户输入脱敏情况
            try:
                _audit = AuditLog()
                if self.privacy_shield and box:
                    _stats = engine.get_last_stats()
                    if _stats:
                        _audit.log_privacy_batch(_stats, "detected")
                    _audit.log_llm_input(self.user_input, has_privacy=True,
                                         preview=ai_input[:200])
                else:
                    _audit.log_llm_input(self.user_input, has_privacy=False,
                                         preview=self.user_input[:200])
            except Exception:
                pass  # 审计失败不影响主流程

            # 检查是否为音频文件请求（音频文件已作为上下文注入，不需要工具调用）
            _AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")
            _path_match = re.search(r'([A-Za-z]:[\\/][^\s]+)', self.user_input)
            _is_audio_request = False
            if _path_match:
                _resolved_path = os.path.abspath(os.path.normpath(_path_match.group(1)))
                if os.path.exists(_resolved_path):
                    _is_audio_request = any(_resolved_path.lower().endswith(ext) for ext in _AUDIO_EXTS)

            # 简单问答或音频文件请求直接跳过工具调用，最快响应
            # 附件场景（含 [离线附件环境上下文] 标记）必须走 ReAct 循环，使用带附件规范的系统提示词
            _has_attachment = "[离线附件环境上下文" in ai_input

            # ★★★ 历史附件回溯：本轮未挂载附件，但 session 中有累积的附件快照
            # 全局索引已在 SystemMessage 中（_build_attachment_index），LLM 能看到所有附件的题号列表
            # 这里只需要：检测到有历史附件 → 强制走 ReAct 循环（避免被 _is_simple_question 短路）
            # 并给一个轻量提示，让 LLM 知道本轮问题可能针对历史附件
            _has_snapshot = bool(self.attachment_fulltext)

            # 同时扫描历史消息中的附件标记（用于检测历史曾挂载过附件，即使快照已丢失）
            _has_history_attachment = False
            for m in self.messages_snapshot:
                if isinstance(m, dict):
                    c = m.get("content", "")
                    if isinstance(c, str) and "[离线附件环境上下文" in c:
                        _has_history_attachment = True
                        break

            # 若本轮无附件但有历史附件或快照，注入轻量提示
            if (not _has_attachment) and (_has_snapshot or _has_history_attachment):
                if _has_snapshot:
                    # 快照可用：LLM 可直接调用工具检索（全局索引已在 SystemMessage 中）
                    ai_input = (
                        f"[提示] 本轮未挂载新附件，但当前会话有历史附件资源（见上方系统提示中的"
                        f"【当前会话附件资源】索引表）。如需检索某道题，请调用 "
                        f"read_attachment_chunk 工具。\n\n"
                        f"[本轮用户提问]：{ai_input}"
                    )
                else:
                    # 快照不可用（可能 session 切换或重启后未恢复）
                    ai_input = (
                        f"[提示] 检测到此前对话曾挂载过附件，但附件全文已不在当前会话快照中。"
                        f"如需精确检索，请告知用户重新挂载附件。\n\n"
                        f"[本轮用户提问]：{ai_input}"
                    )
                _has_attachment = True  # 强制走 ReAct 循环，避免被 _is_simple_question 短路

            if (self._is_simple_question(ai_input) or _is_audio_request) and not _has_attachment:
                self.status.emit("[快速回答] 直接回答问题...")
                reply = self._clean(self._direct_answer(ai_input, memory))
                if self.privacy_shield and box:
                    # ★ 合并跨轮映射表：用「历史映射 + 本轮映射」还原，避免历史占位符无法还原
                    merged_mapping = {**self._box_mapping, **box}
                    reply = engine.deanonymize(reply, merged_mapping)
                    self._box_mapping.update(box)  # 累积本轮映射，供后续轮次还原
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
                    # ★ 强制工具分支也必须走统一的审批执行通道：
                    #   PolicyManager.check → confirm 则 emit need_confirm 等待人工审批 → 通过后才执行。
                    #   不再直接 tool.invoke()，杜绝绕过审批的旁路。
                    try:
                        if (_has_analyze or os.path.isdir(_resolved_path)) and "analyze_project" in self.tools_map:
                            self.status.emit(f"[工具执行] 扫描项目目录: {_resolved_path}")
                            _args = {"target_directory": _resolved_path, "max_depth": self.think_depth}
                            forced_tool_result = self._execute_with_approval("analyze_project", _args)
                            forced_tool_name = "analyze_project"
                        elif (_has_list or _has_read) and os.path.isdir(_resolved_path):
                            if "list_local_files" in self.tools_map:
                                self.status.emit(f"[工具执行] 列出目录: {_resolved_path}")
                                _args = {"target_directory": _resolved_path}
                                forced_tool_result = self._execute_with_approval("list_local_files", _args)
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
                # ★ 合并跨轮映射表：用「历史映射 + 本轮映射」还原，避免历史占位符无法还原
                merged_mapping = {**self._box_mapping, **box}
                final_reply = engine.deanonymize(final_reply, merged_mapping)
                self._box_mapping.update(box)  # 累积本轮映射，供后续轮次还原
                # ★ 审计：记录隐私还原（统计还原的实体类型数）
                try:
                    # box 结构：{占位符: 真实值}，统计各类型数量
                    _type_counts = {}
                    for _ph in box.keys():
                        _m = re.match(r"\[([A-Z]+)_?\d*\]", _ph)
                        _t = _m.group(1) if _m else "OTHER"
                        _type_counts[_t] = _type_counts.get(_t, 0) + 1
                    if _type_counts:
                        AuditLog().log_privacy_batch(_type_counts, "restored")
                except Exception:
                    pass
            # ★ 合并知识库映射表：如果本轮调用了 search_knowledge_base，
            # LLM 回答中会含知识库检索的占位符，需要用 _kb_mapping 还原
            if self._kb_mapping:
                final_reply = engine.deanonymize(final_reply, self._kb_mapping)
                self._kb_mapping.clear()  # 用后即焚
            # ★ 审计：记录最终还原后的 LLM 输出（展示给用户的版本）
            try:
                AuditLog().log_llm_output(final_reply, preview=final_reply[:200])
            except Exception:
                pass

            # 注：ReAct 循环内部已经通过 update_with_full_chain 保存完整工具调用链
            # 这里只需要把 final_reply 的 de-anonymized 版本 emit 给用户
            self.finished.emit(final_reply)

        except KeyboardInterrupt:
            # 用户主动取消，正常结束（不是错误）
            self.status.emit("[已取消] 推理已被用户中断")
            self.finished.emit("推理已取消。")
        except Exception as e:
            self.result_messages = self.messages_snapshot
            friendly = self._translate_error(e)
            self.error.emit(friendly)

    # ──────────────────────────────────────────
    # 错误翻译：将技术异常转为用户可理解的中文提示
    # ──────────────────────────────────────────
    @staticmethod
    def _translate_error(exc: Exception) -> str:
        ename = type(exc).__name__
        emsg = str(exc).lower()

        if any(kw in emsg for kw in ("connection refused", "积极拒绝", "无法连接",
                                       "winerror 10061", "connectionerror")):
            return (
                "❌ 无法连接模型服务\n\n"
                "模型（Ollama / API）未启动或已崩溃，请检查：\n"
                "1. Ollama 是否正在运行（托盘图标 / ollama serve）\n"
                "2. 远程 API 地址是否可达（VPN / 防火墙）\n"
                "3. 端口是否被其他程序占用\n\n"
                f"原始错误：{ename}"
            )

        if any(kw in emsg for kw in ("401", "unauthorized",
                                       "invalid api key", "authentication")):
            return (
                "❌ API 密钥无效或未配置\n\n"
                "请检查设置中的 API Key 是否正确，或重新生成密钥。\n\n"
                f"原始错误：{ename}"
            )

        if any(kw in emsg for kw in ("timeout", "timed out", "connect timeout")):
            return (
                "⏱ 请求超时\n\n"
                "模型服务响应太慢或网络不稳定，请稍后重试。\n"
                "如果使用远程 API，请检查网络连接和 VPN。\n\n"
                f"原始错误：{ename}"
            )

        if any(kw in emsg for kw in ("429", "rate limit", "too many requests")):
            return (
                "⏳ 请求过于频繁\n\n"
                "模型 API 限流，请等待片刻后再发送消息。\n\n"
                f"原始错误：{ename}"
            )

        if any(kw in emsg for kw in ("context length", "max token",
                                       "token limit", "too long")):
            return (
                "📄 上下文过长\n\n"
                "对话历史 + 附件内容超出了模型的上下文窗口。\n"
                "建议：开启新会话 / 缩短附件内容 / 切换更大上下文的模型。\n\n"
                f"原始错误：{ename}"
            )

        if any(kw in emsg for kw in ("modulenotfound", "no module", "import",
                                       "dll load", "onnxruntime")):
            return (
                "🔧 缺少必要的组件或依赖\n\n"
                "程序运行环境不完整，请联系管理员修复。\n\n"
                f"原始错误：{ename}: {exc}"
            )

        if any(kw in emsg for kw in ("permission", "access denied", "拒绝访问")):
            return (
                "🔒 权限不足\n\n"
                "操作被系统拒绝，请以管理员身份运行，或检查目标路径的读写权限。\n\n"
                f"原始错误：{ename}"
            )

        if any(kw in emsg for kw in ("file not found", "no such file",
                                       "找不到", "does not exist")):
            return (
                "📁 文件或路径不存在\n\n"
                "指定的文件或目录不存在，请检查路径是否正确。\n\n"
                f"原始错误：{ename}"
            )

        # 兜底：保留原始异常信息，方便排查
        return f"⚠️ 回答生成失败\n\n{ename}: {exc}"

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
        # ★★★ 把附件全局索引注入 SystemMessage，让 LLM 每轮对话都能看到所有附件的题号列表
        # 这样挂载文件成为整个 session 的全局资源，无需用户每轮重复挂载
        _attachment_index = self._build_attachment_index()
        _system_content = _SYSTEM_PROMPT
        if _attachment_index:
            _system_content = _SYSTEM_PROMPT + "\n" + _attachment_index
        messages = [SystemMessage(content=_system_content)]
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
        last_tool_result = ""  # 初始化，防止循环结束时 NameError

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
            # ★ 审计：记录本轮 LLM 输入(脱敏态)与输出
            try:
                _audit = AuditLog()
                # 输入：最后一条 HumanMessage 的内容
                _last_human = ""
                for _m in reversed(messages[:-1]):
                    _mc = getattr(_m, "content", "")
                    if isinstance(_m, HumanMessage) and isinstance(_mc, str) and _mc.strip():
                        _last_human = _mc
                        break
                if _last_human:
                    _audit.log_llm_input(_last_human, has_privacy=self.privacy_shield,
                                         preview=_last_human[:200])
                # 输出：模型本轮回答
                _ai_content = getattr(ai_msg, "content", "") or ""
                if _ai_content:
                    _audit.log_llm_output(_ai_content, preview=_ai_content[:200])
            except Exception:
                pass  # 审计失败不影响主流程

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
                    # ★ 审计：记录总结轮 LLM 输出
                    try:
                        _audit = AuditLog()
                        _audit.log_llm_input(summary_prompt, has_privacy=self.privacy_shield,
                                             preview=summary_prompt[:200])
                        if content:
                            _audit.log_llm_output(content, preview=content[:200])
                    except Exception:
                        pass

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
                # 兜底：如果仍然为空，给用户一个明确提示，避免静默返回空白
                if not final_reply.strip():
                    final_reply = (
                        "抱歉，模型未能针对该附件生成有效回答。\n"
                        "可能原因：\n"
                        "1. 附件文本过长超出模型上下文，请尝试截取片段或换更小的 PDF\n"
                        "2. 附件为扫描件/图片，且 OCR 未识别到文字（已安装 rapidocr-onnxruntime 仍可能因图片质量过差失败）\n"
                        "3. 模型当前状态异常，请重试或切换模型\n"
                        "请尝试：精简提问、切换深度思考模式、或挂载更小的文档。"
                    )
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
                pm = PolicyManager()
                policy_result = pm.check(tool_name, target_path)

                if policy_result == "deny":
                    # ★ 策略直接拒绝：不询问用户，直接驳回
                    tool_result = f"已拦截：安全策略禁止在 {target_path} 执行 {tool_display_name}"
                    tool_msg = ToolMessage(content=tool_result, tool_call_id=tool_call_id)
                    messages.append(tool_msg)
                    all_success = False
                    self.status.emit(
                        f"[思考 {round_count}/{max_rounds}] 🚫 安全策略已拦截: {tool_display_name}"
                    )
                    try:
                        AuditLog().log_access_control(
                            f"{tool_display_name}({tool_name})", target_path, approved=False)
                    except Exception:
                        pass
                    continue

                elif policy_result == "confirm":
                    self.status.emit(
                        f"[思考 {round_count}/{max_rounds}] 🚨 检测到敏感操作 "
                        f"{tool_display_name}，等待人工审批..."
                    )
                    if not self._request_approval(tool_display_name, tool_args, target_path):
                        # 用户驳回 / 取消 / 超时
                        tool_result = f"已驳回：敏感操作被人工拦截（尝试在 {target_path} 执行 {tool_display_name}）"
                        tool_msg = ToolMessage(content=tool_result, tool_call_id=tool_call_id)
                        messages.append(tool_msg)
                        all_success = False
                        # ★ 审计：记录访问控制驳回
                        try:
                            AuditLog().log_access_control(
                                f"{tool_display_name}({tool_name})", target_path, approved=False)
                        except Exception:
                            pass
                        continue
                    # ★ 审计：记录访问控制通过
                    try:
                        AuditLog().log_access_control(
                            f"{tool_display_name}({tool_name})", target_path, approved=True)
                    except Exception:
                        pass

                # 3. 执行工具
                self.status.emit(
                    f"[思考 {round_count}/{max_rounds}] 执行工具: {tool_display_name}（{target_path}）"
                )

                AuditLog().log_tool_call(tool_name, tool_args, target_path)

                try:
                    self.tool_call_count += 1

                    tool_result = self._execute_tool(tool_name, tool_args)
                    has_executed_tool = True  # 标记已执行过工具
                    last_tool_result = str(tool_result)[:2000]

                    # 脱敏工具返回内容
                    # 注意：search_knowledge_base 返回的已是脱敏文本，跳过避免二次脱敏
                    if self.privacy_shield and box and tool_name != "search_knowledge_base":
                        if isinstance(tool_result, str):
                            tool_result = engine.anonymize(tool_result)[0]
                            # ★ 审计：记录工具结果脱敏
                            try:
                                _r_stats = engine.get_last_stats()
                                if _r_stats:
                                    AuditLog().log_privacy_batch(_r_stats, "anonymized")
                            except Exception:
                                pass

                    AuditLog().log_tool_result(tool_name, str(tool_result)[:500], True)

                except Exception as e:
                    tool_result = f"[工具执行错误] {type(e).__name__}: {e}"
                    round_had_failure = True
                    all_success = False
                    AuditLog().log_tool_result(tool_name, str(e), False)

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
    # 附件全局索引构建：从 attachment_fulltext 生成题号索引表
    # 注入 SystemMessage，让 LLM 每轮对话都能看到所有附件的题号列表
    # ──────────────────────────────────────────
    def _build_attachment_index(self) -> str:
        """
        从 self.attachment_fulltext 生成附件全局索引表。

        返回一段文本，格式如下：
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        【当前会话附件资源】（全局可用，每轮对话均可引用）
        文件1：《试卷.pdf》  总长 45000 字符
          题号索引（共 50 题）：
          [Q1] 1. 下列哪个是正确的？...
          [Q2] 2、计算题：2+2=?...
          ...
          如需某题完整原文，调用：read_attachment_chunk(file='试卷.pdf', question='题号')
        ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

        设计要点：
        - 只放题号索引（每题前 80 字预览），不放全文，控制 token 开销
        - 题号索引让 LLM 知道"有哪些题、每题大概讲什么"，能直接回答简单问题
        - 需要完整题干时，LLM 调用 read_attachment_chunk 按需检索
        - 索引表注入 SystemMessage，每轮对话都存在，实现"全局资源"
        """
        if not self.attachment_fulltext:
            return ""

        import re as _re

        # 题号正则（与 main_window._inject_question_anchors 保持一致）
        _CN_DIGITS_LOCAL = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                            '六': 6, '七': 7, '八': 8, '九': 9}

        def _cn_to_arabic_local(s):
            if not s:
                return None
            if s.isdigit():
                try:
                    return int(s)
                except ValueError:
                    return None
            if '十' in s:
                parts = s.split('十')
                if len(parts) == 2:
                    tens = _CN_DIGITS_LOCAL.get(parts[0], 1) if parts[0] else 1
                    ones = _CN_DIGITS_LOCAL.get(parts[1], 0) if parts[1] else 0
                    return tens * 10 + ones
            return _CN_DIGITS_LOCAL.get(s)

        q_patterns = [
            _re.compile(r'^(\s*)(\d{1,3})\s*[.、)）]\s*(.+)$'),
            _re.compile(r'^(\s*)第\s*([一二三四五六七八九十百零\d]{1,4})\s*题\s*[.、:：)）]?\s*(.*)$'),
            _re.compile(r'^(\s*)题目\s*([一二三四五六七八九十百零\d]{1,4})\s*[.、:：)）]?\s*(.*)$'),
            _re.compile(r'^(\s*)Q\s*(\d{1,3})\s*[.、)）]?\s*(.+)$', _re.IGNORECASE),
        ]

        lines = []
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("【当前会话附件资源】（全局可用，每轮对话均可引用，无需用户重复挂载）")

        for fname, full_text in self.attachment_fulltext.items():
            total_len = len(full_text)
            lines.append(f"文件：《{fname}》  总长 {total_len} 字符")

            # 扫描题号
            questions = []  # [(题号, 题干预览)]
            for line in full_text.split('\n'):
                for pat in q_patterns:
                    m = pat.match(line)
                    if not m:
                        continue
                    num_str = m.group(2)
                    rest = m.group(3) if m.lastindex >= 3 else ""
                    num = _cn_to_arabic_local(num_str)
                    if num is None:
                        try:
                            num = int(num_str)
                        except ValueError:
                            continue
                    if not (1 <= num <= 100):
                        continue
                    # 题干预览：取前 80 字符
                    preview = rest.strip()[:80] if rest else ""
                    questions.append((num, preview))
                    break

            if questions:
                lines.append(f"  题号索引（共 {len(questions)} 题）：")
                for qnum, preview in questions:
                    if preview:
                        lines.append(f"  [Q{qnum}] {preview}...")
                    else:
                        lines.append(f"  [Q{qnum}]（题干在下一行，需调用工具获取完整内容）")
            else:
                lines.append("  （未识别到标准题号，建议用关键词检索）")

            lines.append(f"  如需某题完整原文，调用：read_attachment_chunk(file='{fname}', question='题号')")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)

    # ──────────────────────────────────────────
    # 工具执行层：统一处理所有工具的调用
    # ──────────────────────────────────────────
    def _execute_with_approval(self, tool_name: str, args: dict) -> str:
        """统一审批执行入口：策略检查 → 人工审批 → 执行工具。

        强制工具分支与 ReAct 循环复用同一审批通道，确保不存在绕过审批的第二调用路径。
        策略：deny → 直接拦截；confirm → emit need_confirm 等待人工审批，通过后才执行；
        approve（只读工具在沙箱内）→ 免审批放行。
        """
        target_path = self._resolve_target_path(args)
        tool_display_name = self._map_tool_display_name(tool_name)
        pm = PolicyManager()
        policy_result = pm.check(tool_name, target_path)

        # deny：策略直接拒绝，不询问用户
        if policy_result == "deny":
            self.status.emit(f"🚫 安全策略已拦截: {tool_display_name}")
            try:
                AuditLog().log_access_control(
                    f"{tool_display_name}({tool_name})", target_path, approved=False)
            except Exception:
                pass
            return f"已拦截：安全策略禁止在 {target_path} 执行 {tool_display_name}"

        # confirm：需人工审批
        if policy_result == "confirm":
            self.status.emit(
                f"🚨 检测到敏感操作 {tool_display_name}，等待人工审批..."
            )
            if not self._request_approval(tool_display_name, args, target_path):
                # 驳回 / 取消 / 超时
                self.status.emit(f"🚫 已驳回: {tool_display_name}")
                try:
                    AuditLog().log_access_control(
                        f"{tool_display_name}({tool_name})", target_path, approved=False)
                except Exception:
                    pass
                return f"已驳回：敏感操作被人工拦截（尝试在 {target_path} 执行 {tool_display_name}）"
            # 审计：记录人工审批通过
            try:
                AuditLog().log_access_control(
                    f"{tool_display_name}({tool_name})", target_path, approved=True)
            except Exception:
                pass

        # approve 或已通过人工审批：正常执行工具
        self.status.emit(f"执行工具: {tool_display_name}")
        return self._execute_tool(tool_name, args)

    def _execute_tool(self, tool_name: str, args: dict) -> str:
        """根据工具名调用对应的工具对象，返回工具执行结果"""

        # ──────────────────────────────────────────
        # ★★★ 附件分块读取工具拦截：在 Worker 内部处理，访问 self.attachment_fulltext
        # 该工具的 @tool 占位实现只用于 LLM schema 绑定，实际逻辑在这里
        # ──────────────────────────────────────────
        if tool_name == "read_attachment_chunk":
            return self._execute_read_attachment_chunk(args)

        # ──────────────────────────────────────────
        # ★ 知识库语义检索工具拦截：脱敏 RAG
        # 检索结果已是脱敏文本，映射表存入 self._kb_mapping 供输出层还原
        # ──────────────────────────────────────────
        if tool_name == "search_knowledge_base":
            return self._execute_search_knowledge_base(args)

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

        # 通过 tools_map 中的键做精确匹配（仅大小写不敏感的精确命中）
        # 注意：不使用子串包含匹配——校验对象 ≠ 执行对象会导致无关短名
        # （如 "list"）误命中任意含该子串的工具，造成权限判定错位。
        # 若存在别名需求，应使用显式 ALIAS 映射表精确映射，而非子串模糊匹配。
        for key, tool_obj in self.tools_map.items():
            if tool_name.lower() == key.lower():
                if isinstance(tool_obj, BaseTool):
                    return str(tool_obj.invoke(effective_args))
                return str(tool_obj(effective_args) if callable(tool_obj) else str(tool_obj))

        # 工具不存在，返回错误信息
        return f"[错误] 工具 '{tool_name}' 未找到。可用工具: {list(self.tools_map.keys())}"

    # ──────────────────────────────────────────
    # 附件分块读取引擎：按题号/关键词/字符区间检索长文档
    # ──────────────────────────────────────────
    def _execute_read_attachment_chunk(self, args: dict) -> str:
        """
        实现 read_attachment_chunk 工具的实际逻辑。
        从 self.attachment_fulltext 中按题号/关键词/字符区间检索附件原文片段。

        支持三种检索模式（按优先级）：
        1. 题号检索（question）：识别"第N题"、"N."、"N、"等题号格式，
           返回该题完整题干 + 前后各 1 道题作为上下文。
        2. 关键词检索（keyword）：返回首次出现该关键词的片段（前后各 3000 字符）。
        3. 字符区间检索（char_start + char_length）：从指定位置读取指定长度。

        若三种模式均未指定或未命中，返回错误提示。
        """
        file_name = args.get("file", "").strip()
        question = args.get("question", "").strip()
        keyword = args.get("keyword", "").strip()
        char_start = args.get("char_start", -1)
        char_length = args.get("char_length", 6000)

        # 参数校验
        if not file_name:
            return "❌ [附件读取] 缺少 file 参数，请提供附件文件名。"

        # 在快照中查找附件（支持文件名模糊匹配）
        full_text = ""
        matched_name = ""
        if file_name in self.attachment_fulltext:
            full_text = self.attachment_fulltext[file_name]
            matched_name = file_name
        else:
            # 模糊匹配：用户传文件名可能不完整（如省略扩展名）
            for name, text in self.attachment_fulltext.items():
                if file_name.lower() in name.lower() or name.lower() in file_name.lower():
                    full_text = text
                    matched_name = name
                    break

        if not full_text:
            available = list(self.attachment_fulltext.keys()) if self.attachment_fulltext else "无"
            return (
                f"❌ [附件读取] 未找到附件 '{file_name}'。\n"
                f"可能原因：\n"
                f"  1. 文件名拼写错误（当前快照可用文件：{available}）\n"
                f"  2. 该附件为历史挂载，当前轮未挂载（read_attachment_chunk 仅支持当前轮附件全文检索）\n"
                f"建议：请用户重新挂载该文件后再提问。"
            )

        total_len = len(full_text)

        # ── 模式 3：字符区间检索 ──────────────────────
        if isinstance(char_start, int) and char_start >= 0:
            # 限制 char_length 范围
            try:
                length = int(char_length)
            except (TypeError, ValueError):
                length = 6000
            length = max(500, min(12000, length))
            start = max(0, min(char_start, total_len))
            end = min(start + length, total_len)
            chunk = full_text[start:end]
            return (
                f"📄 [附件片段] 《{matched_name}》字符区间 [{start}, {end})，"
                f"共 {len(chunk)} 字符（全文 {total_len} 字符）：\n\n"
                f"{chunk}"
            )

        # ── 模式 1：题号检索 ──────────────────────────
        if question:
            # 从 question 中提取纯数字题号
            qnum_match = re.search(r"(\d+)", question)
            if qnum_match:
                qnum = qnum_match.group(1)
                # 题号匹配模式：支持 "5." "5、" "5)" "5）" "第5题" "题目5" 等
                # 用正则在全文中查找该题号的边界
                # 关键：题号前必须是行首或换行，避免匹配到正文中的数字
                patterns = [
                    rf"(?:^|\n)\s*第\s*{qnum}\s*题[^\n]*",      # 第5题
                    rf"(?:^|\n)\s*{qnum}\s*[.、)）][^\n]*",       # 5. / 5、 / 5) / 5）
                    rf"(?:^|\n)\s*题目\s*{qnum}[^\n]*",           # 题目5
                    rf"(?:^|\n)\s*Q\s*{qnum}[^\n]*",              # Q5
                ]
                # 找到该题号的起始位置
                target_start = -1
                target_pattern = ""
                for pat in patterns:
                    m = re.search(pat, full_text)
                    if m:
                        target_start = m.start()
                        target_pattern = pat
                        break

                if target_start >= 0:
                    # 找下一道题的起始位置（题号+1 或任意题号模式）
                    next_qnum = str(int(qnum) + 1)
                    next_patterns = [
                        rf"(?:^|\n)\s*第\s*{next_qnum}\s*题",
                        rf"(?:^|\n)\s*{next_qnum}\s*[.、)）]",
                        rf"(?:^|\n)\s*题目\s*{next_qnum}",
                        rf"(?:^|\n)\s*Q\s*{next_qnum}",
                        # 兜底：任意下一题题号
                        rf"(?:^|\n)\s*\d+\s*[.、)）]",
                        rf"(?:^|\n)\s*第\s*\d+\s*题",
                    ]
                    end_pos = total_len
                    for npat in next_patterns:
                        nm = re.search(npat, full_text[target_start + 1:])
                        if nm:
                            end_pos = target_start + 1 + nm.start()
                            break

                    # 同时尝试包含前一道题作为上下文
                    prev_qnum = str(max(1, int(qnum) - 1))
                    prev_patterns = [
                        rf"(?:^|\n)\s*第\s*{prev_qnum}\s*题[^\n]*",
                        rf"(?:^|\n)\s*{prev_qnum}\s*[.、)）][^\n]*",
                    ]
                    prev_start = 0
                    for ppat in prev_patterns:
                        pm = re.search(ppat, full_text[:target_start])
                        if pm:
                            prev_start = pm.start()

                    chunk = full_text[prev_start:end_pos].strip()
                    return (
                        f"📄 [附件片段·题号定位] 《{matched_name}》第 {qnum} 题"
                        f"（含前第 {prev_qnum} 题作为上下文，区间 [{prev_start}, {end_pos})，"
                        f"共 {len(chunk)} 字符）：\n\n"
                        f"{chunk}"
                    )
                else:
                    return (
                        f"⚠️ [附件读取] 在《{matched_name}》中未找到题号 {qnum}。\n"
                        f"可能原因：题号格式不标准（如使用字母 A/B/C 编号）、"
                        f"或该题号超出文档范围。\n"
                        f"建议：尝试用 keyword 参数传入题干关键词，或用 char_start 按顺序浏览。"
                    )
            else:
                return f"⚠️ [附件读取] question 参数 '{question}' 未识别出数字题号，请传入纯数字如 '5' 或 '第5题'。"

        # ── 模式 2：关键词检索 ────────────────────────
        if keyword:
            # 大小写不敏感查找
            idx = full_text.lower().find(keyword.lower())
            if idx >= 0:
                # 前后各 3000 字符
                start = max(0, idx - 3000)
                end = min(total_len, idx + len(keyword) + 3000)
                chunk = full_text[start:end]
                return (
                    f"📄 [附件片段·关键词定位] 《{matched_name}》关键词 '{keyword}'"
                    f"首次出现于位置 {idx}（区间 [{start}, {end})，共 {len(chunk)} 字符）：\n\n"
                    f"{chunk}"
                )
            else:
                return (
                    f"⚠️ [附件读取] 在《{matched_name}》中未找到关键词 '{keyword}'。\n"
                    f"建议：换一个关键词，或用 char_start 按顺序浏览全文。"
                )

        # 三种模式均未指定
        return (
            f"⚠️ [附件读取] 请至少提供一种检索条件：question（题号）、keyword（关键词）或 char_start（起始字符位置）。\n"
            f"当前附件《{matched_name}》全文共 {total_len} 字符。"
        )

    # ──────────────────────────────────────────
    # 知识库语义检索引擎（脱敏 RAG）
    # ──────────────────────────────────────────
    def _get_knowledge_base(self):
        """懒加载知识库实例。依赖未安装时返回 None。"""
        if self._kb_instance is not None:
            return self._kb_instance
        try:
            from yindun.core.knowledge_base import KnowledgeBase
            self._kb_instance = KnowledgeBase()
            if not self._kb_instance.is_available():
                self._kb_instance = None
                return None
            return self._kb_instance
        except Exception:
            self._kb_instance = None
            return None

    # ──────────────────────────────────────────
    # ★ 知识库管理接口（供 GUI 调用）
    # ──────────────────────────────────────────
    def add_to_knowledge_base(self, file_paths, progress_callback=None) -> dict:
        """将文档加入知识库。支持单个路径或路径列表。

        返回 {"total": N, "success": M, "failed": K, "details": [...], "total_chunks": X}
        依赖未就绪时返回 {"error": "...", "available": False}
        """
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        if not file_paths:
            return {"error": "未提供文件路径", "available": True}

        kb = self._get_knowledge_base()
        if kb is None:
            return {
                "error": "知识库功能不可用，请检查依赖（chromadb/langchain-chroma/langchain-ollama）和 Ollama 服务",
                "available": False,
            }
        try:
            return kb.add_documents(file_paths, progress_callback=progress_callback)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "available": True}

    def list_knowledge_base(self) -> dict:
        """列出已入库文档。返回 {"available": bool, "documents": [...], "total_chunks": N}"""
        kb = self._get_knowledge_base()
        if kb is None:
            return {"available": False, "documents": [], "total_chunks": 0,
                    "error": "知识库功能不可用"}
        try:
            docs = kb.list_documents()
            stats = kb.get_stats()
            return {
                "available": True,
                "documents": docs,
                "total_chunks": stats.get("total_chunks", 0),
                "total_documents": stats.get("total_documents", 0),
                "embed_model": stats.get("embed_model", ""),
                "persist_dir": stats.get("persist_dir", ""),
            }
        except Exception as e:
            return {"available": True, "documents": [], "total_chunks": 0,
                    "error": f"{type(e).__name__}: {e}"}

    def remove_from_knowledge_base(self, file_name: str) -> dict:
        """从知识库删除指定文档。返回 {"success": bool, "message": str}"""
        kb = self._get_knowledge_base()
        if kb is None:
            return {"success": False, "message": "知识库功能不可用"}
        try:
            ok = kb.remove_document(file_name)
            return {"success": ok,
                    "message": f"已删除《{file_name}》" if ok else f"未找到《{file_name}》"}
        except Exception as e:
            return {"success": False, "message": f"{type(e).__name__}: {e}"}

    def get_knowledge_base_status(self) -> dict:
        """获取知识库可用性状态。供 GUI 显示依赖检查结果。"""
        try:
            from yindun.core.knowledge_base import KnowledgeBase
            kb = KnowledgeBase()
            available = kb.is_available()
            result = {"available": available}
            # 检查 Ollama 服务
            try:
                import requests
                ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
                r = requests.get(f"{ollama_host}/api/tags", timeout=2)
                if r.status_code == 200:
                    models = [m["name"] for m in r.json().get("models", [])]
                    result["ollama_running"] = True
                    result["has_embed_model"] = any("nomic-embed-text" in m for m in models)
                    result["ollama_models"] = models
                else:
                    result["ollama_running"] = False
                    result["has_embed_model"] = False
            except Exception:
                result["ollama_running"] = False
                result["has_embed_model"] = False
            return result
        except Exception as e:
            return {"available": False, "error": f"{type(e).__name__}: {e}"}

    def _execute_search_knowledge_base(self, args: dict) -> str:
        """
        实现 search_knowledge_base 工具的实际逻辑。
        调用 KnowledgeBase.search() 进行脱敏语义检索。

        返回脱敏片段给 LLM，同时把映射表存入 self._kb_mapping 供输出层还原。
        """
        query = args.get("query", "").strip()
        top_k = args.get("top_k", 4)

        if not query:
            return "❌ [知识库检索] 缺少 query 参数，请提供检索问题。"

        kb = self._get_knowledge_base()
        if kb is None:
            return (
                "❌ [知识库检索] 知识库功能不可用。\n"
                "可能原因：\n"
                "  1. 依赖未安装（请运行: pip install chromadb langchain-chroma langchain-ollama langchain-text-splitters）\n"
                "  2. Ollama 服务未启动\n"
                "  3. 未安装 embedding 模型（请运行: ollama pull nomic-embed-text）"
            )

        try:
            result = kb.search(query, top_k=top_k)
        except Exception as e:
            return f"❌ [知识库检索] 检索失败: {type(e).__name__}: {e}"

        chunks = result.get("chunks", [])
        global_mapping = result.get("global_mapping", {})

        if not chunks:
            return (
                "🔍 [知识库检索] 未找到相关内容。\n"
                "建议：\n"
                "  1. 换一种提问方式\n"
                "  2. 确认文档已入库（可用知识库管理功能查看）\n"
                "  3. 尝试增大 top_k 参数"
            )

        # 合并映射表到本轮全局映射（供输出层 deanonymize 使用）
        # ★ 关键：对占位符加 KB_ 前缀，避免与用户输入脱敏的 box 占位符冲突
        # 例如 [NAME_0] -> [KB_NAME_0]，确保输出层还原时不会误替换
        import re as _re

        def _add_kb_prefix(m):
            # 兼容新旧两套格式：[NAME_0] -> [KB_NAME_0]；[NAME_0_a3f9] -> [KB_NAME_0_a3f9]
            if m.group(3):
                return f"[KB_{m.group(1)}_{m.group(2)}_{m.group(3)}]"
            return f"[KB_{m.group(1)}_{m.group(2)}]"

        kb_prefix_pattern = _re.compile(r"\[([A-Z]+)_(\d+)(?:_([a-z0-9]{4}))?\]")

        for placeholder, real_value in global_mapping.items():
            # 转换占位符：[NAME_0] -> [KB_NAME_0] / [NAME_0_a3f9] -> [KB_NAME_0_a3f9]
            new_placeholder = kb_prefix_pattern.sub(_add_kb_prefix, placeholder)
            self._kb_mapping[new_placeholder] = real_value

        # 格式化检索结果给 LLM（同时把片段内的占位符也加 KB_ 前缀）
        parts = [f"🔍 [知识库检索] 找到 {len(chunks)} 个相关片段（已脱敏）：\n"]
        for i, chunk in enumerate(chunks, 1):
            source = chunk.get("source", "未知")
            score = chunk.get("score", 0)
            content = chunk.get("content", "")
            # 片段内容占位符也加前缀，保持与映射表一致
            content = kb_prefix_pattern.sub(_add_kb_prefix, content)
            parts.append(f"--- 片段 {i}（来源: {source}，相似度: {score:.2f}）---\n{content}\n")

        return "\n".join(parts)

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
            "read_attachment_chunk": "读取附件片段",
            "search_knowledge_base": "知识库检索",
        }
        return name_map.get(tool_name, tool_name)

    # ──────────────────────────────────────────
    # 路径解析：从工具参数中提取并净化目标路径
    # ──────────────────────────────────────────
    def _resolve_target_path(self, args: dict) -> str:
        """
        从工具参数中解析目标路径：
        1. 优先从用户原始输入中提取完整绝对路径
        2. 其次使用 args 中的 target_directory
        3. 默认返回当前沙箱目录
        """
        user_input_path = self._det(self.user_input)
        if user_input_path:
            return user_input_path

        target_dir = ""
        if isinstance(args, dict):
            target_dir = args.get("target_directory", "") or args.get("directory", "") or args.get("path", "") or ""

        if not target_dir or target_dir == "当前沙箱目录":
            return self.sandbox_path

        resolved = self._det(target_dir)
        if resolved:
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
    def _is_sens(path):
        """检测路径是否需要审批（委托给 PolicyManager）"""
        if not path or not isinstance(path, str):
            return True
        pm = PolicyManager()
        result = pm.check("", path)
        # "deny" → 视为敏感（直接拒绝），"confirm" → 视为敏感（需审批）
        return result in ("confirm", "deny")

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
        """清理模型输出中的工具调用残留标签
        修复点：原贪婪正则会把正文里第一个左花括号到最后一个右花括号之间的所有
        内容全部删除（PDF 附件回答常用 JSON 概览，会被整段吃掉导致回答空白）。
        现在改为只清理真正的 tool_call 标签块，绝不删除正文中的 JSON 或花括号。
        """
        if not text:
            return ""
        if not isinstance(text, str):
            return str(text)
        # 用 chr 拼接构造尖括号，避免被外部解析器误判
        _TC_OPEN = chr(60) + "tool_call" + chr(62)
        _TC_CLOSE = chr(60) + "/tool_call" + chr(62)
        _TC_PAIR = re.escape(_TC_OPEN) + r".*?" + re.escape(_TC_CLOSE)
        # 仅清理成对的标签块（DOTALL 让 . 匹配换行）
        t = re.sub(_TC_PAIR, "", text, flags=re.DOTALL)
        # 清理孤立的开/闭标签
        t = re.sub(re.escape(_TC_OPEN) + r"|" + re.escape(_TC_CLOSE), "", t)
        return t.strip()
