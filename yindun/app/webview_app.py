# -*- coding: utf-8 -*-
"""隐盾 · Web 界面承载（pywebview / WebView2）

职责：把 `AgentService` 暴露给前端 JS，并把服务层事件实时推送到页面。

通信契约：
  · JS → Python：`window.pywebview.api.<method>(...)`（返回 Promise）
  · Python → JS：`window.yindun.onEvent(event, payload)`（本模块用 evaluate_js 调用）

★ 架构要点（安全）：前端是**本地文件**（`file://` 直接加载），页面与后端同进程通信，
  不存在本地 HTTP 服务、不监听任何端口 —— 对一个安全产品来说，这比"起个 localhost 服务"
  更干净：没有端口可被本机其它进程探测，也没有 CORS/鉴权面。

启动：`python run.py --web`（或由 yindun.main 按配置选择界面）
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import webview

from yindun import APP_ROOT, __display_version__
from yindun.app.agent_service import AgentService

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


class JsApi:
    """暴露给前端的接口（方法名即 JS 侧调用名）。"""

    def __init__(self, service: AgentService, window_holder: Dict[str, Any]) -> None:
        self._svc = service
        self._window = window_holder

    # ── 启动数据 ─────────────────────────────────
    def bootstrap(self) -> Dict[str, Any]:
        settings = dict(self._svc.settings.data)
        # 态势行要展示真实运行态：沙箱目录来自环境（工具层实际使用的值）
        settings.setdefault("sandbox", os.environ.get("SANDBOX_PATH", str(APP_ROOT)))
        return {
            "version": __display_version__,
            "settings": settings,
            "llm": self._svc.llm_status(),
            "sessions": self._svc.list_sessions(),
            "current_session_id": self._svc.current_session_id(),
        }

    def llm_status(self) -> Dict[str, Any]:
        return self._svc.llm_status()

    # ── 会话 ─────────────────────────────────────
    def list_sessions(self):
        return self._svc.list_sessions()

    def new_session(self) -> str:
        return self._svc.create_session()

    def open_session(self, session_id: str):
        return self._svc.open_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        return self._svc.delete_session(session_id)

    def rename_session(self, session_id: str, title: str) -> bool:
        return self._svc.rename_session(session_id, title)

    # ── 推理 ─────────────────────────────────────
    def send(self, text: str) -> bool:
        return self._svc.send(text)

    def cancel(self) -> bool:
        self._svc.cancel()
        return True

    def approve(self, ok: bool) -> bool:
        self._svc.approve(bool(ok))
        return True

    # ── 附件 ─────────────────────────────────────
    def pick_files(self):
        """弹出系统文件对话框选择附件（解析后挂载到本轮；返回解析摘要）。"""
        window = self._window.get("window")
        if window is None:
            return []
        try:
            import webview as _wv
            from yindun.app.attachment import SUPPORTED_EXTS, normalize_paths
            patterns = " ".join(f"*.{ext}" for ext in SUPPORTED_EXTS)
            picked = window.create_file_dialog(
                _wv.OPEN_DIALOG, allow_multiple=True, file_types=(f"文档 ({patterns})", "所有文件 (*.*)")
            )
        except Exception as exc:
            print(f"[WebView] 打开文件对话框失败：{exc}")
            return []
        paths = normalize_paths(picked)
        if not paths:
            return []
        return self._svc.attach_files(paths)

    def attach_paths(self, paths):
        """直接挂载给定路径（供拖拽或自动化测试使用）。"""
        from yindun.app.attachment import normalize_paths
        return self._svc.attach_files(normalize_paths(paths))

    def clear_attachments(self) -> bool:
        self._svc.clear_attachments()
        return True

    def list_attachments(self):
        return self._svc.list_attachments()

    # ── 审计 ─────────────────────────────────────
    def audit_snapshot(self, limit: int = 200):
        """审计面板数据：统计概览 + 链完整性 + 事件列表（均为脱敏预览）。

        注意：`get_stats()` 的 by_type/by_severity 以枚举为键，直接返回无法 JSON 序列化，
        这里统一转成字符串键的普通字典再交给前端。
        """
        try:
            from yindun.core.audit_log import AuditLog
            log = AuditLog()
            raw_stats = log.get_stats() or {}
            by_type = {str(getattr(k, "value", k)): int(v)
                       for k, v in (raw_stats.get("by_type") or {}).items()}
            by_severity = {str(getattr(k, "value", k)): int(v)
                           for k, v in (raw_stats.get("by_severity") or {}).items()}
            stats = {
                "total_entries": int(raw_stats.get("total_entries", 0)),
                "chain_valid": bool(raw_stats.get("chain_valid", False)),
                "tool_calls": by_type.get("tool_call", 0),
                "privacy_events": sum(v for k, v in by_type.items() if k.startswith("privacy")),
                "approvals": by_type.get("access_control", 0),
                "llm_calls": by_type.get("llm_input", 0),
                "by_type": by_type,
                "by_severity": by_severity,
            }
            entries = log.get_entries() or []
            recent = []
            for item in entries[-max(1, min(int(limit or 200), 1000)):]:
                record = item.to_dict() if hasattr(item, "to_dict") else dict(item)
                details = record.get("details") if isinstance(record.get("details"), dict) else {}
                recent.append({
                    "time": str(record.get("timestamp", "")),
                    "type": str(getattr(record.get("event_type"), "value", record.get("event_type", ""))),
                    "severity": str(getattr(record.get("severity"), "value", record.get("severity", ""))),
                    "message": str(record.get("message", "")),
                    "preview": str(details.get("preview", ""))[:200],
                    "hash": str(record.get("entry_hash", ""))[:16],
                    "prev": str(record.get("previous_hash", ""))[:16],
                })
            return {"stats": stats, "chain_ok": bool(stats["chain_valid"]),
                    "entries": list(reversed(recent))}
        except Exception as exc:
            return {"stats": {}, "chain_ok": False, "entries": [], "error": str(exc)}

    def audit_export(self, fmt: str = "json"):
        """导出审计报告（json/html），返回落盘路径。"""
        try:
            from yindun.core.audit_log import AuditLog
            fmt = "html" if str(fmt).lower() == "html" else "json"
            return AuditLog().save_report(fmt)
        except Exception as exc:
            return f"导出失败：{exc}"

    # ── 知识库（脱敏 RAG）─────────────────────────
    def kb_status(self):
        return self._svc.kb_status()

    def kb_pick_and_add(self):
        """弹窗选择文档并入库（入库即脱敏）。"""
        window = self._window.get("window")
        if window is None:
            return {"ok": False, "error": "窗口未就绪"}
        try:
            import webview as _wv
            from yindun.app.attachment import SUPPORTED_EXTS, normalize_paths
            patterns = " ".join(f"*.{ext}" for ext in SUPPORTED_EXTS)
            picked = window.create_file_dialog(
                _wv.OPEN_DIALOG, allow_multiple=True, file_types=(f"文档 ({patterns})", "所有文件 (*.*)"))
        except Exception as exc:
            return {"ok": False, "error": f"打开文件对话框失败：{exc}"}
        return self._svc.kb_add(normalize_paths(picked))

    def kb_add_paths(self, paths):
        from yindun.app.attachment import normalize_paths
        return self._svc.kb_add(normalize_paths(paths))

    def kb_remove(self, file_name: str):
        return self._svc.kb_remove(file_name)

    # ── 窗口控制（无边框悬浮模式）─────────────────
    def window_action(self, action: str) -> bool:
        """无边框模式下的窗口操作：minimize / toggle_top / close。

        ★ 注意：`toggle_top` 只**更新设置**（下次启动生效），不在运行时改窗口置顶标志——
        实测从 JS 触发的运行时窗口标志变更会卡住 pywebview 事件循环（窗口假死）。
        置顶这种"形态类"设置走重启生效，风险最低。
        """
        window = self._window.get("window")
        if window is None:
            return False
        try:
            if action == "minimize":
                window.minimize()
            elif action == "toggle_top":
                new_value = not bool(self._svc.settings.get("topmost", True))
                self._svc.settings.set("topmost", new_value)
                self._svc.settings.save()
                self._svc._emit("status", f"置顶已设为「{'开' if new_value else '关'}」，重启后生效")
            elif action == "close":
                window.destroy()
            else:
                return False
            return True
        except Exception as exc:
            print(f"[WebView] 窗口操作 {action} 失败：{exc}")
            return False

    # ── 插件 ─────────────────────────────────────
    def plugins_list(self):
        return self._svc.list_plugins()

    def plugin_set_enabled(self, plugin_id: str, enabled: bool):
        return self._svc.set_plugin_enabled(plugin_id, bool(enabled))

    # ── 设置 ─────────────────────────────────────
    def save_settings(self, patch: Dict[str, Any]) -> bool:
        if not isinstance(patch, dict):
            return False
        allowed = {"model", "privacy", "dark_mode", "topmost",
                   "thinking_depth", "permission", "think_mode"}
        for key, value in patch.items():
            if key in allowed:
                self._svc.settings.set(key, value)
        self._svc.settings.save()
        # 窗口置顶等需要即时作用于窗口本身
        window = self._window.get("window")
        if window is not None and "topmost" in patch:
            try:
                window.on_top = bool(patch["topmost"])
            except Exception as exc:
                print(f"[WebView] 应用置顶设置失败：{exc}")
        return True


class WebApp:
    """Web 界面的装配与启动。"""

    def __init__(self, service: Optional[AgentService] = None) -> None:
        self.service = service or AgentService()
        self._window_holder: Dict[str, Any] = {}
        self._pending_push: list[tuple[str, Any]] = []
        self._window: Optional[Any] = None

    # ── 事件推送 ─────────────────────────────────
    def _on_service_event(self, event: str, payload: Any) -> None:
        """服务层事件 → 前端。窗口未就绪时先缓存，避免丢事件。"""
        window = self._window
        if window is None:
            self._pending_push.append((event, payload))
            return
        try:
            window.evaluate_js(
                f"window.yindun && window.yindun.onEvent({json.dumps(event)}, {json.dumps(payload, ensure_ascii=False)})"
            )
        except Exception as exc:
            print(f"[WebView] 推送事件 {event} 失败：{exc}")

    def _flush_pending(self) -> None:
        pending, self._pending_push = self._pending_push, []
        for event, payload in pending:
            self._on_service_event(event, payload)

    # ── 启动 ─────────────────────────────────────
    def run(self) -> None:
        self.service.initialize()
        self.service.set_listener(self._on_service_event)

        api = JsApi(self.service, self._window_holder)
        settings = self.service.settings.data
        # 无边框悬浮模式（产品标志性形态）：窗口本身无系统边框，由前端顶栏承担拖拽与窗口控制；
        # 若拖动异常，可在设置里关掉"无边框悬浮模式"回退到系统边框。
        frameless = bool(settings.get("frameless", True))
        window_kwargs: Dict[str, Any] = {
            "width": 1180,
            "height": 780,
            "min_size": (880, 600),
            "on_top": bool(settings.get("topmost", True)),
            "confirm_close": False,
        }
        if frameless:
            window_kwargs.update({"frameless": True, "easy_drag": True})
        self._window = webview.create_window(
            f"隐盾安全智能体 {__display_version__}",
            url=str(INDEX_HTML),
            js_api=api,
            **window_kwargs,
        )
        self._window_holder["window"] = self._window

        # 算力准备放到后台：界面先出来，就绪后再广播 llm_status
        threading.Thread(target=self.service.prepare_llm, daemon=True).start()

        def _on_loaded() -> None:
            self._flush_pending()

        try:
            self._window.events.loaded += _on_loaded
        except Exception:
            pass

        webview.start(debug=bool(os.environ.get("YINDUN_WEB_DEBUG")))


def run_web_ui() -> None:
    """入口：启动 Web 界面（供 run.py / main.py 调用）。"""
    if not INDEX_HTML.exists():
        raise SystemExit(f"前端资源缺失：{INDEX_HTML}")
    WebApp().run()


if __name__ == "__main__":
    run_web_ui()
