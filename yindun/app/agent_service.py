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
from yindun.app.llm_factory import (TIER_LABEL, build_llm_variants, detect_chat_models,
                                    normalize_base_url,
                                    detect_ollama_models, infer_tier, model_catalog,
                                    probe_endpoint, tier_of)
from yindun.core.permission import PERMISSION_ENV, resolve_mode
from yindun.app.session_store import SessionStore
from yindun.app.settings_store import SettingsStore
from yindun.core.file_tools import (
    analyze_project,
    batch_rename_local_files,
    copy_local_file,
    create_local_file,
    delete_local_file,
    list_local_files,
    make_local_dir,
    modify_local_file,
    move_local_file,
    read_attachment_chunk,
    read_local_file,
    rename_local_file,
    run_local_command,
    search_in_files,
    search_knowledge_base,
)
from yindun.worker.agent_worker import WORKER_EVENTS, Worker
from yindun.plugins.host import PluginHost
from yindun.utils.logger import get_logger

# 服务层的"某件事没做成"（模型预热、插件扩展名、工作流映射、审计写入）以前只 print，
# 打包成 exe 后现场完全看不到——而这些恰恰是排障时最需要的第一手线索。
log = get_logger("service")

Listener = Callable[[str, Any], None]


def build_tools() -> List[Any]:
    """全部可用工具：本地读写 + 文件管理（移动/复制/改名/批量改名/建目录）+ 知识库检索。

    ★ 文件管理族是 V3.6.8 补上的（负责人实测："让他移动几个文件、批量改后缀，他说做不了"）：
    没有这些工具时，模型只能用"读出来 + 新建 + 删掉"硬拼，批量改名更是无路可走——
    能力面缺一块，用户感受到的就是"这个智能体没用"。
    """
    return [
        list_local_files, create_local_file, delete_local_file, read_local_file,
        modify_local_file, move_local_file, copy_local_file, rename_local_file,
        batch_rename_local_files, make_local_dir,
        run_local_command, analyze_project, search_in_files,
        read_attachment_chunk, search_knowledge_base,
    ]


def _time_stamp() -> str:
    """文件名用的时间戳（插件渲染导出失败时兜底命名）。"""
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M%S")


# ── 自定义模型的「供应商」元数据（V3.6.8）────────────────────
# 「模型接入」页按供应商聚合（照 ZCode 的模型设置：左列供应商 / 右侧表单与模型列表）。
# 存储仍是 `custom_models[条目名] = {model_id, base_url, api_key, tier, ...}`——一条模型
# 一行；`provider` 只是**可选**的分组显示名，老配置没有它，所以必须能按 base_url 兜底
# 归组（同一台机器 / 同一个云服务的多条模型要落到同一组，否则左栏会散成一堆单模型分组）。
API_FORMATS = ("chat_completions",)      # 目前**只有** OpenAI 兼容 Chat Completions 真能用
API_FORMAT_LABEL = {"chat_completions": "OpenAI 兼容 Chat Completions"}


def clean_tags(value: Any) -> List[str]:
    """标签数组净化：只留非空字符串、去重、最多 8 个、每个 ≤ 24 字。

    标签是**用户自填**的自由文本（配置可能被手工改坏），进界面、进审计前统一在这里收口。
    另有一条产品口径：标签只许标真实能力——我们没有多模态直连通路（图片走本地 OCR），
    所以界面上不提供「视觉」一类预设标签，也不在代码里预置任何能力暗示。
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        text = str(item or "").strip()[:24]
        if text and text not in out:
            out.append(text)
        if len(out) >= 8:
            break
    return out


def provider_of(name: str, info: Dict[str, Any]) -> str:
    """供应商显示名：显式 `provider` 字段优先，缺失时按 base_url 的 host 兜底。

    兜底顺序说明：host（含端口，能把"同一主机的两个服务"分开）→ 条目名 → 「未命名供应商」。
    绝不用 model_id 兜底：模型名相同而供应商不同的情况很常见，那样会把两家并成一组。
    """
    text = str((info or {}).get("provider") or "").strip()
    if text:
        return text[:40]
    base = str((info or {}).get("base_url") or "").strip()
    try:
        from urllib.parse import urlparse
        host = (urlparse(base).netloc or "").strip()
    except Exception:
        host = ""
    return host[:40] or str(name or "").strip()[:40] or "未命名供应商"


def api_format_of(info: Dict[str, Any]) -> str:
    """接口格式：只有 `chat_completions` 是**真能用**的，其它值一律回落（不认识的格式不能假装支持）。"""
    value = str((info or {}).get("api_format") or "").strip().lower()
    return value if value in API_FORMATS else "chat_completions"


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
        # 两套算力变体：{False: 关掉模型思考的算力, True: 保持模型默认的算力}（V3.5.9）
        # 见 llm_factory.reasoning_for_mode：实测思考型模型不关思考要 42~92 秒、关掉 0.7~3.1 秒
        self._llm_variants: Dict[bool, Any] = {}
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
        self._laya_service: Any = None      # laya 接入服务（懒建，避免启动时做 import 探测）

    # ── 生命周期 ──────────────────────────────────
    def initialize(self) -> None:
        """加载配置与会话（同步、只读磁盘，很快），并把插件能力挂到内核扩展点。"""
        self.settings.load()
        self.sessions.load()
        # ★ 存量明文主动迁移（红队评审整改）：旧版本可能把消息/附件全文以明文写进
        #   会话文件；读取是兼容的，但只有 save() 才把文件整体重写为加密格式——
        #   若用户从不触发保存，明文会长期留在磁盘上。启动即检测并迁移一次。
        self.sessions.migrate_legacy_plaintext()
        self._sync_plugin_capabilities()
        # 启动即下发权限档位与严格模式：界面还没画出来时，工具层也已经按设置行事
        self.apply_runtime_env()
        # 已启用的插件（如 laya 本机模型）在后台预热：否则每次重启后的第一次审批
        # 都要重新吃一次冷启动（实测 17~25 秒），第一次分级就永远看不到模型结论
        self._plugins.preload_enabled()
        # ★ 可读写范围（沙箱根）也要下发：内核解析顺序是「设置 sandbox_dir > 环境变量
        #   SANDBOX_PATH > 程序目录」，而 core 不反向 import app 层，所以由这里把"读设置"的
        #   **只读**回调注入内核（core/permission 提供的注册口）。注入读取器而非快照：
        #   设置一改，下一次工具调用就按新范围判定（立即生效）。
        #   空串 = 未配置 → 内核按旧规则回退，行为与改前完全一致。
        try:
            from yindun.core.permission import register_sandbox_settings_source
            register_sandbox_settings_source(lambda: self.settings.get("sandbox_dir", ""))
        except Exception as exc:
            log.warning("注册可读写范围读取器失败（将按环境变量回退）：%s", exc)

    def set_listener(self, listener: Optional[Listener]) -> None:
        self._listener = listener

    def _llm_for(self, think_mode: Any = None) -> Any:
        """按当前「快速 / 深度思考」挑算力变体（V3.5.9）。

        背景（实测）：思考型模型不关思考时，一句简单提问要 42~92 秒，关掉只要 0.7~3.1 秒。
        两套变体在 `prepare_llm` 时一次建好，这里只做选择——**切模式不必重建算力**，
        也不会打断正在跑的轮次（worker 在每轮开始时取一次）。
        取不到变体（例如只有一套可用）时回落到 `_llm`，绝不返回 None 让调用方去猜。
        """
        from yindun.app.llm_factory import reasoning_for_mode
        key = reasoning_for_mode(think_mode if think_mode is not None
                                 else self.settings.get("think_mode"))
        variants = self._llm_variants or {}
        return variants.get(False if key is False else True) or variants.get(False) or self._llm

    def apply_runtime_env(self, session_id: Optional[str] = None) -> str:
        """把设置里的「工具层即时生效」开关下发给内核（环境变量是界面与工具层之间的唯一协议）。

        管两件事：**权限档位**（plan/ask/auto/full）与**脚本执行开关**（严格模式）。
        权限档位取**本对话生效值**：会话单独设过就用会话的，没设过才用设置页的新对话默认档
        （permission 是"这件事我打算让 AI 做到什么程度"的上下文，按会话记才符合实际使用）。

        工具函数与策略引擎**每次调用**都会重新读这两个变量，所以：
          · 启动 / 切会话 / 每轮开始时调用 → 保证取值与当前上下文一致；
          · 设置页或输入区胶囊改动时立即再调一次 → 正在跑的轮次里下一个工具调用就生效。
        返回值是落到环境变量里的稳定档位键，便于调用方校验/断言。
        """
        sid = session_id or self.current_session_id()
        session_mode = self.sessions.get_permission(sid) if sid else None
        mode = resolve_mode(session_mode or self.settings.get("permission"))
        os.environ[PERMISSION_ENV] = mode
        os.environ["ALLOW_SCRIPT_EXEC"] = "1" if self.settings.get("allow_script_exec", True) else "0"
        # ★ 可读写范围同样在这里下发（V3.6.8）：内核已能直接读设置，但服务层自己还有几处
        #   按 SANDBOX_PATH 取"当前范围"（worker.sandbox_path、工作流默认目录、健康扫描、
        #   状态快照）——不投影就会出现"工具判定按设置、默认目录按旧环境变量"的两套真相。
        self._project_sandbox_env()
        return mode

    def _project_sandbox_env(self) -> str:
        """把设置里的「可读写范围」（sandbox_dir）投影到 SANDBOX_PATH，返回当前生效的绝对路径。

        语义（键名是内核契约，见 core/permission.SETTINGS_KEY）：
          · sandbox_dir 非空 → 写入 SANDBOX_PATH（绝对化），并记住"投影前的原值"；
          · sandbox_dir 为空串 → 还原投影前的原值（空串 = 未配置：按旧规则
            SANDBOX_PATH → 程序目录回退；不能把外部/脚本设的范围一并清掉）。
        """
        configured = str(self.settings.get("sandbox_dir", "") or "").strip()
        if configured:
            resolved = os.path.abspath(configured)
            if not getattr(self, "_projected_sandbox", None):
                # 第一次投影：先留存原值，用户把设置清空时才能还原（否则会退到程序目录，
                # 把"管理员/脚本用 SANDBOX_PATH 指定的范围"丢掉——那是静默改安全边界）
                self._sandbox_env_backup = os.environ.get("SANDBOX_PATH")
            os.environ["SANDBOX_PATH"] = resolved
            self._projected_sandbox = resolved
            return resolved
        projected = getattr(self, "_projected_sandbox", None)
        if projected and os.environ.get("SANDBOX_PATH") == projected:
            backup = getattr(self, "_sandbox_env_backup", None)
            if backup:
                os.environ["SANDBOX_PATH"] = backup
            else:
                os.environ.pop("SANDBOX_PATH", None)
            self._projected_sandbox = None
            self._sandbox_env_backup = None
        from yindun.core.permission import sandbox_root
        return sandbox_root()

    def set_sandbox_dir(self, path: str = "") -> Dict[str, Any]:
        """设置「可读写范围」（设置键 `sandbox_dir`；空串 = 回到默认）。

        这是"把范围改到桌面/某个资料目录"的唯一入口（界面上的目录选择器最终调它）：
          · 非空时必须是**已存在的目录**——不自动创建：写错一个盘符就默默新建一棵空树，
            比直接报错更难排查；失败时保留原值（不静默改动安全设置）。
          · 改完**立即生效**（内核每次调用现读），并写审计（放宽可读写范围是有安全含义的动作）。
        """
        raw = str(path or "").strip()
        if raw:
            resolved = os.path.abspath(os.path.expanduser(raw))
            if not os.path.isdir(resolved):
                return {"ok": False, "error": f"目录不存在或不是目录：{resolved}", "sandbox_dir": raw}
            value: Optional[str] = resolved
        else:
            value = ""
        self.settings.set("sandbox_dir", value)
        self.settings.save()
        effective = self._project_sandbox_env()
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                f"可读写范围改为：{value or '默认（环境变量 → 程序目录）'}",
                {"sandbox_dir": value or "", "effective": effective, "scope": "settings"},
            )
        except Exception as exc:
            print(f"[AgentService] 可读写范围改动写审计失败：{exc}")
        return {"ok": True, "sandbox_dir": value or "", "effective": effective}

    def effective_permission(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """本对话生效的权限档位 + 它是"会话覆盖"还是"跟随默认"（界面据此显示与提示）。"""
        sid = session_id or self.current_session_id()
        session_mode = self.sessions.get_permission(sid) if sid else None
        default_mode = resolve_mode(self.settings.get("permission"))
        return {
            "session_permission": resolve_mode(session_mode) if session_mode else None,
            "default_permission": default_mode,
            "effective": resolve_mode(session_mode) if session_mode else default_mode,
        }

    def set_session_permission(self, mode: str = "") -> Dict[str, Any]:
        """设置**本对话**的权限档位；空值 = 清除覆盖、回到设置页的默认档。
        改完立刻下发到内核环境变量（下一个工具调用即按新档位判定）。"""
        sid = self.current_session_id()
        if not sid:
            return {"ok": False, "error": "没有活动会话"}
        value = resolve_mode(mode) if str(mode or "").strip() else None
        if not self.sessions.set_permission(sid, value):
            return {"ok": False, "error": "会话不存在"}
        self.sessions.save()
        self.apply_runtime_env(sid)
        self._audit_permission_change(sid, value)
        out = {"ok": True}
        out.update(self.effective_permission(sid))
        return out

    def _audit_permission_change(self, session_id: str, value: Optional[str]) -> None:
        """档位改动写审计：放宽权限是有安全含义的动作，必须留痕（哪次会话、改成什么）。
        用既有的 ACCESS_CONTROL 事件类型 + WARNING 级别，不新增枚举（避免审计台账出现无人认识的事件名）。"""
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                f"本对话权限档位改为：{value or '跟随设置默认'}",
                {"session_id": session_id, "permission": value or "default", "scope": "session"},
            )
        except Exception as exc:
            print(f"[AgentService] 权限改动写审计失败：{exc}")

    def audit_anchor_health(self) -> Dict[str, Any]:
        """审计链锚点健康度（供界面/状态事件如实展示"锚点是否生效、落在哪"）。

        为什么服务层要转发一层：内核（audit_log）必须与界面无关，界面拿不到内核对象；
        由服务层把这个只读结论递出去，顶栏「审计链」与审计面板据此显示
        "已迁移（待重建锚点）"/"锚点未生效（降级）"，而不是一律红字。
        """
        try:
            from yindun.core.audit_log import AuditLog
            return AuditLog().anchor_health()
        except Exception as exc:
            return {"state": "unknown", "valid": False, "degraded": False, "fallback": False,
                    "dir": "", "path": "", "migration_source": "",
                    "reason": f"锚点健康检查失败：{type(exc).__name__}: {exc}"}

    def _emit_anchor_advisory(self) -> None:
        """锚点降级/回退/迁移时，走**既有 status 事件**让用户在界面上看到（不是只写日志）。

        为什么必须发出来：锚点写不进去时，"尾部截断 / 降级改写"就重新不可发现，
        而旧实现只在日志里刷 ERROR —— 用户对"防篡改已失效"毫不知情（BUG-0002）。
        """
        try:
            health = self.audit_anchor_health()
        except Exception:
            return
        state = str(health.get("state") or "")
        if state == "degraded":
            self._emit("status", "⚠️ 审计链锚点未生效（防篡改能力降级）："
                                 + (health.get("reason") or "锚点写不进去，尾部截断将无法被发现"))
        elif state == "migrated":
            self._emit("status", "检测到审计目录已迁移（待重建锚点）："
                                 "请在「审计」面板点「重建锚点」恢复校验")
        elif health.get("fallback"):
            self._emit("status", "审计链锚点已回退到备用位置："
                                 f"{health.get('dir') or '未知位置'}（首选位置不可写，防篡改仍生效）")

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
        # ★ 首启体验（V3.6.0）：整个过程分阶段播报给界面。首次启动时用户要等的其实有三段——
        #   ① onefile 解压（在 Python 起来之前，界面还没出现，只能靠文档说明）；
        #   ② 探测算力 + 建算力（秒级）；③ **把模型载入内存**（本机实测 20~60 秒）。
        #   以前这三段全静默，用户看到的是"界面出来了但点了没反应"，容易以为是坏的。
        self._emit("startup", {"phase": "probe", "done": False,
                               "text": "正在检测本机算力与模型…"})
        # ★ 审计锚点自检（V3.6.x，BUG-0002）：锚点写不进去 / 已迁移 / 回退到备用位置，
        #   都要在启动时**主动告诉用户**，而不是等他自己去翻审计面板或日志。
        self._emit_anchor_advisory()
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
        # ★ 模型选择（V3.6.8 修"对着不存在的模型说就绪"）：
        #   · 从未选过（配置为空）→ 自动选用检测到的第一个可对话本机模型并落库；
        #     一个都检测不到就保持空 → 界面显示"请选择模型"，绝不凭空写一个模型名。
        #   · 用户选过（非空）→ **一律不改**：本机没装就是"不可用 + 原因"，
        #     由 llm_status/_diagnose_llm_unavailable 如实告知，绝不静默换成别的模型。
        model_name, model_available, unavailable_reason = self._resolve_model_selection()
        if not model_available:
            # 不可用就**不构建算力**：宁可如实说"未就绪 + 原因"，也不要建出一个指向
            # 不存在模型的对象再对外宣称"已就绪"（ChatOllama 不校验模型名，构建永远
            # "成功"，直到用户发第一句话才 404——那就是假就绪，负责人实测过）。
            self._llm_variants = {}
            self._llm = None
            self._tools_map = build_tools()
            self._llm_error = unavailable_reason or "算力不可用"
            status = self.llm_status()
            self._emit("llm_status", status)
            self._emit("startup", {"phase": "ready", "done": True,
                                   "text": f"算力不可用：{self._llm_error}"})
            return status
        # 一次建好两套算力变体（关思考 / 保持模型默认），每轮按当前模式挑一套用：
        # 切「快速 / 深度思考」不需要重建算力，也不会打断正在跑的轮次。
        self._llm_variants, self._tools_map, models, err = build_llm_variants(
            self.settings.data, build_tools())
        self._llm = self._llm_for(self.settings.get("think_mode"))
        self._llm_error = err
        self._llm_model = model_name
        if models:
            self.settings.set("ollama_models_cache", models)
            self.settings.save()
        status = self.llm_status()
        self._emit("llm_status", status)
        # ★ 第三段等待：把模型载入内存（本机实测 20~60 秒）。放在后台，不阻塞界面可用性。
        #   注意"就绪"必须等**预热真的结束**才播报：早发一次会让界面提前收起提示，
        #   用户以为可以问了、其实第一句话仍在等加载（第一版就是这么错的）。
        if not self._warmup_llm():
            self._emit("startup", {"phase": "ready", "done": True,
                                   "text": f"已就绪：{self.settings.get('model') or ''}"})
        return status

    def _warmup_llm(self) -> bool:
        """后台把聊天模型载入内存（V3.6.0）。返回是否已在后台预热（True 时由它负责播报就绪）。

        为什么要做：Ollama 是**首次调用**才把权重读进内存的，本机实测这一下要 20~60 秒。
        不预热的话，用户点开界面问第一句话就等于在等这个加载——看起来像"卡住了"。
        边界（很重要）：
          · 只对**本机回环 / 局域网**的 Ollama 端点预热；自定义的公网 OpenAI 兼容端点按次计费，
            不该被我们偷偷调用一次；
          · 用"关思考"的变体预热（最省时），预热的是服务端同一个模型实例；
          · 失败只记一条状态，绝不影响算力可用性。
        """
        try:
            from urllib.parse import urlparse
            cfg = self.settings.data if isinstance(self.settings.data, dict) else {}
            model = str(self.settings.get("model") or "")
            if not model or model in (cfg.get("custom_models") or {}):
                return False      # 自定义（公网）模型不预热，直接算就绪
            host = str(self.settings.get("ollama_host") or "http://127.0.0.1:11434")
            h = (urlparse(host).hostname or "").strip().lower()
            is_local = (h in ("127.0.0.1", "localhost", "::1") or h.startswith("10.")
                        or h.startswith("192.168.") or h.startswith("172."))
            if not is_local:
                return False      # 非本机/内网端点不预热（按次计费的不该被我们偷偷调用）
            llm = (self._llm_variants or {}).get(False)
            if llm is None:
                return False

            # 播报"开始载入"必须落在**启动预热线程之前**：这一条是"还要等多久"的唯一提示，
            # 缩进错一层它就会静默（第一版就是这么错的，实测事件流里看不到它）。
            self._emit("startup", {"phase": "load", "done": False,
                                   "text": f"正在把模型载入内存：{model}（首次约 20~60 秒，之后不用再等）…"})

            def _run() -> None:
                import time as _t
                t0 = _t.time()
                try:
                    from langchain_core.messages import HumanMessage
                    llm.invoke([HumanMessage(content="就绪")])
                    self._emit("status", f"模型已载入内存（{_t.time() - t0:.0f} 秒），可以开始提问")
                    self._emit("startup", {"phase": "ready", "done": True,
                                           "text": f"已就绪：{model}"})
                except Exception as exc:
                    print(f"[AgentService] 模型预热失败（不影响使用，首次提问会慢一些）：{exc}")
                    self._emit("startup", {"phase": "ready", "done": True,
                                           "text": f"已就绪：{model}（首次提问可能慢一些）"})

            threading.Thread(target=_run, daemon=True, name="yindun-llm-warmup").start()
            return True
        except Exception as exc:
            print(f"[AgentService] 模型预热调度失败：{exc}")
            return False

    # ── 模型选择（首启自动选 + 不可用如实报）─────────────────
    def _resolve_model_selection(self) -> Any:
        """决定本次要用的模型名，返回 (model, available, reason_if_unavailable)。

        ★ 为什么要有这一步（V3.6.8，负责人实测）：便携版拷到没有该模型的机器上，
          界面与 llm_status 仍宣称"已就绪: qwen3.5:4b"——对着一个不存在的模型说就绪。
          规则：
            · 配置为空（用户从未选过）→ 自动选用**检测到的第一个可对话模型**并落库；
              一个都检测不到就保持空 → 界面显示"请选择模型"，绝不凭空写一个模型名。
            · 配置非空（用户选过）→ **一律不改**：本机没装就是"不可用 + 原因"。
          可用性判据统一用 `model_catalog()["current_available"]`（本机探测 + 自定义条目
          一起算，见 llm_factory），不另立第二套判断。
        """
        catalog = self.model_catalog()
        current = str(self.settings.get("model") or "").strip()
        if not current:
            picked = ""
            for name in (catalog.get("local_detected") or []):
                if str(name or "").strip():
                    picked = str(name).strip()
                    break
            if not picked:
                for entry in (catalog.get("tiers", {}).get("local") or []):
                    if str(entry.get("name") or "").strip():
                        picked = str(entry["name"]).strip()
                        break
            if not picked:
                # 检测不到任何可对话模型：保持空（不写死名字），由界面提示"请选择模型"
                return "", False, self._diagnose_llm_unavailable(catalog=catalog)
            self.settings.set("model", picked)
            self.settings.save()
            self._emit("status", f"未选择过模型，已自动选用本机检测到的 {picked}")
            catalog = self.model_catalog()
            current = picked
        if bool(catalog.get("current_available")):
            return current, True, ""
        return current, False, self._diagnose_llm_unavailable(catalog=catalog)

    def llm_status(self) -> Dict[str, Any]:
        """算力状态：就绪与否 + **为什么不可用**（V3.6.2，路线图 A-1）。

        为什么补这条说明：把算力端点指到一个没人监听的端口时，旧实现只回 `ready:false`、
        `error:null`——界面上就是"未就绪"三个字，用户无从判断该去检查什么。
        演示现场第一号风险就是"Ollama 没起"，产品必须自己把话说明白。

        ★ V3.6.8：状态必须与事实一致——`ready` 只在"算力已建好 **且** 当前模型真的
          在可用清单里"时为真。配置了一个本机不存在的模型时如实报"不可用 + 原因"，
          绝不再出现"模型不存在却报已就绪"。
        """
        detail = self._llm_error
        if self._llm is None and not detail:
            detail = self._diagnose_llm_unavailable()
        try:
            catalog = self.model_catalog()
        except Exception:
            catalog = {}
        available = bool(catalog.get("current_available"))
        if self._llm is not None and not available and not detail:
            # 构建之后模型被删/被换掉：状态跟着事实走，不能继续说"已就绪"
            detail = self._diagnose_llm_unavailable(catalog=catalog)
        return {
            "ready": self._llm is not None and available,
            "model": self.settings.get("model"),
            # 当前模型是否真的可用（界面据此显示"请选择模型"而不是某个名字）
            "model_available": available,
            "local_note": catalog.get("local_note", ""),
            "local_detected": catalog.get("local_detected", []),
            "models": detect_ollama_models(),
            "chat_models": detect_chat_models(),
            "error": detail,
        }

    def _diagnose_llm_unavailable(self, catalog: Optional[Dict[str, Any]] = None) -> str:
        """算力没就绪时给一句**能照着做**的说明（而不是空白）。"""
        model = str(self.settings.get("model") or "").strip()
        host = str(self.settings.get("ollama_host") or "").strip()
        local_host = host.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]", "http://0.0.0.0"))
        installed = []
        try:
            installed = detect_chat_models()
        except Exception:
            installed = []
        if not model:
            return "没有选择模型：请在「设置 → 模型接入」里选一个模型（本机已安装：%s）" % (
                "、".join(installed[:3]) or "（暂无）")
        if local_host:
            if installed and model not in installed:
                return (f"本机没有这个模型：{model}（本机已装：{'、'.join(installed[:3])}）。"
                        f"在设置里换成本机已装的模型，或先 `ollama pull {model}`")
            if not installed:
                # 一个可对话模型都没有：把"为什么空"如实说出来（连不上 / 只有嵌入模型…），
                # 并把两种常见的自救动作都写清楚（Ollama 没起 / 模型没拉）。
                note = ""
                try:
                    if catalog is None:
                        catalog = self.model_catalog()
                    note = str(catalog.get("local_note") or "")
                except Exception:
                    note = ""
                return (f"当前选择的模型不可用：{model}——本机没有检测到可对话的模型。"
                        f"{note}。请确认 Ollama 已启动（命令行执行 `ollama serve`，"
                        f"或双击托盘图标）后点「重新检测」；或在「设置 → 模型接入」里换一个模型，"
                        f"或先 `ollama pull {model}` 把它拉下来")
            return (f"连不上本机算力（{host or '默认地址'}）：请确认 Ollama 已启动"
                    f"（命令行执行 `ollama serve`，或双击托盘图标）后点「重新检测」")
        return (f"连不上算力端点（{host}）：请检查网络、地址与密钥是否正确；"
                f"云端模型还需确认服务可用")

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
        """打开会话：返回**展示用**消息列表（历史占位符已还原）+ 会话级映射表。

        ★ 还原在服务层完成（V3.4.5 修复）：Web 前端拿不到 Fernet 密钥、也无法解密，
        此前界面直接渲染落库的脱敏文本——重开会话后历史回答里的占位符
        （如 [PHONE_0_0x3o]）会原样显示，与"回答返回时自动还原"的承诺不符。
        还原只作用于**返回值**，落库内容仍是脱敏态（"落盘零真值"不变）。
        """
        self.sessions.set_current(session_id)
        self.sessions.save()
        # ★ 权限档位是**会话级**上下文：切会话必须立刻把内核环境变量换成该会话的档位，
        #   否则上一个会话的档位会继续作用于本会话（"只读会话"里却能写盘）。
        self.apply_runtime_env(session_id)
        messages = self.sessions.get_messages(session_id)
        box = self.sessions.get_box_mapping(session_id) or {}
        messages = self._restore_display_text(messages)
        if box and self.settings.get("privacy", True):
            messages = self._restore_placeholders(messages, box)
        return {"id": session_id, "messages": messages, "box_mapping": box}

    @staticmethod
    def _restore_placeholders(messages: List[Dict[str, Any]],
                              box_mapping: Dict[str, str]) -> List[Dict[str, Any]]:
        """把展示副本里的占位符换回真实值（纯函数，不做任何落库写入）。"""
        if not box_mapping:
            return messages
        from yindun.core.privacy_engine import PrivacyEngine
        engine = PrivacyEngine()
        restored: List[Dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if (message.get("role") in ("user", "assistant")
                    and isinstance(content, str) and "[" in content):
                restored.append({**message,
                                 "content": engine.deanonymize(content, box_mapping)})
            else:
                restored.append(message)
        return restored

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
    #
    # ★ 清单口径 = "document_parser 真的有解析分支、且交付形态下跑得起来"：
    #   · 文本家族 / PDF / DOCX / PPTX / XLSX / XLS：内核有分支；
    #   · 图片：内核有 OCR 分支（引擎缺失时给出可行动说明，不是静默跳过）——仍算内核原生，
    #     插件若声明了 png 之类，会在"内核没读出内容"时被兜底调用（attach_files 的回落路径）；
    #   · 音频（mp3/wav/…）：**已移出**——转写依赖 FunASR + torch，而客户端 exe 明确排除 torch，
    #     交付形态下必然读不出内容；继续算"内核原生"会挡住真正能转写的插件。
    KERNEL_NATIVE_EXTS = frozenset({
        "pdf", "docx", "pptx", "xlsx", "xls",
        "csv", "tsv", "txt", "text", "md", "json", "html", "htm", "log",
        "yaml", "yml", "xml", "ini", "conf", "rst",
        "png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp",
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
        # report 仅供界面提示（截断到 300 字），绝不进入模型上下文 / 会话快照
        return [{"name": item.get("name", ""), "chars": item.get("chars", 0),
                 "error": item.get("error"), "via": item.get("via", "内核解析"),
                 "report": (item.get("report") or "")[:300]}
                for item in self._attachments]

    def attachment_count(self) -> int:
        return len(self._attachments)

    # ── 工作流 ───────────────────────────────────
    # 标注口径（V3.6.0）：不再维护一份硬编码的"模拟步骤"清单——数据四件套与人工审核
    # 已在 core/workflow.py 里真实化。现在直接问引擎"这个工具有没有真实执行器"，
    # 没有的（只可能出现在外部自定义模板里）如实标为"未实现"，执行时会失败而不是假装成功。

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
        "wf_data_pipeline": "data_path",     # 数据源：文件或目录（目录则取其中最新数据文件）
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
            engine = self._workflow()
            status = engine.get_workflow_status(instance_id)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        for step in status.get("steps", []) or []:
            step["unimplemented"] = not engine.has_execution_handler(step.get("tool_name"))
        status["impl_notice"] = (
            "内核内置模板的每一步都有真实执行器（含数据采集/清洗/分析/出图与人工审核留痕）；"
            "若某步被标为「未实现」，说明该模板来自外部配置、其工具内核没有实现，执行会如实失败。"
        )
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

    # ── 自定义模型（OpenAI 兼容：局域网 / 公网算力）──
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
                "tier": tier_of(name, info),
                "provider": provider_of(name, info),
                "api_format": api_format_of(info),
                "tags": clean_tags(info.get("tags")),
                "has_key": bool(info.get("api_key")),
                "key_invalid": bool(info.get("key_invalid")),
            })
        return out

    def model_catalog(self) -> Dict[str, Any]:
        """本机 / 局域网 / 公网三档算力清单（供设置页分区与模型下拉分组）。

        ★ 供应商元数据在本层补齐（V3.6.8「模型接入」页按供应商聚合）：`llm_factory`
        只回答"有哪些模型"，而 `provider` / `api_format` / `tags` 是**可选**的界面元数据
        （老配置里没有这些字段）。放在服务层兜底有两个好处：① 老配置无需迁移即可分组；
        ② 内核清单不必引入"供应商"这种界面概念（保持 core/app 的边界干净）。
        """
        catalog = model_catalog(self.settings.data)
        custom = self.settings.get("custom_models") or {}
        for tier in ("local", "lan", "wan"):
            for entry in catalog.get("tiers", {}).get(tier, []) or []:
                if entry.get("source") != "custom":
                    continue
                info = custom.get(entry.get("name"))
                info = info if isinstance(info, dict) else {}
                entry["provider"] = provider_of(str(entry.get("name") or ""), info)
                entry["api_format"] = api_format_of(info)
                entry["tags"] = clean_tags(info.get("tags"))
        return catalog

    def probe_model_endpoint(self, base_url: str, api_key: str = "",
                             name: str = "") -> Dict[str, Any]:
        """探测端点是否可用并列出模型（用户在设置页显式发起；地址只来自输入框）。

        ★ api_key 留空且 `name` 指向已有条目时，用**该条目已存的密钥**去探（V3.6.7 修真实缺陷）：
          页面在存过密钥后会把输入框留空（密钥不回显），用户此时点「测试连接」若拿空密钥去发，
          云服务必然回 401「密钥不被接受」——看着就像"保存之后密钥丢了"。
          密钥只从本机加密存储读取，来源与地址无关（地址仍只来自输入框），也不写进审计。
        """
        key = (api_key or "").strip() or self._stored_api_key(name)
        result = probe_endpoint(base_url, key)
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL,
                AuditSeverity.INFO if result.get("ok") else AuditSeverity.WARNING,
                f"算力连通性探测：{'成功' if result.get('ok') else '失败'}",
                {"endpoint": str(base_url)[:160], "ok": bool(result.get("ok")),
                 "found": int(result.get("count") or 0)},
            )
        except Exception:
            pass
        return result

    def _stored_api_key(self, name: str) -> str:
        """取某个已配置模型已存的密钥（内存里是明文，落盘时由设置层加密）。取不到就返回空串。"""
        if not name:
            return ""
        try:
            entry = (self.settings.get("custom_models") or {}).get(str(name))
            return str((entry or {}).get("api_key") or "")
        except Exception:
            return ""

    def add_custom_model(self, name: str, model_id: str, base_url: str, api_key: str,
                         tier: str = "", provider: str = "", api_format: str = "",
                         tags: Any = None) -> Dict[str, Any]:
        """新增/覆盖一个外部模型配置。api_key 由设置层加密落盘（api_key_enc）。

        tier 为空时按地址自动分档（`llm_factory.infer_tier`）；界面会显示推断结果，
        用户也可在对应档位卡片里显式指定。

        ★ V3.6.8 起多三个**可选**参数（老调用方不传也照常工作）：
          · provider   —— 供应商显示名（界面按它分组）；空 → 按 base_url 的 host 兜底；
          · api_format —— 接口格式，只认 chat_completions，其它值回落（不假装支持）；
          · tags       —— 用户标签数组；None = 保持原值、[] = 清空（区分"不改"与"清空"）。
        """
        name = (name or "").strip()
        model_id = (model_id or "").strip()
        base_url = (base_url or "").strip()
        if not name or not model_id or not base_url:
            return {"ok": False, "error": "名称、模型 ID、接口地址都不能为空"}
        if not base_url.lower().startswith(("http://", "https://")):
            return {"ok": False, "error": "接口地址需以 http:// 或 https:// 开头"}
        # ★ 归一化（V3.6.4）：把"完整端点"（…/chat/completions）还原成基础地址再落盘——
        #   否则 OpenAI 兼容客户端会在 base 后再拼一次 /chat/completions，请求必然 404。
        #   实测：接 GLM-4-Flash 一直失败就是这一步（用户粘的是完整端点）。
        normalized = normalize_base_url(base_url)
        if normalized and normalized != base_url:
            try:
                from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
                AuditLog().add_entry(
                    AuditEventType.ACCESS_CONTROL, AuditSeverity.INFO,
                    f"接口地址已归一化为基础地址：{normalized}",
                    {"name": name, "input": base_url[:160], "normalized": normalized[:160]})
            except Exception:
                pass
            base_url = normalized
        tier = str(tier or "").strip().lower()
        if tier not in ("local", "lan", "wan"):
            tier = infer_tier(base_url)
        models = dict(self.settings.get("custom_models") or {})
        prev = models.get(name)
        # 配置是本地文件，可能被手工改过：坏条目（不是 dict）一律当"没有已存密钥"，别在这里崩
        prev_key = str((prev or {}).get("api_key") or "") if isinstance(prev, dict) else ""
        key = (api_key or "").strip()
        kept_key = False
        if not key and prev_key:
            # ★ 留空＝**沿用已存密钥**（V3.6.7 修真实缺陷）：密钥不回显，所以页面在存过之后把输入框
            #   留空、并写明「留空则不改动」。但这里原先无条件用空串覆盖 → 用户每按一次保存就把密钥
            #   清掉一次，紧接着「测试连接」用的是空密钥 → 云服务回 401「密钥不被接受」。
            #   表现正是用户报的"点击保存后密钥就消失了、不能再保存或测试"。
            #   行为必须与界面承诺一致：留空＝不改动。（要真正清空密钥＝删除该条目后重新添加。）
            key = prev_key
            kept_key = True
        models[name] = {"model_id": model_id, "base_url": base_url,
                        "api_key": key, "tier": tier,
                        # provider：显式给的用显式值，否则按 final base_url 归组（同一 base_url
                        # 的多条模型自然落到同一供应商，这正是界面"＋ 添加模型"的语义）
                        "provider": str(provider or "").strip()[:40]
                                    or provider_of(name, {"base_url": base_url}),
                        "api_format": api_format_of({"api_format": api_format}),
                        "tags": (clean_tags(tags) if tags is not None
                                 else clean_tags((prev or {}).get("tags") if isinstance(prev, dict) else None))}
        self.settings.set("custom_models", models)
        self.settings.save()
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                f"新增外部算力配置：{name}（{TIER_LABEL.get(tier, tier)}）",
                {"name": name, "model_id": model_id, "base_url": base_url,
                 "tier": tier, "has_key": bool(key), "kept_key": kept_key,
                 "provider": models[name].get("provider", "")},
            )
        except Exception:
            pass
        return {"ok": True, "models": self.custom_models(), "tier": tier,
                "tier_label": TIER_LABEL.get(tier, tier), "kept_key": kept_key,
                "provider": models[name].get("provider", "")}

    def remove_custom_model(self, name: str) -> Dict[str, Any]:
        models = dict(self.settings.get("custom_models") or {})
        if name not in models:
            return {"ok": False, "error": f"未找到配置：{name}"}
        models.pop(name, None)
        self.settings.set("custom_models", models)
        # 若当前正在用这个模型，回退到本地模型，避免指向已删除的配置
        #   ★ 回退值必须是**真实检测到**的本机模型；检测不到就清空（=「未选择」），
        #     绝不能写死 `qwen3.5:4b` 这类名字——那正是"界面显示一个本机根本没有的模型"
        #     的来源（负责人实测：同学的机器装了 27b，界面却显示 4b）。
        if self.settings.get("model") == name:
            from yindun.app.llm_factory import get_first_available_model
            self.settings.set("model", get_first_available_model() or "")
            self._llm = None
            self._llm_variants = {}
        self.settings.save()
        return {"ok": True, "models": self.custom_models()}

    def custom_model_key(self, name: str) -> Dict[str, Any]:
        """把某个供应商**已存的本机明文密钥**交给本地界面（用户点「查看」时才调用）。

        ★ 口令与留痕（这是本条通路存在的唯一理由，改之前先读）：
          一个只会"留空＝不改动"的密钥框，用户没法确认自己当初存进去的到底是什么——
          要么去翻配置文件（明文只在内存，磁盘是密文，根本翻不到），要么删了重填。
          所以提供**显式**查看动作（ZCode 的模型设置就是这样：眼睛图标 + 明文回显）。

        ★ 为什么按「供应商名」找密钥：存储粒度是"每条模型一行"（`custom_models[条目名]`），
          而界面是按供应商配置密钥（同一供应商的模型共用一把）。这里先按供应商名匹配，
          再按条目名匹配（兼容单模型条目），取第一条非空密钥。

        ★ 为什么每次查看都写审计、且用 WARNING：查看明文凭据是**用户主动发起的高敏动作**
          （INFO 会淹没在日常事件里，SECURITY 是给篡改/链异常留的级别）；审计只记录
          "谁在什么时候看了哪个供应商的密钥、有没有取到"，**绝不写密钥本身**。

        ★ 交付面：本方法只经 `webview_app.JsApi`（本机 pywebview 私有桥，回环随机
          UUID）暴露给本地窗口；不进控制面、不上报、不落盘。
        """
        target = str(name or "").strip()
        key = ""
        if target:
            models = self.settings.get("custom_models") or {}
            for entry_name, info in models.items():
                if not isinstance(info, dict):
                    continue
                matches = str(entry_name) == target or provider_of(str(entry_name), info) == target
                if matches and info.get("api_key"):
                    key = str(info.get("api_key"))
                    break
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                f"在界面上查看已存的 API Key：{'已显示' if key else '未取到'}（明文仅在本机窗口显示）",
                {"provider": target[:40], "has_key": bool(key)},
            )
        except Exception:
            pass
        if not key:
            return {"ok": False, "error": "该供应商还没有已存的密钥"}
        return {"ok": True, "key": key}

    # ── 上下文容量浮层（V3.6.8）────────────────────
    def context_usage(self) -> Dict[str, Any]:
        """当前上下文的**真实构成**（顶栏「上下文 x/y」点开的浮层用）。

        口径（每一项都是真取数；改这里必须同步 tests/test_ui_paths.py 的断言）：
          · 消息（历史）：本会话消息里**除工具消息以外**的全部内容，按
            `memory_manager.estimate_tokens` 逐条估算——与"何时触发摘要压缩"同一口径；
          · 系统提示词：worker 的常驻基线（挂了附件时加上附件规范），同一估算器折算；
          · 附件：本轮已挂附件的注入字符数（内核按 ATTACHMENT_TOTAL_CHARS 截顶；
            正文是中文，按项目唯一口径 CJK 1 字 ≈ 1 token 折算 token）；
          · 工具结果：会话消息里 role == "tool" 的部分（同一估算口径，与"历史"不重叠）。
        **没有的分类不显示**：我们没有 prompt 缓存、没有"技能 / MCP 工具"这些概念，
        知识库检索片段已经并进工具结果里——宁可少一项，也不编一个数字出来。
        """
        from yindun.core.context_budget import (ATTACHMENT_TOTAL_CHARS, HISTORY_COMPRESS_TOKENS,
                                                MODEL_WINDOW_TOKENS, SUMMARY_TARGET_TOKENS)
        from yindun.core.memory_manager import estimate_tokens
        from yindun.worker.agent_worker import _ATTACHMENT_RULES, _SYSTEM_PROMPT

        try:
            messages = self.sessions.get_messages(self.current_session_id()) or []
        except Exception:
            messages = []
        history = sum(estimate_tokens(str(m.get("content") or ""))
                      for m in messages if (m.get("role") or "") != "tool")
        tools = sum(estimate_tokens(str(m.get("content") or ""))
                    for m in messages if (m.get("role") or "") == "tool")

        # 系统提示词：真挂了附件才会追加附件规范（worker 拼装处同一条件）
        prompt_text = _SYSTEM_PROMPT
        if self._attachments:
            prompt_text = _SYSTEM_PROMPT + _ATTACHMENT_RULES
        system = estimate_tokens(prompt_text)

        # 附件：注入字符量（按总量预算截顶）折算 token
        attach_chars = sum(int(item.get("chars") or 0) for item in self._attachments
                           if not item.get("error"))
        attach_chars = min(attach_chars, ATTACHMENT_TOTAL_CHARS)

        parts = []
        for key, label, tokens in (("history", "消息（历史）", history),
                                   ("system", "系统提示词", system),
                                   ("attach", "附件", attach_chars),
                                   ("tools", "工具结果", tools)):
            if tokens <= 0:
                continue
            parts.append({"key": key, "label": label, "tokens": int(tokens),
                          "percent": round(tokens / MODEL_WINDOW_TOKENS * 100, 1)})
        used = sum(p["tokens"] for p in parts)
        return {
            "limit_tokens": MODEL_WINDOW_TOKENS,
            "used_tokens": used,
            "percent": round(used / MODEL_WINDOW_TOKENS * 100, 1),
            "parts": parts,
            # 底部两行汇总：都是预算表里的真实口径（我们没有 prompt 缓存，
            # 所以这里绝不写"缓存命中率"这类我们没有的指标）
            "footer": {"lines": [
                f"历史超过 {HISTORY_COMPRESS_TOKENS} token 就摘要压缩（压到约 {SUMMARY_TARGET_TOKENS}）",
                f"单轮附件正文预算 {ATTACHMENT_TOTAL_CHARS} 字，超出部分改给检索指引",
            ]},
        }

    # ══ 企业控制面接入（V3.5.0）════════════════════════════
    #  数据边界（写在这里，改代码前先读）：
    #   · 上报的是**掩码审计事件 + 链头 + 元数据**；密钥与明文从不离开本机；
    #   · 下发的策略**只收紧**（验签 → 单调合并 → 落到内核策略引擎），放行类策略无通路；
    #   · **默认关闭**：集中管理必须由用户在设置页显式打开，绝不静默上报。
    def _control(self):
        """控制面接入器（懒加载缓存）。"""
        if getattr(self, "_control_reporter", None) is None:
            from yindun.control.agent_reporter import ControlReporter
            # 两个注入点（测试/运维用）：control_config_path 换配置文件落点，
            # _control_audit 换审计源——测试必须能在不碰本机配置与真实审计目录的前提下跑闭环。
            self._control_reporter = ControlReporter(
                config_path=getattr(self, "control_config_path", None),
                audit=getattr(self, "_control_audit", None) or None,
                settings=self.settings.data)
        return self._control_reporter

    def control_status(self) -> Dict[str, Any]:
        """本机控制面接入状态 + 已落地策略的摘要（不含令牌）。"""
        from yindun.control.apply_policy import describe_local_effect
        status = self._control().status()
        applied = status.get("applied_policy") or {}
        status["effect"] = describe_local_effect(applied) if applied else []
        try:
            from yindun.core.policy_manager import PolicyManager
            status["control_rules"] = PolicyManager().get_control_rules()
        except Exception:
            status["control_rules"] = []
        status["auto_interval"] = int(getattr(self, "_control_interval", 0) or 0)
        status["running"] = bool(getattr(self, "_control_thread", None)
                                 and self._control_thread.is_alive())
        return status

    def control_pair(self, url: str, code: str, name: str = "") -> Dict[str, Any]:
        """用一次性配对码登记本机（不启用上报；开关由 control_set_enabled 控制）。"""
        if not (url or "").strip():
            return {"ok": False, "error": "请填控制面地址"}
        if not (code or "").strip():
            return {"ok": False, "error": "请填配对码（在控制台「生成配对码」获取）"}
        import platform as _pf
        host = (name or "").strip() or f"{_pf.node()}"
        result = self._control().enroll_with_code(url.strip(), code.strip(), host, "")
        return result

    def control_set_enabled(self, enabled: bool) -> Dict[str, Any]:
        """打开/关闭上报。开启时立即做一次同步，失败会如实报错（不假装已连上）。"""
        reporter = self._control()
        if not reporter.status().get("configured"):
            return {"ok": False, "error": "尚未登记：请先用配对码接入控制面"}
        if not enabled:
            reporter.disable()
            self.control_stop_auto()
            return {"ok": True, "enabled": False, "note": "已停用上报（已下发的策略规则保留，可在下方显式撤销）"}
        cfg = dict(reporter._config)          # noqa: SLF001 —— 同一模块内的受控访问
        cfg["enabled"] = True
        reporter.save_config(cfg)
        sync = self.control_sync()
        return {"ok": True, "enabled": True, "sync": sync}

    def control_sync(self) -> Dict[str, Any]:
        """同步一次：上报一批 → 取策略 → 验签 → 只收紧合并 → 落到本地生效。"""
        reporter = self._control()
        if not reporter.enabled:
            return {"ok": False, "error": "上报未启用"}
        # 先取策略并生效，再上报：这样本批上报携带的 policy_version 就是"我刚应用的版本"，
        # 控制台的策略漂移显示一次同步即可收敛（否则永远慢一拍，看着像没生效）。
        policy, reason = reporter.fetch_policy()
        applied: Dict[str, Any] = {}
        if policy:
            applied = self._apply_control_policy(policy)
        sent = reporter.send()
        return {"ok": sent.ok and reason == "ok",
                "report": {"ok": sent.ok, "events": sent.events, "error": sent.error,
                           "alerts": [a.get("code") for a in (sent.alerts or [])]},
                "policy": {"fetched": bool(policy), "reason": reason, "applied": applied}}

    def _apply_control_policy(self, policy: Dict[str, Any]) -> Dict[str, Any]:
        """把策略落到本地：内核规则 + 严格模式 + 插件允许集 + 模型允许集（全部只收紧）。"""
        from yindun.control.apply_policy import apply_policy_to_kernel
        from yindun.core.policy_manager import PolicyManager
        summary = apply_policy_to_kernel(policy, PolicyManager())
        effects: List[str] = []

        # ① 严格模式（只开不关：控制面无权关掉端点自己开的严格模式）
        if policy.get("require_strict_mode") and self.settings.get("allow_script_exec", True):
            self.settings.set("allow_script_exec", False)
            self.apply_runtime_env()        # 环境变量只有一个出口，避免两处写法漂移
            effects.append("已强制开启严格模式（禁用解释器）")

        # ② 插件允许集：停用不在列表内的插件（只减不增）
        allowed_plugins = policy.get("allowed_plugins")
        if allowed_plugins is not None:
            allow = set(allowed_plugins)
            enabled = dict(self.settings.get("plugins_enabled") or {})
            for pid, on in list(enabled.items()):
                if on and pid not in allow:
                    try:
                        self.set_plugin_enabled(pid, False)
                        effects.append(f"已停用插件 {pid}（不在控制面允许集内）")
                    except Exception as exc:
                        effects.append(f"停用插件 {pid} 失败：{exc}")

        # ③ 模型允许集：当前模型不在允许集内则回退到允许集里第一个可用的
        allowed_models = policy.get("allowed_models")
        if allowed_models is not None:
            allow = [str(m) for m in allowed_models]
            current = str(self.settings.get("model") or "")
            if current and current not in allow:
                fallback = next((m for m in allow if m), "")
                if fallback:
                    self.settings.set("model", fallback)
                    self._llm = None
                    self._llm_variants = {}
                    effects.append(f"当前模型不在允许集内，已回退到 {fallback}")
                else:
                    effects.append("允许集为空：所有模型均被禁用（请改用本机或局域网模型）")
        self.settings.save()
        summary["effects"] = effects
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(
                AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                f"已应用控制面策略 v{summary.get('policy_version', 0)}"
                + ("（" + "；".join(effects) + "）" if effects else ""),
                {"policy_version": summary.get("policy_version"), "rules": summary.get("rules"),
                 "sensitive_paths": len(summary.get("sensitive_paths") or []),
                 "floor": summary.get("floor", ""), "effects": effects})
        except Exception:
            pass
        return summary

    def control_clear_rules(self) -> Dict[str, Any]:
        """撤销控制面下发的全部本地规则（运维显式动作，会写审计）。"""
        from yindun.core.policy_manager import PolicyManager
        removed = PolicyManager().clear_control_rules()
        try:
            from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
            AuditLog().add_entry(AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                                 f"已撤销控制面下发的 {removed} 条本地规则", {"removed": removed})
        except Exception:
            pass
        return {"ok": True, "removed": removed}

    def control_start_auto(self, interval_seconds: int = 300) -> Dict[str, Any]:
        """开启定时同步（默认 5 分钟）：上报 + 取策略。失败只记状态，不打断推理。"""
        reporter = self._control()
        if not reporter.enabled:
            return {"ok": False, "error": "请先启用上报"}
        interval = max(30, int(interval_seconds or 300))
        self.control_stop_auto()
        self._control_interval = interval
        stop_event = threading.Event()
        self._control_stop = stop_event

        def _loop():
            while not stop_event.wait(interval):
                try:
                    self.control_sync()
                except Exception as exc:          # 网络抖动不应打断本地使用
                    print(f"[控制面] 定时同步失败（将继续重试）：{exc}")

        self._control_thread = threading.Thread(target=_loop, name="control-sync", daemon=True)
        self._control_thread.start()
        return {"ok": True, "interval": interval, "note": f"已开启定时同步（每 {interval} 秒）"}

    def control_stop_auto(self) -> Dict[str, Any]:
        stop_event = getattr(self, "_control_stop", None)
        if stop_event is not None:
            stop_event.set()
        self._control_stop = None
        thread = getattr(self, "_control_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        self._control_thread = None
        self._control_interval = 0
        return {"ok": True}
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

    # ── laya 风险分级接入（本机 / 局域网 / 正则回退，V3.5.3）──
    # 接入配置与探测放在应用层，分级本身仍由插件执行；两边读的是同一份配置文件
    # （经宿主既有的 plugin_config_* 通道），所以不存在"设置页写的、插件读不到"。
    def _laya(self) -> Any:
        if self._laya_service is None:
            from yindun.app.laya_service import LayaService
            self._laya_service = LayaService(
                read_config=self.plugin_config_read,
                write_config=self.plugin_config_write,
                runtime_lookup=self._laya_runtime,
            )
        return self._laya_service

    def _laya_runtime(self) -> Dict[str, Any]:
        """插件最近一次分级用的来源（供设置页回答"到底有没有在工作"）。

        ★ 必须从**宿主导入的那份模块**里取：插件被宿主按文件路径导入（`yindun_plugin_<id>`），
        和 `import yindun.plugins.builtin...` 是**两个独立的模块对象**、各有各的模块级状态。
        早期实现直接 import 包路径那一份，于是页面上"最近一次分级"永远是空的——真跑推理的是另一份。
        """
        try:
            value = self._plugins.module_attr("laya_risk", "last_source", None)
            text = value() if callable(value) else value      # 插件里它是函数，别当成值读
            return {"last_source": str(text or "")}
        except Exception:
            return {}

    def laya_status(self) -> Dict[str, Any]:
        try:
            return self._laya().status()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}

    def laya_save(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self._laya().save(cfg or {})
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    def laya_probe(self, kind: str = "local", cfg: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            svc = self._laya()
            if str(kind) == "lan":
                return svc.probe_lan((cfg or {}).get("endpoint", ""))
            return svc.probe_local()
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

    def laya_try(self, cfg: Dict[str, Any] = None) -> Dict[str, Any]:
        try:
            return self._laya().try_lan(cfg or {})
        except Exception as exc:
            return {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}

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
            # 用途判定是"先问想干什么"，必须排在"是否相符""操作风险"之前（V3.6.13）。
            # 这里把确定性结论一并交给插件：intent_guard 用它做红线，
            # decision_hint / laya 用它避免给出"相符 + 低危"的自相矛盾提示。
            try:
                from yindun.core.usage_policy import assess_request
                intent = assess_request(self._last_user_request)
            except Exception:
                intent = {"decision": "ok", "category": "", "summary": "", "message": ""}
            context = {
                "tool": approval.get("name"),
                "args": approval.get("args") or {},
                "path": approval.get("path"),
                "user_request": self._last_user_request,
                "intent": intent,
                "model": self.settings.get("model"),
                "ollama_host": self.settings.get("ollama_host") or "http://127.0.0.1:11434",
            }
            advisories = self._plugins.call_hook("advisory_for_approval", context)
            # 建议按 order 排序（0=意图预判，100=其余）：界面上的阅读顺序就是
            # "先用途、再相符、最后操作风险"，而不是由插件目录名决定。
            def _advice_order(item):
                try:
                    return int((item or {}).get("order", 100))
                except Exception:
                    return 100          # 插件给了脏 order 也不能让整组建议消失
            advisories.sort(key=_advice_order)
        except Exception as exc:
            print(f"[AgentService] 征集插件建议失败：{exc}")
            return
        if advisories:
            self._emit("advisories", advisories)
            # 建议（如 laya 风险分级）是"审批为什么这样判"的依据，必须在对话流里留痕：
            # 弹窗关掉之后就再也看不到判断依据，对安全产品是说不清的。
            for item in advisories:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text") or item.get("summary") or "").strip()
                if not text:
                    continue
                self._append_trace({
                    "kind": "judge",
                    "name": str(item.get("plugin") or item.get("name") or "插件建议")[:60],
                    "detail": text[:200],
                    "status": "ok", "ms": 0,
                })
        else:
            # ★ 空结果也要**明确收尾**（V3.6.13）：插件启用了但这次没给建议时，
            #   界面在等一个 advisories 事件；不发就会让"正在研判"一直转。
            self._emit("advisories", [])

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
        """知识库状态：依赖是否装好、**embedding 是否真的可用**、文档/片段数。

        ★ `available` 与 `embedding_ready` 是两件事（V3.6.12，组员评审 BUG-01）：
          · available      = Python 依赖已安装（能不能起向量库）；
          · embedding_ready = Ollama 在线且 embed 模型在（能不能真的向量化）。
          界面必须按后者决定"能否添加文档"，并说清原因——否则用户会看到"就绪"、
          点添加也能点，最后失败被批量结果吞掉。
        """
        kb = self.knowledge_base()
        if kb is None:
            return {"available": False, "embedding_ready": False, "docs": [], "stats": {},
                    "error": self._kb_error or "知识库未初始化"}
        try:
            available = bool(kb.is_available())
        except Exception as exc:
            return {"available": False, "embedding_ready": False, "docs": [], "stats": {},
                    "error": str(exc)}
        embedding_ready = False
        if available:
            try:
                embedding_ready = bool(kb.embedding_ready())
            except Exception as exc:
                print(f"[AgentService] embedding 探活失败：{exc}")
        stats = {}
        docs = []
        if available:
            try:
                stats = kb.get_stats() or {}
                docs = kb.list_documents() or []
            except Exception as exc:
                print(f"[AgentService] 读取知识库状态失败：{exc}")
        embed_model = getattr(kb, "embed_model", "")
        if not available:
            error = "知识库依赖未安装（chromadb / langchain-chroma 等）"
        elif not embedding_ready:
            error = f"本地 embedding 不可用（请确认 Ollama 在线且已拉取 {embed_model}）"
        else:
            error = None
        return {
            "available": available,
            "embedding_ready": embedding_ready,
            "embed_model": embed_model,
            "docs": docs,
            "stats": stats,
            "error": error,
        }

    def kb_add(self, paths: List[str]) -> Dict[str, Any]:
        """把文档入库（入库前逐块脱敏，向量库只存占位符）。

        ★ `ok` 必须反映**真实结果**（V3.6.12，组员评审 BUG-01）：`add_documents()`
          是逐文件容错的——单个文件失败只记进 details、不抛异常。旧实现因此
          "只要批处理跑完就 ok=True"，于是"成功 0/2"也会被界面当成入库成功，
          用户看到一个空库。现在：
          · `success > 0` 才算 ok（全部失败 → ok=False，并带第一条失败原因）；
          · 部分失败 → ok=True 但 `partial=True` + `failed`/`summary`，界面必须提示；
          · 始终把 `summary`（人话统计）带回去，不让失败原因只活在 details 里。
        """
        kb = self.knowledge_base()
        if kb is None:
            return {"ok": False, "error": self._kb_error or "知识库未初始化"}
        files = [p for p in (paths or []) if p]
        if not files:
            return {"ok": False, "error": "未选择文件"}
        try:
            if not kb.is_available():
                return {"ok": False, "error": "知识库依赖未安装（请先安装 chromadb 等组件）"}
            if not kb.embedding_ready():
                return {"ok": False,
                        "error": ("本地 embedding 不可用：Ollama 未在线，或没有拉取 "
                                  f"{getattr(kb, 'embed_model', 'nomic-embed-text')}。"
                                  "先在命令行执行 ollama pull nomic-embed-text，再重试。")}
            self._emit("status", f"正在入库 {len(files)} 个文档（逐块脱敏后向量化）…")
            result = kb.add_documents(files)
            self._emit("status", "")
            success = int(result.get("success") or 0)
            failed = int(result.get("failed") or 0)
            summary = str(result.get("summary") or "")
            errors = [str(item.get("error") or "") for item in (result.get("details") or [])
                      if str(item.get("status")) == "failed"]
            return {
                "ok": success > 0,
                "result": result,
                "summary": summary,
                "failed": failed,
                "partial": success > 0 and failed > 0,
                "error": "" if success else (errors[0] if errors else summary or "没有文档成功入库"),
                "status": self.kb_status(),
            }
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
    # 推理结束但没有任何产出时的兜底说明（见 _finalize）。文案必须如实：
    # 只说明"没拿到结果"，不猜原因、不假装完成。
    _NO_OUTPUT_NOTICE = ("抱歉，这一轮没有产出回答（推理已结束或被取消，但没有返回内容）。"
                         "可以再发一次；如果反复出现，请在设置里换一个模型重试。")

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
        # 思考开关跟着模式走：「快速回答」用关掉思考的算力（实测快 10~30 倍），
        # 「深度思考」用保持模型默认的算力——两套都在 prepare_llm 时建好了。
        worker.llm = self._llm_for(self.settings.get("think_mode"))
        worker.tools_map = self._tools_map
        # 可读写范围：以"设置 sandbox_dir → 环境变量 SANDBOX_PATH → 程序目录"解析后的值为准
        # （与内核判定同一来源），避免 worker 的默认目录与策略判定错位。
        self.apply_runtime_env(sid)      # 按本会话的档位下发（会话覆盖优先，其次设置默认）
        from yindun.core.permission import sandbox_root as _sandbox_root
        worker.sandbox_path = _sandbox_root()
        worker.attachment_fulltext = self.sessions.get_attachment_fulltext(sid)
        worker.session_id = sid

        # ★ 权限等级与脚本执行开关通过环境变量下发给工具层（与旧界面一致）；
        #   但**可读写范围绝不在这里写进 SANDBOX_PATH**（V3.6.8 真机探针逮到的坑）：
        #   环境变量在内核的解析顺序里是**硬覆盖**（设置 > 环境变量 > 默认集合），
        #   一旦把"主根"（程序目录）写进去，默认集合（桌面/文档/下载）就被挤掉——
        #   表现是"打开对话前桌面在范围内、发一句话之后桌面又变成越界要确认"。
        #   范围下发只走 `_project_sandbox_env()`：只有设置里显式配了 sandbox_dir 才写环境变量。
        for topic in WORKER_EVENTS:
            worker.bus.subscribe(topic, self._make_forwarder(run_id, topic))

        with self._lock:
            self._worker = worker
        self._thread = threading.Thread(target=self._run_worker, args=(worker, sid, run_id), daemon=True)
        self._thread.start()
        self._emit("state", self._state_payload(sid, worker))
        return True

    def _append_trace(self, item: Dict[str, Any]) -> None:
        """把一条过程轨迹追加到当前会话并推给界面。

        为什么要落库：过程轨迹是"这个判断凭什么下"的证据，刷新/切会话后还得在。
        失败只打印不抛——轨迹是"看得见"的附加能力，不能因为它打断推理。
        """
        if not isinstance(item, dict):
            return
        self._trace_seq = int(getattr(self, "_trace_seq", 0)) + 1
        record = {"seq": self._trace_seq, "kind": str(item.get("kind") or "info")[:16],
                  "name": str(item.get("name") or "")[:80],
                  "detail": str(item.get("detail") or "")[:200],
                  "status": str(item.get("status") or "")[:16],
                  "ms": int(item.get("ms") or 0)}
        try:
            sid = self.current_session_id()
            messages = self.sessions.get_messages(sid) or []
            messages.append({"role": "trace", "trace": record})
            self.sessions.set_messages(sid, messages)
            # ★ set_messages 只改内存（本仓约定：落盘由调用方显式 save），
            #   漏掉这一行会让轨迹在重启后消失——"落库"就成了空话。
            #   （这条是发布前验收抓到的：内存里有、磁盘上没有。）
            self.sessions.save()
        except Exception as exc:
            print(f"[AgentService] 轨迹落库失败（不影响推理）：{exc}")
        self._emit("trace", record)

    def _make_forwarder(self, run_id: int, topic: str) -> Callable[[Any], None]:
        """把引擎事件转发给界面；已过期的轮次（被取消/替换）直接丢弃。"""
        def _forward(payload: Any) -> None:
            with self._lock:
                if run_id != self._active_run:
                    return
            if topic == "trace":
                # 过程轨迹：先落库再推送（刷新后仍在），seq 由服务层统一分配
                self._append_trace(payload if isinstance(payload, dict) else {})
                return
            if topic == "need_confirm" and isinstance(payload, dict):
                # ★ 告知界面"有没有可征集的建议插件"（V3.6.13）：没有插件时不要
                #   显示"正在研判"占位——否则它会一直转到弹窗关闭，像卡住了。
                payload = dict(payload)
                try:
                    payload["advisory_available"] = bool(
                        self._plugins.enabled_plugins("advisory_for_approval"))
                except Exception:
                    payload["advisory_available"] = True
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
                else:
                    # ★ 兜底（V3.6.7 真机事故）：worker 一条消息链都没留下（线程意外结束/被杀的
                    #   极端情况）时，send() 写下的那条用户提问会孤零零留在会话里——界面上是空气泡、
                    #   刷新后那一轮像没发生过。这里补一条如实说明，保证"有提问就有一条回复记录"。
                    #   （常规的失败/取消路径由 Worker._persist_terminal_reply 自己写，不会走到这里。）
                    self.sessions.set_messages(
                        session_id,
                        self._restore_display_text(
                            list(self.sessions.get_messages(session_id))
                            + [{"role": "assistant", "content": self._NO_OUTPUT_NOTICE}]
                        ),
                    )
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
        # ★ V3.6.2（C-13）：阈值与"什么时候压缩"的说明都来自 core/context_budget.py，
        #   界面直接显示后端给的 note，不再自己拼数字。
        from yindun.core.context_budget import history_usage
        usage = history_usage(messages)
        return {
            "busy": self.busy,
            "session_id": session_id,
            "tool_calls": tool_calls,
            "context_tokens": usage["used"],
            "context_limit": usage["limit"],
            "context_ratio": usage["ratio"],
            "context_will_compress": usage["will_compress"],
            "context_note": usage["note"],
            "message_count": len(messages),
            "model": self.settings.get("model"),
            "privacy": bool(self.settings.get("privacy", True)),
            "permission": self.settings.get("permission"),
            "session_permission": self.effective_permission(session_id).get("session_permission"),
            # 可读写范围：报"当前生效值"（设置 sandbox_dir 优先），与 worker/策略判定同一来源
            "sandbox": (self.worker_sandbox_root()),
            "think_mode": self.settings.get("think_mode") or "快速回答",
        }

    def worker_sandbox_root(self) -> str:
        """当前生效的可读写范围（供状态快照与界面态势行显示；与内核判定同一解析顺序）。"""
        from yindun.core.permission import sandbox_root
        return sandbox_root()

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
