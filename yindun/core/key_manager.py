# -*- coding: utf-8 -*-
"""
隐盾密钥管理中心 — 脱敏密钥持久化存储
=====================================
提供带 TTL 过期的脱敏密钥生成、撤销、查询功能。
密钥以 JSON 文件持久化，含 SHA256 哈希防篡改校验。

使用示例：
    km = KeyManager()
    key = km.generate_key("session_abc", ttl_hours=24)
    valid = km.validate("session_abc")
    km.revoke("session_abc")
"""

import os
import json
import hashlib
import secrets
import time
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta


class KeyEntry:
    """单条密钥记录"""

    def __init__(self, key_id: str, scope: str, cipher_key: str,
                 created_at: str, expires_at: str, metadata: dict = None):
        self.key_id = key_id
        self.scope = scope
        self.cipher_key = cipher_key          # 实际密钥（用于加密脱敏映射）
        self.created_at = created_at
        self.expires_at = expires_at
        self.metadata = metadata or {}
        self.revoked = False
        self.entry_hash = ""

    def compute_hash(self, previous_hash: str = "") -> str:
        data = json.dumps({
            "key_id": self.key_id,
            "scope": self.scope,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "revoked": self.revoked,
            "previous_hash": previous_hash
        }, ensure_ascii=False, sort_keys=True)
        self.entry_hash = hashlib.sha256(data.encode('utf-8')).hexdigest()
        return self.entry_hash

    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now() > exp
        except (ValueError, TypeError):
            return False

    def is_valid(self) -> bool:
        return not self.revoked and not self.is_expired()

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "scope": self.scope,
            "cipher_key": self.cipher_key,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
            "revoked": self.revoked,
            "entry_hash": self.entry_hash
        }

    @classmethod
    def from_dict(cls, data: dict) -> "KeyEntry":
        entry = cls(
            key_id=data.get("key_id", ""),
            scope=data.get("scope", ""),
            cipher_key=data.get("cipher_key", ""),
            created_at=data.get("created_at", ""),
            expires_at=data.get("expires_at", ""),
            metadata=data.get("metadata", {})
        )
        entry.revoked = data.get("revoked", False)
        entry.entry_hash = data.get("entry_hash", "")
        return entry


class KeyManager:
    """
    密钥管理器（单例）

    功能：
    - generate_key(key_id, ttl_hours): 生成新密钥
    - get_key(key_id): 获取有效密钥
    - revoke(key_id): 撤销密钥
    - list_keys(valid_only=True): 列出所有密钥
    - verify_integrity(): 验证密钥链完整性
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, storage_dir: str = None):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._entries: List[KeyEntry] = []
        self._storage_path = Path(storage_dir) if storage_dir else (
            Path(__file__).resolve().parents[2] / "secure_keys"
        )
        self._storage_path.mkdir(exist_ok=True)
        self._load()
        self._initialized = True

    # ── 持久化 ──────────────────────────────

    def _load(self):
        key_file = self._storage_path / "key_store.json"
        if key_file.exists():
            try:
                with open(key_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    old_hash = ""
                    for entry_data in data:
                        entry = KeyEntry.from_dict(entry_data)
                        entry.compute_hash(old_hash)
                        self._entries.append(entry)
                        old_hash = entry.entry_hash
            except Exception:
                pass

    def _save(self):
        key_file = self._storage_path / "key_store.json"
        try:
            with open(key_file, "w", encoding="utf-8") as f:
                json.dump([e.to_dict() for e in self._entries],
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ── CRUD ──────────────────────────────

    def generate_key(self, key_id: str, scope: str = "default",
                     ttl_hours: int = 24, metadata: dict = None) -> Optional[KeyEntry]:
        """
        生成新脱敏密钥。

        参数：
            key_id: 密钥标识（如 session_id）
            scope: 作用域（如 "session", "document"）
            ttl_hours: 有效期（小时），0 表示永不过期
            metadata: 附加元数据

        返回：KeyEntry 或 None（key_id 已存在且有效时）
        """
        # 检查是否已存在有效同名密钥
        existing = self.get_key(key_id)
        if existing:
            return None

        cipher = secrets.token_hex(32)  # 256-bit 随机密钥
        now = datetime.now()
        expires = now + timedelta(hours=ttl_hours) if ttl_hours > 0 else None
        entry = KeyEntry(
            key_id=key_id,
            scope=scope,
            cipher_key=cipher,
            created_at=now.isoformat(),
            expires_at=expires.isoformat() if expires else "",
            metadata=metadata or {}
        )
        # 计算哈希链
        prev_hash = self._entries[-1].entry_hash if self._entries else ""
        entry.compute_hash(prev_hash)
        self._entries.append(entry)
        self._save()
        return entry

    def get_key(self, key_id: str) -> Optional[KeyEntry]:
        """获取有效密钥"""
        for entry in reversed(self._entries):
            if entry.key_id == key_id and entry.is_valid():
                return entry
        return None

    def revoke(self, key_id: str) -> bool:
        """撤销密钥"""
        for entry in self._entries:
            if entry.key_id == key_id and entry.is_valid():
                entry.revoked = True
                self._save()
                return True
        return False

    def list_keys(self, valid_only: bool = True,
                  scope: str = None) -> List[dict]:
        """列出密钥（返回摘要，不含 cipher_key）"""
        result = []
        for entry in self._entries:
            if valid_only and not entry.is_valid():
                continue
            if scope and entry.scope != scope:
                continue
            result.append({
                "key_id": entry.key_id,
                "scope": entry.scope,
                "created_at": entry.created_at,
                "expires_at": entry.expires_at,
                "revoked": entry.revoked,
                "valid": entry.is_valid(),
                "metadata": entry.metadata
            })
        return result

    def verify_integrity(self) -> bool:
        """验证密钥链完整性（哈希防篡改）"""
        prev_hash = ""
        for entry in self._entries:
            data = json.dumps({
                "key_id": entry.key_id,
                "scope": entry.scope,
                "created_at": entry.created_at,
                "expires_at": entry.expires_at,
                "revoked": entry.revoked,
                "previous_hash": prev_hash
            }, ensure_ascii=False, sort_keys=True)
            expected = hashlib.sha256(data.encode('utf-8')).hexdigest()
            if entry.entry_hash != expected:
                return False
            prev_hash = entry.entry_hash
        return True

    def cleanup_expired(self) -> int:
        """清理过期密钥，返回清理数量"""
        before = len(self._entries)
        self._entries = [e for e in self._entries if e.is_valid()]
        cleaned = before - len(self._entries)
        if cleaned > 0:
            self._save()
        return cleaned
