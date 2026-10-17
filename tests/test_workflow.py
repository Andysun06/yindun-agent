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
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录，避免依赖当前工作目录

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
    """测试4: 自定义工作流创建 —— 数据处理流水线（真实采集→清洗→分析→出图）

    这条链路以前是"模拟"的（磁盘上不会多出任何文件，却显示采集到 42 条数据）；
    V3.6.0 起为真实实现，因此本测试改成**真验产物**：
      · 沙箱内放一份真 CSV → 采集步骤必须报出真实行数列数并落盘归一化 JSON；
      · 清洗步骤必须真去空行/去重复并报出删了多少；
      · 分析步骤必须写出 Markdown 统计（含数值列的均值）；
      · 出图步骤必须写真 SVG 文件；
      · 数据源不在沙箱内时，采集步骤必须**如实失败**（不假装成功）。
    """
    print("=" * 60)
    print("🧪 测试4: 自定义工作流 - 数据处理流水线（真实实现）")
    print("=" * 60)

    from pathlib import Path
    from yindun.core.file_tools import SANDBOX

    csv_path = Path(SANDBOX) / "_wf_pipeline_probe.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    # 落点受控：SANDBOX 为内核沙箱根目录，文件名固定，测试结束即删除
    csv_path.write_text(
        "部门,金额,备注\n"
        "研发,1200,正常\n"
        "市场,800,正常\n"
        "研发,1200,正常\n"          # 与第 2 行完全重复 -> 应被去掉
        ",,\n"                      # 空行 -> 应被去掉
        "市场,950,\n"
        "研发,1500,加急\n",
        encoding="utf-8")

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

    # 执行前3步（数据源就是上面那份真 CSV）
    results = []
    for i in range(3):
        step_id = instance.steps[i].step_id
        result = engine.execute_step("custom_001", step_id,
                                     context={"data_source": str(csv_path)})
        results.append(result)
        print(f"  ▶ 步骤{i+1} [{instance.steps[i].name}]: {'✅' if result['success'] else '❌'}"
              + ("" if result["success"] else f" —— {result.get('error')}"))
        assert result["success"], f"步骤「{instance.steps[i].name}」应成功：{result.get('error')}"

    fetch = results[0]["result"]
    assert fetch["row_count"] == 6, f"应读到 6 行（含 1 空行 1 重复），实际 {fetch['row_count']}"
    assert fetch["columns"][:2] == ["部门", "金额"], str(fetch["columns"])
    assert Path(fetch["data_path"]).exists(), f"归一化副本应落盘：{fetch['data_path']}"
    print(f"      ✅ 采集到真实数据: {fetch['summary']}")

    clean = results[1]["result"]
    assert clean["row_count"] == 4, f"清洗后应剩 4 行，实际 {clean['row_count']}"
    assert clean["dropped_empty"] == 1 and clean["dropped_duplicate"] == 1, str(clean)
    assert Path(clean["data_path"]).exists(), clean["data_path"]
    print(f"      ✅ 清洗真实生效: {clean['summary']}")

    analyze = results[2]["result"]
    assert Path(analyze["report_path"]).exists(), analyze["report_path"]
    report_text = Path(analyze["report_path"]).read_text(encoding="utf-8")
    assert "均值" in report_text and "4450" in report_text, report_text[:200]
    print(f"      ✅ 分析报告已落盘: {analyze['summary']}")

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
    assert result["success"], f"出图步骤应成功：{result.get('error')}"
    chart = result["result"]
    svg_path = Path(chart["chart_path"])
    assert svg_path.exists(), f"图表应落盘：{svg_path}"
    assert svg_path.suffix == ".svg" and "<svg" in svg_path.read_text(encoding="utf-8")
    print(f"      ✅ 图表真实生成: {chart['summary']}")

    # 数据源越界 = 如实失败（不假装成功）：沙箱外放一份真实存在的同名 CSV
    import tempfile
    outside_dir = Path(tempfile.mkdtemp(prefix="yindun_wf_outside_"))
    outside = outside_dir / "外部数据.csv"
    # 落点受控：系统临时目录（本测试自己创建），跑完即删
    outside.write_text("部门,金额\n研发,1\n", encoding="utf-8")
    try:
        bad = engine._execution_handlers["fetch_data"]({"source": str(outside)}, {})
        assert bad["ok"] is False and "沙箱" in bad["error"], str(bad)
        print(f"      ✅ 沙箱外数据源被拒绝: {bad['summary']}")
    finally:
        outside.unlink(missing_ok=True)

    csv_path.unlink(missing_ok=True)
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


def test_human_review():
    """测试6: 人工审核步骤 —— 记录真实审批人，未审批不放行，无留痕如实失败

    V3.6.0 之前这个处理器固定返回"已通过人工审核（模拟）"，谁批的、批了什么一律不记；
    现在必须来自引擎的审批门控（reviewer/review_comment），拿不到留痕就报错。
    """
    print("=" * 60)
    print("🧪 测试6: 人工审核步骤（真实审批留痕）")
    print("=" * 60)

    engine = get_workflow_engine()
    template = WorkflowTemplate(template_id="custom_review_probe",
                                name="人工审核探针",
                                description="单步人工审核")
    template.add_step(WorkflowStep(name="人工确认", tool_name="human_review", tool_args={},
                                   approval_type=ApprovalType.MANUAL))
    engine._templates["custom_review_probe"] = template
    engine.create_instance("custom_review_probe", "review_001")
    step_id = engine._instances["review_001"].steps[0].step_id

    blocked = engine.execute_step("review_001", step_id)
    assert not blocked["success"] and blocked.get("needs_approval") is True, str(blocked)
    status = engine.get_workflow_status("review_001")
    assert status["current_step"]["status"] == StepStatus.WAITING_APPROVAL.value, str(status["current_step"])
    print("      ✅ 未审批不放行")

    engine.approve_step("review_001", step_id, reviewer="王工", comment="数据无误，通过")
    done = engine.execute_step("review_001", step_id)
    assert done["success"], str(done)
    payload = done["result"]
    assert payload.get("reviewer") == "王工" and payload.get("comment") == "数据无误，通过", str(payload)
    assert "王工" in payload.get("summary", ""), payload.get("summary")
    print(f"      ✅ 结果带真实审批留痕: {payload['summary']}")

    bare = engine._execution_handlers["human_review"]({}, {})
    assert bare["ok"] is False and "审批" in bare["error"], str(bare)
    assert "模拟" not in str(bare), str(bare)
    print(f"      ✅ 无审批留痕时如实失败: {bare['summary']}")
    print("  ✅ 人工审核测试通过\n")
    return True


def _install_mock_handlers(engine):
    """为流程测试注入 mock 工具，避免依赖真实文件和远程 Ollama LLM。"""
    def _mock_read(args, context=None):
        return {"ok": True, "tool": "read_local_file", "path": args.get("path", ""),
                "file_content": "（测试合同文本）甲方/乙方/付款条款/违约条款/保密条款/交付条款/争议解决",
                "summary": "文件读取成功（mock）"}

    def _mock_analyze(args, context=None):
        focus = args.get("focus", "")
        if focus == "risks":
            return {"ok": True, "tool": "analyze_contract", "focus": focus,
                    "risks": [{"level": "低风险", "item": "测试风险", "detail": "mock"}],
                    "summary": "识别出 1 项风险点（mock）"}
        return {"ok": True, "tool": "analyze_contract", "focus": focus,
                "key_clauses": [{"type": "付款条款", "value": "mock 条款"}],
                "summary": "成功提取 1 项关键条款（mock）"}

    def _mock_analyze_project(args, context=None):
        return {"ok": True, "tool": "analyze_project", "target": args.get("target", ""),
                "result": "（测试代码结构）", "summary": "分析完成（mock）"}

    def _mock_security_scan(args, context=None):
        return {"ok": True, "tool": "security_scan", "findings": [], "summary": "扫描完成（mock）"}

    engine.register_execution_handler("read_local_file", _mock_read)
    engine.register_execution_handler("analyze_contract", _mock_analyze)
    engine.register_execution_handler("analyze_project", _mock_analyze_project)
    engine.register_execution_handler("security_scan", _mock_security_scan)


def main():
    print("\n" + "🧪" * 30)
    print("  隐盾 V3.2 - 协同工作流编排引擎 功能测试")
    print("🧪" * 30 + "\n")

    # 注入 mock 工具，让流程测试只验证编排/审批/导出的机制，
    # 不依赖真实文件路径和远程 Ollama LLM。
    # 实例持久化落点改到临时目录，避免把测试实例写进仓库根的运行时文件
    import tempfile as _tempfile
    from pathlib import Path as _Path
    _wf_engine = get_workflow_engine()
    _wf_engine._persistence_path = _Path(_tempfile.mkdtemp(prefix="yindun_wf_engine_")) / "workflow_instances.json"
    _install_mock_handlers(_wf_engine)

    tests = [
        ("模板列表查询", test_template_listing),
        ("简单工作流执行", test_simple_execution),
        ("审批链机制", test_approval_chain),
        ("自定义工作流", test_custom_workflow),
        ("工作流数据导出", test_workflow_export),
        ("人工审核留痕", test_human_review),
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
