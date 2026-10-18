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
import os
import re
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

# 运行期的监听状态（`serve()` 记录、`/api/v1/listen` 切换、`lan_access_info()` 读取）。
# 放在模块级是因为"当前监听在哪个地址"是**进程级事实**，而这套服务只有一个实例。
_LISTEN_STATE: Dict[str, Any] = {"host": "127.0.0.1", "port": 8787}
_LISTEN_SERVER: Optional[ThreadingHTTPServer] = None
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".woff2": "font/woff2", ".ico": "image/x-icon"}


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def display_version() -> str:
    """产品版本号（唯一来源：`yindun.__display_version__`）。

    控制台页面与客户端界面都不许写死版本——写死就会漂：
    V3.5.x 期间出现过"控制台徽标还写 V3.5.9、客户端已经是 V3.6.0"的不一致，
    每次 bump 要手改三处，漏一处没人发现。现在页面里放 `{{VERSION}}` 占位符，
    由这里注入；`tests/test_version_consistency.py` 会钉住这条链路。
    取不到就返回空串（页面显示空白而不是一个错的版本号）。
    """
    try:
        from yindun import __display_version__
        return str(__display_version__)
    except Exception:
        return ""


def render_static(target: Path) -> bytes:
    """读取控制台静态资源；对 index.html 做版本占位符注入。"""
    blob = target.read_bytes()
    if target.name == "index.html" and b"{{VERSION}}" in blob:
        blob = blob.replace(b"{{VERSION}}", display_version().encode("utf-8"))
    return blob


class ControlServer:
    """路由与业务逻辑（与 HTTP 层解耦，便于直接单测）。"""

    def __init__(self, store: Optional[ControlStore] = None):
        self.store = store or ControlStore()
        # 响应写完之后要做的动作（目前只有"切监听后退役旧服务对象"用；见 run_after_response）。
        # 放在实例上而不是模块级：谁处理了这次请求，就由谁来收尾，多实例（测试里会起好几个）也不会串。
        self._after_response: list = []

    def defer_after_response(self, action) -> None:
        """登记一个"响应写完后执行"的动作（V3.6.7：切监听退役旧服务对象用）。"""
        self._after_response.append(action)

    def run_after_response(self) -> None:
        """执行并清空已登记的动作；任何异常都吞掉——收尾失败不该反过来影响已发出的响应。"""
        actions, self._after_response = self._after_response, []
        for action in actions:
            try:
                action()
            except Exception:
                pass

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

    def require_read(self, headers: Dict[str, str]) -> None:
        """**读**操作鉴权：管理令牌或只读令牌都行（V3.6.2，C-14 多角色）。

        为什么要有只读令牌：巡检 / 看板 / 对接监控系统只需要读；给它们管理令牌等于把
        "下发策略、登记端点"的权限一起给出去。**写操作一律仍只认管理令牌。**
        """
        token = self._bearer(headers.get("authorization", ""))
        if self.store.verify_admin(token) or self.store.verify_viewer(token):
            return
        raise ApiError(401, "令牌无效（读操作可用管理令牌或只读令牌）")

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
        if method == "GET" and path == "/api/v1/pair-code":
            return self.h_pair_code_current(headers)
        if method == "GET" and path == "/api/v1/network":
            return self.h_network(headers)
        if method == "POST" and path == "/api/v1/listen":
            return self.h_listen(headers, body)
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
            self.require_read(headers)
            self._check_silent_endpoints()
            return 200, self.store.overview()
        if method == "GET" and path == "/api/v1/endpoints":
            self.require_read(headers)
            self._check_silent_endpoints()
            return 200, {"endpoints": self.store.list_endpoints()}
        if method == "GET" and path == "/api/v1/events":
            self.require_read(headers)
            return self.h_events(query)
        if method == "GET" and path == "/api/v1/usage":
            self.require_read(headers)
            return 200, {"usage": self.store.usage_summary()}
        if method == "GET" and path == "/api/v1/stats":
            # 统计分析页用：趋势 / 类型 / 级别 / 端点活跃度（全部服务端聚合，界面只画）
            self.require_read(headers)
            days = (query.get("days") or ["14"])[0]
            return 200, self.store.stats(int(days) if str(days).isdigit() else 14)
        if method == "GET" and path == "/api/v1/alerts":
            self.require_read(headers)
            ep = (query.get("endpoint_id") or [""])[0]
            return 200, {"alerts": self.store.list_alerts(ep), "labels": {
                code: {"text": text, "high": high} for code, (text, high) in ALERT_CODES.items()}}
        if method == "POST" and path == "/api/v1/policy":
            return self.h_set_policy(headers, body)
        if method == "POST" and path == "/api/v1/policy/revert":
            return self.h_revert_policy(headers, body)
        if method == "POST" and path == "/api/v1/tokens/viewer":
            self.require_admin(headers)
            token = self.store.ensure_viewer_token()
            return 200, {"ok": True, "token": token,
                         "note": "只读令牌：可看态势/台账/资产/统计，不能下发策略或登记端点；"
                                 "同一份明文也在 <数据目录>/viewer_token.txt"}
        if method == "POST" and path == "/api/v1/backup":
            self.require_admin(headers)
            payload = _json_body(body) if body else {}
            dest = str(payload.get("dir") or (self.store.dir / "backups"))
            result = self.store.backup(dest)
            result["pruned"] = self.store.prune_backups(dest, keep=int(payload.get("keep") or 5))
            return 200, result
        if method == "GET" and path == "/api/v1/self-test":
            self.require_admin(headers)
            return 200, self.h_self_test()
        raise ApiError(404, f"未知接口：{method} {path}")

    @staticmethod
    def _check_silent_endpoints() -> None:
        """读接口顺手做一次"端点失联"检查（V3.6.2，C-14）。

        为什么不用后台定时器：没人看控制台时反而更该安静；有人看的时候顺手检查，
        既不引入常驻线程，也让"告警"和"有人在看"这两件事自然对齐。
        """
        try:
            from yindun.control import alerts as _alerts
            _alerts.check_endpoints()
        except Exception:
            pass          # 告警通道故障绝不能影响读接口

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
        """生成一次性配对码（管理端）。员工机凭码接入，不必持有管理令牌。

        传 `code` 即自定义码（口头/手写转述用），校验与占用检查在 store 层。
        """
        self.require_admin(headers)
        data = _json_body(body) if body else {}
        result = self.store.create_pair_code(
            ttl_minutes=int(data.get("ttl_minutes") or 30),
            note=str(data.get("note") or ""),
            code=str(data.get("code") or ""))
        if not result.get("ok"):
            return 400, {"error": result.get("error") or "配对码生成失败"}
        return 200, result

    def h_pair_code_current(self, headers: Dict[str, str]) -> Tuple[int, Any]:
        """当前可用配对码（只读）。界面要一直显示它，而不是弹一次就没了。"""
        self.require_read(headers)
        return 200, self.store.current_pair_code()

    def h_network(self, headers: Dict[str, str]) -> Tuple[int, Any]:
        """本机监听状态 + 局域网可访问地址（供"同学怎么连进来"这类场景）。

        ★ 只做本机网卡枚举（`socket` 层面，无任何外部请求）：列出非回环、非虚拟网卡的 IPv4，
          拼成 `<scheme>://<ip>:<port>/`。拿不到就返回空列表并说明原因，不猜。
        """
        self.require_read(headers)
        return 200, lan_access_info()

    def h_listen(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        """切换监听地址（仅本机 ↔ 允许局域网）。

        这是**管理动作**（改的是谁能访问控制面），所以只要管理令牌、不要只读令牌。
        """
        self.require_admin(headers)
        data = _json_body(body) if body else {}
        result = rebind_listen(str(data.get("host") or ""))
        if not result.get("ok"):
            return 400, {"error": result.get("error") or "监听地址切换失败"}
        return 200, result

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

    def h_revert_policy(self, headers: Dict[str, str], body: bytes) -> Tuple[int, Any]:
        """策略回滚（V3.6.2，C-14）：把某个 scope 的生效集合回到历史某一版。

        ★ 这里要说清楚"回滚能做什么、不能做什么"：
          · 能：把控制面**推荐给该 scope 的约束集合**恢复成历史上某一版（例如误下发了
            一批只读冻结、想退回上一版）；
          · 不能：**放宽端点**。端点侧收到的是 `merge_tighten(本地, 远程)` 的单调合并，
            即便控制面把推荐改松，端点仍保留它此前已收紧的约束（这条性质有回归用例钉住）。
          · 历史行**永不删除**，只标 active=0 —— 审计仍然能回答"谁都下发过什么"。
        """
        self.require_admin(headers)
        data = _json_body(body)
        scope = str(data.get("scope", "global")).strip() or "global"
        if scope != "global" and not self.store.has_endpoint(scope):
            raise ApiError(400, f"未知端点：{scope}")
        if data.get("version") is None:
            raise ApiError(400, "缺少 version（要回滚到哪一版）")
        result = self.store.revert_scope(scope, data.get("version"))
        if not result.get("ok"):
            raise ApiError(400, str(result.get("error") or "回滚失败"))
        result["effective"] = self.store.policy_for(scope if scope != "global" else "")
        result["note"] = ("已把该范围回滚到 v%s；端点侧只收紧，回滚不会放宽任何既有约束"
                          % result.get("reverted_to"))
        return 200, result

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

class RateLimiter:
    """按客户端令牌桶限流（V3.6.2，C-14）。

    **抽成独立类是为了可测**：限流逻辑不碰网络、不碰请求对象，测试直接喂 IP 就能验
    （`tests/test_control_plane.py` §H4）——比"起个端口猛打一遍"更准，也不必在测试里
    写"构造 URL 发请求"那种既慢又容易被安全门禁合理拦下的代码。

    语义：容量 = burst（突发额度），按 `per_min/60` 每秒补充；桶空即拒绝。
    `per_min <= 0` 表示不启用（限流关闭时 `allow()` 永远返回 True）。
    """

    def __init__(self, per_min: int = 0, burst: Optional[int] = None) -> None:
        self.per_min = max(0, int(per_min or 0))
        self.burst = int(burst) if burst is not None else max(10, self.per_min // 6)
        self._buckets: Dict[str, Tuple[float, float]] = {}      # ip -> (令牌数, 上次补充时刻)
        self._lock = threading.Lock()

    def allow(self, key: str, now: Optional[float] = None) -> bool:
        """记一次访问：返回 True = 放行，False = 应拒绝（调用方回 429）。"""
        if not self.per_min:
            return True
        moment = time.monotonic() if now is None else float(now)
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.burst), moment))
            tokens = min(float(self.burst), tokens + (moment - last) * (self.per_min / 60.0))
            if tokens < 1.0:
                self._buckets[key] = (tokens, moment)
                return False
            self._buckets[key] = (tokens - 1.0, moment)
        return True


def make_handler(app: ControlServer, verbose: bool = True, rate_limit_per_min: int = 0):
    """构造 HTTP 处理器。

    `rate_limit_per_min`（V3.6.2，C-14）：每客户端 IP 每分钟的请求上限，0 = 关闭（默认）。
    为什么默认关：单机/回环演示场景加限流只会添乱；一旦对外监听，就该显式打开。
    """
    limiter = RateLimiter(rate_limit_per_min)
    # 把限流器挂到 app 上（V3.6.7）：切监听会重建服务对象，测试与运维需要一个**可读的凭据**
    # 来确认"新服务仍带着原来的限流配置"——这样就不必为此发真实网络请求。
    app.rate_limiter = limiter

    class Handler(BaseHTTPRequestHandler):
        server_version = "YindunControl/" + CONTROL_VERSION
        sys_version = ""            # 不暴露 Python 版本

        def log_message(self, fmt: str, *args) -> None:      # noqa: A003
            if verbose:
                sys.stdout.write("[控制面] %s - %s\n" % (self.address_string(), fmt % args))

        # ── 限流 ──
        def _rate_limited(self) -> bool:
            """返回 True 表示这次请求应被拒绝（429）。按客户端 IP 计，令牌桶平滑。"""
            ip = str(self.client_address[0]) if self.client_address else "?"
            return not limiter.allow(ip)

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
            self._send(200, render_static(target), STATIC_TYPES[target.suffix.lower()])

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
                if self._rate_limited():
                    self.send_response(429)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Retry-After", "5")
                    self.send_header("Cache-Control", "no-store")
                    body = json.dumps({"error": "请求过于频繁（已触发控制面限流）"},
                                      ensure_ascii=False).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
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
            finally:
                # ★ 响应写完之后才执行"退役动作"（V3.6.7 实测踩到）：切监听要关掉旧的服务对象，
                #   但**旧服务正拿着这次请求**——在 handler 里就关掉它，响应会写不出去
                #   （浏览器侧就是 `切换监听地址失败：Failed to fetch`），
                #   `--no-window` 形态下更严重：旧服务的 serve_forever 跑在主线程，关掉它等于结束进程。
                #   放到 finally 里、响应已在 wfile 里之后执行，顺序就对了。
                app.run_after_response()

    return Handler


def lan_ipv4_addresses() -> list:
    """本机可用于局域网访问的 IPv4 地址（**纯本地枚举，不发任何外部请求**）。

    怎么挑（按优先级）：先用一条 UDP socket"连"一个外部地址让系统选出默认出口网卡的源地址
    （`connect` 不发包，只是让内核做路由选择），再把其它非回环地址补上。
    过滤掉明显不是"别人能连"的地址：回环、APIPA(169.254)、以及常见的虚拟网卡网段
    （VMware `192.168.x.1`/`.5.1` 这类宿主虚拟网段仍是合法地址，所以只按网段做**软**标注，不硬删）。
    """
    found: list = []
    import socket as _socket

    def _usable(ip: str) -> bool:
        if not ip or ip.startswith("127.") or ip.startswith("169.254."):
            return False
        return bool(re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", ip))

    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))          # 只为让内核选出口；UDP connect 不产生流量
        primary = sock.getsockname()[0]
        if _usable(primary):
            found.append(primary)
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for info in _socket.getaddrinfo(_socket.gethostname(), None, _socket.AF_INET):
            ip = info[4][0]
            if _usable(ip) and ip not in found:
                found.append(ip)
    except OSError:
        pass
    return found


def lan_access_info() -> Dict[str, Any]:
    """监听状态 + 局域网可访问地址 + 操作提示（给"别人怎么连进来"这个界面用）。

    ★ 如实说明：默认只监听回环时，**局域网里的别的机器连不上**，这不是网络问题、也不是配对码问题；
      要让别人连，必须切到对外监听，而且 Windows 防火墙第一次会弹询问（没允许就仍然连不上）。
      这几句直接进返回值，界面照着显示即可——省得用户对着"连不上"猜。
    """
    host, port = _LISTEN_STATE.get("host", "127.0.0.1"), int(_LISTEN_STATE.get("port", 8787))
    external = host not in ("127.0.0.1", "localhost", "::1", "")
    ips = lan_ipv4_addresses()
    urls = [f"http://{ip}:{port}/" for ip in ips]
    return {
        "listen_host": host,
        "listen_port": port,
        "external": external,
        "lan_urls": urls if external else [],
        "available_lan_urls": urls,             # 切到对外监听后**将会**可用的地址（提前显示，便于预判）
        "primary_url": (urls[0] if urls else f"http://127.0.0.1:{port}/"),
        # 装了 VMware/VirtualBox/内网穿透的机器会列出好几个地址，别人能连的通常只有第一个
        # （默认出口网卡）。这句提示直接进返回值，界面照着显示，省得对方把虚拟网卡地址抄过去试半天。
        "address_hint": ("列表里第一个通常是 WiFi/热点的地址，优先用它；"
                         "192.168.x.1 这类多为 VMware/VirtualBox 虚拟网卡，别人连不上。"
                         if len(urls) > 1 else ""),
        "firewall_hint": ("对外监听后，Windows 防火墙会弹一次询问，必须选「允许访问」，"
                          "否则别人仍然连不上（没弹就说明之前已拒绝过，需要到防火墙里放行 python/本程序）。"),
        "tls_note": ("本形态不带 TLS：只在自己可信的局域网里用（例如手机热点/宿舍网），"
                     "不要在公共 WiFi 上长期开着；配对码用后即作废、管理令牌不要外发。"),
    }


def rebind_listen(host: str) -> Dict[str, Any]:
    """把监听地址切到 host（`127.0.0.1` 只本机 / `0.0.0.0` 允许局域网），**不重启进程**。

    怎么做：在**新地址**上先建好新的 HTTP 服务（同一个 store、同一个 handler 工厂），
    成功后再关掉旧的、把 `_LISTEN_STATE` 换成新的，最后启动新的服务线程。
    先建后关是有意为之——若新地址绑不上（端口被占/权限），旧服务必须原样还在，
    否则用户会"点一下开关把控制台弄没了"。
    """
    global _LISTEN_STATE, _LISTEN_SERVER
    want = str(host or "").strip() or "127.0.0.1"
    if want not in ("127.0.0.1", "0.0.0.0"):
        return {"ok": False, "error": "只支持 127.0.0.1（仅本机）或 0.0.0.0（允许局域网）"}
    cur = _LISTEN_STATE.get("host", "127.0.0.1")
    if want == cur:
        return {"ok": True, "changed": False, "note": "监听地址没有变化", **lan_access_info()}
    port = int(_LISTEN_STATE.get("port", 8787))
    store = _LISTEN_STATE.get("store")
    if store is None or _LISTEN_SERVER is None:
        return {"ok": False, "error": "当前形态不支持运行期切换监听（请用命令行 --host 重启）"}
    # ★ 一定要在 serve() 之前取走"旧服务用的那个 app"：serve() 会把 _LISTEN_STATE["app"] 换成新 app，
    #   而这次请求是**旧 app 在处理的**——退役动作登记错对象，旧监听就会一直留在那儿（实测：切回仅本机后
    #   `0.0.0.0:port` 仍在 LISTENING）。
    old = _LISTEN_SERVER
    old_app = _LISTEN_STATE.get("app")
    try:
        fresh = serve(want, port, store, verbose=bool(_LISTEN_STATE.get("verbose", True)),
                      rate_limit_per_min=int(_LISTEN_STATE.get("rate_limit", 0)),
                      tls_cert=str(_LISTEN_STATE.get("tls_cert", "")),
                      tls_key=str(_LISTEN_STATE.get("tls_key", "")))
    except OSError as exc:
        return {"ok": False, "error": f"切换失败（{type(exc).__name__}: {exc}）；已保持原监听不变"}
    _LISTEN_SERVER = fresh
    _LISTEN_STATE["host"] = want
    # 谁在跑 serve_forever 决定"要不要自己起线程"（V3.6.7）：server（--no-window）形态下主线程
    # 就是那个循环（它会自动接着服务新对象，见 main 里的 while）；其余形态 HTTP 跑在后台线程，
    # 新对象得自己起一个。**不能两边都 serve_forever 同一个对象**——两个线程同时 accept 同一个
    # socket 是竞态，宁可显式区分归属。
    if _LISTEN_STATE.get("loop_owner") != "main":
        threading.Thread(target=fresh.serve_forever, kwargs={"poll_interval": 0.3},
                         daemon=True, name="yindun-control-http").start()

    def _retire_old(grace: float) -> None:
        """退役旧服务对象：先等 `grace` 秒，再停止接收新连接、最后关掉 socket。

        ★ 为什么要等（V3.6.7 实测）：界面切完开关会紧接着再请求一次 `GET /api/v1/network`
          （把界面刷成服务端的真实状态）。那个连接若落在**已被关掉**的旧 socket 上，
          Windows 会回 `ERR_CONNECTION_RESET`——界面上就是「监听状态接口不可用：Failed to fetch」，
          看着像切换失败。留宽限期后，这次请求落到新旧哪个监听都能被正常应答（同一个 store），
          之后再关旧 socket 就没有这个问题了。
        """
        try:
            time.sleep(grace)
            old.shutdown()               # 停止接收新连接（已接纳的请求继续在各自线程里跑完）
            time.sleep(0.2)
            old.server_close()
        except Exception:
            pass

    # 两条路都留着，缺一条就会漏：① 登记给**处理这次请求的那个 app**（正常路径：响应写完才开始计时）；
    # ② 兜底线程——直接调 rebind_listen（测试/脚本）时没有请求在飞，没人来跑 ①。
    # 两条都用同一个实现（只差宽限期），重复退役是安全的（shutdown/server_close 幂等）。
    if old_app is not None:
        old_app.defer_after_response(lambda: _retire_old(0.8))
    threading.Thread(target=lambda: _retire_old(1.2), daemon=True,
                     name="yindun-control-retire").start()
    desktop.say(f"[控制面] 监听地址已切换：{cur} → {want}（端口 {port}）")
    return {"ok": True, "changed": True, **lan_access_info()}


def serve(host: str = "127.0.0.1", port: int = 8787, store: Optional[ControlStore] = None,
          verbose: bool = True, rate_limit_per_min: int = 0,
          tls_cert: str = "", tls_key: str = "") -> ThreadingHTTPServer:
    app = ControlServer(store)
    httpd = ThreadingHTTPServer((host, port), make_handler(app, verbose, rate_limit_per_min))
    httpd.daemon_threads = True
    if tls_cert and tls_key:
        # ★ TLS（V3.6.2，C-14）：对外监听时的最低要求。证书/私钥路径由运维在命令行给出
        #   （不读请求里的任何东西——控制面自己不去"发现"证书位置）。
        #   自签证书也接受：内网场景常见，但对客户端而言需要显式信任。
        import ssl
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile=tls_cert, keyfile=tls_key)
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    # 记下"当前监听的是什么、怎么重建"：`/api/v1/listen` 运行期切换要用（见 rebind_listen）。
    # ★ 端口取**实际绑定的**（`port=0` 时由系统分配），否则切监听会跑到另一个随机端口上，
    #   界面显示的地址也就跟着错。
    bound_host, bound_port = httpd.server_address[0], httpd.server_address[1]
    _LISTEN_STATE.update({"host": host if port else bound_host, "port": bound_port,
                          "app": app, "store": store, "verbose": verbose,
                          "rate_limit": rate_limit_per_min, "tls_cert": tls_cert, "tls_key": tls_key})
    return httpd


def port_in_use(host: str, port: int, timeout: float = 0.6) -> bool:
    """这个端口上是不是已经有**活着的**服务在监听。

    ★ 为什么必须主动探一次（V3.6.7 实测踩到）：Windows 上 `HTTPServer` 默认
      `allow_reuse_address = 1`（SO_REUSEADDR），**两个进程可以同时绑同一个端口**，
      于是"双击两次 exe"变成两个实例抢连接——浏览器落到哪个实例全看运气，
      用户看到的是旧版本界面却以为是新 exe 没生效（实测：老版本实例还在跑，
      新 exe 起在同一端口，页面上还是旧版本号）。端口上没人时才继续启动。
    """
    if not int(port or 0):
        return False                      # 0 = 由系统分配端口，不存在占用一说
    probe_host = {"0.0.0.0": "127.0.0.1", "::": "::1", "": "127.0.0.1"}.get(host, host)
    import socket
    family = socket.AF_INET6 if ":" in probe_host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(timeout)
        sock.connect((probe_host, int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def _run(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="隐盾控制面（企业侧集中管理 · 演示级原型）")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址（默认仅回环）")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data", default="", help="控制面数据目录（默认 control_data/）")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--log-level", default="", help="日志级别 DEBUG/INFO/WARNING/ERROR（默认 INFO）")
    parser.add_argument("--log-dir", default="", help="日志落点（默认 <APP_ROOT>/logs）")
    parser.add_argument("--log-capture-prints", action="store_true",
                        help="把散装 print 也收进日志（默认不采：避免默认多一条明文落盘面）")
    # ── 生产化开关（V3.6.2，C-14）：默认值都保持"演示友好"，一旦对外监听就显式打开 ──
    parser.add_argument("--rate-limit", type=int, default=0,
                        help="每客户端 IP 每分钟请求上限（0=关闭；对外监听建议 600 左右）")
    parser.add_argument("--tls-cert", default="", help="TLS 证书路径（与 --tls-key 同时给才生效）")
    parser.add_argument("--tls-key", default="", help="TLS 私钥路径")
    parser.add_argument("--backup-dir", default="", help="启动时备份数据库到该目录（顺带清理旧备份）")
    parser.add_argument("--backup-keep", type=int, default=5, help="保留最近几份备份（默认 5）")
    parser.add_argument("--alert-webhook", default="",
                        help="端点失联告警的 Webhook 地址（http/https；留空=不发）")
    parser.add_argument("--alert-silence-minutes", type=int, default=0,
                        help="多久没上报算失联（分钟；0=不判定失联）")
    parser.add_argument("--window", dest="window", nargs="?", const="yes", default="auto",
                        choices=["auto", "yes", "no", "browser"],
                        help="承载方式：auto（默认，回环监听时开桌面窗口）/ yes / no（纯服务）/ browser（用默认浏览器打开）")
    # 两个等价简写：日常更顺手，语义与 --window no / --window browser 完全一致
    parser.add_argument("--no-window", dest="window", action="store_const", const="no",
                        help="等价于 --window no：只起 HTTP 服务（装到服务器上的场景）")
    parser.add_argument("--browser", dest="window", action="store_const", const="browser",
                        help="等价于 --window browser：不弹窗口，用系统默认浏览器打开控制台")
    args = parser.parse_args(argv)

    # 统一日志出口（V3.6.1）：控制面打包成 exe 后原本什么都不落盘，现场排障只能靠终端。
    # 现在固定写 <APP_ROOT>/logs/yindun.log（可用 --log-dir 换），级别默认 INFO。
    # 散装 print 的采集**默认关闭**（多一条明文落盘面要慎重），需要排障时用 --log-capture-prints 打开。
    from yindun.utils.logger import get_logger, install_print_bridge, setup_logging
    if args.log_capture_prints:
        os.environ["YINDUN_LOG_CAPTURE_PRINTS"] = "1"
    setup_logging(level=args.log_level, log_dir=args.log_dir, console=not args.quiet)
    if getattr(sys, "frozen", False):
        install_print_bridge()
    log = get_logger("control")
    log.info("控制面启动请求：host=%s port=%s data=%s window=%s",
             args.host, args.port, args.data or "(默认)", args.window)

    store = ControlStore(Path(args.data) / "control.db") if args.data else ControlStore()
    token = store.ensure_admin_token()
    if args.backup_dir:
        # ★ 启动即备份（V3.6.2，C-14）：便携单文件形态下"库就是一个文件"，
        #   出问题没有回退手段。给了 --backup-dir 就每次启动留一份，并只保留最近 N 份。
        try:
            result = store.backup(args.backup_dir)
            removed = store.prune_backups(args.backup_dir, keep=args.backup_keep)
            log.info("启动备份完成：%s（%s 字节，清理旧备份 %s 份）",
                     result["path"], result["bytes"], removed)
        except Exception as exc:                     # 备份失败不能拦启动，但要留痕
            log.warning("启动备份失败：%s: %s", type(exc).__name__, exc)
    if args.alert_webhook:
        # ★ 端点失联告警路由（V3.6.2，C-14）：Webhook 地址**只来自命令行/配置**，
        #   绝不从请求体里取（那是 SSRF 面）。只在阈值 > 0 时启用。
        from yindun.control import alerts as _alerts
        _alerts.configure(url=args.alert_webhook,
                          silence_minutes=max(0, int(args.alert_silence_minutes)),
                          store=store, logger=log)
    # ── 单实例：端口上已有活着的服务就别再起一个（V3.6.7）──────────────
    # 双击两次 exe 在 Windows 上会**两个实例绑同一端口**（SO_REUSEADDR），
    # 谁先接到连接谁说话 → 用户可能一直看着旧实例的界面，却以为新 exe 没生效。
    if port_in_use(args.host, args.port):
        where = f"http://{args.host if args.host else '127.0.0.1'}:{args.port}/"
        desktop.say(f"[控制面] 端口 {args.port} 上已有服务在运行，本次不再启动（{where}）")
        log.warning("端口 %s 已被占用，拒绝重复启动", args.port)
        desktop.notify("隐盾控制台已经在运行",
                       f"端口 {args.port} 上已经有一个控制台在运行了，本次不再重复启动。\n\n"
                       "请使用已经打开的那个控制台窗口；如果找不到它，"
                       "请在任务管理器里结束旧的「隐盾控制端…便携版.exe」进程后重新双击。\n\n"
                       "（这样做是为了避免两个实例抢同一个端口，让你看到旧版本的界面）")
        return 3
    httpd = serve(args.host, args.port, store, verbose=not args.quiet,
                  rate_limit_per_min=max(0, int(args.rate_limit)),
                  tls_cert=args.tls_cert, tls_key=args.tls_key)
    global _LISTEN_SERVER
    _LISTEN_SERVER = httpd          # 运行期切换监听要用（见 rebind_listen）
    external = args.host not in ("127.0.0.1", "localhost", "::1", "")

    mode, why = desktop.plan_launch(args.host, args.window)
    url = desktop.console_url(args.host, args.port, token if mode == "window" else "")
    # 令牌只在"不会被自动登录"的模式下打印到终端（窗口/浏览器模式已带进去，没必要再吐一次）
    desktop.stdout_banner(args.host, args.port, store.dir, store.dir / "admin_token.txt",
                          token if mode == "server" else "", external=external)
    desktop.say(f"  启动方式   {why}")

    if mode == "server":
        _LISTEN_STATE["loop_owner"] = "main"      # 主线程持有循环（切监听时不用另起线程）
        # ★ 循环而非单次 serve_forever（V3.6.7）：`/api/v1/listen` 会在运行期换掉服务对象，
        #   旧对象的 serve_forever 会因此返回——那不是"该退出了"，要接着服务新的那个；
        #   只有返回时 `_LISTEN_SERVER` 还是自己，才说明是真正停止（Ctrl+C 或外部 shutdown）。
        while True:
            current = _LISTEN_SERVER
            if current is None:
                break
            try:
                current.serve_forever(poll_interval=0.3)
            except KeyboardInterrupt:
                break
            if _LISTEN_SERVER is current:
                break
        desktop.say("[控制面] 已停止")
        try:
            if _LISTEN_SERVER is not None:
                _LISTEN_SERVER.server_close()
        except Exception:
            pass
        return 0

    # 窗口 / 浏览器：HTTP 放后台线程，主线程让给 GUI（pywebview 的循环必须在主线程）
    _LISTEN_STATE["loop_owner"] = "background"
    desktop.serve_in_background(httpd)
    try:
        if mode == "browser":
            if not desktop.open_browser(url):
                # 无控制台时这行 print 是看不见的 → 必须弹窗，否则用户只看到"双击没反应"
                desktop.notify("隐盾控制台", f"未能自动打开浏览器，请手动访问：\n{url}")
            desktop.say("[控制面] 服务运行中，按 Ctrl+C 停止")
            while True:
                time.sleep(0.5)
        else:
            desktop.say("[控制面] 控制台窗口已打开（关闭窗口即退出服务）")
            if not desktop.open_window(url):
                # 兜底：没有 WebView2/桌面环境时退回浏览器，并明确说明发生了什么
                opened = desktop.open_browser(url)
                desktop.say("[控制面] 已在默认浏览器中打开控制台")
                desktop.notify(
                    "隐盾控制台 · 已改用浏览器打开",
                    ("本机没有可用的 WebView2 桌面窗口运行时，控制台已在系统浏览器中打开。\n\n"
                     f"地址：{url}\n"
                     "关闭浏览器标签不会停止服务：服务继续在后台运行，"
                     "要停止请在任务管理器中结束本程序。\n\n"
                     f"日志：{desktop.log_file_hint()}") if opened else
                    ("未能打开控制台窗口，也未能自动打开浏览器。\n\n"
                     f"请手动访问：{url}\n日志：{desktop.log_file_hint()}"))
                while True:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        desktop.say("[控制面] 已停止")
        httpd.shutdown()
        httpd.server_close()
    return 0


def main(argv: Optional[list] = None) -> int:
    """启动入口（带"失败了也要让人看见"的兜底）。

    ★ 为什么必须有这层：控制端 exe 是**无控制台**的窗口形态（V3.6.7）。这时一旦启动失败
      （端口被占、数据目录不可写、WebView2 缺失…），没有任何终端会显示 traceback——
      用户看到的就是"双击了，什么也没发生"。所以这里统一兜住异常，用系统弹窗说清
      失败原因 + 日志路径，再以非 0 退出码收场。
    """
    try:
        return _run(argv)
    except SystemExit:
        raise                       # argparse 的用法错误：照它自己的语义退出
    except KeyboardInterrupt:
        return 0
    except Exception as exc:                                        # noqa: BLE001
        import traceback
        detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        desktop.say(f"[控制面] 启动失败：{detail}")
        try:
            from yindun.utils.logger import get_logger
            get_logger("control").error("启动失败：%s", detail, exc_info=True)
        except Exception:
            pass
        desktop.notify("隐盾控制台启动失败",
                       f"{detail}\n\n常见原因：{args_hint()}\n\n日志：{desktop.log_file_hint()}",
                       error=True)
        return 2


def args_hint() -> str:
    """失败时给出最可能的可照做建议（无控制台时这是用户唯一能看到的线索）。"""
    return ("① 8787 端口已被占用（换个端口或先关掉旧实例）；"
            "② 工作目录没有写权限（换成有权限的目录再运行）；"
            "③ 杀毒软件拦截了本程序")


if __name__ == "__main__":
    raise SystemExit(main())
