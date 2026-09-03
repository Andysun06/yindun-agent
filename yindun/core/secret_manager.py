# -*- coding: utf-8 -*-
"""
隐盾本地密钥管理器

用途：
- 使用 cryptography 库的 Fernet 对称加密（AES-128-CBC + HMAC-SHA256）保护本地敏感数据
- 首次运行时自动生成密钥并持久化，后续启动从文件加载，不重复生成
- 通过单例模式 SecretManager.get_instance() 获取实例

【第 5 步：密钥安全加固】
1. 密钥路径移出项目目录：
   - Windows → %APPDATA%\\Yindun\\.secret_key
   - 其他     → ~/.config/yindun/.secret_key
   - 旧位置（yindun/cache/.secret_key）若存在则自动迁移（复制到新路径，保留旧文件不删）
2. 文件权限收紧：
   - POSIX：os.chmod(0o600)
   - Windows：调用系统 icacls → /inheritance:r /grant:r "%USERNAME%":F
3. DPAPI 包裹（Windows 默认开启，SECRET_DPAPI 开关可关闭）：
   - 用系统 crypt32.dll (CryptProtectData / CryptUnprotectData) 包裹密钥文件内容
   - 非 Windows 或 DPAPI 失败自动回退为"明文密钥 + 权限收紧"
   - 注意：DPAPI 绑定当前 Windows 用户账户，换用户 / 重装系统后密钥不可恢复，
     历史会话中已脱敏占位符将无法还原为明文（安全优先于可恢复性的既定取舍）
"""

import os
import sys
import ctypes
import subprocess
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken


def _default_key_dir() -> Path:
    """返回平台对应的密钥目录：
    Windows → %APPDATA%\\Yindun
    其他     → ~/.config/yindun
    """
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "Yindun"
    return Path.home() / ".config" / "yindun"


class SecretManager:
    """本地密钥管理器（单例）

    使用 Fernet 对称加密算法（AES-128-CBC + HMAC-SHA256）保护本地敏感数据。
    密钥持久化位置随平台（见 _default_key_dir），Windows 下由 DPAPI 包裹。

    使用方式：
        sm = SecretManager.get_instance()
        cipher = sm.encrypt("13812345678")   # 返回 base64 字符串
        plain  = sm.decrypt(cipher)            # 还原明文
    """

    # 单例实例
    _instance: Optional["SecretManager"] = None

    # 第 5 步：新密钥路径（平台相关，移出项目目录）
    _KEY_FILE: Path = _default_key_dir() / ".secret_key"
    # 旧密钥路径（第 4 步及之前）：项目目录内 yindun/cache/.secret_key，迁移后保留不删
    _LEGACY_KEY_FILE: Path = Path(__file__).resolve().parent.parent / "cache" / ".secret_key"

    # DPAPI 包裹开关（Windows 默认开启；关闭则退回明文密钥 + 权限收紧）
    SECRET_DPAPI = True

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

    # ── DPAPI 包裹（Windows 系统自带 crypt32.dll）─────────
    @classmethod
    def _dpapi_supported(cls) -> bool:
        """是否启用 DPAPI：开关开启且为 Windows 平台"""
        return bool(cls.SECRET_DPAPI and sys.platform == "win32")

    @staticmethod
    def _dpapi_protect(data: bytes) -> bytes:
        """CryptProtectData 包裹字节串，返回不可迁移的 blob"""
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        buf = ctypes.create_string_buffer(data)  # 长度 = len(data)+1，cbData 用精确长度
        blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        blob_out = DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    @staticmethod
    def _dpapi_unprotect(blob: bytes) -> bytes:
        """CryptUnprotectData 解包 DPAPI blob（换用户/重装系统后会失败）"""
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

        buf = ctypes.create_string_buffer(blob)
        blob_in = DATA_BLOB(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))
        blob_out = DATA_BLOB()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out))
        if not ok:
            raise ctypes.WinError()
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)

    # ── 密钥落盘体的包裹 / 解包 ───────────────────────────
    def _dump_key_body(self, raw: bytes) -> bytes:
        """把 Fernet 原始密钥转为落盘字节：Windows+DPAPI 时包裹，否则明文"""
        if self._dpapi_supported():
            try:
                return self._dpapi_protect(raw)
            except Exception as e:
                print(f"[SecretManager] DPAPI 包裹失败，回退明文存储：{e}")
        return raw

    def _load_key_body(self, body: bytes) -> bytes:
        """从落盘字节还原 Fernet 原始密钥：优先尝试 DPAPI 解包"""
        if self._dpapi_supported():
            try:
                return self._dpapi_unprotect(body)
            except Exception as e:
                # 可能是旧版明文密钥文件，或换用户/重装后 DPAPI 不可解
                print(f"[SecretManager] DPAPI 解包失败（或为旧明文文件），回退明文读取：{e}")
        return body

    # ── 权限收紧 ──────────────────────────────────────────
    @classmethod
    def _harden_permissions(cls, key_file: Path) -> None:
        """收紧密钥文件权限：POSIX chmod 0o600；Windows 用 icacls 显式收紧"""
        if sys.platform == "win32":
            try:
                user = os.environ.get("USERNAME") or os.getlogin() or ""
                subprocess.run(
                    ["icacls", str(key_file), "/inheritance:r", "/grant:r", f"{user}:F"],
                    check=True, capture_output=True)
                print(f"[SecretManager] 密钥文件权限已收紧（icacls，仅 {user}）")
            except Exception as e:
                print(f"[SecretManager] icacls 收紧权限失败（不影响功能）：{e}")
        else:
            try:
                os.chmod(key_file, 0o600)
            except OSError as e:
                print(f"[SecretManager] 设置密钥文件权限失败（不影响功能）：{e}")

    # ── 密钥加载 / 迁移 / 生成 ────────────────────────────
    def _load_or_create_key(self) -> Fernet:
        """按优先级加载密钥：新位置 → 旧位置迁移 → 重新生成"""
        if self._KEY_FILE.exists():
            fernet = self._try_load_new_key()
            if fernet is not None:
                return fernet
        if self._LEGACY_KEY_FILE.exists():
            fernet = self._migrate_legacy_key()
            if fernet is not None:
                return fernet
        print("[SecretManager] 密钥文件不存在或已损坏，正在重新生成...")
        return self._generate_and_save_key()

    def _try_load_new_key(self) -> Optional[Fernet]:
        """从新位置读取并校验密钥；失败返回 None"""
        try:
            body = self._KEY_FILE.read_bytes()
            raw = self._load_key_body(body)
            return Fernet(raw)
        except Exception as e:
            print(f"[SecretManager] 读取新密钥文件失败：{e}")
            return None

    def _migrate_legacy_key(self) -> Optional[Fernet]:
        """把旧位置（纯文本密钥）迁移到新位置；保留旧文件不删"""
        try:
            raw = self._LEGACY_KEY_FILE.read_bytes()
            fernet = Fernet(raw)  # 旧文件为 Fernet 原始密钥
            self._write_key(raw)
            print(f"[SecretManager] 已从旧位置迁移密钥：{self._LEGACY_KEY_FILE}")
            print(f"[SecretManager]   → 新位置：{self._KEY_FILE}")
            print(f"[SecretManager]   旧文件已保留（可手动删除）：{self._LEGACY_KEY_FILE}")
            return fernet
        except Exception as e:
            print(f"[SecretManager] 迁移旧密钥失败：{e}")
            return None

    def _generate_and_save_key(self) -> Fernet:
        """生成新密钥，写入文件并设置权限"""
        key = Fernet.generate_key()
        self._write_key(key)
        print(f"[SecretManager] 新密钥已生成并保存到：{self._KEY_FILE}")
        return Fernet(key)

    def _write_key(self, raw: bytes) -> None:
        """写密钥到新位置（含 DPAPI 包裹 + 权限收紧）"""
        self._KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        body = self._dump_key_body(raw)
        self._KEY_FILE.write_bytes(body)
        self._harden_permissions(self._KEY_FILE)

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