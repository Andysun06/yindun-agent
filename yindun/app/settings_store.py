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
    "model": "qwen2.5:7b",
    "privacy": True,
    "dark_mode": False,
    "topmost": True,
    "thinking_depth": 3,
    "permission": "完全控制 (读/写/列表)",
    "think_mode": "快速回答",
    "custom_models": {},
    "ollama_models_cache": [],
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

    # ── 读 ────────────────────────────────────────
    @property
    def data(self) -> Dict[str, Any]:
        with self._lock:
            return self._data

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._data.get(key, DEFAULTS.get(key, default) if default is None else default)

    def load(self) -> Dict[str, Any]:
        """从磁盘加载；损坏时保留默认值并保持可见（不静默吞掉）。"""
        with self._lock:
            self._data = dict(DEFAULTS)
            if not self._path.exists():
                return self._data
            try:
                with self._path.open("r", encoding="utf-8") as handle:
                    saved = json.load(handle)
                if isinstance(saved, dict):
                    self._data.update(saved)
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
