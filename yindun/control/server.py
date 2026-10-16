# -*- coding: utf-8 -*-
"""隐盾控制面 · HTTP 服务（Python 标准库，零额外依赖）。

启动：
    python -m yindun.control.server --port 8787          # 默认只监听 127.0.0.1
    python -m yindun.control.server --host 0.0.0.0 ...   # 显式对外（务必自行加 TLS 反代）

安全姿态（演示级原型，如实标注）：
- **默认只听回环**：`--host` 对外需要显式指定，且启动时打印风险提示。
- **令牌鉴权**：管理端与端点各用令牌，服务端只存哈希、比较用 `secrets.compare_digest`。
- **请求体上限** 2 MB、超限 413；JSON 解析失败 400；不做静默截断。
- **静态资源**只从控制台目录取，扩展名白名单 + `realpath/commonpath` 防目录穿越。
- 不做速率限制、不做多租户 RBAC、不做 TLS——这三项属"对外提供服务"的前提条件，
  要上生产必须补齐（文档里写明，不含糊）。
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from yindun.control import CONTROL_VERSION                      # noqa: E402
from yindun.control import desktop                              # noqa: E402
from yindun.control import policy as policy_mod                 # noqa: E402
from yindun.control.models import EndpointReport, ReportError   # noqa: E402
from yindun.control.store import ControlStore                   # noqa: E402
from yindun.control.verify import ALERT_CODES, verify_report    # noqa: E402

MAX_BODY = 2 * 1024 * 1024
WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".woff2": "font/woff2", ".ico": "image/x-icon"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class ControlServer:
    """路由与业务逻辑（与 HTTP 层解耦，便于直接单测）。"""

    def __init__(self, store: Optional[ControlStore] = None):
        self.store = store or ControlStore()

    # ── 鉴权 ──────────────────────────────────────────────
    @staticmethod
    def _bearer(header: str) -> str:
        if not header:
            return ""
        parts = header.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return ""

    def require_admin(self, headers: Dict[str, str]) -> None:
        if not self.store.verify_admin(self._bearer(headers.get("authorization", ""))):
            raise ApiError(401, "管理令牌无效")

    def require_endpoint(self, headers: Dict[str, str], endpoint_id: str) -> None:
        if not self.store.verify_endpoint(endpoint_id, self._bearer(headers.get("authorization", ""))):
            raise ApiError(401, "端点令牌无效或端点未登记")

    # ── 路由 ──────────────────────────────────────────────
    def route(self, method: str, path: str, query: Dict[str, list],
              headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        if method == "POST" and path == "/api/v1/enroll":
            return self.h_enroll(headers, body)
        if method == "POST" and path == "/api/v1/pair-code":
            return self.h_pair_code(headers, body)
        if method == "GET" and path == "/api/v1/pair-codes":
            self.require_admin(headers)
            return 200, {"codes": self.store.list_pair_codes()}
        if method == "GET" and path == "/api/v1/export":
            return self.h_export(headers, query)
        if method == "POST" and path == "/api/v1/report":
            return self.h_report(headers, body)
        if method == "GET" and path == "/api/v1/policy":
            return self.h_policy(headers, query)
        if method == "GET" and path == "/api/v1/overview":
            self.require_admin(headers)
            return 200, self.store.overview()
        if method == "GET" and path == "/api/v1/endpoints":
            self.require_admin(headers)
            return 200, {"endpoints": self.store.list_endpoints()}
        if method == "GET" and path == "/api/v1/events":
            self.require_admin(headers)
            return self.h_events(query)
        if method == "GET" and path == "/api/v1/usage":
            self.require_admin(headers)
            return 200, {"usage": self.store.usage_summary()}
        if method == "GET" and path == "/api/v1/stats":
            # 统计分析页用：趋势 / 类型 / 级别 / 端点活跃度（全部服务端聚合，界面只画）
            self.require_admin(headers)
            days = (query.get("days") or ["14"])[0]
            return 200, self.store.stats(int(days) if str(days).isdigit() else 14)
        if method == "GET" and path == "/api/v1/alerts":
            self.require_admin(headers)
            ep = (query.get("endpoint_id") or [""])[0]
            return 200, {"alerts": self.store.list_alerts(ep), "labels": {
                code: {"text": text, "high": high} for code, (text, high) in ALERT_CODES.items()}}
        if method == "POST" and path == "/api/v1/policy":
            return self.h_set_policy(headers, body)
        if method == "GET" and path == "/api/v1/self-test":
            self.require_admin(headers)
            return 200, self.h_self_test()
        raise ApiError(404, f"未知接口：{method} {path}")

    # ── 处理函数 ──────────────────────────────────────────
    def h_enroll(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        """登记端点。两种方式：

        · **配对码**（企业常规路径）：IT 在控制台生成一次性配对码，员工机用码自助接入——
          无需把管理令牌发给员工，码有有效期且用后作废；
        · **管理令牌**（脚本/批量）：保留给运维脚本使用。
        """
        data = _json_body(body)
        name = str(data.get("name", "")).strip() or "未命名端点"
        endpoint_id = str(data.get("endpoint_id", "")).strip()
        if not endpoint_id:
            raise ApiError(400, "缺少 endpoint_id")
        if len(endpoint_id) > 64 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.:-" for c in endpoint_id):
            raise ApiError(400, "endpoint_id 只允许字母数字与 _ . : -（≤64 字符）")

        code = str(data.get("code", "")).strip()
        if code:
            ok, reason = self.store.consume_pair_code(code, endpoint_id)
            if not ok:
                raise ApiError(403, f"配对码不可用：{reason}")
        else:
            self.require_admin(headers)          # 无配对码时必须持管理令牌
        token, policy_key = self.store.enroll(name, endpoint_id)
        return 200, {"endpoint_id": endpoint_id, "token": token, "policy_key": policy_key,
                     "note": "令牌与策略签名密钥只在此处下发一次，请写入端点配置"}

    def h_pair_code(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        """生成一次性配对码（管理端）。员工机凭码接入，不必持有管理令牌。"""
        self.require_admin(headers)
        data = _json_body(body) if body else {}
        return 200, self.store.create_pair_code(
            ttl_minutes=int(data.get("ttl_minutes") or 30),
            note=str(data.get("note") or ""))

    def h_export(self, headers: Dict[str, str], query: Dict[str, list]) -> Tuple[int, Any]:
        """导出审计（合规检查/等保留档用）。导出的是**掩码后的事件**，与界面所见完全一致。"""
        self.require_admin(headers)
        fmt = ((query.get("format") or ["json"])[0] or "json").lower()
        endpoint_id = (query.get("endpoint_id") or [""])[0]
        try:
            limit = int((query.get("limit") or ["2000"])[0] or 2000)
        except ValueError:
            limit = 2000
        events = self.store.list_events(endpoint_id=endpoint_id, limit=limit)
        alerts = self.store.list_alerts(endpoint_id=endpoint_id, limit=200, exclude_codes=())
        eps = self.store.list_endpoints()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if fmt == "csv":
            import csv as _csv
            import io as _io
            buf = _io.StringIO()
            writer = _csv.writer(buf)
            writer.writerow(["时间", "端点", "类型", "级别", "消息（掩码态）", "序号", "链哈希"])
            for ev in events:
                writer.writerow([ev.get("ts", ""), ev.get("endpoint_id", ""), ev.get("event_type", ""),
                                 ev.get("severity", ""), ev.get("message", ""), ev.get("seq", ""),
                                 (ev.get("entry_hash") or "")[:16]])
            if endpoint_id:                       # 导出单个端点时可带策略漂移快照
                for ep in eps:
                    if ep["endpoint_id"] == endpoint_id:
                        writer.writerow([])
                        writer.writerow(["策略漂移", ep["drift"], "在线", "是" if ep["online"] else "否"])
            data = buf.getvalue().encode("utf-8-sig")     # BOM：Excel 打开不乱码
            ctype = "text/csv; charset=utf-8"
            filename = f"yindun-audit-{stamp}.csv"
        else:
            data = json.dumps({"exported_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                               "data_boundary": "导出内容为端点掩码后的审计事件，不含明文 PII",
                               "endpoints": eps, "events": events, "alerts": alerts},
                              ensure_ascii=False, indent=2).encode("utf-8")
            ctype = "application/json; charset=utf-8"
            filename = f"yindun-audit-{stamp}.json"
        self._last_download = (ctype, filename)
        return 200, data

    def h_report(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        data = _json_body(body)
        endpoint_id = str(data.get("endpoint_id", "")).strip()
        if not endpoint_id:
            raise ApiError(400, "缺少 endpoint_id")
        self.require_endpoint(headers, endpoint_id)
        try:
            report = EndpointReport.from_dict(data)
        except ReportError as exc:
            raise ApiError(400, f"上报数据不合法：{exc}") from exc
        prev = self.store.snapshot_of(endpoint_id)
        result = verify_report(prev, report)
        applied = self.store.apply_report(report, result)
        bundle = self.store.policy_for(endpoint_id)
        return 200, {"ok": result.ok, "accepted": applied,
                     "alerts": [{"code": a.code, "detail": a.detail, "severity": a.severity}
                                for a in result.alerts],
                     "next_seq": (result.new_snapshot.total_count if result.new_snapshot else 0) + 1,
                     "policy_version": bundle.get("version", 0)}

    def h_policy(self, headers: Dict[str, str], query: Dict[str, list]) -> Tuple[int, Any]:
        endpoint_id = (query.get("endpoint_id") or [""])[0]
        if endpoint_id:
            self.require_endpoint(headers, endpoint_id)
            return 200, self.store.policy_for(endpoint_id)
        self.require_admin(headers)
        return 200, {"global": self.store.latest_policy("global"),
                     "history": self.store.policy_history()}

    def h_set_policy(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        self.require_admin(headers)
        data = _json_body(body)
        scope = str(data.get("scope", "global")).strip() or "global"
        bundle = data.get("bundle")
        if not isinstance(bundle, dict):
            raise ApiError(400, "缺少策略包对象 bundle")
        if scope != "global" and not self.store.has_endpoint(scope):
            raise ApiError(400, f"未知端点：{scope}")
        try:
            stored = self.store.set_policy(scope, bundle)
        except (policy_mod.PolicyError, ValueError) as exc:
            # 降级键/未知键在这里被挡下——这是"只收紧"的第一道防线
            raise ApiError(400, f"策略包被拒绝：{exc}") from exc
        return 200, {"scope": scope, "version": stored.get("version"), "sig": stored.get("sig", "")[:16],
                     "summary": policy_mod.describe(stored)}

    def h_events(self, query: Dict[str, list]) -> Tuple[int, Any]:
        get = lambda key, default="": (query.get(key) or [default])[0]     # noqa: E731
        try:
            limit = int(get("limit", "100") or 100)
            offset = int(get("offset", "0") or 0)
        except ValueError as exc:
            raise ApiError(400, "limit/offset 必须是整数") from exc
        filters = dict(endpoint_id=get("endpoint_id"), event_type=get("event_type"),
                       severity=get("severity"), keyword=get("q"))
        events = self.store.list_events(limit=limit, offset=offset, **filters)
        return 200, {"events": events, "total": self.store.count_events(**filters),
                     "types": self.store.event_types(),
                     "limit": max(1, min(limit, 500)), "offset": max(0, offset)}

    def h_self_test(self) -> Dict[str, Any]:
        """控制面自检：把"只收紧"这条不变式当场跑一遍给运维看（不是嘴上说说）。"""
        cases = [
            ({"sensitive_paths": ["D:/薪资/*"]}, {"sensitive_paths": ["D:/合同/*"], "approval_floor": "confirm"}),
            ({"approval_floor": "confirm"}, {"approval_floor": "deny"}),
            ({"allowed_models": ["a", "b"]}, {"allowed_models": ["b", "c"]}),
            ({"allowed_plugins": ["p1", "p2"]}, {"allowed_plugins": ["p2"]}),
            ({}, {"require_strict_mode": True}),
            ({"sensitive_paths": ["X"]}, {}),
        ]
        rows = []
        for base, remote in cases:
            local = policy_mod.empty_local()
            local.update(base)
            merged = policy_mod.merge_tighten(local, remote)
            rows.append({"local": base, "remote": remote, "merged": {
                "sensitive_paths": merged["sensitive_paths"],
                "approval_floor": merged["approval_floor"],
                "allowed_models": merged["allowed_models"],
                "allowed_plugins": merged["allowed_plugins"],
                "require_strict_mode": merged["require_strict_mode"]},
                "monotonic": policy_mod.is_tightening_only(local, merged)})
        rejected = []
        for bad in ({"anonymize": False}, {"approval": "approve"}, {"whitelist": ["rm"]},
                    {"bypass": True}, {"unknown_key": 1}):
            try:
                policy_mod.validate_keys(bad)
                rejected.append({"bundle": bad, "rejected": False})
            except policy_mod.PolicyError as exc:
                rejected.append({"bundle": bad, "rejected": True, "reason": str(exc)})
        return {"cases": rows, "loosening_rejected": rejected,
                "all_monotonic": all(r["monotonic"] for r in rows),
                "all_rejected": all(r["rejected"] for r in rejected),
                "order": list(policy_mod.APPROVAL_RANK)}


def _json_body(body: bytes) -> Dict[str, Any]:
    if not body:
        raise ApiError(400, "请求体为空")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiError(400, f"请求体不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ApiError(400, "请求体必须是 JSON 对象")
    return data


# ══════════════════════════════════════════════════════════════════
#  HTTP 层
# ══════════════════════════════════════════════════════════════════

def make_handler(app: ControlServer, verbose: bool = True):
    class Handler(BaseHTTPRequestHandler):
        server_version = "YindunControl/" + CONTROL_VERSION
        sys_version = ""            # 不暴露 Python 版本

        def log_message(self, fmt: str, *args) -> None:      # noqa: A003
            if verbose:
                sys.stdout.write("[控制面] %s - %s\n" % (self.address_string(), fmt % args))

        # ── 工具 ──
        def _send(self, status: int, payload: Any, ctype: str = "application/json; charset=utf-8") -> None:
            if isinstance(payload, (dict, list)):
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            elif isinstance(payload, bytes):
                data = payload
            else:
                data = str(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _headers(self) -> Dict[str, str]:
            return {k.lower(): v for k, v in self.headers.items()}

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise ApiError(413, f"请求体过大（>{MAX_BODY // 1024}KB）")
            return self.rfile.read(length) if length else b""

        def _static(self, path: str) -> None:
            rel = "index.html" if path in ("/", "") else unquote(path.lstrip("/"))
            if rel.startswith("api/"):
                raise ApiError(404, "未找到")
            target = (WEB_DIR / rel).resolve()
            try:                     # 防目录穿越：解析后必须仍在控制台目录内
                target.relative_to(WEB_DIR.resolve())
            except ValueError:
                raise ApiError(403, "越界路径被拒绝") from None
            if not target.is_file() or target.suffix.lower() not in STATIC_TYPES:
                raise ApiError(404, "未找到")
            self._send(200, target.read_bytes(), STATIC_TYPES[target.suffix.lower()])

        # ── 方法 ──
        def do_GET(self) -> None:      # noqa: N802
            self._dispatch("GET")

        def do_HEAD(self) -> None:     # noqa: N802
            self._dispatch("HEAD")

        def do_POST(self) -> None:     # noqa: N802
            self._dispatch("POST")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            try:
                if not path.startswith("/api/"):
                    return self._static(path)
                body = self._read_body() if method == "POST" else b""
                status, payload = app.route(method, path, parse_qs(parsed.query),
                                            self._headers(), body)
                download = getattr(app, "_last_download", None)
                if download and path == "/api/v1/export" and status == 200:
                    app._last_download = None
                    ctype, filename = download
                    if isinstance(payload, bytes):
                        self.send_response(status)
                        self.send_header("Content-Type", ctype)
                        self.send_header("Content-Length", str(len(payload)))
                        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.send_header("Cache-Control", "no-store")
                        self.end_headers()
                        self.wfile.write(payload)
                        return
                return self._send(status, payload)
            except ApiError as exc:
                return self._send(exc.status, {"error": exc.message})
            except Exception as exc:                      # 兜底：不让异常泄露堆栈给调用方
                if verbose:
                    import traceback
                    traceback.print_exc()
                return self._send(500, {"error": f"服务端内部错误：{type(exc).__name__}"})

    return Handler


def serve(host: str = "127.0.0.1", port: int = 8787, store: Optional[ControlStore] = None,
          verbose: bool = True) -> ThreadingHTTPServer:
    app = ControlServer(store)
    httpd = ThreadingHTTPServer((host, port), make_handler(app, verbose))
    httpd.daemon_threads = True
    return httpd


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="隐盾控制面（企业侧集中管理 · 演示级原型）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data", default="", help="控制面数据目录（默认 control_data/）")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--window", dest="window", nargs="?", const="yes", default="auto",
                        choices=["auto", "yes", "no", "browser"],
                        help="承载方式：auto（默认，回环监听时开桌面窗口）/ yes / no（纯服务）/ browser（用默认浏览器打开）")
    # 两个等价简写：日常更顺手，语义与 --window no / --window browser 完全一致
    parser.add_argument("--no-window", dest="window", action="store_const", const="no",
                        help="等价于 --window no：只起 HTTP 服务（装到服务器上的场景）")
    parser.add_argument("--browser", dest="window", action="store_const", const="browser",
                        help="等价于 --window browser：不弹窗口，用系统默认浏览器打开控制台")
    args = parser.parse_args(argv)

    store = ControlStore(Path(args.data) / "control.db") if args.data else ControlStore()
    token = store.ensure_admin_token()
    httpd = serve(args.host, args.port, store, verbose=not args.quiet)
    external = args.host not in ("127.0.0.1", "localhost", "::1", "")

    mode, why = desktop.plan_launch(args.host, args.window)
    url = desktop.console_url(args.host, args.port, token if mode == "window" else "")
    # 令牌只在"不会被自动登录"的模式下打印到终端（窗口/浏览器模式已带进去，没必要再吐一次）
    desktop.stdout_banner(args.host, args.port, store.dir, store.dir / "admin_token.txt",
                          token if mode == "server" else "", external=external)
    print(f"  启动方式   {why}")

    if mode == "server":
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n[控制面] 已停止")
        finally:
            httpd.server_close()
        return 0

    # 窗口 / 浏览器：HTTP 放后台线程，主线程让给 GUI（pywebview 的循环必须在主线程）
    desktop.serve_in_background(httpd)
    try:
        if mode == "browser":
            if not desktop.open_browser(url):
                print(f"[控制面] 未能自动打开浏览器，请手动访问：{url}")
            print("[控制面] 服务运行中，按 Ctrl+C 停止")
            while True:
                time.sleep(0.5)
        else:
            print("[控制面] 控制台窗口已打开（关闭窗口即退出服务）")
            if not desktop.open_window(url):
                # 兜底：没有 WebView2/桌面环境时退回浏览器，并明确说明发生了什么
                desktop.open_browser(url)
                print("[控制面] 已在默认浏览器中打开控制台")
                while True:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        print("\n[控制面] 已停止")
        httpd.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
