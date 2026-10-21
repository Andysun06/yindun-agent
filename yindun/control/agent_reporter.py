# -*- coding: utf-8 -*-
"""隐盾控制面 · 端点侧上报器（agent side）。

═══ 这个模块会让数据离开本机，所以设计上格外保守 ═══

1. **默认关闭**：`control.json` 里 `enabled=false` 是初始状态。本产品的立身之本是
   "数据不出网"，集中管理是**显式选用**的能力，绝不做静默上报（那等于欺骗用户）。
2. **只发掩码与元数据**：审计日志在落盘前已统一掩码；上报前**再过一遍脱敏管线**
   （纵深防御——即使某天审计层出了漏洞，出网面仍然是干净的）。
3. **本机留痕**：每次上报成功/失败都写入本地审计（SYSTEM 级），
   运维能回答"这台机器往哪里发过什么"。
4. **可审计的进度**：只发服务端还没收到过的条目（按序号），失败不推进进度，
   不丢条目、不重复计入。
5. **策略只能收紧**：从控制面取回的策略包先验签、再走 `policy.merge_tighten`，
   合并结果永远不比本地更宽松（服务端被攻破也放行不了东西）。

配置（`config/control.json`，令牌与签名密钥加密落盘）::

    {"enabled": true, "url": "http://192.168.1.10:8787",
     "endpoint_id": "ep-fuyou-27", "token_enc": "...", "policy_key_enc": "...",
     "last_sent_count": 0, "applied_policy": {...}}
"""
from __future__ import annotations

import json
import platform
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from yindun.control import SCHEMA_VERSION
from yindun.control import policy as policy_mod
from yindun.core.audit_log import AuditEventType, AuditLog, AuditSeverity
from yindun.core.privacy_engine import PrivacyEngine
from yindun.core.secret_manager import SecretManager

TIMEOUT = 15.0
PROBE_WINDOW = 3        # 主动上报窗口：24h 内的条目优先（历史全量在首次上报时补齐）


@dataclass
class ReportResult:
    ok: bool
    events: int = 0
    alerts: List[Dict[str, Any]] = None          # type: ignore[assignment]
    policy_version: int = 0
    error: str = ""
    dry_run: bool = False

    def __post_init__(self) -> None:
        if self.alerts is None:
            self.alerts = []

    def summary(self) -> str:
        if self.error:
            return f"上报失败：{self.error}"
        prefix = "试运行（未发送）" if self.dry_run else "已上报"
        return f"{prefix}：{self.events} 条事件，控制面回告 {len(self.alerts)} 项"


def default_config_path() -> Path:
    try:
        from yindun import APP_ROOT
        return Path(APP_ROOT) / "config" / "control.json"
    except Exception:
        return Path(__file__).resolve().parents[2] / "config" / "control.json"


class ControlReporter:
    """端点侧：采集 → 脱敏 → 上报 → 取策略。"""

    def __init__(self, config_path: Optional[Path] = None, audit: Optional[AuditLog] = None,
                 settings: Optional[Dict[str, Any]] = None):
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.audit = audit or AuditLog()
        self.settings = settings or {}
        self._engine = PrivacyEngine()
        self._config: Dict[str, Any] = self._load_config()

    # ── 配置读写（令牌与签名密钥加密落盘）────────────────────
    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            return {"enabled": False, "url": "", "endpoint_id": "", "last_sent_count": 0}
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except Exception:
            return {"enabled": False, "url": "", "endpoint_id": "", "last_sent_count": 0,
                    "_load_error": "配置文件不是合法 JSON"}
        secret = SecretManager.get_instance()
        for key in ("token_enc", "policy_key_enc"):
            field = key[:-4]
            blob = raw.pop(key, None)
            if blob:
                try:
                    raw[field] = secret.decrypt(blob)
                except Exception:
                    raw[field] = ""
                    raw["_load_error"] = f"{field} 解密失败（密钥失效？）"
        return raw

    def save_config(self, config: Dict[str, Any]) -> None:
        secret = SecretManager.get_instance()
        payload = dict(config)
        for field in ("token", "policy_key"):
            value = payload.pop(field, None)
            if value:
                payload[field + "_enc"] = secret.encrypt(value)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._config = self._load_config()

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("enabled")) and bool(self._config.get("url")) \
            and bool(self._config.get("endpoint_id"))

    def configure(self, url: str, endpoint_id: str, token: str, policy_key: str,
                  enabled: bool = True) -> None:
        cfg = dict(self._config)
        cfg.update({"url": url.rstrip("/"), "endpoint_id": endpoint_id,
                    "token": token, "policy_key": policy_key, "enabled": enabled})
        self.save_config(cfg)

    # ── 配对码登记（企业真实流程：IT 发码 → 员工机自助接入）──────
    def enroll_with_code(self, url: str, code: str, name: str, endpoint_id: str) -> Dict[str, Any]:
        """用一次性配对码在控制面登记本机，返回登记结果（并**不**自动启用上报）。

        · 配对码由管理端在控制台生成、有有效期、用后作废——员工不需要拿到管理令牌；
        · 登记成功后令牌与策略签名密钥**加密落盘**（复用 SecretManager）；
        · 是否启用上报由调用方（设置页的开关）决定，绝不静默开启。
        """
        endpoint_id = (endpoint_id or "").strip() or f"ep-{secrets.token_hex(4)}"
        body = {"code": (code or "").strip(), "name": (name or "").strip() or "未命名端点",
                "endpoint_id": endpoint_id}
        import json as _json
        request = urllib.request.Request(
            url.rstrip("/") + "/api/v1/enroll", method="POST",
            data=_json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8",
                     "User-Agent": "YindunAgent/" + self._version()})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=TIMEOUT) as response:
                data = _json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = _json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            return {"ok": False, "error": f"HTTP {exc.code}：{detail or '登记被拒'}"}
        except Exception as exc:
            return {"ok": False, "error": f"连不上控制面：{type(exc).__name__} {exc}"}

        if not data.get("token"):
            return {"ok": False, "error": "控制面未返回令牌"}
        cfg = dict(self._config)
        cfg.update({"url": url.rstrip("/"), "endpoint_id": data.get("endpoint_id", endpoint_id),
                    "token": data["token"], "policy_key": data.get("policy_key", ""),
                    "enabled": False})            # 登记≠启用：开关由用户在设置页显式打开
        self.save_config(cfg)
        self._trace(True, f"已用配对码登记为 {cfg['endpoint_id']}（尚未启用上报）")
        return {"ok": True, "endpoint_id": cfg["endpoint_id"],
                "note": "登记成功；在设置页打开「上报到控制面」后开始同步"}

    def disable(self) -> None:
        """停用上报（保留配置与已下发的策略规则，便于显式撤销）。"""
        cfg = dict(self._config)
        cfg["enabled"] = False
        self.save_config(cfg)

    def status(self) -> Dict[str, Any]:
        """本机控制面接入状态（给设置页显示，不泄露令牌）。"""
        applied = self._config.get("applied_policy") or {}
        sent = int(self._config.get("last_sent_count", 0) or 0)
        try:
            total = len(self.audit.get_entries())
        except Exception:
            total = 0
        return {
            "configured": bool(self._config.get("url") and self._config.get("endpoint_id")),
            "enabled": bool(self._config.get("enabled")),
            "url": self._config.get("url", ""),
            "endpoint_id": self._config.get("endpoint_id", ""),
            "last_sent_count": sent,
            # 报告 P0-01：界面要能回答"还有多少没同步"，而不是笼统写"上报中"
            "pending_events": max(0, total - sent),
            "total_events": total,
            "last_report_at": self._config.get("last_report_at", ""),
            "last_error": self._config.get("last_error", ""),
            "applied_policy_version": int(applied.get("version", 0) or 0),
            "applied_policy": applied,
        }

    def _remember(self, **fields: Any) -> None:
        cfg = dict(self._config)
        cfg.update(fields)
        self.save_config(cfg)

    # ── 采集 ──────────────────────────────────────────────
    def _chain_state(self) -> Dict[str, Any]:
        entries = self.audit.get_entries()
        detail = self.audit.verify_chain_detail(allow_missing_anchor=True)
        head = entries[-1].entry_hash if entries else ""
        anchor_state = {"verified": "verified", "absent": "absent",
                        "tampered": "tampered"}.get(str(detail.get("anchor")), "unknown")
        return {"total_count": len(entries), "head_hash": head,
                "legacy_prefix": int(detail.get("legacy_prefix") or 0),
                "anchor_state": anchor_state, "entries": entries}

    def _capabilities(self) -> Dict[str, Any]:
        models: List[str] = []
        external: List[str] = []
        try:
            from yindun.app.llm_factory import detect_chat_models
            models = list(detect_chat_models())
        except Exception:
            models = []
        for name in (self.settings.get("custom_models") or {}):
            external.append(str(name))
            if str(name) not in models:
                models.append(str(name))
        strict = not bool(self.settings.get("allow_script_exec", True))
        return {"models": models[:64], "external_models": external[:64],
                "plugins": self._plugin_inventory()[:64],
                "strict_mode": strict,
                "policy_version": int(self._config.get("applied_policy", {}).get("version", 0) or 0),
                "platform": f"{platform.system()}-{platform.release()}"}

    def _plugin_inventory(self) -> List[Dict[str, Any]]:
        """插件资产清单：扫内置/用户两处 manifest，启用状态取自本地设置。

        直接读 manifest 而不是实例化 `PluginHost`——上报器不该为了"看一眼清单"
        就去装配一遍插件宿主（那会带来副作用：注册能力、写审计）。
        """
        enabled = self.settings.get("plugins_enabled") or {}
        roots = [Path(__file__).resolve().parents[1] / "plugins" / "builtin"]
        try:
            from yindun import APP_ROOT
            roots.append(Path(APP_ROOT) / "plugins")
        except Exception:
            pass
        items: Dict[str, Dict[str, Any]] = {}
        for root in roots:
            if not root.is_dir():
                continue
            for manifest in sorted(root.glob("*/manifest.json")):
                try:
                    data = json.loads(manifest.read_text(encoding="utf-8"))
                except Exception:
                    continue
                pid = str(data.get("id") or manifest.parent.name)
                items[pid] = {"id": pid, "version": str(data.get("version", "")),
                              "enabled": bool(enabled.get(pid, False))}
        return list(items.values())

    def _usage(self, state: Dict[str, Any]) -> List[Dict[str, Any]]:
        """端点级聚合用量（**不编造按模型的归因**，理由见 models.UsageRow 注释）。

        口径：`llm_output` 事件数 = 调用次数；LLM 事件的 `content_length` 之和 = 交互体量。
        """
        calls = sum(1 for e in state["entries"] if e.event_type == "llm_output")
        chars = sum(int((e.details or {}).get("content_length") or 0) for e in state["entries"]
                    if e.event_type in ("llm_input", "llm_output"))
        if calls == 0 and chars == 0:
            return []
        scope = "external" if str(self.settings.get("model", "")) in self._capabilities()["external_models"] \
            else "local"
        return [{"model": "(端点合计)", "scope": scope, "calls": calls, "chars": chars}]

    def _sanitize(self, text: Any) -> str:
        """出网前最后一道脱敏（纵深防御：审计层已掩码，这里不依赖它）。"""
        if text is None:
            return ""
        masked, _ = self._engine.anonymize(str(text))
        return masked

    def _sanitize_details(self, details: Dict[str, Any]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for key, value in (details or {}).items():
            if isinstance(value, (dict, list)):
                out[str(key)] = f"<{type(value).__name__} len={len(value)}>"
            else:
                out[str(key)] = self._sanitize(value)[:500]
        return out

    def build_report(self, batch: int = 200, dry_run: bool = False) -> Tuple[Dict[str, Any], ReportResult]:
        state = self._chain_state()
        entries = state["entries"]
        sent = int(self._config.get("last_sent_count", 0) or 0)
        pending = entries[sent:]
        batch_entries = pending[:max(1, min(int(batch), 500))]
        first_seq = sent + 1 if batch_entries else 0
        payload = {
            "schema": SCHEMA_VERSION,
            "endpoint_id": self._config.get("endpoint_id", ""),
            "reported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "version": self._version(),
            "platform": f"{platform.system()}-{platform.release()}",
            "chain": {"total_count": state["total_count"], "head_hash": state["head_hash"],
                      "legacy_prefix": state["legacy_prefix"], "anchor_state": state["anchor_state"],
                      "first_seq": first_seq},
            "events": [self._event_dict(e) for e in batch_entries],
            "capabilities": self._capabilities(),
            "usage": self._usage(state),
        }
        return payload, ReportResult(ok=True, events=len(batch_entries), dry_run=dry_run)

    def _event_dict(self, entry: Any) -> Dict[str, Any]:
        """把本地审计条目转成上报结构——所有文本字段都过一遍脱敏。"""
        return {
            "ts": _iso(entry.timestamp),
            "event_type": str(entry.event_type),
            "severity": str(entry.severity),
            "message": self._sanitize(entry.message)[:2000],
            "details": self._sanitize_details(entry.details),
            "previous_hash": entry.previous_hash or "",
            "entry_hash": entry.entry_hash or "",
            "hash_algo": "hmac-sha256" if not entry.legacy else "legacy",
            "legacy": bool(entry.legacy),
        }

    @staticmethod
    def _version() -> str:
        try:
            from yindun import __version__
            return str(__version__)
        except Exception:
            return ""

    # ── 上报与取策略 ───────────────────────────────────────
    def _post(self, path: str, body: Dict[str, Any], token: str) -> Tuple[int, Dict[str, Any]]:
        url = self._config.get("url", "").rstrip("/") + path
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
            "User-Agent": "YindunAgent/" + self._version(),
        })
        # 控制面通常是内网地址：显式禁掉系统代理，避免把内网请求交给外网代理
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=TIMEOUT) as response:
            return response.status, json.loads(response.read().decode("utf-8") or "{}")

    def send(self, batch: int = 200) -> ReportResult:
        """上报一批。失败不推进进度（下次重试同一条不会丢）。"""
        if not self.enabled:
            return ReportResult(ok=False, error="上报未启用（config/control.json 里 enabled=false）")
        payload, result = self.build_report(batch=batch)
        if not payload["events"] and not payload["chain"]["total_count"]:
            return ReportResult(ok=True, events=0)
        try:
            status, resp = self._post("/api/v1/report", payload, self._config.get("token", ""))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            self._trace(False, f"HTTP {exc.code} {detail}")
            self._remember(last_error=f"HTTP {exc.code}：{detail or '控制面拒绝'}")
            return ReportResult(ok=False, error=f"HTTP {exc.code}：{detail or '控制面拒绝'}")
        except Exception as exc:
            self._trace(False, f"{type(exc).__name__}: {exc}")
            self._remember(last_error=f"{type(exc).__name__}: {exc}")
            return ReportResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        result.ok = bool(resp.get("ok"))
        result.alerts = list(resp.get("alerts") or [])
        result.policy_version = int(resp.get("policy_version") or 0)
        if payload["events"]:
            self._remember(last_sent_count=int(resp.get("next_seq", 0)) - 1,
                           last_report_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                           last_error="")
        self._trace(True, f"上报 {len(payload['events'])} 条；控制面回告 {len(result.alerts)} 项")
        return result

    def fetch_policy(self) -> Tuple[Optional[Dict[str, Any]], str]:
        """取回策略包并**验签 + 只收紧合并**。返回 (合并后的本地策略, 说明)。"""
        if not self.enabled:
            return None, "未启用"
        try:
            url = (self._config.get("url", "").rstrip("/") + "/api/v1/policy?endpoint_id="
                   + urllib.parse.quote(str(self._config.get("endpoint_id", ""))))
            request = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {self._config.get('token','')}",
                "User-Agent": "YindunAgent/" + self._version()})
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=TIMEOUT) as response:
                bundle = json.loads(response.read().decode("utf-8") or "{}")
        except Exception as exc:
            self._trace(False, f"取策略失败：{type(exc).__name__}: {exc}")
            return None, f"取策略失败：{exc}"

        secret = str(self._config.get("policy_key", ""))
        ok, reason = policy_mod.verify_bundle(bundle, secret)
        if not ok:
            # 验签失败 / 含降级键 → 拒绝整包，保留原策略（失败关闭，绝不放行）
            self._trace(False, f"策略包被拒绝：{reason}")
            return None, f"策略包被拒绝：{reason}"
        local = self._config.get("applied_policy") or policy_mod.empty_local()
        merged = policy_mod.merge_tighten(local, bundle)
        if not policy_mod.is_tightening_only(local, merged):
            self._trace(False, "策略合并结果变松，已按内核兜底拒绝")
            return None, "策略合并结果变松，已拒绝"
        cfg = dict(self._config)
        cfg["applied_policy"] = merged
        self.save_config(cfg)
        self._trace(True, f"策略已更新到 v{merged.get('policy_version', 0)}")
        return merged, "ok"

    def _trace(self, ok: bool, message: str) -> None:
        """本机留痕：什么离开了这台机器，用户有权知道（写进本地审计链，可被外部锚定核对）。"""
        try:
            self.audit.add_entry(
                AuditEventType.CONTROL_REPORT,
                AuditSeverity.INFO if ok else AuditSeverity.WARNING,
                f"控制面{'上报' if ok else '上报异常'}：{message}",
                {"目标": str(self._config.get("url", ""))[:120], "成功": ok})
        except Exception as exc:
            print(f"[ControlReporter] 本地留痕失败（不影响主流程）：{exc}")


def _iso(ts: str) -> str:
    """本地审计时间戳（`datetime.isoformat()`）→ 控制面接受的规范 ISO-8601。

    ★ 这里曾经是**上报链路根本不工作**的根因（V3.6.0 冒烟实测）：
      审计条目存的是 `datetime.now().isoformat()`，带**微秒**且**不带时区**
      （`2026-10-16T13:00:24.889339`），而控制面的格式校验只认到秒
      （`_TS_RE` 没有小数位分支）→ 只要真上报一批带审计条目的数据就必然
      400「events[].ts 不是 ISO-8601 时间戳」。套件当时只验了 payload 的字段与脱敏，
      没把 payload 回灌给服务端校验器，所以一路绿灯。
    现在两头都收：本函数输出**带时区、毫秒精度**的规范形态（naive 时间按本机时区解释），
    服务端也接受 1~9 位小数（对其它发送端保持宽容）。
    """
    text = str(ts or "").strip()
    if text:
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if moment.tzinfo is None:
                moment = moment.astimezone()          # 本地审计存的是本机时间
            return moment.isoformat(timespec="milliseconds")
        except Exception:
            pass
    return datetime.now().astimezone().isoformat(timespec="milliseconds")
