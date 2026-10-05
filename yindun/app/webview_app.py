# -*- coding: utf-8 -*-
"""隐盾 · Web 界面承载（pywebview / WebView2）

职责：把 `AgentService` 暴露给前端 JS，并把服务层事件实时推送到页面。

通信契约：
  · JS → Python：`window.pywebview.api.<method>(...)`（返回 Promise）
  · Python → JS：`window.yindun.onEvent(event, payload)`（本模块用 evaluate_js 调用）

★ 架构要点（安全，实测后修正的准确表述）：
  前端是**本地文件**（`file://` 直接加载），不经过本地 HTTP 站点；
  但 pywebview 的 js_api 桥需要一条回环通道——实测它会监听 127.0.0.1 上的**随机端口**，
  接口路径为 `/js_api/<随机会话 UUID>`（uuid1，页面不泄露该值）：
    · 实测无鉴权请求：`POST /js_api/猜测值` → 405；路径穿越 → 404；根路径 → 500
    · 因此暴露面仅限"本机进程 + 已知随机 UUID"，攻破前提等价于本机已被植入可读进程内存的代码
  这是一条**需要如实写进威胁模型**的特性（此前注释称"不监听任何端口"表述有误，已更正）。

启动：`python run.py --web`（或由 yindun.main 按配置选择界面）
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import webview

from yindun import APP_ROOT, __display_version__
from yindun.app import window_layout
from yindun.app.agent_service import AgentService

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


class JsApi:
    """暴露给前端的接口（方法名即 JS 侧调用名）。"""

    def __init__(self, service: AgentService, window_holder: Dict[str, Any]) -> None:
        self._svc = service
        self._window = window_holder
        # 折叠前的完整几何（极简闪发折叠模式用；None = 当前未折叠）
        self._saved_geometry: Optional[Tuple[int, int, int, int]] = None

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
        """弹出系统文件对话框选择附件（解析后挂载到本轮；返回解析摘要）。

        可选扩展名 = 内核支持的 + 已启用插件声明的（插件停用后自动从列表消失）。
        """
        window = self._window.get("window")
        if window is None:
            return []
        try:
            import webview as _wv
            from yindun.app.attachment import normalize_paths
            patterns = " ".join(f"*.{ext}" for ext in self._svc.supported_exts())
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

        链校验用 `verify_chain_detail()`：界面要能直接显示**人话原因**
        （"尾部被截断""锚点缺失""疑似抹掉 HMAC 标记"），而不是只给一个 ❌。
        """
        try:
            from yindun.core.audit_log import AuditLog
            log = AuditLog()
            detail = log.verify_chain_detail()
            raw_stats = log.get_stats() or {}
            by_type = {str(getattr(k, "value", k)): int(v)
                       for k, v in (raw_stats.get("by_type") or {}).items()}
            by_severity = {str(getattr(k, "value", k)): int(v)
                           for k, v in (raw_stats.get("by_severity") or {}).items()}
            stats = {
                "total_entries": int(raw_stats.get("total_entries", 0)),
                # 以 verify_chain_detail 的结论为准（get_stats 内部也会校验，但拿不到原因）
                "chain_valid": bool(detail.get("valid")),
                "chain_reason": str(detail.get("reason") or ""),
                "chain_anchor": str(detail.get("anchor") or "none"),
                "chain_legacy": int(detail.get("legacy", 0)),
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
        """导出审计报告（内核格式 json/html，或插件贡献的格式），返回落盘路径。"""
        return self._svc.export_audit(str(fmt or "json"))

    def audit_export_formats(self):
        """可用导出格式：内核自带 + 已启用插件贡献的（界面据此生成菜单）。"""
        return self._svc.audit_export_formats()

    def audit_reanchor(self, reason: str = ""):
        """显式重建审计链锚点（升级/拷贝审计目录后的确认动作）。

        界面必须在用户点确认后才调用：确认之后，此前发生的"尾部截断"将无法再被发现——
        这一点由调用方负责向用户讲清楚（按钮文案里已写明）。
        """
        try:
            from yindun.core.audit_log import AuditLog
            return AuditLog().reanchor(str(reason or ""))
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
            from yindun.app.attachment import normalize_paths
            patterns = " ".join(f"*.{ext}" for ext in self._svc.supported_exts())
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
        """无边框模式下的窗口操作：minimize / toggle_top / close / mini / restore_window。

        ★ 置顶是**即时生效**的：早前记录过"运行时改窗口标志会卡住 pywebview 事件循环"，
        因此曾做成"重启生效"；后来用当前版本复测（三种调用路径 × 连续切换，并直接读 Win32
        的 WS_EX_TOPMOST 位核对）确认——运行时写 `window.on_top` 真的作用到原生窗口，
        事件循环也保持响应，所以改回即时生效，不再让用户为一次开关去重启。
        万一某台机器上应用失败（异常），退回"已保存、重启生效"并如实告知，而不是假装成功。

        `mini` / `restore_window` 是**极简闪发折叠模式**（旧 Qt 界面的同名功能迁移）：
        折叠成一条浮条（底边对齐、水平居中），展开时精确还原折叠前的几何。
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
                try:
                    window.on_top = new_value            # 立即作用到原生窗口
                    self._svc._emit("status", f"置顶已{'开启' if new_value else '关闭'}")
                except Exception as exc:
                    print(f"[WebView] 即时应用置顶失败（已保存，重启后生效）：{exc}")
                    self._svc._emit("status", f"置顶已设为「{'开' if new_value else '关'}」，本机需重启后生效")
            elif action == "mini":
                self._collapse_to_mini(window)
            elif action == "restore_window":
                self._restore_from_mini(window)
            elif action == "close":
                window.destroy()
            else:
                return False
            return True
        except Exception as exc:
            print(f"[WebView] 窗口操作 {action} 失败：{exc}")
            return False

    # ── 极简闪发折叠模式（几何计算见 app/window_layout.py）──
    def _current_geometry(self, window) -> Optional[Tuple[int, int, int, int]]:
        """读取当前窗口几何；任一维度取不到就返回 None（宁可不折叠，也不要乱跳）。"""
        try:
            x, y, w, h = window.x, window.y, window.width, window.height
        except Exception:
            return None
        if None in (x, y, w, h):
            return None
        return int(x), int(y), int(w), int(h)

    def _collapse_to_mini(self, window) -> None:
        geometry = self._current_geometry(window)
        if geometry is None:
            print("[WebView] 取不到窗口几何，已跳过折叠（避免窗口跳到错误位置）")
            return
        self._saved_geometry = geometry          # 展开时精确还原
        x, y, w, h = geometry
        nx, ny, nw, nh = window_layout.collapse_to_mini(x, y, w, h)
        window.move(nx, ny)
        window.resize(nw, nh)

    def _restore_from_mini(self, window) -> None:
        geometry = self._current_geometry(window)
        if geometry is None:
            print("[WebView] 取不到窗口几何，已跳过展开")
            return
        x, y, w, h = geometry
        nx, ny, nw, nh = window_layout.restore_from_mini(self._saved_geometry, x, y)
        window.move(nx, ny)
        window.resize(nw, nh)
        self._saved_geometry = None

    # ── 边缘拖拽改尺寸（无边框模式下的 resize 入口，由前端热区驱动；单位=逻辑像素）──
    def window_bounds(self) -> Dict[str, Any]:
        """当前窗口几何（逻辑像素，与 window.move/resize 同单位）。
        取不到时明确 ok=False，前端放弃拖拽（不乱动窗口）。"""
        window = self._window.get("window")
        geometry = self._current_geometry(window) if window is not None else None
        if geometry is None:
            return {"ok": False}
        x, y, w, h = geometry
        return {"ok": True, "x": x, "y": y, "w": w, "h": h}

    def window_resize_edge(self, edge: str, dx: int, dy: int,
                           start_x: int, start_y: int, start_w: int, start_h: int) -> bool:
        """按边改尺寸：前端每次 pointermove 重发"相对拖拽起点的总位移"，
        这里以起点几何计算目标（对侧锚定 + 最小尺寸由 window_layout.resize_edge 保证）。
        以起点为基准而非当前值累积，DPI 取整误差不会在连续拖帧中漂移。"""
        window = self._window.get("window")
        if window is None:
            return False
        try:
            nx, ny, nw, nh = window_layout.resize_edge(edge, start_x, start_y, start_w, start_h, dx, dy)
            window.move(nx, ny)
            window.resize(nw, nh)
            return True
        except Exception as exc:
            print(f"[WebView] 边缘改尺寸失败：{type(exc).__name__}: {exc}")
            return False

    # ── 自定义模型（OpenAI 兼容）───────────────────
    def custom_models(self):
        return self._svc.custom_models()

    def add_custom_model(self, name: str, model_id: str, base_url: str, api_key: str):
        return self._svc.add_custom_model(name, model_id, base_url, api_key)

    def remove_custom_model(self, name: str):
        return self._svc.remove_custom_model(name)

    # ── 安全工具 ─────────────────────────────────
    def health_scan(self, root_path: str = ""):
        return self._svc.health_scan(root_path)

    def behavior_profile(self):
        return self._svc.behavior_profile()

    # ── 工作流 ───────────────────────────────────
    def workflow_templates(self):
        return self._svc.workflow_templates()

    def workflow_start(self, template_id: str, path: str = "", name: str = ""):
        return self._svc.workflow_start(template_id, path, name)

    def workflow_status(self, instance_id: str):
        return self._svc.workflow_status(instance_id)

    def workflow_execute(self, instance_id: str):
        return self._svc.workflow_execute_next(instance_id)

    def workflow_approve(self, instance_id: str, step_id: str, approved: bool):
        return self._svc.workflow_approve(instance_id, step_id, bool(approved))

    def workflow_export(self, instance_id: str, format: str = "md"):
        return self._svc.workflow_export(instance_id, format)

    def workflow_export_formats(self):
        return self._svc.workflow_export_formats()

    # ── 插件（能力扩展）──────────────────────────
    def plugins_list(self):
        return self._svc.list_plugins()

    def plugin_set_enabled(self, plugin_id: str, enabled: bool):
        return self._svc.set_plugin_enabled(plugin_id, bool(enabled))

    def plugin_sync_state(self):
        """最近一次能力同步结果：注册了哪些模板、识别器是否生效、有无失败。"""
        return self._svc.plugin_sync_state()

    def plugin_config_read(self, plugin_id: str, name: str):
        return self._svc.plugin_config_read(plugin_id, name)

    def plugin_config_write(self, plugin_id: str, name: str, text: str):
        return self._svc.plugin_config_write(plugin_id, name, text)

    def plugin_selfcheck(self):
        """插件自检：确认每个声明的钩子都真的有同名函数（避免"启用了却没反应"）。"""
        return self._svc.plugin_selfcheck()

    def state_snapshot(self):
        """当前推理状态快照（供前端"忙碌看门狗"轮询）。

        为什么需要：事件推送是单向的，一旦某条事件在传输/渲染中丢失，
        前端会永远停在"推理中"。有了快照轮询，状态失联最多持续一个轮询周期。
        """
        return self._svc._state_payload(self._svc.current_session_id())

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
    def run(self, driver: Optional[Any] = None) -> None:
        """启动界面。driver 为可选的回调（真实使用驱动/自动化测试用）：
        非空时会以 `webview.start(driver, window)` 启动，驱动线程拿到窗口对象，
        其余装配（监听器、算力预热线程、事件缓存 flush）与生产路径完全一致——
        保证"驱动里测到的行为"就是"用户看到的行为"。"""
        self.service.initialize()
        self.service.set_listener(self._on_service_event)

        api = JsApi(self.service, self._window_holder)
        settings = self.service.settings.data
        # 无边框悬浮模式（产品标志性形态）：窗口本身无系统边框，由前端顶栏承担拖拽与窗口控制；
        # 若拖动异常，可在设置里关掉"无边框悬浮模式"回退到系统边框。
        frameless = bool(settings.get("frameless", True))
        window_kwargs: Dict[str, Any] = {
            "width": window_layout.EXPANDED_W,
            "height": window_layout.EXPANDED_H,
            # ★ min_size 必须按**折叠态**给：pywebview/WinForms 的 MinimumSize 只在创建窗口时
            #   作用到原生窗口，运行期改 `window.min_size` 不会生效——按展开态设会静默卡住折叠
            #   （实测：折叠请求 420×60，实际被卡回 880×600）。
            #   边缘拖拽改尺寸由前端热区 + window_resize_edge 实现，最小尺寸在那里按展开态 clamp，
            #   所以这里给折叠态的小值不会让用户把窗口缩到不可用。
            "min_size": window_layout.MINI_MIN_SIZE,
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

        if driver is not None:
            webview.start(driver, self._window, debug=bool(os.environ.get("YINDUN_WEB_DEBUG")))
        else:
            webview.start(debug=bool(os.environ.get("YINDUN_WEB_DEBUG")))


def run_web_ui() -> None:
    """入口：启动 Web 界面（供 run.py / main.py 调用）。"""
    if not INDEX_HTML.exists():
        raise SystemExit(f"前端资源缺失：{INDEX_HTML}")
    WebApp().run()


if __name__ == "__main__":
    run_web_ui()
