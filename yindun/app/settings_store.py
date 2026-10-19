# -*- coding: utf-8 -*-
"""隐盾 · 配置存储（global_config.json）

职责：读写应用配置；自定义模型的 api_key 在**落盘前加密**（Fernet，密钥由 SecretManager
以 DPAPI 保护），内存中保持明文供调用方使用。与旧版 main_window 的实现保持**同一文件格式**，
老用户配置无需迁移。

与界面无关：本模块不导入任何界面框架。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from yindun import APP_ROOT
from yindun.core.secret_manager import SecretManager

CONFIG_FILE = APP_ROOT / "global_config.json"

# 默认配置（与旧版字段名保持一致）
DEFAULTS: Dict[str, Any] = {
    # ★ 不写死默认模型名（V3.6.8，负责人实测）：便携版拷到别的机器上，本机根本没有
    #   `qwen3.5:4b`，界面却显示"已就绪: qwen3.5:4b"——对着一个不存在的模型宣称就绪。
    #   现在默认是**空串 = 未选择**：首启由服务层按本机检测结果自动选第一个可对话模型
    #   （见 AgentService._resolve_model_selection），检测不到就保持空，界面显示"请选择模型"。
    "model": "",
    "ollama_host": "http://127.0.0.1:11434",
    "privacy": True,
    "dark_mode": False,
    # 窗口置顶**默认关闭**（V3.6.7，负责人明确要求）：常驻最上层会挡住别的窗口，
    # 新用户第一眼的观感是"这软件抢焦点"。要置顶在设置页/顶栏图钉里一键开，值会持久化；
    # 已有配置里存着 true 的用户不受影响（默认值只作用于首次运行/新目录）。
    "topmost": False,
    "thinking_depth": 3,
    # 四档权限：plan(计划模式) / ask(变更前确认·默认) / auto(自主模式) / full(完全访问)
    # 旧版中文档位名（彻底审计/安全只读/完全控制）由 core/permission.resolve_mode 自动兼容
    "permission": "ask",
    # 是否允许执行本地脚本（解释器）：关闭 = 严格模式，禁用 python/python3，仅保留只读命令
    "allow_script_exec": True,
    # 可读写范围（沙箱根，V3.6.8）：空串 = 未配置，按"环境变量 SANDBOX_PATH → 程序目录"回退。
    # 键名是**内核契约**：core/permission.SETTINGS_KEY 与它一致，界面只负责读写这个键；
    # 放在这里只是让"新装用户的默认配置"就有这个字段（界面能直接显示当前范围）。
    "sandbox_dir": "",
    "think_mode": "快速回答",
    "custom_models": {},
    "ollama_models_cache": [],
    # 无边框悬浮模式（Web 界面）：窗口无系统边框，由界面顶栏承担拖拽与窗口控制
    "frameless": True,
    # 插件启用状态：{插件ID: bool}（插件默认关闭，按需启用）
    "plugins_enabled": {},
}


class SettingsStore:
    """配置读写（线程安全）。

    用法::

        store = SettingsStore()
        store.load()
        store.set("dark_mode", True)
        store.save()
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else CONFIG_FILE
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = dict(DEFAULTS)
        # 磁盘上**真实出现过**的键（用于区分"用户显式选过"与"从未选过"）。
        # 为什么需要：默认值改成空/新默认后，不能靠"值等于默认值"来判断——那样会把
        # 用户手选的模型误判成默认值而覆盖掉（负责人明确要求不许覆盖用户的选择）。
        self._saved_keys: set = set()

    # ── 读 ────────────────────────────────────────
    @property
    def data(self) -> Dict[str, Any]:
        with self._lock:
            return self._data

    def was_saved(self, key: str) -> bool:
        """该配置键是否曾被写进配置文件（= 用户/上次运行显式设过，而非仅取默认值）。"""
        with self._lock:
            return key in self._saved_keys

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default) if default is None else default)

    def load(self) -> Dict[str, Any]:
        """从磁盘加载；损坏时保留默认值并保持可见（不静默吞掉）。"""
        with self._lock:
            self._data = dict(DEFAULTS)
            self._saved_keys = set()
            if not self._path.exists():
                return self._data
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    saved = json.load(handle)
                if isinstance(saved, dict):
                    self._data.update(saved)
                    self._saved_keys = set(saved.keys())
            except Exception as exc:  # 配置损坏必须可见
                print(f"[SettingsStore] 加载配置失败({type(exc).__name__}): {exc}")
            self._decrypt_custom_model_keys()
            return self._data

    # ── 写 ────────────────────────────────────────
    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def update(self, values: Dict[str, Any]) -> None:
        with self._lock:
            self._data.update(values)

    def save(self) -> None:
        """落盘：api_key 加密为 api_key_enc，内存明文不回写磁盘。"""
        with self._lock:
            payload = dict(self._data)
            models = payload.get("custom_models", {})
            if isinstance(models, dict):
                encrypted_models: Dict[str, Any] = {}
                sm = SecretManager.get_instance()
                for name, info in models.items():
                    if not isinstance(info, dict):
                        continue
                    item = dict(info)
                    api_key = item.pop("api_key", None)
                    item.pop("key_invalid", None)
                    if api_key:
                        try:
                            item["api_key_enc"] = sm.encrypt(api_key)
                        except Exception as exc:
                            # 加密失败绝不回退明文落盘
                            print(f"[SettingsStore] 模型 {name} 的 api_key 加密失败，已丢弃该密钥：{exc}")
                    encrypted_models[name] = item
                payload["custom_models"] = encrypted_models
            try:
                with self._path.open("w", encoding="utf-8") as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
            except Exception as exc:
                print(f"[SettingsStore] 保存配置失败({type(exc).__name__}): {exc}")

    # ── 内部 ─────────────────────────────────────
    def _decrypt_custom_model_keys(self) -> None:
        """加载后：解密 api_key_enc 回填内存明文；失败标记 key_invalid 并审计。"""
        models = self._data.get("custom_models", {})
        if not isinstance(models, dict):
            return
        sm = SecretManager.get_instance()
        for name, info in models.items():
            if not isinstance(info, dict):
                continue
            if not info.get("api_key_enc"):
                continue
            try:
                info["api_key"] = sm.decrypt(info["api_key_enc"])
                info.pop("api_key_enc", None)
            except Exception as exc:
                info["api_key"] = ""
                info["key_invalid"] = True
                print(f"[SettingsStore] 自定义模型 {name} 的 api_key 解密失败，已标记密钥失效：{exc}")
                try:
                    from yindun.core.audit_log import AuditEventType, AuditSeverity, AuditLog
                    AuditLog().add_entry(
                        AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                        f"自定义模型 {name} api_key 解密失败，密钥已失效",
                        {"model_name": name},
                    )
                except Exception:
                    pass
