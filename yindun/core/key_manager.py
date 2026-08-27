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
import hmac
import hashlib
import secrets
import threading
import time
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, timedelta

from .secret_manager import SecretManager
from .audit_log import _get_hmac_key


def _key_canonical(entry: "KeyEntry", previous_hash: str) -> bytes:
    """密钥条目的规范化字节串（compute_hash 与 verify_integrity 共用保持一致）。

    新版(hash_algo=hmac-sha256)引入 cipher_key 参与哈希，可检出密文被篡改；
    旧版(legacy)保持旧结构（无 cipher_key）以向后兼容。
    """
    d = {
        "key_id": entry.key_id,
        "scope": entry.scope,
        "created_at": entry.created_at,
        "expires_at": entry.expires_at,
        "revoked": entry.revoked,
        "previous_hash": previous_hash
    }
    if not entry.legacy:
        d["cipher_key"] = entry.cipher_key
        d["metadata"] = entry.metadata
    return json.dumps(d, ensure_ascii=False, sort_keys=True).encode("utf-8")


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
        self.pending_migration = False        # 旧明文格式待下次保存自动加密
        self.load_error = False               # 解密失败标记（跳过加载）
        self.legacy = False                   # 旧版无 HMAC 标记（按旧 sha256 校验）

    def compute_hash(self, previous_hash: str = "") -> str:
        canonical = _key_canonical(self, previous_hash)
        key = _get_hmac_key()
        if key is None:
            # 密钥缺失：无法计算 HMAC，条目置空哈希（校验将失败），显式告警而非静默
            print("[KeyManager] 审计 HMAC 密钥缺失，无法计算密钥链哈希，条目将无法通过校验")
            self.entry_hash = ""
            return self.entry_hash
        self.entry_hash = hmac.new(key, canonical, hashlib.sha256).hexdigest()
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
        # 密钥加密：用 SecretManager 的 Fernet 把明文密钥加密后落盘，禁止明文写入
        error = False
        try:
            encrypted_key = SecretManager.get_instance().encrypt(self.cipher_key)
        except RuntimeError as e:
            error = True
            encrypted_key = ""
            # 加密失败：不得回退写明文，标记为异常并记录告警，让落盘失败可见
            print(f"[KeyManager] cipher_key 加密失败，已中止密文落盘：{e}")
            try:
                from .audit_log import AuditLog, AuditEventType, AuditSeverity
                AuditLog().add_entry(
                    AuditEventType.ACCESS_CONTROL, AuditSeverity.SECURITY,
                    f"KeyManager cipher_key 加密失败，禁止明文落盘待迁移：{e}",
                    {"key_id": self.key_id}
                )
            except Exception:
                pass
        return {
            "key_id": self.key_id,
            "scope": self.scope,
            "cipher_key": encrypted_key,
            "cipher_key_encrypted": not error,
            "cipher_key_error": error,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "metadata": self.metadata,
            "revoked": self.revoked,
            "entry_hash": self.entry_hash,
            "hash_algo": "hmac-sha256"
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
        # 旧版(无 hash_algo=hmac-sha256)标记 legacy，按旧 sha256 校验（向后兼容）
        entry.legacy = data.get("hash_algo") != "hmac-sha256"

        stored_cipher = entry.cipher_key
        if data.get("cipher_key_encrypted", False):
            # 密文格式：还原明文；解密失败（密钥轮换/损坏）→ 标记无效并跳过
            try:
                entry.cipher_key = SecretManager.get_instance().decrypt(stored_cipher)
            except RuntimeError as e:
                entry.load_error = True
                entry.cipher_key = ""
                print(f"[KeyManager] 密钥条目 {entry.key_id} 解密失败，跳过加载：{e}")
                try:
                    from .audit_log import AuditLog, AuditEventType, AuditSeverity
                    AuditLog().add_entry(
                        AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                        f"KeyManager 密钥条目解密失败，已跳过加载：{e}",
                        {"key_id": entry.key_id}
                    )
                except Exception:
                    pass
        else:
            # 旧明文格式（无加密标记）：向后兼容直接读取明文，标记待迁移
            entry.pending_migration = True
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
        # 并发安全锁（RLock：generate/revoke 会在持锁状态下调用 _save）
        self._lock = threading.RLock()
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
                        if entry.load_error:
                            # 解密失败/被篡改条目：保留在链中，由 verify_integrity 检出
                            # 并报告具体 key_id（不静默跳过）
                            self._entries.append(entry)
                            old_hash = entry.entry_hash
                            continue
                        # 使用存储的 entry_hash 直接链式，不在加载时重算——否则会用篡改后的
                        # 数据自证一致而掩盖篡改（tamper 与 load 双重安全的根因）
                        self._entries.append(entry)
                        old_hash = entry.entry_hash
            except Exception as e:
                print(f"[KeyManager] 密钥存储加载失败：{e}")

    def _save(self):
        key_file = self._storage_path / "key_store.json"
        tmp_file = key_file.with_suffix(".json.tmp")
        try:
            with self._lock:
                # 写临时文件 + 原子替换，避免写一半崩溃留下损坏文件
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump([e.to_dict() for e in self._entries],
                              f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, key_file)
        except Exception as e:
            print(f"[KeyManager] 密钥存储保存失败：{e}")

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

        with self._lock:
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
        with self._lock:
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
        """验证密钥链完整性（HMAC 哈希防篡改；解密失败条目直接判失效）"""
        key = _get_hmac_key()
        # 先检出解密失败/被篡改条目（不静默跳过），报告具体 key_id
        for entry in self._entries:
            if entry.load_error:
                print(f"[KeyManager] 密钥条目 {entry.key_id} 解密失败/被篡改，完整性校验失败")
                return False
        if key is None:
            print("[KeyManager] 审计链无法校验（密钥缺失）")
            return False
        prev_hash = ""
        warned_legacy = False
        for entry in self._entries:
            canonical = _key_canonical(entry, prev_hash)
            if entry.legacy:
                # 旧版无 HMAC 条目：按旧 sha256 校验，并提示已降级
                if not warned_legacy:
                    print("[KeyManager] 发现旧版(无HMAC)密钥条目，按旧 sha256 校验（已降级）")
                    warned_legacy = True
                expected = hashlib.sha256(canonical).hexdigest()
            else:
                expected = hmac.new(key, canonical, hashlib.sha256).hexdigest()
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
