# -*- coding: utf-8 -*-
"""隐盾控制面 · 端点失联告警路由（V3.6.2，路线图 C-14）

**要解决的问题**：端点掉线（拔网线、卸载、进程被杀）在控制台上只会表现为"数据停了"——
没人盯着看就发现不了。生产环境需要把它推到运维已有的通道里。

设计（安全优先，因为这是本项目**唯一**由控制面主动发起的外部请求）：
  · **地址只来自运维配置**（命令行 `--alert-webhook`），**绝不从请求体/上报数据里取** ——
    那是最典型的 SSRF 面；
  · 只允许 `http` / `https`，拒绝带认证信息的 URL（`user:pass@`）、拒绝重定向；
  · 超时 5 秒、失败只记日志（告警通道故障不能反过来拖垮控制面）；
  · **按端点去重**：同一端点在同一"静默窗口"内只发一次，避免刷屏；
  · 阈值可配（`--alert-silence-minutes`），默认 0 = 不判定失联（演示场景不打扰）。

Webhook 收到的是 JSON：{"event": "endpoint_silent", "endpoint_id", "name",
"last_seen_at", "silent_minutes", "threshold_minutes", "generated_at"}。
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

_config: Dict[str, Any] = {"url": "", "silence_minutes": 0, "store": None, "logger": None}
_sent: Dict[str, float] = {}          # endpoint_id -> 上次发送时间（去重）
_lock = threading.Lock()
TIMEOUT = 5.0


def configure(url: str, silence_minutes: int, store=None, logger=None) -> bool:
    """配置告警通道。返回是否真正启用（URL 不合法/阈值为 0 时不启用）。"""
    _config["store"] = store
    _config["logger"] = logger
    _config["silence_minutes"] = max(0, int(silence_minutes or 0))
    _config["url"] = str(url or "").strip()
    if not _config["url"] or not _config["silence_minutes"]:
        _config["url"] = "" if not _config["silence_minutes"] else _config["url"]
        return False
    if not _allowed_url(_config["url"]):
        if logger is not None:
            logger.warning("告警 Webhook 地址不被接受（仅允许 http/https、不允许带认证信息）：%s",
                           _config["url"][:80])
        _config["url"] = ""
        return False
    if logger is not None:
        logger.info("端点失联告警已启用：超过 %s 分钟未上报即推送（去重发送）",
                    _config["silence_minutes"])
    return True


def _allowed_url(url: str) -> bool:
    """URL 白名单：只允许明文 http/https，且不带 user:pass@（避免把凭据拼进 URL）。"""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    return bool(parsed.hostname)


def enabled() -> bool:
    return bool(_config.get("url"))


def check_endpoints(now: Optional[float] = None) -> list:
    """检查所有端点：超过阈值未上报的，推送一条去重告警。返回本次推送的端点列表。

    由服务端在读接口时调用（overview / endpoints）——不引入后台定时器，
    避免"没人看控制台时反而更吵"。
    """
    if not enabled():
        return []
    store = _config.get("store")
    if store is None:
        return []
    threshold = int(_config["silence_minutes"])
    now = now or time.time()
    sent: list = []
    try:
        endpoints = store.list_endpoints()
    except Exception:
        return []
    for item in endpoints:
        last_seen = str(item.get("last_seen_at") or "")
        if not last_seen:
            continue                      # 从没上报过的端点不算"失联"（还没上线）
        try:
            moment = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
            if moment.tzinfo is not None:
                moment = moment.astimezone().replace(tzinfo=None)
            age_minutes = (datetime.now() - moment).total_seconds() / 60.0
        except Exception:
            continue
        if age_minutes < threshold:
            continue
        endpoint_id = str(item.get("endpoint_id") or "")
        with _lock:
            last_sent = _sent.get(endpoint_id, 0.0)
            # 去重窗口 = 静默阈值（同一个"静默期"只提醒一次）
            if now - last_sent < max(60.0, threshold * 60.0):
                continue
            _sent[endpoint_id] = now
        payload = {"event": "endpoint_silent",
                   "endpoint_id": endpoint_id,
                   "name": str(item.get("name") or ""),
                   "last_seen_at": last_seen,
                   "silent_minutes": round(age_minutes, 1),
                   "threshold_minutes": threshold,
                   "generated_at": datetime.now().isoformat(timespec="seconds")}
        if _post(payload):
            sent.append(payload)
    return sent


def _post(payload: Dict[str, Any]) -> bool:
    """把告警推给运维通道。任何异常都不外抛（告警通道不能拖垮控制面）。"""
    url = _config.get("url") or ""
    if not url:
        return False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "YindunControl/" + str(payload.get("threshold_minutes", ""))})
    opener = urllib.request.build_opener(_NoRedirect())      # 不跟随重定向
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            ok = 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        ok = 200 <= exc.code < 300
    except Exception as exc:                                   # noqa: BLE001
        ok = False
        logger = _config.get("logger")
        if logger is not None:
            logger.warning("告警 Webhook 发送失败：%s: %s", type(exc).__name__, exc)
    return ok


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟随重定向：避免"告警地址"被跳转到别处（我们只信任运维给的那个地址）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):    # noqa: D102
        return None
