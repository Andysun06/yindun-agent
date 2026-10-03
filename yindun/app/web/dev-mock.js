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
  const api = {
    bootstrap: () => Promise.resolve({
      version: "V3.3.2",
      settings: {
        model: "qwen2.5:7b-instruct", privacy: true, dark_mode: true, topmost: true,
        thinking_depth: 4, permission: "完全控制 (读/写/列表)", think_mode: "深度思考",
        ollama_models_cache: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"],
      },
      llm: {
        ready: true, model: "qwen2.5:7b-instruct",
        models: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"], error: null,
      },
      sessions, current_session_id: "s1",
    }),
    llm_status: () => Promise.resolve({ ready: true, model: "qwen2.5:7b-instruct", models: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"] }),
    list_sessions: () => Promise.resolve(sessions),
    open_session: (id) => Promise.resolve({ id, messages, box_mapping: {} }),
    new_session: () => Promise.resolve("s-new"),
    save_settings: noop, send: noop, cancel: noop, approve: noop,
    delete_session: noop, rename_session: noop,
  };

  window.__YINDUN_MOCK__ = { api };

  // 预览审批弹窗：加 #mock-approval
  if (location.hash.indexOf("approval") >= 0) {
    setTimeout(() => {
      window.yindun && window.yindun.onEvent("need_confirm", {
        name: "执行命令", path: "E:\\yindun-agent\\dist",
        args: { command: "python report.py --month 2026-09" },
      });
    }, 400);
  }
})();
