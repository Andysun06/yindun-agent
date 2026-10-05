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
    { name: "供应商服务合同_示例.pdf", chars: 18422, error: null, via: "内核解析" },
    { name: "往来邮件_示例.eml", chars: 812, error: null,
      via: "插件解析（扩展附件解析（邮件 / 网页））：按邮件格式解析（eml）：含正文，附件本身未展开" },
    { name: "员工薪酬表_示例.csv", chars: 1260, error: null, via: "内核解析" },
  ];
  const audit = {
    chain_ok: true,
    stats: { total_entries: 128, tool_calls: 23, privacy_events: 41, approvals: 6,
             llm_calls: 18, chain_valid: true,
             chain_reason: "", chain_anchor: "verified", chain_legacy: 0,
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
  // 预览"链校验未通过 + 需要重建锚点"这一态（加 #mock-chainbad）
  const auditBad = JSON.parse(JSON.stringify(audit));
  auditBad.chain_ok = false;
  auditBad.stats.chain_valid = false;
  auditBad.stats.chain_anchor = "none";
  auditBad.stats.chain_reason =
    "锚点缺失但审计链非空 —— 审计目录可能被整体替换、锚点被删除，或这是首次升级" +
    "（可在审计面板点「重建锚点」确认接受当前链）";
  const auditData = () => Promise.resolve(location.hash.indexOf("chainbad") >= 0 ? auditBad : audit);

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
  const capOf = (hook, summary, detail) => [{ hook, kind: hook === "advisory_for_approval" ? "advisory" : "additive", summary, detail, timeout_cap: 5 }];  const plugins = [
    { id: "custom_dict", name: "自定义敏感词表", version: "0.1.0",
      description: "把本单位的项目代号、内部称谓、专用术语加入脱敏范围：命中的词在送往模型/外部算力之前被替换为占位符，还原仍只在本机完成。",
      source: "builtin", hooks: ["recognizer"],
      permissions: { network: "none", filesystem: "read" },
      file_exts: [], export_formats: {},
      config_files: [{ name: "words.txt", label: "敏感词表",
                       hint: "每行一条：普通词按字面匹配；re: 开头按正则；# 开头是注释。改动后立即生效。" }],
      provides: ["自定义实体识别（词表 / 正则）", "命中项计入审计的「插件识别」分组"],
      capabilities: capOf("recognizer", "补充识别敏感实体（只扩大脱敏范围，不能让任何内容免于脱敏）", ""),
      enabled: true, error: null, missing: [], usable: true },
    { id: "office_wf", name: "办公工作流模板包", version: "0.1.0",
      description: "给工作流编排补两个模板：代码安全体检（增强版）与本周工作总结（全部为真实步骤）。模板注册时由内核校验：高危步骤一律强制人工审批。",
      source: "builtin", hooks: ["workflow_template"],
      permissions: { network: "none", filesystem: "none" },
      file_exts: [], export_formats: {}, config_files: [],
      provides: ["工作流模板：代码安全体检（增强版）", "工作流模板：本周工作总结（无演示步骤）"],
      capabilities: capOf("workflow_template", "提供工作流模板（高危步骤会被内核强制改为人工审批）",
                          "模板由内核校验后注册（高危步骤强制人工审批）"),
      enabled: true, error: null, missing: [], usable: true },
    { id: "doc_extra", name: "扩展附件解析（邮件 / 网页）", version: "0.1.0",
      description: "让内核不认识的 .eml / .html / .mht 也能当附件用：邮件按正文与主题抽取、网页按可见文字抽取，产出文本后走同一条脱敏管线。",
      source: "builtin", hooks: ["attachment_parser"],
      permissions: { network: "none", filesystem: "read" },
      file_exts: ["eml", "html", "htm", "mht"], export_formats: {}, config_files: [],
      provides: ["附件格式扩展：.eml / .html / .htm / .mht"],
      capabilities: capOf("attachment_parser", "解析内核不支持的附件格式（产出文本后仍走同一条脱敏管线）",
                          "负责格式：eml、html、htm、mht"),
      enabled: true, error: null, missing: [], usable: true },
    { id: "export_pack", name: "导出格式扩展", version: "0.1.0",
      description: "给审计与工作流补两种插件渲染的导出格式：审计报告导出 Markdown、工作流报告导出排版好的 HTML。停用不影响导出——内核保留自带格式兜底。",
      source: "builtin", hooks: ["export_renderer"],
      permissions: { network: "none", filesystem: "none" },
      file_exts: [], export_formats: { audit: ["markdown"], workflow: ["html_card"] },
      format_labels: { markdown: "Markdown 表格", html_card: "卡片式 HTML" }, config_files: [],
      provides: ["审计导出：Markdown 表格", "工作流报告导出：卡片式 HTML"],
      capabilities: capOf("export_renderer", "渲染导出格式（内核始终保留内置格式作为兜底）",
                          "audit → Markdown 表格；workflow → 卡片式 HTML"),
      enabled: true, error: null, missing: [], usable: true },
    { id: "decision_hint", name: "审批决策提示", version: "0.1.0",
      description: "审批弹窗里给出一条提示：Agent 要执行的操作与你刚才的请求是否相符。用本地模型判断，数据不出本机。",
      source: "builtin", hooks: ["advisory_for_approval"],
      permissions: { network: "local-only", filesystem: "none" },
      file_exts: [], export_formats: {}, config_files: [], provides: [],
      capabilities: capOf("advisory_for_approval", "在人工审批弹窗里给出决策提示（只影响界面提示，不参与任何判定）", ""),
      enabled: true, error: null, missing: [], usable: true },
    { id: "laya_risk", name: "laya 风险分级", version: "0.1.0",
      description: "审批弹窗补一行风险分级（高危/中危/低危）：本机装了 laya 决策模型就用模型打分；没装、加载失败或推理失败时自动回退到正则规则。提示都写明本次来源；推理与规则匹配全在本机，只影响弹窗提示、不参与放行判定。",
      source: "builtin", hooks: ["advisory_for_approval"],
      permissions: { network: "local-only", filesystem: "read" },
      file_exts: [], export_formats: {},
      config_files: [{ name: "risk_rules.txt", label: "风险分级规则（正则回退）",
                       hint: "每行一条：级别|正则|说明（级别=高危/中危；# 开头是注释）。仅在 laya 模型不可用时生效；改动立即生效。" },
                     { name: "backend.txt", label: "分级后端",
                       hint: "一行一个字：auto（默认，装了 laya 用模型、否则正则）/ laya（只用模型，缺 laya 则不提示）/ regex（强制只用正则）。" }],
      provides: ["审批弹窗风险分级（laya 模型打分 / 正则规则回退）"],
      capabilities: capOf("advisory_for_approval", "在人工审批弹窗里给出决策提示（只影响界面提示，不参与任何判定）", ""),
      enabled: false, error: null, missing: [], usable: true },
  ];
  const pluginSync = { templates: ["ext_office_code_audit", "ext_office_weekly_real"], templates_removed: [],
                       recognizer: true, errors: [] };
  const pluginCheck = { ok: true, plugins: [
    { id: "custom_dict", name: "自定义敏感词表", enabled: true, usable: true, ok: true,
      hooks: { recognizer: { ok: true, error: "" } } },
    { id: "office_wf", name: "办公工作流模板包", enabled: true, usable: true, ok: true,
      hooks: { workflow_template: { ok: true, error: "" } } },
  ] };

  const wfTemplates = [
    { id: "wf_contract_review", name: "合同审查工作流", description: "自动解析合同、提取关键条款、生成审查意见、导出报告",
      source: "builtin", plugin_id: "", forced_approvals: 0, steps: 5 },
    { id: "wf_weekly_report", name: "周报生成工作流", description: "收集工作记录、整理结构、生成周报草稿、人工审核、导出",
      source: "builtin", plugin_id: "", forced_approvals: 0, steps: 5 },
    { id: "wf_security_check", name: "代码安全检查工作流", description: "扫描代码仓库、识别安全漏洞、风险评估、生成报告",
      source: "builtin", plugin_id: "", forced_approvals: 0, steps: 5 },
    { id: "ext_office_code_audit", name: "代码安全体检（增强版）",
      description: "分析项目 → 安全扫描 → 风险定级 → 生成修复建议 → 汇总报告 → 导出。全部为真实步骤；最后一步的审批由内核强制。",
      source: "plugin", plugin_id: "office_wf", forced_approvals: 1, steps: 6 },
  ];
  const wfStatus = {
    instance_id: "5021f4c2", template_name: "合同审查工作流", source_plugin: "",
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
        tool_name: "export_file", approval_type: "manual", forced_approval: true,
        status: "pending", demo: false, result: null },
    ],
  };

  const api = {
    bootstrap: () => Promise.resolve({
      version: "V3.4.0",
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
    workflow_export: () => Promise.resolve({ ok: true, renderer: "内核",
      path: "workflow_reports/20261004_合同审查工作流_执行记录.md" }),
    workflow_export_formats: () => Promise.resolve([
      { format: "md", label: "Markdown", source: "内核", plugin_id: "" },
      { format: "html", label: "HTML", source: "内核", plugin_id: "" },
      { format: "docx", label: "Word（兼容格式）", source: "内核", plugin_id: "" },
      { format: "html_card", label: "卡片式 HTML（插件：导出格式扩展）", source: "导出格式扩展", plugin_id: "export_pack" },
    ]),
    plugins_list: () => Promise.resolve(plugins),
    plugin_set_enabled: () => Promise.resolve({ ok: true, plugins }),
    plugin_sync_state: () => Promise.resolve(pluginSync),
    plugin_selfcheck: () => Promise.resolve(pluginCheck),
    plugin_config_read: () => Promise.resolve({
      ok: true, exists: true, path: "plugins_data/custom_dict/words.txt",
      text: "# 自定义敏感词表 · 每行一条\n# 1) 普通词：按字面匹配\n# 2) 正则：以 re: 开头\n#\n# 示例（去掉行首的 # 即可生效）：\n# 隐盾专项\n# 星海计划\n# re:[A-Z]{2,4}-[0-9]{3,6}\n",
    }),
    plugin_config_write: () => Promise.resolve({ ok: true, bytes: 128 }),
    window_action: noop,
    window_bounds: () => Promise.resolve({ ok: true, x: 80, y: 60, w: 1180, h: 780 }),
    window_resize_edge: noop,
    audit_snapshot: () => auditData(),
    audit_reanchor: () => Promise.resolve({ ok: true, count: 128 }),
    audit_export: () => Promise.resolve("（预览模式）audit_report_demo.json"),
    audit_export_formats: () => Promise.resolve([
      { format: "json", label: "JSON", source: "内核", plugin_id: "" },
      { format: "html", label: "HTML", source: "内核", plugin_id: "" },
      { format: "markdown", label: "Markdown 表格（插件：导出格式扩展）", source: "导出格式扩展", plugin_id: "export_pack" },
    ]),
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

  // 预览闪发浮条：加 #mock-mini（折叠态；真实运行时窗口本身会缩成浮条）
  if (location.hash.indexOf("mini") >= 0) {
    setTimeout(() => {
      document.body.classList.add("is-mini");
      document.getElementById("mini-status").textContent = "推理中…";
      document.getElementById("mini-status").classList.add("is-busy");
      document.getElementById("mini-input").value = "把客户张伟的手机号保存到 客户信息.txt";
      document.getElementById("mini-input").focus();
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
