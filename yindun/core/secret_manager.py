# -*- coding: utf-8 -*-
"""
隐盾本地密钥管理器

用途：
- 使用 cryptography 库的 Fernet 对称加密（AES-128-CBC + HMAC-SHA256）保护本地敏感数据
- 首次运行时自动生成密钥并持久化到 yindun/cache/.secret_key
- 后续启动从文件加载密钥，不重复生成
- 密钥文件缺失或损坏时自动重新生成并记录日志
- 通过单例模式 SecretManager.get_instance() 获取实例
"""

import os
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


class SecretManager:
    """本地密钥管理器（单例）

    使用 Fernet 对称加密算法（AES-128-CBC + HMAC-SHA256）保护本地敏感数据。
    密钥持久化于 yindun/cache/.secret_key，权限设置为仅当前用户可读。

    使用方式：
        sm = SecretManager.get_instance()
        cipher = sm.encrypt("13812345678")   # 返回 base64 字符串
        plain  = sm.decrypt(cipher)            # 还原明文
    """

    # 单例实例
    _instance: Optional["SecretManager"] = None

    # 密钥文件路径：相对于本文件定位到 yindun/cache/.secret_key
    _KEY_FILE: Path = Path(__file__).resolve().parent.parent / "cache" / ".secret_key"

    # ── 单例入口 ───────────────────────────────────────────
    @classmethod
    def get_instance(cls) -> "SecretManager":
        """获取 SecretManager 单例实例（首次调用时完成密钥加载）"""
        if cls._instance is None:
            cls()  # 触发 __new__ + __init__ 完成初始化
        return cls._instance  # type: ignore[return-value]

    # ── 构造（单例保护：重复 __init__ 不重复初始化）────────
    def __new__(cls, *args, **kwargs) -> "SecretManager":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._initialized = False  # 标记尚未初始化
            cls._instance = instance
        return cls._instance  # type: ignore[return-value]

    def __init__(self) -> None:
        # 单例保护：已初始化则直接返回
        if getattr(self, "_initialized", False):
            return
        self._fernet: Fernet = self._load_or_create_key()
        self._initialized = True

    # ── 密钥加载 / 生成 ────────────────────────────────────
    def _load_or_create_key(self) -> Fernet:
        """从密钥文件加载密钥；文件不存在或损坏时重新生成"""
        # 尝试从文件加载
        fernet = self._try_load_key()
        if fernet is not None:
            print(f"[SecretManager] 密钥已从文件加载：{self._KEY_FILE}")
            return fernet

        # 加载失败：重新生成
        print("[SecretManager] 密钥文件不存在或已损坏，正在重新生成...")
        return self._generate_and_save_key()

    def _try_load_key(self) -> Optional[Fernet]:
        """尝试从文件读取并校验密钥；失败返回 None"""
        if not self._KEY_FILE.exists():
            return None
        try:
            key = self._KEY_FILE.read_bytes()
            # Fernet 构造函数会对 key 做 base64url + 32 字节长度校验
            return Fernet(key)
        except Exception as e:
            print(f"[SecretManager] 读取密钥文件失败，将重新生成：{e}")
            return None

    def _generate_and_save_key(self) -> Fernet:
        """生成新密钥，写入文件并设置权限"""
        # Fernet.generate_key() 返回 32 字节 base64url 编码的密钥
        key = Fernet.generate_key()

        # 确保父目录存在
        self._KEY_FILE.parent.mkdir(parents=True, exist_ok=True)

        # 写入密钥文件
        self._KEY_FILE.write_bytes(key)

        # 设置权限：仅当前用户可读可写（0o600 语义）
        # 注意：Windows 下 os.chmod 对权限的支持有限，
        # 0o600 在 POSIX 上表示仅属主可读写；Windows 上等效于移除继承权限
        try:
            os.chmod(self._KEY_FILE, 0o600)
        except OSError as e:
            print(f"[SecretManager] 设置密钥文件权限失败（不影响功能）：{e}")

        print(f"[SecretManager] 新密钥已生成并保存到：{self._KEY_FILE}")
        return Fernet(key)

    # ── 对外接口：加密 ─────────────────────────────────────
    def encrypt(self, plaintext: str) -> str:
        """加密明文，返回 base64 编码的密文

        Args:
            plaintext: 待加密的明文字符串

        Returns:
            base64url 编码的密文字符串（Fernet token）

        Raises:
            TypeError:  入参不是 str
            ValueError: 明文为空字符串
            RuntimeError: 加密过程发生异常
        """
        if not isinstance(plaintext, str):
            raise TypeError(f"encrypt 入参必须是 str，收到 {type(plaintext).__name__}")
        if not plaintext:
            raise ValueError("encrypt 入参不能为空字符串")

        try:
            # Fernet.encrypt 返回的 bytes 本身就是 base64url 编码的 ASCII 字符串
            token = self._fernet.encrypt(plaintext.encode("utf-8"))
            return token.decode("ascii")
        except Exception as e:
            raise RuntimeError(f"加密失败：{e}") from e

    # ── 对外接口：解密 ─────────────────────────────────────
    def decrypt(self, ciphertext: str) -> str:
        """解密 base64 密文，返回明文字符串

        Args:
            ciphertext: encrypt() 返回的 base64 密文

        Returns:
            还原后的明文字符串

        Raises:
            TypeError:  入参不是 str
            ValueError: 密文为空字符串
            RuntimeError: 密钥不匹配或密文被篡改
        """
        if not isinstance(ciphertext, str):
            raise TypeError(f"decrypt 入参必须是 str，收到 {type(ciphertext).__name__}")
        if not ciphertext:
            raise ValueError("decrypt 入参不能为空字符串")

        try:
            token = ciphertext.encode("ascii")
            plain_bytes = self._fernet.decrypt(token)
            return plain_bytes.decode("utf-8")
        except InvalidToken as e:
            # 密钥不匹配或密文被篡改
            raise RuntimeError("解密失败：密钥不匹配或密文已损坏") from e
        except Exception as e:
            raise RuntimeError(f"解密失败：{e}") from e


# ── 自测入口 ───────────────────────────────────────────────
if __name__ == "__main__":
    sm = SecretManager.get_instance()
    test_plain = "13812345678"

    print(f"明文  : {test_plain}")
    cipher = sm.encrypt(test_plain)
    print(f"密文  : {cipher}")
    plain = sm.decrypt(cipher)
    print(f"还原  : {plain}")

    assert plain == test_plain, "加密-解密往返不一致！"
    print("✓ 加密-解密往返验证通过")
