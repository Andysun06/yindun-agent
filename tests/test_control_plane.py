# -*- coding: utf-8 -*-
"""隐盾控制面 · 回归测试（链路校验 / 只收紧不变式 / 不泄明文 / 鉴权 / 存储）。

这套件锁住的是控制面的**安全性质**，不是功能表面：
  1. 链校验：截断、同长度改写、缺口、断链、legacy 变短、自报锚点不可信 —— 逐种必须被发现；
  2. 只收紧：降级键与未知键必须被拒；合并的四条单调不变式与幂等性；
  3. 不泄明文：**即使上游审计层出问题**，出网前那道脱敏也必须把明文挡在本机；
  4. 鉴权与存储：令牌只存哈希、未鉴权一律 401、未登记端点不许上报、策略历史不可覆盖；
  5. 边界：静态资源目录穿越、非白名单扩展名、超大请求体。

用法：python tests/test_control_plane.py   （退出码 0 = 全部通过）
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from yindun.control import policy as policy_mod                      # noqa: E402
from yindun.control.agent_reporter import ControlReporter             # noqa: E402
from yindun.control.models import EndpointReport, ReportError, _TS_RE  # noqa: E402
from yindun.control.server import ApiError, ControlServer             # noqa: E402
from yindun.control.store import ControlStore                         # noqa: E402
from yindun.control.verify import ChainSnapshot, verify_report        # noqa: E402

failures: list[str] = []
TMP = Path(tempfile.mkdtemp(prefix="yindun_control_test_"))


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def H(token: str) -> dict:
    return {"authorization": f"Bearer {token}"}


def call(app: ControlServer, method: str, path: str, headers=None, query=None, body=None):
    """直接调用路由层（等价于 HTTP，便于断言状态码而不起端口）。"""
    try:
        return app.route(method, path, query or {}, headers or {},
                         json.dumps(body).encode() if body is not None else None)
    except ApiError as exc:
        return exc.status, {"error": exc.message}


# ══════════════════════════════════════════════════════════════════
#  夹具
# ══════════════════════════════════════════════════════════════════

def make_event(seq: int, prev: str, event_type: str = "tool_call", severity: str = "info",
               message: str = "读取 [FILE_PATH_0_ab12]", legacy: bool = False) -> dict:
    h = f"{seq:064x}"          # 确定性假哈希（够用且可复现）
    return {"ts": "2026-10-16T09:00:00+08:00", "event_type": event_type, "severity": severity,
            "message": message, "details": {"tool": "read_file"},
            "entry_hash": h, "previous_hash": prev, "legacy": legacy}


def make_report(seqs, prev_head="", total=None, head=None, first_seq=None, anchor="verified",
                legacy_prefix=0, capabilities=None, usage=None) -> dict:
    events, prev = [], prev_head
    for seq in seqs:
        events.append(make_event(seq, prev))
        prev = f"{seq:064x}"
    return {
        "schema": 1, "endpoint_id": "ep-test-01",
        "reported_at": "2026-10-16T09:00:00+08:00", "version": "3.5.0",
        "chain": {"total_count": total if total is not None else len(list(seqs)),
                  "head_hash": head if head is not None else (events[-1]["entry_hash"] if events else ""),
                  "legacy_prefix": legacy_prefix, "anchor_state": anchor,
                  "first_seq": first_seq if first_seq is not None else (list(seqs)[0] if seqs else 0)},
        "events": events,
        "capabilities": capabilities or {"models": ["qwen2.5:7b"], "external_models": [],
                                         "plugins": [], "strict_mode": False, "platform": "Windows-11"},
        "usage": usage or [],
    }


# ══════════════════════════════════════════════════════════════════
section("【A】入参校验：类型、长度、条数、时间戳")
# ══════════════════════════════════════════════════════════════════
good = make_report([1, 2])
rep = EndpointReport.from_dict(good)
check("合法上报可解析", rep.endpoint_id == "ep-test-01" and len(rep.events) == 2)
check("事件类型对齐内核枚举（小写蛇形）", rep.events[0].event_type == "tool_call")

dirty = make_report([1])
dirty["events"][0]["event_type"] = "some_new_type"
check("未知事件类型归入 OTHER_*（前向兼容、不污染界面）",
      EndpointReport.from_dict(dirty).events[0].event_type.startswith("OTHER_"))

dirty = make_report([1])
dirty["events"][0]["severity"] = "很奇怪"
check("未知级别降级为 info", EndpointReport.from_dict(dirty).events[0].severity == "info")

dirty = make_report([1])
dirty["events"][0]["message"] = "x" * 3000
try:
    EndpointReport.from_dict(dirty); long_msg_ok = False
except ReportError:
    long_msg_ok = True
check("超长 message 整包拒绝", long_msg_ok)

dirty = make_report([1])
dirty["events"][0]["entry_hash"] = "ZZZ"
try:
    EndpointReport.from_dict(dirty); bad_hash_ok = False
except ReportError:
    bad_hash_ok = True
check("非法哈希整包拒绝", bad_hash_ok)

dirty = make_report([1])
dirty["reported_at"] = "昨天"
try:
    EndpointReport.from_dict(dirty); bad_ts_ok = False
except ReportError:
    bad_ts_ok = True
check("非法时间戳整包拒绝", bad_ts_ok)

dirty = make_report(list(range(1, 502)))
try:
    EndpointReport.from_dict(dirty); big_ok = False
except ReportError:
    big_ok = True
check("单批超 500 条拒绝", big_ok)

dirty = make_report([1])
dirty["events"][0]["details"] = {"nested": [1, 2, 3], "big": "y" * 900}
d = EndpointReport.from_dict(dirty).events[0].details
check("details 只留一层且截断", d["nested"] == "<list len=3>" and len(d["big"]) == 500,
      str(d))

dirty = make_report([1])
dirty["schema"] = 99
try:
    EndpointReport.from_dict(dirty); schema_ok = False
except ReportError:
    schema_ok = True
check("不支持的 schema 版本拒绝", schema_ok)

# ══════════════════════════════════════════════════════════════════
section("【B】审计链外部锚定：六种删改手法逐种必须被发现")
# ══════════════════════════════════════════════════════════════════
snap = None
r1 = verify_report(snap, EndpointReport.from_dict(make_report([1, 2, 3])))
check("首报只做锚定（不判高危）", [a.code for a in r1.alerts] == ["FIRST_REPORT"],
      str([a.code for a in r1.alerts]))
check("首报快照记录了条数与链头",
      r1.new_snapshot.total_count == 3 and r1.new_snapshot.head_hash == f"{3:064x}")
snap = r1.new_snapshot

r2 = verify_report(snap, EndpointReport.from_dict(make_report([4, 5], prev_head=f"{3:064x}", total=5)))
check("正常增量：无高危告警且快照推进", r2.ok and not [a for a in r2.alerts if a.severity != "低"]
      and r2.new_snapshot.total_count == 5, str([a.code for a in r2.alerts]))
snap = r2.new_snapshot

r3 = verify_report(snap, EndpointReport.from_dict(make_report([1, 2], prev_head="", total=2)))
check("尾部截断被发现（TRUNCATED）", not r3.ok and "TRUNCATED" in r3.alert_codes(),
      str(r3.alert_codes()))
check("截断不降低已锚定的条数（锚点只增不减）", r3.new_snapshot.total_count == 5)

r4 = verify_report(snap, EndpointReport.from_dict(
    make_report([4, 5], prev_head=f"{3:064x}", total=5, head=f"{999:064x}")))
check("同长度改写链头被发现（HEAD_MISMATCH/REWRITTEN）",
      not r4.ok and ({"HEAD_MISMATCH", "REWRITTEN"} & set(r4.alert_codes())), str(r4.alert_codes()))

r5 = verify_report(snap, EndpointReport.from_dict(
    make_report([8, 9], prev_head=f"{7:064x}", total=9, first_seq=8)))
check("中间区间未上报被发现（GAP）", not r5.ok and "GAP" in r5.alert_codes(), str(r5.alert_codes()))

r6 = verify_report(snap, EndpointReport.from_dict(
    make_report([6, 7], prev_head=f"{99:064x}", total=7)))
check("批内断链被发现（BROKEN_LINK）", not r6.ok and "BROKEN_LINK" in r6.alert_codes(),
      str(r6.alert_codes()))

r7 = verify_report(snap, EndpointReport.from_dict(make_report([], total=5, head=f"{5:064x}")))
check("空批不影响锚定", r7.ok and r7.new_snapshot.total_count == 5)

r8 = verify_report(snap, EndpointReport.from_dict(
    make_report([], total=5, head=f"{5:064x}", legacy_prefix=0)))
check("legacy 前缀归零不报警（与上次同为 0）", r8.ok, str(r8.alert_codes()))

snap_legacy = ChainSnapshot(5, f"{5:064x}", 3, "", "")
r9 = verify_report(snap_legacy, EndpointReport.from_dict(
    make_report([], total=5, head=f"{5:064x}", legacy_prefix=1)))
check("legacy 前缀变短被发现（LEGACY_SHRINK）", not r9.ok and "LEGACY_SHRINK" in r9.alert_codes(),
      str(r9.alert_codes()))

r10 = verify_report(snap, EndpointReport.from_dict(
    make_report([], total=5, head=f"{5:064x}", anchor="tampered")))
check("端点自报锚点不可信被发现（ANCHOR_TAMPERED）",
      not r10.ok and "ANCHOR_TAMPERED" in r10.alert_codes(), str(r10.alert_codes()))

r11 = verify_report(snap, EndpointReport.from_dict(
    make_report([4, 5], prev_head=f"{3:064x}", total=5, first_seq=4)))
check("重复上报只记低危（不冤枉端点重试）",
      all(a.severity == "低" for a in r11.alerts), str([(a.code, a.severity) for a in r11.alerts]))

r12 = verify_report(snap, EndpointReport.from_dict(make_report([6], prev_head=f"{5:064x}", total=6)))
check("正常上报不产生高危告警", r12.ok and not [a for a in r12.alerts if a.severity != "低"],
      str(r12.alert_codes()))

# 高危事件本身要进告警流（运维在一个视图里看到"链有问题"和"有高危操作"）
rep_high = make_report([6], prev_head=f"{5:064x}", total=6)
rep_high["events"][0]["severity"] = "critical"
r13 = verify_report(snap, EndpointReport.from_dict(rep_high))
check("端点高危事件进入告警流（HIGH_EVENT）", "HIGH_EVENT" in r13.alert_codes(), str(r13.alert_codes()))
check("高危事件不改变链自洽结论", r13.ok)

# ══════════════════════════════════════════════════════════════════
section("【C】策略：降级键必拒 + 只收紧的单调不变式")
# ══════════════════════════════════════════════════════════════════
FORBIDDEN = [{"anonymize": False}, {"exempt": ["PHONE"]}, {"bypass": True},
             {"auto_approve": ["write_file"]}, {"whitelist": ["rm -rf /"]},
             {"disable_audit": True}, {"approval_rules": []}, {"allow_script_exec": True}]
bad_pass = []
for bundle in FORBIDDEN:
    try:
        policy_mod.validate_keys(bundle); bad_pass.append(bundle)
    except policy_mod.PolicyError:
        pass
check(f"{len(FORBIDDEN)} 个降级键全部被拒", not bad_pass, str(bad_pass))
try:
    policy_mod.validate_keys({"whatever_new_key": 1}); unknown_ok = False
except policy_mod.PolicyError:
    unknown_ok = True
check("未知键被拒（不给未来语义留后门）", unknown_ok)

SECRET = "s3cret-token"
bundle = {"version": 1, "sensitive_paths": ["D:/薪资/*"], "approval_floor": "confirm"}
bundle["sig"] = policy_mod.sign(bundle, SECRET)
check("正确密钥验签通过", policy_mod.verify_bundle(bundle, SECRET)[0])
check("错误密钥验签失败", not policy_mod.verify_bundle(bundle, "other")[0])
tampered = dict(bundle); tampered["approval_floor"] = "approve"
check("改包体后验签失败（防中间人降级）", not policy_mod.verify_bundle(tampered, SECRET)[0])
unsigned = {"version": 1, "note": "no sig"}
check("无签名的包被拒", not policy_mod.verify_bundle(unsigned, SECRET)[0])

# 单调性：四条不变式
base = policy_mod.empty_local()
base.update({"sensitive_paths": ["A", "B"], "approval_floor": "confirm",
             "allowed_models": ["m1", "m2"], "allowed_plugins": ["p1"], "require_strict_mode": False})
remote = {"sensitive_paths": ["C"], "approval_floor": "deny",
          "allowed_models": ["m2", "m3"], "allowed_plugins": ["p1", "p2"],
          "require_strict_mode": True}
merged = policy_mod.merge_tighten(base, remote)
check("敏感路径取并集（只增不减）", set(merged["sensitive_paths"]) == {"A", "B", "C"},
      str(merged["sensitive_paths"]))
check("审批下限取更严（confirm → deny）", merged["approval_floor"] == "deny")
check("允许模型取交集（只缩不扩）", merged["allowed_models"] == ["m2"], str(merged["allowed_models"]))
check("允许插件取交集", merged["allowed_plugins"] == ["p1"], str(merged["allowed_plugins"]))
check("严格模式取或（可被打开）", merged["require_strict_mode"] is True)
check("合并结果单调（自检函数认可）", policy_mod.is_tightening_only(base, merged))
check("幂等：同一包合并两次结果一致",
      policy_mod.merge_tighten(merged, remote)["sensitive_paths"] == merged["sensitive_paths"])

# 反向：远端试图"放宽"时必须无效
weaker = {"sensitive_paths": [], "approval_floor": "approve", "allowed_models": ["m1", "m2", "m9"]}
merged2 = policy_mod.merge_tighten(base, weaker)
check("远端给 approve 不能降低 confirm", merged2["approval_floor"] == "confirm")
check("远端给更长允许清单不能扩集", set(merged2["allowed_models"]) <= {"m1", "m2"})
check("远端给空路径不能清空本地路径", set(merged2["sensitive_paths"]) >= {"A", "B"})

# 幂等与版本
check("策略版本取较大者", policy_mod.merge_tighten(
    {"policy_version": 7}, {"version": 3})["policy_version"] == 7)

# ══════════════════════════════════════════════════════════════════
section("【D】服务端：鉴权、存储、令牌只存哈希、策略历史")
# ══════════════════════════════════════════════════════════════════
store = ControlStore(TMP / "control.db")
app = ControlServer(store)
admin = store.ensure_admin_token()
check("管理令牌可验证", store.verify_admin(admin))
check("错误管理令牌被拒", not store.verify_admin("nope"))
check("令牌明文不落库（数据库里只有哈希）",
      admin not in (TMP / "control.db").read_bytes().decode("utf-8", "ignore"))

check("未鉴权访问概览 → 401", call(app, "GET", "/api/v1/overview")[0] == 401)
check("错误令牌访问概览 → 401", call(app, "GET", "/api/v1/overview", H("bad"))[0] == 401)

st, enrolled = call(app, "POST", "/api/v1/enroll", H(admin),
                    body={"name": "测试端点", "endpoint_id": "ep-test-01"})
check("登记端点成功", st == 200 and enrolled["endpoint_id"] == "ep-test-01")
ep_token, policy_key = enrolled["token"], enrolled["policy_key"]
check("端点令牌不落库", ep_token not in (TMP / "control.db").read_bytes().decode("utf-8", "ignore"))

st, _ = call(app, "POST", "/api/v1/report", H("bad"), body=make_report([1, 2]))
check("错误端点令牌上报 → 401", st == 401)
st, _ = call(app, "POST", "/api/v1/report", H(admin), body=make_report([1, 2]))
check("管理令牌不能冒充端点上报 → 401", st == 401)

st, res = call(app, "POST", "/api/v1/report", H(ep_token), body=make_report([1, 2, 3]))
check("端点上报成功且返回续传序号", st == 200 and res["next_seq"] == 4, str(res))
st, res2 = call(app, "POST", "/api/v1/report", H(ep_token),
                body=make_report([4, 5], prev_head=f"{3:064x}", total=5))
check("增量上报推进锚定", st == 200 and res2["next_seq"] == 6)

st, ov = call(app, "GET", "/api/v1/overview", H(admin))
check("概览统计与上报一致", ov["endpoints"] == 1 and ov["events"] == 5, str(ov))
st, eps = call(app, "GET", "/api/v1/endpoints", H(admin))
check("端点列表带链状态", eps["endpoints"][0]["chain_verdict"] == "已锚定",
      str(eps["endpoints"][0]["chain_verdict"]))
check("首次上报后『首次锚定』时间必须落地（登记行是空的，不能沿用空串）",
      bool(eps["endpoints"][0]["first_seen_at"]), str(eps["endpoints"][0]["first_seen_at"]))
check("端点列表带能力清单（模型）", "qwen2.5:7b" in eps["endpoints"][0]["models"])
check("端点列表带聚合用量描述", eps["endpoints"][0]["total_count"] == 5)

# 触发一次链告警，确认会被统计与展示
call(app, "POST", "/api/v1/report", H(ep_token), body=make_report([1], prev_head="", total=1))
st, ov = call(app, "GET", "/api/v1/overview", H(admin))
check("链告警被计入概览", ov["chain_alerts"] >= 1, str(ov))
st, eps = call(app, "GET", "/api/v1/endpoints", H(admin))
check("端点被标记为链异常", eps["endpoints"][0]["chain_verdict"] == "链异常")

st, err = call(app, "POST", "/api/v1/policy", H(admin),
               body={"scope": "global", "bundle": {"anonymize": False}})
check("降级策略包 → 400 且带原因", st == 400 and "anonymize" in err.get("error", ""), str(err))
st, hist = call(app, "GET", "/api/v1/policy", H(admin))
check("被拒的策略包不入库（历史仍为空）", not hist["history"], str(hist["history"]))

st, ok = call(app, "POST", "/api/v1/policy", H(admin),
              body={"scope": "global", "bundle": {"sensitive_paths": ["D:/薪资/*"],
                                                  "approval_floor": "confirm"}})
check("合法策略签发成功并带签名", st == 200 and len(ok["sig"]) == 16, str(ok))
st, ok2 = call(app, "POST", "/api/v1/policy", H(admin),
               body={"scope": "global", "bundle": {"require_strict_mode": True}})
check("再次下发版本递增", ok2["version"] == ok["version"] + 1, f"{ok['version']} → {ok2['version']}")
st, hist = call(app, "GET", "/api/v1/policy", H(admin))
check("策略历史保留每一条（不可覆盖）", len(hist["history"]) == 2, str(hist["history"]))

st, bundle = call(app, "GET", "/api/v1/policy", H(ep_token), {"endpoint_id": ["ep-test-01"]})
check("端点可取到策略包", st == 200 and bundle.get("sig"))
check("端点取到的包是全局+端点合并后的结果（含两次下发的并集）",
      bundle["sensitive_paths"] == ["D:/薪资/*"] and bundle["require_strict_mode"] is True,
      json.dumps(bundle, ensure_ascii=False))
ok_sig, reason = policy_mod.verify_bundle(bundle, policy_key)
check("端点用登记时拿到的密钥验签通过（闭环）", ok_sig, reason)

st, st_res = call(app, "GET", "/api/v1/self-test", H(admin))
check("服务端自检：所有合并案例单调", st_res["all_monotonic"])
check("服务端自检：所有降级包被拒", st_res["all_rejected"])

# ══════════════════════════════════════════════════════════════════
section("【E】上报器：默认关闭、出网只发掩码、失败不推进进度")
# ══════════════════════════════════════════════════════════════════
RAW_PHONE = "13812345678"
RAW_ID = "110101199003072316"
RAW_EMAIL = "zhang.wei@example.com"
RAW_PATH = "D:\\公司\\薪资\\2026Q2.xlsx"


class FakeEntry:
    def __init__(self, event_type="llm_input", severity="info", message="", details=None,
                 legacy=False, seq=1):
        # ★ 时间戳刻意用**真实审计条目的形态**：datetime.now().isoformat() 带微秒、不带时区
        #   （V3.6.0 之前这里写死 "2026-10-16T09:00:00"，掩盖了"服务端只认到秒"的格式冲突）
        self.timestamp = (datetime.now() if seq % 2 else datetime(2026, 10, 16, 9, 0, 0, 0)).isoformat()
        self.event_type = event_type
        self.severity = severity
        self.message = message
        self.details = details or {}
        self.entry_hash = f"{seq:064x}"
        self.previous_hash = f"{seq - 1:064x}" if seq > 1 else ""
        self.legacy = legacy


class FakeAudit:
    """替身审计日志：故意在 message/details 里放**明文 PII**（模拟上游出问题）。"""

    def __init__(self):
        self.entries = [
            FakeEntry("tool_call", "info", f"读取 {RAW_PATH}", {"target": RAW_PATH}, seq=1),
            FakeEntry("llm_input", "info", f"客户手机 {RAW_PHONE}，身份证 {RAW_ID}", seq=2),
            FakeEntry("llm_output", "info", f"已发送至 {RAW_EMAIL}", {"content_length": 120}, seq=3),
            FakeEntry("access_control", "critical", f"拒绝写入 {RAW_PATH}", seq=4),
        ]
        self.traces = []

    def get_entries(self, filters=None):
        return list(self.entries)

    def verify_chain_detail(self, allow_missing_anchor=False):
        return {"valid": True, "total": len(self.entries), "legacy": 0, "legacy_prefix": 0,
                "anchor": "verified", "reason": ""}

    def add_entry(self, *args, **kwargs):
        self.traces.append((args, kwargs))


cfg = TMP / "config" / "control.json"
audit = FakeAudit()
reporter = ControlReporter(config_path=cfg, audit=audit, settings={"model": "qwen2.5:7b"})
check("首次使用默认不启用上报", not reporter.enabled)
res = reporter.send()
check("未启用时 send() 明确报错而不是静默失败", (not res.ok) and "未启用" in res.error, res.error)

reporter.configure("http://127.0.0.1:9/", "ep-test-01", ep_token, policy_key, enabled=True)
check("配置后启用", reporter.enabled)
raw_cfg = cfg.read_text(encoding="utf-8")
check("令牌与签名密钥加密落盘（配置文件里没有明文）",
      ep_token not in raw_cfg and policy_key not in raw_cfg)
check("配置文件里没有明文 PII", RAW_PHONE not in raw_cfg)

payload, _ = reporter.build_report()
blob = json.dumps(payload, ensure_ascii=False)
check("上报体不含任何明文 PII（手机/身份证/邮箱/路径）",
      not any(x in blob for x in (RAW_PHONE, RAW_ID, RAW_EMAIL, RAW_PATH, "薪资")), blob[:200])
check("上报体确实带上了占位符（说明是脱敏而非丢弃）",
      "[PHONE_" in blob or "[IDCARD_" in blob or "[EMAIL_" in blob or "[SENSITIVE_PATH_" in blob)
check("上报体带链声明与续传序号",
      payload["chain"]["total_count"] == 4 and payload["chain"]["first_seq"] == 1, str(payload["chain"]))
check("上报体带能力清单", payload["capabilities"]["models"] is not None)
check("用量为端点级聚合（不编造按模型归因）",
      payload["usage"] and payload["usage"][0]["model"] == "(端点合计)", str(payload["usage"]))

# ★ 回归（V3.6.0 冒烟实测出来的真缺陷）：把端点自己生成的 payload **回灌给服务端校验器**。
#   教训：以前只验了"payload 字段齐、脱敏了"，没让服务端的 EndpointReport 真正解析它——
#   于是审计时间戳带微秒（datetime.isoformat()）而服务端正则只认到秒，**真实上报必然 400**，
#   套件却一路绿灯。现在把这条链路钉死：端点产出 → 服务端解析 → 校验通过。
from yindun.control.models import EndpointReport as _EndpointReport  # noqa: E402
try:
    _parsed = _EndpointReport.from_dict(payload)
    _parse_error = ""
except Exception as exc:                                              # noqa: BLE001
    _parsed, _parse_error = None, f"{type(exc).__name__}: {exc}"
check("端点产出的上报体能被服务端校验器接受（时间戳格式两端一致）",
      _parsed is not None, _parse_error)
check("微秒级/带时区的时间戳都在服务端格式白名单内",
      _EndpointReport.from_dict(make_report([1])).chain.total_count == 1
      and bool(_TS_RE.match("2026-10-16T13:00:24.889339"))
      and bool(_TS_RE.match("2026-10-16T13:00:24.889+08:00"))
      and bool(_TS_RE.match("2026-10-16T13:00:24Z")),
      "服务端时间戳正则不接受小数秒/时区")
check("非法时间戳仍然被拒（放宽没有变成不校验）",
      not _TS_RE.match("2026-10-16T13:00:24.889339 随便写") and not _TS_RE.match("昨天下午"))

# 失败不应推进进度
before = reporter._config.get("last_sent_count", 0)
bad = reporter.send()
check("连不通时报错", not bad.ok and bad.error)
check("失败不推进上报进度（下次会重发而不是丢条目）",
      reporter._config.get("last_sent_count", 0) == before)
check("失败也在本机留痕（审计里能看到）", any("控制面" in str(t) for t in audit.traces))

# 验签失败的策略包必须被拒（失败关闭）
reporter._config["policy_key"] = "wrong-key"
merged_policy, reason = reporter.fetch_policy()
check("策略包验签失败时拒绝并保留原策略", merged_policy is None and "拒绝" in reason, reason)

# 冒名（服务端用错误密钥签发的包）也不该被接受
reporter._config["policy_key"] = policy_key
bad_bundle = {"version": 1, "sensitive_paths": ["X"], "sig": policy_mod.sign({"version": 1}, "attacker")}
from yindun.control import verify as _verify  # noqa: E402  仅为断言 ALERT_CODES 完整
check("告警码表覆盖验证器产出的全部代码",
      all(a.code in _verify.ALERT_CODES for a in
          verify_report(None, EndpointReport.from_dict(make_report([1]))).alerts))

# ══════════════════════════════════════════════════════════════════
section("【F】边界：静态资源与请求体")
# ══════════════════════════════════════════════════════════════════
from yindun.control.server import MAX_BODY, WEB_DIR  # noqa: E402
check("控制台静态资源随模块分发（index.html / styles.css / app.js 齐备）",
      all((WEB_DIR / f).is_file() for f in ("index.html", "styles.css", "app.js")))
check("前端不引用任何外部资源（离线可用）",
      "http://" not in (WEB_DIR / "index.html").read_text(encoding="utf-8").replace(
          "http://127.0.0.1:8787", "") and "https://" not in (WEB_DIR / "index.html").read_text(encoding="utf-8"))
check("请求体上限存在且合理", 1024 * 1024 <= MAX_BODY <= 8 * 1024 * 1024)
check("上报路由不接受未知接口", call(app, "GET", "/api/v1/nope", H(admin))[0] == 404)

# ══════════════════════════════════════════════════════════════════
section("【G】并发：仪表盘并行请求不得打爆存储")
# ══════════════════════════════════════════════════════════════════
# 背景：初版"单连接 + 读不加锁"在串行 curl 下一切正常，但控制台一次并行发 4 个请求时
# 读出 `sqlite3.InterfaceError: bad parameter or other API misuse`（表现为登录后立刻 401）。
# 现改为线程局部连接；本组用例把"并发读"钉住，防止回退。
import threading                                                       # noqa: E402

errors: list[str] = []


def hammer(fn, times=6):
    def run():
        for _ in range(times):
            try:
                fn()
            except Exception as exc:                                   # noqa: BLE001
                errors.append(f"{type(exc).__name__}: {exc}")
    return run


threads = [threading.Thread(target=hammer(lambda: call(app, "GET", "/api/v1/overview", H(admin))))
           for _ in range(4)]
threads += [threading.Thread(target=hammer(lambda: call(app, "GET", "/api/v1/endpoints", H(admin))))
            for _ in range(4)]
threads += [threading.Thread(target=hammer(lambda: call(app, "GET", "/api/v1/events?limit=50", H(admin))))
            for _ in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()
check("12 线程 × 6 轮并发读全部无异常（线程局部连接生效）", not errors, str(errors[:3]))

write_errors: list[str] = []


def write_worker(i: int):
    def run():
        for j in range(3):
            try:
                st, _ = call(app, "POST", "/api/v1/report", H(ep_token),
                             body=make_report([1000 + i * 10 + j], prev_head="", total=1))
            except Exception as exc:                                   # noqa: BLE001
                write_errors.append(f"{type(exc).__name__}: {exc}")
    return run


writers = [threading.Thread(target=write_worker(i)) for i in range(4)]
for t in writers:
    t.start()
for t in writers:
    t.join()
check("4 线程并发写上报无异常（写锁串行化生效）", not write_errors, str(write_errors[:3]))


# ══════════════════════════════════════════════════════════════════
section("【H】闭环实测：控制面下发 → 端点本地真的生效（真实 HTTP + 真实策略引擎）")
# ══════════════════════════════════════════════════════════════════
# 这一组回答的是"控制端和本地 agent 到底连没连上、策略到底生不生效"：
# 起真实 HTTP 控制面 → 生成配对码 → 本地 agent 用码接入 → 上报 → 下发策略 →
# **断言本地策略引擎的判定真的变了**（这是"生效"的唯一证据，配置里存着不算）。
import threading                                                      # noqa: E402
import urllib.error                                                  # noqa: E402
import urllib.request                                                 # noqa: E402

from yindun.app.agent_service import AgentService                     # noqa: E402
from yindun.app.session_store import SessionStore                     # noqa: E402
from yindun.app.settings_store import SettingsStore                   # noqa: E402
from yindun.control.server import serve as serve_control              # noqa: E402
from yindun.core.policy_manager import PolicyManager                  # noqa: E402

E2E = TMP / "e2e"
E2E.mkdir(parents=True, exist_ok=True)
# ★ 必须先建策略引擎（单例）：配置目录只能在首次实例化时钉住，
#   否则测试会写进本机真实 config/policy.json。
pm = PolicyManager(config_dir=str(E2E / "policy"))
pm.clear_control_rules()

e2e_store = ControlStore(E2E / "control.db")
httpd = serve_control("127.0.0.1", 8799, e2e_store, verbose=False)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8799"
e2e_admin = e2e_store.ensure_admin_token()


def http_json(method: str, path: str, body=None, token: str = ""):
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "Authorization": "Bearer " + token})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        resp = opener.open(req, timeout=10)
    except urllib.error.HTTPError as exc:      # 4xx/5xx 也是"结果"，交回给断言判断
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8")), dict(exc.headers)
        except Exception:
            return exc.code, raw, dict(exc.headers)
    with resp:
        raw = resp.read()
        for ctype in ("application/json", "text/csv"):
            if resp.headers.get("Content-Type", "").startswith(ctype):
                return resp.status, (json.loads(raw.decode("utf-8")) if ctype == "application/json"
                                     else raw.decode("utf-8-sig")), dict(resp.headers)
        return resp.status, raw, dict(resp.headers)


# ① 沙箱内的对象：只读白名单 + 沙箱内 → 未下发策略时"自动放行"
from yindun.core.policy_manager import SANDBOX_ROOT                    # noqa: E402
normal_file = str(Path(SANDBOX_ROOT) / "docs" / "readme_probe.txt")
sensitive_file = str(Path(SANDBOX_ROOT) / "合同" / "甲方合同.txt")
check("前置：沙箱内只读操作未下发策略时自动放行（对照基线）",
      pm.check("read_local_file", normal_file) == "approve", pm.check("read_local_file", normal_file))

# ② 控制台生成配对码（企业常规路径：IT 发码，员工机自助接入）
st, code_info, _ = http_json("POST", "/api/v1/pair-code", {"ttl_minutes": 30, "note": "E2E"}, e2e_admin)
check("管理端能生成配对码", st == 200 and len(code_info.get("code", "")) == 6, str(code_info))

# ③ 本地 agent 用配对码接入（不持有管理令牌）
svc = AgentService(settings=SettingsStore(path=E2E / "cfg.json"),
                   sessions=SessionStore(path=E2E / "sess.json"),
                   plugin_data_root=E2E / "plugins")
svc.control_config_path = E2E / "control.json"        # 不碰本机真实配置
svc._control_audit = audit                            # 复用本套件前面的假审计（不写本机审计目录）
paired = svc.control_pair(BASE, code_info["code"], "E2E 财务机")
check("用配对码接入成功（无需管理令牌）", paired.get("ok"), str(paired))
check("接入后默认**不启用**上报（绝不静默上报）", svc.control_status()["enabled"] is False)

st, again, _ = http_json("POST", "/api/v1/enroll",
                         {"code": code_info["code"], "name": "重放", "endpoint_id": "ep-replay"})
check("配对码一次性：重放被拒", st == 403, f"{st} {again}")

# ④ 启用上报 → 同步（上报 + 取策略 + 落地）
enabled = svc.control_set_enabled(True)
check("启用上报并完成首次同步", enabled.get("ok") and enabled.get("sync", {}).get("report", {}).get("ok"),
      str(enabled.get("sync", {}).get("report")))
st, ov, _ = http_json("GET", "/api/v1/overview", None, e2e_admin)
check("控制面已看到该端点与事件", ov["endpoints"] == 1 and ov["events"] > 0, str(ov))

# ⑤ 下发"敏感路径"策略 → 断言**本地判定真的变了**
st, pushed, _ = http_json("POST", "/api/v1/policy", {
    "scope": "global",
    "bundle": {"sensitive_paths": [str(Path(SANDBOX_ROOT) / "合同" / "*")],
               "note": "E2E 合同目录收紧"}}, e2e_admin)
check("下发敏感路径策略成功", st == 200 and pushed.get("version") == 1, str(pushed))
sync2 = svc.control_sync()
check("端点同步并应用策略", sync2.get("policy", {}).get("applied", {}).get("ok"), str(sync2.get("policy")))
check("★ 生效证据①：合同目录的只读操作被收紧为需审批",
      pm.check("read_local_file", sensitive_file) == "confirm",
      pm.check("read_local_file", sensitive_file))
check("★ 生效证据②：普通目录不受影响（是精准收紧，不是一刀切）",
      pm.check("read_local_file", normal_file) == "approve",
      pm.check("read_local_file", normal_file))
rules_after_first = len(pm.get_control_rules())
svc.control_sync()
check("★ 幂等：重复同步不会累积重复规则",
      len(pm.get_control_rules()) == rules_after_first, f"{rules_after_first} → {len(pm.get_control_rules())}")

# ⑥ 下发"审批下限=confirm" → 连只读自动放行也被冻结
http_json("POST", "/api/v1/policy", {"scope": "global", "bundle": {"approval_floor": "confirm"}}, e2e_admin)
svc.control_sync()
check("★ 生效证据③：审批下限生效——只读自动放行被冻结",
      pm.check("read_local_file", normal_file) == "confirm", pm.check("read_local_file", normal_file))

# ⑦ 应急熔断：审批下限=deny
http_json("POST", "/api/v1/policy", {"scope": "global", "bundle": {"approval_floor": "deny"}}, e2e_admin)
svc.control_sync()
check("★ 生效证据④：熔断生效——一切操作直接拒绝",
      pm.check("create_local_file", normal_file) == "deny" and pm.check("read_local_file", normal_file) == "deny")

# ⑧ 远程强制严格模式
http_json("POST", "/api/v1/policy", {"scope": "global", "bundle": {"require_strict_mode": True}}, e2e_admin)
svc.control_sync()
check("★ 生效证据⑤：远程要求严格模式 → 本地设置与运行环境都关掉解释器",
      svc.settings.get("allow_script_exec") is False and os.environ.get("ALLOW_SCRIPT_EXEC") == "0",
      f"{svc.settings.get('allow_script_exec')} / {os.environ.get('ALLOW_SCRIPT_EXEC')}")

# ⑨ 控制面视角：在线状态与策略漂移
st, eps2, _ = http_json("GET", "/api/v1/endpoints", None, e2e_admin)
ep = eps2["endpoints"][0]
check("控制面显示端点在线", ep["online"] is True, str(ep.get("online")))
check("控制面显示策略已同步（漂移为零）", ep["drift"] == "已同步", str(ep["drift"]))
check("端点上报了它自己看到的策略版本", ep["policy_have"] >= 1, str(ep["policy_have"]))

# ⑩ 审计导出（合规留档）：内容与界面一致，且不含明文
st, exported, headers = http_json("GET", "/api/v1/export?format=json", None, e2e_admin)
check("JSON 导出成功且声明了数据边界",
      st == 200 and isinstance(exported, dict) and "不含明文" in exported.get("data_boundary", ""), str(st))
blob = json.dumps(exported, ensure_ascii=False)
check("导出内容不含明文 PII（与界面所见一致）",
      not any(x in blob for x in (RAW_PHONE, RAW_ID, RAW_EMAIL, RAW_PATH)))
st, csv_text, headers2 = http_json("GET", "/api/v1/export?format=csv", None, e2e_admin)
check("CSV 导出可用且带下载文件名（Excel 直接打开）",
      st == 200 and "yindun-audit-" in headers2.get("Content-Disposition", "")
      and "时间" in csv_text[:40], str(headers2.get("Content-Disposition")))

# ⑪ 定时同步：能起、能停、不阻塞
started = svc.control_start_auto(30)
check("定时同步可启动（每 30 秒）", started.get("ok") and svc.control_status()["running"])
check("定时同步可停止", svc.control_stop_auto().get("ok") and not svc.control_status()["running"])

# ⑫ 断开与撤销：断开保留规则（不静默放松），显式撤销才移除
svc.control_set_enabled(False)
check("断开上报后本地规则仍在（不静默放松策略）",
      len(pm.get_control_rules()) > 0 and pm.check("read_local_file", normal_file) == "deny")
cleared = svc.control_clear_rules()
check("显式撤销后规则清空、判定回到基线",
      cleared.get("removed", 0) > 0 and pm.check("read_local_file", normal_file) == "approve",
      str(cleared))

# ── 【I】统计分析接口 + 桌面外壳的承载决策（V3.5.5）──────────
section("【I】统计分析（服务端聚合）与桌面外壳启动形态")

# ⑬ /api/v1/stats：需管理令牌，且数字必须与已上报的事件对得上
st, _unauth, _ = http_json("GET", "/api/v1/stats", None, "")
check("统计接口需要管理令牌（未鉴权 → 401）", st == 401, str(st))
st, stats, _ = http_json("GET", "/api/v1/stats?days=7", None, e2e_admin)
check("统计接口返回 200", st == 200, str(st))
check("趋势补齐每一天（缺事件的日期也要给 0，横轴不能压缩）",
      isinstance(stats.get("daily"), list) and len(stats.get("daily")) == 7,
      str(len(stats.get("daily") or [])))
check("趋势各天之和 = 累计事件数（服务端聚合，不是界面自己按当前页算）",
      sum(d["count"] for d in stats["daily"]) == stats["total_events"],
      f"{sum(d['count'] for d in stats['daily'])} vs {stats.get('total_events')}")
check("事件总数与总览口径一致（两处不能各说各话）",
      stats["total_events"] == ov["events"], f"{stats['total_events']} vs {ov['events']}")
check("类型分布按条数倒序、且条数之和 = 总数",
      bool(stats["by_type"]) and sum(t["count"] for t in stats["by_type"]) == stats["total_events"],
      str(stats["by_type"]))
check("级别构成覆盖已上报的级别且计数之和 = 总数",
      set(stats["by_severity"]) <= {"info", "warning", "critical", "security"}
      and sum(stats["by_severity"].values()) == stats["total_events"], str(stats["by_severity"]))
check("端点活跃度按条数倒序、且都是已登记的端点",
      all(stats["top_endpoints"][i]["count"] >= stats["top_endpoints"][i + 1]["count"]
          for i in range(len(stats["top_endpoints"]) - 1))
      and all(t["endpoint_id"] in {e["endpoint_id"] for e in eps2["endpoints"]}
              for t in stats["top_endpoints"]), str(stats["top_endpoints"]))
check("days 越界值被收敛（不接受的参数不能把服务打爆）",
      http_json("GET", "/api/v1/stats?days=99999", None, e2e_admin)[1].get("days") == 90
      and http_json("GET", "/api/v1/stats?days=abc", None, e2e_admin)[1].get("days") == 14)

# ⑭ stats() 的边界：空库也要给出可画的形状（不抛异常、不缺字段）
empty_store = ControlStore(Path(TMP) / "empty" / "control.db")
empty_stats = empty_store.stats()
check("空库时统计仍返回完整形状（14 根 0 柱，而不是报错）",
      len(empty_stats["daily"]) == 14 and all(d["count"] == 0 for d in empty_stats["daily"])
      and empty_stats["total_events"] == 0 and empty_stats["by_type"] == []
      and empty_stats["top_endpoints"] == [], str(empty_stats)[:140])
empty_store.close()

# ⑭b 令牌取回（★ 真事故回归）：第二次启动必须拿到**同一枚**单行 ASCII 令牌
#   旧实现把整份文件的注释头也算作令牌 → 窗口自动登录时 HTTP 头含中文与换行 →
#   浏览器抛 "non-ISO-8859-1 code point"，表现成"控制台登录不了"。
tok_store = ControlStore(Path(TMP) / "tokenfile" / "control.db")
first = tok_store.ensure_admin_token()
second = tok_store.ensure_admin_token()          # 模拟"第二次启动"
third = tok_store.ensure_admin_token()
check("首次生成的令牌是单行 ASCII（能放进 HTTP 头）",
      bool(first) and first.isascii() and chr(10) not in first, repr(first))
check("第二次启动取回的是同一枚令牌，且不含注释头/换行",
      second == first == third and second.isascii() and "#" not in second, repr(second))
check("令牌文件里确实有注释头（说明上面挡住的正是真会发生的情况）",
      "#" in (Path(TMP) / "tokenfile" / "admin_token.txt").read_text(encoding="utf-8"))
check("取回的令牌能真正通过鉴权（不是「看着像令牌」）", tok_store.verify_admin(second))
# 文件被手工改坏：宁可返回空（提示重置），也不交出一个注定失败的令牌
(Path(TMP) / "tokenfile" / "admin_token.txt").write_text("被手改坏了" + chr(10), encoding="utf-8")
check("令牌文件被改坏 → 返回空串（不交出注定鉴权失败的字符串）",
      tok_store.ensure_admin_token() == "")
tok_store.close()
# ⑮ 桌面外壳：承载方式决策 + 自动登录地址（不真开窗口，只验决策与拼地址）
from yindun.control import desktop  # noqa: E402
check("默认（回环监听）→ 开桌面窗口；对外监听 → 只起服务（服务器场景不该弹窗口）",
      desktop.plan_launch("127.0.0.1", "auto")[0] == "window"
      and desktop.plan_launch("0.0.0.0", "auto")[0] == "server")
check("显式开关优先于默认策略",
      desktop.plan_launch("0.0.0.0", "yes")[0] == "window"
      and desktop.plan_launch("127.0.0.1", "no")[0] == "server"
      and desktop.plan_launch("127.0.0.1", "browser")[0] == "browser")
check("窗口地址把令牌放在 fragment（不进服务器日志、不随 Referer 外发）",
      desktop.console_url("127.0.0.1", 8787, "tok") == "http://127.0.0.1:8787/#token=tok",
      desktop.console_url("127.0.0.1", 8787, "tok"))
check("监听 0.0.0.0 时窗口仍访问回环地址（监听地址本身不可访问）",
      desktop.console_url("0.0.0.0", 8787).startswith("http://127.0.0.1:8787/"))
check("纯服务模式不把令牌放进地址（终端打印，由人自己输）",
      desktop.console_url("127.0.0.1", 8787, "") == "http://127.0.0.1:8787/")
check("非单行/含中文的令牌不会进到地址里（HTTP 头只能承载 ISO-8859-1）",
      desktop.usable_token("abc") == "abc" and desktop.usable_token("a" + chr(10) + "b") == ""
      and desktop.usable_token("令牌") == "" and desktop.usable_token("  x  ") == "x")
check("控制台支持从 fragment 自动登录，并立刻从地址栏抹掉",
      "#token=" in Path("yindun/control/web/app.js").read_text(encoding="utf-8")
      and "history.replaceState" in Path("yindun/control/web/app.js").read_text(encoding="utf-8"))
check("控制台含统计分析页（导航项 + 渲染函数 + 服务端接口）",
      'data-page="stats"' in Path("yindun/control/web/index.html").read_text(encoding="utf-8")
      and "async function renderStats" in Path("yindun/control/web/app.js").read_text(encoding="utf-8")
      and "/api/v1/stats" in Path("yindun/control/server.py").read_text(encoding="utf-8"))


httpd.shutdown()
httpd.server_close()
e2e_store.close()

# 收尾
store.close()
print("\n" + "=" * 78)
if failures:
    print(f"❌ 控制面测试失败：{len(failures)} 项")
    for item in failures:
        print("   ·", item)
    sys.exit(1)
print(f"✅ 控制面测试全部通过（临时目录 {TMP}）")
sys.exit(0)
