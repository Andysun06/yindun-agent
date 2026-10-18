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
    // 过程轨迹（预览用）：思考耗时 / 工具动作 / 关键判定——真实运行时由后端 trace 事件产生
    { role: "trace", trace: { seq: 1, kind: "thinking", name: "思考", detail: "3 条上下文", status: "ok", ms: 2400 } },
    { role: "trace", trace: { seq: 2, kind: "tool", name: "读取本地文件", detail: "assets/供应商服务合同_示例.md", status: "ok", ms: 120 } },
    { role: "trace", trace: { seq: 3, kind: "judge", name: "隐私隔离网关",
        detail: "识别到 5 类敏感实体（PHONE×2、IDCARD×1、BANKCARD×1、EMAIL×1、NAME×2），已替换为占位符", status: "ok", ms: 0 } },
    { role: "trace", trace: { seq: 4, kind: "judge", name: "laya 风险分级",
        detail: "该操作涉及读取客户资料，风险等级：中 —— 建议人工确认后再落盘", status: "ok", ms: 810 } },
    { role: "trace", trace: { seq: 5, kind: "tool", name: "创建本地文件", detail: "合同要点_20261003.md", status: "running", ms: 0 } },
    { role: "trace", trace: { seq: 6, kind: "tool", name: "创建本地文件", detail: "写入前经人工审批放行", status: "ok", ms: 1580 } },
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
  let mockPicked = 1;
  const attachments = [
    { name: "供应商服务合同_示例.pdf", chars: 18422, error: null, via: "内核解析",
      report: "【隐私风险报告】\n第 2 行  手机号：138****5678（已脱敏后仅供核对）" },
    { name: "往来邮件_示例.eml", chars: 812, error: null,
      via: "插件解析（扩展附件解析（邮件 / 网页））：按邮件格式解析（eml）：含正文，附件本身未展开" },
    { name: "员工薪酬表_示例.csv", chars: 1260, error: null, via: "内核解析",
      report: "【隐私风险报告】\n第 3 行  银行卡：6222**********8887" },
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

  // 安全工具结构化样例：健康体检（4 条 findings / 3 档 severity）与行为画像
  // （37 次工具调用 + 3 条不同 severity 的 anomaly）。summary 故意保留 `======`
  // 报告文本，用来在回归里证明"默认视图不再贴纯文本，原文只在折叠项里"。
  const healthReport = {
    scan_root: "E:/yindun-agent/workspace",
    scan_time: "2026-10-16 10:24:08",
    total_files: 128,
    scanned_files: 121,
    findings_count: 4,
    risk_score: 43,
    findings: [
      { file: "E:/yindun-agent/workspace/keys/id_rsa", severity: "绝密", category: "sensitive_file",
        description: "发现 SSH 私钥文件，凭据可能被直接读取", matches: [], line: null },
      { file: "E:/yindun-agent/workspace/.env", severity: "机密", category: "sensitive_file",
        description: "发现环境变量配置，可能含明文密钥", matches: [], line: null },
      { file: "E:/yindun-agent/workspace/客户资料_2026Q3.csv", severity: "机密", category: "sensitive_content",
        description: "检测到手机号 3 处、身份证号 1 处（已按脱敏规则打码展示）",
        matches: ["138****5678", "139****9012", "110***********1234"], line: 12 },
      { file: "E:/yindun-agent/workspace/logs/agent.log", severity: "内部", category: "permission",
        description: "文件权限过宽：其他用户可读", matches: ["rw-rw-r--"], line: null },
    ],
  };
  const healthSummary = [
    "=".repeat(60),
    "隐盾隐私健康体检报告",
    "扫描路径: E:/yindun-agent/workspace",
    "扫描时间: 2026-10-16 10:24:08",
    "扫描文件: 121 / 128",
    "发现问题: 4 项",
    "风险评分: 43/100",
    "=".repeat(60),
    "",
    "[绝密] 1 项:",
    "  ⚠ 发现 SSH 私钥文件，凭据可能被直接读取",
    "     → E:/yindun-agent/workspace/keys/id_rsa",
    "[机密] 2 项:",
    "  ⚠ 发现环境变量配置，可能含明文密钥",
    "     → E:/yindun-agent/workspace/.env",
  ].join("\n");

  const behaviorProfile = {
    session_id: "sess_20261016_demo",
    model_name: "qwen2.5:7b",
    time_range: ["2026-10-16T09:12:00", "2026-10-16T10:24:08"],
    total_tool_calls: 37,
    tool_call_breakdown: {
      read_local_file: 18, search_in_files: 8, create_local_file: 6,
      run_local_command: 3, delete_local_file: 2,
    },
    operation_breakdown: { read: 26, write: 6, delete: 2, execute: 3 },
    unique_paths: 14,
    accessed_paths: [
      "E:/yindun-agent/workspace/合同/供应商服务合同_示例.md",
      "E:/yindun-agent/workspace/客户资料_2026Q3.csv",
    ],
    sensitive_paths_accessed: ["C:/Users/demo/.ssh/id_rsa", "E:/yindun-agent/workspace/.env"],
    path_jumps: 7,
    risk_score: 62,
    risk_factors: ["访问了 2 个敏感路径 (+20)", "路径跳跃 7 次 (+20)", "存在删除类操作 (+10)"],
    total_llm_calls: 16,
    avg_input_length: 328,
    avg_output_length: 612,
  };
  const behaviorAnomalies = [
    { session_id: "sess_20261016_demo", timestamp: "2026-10-16T10:02:11",
      anomaly_type: "path_hopping", severity: "medium",
      description: "跨 7 个不相关目录操作，可能存在遍历风险",
      details: { directories: ["合同", "客户", "日志", "导出", "脚本", "缓存", "临时"] } },
    { session_id: "sess_20261016_demo", timestamp: "2026-10-16T09:58:40",
      anomaly_type: "sensitive_access", severity: "high",
      description: "访问敏感路径: C:/Users/demo/.ssh/id_rsa",
      details: { tool: "read_local_file", path: "C:/Users/demo/.ssh/id_rsa" } },
    { session_id: "sess_20261016_demo", timestamp: "2026-10-16T09:14:05",
      anomaly_type: "off_hours", severity: "low",
      description: "非工作时间操作（0:00）", details: { hour: 0 } },
  ];
  const behaviorSummary = [
    "=".repeat(50),
    "模型行为画像: sess_20261016_demo",
    "模型: qwen2.5:7b",
    "工具调用: 37 次",
    "访问路径: 14 个",
    "敏感路径: 2 个",
    "风险评估: 62/100",
    "=".repeat(50),
  ].join("\n");

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
    { id: "custom_dict", category: "安全", name: "自定义敏感词表", version: "0.1.0",
      description: "把本单位的项目代号、内部称谓、专用术语加入脱敏范围：命中的词在送往模型/外部算力之前被替换为占位符，还原仍只在本机完成。",
      source: "builtin", hooks: ["recognizer"],
      permissions: { network: "none", filesystem: "read" },
      file_exts: [], export_formats: {},
      config_files: [{ name: "words.txt", label: "敏感词表",
                       hint: "每行一条：普通词按字面匹配；re: 开头按正则；# 开头是注释。改动后立即生效。" }],
      provides: ["自定义实体识别（词表 / 正则）", "命中项计入审计的「插件识别」分组"],
      capabilities: capOf("recognizer", "补充识别敏感实体（只扩大脱敏范围，不能让任何内容免于脱敏）", ""),
      runtime: { calls: 12, ts: Math.floor(Date.now() / 1000) - 96, ms: 8, status: "ok", hook: "recognizer" },
      enabled: true, error: null, missing: [], usable: true },
    { id: "office_wf", category: "办公", name: "办公工作流模板包", version: "0.1.0",
      description: "给工作流编排补两个模板：代码安全体检（增强版）与本周工作总结（全部为真实步骤）。模板注册时由内核校验：高危步骤一律强制人工审批。",
      source: "builtin", hooks: ["workflow_template"],
      permissions: { network: "none", filesystem: "none" },
      file_exts: [], export_formats: {}, config_files: [],
      provides: ["工作流模板：代码安全体检（增强版）", "工作流模板：本周工作总结"],
      capabilities: capOf("workflow_template", "提供工作流模板（高危步骤会被内核强制改为人工审批）",
                          "模板由内核校验后注册（高危步骤强制人工审批）"),
      runtime: { calls: 2, ts: Math.floor(Date.now() / 1000) - 3600, ms: 3, status: "ok", hook: "workflow_template" },
      enabled: true, error: null, missing: [], usable: true },
    { id: "doc_extra", category: "文档", name: "扩展附件解析（邮件 / 网页）", version: "0.1.0",
      description: "让内核不认识的 .eml / .html / .mht 也能当附件用：邮件按正文与主题抽取、网页按可见文字抽取，产出文本后走同一条脱敏管线。",
      source: "builtin", hooks: ["attachment_parser"],
      permissions: { network: "none", filesystem: "read" },
      file_exts: ["eml", "html", "htm", "mht"], export_formats: {}, config_files: [],
      provides: ["附件格式扩展：.eml / .html / .htm / .mht"],
      capabilities: capOf("attachment_parser", "解析内核不支持的附件格式（产出文本后仍走同一条脱敏管线）",
                          "负责格式：eml、html、htm、mht"),
      runtime: {},
      enabled: true, error: null, missing: [], usable: true },
    { id: "export_pack", category: "导出", name: "导出格式扩展", version: "0.1.0",
      description: "给审计与工作流补两种插件渲染的导出格式：审计报告导出 Markdown、工作流报告导出排版好的 HTML。停用不影响导出——内核保留自带格式兜底。",
      source: "builtin", hooks: ["export_renderer"],
      permissions: { network: "none", filesystem: "none" },
      file_exts: [], export_formats: { audit: ["markdown"], workflow: ["html_card"] },
      format_labels: { markdown: "Markdown 表格", html_card: "卡片式 HTML" }, config_files: [],
      provides: ["审计导出：Markdown 表格", "工作流报告导出：卡片式 HTML"],
      capabilities: capOf("export_renderer", "渲染导出格式（内核始终保留内置格式作为兜底）",
                          "audit → Markdown 表格；workflow → 卡片式 HTML"),
      runtime: {},
      enabled: true, error: null, missing: [], usable: true },
    { id: "decision_hint", category: "安全", name: "审批决策提示", version: "0.1.0",
      description: "审批弹窗里给出一条提示：Agent 要执行的操作与你刚才的请求是否相符。用本地模型判断，数据不出本机。",
      source: "builtin", hooks: ["advisory_for_approval"],
      permissions: { network: "local-only", filesystem: "none" },
      file_exts: [], export_formats: {}, config_files: [], provides: [],
      capabilities: capOf("advisory_for_approval", "在人工审批弹窗里给出决策提示（只影响界面提示，不参与任何判定）", ""),
      runtime: { calls: 3, ts: Math.floor(Date.now() / 1000) - 620, ms: 4210, status: "ok", hook: "advisory_for_approval" },
      enabled: true, error: null, missing: [], usable: true },
    { id: "laya_risk", category: "安全", name: "laya 风险分级", version: "0.1.0",
      description: "审批弹窗补一行风险分级（高危/中危/低危）：本机装了 laya 决策模型就用模型打分；没装、加载失败或推理失败时自动回退到正则规则。提示都写明本次来源；推理与规则匹配全在本机，只影响弹窗提示、不参与放行判定。",
      source: "builtin", hooks: ["advisory_for_approval"],
      permissions: { network: "local-only", filesystem: "read" },
      file_exts: [], export_formats: {},
      config_files: [{ name: "laya_source.json", label: "laya 接入配置", managed: true,
                       hint: "由设置页「laya 风险分级」维护（本机 / 局域网 / 正则三档）。" },
                     { name: "risk_rules.txt", label: "风险分级规则（正则回退）",
                       hint: "每行一条：级别|正则|说明（级别=高危/中危；# 开头是注释）。" }],
      provides: ["审批弹窗风险分级（laya 模型打分 / 正则规则回退）"],
      capabilities: capOf("advisory_for_approval", "在人工审批弹窗里给出决策提示（只影响界面提示，不参与任何判定）", ""),
      enabled: false, error: null, missing: [], usable: true },
  ];
  const layaStatus = {
    source: "local", config: { source: "local", auto: true, endpoint: "", model: "", timeout: 8 },
    local: { ok: true, available: true, detail: "本机已安装 laya（site-packages/laya/__init__.py）" },
    lan: { available: false, endpoint: "", detail: "未配置：需要内网服务地址（OpenAI 兼容 /chat/completions）" },
    regex: { path: "plugins_data/laya_risk/risk_rules.txt", count: 14 },
    active_backend: "local", active_label: "本机 laya 模型",
    last_used: "本机 laya 模型", plugin_runtime: { last_source: "本机 laya 模型" }, config_error: "",
  };
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
    impl_notice: "内核内置模板的每一步都有真实执行器（含数据采集/清洗/分析/出图与人工审核留痕）。",
    steps: [
      { step_id: "s1", name: "读取合同文件", description: "读取待审查的合同文档内容", tool_name: "read_local_file",
        approval_type: "auto", status: "completed", unimplemented: false,
        result: "{'ok': True, 'tool': 'read_local_file', 'summary': '已读取《供应商服务合同_示例.md》（18422 字符）'}" },
      { step_id: "s2", name: "提取关键条款", description: "从合同中提取付款条款、违约条款、保密条款等关键信息",
        tool_name: "analyze_contract", approval_type: "auto", status: "completed", unimplemented: false,
        result: "{'ok': True, 'summary': '已提取 6 条关键条款（本地模型）'}" },
      { step_id: "s3", name: "风险点分析", description: "识别合同中的潜在风险和不利条款",
        tool_name: "analyze_contract", approval_type: "auto", status: "waiting_approval", unimplemented: false, result: null },
      // 沙盘里保留一个"已审批、待执行"的步骤：这是"审批通过 → 执行下一步"的中间态，
      // 界面文案是「已审批，待执行」（V3.6.0 修的真实卡死点就卡在这个状态上）
      { step_id: "s4", name: "生成审查意见", description: "基于分析结果生成专业的审查意见书",
        tool_name: "generate_report", approval_type: "manual", status: "approved",
        reviewer: "张三", unimplemented: false, result: null },
      { step_id: "s5", name: "导出PDF报告", description: "将审查意见导出为PDF文件",
        tool_name: "export_file", approval_type: "manual", forced_approval: true,
        status: "pending", unimplemented: false, result: null },
      // 外部模板里的未实现工具：界面应标「未实现」，而不是假装能跑
      { step_id: "s6", name: "外呼通知（第三方模板）", description: "来自外部配置的步骤，内核没有对应执行器",
        tool_name: "notify_vendor", approval_type: "auto", status: "pending",
        unimplemented: true, result: null },
    ],
  };

  // 沙盘里的自定义模型（预览用状态）：保存/删除会真的改它，三档列表跟着变
  const mockCustomModels = new Map();

  const api = {
    bootstrap: () => Promise.resolve({
      // 预览模式不写死版本号：这份 mock 只在本地看界面用（`index.html#mock`），
      // 写死产品版本就会漂——版本号唯一来源是 `yindun/__init__.py`，随打包注入/载荷下发。
      version: "预览模式（界面沙盘）",
      settings: {
        model: "qwen3.5:4b", ollama_host: "http://127.0.0.1:11434", privacy: true, allow_script_exec: true,
        dark_mode: location.hash.indexOf("light") < 0, topmost: true,
        thinking_depth: 7, permission: "ask", think_mode: "深度思考", sandbox: "E:\yindun-agent",
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
    // 预览模式模拟"用户选了一份文档"：真实运行时由系统对话框返回解析摘要。
    // 早先这里固定返回 []，点「＋」既不弹窗也不出提示，在沙盘里看起来就是"按钮点不了"。
    pick_files: () => {
      const item = { name: `新增附件_${mockPicked++}_示例.md`, chars: 2048, error: null, via: "内核解析" };
      attachments.push(item);
      return Promise.resolve([item]);
    },
    list_attachments: () => Promise.resolve(attachments),
    clear_attachments: () => Promise.resolve(true),
    kb_status: () => Promise.resolve(kb),
    custom_models: () => Promise.resolve([
      { name: "公司内网Qwen", model_id: "qwen2.5:14b", base_url: "http://10.0.0.8:8000/v1",
        tier: "lan", has_key: false, key_invalid: false },
      { name: "云端GLM", model_id: "glm-4-flash", base_url: "https://open.bigmodel.cn/api/paas/v4",
        tier: "wan", has_key: true, key_invalid: false },
    ]),
    model_catalog: () => Promise.resolve({
      current: "qwen2.5:7b-instruct",
      ollama_host: "http://127.0.0.1:11434",
      local_detected: ["qwen2.5:7b-instruct", "qwen3.5:4b"],
      tier_labels: { local: "本机模型", lan: "局域网模型", wan: "公网模型" },
      tier_boundary: {
        local: "数据不出这台电脑",
        lan: "数据出这台电脑，但不出企业内网",
        wan: "数据出企业内网；送出前内容已在本机脱敏",
      },
      // ★ 沙盘里的自定义模型是**有状态的**（V3.6.7）：保存/删除要能真的反映到三档列表上，
      //   否则预览里点「保存」看不到结果、"密钥留空＝不改动"这条语义也无从验证。
      tiers: {
        local: [
          { name: "qwen2.5:7b-instruct", model_id: "qwen2.5:7b-instruct", tier: "local",
            source: "ollama", base_url: "http://127.0.0.1:11434", kind: "ollama", note: "本机 Ollama 已安装" },
          { name: "qwen3.5:4b", model_id: "qwen3.5:4b", tier: "local",
            source: "ollama", base_url: "http://127.0.0.1:11434", kind: "ollama", note: "本机 Ollama 已安装" },
        ],
        lan: [
          { name: "公司内网Qwen", model_id: "qwen2.5:14b", tier: "lan", source: "custom",
            base_url: "http://10.0.0.8:8000/v1", kind: "openai", has_key: false, key_invalid: false, note: "" },
        ],
        wan: [
          { name: "云端GLM", model_id: "glm-4-flash", tier: "wan", source: "custom",
            base_url: "https://open.bigmodel.cn/api/paas/v4", kind: "openai", has_key: true, key_invalid: false, note: "" },
          ...[...mockCustomModels.entries()].map(([name, m]) => ({
            name, model_id: m.model_id, tier: m.tier, source: "custom",
            base_url: m.base_url, kind: "openai", has_key: !!m.has_key, key_invalid: false, note: "",
          })),
        ],
      },
    }),
    // 保存/删除自来模型（预览沙盘）：与真实后端**同一套契约**——
    // 密钥留空＝沿用已存的那把（回报 kept_key），新建时留空就是空。
    add_custom_model: (name, model_id, base_url, api_key, tier) => {
      const key = String(api_key || "").trim();
      const before = mockCustomModels.get(name);
      const kept = !key && !!(before && before.has_key);
      mockCustomModels.set(name, { model_id, base_url, tier: tier || "wan",
                                   has_key: kept || !!key });
      return Promise.resolve({ ok: true, tier: tier || "wan",
        tier_label: { local: "本机模型", lan: "局域网模型", wan: "公网模型" }[tier || "wan"],
        kept_key: kept, models: [...mockCustomModels.keys()] });
    },
    remove_custom_model: (name) => Promise.resolve({ ok: mockCustomModels.delete(name), models: [] }),
    // 探测回报"收到的参数"，这样界面有没有把条目名捎上（好让后端回落已存密钥）在预览里就能验
    probe_model_endpoint: (url, api_key, name) => Promise.resolve(
      { ok: true, style: "openai", models: ["qwen2.5:14b", "qwen2.5:32b"], count: 2,
        endpoint: "http://10.0.0.8:8000/v1", note: "已连通，发现 2 个模型",
        params: { url, key_empty: !String(api_key || "").trim(), entry: name || "" } }),
    control_status: () => Promise.resolve({
      configured: true, enabled: true, url: "http://10.0.0.9:8787", endpoint_id: "ep-e2e-01",
      last_sent_count: 32, last_report_at: "2026-10-16T10:12:33", last_error: "",
      applied_policy_version: 4, auto_interval: 300, running: false,
      effect: ["2 条敏感路径：命中即需人工审批", "审批下限：所有操作都要人工确认（含只读操作，冻结自动放行）",
               "严格模式：禁用解释器，只保留只读命令"],
      control_rules: [
        { path_pattern: "E:/yindun-agent/合同/*", policy: "confirm", from_control_version: 4, source: "control" },
        { path_pattern: "*", policy: "confirm", from_control_version: 4, source: "control" },
      ],
    }),
    control_pair: () => Promise.resolve({ ok: true, endpoint_id: "ep-e2e-01", note: "登记成功" }),
    control_set_enabled: () => Promise.resolve({ ok: true, enabled: true }),
    control_sync: () => Promise.resolve({ ok: true, report: { events: 4 }, policy: { applied: { policy_version: 4 } } }),
    control_clear_rules: () => Promise.resolve({ ok: true, removed: 2 }),
    control_auto: () => Promise.resolve({ ok: true, interval: 300 }),
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
    laya_status: () => Promise.resolve(layaStatus),
    laya_save: (cfg) => Promise.resolve({ ok: true, config: Object.assign({}, layaStatus.config, cfg || {}), detail: "已保存（预览模式）" }),
    laya_probe: (kind) => Promise.resolve({ ok: true, detail: "预览模式：本机 laya 可用", available: true }),
    laya_try: () => Promise.resolve({ ok: true, detail: "预览模式：服务已返回分级结果，判为「高危」：0.82", level: "高危" }),
    set_session_permission: (mode) => Promise.resolve({ ok: true, session_permission: mode || null,
      default_permission: "ask", effective: mode || "ask", posture: { permission: mode || "ask" } }),
    plugin_set_enabled: (id, on) => {      // 与后端同语义：真的改状态再回列表
      const item = plugins.find((x) => x.id === id);
      if (item) item.enabled = !!on;
      return Promise.resolve({ ok: true, plugins });
    },
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
    // 安全工具：返回结构与后端保持同形（health_scan.json 是 JSON 文本）
    health_scan: () => Promise.resolve({
      ok: true, target: healthReport.scan_root, summary: healthSummary,
      json: JSON.stringify(healthReport, null, 2),
    }),
    behavior_profile: () => Promise.resolve({
      ok: true, summary: behaviorSummary,
      profile: behaviorProfile, anomalies: behaviorAnomalies,
    }),
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
