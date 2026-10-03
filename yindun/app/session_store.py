# -*- coding: utf-8 -*-
"""隐盾 · 会话存储（chat_sessions.json）

职责：多会话持久化（消息 / 附件全文快照 / 跨轮脱敏映射），**内容加密落盘**。
与旧版 main_window 的存储格式兼容（可读旧文件），并在新版做了两点改进：

1. **整条消息加密**：新版把除 role 之外的字段（content / tool_calls / tool_call_id）整体
   序列化后加密为 `payload_enc`。旧版只加密 content，助手消息里的工具调用参数会以明文落盘
   —— 那是泄露面。加载时兼容三种历史形态：`payload_enc`（新）→ `content_enc`（旧）→ `content`（更旧）。
2. **保留工具消息**：不再丢弃 role=tool 的消息（旧版加载时只留 user/assistant/system，
   导致跨天继续对话时工具调用序列不完整）。这样"存 → 读 → 再推理"才闭环。

与界面无关：本模块不导入任何界面框架。
"""
from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from yindun import APP_ROOT
from yindun.core.secret_manager import SecretManager

SESSIONS_FILE = APP_ROOT / "chat_sessions.json"

# 允许持久化的角色（tool 必须保留，否则工具调用链断裂）
KNOWN_ROLES = {"user", "assistant", "system", "tool"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SessionStore:
    """多会话存储（线程安全，内容加密落盘）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else SESSIONS_FILE
        self._lock = threading.RLock()
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._current_id: Optional[str] = None

    # ── 查询 ─────────────────────────────────────
    @property
    def current_id(self) -> Optional[str]:
        with self._lock:
            return self._current_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        """会话摘要列表（按更新时间倒序），供界面左侧列表使用。"""
        with self._lock:
            items = []
            for sess in self._sessions.values():
                items.append({
                    "id": sess["id"],
                    "title": sess["title"],
                    "created_at": sess.get("created_at", ""),
                    "updated_at": sess.get("updated_at", ""),
                    "message_count": len(sess.get("messages", []) or []),
                })
            items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
            return items

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            sess = self._sessions.get(session_id)
            return dict(sess) if sess else None

    def get_messages(self, session_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            sess = self._sessions.get(session_id) or {}
            return list(sess.get("messages", []) or [])

    def get_box_mapping(self, session_id: str) -> Dict[str, str]:
        with self._lock:
            sess = self._sessions.get(session_id) or {}
            return dict(sess.get("box_mapping", {}) or {})

    def get_attachment_fulltext(self, session_id: str) -> Dict[str, str]:
        with self._lock:
            sess = self._sessions.get(session_id) or {}
            return dict(sess.get("attachment_fulltext", {}) or {})

    # ── 修改 ─────────────────────────────────────
    def create(self, title: str = "") -> str:
        with self._lock:
            sid = uuid.uuid4().hex
            self._sessions[sid] = {
                "id": sid,
                "title": title or f"新对话 {datetime.now().strftime('%m-%d %H:%M')}",
                "created_at": _now(),
                "updated_at": _now(),
                "messages": [],
                "attachment_fulltext": {},
                "box_mapping": {},
            }
            self._current_id = sid
            return sid

    def delete(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                return False
            self._sessions.pop(session_id, None)
            if self._current_id == session_id:
                self._current_id = next(iter(self._sessions), None)
            return True

    def rename(self, session_id: str, title: str) -> bool:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return False
            sess["title"] = title
            sess["updated_at"] = _now()
            return True

    def set_current(self, session_id: Optional[str]) -> None:
        with self._lock:
            self._current_id = session_id if session_id in self._sessions else None

    def set_messages(self, session_id: str, messages: List[Dict[str, Any]]) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            sess["messages"] = [dict(m) for m in messages if isinstance(m, dict)]
            sess["updated_at"] = _now()

    def set_box_mapping(self, session_id: str, mapping: Dict[str, str]) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is not None and mapping:
                sess["box_mapping"] = dict(mapping)

    def set_attachment_fulltext(self, session_id: str, fulltext: Dict[str, str]) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess is not None:
                sess["attachment_fulltext"] = dict(fulltext or {})

    # ── 持久化 ───────────────────────────────────
    def load(self) -> None:
        """加载并解密；任何解密失败都留痕（不静默吞掉，也不回退明文）。"""
        with self._lock:
            self._sessions, self._current_id = {}, None
            if not self._path.exists():
                return
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except Exception as exc:
                print(f"[SessionStore] 加载会话失败({type(exc).__name__}): {exc}")
                return
            if not isinstance(payload, dict):
                return
            sm = SecretManager.get_instance()
            for item in payload.get("sessions", []) or []:
                if not isinstance(item, dict):
                    continue
                sid, title = item.get("id"), item.get("title")
                if not sid or not isinstance(title, str):
                    continue
                self._sessions[sid] = {
                    "id": sid,
                    "title": title,
                    "created_at": str(item.get("created_at", "")),
                    "updated_at": str(item.get("updated_at", "")),
                    "messages": self._decode_messages(sm, sid, item.get("messages")),
                    "attachment_fulltext": self._decode_attachments(sm, sid, item),
                    "box_mapping": item.get("box_mapping") or {},
                }
            sid = payload.get("current_session_id")
            self._current_id = sid if sid in self._sessions else None

    def save(self) -> bool:
        """加密落盘。返回是否成功（失败时调用方应提示，不许静默）。"""
        with self._lock:
            sm = SecretManager.get_instance()
            out_sessions = []
            for sess in self._sessions.values():
                record = {
                    "id": sess["id"], "title": sess["title"],
                    "created_at": sess.get("created_at", ""),
                    "updated_at": sess.get("updated_at", ""),
                    "messages": [self._encode_message(sm, sess["id"], m)
                                 for m in sess.get("messages", []) or []],
                    "box_mapping": sess.get("box_mapping", {}) or {},
                }
                att = sess.get("attachment_fulltext") or {}
                if att:
                    try:
                        record["attachment_fulltext_enc"] = sm.encrypt(
                            json.dumps(att, ensure_ascii=False))
                    except Exception as exc:
                        print(f"[SessionStore] 会话 {sess['id']} 附件快照加密失败，已跳过落盘：{exc}")
                        self._audit("会话附件快照加密失败，已跳过落盘（禁止明文落盘）",
                                    {"session_id": sess["id"], "error": str(exc)}, "SECURITY")
                out_sessions.append(record)
            payload = {"sessions": out_sessions, "current_session_id": self._current_id}
            try:
                with self._path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                return True
            except Exception as exc:
                print(f"[SessionStore] 保存会话失败({type(exc).__name__}): {exc}")
                return False

    # ── 内部：编解码 ─────────────────────────────
    @staticmethod
    def _encode_message(sm: SecretManager, session_id: str, message: Dict[str, Any]) -> Dict[str, Any]:
        """新版：role 之外的字段整体加密为 payload_enc（含工具调用参数，避免明文落盘）。"""
        role = message.get("role")
        if role not in KNOWN_ROLES:
            role = "user"
        body = {k: v for k, v in message.items() if k != "role" and v not in (None, "", [], {})}
        record: Dict[str, Any] = {"role": role}
        if not body:
            return record
        try:
            record["payload_enc"] = sm.encrypt(json.dumps(body, ensure_ascii=False))
        except Exception as exc:
            print(f"[SessionStore] 会话 {session_id} 消息加密失败，已落盘空内容：{exc}")
            SessionStore._audit("会话消息加密失败，已落盘空内容（禁止明文落盘）",
                                {"session_id": session_id, "error": str(exc)}, "SECURITY")
        return record

    @staticmethod
    def _decode_messages(sm: SecretManager, session_id: str, raw: Any) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        for msg in raw if isinstance(raw, list) else []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            if role not in KNOWN_ROLES:
                continue
            if isinstance(msg.get("payload_enc"), str):
                try:
                    body = json.loads(sm.decrypt(msg["payload_enc"]))
                    record = dict(body) if isinstance(body, dict) else {}
                    record["role"] = role
                    messages.append(record)
                    continue
                except Exception as exc:
                    print(f"[SessionStore] 会话 {session_id} 消息 payload_enc 解密失败，已跳过：{exc}")
                    SessionStore._audit("会话消息 payload_enc 解密失败，已跳过该条",
                                        {"session_id": session_id, "error": str(exc)})
                    continue
            # 旧格式兼容：content_enc / 明文 content
            content: Optional[str] = None
            if isinstance(msg.get("content_enc"), str):
                try:
                    content = sm.decrypt(msg["content_enc"])
                except Exception as exc:
                    print(f"[SessionStore] 会话 {session_id} 消息 content_enc 解密失败，已置空：{exc}")
                    SessionStore._audit("会话消息 content_enc 解密失败，内容已置空",
                                        {"session_id": session_id, "error": str(exc)})
                    content = ""
            elif isinstance(msg.get("content"), str):
                content = msg["content"]
            if content is not None:
                messages.append({"role": role, "content": content})
        return messages

    @staticmethod
    def _decode_attachments(sm: SecretManager, session_id: str, item: Dict[str, Any]) -> Dict[str, str]:
        if isinstance(item.get("attachment_fulltext_enc"), str):
            try:
                data = json.loads(sm.decrypt(item["attachment_fulltext_enc"]))
                return data if isinstance(data, dict) else {}
            except Exception as exc:
                print(f"[SessionStore] 会话 {session_id} 附件快照解密失败，已置空：{exc}")
                SessionStore._audit("会话附件快照解密失败，已置空",
                                    {"session_id": session_id, "error": str(exc)})
                return {}
        legacy = item.get("attachment_fulltext", {})
        return legacy if isinstance(legacy, dict) else {}

    @staticmethod
    def _audit(message: str, details: Dict[str, Any], severity: str = "WARNING") -> None:
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.PRIVACY_SENSITIVE,
                getattr(AuditSeverity, severity, AuditSeverity.WARNING),
                message, details,
            )
        except Exception:
            pass
