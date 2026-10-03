# -*- coding: utf-8 -*-
"""隐盾 · 插件宿主（发现 / 校验 / 启停 / 调用）

插件形态：一个目录，内含 `manifest.json` 与入口模块（默认 `plugin.py`）

    <plugin_dir>/
      manifest.json
      plugin.py

manifest 字段：
    id            必填，唯一标识（字母数字下划线）
    name          必填，展示名
    version       必填，语义化版本
    entry         选填，入口模块文件名（默认 plugin.py）
    description   选填，一句话说明（会显示在设置页）
    hooks         必填，声明的钩子名（必须是 HOOKS 里的"建议型"钩子）
    requires      选填 {"python": ["torch>=2.0"], "ollama_models": ["qwen2.5*"]}
    permissions   选填 {"network": "local-only"|"none", "filesystem": "read"|"none"}

★ 安全边界：
  · HOOKS 只包含"给人类看的建议"，插件**无法**参与脱敏/白名单/审批放行判定；
  · 宿主不下载任何代码；插件目录里的内容由使用者自行负责（安装即启用前有明确提示）；
  · 每个钩子调用都被 try/except 与超时包裹，插件异常/卡死不会影响主流程。
"""
from __future__ import annotations

import importlib.util
import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from yindun import APP_ROOT

# ── 允许的钩子（全部为"建议型"：只影响界面提示，不影响任何安全判定）──
HOOKS = {
    "advisory_for_approval": "在人工审批弹窗里给出一条决策提示（如：该操作与你刚才的请求是否相符）",
}

_ID_RE = re.compile(r"^[A-Za-z0-9_]{2,32}$")
_HOOK_TIMEOUT = 3.0          # 单个插件的单次钩子调用超时（秒）
_REQUIRED_FIELDS = ("id", "name", "version", "hooks")


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
    error: Optional[str] = None      # manifest 校验失败的原因
    missing: List[str] = field(default_factory=list)   # 未满足的依赖（人类可读）
    enabled: bool = False
    _module: Any = None

    def to_public(self) -> Dict[str, Any]:
        return {
            "id": self.plugin_id, "name": self.name, "version": self.version,
            "description": self.description, "source": self.source,
            "hooks": list(self.hooks), "permissions": dict(self.permissions),
            "enabled": bool(self.enabled), "error": self.error,
            "missing": list(self.missing),
            "usable": self.error is None and not self.missing,
        }


class PluginHost:
    """插件宿主：发现 → 校验 → 启停 → 调用。"""

    def __init__(self,
                 enabled_lookup: Optional[Callable[[str], bool]] = None,
                 enabled_setter: Optional[Callable[[str, bool], None]] = None,
                 builtin_dir: Optional[Path] = None,
                 user_dir: Optional[Path] = None,
                 model_lister: Optional[Callable[[], List[str]]] = None) -> None:
        self._builtin_dir = Path(builtin_dir) if builtin_dir else Path(__file__).resolve().parent / "builtin"
        self._user_dir = Path(user_dir) if user_dir else APP_ROOT / "plugins"
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
        missing_fields = [f for f in _REQUIRED_FIELDS if not data.get(f)]
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
        )
        if missing_fields:
            record.error = f"manifest 缺少必填字段：{', '.join(missing_fields)}"
            return record
        if not _ID_RE.match(plugin_id):
            # 非法 id 不要静默丢弃，也不要拿怪 id 当键：改用目录名登记，
            # 让设置页能显示"某个插件目录的配置有问题"，用户才有机会修好它。
            record.plugin_id = directory.name
            record.error = f"插件 id 非法（仅允许字母数字下划线，2-32 位）：{data.get('id')}"
            return record
        bad_hooks = [h for h in record.hooks if h not in HOOKS]
        if bad_hooks:
            # 关键安全约束：不接受未声明的钩子（尤其不得申请"判定型"能力）
            record.error = f"声明了不支持的钩子：{', '.join(bad_hooks)}（插件只能提供建议型钩子）"
            return record
        if not (directory / record.entry).is_file():
            record.error = f"找不到入口模块：{record.entry}"
            return record
        record.missing = self._check_requirements(record)
        record.enabled = bool(self._enabled_lookup(plugin_id))
        return record

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
        record._module = module
        return module

    def call_hook(self, hook: str, context: Dict[str, Any]) -> List[Dict[str, Any]]:
        """调用所有已启用插件的指定钩子，返回建议列表。

        每个插件：
          · 依赖未满足 / 未启用 → 跳过
          · 抛异常 → 记录并跳过（不影响其它插件与主流程）
          · 超时（默认 3s）→ 跳过并在结果里标注（避免拖住界面）
        """
        if hook not in HOOKS:
            return []
        results: List[Dict[str, Any]] = []
        for record in list(self._records.values()):
            if not record.enabled or record.error or record.missing:
                continue
            if hook not in record.hooks:
                continue
            outcome: Dict[str, Any] = {}

            def _run() -> None:
                try:
                    module = self._module_for(record)
                    fn = getattr(module, hook, None)
                    outcome["value"] = fn(dict(context)) if callable(fn) else None
                except Exception as exc:
                    outcome["error"] = f"{type(exc).__name__}: {exc}"

            thread = threading.Thread(target=_run, daemon=True)
            started = time.monotonic()
            thread.start()
            thread.join(timeout=_HOOK_TIMEOUT)
            if thread.is_alive():
                print(f"[PluginHost] 插件 {record.plugin_id} 的 {hook} 超时（>{_HOOK_TIMEOUT}s），已跳过")
                continue
            if outcome.get("error"):
                print(f"[PluginHost] 插件 {record.plugin_id} 的 {hook} 失败：{outcome['error']}")
                continue
            value = outcome.get("value")
            if not isinstance(value, dict):
                continue
            value.setdefault("source", record.name)
            value["plugin_id"] = record.plugin_id
            value["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            results.append(value)
        return results
