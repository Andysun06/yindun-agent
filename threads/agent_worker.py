# -*- coding: utf-8 -*-
"""
隐盾 V2.2.0 — 🧠 异步大脑驱动舱
专门处理本地大模型连接、脱敏网关推演与物理工具调度的后台子线程，保护主界面永不卡死
⭐ 升级：使用 SummarizableChatHistory 管理上下文记忆，支持 token 阈值与自动摘要
"""
import os
import re
import threading
from PySide6.QtCore import QObject, Signal
from langchain_core.messages import HumanMessage, AIMessage
from core.privacy_engine import PrivacyEngine
from core.memory_manager import SummarizableChatHistory


class Worker(QObject):
    # 建立与 GUI 界面进行安全跨线程通信的信号管道
    finished = Signal(str)       # 运行完成，向界面投递 AI 的最终标准回答
    error = Signal(str)          # 运行报错，向界面投递捕获到的系统异常
    status = Signal(str)         # 状态流，实时更新界面底部的"隐盾大脑研判中..."文字
    need_confirm = Signal(dict)  # 拦截信号，触发高危跨目录越界操作时的动态拦截通知

    def __init__(self):
        super().__init__()
        self.user_input = ""
        self.messages_snapshot: list[dict] = []  # 从 MainWindow 传入的历史消息快照
        self.think_mode = "快速"
        self.privacy_shield = True
        self.llm = None
        self.tools_map = {}
        self.sandbox_path = os.path.abspath(".")
        self._approved = threading.Event()
        self._approved_val = None
        # ⭐ 处理完成后存储最新的消息列表（含摘要标记），供 MainWindow 读取
        self.result_messages: list[dict] = []

    def approve(self, ok):
        """接收来自人类安全员的二次审计决策结果"""
        self._approved_val = ok
        self._approved.set()

    def run(self):
        """线程点火后的中央核心运行逻辑"""
        try:
            # ⭐ 0. 从快照重建记忆管理器
            memory = SummarizableChatHistory.from_dict_list(
                self.messages_snapshot,
                max_tokens=5000
            )

            # 1. 拦截输入流，调用纯 Python 引擎进行就地数据脱敏隔离
            engine = PrivacyEngine()
            ai_input, box = engine.anonymize(self.user_input) if self.privacy_shield else (self.user_input, {})

            # 2. 如果开启深度自检模式，触发影子大脑执行第一轮合规性安全推演
            if "深度" in self.think_mode:
                self.status.emit("[思考] 影子合规推演中")
                try:
                    self.llm.invoke(f"简要分析安全隐患：'{ai_input}'")
                except:
                    pass

            # ⭐ 3. 通过记忆管理器获取上下文（自动处理 token 阈值与摘要压缩）
            self.status.emit("[记忆] 正在加载对话上下文...")
            hist = memory.get_context_messages(self.llm)
            hist.append(HumanMessage(content=ai_input))

            # 调试：若触发了摘要，通知 UI
            if memory.total_tokens() > memory.max_tokens and memory._summary:
                self.status.emit(f"[记忆] 上下文已压缩（原{memory.total_tokens()}token → 摘要+最近消息）")

            # 4. 推动算力底座执行 invoke 决策推理
            resp = self.llm.invoke(hist)

            # 5. 判定大模型是否企图暗中调动物理机械臂工具
            if resp.tool_calls:
                tc = resp.tool_calls[0]
                tname, targs = tc["name"], tc["args"]

                # 精准解析物理落脚点路径
                rp = self._resolve(self.user_input, targs)
                sens = self._is_sens(rp)

                # 对机械臂内的字符串参数执行就地解密还原
                ca = {k: engine.deanonymize(v, box) if isinstance(v, str) and self.privacy_shield and box else v
                      for k, v in targs.items()}

                # 核心合规网关拦截
                if sens:
                    self.need_confirm.emit({"name": tname, "args": ca, "path": rp})
                    self._approved.clear()
                    self._approved.wait()

                    if not self._approved_val:
                        self.finished.emit("已驳回：敏感操作被拦截。")
                        self.result_messages = memory.to_dict_list()
                        return

                # 人类审计通过或常规目录，安全放行机械臂下发写盘
                self.status.emit(f"执行工具: {tname}")
                os.environ["SANDBOX_PATH"] = rp
                tr = self.tools_map[tname].invoke(ca)

                # 将工具执行结果喂回大模型，润色整理出最终汇报文本
                fin = self.llm.invoke(f"用户说：'{ai_input}'。工具{tname}结果：'{tr}'。中文回复。")
                reply = self._clean(fin.content)
            else:
                reply = self._clean(resp.content)

            # 6. 数据还原网关：把 AI 吐出的脱敏符号还原为真实明文投递给用户
            if self.privacy_shield and box:
                reply = engine.deanonymize(reply, box)

            # ⭐ 7. 将本轮对话写入记忆管理器（自动处理摘要压缩）
            memory.update_with_context_result(self.user_input, reply)
            self.result_messages = memory.to_dict_list()

            self.finished.emit(reply)

        except Exception as e:
            self.result_messages = self.messages_snapshot  # 出错时保持原快照不变
            self.error.emit(f"{type(e).__name__}: {e}")

    # ── 路径解析与安全检查（保持不变）────────────────────
    def _resolve(self, text, args):
        det = self._det(text)
        if det: return det
        desc = args.get("target_directory", "当前沙箱目录")
        return self._dyn(desc) if desc and desc not in ["当前沙箱目录", "项目根目录"] else self.sandbox_path

    @staticmethod
    def _det(text):
        if "桌面" in text: return os.path.join(os.path.expanduser("~"), "Desktop")
        m = re.search(r"([A-Za-z])\s*盘(?:下的|的|内|目录|文件夹|根目录)?\s*([\w\u4e00-\u9fa5]*)", text)
        if m:
            d, sub = m.group(1).upper(), m.group(2).strip()
            if sub and not any(w in sub for w in ["创建", "新建", "写", "看", "删除", "移除", "清理"]):
                return os.path.abspath(f"{d}:\\{sub}")
            return os.path.abspath(f"{d}:\\")
        m2 = re.search(r"([A-Za-z]:\\[\w\u4e00-\u9fa5\\]*)", text)
        return os.path.abspath(m2.group(1)) if m2 else None

    @staticmethod
    def _dyn(desc):
        if not desc or "项目根目录" in desc or "当前沙箱" in desc: return os.path.abspath(".")
        if "桌面" in desc: return os.path.join(os.path.expanduser("~"), "Desktop")
        p = desc.strip()
        if "盘" in p: parts = p.split("盘"); p = parts[0].upper() + ":\\" + (parts[1] if len(parts) > 1 else "")
        for s in ["内", "中", "目录", "文件夹", "根目录"]:
            if p.endswith(s): p = p[:-len(s)]
        try: return os.path.abspath(os.path.normpath(p.strip()))
        except: return os.path.abspath(".")

    @staticmethod
    def _is_sens(path):
        return path.lower() not in (os.path.abspath(".").lower(), os.path.join(os.path.expanduser("~"), "Desktop").lower())

    @staticmethod
    def _clean(text):
        if not text: return ""
        t = re.sub(r'(?i)(?:brtc|portun|tool_call)?\s*\{.*\}\s*</tool_call>?', '', text)
        return re.sub(r'(?i)</?tool_call>', '', t).strip()
