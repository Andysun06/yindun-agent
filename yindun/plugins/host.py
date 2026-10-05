# -*- coding: utf-8 -*-
"""隐盾 · 插件宿主（发现 / 校验 / 启停 / 调用 / 能力查询）

插件形态：一个目录，内含 `manifest.json` 与入口模块（默认 `plugin.py`）

    <plugin_dir>/
      manifest.json
      plugin.py
      data/            ← 插件自有数据（用户插件的可写数据落在插件目录内）

manifest 字段：
    id            必填，唯一标识（字母数字下划线）
    name          必填，展示名
    version       必填，语义化版本
    entry         选填，入口模块文件名（默认 plugin.py）
    description   选填，一句话说明（会显示在设置页）
    hooks         必填，声明的钩子名（必须是 HOOK_SPECS 里的钩子）
                  ★ 入口模块必须定义**与钩子同名**的函数（宿主按名字查找）；
                    缺函数不会静默通过：调用时记为错误，自检（selfcheck）会直接报出来
    requires      选填 {"python": ["torch>=2.0"], "ollama_models": ["qwen2.5*"]}
    permissions   选填 {"network": "local-only"|"none", "filesystem": "read"|"none"}
    timeout_seconds 选填，单次钩子调用的超时（仍会被该钩子的硬上限截断）
    file_exts     选填（attachment_parser 必填），声明负责的扩展名（小写、不带点）
    export_formats 选填（export_renderer 必填）{"audit": ["markdown"], "workflow": ["html"]}
    config_files  选填，用户可编辑的文本文件（相对插件数据目录）
    provides      选填，人读的能力说明（界面展示用）

★ 安全边界（改动前先读 tests/test_plugins.py 与 tests/test_plugin_capabilities.py）：
  1. **内核不可插件化**：脱敏网关 / 策略白名单 / 审批放行 / 审计链 / 沙箱文件工具 /
     模型接入始终由内核实现；插件永远拿不到这些判定权（见 FORBIDDEN_HOOKS）。
  2. **插件只能做加法**：多识别一类实体（范围更大＝更安全）、多解析一种格式、
     多一种导出格式、多一个工作流模板。任何"减少 / 豁免 / 绕过"的能力不开放。
  3. **载荷净化**：钩子返回值一律经 HookSpec.normalize 收敛（限长、限宽、限类型、
     强制前缀），脏数据被丢弃而不是照单全收。
  4. **内核兜底**：导出、工作流模板等能力即使插件全部停用也仍然可用。
  5. 宿主不下载任何代码；插件是本地代码，但启用/停用、依赖状态都会显示并写入审计。
"""
from __future__ import annotations

import importlib.util
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from yindun import APP_ROOT

# ──────────────────────────────────────────────
# 钩子契约
# ──────────────────────────────────────────────
# kind:
#   advisory —— 只产出"给人看的提示"，不进入任何执行/安全路径
#   additive —— 产出"增量能力"（多识别 / 多解析 / 多格式 / 多模板），内核负责合并与兜底
#
# 约定：normalize 把插件返回值收敛成内核可安全消费的结构；返回 None 表示丢弃该结果。
#      matches 决定这个插件是否该被本次调用唤醒（避免无关插件被反复加载）。
@dataclass(frozen=True)
class HookSpec:
    name: str
    kind: str
    summary: str
    returns: str = ""
    matches: Optional[Callable[["PluginRecord", Dict[str, Any]], bool]] = None
    default_timeout: float = 5.0
    max_timeout: float = 120.0   # 该钩子的硬上限（内联路径不允许长时间阻塞）
    normalize: Optional[Callable[[Any, "PluginRecord", Dict[str, Any]], Optional[Dict[str, Any]]]] = None


# 永不开放的"判定/放行"类能力名。插件声明这些名字时给出明确拒绝理由，
# 而不是笼统的"不支持的钩子"——让人一眼看懂边界在哪。
FORBIDDEN_HOOKS = {
    "approve", "allow_operation", "deny_operation", "allow", "deny",
    "mask", "unmask", "skip_privacy", "bypass", "bypass_privacy",
    "decide_risk", "risk_verdict", "enforce", "policy", "whitelist",
    "write_audit", "tamper_audit", "sandbox_root", "set_permission",
}

# 实体类型名：插件返回的类型会被强制加 PLUGIN_ 前缀，避免与内核正则键（PHONE 等）撞名
_PLUGIN_ENTITY_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,23}$")
_LEVELS = ("绝密", "机密", "内部", "公开")
_MAX_SPANS_PER_PLUGIN = 200
_MAX_SPAN_CHARS = 120
_SPAN_NEWLINE = re.compile(r"[\r\n]")

_FILE_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,32}$")
_EXT_RE = re.compile(r"^[a-z0-9]{1,8}$")
_FORMAT_RE = re.compile(r"^[a-z0-9_]{1,16}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
_MAX_CONFIG_BYTES = 256 * 1024
_MAX_TEMPLATES_PER_PLUGIN = 20
_MAX_STEPS_PER_TEMPLATE = 30
_MAX_EXPORT_BYTES = 2 * 1024 * 1024

# 内核保留的导出格式名：插件不得占用，否则"内核兜底"会变成"内核被顶替"。
# 插件要用自己的名字（可在 manifest 的 format_labels 里给出中文标签）。
RESERVED_EXPORT_FORMATS = {
    "audit": {"json", "html"},
    "workflow": {"md", "html", "json", "docx"},
}

_AUTO_WORDS = ("auto", "auto_execute", "automatic", "自动")


# ── 载荷净化：插件返回值 → 内核可消费的干净结构 ──
# ── 运行状态记录：回答"这个插件启用后到底有没有在工作" ──
def _note_runtime(record: PluginRecord, status: str, ms: int, hook: str, error: str = "") -> None:
    """记录插件最近一次被调用（仅统计真正被征集的调用，未启用/未匹配不算）。

    status：ok（返回了被采纳的结果）/ empty（运行了但本次无内容）/ error / timeout。
    界面据此显示"已触发 N 次 · 最近 … · 上次结果"，而不是只给一个开关让人猜。
    """
    rt = record.runtime
    rt["calls"] = int(rt.get("calls", 0)) + 1
    rt["ts"] = time.time()
    rt["ms"] = int(ms)
    rt["status"] = status
    rt["hook"] = hook
    if error:
        rt["error"] = str(error)[:200]
    else:
        rt.pop("error", None)


def _norm_recognizer(value: Any, record: "PluginRecord", context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """净化"补充识别"返回值：只接受落在正文坐标内的短区间，类型强制加 PLUGIN_ 前缀。"""
    text = context.get("text") or ""
    raw = value.get("spans") if isinstance(value, dict) else value
    if not isinstance(raw, (list, tuple)):
        return None
    spans: List[Dict[str, Any]] = []
    occupied: List[Tuple[int, int]] = []
    for item in list(raw)[:_MAX_SPANS_PER_PLUGIN]:
        if not isinstance(item, dict):
            continue
        etype = str(item.get("type") or "").strip().upper()
        if not _PLUGIN_ENTITY_RE.match(etype):
            continue
        try:
            start, end = int(item.get("start")), int(item.get("end"))
        except Exception:
            continue
        if not (0 <= start < end <= len(text)):
            continue
        if end - start > _MAX_SPAN_CHARS:
            continue
        fragment = text[start:end]
        if not fragment.strip() or _SPAN_NEWLINE.search(fragment):
            continue
        if any(s < end and start < e for s, e in occupied):   # 插件内部重叠：先到先得
            continue
        level = item.get("level") if item.get("level") in _LEVELS else "机密"
        occupied.append((start, end))
        spans.append({"start": start, "end": end, "text": fragment,
                      "type": f"PLUGIN_{etype}", "level": level})
    if not spans:
        return None
    return {"spans": spans, "count": len(spans)}


def _norm_attachment_parser(value: Any, record: "PluginRecord", context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """净化"附件解析"返回值：只取文本，长度按调用方给定的上限截断。"""
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    limit = int(context.get("max_chars") or 60000)
    return {"text": text[:limit], "truncated": len(text) > limit,
            "note": str(value.get("note") or "")[:200]}


def _norm_export_renderer(value: Any, record: "PluginRecord", context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """净化"导出渲染"返回值。上下文里的数据由内核提供，且早已脱敏。"""
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    ext = str(value.get("ext") or context.get("format") or "txt").lower().lstrip(".")
    if not _EXT_RE.match(ext):
        ext = "txt"
    return {"text": text[:_MAX_EXPORT_BYTES], "ext": ext,
            "suggested_name": str(value.get("suggested_name") or "")[:80]}


def _norm_workflow_template(value: Any, record: "PluginRecord", context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """净化"工作流模板"返回值（结构性收敛）。

    注意：这里**不做**安全判定——"高危步骤强制人工审批、禁止覆盖内置模板"
    由内核 `WorkflowEngine.register_external_template` 执行，插件层无权绕过。
    """
    raw = value.get("templates") if isinstance(value, dict) else value
    if not isinstance(raw, (list, tuple)):
        return None
    templates: List[Dict[str, Any]] = []
    for item in list(raw)[:_MAX_TEMPLATES_PER_PLUGIN]:
        if not isinstance(item, dict):
            continue
        steps_raw = item.get("steps")
        if not isinstance(steps_raw, (list, tuple)) or not steps_raw:
            continue
        steps: List[Dict[str, Any]] = []
        for step in list(steps_raw)[:_MAX_STEPS_PER_TEMPLATE]:
            if not isinstance(step, dict) or not str(step.get("tool_name") or "").strip():
                continue
            args = step.get("tool_args")
            approval = str(step.get("approval") or "manual").strip().lower()
            steps.append({
                "name": str(step.get("name") or "未命名步骤")[:60],
                "description": str(step.get("description") or "")[:200],
                "tool_name": str(step.get("tool_name")).strip()[:40],
                "tool_args": dict(args) if isinstance(args, dict) else {},
                # 保守映射：只有明确写 auto 才算自动，其余一律要求人工审批
                "approval": "auto" if approval in _AUTO_WORDS else "manual",
            })
        if not steps:
            continue
        templates.append({
            "id": str(item.get("id") or "").strip()[:40],
            "name": str(item.get("name") or "")[:60],
            "description": str(item.get("description") or "")[:200],
            "path_var": str(item.get("path_var") or "")[:40],
            "steps": steps,
        })
    if not templates:
        return None
    return {"templates": templates, "count": len(templates)}


def _match_ext(record: "PluginRecord", context: Dict[str, Any]) -> bool:
    asked = str(context.get("ext") or "").lower().lstrip(".")
    return bool(asked) and asked in record.file_exts


def _match_export(record: "PluginRecord", context: Dict[str, Any]) -> bool:
    kind = str(context.get("kind") or "").lower()
    fmt = str(context.get("format") or "").lower()
    return fmt in (record.export_formats.get(kind) or [])


HOOK_SPECS: Dict[str, HookSpec] = {
    "advisory_for_approval": HookSpec(
        name="advisory_for_approval", kind="advisory",
        summary="在人工审批弹窗里给出决策提示（只影响界面提示，不参与任何判定）",
        returns="{level, text, …}",
        default_timeout=5.0, max_timeout=120.0,
    ),
    "recognizer": HookSpec(
        name="recognizer", kind="additive",
        summary="补充识别敏感实体（只扩大脱敏范围，不能让任何内容免于脱敏）",
        returns="{spans: [{start, end, type, level}]}",
        default_timeout=1.5, max_timeout=5.0, normalize=_norm_recognizer,
    ),
    "attachment_parser": HookSpec(
        name="attachment_parser", kind="additive",
        summary="解析内核不支持的附件格式（产出文本后仍走同一条脱敏管线）",
        returns="{text, note}",
        matches=_match_ext, default_timeout=30.0, max_timeout=30.0,
        normalize=_norm_attachment_parser,
    ),
    "workflow_template": HookSpec(
        name="workflow_template", kind="additive",
        summary="提供工作流模板（高危步骤会被内核强制改为人工审批）",
        returns="{templates: [...]}",
        default_timeout=10.0, max_timeout=10.0, normalize=_norm_workflow_template,
    ),
    "export_renderer": HookSpec(
        name="export_renderer", kind="additive",
        summary="渲染导出格式（内核始终保留内置格式作为兜底）",
        returns="{text, ext, suggested_name}",
        matches=_match_export, default_timeout=15.0, max_timeout=15.0,
        normalize=_norm_export_renderer,
    ),
}

# 兼容旧引用：钩子名 → 说明
HOOKS: Dict[str, str] = {name: spec.summary for name, spec in HOOK_SPECS.items()}

_HOOK_TIMEOUT = 5.0          # 未声明超时时的默认值


@dataclass
class PluginRecord:
    """一个已发现的插件。"""
    plugin_id: str
    name: str
    version: str
    description: str
    path: Path
    source: str                      # builtin | user
    hooks: List[str] = field(default_factory=list)
    requires: Dict[str, Any] = field(default_factory=dict)
    permissions: Dict[str, Any] = field(default_factory=dict)
    entry: str = "plugin.py"
    timeout_seconds: float = _HOOK_TIMEOUT
    timeout_declared: bool = False
    file_exts: List[str] = field(default_factory=list)
    export_formats: Dict[str, List[str]] = field(default_factory=dict)
    format_labels: Dict[str, str] = field(default_factory=dict)
    config_files: List[Dict[str, str]] = field(default_factory=list)
    provides: List[str] = field(default_factory=list)
    error: Optional[str] = None      # manifest 校验失败的原因
    missing: List[str] = field(default_factory=list)   # 未满足的依赖（人类可读）
    enabled: bool = False
    runtime: Dict[str, Any] = field(default_factory=dict)  # 最近一次被调用的运行状态（界面用）
    _module: Any = None

    # ── 展示用 ────────────────────────────────
    def capabilities(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for hook in self.hooks:
            spec = HOOK_SPECS.get(hook)
            if spec is None:
                continue
            detail = ""
            if hook == "attachment_parser":
                detail = "负责格式：" + "、".join(self.file_exts)
            elif hook == "export_renderer":
                detail = "；".join(
                    f"{kind} → " + "/".join(self.format_labels.get(f) or f for f in formats)
                    for kind, formats in sorted(self.export_formats.items())
                )
            elif hook == "workflow_template":
                detail = "模板由内核校验后注册（高危步骤强制人工审批）"
            out.append({"hook": hook, "kind": spec.kind, "summary": spec.summary,
                        "detail": detail, "timeout_cap": spec.max_timeout})
        return out

    def to_public(self) -> Dict[str, Any]:
        return {
            "id": self.plugin_id, "name": self.name, "version": self.version,
            "description": self.description, "source": self.source,
            "hooks": list(self.hooks), "permissions": dict(self.permissions),
            "enabled": bool(self.enabled), "error": self.error,
            "timeout_seconds": self.timeout_seconds,
            "timeout_declared": bool(self.timeout_declared),
            "missing": list(self.missing),
            "usable": self.error is None and not self.missing,
            "file_exts": list(self.file_exts),
            "export_formats": {k: list(v) for k, v in self.export_formats.items()},
            "format_labels": dict(self.format_labels),
            "config_files": [dict(item) for item in self.config_files],
            "provides": list(self.provides),
            "runtime": dict(self.runtime),
            "capabilities": self.capabilities(),
        }


class PluginHost:
    """插件宿主：发现 → 校验 → 启停 → 调用 → 能力查询。"""

    def __init__(self,
                 enabled_lookup: Optional[Callable[[str], bool]] = None,
                 enabled_setter: Optional[Callable[[str, bool], None]] = None,
                 builtin_dir: Optional[Path] = None,
                 user_dir: Optional[Path] = None,
                 model_lister: Optional[Callable[[], List[str]]] = None,
                 data_root: Optional[Path] = None) -> None:
        self._builtin_dir = Path(builtin_dir) if builtin_dir else Path(__file__).resolve().parent / "builtin"
        self._user_dir = Path(user_dir) if user_dir else APP_ROOT / "plugins"
        self._data_root = Path(data_root) if data_root else APP_ROOT / "plugins_data"
        self._enabled_lookup = enabled_lookup or (lambda _pid: False)
        self._enabled_setter = enabled_setter or (lambda _pid, _on: None)
        self._model_lister = model_lister       # 返回本地可用模型名列表（用于依赖检查）
        self._records: Dict[str, PluginRecord] = {}
        self._lock = threading.RLock()

    # ── 发现与校验 ────────────────────────────────
    def discover(self) -> List[PluginRecord]:
        """扫描两处插件目录，解析并校验 manifest。用户目录同名插件覆盖内置插件。"""
        found: Dict[str, PluginRecord] = {}
        for directory, source in ((self._builtin_dir, "builtin"), (self._user_dir, "user")):
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                if not child.is_dir():
                    continue
                record = self._load_manifest(child, source)
                if record is None:
                    continue
                if record.plugin_id in found and source == "builtin":
                    continue          # 用户安装的同 id 插件优先
                found[record.plugin_id] = record
        with self._lock:
            self._records = found
        return list(found.values())

    def _load_manifest(self, directory: Path, source: str) -> Optional[PluginRecord]:
        manifest_path = directory / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return PluginRecord(plugin_id=directory.name, name=directory.name, version="-",
                                description="", path=directory, source=source,
                                error=f"manifest.json 解析失败：{exc}")
        missing_fields = [f for f in ("id", "name", "version", "hooks") if not data.get(f)]
        plugin_id = str(data.get("id") or directory.name)
        record = PluginRecord(
            plugin_id=plugin_id,
            name=str(data.get("name") or plugin_id),
            version=str(data.get("version") or "-"),
            description=str(data.get("description") or ""),
            path=directory, source=source,
            hooks=[str(h) for h in (data.get("hooks") or [])],
            requires=dict(data.get("requires") or {}),
            permissions=dict(data.get("permissions") or {}),
            entry=str(data.get("entry") or "plugin.py"),
            file_exts=self._normalize_exts(data.get("file_exts")),
            export_formats=self._normalize_export_formats(data.get("export_formats")),
            format_labels=self._normalize_format_labels(data.get("format_labels")),
            config_files=self._normalize_config_files(data.get("config_files")),
            provides=[str(p)[:80] for p in (data.get("provides") or [])][:12],
        )
        record.timeout_declared = data.get("timeout_seconds") is not None
        try:
            declared = float(data.get("timeout_seconds") or _HOOK_TIMEOUT)
        except Exception:
            declared = _HOOK_TIMEOUT
        record.timeout_seconds = max(1.0, min(declared, 120.0))
        if missing_fields:
            record.error = f"manifest 缺少必填字段：{', '.join(missing_fields)}"
            return record
        if not _ID_RE.match(plugin_id):
            # 非法 id 不要静默丢弃，也不要拿怪 id 当键：改用目录名登记，
            # 让设置页能显示"某个插件目录的配置有问题"，用户才有机会修好它。
            record.plugin_id = directory.name
            record.error = f"插件 id 非法（仅允许字母数字下划线，2-32 位）：{data.get('id')}"
            return record
        forbidden = sorted(h for h in record.hooks if h in FORBIDDEN_HOOKS)
        if forbidden:
            record.error = (f"申请了永不开放的能力：{', '.join(forbidden)}"
                            f"（插件不得参与脱敏豁免 / 白名单 / 审批放行 / 审计写入）")
            return record
        bad_hooks = [h for h in record.hooks if h not in HOOK_SPECS]
        if bad_hooks:
            record.error = f"声明了不支持的钩子：{', '.join(bad_hooks)}（可用钩子见 docs/插件开发.md）"
            return record
        # 声明了增量能力却没说明"管什么"，一律拒绝：含糊的插件无法被安全调用
        if "attachment_parser" in record.hooks and not record.file_exts:
            record.error = "attachment_parser 插件必须用 file_exts 声明负责的扩展名"
            return record
        if "export_renderer" in record.hooks and not record.export_formats:
            record.error = "export_renderer 插件必须用 export_formats 声明支持的导出类型与格式"
            return record
        for kind, formats in (record.export_formats or {}).items():
            reserved = sorted(set(formats) & RESERVED_EXPORT_FORMATS.get(kind, set()))
            if reserved:
                record.error = (f"导出格式名被内核保留：{', '.join(reserved)}"
                                f"（内核格式始终可用，插件请用自己的格式名，"
                                f"可在 format_labels 里给中文标签）")
                return record
        if not (directory / record.entry).is_file():
            record.error = f"找不到入口模块：{record.entry}"
            return record
        record.missing = self._check_requirements(record)
        record.enabled = bool(self._enabled_lookup(plugin_id))
        return record

    @staticmethod
    def _normalize_exts(value: Any) -> List[str]:
        out: List[str] = []
        for item in (value if isinstance(value, (list, tuple)) else []) or []:
            ext = str(item).strip().lower().lstrip(".")
            if _EXT_RE.match(ext) and ext not in out:
                out.append(ext)
        return out[:24]

    @staticmethod
    def _normalize_export_formats(value: Any) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        if not isinstance(value, dict):
            return out
        for kind in ("audit", "workflow"):
            formats: List[str] = []
            for item in (value.get(kind) or []):
                fmt = str(item).strip().lower()
                if _FORMAT_RE.match(fmt) and fmt not in formats:
                    formats.append(fmt)
            if formats:
                out[kind] = formats[:8]
        return out

    @staticmethod
    def _normalize_format_labels(value: Any) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if not isinstance(value, dict):
            return out
        for key, label in list(value.items())[:16]:
            name = str(key).strip().lower()
            if _FORMAT_RE.match(name) and str(label).strip():
                out[name] = str(label).strip()[:24]
        return out

    @staticmethod
    def _normalize_config_files(value: Any) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        for item in (value if isinstance(value, (list, tuple)) else []) or []:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            # 只允许平台中立的简单文件名：挡掉子目录与 ../ 越界写法
            if not _FILE_NAME_RE.match(name) or ".." in name:
                continue
            out.append({"name": name,
                        "label": str(item.get("label") or name)[:40],
                        "hint": str(item.get("hint") or "")[:200]})
        return out[:6]

    def _check_requirements(self, record: PluginRecord) -> List[str]:
        """检查声明依赖：Python 包是否可导入、所需的本地模型是否存在。"""
        missing: List[str] = []
        for pkg in record.requires.get("python", []) or []:
            name = re.split(r"[<>=!\[;]", str(pkg), 1)[0].strip()
            if not name:
                continue
            if importlib.util.find_spec(name) is None:
                missing.append(f"缺少 Python 依赖：{pkg}（pip install {name}）")
        wanted = [str(m) for m in (record.requires.get("ollama_models") or [])]
        if wanted and self._model_lister is not None:
            try:
                available = list(self._model_lister() or [])
            except Exception:
                available = []
            for pattern in wanted:
                prefix = pattern.rstrip("*")
                if not any(m.startswith(prefix) for m in available):
                    missing.append(f"缺少本地模型：{pattern}（ollama pull {prefix.rstrip(':') or pattern}）")
        return missing

    # ── 查询与启停 ────────────────────────────────
    def list_plugins(self) -> List[Dict[str, Any]]:
        with self._lock:
            records = list(self._records.values())
        if not records:
            records = self.discover()
        return [r.to_public() for r in records]

    def get(self, plugin_id: str) -> Optional[PluginRecord]:
        with self._lock:
            record = self._records.get(plugin_id)
        if record is None:
            self.discover()
            with self._lock:
                record = self._records.get(plugin_id)
        return record

    def enabled_plugins(self, hook: Optional[str] = None) -> List[PluginRecord]:
        """当前可用（已启用、无错误、依赖齐全）的插件；可按钩子过滤。"""
        if not self._records:
            self.discover()
        out: List[PluginRecord] = []
        with self._lock:
            records = list(self._records.values())
        for record in records:
            if not record.enabled or record.error or record.missing:
                continue
            if hook and hook not in record.hooks:
                continue
            out.append(record)
        return out

    def set_enabled(self, plugin_id: str, enabled: bool) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"未找到插件：{plugin_id}"}
        if enabled and record.error:
            return {"ok": False, "error": f"插件不可用：{record.error}"}
        if enabled and record.missing:
            return {"ok": False, "error": "依赖未满足：" + "；".join(record.missing)}
        record.enabled = bool(enabled)
        self._enabled_setter(plugin_id, bool(enabled))
        if not enabled:
            record._module = None
        return {"ok": True, "plugins": self.list_plugins()}

    # ── 插件数据目录 / 可编辑配置 ──────────────────
    def data_dir(self, plugin_id: str) -> Optional[Path]:
        """插件的数据目录（用户插件=插件目录内 data/；内置插件=<APP_ROOT>/plugins_data/<id>）。

        内置插件在打包版里位于只读的解包目录，必须把可写数据放到 APP_ROOT，
        否则用户改了词表、重启就丢。
        """
        record = self.get(plugin_id)
        if record is None:
            return None
        base = (record.path / "data") if record.source == "user" else (self._data_root / record.plugin_id)
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        return base

    def _declared_config(self, record: PluginRecord, name: str) -> Optional[Dict[str, str]]:
        if not _FILE_NAME_RE.match(name or "") or ".." in (name or ""):
            return None
        for item in record.config_files:
            if item.get("name") == name:
                return item
        return None

    def read_config_file(self, plugin_id: str, name: str) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"未找到插件：{plugin_id}"}
        if self._declared_config(record, name) is None:
            return {"ok": False, "error": f"该文件未在 manifest 的 config_files 中声明：{name}"}
        directory = self.data_dir(plugin_id)
        if directory is None:
            return {"ok": False, "error": "插件数据目录不可用"}
        path = directory / name
        text = ""
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")[:_MAX_CONFIG_BYTES]
            except Exception as exc:
                return {"ok": False, "error": f"读取失败：{exc}"}
        return {"ok": True, "text": text, "path": str(path), "exists": path.is_file()}

    def write_config_file(self, plugin_id: str, name: str, text: str) -> Dict[str, Any]:
        record = self.get(plugin_id)
        if record is None:
            return {"ok": False, "error": f"未找到插件：{plugin_id}"}
        if self._declared_config(record, name) is None:
            return {"ok": False, "error": f"该文件未在 manifest 的 config_files 中声明：{name}"}
        payload = str(text or "")
        if len(payload.encode("utf-8")) > _MAX_CONFIG_BYTES:
            return {"ok": False, "error": f"内容过大（上限 {_MAX_CONFIG_BYTES // 1024}KB）"}
        directory = self.data_dir(plugin_id)
        if directory is None:
            return {"ok": False, "error": "插件数据目录不可用"}
        path = directory / name          # 文件名已校验：无分隔符、无 ..，落点受控在插件数据目录内
        try:
            path.write_text(payload, encoding="utf-8")
        except Exception as exc:
            return {"ok": False, "error": f"写入失败：{exc}"}
        return {"ok": True, "path": str(path), "bytes": len(payload.encode("utf-8"))}

    # ── 能力查询（供界面/服务层声明"谁提供了什么"）──
    def claimed_exts(self) -> List[str]:
        """所有可用插件声明负责的扩展名（供文件对话框与解析回退使用）。"""
        out: List[str] = []
        for record in self.enabled_plugins("attachment_parser"):
            for ext in record.file_exts:
                if ext not in out:
                    out.append(ext)
        return out

    def export_formats(self, kind: str) -> List[Dict[str, str]]:
        """某类导出（audit / workflow）可用的**插件**格式（内核内置格式不在此列）。"""
        kind = str(kind or "").lower()
        out: List[Dict[str, str]] = []
        seen = set()
        for record in self.enabled_plugins("export_renderer"):
            for fmt in record.export_formats.get(kind, []):
                if fmt in seen:
                    continue
                seen.add(fmt)
                out.append({"format": fmt,
                            "label": record.format_labels.get(fmt) or fmt,
                            "plugin_id": record.plugin_id,
                            "source": record.name, "kind": "additive"})
        return out

    # ── 自检（用户点一下就能知道插件是否真的能跑）──
    def selfcheck(self, plugin_id: str = "") -> Dict[str, Any]:
        """逐个导入插件模块，确认每个声明的钩子都真的有同名可调用函数。

        为什么必须有：钩子函数缺失时，调用侧只会"什么都没发生"——
        用户以为启用了、其实一次都没生效。这种静默失效在安全产品里等于欺骗，
        所以提供一次显式自检，把"能不能跑"变成可核对的结论。
        """
        records = [self.get(plugin_id)] if plugin_id else list(self._records.values())
        if not records:
            self.discover()
            records = [self.get(plugin_id)] if plugin_id else list(self._records.values())
        report: List[Dict[str, Any]] = []
        for record in records:
            if record is None:
                continue
            item: Dict[str, Any] = {"id": record.plugin_id, "name": record.name,
                                    "enabled": record.enabled, "usable": record.error is None
                                    and not record.missing, "hooks": {}, "error": record.error}
            if record.error or record.missing:
                item["skipped"] = True
                report.append(item)
                continue
            for hook in record.hooks:
                spec = HOOK_SPECS.get(hook)
                if spec is None or spec.normalize is None:
                    # 建议型钩子：只看函数是否存在
                    pass
                outcome: Dict[str, Any] = {}

                def _probe() -> None:
                    try:
                        module = self._module_for(record)
                        outcome["ok"] = callable(getattr(module, hook, None))
                    except Exception as exc:
                        outcome["ok"] = False
                        outcome["error"] = f"{type(exc).__name__}: {exc}"

                thread = threading.Thread(target=_probe, daemon=True)
                thread.start()
                thread.join(10.0)
                if thread.is_alive():
                    item["hooks"][hook] = {"ok": False, "error": "自检超时（>10s）"}
                elif outcome.get("ok"):
                    item["hooks"][hook] = {"ok": True, "error": ""}
                else:
                    item["hooks"][hook] = {"ok": False,
                                           "error": outcome.get("error")
                                           or f"入口模块缺少 {hook}() 函数"}
            item["ok"] = all(h.get("ok") for h in item["hooks"].values()) if item["hooks"] else True
            report.append(item)
        return {"ok": all(item.get("ok") for item in report),
                "plugins": report}

    # ── 调用 ─────────────────────────────────────
    def _module_for(self, record: PluginRecord):
        if record._module is not None:
            return record._module
        entry = record.path / record.entry
        spec = importlib.util.spec_from_file_location(f"yindun_plugin_{record.plugin_id}", entry)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"无法加载插件模块：{entry}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # 与插件的约定：数据目录由宿主注入。内置插件在打包版里位于只读解包目录，
        # 可写数据必须落在 APP_ROOT 下，否则用户改了词表、重启就丢。
        try:
            module.PLUGIN_ID = record.plugin_id
            directory = self.data_dir(record.plugin_id)
            module.PLUGIN_DATA_DIR = str(directory) if directory else ""
        except Exception as exc:
            print(f"[PluginHost] 注入插件数据目录失败（{record.plugin_id}）：{exc}")
        record._module = module
        return module

    def call_hook(self, hook: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """调用所有已启用插件的指定钩子，返回**净化后**的结果列表。

        每个插件：
          · 依赖未满足 / 未启用 / 未声明该钩子 / 与本次上下文无关 → 跳过
          · 抛异常 → 记录并跳过（不影响其它插件与主流程）
          · 超时（按钩子硬上限截断）→ 跳过并提示（内联路径不允许长时间阻塞）
          · 返回值不合法 → 丢弃（宁可少一条增强，也不让脏数据进入内核）
        """
        spec = HOOK_SPECS.get(hook)
        if spec is None:
            return []
        results: List[Dict[str, Any]] = []
        for record in self.enabled_plugins(hook):
            if spec.matches is not None:
                try:
                    if not spec.matches(record, context):
                        continue
                except Exception:
                    continue
            outcome: Dict[str, Any] = {}

            def _run() -> None:
                try:
                    module = self._module_for(record)
                    fn = getattr(module, hook, None)
                    if not callable(fn):
                        # 静默跳过会造成"启用了却没反应"的假象，必须当成错误暴露出来
                        outcome["error"] = f"入口模块没有定义与钩子同名的函数：{hook}()"
                        return
                    outcome["value"] = fn(dict(context))
                except Exception as exc:
                    outcome["error"] = f"{type(exc).__name__}: {exc}"

            timeout = min(record.timeout_seconds if record.timeout_declared else spec.default_timeout,
                          spec.max_timeout)
            thread = threading.Thread(target=_run, daemon=True)
            started = time.monotonic()
            thread.start()
            thread.join(timeout=timeout)
            if thread.is_alive():
                _note_runtime(record, "timeout", int(timeout * 1000), hook,
                              f"超时（>{timeout:.1f}s）")
                print(f"[PluginHost] 插件 {record.plugin_id} 的 {hook} 超时（>{timeout:.1f}s），已跳过")
                continue
            if outcome.get("error"):
                _note_runtime(record, "error", int((time.monotonic() - started) * 1000), hook,
                              outcome["error"])
                print(f"[PluginHost] 插件 {record.plugin_id} 的 {hook} 失败：{outcome['error']}")
                continue
            value = outcome.get("value")
            if spec.normalize is not None:
                value = spec.normalize(value, record, context)
            if not isinstance(value, dict):
                _note_runtime(record, "empty", int((time.monotonic() - started) * 1000), hook)
                continue
            _note_runtime(record, "ok", int((time.monotonic() - started) * 1000), hook)
            value.setdefault("source", record.name)
            value["plugin_id"] = record.plugin_id
            value["kind"] = spec.kind
            value["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            results.append(value)
        return results
