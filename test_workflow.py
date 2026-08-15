# -*- coding: utf-8 -*-
"""
协同工作流编排引擎 - 功能测试脚本
==================================
验证：
1. 模板加载与实例化
2. 审批链机制
3. 步骤执行与上下文传递
4. 进度查询与状态管理
"""
import sys
sys.path.insert(0, ".")

from yindun.core.workflow import (
    WorkflowEngine, WorkflowStep, WorkflowTemplate,
    StepStatus, ApprovalType, get_workflow_engine
)


def test_template_listing():
    """测试1: 列出所有可用模板"""
    print("=" * 60)
    print("🧪 测试1: 模板列表查询")
    print("=" * 60)

    engine = get_workflow_engine()
    templates = engine.list_templates()

    for t in templates:
        print(f"  📋 [{t['id']}] {t['name']}")
        print(f"     描述: {t['description']}")

    assert len(templates) >= 3, f"应至少有3个模板，实际: {len(templates)}"
    print(f"  ✅ 共发现 {len(templates)} 个业务模板\n")
    return True


def test_simple_execution():
    """测试2: 简单工作流执行（无审批步骤）"""
    print("=" * 60)
    print("🧪 测试2: 简单工作流执行 - 合同审查 (前3步)")
    print("=" * 60)

    engine = get_workflow_engine()
    instance = engine.create_instance("wf_contract_review", "test_contract_001")

    print(f"  🔖 实例ID: {instance.template_id}")
    print(f"  📝 模板: {instance.name}")

    # 执行前3个 AUTO 类型步骤
    steps = instance.steps
    for i in range(3):
        step = steps[i]
        print(f"\n  ▶ 执行步骤 {i+1}: {step.name}")
        print(f"      审批类型: {step.approval_type.value}")

        result = engine.execute_step("test_contract_001", step.step_id,
                                     context={"contract_path": "C:/test/contract.pdf"})

        if result["success"]:
            print(f"      ✅ 成功: {result['result']['status']}")
        else:
            print(f"      ❌ 失败: {result['error']}")

    # 查询状态
    status = engine.get_workflow_status("test_contract_001")
    progress = status["progress"]
    print(f"\n  📊 进度: {progress['completed']}/{progress['total']} ({progress['percentage']}%)")

    assert progress["completed"] == 3, f"应完成3步，实际: {progress['completed']}"
    print("  ✅ 无审批步骤执行测试通过\n")
    return True


def test_approval_chain():
    """测试3: 审批链机制"""
    print("=" * 60)
    print("🧪 测试3: 审批链机制 - 生成报告步骤")
    print("=" * 60)

    engine = get_workflow_engine()
    instance = engine.create_instance("wf_contract_review", "test_approval_001")

    # 先完成前3步
    for i in range(3):
        engine.execute_step("test_approval_001", instance.steps[i].step_id,
                            context={"contract_path": "C:/test/contract.pdf"})

    # 第4步需要审批
    step4 = instance.steps[3]
    print(f"\n  ▶ 步骤4: {step4.name}")
    print(f"      审批类型: {step4.approval_type.value} (MANUAL)")
    print(f"      预期: 需要等待审批")

    # 尝试执行，应被拦截
    result = engine.execute_step("test_approval_001", step4.step_id)
    assert not result["success"]
    assert result.get("needs_approval") == True
    print(f"      ✅ 审批拦截生效: {result['error']}")

    # 查询当前状态
    status = engine.get_workflow_status("test_approval_001")
    current = status["current_step"]
    assert current is not None
    assert current["status"] == StepStatus.WAITING_APPROVAL.value
    print(f"      ✅ 状态正确: {current['status']}")

    # 模拟人工审批通过
    print(f"\n  ✍️  模拟审批: 审核人=张三, 意见=同意生成")
    approved = engine.approve_step("test_approval_001", step4.step_id,
                                   reviewer="张三", comment="同意生成")
    assert approved, "审批操作应成功"
    print(f"      ✅ 审批通过")

    # 审批后可以执行
    result = engine.execute_step("test_approval_001", step4.step_id)
    assert result["success"]
    print(f"      ✅ 审批后执行成功")

    # 测试拒绝
    step5 = instance.steps[4]
    print(f"\n  ▶ 步骤5: {step5.name}")
    print(f"      审批类型: {step5.approval_type.value} (MANUAL)")

    engine.execute_step("test_approval_001", step5.step_id)  # 触发等待审批
    rejected = engine.reject_step("test_approval_001", step5.step_id,
                                  reviewer="李四", comment="暂不导出")
    assert rejected
    print(f"      ✅ 审批拒绝成功")

    # 验证进度
    status = engine.get_workflow_status("test_approval_001")
    progress = status["progress"]
    print(f"\n  📊 最终进度: {progress['completed']}/{progress['total']} ({progress['percentage']}%)")
    print(f"     等待审批: {progress['waiting_approval']}, 失败: {progress['failed']}")
    print("  ✅ 审批链机制测试通过\n")
    return True


def test_custom_workflow():
    """测试4: 自定义工作流创建"""
    print("=" * 60)
    print("🧪 测试4: 自定义工作流 - 数据处理流水线")
    print("=" * 60)

    template = WorkflowTemplate(
        template_id="custom_data_pipeline",
        name="数据处理流水线",
        description="数据采集 -> 清洗 -> 分析 -> 可视化"
    )
    template.add_step(WorkflowStep(
        name="采集数据",
        tool_name="fetch_data",
        tool_args={"source": "{{data_source}}"},
        approval_type=ApprovalType.AUTO,
    ))
    template.add_step(WorkflowStep(
        name="数据清洗",
        tool_name="clean_data",
        approval_type=ApprovalType.AUTO,
    ))
    template.add_step(WorkflowStep(
        name="数据分析",
        tool_name="analyze_data",
        approval_type=ApprovalType.AUTO,
    ))
    template.add_step(WorkflowStep(
        name="生成可视化",
        tool_name="generate_chart",
        approval_type=ApprovalType.MANUAL,
    ))

    # 注册模板到引擎
    engine = get_workflow_engine()
    engine._templates["custom_data_pipeline"] = template

    # 创建实例并执行
    instance = engine.create_instance("custom_data_pipeline", "custom_001")
    print(f"  🔖 自定义实例: {instance.template_id}")

    # 执行前3步
    for i in range(3):
        step_id = instance.steps[i].step_id
        result = engine.execute_step("custom_001", step_id,
                                     context={"data_source": "api_server"})
        print(f"  ▶ 步骤{i+1} [{instance.steps[i].name}]: {'✅' if result['success'] else '❌'}")

    # 第4步需要审批
    step4_id = instance.steps[3].step_id
    engine.execute_step("custom_001", step4_id)
    status = engine.get_workflow_status("custom_001")
    current = status["current_step"]
    assert current is not None
    print(f"  ▶ 步骤4 [{current['name']}]: 等待审批 ({current['status']})")

    # 审批通过并执行
    engine.approve_step("custom_001", step4_id, reviewer="admin", comment="OK")
    result = engine.execute_step("custom_001", step4_id)
    assert result["success"]
    print(f"  ✅ 审批后执行成功")

    print("  ✅ 自定义工作流测试通过\n")
    return True


def test_workflow_export():
    """测试5: 工作流数据导出"""
    print("=" * 60)
    print("🧪 测试5: 工作流状态导出")
    print("=" * 60)

    engine = get_workflow_engine()
    instance = engine.create_instance("wf_weekly_report", "export_test")

    # 执行第一步
    step_id = instance.steps[0].step_id
    engine.execute_step("export_test", step_id)

    # 导出JSON
    json_data = engine.export_workflow_data("export_test")
    assert json_data is not None

    import json
    data = json.loads(json_data)
    print(f"  📦 导出数据大小: {len(json_data)} 字符")
    print(f"  📋 模板名称: {data['workflow']['name']}")
    print(f"  📊 步骤数量: {len(data['workflow']['steps'])}")
    print(f"  📈 进度: {data['status']['progress']['percentage']}%")

    # 打印状态摘要
    print("\n  步骤详情:")
    for step in data['workflow']['steps']:
        status_icon = {
            "pending": "⏳", "waiting_approval": "🔒", "approved": "✅",
            "executing": "🔄", "completed": "✅", "failed": "❌", "skipped": "⏭"
        }.get(step['status'], "❓")
        print(f"    {status_icon} {step['name']} [{step['status']}]")

    print("\n  ✅ 导出功能测试通过\n")
    return True


def main():
    print("\n" + "🧪" * 30)
    print("  隐盾 V3.2 - 协同工作流编排引擎 功能测试")
    print("🧪" * 30 + "\n")

    tests = [
        ("模板列表查询", test_template_listing),
        ("简单工作流执行", test_simple_execution),
        ("审批链机制", test_approval_chain),
        ("自定义工作流", test_custom_workflow),
        ("工作流数据导出", test_workflow_export),
    ]

    results = []
    for name, test_fn in tests:
        try:
            passed = test_fn()
            results.append((name, "PASS" if passed else "FAIL"))
        except Exception as e:
            print(f"  ❌ 异常: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, f"ERROR: {e}"))

    print("=" * 60)
    print("📊 测试结果汇总")
    print("=" * 60)

    all_pass = True
    for name, status in results:
        icon = "✅" if status == "PASS" else "❌"
        print(f"  {icon} {name}: {status}")
        if status != "PASS":
            all_pass = False

    print("=" * 60)
    if all_pass:
        print("  🎉 所有测试通过！工作流引擎已就绪。")
    else:
        print("  ⚠️  部分测试未通过，请检查。")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
