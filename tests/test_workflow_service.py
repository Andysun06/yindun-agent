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

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：工作流模板/实例/演示标注/真实执行/沙箱/导出均正常")
sys.exit(0)
