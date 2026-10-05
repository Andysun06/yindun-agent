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
from yindun.plugins.host import PluginHost

Listener = Callable[[str, Any], None]


def build_tools() -> List[Any]:
    """全部可用工具（与旧界面保持一致：9 个本地工具 + 1 个知识库检索）。"""
    return [
        list_local_files, create_local_file, delete_local_file, read_local_file,
        modify_local_file, run_local_command, analyze_project, search_in_files,
        read_attachment_chunk, search_knowledge_base,
    ]


def _time_stamp() -> str:
    """文件名用的时间戳（插件渲染导出失败时兜底命名）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class AgentService:
    """推理编排服务：一次对话的完整生命周期。"""

    def __init__(self,
                 settings: Optional[SettingsStore] = None,
                 sessions: Optional[SessionStore] = None,
                 plugin_data_root: Optional[Path] = None) -> None:
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
        # 知识库实例（懒加载并复用：Chroma + 本地 embedding 初始化较重）
        self._kb = None
        self._kb_error: Optional[str] = None
        # 用户本轮原话（供插件做"是否与请求相符"的判断；不含附件正文）
        self._last_user_request: str = ""
        # 工作流引擎与实例（引擎单例；实例仅在内存，重启即丢——已在界面注明）
        self._wf_engine = None
        self._wf_instances: Dict[str, str] = {}
        self._wf_running = False
        # 插件宿主：重依赖能力按需启用，内核保持精简
        # plugin_data_root 可注入（默认 <APP_ROOT>/plugins_data）：内置插件的可写数据落在这里，
        # 打包版解包目录只读，所以不能放插件自身目录内。
        self._plugins = PluginHost(
            enabled_lookup=lambda pid: bool((self.settings.get("plugins_enabled") or {}).get(pid, False)),
            enabled_setter=self._persist_plugin_enabled,
            model_lister=detect_ollama_models,
            data_root=plugin_data_root,
        )
        # 最近一次能力同步的结果（注册了哪些模板、识别器是否挂上），供界面与审计展示
        self._plugin_sync: Dict[str, Any] = {"templates": [], "templates_removed": [],
                                             "recognizer": False, "errors": []}

    # ── 生命周期 ──────────────────────────────────
    def initialize(self) -> None:
        """加载配置与会话（同步、只读磁盘，很快），并把插件能力挂到内核扩展点。"""
        self.settings.load()
        self.sessions.load()
        self._sync_plugin_capabilities()

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
        # ★ 本地回环与内网地址都绕过系统代理（Clash/V2Ray 会劫持 127.0.0.1 与内网段的请求，
        #   导致 langchain-ollama 调本地/内网 Ollama 时 502）。内网地址从设置里取。
        host_cfg = str(self.settings.get("ollama_host") or "http://127.0.0.1:11434")
        # 统一出口：知识库 / 工作流 / 状态探测等读取 OLLAMA_HOST 的组件都跟随设置（含内网地址）
        os.environ["OLLAMA_HOST"] = host_cfg
        try:
            from urllib.parse import urlparse
            h = (urlparse(host_cfg).hostname or "").strip()
            if h:
                parts = [p.strip() for p in os.environ.get("NO_PROXY", "").split(",") if p.strip()]
                if h not in parts:
                    parts.append(h)
                    os.environ["NO_PROXY"] = ",".join(parts)
                    os.environ["no_proxy"] = os.environ["NO_PROXY"]
        except Exception:
            pass
        # ★ 首启兜底（真实使用中发现）：settings 默认模型名可能与本机实际拉取的不一致
        #   （如 "qwen2.5:7b" vs "qwen2.5:7b-instruct"），ChatOllama 构建时不校验模型名，
        #   直到发消息才 404。这里把模型名对齐到本机实际存在的对话模型并如实告知。
        #   ★ 内网/远程 Ollama 例外：本机探测不到内网模型，"对齐"会把用户填的内网模型名
        #   覆盖成本机模型——非本地地址一律跳过兜底，信任用户配置。
        is_local_host = ("127.0.0.1" in host_cfg) or ("localhost" in host_cfg) or ("[::1]" in host_cfg)
        try:
            configured = str(self.settings.get("model") or "")
            custom = self.settings.get("custom_models") or {}
            if configured and configured not in custom and is_local_host:
                from yindun.app.llm_factory import detect_chat_models
                available = detect_chat_models()
                if available and configured not in available:
                    fallback = available[0]
                    self.settings.set("model", fallback)
                    self.settings.save()
                    self._emit("status", f"模型 {configured} 本机未安装，已改用 {fallback}")
        except Exception as exc:
            print(f"[AgentService] 模型名对齐失败（按原配置继续）：{exc}")
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
    # 内核原生解析的格式：这些格式以内核为准（插件不能顶替），插件只在内核解析不出内容时兜底。
    # 其余扩展名（.eml/.html/…）由声明了该扩展名的插件优先解析，内核仍是兜底。
    # 这条规则让"插件增强"与"内核兜底"同时成立：插件永远抢不走内核的既有能力，
    # 但可以补上内核本来就不支持的格式，并且解析结果一样要过脱敏管线。
    KERNEL_NATIVE_EXTS = frozenset({
        "pdf", "docx", "xlsx", "xls", "csv", "txt", "md",
        "mp3", "wav", "flac", "m4a", "aac", "ogg", "opus", "wma",
    })

    def supported_exts(self) -> List[str]:
        """文件对话框可选的扩展名 = 内核支持的 + 已启用插件声明的（内核优先）。"""
        from yindun.app.attachment import SUPPORTED_EXTS
        out = list(SUPPORTED_EXTS)
        try:
            for ext in self._plugins.claimed_exts():
                if ext not in out:
                    out.append(ext)
        except Exception as exc:
            print(f"[AgentService] 读取插件扩展名失败：{exc}")
        return out

    def attach_files(self, paths: List[str]) -> List[Dict[str, Any]]:
        """解析并挂载附件（同步；解析大文档时前端可显示等待态）。

        内核解析器优先；内核不认识的格式（如 .eml / .html）由声明了该扩展名的
        插件解析——**插件产出的文本同样要过隐私扫描与脱敏**，与内核路径完全一致。

        返回解析结果摘要列表：[{name, chars, error}]，供界面提示。
        """
        added: List[Dict[str, Any]] = []
        existing = {item.get("path") for item in self._attachments}
        for path in paths:
            if not path or path in existing:
                continue
            self._emit("status", f"正在解析附件：{Path(path).name} …")
            ext = Path(path).suffix.lower().lstrip(".")
            claimed = ext and ext not in self.KERNEL_NATIVE_EXTS and ext in self._plugin_exts()
            if claimed:
                # 插件优先（该格式不属内核原生）：插件接不住再回落到内核兜底解析
                record = self._parse_attachment_with_plugins(path)
                if not (record.get("text") or "").strip():
                    record = parse_attachment(path)
            else:
                record = parse_attachment(path)
                if not (record.get("text") or "").strip():
                    record = self._parse_attachment_with_plugins(path, record)
            self._attachments.append(record)
            added.append({"name": record["name"], "chars": record["chars"],
                          "error": record.get("error"), "via": record.get("via", "内核解析")})
        self._emit("status", "")
        self._emit("attachments", self.list_attachments())
        return added

    def _plugin_exts(self) -> List[str]:
        try:
            return self._plugins.claimed_exts()
        except Exception as exc:
            print(f"[AgentService] 读取插件扩展名失败：{exc}")
            return []

    def _parse_attachment_with_plugins(self, path: str,
                                       record: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """把附件交给声明了该扩展名的插件解析。

        插件返回的文本与内核路径同待遇：先做隐私扫描，再进入会话快照；
        送模型时同样走脱敏网关。插件失败 / 超时 / 返回空 → 保持原记录
        （record 为 None 时返回一条"未解析成功"的空白记录，交给内核兜底）。
        """
        base = dict(record) if isinstance(record, dict) else {
            "name": Path(path).name, "path": str(path), "text": "", "chars": 0, "error": None,
        }
        ext = Path(path).suffix.lower().lstrip(".")
        if not ext:
            return base
        try:
            results = self._plugins.call_hook("attachment_parser", {
                "path": str(path), "ext": ext, "name": base.get("name", ""),
                "max_chars": 200000,
            })
        except Exception as exc:
            print(f"[AgentService] 插件解析附件失败：{exc}")
            return base
        for item in results:
            text = (item.get("text") or "").strip()
            if not text:
                continue
            patched = dict(base)
            patched["text"] = text
            patched["chars"] = len(text)
            patched["error"] = None
            patched["via"] = f"插件解析（{item.get('source') or item.get('plugin_id')}）"
            note = (item.get("note") or "").strip()
            if note:
                patched["via"] += f"：{note}"
            if item.get("truncated"):
                patched["via"] += "（超长已截断）"
            try:      # 与内核路径一致的隐私扫描（只出报告，不改正文）
                from yindun.utils.privacy_scanner import PrivacyScanner
                PrivacyScanner().scan(text)
            except Exception:
                pass
            return patched
        if not (base.get("text") or "").strip() and not base.get("error"):
            base["error"] = "未能提取到文本（内核不解析该格式，插件也未接住）"
        return base

    def clear_attachments(self) -> None:
        self._attachments = []
        self._emit("attachments", [])

    def list_attachments(self) -> List[Dict[str, Any]]:
        return [{"name": item.get("name", ""), "chars": item.get("chars", 0),
                 "error": item.get("error"), "via": item.get("via", "内核解析")}
                for item in self._attachments]

    def attachment_count(self) -> int:
        return len(self._attachments)

    # ── 工作流 ───────────────────────────────────
    # 这些 handler 目前返回"（模拟）"结果（见 core/workflow.py 的注册处）。
    # 界面必须如实标注，不能让人以为它们真在采集数据/画图。
    DEMO_STEP_TOOLS = frozenset({"human_review", "fetch_data", "clean_data", "analyze_data", "generate_chart"})

    def _workflow(self):
        if self._wf_engine is None:
            from yindun.core.workflow import get_workflow_engine
            self._wf_engine = get_workflow_engine()
            # 实例现在会从磁盘恢复（重启不丢）：把"实例→模板"映射接回来，
            # 否则恢复出来的实例在界面上显示为"未跟踪"，没法继续执行/审批。
            try:
                for item in self._wf_engine.list_instances():
                    self._wf_instances.setdefault(item["instance_id"], item["template_id"])
            except Exception as exc:
                print(f"[AgentService] 同步工作流实例映射失败：{exc}")
        return self._wf_engine

    def workflow_templates(self) -> List[Dict[str, Any]]:
        try:
            return self._workflow().list_templates()
        except Exception as exc:
            print(f"[AgentService] 读取工作流模板失败：{exc}")
            return []

    # 各模板需要的上下文变量（界面只需给一个"文档/项目路径"）
    _WF_PATH_KEY = {
        "wf_contract_review": "contract_path",
        "wf_security_check": "project_path",
    }

    def workflow_start(self, template_id: str, path: str = "", name: str = "") -> Dict[str, Any]:
        """创建工作流实例。path 按模板映射到上下文变量（留空用沙箱目录）；name 为自定义实例名。

        变量名来源：内核模板查 `_WF_PATH_KEY`；插件模板用它在 manifest 里声明的 path_var。
        """
        try:
            engine = self._workflow()
            # 引擎契约：create_instance 返回【实例对象】，且实例 id 存在其 template_id 字段上
            instance = engine.create_instance(template_id, custom_name=(name or "").strip())
            if instance is None:
                return {"ok": False, "error": f"模板不存在：{template_id}"}
            instance_id = instance.template_id
            context: Dict[str, Any] = {}
            key = self._WF_PATH_KEY.get(template_id) or engine.external_path_var(template_id)
            if key:
                context[key] = (path or "").strip() or os.environ.get("SANDBOX_PATH", str(APP_ROOT))
            engine.set_instance_context(instance_id, context)
            self._wf_instances[instance_id] = template_id
            return {"ok": True, "instance_id": instance_id, "context": context,
                    "status": self.workflow_status(instance_id)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def workflow_status(self, instance_id: str) -> Dict[str, Any]:
        try:
            status = self._workflow().get_workflow_status(instance_id)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        for step in status.get("steps", []) or []:
            step["demo"] = step.get("tool_name") in self.DEMO_STEP_TOOLS
        status["demo_notice"] = "标记为「演示」的步骤目前返回模拟结果，未接入真实数据源。"
        mappable = [(sid, tid) for sid, tid in self._wf_instances.items() if sid == instance_id]
        status["tracked"] = bool(mappable)
        return status

    def workflow_execute_next(self, instance_id: str) -> Dict[str, Any]:
        """执行下一个可执行步骤（可能调用 LLM，故放后台线程；进度以 workflow 事件推送）。"""
        try:
            engine = self._workflow()
            step = engine.get_next_executable_step(instance_id)
            if step is None:
                return {"ok": False, "error": "没有可执行的步骤（可能都在等待审批或已完成）"}
            step_name, step_id = step.name, step.step_id
            with self._lock:
                if self._wf_running:
                    return {"ok": False, "error": "已有步骤正在执行"}
                self._wf_running = True
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        def _run() -> None:
            self._emit("workflow", {"phase": "start", "instance_id": instance_id, "step": step_name})
            try:
                result = engine.execute_step(instance_id, step_id)
                payload = {"phase": "done", "instance_id": instance_id, "step": step_name,
                           "success": bool(result.get("success")), "error": result.get("error"),
                           "status": self.workflow_status(instance_id)}
            except Exception as exc:
                payload = {"phase": "done", "instance_id": instance_id, "step": step_name,
                           "success": False, "error": f"{type(exc).__name__}: {exc}",
                           "status": self.workflow_status(instance_id)}
            finally:
                with self._lock:
                    self._wf_running = False
            self._emit("workflow", payload)

        threading.Thread(target=_run, daemon=True).start()
        return {"ok": True, "step": step_name}

    def workflow_approve(self, instance_id: str, step_id: str, approved: bool) -> Dict[str, Any]:
        try:
            engine = self._workflow()
            ok = engine.approve_step(instance_id, step_id, reviewer="user") if approved \
                else engine.reject_step(instance_id, step_id, reviewer="user")
            return {"ok": bool(ok), "status": self.workflow_status(instance_id)}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    CORE_WORKFLOW_FORMATS = ("md", "html", "json", "docx")

    def workflow_export_formats(self) -> List[Dict[str, Any]]:
        """可用的导出格式 = 内核自带（兜底，永远可选）+ 已启用插件贡献的。

        内核格式不因插件启停而消失——这是"内核兜底、插件增强"的落地约束：
        插件只能追加格式，不能替换或删掉内核格式。
        """
        out: List[Dict[str, Any]] = [
            {"format": "md", "label": "Markdown", "source": "内核", "plugin_id": ""},
            {"format": "html", "label": "HTML", "source": "内核", "plugin_id": ""},
            {"format": "docx", "label": "Word（兼容格式）", "source": "内核", "plugin_id": ""},
        ]
        try:
            for item in self._plugins.export_formats("workflow"):
                out.append({"format": item["format"],
                            "label": f"{item.get('label') or item['format']}（插件：{item['source']}）",
                            "source": item["source"], "plugin_id": item["plugin_id"]})
        except Exception as exc:
            print(f"[AgentService] 读取插件导出格式失败：{exc}")
        return out

    def workflow_export(self, instance_id: str, format: str = "md") -> Dict[str, Any]:
        """导出执行记录。

        · 内核格式：由引擎写盘并统一脱敏（原有行为）；
        · 插件格式：由插件（export_renderer）渲染，但**送进插件的数据来自已脱敏链路**
          （实例状态与报告正文），落盘仍走引擎的报告目录与文件名清洗。
        """
        fmt = (format or "md").strip().lower()
        try:
            engine = self._workflow()
            status = self.workflow_status(instance_id)
            title = f"{status.get('template_name', '工作流')}_执行记录"
            if fmt in self.CORE_WORKFLOW_FORMATS:
                path = engine._write_report_to_file(title, self._render_instance_report(instance_id), fmt)
                return {"ok": True, "path": str(path), "format": fmt, "renderer": "内核"}
            from datetime import datetime
            rendered = self._plugins.call_hook("export_renderer", {
                "kind": "workflow", "format": fmt,
                "payload": {"instance_id": instance_id, "status": status,
                            "report_markdown": self._render_instance_report(instance_id),
                            "generated_at": datetime.now().isoformat(timespec="seconds")},
            })
            for item in rendered:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                path = engine.write_rendered_report(title, item.get("text"),
                                                    item.get("ext") or fmt)
                return {"ok": True, "path": str(path), "format": fmt,
                        "renderer": f"插件 {item.get('source')}",
                        "plugin_id": item.get("plugin_id")}
            return {"ok": False, "error": f"没有插件提供该导出格式：{fmt}"}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def _render_instance_report(self, instance_id: str) -> str:
        status = self.workflow_status(instance_id)
        progress = status.get("progress", {}) or {}
        lines = [f"# {status.get('template_name', '工作流')} · 执行记录", "",
                 f"- 实例：{instance_id}",
                 f"- 进度：{progress.get('completed', 0)}/{progress.get('total', 0)} 步",
                 f"- 是否完成：{'是' if status.get('is_complete') else '否'}", "",
                 "| 步骤 | 状态 | 工具 | 结果摘要 |", "| --- | --- | --- | --- |"]
        for step in status.get("steps", []) or []:
            summary = str(step.get("result") or step.get("error") or "")[:80].replace("\n", " ")
            lines.append(f"| {step.get('name', '')} | {step.get('status', '')} | "
                         f"{step.get('tool_name', '')}{'（演示）' if step.get('demo') else ''} | {summary} |")
        return "\n".join(lines)

    # ── 自定义模型（OpenAI 兼容云端算力）─────────────
    def custom_models(self) -> List[Dict[str, Any]]:
        """列出已配置的外部模型（api_key 只说是否已配置，不回传明文）。"""
        models = self.settings.get("custom_models") or {}
        out = []
        for name, info in models.items():
            if not isinstance(info, dict):
                continue
            out.append({
                "name": name,
                "model_id": info.get("model_id", ""),
                "base_url": info.get("base_url", ""),
                "has_key": bool(info.get("api_key")),
                "key_invalid": bool(info.get("key_invalid")),
            })
        return out

    def add_custom_model(self, name: str, model_id: str, base_url: str, api_key: str) -> Dict[str, Any]:
        """新增/覆盖一个外部模型配置。api_key 由设置层加密落盘（api_key_enc）。"""
        name = (name or "").strip()
        model_id = (model_id or "").strip()
        base_url = (base_url or "").strip()
        if not name or not model_id or not base_url:
            return {"ok": False, "error": "名称、模型 ID、接口地址都不能为空"}
        if not base_url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "接口地址需以 http:// 或 https:// 开头"}
        models = dict(self.settings.get("custom_models") or {})
        models[name] = {"model_id": model_id, "base_url": base_url, "api_key": (api_key or "").strip()}
        self.settings.set("custom_models", models)
        self.settings.save()
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                f"新增外部算力配置：{name}",
                {"name": name, "model_id": model_id, "base_url": base_url,
                 "has_key": bool(api_key)},
            )
        except Exception:
            pass
        return {"ok": True, "models": self.custom_models()}

    def remove_custom_model(self, name: str) -> Dict[str, Any]:
        models = dict(self.settings.get("custom_models") or {})
        if name not in models:
            return {"ok": False, "error": f"未找到配置：{name}"}
        models.pop(name, None)
        self.settings.set("custom_models", models)
        # 若当前正在用这个模型，回退到本地模型，避免指向已删除的配置
        if self.settings.get("model") == name:
            from yindun.app.llm_factory import get_first_available_model
            fallback = get_first_available_model() or "qwen3.5:4b"
            self.settings.set("model", fallback)
            self._llm = None
        self.settings.save()
        return {"ok": True, "models": self.custom_models()}

    # ── 安全工具（健康扫描 / 行为画像）───────────────
    def health_scan(self, root_path: str = "") -> Dict[str, Any]:
        """扫描目标目录的敏感数据分布（用于"隐私健康体检"）。"""
        try:
            from yindun.core.health_scanner import HealthScanner
            target = (root_path or "").strip() or os.environ.get("SANDBOX_PATH", str(APP_ROOT))
            report = HealthScanner().scan(target, depth=3)
            return {"ok": True, "summary": report.summary(), "json": report.to_json(),
                    "target": target}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def behavior_profile(self, limit: int = 500) -> Dict[str, Any]:
        """基于审计日志生成"模型行为画像"与异常检测结果。"""
        try:
            from yindun.core.audit_log import AuditLog
            from yindun.core.behavior_analyzer import BehaviorAnalyzer
            entries = AuditLog().get_entries() or []
            analyzer = BehaviorAnalyzer()
            profile = analyzer.build_profile(entries[-max(50, min(int(limit or 500), 2000)):],
                                             session_id="", )
            anomalies = analyzer.detect_anomalies(entries, session_id="")
            # AnomalyRecord / 画像对象都是 dataclass（没有 to_dict），用 asdict 序列化
            from dataclasses import asdict, is_dataclass

            def _plain(obj: Any) -> Dict[str, Any]:
                if hasattr(obj, "to_dict"):
                    return obj.to_dict()
                if is_dataclass(obj):
                    return asdict(obj)
                return {"summary": str(obj)}

            return {
                "ok": True,
                "summary": profile.summary(),
                "profile": _plain(profile),
                "anomalies": [_plain(a) for a in (anomalies or [])][:20],
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ── 插件（能力扩展）───────────────────────────
    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出可用插件（内置 + 用户安装），含能力贡献、依赖满足情况与启用状态。"""
        try:
            return self._plugins.list_plugins()
        except Exception as exc:
            print(f"[AgentService] 插件发现失败：{exc}")
            return []

    def plugin_sync_state(self) -> Dict[str, Any]:
        """最近一次能力同步的结果：注册了哪些模板、识别器是否挂上、有没有失败。"""
        return dict(self._plugin_sync)

    def plugin_config_read(self, plugin_id: str, name: str) -> Dict[str, Any]:
        return self._plugins.read_config_file(plugin_id, name)

    def plugin_selfcheck(self) -> Dict[str, Any]:
        """逐个导入插件模块，确认声明的钩子都真的有同名函数。

        为什么必须有：钩子函数缺失时调用侧只会"什么都没发生"——用户以为启用了、
        其实一次都没生效。这种静默失效必须能被一次显式自检戳破。
        """
        try:
            return self._plugins.selfcheck()
        except Exception as exc:
            return {"ok": False, "plugins": [], "error": f"{type(exc).__name__}: {exc}"}

    def plugin_config_write(self, plugin_id: str, name: str, text: str) -> Dict[str, Any]:
        """写入插件声明的配置文件（如敏感词表）。

        为什么留痕：词表直接决定脱敏范围，改它等于改安全策略的一部分，
        与"启用插件"同级重要——审计里必须能看到"谁在什么时候改了词表"。
        """
        result = self._plugins.write_config_file(plugin_id, name, text)
        if result.get("ok"):
            try:
                from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
                AuditLog().add_entry(
                    AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                    f"插件配置更新：{plugin_id}/{name}",
                    {"plugin_id": plugin_id, "file": name, "bytes": result.get("bytes")},
                )
            except Exception:
                pass
        return result

    def set_plugin_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        """启用/停用插件，并**立即同步它贡献的能力**。

        能力变更写入审计（插件是可执行代码，必须留痕）；审计详情里带上同步结果，
        这样"启用了但没生效""停用了能力还在"这类问题在日志里一眼可查。
        """
        result = self._plugins.set_enabled(plugin_id, bool(enabled))
        sync = self._sync_plugin_capabilities() if result.get("ok") else {}
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL,
                AuditSeverity.WARNING if enabled else AuditSeverity.INFO,
                f"插件{'启用' if enabled else '停用'}：{plugin_id}",
                {"plugin_id": plugin_id, "enabled": bool(enabled),
                 "ok": bool(result.get("ok")), "error": result.get("error"),
                 "templates_registered": sync.get("templates"),
                 "templates_removed": sync.get("templates_removed"),
                 "recognizer_active": sync.get("recognizer"),
                 "sync_errors": sync.get("errors")},
            )
        except Exception:
            pass
        return result

    def _persist_plugin_enabled(self, plugin_id: str, enabled: bool) -> None:
        state = dict(self.settings.get("plugins_enabled") or {})
        state[plugin_id] = bool(enabled)
        self.settings.set("plugins_enabled", state)
        self.settings.save()

    # ── 能力同步：把"已启用插件"的能力挂到内核扩展点 ──
    def _sync_plugin_capabilities(self) -> Dict[str, Any]:
        """启动时与每次启停后调用。

        原则：**能力跟着开关走**。停用插件必须立刻收回它贡献的能力——
        "界面显示已停用、实际还在生效"对安全产品是欺骗级缺陷。
        """
        summary: Dict[str, Any] = {"templates": [], "templates_removed": [],
                                   "recognizer": False, "errors": []}

        # 1) 工作流模板：先收回该插件的历史模板，再按当前启用状态重新注册
        #    （引擎懒加载：没有任何模板插件、且引擎尚未创建时，不为了同步去初始化它）
        if self._wf_engine is not None or self._plugins.enabled_plugins("workflow_template"):
            try:
                engine = self._workflow()
                for plugin in self.list_plugins():
                    for template_id in engine.external_template_ids(plugin.get("id", "")):
                        if engine.unregister_external_template(template_id):
                            summary["templates_removed"].append(template_id)
                for item in self._plugins.call_hook("workflow_template", {"mode": "templates"}):
                    for spec in item.get("templates") or []:
                        result = engine.register_external_template(spec, item["plugin_id"])
                        if result.get("ok"):
                            summary["templates"].append(result["template_id"])
                        else:
                            summary["errors"].append(
                                f"{item['plugin_id']} / {result.get('template_id')}："
                                f"{result.get('error')}")
            except Exception as exc:
                summary["errors"].append(f"工作流模板同步失败：{type(exc).__name__}: {exc}")

        # 2) 识别器：有启用的识别类插件才挂适配器（没有挂载 = 脱敏路径零额外开销）
        try:
            from yindun.core.privacy_engine import (register_extra_recognizer,
                                                    unregister_extra_recognizer)
            if self._plugins.enabled_plugins("recognizer"):
                register_extra_recognizer("plugins", self._plugin_recognizer)
                summary["recognizer"] = True
            else:
                unregister_extra_recognizer("plugins")
        except Exception as exc:
            summary["errors"].append(f"识别器挂载失败：{type(exc).__name__}: {exc}")

        if summary["errors"]:
            for message in summary["errors"]:
                print(f"[AgentService] 能力同步：{message}")
        self._plugin_sync = summary
        return summary

    def _plugin_recognizer(self, text: str) -> List[Dict[str, Any]]:
        """内核脱敏流程的"追加识别器"适配器（由 PrivacyEngine 在脱敏前调用）。

        只返回**追加**命中的区间；内核会再复核一遍（越界 / 超长 / 跨行 / 重叠丢弃）。
        因此插件写坏了最坏是"没生效"，不存在"让内容免于脱敏"的路径。
        """
        try:
            results = self._plugins.call_hook("recognizer", {"text": text})
        except Exception as exc:
            print(f"[AgentService] 插件识别器调用失败：{exc}")
            return []
        spans: List[Dict[str, Any]] = []
        for item in results:
            spans.extend(item.get("spans") or [])
        return spans

    # ── 审计导出（内核格式 + 插件格式）─────────────
    CORE_AUDIT_FORMATS = ("json", "html")

    def audit_export_formats(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = [
            {"format": "json", "label": "JSON", "source": "内核", "plugin_id": ""},
            {"format": "html", "label": "HTML", "source": "内核", "plugin_id": ""},
        ]
        try:
            for item in self._plugins.export_formats("audit"):
                out.append({"format": item["format"],
                            "label": f"{item.get('label') or item['format']}（插件：{item['source']}）",
                            "source": item["source"], "plugin_id": item["plugin_id"]})
        except Exception as exc:
            print(f"[AgentService] 读取插件导出格式失败：{exc}")
        return out

    def export_audit(self, fmt: str = "json") -> str:
        """导出审计报告，返回落盘路径；失败时返回"导出失败：…"（沿用界面既有契约）。"""
        fmt = (fmt or "json").strip().lower()
        try:
            from yindun.core.audit_log import AuditLog
            log = AuditLog()
            if fmt in self.CORE_AUDIT_FORMATS:
                return log.save_report(fmt)
            import json as _json
            from datetime import datetime
            payload = _json.loads(log.export_report("json"))
            payload["generated_at"] = datetime.now().isoformat(timespec="seconds")
            rendered = self._plugins.call_hook("export_renderer", {
                "kind": "audit", "format": fmt,
                "payload": payload, "generated_at": payload["generated_at"],
            })
            for item in rendered:
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                ext = str(item.get("ext") or fmt)
                name = Path(str(item.get("suggested_name")
                                 or f"audit_report_plugin_{_time_stamp()}.{ext}")).name
                return log.save_rendered(item.get("text"), name)
            return f"导出失败：没有插件提供该格式（{fmt}）"
        except Exception as exc:
            return f"导出失败：{type(exc).__name__}: {exc}"

    def _emit_advisories(self, approval: Dict[str, Any]) -> None:
        """审批出现后，**在后台**向已启用的插件征集建议。

        注意：只有本地模型参与的判断才会慢，因此绝不能阻塞审批弹窗——
        弹窗先出（need_confirm 立即转发），建议算完再以 advisories 事件补发，
        界面在弹窗上增量显示；用户随时可以先做决定。
        """
        try:
            context = {
                "tool": approval.get("name"),
                "args": approval.get("args") or {},
                "path": approval.get("path"),
                "user_request": self._last_user_request,
                "model": self.settings.get("model"),
                "ollama_host": self.settings.get("ollama_host") or "http://127.0.0.1:11434",
            }
            advisories = self._plugins.call_hook("advisory_for_approval", context)
        except Exception as exc:
            print(f"[AgentService] 征集插件建议失败：{exc}")
            return
        if advisories:
            self._emit("advisories", advisories)

    # ── 知识库（脱敏 RAG）─────────────────────────
    def knowledge_base(self):
        """懒加载并复用 KnowledgeBase 实例；不可用时返回 None 并记录原因。"""
        if self._kb is not None or self._kb_error:
            return self._kb
        try:
            from yindun.core.knowledge_base import KnowledgeBase
            self._kb = KnowledgeBase()
        except Exception as exc:
            self._kb_error = f"{type(exc).__name__}: {exc}"
            print(f"[AgentService] 知识库初始化失败：{exc}")
        return self._kb

    def kb_status(self) -> Dict[str, Any]:
        """知识库状态：是否可用、embedding 模型、文档/片段数。"""
        kb = self.knowledge_base()
        if kb is None:
            return {"available": False, "docs": [], "stats": {},
                    "error": self._kb_error or "知识库未初始化"}
        try:
            available = bool(kb.is_available())
        except Exception as exc:
            return {"available": False, "docs": [], "stats": {}, "error": str(exc)}
        stats = {}
        docs = []
        if available:
            try:
                stats = kb.get_stats() or {}
                docs = kb.list_documents() or []
            except Exception as exc:
                print(f"[AgentService] 读取知识库状态失败：{exc}")
        return {
            "available": available,
            "embed_model": getattr(kb, "embed_model", ""),
            "docs": docs,
            "stats": stats,
            "error": None if available else "本地 embedding 不可用（请确认 Ollama 在线且已拉取 nomic-embed-text）",
        }

    def kb_add(self, paths: List[str]) -> Dict[str, Any]:
        """把文档入库（入库前逐块脱敏，向量库只存占位符）。"""
        kb = self.knowledge_base()
        if kb is None:
            return {"ok": False, "error": self._kb_error or "知识库未初始化"}
        files = [p for p in (paths or []) if p]
        if not files:
            return {"ok": False, "error": "未选择文件"}
        try:
            if not kb.is_available():
                return {"ok": False, "error": "本地 embedding 不可用（Ollama / nomic-embed-text 未就绪）"}
            self._emit("status", f"正在入库 {len(files)} 个文档（逐块脱敏后向量化）…")
            result = kb.add_documents(files)
            self._emit("status", "")
            return {"ok": True, "result": result, "status": self.kb_status()}
        except Exception as exc:
            self._emit("status", "")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def kb_remove(self, file_name: str) -> Dict[str, Any]:
        kb = self.knowledge_base()
        if kb is None:
            return {"ok": False, "error": self._kb_error or "知识库未初始化"}
        try:
            ok = bool(kb.remove_document(file_name))
            return {"ok": ok, "status": self.kb_status()}
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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
        self._last_user_request = text
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
            # 审批弹窗出现后，后台征集插件建议（不阻塞弹窗与用户决策）
            if topic == "need_confirm" and isinstance(payload, dict):
                threading.Thread(target=self._emit_advisories, args=(dict(payload),), daemon=True).start()
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
        # ★ 用记忆模块的中英文感知估算器（全项目唯一口径）：
        #   旧实现 len//2 与触发摘要压缩的估算器不是一套，看板数字和"何时压缩"对不上。
        from yindun.core.memory_manager import DEFAULT_MAX_HISTORY_TOKENS, estimate_tokens
        context_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in messages)
        return {
            "busy": self.busy,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "context_tokens": context_tokens,
            "context_limit": DEFAULT_MAX_HISTORY_TOKENS,
            "message_count": len(messages),
            "model": self.settings.get("model"),
            "privacy": bool(self.settings.get("privacy", True)),
            "permission": self.settings.get("permission"),
            "sandbox": os.environ.get("SANDBOX_PATH", str(APP_ROOT)),
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
