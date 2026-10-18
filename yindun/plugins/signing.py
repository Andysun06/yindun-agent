# -*- coding: utf-8 -*-
"""隐盾 · 插件来源校验（V3.6.2，路线图 C-15）

**为什么需要它**：插件的安全边界是"只做加法"（不得参与脱敏豁免 / 白名单 / 审批放行 / 审计写入，
见 `host.py` 的 `FORBIDDEN_HOOKS`）。但**"从哪来的插件"本身没有校验**——只要把目录丢进
`plugins_data/`，内核就会去加载它。要让"部门内共享插件包"这件事成立，必须先有来源校验：
谁签的、改动过没有。否则"只做加法"的边界等于给任意第三方代码开了一道侧门。

设计（保守，默认不改变现有行为）：
  · **签名可选**：清单里带 `signature` 字段就校验；不带就标为"未签名"并如实展示。
  · **可信公钥白名单**：`plugins_data/trusted_keys/*.pub`（PEM）。只有白名单里的公钥验过的插件
    才叫"已签名（可信）"；签了但公钥不在白名单 → "签名不受信"（单独一档，不冒充可信）。
  · **强制模式**：设置项 `require_signed_plugins`（或环境变量 `YINDUN_REQUIRE_SIGNED_PLUGINS=1`）
    打开后，**用户目录**的插件必须"已签名且可信"才会被加载；内核自带插件（builtin）随产品发布，
    视为可信，不受此限。
  · **签名覆盖整份清单**：签名对象是"去掉 signature 字段后的规范化 JSON"，任何一个字段被改动
    都会导致校验失败（改 `hooks`、改 `file_exts` 都逃不掉）。

用法（生成密钥对 → 签名 → 放进白名单）：

    python -m yindun.plugins.signing gen-key plugins_data/trusted_keys/dept-a
    python -m yindun.plugins.signing sign --key dept-a.key --manifest <插件目录>/manifest.json

本模块与界面无关；只用标准库 + `cryptography`（已是既有依赖，Fernet 同源）。
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SIGNATURE_FIELD = "signature"
TRUSTED_KEYS_DIRNAME = "trusted_keys"


def canonical_payload(manifest: Dict[str, Any]) -> bytes:
    """签名/校验的对象：去掉 signature 字段后的**规范化** JSON（键排序、无多余空白）。

    规范化保证了"签名与字段顺序无关，但与任一字段的取值绑定"——改动任何字段都会验签失败。
    """
    payload = {k: v for k, v in dict(manifest or {}).items() if k != SIGNATURE_FIELD}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _ed25519():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    return ed25519


def generate_keypair(out_dir: Optional[Path] = None, name: str = "dept") -> Tuple[str, str]:
    """生成一对 Ed25519 密钥，返回 (私钥 PEM, 公钥 PEM)。私钥由调用方自行保管。"""
    from cryptography.hazmat.primitives import serialization
    ed25519 = _ed25519()
    private = ed25519.Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()).decode("utf-8")
    public_pem = private.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo).decode("utf-8")
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        # 落点受控：调用方指定的密钥目录（默认 plugins_data/trusted_keys），文件名固定
        (out_dir / f"{name}.key").write_text(private_pem, encoding="utf-8")
        (out_dir / f"{name}.pub").write_text(public_pem, encoding="utf-8")
    return private_pem, public_pem


def sign_manifest(manifest: Dict[str, Any], private_key_pem: str) -> str:
    """对清单签名，返回 base64 字符串（调用方写进 manifest 的 `signature` 字段）。"""
    from cryptography.hazmat.primitives import serialization
    ed25519 = _ed25519()
    key = serialization.load_pem_private_key(str(private_key_pem).encode("utf-8"), password=None)
    signature = key.sign(canonical_payload(manifest))
    return base64.b64encode(signature).decode("ascii")


def verify_signature(manifest: Dict[str, Any], public_key_pem: str) -> bool:
    """用给定公钥校验清单签名；任何异常都判为"验签失败"（fail-closed）。"""
    signature_b64 = str((manifest or {}).get(SIGNATURE_FIELD) or "").strip()
    if not signature_b64:
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        ed25519 = _ed25519()
        key = serialization.load_pem_public_key(str(public_key_pem).encode("utf-8"))
        key.verify(base64.b64decode(signature_b64), canonical_payload(manifest))
        return True
    except Exception:
        return False


def trusted_keys(data_root: Optional[Path]) -> List[Tuple[str, str]]:
    """可信公钥白名单：`<插件数据目录>/trusted_keys/*.pub` → [(文件名, PEM 内容)]。"""
    if not data_root:
        return []
    keys_dir = Path(data_root) / TRUSTED_KEYS_DIRNAME
    if not keys_dir.is_dir():
        return []
    out: List[Tuple[str, str]] = []
    for item in sorted(keys_dir.glob("*.pub")):
        try:
            out.append((item.stem, item.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def evaluate_manifest(manifest: Dict[str, Any], data_root: Optional[Path],
                      source: str = "") -> Dict[str, Any]:
    """判定一份清单的来源状态。

    返回 `{"status": verified|untrusted|unsigned, "signer": 公钥名, "detail": 人话说明}`：
      · builtin（内核自带）：直接 `verified`，signer 标注"内核"，不受白名单约束；
      · 带 signature 且命中白名单 → verified（signer = 公钥文件名）；
      · 带 signature 但白名单里没有 → untrusted（**不冒充可信**）；
      · 没带 signature → unsigned。
    """
    if source == "builtin":
        return {"status": "verified", "signer": "内核", "detail": "内核自带插件（随产品发布）"}
    has_signature = bool(str((manifest or {}).get(SIGNATURE_FIELD) or "").strip())
    if not has_signature:
        return {"status": "unsigned", "signer": "",
                "detail": "未签名（来源校验关闭时可用；开启强制签名后会被拒绝）"}
    for name, pem in trusted_keys(data_root):
        if verify_signature(manifest, pem):
            return {"status": "verified", "signer": name, "detail": f"已签名且可信（签名者：{name}）"}
    return {"status": "untrusted", "signer": "",
            "detail": "签名不受信：清单带签名，但没有任何一份可信公钥能验过它"}


def require_signed_plugins(settings=None) -> bool:
    """是否要求用户插件必须"已签名且可信"（默认关闭：不改变既有行为）。

    打开方式（任选）：环境变量 `YINDUN_REQUIRE_SIGNED_PLUGINS=1`，或设置项
    `require_signed_plugins=true`（桌面版设置页写入，运维可统一下发）。
    """
    env = os.environ.get("YINDUN_REQUIRE_SIGNED_PLUGINS", "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    try:
        if settings is not None and bool(settings.get("require_signed_plugins")):
            return True
    except Exception:
        pass
    return False


def _cli(argv: List[str]) -> int:
    """极简命令行：gen-key / sign / verify（给"部门内共享插件包"的流程用）。"""
    if not argv:
        print(__doc__)
        return 0
    action = argv[0]
    args = dict(zip(argv[1::2], argv[2::2]))
    if action == "gen-key":
        out = Path(args.get("--out", "plugins_data"))
        name = args.get("--name", "dept")
        private_pem, _ = generate_keypair(out / TRUSTED_KEYS_DIRNAME, name)
        print(f"✅ 已生成密钥对：{out / TRUSTED_KEYS_DIRNAME / (name + '.key')}（私钥，自行保管）"
              f" 与 {name}.pub（公钥，放进白名单）")
        print(f"   私钥长度 {len(private_pem)} 字符；**私钥不要入库**（.gitignore 已忽略 plugins_data/）")
        return 0
    if action == "sign":
        key_path = Path(args.get("--key", ""))
        manifest_path = Path(args.get("--manifest", "manifest.json"))
        if not key_path.is_file() or not manifest_path.is_file():
            print("用法：sign --key <私钥.pem> --manifest <manifest.json>")
            return 2
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest[SIGNATURE_FIELD] = sign_manifest(manifest, key_path.read_text(encoding="utf-8"))
        # 落点受控：就地更新调用方指定的插件清单（流程由使用者发起）
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        print(f"✅ 已签名并写回：{manifest_path}")
        return 0
    if action == "verify":
        manifest_path = Path(args.get("--manifest", "manifest.json"))
        data_root = Path(args.get("--data", "plugins_data"))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        result = evaluate_manifest(manifest, data_root, source="user")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "verified" else 1
    print(f"未知命令：{action}")
    return 2


if __name__ == "__main__":
    import sys
    sys.exit(_cli(sys.argv[1:]))
