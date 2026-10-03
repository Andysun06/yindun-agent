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
        return {
            "version": __display_version__,
            "settings": self._svc.settings.data,
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
        self._window = webview.create_window(
            f"隐盾安全智能体 {__display_version__}",
            url=str(INDEX_HTML),
            js_api=api,
            width=1180,
            height=780,
            min_size=(880, 600),
            on_top=bool(settings.get("topmost", True)),
            confirm_close=False,
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
