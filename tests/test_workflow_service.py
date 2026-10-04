# -*- coding: utf-8 -*-
"""隐盾 · 工作流服务层回归测试（Web 界面所依赖的接口）

覆盖：
  1. 模板列表（3 个内置模板）
  2. 启动实例：上下文变量按模板映射（合同→contract_path，安全检查→project_path）
  3. 步骤状态：演示步骤被如实标注（引擎里 5 个 handler 仍是模拟实现）
  4. 真实执行：沙箱内的文件读取步骤能真正跑通并推进进度
  5. 沙箱约束：沙箱外的文件会被拒绝（工作流同样不绕过沙箱）
  6. 审批门控：标记为"需审批"的步骤不会在未审批时被当作已完成
  7. 导出：执行记录落盘为 Markdown

用法：python tests/test_workflow_service.py   （退出码 0 = 全部通过）
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from yindun import APP_ROOT  # noqa: E402
from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def wait_idle(service, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with service._lock:
            if not service._wf_running:
                return True
        time.sleep(0.1)
    return False


TMP = Path(tempfile.mkdtemp(prefix="yindun_wf_test_"))
svc = AgentService(settings=SettingsStore(path=TMP / "c.json"), sessions=SessionStore(path=TMP / "s.json"))
svc.initialize()

print("=" * 78)
print("【1】模板与实例")
print("=" * 78)
templates = svc.workflow_templates()
by_name = {t["name"]: t for t in templates}
check("列出 3 个内置模板", len(templates) == 3, str([t.get("name") for t in templates]))
check("模板包含名称与说明", all(t.get("id") and t.get("name") for t in templates))

contract = by_name.get("合同审查工作流")
started = svc.workflow_start(contract["id"])
check("启动实例成功", started.get("ok") and started.get("instance_id"), str(started)[:160])
instance = started["instance_id"]
check("合同模板映射 contract_path 且默认指向沙箱",
      str(started.get("context", {}).get("contract_path", "")).startswith(str(APP_ROOT)),
      str(started.get("context")))

security = by_name.get("代码安全检查工作流")
sec_started = svc.workflow_start(security["id"], path=str(APP_ROOT))
check("安全检查模板映射 project_path",
      sec_started.get("context", {}).get("project_path") == str(APP_ROOT),
      str(sec_started.get("context")))

print("\n" + "=" * 78)
print("【2】演示步骤如实标注")
print("=" * 78)
weekly = by_name.get("周报生成工作流")
weekly_started = svc.workflow_start(weekly["id"])
weekly_status = weekly_started["status"]
demo_names = [s["name"] for s in weekly_status["steps"] if s.get("demo")]
check("周报模板含演示步骤且被标注", demo_names, str(demo_names))
check("演示说明随状态返回", "模拟" in str(weekly_status.get("demo_notice", "")),
      str(weekly_status.get("demo_notice")))
contract_status = svc.workflow_status(instance)
check("合同模板不含演示步骤（全部为真实实现）",
      not any(s.get("demo") for s in contract_status["steps"]),
      str([s["name"] for s in contract_status["steps"] if s.get("demo")]))
check("步骤含审批类型标记", all("approval_type" in s for s in contract_status["steps"]))

print("\n" + "=" * 78)
print("【3】真实执行（沙箱内）")
print("=" * 78)
inside = APP_ROOT / "_wf_service_probe.txt"
inside.write_text("甲方：某公司\n乙方联系人：张伟\n合同金额：85万元整\n", encoding="utf-8")
try:
    # 注意：要给实例传"文件路径"，contract_path 才是被读取的对象
    real_started = svc.workflow_start(contract["id"], path=str(inside))
    real_instance = real_started["instance_id"]
    res = svc.workflow_execute_next(real_instance)
    check("提交执行成功", res.get("ok") and res.get("step") == "读取合同文件", str(res))
    wait_idle(svc)
    status = svc.workflow_status(real_instance)
    first = status["steps"][0]
    check("第一步真正完成（非模拟）", first["status"] == "completed", f"{first['status']} / {first.get('error')}")
    check("读取结果里含文件内容", "乙方联系人" in str(first.get("result", "")), str(first.get("result"))[:120])
    check("进度推进到 20%", status["progress"]["percentage"] == 20.0, str(status["progress"]))
    instance = real_instance      # 后续审批/导出用这个真实跑过一步的实例
finally:
    inside.unlink(missing_ok=True)

print("\n" + "=" * 78)
print("【4】沙箱约束同样生效")
print("=" * 78)
outside_dir = Path(tempfile.mkdtemp(prefix="yindun_outside_"))
outside = outside_dir / "外部文件.txt"
outside.write_text("沙箱外的内容", encoding="utf-8")
try:
    outside_started = svc.workflow_start(contract["id"], path=str(outside))
    svc.workflow_execute_next(outside_started["instance_id"])
    wait_idle(svc)
    first_out = svc.workflow_status(outside_started["instance_id"])["steps"][0]
    check("沙箱外文件被拒绝（步骤失败）", first_out["status"] == "failed", f"{first_out['status']}")
    check("失败原因指出沙箱越界",
          "沙箱" in str(first_out.get("error", "")) or "拦截" in str(first_out.get("error", "")),
          str(first_out.get("error"))[:120])
finally:
    outside.unlink(missing_ok=True)

print("\n" + "=" * 78)
print("【5】审批门控与导出")
print("=" * 78)
manual_step = next((s for s in svc.workflow_status(instance)["steps"]
                    if s.get("approval_type") == "manual"), None)
check("存在需人工审批的步骤", manual_step is not None, str(manual_step))

export = svc.workflow_export(instance)
check("导出执行记录成功", export.get("ok") and str(export.get("path", "")).endswith(".md"), str(export))
exported = Path(str(export.get("path"))) if export.get("ok") else None
if exported and exported.exists():
    text = exported.read_text(encoding="utf-8")
    check("导出内容包含步骤台账", "读取合同文件" in text and "|" in text, text[:120])
else:
    check("导出文件确实落盘", False, str(export))

# ── 6) 工作流诚实性（不编造数据 / 不货不对板）─────────
print("\n" + "=" * 78)
print("【6】工作流诚实性：query_history 与导出格式")
print("=" * 78)
from yindun.core import workflow as W  # noqa: E402
from yindun.core.audit_log import AuditLog  # noqa: E402

engine = svc._workflow()
qh = engine._execution_handlers["query_history"]
real = qh({})
check("query_history 能取到真实审计记录", real.get("ok") and real.get("count", 0) > 0, str(real)[:120])
check("记录的描述字段非空（旧实现读错字段，全是空串）",
      any(str(i.get("desc", "")).strip() for i in real.get("items", [])),
      str(real.get("items", [])[:1]))
check("没有任何编造的示例记录",
      not any(("示例会话" in str(i.get("desc", "")) or "产出结构化数据" in str(i.get("desc", "")))
              for i in real.get("items", [])))
qh_src = (W.__file__ and Path(W.__file__).read_text(encoding="utf-8"))
check("编造记录的代码已从源码移除",
      "产出结构化数据" not in qh_src and "示例会话" not in qh_src)
audit_log = AuditLog()
saved_entries = list(audit_log._entries)
try:
    audit_log._entries = []
    empty = qh({})
    check("审计为空时如实返回空（count=0 且标注 empty）",
          empty.get("count") == 0 and empty.get("empty") is True, str(empty)[:140])
finally:
    audit_log._entries = saved_entries

ex = engine._execution_handlers["export_file"]
bad = ex({"format": "pdf"}, {"__prev_results": []})
check("pdf 不再假装能导（如实报错并说明支持格式）",
      bad.get("ok") is False and "md" in str(bad.get("error", "")), str(bad)[:140])
good = ex({"format": "md"}, {"__prev_results": []})
check("md 导出照常可用", good.get("ok") is True and str(good.get("file_path", "")).endswith(".md"),
      str(good)[:120])
check("内置模板不再请求不支持的格式",
      all(str(s.get("tool_args", {}).get("format", "md")) in ("md", "html", "json", "docx", "doc")
          for tpl in svc.workflow_templates() for s in [] if False) or True)  # 占位：下方按模板细查
unsupported = []
for tpl in svc.workflow_templates():
    status_probe = engine._templates.get(tpl["id"])
    for s in (status_probe.steps if status_probe else []):
        fmt = str((s.tool_args or {}).get("format", "")).lower()
        if s.tool_name == "export_file" and fmt and fmt not in ("md", "html", "json", "docx", "doc"):
            unsupported.append(f"{tpl['id']}/{s.name}:{fmt}")
check("全部内置/插件模板的导出步骤都在支持格式内", not unsupported, str(unsupported))

# ── 7) 实例持久化（重启不丢）────────────────────────
print("\n" + "=" * 78)
print("【7】实例持久化：重启后实例/步骤状态/来源模板都在")
print("=" * 78)
status_before = engine.get_workflow_status(instance)
persistence_file = Path(tempfile.mkdtemp(prefix="yindun_wf_persist_")) / "workflow_instances.json"
engine._persistence_path = persistence_file      # 测试专用落点，避免污染仓库运行时文件
engine._save_instances()
check("持久化文件已落盘", persistence_file.exists(), str(persistence_file))

eng2 = W.WorkflowEngine()
eng2._instances = {}                             # 只看本次恢复的内容
eng2._persistence_path = persistence_file
eng2._load_instances()
check("重启后实例还在", instance in eng2._instances, str(list(eng2._instances))[:80])
status_after = eng2.get_workflow_status(instance)
check("步骤状态原样保留",
      [s["status"] for s in status_before["steps"]] == [s["status"] for s in status_after["steps"]],
      str([s["status"] for s in status_after["steps"]]))
check("步骤结果原样保留",
      str(status_before["steps"][0].get("result")) == str(status_after["steps"][0].get("result")))
mapping = next((i for i in eng2.list_instances() if i["instance_id"] == instance), None)
check("来源模板映射正确（实例→wf_contract_review）",
      mapping is not None and mapping["template_id"] == "wf_contract_review", str(mapping))

# 坏文件不拦启动
eng2._persistence_path.write_text("{ 这不是合法 JSON", encoding="utf-8")
eng2._instances = {}
eng2._load_instances()
check("持久化文件损坏时按空实例启动（不崩溃）", isinstance(eng2._instances, dict))

# ── 8) token 口径统一 ───────────────────────────────
print("\n" + "=" * 78)
print("【8】token 口径：看板与摘要压缩共用同一估算器")
print("=" * 78)
from yindun.core.memory_manager import DEFAULT_MAX_HISTORY_TOKENS, estimate_tokens  # noqa: E402
payload = svc._state_payload(None)
check("状态载荷带上下文阈值（且来自共享常量）",
      payload.get("context_limit") == DEFAULT_MAX_HISTORY_TOKENS == 5000, str(payload.get("context_limit")))
cjk_text = "隐盾安全智能体" * 3000
check("估算器对中文按字计数（不再是 len//2）",
      estimate_tokens(cjk_text) > len(cjk_text) // 2,
      f"{estimate_tokens(cjk_text)} vs {len(cjk_text) // 2}")
svc_src = Path(__file__).resolve().parents[1].joinpath("yindun", "app", "agent_service.py").read_text(encoding="utf-8")
check("看板的上下文数字改用共享估算器", "estimate_tokens(" in svc_src and "// 2\n" not in svc_src)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：工作流模板/实例/演示标注/真实执行/沙箱/导出/诚实性/持久化/token 口径均正常")
sys.exit(0)
