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
from webview.window import FixPoint   # 边缘改尺寸的单次原生 resize 锚点（pywebview ≥5）

from yindun import APP_ROOT, __display_version__
from yindun.app import window_layout
from yindun.app.agent_service import AgentService

WEB_DIR = Path(__file__).resolve().parent / "web"
INDEX_HTML = WEB_DIR / "index.html"


def doc_file_types(exts) -> tuple:
    """文件对话框的过滤器。

    ★ 扩展名之间**必须是分号**（V3.6.7 修真实缺陷）：pywebview 6.x 的校验正则是
      `^([\\w ]+)\\((\\*(?:\\.(?:\\w+|\\*))*(?:;\\*(?:\\.(?:\\w+|\\*))*)*)\\)$` —— 只认 `;`，
      用空格拼出来的 `文档 (*.pdf *.docx)` 会被判为非法过滤器并**直接抛错**，
      表现是"点添加文档/附件，弹窗根本不出现，只弹一句 打开文件对话框失败"。
      实测：空格分隔 → ValueError；分号分隔 → 正常解析。
    第二个过滤器放"所有文件"，是为了让用户**至少能选到**列表外的格式（解析器对未知扩展名
    有兜底：能读成文本就读，读不了会给出可读原因，而不是静默跳过）。
    """
    patterns = ";".join(f"*.{ext}" for ext in exts)
    return (f"文档 ({patterns})", "所有文件 (*.*)")


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
        # 态势行要展示真实运行态：可读写范围按内核的解析顺序取"当前生效值"
        # （设置 sandbox_dir > 环境变量 SANDBOX_PATH > 程序目录），不要另写一套 fallback。
        settings["sandbox"] = self._svc.worker_sandbox_root()
        # ★ 算力清单的关键字段放进首屏载荷（V3.6.8）：界面第一屏就要能**诚实地**显示
        #   "请选择模型"或"当前模型不可用 + 原因"，不必再等一次异步调用、也不靠猜。
        #   判定口径与 llm_status 同一处（llm_factory.model_catalog 的 current_available）。
        catalog = self._svc.model_catalog()
        model_catalog_brief = {
            "current": catalog.get("current", ""),
            "current_available": bool(catalog.get("current_available")),
            "local_note": catalog.get("local_note", ""),
            "local_detected": list(catalog.get("local_detected") or []),
        }
        return {
            "version": __display_version__,
            "settings": settings,
            "llm": self._svc.llm_status(),
            "model_catalog": model_catalog_brief,
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

    def set_session_permission(self, mode: str = ""):
        """设置**本对话**的权限档位（空 = 跟随设置页默认档）。改完立刻作用于下一次工具调用。"""
        return self._svc.set_session_permission(mode)

    def session_permission(self):
        """本对话生效的权限档位 + 是否被会话覆盖（界面初始化胶囊用）。"""
        return self._svc.effective_permission()

    # ── laya 风险分级接入（本机 / 局域网 / 正则回退）──
    def laya_status(self):
        return self._svc.laya_status()

    def laya_save(self, cfg: Dict[str, Any] = None):
        return self._svc.laya_save(cfg or {})

    def laya_probe(self, kind: str = "local", cfg: Dict[str, Any] = None):
        return self._svc.laya_probe(kind, cfg or {})

    def laya_try(self, cfg: Dict[str, Any] = None):
        return self._svc.laya_try(cfg or {})

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
            picked = window.create_file_dialog(
                _wv.OPEN_DIALOG, allow_multiple=True, file_types=doc_file_types(self._svc.supported_exts())
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

    def pick_directory(self):
        """选择「可读写范围（沙箱目录）」。

        用 pywebview 的原生目录选择器（`FOLDER_DIALOG`）；窗口未就绪、对话框不可用或用户
        取消都**如实**返回 `{"ok": False, "error": ...}`，让界面给出可读提示而不是静默无反应。
        这里只返回一个路径字符串，不做任何"这个路径能不能读写"的判定——判定在内核。
        """
        window = self._window.get("window")
        if window is None:
            return {"ok": False, "error": "窗口未就绪"}
        try:
            import webview as _wv
            from yindun.app.attachment import normalize_paths
            picked = window.create_file_dialog(_wv.FOLDER_DIALOG)
        except Exception as exc:
            return {"ok": False, "error": f"打开目录对话框失败：{exc}"}
        paths = normalize_paths(picked)
        if not paths:
            return {"ok": False, "error": "未选择目录（已取消）"}
        return {"ok": True, "path": paths[0]}

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

        ★ 另附 `chain_state` 与锚点健康度（anchor_health）：把"合法迁移（待重建锚点）"
          与"校验未通过（疑似被动手脚）"、以及"锚点未生效（防篡改能力降级）"在数据层
          就区分开——顶栏与审计面板据此显示可操作状态，而不是一律红字。
        """
        try:
            from yindun.core.audit_log import AuditLog
            log = AuditLog()
            detail = log.verify_chain_detail()
            health = log.anchor_health()
            raw_stats = log.get_stats() or {}
            by_type = {str(getattr(k, "value", k)): int(v)
                       for k, v in (raw_stats.get("by_type") or {}).items()}
            by_severity = {str(getattr(k, "value", k)): int(v)
                           for k, v in (raw_stats.get("by_severity") or {}).items()}
            chain_state = str(detail.get("state") or health.get("state") or "")
            anchor_dir = str(health.get("dir") or "")
            if chain_state == "migrated":
                anchor_note = "审计目录已迁移（待重建锚点）"
            elif chain_state == "degraded":
                anchor_note = "锚点未生效（防篡改能力降级）"
            elif health.get("fallback"):
                anchor_note = f"锚点已回退到备用位置：{anchor_dir or '未知'}"
            elif anchor_dir:
                anchor_note = f"锚点位置：{anchor_dir}"
            else:
                anchor_note = ""
            stats = {
                "total_entries": int(raw_stats.get("total_entries", 0)),
                # 以 verify_chain_detail 的结论为准（get_stats 内部也会校验，但拿不到原因）
                "chain_valid": bool(detail.get("valid")),
                "chain_reason": str(detail.get("reason") or ""),
                "chain_anchor": str(detail.get("anchor") or "none"),
                "chain_legacy": int(detail.get("legacy", 0)),
                # 机器可读状态：ok / empty / migrated / degraded / missing / tampered / no_key / accepted
                "chain_state": chain_state,
                "chain_anchor_path": str(health.get("path") or ""),
                "chain_anchor_dir": anchor_dir,
                "chain_anchor_degraded": bool(health.get("degraded")),
                "chain_anchor_fallback": bool(health.get("fallback")),
                "chain_anchor_note": anchor_note,
                "chain_anchor_migration_source": str(health.get("migration_source") or ""),
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
                    "chain_state": chain_state, "anchor_health": health,
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
            picked = window.create_file_dialog(
                _wv.OPEN_DIALOG, allow_multiple=True, file_types=doc_file_types(self._svc.supported_exts()))
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
                new_value = not bool(self._svc.settings.get("topmost", False))
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
            elif action == "toggle_maximize":
                return self._toggle_maximize(window)
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

    def _toggle_maximize(self, window) -> bool:
        """最大化 ⇄ 还原。返回是否真的执行了（折叠态下拒绝时返回 False）。

        ★ 与「极简闪发折叠」共存：折叠态下窗口被改成一条浮条（尺寸与位置都动过）。
          此时**拒绝**而不是"偷偷先展开再最大化"——展开是另一个显式动作；如果这里单方面
          还原几何，页面的 `body.is-mini` 还停在折叠态，界面会卡在"看起来最大化了、
          实际还是浮条/主界面仍隐藏"的半个状态里。前端会据此提示"先退出闪发模式"。
        ★ 与无边框 + 边缘热区共存：最大化交给系统（原生 SetWindowPos + WM_GETMINMAXINFO），
          前端热区只在非最大化时按起点几何改尺寸；最小尺寸约束来自创建窗口时的 min_size
          （折叠态尺寸），它只限制"最小"，不会把最大化卡回小窗；还原后回到折叠前的几何。
        ★ 降级：个别 pywebview 后端没有 maximize/restore，退回 toggle_fullscreen()，
          并在日志里**如实**写明降级（不假装成功）。最大化状态由 pywebview 维护
          （Window.maximized，随原生 maximized/restored 事件更新），我们不自己记账。
        """
        if self._saved_geometry is not None:
            print("[WebView] 当前处于闪发折叠态，已忽略最大化请求（先展开再最大化）")
            return False
        try:
            if bool(getattr(window, "maximized", False)):
                window.restore()
            else:
                window.maximize()
            return True
        except Exception as exc:
            print(f"[WebView] maximize/restore 不可用（{type(exc).__name__}: {exc}），退回全屏切换")
            window.toggle_fullscreen()
            return True

    def window_state(self) -> Dict[str, Any]:
        """窗口状态快照：{maximized, topmost, mini}（前端窗口按钮的图标/高亮用它校正）。

        为什么需要：置顶与最大化按钮必须显示**真实状态**，不能靠"点一下就翻转"记账——
        用户用系统快捷键、或窗口被别的方式改过状态时，本地记账会立刻与事实不符。
        这里直接读原生窗口：`maximized` 由 pywebview 随原生 maximized/restored 事件维护；
        `topmost` 读窗口的 on_top（窗口未就绪时回落已保存的设置项）；`mini` 取折叠标记
        （折叠前保存了几何 = 当前处于闪发浮条形态）。
        """
        window = self._window.get("window")
        return {
            "maximized": bool(getattr(window, "maximized", False)) if window is not None else False,
            "topmost": (bool(getattr(window, "on_top", False)) if window is not None
                        else bool(self._svc.settings.get("topmost", False))),
            "mini": self._saved_geometry is not None,
        }

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
        """按边改尺寸：前端每次 pointermove 重发"相对拖拽起点的总位移"（screenX/screenY，
        不随窗口自身移动而变化——clientX 相对视口原点，w/n 边拖拽会移动窗口本身，
        视口原点随之移动，clientX 混入"窗口位移"形成正反馈，是拖拽严重抖动的根因之一）。
        这里以起点几何计算目标（对侧锚定 + 最小尺寸由 window_layout.resize_edge 保证），
        并用**单次** resize(fix_point) 原生调用完成"改尺寸+锚定对侧边"——
        旧版 move+resize 是两次 SetWindowPos，两次之间窗口位置与尺寸短暂不一致（抖动另一根因）；
        pywebview 6.x 的 winforms resize 内部即一次 SetWindowPos。
        以起点为基准而非当前值累积，DPI 取整误差不会在连续拖帧中漂移。"""
        window = self._window.get("window")
        if window is None:
            return False
        try:
            nx, ny, nw, nh = window_layout.resize_edge(edge, start_x, start_y, start_w, start_h, dx, dy)
            # 锚点选择：拖哪条边就锚定对侧边（w→EAST、n→SOUTH），winforms 的 resize
            # 只看 EAST/SOUTH 两个位（缺省=北西角固定），单次 SetWindowPos 完成定位+改尺寸
            fix = FixPoint.EAST if "w" in edge else FixPoint.WEST
            if "n" in edge:
                fix = fix | FixPoint.SOUTH
            try:
                window.resize(nw, nh, fix)     # 单次原生调用（pywebview ≥5 支持 fix_point）
            except TypeError:
                window.move(nx, ny)            # 旧版 pywebview 兜底：两次调用（退化为轻微抖动）
                window.resize(nw, nh)
            return True
        except Exception as exc:
            print(f"[WebView] 边缘改尺寸失败：{type(exc).__name__}: {exc}")
            return False

    # ── 自定义模型（OpenAI 兼容：局域网 / 公网）─────
    def custom_models(self):
        return self._svc.custom_models()

    def model_catalog(self):
        """本机 / 局域网 / 公网三档算力清单（设置页分区 + 模型下拉分组）。"""
        return self._svc.model_catalog()

    def probe_model_endpoint(self, base_url, api_key="", name=""):
        """连通性探测（用户点「测试连接」才发起；地址来自输入框）。

        `name` 是从设置页捎来的**条目名**：密钥不回显，所以存过密钥之后输入框是空的，
        服务端据此回落到该条目已存的密钥去测（V3.6.7）——否则会拿空密钥撞 401，
        看起来像"保存后密钥丢了"。少了这个参数，pywebview 会因为多传一个位置参数直接抛错。
        """
        return self._svc.probe_model_endpoint(base_url, api_key, name)

    def add_custom_model(self, name: str, model_id: str, base_url: str, api_key: str,
                         tier: str = "", provider: str = "", api_format: str = "",
                         tags=None):
        """新增/覆盖外部模型。provider / api_format / tags 是 V3.6.8 起的可选供应商元数据
        （老前端只传五个位置参数也照常工作）。"""
        return self._svc.add_custom_model(name, model_id, base_url, api_key, tier,
                                          provider, api_format, tags)

    def remove_custom_model(self, name: str):
        return self._svc.remove_custom_model(name)

    def custom_model_key(self, name: str):
        """把某个供应商已存的**明文密钥**交给界面（用户点「查看」时才调用）。

        只有本机窗口能走到这里：`JsApi` 由 pywebview 挂在**回环随机 UUID** 路径上
        （见本文件顶部威胁模型），不经任何网络出口。每次调用都会在服务层写一条
        `access_control` WARNING 审计（记录"看了哪个供应商"，不记密钥本身）。
        """
        return self._svc.custom_model_key(name)

    def context_usage(self):
        """上下文容量构成（顶栏浮层用）：全部为后端真实取数，口径见服务层注释。"""
        return self._svc.context_usage()

    # ── 企业控制面（V3.5.0）──────────────────────
    def control_status(self):
        return self._svc.control_status()

    def control_pair(self, url, code, name=""):
        """用配对码接入控制面（IT 发码 → 员工机自助接入；默认不启用上报）。"""
        return self._svc.control_pair(url, code, name)

    def control_set_enabled(self, enabled: bool):
        """打开/关闭上报（开启时立即同步一次，失败如实返回）。"""
        return self._svc.control_set_enabled(bool(enabled))

    def control_sync(self):
        """立即同步：先取策略并生效，再上报（带上刚应用的策略版本）。"""
        return self._svc.control_sync()

    def control_clear_rules(self):
        """撤销控制面下发的本地规则（显式动作，写审计）。"""
        return self._svc.control_clear_rules()

    def control_auto(self, enabled: bool, interval_seconds: int = 300):
        return (self._svc.control_start_auto(int(interval_seconds)) if enabled
                else self._svc.control_stop_auto())

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
        allowed = {"model", "ollama_host", "privacy", "dark_mode", "topmost",
                   "thinking_depth", "permission", "think_mode", "allow_script_exec",
                   "sandbox_dir"}     # 可读写范围（V3.6.8）：空串 = 回到默认（SANDBOX_PATH → 程序目录）
        # 可读写范围走服务层专用入口（set_sandbox_dir）：目录必须真实存在、改完立即生效、
        # 并写一条 access_control 审计（放宽范围是有安全含义的动作）。失败如实返回 False，
        # 让界面提示用户，而不是"看起来改成功了、其实没生效"。
        if "sandbox_dir" in patch:
            applied = self._svc.set_sandbox_dir(str(patch.get("sandbox_dir") or ""))
            if not applied.get("ok"):
                return False
        for key, value in patch.items():
            if key in allowed and key != "sandbox_dir":
                self._svc.settings.set(key, value)
        self._svc.settings.save()
        # 权限档位改了要**立刻**落到内核（工具层每次调用现读环境变量）：
        # 只写配置文件的话，正在跑的轮次仍会用旧档位，那就是"假立即生效"。
        if "permission" in patch or "allow_script_exec" in patch:
            self._svc.apply_runtime_env()
        # 换模型 / 换 Ollama 地址（含内网）→ 后台重建算力，不用重启；
        # worker 在每轮开始时取 self._llm 的新引用，重建不会打断在跑的轮次。
        if any(k in patch for k in ("model", "ollama_host")):
            threading.Thread(target=self._svc.prepare_llm, daemon=True).start()
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
        self._api: Optional[JsApi] = None

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

    def _on_window_native_state(self) -> None:
        """原生最大化/还原事件 → 推给前端刷新窗口按钮（图标 / is-active / 禁用态）。"""
        if self._api is None:
            return
        try:
            self._on_service_event("window_state", self._api.window_state())
        except Exception as exc:
            print(f"[WebView] 推送窗口状态失败：{exc}")

    # ── 启动 ─────────────────────────────────────
    def run(self, driver: Optional[Any] = None) -> None:
        """启动界面。driver 为可选的回调（真实使用驱动/自动化测试用）：
        非空时会以 `webview.start(driver, window)` 启动，驱动线程拿到窗口对象，
        其余装配（监听器、算力预热线程、事件缓存 flush）与生产路径完全一致——
        保证"驱动里测到的行为"就是"用户看到的行为"。"""
        self.service.initialize()
        self.service.set_listener(self._on_service_event)

        api = JsApi(self.service, self._window_holder)
        self._api = api
        settings = self.service.settings.data
        # 无边框悬浮是**产品形态本身**（不是可选项）：窗口无系统边框，由前端顶栏承担拖拽与窗口控制。
        # V3.5.0 起移除了该开关——它只能在创建窗口时生效、改了要重启，与"所有设置立即生效"冲突；
        # 保留固定形态后既没有重启项，也不会出现"关了它界面还是无边框"的中间态。
        frameless = True
        window_kwargs: Dict[str, Any] = {
            "width": window_layout.EXPANDED_W,
            "height": window_layout.EXPANDED_H,
            # ★ min_size 必须按**折叠态**给：pywebview/WinForms 的 MinimumSize 只在创建窗口时
            #   作用到原生窗口，运行期改 `window.min_size` 不会生效——按展开态设会静默卡住折叠
            #   （实测：折叠请求 420×60，实际被卡回 880×600）。
            #   边缘拖拽改尺寸由前端热区 + window_resize_edge 实现，最小尺寸在那里按展开态 clamp，
            #   所以这里给折叠态的小值不会让用户把窗口缩到不可用。
            "min_size": window_layout.MINI_MIN_SIZE,
            "on_top": bool(settings.get("topmost", False)),   # 默认不置顶（与 settings_store 默认值一致）
            "confirm_close": False,
        }
        if frameless:
            # ★ 拖动只发生在**顶栏/浮条**（V3.6.4 修）：pywebview 的 `easy_drag=True` 会装一个
            #   **全局且完全不筛选元素**的 mousedown 监听（见 pywebview/js/customize.js），
            #   于是"在输入框里按下拖动"也会把整个窗口拖走（用户实测反馈），文本选择同时失效。
            #   改用 pywebview 的官方"拖动区域"机制：只有带 `.pywebview-drag-region` 的元素
            #   （顶栏、闪发浮条）及其子元素能拖动窗口，其余区域一律不接管鼠标事件。
            webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False   # 区域内任意位置都可拖
            window_kwargs.update({"frameless": True, "easy_drag": False})
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

        # 最大化 / 还原由**原生事件**驱动：pywebview 内部已经维护 Window.maximized，
        # 这里再订阅一次是为了**主动通知前端**刷新窗口按钮图标——用户用系统手段最大化
        # （双击标题栏、Win+↑ 等）时，界面不能停在旧图标上。订阅失败不影响功能
        # （下一次点按钮时 window_state() 仍会读到真实状态）。
        for event_name in ("maximized", "restored"):
            try:
                getattr(self._window.events, event_name).__iadd__(self._on_window_native_state)
            except Exception as exc:
                print(f"[WebView] 订阅原生窗口事件 {event_name} 失败：{exc}")

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
