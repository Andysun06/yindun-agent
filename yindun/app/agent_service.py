# -*- coding: utf-8 -*-
"""隐盾 · 推理编排服务（与界面无关）

把"一次对话"从界面里彻底解放出来：本模块负责创建/注入 Worker、订阅事件总线、
把过程事件转发给界面、结束时落库（消息 + 跨轮脱敏映射），并处理审批与取消。

界面只需：
  1. 调用 `send()` / `cancel()` / `approve()`；
  2. 通过 `set_listener(fn)` 接收事件：`fn(event, payload)`。

事件（event 名与 worker 的事件主题一致，另加两个服务层事件）：
  - status / intermediate_result / finished / error / need_confirm / approval_expired  ← 引擎事件
  - "state"        ：{busy, tool_calls, context_tokens, session_id}  界面刷新忙碌态/看板
  - "llm_status"   ：{ready, model, models, error}                    算力状态变化

与界面无关：本模块不导入任何界面框架。
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from yindun import APP_ROOT
from yindun.app.attachment import build_attachment_context, parse_attachment
from yindun.app.llm_factory import build_llm, detect_chat_models, detect_ollama_models
from yindun.app.session_store import SessionStore
from yindun.app.settings_store import SettingsStore
from yindun.core.file_tools import (
    analyze_project,
    create_local_file,
    delete_local_file,
    list_local_files,
    modify_local_file,
    read_attachment_chunk,
    read_local_file,
    run_local_command,
    search_in_files,
    search_knowledge_base,
)
from yindun.worker.agent_worker import WORKER_EVENTS, Worker

Listener = Callable[[str, Any], None]


def build_tools() -> List[Any]:
    """全部可用工具（与旧界面保持一致：9 个本地工具 + 1 个知识库检索）。"""
    return [
        list_local_files, create_local_file, delete_local_file, read_local_file,
        modify_local_file, run_local_command, analyze_project, search_in_files,
        read_attachment_chunk, search_knowledge_base,
    ]


class AgentService:
    """推理编排服务：一次对话的完整生命周期。"""

    def __init__(self,
                 settings: Optional[SettingsStore] = None,
                 sessions: Optional[SessionStore] = None) -> None:
        self.settings = settings or SettingsStore()
        self.sessions = sessions or SessionStore()
        self._listener: Optional[Listener] = None
        self._worker: Optional[Worker] = None
        self._thread: Optional[threading.Thread] = None
        self._busy = False
        self._lock = threading.RLock()
        self._llm = None
        self._tools_map: Dict[str, Any] = {}
        self._llm_error: Optional[str] = None
        self._run_seq = 0
        self._active_run = 0
        # 本轮待发送的附件（解析后的 {name, path, text, chars, error}）
        self._attachments: List[Dict[str, Any]] = []
        # 本轮用户消息的"展示文本"（附件场景下与送模型的上下文不同）
        self._last_display_text: str = ""

    # ── 生命周期 ──────────────────────────────────
    def initialize(self) -> None:
        """加载配置与会话（同步、只读磁盘，很快）。"""
        self.settings.load()
        self.sessions.load()

    def set_listener(self, listener: Optional[Listener]) -> None:
        self._listener = listener

    def _emit(self, event: str, payload: Any = None) -> None:
        listener = self._listener
        if listener is None:
            return
        try:
            listener(event, payload)
        except Exception as exc:  # 界面问题不影响推理
            print(f"[AgentService] 事件 {event} 投递失败：{exc}")

    # ── 算力 ─────────────────────────────────────
    def prepare_llm(self) -> Dict[str, Any]:
        """构建算力（可能耗时，调用方应放后台线程）。"""
        self._llm, self._tools_map, models, err = build_llm(self.settings.data, build_tools())
        self._llm_error = err
        if models:
            self.settings.set("ollama_models_cache", models)
            self.settings.save()
        status = self.llm_status()
        self._emit("llm_status", status)
        return status

    def llm_status(self) -> Dict[str, Any]:
        return {
            "ready": self._llm is not None,
            "model": self.settings.get("model"),
            "models": detect_ollama_models(),
            "chat_models": detect_chat_models(),
            "error": self._llm_error,
        }

    # ── 会话 ─────────────────────────────────────
    def list_sessions(self) -> List[Dict[str, Any]]:
        return self.sessions.list_sessions()

    def current_session_id(self) -> Optional[str]:
        return self.sessions.current_id

    def create_session(self, title: str = "") -> str:
        sid = self.sessions.create(title)
        self.sessions.save()
        return sid

    def open_session(self, session_id: str) -> Dict[str, Any]:
        """打开会话：返回消息列表 + 还原后的展示内容由界面负责渲染。

        注意：历史消息里的占位符需要会话级映射才能还原，界面应把
        `box_mapping` 一并交给还原逻辑（与旧界面语义一致）。
        """
        self.sessions.set_current(session_id)
        self.sessions.save()
        return {
            "id": session_id,
            "messages": self.sessions.get_messages(session_id),
            "box_mapping": self.sessions.get_box_mapping(session_id),
        }

    def delete_session(self, session_id: str) -> bool:
        ok = self.sessions.delete(session_id)
        if ok:
            self.sessions.save()
        return ok

    def rename_session(self, session_id: str, title: str) -> bool:
        ok = self.sessions.rename(session_id, title)
        if ok:
            self.sessions.save()
        return ok

    # ── 附件 ─────────────────────────────────────
    def attach_files(self, paths: List[str]) -> List[Dict[str, Any]]:
        """解析并挂载附件（同步；解析大文档时前端可显示等待态）。

        返回解析结果摘要列表：[{name, chars, error}]，供界面提示。
        """
        added: List[Dict[str, Any]] = []
        existing = {item.get("path") for item in self._attachments}
        for path in paths:
            if not path or path in existing:
                continue
            self._emit("status", f"正在解析附件：{Path(path).name} …")
            record = parse_attachment(path)
            self._attachments.append(record)
            added.append({"name": record["name"], "chars": record["chars"], "error": record["error"]})
        self._emit("status", "")
        self._emit("attachments", self.list_attachments())
        return added

    def clear_attachments(self) -> None:
        self._attachments = []
        self._emit("attachments", [])

    def list_attachments(self) -> List[Dict[str, Any]]:
        return [{"name": item.get("name", ""), "chars": item.get("chars", 0),
                 "error": item.get("error")} for item in self._attachments]

    def attachment_count(self) -> int:
        return len(self._attachments)

    # ── 推理 ─────────────────────────────────────
    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    def send(self, text: str, attachments: Optional[List[Dict[str, str]]] = None) -> bool:
        """发起一轮对话。返回是否成功启动（忙碌或算力未就绪则拒绝）。

        附件：使用 `attach_files()` 预先挂载的解析结果；本轮附件会合并进会话的
        累积快照（跨轮可通过 read_attachment_chunk 工具检索），发送后清空待发列表。
        """
        text = (text or "").strip()
        if not text:
            return False
        with self._lock:
            if self._busy:
                return False
            if self._llm is None:
                self._emit("error", self._llm_error or "算力未就绪")
                return False

            sid = self.sessions.current_id or self.sessions.create()
            self._busy = True
            self._run_seq += 1
            run_id = self._run_seq
            self._active_run = run_id

        pending = list(self._attachments)

        # 历史快照必须在写入本轮消息之前取（与旧界面语义一致：快照=历史，本轮由引擎自行追加）
        history_snapshot = self.sessions.get_messages(sid)

        # 附件上下文：本轮新附件 + 历史累积快照（统一走服务层实现）
        snapshot = self.sessions.get_attachment_fulltext(sid)
        context, updated_snapshot = build_attachment_context(text, pending, snapshot)
        if pending:
            self.sessions.set_attachment_fulltext(sid, updated_snapshot)
            self._attachments = []

        # 界面上展示的消息保持"用户原话 + 附件名"，不把附件正文写进气泡
        display_text = text
        if pending:
            names = "、".join(item.get("name", "") for item in pending)
            display_text = f"📎 附件：{names}\n{text}"
        messages = list(history_snapshot)
        messages.append({"role": "user", "content": display_text})
        self.sessions.set_messages(sid, messages)
        self._last_display_text = display_text
        self.sessions.save()

        worker = Worker()
        worker.user_input = context          # 送模型的是"附件上下文 + 提问"
        worker.messages_snapshot = history_snapshot
        worker._box_mapping_restore = self.sessions.get_box_mapping(sid)
        worker.think_mode = self.settings.get("think_mode") or "快速回答"
        worker.privacy_shield = bool(self.settings.get("privacy", True))
        worker.think_depth = int(self.settings.get("thinking_depth", 3) or 3)
        worker.llm = self._llm
        worker.tools_map = self._tools_map
        worker.sandbox_path = os.environ.get("SANDBOX_PATH", str(APP_ROOT))
        worker.attachment_fulltext = self.sessions.get_attachment_fulltext(sid)
        worker.session_id = sid

        # 权限等级通过环境变量下发给工具层（与旧界面一致）
        os.environ["PERMISSION_LEVEL"] = str(self.settings.get("permission", "完全控制 (读/写/列表)"))
        os.environ["SANDBOX_PATH"] = worker.sandbox_path

        for topic in WORKER_EVENTS:
            worker.bus.subscribe(topic, self._make_forwarder(run_id, topic))

        with self._lock:
            self._worker = worker
        self._thread = threading.Thread(target=self._run_worker, args=(worker, sid, run_id), daemon=True)
        self._thread.start()
        self._emit("state", self._state_payload(sid, worker))
        return True

    def _make_forwarder(self, run_id: int, topic: str) -> Callable[[Any], None]:
        """把引擎事件转发给界面；已过期的轮次（被取消/替换）直接丢弃。"""
        def _forward(payload: Any) -> None:
            with self._lock:
                if run_id != self._active_run:
                    return
            self._emit(topic, payload)
        return _forward

    def _run_worker(self, worker: Worker, session_id: str, run_id: int) -> None:
        try:
            worker.run()
        except Exception as exc:  # 兜底：绝不让工作线程静默死掉
            self._emit("error", f"推理线程异常：{exc}")
        finally:
            self._finalize(worker, session_id, run_id)

    def _finalize(self, worker: Worker, session_id: str, run_id: int) -> None:
        """落库：消息链 + 跨轮脱敏映射，并广播空闲状态。"""
        with self._lock:
            current = run_id == self._active_run
            self._busy = False
            self._worker = None
        if current:
            try:
                result = getattr(worker, "result_messages", None)
                if result:
                    self.sessions.set_messages(session_id, self._restore_display_text(result))
                box = getattr(worker, "_box_mapping", None)
                if box:
                    self.sessions.set_box_mapping(session_id, box)
                self.sessions.save()
            except Exception as exc:
                print(f"[AgentService] 会话落库失败：{exc}")
        self._emit("state", self._state_payload(session_id, worker))

    def _restore_display_text(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """把"附件上下文"那条用户消息换回界面展示文本。

        引擎收到的 user_input 是"附件正文 + 提问"（内容很长），若原样落库：
        ① 切换会话重渲染时会把整篇附件正文当成用户发言铺满气泡；
        ② 附件正文会以用户消息形态二次落盘（本应只存在附件快照里）。
        展示文本在发送时已记录（_last_display_text），这里替换回去。
        """
        display = getattr(self, "_last_display_text", "")
        if not display:
            return messages
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, str):
                continue
            if "[人类当前实时提问]" in content or "[离线附件环境上下文" in content:
                patched = list(messages)
                patched[index] = {**message, "content": display}
                return patched
        return messages

    def _state_payload(self, session_id: Optional[str], worker: Optional[Worker] = None) -> Dict[str, Any]:
        worker = worker or self._worker
        tool_calls = getattr(worker, "tool_call_count", 0) if worker else 0
        messages = getattr(worker, "result_messages", []) if worker else []
        context_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 2
        return {
            "busy": self.busy,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "context_tokens": context_tokens,
            "message_count": len(messages),
            "model": self.settings.get("model"),
            "privacy": bool(self.settings.get("privacy", True)),
            "think_mode": self.settings.get("think_mode") or "快速回答",
        }

    def cancel(self) -> None:
        """取消当前推理（立即返回，不等待在途模型请求）。"""
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.cancel()

    def approve(self, ok: bool) -> None:
        """人工审批结果：ok=True 放行，False 驳回。"""
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.approve(bool(ok))
