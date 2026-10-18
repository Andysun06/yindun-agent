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
    catalog: null,           // 三档算力清单（本机 / 局域网 / 公网）
    plugins: [],             // 插件清单（市场页渲染 + 搜索过滤都基于它）
    settingsPage: null,      // 当前设置页标识（判断"插件被停用后要不要退回"用）
    settingsPlugin: null,    // 当前打开的插件设置页属于哪个插件
    sessionPermission: null, // 本对话的权限覆盖（null = 跟随设置默认）
    laya: null,              // laya 接入状态（本机 / 局域网 / 正则回退）
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

    // 后端现在下发档位键（plan/ask/auto/full），这里翻成中文档位名显示（含旧中文名的兼容）
    const permission = (payload && payload.permission) || s.permission || "—";
    $("posture-permission").querySelector("span").textContent =
      (PERM_MODES[permKey(permission)] || {}).label || String(permission).replace(/\s*\(.*\)$/, "");

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
    // 阈值与说明都来自后端预算表（core/context_budget.py）——界面不再自己写数，
    // 也就不会出现"界面说 5000、实际按 8192 压缩"这种对不上账的情况（C-13）。
    const limit = (payload && payload.context_limit) || 5000;
    $("stat-tokens").textContent = `上下文 ${tokens >= 1000 ? (tokens / 1000).toFixed(1) + "k" : tokens}/${limit >= 1000 ? (limit / 1000).toFixed(0) + "k" : limit}`;
    const nearLimit = limit > 0 && tokens / limit >= 0.8;
    $("stat-tokens").classList.toggle("is-warn", nearLimit);
    $("stat-tokens").title = (payload && payload.context_note)
      || `按中英文分权重估算的上下文占用；超过 ${limit} 会触发历史摘要压缩（估算口径与摘要压缩一致）`;
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
      // 注意：entry 是外层 .row，.entry__body 是 .entry 的子节点——
      // 插到 body 的父节点（.entry）里，否则 insertBefore 会抛
      // NotFoundError（工具活动流在真实工具链下一直渲染不出来）。
      (body.parentElement || entry).insertBefore(box, body);
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
  /** 过程轨迹时间线：把"它在想什么、做了什么、凭什么这么判"显性化。
   *  行型三态：思考（耗时）/ 工具（名称+目标+结果）/ 关键判定（隐私网关、laya 风险等）。
   *  设计取舍：轨迹是"看得见但不是主角"——低对比度、等宽元信息、只占一行；
   *  关键判定给一条左侧色条与徽标，扫一眼能注意到，又不会打断正文阅读。 */
  const TRACE_ICON = { thinking: "◍", tool: "▸", judge: "◆", info: "·" };
  const TRACE_STATE = { running: "进行中", ok: "已完成", fail: "失败" };

  function traceRowEl(item) {
    const kind = String(item.kind || "info");
    const row = document.createElement("div");
    row.className = `trace trace--${kind}`;
    if (item.status) row.classList.add("is-" + String(item.status));
    row.dataset.kind = kind;
    row.dataset.name = String(item.name || "");
    if (item.seq) row.dataset.seq = String(item.seq);
    const ms = Number(item.ms || 0);
    const time = ms > 0 ? `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)} 秒` : "";
    const state = TRACE_STATE[item.status] || "";
    row.innerHTML =
      `<span class="trace__ico">${TRACE_ICON[kind] || "·"}</span>` +
      `<span class="trace__name">${esc(item.name || "")}</span>` +
      (item.detail ? `<span class="trace__detail">${esc(item.detail)}</span>` : "") +
      ((state || time) ? `<span class="trace__state">${esc([state, time].filter(Boolean).join(" · "))}</span>` : "");
    return row;
  }

  /** 实时追加：优先就地更新"进行中"的同一动作，避免"调用中/完成"刷成两行 */
  function appendTrace(item) {
    if (!item) return;
    const box = ensureTraceBox(state.streamBubble);
    if (!box) return;
    if (item.status && item.status !== "running") {
      const pending = [...box.querySelectorAll(`.trace[data-kind="${item.kind}"][data-name="${String(item.name || "").replace(/"/g, "")}"].is-running`)].pop();
      if (pending) {
        const fresh = traceRowEl(item);
        fresh.dataset.seq = pending.dataset.seq || "";
        pending.replaceWith(fresh);
        scrollToEnd();
        return;
      }
    }
    box.appendChild(traceRowEl(item));
    scrollToEnd();
  }

  function ensureTraceBox(entry) {
    if (!entry) return null;
    const body = entry.querySelector(".entry__body");
    if (!body) return null;
    let box = entry.querySelector(".entry__trace");
    if (!box) {
      box = document.createElement("div");
      box.className = "entry__trace";
      (body.parentElement || entry).insertBefore(box, body);
    }
    return box;
  }

  /** 载入历史时把连续轨迹归入同一块（刷新/切会话后仍看得见过程） */
  function traceBlockEl() {
    const row = document.createElement("div");
    row.className = "row row--trace";
    row.innerHTML = `<div class="trace-block"></div>`;
    return row;
  }
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
    let index = 0, traceBlock = null;
    for (const m of state.messages) {
      if (m.role === "trace" && m.trace) {
        if (!traceBlock) { traceBlock = traceBlockEl(); chat.appendChild(traceBlock); }
        traceBlock.querySelector(".trace-block").appendChild(traceRowEl(m.trace));
        continue;
      }
      traceBlock = null;                     // 非轨迹消息打断分组
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
      ? "推理中…（可随时点停止）"
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

  /* 两个输入框（主输入 / 浮条）的自动增高上限：浮条所在窗口固定 60px，不能按 180 长 */
  const AUTOSIZE_MAX = { input: 180, "mini-input": 44 };
  function autosizeInput(el) {
    if (!el) return;
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, AUTOSIZE_MAX[el.id] || 180) + "px";
  }

  /** 把草稿从一个输入框**搬**到另一个（复制 + 清空来源，光标落末尾）。
   *  为什么必须搬：主输入框与浮条是两个独立控件，不搬就会出现两种丢字——
   *    ① 折叠时：刚在主输入框打好的字跟着界面一起被隐藏（浮条里空着，只能重打）；
   *    ② 展开时：折叠期间在浮条里打的字被 setMini 清掉（历史上这里确实直接 value=""）。
   *  "复制 + 清空来源"保证任意时刻只有一处持有草稿，来回折叠不会复制出两份。 */
  function carryDraft(toId, fromId) {
    const to = $(toId), from = $(fromId);
    if (!to || !from) return;
    to.value = from.value || "";
    from.value = "";
    from.style.height = "";
    autosizeInput(to);
    try {
      to.focus();
      to.setSelectionRange(to.value.length, to.value.length);
    } catch (e) { /* 元素不可聚焦/不支持选区时忽略：草稿本身已经搬过去 */ }
  }

  /** 清空两份草稿（发送后调用，避免展开时把已发出的内容又搬回来） */
  function clearDrafts() {
    ["input", "mini-input"].forEach((id) => {
      const el = $(id);
      if (el) { el.value = ""; el.style.height = ""; }
    });
  }

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
      // 折叠：把主输入框里的草稿带进浮条——否则刚打的字跟着界面一起消失
      carryDraft("mini-input", "input");
      setMiniStatus(state.busy ? "推理中…" : "", state.busy);
    } else {
      // 展开：把折叠期间在浮条里打的字带回来（旧实现直接清空，属于丢字）
      carryDraft("input", "mini-input");
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
    // 忙判定必须在清空之前：旧写法先 value="" 再判忙，模型推理中按回车
    // 会把用户打的字吞掉且不留痕迹（既没发出去，输入框也空了）。
    if (state.busy) { toast("正在推理中，请稍候或先停止"); return; }
    clearDrafts();
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
      // 后端 kb_status 的文档项用 `file` 字段（list_documents 的结构）；
      // 只认 name/file_name/source 会让列表全部显示成"—"（真机截图暴露）。
      const name = typeof doc === "string" ? doc : (doc.name || doc.file_name || doc.file || doc.source || "—");
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

  /* ══ 算力接入：本机 / 局域网 / 公网 三档卡片 ══════════════════
     分档不是分类癖好，而是**数据边界的说明书**：
       本机 = 数据不出这台电脑；局域网 = 出机器但不出企业；公网 = 出企业，内容已本机脱敏。
     每张卡自带「边界说明 + 现有条目 + 添加表单」，用户不必在字段之间猜关系。 */
  const TIER_META = {
    local: { label: "本机模型", boundary: "数据不出这台电脑", cls: "tier--local",
             hint: "本机运行的 Ollama（默认 127.0.0.1:11434）" },
    lan: { label: "局域网模型", boundary: "数据出这台电脑，但不出企业内网", cls: "tier--lan",
           hint: "公司内网的推理服务 / Ollama，填内网地址（10.x / 192.168.x / 内网主机名）" },
    wan: { label: "公网模型", boundary: "数据出企业内网；送出前内容已在本机脱敏", cls: "tier--wan",
           hint: "云端 OpenAI 兼容接口（https://…/v1）；密钥加密存本机、不回显" },
  };

  /* ══ 思考强度四档 ═══════════════════════════════════════════
     界面给四档（低/中/高/最高），底层仍是 worker 的 thinking_depth(1–10)：
     档位只是把"该调几轮"这件事从数字变成人话。相邻档位的数值区间见 levelOf()。 */
  const THINK_LEVELS = [
    { key: "low",  label: "低",   depth: 2,  hint: "最快：只做一两轮推理，适合闲聊与简单问答" },
    { key: "mid",  label: "中",   depth: 4,  hint: "日常默认：够应付多数指令" },
    { key: "high", label: "高",   depth: 7,  hint: "慢一些：多轮自检，适合改代码 / 排查" },
    { key: "max",  label: "最高", depth: 10, hint: "最慢也最稳：多轮推理，适合复杂任务" },
  ];
  const levelOf = (depth) => {
    const d = Number(depth);
    if (!isFinite(d)) return THINK_LEVELS[0];
    // 旧值（1–10）落到最近的档：<=3 低、<=5 中、<=8 高、其余最高
    if (d <= 3) return THINK_LEVELS[0];
    if (d <= 5) return THINK_LEVELS[1];
    if (d <= 8) return THINK_LEVELS[2];
    return THINK_LEVELS[3];
  };

  /* AI 权限四档的说明文案来自内核 yindun/core/permission.py 的 HINTS/LABELS
     （单一事实来源在 Python 侧；此处同步为界面文案，tests/test_permission.py 会比对）。
     PERM_ORDER 的顺序 = 内核 MODES：由紧到松。 */
  const PERM_ORDER = ["plan", "ask", "auto", "full"];
  const PERM_MODES = {
    plan: { label: "计划模式", hint: "只读分析、给出计划；不写盘、不删除、不执行命令" },
    ask:  { label: "变更前确认", hint: "读操作自动放行；写盘 / 删除 / 执行命令前逐次人工确认（默认，最稳）" },
    auto: { label: "自主模式", hint: "沙箱内的创建与写入自动放行；删除与执行命令仍逐次确认" },
    full: { label: "完全访问", hint: "沙箱内的创建 / 写入 / 删除自动放行；执行命令仍逐次确认（底线不可越）" },
  };
  const permKey = (v) => {
    const s = String(v || "");
    if (PERM_MODES[s]) return s;                       // 新档位名
    if (s.indexOf("彻底审计") >= 0 || s.indexOf("安全只读") >= 0) return "plan";   // 旧中文档位
    if (s.indexOf("完全控制") >= 0) return "ask";
    return "ask";                                      // 空值 / 未知 → 默认档
  };

  /** 渲染「模型接入」页：左侧按三档列供应商，右侧是选中项的详情表单。
   *  范式参考主流 agent：左边管"有哪几个入口"，右边管"这一个怎么配"。 */
  function renderProv(cat) {
    const box = $("prov-list"), detail = $("prov-detail");
    if (!box || !cat || !cat.tiers) return;
    state.catalog = cat;
    const cur = (state.settings && state.settings.model) || cat.current || "";
    const sel = state.prov || (state.prov = { tier: "local", name: "" });
    const items = (t) => cat.tiers[t] || [];

    box.innerHTML = ["local", "lan", "wan"].map((tier) => {
      const meta = TIER_META[tier];
      const rows = items(tier).map((m) => {
        const active = sel.tier === tier && (tier === "local" || sel.name === m.name);
        const meta = tier === "local" ? "本机已安装" : (m.base_url || "");
        return `<button class="prov__item${active ? " is-active" : ""}" type="button"
          data-t="${tier}" data-n="${esc(m.name)}">
          <span class="prov__name">${esc(m.name)}${m.name === cur ? `<i class="prov__cur">使用中</i>` : ""}</span>
          <span class="prov__meta">${esc(meta)}</span>
        </button>`;
      }).join("");
      // 本机档没有"添加"：本机模型由 Ollama 安装决定，探测得到，不是手工添加的对象
      const add = tier === "local" ? "" :
        `<button class="prov__add" type="button" data-addt="${tier}">＋ 添加${meta.label}</button>`;
      return `<div class="prov__group">
        <div class="prov__gtitle"><span class="tier-dot tier-dot--${tier}"></span>
          <b>${meta.label}</b><em>${meta.boundary}</em></div>
        ${rows}
        ${add}
      </div>`;
    }).join("");

    // ── 右侧详情 ──
    if (sel.tier === "local") {
      const models = items("local");
      detail.innerHTML = `
        <h3 class="prov__title">本机 Ollama<span class="prov__badge">数据不出这台电脑</span></h3>
        <p class="prov__desc">本机安装的 Ollama 服务。把地址改成别的机器，数据就会出这台电脑——
          那种情况请改用「局域网模型」显式配置。</p>
        <div class="setcard">
          <div class="setrow">
            <div class="setrow__text"><b>服务地址</b><span>默认 http://127.0.0.1:11434</span></div>
            <div class="setrow__ctrl"><input type="text" id="ollama-host" spellcheck="false"
              autocomplete="off" placeholder="http://127.0.0.1:11434"></div>
          </div>
          <div class="setrow">
            <div class="setrow__text"><b>连通性</b><span id="probe-local">点右侧按钮发一次探测（地址只取自本页输入框）</span></div>
            <div class="setrow__ctrl">
              <button class="btn" type="button" data-probe="local">测试连接</button>
              <button class="btn" type="button" data-rescan="1">重新检测</button>
            </div>
          </div>
        </div>
        <h4 class="prov__subtitle">已检测到的模型<span class="muted">（点一下即切换）</span></h4>
        <div class="prov__models">${models.length ? models.map((m) => `
          <button class="prov__chip${m.name === cur ? " is-cur" : ""}" type="button" data-pick="${esc(m.name)}">
            ${esc(m.name)}${m.name === cur ? " ✓" : ""}</button>`).join("")
          : `<span class="muted">没检测到模型——装好 Ollama 后点「重新检测」</span>`}</div>`;
    } else {
      const list = items(sel.tier);
      const found = sel.name ? list.find((m) => m.name === sel.name) : null;
      const m = found || { name: "", model_id: "", base_url: "", tier: sel.tier, has_key: false };
      detail.innerHTML = `
        <h3 class="prov__title">${found ? esc(m.name) : "添加" + TIER_META[sel.tier].label}
          <span class="prov__badge">${TIER_META[sel.tier].boundary}</span></h3>
        <p class="prov__desc">${TIER_META[sel.tier].hint}；出网内容在本机脱敏之后才发送，密钥加密存本机、不回显。</p>
        <div class="setcard">
          <div class="setrow"><div class="setrow__text"><b>显示名称</b><span>聊天时下拉里显示的名字</span></div>
            <div class="setrow__ctrl"><input type="text" id="p-name" value="${esc(m.name)}"
              placeholder="如 ${sel.tier === "lan" ? "公司内网Qwen" : "云端GLM"}"></div></div>
          <div class="setrow"><div class="setrow__text"><b>模型 ID</b><span>服务端认的模型名（探测通过后可自动填）</span></div>
            <div class="setrow__ctrl"><input type="text" id="p-model" value="${esc(m.model_id || "")}"
              placeholder="${sel.tier === "lan" ? "qwen2.5:14b" : "glm-4-flash"}"></div></div>
          <div class="setrow"><div class="setrow__text"><b>接口地址</b><span>OpenAI 兼容端点</span></div>
            <div class="setrow__ctrl"><input type="text" id="p-url" value="${esc(m.base_url || "")}"
              placeholder="${sel.tier === "lan" ? "http://10.0.0.8:8000/v1" : "https://…/v1"}"></div></div>
          <div class="setrow"><div class="setrow__text"><b>API Key</b><span>${sel.tier === "lan" ? "内网通常可留空" : "只在本机加密保存"}</span></div>
            <div class="setrow__ctrl"><input type="text" id="p-key" value=""
              placeholder="${m.has_key ? "已配置（留空则不改动）" : "未配置"}"></div></div>
          <div class="setrow"><div class="setrow__text"><b>连通性</b><span id="probe-detail">尚未测试</span></div>
            <div class="setrow__ctrl">
              <button class="btn" type="button" data-probe="detail">测试连接</button>
              <button class="btn btn--primary" type="button" data-save="1">保存</button>
              ${found ? `<button class="btn" type="button" data-remove="${esc(m.name)}">删除</button>` : ""}
            </div></div>
        </div>`;
    }
    bindProvEvents(cat);
    paintModelTrigger();
  }

  function bindProvEvents(cat) {
    const box = $("prov-list"), detail = $("prov-detail");
    if (!box || !detail) return;
    box.querySelectorAll("[data-t]").forEach((el) => {
      el.onclick = () => { state.prov = { tier: el.dataset.t, name: el.dataset.n }; renderProv(cat); };
    });
    box.querySelectorAll("[data-addt]").forEach((el) => {
      el.onclick = () => { state.prov = { tier: el.dataset.addt, name: "" }; renderProv(cat); };
    });
    detail.querySelectorAll("[data-pick]").forEach((el) => {
      el.onclick = () => persist({ model: el.dataset.pick });
    });
    const host = $("ollama-host");
    if (host) {
      host.value = (state.settings && state.settings.ollama_host) || "http://127.0.0.1:11434";
      host.onchange = (e) => {
        const v = String(e.target.value || "").trim() || "http://127.0.0.1:11434";
        e.target.value = v;
        persist({ ollama_host: v });
      };
    }
    detail.querySelectorAll("[data-rescan]").forEach((el) => {
      el.onclick = async () => {
        if ($("probe-local")) $("probe-local").textContent = "检测中…";
        renderProv(await call("model_catalog"));
      };
    });
    detail.querySelectorAll("[data-probe]").forEach((el) => {
      el.onclick = async () => {
        const isLocal = el.dataset.probe === "local";
        const url = isLocal ? ($("ollama-host").value || "").trim() : ($("p-url").value || "").trim();
        const key = isLocal ? "" : ($("p-key").value || "").trim();
        const note = isLocal ? $("probe-local") : $("probe-detail");
        note.textContent = "正在测试连接…"; note.className = "";
        const res = await call("probe_model_endpoint", url, key);
        if (res && res.ok) {
          note.textContent = "✅ " + (res.note || "已连通")
            + (res.models && res.models.length ? "：" + res.models.slice(0, 3).join("、") : "");
          note.className = "is-ok";
          if (!isLocal) {
            const mid = $("p-model");
            if (mid && !mid.value.trim() && res.models && res.models.length) mid.value = res.models[0];
          }
        } else {
          note.textContent = "❌ " + ((res && res.error) || "连不上");
          note.className = "is-bad";
        }
      };
    });
    detail.querySelectorAll("[data-save]").forEach((el) => {
      el.onclick = async () => {
        const tier = state.prov.tier;
        const val = (id) => (($(id) && $(id).value) || "").trim();
        const name = val("p-name"), modelId = val("p-model"), url = val("p-url"), key = val("p-key");
        const note = $("probe-detail");
        if (!name || !modelId || !url) {
          note.textContent = "显示名称、模型 ID、接口地址都要填"; note.className = "is-bad"; return;
        }
        const res = await call("add_custom_model", name, modelId, url, key, tier);
        if (res && res.ok) {
          toast(`已保存到「${res.tier_label || tier}」`);
          state.prov = { tier, name };
          renderProv(await call("model_catalog"));
          fillSettings();
        } else {
          note.textContent = "保存失败：" + ((res && res.error) || "未知原因"); note.className = "is-bad";
        }
      };
    });
    detail.querySelectorAll("[data-remove]").forEach((el) => {
      el.onclick = async () => {
        const res = await call("remove_custom_model", el.dataset.remove);
        if (res && res.ok) {
          toast("已删除");
          state.prov = { tier: state.prov.tier, name: "" };
          renderProv(await call("model_catalog"));
          fillSettings();
        } else toast("删除失败：" + ((res && res.error) || "未知原因"));
      };
    });
  }
  function paintModelTrigger() {
    const val = $("model-value"), dot = $("model-tier-dot");
    if (!val) return;
    const cur = (state.settings && state.settings.model) || "";
    val.textContent = cur || "未选择模型";
    if (!dot) return;
    let tier = "";
    const cat = state.catalog;
    if (cat && cat.tiers) {
      for (const t of ["local", "lan", "wan"]) {
        if ((cat.tiers[t] || []).some((m) => m.name === cur)) { tier = t; break; }
      }
    }
    dot.className = "tier-dot" + (tier ? ` tier-dot--${tier}` : "");
    dot.title = tier ? `${TIER_META[tier].label} · ${TIER_META[tier].boundary}` : "未在清单中（可能已手动指定）";
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
    approved: ["已审批，待执行", "wf-status--run"],
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
            ${st.unimplemented ? `<span class="plugin-badge plugin-badge--warn">未实现</span>` : ""}
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
    const todo = (status.steps || []).filter((x) => x.unimplemented).length;
    $("wf-notice").innerHTML = [
      status.impl_notice || "",
      todo ? `本模板含 ${todo} 个未实现步骤。` : "",
      "工作流实例会持久化（重启后可继续执行/审批）；导出记录落盘前已脱敏。",
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

  // 插件图标：统一画一枚拼图（插件＝可拼装的能力）。各插件靠名称区分，
  // 不用"插件名首字"当图标——汉字首字辨识度低，还容易被误读成缩写。
  const PUZZLE_SVG = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 5H10A2 2 0 0 1 14 5H18A1 1 0 0 1 19 6V10A2 2 0 0 0 19 14V18A1 1 0 0 1 18 19H14A2 2 0 0 1 10 19H6A1 1 0 0 1 5 18V14A2 2 0 0 0 5 10V6A1 1 0 0 1 6 5Z"/></svg>`;

  // 自检结果缓存：{plugin_id: {hook: {ok, error}}}
  let pluginCheck = null;

  // 插件分类顺序：没写 category 的落到"扩展"（manifest 里 category 是可选项）
  const CATEGORY_ORDER = ["安全", "文档", "办公", "导出", "扩展"];

  function renderPlugins(list) {
    const box = $("plugin-groups");
    if (!box) return;
    const plugins = Array.isArray(list) ? list : [];
    state.plugins = plugins;
    const kw = ($("plugin-search") && $("plugin-search").value || "").trim().toLowerCase();
    const hit = (p) => !kw || [p.name, p.description, p.category, p.id]
      .filter(Boolean).join(" ").toLowerCase().indexOf(kw) >= 0;
    const shown = plugins.filter(hit);

    const cnt = $("plugin-count");
    if (cnt) cnt.textContent = `${plugins.filter((p) => p.enabled).length} / ${plugins.length} 已启用`;

    // 按分类分组、双列卡片
    const groups = [];
    shown.forEach((p) => {
      const cat = CATEGORY_ORDER.indexOf(p.category) >= 0 ? p.category : "扩展";
      const g = groups.find((x) => x[0] === cat);
      if (g) g[1].push(p); else groups.push([cat, [p]]);
    });
    groups.sort((a, b) => CATEGORY_ORDER.indexOf(a[0]) - CATEGORY_ORDER.indexOf(b[0]));

    box.innerHTML = groups.map(([cat, items]) => `
      <div class="market__sec"><b>${esc(cat)}</b><span>${items.length} 个</span></div>
      <div class="market__grid">${items.map((p) => {
      const caps = Array.isArray(p.capabilities) ? p.capabilities : [];
      const badges = [
        `<span class="plugin-badge plugin-badge--src">${esc(SOURCE_LABEL[p.source] || p.source)}</span>`,
        ...caps.map((c) => `<span class="plugin-badge">${esc(KIND_LABEL[c.kind] || c.kind)}</span>`),
        p.permissions && p.permissions.network ? `<span class="plugin-badge">${esc(PERM_LABEL[p.permissions.network] || p.permissions.network)}</span>` : "",
        (p.missing && p.missing.length) ? `<span class="plugin-badge plugin-badge--warn">依赖未满足</span>` : "",
        // 来源校验（本轮 C-15）：只对**用户安装**的插件显示签名状态——
        // 内核自带的随产品发布，"已签名"没有信息量；未签名的用户插件要能被一眼看出来。
        p.source === "user" && p.signature_status === "verified"
          ? `<span class="plugin-badge plugin-badge--ok" title="${esc(p.signature_detail || "")}">已签名（${esc(p.signer || "可信公钥")}）</span>` : "",
        p.source === "user" && p.signature_status === "unsigned"
          ? `<span class="plugin-badge plugin-badge--warn" title="未签名：来源无法校验。开启「仅加载签名插件」后将被拒绝加载">未签名</span>` : "",
        p.source === "user" && p.signature_status === "untrusted"
          ? `<span class="plugin-badge plugin-badge--warn" title="${esc(p.signature_detail || "")}">签名不受信</span>` : "",
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
      // 插件设置**不在这一页编辑**（V3.5.4 起）：有设置的插件启用后会在左侧设置栏
      // 出现它自己的栏目，这里只给一个跳转入口——市场页管"有哪些插件、开不开"，
      // 设置页管"这个插件怎么配"，两者不混在一起（负责人实测后明确的口径）。
      const hasSettings = (Array.isArray(p.config_files) ? p.config_files : []).length > 0
        || PLUGIN_SETTINGS_RENDERERS[p.id];
      const settingsHint = hasSettings
        ? (p.enabled
            ? `<div class="plugin-item__ops"><button class="btn btn--primary" data-open-settings="${esc(p.id)}">打开「${esc(p.name)}」设置</button></div>`
            : `<div class="pcard__desc">启用后，左侧设置栏的「插件」分组里会出现它的专属设置。</div>`)
        : `<div class="pcard__desc">这个插件没有可调项：启用即生效。</div>`;
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
      return `<div class="pcard${p.enabled ? " is-on" : ""}" data-card="${esc(p.id)}">
        <div class="pcard__main">
          <span class="pcard__ico" aria-hidden="true">${PUZZLE_SVG}</span>
          <div class="pcard__text">
            <span class="pcard__name">${esc(p.name)}<span class="pcard__ver">v${esc(p.version)}</span></span>
            <span class="pcard__desc" title="${esc(p.description || "")}">${esc(p.description || "")}</span>
          </div>
          <div class="pcard__side">
            <label class="switch" title="${p.enabled ? "停用" : "启用"}"><input type="checkbox" data-plugin="${esc(p.id)}" ${p.enabled ? "checked" : ""} ${disabled ? "disabled" : ""}><span class="switch__track"></span></label>
            <button class="pcard__more" data-more="${esc(p.id)}" title="详情、能力清单与配置" aria-expanded="false">⋯</button>
          </div>
        </div>
        <div class="pcard__state">${badges}</div>
        <div class="pcard__detail" data-detail="${esc(p.id)}" hidden>
          <div class="plugin-item__runtime ${runCls}" title="只统计实际被调用的次数；未触发的插件不代表失效，而是还没遇到对应场景">运行状态：${runText}</div>
          ${capList ? `<div class="plugin-caps">${capList}</div>` : ""}
          ${(p.missing && p.missing.length) ? `<div class="pcard__desc">${p.missing.map(esc).join("<br>")}</div>` : ""}
          ${settingsHint}
        </div>
      </div>`;
    }).join("")}</div>`).join("") || `<div class="market__empty">没有匹配的插件（换个关键词试试）</div>`;

    // ⋯ 展开/收起详情
    box.querySelectorAll("[data-more]").forEach((btn) => {
      btn.onclick = () => {
        const det = box.querySelector(`[data-detail="${btn.dataset.more}"]`);
        if (!det) return;
        det.hidden = !det.hidden;
        btn.setAttribute("aria-expanded", det.hidden ? "false" : "true");
      };
    });
    box.querySelectorAll("[data-plugin]").forEach((input) => {
      input.onchange = async () => {
        const res = await call("plugin_set_enabled", input.dataset.plugin, input.checked);
        if (res && res.ok) {
          state.plugins = res.plugins || state.plugins;
          renderPlugins(state.plugins);
          // 启停即刷新设置栏：启用后它的专属栏目出现，停用后栏目隐藏、能力同时被宿主收回
          refreshPluginNav();
          const name = ((state.plugins || []).find((x) => x.id === input.dataset.plugin) || {}).name || "";
          toast(input.checked
            ? (pluginSettingsEntries().some((p) => p.id === input.dataset.plugin)
                ? `已启用「${name}」· 可在左侧「插件」分组里设置` : "插件已启用（已写入审计）")
            : "插件已停用");
          refreshSyncState();
          refreshExportFormats();
        } else {
          toast("操作失败：" + ((res && res.error) || "未知原因"));
          input.checked = !input.checked;
        }
      };
    });
    // 「打开设置」→ 跳到该插件的专属设置栏目
    box.querySelectorAll("[data-open-settings]").forEach((btn) => {
      btn.onclick = () => openPluginSettings(btn.dataset.openSettings);
    });
  }

  async function refreshPlugins() {
    const list = await call("plugins_list");
    renderPlugins(list);
    refreshPluginNav();
  }

  /* ══ 插件专属设置（V3.5.4）══════════════════════════════════
     口径（负责人实测后明确）：**插件页只管"有哪些插件、开不开"，
     插件怎么配放到设置栏里**。带设置的插件一旦启用，左侧「插件」分组里
     就出现它自己的栏目；停用即隐藏，能力同时被宿主收回（不是"界面藏起来、
     实际还在跑"——启停本身就走 plugin_set_enabled 的注册/注销）。
     通用设置页由 config_files 生成（文本配置），有专属界面的插件
     （如 laya 的三级接入）注册在 PLUGIN_SETTINGS_RENDERERS 里覆盖。 */
  const PLUGIN_SETTINGS_RENDERERS = {
    laya_risk: { render: renderLayaSettings, subtitle: "审批弹窗的风险提示：本机模型 / 局域网服务 / 正则规则回退" },
  };

  /** 当前该显示哪些插件设置栏目：启用 + 有设置（config_files 或专属渲染器） */
  function pluginSettingsEntries(list) {
    const plugins = Array.isArray(list) ? list : (state.plugins || []);
    return plugins
      .filter((p) => p.enabled && (((p.config_files || []).length) > 0 || PLUGIN_SETTINGS_RENDERERS[p.id]))
      .sort((a, b) => (CATEGORY_ORDER.indexOf(a.category) - CATEGORY_ORDER.indexOf(b.category))
                    || String(a.name).localeCompare(String(b.name), "zh"));
  }

  function refreshPluginNav() {
    const group = $("setnav-plugins");
    if (!group) return;
    const entries = pluginSettingsEntries();
    const current = group.querySelectorAll(".setnav__item");
    const sig = entries.map((p) => p.id + ":" + p.name).join("|");
    if (group.dataset.sig !== sig) {
      group.dataset.sig = sig;
      group.querySelectorAll(".setnav__item").forEach((el) => el.remove());
      entries.forEach((p) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "setnav__item";
        btn.dataset.set = "plugin-settings";
        btn.dataset.plugin = p.id;
        btn.textContent = p.name;
        btn.onclick = () => setSettingsPage("plugin-settings", p.id);
        group.appendChild(btn);
      });
    }
    group.hidden = entries.length === 0;
    // 当前正开着某个插件的设置页、而它刚被停用 → 退回插件页（不留一个空壳页面）
    if (state.settingsPage === "plugin-settings" && !entries.some((p) => p.id === state.settingsPlugin)) {
      setSettingsPage("plugin");
      toast("该插件已停用，设置栏目已隐藏");
    }
  }

  function openPluginSettings(pluginId) {
    if (!pluginSettingsEntries().some((p) => p.id === pluginId)) {
      toast("先启用这个插件，它的设置才会出现");
      return;
    }
    setSettingsPage("plugin-settings", pluginId);
  }

  /** 插件设置页：题头随插件而变；正文由专属渲染器或配置项生成 */
  async function renderPluginSettings(pluginId) {
    const page = $("plugin-settings-page");
    if (!page) return;
    const p = (state.plugins || []).find((x) => x.id === pluginId);
    const custom = PLUGIN_SETTINGS_RENDERERS[pluginId];
    page.dataset.set = "plugin-settings";      // 供 setSettingsPage 的显隐判断使用
    page.innerHTML = `
      <div class="pset__head"><span class="pset__tag">插件</span>
        <b>${esc((p && p.name) || pluginId)}</b></div>
      <div id="pset-body"></div>`;
    const body = $("pset-body");
    if (custom) { await custom.render(body); return; }
    // 通用：每个配置项一张卡（标签 + 提示 + 路径 + 编辑器）
    // managed=true 的文件由专属渲染器接管（例：laya 的三级接入），通用页面不把它当便签改
    const files = ((p && p.config_files) || []).filter((f) => !f.managed);
    if (!files.length) {
      body.innerHTML = `<div class="market__empty">这个插件没有可调项：启用即生效。</div>`;
      return;
    }
    body.innerHTML = files.map((f) => `
      <div class="pset__file setcard" data-file="${esc(f.name)}">
        <div class="setrow"><div class="setrow__text"><b>${esc(f.label || f.name)}</b>
          <span>${esc(f.hint || "")}</span></div>
          <div class="setrow__ctrl">
            <button class="btn" data-pset-load="${esc(f.name)}" type="button">读取</button>
            <button class="btn btn--primary" data-pset-save="${esc(f.name)}" type="button">保存</button>
          </div>
        </div>
        <div class="setrow setrow--stack">
          <textarea class="plugin-cfg__text" data-pset-text="${esc(f.name)}" rows="9" spellcheck="false"></textarea>
          <div class="pset__path" data-pset-path="${esc(f.name)}"></div>
        </div>
      </div>`).join("");
    const load = async (name) => {
      const res = await call("plugin_config_read", pluginId, name);
      if (!res || !res.ok) { toast("读取失败：" + ((res && res.error) || "未知原因")); return; }
      body.querySelector(`[data-pset-text="${name}"]`).value = res.text || "";
      const pathEl = body.querySelector(`[data-pset-path="${name}"]`);
      if (pathEl) pathEl.textContent = res.path || "";
    };
    body.querySelectorAll("[data-pset-load]").forEach((b) => { b.onclick = () => load(b.dataset.psetLoad); });
    body.querySelectorAll("[data-pset-save]").forEach((b) => {
      b.onclick = async () => {
        const name = b.dataset.psetSave;
        const res = await call("plugin_config_write", pluginId, name,
                               body.querySelector(`[data-pset-text="${name}"]`).value);
        if (res && res.ok) { toast("已保存（改动已写入审计）"); refreshSyncState(); }
        else toast("保存失败：" + ((res && res.error) || "未知原因"));
      };
    });
    files.forEach((f) => load(f.name));      // 进页面即载入，省一次点击
  }

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
      renderProv(await call("model_catalog"));
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
    // 设置视图接管整区：列宽在这里统一收口（行内样式优先级高于 CSS 类，
    // 所以任何窗口 resize 都不能把它覆盖回三栏，否则设置页又被挤成窄条）。
    if (app.classList.contains("is-settings-open")) {
      app.style.gridTemplateColumns = "minmax(0, 1fr)";
      return;
    }
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

  /** 窗口控制按钮落位：跟着"当前可见的右上角"走。
   *  1) 设置视图：顶栏与右栏都隐藏 → 落在设置页头部右侧（#set-actions 之后）；
   *  2) 右栏展开：右栏本就占满窗口右侧 → 落在右栏标签栏内（即窗口右上角）；
   *  3) 其余（右栏收起）：回到中栏顶栏最右。
   *  只挪 DOM、不重建按钮（id 与 onclick 绑定不受影响）：appendChild 会连事件一起搬走，
   *  用 innerHTML 重建则会把绑定丢光。 */
  function placeWinCtrls() {
    const box = $("win-ctrls");
    if (!box) return;
    const app = document.querySelector(".app");
    const settingsOpen = !!(app && app.classList.contains("is-settings-open"));
    const tabs = document.querySelector(".panel__tabs");
    const head = document.querySelector(".setmain__head");
    const topbar = document.querySelector(".topbar");
    let host = topbar, where = "topbar";
    if (settingsOpen && head) { host = head; where = "head"; }
    else if (tabs && !rightCollapsed()) { host = tabs; where = "panel"; }
    if (host && box.parentElement !== host) host.appendChild(box);
    box.classList.toggle("win-ctrls--panel", where === "panel");
    box.classList.toggle("win-ctrls--head", where === "head");
  }

  /** 收起/展开右栏。persist=false 用于"因窗口窄而自动收起"——
   *  自动行为不能被当成用户意图存下来，否则"临时窄一下"会被永久记住。 */
  function setRightCollapsed(isCollapsed, persist = true) {
    document.querySelector(".app").classList.toggle("is-right-collapsed", isCollapsed);
    if (persist) {
      try { localStorage.setItem("yd_right_collapsed", isCollapsed ? "1" : "0"); } catch (e) {}
    }
    placeWinCtrls();
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

  /* ── 设置视图（独立视图：左设置导航 + 右内容）────────────
     为什么独立成视图而不放右侧窄面板：设置项是"左标题说明 / 右控件"的行式布局，
     300~600px 的窄栏放不下，只能挤成竖排表单——那正是上一版难看的根因。 */
  const SET_META = {
    model: ["模型接入", "按数据边界分三档：本机 / 局域网 / 公网"],
    control: ["企业控制面", "接入公司控制面：只上传掩码事件，只接受收紧策略"],
    safety: ["隐私与权限", "脱敏网关、AI 权限四档与执行严格模式"],
    kb: ["本地知识库", "入库即脱敏的本地检索（脱敏 RAG）"],
    plugin: ["插件", "启用 / 停用能力扩展；带设置的插件启用后会在左侧出现专属栏目"],
    appearance: ["窗口与外观", "悬浮窗行为与明暗主题"],
  };
  async function openSettings(page) {
    const view = $("settings-view");
    if (!view) return;
    view.hidden = false;
    const sheet = document.querySelector(".sheet");
    if (sheet) sheet.hidden = true;
    // 接管整区：收起会话栏与右侧面板（设置是"独立视图"，不是窄栏里的抽屉）。
    // 列宽必须由 JS 写行内样式——本项目的列宽单一出口就是行内 grid-template-columns，
    // 行内优先级高于 CSS 类，所以不能只在样式表里改。
    const app = document.querySelector(".app");
    app.classList.add("is-settings-open");
    applyLayout();                      // 列宽走单一出口
    placeWinCtrls();                    // 顶栏与右栏都被隐藏 → 窗口按钮改挂设置页头部右侧
    if (page) setSettingsPage(page); else if (!state.settingsPage) setSettingsPage("model");
    if (!state.catalog) renderProv(await call("model_catalog"));
  }
  function closeSettings() {
    const view = $("settings-view");
    if (view) view.hidden = true;
    const sheet = document.querySelector(".sheet");
    if (sheet) sheet.hidden = false;
    document.querySelector(".app").classList.remove("is-settings-open");
    applyLayout();                      // 列宽回到用户的折叠/拖拽状态（单一出口）
    placeWinCtrls();                    // 窗口按钮回到顶栏 / 右栏
  }
  function setSettingsPage(page, pluginId) {
    state.settingsPage = page;
    state.settingsPlugin = pluginId || null;
    if (page === "control") renderControlPage();
    // 插件列表按需加载：右栏的"设置"面板已不是入口（设置改成了独立视图），
    // 只在打开这一页时拉一次——否则市场页会一片空白（真实缺陷，实测发现）。
    if (page === "plugin") refreshPlugins();
    const meta = page === "plugin-settings"
      ? pluginSettingsMeta(pluginId)
      : (SET_META[page] || SET_META.model);
    $("set-title").textContent = meta[0];
    $("set-sub").textContent = meta[1];
    document.querySelectorAll(".setnav__item").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.set === page
        && (page !== "plugin-settings" || b.dataset.plugin === pluginId)));
    // ★ 插件设置页要**先渲染再切显隐**（本轮修）：`renderPluginSettings` 会把内容填进
    //   `#plugin-settings-page`，而"哪一页可见"由 data-set 的 .is-active 决定。旧顺序是先切
    //   显隐、后渲染——首次进入时该容器还没渲染过、也没有 data-set，于是**第一次点插件设置是
    //   一张白板**，得再点一次才出现（界面自动化回归抓出来的真实缺陷）。
    if (page === "plugin-settings") renderPluginSettings(pluginId);
    document.querySelectorAll(".setpage").forEach((p) =>
      p.classList.toggle("is-active", p.dataset.set === page));
  }

  function pluginSettingsMeta(pluginId) {
    const p = (state.plugins || []).find((x) => x.id === pluginId);
    const custom = PLUGIN_SETTINGS_RENDERERS[pluginId];
    return [(p && p.name) || "插件设置",
            (custom && custom.subtitle)
              || ((p && p.description) || "该插件的专属设置").slice(0, 80)];
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
  /** 置顶按钮的状态显示：置顶中高亮（明暗两套主题各有自己的取色，见 .win-btn.is-active）。
   *  只画状态，不改变窗口——真正的窗口动作在 window_action 里。 */
  function paintPinState() {
    const pin = $("btn-pin");
    if (!pin) return;
    const on = !!(state.settings && state.settings.topmost);
    pin.classList.toggle("is-active", on);
    pin.title = on ? "取消置顶" : "置顶";
    pin.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function bindWindowControls() {
    // 无边框悬浮是产品形态（不是设置项）：固定打开，让拖边缘热区与顶栏拖动始终可用，
    // 也就没有"改了要重启"这一说。
    document.documentElement.dataset.frameless = "1";
    placeWinCtrls();
    paintPinState();
    document.querySelectorAll("[data-win]").forEach((btn) => {
      btn.onclick = async () => {
        const action = btn.dataset.win;
        const ok = await call("window_action", action);
        if (action === "toggle_top") {
          if (ok === false) { toast("置顶切换失败"); return; }
          state.settings.topmost = !state.settings.topmost;
          paintPinState();
          toast(state.settings.topmost ? "已置顶显示" : "已取消置顶");
          const sw = $("sw-topmost");
          if (sw) sw.checked = !!state.settings.topmost;
        }
      };
    });
  }

  /* ── 企业控制面页（V3.5.0）─────────────────────────
     设计口径：接入是"显式"的（配对码 + 开关），状态是"如实"的（连不上就说连不上），
     已生效策略是"可见"的（列出控制面下发的规则，可一键撤销）。 */
  async function renderControlPage() {
    const st = await call("control_status");
    if (!st) return;
    const state = {
      text: st.configured
        ? `${st.enabled ? "上报中" : "已接入（未上报）"} · ${st.endpoint_id} · ${st.url}`
          + (st.last_report_at ? ` · 最后上报 ${st.last_report_at.slice(11, 19)}` : "")
          + (st.last_error ? ` · 最近错误：${st.last_error}` : "")
        : "未接入：向 IT 索取配对码后在下方接入",
      cls: st.last_error ? "is-bad" : (st.configured ? "is-ok" : ""),
    };
    const box = $("ctrl-state");
    if (box) { box.textContent = state.text; box.className = state.cls; }
    if ($("sw-ctrl-report")) $("sw-ctrl-report").checked = !!st.enabled;
    if ($("sw-ctrl-auto")) $("sw-ctrl-auto").checked = !!st.running;
    const rules = $("ctrl-rules");
    if (rules) {
      const list = st.control_rules || [];
      const effect = st.effect || [];
      rules.innerHTML = (list.length || effect.length)
        ? (effect.map((e) => `<li>${esc(e)}</li>`).join("")
           + list.slice(0, 6).map((r) => `<li class="muted">规则：${esc(r.path_pattern || "*")} → ${
             r.policy === "deny" ? "直接拒绝" : "需人工审批"}（来自控制面 v${esc(String(r.from_control_version || 0))}）</li>`).join(""))
        : `<li class="muted">尚未应用任何控制面策略</li>`;
    }
  }

  /* ── laya 风险分级：接入页（V3.5.3）─────────────────────
     口径与「模型接入」同源，因为这里是同一类问题——**同一能力可以来自不同数据边界**：
       本机 laya 模型（数据不出这台电脑）
       局域网 laya 服务（出机器、不出企业）
       正则规则（无模型时的保险回退，全离线）
     页面只做三件事：说清当前生效的是哪一级、让你配另外两级、真连通一次给你看。 */
  const LAYA_SOURCES = {
    local: { label: "本机 laya 模型", boundary: "数据不出这台电脑",
             hint: "本机已安装 laya（pip install laya，依赖 torch）。首次加载可能需要联网下载权重——涉密环境请预先离线导入权重目录。" },
    lan: { label: "局域网 laya 服务", boundary: "数据出这台电脑，但不出企业内网",
           hint: "公司内网部署的 laya 推理服务。需要暴露一个 OpenAI 兼容接口（/chat/completions）；地址只用于本机发起的探测与打分请求，不经过任何第三方。" },
    regex: { label: "正则规则（回退）", boundary: "全离线，不依赖任何模型",
             hint: "没有 laya 时使用。规则可在下方直接编辑：每行一条「级别|正则|说明」。规则只产生弹窗提示，不改变任何放行判定。" },
  };

  async function renderLayaSettings(host) {
    const st = await call("laya_status");
    if (st) state.laya = st;
    const cur = st || {};
    if (!host) return;
    host.innerHTML = `
      <div class="laya__state" id="laya-state">
        <span class="laya__tierdot" id="laya-dot"></span>
        <b id="laya-active">正在检测…</b>
        <span class="laya__result" id="laya-active-note"></span>
      </div>
      <div class="prov">
        <div class="prov__list" id="laya-list"></div>
        <div class="prov__detail" id="laya-detail"></div>
      </div>
      <p class="setfoot">laya 只做一件事：在人工审批弹窗里补一行风险分级（高危 / 中危 / 低危），
        <b>不参与任何放行判定</b>——判定权永远在内核。三级来源按「数据边界」分档：
        本机 laya 模型（数据不出本机）→ 局域网 laya 服务（出机器、不出企业）→ 本机正则规则（全离线回退）。
        每次提示都会写明本次来源，不冒充。</p>`;
    // 顶部：当前生效后端 + 最近一次分级的来源（"到底有没有在工作"的直接证据）
    const dot = $("laya-dot");
    if (dot) dot.className = "laya__tierdot " + (cur.active_backend === "regex" ? "is-off" : "is-ok");
    if ($("laya-active")) $("laya-active").textContent = cur.active_label || "未接入（将使用正则规则）";
    if ($("laya-active-note")) {
      $("laya-active-note").textContent = [
        cur.last_used ? `最近一次分级：${cur.last_used}` : "本会话还没有触发过审批分级",
        cur.local && cur.local.detail ? `本机：${cur.local.detail}` : "",
        cur.lan && cur.lan.detail ? `局域网：${cur.lan.detail}` : "",
      ].filter(Boolean).join(" · ");
    }
    const sel = state.layaSel || (state.layaSel = { tier: cur.source || "local" });
    const tiers = ["local", "lan", "regex"];
    const list = $("laya-list");
    if (list) {
      list.innerHTML = tiers.map((t) => {
        const meta = LAYA_SOURCES[t];
        const info = t === "regex" ? null : (cur[t] || {});
        const ok = t === "regex" ? true : !!info.available;
        const active = cur.active_backend === t;
        return `<button class="prov__item${sel.tier === t ? " is-active" : ""}" type="button" data-t="${t}">
          <span class="prov__name"><span class="laya__tierdot ${ok ? "is-ok" : "is-off"}"></span>${esc(meta.label)}
            ${active ? `<i class="prov__cur">使用中</i>` : ""}</span>
          <span class="prov__meta">${esc(meta.boundary)}</span>
        </button>`;
      }).join("");
    }
    const det = $("laya-detail");
    if (det) det.innerHTML = layaDetailHtml(sel.tier, cur);
    bindLayaDetail(sel.tier, host);
  }

  function layaDetailHtml(tier, cur) {
    const meta = LAYA_SOURCES[tier] || LAYA_SOURCES.local;
    const head = `<div class="prov__title">${esc(meta.label)}<span class="prov__badge">${esc(meta.boundary)}</span></div>
      <div class="prov__desc">${esc(meta.hint)}</div>`;
    if (tier === "regex") {
      const rules = (cur.regex || {});
      return head + `
        <div class="setcard">
          <div class="setrow"><div class="setrow__text"><b>规则文件</b>
            <span>${rules.path ? esc(rules.path) : "risk_rules.txt"} · 已载入 ${rules.count || 0} 条规则</span></div>
            <div class="setrow__ctrl">
              <button class="btn" data-laya="load-rules" type="button">读取</button>
              <button class="btn btn--primary" data-laya="save-rules" type="button">保存</button>
            </div>
          </div>
        </div>
        <textarea class="laya__rules" id="laya-rules" spellcheck="false" placeholder="点「读取」载入当前规则…"></textarea>
        <div class="laya__probe"><span class="laya__result" id="laya-result"></span></div>`;
    }
    if (tier === "local") {
      const info = cur.local || {};
      return head + `
        <div class="setcard">
          <div class="setrow"><div class="setrow__text"><b>本机 laya 检测</b><span>${esc(info.detail || "尚未检测")}</span></div>
            <div class="setrow__ctrl"><button class="btn" data-laya="probe-local" type="button">重新检测</button></div>
          </div>
        </div>
        <div class="laya__probe"><span class="laya__result" id="laya-result">${info.available ? "本机 laya 可用：审批弹窗将用模型打分" : "本机未检测到 laya：将按下一条可用来源（局域网 → 正则）"}</span></div>`;
    }
    const info = cur.lan || {};
    const cfg = cur.config || {};
    return head + `
      <div class="setcard">
        <div class="setrow"><div class="setrow__text"><b>服务地址</b><span>内网地址，例如 http://10.0.0.8:8080/v1</span></div>
          <div class="setrow__ctrl"><input type="text" id="laya-endpoint" value="${esc(cfg.endpoint || "")}" placeholder="http://10.0.0.8:8080/v1" spellcheck="false"></div>
        </div>
        <div class="setrow"><div class="setrow__text"><b>模型名</b><span>该服务上提供分级能力的模型名（可留空，由服务端默认）</span></div>
          <div class="setrow__ctrl"><input type="text" id="laya-model" value="${esc(cfg.model || "")}" placeholder="laya" spellcheck="false"></div>
        </div>
        <div class="setrow"><div class="setrow__text"><b>连通性</b><span>${esc(info.detail || "点右侧按钮发一次真实探测（地址只取本页输入框）")}</span></div>
          <div class="setrow__ctrl">
            <button class="btn" data-laya="probe-lan" type="button">测试连接</button>
            <button class="btn" data-laya="try-lan" type="button">试打分</button>
            <button class="btn btn--primary" data-laya="save-lan" type="button">保存</button>
          </div>
        </div>
      </div>
      <div class="laya__probe"><span class="laya__result" id="laya-result"></span></div>
      <p class="setfoot">「试打分」会真发一次分级请求并回显它返回的级别——不是"声称能连上"，而是把一次真实结果给你看。
        接口口径：OpenAI 兼容的 <code>/chat/completions</code>，回复里需要出现 0–1 的概率或「高危/中危/低危」字样；解析不出就如实报错并回退正则。</p>`;
  }

  function bindLayaDetail(tier, host) {
    // ★ `host` 必须是入参（本轮修）：本函数里多处 `renderLayaSettings(host)` 重新渲染，
    //   旧签名只有 `tier`，`host` 只在 renderLayaSettings 的作用域里——于是**切档/保存后
    //   重渲染直接抛 ReferenceError: host is not defined**：用户点「局域网」「正则」没反应，
    //   详情面板停在原档（界面自动化回归抓出来的真实缺陷，laya 三级接入是亮点功能，不能用不了）。
    const box = $("laya-detail");
    if (!box) return;
    const put = (text, cls) => {
      const el = $("laya-result");
      if (el) { el.textContent = text || ""; el.className = "laya__result" + (cls ? " " + cls : ""); }
    };
    box.querySelectorAll("[data-laya]").forEach((btn) => {
      btn.onclick = async () => {
        const act = btn.dataset.laya;
        if (act === "load-rules") {
          const res = await call("plugin_config_read", "laya_risk", "risk_rules.txt");
          if (res && res.ok) { $("laya-rules").value = res.text || ""; put("已载入规则，可直接编辑后保存", "is-ok"); }
          else put("读取失败：" + ((res && res.error) || "未知原因"), "is-bad");
        } else if (act === "save-rules") {
          const res = await call("plugin_config_write", "laya_risk", "risk_rules.txt", $("laya-rules").value);
          if (res && res.ok) { put("规则已保存并生效（改动已写入审计）", "is-ok"); renderLayaSettings(host); }
          else put("保存失败：" + ((res && res.error) || "未知原因"), "is-bad");
        } else if (act === "probe-local" || act === "probe-lan") {
          put("探测中…");
          const res = await call("laya_probe", act === "probe-local" ? "local" : "lan",
                                 act === "probe-lan" ? { endpoint: $("laya-endpoint").value.trim() } : null);
          put((res && res.detail) || "探测没有返回结果", res && res.ok ? "is-ok" : "is-bad");
        } else if (act === "try-lan") {
          put("正在真发一次分级请求…");
          const res = await call("laya_try",
                                 { endpoint: $("laya-endpoint").value.trim(), model: $("laya-model").value.trim() });
          put((res && res.detail) || "没有返回结果", res && res.ok ? "is-ok" : "is-bad");
        } else if (act === "save-lan") {
          const res = await call("laya_save", { source: "lan",
            endpoint: $("laya-endpoint").value.trim(), model: $("laya-model").value.trim() });
          if (res && res.ok) { put(res.detail || "已保存", "is-ok"); renderLayaSettings(host); }
          else put("保存失败：" + ((res && res.error) || "未知原因"), "is-bad");
        }
      };
    });
    const list = $("laya-list");
    if (list) {
      list.querySelectorAll("[data-t]").forEach((b) => {
        b.onclick = async () => {
          state.layaSel = { tier: b.dataset.t };
          renderLayaSettings(host);
          // 切到本机/局域网即表示"用它"（可选来源里手动置顶），保存 source
          if (b.dataset.t !== "regex") {
            await call("laya_save", { source: b.dataset.t });
            renderLayaSettings(host);
          }
        };
      });
    }
  }

  /* ── 设置抽屉 ─────────────────────────────────────── */
  /** 思考强度：胶囊 + 下拉（低 / 中 / 高 / 最高）。旧数值配置折算到最近档显示。 */
  function paintThinkLevel(depth) {
    const box = $("think-select");
    if (!box) return;
    const cur = levelOf(depth);
    if ($("think-value")) $("think-value").textContent = cur.label;
    const pop = $("think-pop");
    if (pop) {
      pop.innerHTML = THINK_LEVELS.map((lv) => `
        <button type="button" class="think__opt${lv.key === cur.key ? " is-cur" : ""}"
                role="option" aria-selected="${lv.key === cur.key}" data-level="${lv.key}">
          <b>${esc(lv.label)}</b><span>${esc(lv.hint)}</span>
        </button>`).join("");
    }
    box.setAttribute("data-level", cur.key);
  }

  function closeThinkPop() {
    const pop = $("think-pop"), box = $("think-select"), trg = $("think-trigger");
    if (pop) pop.hidden = true;
    if (box) box.classList.remove("is-open");
    if (trg) trg.setAttribute("aria-expanded", "false");
  }

  function openThinkPop() {
    const pop = $("think-pop"), box = $("think-select"), trg = $("think-trigger");
    if (!pop) return;
    pop.hidden = false;
    if (box) box.classList.add("is-open");
    if (trg) trg.setAttribute("aria-expanded", "true");
  }

  /** 权限四档（设置页）：高亮当前档并同步说明文案（文案与内核 HINTS 一致）。
   *  这里是**新对话的默认档**；本对话的覆盖在输入区胶囊里改（见 paintPermPill）。 */
  function paintPermModes(value) {
    const box = $("perm-modes");
    if (!box) return;
    const cur = permKey(value);
    box.querySelectorAll(".permseg__btn").forEach((b) => {
      const on = b.dataset.mode === cur;
      b.classList.toggle("is-active", on);
      b.setAttribute("aria-checked", on ? "true" : "false");
    });
    const hint = $("perm-hint");
    if (hint) hint.textContent = (PERM_MODES[cur] || {}).hint || "—";
    const defNote = $("perm-default-note");
    if (defNote) defNote.textContent = `当前默认：${(PERM_MODES[cur] || {}).label || cur}`;
  }

  /** 权限四档（输入区胶囊）：显示**本对话生效**的档位，下拉里可切本对话或改默认。
   *  sessionMode=null 表示本对话跟随默认（不覆盖）。 */
  function paintPermPill(sessionMode) {
    const box = $("perm-pill");
    if (!box) return;
    const def = permKey((state.settings || {}).permission);
    const override = sessionMode ? permKey(sessionMode) : "";
    const cur = override || def;
    box.dataset.mode = cur;
    if ($("perm-value")) $("perm-value").textContent = (PERM_MODES[cur] || {}).label || cur;
    const trg = $("perm-trigger");
    if (trg) {
      trg.title = override
        ? `本对话：${(PERM_MODES[cur] || {}).label}（已覆盖默认）· 点此修改`
        : `本对话：跟随默认 ${(PERM_MODES[def] || {}).label} · 点此为本对话单独设置`;
    }
    const pop = $("perm-pop");
    if (pop) {
      const opt = (m) => `<button type="button" class="perm__opt${m === cur ? " is-cur" : ""}"
          role="option" aria-selected="${m === cur}" data-mode="${m}">
          <svg class="perm__ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l7 3v5.5c0 4.3-2.9 7.9-7 9-4.1-1.1-7-4.7-7-9V6l7-3z"/></svg>
          <span><b>${esc(PERM_MODES[m].label)}</b><em>${esc(PERM_MODES[m].hint)}</em></span></button>`;
      pop.innerHTML = PERM_ORDER.map(opt).join("")
        + `<div class="perm__sep"></div>
           <button type="button" class="perm__opt${override ? "" : " is-cur"}" role="option"
                   aria-selected="${override ? "false" : "true"}" data-mode="">
             <span><b>跟随设置默认</b><em>新对话默认 ${esc((PERM_MODES[def] || {}).label || def)}；本对话不单独覆盖</em></span>
           </button>
           <div class="perm__note">执行命令在任何档位都要人工确认；沙箱、脱敏网关与审计链与档位无关。</div>`;
    }
  }

  function closePermPop() {
    const pop = $("perm-pop"), box = $("perm-pill"), trg = $("perm-trigger");
    if (pop) pop.hidden = true;
    if (box) box.classList.remove("is-open");
    if (trg) trg.setAttribute("aria-expanded", "false");
  }

  function openPermPop() {
    const pop = $("perm-pop"), box = $("perm-pill"), trg = $("perm-trigger");
    if (!pop) return;
    pop.hidden = false;
    if (box) box.classList.add("is-open");
    if (trg) trg.setAttribute("aria-expanded", "true");
  }

  function fillSettings() {
    const s = state.settings || {};
    // 三档算力清单由 renderProv() 渲染；这里只同步与模型相关的少量状态。
    const models = (state.llm.models && state.llm.models.length) ? state.llm.models : (s.ollama_models_cache || []);
    state.modelOptions = models;
    paintModelTrigger();
    if (!models.length) $("model-hint").textContent = "未检测到本机模型：装好 Ollama 后点上面的「重新检测」";
    else $("model-hint").textContent = `共 ${models.length} 个可用模型 · 按数据边界分三档`;

    $("sw-privacy").checked = !!s.privacy;
    $("sw-script-exec").checked = s.allow_script_exec !== false;
    $("sw-topmost").checked = !!s.topmost;
    paintThinkLevel(s.thinking_depth);
    paintPermModes(s.permission);
    // 输入区胶囊显示"本对话生效档位"：后端在 bootstrap/state 里给出 session_permission，
    // 拿不到时按"跟随默认"显示（新会话的初始状态）。
    paintPermPill(state.sessionPermission);

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
        // 本轮起这条通道也承载**真流式**（模型逐块生成、后端还原占位符后推全文），
        // 所以首次收到内容时要摘掉"等待点"标记（is-thinking），否则会一边出字一边转圈。
        {
          const row = ensureStreamBubble();
          const body = row.querySelector(".entry__body");
          body.classList.remove("is-thinking");
          body.innerHTML = md(payload || "");
          scrollToEnd();
        }
        break;
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
      case "trace":
        // 结构化过程轨迹：思考耗时 / 工具动作 / 关键判定（隐私网关、laya 风险等）
        if (state.busy) appendTrace(payload || {});
        break;
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
        // 后端把"本对话生效的权限档位"一并带回来：切会话/改档位后胶囊与态势条都要跟着变
        if ("permission" in payload || "session_permission" in payload) {
          state.sessionPermission = payload.session_permission || null;
          paintPermPill(state.sessionPermission);
          renderPosture(payload);
        }
        if (!payload.busy) {
          hideApproval();
          const sid = payload.session_id;
          if (sid) { state.currentId = sid; }
        }
        break;
      }
      case "startup": {
        // 启动阶段播报（探测算力 → 载入模型 → 就绪）：见 AgentService.prepare_llm / _warmup_llm。
        // 首次启动"模型载入内存"要 20~60 秒，以前这段全静默 → 用户以为界面坏了。
        const p2 = payload || {};
        const boot = $("bootline");
        if (boot) {
          if (p2.done) { boot.hidden = true; boot.textContent = ""; }
          else { boot.hidden = false; boot.textContent = p2.text || "正在准备…"; }
        }
        if (p2.done) setStatus(p2.text || "");
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
    state.sessionPermission = data.session_permission || null;   // 本对话的权限覆盖（null=跟随默认）
    $("brand-version").textContent = data.version || "";
    const seg = state.settings.think_mode || "快速回答";
    document.querySelectorAll("#mode-switch .seg__btn").forEach((b) =>
      b.classList.toggle("is-active", b.dataset.mode === seg));
    fillSettings(); renderSessions();
    renderStats({ model: state.settings.model, privacy: state.settings.privacy,
                  permission: state.settings.permission,
                  session_permission: state.sessionPermission, sandbox: state.settings.sandbox });
    call("audit_snapshot", 1).then((snap) => { if (snap) { state.audit = snap; renderPosture(null); } });
    if (state.currentId) await openSession(state.currentId); else renderMessages();
    onEvent("llm_status", state.llm);
    if (!(state.llm && state.llm.ready)) {   // 还没就绪：先说明在准备，别让用户对着空界面猜
      const boot0 = $("bootline");
      if (boot0) { boot0.hidden = false; boot0.textContent = "正在准备算力与模型…（首次启动较慢）"; }
    }
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
      clearDrafts();                 // 与浮条共用同一条清空路径（含自动增高复位）
      await sendText(text);
    });

    // 闪发浮条：回车发送（自动展开，且先展开视图再发送）、Shift+Enter 换行、Esc 展开、按钮发送
    $("btn-mini").onclick = () => setMini(true);
    $("btn-mini-expand").onclick = () => setMini(false);
    $("btn-mini-send").onclick = miniSubmit;
    $("mini-input").addEventListener("keydown", (e) => {
      // 只拦"不按 Shift 的 Enter"：Shift+Enter 交给控件默认行为插入换行
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); miniSubmit(); }
      if (e.key === "Escape") { e.preventDefault(); setMini(false); }
    });
    $("mini-input").addEventListener("input", (e) => autosizeInput(e.target));
    document.addEventListener("keydown", (e) => {
      // 全局 Esc：折叠态下展开（浮条只有一个输入框，不会与其它 Esc 语义冲突）
      if (e.key === "Escape" && miniOn()) { e.preventDefault(); setMini(false); }
    });

    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault(); $("composer").requestSubmit();
      }
    });
    $("input").addEventListener("input", (e) => autosizeInput(e.target));

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
    // 模型接入页由 renderProv() 渲染并自带事件绑定（见文件上半部分）
    $("btn-health").onclick = () => runTool("health");
    $("btn-behavior").onclick = () => runTool("behavior");
    $("btn-audit").onclick = () => openPane("audit");
    $("audit-filter").oninput = paintAuditList;
    $("btn-plugin-check").onclick = runPluginCheck;
    $("btn-settings").onclick = () => openSettings();
    // 设置视图：返回 + 左导航切页 + 外观页里的主题开关
    const setBack = $("set-back");
    if (setBack) setBack.onclick = () => closeSettings();
    document.querySelectorAll(".setnav__item").forEach((b) => {
      b.onclick = () => setSettingsPage(b.dataset.set);
    });
    // 企业控制面页的交互
    const ctrlNote = (msg, cls) => { const n = $("ctrl-note"); if (n) { n.textContent = msg; n.className = "tier__note " + (cls || ""); } };
    if ($("btn-ctrl-pair")) $("btn-ctrl-pair").onclick = async () => {
      const url = ($("ctrl-url").value || "").trim();
      const code = ($("ctrl-code").value || "").trim().toUpperCase();
      if (!url || !code) return ctrlNote("控制面地址与配对码都要填", "is-bad");
      ctrlNote("正在接入…");
      const res = await call("control_pair", url, code, "");
      if (res && res.ok) { ctrlNote("接入成功：" + res.note, "is-ok"); $("ctrl-code").value = ""; renderControlPage(); }
      else ctrlNote("接入失败：" + ((res && res.error) || "未知原因"), "is-bad");
    };
    if ($("sw-ctrl-report")) $("sw-ctrl-report").onchange = async (e) => {
      const res = await call("control_set_enabled", e.target.checked);
      if (!res || !res.ok) { toast("切换失败：" + ((res && res.error) || "")); e.target.checked = !e.target.checked; return; }
      toast(e.target.checked ? "已启用上报（掩码事件与元数据）" : "已停用上报");
      renderControlPage();
    };
    if ($("sw-ctrl-auto")) $("sw-ctrl-auto").onchange = async (e) => {
      const res = await call("control_auto", e.target.checked, 300);
      if (!res || !res.ok) { toast("切换失败：" + ((res && res.error) || "")); e.target.checked = !e.target.checked; return; }
      toast(e.target.checked ? "已开启定时同步（每 5 分钟）" : "已停止定时同步");
      renderControlPage();
    };
    if ($("btn-ctrl-sync")) $("btn-ctrl-sync").onclick = async () => {
      ctrlNote("同步中…");
      const res = await call("control_sync");
      if (res && res.ok) {
        const rep = (res.report || {}), pol = (res.policy || {});
        ctrlNote(`同步完成：上报 ${rep.events || 0} 条，策略 v${(pol.applied || {}).policy_version || 0}`, "is-ok");
      } else ctrlNote("同步失败：" + ((res && res.error) || (res && res.report && res.report.error) || "未知原因"), "is-bad");
      renderControlPage();
    };
    if ($("btn-ctrl-clear")) $("btn-ctrl-clear").onclick = async () => {
      const res = await call("control_clear_rules");
      toast(res && res.ok ? `已撤销 ${res.removed} 条控制面规则` : "撤销失败");
      renderControlPage();
    };
    // 插件市场搜索：客户端过滤（插件数是个位数到几十，不需要往返一次）
    if ($("plugin-search")) $("plugin-search").oninput = () => renderPlugins(state.plugins);
    const themeBtn2 = $("btn-theme-2");
    if (themeBtn2) themeBtn2.onclick = () => persist({ dark_mode: document.documentElement.dataset.theme !== "dark" });
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
    // 权限四档（设置页）：这里是**新对话默认档**，点击即落库；本对话的覆盖在输入区胶囊里改。
    const permBox = $("perm-modes");
    if (permBox) {
      permBox.onclick = (e) => {
        const btn = e.target.closest(".permseg__btn");
        if (!btn) return;
        const mode = btn.dataset.mode;
        persist({ permission: mode });
        // 本对话没有单独覆盖时，默认档一改，本对话立刻跟着变（没有覆盖才跟随）
        if (!state.sessionPermission) paintPermPill(null);
        toast(`新对话默认权限：${(PERM_MODES[mode] || {}).label || mode}`);
      };
    }
    // 权限四档（输入区胶囊）：只作用于**本对话**。选「跟随设置默认」= 清除本对话覆盖。
    const permPill = $("perm-pill");
    if (permPill) {
      const permTrig = $("perm-trigger"), permPop = $("perm-pop");
      permTrig.onclick = () => (permPop.hidden ? openPermPop() : closePermPop());
      permPop.onclick = async (e) => {
        const btn = e.target.closest(".perm__opt");
        if (!btn) return;
        const mode = btn.dataset.mode || "";        // "" = 跟随设置默认
        closePermPop();
        const res = await call("set_session_permission", mode);
        if (!res || !res.ok) { toast("切换失败：" + ((res && res.error) || "未知原因")); return; }
        // ★ 字段名必须与内核返回一致（本轮修）：`set_session_permission` 返回的是
        //   `session_permission` / `default_permission` / `effective`，旧代码读的 `res.permission`
        //   永远是 undefined → 胶囊每次都退回"跟随设置默认"，用户选了档位却看不出生效
        //   （后端其实已经生效——界面显示与实际行为不一致，比功能缺失更糟）。
        state.sessionPermission = res.session_permission || null;
        paintPermPill(state.sessionPermission);
        renderPosture(res.posture || null);
        toast(mode ? `本对话权限：${(PERM_MODES[mode] || {}).label || mode}` : "本对话权限：跟随设置默认");
      };
      document.addEventListener("click", (e) => { if (!permPill.contains(e.target)) closePermPop(); });
    }
    // 思考强度：胶囊 + 下拉（点选即落库；点浮层外或 Esc 关闭）
    const thinkSel = $("think-select");
    if (thinkSel) {
      const thinkTrig = $("think-trigger"), thinkPop = $("think-pop");
      thinkTrig.onclick = () => (thinkPop.hidden ? openThinkPop() : closeThinkPop());
      thinkPop.onclick = (e) => {
        const btn = e.target.closest(".think__opt");
        if (!btn) return;
        const lv = THINK_LEVELS.find((l) => l.key === btn.dataset.level);
        closeThinkPop();
        if (!lv) return;
        persist({ thinking_depth: lv.depth });
        toast(`思考强度 · ${lv.label}（约 ${lv.depth} 轮推理）`);
      };
      document.addEventListener("click", (e) => { if (!thinkSel.contains(e.target)) closeThinkPop(); });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeThinkPop(); });
    }
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
      const cur = (state.settings && state.settings.model) || "";
      const cat = state.catalog || { tiers: { local: [], lan: [], wan: [] } };
      let html = "", shown = 0, firstPickable = -1;

      // 按三档分组渲染；每组带标题与边界说明，用户一眼看出"这个模型会把数据带到哪"
      ["local", "lan", "wan"].forEach((tier) => {
        const meta = TIER_META[tier];
        const items = (cat.tiers[tier] || []).filter((m) => !q || m.name.toLowerCase().includes(q));
        if (!items.length) return;
        html += `<div class="model-group"><span class="tier-dot tier-dot--${tier}"></span>
          <b>${esc(meta.label)}</b><span class="model-group__boundary">${esc(meta.boundary)}</span></div>`;
        html += items.map((m) => {
          if (firstPickable < 0) firstPickable = shown;
          shown += 1;
          const extra = m.source === "ollama" ? "本机已安装"
            : (m.model_id && m.model_id !== m.name ? m.model_id : (m.base_url || ""));
          return `<button type="button" class="model-opt${m.name === cur ? " is-cur" : ""}" role="option"
            aria-selected="${m.name === cur}" data-model="${esc(m.name)}">
            <span class="model-opt__name">${esc(m.name)}</span>
            <span class="model-opt__meta">${esc(String(extra).slice(0, 42))}</span>
            ${m.name === cur ? `<span class="model-opt__tick">✓</span>` : ""}
          </button>`;
        }).join("");
      });

      // 不在清单里的当前模型（例如清单尚未刷新）也要能看到
      if (cur && !["local", "lan", "wan"].some((t) => (cat.tiers[t] || []).some((m) => m.name === cur))) {
        html += `<div class="model-group"><b>其他</b><span class="model-group__boundary">不在当前清单中</span></div>
          <button type="button" class="model-opt is-cur" role="option" aria-selected="true" data-model="${esc(cur)}">
            <span class="model-opt__name">${esc(cur)}</span><span class="model-opt__tick">✓</span></button>`;
        if (firstPickable < 0) firstPickable = 0;
        shown += 1;
      }

      // 搜索无结果时，允许直接使用输入的名称（模型名可以手填）
      if (q && !shown) {
        html = `<button type="button" class="model-opt model-opt--manual" role="option" data-model="${esc(q)}"
          >直接使用「${esc(q)}」</button>`;
        firstPickable = 0;
      }
      html += `<div class="model-select__actions">
        <button type="button" class="model-opt model-opt--act" data-open-settings="1">＋ 接入局域网 / 公网模型</button></div>`;
      mList.innerHTML = html || `<div class="model-opt model-opt--empty">没有可用模型</div>`;
      mHl = firstPickable;
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
    mList.onclick = async (e) => {
      if (e.target.closest("[data-open-settings]")) {
        closeModelPop();
        state.prov = { tier: "lan", name: "" };      // 直接落到「添加局域网模型」
        openSettings("model");
        renderProv(await call("model_catalog"));
        return;
      }
      const btn = e.target.closest("[data-model]");
      if (!btn) return;
      persist({ model: btn.dataset.model });
      closeModelPop();
    };
    document.addEventListener("click", (e) => {
      if (!mSel.contains(e.target)) closeModelPop();
    });
    // Ollama 地址的绑定在 renderProv() 里（该输入框随详情面板一起渲染/重建）
    // 思考强度的绑定在上面的「输入区胶囊」一段（下拉在 #think-select 里）

    restoreLayout();
    window.addEventListener("resize", onViewportResize);
    window.addEventListener("pywebviewready", bootstrap);
    // 开发预览：假桥接已就绪，直接走一次 bootstrap（正式运行时该分支不成立）
    if (window.__YINDUN_MOCK__) bootstrap();
    else if (!window.pywebview) renderMessages();   // 浏览器裸开：至少渲染空态
  });
})();
