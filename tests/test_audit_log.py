# -*- coding: utf-8 -*-
"""
隐盾全链路审计黑匣子 - 功能验证测试
验证项：
1. AuditLog 单例与持久化
2. 8种事件类型记录
3. 哈希链生成与校验（防篡改）
4. log_privacy_batch 批量方法
5. log_llm_input/output preview 参数
6. 筛选查询
7. 统计概览
8. JSON 报告导出
9. HTML 报告导出
10. 防篡改检测（篡改后校验失败）
"""
import os
import sys
import json
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

# ★ 审计 HMAC 密钥与链锚点都落在【用户目录】(%APPDATA%/Yindun)。测试必须把它们也隔离到
#   临时目录：否则跑一次测试就会往真实用户目录写锚点（也可能动到真实密钥）。
_TEST_APPDATA = os.path.join(tempfile.gettempdir(), "yindun_test_audit_appdata")
if os.path.exists(_TEST_APPDATA):
    shutil.rmtree(_TEST_APPDATA, ignore_errors=True)
os.makedirs(_TEST_APPDATA, exist_ok=True)
os.environ["APPDATA"] = _TEST_APPDATA

# 临时审计日志目录，测试后清理
_TEST_AUDIT_DIR = os.path.join(tempfile.gettempdir(), "yindun_test_audit")
os.environ["YINDUN_AUDIT_TEST_DIR"] = _TEST_AUDIT_DIR

# 清理旧测试数据
if os.path.exists(_TEST_AUDIT_DIR):
    shutil.rmtree(_TEST_AUDIT_DIR)
os.makedirs(_TEST_AUDIT_DIR, exist_ok=True)

# 动态修改 AuditLog 的存储路径（通过 monkey patch）
from yindun.core.audit_log import AuditLog, AuditEventType, AuditSeverity
import yindun.core.audit_log as _al          # 用于打桩锚点候选落点（隔离真实用户目录/程序目录）

# 强制重新初始化单例，指向测试目录
AuditLog._instance = None
_orig_init = AuditLog.__init__
def _patched_init(self):
    if hasattr(self, '_initialized') and self._initialized:
        return
    self._entries = []
    from pathlib import Path
    self._storage_path = Path(_TEST_AUDIT_DIR)
    self._storage_path.mkdir(exist_ok=True)
    self._current_session_id = None
    import threading
    self._lock = threading.RLock()  # ★ 与生产 __init__ 保持一致，否则 add_entry 的 with self._lock 报错
    # ★ 锚点运行态（与生产 __init__ 一致）
    self._anchor_write_state = {}
    self._anchor_write_signature = None
    self._anchor_read_path = None
    self._load_logs()
    self._initialized = True
AuditLog.__init__ = _patched_init

# ★ 锚点候选落点隔离：默认实现会回退到「程序目录/cache」与「用户主目录」——测试绝不能
#   往仓库或真实用户目录写锚点，这里统一换成临时目录，并且每条锚点用例都显式重设。
_ANCHOR_PRIMARY = os.path.join(_TEST_APPDATA, "Yindun")
_ANCHOR_FALLBACK = os.path.join(tempfile.gettempdir(), "yindun_test_anchor_fallback")


def _set_anchor_dirs(*dirs):
    """打桩锚点候选落点（顺序即优先级）。"""
    from pathlib import Path
    _al._anchor_dirs = lambda: [Path(d) for d in dirs]


def _default_anchor_dirs():
    _set_anchor_dirs(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)


def _clear_anchor_files(*dirs):
    """清掉指定目录里的锚点文件/占位目录（避免用例之间互相干扰）。"""
    from pathlib import Path
    for raw in dirs:
        directory = Path(raw)
        if not directory.is_dir():
            continue
        for path in directory.glob("audit_chain.anchor.*.json"):
            try:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink()
            except OSError:
                pass


_default_anchor_dirs()


def test_01_singleton_and_persistence():
    """测试1：单例与持久化"""
    a1 = AuditLog()
    a2 = AuditLog()
    assert a1 is a2, "AuditLog 应为单例"
    print("✅ 测试1通过: 单例模式正常")


def test_02_all_event_types():
    """测试2：8种事件类型记录"""
    audit = AuditLog()
    audit.set_session_id("test_session_001")

    # 1. 会话开始
    audit.log_session_start("test_session_001")
    # 2. LLM 输入（含 preview）
    audit.log_llm_input("你好，我的手机号是13800138000", has_privacy=True,
                        preview="你好，我的手机号是[PHONE_0]")
    # 3. 隐私检测（批量）
    audit.log_privacy_batch({"PHONE": 1, "NAME": 2}, "detected")
    # 4. 工具调用
    audit.log_tool_call("read_local_file", {"target_directory": "E:\\test"}, "E:\\test")
    # 5. 访问控制（通过）
    audit.log_access_control("读取文件(read_local_file)", "E:\\test", approved=True)
    # 6. 工具结果
    audit.log_tool_result("read_local_file", True, "文件内容...")
    # 7. 隐私脱敏
    audit.log_privacy_batch({"PHONE": 1}, "anonymized")
    # 8. LLM 输出
    audit.log_llm_output("这是模型的回答", preview="这是模型的回答")
    # 9. 隐私还原
    audit.log_privacy_batch({"PHONE": 1, "NAME": 2}, "restored")
    # 10. 会话结束
    audit.log_session_end()

    entries = audit.get_entries()
    event_types = {e.event_type for e in entries}
    expected_types = {"session_start", "llm_input", "privacy_sensitive",
                      "tool_call", "access_control", "tool_result", "llm_output",
                      "session_end"}
    assert expected_types.issubset(event_types), f"缺少事件类型: {expected_types - event_types}"
    print(f"✅ 测试2通过: 8种事件类型全部记录成功（共{len(entries)}条）")


def test_03_hash_chain_valid():
    """测试3：哈希链校验（正常状态应通过）"""
    audit = AuditLog()
    assert audit.verify_chain() is True, "正常哈希链应校验通过"
    print("✅ 测试3通过: 哈希链完整性校验通过")


def test_04_hash_chain_tamper_detection():
    """测试4：防篡改检测（篡改后应失败）"""
    audit = AuditLog()
    if len(audit._entries) < 2:
        print("⚠️ 测试4跳过: 日志条目不足")
        return
    # 备份原始内容
    orig_message = audit._entries[1].message
    # 篡改第2条日志的 message
    audit._entries[1].message = "【被篡改的内容】"
    tampered = audit.verify_chain()
    # 恢复
    audit._entries[1].message = orig_message
    assert tampered is False, "篡改后哈希链应校验失败"
    # 恢复后应再次通过
    assert audit.verify_chain() is True, "恢复后哈希链应再次通过"
    print("✅ 测试4通过: 篡改被成功检测（篡改后校验失败，恢复后通过）")


def test_05_preview_parameter():
    """测试5：preview 参数正确存储"""
    audit = AuditLog()
    entries = audit.get_entries({"event_type": "llm_input"})
    found_preview = False
    for e in entries:
        if e.details.get("preview"):
            found_preview = True
            break
    assert found_preview, "应至少有一条 llm_input 包含 preview"
    print("✅ 测试5通过: preview 参数正确存储")


def test_06_privacy_batch():
    """测试6：log_privacy_batch 批量方法"""
    audit = AuditLog()
    before_count = len(audit.get_entries({"event_type": "privacy_sensitive"}))
    audit.log_privacy_batch({"PHONE": 3, "EMAIL": 2, "NAME": 1}, "detected")
    after_count = len(audit.get_entries({"event_type": "privacy_sensitive"}))
    assert after_count == before_count + 1, "批量方法应增加1条日志"
    # 空统计不应增加
    audit.log_privacy_batch({}, "detected")
    assert len(audit.get_entries({"event_type": "privacy_sensitive"})) == after_count, "空统计不应增加日志"
    print("✅ 测试6通过: 批量方法正常（空统计不记录）")


def test_07_filter_query():
    """测试7：筛选查询"""
    audit = AuditLog()
    # 按事件类型筛选
    tool_calls = audit.get_entries({"event_type": "tool_call"})
    assert all(e.event_type == "tool_call" for e in tool_calls), "筛选结果应全部为 tool_call"
    # 按严重程度筛选
    criticals = audit.get_entries({"severity": "critical"})
    assert all(e.severity == "critical" for e in criticals), "筛选结果应全部为 critical"
    # 按会话筛选
    session_entries = audit.get_entries({"session_id": "test_session_001"})
    assert all(e.session_id == "test_session_001" for e in session_entries), "筛选结果应属于指定会话"
    print(f"✅ 测试7通过: 筛选查询正常（tool_call={len(tool_calls)}, critical={len(criticals)}, session={len(session_entries)}）")


def test_08_stats():
    """测试8：统计概览"""
    audit = AuditLog()
    stats = audit.get_stats()
    assert "total_entries" in stats
    assert "by_type" in stats
    assert "by_severity" in stats
    assert "by_session" in stats
    assert "chain_valid" in stats
    assert stats["chain_valid"] is True, "统计中链条应为有效"
    assert stats["total_entries"] > 0, "应有日志条目"
    print(f"✅ 测试8通过: 统计概览正常（总{stats['total_entries']}条，类型{len(stats['by_type'])}种）")


def test_09_json_report():
    """测试9：JSON 报告导出"""
    audit = AuditLog()
    report_json = audit.export_report("json")
    report = json.loads(report_json)
    assert "generated_at" in report
    assert "chain_valid" in report
    assert "stats" in report
    assert "entries" in report
    assert isinstance(report["entries"], list)
    # 保存到文件
    filepath = audit.save_report("json", "test_report.json")
    assert os.path.exists(filepath), "JSON 报告文件应存在"
    with open(filepath, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
    assert "entries" in loaded
    print(f"✅ 测试9通过: JSON 报告导出成功（{len(report['entries'])}条记录，文件: {filepath}）")


def test_10_html_report():
    """测试10：HTML 报告导出"""
    audit = AuditLog()
    report_html = audit.export_report("html")
    assert "<!DOCTYPE html>" in report_html
    assert "<table>" in report_html
    assert "隐盾安全审计报告" in report_html
    assert 'id="audit-q"' in report_html and 'id="audit-type"' in report_html
    assert "Content-Security-Policy" in report_html
    # 保存到文件
    filepath = audit.save_report("html", "test_report.html")
    assert os.path.exists(filepath), "HTML 报告文件应存在"
    file_size = os.path.getsize(filepath)
    assert file_size > 1000, "HTML 报告文件应大于1KB"
    print(f"✅ 测试10通过: HTML 报告导出成功（文件大小: {file_size} 字节）")


def test_11_persistence_reload():
    """测试11：持久化重载"""
    audit = AuditLog()
    count_before = len(audit.get_entries())
    # 添加一条新日志
    audit.log_tool_call("test_tool", {"arg": "value"}, "")
    count_after_add = len(audit.get_entries())
    assert count_after_add == count_before + 1, "添加日志后数量应+1"
    # 重置单例，模拟重启
    AuditLog._instance = None
    audit2 = AuditLog()
    count_after_reload = len(audit2.get_entries())
    assert count_after_reload == count_after_add, f"重载后日志数量应一致（{count_after_add} vs {count_after_reload}）"
    # 链条仍应有效
    assert audit2.verify_chain() is True, "重载后哈希链仍应有效"
    print(f"✅ 测试11通过: 持久化重载正常（重载后{count_after_reload}条，链条有效）")


def test_12_access_control_log():
    """测试12：访问控制审批记录"""
    audit = AuditLog()
    # 记录一次拒绝
    audit.log_access_control("删除文件(delete_local_file)", "E:\\system32", approved=False)
    # 记录一次通过
    audit.log_access_control("读取文件(read_local_file)", "E:\\docs", approved=True)
    ac_entries = audit.get_entries({"event_type": "access_control"})
    assert len(ac_entries) >= 2, "应至少有2条访问控制记录"
    # 拒绝应为 critical
    rejected = [e for e in ac_entries if not e.details.get("approved")]
    approved = [e for e in ac_entries if e.details.get("approved")]
    assert all(e.severity == "critical" for e in rejected), "拒绝应为 critical"
    assert all(e.severity == "security" for e in approved), "通过应为 security"
    print(f"✅ 测试12通过: 访问控制记录正常（拒绝={len(rejected)}, 通过={len(approved)}）")


def test_13_tool_result_contract():
    """测试13：log_tool_result 参数契约（第二参数必须为 bool，第三参数为结果文本）

    回归背景：agent_worker.py 曾把结果文本传入 success、True 传入 result，
    导致函数内部 result[:500] 对 bool 取下标抛 TypeError，成功的工具调用被覆盖为失败。
    """
    audit = AuditLog()
    # 正确调用：成功
    audit.log_tool_result("contract_tool", True, "✅ 成功读取文件内容示例")
    # 正确调用：失败
    audit.log_tool_result("contract_tool", False, "FileNotFoundError: no such file")
    tr_entries = audit.get_entries({"event_type": "tool_result"})
    mine = [e for e in tr_entries if e.details.get("tool_name") == "contract_tool"]
    assert len(mine) == 2, "应记录两条 tool_result"
    ok = next(e for e in mine if e.details.get("success") is True)
    bad = next(e for e in mine if e.details.get("success") is False)
    assert ok.severity == "info", "成功的 tool_result 应为 info 级别"
    assert bad.severity == "warning", "失败的 tool_result 应为 warning 级别"
    assert "成功读取" in ok.details.get("result", ""), "成功调用的结果文本应被完整记录"
    assert "FileNotFoundError" in bad.details.get("result", ""), "失败调用的错误文本应被完整记录"
    print("✅ 测试13通过: log_tool_result 契约正常（bool success + 结果文本 + 级别映射）")


def test_14_tool_result_caller_consistency():
    """测试14：静态扫描 agent_worker.py 中 log_tool_result 的调用点

    签名为 log_tool_result(tool_name: str, success: bool, result: str = "")。
    本测试断言所有调用点的第二实参都是布尔字面量，防止参数顺序再次写反。
    """
    import re
    # 仓库根目录 = tests/ 的上级（本测试文件可位于 tests/ 下，路径必须锚定根目录）
    caller_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "yindun", "worker", "agent_worker.py")
    with open(caller_path, "r", encoding="utf-8") as f:
        source = f.read()
    call_lines = [ln.strip() for ln in source.splitlines() if "log_tool_result(" in ln]
    assert call_lines, "agent_worker.py 中应存在 log_tool_result 调用"
    pattern = re.compile(r"log_tool_result\(\s*[^,()]+,\s*(True|False)\s*,")
    for ln in call_lines:
        assert pattern.search(ln), (
            f"log_tool_result 调用参数顺序疑似错误（第二实参必须是 True/False）: {ln}"
        )
    print(f"✅ 测试14通过: {len(call_lines)} 处 log_tool_result 调用点参数顺序均正确")


# ══════════════════════════════════════════════════════════════
# 链锚点加固（防"降级改写"与"尾部截断"）
#
# 背景：哈希链只能证明"中间没被改"。旧实现里 legacy（无 HMAC）标记是**从文件里读的**，
# 攻击者抹掉 hash_algo 再用无密钥 sha256 重算整链即可通过校验；尾部截断也无人察觉。
# 现在用 HMAC 锚点（条数 + 链头 + 历史 legacy 前缀长度）把这两条路堵上。
# ══════════════════════════════════════════════════════════════
def _fresh_chain(count: int = 5):
    """重建一条干净的链（清掉链文件与锚点后重新加载单例）。"""
    from pathlib import Path
    log = AuditLog()
    chain = Path(_TEST_AUDIT_DIR) / "audit_chain.json"
    anchor = log._anchor_path()
    for path in (chain, anchor):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    AuditLog._instance = None
    log = AuditLog()
    for i in range(count):
        log.add_entry(AuditEventType.TOOL_CALL, AuditSeverity.INFO, f"锚点测试事件{i}", {"i": i})
    return log


def _reload_singleton():
    AuditLog._instance = None
    return AuditLog()


def _chain_path():
    from pathlib import Path
    return Path(_TEST_AUDIT_DIR) / "audit_chain.json"


def test_15_anchor_created_and_outside_audit_dir():
    """测试15：正常写入会建立锚点，且锚点不在审计目录内（同盘就等于没做）"""
    log = _fresh_chain(5)
    detail = log.verify_chain_detail()
    assert detail["valid"] is True, f"正常链应校验通过：{detail}"
    assert detail["anchor"] == "verified", f"应已建立并校验锚点：{detail}"
    anchor = log._anchor_path()
    assert anchor.exists(), f"锚点文件应存在：{anchor}"
    assert anchor.parent != log._storage_path, (
        f"锚点必须放在密钥旁边（用户目录），不能与审计日志同盘：{anchor.parent} vs {log._storage_path}"
    )
    # 追加条目后锚点跟着刷新，仍应通过
    log.add_entry(AuditEventType.TOOL_CALL, AuditSeverity.INFO, "追加一条")
    assert _reload_singleton().verify_chain_detail()["valid"] is True, "追加后链应仍有效"
    print(f"✅ 测试15通过: 锚点已建立在 {anchor.parent.name}/（审计目录之外），追加后仍有效")


def test_16_downgrade_rewrite_is_detected():
    """测试16：抹掉 HMAC 标记 + 无密钥重算整链（降级改写）必须被判无效"""
    import hashlib
    from yindun.core.audit_log import AuditEntry, _canonical

    log = _fresh_chain(5)
    data = json.loads(_chain_path().read_text(encoding="utf-8"))
    prev = ""
    for item in data:
        item.pop("hash_algo", None)                       # 抹掉 HMAC 标记 → 变成 legacy
        entry = AuditEntry.from_dict(item)
        entry.entry_hash = hashlib.sha256(_canonical(entry, prev)).hexdigest()
        item["entry_hash"] = entry.entry_hash
        item["previous_hash"] = prev
        prev = entry.entry_hash
    _chain_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    detail = _reload_singleton().verify_chain_detail()
    assert detail["valid"] is False, f"降级改写必须被判无效，实际：{detail}"
    assert "旧版条目" in detail["reason"] or "降级" in detail["reason"], detail["reason"]
    print(f"✅ 测试16通过: 降级改写被识破 —— {detail['reason']}")


def test_17_tail_truncation_is_detected():
    """测试17：删掉尾部条目（掩盖最近行为）必须被判无效"""
    log = _fresh_chain(5)
    data = json.loads(_chain_path().read_text(encoding="utf-8"))
    _chain_path().write_text(json.dumps(data[:-2], ensure_ascii=False, indent=2), encoding="utf-8")

    detail = _reload_singleton().verify_chain_detail()
    assert detail["valid"] is False, f"尾部截断必须被判无效，实际：{detail}"
    assert "截断" in detail["reason"], detail["reason"]
    print(f"✅ 测试17通过: 尾部截断被识破 —— {detail['reason']}")


def test_18_anchor_tamper_is_detected():
    """测试18：改写锚点（把条数改小以掩盖截断）必须被判无效"""
    log = _fresh_chain(5)
    anchor = log._anchor_path()
    payload = json.loads(anchor.read_text(encoding="utf-8"))
    payload["count"] = 1                                   # 只改内容，不重签
    anchor.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    detail = _reload_singleton().verify_chain_detail()
    assert detail["valid"] is False, f"锚点被改写必须被判无效，实际：{detail}"
    assert "锚点" in detail["reason"], detail["reason"]
    print(f"✅ 测试18通过: 锚点改写被识破 —— {detail['reason']}")


def test_19_reanchor_after_explicit_confirmation():
    """测试19：锚点缺失（升级/拷贝审计目录）时判无效，显式确认后可重建"""
    log = _fresh_chain(5)
    # ★ 必须把**所有候选落点**的锚点都清掉（V3.6.11）：只删首选时，新逻辑会找到
    #   "另一路径上与当前链自洽的旧锚点"并判为**迁移**（这是 BUG-0001 要的区分），
    #   那属于预期行为、不是"锚点被删"的场景。这里模拟的是真正的"锚点全没了"。
    _clear_anchor_files(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)

    detail = _reload_singleton().verify_chain_detail()
    assert detail["valid"] is False and "锚点缺失" in detail["reason"], f"锚点缺失应判无效：{detail}"

    log = _reload_singleton()
    result = log.reanchor(reason="单元测试：确认接受当前链")
    assert result.get("ok") is True, f"链自洽时重建锚点应成功：{result}"
    detail = _reload_singleton().verify_chain_detail()
    assert detail["valid"] is True and detail["anchor"] == "verified", f"重建后应恢复有效：{detail}"
    print("✅ 测试19通过: 锚点缺失被判无效 → 显式重建后恢复有效（且重建动作本身写入审计）")



def test_20_fresh_run_does_not_alarm():
    """测试20：首次运行（没有任何锚点）不该报「疑似篡改」——组员评审 BUG-0001 的反面。"""
    _clear_anchor_files(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)
    _default_anchor_dirs()
    log = _al.AuditLog()
    log.add_entry(list(_al.AuditEventType)[0], _al.AuditSeverity.INFO, "首次运行")
    health = log.anchor_health()
    assert health.get("state") == "ok", health
    assert health.get("valid") is True, health
    assert health.get("degraded") is False, health
    assert "篡改" not in str(health.get("reason") or ""), health
    print(f"✅ 测试20通过: 首次运行锚点状态 ok / 无篡改告警 —— {health.get('path')}")


def test_21_migration_is_reported_as_migration_not_tamper():
    """测试21：审计目录迁移（当前指纹锚点缺失、但存在与本链自洽的旧指纹锚点）→ 应判「迁移」。

    这是 BUG-0001 的正面用例：合法迁移不能再被呈现成「疑似被篡改」，且必须给可操作指引。
    """
    import shutil as _shutil
    from pathlib import Path as _Path
    _clear_anchor_files(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)
    _set_anchor_dirs(_ANCHOR_PRIMARY)
    log = _al.AuditLog()
    log.add_entry(list(_al.AuditEventType)[0], _al.AuditSeverity.INFO, "迁移前的链")
    anchors = list(_Path(_ANCHOR_PRIMARY).glob("audit_chain.anchor.*.json"))
    assert anchors, "前置条件：应当已生成锚点"
    real = anchors[0]
    legacy = real.with_name("audit_chain.anchor.deadbeef.json")
    _shutil.copy2(real, legacy)          # 造出「旧路径指纹」的锚点
    real.unlink()                        # 当前指纹的锚点缺失
    health = _al.AuditLog().anchor_health()
    assert health.get("state") == "migrated", health
    reason = str(health.get("reason") or "")
    assert "迁移" in reason, health
    assert "重建" in reason, health      # 必须给出可操作指引
    print(f"✅ 测试21通过: 迁移态被判为 migrated 且给出重建指引 —— {reason[:56]}…")


def test_22_anchor_write_falls_back_and_reports_it():
    """测试22：首选位置不可写时回退到备用位置，并**如实上报实际落点**（BUG-0002）。"""
    from pathlib import Path as _Path
    _clear_anchor_files(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)
    # 首选指向一个不存在的盘符：目录都建不出来，写必然失败
    _set_anchor_dirs("Z:/definitely_no_such_dir", _ANCHOR_FALLBACK)
    try:
        log = _al.AuditLog()
        log.add_entry(list(_al.AuditEventType)[0], _al.AuditSeverity.INFO, "回退用例")
        health = log.anchor_health()
        assert health.get("fallback") is True, health
        assert health.get("degraded") is False, health
        actual = str(health.get("path") or "").replace("\\", "/")
        assert _ANCHOR_FALLBACK.replace("\\", "/") in actual, health
        assert list(_Path(_ANCHOR_FALLBACK).glob("audit_chain.anchor.*.json")), "备用位置里应当有锚点"
        print(f"✅ 测试22通过: 锚点回退到备用位置且如实上报 —— {health.get('path')}")
    finally:
        _default_anchor_dirs()

def test_23_verify_warning_dedup():
    """测试23：校验失败告警**进程内去重**（第三轮评审附带观察）。

    现场：同一次启动里同一条校验失败告警连续出现 4 次（多个消费者各自 verify + log）。
    规则：同一 reason 只在状态变化时记一次；校验成功后清账，所以"好了又坏"会重新告警。
    """
    import logging
    # 清掉所有候选落点的旧锚点：否则 test_22 在备用位置留下的高条数锚点会被
    # `_read_anchor` 选中（它取条数最大的），新链反而被判"尾部被截断"。
    _clear_anchor_files(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)
    log = _fresh_chain(5)
    original = log._entries[1].message
    log._entries[1].message = "【测试23：被篡改的内容】"

    records = []

    class _Capture(logging.Handler):
        def emit(self, record):        # noqa: D102
            records.append(record)

    handler = _Capture()
    logger = _al.log
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        for _ in range(4):
            detail = log.verify_chain_detail()
            assert detail["valid"] is False, detail
        warnings = [r for r in records if "校验失败" in r.getMessage()]
        assert len(warnings) == 1, f"同一 reason 连续 4 次只应写 1 条告警，实际 {len(warnings)}"

        # 结果变化：恢复 → 校验通过（并清掉记账）
        log._entries[1].message = original
        assert log.verify_chain_detail()["valid"] is True
        # 再次失败 → 必须重新告警（不能把"结果变化"吞掉）
        log._entries[1].message = "【测试23：再次被篡改】"
        assert log.verify_chain_detail()["valid"] is False
        warnings = [r for r in records if "校验失败" in r.getMessage()]
        assert len(warnings) == 2, f"状态变化后应再写 1 条告警，实际 {len(warnings)}"
        print("✅ 测试23通过: 校验失败告警去重（4 次→1 条；状态变化后再记 1 条）")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        log._entries[1].message = original


def test_24_llm_output_empty_marker():
    """测试24：产品兜底文案不是模型输出（empty_output 标记，BUG-0008）。"""
    _clear_anchor_files(_ANCHOR_PRIMARY, _ANCHOR_FALLBACK)
    log = _fresh_chain(2)
    log.log_llm_output("", preview="（模型本轮没有产出任何内容）", empty_output=True)
    entries = log.get_entries({"event_type": "llm_output"})
    assert entries, "应写入一条 llm_output"
    last = entries[-1]
    assert last.details.get("content_length") == 0, last.details
    assert last.details.get("empty_output") is True, last.details
    assert "没有产出" in str(last.details.get("preview") or ""), last.details
    # 正常输出不带该标记（不误伤既有语义）
    log.log_llm_output("正常回答", preview="正常回答")
    normal = log.get_entries({"event_type": "llm_output"})[-1]
    assert normal.details.get("content_length") == 4, normal.details
    assert "empty_output" not in normal.details, normal.details
    print("✅ 测试24通过: 兜底输出记 content_length=0 + empty_output=True；正常输出不受影响")


def test_25_html_escaping_and_time():
    """测试25：HTML 导出必须转义用户可控字段（防存储型 XSS），时间去掉 T/微秒。"""
    from yindun.core.audit_log import _audit_html_row

    class _Entry:
        timestamp = "2026-10-21T09:27:40.773380"
        event_type = "llm_output"
        severity = "info"
        message = '<img src=x onerror=alert(1)>'
        details = {"x": "<script>alert(2)</script>"}

    row = _audit_html_row(_Entry())
    assert "<img" not in row and "&lt;img" in row, row
    assert "<script>" not in row and "&lt;script&gt;" in row, row
    assert "2026-10-21 09:27:40" in row and "773380" not in row, row
    print("✅ 测试25通过: HTML 导出已转义 + 时间格式化为可读形式")


def test_26_audit_long_digit_masking():
    """测试26：审计预览对超长数字做掩码（非 Luhn 的形似卡号也不明文落盘）。

    V3.6.21：识别规则补齐后，"卡号"上下文里的数字会先被引擎认成
    BANKCARD_LOOKALIKE 并整段替换成占位符——这比"前6后4"更强，两种形态都算通过；
    断言的核心是**完整数字不得出现**。
    """
    from yindun.core.audit_log import _mask_text

    out = _mask_text("随手编的卡号 6222021234567893444 请核对")
    assert "6222021234567893444" not in out, out
    assert "BANKCARD_LOOKALIKE" in out or ("622202" in out and "3444" in out), out
    # 旧条目（修复前写入）在导出时也要被重新掩码，不能把历史明文再泄一次
    from yindun.core.audit_log import _audit_html_row

    class _OldEntry:
        timestamp = "2026-10-21T09:27:40.773380"
        event_type = "llm_output"
        severity = "info"
        message = "旧卡号 6222021234567893444"
        details = {"preview": "旧卡号 6222021234567893444"}

    row = _audit_html_row(_OldEntry())
    assert "6222021234567893444" not in row, row
    assert "BANKCARD_LOOKALIKE" in row or "622202******3444" in row, row
    print("✅ 测试26通过: 审计预览对超长数字做了掩码（占位符或前6后4，含旧条目导出）")

if __name__ == "__main__":
    print("=" * 60)
    print("隐盾全链路审计黑匣子 - 功能验证测试")
    print("=" * 60)
    print()


    tests = [
        test_01_singleton_and_persistence,
        test_02_all_event_types,
        test_03_hash_chain_valid,
        test_04_hash_chain_tamper_detection,
        test_05_preview_parameter,
        test_06_privacy_batch,
        test_07_filter_query,
        test_08_stats,
        test_09_json_report,
        test_10_html_report,
        test_11_persistence_reload,
        test_12_access_control_log,
        test_13_tool_result_contract,
        test_14_tool_result_caller_consistency,
        test_15_anchor_created_and_outside_audit_dir,
        test_16_downgrade_rewrite_is_detected,
        test_17_tail_truncation_is_detected,
        test_18_anchor_tamper_is_detected,
        test_19_reanchor_after_explicit_confirmation,
        test_20_fresh_run_does_not_alarm,
        test_21_migration_is_reported_as_migration_not_tamper,
        test_22_anchor_write_falls_back_and_reports_it,
        test_23_verify_warning_dedup,
        test_24_llm_output_empty_marker,
        test_25_html_escaping_and_time,
        test_26_audit_long_digit_masking,
    ]
    passed = 0
    failed = 0
    for i, test in enumerate(tests, 1):
        try:
            test()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"❌ 测试{i:02d}失败 [{test.__name__}]: {type(e).__name__}: {e}")

    print()
    print("=" * 60)
    print(f"测试结果: {passed}/{len(tests)} 通过, {failed} 失败")
    print("=" * 60)

    # 清理测试目录
    try:
        if os.path.exists(_TEST_AUDIT_DIR):
            shutil.rmtree(_TEST_AUDIT_DIR)
            print(f"已清理测试目录: {_TEST_AUDIT_DIR}")
    except Exception:
        pass

    sys.exit(0 if failed == 0 else 1)
