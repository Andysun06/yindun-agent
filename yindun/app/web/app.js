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

  /* ── 数据看板 ─────────────────────────────────── */
  function renderStats(payload) {
    const model = (payload && payload.model) || state.settings.model || "—";
    $("stat-model").textContent = model.length > 26 ? model.slice(0, 24) + "…" : model;
    $("stat-tools").textContent = `工具 ${(payload && payload.tool_calls) || 0}`;
    const tokens = (payload && payload.context_tokens) || 0;
    $("stat-tokens").textContent = `上下文 ${tokens >= 1000 ? (tokens / 1000).toFixed(1) + "k" : tokens}`;
    const privacy = payload ? payload.privacy : state.settings.privacy;
    const el = $("stat-privacy");
    el.textContent = privacy ? "隐私网关 开" : "隐私网关 关";
    el.className = "stats__item" + (privacy ? "" : " stats__item--bad");
  }

  /* ── 渲染：消息 ───────────────────────────────────── */
  function emptyState() {
    return `<div class="chat__empty">
      <h1>让大模型看不见敏感数据，却依然能把活干完</h1>
      <p>数据进模型前在内存中被替换为加密占位符，模型全程只见占位符，回答返回时自动还原。</p>
      <p>文件读写经沙箱校验，命令走白名单，高危操作强制人工审批，全过程写入哈希链审计。</p>
    </div>`;
  }

  function bubbleEl(role, html, meta = "") {
    const row = document.createElement("div");
    row.className = "row" + (role === "user" ? " row--user" : "");
    row.innerHTML = `<div class="bubble">${html}${meta ? `<div class="bubble__meta">${meta}</div>` : ""}</div>`;
    return row;
  }

  function renderMessages() {
    const chat = $("chat");
    chat.innerHTML = "";
    if (!state.messages.length) { chat.innerHTML = emptyState(); return; }
    for (const m of state.messages) {
      if (m.role === "user") { chat.appendChild(bubbleEl("user", md(m.content || ""))); }
      else if (m.role === "assistant" && m.content) {
        chat.appendChild(bubbleEl("assistant", md(m.content)));
      }
    }
    scrollToEnd();
  }

  function scrollToEnd() { const c = $("chat"); c.scrollTop = c.scrollHeight; }

  function appendUser(text) {
    const chat = $("chat");
    if (chat.querySelector(".chat__empty")) chat.innerHTML = "";
    const row = bubbleEl("user", md(text));
    chat.appendChild(row); scrollToEnd();
  }

  /** 思考中的临时气泡（中间结果/状态都写进它） */
  function ensureStreamBubble() {
    if (state.streamBubble) return state.streamBubble;
    const chat = $("chat");
    if (chat.querySelector(".chat__empty")) chat.innerHTML = "";
    const row = bubbleEl("assistant", `<span class="typing"><i></i><i></i><i></i></span>`);
    row.querySelector(".bubble").classList.add("is-thinking");
    chat.appendChild(row);
    state.streamBubble = row;
    scrollToEnd();
    return row;
  }

  function finalizeBubble(text, tagText) {
    const row = state.streamBubble;
    state.streamBubble = null;
    if (!row) { const chat = $("chat"); chat.appendChild(bubbleEl("assistant", md(text))); scrollToEnd(); return; }
    const bubble = row.querySelector(".bubble");
    bubble.classList.remove("is-thinking");
    const secs = Math.max(1, Math.round((Date.now() - state.startedAt) / 1000));
    bubble.innerHTML = md(text) + `<div class="bubble__meta">
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
  }

  function setStatus(text) { $("status-line").textContent = text || ""; }

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
    $("approval-tool").textContent = payload.name || "—";
    $("approval-path").textContent = payload.path || "—";
    const text = formatPayload(payload.args);
    const wrap = $("approval-payload-wrap");
    if (text) { $("approval-payload").textContent = text.slice(0, 1200); wrap.style.display = ""; }
    else wrap.style.display = "none";
    $("approval-root").classList.add("is-open");
    setStatus("等待人工合规审批…");
  }
  function hideApproval() { $("approval-root").classList.remove("is-open"); }

  /* ── 附件条 ───────────────────────────────────── */
  function renderChips() {
    const box = $("chips");
    box.innerHTML = "";
    for (const item of state.attachments) {
      const chip = document.createElement("span");
      chip.className = "chip" + (item.error ? " chip--bad" : "");
      const size = item.chars ? `${(item.chars / 1000).toFixed(1)}k 字` : (item.error || "空");
      chip.innerHTML = `<span>📎 ${esc(item.name)} · ${esc(size)}</span><span class="chip__x" title="移除">×</span>`;
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
    $("audit-chain").className = "chain-badge " + (ok ? "chain-badge--ok" : "chain-badge--bad");
    $("audit-chain").textContent = ok
      ? "✔ 哈希链完整性验证通过（HMAC-SHA256 链式校验）"
      : "⚠︎ 哈希链校验未通过（存在被篡改或密钥不匹配的可能）";
    const num = (v) => (v === undefined || v === null ? "—" : String(v));
    const pairs = [
      ["总日志数", num(stats.total_entries)],
      ["工具调用", num(stats.tool_calls)],
      ["隐私事件", num(stats.privacy_events)],
      ["审批决策", num(stats.approvals)],
    ];
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
      return `<div class="kb-item"><span class="kb-item__name">📄 ${esc(name)}</span>
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

  /* ── 窗口控制 ─────────────────────────────────── */
  function bindWindowControls() {
    const frameless = !!(state.settings && state.settings.frameless);
    document.documentElement.dataset.frameless = frameless ? "1" : "0";
    document.querySelectorAll("[data-win]").forEach((btn) => {
      btn.onclick = async () => {
        const action = btn.dataset.win;
        await call("window_action", action);
        if (action === "toggle_top") toast("置顶设置已保存，重启后生效");
      };
    });
  }

  /* ── 设置抽屉 ─────────────────────────────────────── */
  function fillSettings() {
    const s = state.settings || {};
    const sel = $("sel-model");
    const models = (state.llm.models && state.llm.models.length) ? state.llm.models : (s.ollama_models_cache || []);
    sel.innerHTML = "";
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m; opt.textContent = m;
      if (m === s.model) opt.selected = true;
      sel.appendChild(opt);
    }
    if (s.model && !models.includes(s.model)) {
      const opt = document.createElement("option");
      opt.value = s.model; opt.textContent = s.model; opt.selected = true;
      sel.appendChild(opt);
    }
    if (!models.length) $("model-hint").textContent = "未检测到本地模型：请先安装 Ollama 并拉取模型（ollama pull qwen2.5:7b）";
    else $("model-hint").textContent = `已检测到 ${models.length} 个本地模型`;

    $("sw-privacy").checked = !!s.privacy;
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
      case "status":
        setStatus(payload); break;
      case "intermediate_result":
        ensureStreamBubble().querySelector(".bubble").innerHTML = md(payload || ""); scrollToEnd(); break;
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
      case "approval_expired": hideApproval(); setStatus("审批超时，已按驳回处理"); break;
      case "state": {
        setBusy(!!payload.busy);
        renderStats(payload);
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
    fillSettings(); renderSessions(); renderStats({ model: state.settings.model, privacy: state.settings.privacy });
    if (state.currentId) await openSession(state.currentId); else renderMessages();
    onEvent("llm_status", state.llm);
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
      appendUser(text);
      state.startedAt = Date.now();
      setBusy(true);
      const ok = await call("send", text);
      if (!ok) { setBusy(false); toast("发送失败：算力未就绪或正在推理"); }
      else await refreshSessions();
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
    $("btn-audit").onclick = async () => {
      $("audit-drawer").classList.add("is-open");
      renderAudit(await call("audit_snapshot", 300));
    };
    $("btn-audit-close").onclick = () => $("audit-drawer").classList.remove("is-open");
    $("audit-filter").oninput = paintAuditList;
    $("btn-audit-export-json").onclick = async () => {
      const path = await call("audit_export", "json");
      toast(path ? `已导出：${path}` : "导出失败");
    };
    $("btn-audit-export-html").onclick = async () => {
      const path = await call("audit_export", "html");
      toast(path ? `已导出：${path}` : "导出失败");
    };
    $("btn-settings").onclick = async () => {
      const info = await call("llm_status");
      if (info) { state.llm = info; }
      fillSettings(); bindWindowControls(); refreshKb();
      $("drawer").classList.add("is-open");
    };
    $("btn-drawer-close").onclick = () => $("drawer").classList.remove("is-open");
    $("btn-sidebar").onclick = () => $("sidebar").classList.toggle("is-open");
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

    $("sel-model").onchange = (e) => persist({ model: e.target.value });
    $("sw-privacy").onchange = (e) => persist({ privacy: e.target.checked });
    $("sw-topmost").onchange = (e) => persist({ topmost: e.target.checked });
    $("sw-frameless").onchange = (e) => {
      persist({ frameless: e.target.checked });
      toast("无边框悬浮模式将在重启后生效");
    };
    $("sel-permission").onchange = (e) => persist({ permission: e.target.value });
    $("rng-depth").oninput = (e) => { $("depth-value").textContent = e.target.value; };
    $("rng-depth").onchange = (e) => persist({ thinking_depth: Number(e.target.value) });

    window.addEventListener("pywebviewready", bootstrap);
    // 开发预览：假桥接已就绪，直接走一次 bootstrap（正式运行时该分支不成立）
    if (window.__YINDUN_MOCK__) bootstrap();
    else if (!window.pywebview) renderMessages();   // 浏览器裸开：至少渲染空态
  });
})();
