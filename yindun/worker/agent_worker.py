# -*- coding: utf-8 -*-
"""
隐盾 V3.0 — 🧠 原生 Tool Calling Agent
彻底抛弃正则解析与结构化字段提取，全面迁移到 LangChain 原生工具调用协议。

核心架构：
1. llm 已经在界面层通过 bind_tools(tools_list) 绑定了所有可用工具
2. ReAct 循环：llm.invoke(messages) -> 检查 tool_calls -> 执行工具 -> 追加 ToolMessage -> 循环
3. 路径解析：在工具执行前独立完成，从 tool_call args 中提取 target_directory
4. 权限熔断：跨目录操作触发 need_confirm 事件等待人工审批
5. 隐私脱敏：PrivacyEngine 在输入/输出层面统一处理

★ 与界面解耦（视图层重构第一步）：本模块**不依赖任何界面框架**（无 Qt）。
  对外的过程事件统一走 `self.bus`（yindun.core.event_bus.EventBus），
  主题见下方 WORKER_EVENTS；界面通过订阅总线接收（Qt 旧界面用 gui/worker_bridge.py 桥接，
  后续 Web 界面直接订阅）。
"""
import os
import queue
import re
import threading
import json
import time
from pathlib import Path
import concurrent.futures
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from yindun.core.event_bus import EventBus
from yindun.core.privacy_engine import PrivacyEngine, _NEW_PLACEHOLDER_RE
from yindun.core.memory_manager import DEFAULT_MAX_HISTORY_TOKENS, SummarizableChatHistory
from yindun.core.audit_log import AuditLog
from yindun.core.policy_manager import PolicyManager

# 推理引擎对外发布的事件主题（界面只需订阅这些）
WORKER_EVENTS = (
    "status",               # str：过程状态文本（状态栏）
    "trace",                # dict：结构化过程轨迹（思考/工具/关键判定），界面渲染成时间线
    "intermediate_result",  # str：中间结果（渐进展示）
    "finished",             # str：最终回答
    "error",                # str：错误信息（已翻译为友好文案）
    "need_confirm",         # dict：{name, args, path} 请求人工审批
    "approval_expired",     # None：审批超时，界面应关闭残留弹窗
)

# ──────────────────────────────────────────────
# Agent 系统提示词：告诉模型它能做什么，以及工具使用规范
#
# ★ 结构（V3.6.3 重构，目标是"别让 agent 拖累模型能力"）：
#   · `_SYSTEM_PROMPT` = **常驻基线**，必须短、必须把三条硬规则放在最前面；
#   · `_ATTACHMENT_RULES` = 附件规范（题号定位 / 长文档分块 / OCR 话术等），
#     **只在真的挂了附件时**追加。
#   为什么这么拆：实测（本机 qwen3.5:4b，A/B 对照）——把附件规范常驻在提示词里时，
#   一句"读一下沙箱里的 x.txt"它**一次工具都不调**，还回出"我会严格按照规范使用
#   function call 格式"这种自说自话；换成精简基线后**立刻正常调用工具**。
#   小模型的注意力就这么点，常驻的长规则会把"现在该干什么"淹没。
# ──────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "你是「隐盾」，一个在**本机沙箱内**工作的中文智能助手。\n\n"
    "【三条硬规则（最高优先级，任何情况下都要遵守）】\n"
    "1. **对话历史里已经有的信息，直接用**——用户上一轮告诉过你、或你上一轮答过的内容，"
    "直接回答，禁止说'请上传文件 / 请提供资料 / 我没有记忆'。\n"
    "2. **涉及文件、目录、项目、命令的任务，必须先调用工具再回答**，不要凭猜测或常识作答。\n"
    "3. **工具失败或返回警告/错误时，如实告诉用户失败原因和下一步建议**，"
    "严禁编造文件内容、数据或执行结果。读不到就说读不到。\n\n"
    "【你能调用的工具】\n"
    "1. list_local_files(target_directory) —— 列出目录下的文件\n"
    "2. read_local_file(filename, target_directory) —— 读取文件内容\n"
    "3. create_local_file(filename, content, target_directory) —— 新建文件\n"
    "4. modify_local_file(filename, content, target_directory) —— 修改文件\n"
    "5. delete_local_file(filename, target_directory) —— 删除文件\n"
    "6. run_local_command(command) —— 执行白名单内的本地命令\n"
    "7. analyze_project(target) —— 递归分析项目结构与关键代码\n"
    "8. search_in_files(keyword, target_directory) —— 在文件中搜索内容\n"
    "9. search_knowledge_base(query, top_k=4) —— 在本地知识库做语义检索（合同/报表/规章等）\n"
    "10. read_attachment_chunk(file, question/keyword/char_start) —— 读取长附件的指定片段\n\n"
    "【工具使用要点】\n"
    "1. 用户给了**完整绝对路径**（如 E:\\qwen\\a.txt）就原样填进 target_directory；"
    "只给文件名时 target_directory 留空（默认当前工作目录）。\n"
    "2. 可以连续调用多个工具（例如先列目录、再读文件、最后总结）；拿到结果后再给结论。\n"
    "3. 知识库检索用 search_knowledge_base(query='用户的原话问题')；"
    "它返回的内容已脱敏（形如 [KB_PHONE_0]），你照常作答即可，占位符会自动还原给用户。\n\n"
    "【回答要求】\n"
    "1. 用中文回答，直接给结论；不要描述工具调用过程，也不要把工具调用写成文字（要用真正的工具调用格式）。\n"
    "2. 工具结果与你的推测冲突时，**以工具结果为准**。\n"
    "3. 工具失败时，把错误翻译成人话并给出可执行的下一步（换路径、换文件名、先列目录等）。\n"
    "4. 不确定的地方如实说'不确定'，不要编。\n"
)

# 附件相关规范：仅在"本轮或历史确实挂了附件"时追加（见下方拼装处）。
# 这里保留完整规则（题号定位 / 长文档分块 / OCR 话术 / 跨轮附件），因为挂附件时它们确实是必需的；
# 但**不再常驻**——常驻会把小模型的注意力从"该调工具了"上引开（实测见文件头注释）。
_ATTACHMENT_RULES = (
    "\n【附件文档处理规范】（当前会话挂有附件，按此执行）\n"
    "1. 类型识别：先判断附件类型（PDF/Word/Excel/TXT/音频转写），并推断文档主题。\n"
    "2. 结构化提取（非音频）：提取标题与章节层级；表格用 Markdown 表格还原（保留行列对应）；"
    "列表/编号项保持原序。\n"
    "3. 音频转写：推断说话人角色并用 [说话人]: 文本 重组；识别问答、决议、待办；"
    "转写无标点或断句混乱时先恢复句子边界。\n"
    "4. 多维度分析（按用户意图选择）：摘要（3 句核心 + 要点）；关键信息抽取（人名/机构/日期/金额/"
    "条款编号/数据指标）；风险扫描（标注敏感数据出现位置）；对比问答（引用原文片段为证，标注'见第X段'）。\n"
    "5. 输出格式：先给【附件概览】（类型/主题/字数/敏感项计数），再回答用户问题。\n"
    "6. 局限告知：若表格/图片/公式导致解析缺失，明确说明'该部分未解析到'，不要编造内容。\n"
    "   OCR 话术规则（禁止编造失败）：\n"
    "   - 出现 [本文档部分页面通过 OCR 识别，可能存在识别误差] = OCR **成功**，可正常引用，"
    "必要时标注'（OCR 识别，可能存在误差）'。\n"
    "   - 只有明确标注 [本页为扫描图片/空白页，OCR 未识别到文字内容] 的页，才说'该页未识别到文字'；"
    "不要写成'OCR 技术失败/未通过 OCR 识别'。\n"
    "   - 附件标注 [OCR 引擎未安装] 时，提示用户运行 `pip install rapidocr-onnxruntime` 后重新挂载。\n"
    "7. 跨轮附件追问：用户用'上面/刚才/这个文档/文中/它/这份'追问时，**回溯对话历史里的附件内容作答**；"
    "不要输出【附件概览】，结尾用一行注明'（参考：历史挂载文档《文件名》）'。\n"
    "   - 禁止以'您没有上传文件''请上传文件后再提问'为由拒绝。\n"
    "   - 若确实取不到（历史被压缩），**先用 read_attachment_chunk 再取一次**；"
    "仍取不到才如实说明'未能取到《文件名》的原文，请重新挂载'。\n"
    "8. 题号定位（试卷/题库场景，禁止跳过定位直接作答）：\n"
    "   A. 先在上下文找 [Q<题号>] 锚点；找到就输出一行【定位】Q<题号> 原文：「该题完整题干（含选项）」。\n"
    "   B. 再输出【答案】与【解析】。\n"
    "   C. 上下文里没有锚点时，必须调用 read_attachment_chunk(file='文件名', question='题号') 检索后再作答。\n"
    "   D. 工具也没找到该题号时，如实说明'未在文档中定位到第 N 题'，禁止编造题干与答案。\n"
    "   E. 问'第5题到第8题'要逐题执行；问关键词题用 read_attachment_chunk(file=…, keyword=…)。\n"
    "9. 长文档分块：附件末尾出现【长文档分块提示】时说明已截断，需要的内容不在已展示部分就必须调用工具：\n"
    "   a. 题号：read_attachment_chunk(file='文件名', question='5')\n"
    "   b. 关键词：read_attachment_chunk(file='文件名', keyword='三角函数')\n"
    "   c. 字符区间：read_attachment_chunk(file='文件名', char_start=30000)（char_length 500~12000，默认 6000）\n"
    "   不要回答'文档中未找到'就结束，也不要凭题号猜题干。\n"
)


class _EventTopic:
    """把 `self.status.emit(x)` 这类调用面映射到事件总线。

    内核不再依赖 Qt；保留 `.emit(payload)` 形态是为了让既有调用点零改动，
    而 `bus` 才是对外契约（界面订阅总线，见 WORKER_EVENTS）。
    """

    __slots__ = ("_bus", "_topic")

    def __init__(self, bus: EventBus, topic: str):
        self._bus = bus
        self._topic = topic

    def emit(self, payload=None) -> None:
        self._bus.emit(self._topic, payload)


class Worker:
    """Agent Worker：基于 LangChain 原生 Tool Calling 协议（与界面框架无关）"""

    # 跨轮映射表上限：超出后按插入顺序 FIFO 淘汰最早的条目
    _BOX_MAPPING_MAX = 300

    # ──────────────────────────────────────────
    # 取消执行机制
    # ──────────────────────────────────────────
    def __init__(self):
        # 事件总线：界面（Qt 桥 / Web 桥）订阅它接收推理过程事件
        self.bus = EventBus()
        # 兼容别名：self.status.emit(...) 等价于 self.bus.emit("status", ...)
        self.status = _EventTopic(self.bus, "status")
        self.intermediate_result = _EventTopic(self.bus, "intermediate_result")
        self.finished = _EventTopic(self.bus, "finished")
        self.error = _EventTopic(self.bus, "error")
        self.need_confirm = _EventTopic(self.bus, "need_confirm")
        self.approval_expired = _EventTopic(self.bus, "approval_expired")
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

    def _request_approval(self, name, args, path, timeout: float = 300.0) -> bool:
        """统一的敏感操作人工审批等待。

        先 clear 审批事件再 emit need_confirm（修正 clear/emit 顺序，消除
        GUI 先响应导致批准被误清的竞态）；随后用联合等待（审批事件 / 取消事件
        / 超时）保证任何情形都能被唤醒返回，且不会被取消/超时永久卡死。

        超时不宜过短：人类安全员需要真实阅读审批内容，60 秒内读完弹窗并
        点击极易超时被静默驳回，且超时后弹窗仍留在屏幕上，用户再点"授权"
        会误以为已放行（实际已被驳回）。默认 300 秒，并在超时时广播
        approval_expired 关闭残留弹窗。

        返回 True=批准；False=驳回/取消/超时（默认 timeout 300s）。
        """
        self._approved.clear()
        payload = {"name": name, "args": args, "path": path}
        # 防盲签（红队评审整改）：`python x.py` 这类命令，弹窗只显示命令串看不出脚本做什么——
        # 审批前把脚本开头一段读出来一并展示（读不到就不加该字段，绝不影响审批流程）。
        try:
            if isinstance(args, dict):
                from yindun.core.file_tools import preview_command_script
                preview = preview_command_script(str(args.get("command") or ""),
                                                 getattr(self, "sandbox_path", "") or "")
                if preview:
                    payload["script_preview"] = preview
        except Exception:
            pass
        self.need_confirm.emit(payload)
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
                self.status.emit("🚨 人工审批超时（5 分钟未响应），已按驳回处理")
                self.approval_expired.emit()  # 关闭残留审批弹窗，防止死弹窗误导
                break
        self._approved_val = approved
        return approved

    def _invoke_llm_with_cancel_check(self, messages, check_interval=0.5):
        """
        可中断的 LLM 调用：使用线程池包装 llm.invoke()，
        每隔 check_interval 秒检查取消标志，实现即时响应取消请求。

        ★ 注意不要用 `with ThreadPoolExecutor(...)`：with 退出时会执行
        shutdown(wait=True)，在取消分支上会阻塞到模型生成完毕才抛出取消，
        使"0.5 秒取消"退化为 UI 假象（后台仍占满算力、worker 线程无法及时退出）。
        这里改为显式 shutdown(wait=False, cancel_futures=True)：worker 线程立即
        响应取消返回，模型请求在后台自然结束、结果被丢弃。
        """
        if self._cancel_requested.is_set():
            raise KeyboardInterrupt("用户取消")

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        _t0 = time.time()
        try:
            future = executor.submit(self.llm.invoke, messages)
            while not future.done():
                if self._cancel_requested.is_set():
                    # 用户点击取消：立即返回，不等待在途的模型请求
                    self.status.emit("[已取消] 推理已被用户中断")
                    future.cancel()
                    raise KeyboardInterrupt("用户取消")
                # 等待一小段时间再检查（避免频繁轮询）
                threading.Event().wait(check_interval)
            _result = future.result()
            # 思考耗时：主流 agent 会把这段显性化，用户据此判断"它在认真想还是在卡住"
            self._trace("thinking", "思考", f"{len(messages)} 条上下文", "ok",
                        ms=int((time.time() - _t0) * 1000))
            return _result
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _invoke_llm_streaming(self, messages, check_interval=0.5, throttle_ms=80):
        """流式调用 LLM：生成中的文本**边生成边推送**，首字延迟从"整段生成完"降到"第一个字"。

        为什么值得做（V3.6.2，路线图 C-12）：此前是 `llm.invoke` 一次性拿整段——4B 模型一段
        百来字的回答要等十几秒，界面只有等待点；"它在想还是卡住了"的体感差异极大。

        做法与边界：
          · 用 `self.llm.stream()` 逐块消费，块内容**先还原占位符再推送**（模型看到的是占位符，
            用户该看到真值；跨块的半个占位符先扣住不推，避免界面闪现 `[NAME_0_a`）；
          · 推送走既有 `intermediate_result` 事件（语义就是"渐进展示"），前端已有渲染分支，
            **不新增事件类型**；`finished` 仍是"收尾一次性完整文本"，语义不变；
          · 合并后的消息（工具调用参数也在块里）交给后续 ReAct 逻辑——流式不会漏掉工具调用；
          · 取消语义与 `_invoke_llm_with_cancel_check` 一致：0.5 秒内响应，不等模型生成完；
          · `stream()` 不可用（某些自定义算力端点）时自动回落整段 invoke。
        """
        if self._cancel_requested.is_set():
            raise KeyboardInterrupt("用户取消")
        if not hasattr(self.llm, "stream"):
            return self._invoke_llm_with_cancel_check(messages, check_interval)

        chunk_queue: "queue.Queue" = queue.Queue()
        _t0 = time.time()
        merged = None
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def _producer():
            try:
                for chunk in self.llm.stream(messages):
                    chunk_queue.put(("chunk", chunk))
                chunk_queue.put(("done", None))
            except Exception as exc:                     # noqa: BLE001 —— 翻译成人话交给主线程
                chunk_queue.put(("error", exc))

        try:
            executor.submit(_producer)
            raw_so_far = ""
            last_push = 0.0
            while True:
                try:
                    kind, payload = chunk_queue.get(timeout=check_interval)
                except queue.Empty:
                    if self._cancel_requested.is_set():
                        self.status.emit("[已取消] 推理已被用户中断")
                        raise KeyboardInterrupt("用户取消")
                    continue
                if kind == "error":
                    raise payload
                if kind == "done":
                    break
                merged = payload if merged is None else (merged + payload)
                piece = getattr(payload, "content", "") or ""
                if piece:
                    raw_so_far += piece
                    now = time.time()
                    if (now - last_push) * 1000 >= throttle_ms:
                        last_push = now
                        self._push_partial(raw_so_far)
                if self._cancel_requested.is_set():
                    self.status.emit("[已取消] 推理已被用户中断")
                    raise KeyboardInterrupt("用户取消")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if merged is None:
            return self._invoke_llm_with_cancel_check(messages, check_interval)
        self._push_partial(raw_so_far, final=True)
        self._trace("thinking", "思考", f"{len(messages)} 条上下文", "ok",
                    ms=int((time.time() - _t0) * 1000))
        return merged

    def _push_partial(self, raw_text: str, final: bool = False) -> None:
        """把"生成中的原文"还原占位符后推给界面（跨块的半个占位符先扣住不推）。"""
        try:
            visible = self._clean(self._restore_for_display(raw_text, final=final))
        except Exception:
            return                      # 渐进展示是附加能力，出错绝不影响推理
        if visible:
            self.intermediate_result.emit(visible)

    @staticmethod
    def _restore_partial(raw_text: str, hold_tail: bool = True) -> str:
        """把流式原文里的占位符长回来，并（默认）扣住结尾可能是"半个占位符"的部分。

        例：结尾是 `…[NAME_0_a` 时先不推——否则界面会短暂闪出半截占位符；
        等后续块补齐再一起推，用户看到的永远是完整占位符或完整真值。
        """
        text = raw_text
        if hold_tail:
            bracket = text.rfind("[")
            if bracket >= 0 and "]" not in text[bracket:]:
                text = text[:bracket]
        return text

    def _restore_for_display(self, raw_text: str, final: bool = False) -> str:
        """流式文本 → 界面可见文本：先扣半个占位符，再用本轮映射还原真值。"""
        text = self._restore_partial(raw_text, hold_tail=not final)
        kb_map = getattr(self, "_kb_mapping", None) or {}
        box_map = getattr(self, "_box_mapping", None) or {}
        mapping = dict(kb_map)
        mapping.update(box_map)
        if not mapping:
            return text
        try:
            engine = getattr(self, "_partial_engine", None)
            if engine is None:
                from yindun.core.privacy_engine import PrivacyEngine as _PE
                engine = _PE()
                self._partial_engine = engine
            return engine.deanonymize(text, mapping, strict=False)
        except Exception:
            for placeholder, value in mapping.items():
                if placeholder in text:
                    text = text.replace(placeholder, str(value))
            return text

    def _trace(self, kind: str, name: str, detail: str = "", status: str = "",
               ms: int = 0) -> None:
        """发一条结构化过程轨迹（best-effort：界面没订阅也不影响推理）。

        kind: thinking 思考 / tool 工具 / judge 关键判定（隐私网关、laya 风险等）
        界面据此渲染成时间线；落库由服务层负责，刷新后仍在。
        """
        try:
            tid = int(getattr(self, "_trace_seq", 0)) + 1
            self._trace_seq = tid
            self.bus.emit("trace", {"id": tid, "kind": kind, "name": str(name)[:80],
                                    "detail": str(detail)[:200], "status": status,
                                    "ms": int(ms or 0)})
        except Exception:
            pass          # 轨迹是"看得见"的附加能力，绝不因为它打断推理

    # ──────────────────────────────────────────
    # 主入口：根据模式执行不同逻辑
    # ──────────────────────────────────────────
    def run(self):
        try:
            # ★ 每轮对话开始时清空知识库映射表（上轮的已用后即焚）
            self._kb_mapping = {}
            # ★ 工具调用短路缓存与"已深化"标记同样按轮次隔离：跨轮复用会把
            #   "用户让我再读一次这个文件"也短路掉（文件可能已经变了）
            self._tool_fp_cache = {}
            self._deepen_used = False
            # ★ 恢复跨轮脱敏映射表（从会话数据恢复，保证历史占位符可还原）
            if self._box_mapping_restore:
                self._box_mapping = dict(self._box_mapping_restore)
                self._box_mapping_restore.clear()
            memory = SummarizableChatHistory.from_dict_list(
                self.messages_snapshot, max_tokens=DEFAULT_MAX_HISTORY_TOKENS
            )
            engine = PrivacyEngine()
            self._engine = engine  # ★ 新增：供 _anonymize_tool_output 复用

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
                        # 关键判定显性化：用户看得见"网关认出了什么、替换了什么"
                        _summary = "、".join(f"{k}×{v}" for k, v in list(_stats.items())[:6])
                        self._trace("judge", "隐私隔离网关",
                                    f"识别到 {len(_stats)} 类敏感实体（{_summary}），已替换为占位符",
                                    "ok")
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

            # ★ 知识库有文档时，禁止走"跳过工具的快速直答"：否则 search_knowledge_base
            #   永远不会被调用，模型会凭空作答（甚至误判为"拒绝提供敏感信息"）。
            #   仅在"本会走快速直答"时才探测 KB，避免给普通问答增加开销。
            _is_simple = self._is_simple_question(ai_input)
            _kb_has_docs = False
            if _is_simple and not _has_attachment and not _is_audio_request:
                _kb = self._get_knowledge_base()
                if _kb is not None:
                    try:
                        _kb_has_docs = _kb.get_stats().get("total_documents", 0) > 0
                    except Exception:
                        _kb_has_docs = False
                if _kb_has_docs:
                    ai_input = (
                        "[提示] 本地知识库已入库文档；若本问题可能涉及已入库的合同/报表/员工信息等内容，"
                        "请先调用 search_knowledge_base 工具检索后再作答。\n\n" + ai_input
                    )

            if (_is_simple or _is_audio_request) and not _has_attachment and not _kb_has_docs:
                self.status.emit("[快速回答] 直接回答问题...")
                # ★ 进度事件（真机事故整改）：聊天区的"正在思考"等待动画只挂在
                #   intermediate_result 上（前端 ensureStreamBubble）；直答路径以前一条都不发，
                #   用户在整段模型生成期间**在对话区看不到任何东西**（反馈原话：
                #   "等了好久了没回复，也没有思考中之类的动画"）。这里复用既有通道推一条占位，
                #   真回答到达时由 finished 整段覆盖，不新增事件类型。
                self.intermediate_result.emit("正在生成回答…")
                reply_msg = self._direct_answer_message(ai_input, memory)
                reply = self._clean(getattr(reply_msg, "content", "") or "")
                # ★ 空产出兜底（真机事故，V3.6.7）：实测 qwen3.5:4b 问"我的桌面有几张图片？"
                #   会返回 content='' + tool_calls=[list_local_files(...)]——只取 content 就等于
                #   把模型的工具调用静默丢掉，用户拿到一个**空气泡**，会话里那条空助手消息
                #   下次加载还会被丢掉（只剩自己的提问）。两种空产出必须分开处理：
                #   · 模型要工具 → 转到下面的工具推理循环，让它真的去调（并如实说明调不到）；
                #   · 什么都没有 → 给一条可读的说明，绝不发空白。
                if reply:
                    # ★ 先留一份**还原前**的文本给审计链（V3.6.7）：审计链落盘必须是占位符形态，
                    #   而下面这行会把 reply 覆盖成还原后的明文——顺序反了就等于把敏感内容写进审计。
                    _raw_for_audit = reply
                    if self.privacy_shield and (box or self._box_mapping):
                        # ★ 合并跨轮映射表：用「历史映射 + 本轮映射」还原，避免历史占位符无法还原
                        merged_mapping = {**self._box_mapping, **box}
                        reply = engine.deanonymize(reply, merged_mapping)
                        if box:
                            self._box_mapping.update(box)  # 累积本轮映射，供后续轮次还原
                    memory.update_with_context_result(self.user_input, reply)
                    self.result_messages = memory.to_dict_list()
                    self._ensure_reply_persisted(reply)
                    # ★ 审计链只存**掩码态**（V3.6.7 补齐）：直答快路径以前只记 llm_input、
                    #   不记 llm_output，事故那轮在审计链上解释不了"为什么没有回复"。
                    #   但绝不能改记还原后的明文——落盘审计链必须是占位符形态（隐私不变量）。
                    try:
                        AuditLog().log_llm_output(_raw_for_audit, preview=_raw_for_audit[:200])
                    except Exception:
                        pass
                    self.finished.emit(reply)
                    return
                if getattr(reply_msg, "tool_calls", None):
                    # 直答这条车道本来就不执行工具；模型却明确要调工具 → 换到工具推理循环，
                    # 不把它的工具调用吞掉（用户问的是真实世界的信息，答案只能由工具给出）。
                    self.status.emit("[快速回答] 模型需要调用工具，转入工具推理...")
                    self._trace("thinking", "改用工具推理",
                                "直答未产出文字：模型要求调用工具", "ok")
                else:
                    # 既没文字也没工具调用：如实说明这一轮没拿到回答，并落库（不再是空气泡）。
                    self.status.emit("[快速回答] 模型没有给出回答，已如实说明")
                    _no_answer = (
                        "抱歉，模型这一轮没有给出回答。\n"
                        "本机小模型偶发空回复（实测约每 5 次出现 1 次，与本机算力负载有关），"
                        "不是你的操作有问题。\n"
                        "可以试试：再发一次、把问题问得更具体、切换到「深度思考」模式，或换用更大的模型。"
                    )
                    if self.privacy_shield and (box or self._box_mapping):
                        self._box_mapping.update(box)
                    memory.update_with_context_result(self.user_input, _no_answer)
                    self.result_messages = memory.to_dict_list()
                    self._ensure_reply_persisted(_no_answer)
                    self.finished.emit(_no_answer)
                    return

            # ──────────────────────────────────────────
            # ★★★ 关键修复：当用户输入包含完整路径和分析关键词时，
            #     强制调用 analyze_project 工具，不依赖 LLM 的工具调用决策
            #     （LLM 在快速模式下经常偷懒不调用工具而直接编造回答）
            #     注意：音频文件已在前面被拦截，不会到达这里
            # ──────────────────────────────────────────
            forced_tool_result = None
            forced_tool_name = None
            forced_tool_args = None      # 注入 ToolMessage 时需要回填对应的 tool_call 参数
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
                            _raw = self._execute_with_approval("analyze_project", _args)
                            forced_tool_result, _ = self._anonymize_tool_output(_raw, "analyze_project")
                            forced_tool_name = "analyze_project"
                            forced_tool_args = dict(_args)
                        elif (_has_list or _has_read) and os.path.isdir(_resolved_path):
                            if "list_local_files" in self.tools_map:
                                self.status.emit(f"[工具执行] 列出目录: {_resolved_path}")
                                _args = {"target_directory": _resolved_path}
                                _raw = self._execute_with_approval("list_local_files", _args)
                                forced_tool_result, _ = self._anonymize_tool_output(_raw, "list_local_files")
                                forced_tool_name = "list_local_files"
                                forced_tool_args = dict(_args)
                    except Exception as _e:
                        forced_tool_result = f"[工具执行出错] {_e}"
                    self.tool_call_count += 1

            # ★ 补充强制分支：**没有显式路径**、但明确在问"目录/沙箱里有什么"时，
            #   也强制走 list_local_files。实测 7B 模型在这类请求下会不调工具、
            #   直接编造一段回答（"没有生成报告的功能"之类），对用户来说等于答非所问。
            #   触发条件收紧为"列举动词 + 目录类名词"同时命中，避免劫持普通问答。
            _list_verb = any(w in self.user_input for w in
                             ["列出", "列一下", "看看", "查看", "有哪些", "有什么", "显示"])
            _dir_noun = any(w in self.user_input for w in ["文件", "文件夹", "目录", "沙箱"])
            if forced_tool_result is None and not _path_match and _list_verb and _dir_noun:
                if "list_local_files" in self.tools_map:
                    self.status.emit(f"[工具执行] 列出沙箱目录: {self.sandbox_path}")
                    try:
                        _args = {"target_directory": self.sandbox_path}
                        _raw = self._execute_with_approval("list_local_files", _args)
                        forced_tool_result, _ = self._anonymize_tool_output(_raw, "list_local_files")
                        forced_tool_name = "list_local_files"
                        forced_tool_args = dict(_args)
                    except Exception as _e:
                        forced_tool_result = f"[工具执行出错] {_e}"
                    self.tool_call_count += 1

            # ★ 第三个强制分支（V3.6.3，"别让 agent 成为短板"）：**问某个具体文件的内容**
            #   但没有给绝对路径时（"读一下沙箱里的 客户名单.txt""demo_contract.txt 里写了什么"），
            #   由宿主直接把文件读出来喂给模型。实测：4B 模型这类请求**经常不调工具**，
            #   而是回一句"请上传文件/我没看到附件"——工具明明在手边，用户却拿不到数据。
            #   触发条件收紧为"读类动词 + 带扩展名的文件名"，避免劫持普通问答。
            _read_verb = any(w in self.user_input for w in
                             ["读取", "读一下", "读读", "看看", "查看", "打开", "内容",
                              "写了什么", "里面是什么", "有什么内容"])
            _file_name = re.search(
                r'([\w\u4e00-\u9fa5\-. ]{1,60}?\.(?:txt|csv|md|json|log|py|html?|xlsx?|docx?|pdf))',
                self.user_input, re.I)
            if (forced_tool_result is None and _file_name and _read_verb
                    and "read_local_file" in self.tools_map):
                _name = _file_name.group(1).strip()
                try:
                    self.status.emit(f"[工具执行] 读取文件: {_name}")
                    _args = {"filename": _name, "target_directory": self.sandbox_path}
                    _raw = self._execute_with_approval("read_local_file", _args)
                    forced_tool_result, _ = self._anonymize_tool_output(_raw, "read_local_file")
                    forced_tool_name = "read_local_file"
                    forced_tool_args = dict(_args)
                except Exception as _e:
                    forced_tool_result = f"[工具执行出错] {_e}"
                self.tool_call_count += 1

            # 进入 ReAct 循环（快速模式和深度模式都走这个循环，区别在于最大轮次）
            # 如果已经强制执行了工具，把结果作为上下文注入，让 LLM 做总结
            if "深度" in self.think_mode:
                max_rounds = self.think_depth * 2  # depth 3 → 6轮, depth 1 → 2轮, depth 10 → 20轮
            else:
                # 快速模式：轮次只在"模型主动要工具"时才消耗，所以给到 6 轮不会变慢，
                # 却能避免"读两个文件 + 写一个文件"这类常见任务在第 3 轮被硬截断
                # （截断后走"已达最大轮次"分支，答案质量明显下降，看起来就像坏了）。
                max_rounds = 6

            final_reply = self._run_react_loop(
                ai_input, box, memory, engine, max_rounds,
                forced_tool_result=forced_tool_result,
                forced_tool_name=forced_tool_name,
                forced_tool_args=forced_tool_args,
            )

            _reply_before_restore = final_reply  # ★ 脱敏态副本：审计记录用，避免还原后明文落盘
            if self.privacy_shield and (box or self._box_mapping):
                # ★ 合并跨轮映射表：用「历史映射 + 本轮映射」还原，避免历史占位符无法还原
                merged_mapping = {**self._box_mapping, **box}
                final_reply = engine.deanonymize(final_reply, merged_mapping)
                if box:
                    self._box_mapping.update(box)  # 累积本轮映射，供后续轮次还原
                # ★ 审计：记录隐私还原（统计还原的实体类型数）
                try:
                    # box 结构：{占位符: 真实值}，统计各类型数量
                    _type_counts = {}
                    for _ph in box.keys():
                        _m = _NEW_PLACEHOLDER_RE.match(_ph)  # 新格式 [TYPE_N_nonce]
                        if _m:
                            _t = _m.group(1)
                        else:
                            _m2 = re.match(r"\[([A-Z_]+?)(?:_\d+)?\]", _ph)  # 兼容旧格式 [TYPE_N]
                            _t = _m2.group(1) if _m2 else "OTHER"
                        _type_counts[_t] = _type_counts.get(_t, 0) + 1
                    if _type_counts:
                        AuditLog().log_privacy_batch(_type_counts, "restored")
                except Exception:
                    pass
            # ★ 合并知识库映射表：如果本轮调用了 search_knowledge_base，
            # LLM 回答中会含知识库检索的占位符，需要用 _kb_mapping 还原。
            # ★ 用 restore_kb_placeholders 而不是精确替换（V3.6.0）：模型会重写占位符编号、
            #   甚至丢掉 nonce，精确匹配必然漏还原（用户看到答案里残留 [KB_…]）。
            if self._kb_mapping:
                final_reply, _kb_unresolved = engine.restore_kb_placeholders(final_reply, self._kb_mapping)
                self._kb_mapping.clear()  # 用后即焚
                if _kb_unresolved:
                    # 不静默：告诉用户有几处没能还原，以及为什么（宁可少还原，也不猜错值）
                    final_reply += (f"\n\n（提示：有 {_kb_unresolved} 处知识库占位符未能还原——"
                                    "模型改动了占位符编号，无法唯一确定对应实体。可在知识库面板按来源核对。）")
            # ★ 反编造兜底（V3.6.3）：本轮没有任何成功的工具结果、回答却在罗列"文件内容"时，
            #   如实加一句"未经核实"。放在还原之后、播报之前——用户看到的就是最终文本。
            final_reply = self._guard_ungrounded_dump(
                final_reply,
                bool(getattr(self, "_turn_tool_success", False)),
                list(getattr(self, "_turn_tool_names", []) or []),
            )
            # ★ 审计：记录最终还原后的 LLM 输出（展示给用户的版本）
            try:
                AuditLog().log_llm_output(_reply_before_restore, preview=_reply_before_restore[:200])
            except Exception:
                pass

            # 注：ReAct 循环内部已经通过 update_with_full_chain 保存完整工具调用链
            # 这里只需要把 final_reply 的 de-anonymized 版本 emit 给用户
            self.finished.emit(final_reply)

        except KeyboardInterrupt:
            # 用户主动取消，正常结束（不是错误）
            self.status.emit("[已取消] 推理已被用户中断")
            # ★ 取消也要落库（V3.6.7）：以前这条提示只经 finished 飘一下，会话里既没有回复
            #   也没有"这轮被取消"的痕迹——重启后那一轮看起来像没发生过（只有用户提问）。
            self._persist_terminal_reply("推理已取消。")
            self.finished.emit("推理已取消。")
        except Exception as e:
            friendly = self._translate_error(e)
            # ★ 失败必须留下用户看得见、且能落库的回复（V3.6.7 真机整改）。旧写法是
            #   `self.result_messages = self.messages_snapshot`——把消息链回退成"本轮之前的历史"，
            #   等于服务层写库时**把用户刚发的那条提问一起删掉**，而失败说明只经 error 事件
            #   在界面上闪一下（重启后什么都没有）。现在把"用户提问 + 失败说明"写进链尾，
            #   界面和会话记录口径一致（落库仍走脱敏后的文本，不放松任何隐私不变量）。
            self._persist_terminal_reply(friendly)
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
                         forced_tool_result=None, forced_tool_name=None,
                         forced_tool_args=None) -> str:
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
        # ★ V3.6.3：附件规范也**只在真挂了附件时**才追加——常驻会把小模型的注意力
        #   从"该调工具了"上引开（同一问题：常驻时长规则 → 一次工具都不调；精简后 → 正常调用）。
        _attachment_index = self._build_attachment_index()
        _system_content = _SYSTEM_PROMPT
        if _attachment_index or getattr(self, "attachment_fulltext", None):
            _system_content = _SYSTEM_PROMPT + _ATTACHMENT_RULES
        if _attachment_index:
            _system_content = _system_content + "\n" + _attachment_index
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
        # ★ V3.6.3：本轮是否存在"成功的工具结果" + 本轮调用过哪些工具（反编造兜底要用）
        self._turn_tool_success = False
        self._turn_tool_names = []
        if forced_tool_result and forced_tool_name:
            # ★ 协议要求：ToolMessage 必须回应一条"声明了对应 tool_call 的 AIMessage"。
            #   旧实现注入的是无 tool_calls 的 AIMessage + 孤立 ToolMessage —— 对模型属非法序列，
            #   实测 7B 模型会因此完全忽略工具结果、答非所问（"请提供文件名或目录"）。
            # ★ V3.6.3 两处补强（真机实测暴露）：
            #   ① 失败的强制结果也要带"禁止编造"指令——否则模型会拿**历史里上一轮**的文件内容
            #      当成本轮答案（实测：问"读不存在的文件"，它交出一份上一轮文件的汇总报告）；
            #   ② 成功的强制结果要记进 `_turn_tool_success`，否则反编造兜底会把正常回答误判成"未核实"。
            _forced_failed = any(mark in str(forced_tool_result)[:200] for mark in self._FAILURE_MARKS)
            _forced_content = self._with_failure_directive(forced_tool_result)
            if _forced_failed:
                _forced_content += ("\n（注意：**上面这份失败结果才是本轮的工具结果**；"
                                    "不要引用历史对话里其它文件的内容来回答本轮问题。）")
                self._turn_tool_names.append(forced_tool_name)
            else:
                self._turn_tool_success = True
                self._turn_tool_names.append(forced_tool_name)
            messages.append(AIMessage(
                content="",
                tool_calls=[{
                    "name": forced_tool_name,
                    "args": dict(forced_tool_args or {}),
                    "id": "forced_tool_001",
                }],
            ))
            messages.append(ToolMessage(
                content=f"[{forced_tool_name} 执行结果]\n{_forced_content}",
                tool_call_id="forced_tool_001"
            ))
            last_tool_result = str(forced_tool_result)[:2000]
            has_executed_tool = True
            self.status.emit("[工具执行完成] 已读取/扫描，准备总结…")
            # 渐进输出：把工具结果先发给用户看
            self.intermediate_result.emit("正在为您整理工具返回的内容…")
        else:
            last_tool_result = ""

        # 记录本轮产生的所有中间消息（排除初始系统/上下文），用于后续持久化
        round_start_idx = len(messages) - 1  # 从 HumanMessage 开始记录
        round_count = 0
        consecutive_failures = 0
        # ★ 无进展轮数：连续多少轮"工具没成功 + 模型也没给出可交付文字"（见循环末尾的收敛判定）。
        #   与 consecutive_failures 分开统计：后者只认抛异常/被拦截，前者还覆盖"工具正常返回但
        #   结果是警告/越界/重复调用"这类声明式失败——它才是把人晾在原地的那一种。
        no_progress_rounds = 0
        # ★ 每次推理在这里重置"按轮次"的状态：重复调用短路缓存与深化标记。
        #   放在循环入口而不是 run()，是为了无论从哪条路径进循环都保证隔离
        #   （跨轮复用会把"用户让我再读一次这个文件"短路掉——文件可能已经变了）。
        self._tool_fp_cache = {}
        self._deepen_used = False
        self._text_call_retried = False   # "把工具调用写成了文字"只重试一次，再犯就如实标注
        last_tool_result = ""  # 初始化，防止循环结束时 NameError

        while round_count < max_rounds:
            # 取消检测
            if self._cancel_requested.is_set():
                self.status.emit("[已取消] 推理已被用户中断")
                self._persist_terminal_reply("推理已取消。")   # 取消也要在会话里留痕，见更下方说明
                return "推理已取消。"

            round_count += 1
            self.status.emit(f"[思考 {round_count}/{max_rounds}] 模型判断中...")

            # 让模型决定：回答或调用工具
            ai_msg = self._invoke_llm_streaming(messages)
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
                    # ★ 已有实质答案就别再逼它"继续深入"：多跑一轮既慢，实测还常把
                    #   已经正确的答案改写坏（小模型在深化轮会丢细节）。每轮最多强制一次。
                    _substantive_now = self._looks_substantive(
                        content if isinstance(content, str) else "")
                    if _substantive_now or getattr(self, "_deepen_used", False):
                        self.status.emit("[完成] 已得到实质回答，跳过深化轮")
                    else:
                        # 未达到最低思考轮数，强制继续深入分析
                        self._deepen_used = True
                        self.status.emit(f"[深化 {round_count}/{min_rounds}] 未达最低思考轮数，强制继续分析...")
                        deepen_prompt = (
                        "\n\n请基于以上工具执行结果，继续深入分析："
                        "1. 重点解读关键文件的具体实现逻辑\n"
                        "2. 总结项目的核心功能、技术亮点和潜在风险\n"
                        "3. 如果有遗漏的重要文件，请调用工具继续读取分析"
                    )
                    messages.append(HumanMessage(content=deepen_prompt,
                                                additional_kwargs={"internal": True}))
                    has_executed_tool = False  # 重置标记，允许下一轮继续强制
                    continue  # 不返回，继续下一轮推理

                if has_executed_tool and not self._looks_substantive(
                        content if isinstance(content, str) else ""):
                    # 达到最低轮数且答案还很薄，追加总结轮次。
                    # ★ 若模型这一轮已经给出实质回答（够长 + 有结构），**不跑**这一次
                    #   全量生成：省一次完整推理，也避免把已正确的答案改写坏。
                    self.status.emit("[总结] 工具执行完毕，正在生成最终总结...")
                    summary_prompt = "\n\n请基于以上工具执行结果，给用户提供一个详细的总结报告。"
                    messages.append(HumanMessage(content=summary_prompt,
                                                additional_kwargs={"internal": True}))
                    final_msg = self._invoke_llm_streaming(messages)
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
                    # ★ 小模型会把"工具调用"写成普通文字（实测 qwen3.5:4b：有知识库提示时写出
                    #   `【知识库检索】search_knowledge_base(query=…, top_k=5)` 这类伪调用行），
                    #   若放任它当回答，用户看到的就是一行假的工具调用。这里给一次重试机会：
                    #   明确要求它要么用工具调用格式、要么直接作答；再犯就如实标注，不假装已执行。
                    if self._looks_like_text_tool_call(final_reply):
                        if not getattr(self, "_text_call_retried", False):
                            self._text_call_retried = True
                            self.status.emit("[模型] 把工具调用写成了文字，已要求它改用工具调用格式重试…")
                            messages.append(HumanMessage(
                                content="你刚才把工具调用写成了普通文字，系统不会执行这种写法。"
                                        "如果确实需要调用工具，请使用工具调用（function call）格式；"
                                        "否则请直接回答用户的问题。"))
                            continue
                        final_reply += ("\n\n（提示：模型这一轮把工具调用写成了文字，因此**没有被真正执行**；"
                                        "可以再发一次，或换用更大的模型。）")
                    # ★ 落地性兜底：模型若无视工具结果（实测 7B 常发生），把真实读取结果一并呈现
                    if forced_tool_result and forced_tool_name:
                        final_reply = self._ensure_grounded(final_reply, forced_tool_name, forced_tool_result)
                    # 保存完整工具调用链（从本轮的 HumanMessage 之后到结束）
                    chain = messages[round_start_idx:]
                    memory.update_with_full_chain(chain)
                    self.result_messages = memory.to_dict_list()
                    self._ensure_reply_persisted(final_reply)
                    return final_reply
                # 模型返回空内容，尝试直接回答
                self.status.emit("[模型] 未给出明确回答，重新生成...")
                reply_msg = self._direct_answer(ai_input, memory)
                final_reply = self._clean(reply_msg)
                # 兜底：如果仍然为空，给用户一个明确提示，避免静默返回空白。
                # ★ 文案按场景分支（V3.5.9 修）：原来只有"附件"那一套话术，
                #   于是**没挂附件**的时候也会收到"附件过长/扫描件 OCR 失败"的建议——答非所问。
                if not final_reply.strip():
                    if self.attachment_fulltext:
                        final_reply = (
                            "抱歉，模型未能针对该附件生成有效回答。\n"
                            "可能原因：\n"
                            "1. 附件文本过长超出模型上下文，请尝试截取片段或换更小的 PDF\n"
                            "2. 附件为扫描件/图片，且 OCR 未识别到文字（已安装 rapidocr-onnxruntime 仍可能因图片质量过差失败）\n"
                            "3. 模型当前状态异常，请重试或切换模型\n"
                            "请尝试：精简提问、切换深度思考模式、或挂载更小的文档。"
                        )
                    else:
                        final_reply = (
                            "抱歉，模型这一轮没有给出回答。\n"
                            "本机小模型偶发空回复（实测约每 5 次出现 1 次，与本机算力负载有关），"
                            "不是你的操作有问题。\n"
                            "可以试试：再发一次、把问题问得更具体、切换到「深度思考」模式，或换用更大的模型。"
                        )
                chain = messages[round_start_idx:]
                memory.update_with_full_chain(chain)
                self.result_messages = memory.to_dict_list()
                self._ensure_reply_persisted(final_reply)
                return final_reply

            # 执行每个工具调用
            self.status.emit(f"[思考 {round_count}/{max_rounds}] 准备调用 {len(tool_calls)} 个工具...")
            all_success = True
            round_had_failure = False
            round_made_progress = False   # 本轮是否拿到过成功工具结果（无进展收敛依据，见循环末尾）

            for tc in tool_calls:
                # 取消检测
                if self._cancel_requested.is_set():
                    self.status.emit("[已取消] 推理已被用户中断")
                    self._persist_terminal_reply("推理已取消。")   # 取消也要在会话里留痕
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
                    tool_msg = ToolMessage(content=self._with_failure_directive(tool_result),
                                           tool_call_id=tool_call_id)
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
                    tool_msg = ToolMessage(content=self._with_failure_directive(tool_result),
                                           tool_call_id=tool_call_id)
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
                        tool_msg = ToolMessage(content=self._with_failure_directive(tool_result),
                                               tool_call_id=tool_call_id)
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
                _tool_tid = int(getattr(self, "_trace_seq", 0)) + 1
                self._trace("tool", tool_display_name, str(target_path or ""), "running")

                try:
                    self.tool_call_count += 1
                    _t_tool = time.time()

                    # ★ 重复调用短路（仅只读工具）：同一工具同一参数再来一次，
                    #   直接复用已有结果并明确提示"别再重复"——小模型最容易在这里打转，
                    #   既浪费轮次又让用户看着像卡住了。
                    _fp = self._call_fingerprint(tool_name, tool_args)
                    if tool_name in self._READONLY_TOOLS and _fp in getattr(self, "_tool_fp_cache", {}):
                        tool_result = (str(getattr(self, "_tool_fp_cache")[_fp])
                                       + "\n[提示] 你刚刚已用完全相同的参数调用过这个工具，"
                                         "结果不会变化；请直接基于上面的结果作答，不要重复调用。")
                        self._trace("tool", tool_display_name, "重复调用已短路（复用上次结果）", "ok", ms=0)
                        tool_msg = ToolMessage(content=str(tool_result), tool_call_id=tool_call_id)
                        messages.append(tool_msg)
                        continue

                    tool_result = self._execute_tool(tool_name, tool_args)
                    has_executed_tool = True  # 标记已执行过工具
                    last_tool_result = str(tool_result)[:2000]

                    # 统一脱敏工具返回内容（内部已处理：搜索知识库豁免、映射合并进跨轮表、审计）
                    tool_result, _ = self._anonymize_tool_output(tool_result, tool_name)

                    # ★ 真值校验：写盘/删除类操作到文件系统核对一遍，把"声称成功"与事实对齐
                    _verdict = self._verify_tool_outcome(tool_name, tool_args, str(tool_result))
                    if _verdict:
                        tool_result = str(tool_result) + "\n" + _verdict
                        all_success = False
                        self._trace("judge", "工具结果校验", _verdict[:120], "fail", ms=0)

                    if tool_name in self._READONLY_TOOLS:
                        if not hasattr(self, "_tool_fp_cache"):
                            self._tool_fp_cache = {}
                        self._tool_fp_cache[_fp] = str(tool_result)[:2000]

                    AuditLog().log_tool_result(tool_name, True, str(tool_result)[:500])
                    self._trace("tool", tool_display_name, "执行完成", "ok",
                                ms=int((time.time() - _t_tool) * 1000))

                except Exception as e:
                    # ★ 失败反馈"可行动化"：裸异常会让小模型原样重试，翻译成人话才有机会一轮纠正
                    tool_result = self._humanize_tool_error(tool_name, e)
                    round_had_failure = True
                    all_success = False
                    AuditLog().log_tool_result(tool_name, False, str(e))
                    self._trace("tool", tool_display_name, str(e)[:80], "fail",
                                ms=int((time.time() - _t_tool) * 1000))

                # 4. 将工具执行结果追加到消息列表
                #    成功/失败分流：成功过就记下来（反编造兜底据此判断），
                #    失败形态统一追加"禁止编造"指令（模型实测会无视 ⚠️ 直接编内容）。
                _failed_result = any(mark in str(tool_result)[:200] for mark in self._FAILURE_MARKS)
                if not _failed_result:
                    self._turn_tool_success = True
                    round_made_progress = True
                self._turn_tool_names.append(tool_name)
                tool_msg = ToolMessage(content=self._with_failure_directive(tool_result),
                                       tool_call_id=tool_call_id)
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
                    self._ensure_reply_persisted(final_reply)
                    return final_reply
            else:
                consecutive_failures = 0
                # 渐进输出：工具执行完成后，向用户展示当前思考状态
                if has_executed_tool:
                    self.intermediate_result.emit(
                        f"[第 {round_count} 轮] 已完成工具检索，正在结合工具结果深入分析中..."
                    )

            # ★ 无进展收敛（V3.6.7，真机事故兜底）：上面的"连续 2 轮失败"只认**抛异常**的工具
            #   调用；而现实里最耗人的那类打转是"工具每次都正常返回、但结果是警告/越界/重复调用"
            #   ——声明式失败（如"⚠️ 指定路径超出安全沙箱"）不算 round_had_failure，于是模型可以
            #   把 max_rounds 全部用完，用户就一直在等（每轮都是一次完整生成）。
            #   阈值取 2，与既有"连续 2 轮工具调用失败就放弃"同一标准：一轮没成功可能是模型在
            #   换参数试探，两轮都换不来任何成功结果，继续撞墙的期望收益极低；小模型每轮 1~40 秒，
            #   继续等是纯粹的浪费。命中即**如实收尾**（说清没做成、为什么、下一步），不再空转。
            if round_made_progress:
                no_progress_rounds = 0
            else:
                no_progress_rounds += 1
                if no_progress_rounds >= self._NO_PROGRESS_ROUNDS_LIMIT:
                    self.status.emit(
                        f"[放弃] 连续 {no_progress_rounds} 轮没有成功结果，已如实收尾"
                    )
                    # 摘一句最近的工具返回作为"凭什么说没做成"的证据。★ 先过工具输出脱敏通道：
                    # 这段文字会进最终回复、也会进审计副本，和工具结果的待遇必须一致
                    # （脱敏产生的映射并入跨轮表，用户看到的仍是还原后的真值）。
                    _hint_raw = (str(last_tool_result or "").strip().splitlines() or [""])[0][:120]
                    _hint, _ = self._anonymize_tool_output(_hint_raw)
                    final_reply = (
                        f"抱歉，我连续 {no_progress_rounds} 轮都没能取得有效结果，已经停止重试"
                        f"（本轮共推理 {round_count} 次）。\n"
                        + (f"最近一次工具返回：{_hint}\n" if _hint else "")
                        + "可能的原因：\n"
                        "1. 目标不在我的沙箱目录内——越界路径会被安全策略挡下（这是设计如此，不会绕过）；\n"
                        "2. 我的工具箱里没有能完成这件事的能力（例如「统计某个目录里有几张图片」这类专门统计）；\n"
                        "3. 模型给出的工具参数不可用（例如目录名不存在）。\n"
                        "可以试试：把文件放到沙箱目录（或告诉我沙箱内的准确路径）、把问题问得更具体，"
                        "或者换一个更大的模型再试。"
                    )
                    chain = messages[round_start_idx:]
                    memory.update_with_full_chain(chain)
                    self.result_messages = memory.to_dict_list()
                    self._ensure_reply_persisted(final_reply)
                    return final_reply

            # 一轮工具执行完毕，继续循环让模型基于结果生成下一轮决策

        # 达到最大轮次，让模型总结
        self.status.emit("[总结] 已达最大推理轮次，正在生成最终回答...")
        final_msg = self._invoke_llm_with_cancel_check(messages)
        messages.append(final_msg)
        content = getattr(final_msg, "content", "")
        final_reply = self._clean(content) if content and isinstance(content, str) else last_tool_result
        # ★ 空产出兜底：`_clean` 之后可能是空串（只有空白/只有 tool_call 标签），而
        #   last_tool_result 也可能为空（本轮一个工具都没执行）——旧实现会把空串当回答返回，
        #   用户看到的就是一个空气泡。宁可如实说明，也不发空白。
        if not str(final_reply or "").strip():
            final_reply = (
                f"抱歉，我在 {round_count} 轮推理内没有拿到可用的结果，已停止。\n"
                "可能原因：模型一直没给出最终回答（本机小模型偶发空回复），"
                "或目标内容不在沙箱目录内因而被安全策略挡下。\n"
                "可以试试：再发一次、把问题问得更具体、切换到「深度思考」模式，或换用更大的模型。"
            )
        # 保存完整消息链
        chain = messages[round_start_idx:]
        memory.update_with_full_chain(chain)
        self.result_messages = memory.to_dict_list()
        self._ensure_reply_persisted(final_reply)
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

    # ──────────────────────────────────────────
    # 模型利用率与工具链完善（V3.5.0）
    #   目标：别让智能体"显得笨"。实测痛点来自四件事——
    #   ① 工具报成功但盘上没东西（小模型据此编造"已完成"）；
    #   ② 工具失败后模型只看到裸异常（FileNotFoundError…），下一轮只能重复撞墙；
    #   ③ 同一工具同参数反复调用；④ 明明已经有答案还硬跑一次全量总结生成。
    # ──────────────────────────────────────────
    _READONLY_TOOLS = {"list_local_files", "read_local_file", "search_in_files",
                       "analyze_project", "scan_privacy", "read_attachment_chunk",
                       "search_knowledge_base", "list_attachments"}

    # 无进展收敛阈值（V3.6.7）：连续这么多轮"没有成功工具结果 + 模型也没给出可交付文字"
    # 就如实收尾。取 2 与既有的"连续 2 轮工具调用失败就放弃"保持同一标准（不新立尺子）：
    # 一轮没成功可能是模型在换参数试探，两轮都换不来任何成功结果，继续撞墙的期望收益极低。
    _NO_PROGRESS_ROUNDS_LIMIT = 2

    def _call_fingerprint(self, tool_name: str, args: dict) -> str:
        """同一工具同一参数的指纹（用于识别重复调用）。"""
        try:
            return tool_name + "|" + json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str)
        except Exception:
            return tool_name + "|" + str(args)

    def _humanize_tool_error(self, tool_name: str, exc: Exception) -> str:
        """把裸异常翻译成"模型能照做"的反馈。

        小模型看到 `FileNotFoundError: [Errno 2] No such file or directory` 只会再试同一次；
        告诉它"路径不对，先用 list_local_files 确认"才有机会一轮纠正。
        """
        kind = type(exc).__name__
        text = str(exc)
        if isinstance(exc, PermissionError) or "沙箱" in text or "越界" in text or "拒绝" in text:
            return (f"[工具执行失败] {tool_name}：目标路径不在允许的沙箱目录内，已被安全策略拒绝。"
                    f"请改用沙箱根目录下的路径，或向用户确认文件位置；不要重复调用同一路径。")
        if isinstance(exc, FileNotFoundError) or "No such file" in text:
            return (f"[工具执行失败] {tool_name}：文件或目录不存在。"
                    f"请先用 list_local_files 列出目录确认名称（注意扩展名与层级），再重试；不要凭猜测重复调用。")
        if isinstance(exc, IsADirectoryError):
            return (f"[工具执行失败] {tool_name}：这是一个目录而不是文件。"
                    f"要查看目录内容请改用 list_local_files；要读文件请给出具体文件名。")
        if isinstance(exc, UnicodeDecodeError):
            return (f"[工具执行失败] {tool_name}：该文件不是纯文本（可能是 PDF/图片/二进制）。"
                    f"请告知用户改用附件挂载（内核会解析），不要把它当文本反复读取。")
        if "timed out" in text.lower() or "超时" in text:
            return (f"[工具执行失败] {tool_name}：执行超时。请缩小范围（例如只读文件的前一段/只列一层目录）后重试。")
        return (f"[工具执行失败] {tool_name}：{kind}: {text[:200]}。"
                f"请换一种做法或告知用户失败原因，不要用完全相同的参数重复调用。")

    def _verify_tool_outcome(self, tool_name: str, args: dict, result: str) -> str:
        """工具结果的真值校验：写盘类操作到文件系统上核对一遍。

        返回空串表示"无需追加"；否则返回要加在工具结果后的一段**事实修正**。
        这是"杜绝小模型幻觉式假成功"的最后一道：它说写好了，我们就去看一眼到底有没有。
        """
        try:
            target = str((args or {}).get("file_path") or (args or {}).get("target_path")
                         or (args or {}).get("path") or "")
            if not target:
                return ""
            path = Path(target)
            if tool_name in ("create_local_file", "write_file", "export_file"):
                if not path.exists():
                    return ("[事实校验] 该工具声称写入成功，但文件系统上不存在 "
                            + target + "。请勿据此声称「已完成」；"
                            "请检查路径是否正确，或把失败如实告知用户。")
                if path.is_file() and path.stat().st_size == 0:
                    return ("[事实校验] 文件 " + target
                            + " 已创建但内容为空，请确认是否真的写入了内容。")
            elif tool_name == "delete_local_file":
                if path.exists():
                    return ("[事实校验] 该工具声称已删除，但 " + target
                            + " 仍然存在；请如实告知用户删除未生效。")
        except Exception:
            return ""            # 校验本身失败时保持沉默，不影响主流程
        return ""

    def _looks_like_text_tool_call(self, text: str) -> bool:
        """判断这段"回答"其实是模型**用文字写的工具调用**（而不是真回答）。

        实测依据（V3.5.9，qwen3.5:4b）——小模型会用两种形态把工具调用"写出来"：
          ① 伪调用行：`【知识库检索】search_knowledge_base(query=…, top_k=5)`
          ② JSON 代码块：```json {"tool_calls": [{"name": "search_knowledge_base", …}]} ```
        两种情况的结构化 `tool_calls` 字段都是空的，于是这行文字被当成回答呈现给用户
        （看起来像工具跑了，其实一次都没执行）。

        判据刻意收紧，宁可漏判也不要误判：
          · 形态②：文本里出现 JSON 键 `"tool_calls"` **且**同时出现我们真实存在的工具名（≤800 字符）；
          · 形态①：某一行以可选的中文方括号标记开头，紧接着是真实工具名 + 左括号（≤300 字符）。
        命中后由调用方决定怎么办（重试一次 → 再犯则如实标注），这里只做识别。
        """
        body = (text or "").strip()
        if not body:
            return False
        names = [str(n) for n in (self.tools_map or {}) if n]
        if not names:
            return False
        # 形态②：JSON 代码块里的 tool_calls（模型"写"出结构化调用）
        if len(body) <= 800 and '"tool_calls"' in body and any(
                ('"%s"' % n) in body or ("'%s'" % n) in body for n in names):
            return True
        # 形态①：伪调用行
        if len(body) > 300:
            return False
        import re as _re
        pattern = r"(?m)^\s*(?:[【\[][^】\]\n]{0,14}[】\]])?\s*(?:%s)\s*\(" % "|".join(
            _re.escape(n) for n in names)
        return _re.search(pattern, body) is not None

    def _ensure_reply_persisted(self, final_reply: str) -> None:
        """保证**落库的消息链最后一条助手消息就是用户看到的那段文字**。

        为什么必须有（实测出来的真缺陷，V3.5.7）：本机 4B 模型偶发返回空内容
        （实测 5 次里 1 次：50 秒后 content 为空）。此时走"重新生成 → 抱歉提示"这条兜底，
        提示**只通过 finished 事件送到了界面**，而落库用的是 `result_messages` 里的消息链——
        链尾还停在那条内容为空的助手消息上。后果：界面上有答复、库里是空的，
        重启后再看那一轮是空白（E2E 套件正是这样逮到它的：user:89 | assistant:0）。
        这里做一件小事：若链尾不是这段文字，就把它补上（不覆盖已有的非空内容）。
        """
        body = (final_reply or "").strip()
        if not body or not isinstance(self.result_messages, list) or not self.result_messages:
            return
        last = self.result_messages[-1]
        if not isinstance(last, dict):
            return
        if last.get("role") == "assistant" and str(last.get("content") or "").strip():
            return                                   # 链尾已有内容：不动它（更可能是模型的原始回答）
        self.result_messages = list(self.result_messages) + [
            {"role": "assistant", "content": final_reply}
        ]

    def _persist_terminal_reply(self, text: str) -> None:
        """为"非正常收尾"（失败 / 取消）准备一条**可落库**的消息链：用户提问 + 说明文本。

        为什么必须有（V3.6.7，真机事故整改）：失败分支旧写法是
        `self.result_messages = self.messages_snapshot`，而 messages_snapshot 是**本轮之前**
        的历史（服务层 send() 先写用户提问、再把它传给 worker）——于是服务层落库时会把用户
        刚发的那条一起覆盖掉，失败说明又只经 error 事件飘一下：重启后那一轮彻底消失。
        这里按"用户提问 + 助手说明"补齐，链尾就是用户看到的那段文字（口径与界面一致）。

        注意：写进去的用户文本是本轮送给模型的 user_input；若它含附件上下文，服务层
        `_restore_display_text` 会把它换回界面展示文本（与其它路径同一处理）。
        """
        body = str(text or "").strip()
        if not body:
            return
        try:
            chain = [dict(m) for m in (self.messages_snapshot or []) if isinstance(m, dict)]
            chain.append({"role": "user", "content": self.user_input})
            chain.append({"role": "assistant", "content": text})
            self.result_messages = chain
        except Exception:
            # 兜底失败也不能让异常冒出去（它发生在异常处理路径里，再抛就会吞掉真正的失败原因）
            self.result_messages = self.messages_snapshot

    @staticmethod
    def _looks_substantive(text: str) -> bool:
        """判断模型这一轮的文本是否已经是"能交付的回答"（而非"我这就去读"这类过渡语）。

        依据：长度够 + 有多行或结构化标记。宁可偏向"是"——多跑一次总结生成既慢又容易
        把已经正确的答案改写坏（实测小模型在总结轮会丢细节）。
        """
        body = (text or "").strip()
        if not body:
            return False
        # 典型过渡语（"我这就去读""正在处理"）：短且只有承诺，没有结论 → 不算回答
        promise = ("我这就", "马上", "正在", "稍等", "请稍候", "接下来我", "我先去", "让我先")
        has_structure = (chr(10) in body) or any(
            k in body for k in ("：", "、", "- ", "1.", "•", "|", "；"))
        if len(body) < 40 and any(k in body for k in promise):
            return False
        if len(body) >= 90:
            return True
        return has_structure and len(body) >= 30

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
    # 统一工具输出脱敏通道（第2步）
    # ──────────────────────────────────────────
    def _get_engine(self):
        """取得本轮 PrivacyEngine 实例（run() 中已创建则复用，否则兜底新建）"""
        engine = getattr(self, "_engine", None)
        if engine is None:
            engine = PrivacyEngine()
            self._engine = engine
        return engine

    def _merge_box_mapping(self, mapping: dict) -> None:
        """合并映射到跨轮映射表，并在超限时 FIFO 淘汰最早条目。"""
        if not mapping:
            return
        self._box_mapping.update(mapping)
        overflow = len(self._box_mapping) - self._BOX_MAPPING_MAX
        if overflow > 0:
            for k in list(self._box_mapping.keys())[:overflow]:
                self._box_mapping.pop(k, None)

    def _anonymize_tool_output(self, tool_result, tool_name: str = ""):
        """★ 统一工具输出脱敏通道 —— 所有进入模型上下文的工具结果必须且只能经过这里。

        与第1步的区别：本方法保留脱敏产生的 mapping 并合并进跨轮映射表，
        使最终回复能把占位符还原成用户可读的明文。

        返回：(脱敏后文本, 本次新增映射 dict)
        """
        if not self.privacy_shield:
            return tool_result, {}
        # 知识库检索返回的已是脱敏文本（KB_ 前缀占位符），二次脱敏会破坏其映射
        if tool_name == "search_knowledge_base":
            return tool_result, {}
        if not isinstance(tool_result, str) or not tool_result:
            return tool_result, {}

        engine = self._get_engine()
        anon_text, mapping = engine.anonymize(tool_result)
        if mapping:
            self._merge_box_mapping(mapping)
            try:
                _stats = engine.get_last_stats()
                if _stats:
                    AuditLog().log_privacy_batch(_stats, "anonymized")
            except Exception:
                pass
        return anon_text, mapping

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
        """让模型直接回答用户问题（不调用工具），返回文本（调用方只关心文字时用它）。"""
        resp = self._direct_answer_message(ai_input, memory)
        content = getattr(resp, "content", "")
        return content if isinstance(content, str) else str(content)

    def _direct_answer_message(self, ai_input: str, memory):
        """直接回答（不调用工具）的**原始模型消息**。

        为什么要把"消息"给调用方而不是只回文本（V3.6.7，真机事故）：实测 qwen3.5:4b 对
        「我的桌面有几张图片？」这类需要真实世界信息的问题会返回
        `content='' + tool_calls=[list_local_files(target_directory='系统物理桌面')]`——
        旧写法只取 content，等于把模型的工具调用**静默丢掉**：工具一次没跑、回答是空串、
        用户拿到一个空气泡。调用方据此可以改走工具推理，而不是把空白当答案。
        """
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
        return self._invoke_llm_with_cancel_check(messages)

    # ──────────────────────────────────────────
    # 工具结果落地性兜底
    # ──────────────────────────────────────────
    @staticmethod
    def _evidence_tokens(tool_result: str, limit: int = 12) -> list:
        """从工具结果里抽取"可核对的证据词"——只取形如 xxx.ext 的文件名。

        只取带扩展名的文件名，避免把"文件/目录"这类通用词当证据
        （否则模型的敷衍回答也会被误判为"已经用上了工具结果"）。
        """
        import re as _re
        tokens = _re.findall(r"[A-Za-z0-9_\u4e00-\u9fa5\-]{2,40}\.[A-Za-z0-9]{1,6}", tool_result or "")
        uniq = []
        for token in tokens:
            if token not in uniq:
                uniq.append(token)
        return uniq[:limit]

    def _ensure_grounded(self, reply: str, tool_name: str, tool_result: str) -> str:
        """兜底：强制工具已执行、但模型回答没引用任何工具读到的文件名时，把真实结果附给用户。

        背景：7B 本地模型实测会完全无视工具结果（回答"请提供文件名或目录"），
        用户因此拿不到本该给出的文件列表。与其依赖模型自觉，不如把本地工具的真实
        读取结果直接呈现——数据本来就该以工具为准，模型的话只作补充。
        """
        if not tool_result or not reply:
            return reply
        tokens = self._evidence_tokens(tool_result)
        if not tokens or any(token in reply for token in tokens):
            return reply
        trimmed = tool_result.strip()
        if len(trimmed) > 1500:
            trimmed = trimmed[:1500] + "\n…（内容较长，已截断）"
        return (reply.rstrip() + "\n\n"
                f"—— 以下为本地工具（{tool_name}）实际读取到的内容，供你核对 ——\n{trimmed}")

    # 失败指令：任何"没成功"的工具结果后面都追加这一句，逼模型如实转述而不是编内容。
    # 实测依据（V3.6.3）：工具因沙箱越界返回警告时，4B 模型**没有**如实说"读不到"，
    # 而是编了一张"张三/北京科技集团/138****5678"的表格——对以"实事求是"为卖点的产品是硬伤。
    _FAILURE_MARKS = ("❌", "⚠️", "🚫", "工具执行出错", "已拦截：", "已驳回：", "失败：", "无法")
    _FAILURE_DIRECTIVE = (
        "\n\n❗系统提示：本次工具调用**未成功**。回答时必须如实说明失败原因与下一步建议"
        "（例如先列目录确认文件名），**禁止编造文件内容、数据或执行结果**。"
    )

    @classmethod
    def _with_failure_directive(cls, tool_result) -> str:
        """工具结果是失败/警告形态时，追加"禁止编造"的明确指令（幂等）。"""
        text = str(tool_result)
        if cls._FAILURE_DIRECTIVE in text:
            return text
        if any(mark in text[:200] for mark in cls._FAILURE_MARKS):
            return text + cls._FAILURE_DIRECTIVE
        return text

    @staticmethod
    def _looks_like_content_dump(reply: str) -> bool:
        """回答里是否"像在罗列内容"（代码块 / Markdown 表格 / 连续的空格对齐表格）。

        只用于"本轮没有任何成功工具结果"时的兜底判断——正常聊天很少出现这些形态，
        所以触发面窄，不会动不动给用户加免责话。
        """
        text = str(reply or "")
        if "```" in text:
            return True
        lines = text.splitlines()
        tableish = sum(1 for ln in lines if ln.count("|") >= 2)
        if tableish >= 2:
            return True
        spaced = 0
        for ln in lines:
            stripped = ln.strip()
            # 长度阈值按**字符**而不是字节：中文表格一行可能只有 6 个字
            # （"姓名  电话"）——之前用 >=8 会让中文表格整类漏判（实测踩到）。
            if len(stripped) >= 5 and re.search(r"\S\s{2,}\S", stripped):
                spaced += 1
                if spaced >= 3:
                    return True
            else:
                spaced = 0
        return False

    @staticmethod
    def _asks_for_content(user_text: str) -> bool:
        """用户是否在要"文件/目录里的内容"（决定要不要给它加"未经核实"提示）。"""
        text = str(user_text or "")
        if re.search(r"[\w\u4e00-\u9fa5-]{1,60}\.(?:txt|csv|md|json|log|py|html?|xlsx?|docx?|pdf)", text, re.I):
            return True
        return any(w in text for w in
                   ["文件", "文档", "目录", "内容", "表格", "名单", "清单", "列表",
                    "读", "查看", "看看", "搜索", "找出", "列出"])

    def _guard_ungrounded_dump(self, reply: str, tool_success: bool,
                               tool_names: list) -> str:
        """反编造兜底：本轮**没有任何成功的工具结果**，回答却在罗列内容时，如实加一句提示。

        这是"提示词硬规则 + 失败指令"之后的最后一道网：模型可能仍然编内容，
        而编出来的"文件内容"对用户来说与真实内容无法区分。宁可加一句机器核验过的提示，
        也不让用户把编造的内容当成文件事实。
        """
        if tool_success or not reply:
            return reply
        if not self._asks_for_content(self.user_input):
            return reply
        if not self._looks_like_content_dump(reply):
            return reply
        detail = ("本轮没有调用任何工具" if not tool_names
                  else f"本轮工具调用（{'、'.join(tool_names[:3])}）没有成功返回内容")
        return (str(reply).rstrip() +
                f"\n\n⚠️ 提示：{detail}，**以上内容未经核实**（可能是模型自行生成的示例），"
                "请先让我读取文件后再确认。")

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
