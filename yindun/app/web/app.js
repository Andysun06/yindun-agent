/* ==========================================================================
   隐盾 · Web 界面逻辑（无构建步骤：原生 ES + pywebview js_api 桥）
   与 Python 的契约：
     · JS → Python：window.pywebview.api.<method>(...)  （返回 Promise）
     · Python → JS：window.yindun.onEvent(event, payload)（由 Python 端 evaluate_js 调用）
   ========================================================================== */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    sessions: [], currentId: null, messages: [], busy: false,
    settings: {}, llm: { ready: false, model: "", models: [] },
    streamBubble: null, startedAt: 0,
    attachments: [], audit: null,
    pane: "settings", auditFormats: [], wfFormats: [],
  };

  /* ── 工具 ─────────────────────────────────────────── */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** 极简 Markdown 渲染（先转义再套标签，保证不引入 HTML 注入）
   *  支持：代码块、表格、行内代码、粗体、标题、列表、换行 */
  function md(text) {
    const blocks = [];
    const stash = (html) => { blocks.push(html); return `\u0000B${blocks.length - 1}\u0000`; };
    let out = esc(text);

    // 1) 代码块
    out = out.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) =>
      stash(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`));

    // 2) 表格（表头 + |---| 分隔行的连续块）
    const lines = out.split("\n");
    const kept = [];
    for (let i = 0; i < lines.length; i++) {
      const isRow = /^\s*\|.*\|\s*$/.test(lines[i]);
      const isSep = /^\s*\|[\s:|-]+\|\s*$/.test(lines[i + 1] || "");
      if (isRow && isSep) {
        const cells = (line) => line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
        const head = cells(lines[i]);
        const body = [];
        i += 2;
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { body.push(cells(lines[i])); i++; }
        i--;
        kept.push(stash(
          `<table><thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead>` +
          `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`));
      } else {
        kept.push(lines[i]);
      }
    }
    out = kept.join("\n");

    // 3) 行内格式
    out = out
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      .replace(/^#{1,3}\s+(.*)$/gm, "<strong>$1</strong>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/^\s*[-*]\s+(.*)$/gm, "• $1")
      .replace(/\n/g, "<br>");

    return out.replace(/\u0000B(\d+)\u0000/g, (_m, i) => blocks[Number(i)]);
  }

  function toast(text, ms = 2600) {
    const el = document.createElement("div");
    el.className = "toast";
    el.textContent = text;
    $("toast-root").appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  const CLIP_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.4 11.1l-8.5 8.5a5 5 0 01-7.1-7.1l8.6-8.5a3.3 3.3 0 014.7 4.7l-8.6 8.5a1.7 1.7 0 01-2.4-2.4l7.9-7.8"/></svg>';

  // 换行符：显式构造，避免源码转义在工具链里被改写
  const NL = String.fromCharCode(10);

  const api = () =>
    (window.pywebview && window.pywebview.api) ||
    (window.__YINDUN_MOCK__ && window.__YINDUN_MOCK__.api) ||   // 开发预览（#mock）
    null;
  async function call(method, ...args) {
    const bridge = api();
    if (!bridge || typeof bridge[method] !== "function") {
      toast("桥接未就绪：" + method);
      return null;
    }
    try { return await bridge[method](...args); }
    catch (err) { toast("调用失败：" + method); console.error(err); return null; }
  }

  /* ── 渲染：会话列表 ───────────────────────────────── */
  function renderSessions() {
    const box = $("session-list");
    box.innerHTML = "";
    if (!state.sessions.length) {
      box.innerHTML = `<div class="session-item__meta" style="padding:8px 12px">还没有对话</div>`;
      return;
    }
    for (const s of state.sessions) {
      const el = document.createElement("div");
      el.className = "session-item" + (s.id === state.currentId ? " is-active" : "");
      el.innerHTML = `<span class="session-item__title">${esc(s.title)}</span>
        <span class="session-item__meta">${s.message_count} 条消息 · ${esc((s.updated_at || "").replace("T", " "))}</span>
        <span class="session-item__ops">
          <button class="op" data-act="rename" title="重命名">✎</button>
          <button class="op" data-act="delete" title="删除">🗑</button>
        </span>`;
      el.onclick = (e) => { if (!e.target.closest(".op")) openSession(s.id); };
      el.querySelector('[data-act="rename"]').onclick = (e) => { e.stopPropagation(); renameSession(s); };
      el.querySelector('[data-act="delete"]').onclick = (e) => { e.stopPropagation(); deleteSession(s, e.target); };
      box.appendChild(el);
    }
  }

  /* ── 会话管理：重命名 / 删除（删除为两步确认，避免误删）────── */
  async function renameSession(session) {
    const item = [...document.querySelectorAll(".session-item")].find(
      (el) => el.querySelector(".session-item__title").textContent === session.title);
    const titleEl = item && item.querySelector(".session-item__title");
    if (!titleEl) return;
    const input = document.createElement("input");
    input.type = "text";
    input.value = session.title;
    input.className = "session-item__edit";
    titleEl.replaceWith(input);
    input.focus(); input.select();
    const commit = async () => {
      const title = input.value.trim();
      if (title && title !== session.title) {
        await call("rename_session", session.id, title);
        if (session.id === state.currentId) $("chat-title").textContent = title;
        await refreshSessions();
      } else {
        await refreshSessions();
      }
    };
    input.onblur = commit;
    input.onkeydown = (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.onblur = null; commit(); }
      if (e.key === "Escape") { input.onblur = null; refreshSessions(); }
    };
  }

  async function deleteSession(session, button) {
    if (button.dataset.armed !== "1") {
      button.dataset.armed = "1";
      button.textContent = "确认";
      button.classList.add("op--danger");
      setTimeout(() => { button.dataset.armed = ""; button.textContent = "🗑"; button.classList.remove("op--danger"); }, 3000);
      return;
    }
    await call("delete_session", session.id);
    await refreshSessions();
    if (session.id === state.currentId) {
      state.currentId = null; state.messages = []; renderMessages();
      $("chat-title").textContent = "新对话";
    }
    toast("会话已删除");
  }

  /* ── 安全态势（真实状态，非装饰）───────────────────── */
  function renderPosture(payload) {
    const s = state.settings || {};
    const privacy = payload && "privacy" in payload ? payload.privacy : s.privacy;
    const p = $("posture-privacy");
    p.classList.toggle("is-on", !!privacy);
    p.classList.toggle("is-off", !privacy);
    p.querySelector("span").textContent = privacy ? "已启用" : "已关闭";

    const permission = (payload && payload.permission) || s.permission || "—";
    $("posture-permission").querySelector("span").textContent = permission.replace(/\s*\(.*\)$/, "");

    const sandbox = (payload && payload.sandbox) || s.sandbox || state.sandbox || "—";
    $("posture-sandbox").querySelector("span").textContent = sandbox;

    const chain = state.audit && state.audit.chain_ok;
    const c = $("posture-chain");
    c.classList.toggle("is-on", chain === true);
    c.classList.toggle("is-off", chain === false);
    c.querySelector("span").textContent = chain === true ? "完整性通过" : (chain === false ? "校验未通过" : "未校验");
  }

  /* ── 数据看板 ─────────────────────────────────── */
  function renderStats(payload) {
    const model = (payload && payload.model) || state.settings.model || "—";
    $("stat-model").textContent = model.length > 24 ? model.slice(0, 22) + "…" : model;
    $("stat-tools").textContent = `工具 ${(payload && payload.tool_calls) || 0}`;
    const tokens = (payload && payload.context_tokens) || 0;
    const limit = (payload && payload.context_limit) || 5000;
    $("stat-tokens").textContent = `上下文 ${tokens >= 1000 ? (tokens / 1000).toFixed(1) + "k" : tokens}/${limit >= 1000 ? (limit / 1000).toFixed(0) + "k" : limit}`;
    $("stat-tokens").title = `按中英文分权重估算的上下文占用；超过 ${limit} 会触发历史摘要压缩（估算口径与摘要压缩一致）`;
    renderPosture(payload);
  }

  /* ── 渲染：消息 ───────────────────────────────────── */
  const pad = (n) => String(n).padStart(2, "0");

  function emptyState() {
    const starters = [
      ["读取沙箱里的文档，列出关键条款", "M6 3h9l4 4v14H6zM15 3v4h4"],
      ["检查这个项目的结构，指出风险点", "M4 6h6v5H4zM14 6h6v5h-6zM9 18h6"],
      ["把上一步的结论整理成一份报告文件", "M6 3h9l4 4v14H6zM8 12h8M8 16h5"],
    ];
    return `<div class="chat__empty">
      <h1>让大模型看不见敏感数据，也能把活干完</h1>
      <p>文本在内存中被替换成占位符后才送进模型；模型全程只见占位符，回答返回时在本机还原。
         文件读写走沙箱，命令走白名单，高危操作需你确认，全过程写入审计链。</p>
      <div class="starters">${starters.map(([text, path]) => `
        <button class="starter" data-starter="${esc(text)}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="${path}"/></svg>
          ${esc(text)}
        </button>`).join("")}</div>
    </div>`;
  }

  /** 助手回答：画布上的纯文本（Codex 式，无卡片）+ 一行极小的标记 */
  function entryEl(html, metaHtml = "", index = null) {
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<div class="entry">
      <div class="entry__marker"><span class="entry__dot"></span><span>隐盾</span>${index === null ? "" : `<span>#${pad(index)}</span>`}</div>
      <div class="entry__body">${html}${metaHtml ? `<div class="bubble__meta">${metaHtml}</div>` : ""}</div>
    </div>`;
    return row;
  }

  /** 工具/状态行：紧凑单行（Codex 式的活动流），最多保留 6 行 */
  function appendToolLine(text) {
    const entry = state.streamBubble;
    if (!entry) return;
    const body = entry.querySelector(".entry__body");
    if (!body) return;
    let box = entry.querySelector(".entry__tools");
    if (!box) {
      box = document.createElement("div");
      entry.insertBefore(box, body);
    }
    const last = box.lastElementChild;
    if (last && last.textContent.trim() === String(text).trim()) return;   // 去重连续重复
    const line = document.createElement("div");
    line.className = "tool-line";
    line.innerHTML = `<span class="tool-line__mark">▸</span><span class="tool-line__text">${esc(text)}</span>`;
    box.appendChild(line);
    while (box.children.length > 6) box.removeChild(box.firstElementChild);
    scrollToEnd();
  }

  /** 用户输入 = 指令票：左轨加粗 + 小标签 */
  function slipEl(html) {
    const row = document.createElement("div");
    row.className = "row row--user";
    row.innerHTML = `<div class="slip"><div class="bubble">${html}</div></div>`;
    return row;
  }

  function renderMessages() {
    const chat = $("chat");
    chat.innerHTML = "";
    if (!state.messages.length) { chat.innerHTML = emptyState(); bindStarters(); return; }
    let index = 0;
    for (const m of state.messages) {
      if (m.role === "user") { chat.appendChild(slipEl(md(m.content || ""))); }
      else if (m.role === "assistant" && m.content) {
        index += 1;
        chat.appendChild(entryEl(md(m.content), "", index));
      }
    }
    scrollToEnd();
  }

  function bindStarters() {
    document.querySelectorAll("[data-starter]").forEach((btn) => {
      btn.onclick = () => {
        $("input").value = btn.dataset.starter;
        $("input").focus();
      };
    });
  }

  function scrollToEnd() { const c = $("chat"); c.scrollTop = c.scrollHeight; }

  function appendUser(text) {
    const chat = $("chat");
    if (chat.querySelector(".chat__empty")) chat.innerHTML = "";
    chat.appendChild(slipEl(md(text))); scrollToEnd();
  }

  /** 思考中的临时条目（中间结果/等待态都写进它） */
  function ensureStreamBubble() {
    if (state.streamBubble) return state.streamBubble;
    const chat = $("chat");
    if (chat.querySelector(".chat__empty")) chat.innerHTML = "";
    const index = chat.querySelectorAll(".entry").length + 1;
    const row = entryEl(`<span class="typing"><i></i><i></i><i></i></span>`, "", index);
    row.querySelector(".entry__body").classList.add("is-thinking");
    chat.appendChild(row);
    state.streamBubble = row;
    scrollToEnd();
    return row;
  }

  function finalizeBubble(text, tagText) {
    const row = state.streamBubble;
    state.streamBubble = null;
    if (!row) {
      const chat = $("chat");
      chat.appendChild(entryEl(md(text), "", chat.querySelectorAll(".entry").length + 1));
      scrollToEnd();
      return;
    }
    const body = row.querySelector(".entry__body");
    body.classList.remove("is-thinking");
    const secs = Math.max(1, Math.round((Date.now() - state.startedAt) / 1000));
    body.innerHTML = md(text) + `<div class="bubble__meta">
        <span class="bubble__tag">${esc(tagText || "快速")}</span><span>${secs}s</span></div>`;
    scrollToEnd();
  }

  /* ── 忙碌态 ───────────────────────────────────────── */
  function setBusy(busy) {
    state.busy = busy;
    const btn = $("btn-send");
    btn.classList.toggle("icon-btn--stop", busy);
    btn.title = busy ? "停止生成" : "发送";
    btn.innerHTML = busy
      ? `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 12l16-8-6 8 6 8-16-8z"/></svg>`;
    $("input").disabled = busy;
    $("composer-hint").textContent = busy
      ? "推理中…（可随时点停止，取消立即生效）"
      : "数据在本机脱敏后才送入模型";
    if (busy) startBusyWatchdog(); else stopBusyWatchdog();
  }

  /* ── 忙碌看门狗（事件丢失自愈）──────────────────────
     事件推送是单向的：某条 state/finished 事件一旦丢失（页面重载、渲染异常、
     桥接抖动），界面会永远停在"推理中"，只能重启。看门狗在忙碌期间每 3 秒
     向后端要一次状态快照：若后端其实已空闲，就按会话存储重渲染并恢复输入——
     状态失联最多持续一个轮询周期，而不是永远卡死。 */
  let watchdogTimer = null;

  function startBusyWatchdog() {
    if (watchdogTimer) return;
    watchdogTimer = setInterval(async () => {
      try {
        const snap = await call("state_snapshot");
        if (!snap || snap.busy !== false) return;
        // 后端已空闲而前端还在忙 → 事件丢了，按会话存储自愈
        stopBusyWatchdog();
        setBusy(false);
        if (snap.session_id) await openSession(snap.session_id);   // 从会话存储重渲染（回复不会丢）
        const llm = await call("llm_status");
        if (llm) { state.llm = llm; renderPosture({ model: llm.model }); }
        await refreshSessions();
        toast("检测到状态不同步，已自动恢复");
      } catch (err) { /* 桥接抖动：下个周期再试 */ }
    }, 3000);
  }

  function stopBusyWatchdog() {
    if (watchdogTimer) { clearInterval(watchdogTimer); watchdogTimer = null; }
  }

  function setStatus(text) { $("status-line").textContent = text || ""; }

  /* ── 极简闪发浮条（折叠态）──────────────────────────
     旧 Qt 界面「极简闪发折叠模式」的迁移：
       · 折叠后窗口本身缩成一条浮条（几何由 Python 侧计算：底边对齐 + 水平居中）
       · 浮条内输入回车 = 自动展开完整界面并发送（与旧实现一致）
       · 浮条常显一行状态，折叠时也能看出"是否在推理" */
  function miniOn() { return document.body.classList.contains("is-mini"); }

  function setMiniStatus(text, busy) {
    const el = $("mini-status");
    if (!el) return;
    el.textContent = text || "";
    el.classList.toggle("is-busy", !!busy);
  }

  async function setMini(on) {
    if (on === miniOn()) return;
    document.body.classList.toggle("is-mini", on);
    const ok = await call("window_action", on ? "mini" : "restore_window");
    if (ok === false) {
      // 窗口没动成就别让界面假装已折叠（否则用户会看到"界面没了但窗口还在"）
      document.body.classList.toggle("is-mini", !on);
      toast("窗口折叠失败：可能是系统不允许改变窗口尺寸");
      return;
    }
    if (on) {
      setMiniStatus(state.busy ? "推理中…" : "", state.busy);
      $("mini-input").focus();
    } else {
      $("mini-input").value = "";
      $("mini-input").focus();
    }
  }

  async function sendText(text) {
    text = (text || "").trim();
    if (!text) return;
    appendUser(text);
    state.startedAt = Date.now();
    setBusy(true);
    const ok = await call("send", text);
    if (!ok) { setBusy(false); toast("发送失败：算力未就绪或正在推理"); }
    else await refreshSessions();
  }

  /** 浮条闪发：展开 → 发送（顺序与旧实现一致，避免在浮条里发送后看不到结果） */
  async function miniSubmit() {
    const input = $("mini-input");
    const text = (input.value || "").trim();
    if (!text) return;
    input.value = "";
    if (state.busy) { toast("正在推理中，请稍候或先停止"); return; }
    if (miniOn()) await setMini(false);
    await sendText(text);
  }

  /* ── 审批弹窗 ─────────────────────────────────────── */
  function formatPayload(args) {
    if (!args || typeof args !== "object") return "";
    if (args.command) return `【即将执行的命令】\n${args.command}`;
    const parts = [];
    if (args.content) parts.push(`【即将写入的内容（共 ${String(args.content).length} 字）】\n${args.content}`);
    if (args.new_content) parts.push(`【替换后的内容】\n${args.new_content}`);
    if (args.old_content) parts.push(`【将被替换的旧内容】\n${args.old_content}`);
    if (args.filename) parts.push(`【目标文件】\n${args.filename}`);
    if (!parts.length) parts.push(`【工具参数】\n${JSON.stringify(args, null, 2)}`);
    return parts.join("\n\n");
  }

  function showApproval(payload) {
    renderAdvisories([]);   // 建议是异步补发的（本地模型判断可能慢），先清空
    $("approval-tool").textContent = payload.name || "—";
    $("approval-path").textContent = payload.path || "—";
    let text = formatPayload(payload.args);
    // 防盲签：解释器命令展示脚本内容预览（后端在审批前读好；无此字段则不显示）
    if (payload.script_preview) {
      text += `\n\n【脚本内容预览（执行前请核对）】\n${payload.script_preview}`;
    }
    const wrap = $("approval-payload-wrap");
    if (text) { $("approval-payload").textContent = text.slice(0, 2400); wrap.style.display = ""; }
    else wrap.style.display = "none";
    $("approval-root").classList.add("is-open");
    setStatus("等待人工合规审批…");
  }
  function hideApproval() {
    $("approval-root").classList.remove("is-open");
    renderAdvisories([]);
  }

  /* ── 附件条 ───────────────────────────────────── */
  function renderChips() {
    const box = $("chips");
    box.innerHTML = "";
    for (const item of state.attachments) {
      const chip = document.createElement("span");
      chip.className = "chip" + (item.error ? " chip--bad" : "");
      const size = item.chars ? `${(item.chars / 1000).toFixed(1)}k 字` : (item.error || "空");
      // 解析来源与隐私风险报告要能看见：插件解析的附件与内核解析走同一条脱敏管线，
      // 用户有权知道是谁解析的；报告里的部分掩码片段只在此处展示，不进模型上下文
      const tips = [];
      if (item.via) tips.push(`解析来源：${item.via}`);
      if (item.report) tips.push(`隐私风险报告（仅本地提示，不进模型）：\n${item.report}`);
      const title = tips.length ? ` title="${esc(tips.join("\n\n"))}"` : "";
      const flag = item.report ? `<span class="chip__flag" title="含敏感信息（已脱敏）">⚠</span>` : "";
      chip.innerHTML = `<span class="chip__ico"${title}>` + CLIP_ICON + `</span><span>${esc(item.name)}</span>` + flag +
        `<span class="chip__size">${esc(size)}</span><span class="chip__x" title="移除">×</span>`;
      chip.querySelector(".chip__x").onclick = async () => {
        await call("clear_attachments");
        state.attachments = [];
        renderChips();
      };
      box.appendChild(chip);
    }
  }

  /* ── 审计面板 ─────────────────────────────────── */
  const SEV_CLASS = { warning: "audit-item__type--warn", critical: "audit-item__type--crit", security: "audit-item__type--crit" };

  function renderAudit(data) {
    state.audit = data || { stats: {}, chain_ok: false, entries: [] };
    const stats = state.audit.stats || {};
    const ok = !!state.audit.chain_ok;
    const badge = $("audit-chain");
    badge.className = "chain-badge " + (ok ? "chain-badge--ok" : "chain-badge--bad");
    badge.textContent = ok
      ? "✔ 哈希链完整性验证通过（HMAC-SHA256 链式校验 + 链锚点）"
      : "⚠︎ 哈希链校验未通过";
    // 校验未通过时给出**人话原因**，并在"锚点缺失"这一种情况下给出可执行动作。
    // 只显示 ❌ 会让人无从下手：是数据坏了、还是刚升级需要重建锚点，必须说清楚。
    const reason = String(stats.chain_reason || "");
    const anchor = String(stats.chain_anchor || "none");
    const box = $("audit-chain-detail");
    if (box) {
      const needsAnchor = !ok && reason.includes("锚点缺失");
      box.innerHTML = (ok && !reason) ? "" :
        `<span class="chain-detail__text">${esc(reason || "校验未通过")}</span>` +
        (needsAnchor
          ? `<button class="btn btn--ghost" id="btn-reanchor" title="把当前链作为新的信任起点。确认后，此前的尾部截断将无法再被发现">重建锚点（接受当前链）</button>`
          : "");
      const btn = $("btn-reanchor");
      if (btn) btn.onclick = async () => {
        const res = await call("audit_reanchor", "用户在审计面板确认重建锚点");
        toast(res && res.ok ? `已重建锚点（当前 ${res.count} 条）` : `重建失败：${(res && res.error) || "未知原因"}`);
        renderAudit(await call("audit_snapshot", 300));
      };
    }
    const num = (v) => (v === undefined || v === null ? "—" : String(v));
    const pairs = [
      ["总日志数", num(stats.total_entries)],
      ["工具调用", num(stats.tool_calls)],
      ["隐私事件", num(stats.privacy_events)],
      ["审批决策", num(stats.approvals)],
    ];
    renderPosture(null);
    $("audit-stats").innerHTML = pairs.map(([label, value]) =>
      `<div class="stat"><div class="stat__num">${esc(value)}</div><div class="stat__label">${esc(label)}</div></div>`).join("");
    paintAuditList();
  }

  function paintAuditList() {
    const keyword = ($("audit-filter").value || "").trim().toLowerCase();
    const entries = ((state.audit && state.audit.entries) || []).filter((e) => {
      if (!keyword) return true;
      return [e.type, e.severity, e.message, e.preview].join(" ").toLowerCase().includes(keyword);
    });
    const list = $("audit-list");
    if (!entries.length) { list.innerHTML = `<div class="audit-item__msg" style="color:var(--text-3)">没有匹配的审计事件</div>`; return; }
    list.innerHTML = entries.slice(0, 300).map((e) => `
      <div class="audit-item">
        <div class="audit-item__head">
          <span>${esc((e.time || "").replace("T", " ").slice(0, 19))}</span>
          <span class="audit-item__type ${SEV_CLASS[e.severity] || ""}">${esc(e.type || "")}</span>
          <span>${esc(e.severity || "")}</span>
        </div>
        <div class="audit-item__msg">${esc(e.message || "")}</div>
        ${e.preview ? `<div class="audit-item__msg" style="color:var(--text-2)">${esc(e.preview)}</div>` : ""}
        <div class="audit-item__hash">#${esc(e.hash || "")} ← ${esc(e.prev || "")}</div>
      </div>`).join("");
  }

  /* ── 知识库 ───────────────────────────────────── */
  function renderKb(status) {
    const data = status || {};
    const hint = $("kb-status");
    const available = !!data.available;
    hint.textContent = available
      ? `就绪 · 文档 ${(data.stats && data.stats.total_documents) || (data.docs || []).length} 篇 · 片段 ${(data.stats && data.stats.total_chunks) || "—"} · 模型 ${data.embed_model || "—"}`
      : `不可用：${data.error || "未知原因"}`;
    hint.style.color = available ? "var(--text-3)" : "var(--danger-500)";
    $("btn-kb-add").disabled = !available;
    const list = $("kb-list");
    const docs = data.docs || [];
    if (!docs.length) {
      list.innerHTML = `<div class="kb-item__meta">${available ? "还没有文档，点「添加文档」入库" : ""}</div>`;
      return;
    }
    list.innerHTML = docs.map((doc) => {
      const name = typeof doc === "string" ? doc : (doc.name || doc.file_name || doc.source || "—");
      const meta = typeof doc === "string" ? "" : (doc.chunks ? `${doc.chunks} 片段` : "");
      return `<div class="kb-item"><span class="kb-item__name">${esc(name)}</span>
        <span class="kb-item__meta">${esc(meta)} <span class="chip__x" data-kb="${esc(name)}" title="移除">×</span></span></div>`;
    }).join("");
    list.querySelectorAll("[data-kb]").forEach((el) => {
      el.onclick = async () => {
        const res = await call("kb_remove", el.dataset.kb);
        if (res && res.ok) { toast("已移除"); renderKb(res.status); }
        else toast("移除失败：" + ((res && res.error) || "未知原因"));
      };
    });
  }

  async function refreshKb() { renderKb(await call("kb_status")); }

  /* ── 外部算力（自定义模型）────────────────────── */
  function renderCustomModels(list) {
    const box = $("cm-list");
    const models = Array.isArray(list) ? list : [];
    if (!models.length) { box.innerHTML = `<div class="cm-item__meta">尚未配置外部模型（本地模型无需配置）</div>`; return; }
    box.innerHTML = models.map((m) => `
      <div class="cm-item">
        <span>
          <span class="cm-item__name">${esc(m.name)}</span>
          <span class="cm-item__meta">${esc(m.model_id)} · ${m.has_key ? (m.key_invalid ? "密钥失效" : "密钥已配置") : "无密钥"}</span>
        </span>
        <span class="chip__x" data-cm="${esc(m.name)}" title="移除">×</span>
      </div>`).join("");
    box.querySelectorAll("[data-cm]").forEach((el) => {
      el.onclick = async () => {
        const res = await call("remove_custom_model", el.dataset.cm);
        if (res && res.ok) { toast("已移除"); renderCustomModels(res.models); fillSettings(); }
        else toast("移除失败：" + ((res && res.error) || "未知原因"));
      };
    });
  }

  /* ── 安全工具 ─────────────────────────────────── */
  async function runTool(which) {
    const box = $("tool-result");
    box.style.display = "";
    box.textContent = which === "health" ? "正在扫描…" : "正在分析…";
    const res = which === "health" ? await call("health_scan", "")
                                   : await call("behavior_profile");
    if (!res || !res.ok) { box.textContent = "失败：" + ((res && res.error) || "未知原因"); return; }
    if (which === "health") {
      box.textContent = res.summary || "(无内容)";
    } else {
      const lines = (res.anomalies || []).map((a) =>
        `· [${a.severity || a.level || "提示"}] ${a.description || a.message || JSON.stringify(a)}`);
      const parts = [res.summary || ""];
      if (lines.length) { parts.push("", "【异常检测】"); parts.push(...lines); }
      box.textContent = parts.join(NL);
    }
  }

  /* ── 工作流 ───────────────────────────────────── */
  const WF_STATUS_LABEL = {
    pending: ["待执行", ""], running: ["执行中", "wf-status--run"],
    completed: ["已完成", "wf-status--done"], failed: ["失败", "wf-status--fail"],
    skipped: ["已跳过", ""], waiting_approval: ["待审批", "wf-status--wait"],
  };

  function renderWfTemplates(list) {
    const box = $("wf-templates");
    state.wfTemplates = Array.isArray(list) ? list : [];
    if (!state.wfTemplates.length) { box.innerHTML = `<div class="wf-template__desc">没有可用模板</div>`; return; }
    box.innerHTML = state.wfTemplates.map((t) => `
      <button class="wf-template ${t.id === state.wfTemplateId ? "is-active" : ""}" data-tpl="${esc(t.id)}">
        <span class="wf-template__name">${esc(t.name)}
          ${t.source === "plugin" ? `<span class="plugin-badge plugin-badge--src">插件</span>` : ""}
        </span>
        <span class="wf-template__desc">${esc(t.description || "")}</span>
      </button>`).join("");
    box.querySelectorAll("[data-tpl]").forEach((btn) => {
      btn.onclick = async () => {
        state.wfTemplateId = btn.dataset.tpl;
        renderWfTemplates(state.wfTemplates);
        const res = await call("workflow_start", btn.dataset.tpl,
                               $("wf-path").value || "", $("wf-name").value || "");
        if (!res || !res.ok) { toast("启动失败：" + ((res && res.error) || "未知原因")); return; }
        state.wfInstance = res.instance_id;
        renderWf(res.status);
      };
    });
  }

  function renderWf(status) {
    if (!status || status.error) { toast("工作流状态读取失败"); return; }
    state.wfStatus = status;
    $("wf-run").style.display = "";
    $("wf-run-title").textContent = status.template_name || "执行";
    const p = status.progress || {};
    $("wf-progress").innerHTML = `<span>${p.completed || 0}/${p.total || 0} 步</span>
      <span class="wf-progress__bar"><span class="wf-progress__fill" style="width:${p.percentage || 0}%"></span></span>
      <span>${p.percentage || 0}%</span>`;
    $("wf-steps").innerHTML = (status.steps || []).map((st, i) => {
      const [label, cls] = WF_STATUS_LABEL[st.status] || [st.status, ""];
      const ops = st.status === "waiting_approval"
        ? `<div class="wf-step__ops">
             <button class="btn" data-approve="${esc(st.step_id)}" data-ok="1">审批通过</button>
             <button class="btn btn--danger" data-approve="${esc(st.step_id)}" data-ok="0">驳回</button>
           </div>` : "";
      const result = st.error ? `<div class="wf-step__result">⚠︎ ${esc(st.error)}</div>`
        : (st.result ? `<div class="wf-step__result">${esc(String(st.result).slice(0, 240))}</div>` : "");
      return `<div class="wf-step">
        <div class="wf-step__idx">${pad(i + 1)}</div>
        <div class="wf-step__body">
          <div class="wf-step__name">${esc(st.name)}
            <span class="wf-status ${cls}">${esc(label)}</span>
            ${st.demo ? `<span class="plugin-badge plugin-badge--warn">演示步骤</span>` : ""}
            ${st.approval_type === "manual" ? `<span class="plugin-badge">${st.forced_approval ? "审批由内核强制" : "需审批"}</span>` : ""}
          </div>
          <div class="wf-step__desc">${esc(st.description || "")}</div>
          ${result}
        </div>
        ${ops}
      </div>`;
    }).join("");
    $("wf-steps").querySelectorAll("[data-approve]").forEach((btn) => {
      btn.onclick = async () => {
        const res = await call("workflow_approve", state.wfInstance, btn.dataset.approve, btn.dataset.ok === "1");
        if (res && res.ok) { toast(btn.dataset.ok === "1" ? "已通过审批" : "已驳回"); renderWf(res.status); }
        else toast("操作失败：" + ((res && res.error) || "该步骤当前不在待审批状态"));
      };
    });
    const demo = (status.steps || []).filter((x) => x.demo).length;
    $("wf-notice").innerHTML = [
      status.demo_notice || "",
      demo ? `本模板含 ${demo} 个演示步骤。` : "",
      "工作流实例仅保存在内存中，重启后不保留；导出记录落盘前已脱敏。",
    ].filter(Boolean).join(" ");
  }

  async function refreshWorkflow() {
    if (!state.wfTemplates) renderWfTemplates(await call("workflow_templates"));
    else renderWfTemplates(state.wfTemplates);
  }

  /* ── 插件（能力扩展）────────────────────────────── */
  const SOURCE_LABEL = { builtin: "内置", user: "用户安装" };
  const PERM_LABEL = { "local-only": "仅本机网络", none: "无网络" };
  const KIND_LABEL = { advisory: "建议型", additive: "增强型" };

  // 自检结果缓存：{plugin_id: {hook: {ok, error}}}
  let pluginCheck = null;

  function renderPlugins(list) {
    const box = $("plugin-list");
    const plugins = Array.isArray(list) ? list : [];
    if (!plugins.length) {
      box.innerHTML = `<div class="plugin-item__desc">没有发现插件</div>`;
      return;
    }
    box.innerHTML = plugins.map((p) => {
      const caps = Array.isArray(p.capabilities) ? p.capabilities : [];
      const badges = [
        `<span class="plugin-badge plugin-badge--src">${esc(SOURCE_LABEL[p.source] || p.source)}</span>`,
        ...caps.map((c) => `<span class="plugin-badge">${esc(KIND_LABEL[c.kind] || c.kind)}</span>`),
        p.permissions && p.permissions.network ? `<span class="plugin-badge">${esc(PERM_LABEL[p.permissions.network] || p.permissions.network)}</span>` : "",
        (p.missing && p.missing.length) ? `<span class="plugin-badge plugin-badge--warn">依赖未满足</span>` : "",
        p.error ? `<span class="plugin-badge plugin-badge--warn">${esc(p.error)}</span>` : "",
      ].filter(Boolean).join("");
      const disabled = !p.usable || !!p.error;
      // 能力清单：把"这个插件到底提供了什么"逐条写出来，而不是让人猜
      const capList = caps.map((c) => {
        const check = pluginCheck && pluginCheck[p.id] && pluginCheck[p.id][c.hook];
        const state = check ? (check.ok ? `<span class="cap__ok">自检通过</span>`
                                       : `<span class="cap__bad">${esc(check.error || "自检未通过")}</span>`) : "";
        return `<div class="cap"><span class="cap__hook">${esc(c.hook)}</span>
          <span class="cap__text">${esc(c.summary)}${c.detail ? `　·　${esc(c.detail)}` : ""}</span>${state}</div>`;
      }).join("");
      // 可编辑配置（如敏感词表）：直接嵌在卡片里，改完立即生效
      const cfgFiles = Array.isArray(p.config_files) ? p.config_files : [];
      const cfg = cfgFiles.map((f) => `
        <div class="plugin-cfg" data-cfg-for="${esc(p.id)}" data-cfg-name="${esc(f.name)}" hidden>
          <div class="plugin-cfg__head">
            <span>${esc(f.label || f.name)}<span class="plugin-item__ver">${esc(f.name)}</span></span>
            <button class="btn btn--ghost" data-cfg-toggle>收起</button>
          </div>
          <textarea class="plugin-cfg__text" rows="7" spellcheck="false"></textarea>
          <div class="plugin-cfg__ops">
            <span class="field__hint">${esc(f.hint || "")}</span>
            <button class="btn btn--primary" data-cfg-save>保存并生效</button>
          </div>
        </div>`).join("");
      const cfgBtns = cfgFiles.map((f) =>
        `<button class="btn btn--ghost" data-cfg-open="${esc(f.name)}" data-cfg-plugin="${esc(p.id)}">编辑${esc(f.label || f.name)}</button>`
      ).join("");
      // 运行状态：回答"启用之后它到底有没有在工作"——只有真正被调用过才算数
      const rt = p.runtime || {};
      let runText, runCls;
      if (!p.enabled) {
        runText = "未启用"; runCls = "is-idle";
      } else if (!rt.calls) {
        runText = "等待触发（出现相关场景时自动运行）"; runCls = "is-idle";
      } else {
        const t = rt.ts ? new Date(rt.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }) : "—";
        const st = rt.status === "ok" ? "正常"
          : rt.status === "empty" ? "本次无内容"
          : rt.status === "timeout" ? "超时被跳过"
          : rt.status === "error" ? "出错" : "—";
        runText = `已触发 ${rt.calls} 次 · 最近 ${t} · ${st}` +
          (rt.status === "error" && rt.error ? `：${esc(rt.error)}` : "");
        runCls = (rt.status === "error" || rt.status === "timeout") ? "is-bad" : "is-ok";
      }
      return `<div class="plugin-item">
        <div class="plugin-item__head">
          <span class="plugin-item__name">${esc(p.name)}<span class="plugin-item__ver">v${esc(p.version)}</span></span>
          <label class="switch"><input type="checkbox" data-plugin="${esc(p.id)}" ${p.enabled ? "checked" : ""} ${disabled ? "disabled" : ""}><span class="switch__track"></span></label>
        </div>
        <div class="plugin-item__runtime ${runCls}" title="只统计实际被调用的次数；未触发的插件不代表失效，而是还没遇到对应场景">运行状态：${runText}</div>
        <div class="plugin-item__desc" title="${esc(p.description || "")}">${esc(p.description || "")}</div>
        <div class="plugin-item__meta">${badges}</div>
        ${capList ? `<div class="plugin-caps">${capList}</div>` : ""}
        ${(p.missing && p.missing.length) ? `<div class="plugin-item__desc">${p.missing.map(esc).join("<br>")}</div>` : ""}
        ${cfgBtns ? `<div class="plugin-item__ops">${cfgBtns}</div>` : ""}
        ${cfg}
      </div>`;
    }).join("");
    box.querySelectorAll("[data-plugin]").forEach((input) => {
      input.onchange = async () => {
        const res = await call("plugin_set_enabled", input.dataset.plugin, input.checked);
        if (res && res.ok) {
          toast(input.checked ? "插件已启用（已写入审计）" : "插件已停用");
          renderPlugins(res.plugins);
          refreshSyncState();
          refreshExportFormats();
        } else {
          toast("操作失败：" + ((res && res.error) || "未知原因"));
          input.checked = !input.checked;
        }
      };
    });
    // 打开配置编辑器：内容按需读取（不预读，避免一次拉一堆文件）
    box.querySelectorAll("[data-cfg-open]").forEach((btn) => {
      btn.onclick = async () => {
        const holder = box.querySelector(
          `[data-cfg-for="${btn.dataset.cfgPlugin}"][data-cfg-name="${btn.dataset.cfgOpen}"]`);
        if (!holder) return;
        const res = await call("plugin_config_read", btn.dataset.cfgPlugin, btn.dataset.cfgOpen);
        if (!res || !res.ok) { toast("读取失败：" + ((res && res.error) || "未知原因")); return; }
        holder.hidden = false;
        holder.querySelector(".plugin-cfg__text").value = res.text || "";
        holder.querySelector(".plugin-cfg__text").dataset.path = res.path || "";
      };
    });
    box.querySelectorAll("[data-cfg-toggle]").forEach((btn) => {
      btn.onclick = () => { btn.closest(".plugin-cfg").hidden = true; };
    });
    box.querySelectorAll("[data-cfg-save]").forEach((btn) => {
      btn.onclick = async () => {
        const holder = btn.closest(".plugin-cfg");
        const res = await call("plugin_config_write", holder.dataset.cfgFor,
                               holder.dataset.cfgName, holder.querySelector(".plugin-cfg__text").value);
        if (res && res.ok) {
          toast("已保存并立即生效（改动已写入审计）");
          refreshSyncState();
        } else {
          toast("保存失败：" + ((res && res.error) || "未知原因"));
        }
      };
    });
  }

  async function refreshPlugins() { renderPlugins(await call("plugins_list")); }

  async function refreshSyncState() {
    const el = $("plugin-sync");
    if (!el) return;
    try {
      const sync = await call("plugin_sync_state");
      const parts = [];
      if (sync.recognizer) parts.push("追加识别已挂载（脱敏范围含插件词表）");
      if (sync.templates && sync.templates.length) parts.push(`插件模板 ${sync.templates.length} 个已注册`);
      if (sync.errors && sync.errors.length) parts.push(`⚠︎ ${sync.errors.join("；")}`);
      el.textContent = parts.length ? "能力同步：" + parts.join("；") : "能力同步：当前没有插件能力生效（全部为内核自带能力）";
    } catch (err) {
      el.textContent = "能力同步：读取失败";
    }
  }

  // 插件自检：点一次就知道"启用了的插件到底能不能跑"，不靠猜
  async function runPluginCheck() {
    const res = await call("plugin_selfcheck");
    pluginCheck = {};
    (res && res.plugins ? res.plugins : []).forEach((item) => { pluginCheck[item.id] = item.hooks || {}; });
    const bad = (res && res.plugins ? res.plugins : []).filter((x) => !x.ok && !x.skipped);
    toast(res && res.ok ? "插件自检通过：所有声明的钩子都有对应实现"
                        : `插件自检发现问题：${bad.map((x) => x.name).join("、") || "见插件卡片"}`);
    renderPlugins(await call("plugins_list"));
  }

  /* ── 插件建议（审批弹窗内）──────────────────────── */
  function renderAdvisories(list) {
    const wrap = $("approval-advisory-wrap");
    const items = Array.isArray(list) ? list : [];
    if (!items.length) { wrap.style.display = "none"; return; }
    $("approval-advisory").innerHTML = items.map((a) => `
      <div class="advisory__item ${a.level === "warn" ? "advisory__item--warn" : ""}">
        <span class="advisory__src">${esc(a.source || "插件")}</span>${esc(a.text || "")}
      </div>`).join("");
    wrap.style.display = "";
  }

  /* ── 三栏布局：左栏 / 右面板 折叠与切换 ─────────────── */
  const PANE_LOADERS = {
    settings: async () => {
      const info = await call("llm_status");
      if (info) state.llm = info;
      fillSettings(); bindWindowControls(); refreshKb(); refreshPlugins();
      refreshSyncState(); refreshExportFormats();
      renderCustomModels(await call("custom_models"));
    },
    audit: async () => { renderAudit(await call("audit_snapshot", 300)); },
    workflow: async () => { await refreshWorkflow(); },
  };

  /* ── 三栏宽度（可拖拽调整 + 折叠，状态持久化）─────────
     单一出口原则：**宽度只由 JS 写进 .app 的 inline grid-template-columns**，
     CSS 类只作状态标记（供"折叠态隐藏某块""迷你浮条"等样式用），不再各自定义列宽——
     否则"类里的列宽"与"行内列宽"会互相覆盖，出现"拖了没反应""折叠后又跳回来"。 */
  const LAYOUT = {
    left: { min: 200, max: 420, def: 248 },
    right: { min: 300, max: 640, def: 400 },
    centerMin: 420,          // 对话区保底宽度：两侧都展开时不允许把它挤没
  };
  const layoutState = { left: LAYOUT.left.def, right: LAYOUT.right.def };
  // 因"窗口太窄"被自动收起的右栏：窗口重新变宽时应当自动恢复。
  // 与"用户主动收起"区分开——用户主动收起的，窗口再宽也不该擅自展开。
  let autoCollapsedRight = false;
  const NARROW_W = 1180;

  function collapsed(side) {
    return document.querySelector(".app").classList.contains(`is-${side}-collapsed`);
  }

  /** 把当前状态写成栅格列宽（折叠 = 0px），并把拖拽手柄挪到列边界上 */
  function applyLayout() {
    const app = document.querySelector(".app");
    if (!app) return;
    const leftW = collapsed("left") ? 0 : layoutState.left;
    const rightW = collapsed("right") ? 0 : layoutState.right;
    app.style.gridTemplateColumns = `${leftW}px minmax(0, 1fr) ${rightW}px`;
    document.querySelectorAll("[data-resize]").forEach((handle) => {
      const isLeft = handle.dataset.resize === "left";
      handle.hidden = collapsed(isLeft ? "left" : "right");
      if (isLeft) { handle.style.left = Math.max(0, leftW - 5) + "px"; handle.style.right = ""; }
      else { handle.style.right = Math.max(0, rightW - 5) + "px"; handle.style.left = ""; }
    });
  }

  /** 收敛到合法范围：单侧上下限 + 中心区保底（窄窗口时优先让出右栏） */
  function clampLayout() {
    layoutState.left = Math.min(LAYOUT.left.max, Math.max(LAYOUT.left.min, Math.round(layoutState.left)));
    layoutState.right = Math.min(LAYOUT.right.max, Math.max(LAYOUT.right.min, Math.round(layoutState.right)));
    const used = (collapsed("left") ? 0 : layoutState.left) + (collapsed("right") ? 0 : layoutState.right);
    const room = window.innerWidth - LAYOUT.centerMin;
    if (used > room) {
      let over = used - room;
      if (!collapsed("right")) {
        const canGive = Math.min(over, layoutState.right - LAYOUT.right.min);
        layoutState.right -= canGive; over -= canGive;
      }
      if (over > 0 && !collapsed("left")) {
        const canGive = Math.min(over, layoutState.left - LAYOUT.left.min);
        layoutState.left -= canGive;
      }
    }
  }

  function saveLayout() {
    try {
      localStorage.setItem("yd_layout", JSON.stringify({ left: layoutState.left, right: layoutState.right }));
    } catch (e) {}
  }

  /** 只读布局快照：供自动化校验（无头/驱动脚本）直接读状态，不必从像素反推 */
  window.__ydLayout = () => {
    const app = document.querySelector(".app");
    return {
      left: layoutState.left, right: layoutState.right,
      leftCollapsed: collapsed("left"), rightCollapsed: collapsed("right"),
      cols: app ? app.style.gridTemplateColumns : "",
      innerWidth: window.innerWidth,
      limits: LAYOUT,
    };
  };

  function loadLayout() {
    try {
      const raw = localStorage.getItem("yd_layout");
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved && Number(saved.left) > 0) layoutState.left = Number(saved.left);
      if (saved && Number(saved.right) > 0) layoutState.right = Number(saved.right);
    } catch (e) {}
  }

  /** 拖拽手柄：拖动改宽度、双击复位、方向键微调（Shift 加速）
   *  按下时把 pointermove/pointerup 挂到 window 上（而不是靠 setPointerCapture）：
   *  指针一旦移出手柄，挂在手柄上的监听就收不到事件了，拖拽会"中途断掉"。 */
  function bindResizeHandles() {
    document.querySelectorAll("[data-resize]").forEach((handle) => {
      const side = handle.dataset.resize;              // left | right
      handle.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        const app = document.querySelector(".app");
        const startX = e.clientX;
        const startW = layoutState[side];
        app.classList.add("is-resizing");
        const onMove = (ev) => {
          // 左栏向右拖 = 变宽；右栏向左拖 = 变宽（对称手感）
          const delta = side === "left" ? ev.clientX - startX : startX - ev.clientX;
          layoutState[side] = startW + delta;
          clampLayout(); applyLayout();
        };
        const onUp = () => {
          window.removeEventListener("pointermove", onMove);
          window.removeEventListener("pointerup", onUp);
          window.removeEventListener("pointercancel", onUp);
          app.classList.remove("is-resizing");
          saveLayout();
        };
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        window.addEventListener("pointercancel", onUp);
      });
      handle.addEventListener("dblclick", () => {
        layoutState[side] = LAYOUT[side].def;
        clampLayout(); applyLayout(); saveLayout();
        toast(`已恢复${side === "left" ? "会话栏" : "面板"}默认宽度`);
      });
      handle.addEventListener("keydown", (e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const step = (e.shiftKey ? 40 : 16) * (side === "left" ? 1 : -1);
        layoutState[side] += (e.key === "ArrowRight" ? step : -step);
        clampLayout(); applyLayout(); saveLayout();
      });
    });
  }

  /* ── 无边框窗口：拖边缘改尺寸 ──────────────────────────────
     frameless 窗口没有系统 resize 边框，改尺寸入口是窗口外缘的 8 向热区
     （index.html #edge-zone，仅在 html[data-frameless="1"] 且非折叠态显示）：
     pointerdown 取起点几何（window_bounds，逻辑像素，与 window.move/resize 同单位），
     之后每次 move 只把"相对起点的总位移"发给 window_resize_edge——
     **对侧边钉住与最小尺寸 clamp 全在后端**（window_layout.resize_edge，被回归测试锁住），
     前端不复制这套算术，避免两处 clamp/锚点规则漂移。
     三个不能忘（前两个都是"严重抖动"的实测根因）：
       · 位移必须用**屏幕坐标**（screenX/screenY）：clientX 相对视口原点，而 w/n 边拖拽
         会移动窗口本身 → 视口原点跟着移动 → clientX 混入"窗口位移"形成正反馈，
         窗口边缘永远追不上鼠标、来回振荡（修复前拖 w/n 边疯狂抖动的根因）；
         screenX 以屏幕为原点，不随窗口移动变化。
       · 热区的 mousedown 必须 stopPropagation——pywebview 的 easy_drag 监听 window 上的
         mousedown 会"拖走整个窗口"，不吞事件的话"拖边"会变成"拖窗"；
       · 后端用单次 resize(fix_point) 原生调用完成"改尺寸+锚定对侧边"（不再 move+resize
         两次 SetWindowPos，两次之间的中间态是抖动另一来源），拖拽期间 body 加
         is-edge-resizing 禁止文本选中，防止重排时闪选。 */
  function bindEdgeResize() {
    document.querySelectorAll("#edge-zone [data-edge]").forEach((el) => {
      const dir = el.dataset.edge;
      el.addEventListener("mousedown", (e) => { e.stopPropagation(); });
      el.addEventListener("pointerdown", (e) => {
        if (e.button !== 0) return;
        e.preventDefault(); e.stopPropagation();
        call("window_bounds").then((b) => {
          if (!b || !b.ok || typeof b.x !== "number") return;
          const start = { x: b.x, y: b.y, w: b.w, h: b.h };
          const sx = e.screenX, sy = e.screenY;   // 屏幕坐标：不随窗口移动变化（见上）
          let last = null, raf = 0;
          const apply = (dx, dy) => {
            call("window_resize_edge", dir, Math.round(dx), Math.round(dy), start.x, start.y, start.w, start.h);
          };
          const flush = () => { raf = 0; if (last) { const d = last; last = null; apply(d.dx, d.dy); } };
          const onMove = (ev) => {
            last = { dx: ev.screenX - sx, dy: ev.screenY - sy };
            if (!raf) raf = requestAnimationFrame(flush);   // 每帧最多一次桥接调用
          };
          const onUp = () => {
            window.removeEventListener("pointermove", onMove);
            window.removeEventListener("pointerup", onUp);
            window.removeEventListener("pointercancel", onUp);
            document.body.classList.remove("is-edge-resizing");
            if (raf) { cancelAnimationFrame(raf); raf = 0; }
            if (last) { apply(last.dx, last.dy); last = null; }   // 收尾一次，保证最后一帧落位
          };
          document.body.classList.add("is-edge-resizing");
          window.addEventListener("pointermove", onMove);
          window.addEventListener("pointerup", onUp);
          window.addEventListener("pointercancel", onUp);
        });
      });
    });
  }

  function setLeftCollapsed(isCollapsed) {
    document.querySelector(".app").classList.toggle("is-left-collapsed", isCollapsed);
    try { localStorage.setItem("yd_left_collapsed", isCollapsed ? "1" : "0"); } catch (e) {}
    applyLayout();
  }

  /** 收起/展开右栏。persist=false 用于"因窗口窄而自动收起"——
   *  自动行为不能被当成用户意图存下来，否则"临时窄一下"会被永久记住。 */
  function setRightCollapsed(isCollapsed, persist = true) {
    document.querySelector(".app").classList.toggle("is-right-collapsed", isCollapsed);
    if (persist) {
      try { localStorage.setItem("yd_right_collapsed", isCollapsed ? "1" : "0"); } catch (e) {}
    }
    applyLayout();
  }

  function rightCollapsed() {
    return collapsed("right");
  }

  /** 打开右侧面板的某一页（若已收起则展开） */
  async function openPane(name) {
    document.querySelectorAll(".ptab").forEach((t) => t.classList.toggle("is-active", t.dataset.pane === name));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("is-active", p.id === "pane-" + name));
    state.pane = name;
    setRightCollapsed(false);
    await renderPanelActions(name);
    const loader = PANE_LOADERS[name];
    if (loader) await loader();
  }

  /** 面板顶部动作区：按当前页显示导出入口（格式来自内核 + 已启用插件） */
  async function renderPanelActions(name) {
    const box = $("panel-actions");
    if (!box) return;
    box.innerHTML = "";
    if (name === "audit") {
      const formats = state.auditFormats || await refreshExportFormats();
      (formats || []).forEach((f) => {
        const btn = document.createElement("button");
        btn.className = "btn btn--ghost";
        btn.textContent = f.label;
        btn.title = f.source === "内核" ? "内核自带格式（始终可用）" : `由插件提供：${f.source}`;
        btn.onclick = async () => {
          const res = await call("audit_export", f.format);
          toast(typeof res === "string" && res.startsWith("导出失败") ? res : `已导出：${res}`);
        };
        box.appendChild(btn);
      });
    } else if (name === "workflow" && state.wfInstance) {
      const btn = document.createElement("button");
      btn.className = "btn btn--ghost";
      btn.textContent = "导出记录";
      btn.onclick = () => $("btn-wf-export").click();
      box.appendChild(btn);
    }
  }

  /** 拉取可用导出格式（审计 + 工作流），供面板动作区与工作流导出下拉使用 */
  async function refreshExportFormats() {
    try {
      state.auditFormats = await call("audit_export_formats");
      state.wfFormats = await call("workflow_export_formats");
    } catch (err) {
      state.auditFormats = state.auditFormats || [];
      state.wfFormats = state.wfFormats || [];
    }
    const sel = $("wf-format");
    if (sel) {
      const keep = sel.value;
      sel.innerHTML = (state.wfFormats || []).map((f) =>
        `<option value="${esc(f.format)}">${esc(f.label)}</option>`).join("");
      if (keep) sel.value = keep;
    }
    return state.auditFormats;
  }

  function restoreLayout() {
    let left = false, right = false;
    try {
      left = localStorage.getItem("yd_left_collapsed") === "1";
      right = localStorage.getItem("yd_right_collapsed") === "1";
    } catch (e) {}
    loadLayout();                 // 恢复上次拖出来的三栏宽度
    // 窄窗口默认收起右栏，把空间让给对话（记为"自动收起"，窗口变宽时自动恢复；不写偏好）
    if (window.innerWidth < NARROW_W) { right = true; autoCollapsedRight = true; }
    document.querySelector(".app").classList.toggle("is-left-collapsed", left);
    document.querySelector(".app").classList.toggle("is-right-collapsed", right);
    bindResizeHandles();
    bindEdgeResize();            // 无边框窗口：拖边缘改尺寸（frameless 模式下热区才可见）
    clampLayout();
    applyLayout();
  }

  /** 窗口尺寸变化：窄了自动收右栏、宽了自动放回来（只对"自动收起"生效，且不写偏好） */
  function onViewportResize() {
    if (window.innerWidth < NARROW_W && !rightCollapsed()) {
      autoCollapsedRight = true;
      setRightCollapsed(true, false);
    } else if (window.innerWidth >= NARROW_W && autoCollapsedRight && rightCollapsed()) {
      autoCollapsedRight = false;
      setRightCollapsed(false, false);
    }
    clampLayout();          // 窗口变窄时收敛列宽，别把对话区挤没
    applyLayout();
  }

  /* ── 窗口控制 ─────────────────────────────────── */
  function bindWindowControls() {
    const frameless = !!(state.settings && state.settings.frameless);
    document.documentElement.dataset.frameless = frameless ? "1" : "0";
    document.querySelectorAll("[data-win]").forEach((btn) => {
      btn.onclick = async () => {
        const action = btn.dataset.win;
        const ok = await call("window_action", action);
        // 置顶现在是即时生效的（复测确认运行时改 on_top 会作用到原生窗口）：
        // 成功就按实际结果提示，失败则如实说"重启后生效"，不假装成功。
        if (action === "toggle_top") {
          if (ok === false) { toast("置顶切换失败"); return; }
          state.settings.topmost = !state.settings.topmost;
          toast(state.settings.topmost ? "已置顶显示（立即生效）" : "已取消置顶（立即生效）");
          const sw = $("sw-topmost");
          if (sw) sw.checked = !!state.settings.topmost;
        }
      };
    });
  }

  /* ── 设置抽屉 ─────────────────────────────────────── */
  function fillSettings() {
    const s = state.settings || {};
    // 模型：自绘下拉（触发器+浮层），列表来自本机探测 / 缓存；
    // 搜索框同时承担"过滤"与"手输"——内网/远程 Ollama 的模型本机探测不到，直接输入即可。
    const models = (state.llm.models && state.llm.models.length) ? state.llm.models : (s.ollama_models_cache || []);
    state.modelOptions = models;
    $("model-value").textContent = s.model || "未选择";
    if (!models.length) $("model-hint").textContent = "未检测到本地模型：请先安装 Ollama 并拉取模型（ollama pull qwen3.5:4b）";
    else $("model-hint").textContent = `已检测到 ${models.length} 个本地模型；内网模型可直接输入`;
    $("ollama-host").value = s.ollama_host || "http://127.0.0.1:11434";

    $("sw-privacy").checked = !!s.privacy;
    $("sw-script-exec").checked = s.allow_script_exec !== false;
    $("sw-topmost").checked = !!s.topmost;
    $("sw-frameless").checked = !!s.frameless;
    $("rng-depth").value = s.thinking_depth ?? 3;
    $("depth-value").textContent = s.thinking_depth ?? 3;
    $("sel-permission").value = s.permission || "完全控制 (读/写/列表)";

    document.documentElement.dataset.theme = s.dark_mode ? "dark" : "light";
    // 注意：无边框模式的窗口控制按钮必须在"设置已加载后"决定是否显示，
    // 因此绑定放在这里（fillSettings 由 bootstrap / 保存设置 / 打开设置页触发），
    // 而不是 DOMContentLoaded —— 那时 settings 还是空的。
    bindWindowControls();
  }

  async function persist(patch) {
    Object.assign(state.settings, patch);
    fillSettings();
    await call("save_settings", patch);
  }

  /* ── 事件：来自 Python ───────────────────────────── */
  const onEvent = (event, payload) => {
    switch (event) {
      case "status": {
        const text = String(payload || "").trim();
        setStatus(text);
        // 折叠态下浮条也要能看出进展（否则用户不知道点没点上）
        if (miniOn()) setMiniStatus(text || (state.busy ? "推理中…" : ""), state.busy);
        // 忙时把过程状态作为紧凑"工具行"追加到当前条目（Codex 式活动流）
        if (text && state.busy) appendToolLine(text);
        break;
      }
      case "intermediate_result":
        // 注意：助手回答现在是"编号记录条目"（.entry__body），不再是 .bubble。
        // 旧选择器会返回 null，导致流式中间结果更新静默失败（界面长时间只显示等待点）。
        ensureStreamBubble().querySelector(".entry__body").innerHTML = md(payload || ""); scrollToEnd(); break;
      case "finished": {
        const tag = (state.settings.think_mode || "").includes("深度") ? "思考" : "快速";
        finalizeBubble(payload || "", tag); setStatus(""); break;
      }
      case "error":
        if (state.streamBubble) finalizeBubble(payload || "出错了", "错误");
        else { const chat = $("chat"); chat.appendChild(bubbleEl("assistant", md("⚠︎ " + (payload || "出错了")))); scrollToEnd(); }
        setStatus(""); break;
      case "need_confirm": showApproval(payload || {}); break;
      case "attachments": state.attachments = Array.isArray(payload) ? payload : []; renderChips(); break;
      case "advisories": renderAdvisories(payload); break;
      case "workflow": {
        if (payload && payload.phase === "start") {
          setStatus(`工作流执行中：${payload.step || ""}`);
        } else if (payload && payload.status) {
          setStatus("");
          if (payload.success === false) toast(`步骤失败：${payload.error || "未知原因"}`);
          if (state.wfInstance === payload.instance_id) renderWf(payload.status);
        }
        break;
      }
      case "approval_expired": hideApproval(); setStatus("审批超时，已按驳回处理"); break;
      case "state": {
        setBusy(!!payload.busy);
        renderStats(payload);
        if (miniOn()) setMiniStatus(payload.busy ? "推理中…" : "已完成", payload.busy);
        if (!payload.busy) {
          hideApproval();
          const sid = payload.session_id;
          if (sid) { state.currentId = sid; }
        }
        break;
      }
      case "llm_status": {
        state.llm = payload || state.llm;
        const ready = !!state.llm.ready;
        $("llm-dot").className = "dot " + (ready ? "dot--ok" : "dot--warn");
        $("llm-label").textContent = ready ? `算力就绪 · ${state.llm.model}` : "算力未就绪（检查 Ollama）";
        fillSettings();
        break;
      }
      default: break;
    }
  };

  /* ── 动作 ─────────────────────────────────────────── */
  async function openSession(id) {
    const data = await call("open_session", id);
    if (!data) return;
    state.currentId = data.id;
    state.messages = data.messages || [];
    const item = state.sessions.find((s) => s.id === id);
    $("chat-title").textContent = item ? item.title : "对话";
    $("session-id-mark").textContent = id ? `NO. ${String(id).slice(0, 8)}` : "";
    renderSessions(); renderMessages();
  }

  async function newSession() {
    const id = await call("new_session");
    await refreshSessions();
    if (id) await openSession(id);
  }

  async function refreshSessions() {
    const list = await call("list_sessions");
    if (Array.isArray(list)) { state.sessions = list; renderSessions(); }
  }

  async function bootstrap() {
    const data = await call("bootstrap");
    if (!data) return;
    state.settings = data.settings || {};
    state.llm = data.llm || state.llm;
    state.sessions = data.sessions || [];
    state.currentId = data.current_session_id || null;
    $("brand-version").textContent = data.version || "";
    const seg = state.settings.think_mode || "快速回答";
    document.querySelectorAll("#mode-switch .seg__btn").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.mode === seg));
    fillSettings(); renderSessions();
    renderStats({ model: state.settings.model, privacy: state.settings.privacy,
                  permission: state.settings.permission, sandbox: state.settings.sandbox });
    call("audit_snapshot", 1).then((snap) => { if (snap) { state.audit = snap; renderPosture(null); } });
    if (state.currentId) await openSession(state.currentId); else renderMessages();
    onEvent("llm_status", state.llm);
    // ★ 首屏右栏默认停在"设置"页，但该页的加载器只在"点击标签页"时触发——
    //   不主动跑一次的话，插件列表 / 知识库状态 / 自定义模型要等用户"切走再切回"才出现，
    //   表现为"设置页是空的、插件（如 laya 风险分级）找不到"（用户实测踩到）。
    //   这里只跑加载器，不动右栏折叠状态（不覆盖用户"手动收起"的偏好）。
    const initialPane = document.querySelector(".pane.is-active");
    const initialName = initialPane ? initialPane.id.replace("pane-", "") : "";
    if (PANE_LOADERS[initialName]) PANE_LOADERS[initialName]();
  }

  /* ── 事件绑定 ─────────────────────────────────────── */
  window.yindun = { onEvent };

  document.addEventListener("DOMContentLoaded", () => {
    $("composer").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (state.busy) { await call("cancel"); return; }
      const input = $("input");
      const text = input.value.trim();
      if (!text) return;
      input.value = ""; input.style.height = "auto";
      await sendText(text);
    });

    // 闪发浮条：回车发送（自动展开）、Esc 展开、按钮发送
    $("btn-mini").onclick = () => setMini(true);
    $("btn-mini-expand").onclick = () => setMini(false);
    $("btn-mini-send").onclick = miniSubmit;
    $("mini-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); miniSubmit(); }
      if (e.key === "Escape") { e.preventDefault(); setMini(false); }
    });
    document.addEventListener("keydown", (e) => {
      // 全局 Esc：折叠态下展开（浮条只有一个输入框，不会与其它 Esc 语义冲突）
      if (e.key === "Escape" && miniOn()) { e.preventDefault(); setMini(false); }
    });

    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault(); $("composer").requestSubmit();
      }
    });
    $("input").addEventListener("input", (e) => {
      const el = e.target; el.style.height = "auto";
      el.style.height = Math.min(el.scrollHeight, 180) + "px";
    });

    $("btn-new-session").onclick = newSession;
    $("btn-kb-add").onclick = async () => {
      toast("正在入库（本地解析 + 逐块脱敏 + 向量化）…", 8000);
      const res = await call("kb_pick_and_add");
      if (res && res.ok) {
        toast("入库完成");
        renderKb(res.status);
      } else if (res && res.error) {
        toast("入库失败：" + res.error);
      }
      await refreshKb();
    };
    $("btn-kb-refresh").onclick = refreshKb;
    bindWindowControls();
    $("btn-attach").onclick = async () => {
      const added = await call("pick_files");
      if (Array.isArray(added)) {
        const bad = added.filter((x) => x.error);
        if (bad.length) toast(`有 ${bad.length} 个附件解析失败：${bad[0].name}`);
        else if (added.length) toast(`已挂载 ${added.length} 个附件（本地解析，不联网）`);
        const list = await call("list_attachments");
        state.attachments = Array.isArray(list) ? list : state.attachments;
        renderChips();
      }
    };
    $("btn-workflow").onclick = () => openPane("workflow");
    $("btn-wf-exec").onclick = async () => {
      if (!state.wfInstance) { toast("请先选择模板"); return; }
      const res = await call("workflow_execute", state.wfInstance);
      if (res && res.ok) setStatus(`正在执行：${res.step}`);
      else toast((res && res.error) || "执行失败");
    };
    $("btn-wf-export").onclick = async () => {
      if (!state.wfInstance) { toast("请先选择模板"); return; }
      const fmt = ($("wf-format") && $("wf-format").value) || "md";
      const res = await call("workflow_export", state.wfInstance, fmt);
      toast(res && res.ok ? `已导出（${res.renderer || "内核"}）：${res.path}`
                          : `导出失败：${(res && res.error) || "未知原因"}`);
    };
    $("btn-cm-add").onclick = async () => {
      const payload = [$("cm-name").value.trim(), $("cm-model").value.trim(),
                       $("cm-url").value.trim(), $("cm-key").value.trim()];
      if (!payload[0] || !payload[1] || !payload[2]) { toast("名称、模型 ID、接口地址都要填"); return; }
      const res = await call("add_custom_model", ...payload);
      if (res && res.ok) {
        toast("已添加外部模型（密钥已加密保存）");
        ["cm-name", "cm-model", "cm-url", "cm-key"].forEach((id) => { $(id).value = ""; });
        renderCustomModels(res.models); fillSettings();
      } else toast("添加失败：" + ((res && res.error) || "未知原因"));
    };
    $("btn-health").onclick = () => runTool("health");
    $("btn-behavior").onclick = () => runTool("behavior");
    $("btn-audit").onclick = () => openPane("audit");
    $("audit-filter").oninput = paintAuditList;
    $("btn-plugin-check").onclick = runPluginCheck;
    $("btn-settings").onclick = () => openPane("settings");
    $("btn-toggle-right").onclick = () => { autoCollapsedRight = false; setRightCollapsed(!rightCollapsed()); };
    $("btn-panel-collapse").onclick = () => { autoCollapsedRight = false; setRightCollapsed(true); };
    $("btn-sidebar").onclick = () => {
      const app = document.querySelector(".app");
      setLeftCollapsed(!app.classList.contains("is-left-collapsed"));
    };
    document.querySelectorAll(".ptab").forEach((tab) => {
      tab.onclick = () => openPane(tab.dataset.pane);
    });
    $("btn-theme").onclick = () => persist({ dark_mode: document.documentElement.dataset.theme !== "dark" });
    $("btn-allow").onclick = async () => { hideApproval(); await call("approve", true); };
    $("btn-deny").onclick = async () => { hideApproval(); await call("approve", false); };

    document.querySelectorAll("#mode-switch .seg__btn").forEach((btn) => {
      btn.onclick = async () => {
        document.querySelectorAll("#mode-switch .seg__btn").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        await persist({ think_mode: btn.dataset.mode });
      };
    });

    $("sw-privacy").onchange = (e) => persist({ privacy: e.target.checked });
    $("sw-script-exec").onchange = (e) => persist({ allow_script_exec: e.target.checked });
    $("sw-topmost").onchange = (e) => persist({ topmost: e.target.checked });
    $("sw-frameless").onchange = (e) => {
      persist({ frameless: e.target.checked });
      toast("无边框悬浮模式将在重启后生效");
    };
    $("sel-permission").onchange = (e) => persist({ permission: e.target.value });
    // 模型下拉（自绘，替代原生 datalist——弹层不随主题、内网手输无回显）：
    // 触发器开合 / 搜索过滤 / 键盘 ↑↓·Enter·Esc / 点选与手输（回车提交非匹配名=内网模型入口）/
    // 点浮层外关闭。选择即 persist（后端立即重建算力）。
    const mSel = $("model-select"), mTrig = $("model-trigger"), mPop = $("model-pop"),
          mSearch = $("model-search"), mList = $("model-list");
    let mHl = -1;                                   // 键盘高亮 index
    const closeModelPop = () => {
      mPop.hidden = true; mTrig.setAttribute("aria-expanded", "false");
      mSel.classList.remove("is-open"); mHl = -1;
    };
    const renderModelList = (kw) => {
      const q = String(kw || "").trim().toLowerCase();
      const opts = (state.modelOptions || []).filter((m) => !q || m.toLowerCase().includes(q));
      const manual = q && !opts.some((m) => m.toLowerCase() === q);
      const cur = state.settings.model || "";
      let html = "";
      if (manual) html += `<button type="button" class="model-opt model-opt--manual" role="option" data-model="${esc(q)}">使用「${esc(q)}」</button>`;
      html += opts.map((m) => `<button type="button" class="model-opt${m === cur ? " is-cur" : ""}" role="option" aria-selected="${m === cur}" data-model="${esc(m)}">${esc(m)}${m === cur ? " ✓" : ""}</button>`).join("");
      mList.innerHTML = html || `<div class="model-opt model-opt--empty">没有匹配的模型</div>`;
      mHl = manual ? 0 : (opts.length ? 0 : -1);
      [...mList.querySelectorAll(".model-opt")].forEach((el, i) => el.classList.toggle("is-hl", i === mHl));
    };
    const openModelPop = () => {
      mPop.hidden = false; mTrig.setAttribute("aria-expanded", "true");
      mSel.classList.add("is-open");
      mSearch.value = ""; renderModelList(""); mSearch.focus();
    };
    mTrig.onclick = () => (mPop.hidden ? openModelPop() : closeModelPop());
    mSearch.oninput = () => renderModelList(mSearch.value);
    mSearch.onkeydown = (e) => {
      const items = [...mList.querySelectorAll(".model-opt:not(.model-opt--empty)")];
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!items.length) return;
        mHl = (mHl + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        [...mList.querySelectorAll(".model-opt")].forEach((el, i) => el.classList.toggle("is-hl", i === mHl));
        items[Math.max(0, mHl)]?.scrollIntoView({ block: "nearest" });
      } else if (e.key === "Enter") {
        e.preventDefault();
        const pick = items[Math.max(0, mHl)] || items[0];
        const name = (pick && pick.dataset.model) || mSearch.value.trim();
        if (name) { persist({ model: name }); closeModelPop(); }
      } else if (e.key === "Escape") {
        closeModelPop();
      }
    };
    mList.onclick = (e) => {
      const btn = e.target.closest("[data-model]");
      if (!btn) return;
      persist({ model: btn.dataset.model });
      closeModelPop();
    };
    document.addEventListener("click", (e) => {
      if (!mSel.contains(e.target)) closeModelPop();
    });
    $("ollama-host").onchange = (e) => {
      const v = String(e.target.value || "").trim() || "http://127.0.0.1:11434";
      e.target.value = v;
      if (v !== state.settings.ollama_host) persist({ ollama_host: v });
    };
    $("rng-depth").oninput = (e) => { $("depth-value").textContent = e.target.value; };
    $("rng-depth").onchange = (e) => persist({ thinking_depth: Number(e.target.value) });

    restoreLayout();
    window.addEventListener("resize", onViewportResize);
    window.addEventListener("pywebviewready", bootstrap);
    // 开发预览：假桥接已就绪，直接走一次 bootstrap（正式运行时该分支不成立）
    if (window.__YINDUN_MOCK__) bootstrap();
    else if (!window.pywebview) renderMessages();   // 浏览器裸开：至少渲染空态
  });
})();
