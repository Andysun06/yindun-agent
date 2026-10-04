/* ==========================================================================
   隐盾 · 界面开发预览用的假数据（DEV ONLY）
   --------------------------------------------------------------------------
   用途：不启动 App、不起后端，直接用浏览器/无头浏览器打开界面调样式与布局：
       file:///<repo>/yindun/app/web/index.html#mock
   安全：只有 URL 带 #mock 时才注入假桥接；正式运行（pywebview）永远不满足该条件，
         因此本文件在生产路径上是死代码。假数据均为公开测试样例（非真实个人信息）。
   ========================================================================== */
(() => {
  if (location.hash.indexOf("mock") < 0) return;

  const sessions = [
    { id: "s1", title: "客户合同隐私审查", created_at: "2026-10-03T10:02:11", updated_at: "2026-10-03T10:09:40", message_count: 6 },
    { id: "s2", title: "供应链合同要点提炼", created_at: "2026-10-02T15:20:03", updated_at: "2026-10-02T15:31:12", message_count: 4 },
    { id: "s3", title: "薪酬表字段核对", created_at: "2026-10-01T09:11:00", updated_at: "2026-10-01T09:14:22", message_count: 2 },
  ];

  const messages = [
    { role: "user", content: "帮我看看 assets 目录里的供应商合同，把乙方联系人和付款条款整理出来。" },
    {
      role: "assistant",
      content:
        "已用 read_local_file 读取《供应商服务合同_示例.md》，要点如下：\n\n" +
        "**乙方联系人**\n- 姓名：张伟\n- 电话：13812345678\n\n" +
        "**付款条款**\n1. 合同金额 85 万元整，签署后 15 个工作日内支付 30%\n2. 验收合格后 30 个工作日内支付尾款\n\n" +
        "```text\n注意：以上真实值仅在你的本机输出层还原，模型全程只见占位符\n```",
    },
    { role: "user", content: "把这段整理成一份可以归档的 md 报告，放到沙箱里。" },
    {
      role: "assistant",
      content: "已创建文件 `合同要点_20261003.md`（写入前经人工审批放行）。摘要如下：\n\n| 项目 | 内容 |\n| --- | --- |\n| 乙方联系人 | 张伟 |\n| 联系方式 | 13812345678 |\n| 合同金额 | 85 万元整 |\n| 付款节点 | 30% 预付 / 尾款验收后 30 日 |",
    },
  ];

  const noop = () => Promise.resolve(true);
  const attachments = [
    { name: "供应商服务合同_示例.pdf", chars: 18422, error: null },
    { name: "员工薪酬表_示例.csv", chars: 1260, error: null },
  ];
  const audit = {
    chain_ok: true,
    stats: { total_entries: 128, tool_calls: 23, privacy_events: 41, approvals: 6,
             llm_calls: 18, chain_valid: true,
             by_type: { tool_call: 23, privacy_sensitive: 41, access_control: 6, llm_input: 18 },
             by_severity: { info: 114, warning: 12, security: 2 } },
    entries: [
      { time: "2026-10-03T10:09:41", type: "privacy_restored", severity: "info", message: "输出层还原占位符 3 处（仅在本机内存还原）", preview: "", hash: "9f2c41ab77e0d315", prev: "41aa08c3d1b77e92" },
      { time: "2026-10-03T10:09:38", type: "llm_output", severity: "info", message: "模型输出已脱敏回填，长度 412", preview: "乙方联系人为 [NAME_0_7k2p]，联系电话 [PHONE_0_mx91]", hash: "41aa08c3d1b77e92", prev: "77de10aa9c3f4b20" },
      { time: "2026-10-03T10:09:12", type: "access_control", severity: "warning", message: "人工审批通过：创建文件（合同要点_20261003.md）", preview: "", hash: "77de10aa9c3f4b20", prev: "a0c1d5e2b6f83047" },
      { time: "2026-10-03T10:09:03", type: "tool_call", severity: "info", message: "调用工具 read_local_file（沙箱内）", preview: "", hash: "a0c1d5e2b6f83047", prev: "cd77b2190ae4f38a" },
      { time: "2026-10-03T10:08:55", type: "privacy_detected", severity: "info", message: "检测到敏感实体 5 类：NAME/PHONE/MONEY/BANKCARD/ADDRESS", preview: "", hash: "cd77b2190ae4f38a", prev: "2b90fa4c17de6a55" },
    ],
  };

  const kb = {
    available: true,
    embed_model: "nomic-embed-text",
    stats: { total_documents: 3, total_chunks: 5 },
    docs: [
      { name: "供应商服务合同_示例.md", chunks: 2 },
      { name: "隐盾产品说明_示例.md", chunks: 2 },
      { name: "员工薪酬表_示例.csv", chunks: 1 },
    ],
    error: null,
  };
  const plugins = [
    { id: "decision_hint", name: "审批决策提示", version: "0.1.0",
      description: "审批弹窗里给出一条提示：Agent 要执行的操作与你刚才的请求是否相符。用本地模型判断，数据不出本机。",
      source: "builtin", hooks: ["advisory_for_approval"],
      permissions: { network: "local-only", filesystem: "none" },
      enabled: true, error: null, missing: [], usable: true },
    { id: "laya_risk", name: "laya 风险分级（示例）", version: "0.2.1",
      description: "用 laya 决策模型给待审批操作分级。需要额外安装 torch 与 laya，属于按需安装的重依赖插件。",
      source: "user", hooks: ["advisory_for_approval"],
      permissions: { network: "none", filesystem: "read" },
      enabled: false, error: null,
      missing: ["缺少 Python 依赖：torch>=2.0（pip install torch）"], usable: false },
  ];

  const wfTemplates = [
    { id: "wf_contract_review", name: "合同审查工作流", description: "自动解析合同、提取关键条款、生成审查意见、导出报告" },
    { id: "wf_weekly_report", name: "周报生成工作流", description: "收集工作记录、整理结构、生成周报草稿、人工审核、导出" },
    { id: "wf_security_check", name: "代码安全检查工作流", description: "扫描代码仓库、识别安全漏洞、风险评估、生成报告" },
  ];
  const wfStatus = {
    instance_id: "5021f4c2", template_name: "合同审查工作流",
    is_complete: false, has_failed: false,
    progress: { total: 5, completed: 2, waiting_approval: 1, failed: 0, percentage: 40.0 },
    demo_notice: "标记为「演示」的步骤目前返回模拟结果，未接入真实数据源。",
    steps: [
      { step_id: "s1", name: "读取合同文件", description: "读取待审查的合同文档内容", tool_name: "read_local_file",
        approval_type: "auto", status: "completed", demo: false,
        result: "{'ok': True, 'tool': 'read_local_file', 'summary': '已读取《供应商服务合同_示例.md》（18422 字符）'}" },
      { step_id: "s2", name: "提取关键条款", description: "从合同中提取付款条款、违约条款、保密条款等关键信息",
        tool_name: "analyze_contract", approval_type: "auto", status: "completed", demo: false,
        result: "{'ok': True, 'summary': '已提取 6 条关键条款（本地模型）'}" },
      { step_id: "s3", name: "风险点分析", description: "识别合同中的潜在风险和不利条款",
        tool_name: "analyze_contract", approval_type: "auto", status: "waiting_approval", demo: false, result: null },
      { step_id: "s4", name: "生成审查意见", description: "基于分析结果生成专业的审查意见书",
        tool_name: "generate_report", approval_type: "manual", status: "pending", demo: false, result: null },
      { step_id: "s5", name: "导出PDF报告", description: "将审查意见导出为PDF文件",
        tool_name: "export_file", approval_type: "manual", status: "pending", demo: false, result: null },
    ],
  };

  const api = {
    bootstrap: () => Promise.resolve({
      version: "V3.3.2",
      settings: {
        model: "qwen2.5:7b-instruct", privacy: true,
        dark_mode: location.hash.indexOf("light") < 0, topmost: true,
        thinking_depth: 4, permission: "完全控制 (读/写/列表)", think_mode: "深度思考", frameless: true, sandbox: "E:\yindun-agent",
        ollama_models_cache: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"],
      },
      llm: {
        ready: true, model: "qwen2.5:7b-instruct",
        models: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"], error: null,
      },
      sessions, current_session_id: location.hash.indexOf("empty") >= 0 ? null : "s1",
    }),
    llm_status: () => Promise.resolve({ ready: true, model: "qwen2.5:7b-instruct", models: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"] }),
    list_sessions: () => Promise.resolve(sessions),
    open_session: (id) => Promise.resolve({ id, messages, box_mapping: {} }),
    new_session: () => Promise.resolve("s-new"),
    pick_files: () => Promise.resolve([]),
    list_attachments: () => Promise.resolve(attachments),
    clear_attachments: () => Promise.resolve(true),
    kb_status: () => Promise.resolve(kb),
    kb_pick_and_add: () => Promise.resolve({ ok: true, status: kb }),
    kb_add_paths: () => Promise.resolve({ ok: true, status: kb }),
    kb_remove: () => Promise.resolve({ ok: true, status: kb }),
    workflow_templates: () => Promise.resolve(wfTemplates),
    workflow_start: () => Promise.resolve({ ok: true, instance_id: "5021f4c2", status: wfStatus }),
    workflow_status: () => Promise.resolve(wfStatus),
    workflow_execute: () => Promise.resolve({ ok: true, step: "风险点分析" }),
    workflow_approve: () => Promise.resolve({ ok: true, status: wfStatus }),
    workflow_export: () => Promise.resolve({ ok: true, path: "workflow_reports/20261004_合同审查工作流_执行记录.md" }),
    plugins_list: () => Promise.resolve(plugins),
    plugin_set_enabled: () => Promise.resolve({ ok: true, plugins }),
    window_action: noop,
    audit_snapshot: () => Promise.resolve(audit),
    audit_export: () => Promise.resolve("（预览模式）audit_report_demo.json"),
    save_settings: noop, send: noop, cancel: noop, approve: noop,
    delete_session: noop, rename_session: noop,
  };

  window.__YINDUN_MOCK__ = { api };



  // 预览用：把附件挂到界面上（真实运行时由 pick_files → attachments 事件驱动）
  setTimeout(() => {
    window.yindun && window.yindun.onEvent("attachments", attachments);
  }, 300);

  // 预览审计面板：加 #mock-audit
  if (location.hash.indexOf("audit") >= 0) {
    setTimeout(() => document.getElementById("btn-audit").click(), 500);
  }

  // 预览工作流面板：加 #mock-workflow
  if (location.hash.indexOf("workflow") >= 0) {
    setTimeout(async () => {
      document.getElementById("btn-workflow").click();
      await new Promise((r) => setTimeout(r, 400));
      const tpl = document.querySelector("[data-tpl]");
      tpl && tpl.click();
    }, 500);
  }

  // 预览诊断：加 #mock-diag → 把三栏实测宽度写进 DOM（便于无头 --dump-dom 校验）
  if (location.hash.indexOf("diag") >= 0) {
    setTimeout(() => {
      const L = document.querySelector(".side--left").getBoundingClientRect().width;
      const C = document.querySelector(".main").getBoundingClientRect().width;
      const R = document.querySelector(".side--right").getBoundingClientRect().width;
      const el = document.createElement("div");
      el.id = "__diag2";
      const cs = getComputedStyle(document.querySelector(".app"));
      el.textContent = "APPCLASS=" + document.querySelector(".app").className +
        " LEFT=" + Math.round(L) + " CENTER=" + Math.round(C) + " RIGHT=" + Math.round(R) +
        " DISPLAY=" + cs.display + " COLS=" + cs.gridTemplateColumns;
      document.body.appendChild(el);
    }, 1800);
  }

  // 预览：左右两栏都收起（加 #mock-collapsed）
  if (location.hash.indexOf("collapsed") >= 0) {
    setTimeout(() => {
      document.querySelector(".app").classList.add("is-left-collapsed", "is-right-collapsed");
    }, 400);
  }

  // 预览设置面板：加 #mock-settings
  if (location.hash.indexOf("settings") >= 0) {
    setTimeout(() => document.getElementById("btn-settings").click(), 500);
  }

  // 预览审批弹窗：加 #mock-approval
  if (location.hash.indexOf("approval") >= 0) {
    setTimeout(() => {
      window.yindun && window.yindun.onEvent("need_confirm", {
        name: "执行命令", path: "E:\\yindun-agent\\dist",
        args: { command: "python report.py --month 2026-09" },
      });
      // 插件建议是异步补发的（本地模型判断可能慢），这里模拟慢一步到达
      setTimeout(() => window.yindun && window.yindun.onEvent("advisories", [
        { source: "审批决策提示", level: "warn",
          text: "本地模型判断：与你的请求存疑。你只要求汇总要点，未要求生成新文件。" },
      ]), 900);
    }, 400);
  }
})();
