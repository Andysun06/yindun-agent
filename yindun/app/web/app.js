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
    pane: "settings", auditFormats: [],
    catalog: null,           // 三档算力清单（本机 / 局域网 / 公网）
    plugins: [],             // 插件清单（市场页渲染 + 搜索过滤都基于它）
    settingsPage: null,      // 当前设置页标识（判断"插件被停用后要不要退回"用）
    settingsPlugin: null,    // 当前打开的插件设置页属于哪个插件
    sessionPermission: null, // 本对话的权限覆盖（null = 跟随设置默认）
    laya: null,              // laya 接入状态（本机 / 局域网 / 正则回退）
    toolBusy: false,         // 安全工具同一时间只跑一个（避免结果互相覆盖）
    prov: null,              // 模型接入页"正在查看"的供应商 {tier, provider, builtin, isNew?}
    windowMini: false,       // 原生窗口是否处于闪发折叠态（决定最大化按钮的语义性禁用）
    kbNotice: "",            // 知识库列表上方的持久说明（失败 / 部分失败原因）
  };

  /* ── 工具 ─────────────────────────────────────────── */
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /** 行内标记 → 受控标签（入参必须是**已经 esc() 过**的文本）。
   *  为什么强调"已转义"：模型输出是不可信内容，这里只做"把已转义的标记换回白名单标签"，
   *  绝不二次解码 HTML 实体；链接只放行 http(s)/mailto，javascript:/data: 等一律按普通文字显示。
   *  行内代码先占位、最后回填，避免代码内容里的 ** / 链接被继续改写。 */
  function inlineFormat(escapedText) {
    const codes = [];
    let out = escapedText.replace(/`([^`\n]+)`/g, (_m, code) => {
      codes.push(code);
      return `\u0000C${codes.length - 1}\u0000`;
    });
    out = out
      .replace(/\[([^\[\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/gi,
        (_m, label, href) =>
          `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`)
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    return out.replace(/\u0000C(\d+)\u0000/g, (_m, i) => `<code>${codes[Number(i)]}</code>`);
  }

  /** 表格单元格渲染：行内标记 + 把转义后的换行标记还原成真正的 <br>。
   *  安全边界（这条路径最终进 innerHTML）：只认 esc() 产出的 `&lt;br&gt;` /
   *  `&lt;br/&gt;` / `&lt;br /&gt;` 形态；带属性的 <br on…> 与其它任何标签
   *  （<img>、<b>、<script> …）都不会命中，仍是转义文本，浏览器只显示不解析。
   *  顺序也必须是"先 esc() 再替换"：直接对原文替换 <br> 会把整条注入通道打开。 */
  function tableCell(escapedCell) {
    // 先把行内代码整体占位：代码里的 "<br>" 是**要原样显示的内容**，不能被下面的换行替换拆开。
    const codes = [];
    const protectedCell = escapedCell.replace(/`([^`\n]+)`/g, (_m, code) => {
      codes.push(code);
      return `\u0000K${codes.length - 1}\u0000`;
    });
    const html = protectedCell
      .split(/&lt;br\s*\/?&gt;/i)
      .map((line) => inlineFormat(line.replace(/^\s*[-*]\s+/, "• ")))
      .join("<br>");
    return html.replace(/\u0000K(\d+)\u0000/g, (_m, i) => `<code>${codes[Number(i)]}</code>`);
  }

  /** 极简 Markdown 渲染（先转义再套标签，保证不引入 HTML 注入）
   *  支持：代码块、表格（单元格内行内标记/安全链接/<br> 换行）、行内代码、粗体、标题、列表、换行 */
  /** 行内格式（输入必须是**已转义**的文本；不再二次转义，表格/引用块共用）。 */
  function mdInline(text) {
    return text
      .replace(/`([^`\n]+)`/g, "<code>$1</code>")
      // 标题层级：模型常用 #### / #####（四级以上旧实现不认，原样漏出 "#"）。
      // 统一渲染成加粗行——对话区不引入 h1~h6 的版式跳变，但绝不能再漏字面量。
      .replace(/^#{1,6}\s+(.*)$/gm, "<strong>$1</strong>")
      .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
      .replace(/^\s*[-*]\s+(.*)$/gm, "• $1")
      .replace(/\n/g, "<br>");
  }

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
        // 单元格内容同样走"先转义再套标签"：这里传入的 head/cells 已是 esc() 后的文本
        kept.push(stash(
          `<table><thead><tr>${head.map((h) => `<th>${tableCell(h)}</th>`).join("")}</tr></thead>` +
          `<tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${tableCell(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`));
      } else {
        kept.push(lines[i]);
      }
    }
    out = kept.join("\n");

    // 2.5) 引用块：连续 `>` 行（含 `>` 空行、`> - 列表`）合并成 <blockquote>
    //      负责人实测：模型用 `> ` 引用 OCR 原文时，界面把 `>` 原样显示成了纯文本。
    //      ★ 此时文本已经过 esc()，`>` 已变成 `&gt;`——必须按转义后的形态匹配。
    out = out.replace(/(?:^|\n)((?:[ \t]*&gt;[^\n]*(?:\n|$))+)/g, (_m, body) => {
      const inner = body.replace(/\n$/, "").split("\n")
        .map((line) => line.replace(/^[ \t]*&gt;\s?/, ""))
        .join("\n");
      if (!inner.trim()) return "\n";            // 空引用块不占位
      return "\n" + stash(`<blockquote>${mdInline(inner)}</blockquote>`) + "\n";
    });

    // 2.6) 分隔线：`---` / `***` / `___`（独占一行、3 个以上）→ <hr>
    out = out.replace(/^[ \t]*([-*_])\1{2,}[ \t]*$/gm, () => stash("<hr>"));

    // 3) 行内格式
    out = mdInline(out);

    // 4) 空行收敛（V3.6.12，负责人反馈「有些地方空白太大」）：
    //    模型常在代码块前后留 2~3 个空行，旧实现每个空行都变 <br>，再叠加 <pre> 的外边距
    //    → 视觉上是大片空白。这里：连续空行最多留一个；块级元素（代码块/表格/引用/分隔线，
    //    此时都是占位符）前后不留空行。
    out = out.replace(/\n{3,}/g, "\n\n")
      .replace(/\n+(?=\u0000B\d+\u0000)/g, "\n")
      .replace(/(\u0000B\d+\u0000)\n+/g, "$1\n");

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

  /* 桥接就绪等待（组员评审 BUG-0010）：
     pywebview 的 `window.pywebview.api` 是**页面加载之后**才注入的（`pywebviewready`）。
     旧实现桥对象一缺席就立刻 toast「桥接未就绪」，启动瞬间或重载后点窗口按钮 =
     报一句没人看得懂的话、按钮毫无反应（"最大化后无法还原"的第二条路径）。
     现在只在「桥对象整体不存在」时进入等待（每 80ms 探一次，最多约 2.5 秒）；
     方法缺失（桥在、但后端没这个方法）仍然立即报「桥接未就绪：方法」，不改变其它
     接口的调用时序。超时才提示「界面还在初始化」。 */
  const BRIDGE_POLL_MS = 80;
  const BRIDGE_WAIT_MS = 2500;
  function waitForBridge() {
    return new Promise((resolve) => {
      const deadline = Date.now() + BRIDGE_WAIT_MS;
      const tick = () => {
        if (api()) { resolve(true); return; }
        if (Date.now() >= deadline) { resolve(false); return; }
        setTimeout(tick, BRIDGE_POLL_MS);
      };
      tick();
    });
  }

  async function call(method, ...args) {
    let bridge = api();
    if (!bridge) {
      await waitForBridge();
      bridge = api();
      if (!bridge) {
        toast("界面还在初始化，请稍候再点");
        return null;
      }
    }
    if (typeof bridge[method] !== "function") {
      toast("桥接未就绪：" + method);
      return null;
    }
    try { return await bridge[method](...args); }
    catch (err) {
      // ★ V3.6.21：以前只 toast "调用失败"，真实异常被吞，界面只能显示"未知原因"。
      //   现在把异常原文一起抛给用户，并保留 console 堆栈供排障。
      const detail = (err && (err.message || String(err))) || "未知异常";
      toast("调用失败：" + method + " — " + detail);
      console.error(err);
      return null;
    }
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
  /* ── 可读写范围（沙箱目录，V3.6.8）─────────────────────────
     设置键 `sandbox_dir`：非空 = 用户选定；空串 = 回到默认（后端按 SANDBOX_PATH 环境变量，
     再退到程序所在目录）。界面**只显示与保存**，越界怎么判定完全在内核（按权限档 + 审计），
     这里任何"自己判断一下越界"的实现都是在制造第二套口径。 */
  const effectiveSandbox = () => {
    const s = state.settings || {};
    return String(s.sandbox_dir || "").trim() || String(s.sandbox || "").trim() || "—";
  };

  function paintSandbox() {
    const s = state.settings || {};
    const custom = String(s.sandbox_dir || "").trim();
    if ($("sandbox-path")) $("sandbox-path").textContent = effectiveSandbox();
    if ($("sandbox-note")) {
      $("sandbox-note").textContent = custom
        ? "已自定义：助手只能读写这个目录及其子目录（越界按当前权限档判定并写入审计）。点「清除」回到默认。"
        : "当前用默认范围（未自定义）。选一个目录可以把它限制到你指定的工作区；清空即回到默认。";
    }
  }

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

    // 沙箱路径与设置页「可读写范围」**同一来源**（V3.6.8）：设置里填了就用它，
    // 否则回到后端给的默认值（SANDBOX_PATH 环境变量 → 程序所在目录）。
    // 界面只负责显示，越界判定不在这里做。
    const sandbox = effectiveSandbox();
    $("posture-sandbox").querySelector("span").textContent = sandbox;

    const chain = state.audit && state.audit.chain_ok;
    const c = $("posture-chain");
    c.classList.toggle("is-on", chain === true);
    c.classList.toggle("is-off", chain === false);
    c.querySelector("span").textContent = chain === true ? "完整性通过" : (chain === false ? "校验未通过" : "未校验");
  }

  /* ── 数据看板 ─────────────────────────────────── */
  function renderStats(payload) {    const model = (payload && payload.model) || state.settings.model || "—";
    $("stat-model").textContent = model.length > 24 ? model.slice(0, 22) + "…" : model;
    $("stat-tools").textContent = `工具 ${(payload && payload.tool_calls) || 0}`;
    const tokens = (payload && payload.context_tokens) || 0;
    // 阈值来自后端预算表（core/context_budget.py）——界面不再自己写数，
    // 也就不会出现"界面说 5000、实际按 8192 压缩"这种对不上账的情况（C-13）。
    const limit = (payload && payload.context_limit) || 5000;
    const label = `上下文 ${fmtTokens(tokens)}/${fmtTokens(limit)}`;
    const btn = $("stat-tokens");
    btn.textContent = label;
    const nearLimit = limit > 0 && tokens / limit >= 0.8;
    btn.classList.toggle("is-warn", nearLimit);
    // ★ 长 tooltip 文案已移除（V3.6.8）：占用口径与明细都在点开的浮层里（#ctx-pop），
    //   hover 只留一句"点开看什么"，不再把预算规则塞进 title。
    btn.title = "上下文容量：点击查看构成明细（消息 / 系统提示词 / 附件 / 工具结果）";
    // 浮层开着时同步刷新（沿用既有刷新时机：一轮结束的 state 事件，不新增轮询）
    const pop = $("ctx-pop");
    if (pop && !pop.hidden) renderCtxPop();
    renderPosture(payload);
  }

  const fmtTokens = (n) => {
    const v = Number(n) || 0;
    return v >= 1000 ? (v / 1000).toFixed(1) + "k" : String(v);
  };

  /* ── 上下文容量浮层（V3.6.8，照 ZCode 的形态）─────────────
     结构：标题行（已用/上限 + 百分比）→ 进度条 → 分类占比列表 → 底部两行汇总。
     数字**全部来自后端** `context_usage()`（服务层注释里逐项写了取数口径），前端只做呈现：
     进度条宽度直接用后端 percent，列表每项的百分比也原样显示——界面上不出现任何自己算的占比。
     底部汇总同理来自预算表；我们没有 prompt 缓存，所以不会出现"缓存命中率"这类我们没有的指标。 */
  async function renderCtxPop() {
    const data = await call("context_usage");
    if (!data || !$("ctx-list")) return;
    const pct = Number(data.percent) || 0;
    $("ctx-used").textContent = `已用 ${fmtTokens(data.used_tokens)} / ${fmtTokens(data.limit_tokens)}`;
    $("ctx-pct").textContent = `${pct}%`;
    $("ctx-fill").style.width = Math.max(0, Math.min(100, pct)) + "%";
    const parts = data.parts || [];
    $("ctx-list").innerHTML = parts.length
      ? parts.map((p, i) => `
        <div class="ctx__item" data-key="${esc(p.key)}">
          <i class="ctx__dot ctx__dot--${i % 4}"></i>
          <span class="ctx__label">${esc(p.label)}</span>
          <span class="ctx__pct">${esc(String(p.percent))}%</span>
        </div>`).join("")
      : `<div class="ctx__empty">当前没有可统计的上下文占用</div>`;
    $("ctx-foot").innerHTML = ((data.footer && data.footer.lines) || [])
      .map((line) => `<span>${esc(line)}</span>`).join("");
  }

  function openCtxPop() {
    const pop = $("ctx-pop");
    if (!pop) return;
    pop.hidden = false;
    $("stat-tokens").setAttribute("aria-expanded", "true");
    renderCtxPop();
  }

  function closeCtxPop() {
    const pop = $("ctx-pop");
    if (pop) pop.hidden = true;
    if ($("stat-tokens")) $("stat-tokens").setAttribute("aria-expanded", "false");
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
      <p>送模型的文本先在本机脱敏，回答在本机还原；文件走沙箱、执行命令需确认，全程写入审计链。</p>
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
    // 升级：等待指示让位给具体动作（状态文字本身就是"在动"的证据），
    // 否则"正在思考…"会与工具行同时挂着，看起来像两条并存的提示
    clearWaiting(entry);
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
    // 落点：当前流式气泡 → 没有就现建一条（发送时已建好，这里是"事件早到/页面中途重载"的兜底）。
    // 旧实现在没有气泡时直接丢帧——"它在想还是在卡住"就完全看不出来了（本次事故的观感之一）。
    const entry = state.streamBubble || (state.busy ? ensureStreamBubble() : null);
    const box = ensureTraceBox(entry);
    if (!box) return;
    clearWaiting(entry);   // 轨迹本身就是活动证据：等待指示让位，避免"转圈 + 轨迹"双重提示
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
    // 整屏重绘（切会话 / 看门狗自愈重载）后旧气泡节点已脱离画布：必须丢掉引用，
    // 否则后续 append 会写进一个不在文档里的节点——用户看到的就是"回复没出现"（同族事故）
    state.streamBubble = null;
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
      else if (m.role === "assistant") {
        index += 1;
        // 空的助手记录（旧数据/中断留下）也要如实呈现：直接跳过会让人以为"那一轮没发生过"
        // （事故重启后的观感就是如此），与 finalizeBubble 的空回复口径保持一致。
        const empty = !String(m.content || "").trim();
        chat.appendChild(entryEl(md(empty ? EMPTY_REPLY_NOTE : m.content),
          empty ? `<span class="bubble__tag">无内容</span>` : "", index));
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

  /** 空终稿兜底文案。为什么必须有：真机事故里后端在"直答快路径吞掉工具调用"后回了空串，
   *  界面照画一个**空气泡**——用户既没有回答、也没有任何解释，重启后那轮还消失了。
   *  宁可给一句如实说明，也不让这轮在界面上变成空白。 */
  const EMPTY_REPLY_NOTE =
    "模型这一轮没有给出内容，可以再问一次或换个说法";

  /** 思考中的临时条目。**发送时立刻建**（不依赖后端第一条事件）：
   *  按下发送到模型吐出第一个字之间有实打实的空窗期，此前这段对话区毫无变化，
   *  用户只能盯着顶栏的"已就绪"猜有没有点上（负责人实测反馈）。
   *  它同时是 status（→ 工具行）/ trace（→ 轨迹行）/ intermediate_result（→ 流式正文）
   *  的同一个落点，因此不会出现"等待气泡 + 正文气泡"两条并存。
   *  升级由 clearWaiting() 完成：摘掉脉动指示，条目本身留下、内容写进同一个 .entry__body。 */
  function ensureStreamBubble() {
    if (state.streamBubble) return state.streamBubble;
    const chat = $("chat");
    if (chat.querySelector(".chat__empty")) chat.innerHTML = "";
    const index = chat.querySelectorAll(".entry").length + 1;
    const row = entryEl(
      `<span class="waiting"><span class="typing"><i></i><i></i><i></i></span>` +
      `<span class="waiting__text">正在思考…</span></span>`, "", index);
    row.querySelector(".entry__body").classList.add("is-thinking");
    chat.appendChild(row);
    state.streamBubble = row;
    scrollToEnd();
    return row;
  }

  /** 等待态 → 有活动/有内容：摘掉脉动指示（条目留下，正文与工具行写进同一个条目）。
   *  为什么不是"删掉等待条目再建一条"：那会出现气泡闪烁、编号跳号，
   *  也会破坏"trace 落到当前气泡里"这条约定。 */
  function clearWaiting(entry) {
    const row = entry || state.streamBubble;
    if (!row) return;
    const body = row.querySelector(".entry__body");
    if (body) body.classList.remove("is-thinking");
    const wait = row.querySelector(".waiting");
    if (wait) wait.remove();
  }

  /** 发送被拒（算力未就绪/正在推理）时撤掉刚建的等待气泡：它一条内容都没有，
   *  留着就是事故里那种"空气泡"。已有内容则一律保留在画布上（内容不能丢）。 */
  function dropWaitingBubble() {
    const row = state.streamBubble;
    if (!row) return;
    const body = row.querySelector(".entry__body");
    const wait = row.querySelector(".waiting");
    // "纯等待"＝气泡里只有那个脉动指示（没有工具行/轨迹/正文）
    const pureWaiting = !!wait && !!body && body.children.length === 1
      && body.firstElementChild === wait
      && !row.querySelector(".entry__tools") && !row.querySelector(".entry__trace");
    if (pureWaiting) { state.streamBubble = null; row.remove(); }
  }

  /** 本轮已空闲（state.busy=false）但气泡还开着：说明 finished/error 这条终止事件没送到
   *  （桥接抖动、渲染异常）。必须收口——两份代价都真实:
   *    ① 继续显示"正在思考…"→ 看起来永远卡住；
   *    ② 引用留着 → 下一轮的工具行/正文会写进**上一轮**的条目里（回复挂到错的问题下面）。
   *  收口口径与空回复一致：有内容就保留内容（只补状态标记），没内容才给兜底文案。
   *  正常路径走不到这里：后端在同一线程里先发 finished/error、再由 _finalize 发 state，
   *  到达前端时引用已被 finalizeBubble 清掉。 */
  function closeStaleBubble() {
    if (state.streamBubble) finalizeBubble("", "无内容");
  }

  /** 结束本轮：把等待气泡固定成最终回复。空回复防御见 EMPTY_REPLY_NOTE。 */
  function finalizeBubble(text, tagText) {
    const row = state.streamBubble;
    state.streamBubble = null;
    const content = String(text == null ? "" : text);
    const isEmpty = !content.trim();
    if (!row) {
      const chat = $("chat");
      // 没有可落的条目（终止事件先到/页面刚重载）：空串也要留下如实说明，不能什么都不显示
      chat.appendChild(entryEl(md(isEmpty ? EMPTY_REPLY_NOTE : content),
        isEmpty ? `<span class="bubble__tag">无内容</span>` : "",
        chat.querySelectorAll(".entry").length + 1));
      scrollToEnd();
      return;
    }
    clearWaiting(row);
    const body = row.querySelector(".entry__body");
    if (!body) { scrollToEnd(); return; }
    const secs = state.startedAt
      ? Math.max(1, Math.round((Date.now() - state.startedAt) / 1000)) : 0;
    const metaHtml = (tag) => `<div class="bubble__meta">
        <span class="bubble__tag">${esc(tag)}</span>${secs ? `<span>${secs}s</span>` : ""}</div>`;
    if (isEmpty && body.textContent.trim()) {
      // 空终稿，但气泡里已有流式正文：正文优先——只补状态标记，绝不把已收到的内容抹掉
      body.insertAdjacentHTML("beforeend", metaHtml("无内容"));
      scrollToEnd();
      return;
    }
    body.classList.toggle("is-empty-reply", isEmpty);
    body.innerHTML = md(isEmpty ? EMPTY_REPLY_NOTE : content) + metaHtml(isEmpty ? "无内容" : (tagText || "快速"));
    scrollToEnd();
  }

  /* ── 忙碌态 ───────────────────────────────────────── */
  function setBusy(busy) {
    state.busy = busy;
    const btn = $("btn-send");
    // 忙碌态：方形停止键 + 模型名前的小转圈（参考 ZCode 新输入区）；
    // 两个类互斥，避免 pill--send 的浅底盖住 pill--stop 的红色。
    btn.classList.toggle("pill--stop", busy);
    btn.classList.toggle("pill--send", !busy);
    btn.title = busy ? "停止生成" : "发送";
    btn.innerHTML = busy
      ? `<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>`
      : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>`;
    const spinner = $("composer-spinner");
    if (spinner) spinner.hidden = !busy;
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

  /** 状态行显示层口径（组员评审 BUG-0009）：
   *  后端状态文本形如 `[思考 1/6] 模型判断中...`（ASCII 括号），但「思考」同时是
   *  顶栏模式名（快速 / 思考）——同一屏出现「快速」模式标签 + 「思考 1/6」状态行，
   *  读起来互相打架。这里只改写**显示前缀**为「推理」，不动后端语义，也不碰气泡底部
   *  的模式标签（那条走 `finished` 的 turnMode 快照，是另一条路径）。 */
  function displayStatusText(text) {
    // ★ 后端真实发射的是 ASCII 括号（`[思考 1/6] 模型判断中...`，见 agent_worker.py），
    //   旧正则只认全角 `【思考 N/M】`，真机上永远不命中——测试却用全角串，属于假绿。
    //   这里两种括号都接受，输出统一成全角「【推理 N/M】」；后端保持 ASCII 不改。
    return String(text || "").replace(
      /^(\s*)[\[【]思考\s*(\d+)\s*\/\s*(\d+)[\]】]/, "$1【推理 $2/$3】");
  }
  function setStatus(text) { $("status-line").textContent = displayStatusText(text); }

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

  /** Ctrl+Enter / Cmd+Enter 换行（报告 P1-06）：浏览器默认不会给 textarea 插入换行，
   *  旧实现又把它当 Enter 直接发送，习惯这个组合的人会误发半截消息。 */
  function insertNewline(el) {
    if (!el) return;
    const start = el.selectionStart == null ? el.value.length : el.selectionStart;
    const end = el.selectionEnd == null ? start : el.selectionEnd;
    el.value = el.value.slice(0, start) + "\n" + el.value.slice(end);
    el.selectionStart = el.selectionEnd = start + 1;
    autosizeInput(el);
    el.dispatchEvent(new Event("input", { bubbles: true }));
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
    // 折叠态下最大化按钮要禁用（服务端也会拒绝：浮条上最大化会卡在半个状态里）
    paintWindowState();
  }

  async function sendText(text) {
    text = (text || "").trim();
    if (!text) return;
    appendUser(text);
    state.startedAt = Date.now();
    // ★ 记下**这一轮发出时**的模式与思考强度（组员评审 BUG-0006）：气泡底部的模式标签
    //   原先在 `finished` 时才去读 `state.settings.think_mode`——期间用户改了模式（或某次
    //   设置回填把 `state.settings` 覆盖回默认），已发出的那一轮就会被贴上**别的**模式标签
    //   （实测：顶栏/输入区是「思考 最高」，气泡却写「快速 2s」）。按轮次快照，标签只反映
    //   这一轮实际用的模式。
    state.turnMode = state.settings.think_mode || "快速回答";
    state.turnDepth = state.settings.thinking_depth;
    setBusy(true);
    // ★ 发送即建"等在那儿"的等待气泡：不等后端第一条事件（直答快路径以前一条都不发，
    //   整段生成期对话区毫无变化——用户看到的正是"点了没反应、也没有思考动画"）。
    ensureStreamBubble();
    // 刚点完「快速/思考」就立刻回车时，先把防抖中的模式落库，保证本轮用的是用户看到的模式
    await ensureThinkModeSaved();
    const ok = await call("send", text);
    if (!ok) {
      setBusy(false);
      // 根本没开始推理：把刚建的空等待气泡撤掉（留着就是一条空气泡），提示语保持不变
      dropWaitingBubble();
      toast("发送失败：算力未就绪或正在推理");
    } else await refreshSessions();
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
    // 建议是异步补发的（本地模型判断可能慢）。旧实现先清空，结果晚到半秒时
    // 弹窗突然长高、内容整体下移——负责人实测"抖一下"。现在先占位并显示
    // "研判中"动画，高度按两条建议预留；结果到达后就地替换，不再跳。
    // 没有启用任何建议插件时不占位（否则会一直转到弹窗关闭，像卡住了）。
    if (payload.advisory_available === false) setAdvisoryLoading(false);
    else setAdvisoryLoading(true);
    // 用途红线由内核在模型/工具之前判定（V3.6.13）。审批弹窗最上方先回答
    // "这个请求想干什么"，再让人看"操作是否相符/操作风险"——顺序反了就会
    // 出现"与请求相符 + 低危"给一单卖个人信息的操作背书。
    const intent = String(payload.intent_warning || "").trim();
    const intentBox = $("approval-intent");
    if (intent) {
      intentBox.textContent = intent;
      intentBox.style.display = "";
      $("approval-lead").textContent = "该请求涉及敏感用途，已挂起等待人工审批。";
    } else {
      intentBox.style.display = "none";
      $("approval-lead").textContent = "检测到高危操作，已挂起等待人工审批。";
    }
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
    setAdvisoryLoading(false);
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

  /** 链校验是否失败。后端历史上给过三种同义写法，前端都认：
   *  顶层 `chain_ok === false`、嵌套 `chain.ok === false`、`stats.chain_valid === false`。
   *  注意：只在字段**明确为 false** 时算失败（缺字段不冒充实锤失败）。 */
  function auditChainFailed(data) {
    if (!data) return false;
    if (typeof data.chain_ok === "boolean") return !data.chain_ok;
    if (data.chain && typeof data.chain.ok === "boolean") return !data.chain.ok;
    const stats = data.stats || {};
    if (typeof stats.chain_valid === "boolean") return !stats.chain_valid;
    return false;
  }

  function auditChainReason(data) {
    const stats = (data && data.stats) || {};
    const chain = (data && data.chain) || {};
    return String(
      (data && (data.chain_reason || data.reason)) || stats.chain_reason || chain.reason || "");
  }

  function auditChainAnchor(data) {
    const stats = (data && data.stats) || {};
    const chain = (data && data.chain) || {};
    return String(stats.chain_anchor || chain.anchor || (data && data.chain_anchor) || "none");
  }

  /** 把右侧面板切到审计页（只改 DOM 状态，不调用 openPane —— 否则会和
   *  PANE_LOADERS.audit → renderAudit 形成递归）。 */
  function activateAuditPane() {
    document.querySelectorAll(".ptab").forEach((t) =>
      t.classList.toggle("is-active", t.dataset.pane === "audit"));
    document.querySelectorAll(".pane").forEach((p) =>
      p.classList.toggle("is-active", p.id === "pane-audit"));
    state.pane = "audit";
    startAuditLive();
  }

  /* 审计面板实时刷新：面板打开期间每 2 秒拉一次快照；没有新条目就不重绘，
     重绘时保留滚动位置，避免用户正在翻看时被拉回顶部。 */
  let auditLiveTimer = null;
  let auditLiveSig = "";
  function auditSig(data) {
    const s = (data && data.stats) || {};
    const e = ((data && data.entries) || [])[0] || {};
    return [s.total_entries || 0, s.chain_valid, data && data.chain_ok,
            e.hash || "", e.time || ""].join("|");
  }
  async function refreshAuditLive() {
    if (state.pane !== "audit" || rightCollapsed() || document.visibilityState === "hidden") return;
    try {
      const data = await call("audit_snapshot", 300);
      const sig = auditSig(data);
      if (sig === auditLiveSig) return;
      const list = $("audit-list");
      const top = list ? list.scrollTop : 0;
      renderAudit(data);
      if (list) list.scrollTop = top;
    } catch (e) { /* 下一次 tick 再试；不打断当前界面 */ }
  }
  function startAuditLive() {
    stopAuditLive();
    auditLiveTimer = setInterval(refreshAuditLive, 2000);
  }
  function stopAuditLive() {
    if (auditLiveTimer) { clearInterval(auditLiveTimer); auditLiveTimer = null; }
  }

  function renderAudit(data) {
    state.audit = data || { stats: {}, chain_ok: false, entries: [] };
    auditLiveSig = auditSig(state.audit);
    const stats = state.audit.stats || {};
    const failed = auditChainFailed(state.audit);
    const wasFailed = !!state.auditFailed;
    // 没有明确的链信号时沿用旧口径（按"未通过"显示徽标），但不触发自动展开 / 横幅——
    // 那两者必须有明确的 `false` 才动作，避免把"后端没给字段"当成实锤篡改。
    const hasSignal = typeof state.audit.chain_ok === "boolean"
      || (state.audit.chain && typeof state.audit.chain.ok === "boolean")
      || typeof stats.chain_valid === "boolean";
    const ok = hasSignal ? !failed : false;
    const badge = $("audit-chain");
    badge.className = "chain-badge " + (ok ? "chain-badge--ok" : "chain-badge--bad");
    badge.textContent = ok
      ? "✔ 哈希链完整性通过（HMAC-SHA256 + 链锚点）"
      : "⚠︎ 哈希链校验未通过";
    // 校验未通过时给出**人话原因**；"锚点缺失 / 迁移"这一类是可执行动作，必须给按钮。
    const reason = auditChainReason(state.audit);
    const anchor = auditChainAnchor(state.audit);
    const needsAnchor = failed && !reason.includes("哈希不匹配") &&
      (reason.includes("锚点缺失") || reason.includes("迁移") || reason.includes("首次升级") ||
       anchor === "none");
    // ★ 顶部红色横幅（BUG-0011）：校验失败这件事必须"看得见 + 点得到"，
    //   而不是只在收起的右栏里变一个红字。
    const banner = $("audit-banner");
    const bannerText = $("audit-banner-text");
    if (banner) {
      banner.hidden = !failed;
      if (failed && bannerText) {
        bannerText.textContent = "审计链校验未通过：" + (reason || "未给出原因");
      }
      const reanchor = $("btn-reanchor");
      if (reanchor) {
        reanchor.hidden = !needsAnchor;
        reanchor.onclick = async () => {
          const res = await call("audit_reanchor", "用户在审计面板确认重建锚点");
          toast(res && res.ok ? `已重建锚点（当前 ${res.count} 条）` : `重建失败：${(res && res.error) || "未知原因"}`);
          renderAudit(await call("audit_snapshot", 300));
        };
      }
    }
    // ★ 自动展开（BUG-0011）：只在「状态从正常变为失败」时发生一次。
    //   用户手动收起后，同样的失败反复到达也不会再把面板弹开（wasFailed 已为 true）；
    //   状态恢复后 auditFailed 归零，下一次新的失败才会再次自动展开。
    if (failed && !wasFailed) {
      activateAuditPane();
      if (rightCollapsed()) setRightCollapsed(false);
      const scroller = document.querySelector(".panel__body");
      if (scroller) scroller.scrollTop = 0;
      if (banner) banner.scrollIntoView({ block: "start" });
    }
    state.auditFailed = failed;
    const box = $("audit-chain-detail");
    if (box) {
      box.innerHTML = (ok && !reason) ? "" :
        `<span class="chain-detail__text">${esc(reason || "校验未通过")}</span>`;
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
  /** 列表上方的持久说明：入库失败/部分失败的原因必须留在面上（toast 一闪就没了）。
   *  保留到下一次入库开始时才清空，刷新列表 / 重新进入设置页都不会丢。 */
  function paintKbNotice(text, kind = "") {
    state.kbNotice = String(text || "");
    const el = $("kb-notice");
    if (!el) return;
    el.textContent = state.kbNotice;
    el.hidden = !state.kbNotice;
    el.className = "kb-notice" + (kind === "bad" ? " is-bad" : (kind === "warn" ? " is-warn" : ""));
  }

  function renderKb(status) {
    const data = status || {};
    const hint = $("kb-status");
    const available = !!data.available;
    // `available`（依赖装没装）与 `embedding_ready`（Ollama + embed 模型能不能用）是两件事：
    // 只有后者为真才允许点「添加文档」，否则用户点了必然失败。旧后端/沙盘没有该字段时按可用处理。
    const embeddingReady = data.embedding_ready !== false;
    const usable = available && embeddingReady;
    if (!available) {
      hint.textContent = `不可用：${data.error || "未知原因"}`;
    } else if (!embeddingReady) {
      hint.textContent = `不可用：${data.error || "本地向量模型未就绪"}`;
    } else {
      hint.textContent = `就绪 · 文档 ${(data.stats && data.stats.total_documents) || (data.docs || []).length} 篇 · 片段 ${(data.stats && data.stats.total_chunks) || "—"} · 模型 ${data.embed_model || "—"}`;
    }
    hint.style.color = usable ? "var(--text-3)" : "var(--danger-500)";
    const addBtn = $("btn-kb-add");
    addBtn.disabled = !usable;
    addBtn.title = usable ? "" : (data.error || "知识库不可用，暂不能添加文档");
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
             hint: "本机运行的 Ollama，默认 127.0.0.1:11434" },
    lan: { label: "局域网模型", boundary: "数据出这台电脑，但不出企业内网", cls: "tier--lan",
           hint: "公司内网的推理服务，填内网地址" },
    wan: { label: "公网模型", boundary: "数据出企业内网；送出前内容已在本机脱敏", cls: "tier--wan",
           hint: "云端 OpenAI 兼容接口" },
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

  /* ══ 模型接入页（V3.6.8 按 ZCode「模型设置」的结构重做）════════════════════
     结构：左列 = 三档分组（本机 / 局域网 / 公网）内**按供应商聚合**（自定义模型按 base_url
           归组，provider 兜底在服务层做，老配置无需迁移）；右侧 = 选中供应商的表单
           （显示名称 / Base URL / API 格式 / API Key）+ 它的模型列表 + 底部动作。
     两条硬规则（都是"照搬前先想清楚语义"的结果）：
       ① **背景高亮只表示"正在查看"的那一项**（`.prov__item.is-active`，同一时刻最多一个）。
          「使用中」一律用绿点 + 绿字（`.usage-flag`）——旧实现两种语义都用背景，截图里
          两个条目同时亮，"谁在跑 / 我在看谁"根本分不清（负责人实测报的缺陷）。
       ② **同一时刻只有一个"使用中"模型**（单选式，点模型名即切换）：ZCode 的模型列表是
          多个启停开关，但我们的后端确实只有一个 `settings.model`（换模型＝重建算力），
          做成开关会让人以为能同时开好几个——这里有意不照搬，不变量由内核保证。 */

  const OLLAMA_PROVIDER = "本机 Ollama";   // 本机档的内置供应商（Ollama 探测得来，不是手工配置项）
  // 手动添加的本机模型单独一组：**不并进「本机 Ollama」**——那一组是"探测到的"（不可编辑 /
  // 不可删除），并进去会让用户没法改/删自己手填错的条目；换个清楚的组名反而更好懂。
  const MANUAL_LOCAL_PROVIDER = "本机 Ollama（手动添加）";

  // 接口格式（V3.6.21：四种都真能用）。
  //   chat_completions —— OpenAI 兼容（含免 Key 中转）
  //   ollama_native    —— Ollama 原生 /api/chat（远程/局域网 Ollama）
  //   anthropic        —— Anthropic Messages API
  //   responses        —— OpenAI Responses API
  // 内核侧 `agent_service.API_FORMATS` 必须与此一致；未知值回落 chat_completions。
  const API_FORMATS = [
    { key: "chat_completions", label: "OpenAI 兼容 Chat Completions", ok: true },
    { key: "ollama_native", label: "Ollama 原生接口（/api/chat）", ok: true },
    { key: "anthropic", label: "Anthropic Messages", ok: true },
    { key: "responses", label: "OpenAI Responses", ok: true },
  ];

  const currentModel = () => (state.settings && state.settings.model) || "";

  /** 算力清单是否已到（拿到之前界面不许宣称任何模型在用，显示「检查模型…」）。 */
  const catalogReady = () => !!(state.catalog && typeof state.catalog.current_available === "boolean");

  /** **可用**的当前模型（「使用中」判定用这个，不是配置里的裸名字）。
   *  K（负责人实测）：便携版在同学机器上胶囊显示 `qwen3.5:4b`，而那台机器根本没有这个模型——
   *  界面把"配置里写了什么"当成"现在能用什么"是错的。`current_available` 由后端给
   *  （清单里真的能选到才算），为 false 时一律当作"没选模型"，任何地方都不再显示这个名字。 */
  const activeModel = () => {
    const cur = currentModel();
    if (!catalogReady()) return "";
    return state.catalog.current_available ? cur : "";
  };

  /** 「使用中」标记：绿点（+ 可选的"使用中"文字），**绝不使用背景**（规则 ①）。
   *  `data-cur-flag` 的值 = 命中的模型名；paintUsage() 按当前模型统一收起/放出。 */
  const usageFlag = (cur, dotOnly) => dotOnly
    ? `<i class="usage-flag usage-flag--dot" data-cur-flag="${esc(cur)}" title="使用中"></i>`
    : `<span class="usage-flag" data-cur-flag="${esc(cur)}"><i class="usage-flag__dot"></i>使用中</span>`;

  /** 重绘「使用中」标记（切模型后当场生效）。
   *  为什么不整页重渲染：右栏表单里可能有用户刚敲了一半的 Base URL / 密钥，
   *  整页重绘会把它们吞掉（旧实现的老毛病）。
   *  做法：右栏模型行按 `data-cur-flag` 就地收起/放出；**左列整块重绘并重绑事件**——
   *  供应商条目的绿点取决于"这一组里有没有正在用的模型"，那是渲染期算出来的，
   *  只靠标记切换会留下过期状态（左列没有输入控件，重绘无副作用）。 */
  function paintUsage() {
    const cur = activeModel();
    document.querySelectorAll("#prov-detail [data-cur-flag]").forEach((el) => {
      el.hidden = !cur || el.dataset.curFlag !== cur;
    });
    if (state.catalog && $("prov-list")) {
      renderProvList();
      bindProvListEvents();      // 事件委托绑在容器上；重绘后仍有效，这里调用只为幂等保证
    }
  }

  /** 一档内按供应商聚合。本机档的 Ollama 探测结果归到内置供应商「本机 Ollama」。 */
  function providersOfTier(cat, tier) {
    const groups = [], byKey = new Map();
    (((cat || {}).tiers || {})[tier] || []).forEach((m) => {
      const builtin = m.source === "ollama";
      const provider = builtin ? OLLAMA_PROVIDER : (m.provider || "未命名供应商");
      const key = `${tier}::${provider}::${builtin ? 1 : 0}`;
      let g = byKey.get(key);
      if (!g) { g = { key, tier, provider, builtin, models: [] }; byKey.set(key, g); groups.push(g); }
      g.models.push(m);
    });
    groups.sort((a, b) => (a.builtin === b.builtin ? 0 : (a.builtin ? -1 : 1)));   // 内置排最前
    return groups;
  }

  /** 某个模型挂在哪个供应商下（触发器显示「供应商 / 模型」、选择器默认展开项都用它）。 */
  function placementOf(name) {
    if (!name) return null;
    for (const tier of ["local", "lan", "wan"]) {
      for (const g of providersOfTier(state.catalog, tier)) {
        if (g.models.some((m) => m.name === name)) return g;
      }
    }
    return null;
  }

  function formatOptions(current) {
    const cur = API_FORMATS.some((f) => f.key === current) ? current : "chat_completions";
    return API_FORMATS.map((f) =>
      `<option value="${f.key}"${f.key === cur ? " selected" : ""}${f.ok ? "" : " disabled"}>` +
      `${esc(f.label)}${f.ok ? "" : "（暂不支持）"}</option>`).join("");
  }

  const splitTags = (text) =>
    String(text || "").split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean);

  /** 模型列表：每行 = 显示名称 + 模型 ID + 标签 + 「使用中」标记 + 编辑/删除。
   *  本机 Ollama 的条目由安装决定，不提供编辑/删除（改不了，也不该假装能改）。 */
  function modelListHtml(models, builtin) {
    const rows = (models || []).map((m) => {
      const tags = (m.tags || []).map((t) => `<i class="mtag">${esc(t)}</i>`).join("");
      const mid = builtin ? "本机已安装" : String(m.model_id || "");
      const showMid = !!mid && mid !== m.name;
      return `<div class="mrow" data-mname="${esc(m.name)}">
        <button class="mrow__use" type="button" data-use="${esc(m.name)}" title="点一下即切换为使用中的模型">
          <span class="mrow__name">${esc(m.name)}</span>
          ${showMid ? `<span class="mrow__mid">${esc(mid)}</span>` : ""}
          ${tags}
        </button>
        ${usageFlag(m.name, false)}
        ${builtin ? "" : `<span class="mrow__ops">
          <button class="mbtn" type="button" data-edit="${esc(m.name)}">编辑</button>
          <button class="mbtn mbtn--danger" type="button" data-del="${esc(m.name)}">删除</button></span>`}
      </div>`;
    }).join("");
    const body = rows
      ? `<div class="mlist" id="p-models">${rows}</div>`
      : (builtin
         // 本机档的空态文案要指向**真实存在的**出口（本机档没有"＋ 添加模型"按钮，
         // 出口在下面那句说明与「重新检测 / ＋ 手动添加本机模型」上，别指着不存在的按钮）
         ? `<div class="mlist mlist--empty">还没有检测到可对话的本机模型，可用下面的「重新检测」或「＋ 手动添加本机模型」</div>`
         : `<div class="mlist mlist--empty">这个供应商还没有模型，点下面「＋ 添加模型」添加</div>`);
    // 添加模型只对自定义供应商开放（本机 Ollama 的模型来自安装，不是手工添加的对象）；
    // 本机档的手动添加入口在"空态说明"旁边（data-addlocal），见 renderProv。
    return body + (builtin ? "" : `<button class="prov__add" type="button" data-addmodel="1">＋ 添加模型</button>`);
  }

  /** 模型编辑面板：新增供应商时它就是"第一个模型"（必填，随供应商一起保存）；
   *  已有供应商时默认收起，点「编辑 / ＋ 添加模型」才展开。 */
  function modelEditPanelHtml(isNew) {
    return `<div class="medit" id="pm-panel"${isNew ? "" : " hidden"}>
      <div class="medit__title" id="pm-title">${isNew ? "第一个模型" : "添加模型"}</div>
      <div class="medit__row"><label for="pm-model">模型 ID</label>
        <input type="text" id="pm-model" spellcheck="false" placeholder="服务端认的模型名，如 glm-4-flash"></div>
      <div class="medit__row"><label for="pm-name">显示名称</label>
        <input type="text" id="pm-name" spellcheck="false" placeholder="留空 = 用模型 ID"></div>
      <div class="medit__row"><label for="pm-tags">标签</label>
        <input type="text" id="pm-tags" spellcheck="false"
          placeholder="可选，逗号分隔；只标真实能力（如 长文 / 中文 / 低成本）"></div>
      <div class="medit__row"><label for="pm-ctx">上下文窗口</label>
        <input type="number" id="pm-ctx" min="2048" max="131072" step="1024"
          placeholder="tokens，留空＝按模型家族估值（未知默认 16384）"></div>
      ${isNew ? "" : `<div class="medit__ops">
        <button class="btn btn--primary" type="button" data-msave="1">保存模型</button>
        <button class="btn" type="button" data-mcancel="1">取消</button></div>`}
      <div class="medit__hint" id="pm-note">${isNew
        ? "供应商需挂至少一个模型才能保存：填好模型 ID 后点上面的「保存」。"
        : ""}</div>
    </div>`;
  }

  /** 供应商表单（局域网 / 公网；也可能是显式配到本机档的自建服务）。 */
  function provFormHtml(g, sel) {
    const meta = TIER_META[sel.tier];
    const isNew = !!sel.isNew;
    const first = (g && g.models[0]) || {};
    const hasKey = !!(g && g.models.some((m) => m.has_key));
    const keyHint = hasKey
      ? "已在本机加密保存；留空＝不改动，不会清空"
      : (sel.tier === "lan" ? "内网通常可留空，只在本机加密保存" : "只在本机加密保存");
    return `
      <h3 class="prov__title">${esc(isNew ? "添加" + meta.label : g.provider)}
        <span class="prov__badge">${esc(meta.boundary)}</span></h3>
      <p class="prov__desc">${esc(meta.hint)}；出网内容先在本机脱敏，密钥加密存本机、不回显。</p>
      <div class="setcard">
        <div class="setrow"><div class="setrow__text"><b>显示名称</b>
            <span>供应商名：左列分组与模型选择器里显示的名字</span></div>
          <div class="setrow__ctrl"><input type="text" id="p-provider" spellcheck="false"
            value="${esc(isNew ? "" : g.provider)}"
            placeholder="${sel.tier === "lan" ? "如 公司内网" : "如 云端GLM"}"></div></div>
        <div class="setrow"><div class="setrow__text"><b>Base URL</b>
            <span>OpenAI 兼容的基础地址（不要带 /chat/completions）</span></div>
          <div class="setrow__ctrl"><input type="text" id="p-url" spellcheck="false"
            value="${esc(isNew ? "" : (first.base_url || ""))}"
            placeholder="${sel.tier === "lan" ? "http://10.0.0.8:8000/v1" : "https://…/v1"}"></div></div>
        <div class="setrow"><div class="setrow__text"><b>API 格式</b>
            <span>仅 OpenAI 兼容 Chat Completions 可用，其它格式暂不支持</span></div>
          <div class="setrow__ctrl"><select id="p-format">${formatOptions(first.api_format || "chat_completions")}</select></div></div>
        <div class="setrow"><div class="setrow__text"><b>API Key</b><span>${keyHint}</span></div>
          <div class="setrow__ctrl">
            <input type="text" id="p-key" value="" spellcheck="false" autocomplete="off"
              placeholder="${hasKey ? "已配置（留空则沿用已存密钥）" : "未配置"}">
            <button class="btn" type="button" id="p-key-eye"
              title="查看本机已存密钥（写入审计）">查看</button>
          </div></div>
        <div class="setrow"><div class="setrow__text"><b>连通性</b><span id="probe-detail">尚未测试</span></div>
          <div class="setrow__ctrl">
            <button class="btn" type="button" data-probe="detail">测试连接</button>
            <button class="btn btn--primary" type="button" data-save="1">保存</button>
            ${isNew ? "" : `<button class="btn" type="button" data-remove-provider="1">删除</button>`}
          </div></div>
      </div>
      <h4 class="prov__subtitle">模型列表
        <span class="muted">（同一时刻只有一个「使用中」；点模型名即切换）</span></h4>
      ${isNew ? "" : modelListHtml(g.models, false)}
      ${modelEditPanelHtml(isNew)}`;
  }

  /** 左列用的分组：**本机档永远有一个可进入的「本机 Ollama」条目**，
   *  即便 Ollama 连不上 / 一个模型都没装（`tiers.local` 为空）。
   *  为什么必须有（负责人反馈三次的那条缺陷的根因）：本机档的右栏承载
   *  「重新检测 / ＋ 手动添加本机模型」这两条**出路**，而左列是唯一的档位导航——
   *  本机档一旦没有条目，用户点了别的档（例如「＋ 添加供应商」进了局域网草稿）之后
   *  就再也回不到本机档，真机表现就是「点了添加局域网模型之后就点不动添加本地模型了」。
   *  只影响左列（模型选择器弹层仍按真实模型聚合，不凭空多一个空组）。 */
  function groupsForList(cat, tier) {
    const groups = providersOfTier(cat, tier);
    if (tier === "local" && !groups.some((g) => g.builtin)) {
      groups.unshift({ key: `local::${OLLAMA_PROVIDER}::1`, tier: "local",
                       provider: OLLAMA_PROVIDER, builtin: true, models: [] });
    }
    return groups;
  }

  /** 左列（三档分组 → 供应商 + 本机档摊开的模型子项）单独成函数：
   *  切模型时只需重绘它（见 paintUsage），右栏未保存的表单不受影响。 */
  function renderProvList() {
    const box = $("prov-list"), cat = state.catalog, sel = state.prov;
    if (!box || !cat || !sel) return;
    const cur = activeModel() || cat.current || "";
    box.innerHTML = ["local", "lan", "wan"].map((tier) => {
      const meta = TIER_META[tier];
      const rows = groupsForList(cat, tier).map((g) => {
        // 「正在查看」＝唯一允许背景高亮的状态（规则 ①）
        const active = !sel.isNew && sel.tier === tier && sel.provider === g.provider
          && !!sel.builtin === !!g.builtin;
        const usedBy = g.models.find((m) => m.name === cur);
        const info = g.builtin
          ? (g.models.length ? `${g.models.length} 个已安装模型` : "还没有检测到模型")
          : `${g.models.length} 个模型 · ${String((g.models[0] || {}).base_url || "").replace(/^https?:\/\//, "")}`;
        // 本机档额外摊开 Ollama 已安装模型（点一下即切换）：本机模型就那几个，少一步点击；
        // 局域网/公网可能挂很多模型，不摊开（避免左列变成一长串），数量在 meta 里写明。
        const subs = g.builtin ? g.models.map((m) => `
          <button class="prov__sub" type="button" data-t="${tier}" data-prov="${esc(g.provider)}"
                  data-builtin="1" data-use="${esc(m.name)}" title="点一下即切换为使用中的模型">
            <span class="prov__subname">${esc(m.name)}</span>${usageFlag(m.name, true)}
          </button>`).join("") : "";
        return `<button class="prov__item${active ? " is-active" : ""}" type="button"
            data-t="${tier}" data-prov="${esc(g.provider)}" data-builtin="${g.builtin ? 1 : 0}">
            <span class="prov__name">${esc(g.provider)}${usageFlag(usedBy ? usedBy.name : "", true)}</span>
            <span class="prov__meta">${esc(info)}</span>
          </button>${subs}`;
      }).join("");
      // 每一档都有**同一处**的「＋ 添加…」入口（"添加"通路与档位解耦）：本机档的"添加"
      // 不是加供应商，而是手动填一个本机模型——入口仍放在本机群组里，点了就进本机档并
      // 当场展开手动添加面板，不必再"先点回本机档、再去右栏找按钮"。
      const add = tier === "local"
        ? `<button class="prov__add" type="button" data-addt="local">＋ 手动添加本机模型</button>`
        : `<button class="prov__add" type="button" data-addt="${tier}">＋ 添加供应商</button>`
          + (tier === "wan"
            ? `<button class="prov__add" type="button" data-addrelay="1"
                       title="公共中转站：免 Key、有模型白名单与限流（适合评委/临时机器）">＋ 添加公共中转（免 Key）</button>`
            : "");
      return `<div class="prov__group">
        <div class="prov__gtitle"><span class="tier-dot tier-dot--${tier}"></span>
          <b>${meta.label}</b><em>${meta.boundary}</em></div>
        ${rows}
        ${add}
      </div>`;
    }).join("");
    // 左列自己的"使用中"绿点在渲染期就定下来了（paintUsage 会重绘它），
    // 这里只需保证首次渲染后标记状态正确
    paintUsageFlagsIn(box, cur);
  }

  /** 只切换容器内的 `data-cur-flag` 显隐（不重绘 DOM）。 */
  function paintUsageFlagsIn(root, cur) {
    root.querySelectorAll("[data-cur-flag]").forEach((el) => {
      el.hidden = !cur || el.dataset.curFlag !== cur;
    });
  }
  /** 渲染「模型接入」页：左侧按三档列供应商，右侧是选中供应商的表单与模型列表。 */
  function renderProv(cat) {
    const box = $("prov-list"), detail = $("prov-detail");
    if (!box || !detail || !cat || !cat.tiers) return;
    state.catalog = cat;
    const cur = activeModel() || cat.current || "";

    // 选中的供应商必须仍然存在（删掉 / 改名 / 清单刷新后消失 → 回落到本机档内置供应商），
    // 否则左列会出现"没有一个条目是选中态"或高亮错位。
    const findGroup = (s) => (s && providersOfTier(cat, s.tier).find(
      (g) => g.provider === s.provider && !!g.builtin === !!s.builtin)) || null;
    if (!state.prov || (!state.prov.isNew && !findGroup(state.prov))) {
      state.prov = { tier: "local", provider: OLLAMA_PROVIDER, builtin: true };
    }
    const sel = state.prov;

    renderProvList();
    // 注意：接下来的右栏渲染 + bindProvEvents 之后才轮到 paintUsage，
    // 而 paintUsage 会重绘左列——所以左列事件单独绑（见 bindProvListEvents）。

    // ── 右侧：选中供应商的表单 + 模型列表 ──
    if (sel.builtin) {
      const g = findGroup(sel) || { provider: OLLAMA_PROVIDER, models: [] };
      detail.innerHTML = `
        <h3 class="prov__title">${esc(OLLAMA_PROVIDER)}<span class="prov__badge">${esc(TIER_META.local.boundary)}</span></h3>
        <p class="prov__desc" title="把地址改成别的机器，数据就会出这台电脑；那种情况请改用「局域网模型」">本机安装的 Ollama 服务</p>
        <div class="setcard">
          <div class="setrow">
            <div class="setrow__text"><b>服务地址</b><span>默认 http://127.0.0.1:11434</span></div>
            <div class="setrow__ctrl"><input type="text" id="ollama-host" spellcheck="false"
              autocomplete="off" placeholder="http://127.0.0.1:11434"></div>
          </div>
          <div class="setrow">
            <div class="setrow__text"><b>连通性</b><span id="probe-local" title="只取本页输入框里的地址">尚未测试</span></div>
            <div class="setrow__ctrl">
              <button class="btn" type="button" data-probe="local">测试连接</button>
              <button class="btn" type="button" data-rescan="1">重新检测</button>
            </div>
          </div>
        </div>
        <h4 class="prov__subtitle">本机模型
          <span class="muted">（同一时刻只有一个「使用中」；点模型名即切换）</span></h4>
        ${modelListHtml(g.models, true)}
        <!-- 本机档空态（K）：把后端给的 local_note **原样**显示出来（为什么空：连不上服务 /
             一个都没装 / 只找到向量模型…），并当场给出两条可走的路：重新检测、手动添加。 -->
        <div class="mrow-note" id="local-note">${esc(cat.local_note || "")}</div>
        <div class="prov__actions">
          <button class="btn" type="button" data-rescan="1">重新检测</button>
          <button class="btn" type="button" data-addlocal="1">＋ 手动添加本机模型</button>
        </div>
        <div class="medit" id="local-add" hidden>
          <div class="medit__title">＋ 手动添加本机模型</div>
          <div class="medit__row"><label for="la-name">显示名称</label>
            <input type="text" id="la-name" spellcheck="false" placeholder="留空 = 用模型名"></div>
          <div class="medit__row"><label for="la-model">模型名</label>
            <input type="text" id="la-model" spellcheck="false" placeholder="如 qwen3.5:27b"></div>
          <div class="medit__ops">
            <button class="btn btn--primary" type="button" data-lasave="1">添加</button>
            <button class="btn" type="button" data-lacancel="1">取消</button>
          </div>
          <div class="medit__hint" id="la-note">走 Ollama 的 OpenAI 兼容接口
            （<span class="mono" id="la-base">—</span>，密钥固定填 <span class="mono">ollama</span>）。</div>
        </div>`;
    } else {
      detail.innerHTML = provFormHtml(findGroup(sel), sel);
    }
    bindProvEvents();
    paintUsage();
    paintModelTrigger();
  }

  /** 展开本机档的「＋ 手动添加本机模型」面板并聚焦模型名输入框。
   *  左列的添加入口与右栏的空态按钮共用这一个出口，行为必须一致。 */
  function openLocalAddPanel() {
    const la = $("local-add");
    if (!la) return;
    la.hidden = false;
    const m = $("la-model");
    if (m) m.focus();
  }

  /** 左列事件：**事件委托**（`#prov-list` 容器本身从不被替换，只有 innerHTML 会变）。
   *  旧实现把 onclick 逐个绑到渲染出来的按钮上，有两个隐患：
   *  ① paintUsage() 会重绘左列，重绘后旧节点连同 handler 一起被摘掉（"用过一次就失效"）；
   *  ② `.prov__sub`（本机档摊开的模型子项）同时带 `data-t` 与 `data-use`，后绑的 `[data-t]`
   *     会把 `[data-use]` 的处理函数**覆盖**掉 → 点本机档的模型名"点了没反应"（切换不生效）。
   *  委托只认容器上的一个 onclick，优先级 data-use > data-t > data-addt，重绘多少次都有效。
   *  用 `box.onclick = ...`（赋值而非 addEventListener）：重复调用只是替换，不会叠加监听。 */
  function bindProvListEvents() {
    const box = $("prov-list");
    if (!box) return;
    box.onclick = (ev) => {
      const el = ev.target.closest("[data-use],[data-t],[data-addt],[data-addrelay]");
      if (!el || !box.contains(el)) return;
      // ⓪「＋ 添加公共中转（免 Key）」：一键接入评审/临时机器可用的公共中转站。
      //   中转站只在服务端持有模型 Key，客户端零 Key；模型有白名单与限流（见服务端说明）。
      if (el.dataset.addrelay) {
        (async () => {
          const res = await call("add_custom_model", "公共中转 · deepseek-v4.1-flash", "deepseek-v4.1-flash",
                                 "https://yindun-relay.pages.dev/api/ai", "",
                                 "wan", "公共中转", "chat_completions");
          if (!res || res.ok === false) {
            toast("添加中转站失败：" + ((res && res.error) || "未知原因"));
            return;
          }
          toast("已添加公共中转（免 Key）。点一下它即可切换使用");
          state.catalog = await call("model_catalog");
          state.prov = { tier: "wan", provider: "公共中转", builtin: false };
          renderProv(state.catalog);
        })();
        return;
      }
      // ①「＋ 添加…」：本机档 = 进本机档并当场展开手动添加面板；局域网 / 公网 = 新供应商草稿
      if (el.dataset.addt) {
        if (el.dataset.addt === "local") {
          state.prov = { tier: "local", provider: OLLAMA_PROVIDER, builtin: true };
          renderProv(state.catalog);
          openLocalAddPanel();
          return;
        }
        // 新供应商是"草稿"：存储粒度是"一条模型一行"，所以必须先有第一个模型才能落盘
        state.prov = { tier: el.dataset.addt, provider: "", builtin: false, isNew: true };
        renderProv(state.catalog);
        return;
      }
      // ② 本机档摊开的模型子项：点一下即切换（必须在 data-t 之前判断，子项两个属性都有）
      if (el.dataset.use) {
        ev.stopPropagation();
        state.prov = { tier: el.dataset.t, provider: el.dataset.prov, builtin: el.dataset.builtin === "1" };
        if (el.dataset.use !== activeModel()) persist({ model: el.dataset.use });
        else renderProv(state.catalog);
        return;
      }
      // ③ 点供应商条目 = "正在查看"它（背景高亮的唯一出口）
      if (el.dataset.t) {
        state.prov = { tier: el.dataset.t, provider: el.dataset.prov, builtin: el.dataset.builtin === "1" };
        renderProv(state.catalog);
      }
    };
  }

  function bindProvEvents() {
    const box = $("prov-list"), detail = $("prov-detail");
    if (!box || !detail) return;
    const val = (id) => (($(id) && $(id).value) || "").trim();
    const groupOf = () => (!state.prov || state.prov.isNew) ? null
      : providersOfTier(state.catalog, state.prov.tier).find(
          (g) => g.provider === state.prov.provider && !!g.builtin === !!state.prov.builtin) || null;

    /** 供应商级三件套的当前表单值（保存供应商 / 添加模型都用它，空密钥＝沿用已存）。 */
    const formValue = () => ({
      tier: state.prov.tier,
      provider: val("p-provider"),
      url: val("p-url"),
      key: val("p-key"),
      format: (($("p-format") && $("p-format").value) || "chat_completions"),
    });

    /** 改完配置的统一收口：重取清单 + 重渲染 + 同步输入区。
     *  顺带把内核可能改掉的当前模型读回来（删掉"正在用"的条目时内核会回退本地模型——
     *  界面必须如实反映，否则触发器还挂着已经不存在的模型名）。 */
    const afterChange = async () => {
      const info = await call("llm_status");
      if (info) {
        state.llm = info;
        if (info.model) state.settings.model = info.model;
      }
      renderProv(await call("model_catalog"));
      fillSettings();
    };

    // 左列：事件委托（容器 #prov-list 常驻，重绘不失效；见 bindProvListEvents 注释）
    bindProvListEvents();
    // 右栏模型列表：点模型名 = 切换（单选式"使用中"）；编辑/删除是显式动作
    detail.querySelectorAll(".mrow__use").forEach((el) => {
      el.onclick = () => { if (el.dataset.use !== activeModel()) persist({ model: el.dataset.use }); };
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
    // ── 手动添加本机模型（K）──
    // 为什么需要：自动探测依赖"Ollama 服务在跑 + 模型装在默认位置"，同学机器上两样都不成立时
    // 界面就成了死路（只能绕去填"局域网模型 localhost"）。这里允许直接填模型名，
    // 落到 custom_models 的 **tier=local**（与本机档同栏），地址用 `<ollama_host>/v1`、
    // 密钥固定 `ollama`——Ollama 的 /v1 就是 OpenAI 兼容端点、不做鉴权校验，
    // 这样手动条目与探测条目在下游走同一条客户端通路（不要改成别的值：换了就打不通）。
    const localAdd = $("local-add");
    if (localAdd) {
      const ollamaBase = () => {
        const host = String((state.settings && state.settings.ollama_host) || "http://127.0.0.1:11434")
          .trim().replace(/\/+$/, "");
        return /\/v1$/i.test(host) ? host : host + "/v1";
      };
      if ($("la-base")) $("la-base").textContent = ollamaBase();
      detail.querySelectorAll("[data-addlocal]").forEach((el) => {
        el.onclick = () => openLocalAddPanel();      // 与左列添加入口共用同一出口
      });
      detail.querySelectorAll("[data-lacancel]").forEach((el) => {
        el.onclick = () => { localAdd.hidden = true; };
      });
      detail.querySelectorAll("[data-lasave]").forEach((el) => {
        el.onclick = async () => {
          const note = $("la-note");
          const mid = val("la-model"), mname = val("la-name") || mid;
          if (!mid) {
            note.textContent = "模型名不能为空（本机已装可先用 `ollama list` 查看）";
            note.className = "medit__hint is-bad"; return;
          }
          const res = await call("add_custom_model", mname, mid, ollamaBase(), "ollama",
                                 "local", MANUAL_LOCAL_PROVIDER, "chat_completions", []);
          if (!res || !res.ok) {
            note.textContent = "添加失败：" + ((res && res.error) || "未知原因");
            note.className = "medit__hint is-bad"; return;
          }
          toast(`已添加本机模型「${mname}」（走 Ollama 的 OpenAI 兼容接口）`);
          state.prov = { tier: "local", provider: MANUAL_LOCAL_PROVIDER, builtin: false };
          renderProv(await call("model_catalog"));
          fillSettings();
        };
      });
    }
    detail.querySelectorAll("[data-probe]").forEach((el) => {
      el.onclick = async () => {
        const isLocal = el.dataset.probe === "local";
        const url = isLocal ? val("ollama-host") : val("p-url");
        const key = isLocal ? "" : val("p-key");
        // ★ 把**条目名**一起交给后端（V3.6.7 修）：密钥不回显，所以存过之后输入框是空的——
        //   后端据此回落到该条目已存的密钥去探，否则会拿空密钥撞 401，看起来像"密钥丢了"。
        //   供应商可能有多个模型，密钥在组内共享，取第一条的条目名即可。
        const g = groupOf();
        const entryName = (isLocal || !g) ? "" : String((g.models[0] || {}).name || "");
        const note = isLocal ? $("probe-local") : $("probe-detail");
        note.textContent = "正在测试连接…"; note.className = "";
        const res = await call("probe_model_endpoint", url, key, entryName);
        if (res && res.ok) {
          note.textContent = "✅ " + (res.note || "已连通")
            + (res.models && res.models.length ? "：" + res.models.slice(0, 3).join("、") : "");
          note.className = "is-ok";
          if (!isLocal) {
            // ★ 地址归一化回填（V3.6.7）：用户常把"完整端点"（…/chat/completions）当基础地址填，
            //   那样会拼出 …/chat/completions/chat/completions 而必然失败。探测已识别出基础地址，
            //   这里回填并说明——用户看得见"该填什么"，不用猜。
            const urlBox = $("p-url");
            const normalized = String(res.endpoint || "");
            if (urlBox && normalized && normalized !== String(urlBox.value || "").trim()) {
              urlBox.value = normalized;
              note.textContent += `（地址已按基础地址识别：${normalized}）`;
            }
            // 探测到的模型名回填到"添加模型"面板（仅在面板打开且模型 ID 还空着时，不越权改写）
            const mid = $("pm-model");
            if (mid && !$("pm-panel").hidden && !mid.value.trim() && res.models && res.models.length) {
              mid.value = res.models[0];
            }
          }
        } else {
          note.textContent = "❌ " + ((res && res.error) || "连不上");
          note.className = "is-bad";
        }
      };
    });
    detail.querySelectorAll("[data-save]").forEach((el) => {
      el.onclick = async () => {
        const note = $("probe-detail");
        const f = formValue();
        if (!f.provider || !f.url) {
          note.textContent = "显示名称与 Base URL 都要填"; note.className = "is-bad"; return;
        }
        let keptKey = false;
        if (state.prov.isNew) {
          const mid = val("pm-model"), mname = val("pm-name") || mid;
          if (!mid) {
            note.textContent = "先填第一个模型的 ID（供应商要挂至少一个模型才能保存）";
            note.className = "is-bad"; return;
          }
          const res = await call("add_custom_model", mname, mid, f.url, f.key, f.tier,
                                 f.provider, f.format, splitTags(val("pm-tags")));
          if (!res || !res.ok) {
            note.textContent = "保存失败：" + ((res && res.error) || "未知原因"); note.className = "is-bad"; return;
          }
          keptKey = !!res.kept_key;
          state.prov = { tier: res.tier || f.tier, provider: f.provider, builtin: false };
          toast(`已添加供应商「${f.provider}」（${res.tier_label || f.tier}）`
                + (keptKey ? "（密钥沿用已存）" : ""));
        } else {
          // 已有供应商：Base URL / API 格式 / 密钥 / 供应商名对**整组**生效（表单承诺的一致配置）
          const g = groupOf();
          if (!g) return;
          let bad = "";
          for (const m of g.models) {
            const res = await call("add_custom_model", m.name, m.model_id, f.url, f.key, f.tier,
                                   f.provider, f.format, m.tags || []);
            if (!res || !res.ok) { bad = (res && res.error) || "未知原因"; break; }
            keptKey = keptKey || !!res.kept_key;
          }
          if (bad) { note.textContent = "保存失败：" + bad; note.className = "is-bad"; return; }
          state.prov = { tier: f.tier, provider: f.provider, builtin: false };
          // 留空保存时后端会沿用已存密钥——把这件事说出来，用户才知道密钥没有丢
          toast(`已保存供应商「${f.provider}」` + (keptKey ? "（密钥沿用已存）" : ""));
        }
        await afterChange();
      };
    });
    detail.querySelectorAll("[data-del]").forEach((el) => {
      el.onclick = async () => {
        const res = await call("remove_custom_model", el.dataset.del);
        if (!res || !res.ok) { toast("删除失败：" + ((res && res.error) || "未知原因")); return; }
        toast("已删除模型");
        await afterChange();
      };
    });
    detail.querySelectorAll("[data-remove-provider]").forEach((el) => {
      el.onclick = async () => {
        const g = groupOf();
        if (!g || !g.models.length) return;
        const names = g.models.map((m) => m.name);
        // 删供应商会连带它的模型与已存密钥，不可逆 → 必须显式确认（原生 confirm 够用，
        // 不为此引入新组件；取消就什么都不做）
        if (!window.confirm(`删除供应商「${g.provider}」及其 ${names.length} 个模型？本机已存的密钥会一并删除。`)) return;
        for (const name of names) await call("remove_custom_model", name);
        toast("已删除供应商");
        state.prov = { tier: state.prov.tier, provider: "", builtin: false };
        await afterChange();
      };
    });
    // ── 模型编辑面板（添加 / 编辑）──
    const panel = $("pm-panel");
    const openPanel = (model) => {
      if (!panel) return;
      panel.hidden = false;
      panel.dataset.editing = model ? model.name : "";
      if ($("pm-title")) $("pm-title").textContent = model ? `编辑模型 · ${model.name}` : "添加模型";
      if ($("pm-model")) $("pm-model").value = model ? (model.model_id || "") : "";
      if ($("pm-name")) $("pm-name").value = model ? model.name : "";
      if ($("pm-tags")) $("pm-tags").value = model ? (model.tags || []).join("，") : "";
      if ($("pm-ctx")) $("pm-ctx").value = model && model.context_window ? model.context_window : "";
      const note = $("pm-note");
      if (note) { note.textContent = ""; note.className = "medit__hint"; }
      if ($("pm-model")) $("pm-model").focus();
    };
    detail.querySelectorAll("[data-addmodel]").forEach((el) => {
      el.onclick = () => openPanel(null);
    });
    detail.querySelectorAll("[data-edit]").forEach((el) => {
      el.onclick = () => {
        const g = groupOf();
        const m = g && g.models.find((x) => x.name === el.dataset.edit);
        if (m) openPanel(m);
      };
    });
    detail.querySelectorAll("[data-mcancel]").forEach((el) => {
      el.onclick = () => { if (panel) panel.hidden = true; };
    });
    detail.querySelectorAll("[data-msave]").forEach((el) => {
      el.onclick = async () => {
        const f = formValue(), g = groupOf();
        const mid = val("pm-model"), mname = val("pm-name") || mid;
        const note = $("pm-note");
        if (!mid) { note.textContent = "模型 ID 不能为空"; note.className = "medit__hint is-bad"; return; }
        if (!f.url) { note.textContent = "先把上面的 Base URL 填上"; note.className = "medit__hint is-bad"; return; }
        const editing = (panel && panel.dataset.editing) || "";
        const ctxWindow = Number(($("pm-ctx") && $("pm-ctx").value) || 0) || 0;
        const res = await call("add_custom_model", mname, mid, f.url, f.key, f.tier,
                               f.provider, f.format, splitTags(val("pm-tags")), ctxWindow);
        if (!res || !res.ok) {
          note.textContent = "保存失败：" + ((res && res.error) || "未知原因");
          note.className = "medit__hint is-bad"; return;
        }
        // 改了显示名称 = 换了存储键：把旧条目删掉，避免"改完名又多出一条"（存储以条目名为键）
        if (editing && editing !== mname) await call("remove_custom_model", editing);
        if (g) state.prov = { tier: f.tier, provider: f.provider || g.provider, builtin: false };
        toast(`已保存模型「${mname}」`);
        await afterChange();
      };
    });
    // ── API Key「查看」（眼睛）──
    // 现状：密钥加密存本机、界面不回显，输入框"留空＝不改动"。但没有**显式查看**的话，
    // 用户没法确认当初存进去的是什么（磁盘上是密文，配置文件里也翻不到）。
    // 所以提供这个显式动作，并要求服务层每次查看都写一条审计（access_control / WARNING）——
    // 文档里"界面不回显"的旧口径随之改为"默认不回显，点「查看」可显示且留痕"（文档由负责人统一改）。
    const eye = $("p-key-eye");
    if (eye) {
      eye.onclick = async () => {
        const box2 = $("p-key");
        if (box2.dataset.revealed === "1") {           // 再点一次 = 隐藏并回到"不改动"状态
          box2.value = ""; box2.dataset.revealed = "0";
          eye.textContent = "查看"; eye.title = "查看本机已存密钥（写入审计）";
          return;
        }
        const res = await call("custom_model_key", formValue().provider);
        if (!res || !res.ok) { toast((res && res.error) || "没有已存的密钥"); return; }
        box2.value = res.key || ""; box2.dataset.revealed = "1";
        eye.textContent = "隐藏"; eye.title = "隐藏并清空（回到「留空＝不改动」）";
        toast("已显示本机存储的密钥；这次查看已写入审计");
      };
    }
  }

  /** 输入区触发器：显示「供应商 / 当前模型」（ZCode 的形态），点开是两级弹层。
   *  ★ 诚实优先（K）：清单没拿到 → 「检查模型…」；配置的模型**不在可用清单里** →
   *    「请选择模型」（绝不把那个不存在的名字显示成"在用"）；都正常 → 「供应商 / 模型」。 */
  function paintModelTrigger() {
    const val = $("model-value"), dot = $("model-tier-dot");
    if (!val) return;
    if (!catalogReady()) {
      val.textContent = "检查模型…";
      if (dot) { dot.className = "tier-dot"; dot.title = "正在读取本机可用模型…"; }
      return;
    }
    const cur = activeModel();
    if (!cur) {
      val.textContent = "请选择模型";
      if (dot) {
        dot.className = "tier-dot";
        dot.title = "配置里的模型不在可用清单里（可能没装、或服务没起）——点这里选一个可用模型";
      }
      return;
    }
    const g = placementOf(cur);
    val.textContent = g ? `${g.provider} / ${cur}` : cur;
    if (!dot) return;
    dot.className = "tier-dot" + (g ? ` tier-dot--${g.tier}` : "");
    dot.title = g ? `${TIER_META[g.tier].label} · ${TIER_META[g.tier].boundary}` : "未在清单中（可能已手动指定）";
  }

  /* ── 安全工具（隐私健康体检 / 模型行为画像）────────────
     后端把两件事都整理成了结构化数据：health_scan 的 json 是 JSON 文本，
     behavior_profile 直接给 profile 对象 + anomalies 数组。界面按字段渲染，
     不再把 summary 的等宽文本贴出来——`======` 一类分隔线只属于原始报告，
     默认视图里出现就说明又退回了"贴文本"。 */
  const HEALTH_SEV = {
    "绝密": { cls: "critical", rank: 0 },
    "机密": { cls: "critical", rank: 1 },
    "内部": { cls: "warn", rank: 2 },
    "公开": { cls: "muted", rank: 3 },
  };
  const HEALTH_CAT = { sensitive_file: "敏感文件", sensitive_content: "敏感内容", permission: "权限异常" };
  const ANOMALY_TYPE = {
    high_frequency: "高频操作", sensitive_access: "敏感路径访问",
    path_hopping: "路径跳跃", off_hours: "非工作时间",
  };
  const ANOMALY_SEV = {
    critical: { label: "严重", cls: "critical", rank: 0 },
    high: { label: "高", cls: "critical", rank: 1 },
    medium: { label: "中", cls: "warn", rank: 2 },
    low: { label: "低", cls: "muted", rank: 3 },
  };
  const OP_LABEL = { read: "读取", write: "写入", delete: "删除", execute: "执行" };
  const TOOL_LABEL = {
    read_local_file: "读取文件", list_local_files: "列出文件", search_in_files: "搜索文件",
    analyze_project: "分析项目", create_local_file: "创建文件", modify_local_file: "修改文件",
    replace_in_file: "替换内容", delete_local_file: "删除文件", run_local_command: "执行命令",
  };

  /** 时间戳压到「MM-DD HH:MM」：后端给的是 ISO（带 T、带微秒），在右栏窄宽度里
   *  会把整行挤到换行；定位一次操作到分钟足够，秒/微秒只是噪声。 */
  function shortTime(value) {
    const text = String(value || "").replace("T", " ");
    const m = text.match(/([0-9]{4})-([0-9]{2})-([0-9]{2})[ ]([0-9]{2}):([0-9]{2})/);
    if (m) return `${m[2]}-${m[3]} ${m[4]}:${m[5]}`;
    const hm = text.match(/[ ]([0-9]{2}):([0-9]{2})/);
    return hm ? `${hm[1]}:${hm[2]}` : text;
  }

  const riskInfo = (score) => {
    const n = Number(score) || 0;
    if (n >= 60) return { label: "高风险", cls: "critical" };
    if (n >= 30) return { label: "中风险", cls: "warn" };
    return { label: "低风险", cls: "ok" };
  };

  function toolMetric(num, label, cls, sub) {
    return `<div class="tool-metric${cls ? " tool-metric--" + cls : ""}">
      <span class="tool-metric__num">${esc(num)}</span>
      <span class="tool-metric__label">${esc(label)}${sub ? `<i>${esc(sub)}</i>` : ""}</span>
    </div>`;
  }

  function rawReport(text) {
    if (!text) return "";
    return `<details class="tool-raw"><summary>查看原始报告</summary><pre>${esc(text)}</pre></details>`;
  }

  function healthFinding(f) {
    const sev = HEALTH_SEV[f.severity] ? f.severity : "公开";
    const file = String(f.file || "");
    const name = file.split(/[\\/]/).filter(Boolean).pop() || "—";
    const all = Array.isArray(f.matches) ? f.matches : [];
    const shown = all.slice(0, 3);
    const rest = all.length - shown.length;
    const line = (f.line === null || f.line === undefined || f.line === "")
      ? "" : `<span class="health-finding__line">第 ${esc(f.line)} 行</span>`;
    return `<li class="health-finding" data-severity="${esc(sev)}">
      <div class="health-finding__head">
        <span class="health-finding__name" title="${esc(file)}">${esc(name)}</span>
        <span class="health-finding__cat">${esc(HEALTH_CAT[f.category] || f.category || "其他")}</span>
        ${line}
      </div>
      <div class="health-finding__desc">${esc(f.description || "—")}</div>
      <div class="health-finding__path">${esc(file || "—")}</div>
      ${shown.length ? `<div class="health-finding__matches">${
        shown.map((m) => `<code>${esc(m)}</code>`).join("")
      }${rest > 0 ? `<span class="health-finding__more">+${rest}</span>` : ""}</div>` : ""}
    </li>`;
  }

  function renderHealthTool(res) {
    const box = $("tool-result");
    let report = null;
    try { report = JSON.parse(res.json || ""); } catch (err) { report = null; }
    if (!report || typeof report !== "object") {
      box.innerHTML = `<div class="tool-fail" role="alert"><b>体检结果解析失败</b>
        <span>${esc(res.error || "后端返回的报告不是合法 JSON")}</span></div>` + rawReport(res.summary || res.json || "");
      return;
    }
    const findings = Array.isArray(report.findings) ? report.findings : [];
    const risk = riskInfo(report.risk_score);
    const groups = Object.keys(HEALTH_SEV)
      .sort((a, b) => HEALTH_SEV[a].rank - HEALTH_SEV[b].rank)
      .map((sev) => {
        const items = findings.filter((f) => f.severity === sev);
        if (!items.length) return "";
        return `<div class="health-group health-group--${HEALTH_SEV[sev].cls}" data-severity="${esc(sev)}">
          <div class="health-group__head">
            <span class="sev-badge sev-badge--${HEALTH_SEV[sev].cls}">${esc(sev)}</span>
            <span class="health-group__count">${items.length} 项</span>
          </div>
          <ul class="health-list">${items.map(healthFinding).join("")}</ul>
        </div>`;
      }).join("");
    // 其余未登记分级（后端将来扩档）也如实展示，不能静默吞掉
    const known = new Set(Object.keys(HEALTH_SEV));
    const other = findings.filter((f) => !known.has(f.severity));
    const otherGroup = other.length ? `<div class="health-group health-group--muted" data-severity="其他">
      <div class="health-group__head"><span class="sev-badge sev-badge--muted">其他</span>
      <span class="health-group__count">${other.length} 项</span></div>
      <ul class="health-list">${other.map(healthFinding).join("")}</ul></div>` : "";
    const empty = findings.length ? "" : `<div class="tool-empty tool-empty--ok">
      <span class="tool-empty__mark">✓</span>
      <b>未发现隐私风险</b>
      <span>扫描 ${esc(report.scanned_files ?? 0)} 个文件，未见敏感文件 / 内容 / 权限异常</span>
    </div>`;
    box.innerHTML = `
      <div class="tool-metrics">
        ${toolMetric(report.scanned_files ?? 0, "扫描文件", "", `共 ${report.total_files ?? 0} 个`)}
        ${toolMetric(report.findings_count ?? findings.length, "发现问题", findings.length ? "bad" : "ok")}
        ${toolMetric(report.risk_score ?? 0, "风险评分", risk.cls, risk.label)}
      </div>
      <div class="tool-target" title="${esc(report.scan_root || "")}">扫描范围：${esc(report.scan_root || "—")}</div>
      ${empty || `<div class="health-groups">${groups}${otherGroup}</div>`}
      ${rawReport(res.summary)}`;
  }

  function renderBehaviorTool(res) {
    const box = $("tool-result");
    const p = res.profile || {};
    const anomalies = (Array.isArray(res.anomalies) ? res.anomalies : []).slice()
      .sort((a, b) => ((ANOMALY_SEV[a.severity] || {}).rank ?? 9) - ((ANOMALY_SEV[b.severity] || {}).rank ?? 9));
    const risk = riskInfo(p.risk_score);
    const tools = Object.entries(p.tool_call_breakdown || {})
      .map(([name, count]) => [name, Number(count) || 0])
      .filter(([, count]) => count > 0)
      .sort((a, b) => b[1] - a[1]);
    const maxCalls = Math.max(1, ...tools.map(([, count]) => count));
    const total = tools.reduce((sum, [, count]) => sum + count, 0) || Number(p.total_tool_calls) || 0;
    const ops = ["read", "write", "delete", "execute"]
      .map((key) => [key, Number((p.operation_breakdown || {})[key]) || 0]);
    const sensitive = Array.isArray(p.sensitive_paths_accessed) ? p.sensitive_paths_accessed : [];
    const range = Array.isArray(p.time_range) ? p.time_range : [];
    const toolBars = tools.length
      ? `<div class="hbars">${tools.map(([name, count]) => `
          <div class="hbar">
            <span class="hbar__name" title="${esc(name)}">${esc(TOOL_LABEL[name] || name)}</span>
            <span class="hbar__track"><i style="width:${Math.round((count / maxCalls) * 100)}%"></i></span>
            <span class="hbar__num">${count}${total ? `<em>${Math.round((count / total) * 100)}%</em>` : ""}</span>
          </div>`).join("")}</div>`
      : `<div class="tool-section__hint">最近记录里没有工具调用</div>`;
    const anomalyItems = anomalies.map((a) => {
      const sev = ANOMALY_SEV[a.severity] || { label: a.severity || "提示", cls: "muted" };
      return `<div class="anomaly anomaly--${sev.cls}" data-severity="${esc(a.severity || "")}">
        <span class="sev-badge sev-badge--${sev.cls}">${esc(sev.label)}</span>
        <div class="anomaly__body">
          <div class="anomaly__head">
            <b>${esc(ANOMALY_TYPE[a.anomaly_type] || a.anomaly_type || "异常")}</b>
            ${a.timestamp ? `<span class="anomaly__time" title="${esc(a.timestamp)}">${esc(shortTime(a.timestamp))}</span>` : ""}
          </div>
          <div class="anomaly__desc">${esc(a.description || "—")}</div>
        </div>
      </div>`;
    }).join("");
    const factors = Array.isArray(p.risk_factors) ? p.risk_factors.filter(Boolean) : [];
    box.innerHTML = `
      <div class="tool-metrics" data-count="4">
        ${toolMetric(p.total_tool_calls ?? 0, "工具调用")}
        ${toolMetric(p.unique_paths ?? 0, "唯一路径")}
        ${toolMetric(p.path_jumps ?? 0, "路径跳跃")}
        ${toolMetric(p.risk_score ?? 0, "风险评分", risk.cls, risk.label)}
      </div>
      <div class="tool-target" title="${esc((range[0] || "") + " ~ " + (range[1] || ""))}">
        ${esc(p.model_name || "模型未标记")} · ${esc(shortTime(range[0]) || "—")} ~ ${esc(shortTime(range[1]) || "—")}
      </div>
      <div class="tool-section">
        <div class="tool-section__title">工具调用分布 <span>共 ${esc(p.total_tool_calls ?? total)} 次</span></div>
        ${toolBars}
      </div>
      <div class="tool-section">
        <div class="tool-section__title">操作类型分布</div>
        ${ops.some(([, count]) => count)
          ? `<div class="tool-ops">${ops.filter(([, count]) => count).map(([key, count]) => `
          <span class="tool-op"><b>${count}</b>${OP_LABEL[key]}</span>`).join("")}</div>`
          : `<div class="tool-section__hint">本次窗口内没有读写操作</div>`}
      </div>
      <div class="tool-section">
        <div class="tool-section__title">被访问的敏感路径 <span>${sensitive.length} 个</span></div>
        ${sensitive.length
          ? `<ul class="tool-paths">${sensitive.map((path) => `<li title="${esc(path)}">${esc(path)}</li>`).join("")}</ul>`
          : `<div class="tool-section__hint">未访问敏感路径</div>`}
      </div>
      ${factors.length ? `<div class="tool-section">
        <div class="tool-section__title">风险因子</div>
        <ul class="tool-paths tool-paths--factors">${factors.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>
      </div>` : ""}
      <div class="tool-section">
        <div class="tool-section__title">异常检测 <span>${anomalies.length} 条</span></div>
        ${anomalies.length ? `<div class="anomaly-list">${anomalyItems}</div>` : `<div class="tool-empty tool-empty--ok">
          <span class="tool-empty__mark">✓</span>
          <b>未发现异常行为</b>
          <span>最近记录未命中高频 / 敏感路径 / 路径跳跃 / 非工作时间规则</span>
        </div>`}
      </div>
      ${rawReport(res.summary)}`;
  }

  async function runTool(which) {
    if (state.toolBusy) return;
    state.toolBusy = true;
    const box = $("tool-result");
    const buttons = [$("btn-health"), $("btn-behavior")].filter(Boolean);
    buttons.forEach((b) => { b.disabled = true; b.classList.add("is-busy"); });
    box.style.display = "";
    box.innerHTML = `<div class="tool-loading" role="status" aria-live="polite">
      <span class="tool-loading__mark"></span>${which === "health" ? "正在扫描敏感文件与内容…" : "正在分析审计日志…"}
    </div>`;
    try {
      const res = which === "health" ? await call("health_scan", "") : await call("behavior_profile");
      if (!res || !res.ok) {
        // error 是后端原文：排障时逐字可查，不要在前端改写或截断
        box.innerHTML = `<div class="tool-fail" role="alert"><b>执行失败</b>
          <span>${esc((res && res.error) || "未知原因")}</span></div>`;
        return;
      }
      if (which === "health") renderHealthTool(res); else renderBehaviorTool(res);
    } finally {
      state.toolBusy = false;
      buttons.forEach((b) => { b.disabled = false; b.classList.remove("is-busy"); });
    }
  }

  /* ── 工作流（界面入口已移除，引擎保留）───────────────────
     界面层（右栏模板面板 / 顶部入口按钮 / 步骤台账）按负责人要求整块砍掉：`state.wf*`、
     桥调用与面板容器一并不再存在。**引擎保留**：`core/` 的工作流模块、`agent_service`
     的工作流服务方法、`tests/test_workflow*.py` 都是内核能力与已验证资产，随时可以再接界面
     （接回时只需恢复面板 + 调用桥）。 */

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
            : `<div class="pcard__desc">启用后，左侧「插件」分组会出现它的专属设置。</div>`)
        : `<div class="pcard__desc">这个插件没有可调项：启用即生效。</div>`;
      // 运行状态：回答"启用之后它到底有没有在工作"——只有真正被调用过才算数
      const rt = p.runtime || {};
      let runText, runCls;
      if (!p.enabled) {
        runText = "未启用"; runCls = "is-idle";
      } else if (!rt.calls) {
        runText = "等待触发"; runCls = "is-idle";
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
          <div class="plugin-item__runtime ${runCls}" title="未触发不代表失效；出现相关场景时自动运行">运行状态：${runText}</div>
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
    laya_risk: { render: renderLayaSettings, subtitle: "审批弹窗的风险分级来源：本机 / 局域网 / 正则" },
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
  let advisoryLoadingTimer = null;
  function setAdvisoryLoading(on) {
    const wrap = $("approval-advisory-wrap");
    const box = $("approval-advisory-loading");
    if (!wrap || !box) return;
    clearTimeout(advisoryLoadingTimer);
    if (!on) { box.style.display = "none"; return; }
    $("approval-advisory").innerHTML = "";
    wrap.style.display = "";
    box.style.display = "";
    $("approval-advisory-loading-text").textContent =
      "正在研判：请求用途 → 是否相符 → 操作风险…";
    // 研判较久时不取消占位（否则又抖一次），只改文案：人可以先用上面的
    // 目标路径/参数自行决定，建议晚到会继续补上。
    advisoryLoadingTimer = setTimeout(() => {
      const text = $("approval-advisory-loading-text");
      if (text && $("approval-advisory-loading").style.display !== "none") {
        text.textContent = "仍在研判…你可以先按自己的判断决定（建议结果会继续补上）";
      }
    }, 8000);
  }

  function renderAdvisories(list) {
    const wrap = $("approval-advisory-wrap");
    const items = Array.isArray(list) ? list : [];
    if (!items.length) { wrap.style.display = "none"; return; }
    clearTimeout(advisoryLoadingTimer);
    $("approval-advisory-loading").style.display = "none";
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
    workflow: async () => { await renderWorkflowTemplates(); wfPoll(true); },
  };

  /* ── 工作流面板（V3.6.21 恢复入口）─────────────────────────────
     只暴露引擎真实有的能力：模板列表 / 启动 / 下一步 / 步骤审批 / 导出。
     不提供分支、回退、单步重跑——引擎不支持，界面不画这种按钮。 */
  const wfState = { instanceId: null, status: null, timer: null };

  function wfStatusLabel(s) {
    return ({
      pending: "待执行", running: "执行中", completed: "已完成",
      waiting_approval: "待审批", approved: "已批准", rejected: "已驳回",
      failed: "失败", skipped: "跳过",
    })[s] || String(s || "—");
  }

  function wfStatusClass(s) {
    if (s === "completed") return "wf-step__status--done";
    if (s === "waiting_approval") return "wf-step__status--waiting";
    if (s === "failed" || s === "rejected") return "wf-step__status--failed";
    return "";
  }

  async function renderWorkflowTemplates() {
    const box = $("wf-templates");
    if (!box) return;
    const list = await call("workflow_templates");
    const tpls = Array.isArray(list) ? list : [];
    box.innerHTML = tpls.length
      ? tpls.map((t) => `
        <div class="wf-card">
          <div class="wf-card__name">${esc(t.name || t.id)}</div>
          <div class="wf-card__desc">${esc(t.description || "")}</div>
          <div class="wf-card__foot">
            <span class="wf-card__meta">${Number(t.steps) || 0} 步 · ${t.source === "plugin" ? "插件提供" : "内核内置"}</span>
            <button class="btn btn--primary" data-wf-start="${esc(t.id)}" type="button">启动</button>
          </div>
        </div>`).join("")
      : `<div class="wf-card__desc">没有可用的工作流模板（内核模板缺失或插件未启用）。</div>`;
  }

  function wfRender() {
    const box = $("wf-instance");
    if (!box) return;
    const st = wfState.status;
    if (!st || !wfState.instanceId) { box.hidden = true; box.innerHTML = ""; return; }
    const steps = st.steps || [];
    const done = steps.filter((s) => s.status === "completed" || s.status === "skipped").length;
    const waiting = steps.find((s) => s.status === "waiting_approval");
    const pending = steps.find((s) => s.status === "pending" || s.status === "approved");
    const finished = !!st.is_complete;
    const failed = !!st.has_failed;
    box.hidden = false;
    box.innerHTML = `
      <div class="wf-instance__head">
        <span class="wf-instance__name">${esc(st.template_name || "工作流实例")}</span>
        <span class="wf-instance__progress">${done}/${steps.length}${finished ? " · 已完成" : failed ? " · 失败" : ""}</span>
      </div>
      <div class="wf-steps">
        ${steps.map((s) => `<div class="wf-step">
          <span class="wf-step__name">${esc(s.name)}</span>
          <span class="wf-step__status ${wfStatusClass(s.status)}">${wfStatusLabel(s.status)}</span>
        </div>`).join("")}
      </div>
      <div class="wf-actions">
        ${waiting ? `<button class="btn btn--primary" data-wf-approve="1" type="button">通过：${esc(waiting.name)}</button>
                     <button class="btn btn--danger" data-wf-reject="1" type="button">驳回</button>` : ""}
        ${!waiting && pending && !finished ? `<button class="btn" data-wf-next="1" type="button">执行下一步：${esc(pending.name)}</button>` : ""}
        ${done > 0 ? `<button class="btn" data-wf-export="1" type="button">导出执行记录</button>` : ""}
      </div>`;
  }

  async function wfRefresh() {
    if (!wfState.instanceId) return;
    const st = await call("workflow_status", wfState.instanceId);
    if (st && !st.error) { wfState.status = st; wfRender(); }
  }

  function wfPoll(immediate) {
    if (wfState.timer) { clearInterval(wfState.timer); wfState.timer = null; }
    if (immediate) wfRefresh();
    // 面板打开期间每 2 秒刷新一次；实例完成后停表，避免空转
    wfState.timer = setInterval(() => {
      if (!wfState.instanceId) return;
      if (state.pane !== "workflow") { clearInterval(wfState.timer); wfState.timer = null; return; }
      if (wfState.status && (wfState.status.is_complete || wfState.status.has_failed)) return;
      wfRefresh();
    }, 2000);
  }

  async function wfStart(templateId) {
    const path = ($("wf-path") && $("wf-path").value.trim()) || "";
    const res = await call("workflow_start", templateId, path);
    if (!res || res.ok === false || !res.instance_id) {
      toast("启动失败：" + ((res && res.error) || "未知原因"));
      return;
    }
    wfState.instanceId = res.instance_id;
    wfState.status = res.status || null;
    wfRender();
    wfPoll(false);
    wfRefresh();
  }

  async function wfNext() {
    if (!wfState.instanceId) return;
    const res = await call("workflow_execute", wfState.instanceId);
    if (res && res.ok === false) toast("无法继续：" + (res.error || "未知原因"));
    wfRefresh();
  }

  async function wfApprove(approved) {
    if (!wfState.instanceId || !wfState.status) return;
    const waiting = (wfState.status.steps || []).find((s) => s.status === "waiting_approval");
    if (!waiting) return;
    const res = await call("workflow_approve", wfState.instanceId, waiting.step_id, !!approved);
    if (res && res.ok === false) toast("审批失败：" + (res.error || "未知原因"));
    wfRefresh();
  }

  async function wfExport() {
    if (!wfState.instanceId) return;
    const res = await call("workflow_export", wfState.instanceId, "md");
    toast(res && res.ok ? `已导出：${res.path}` : `导出失败：${(res && res.error) || "未知原因"}`);
  }

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
    if (isCollapsed) stopAuditLive();
    else if (state.pane === "audit") startAuditLive();
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
    if (name === "audit") startAuditLive(); else stopAuditLive();
  }

  /* ── 设置视图（独立视图：左设置导航 + 右内容）────────────
     为什么独立成视图而不放右侧窄面板：设置项是"左标题说明 / 右控件"的行式布局，
     300~600px 的窄栏放不下，只能挤成竖排表单——那正是上一版难看的根因。 */
  const SET_META = {
    model: ["模型接入", "按数据边界分三档：本机 / 局域网 / 公网"],
    control: ["企业控制面", "接入公司控制面：只上传掩码事件，只接受收紧策略"],
    safety: ["隐私与权限", "脱敏网关、AI 权限四档与执行严格模式"],
    kb: ["本地知识库", "入库即脱敏的本地检索（脱敏 RAG）"],
    plugin: ["插件", "启用 / 停用插件；带设置的插件启用后出现在左侧栏"],
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
    }
  }

  /** 拉取审计导出格式（内核自带 + 已启用插件贡献的），供面板动作区生成菜单 */
  async function refreshExportFormats() {
    try {
      state.auditFormats = await call("audit_export_formats");
    } catch (err) {
      state.auditFormats = state.auditFormats || [];
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

  /* ── 窗口控制（V3.6.8：置顶 / 最小化 / 最大化 / 关闭）────────
     状态一律以 `window_state()`（原生窗口的真实状态）为准，**不本地记账翻转**：
     用户用系统快捷键或窗口被别的方式改过状态时，本地记账会立刻与事实不符。
     图标：最大化 = 方框，还原 = 两个错位方框（两枚 SVG 由 body.is-maximized 切换显隐）。 */
  async function paintWindowState(snapshot) {
    const st = snapshot || await call("window_state");
    if (!st) return;
    state.settings.topmost = !!st.topmost;
    const pin = $("btn-pin"), max = $("btn-max");
    if (pin) {
      pin.classList.toggle("is-active", !!st.topmost);
      pin.title = st.topmost ? "取消置顶" : "置顶";
      pin.setAttribute("aria-pressed", st.topmost ? "true" : "false");
    }
    state.windowMini = !!st.mini;
    if (max) {
      // 折叠态（闪发浮条）下窗口几何已经被改过：最大化会卡在"看起来最大、其实是浮条"
      // 的半个状态里，所以这里**禁用并如实提示**（服务端也会拒绝，见 window_action）。
      max.disabled = !!st.mini;
      max.classList.toggle("is-active", !!st.maximized);
      max.setAttribute("aria-pressed", st.maximized ? "true" : "false");
      max.title = st.mini ? "先退出闪发模式再最大化" : (st.maximized ? "还原窗口" : "最大化");
      document.body.classList.toggle("is-maximized", !!st.maximized);
    }
    const sw = $("sw-topmost");
    if (sw) sw.checked = !!st.topmost;
  }

  /** 最大化/还原失败时按后端 `last_window_error()` 的真实原因分档提示。
   *  旧实现固定说「先退出闪发模式」，但失败也可能是"本窗口不支持最大化"或"还原失败"。
   *  桥方法缺失 / 返回 null / 调用异常一律回落通用文案——提示可以不够具体，绝不能因此抛错。 */
  async function windowActionFailureText() {
    let reason = "";
    try {
      const bridge = api();
      // 先确认方法存在：call() 在方法缺失时会额外 toast「桥接未就绪」，这里不需要那条噪音。
      if (bridge && typeof bridge.last_window_error === "function") {
        const value = await call("last_window_error");
        reason = typeof value === "string" ? value : "";
      }
    } catch (_) { reason = ""; }
    if (reason === "mini") return "先退出闪发模式，再最大化窗口";
    if (reason === "unsupported") return "当前窗口环境不支持最大化";
    if (reason === "restore_failed") return "还原失败，请重试或重启窗口";
    return "窗口操作失败";
  }

  function bindWindowControls() {
    // 无边框悬浮是产品形态（不是设置项）：固定打开，让拖边缘热区与顶栏拖动始终可用，
    // 也就没有"改了要重启"这一说。
    document.documentElement.dataset.frameless = "1";
    placeWinCtrls();
    paintWindowState();                 // 只画状态，不改变窗口
    document.querySelectorAll("[data-win]").forEach((btn) => {
      btn.onclick = async () => {
        // ★ 防连点（V3.6.12，组员评审）：pywebview 的每次桥调用都会新开线程，
        //   连点会并发写 WinForms 窗口属性，原生窗口可能假死。这里在**第一个 await 之前**
        //   同步禁用本按钮（disabled 不再派发 click），busy 标记再兜一层——
        //   因为 paintWindowState() 重绘时可能按真实状态把 disabled 改回来。
        if (btn.dataset.busy === "1") return;
        btn.dataset.busy = "1";
        btn.disabled = true;
        const action = btn.dataset.win;
        try {
          const ok = await call("window_action", action);
          if (action === "toggle_top") {
            if (ok === false) { toast("置顶切换失败"); return; }
            if (!ok) return;                 // null = call() 已经提示过（超时 / 桥缺失 / 异常）
            await paintWindowState();        // 用真实状态刷新高亮（而不是本地取反）
            toast(state.settings.topmost ? "已置顶显示" : "已取消置顶");
          } else if (action === "toggle_maximize") {
            if (ok === false) {
              // 失败原因以后端为准（mini / unsupported / restore_failed / 空）；旧文案只覆盖第一种。
              toast(await windowActionFailureText());
              await paintWindowState();
              return;
            }
            if (!ok) return;                 // 同上：别在失败后假装图标已切换
            await paintWindowState();        // 图标与 is-active 跟着原生状态走
          }
        } finally {
          btn.dataset.busy = "";
          // 请求返回后恢复；但折叠态下最大化的禁用是**语义性**的，必须保持。
          btn.disabled = action === "toggle_maximize" && !!state.windowMini;
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
    const pending = Number(st.pending_events || 0);
    const lastSync = st.last_report_at ? st.last_report_at.slice(11, 19) : "从未";
    const state = {
      // P0-01：不再笼统写"上报中"，而是把"还有多少没同步、上次同步几时"摆出来。
      text: st.configured
        ? (st.enabled
            ? `已连接 · ${pending ? `待同步 ${pending} 条` : "已同步"} · 上次同步 ${lastSync}`
            : `已接入（未上报） · ${st.endpoint_id} · ${st.url}`)
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
             hint: "本机已安装 laya；首次加载可能需要联网下载权重。" },
    lan: { label: "局域网 laya 服务", boundary: "数据出这台电脑，但不出企业内网",
           hint: "内网部署的 laya 服务，需提供 OpenAI 兼容接口。" },
    regex: { label: "正则规则（回退）", boundary: "全离线，不依赖任何模型",
             hint: "没有 laya 时使用；每行一条「级别|正则|说明」，只产生提示、不参与放行判定。" },
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
      <p class="setfoot">laya 只在审批弹窗里补一行风险分级（高 / 中 / 低危），<b>不参与任何放行判定</b>。
        三级来源按数据边界分档：本机模型（数据不出本机）→ 局域网服务 → 本机正则规则。</p>`;
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
        <div class="laya__probe"><span class="laya__result" id="laya-result">${info.available ? "本机 laya 可用，审批弹窗将用模型打分" : "本机未检测到 laya，将用下一条可用来源"}</span></div>`;
    }
    const info = cur.lan || {};
    const cfg = cur.config || {};
    return head + `
      <div class="setcard">
        <div class="setrow"><div class="setrow__text"><b>服务地址</b><span>内网 laya 服务地址</span></div>
          <div class="setrow__ctrl"><input type="text" id="laya-endpoint" value="${esc(cfg.endpoint || "")}" placeholder="http://10.0.0.8:8080/v1" spellcheck="false"></div>
        </div>
        <div class="setrow"><div class="setrow__text"><b>模型名</b><span>分级模型名（可留空，由服务端默认）</span></div>
          <div class="setrow__ctrl"><input type="text" id="laya-model" value="${esc(cfg.model || "")}" placeholder="laya" spellcheck="false"></div>
        </div>
        <div class="setrow"><div class="setrow__text"><b>连通性</b><span>${esc(info.detail || "尚未测试")}</span></div>
          <div class="setrow__ctrl">
            <button class="btn" data-laya="probe-lan" type="button">测试连接</button>
            <button class="btn" data-laya="try-lan" type="button">试打分</button>
            <button class="btn btn--primary" data-laya="save-lan" type="button">保存</button>
          </div>
        </div>
      </div>
      <div class="laya__probe"><span class="laya__result" id="laya-result"></span></div>
      <p class="setfoot">「试打分」会真发一次分级请求并回显结果。接口为 OpenAI 兼容的 <code>/chat/completions</code>；
        解析不出时如实报错并回退正则。</p>`;
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
  /* ── 思考强度（照 ZCode：模型卡片底部的四档滑杆）─────────────
     触发器只显示档位词；真正的控制在模型卡片里（滑杆 + Ctrl+T 循环）。
     语义不变：四档仍映射到同一份 thinking_depth。 */
  function paintThinkLevel(depth) {
    const box = $("think-select");
    if (!box) return;
    const cur = levelOf(depth);
    const idx = Math.max(0, THINK_LEVELS.findIndex((lv) => lv.key === cur.key));
    if ($("think-value")) $("think-value").textContent = cur.label;
    const trg = $("think-trigger");
    // 轮次口径统一（BUG-0009）：thinking_depth 是"推理轮次档"，后端实际用
    // `max_rounds = think_depth × 2`，所以界面上说「最多 2N 轮」，不写「约 N 轮」。
    if (trg) trg.title = `思考强度：${cur.label}（最多 ${cur.depth * 2} 轮推理）—— ${cur.hint}（Ctrl+T 循环）`;
    const slider = $("think-slider");
    if (slider) {
      slider.value = String(idx);
      slider.classList.toggle("is-max", idx === THINK_LEVELS.length - 1);
      slider.style.setProperty("--pct", `${(idx / (THINK_LEVELS.length - 1)) * 100}%`);
      slider.title = `思考强度：${cur.label}（最多 ${cur.depth * 2} 轮推理）—— ${cur.hint}`;
    }
    if ($("mpick-think-label")) $("mpick-think-label").textContent = cur.label;
    box.setAttribute("data-level", cur.key);
  }

  /** 点思考档位 = 打开模型卡片并聚焦滑杆（ZCode 里思考强度就长在模型卡片底部）。 */
  function openThinkPanel() {
    const pop = $("model-pop");
    if (!pop) return;
    if (pop.hidden) {
      // 交给模型选择器自己的打开逻辑（会在下一段绑定里定义）
      if (typeof window.__openModelPop === "function") window.__openModelPop();
      else { pop.hidden = false; }
    }
    // ★ 卡片可能是通过这里（而不是模型触发器）打开的：aria-expanded 必须同步，
    //   否则辅助技术与回归用例都读不到"卡片已展开"。
    const trg = $("think-trigger");
    if (trg) trg.setAttribute("aria-expanded", "true");
    const slider = $("think-slider");
    if (slider) { try { slider.focus(); } catch (_) {} }
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
    paintSandbox();                     // 可读写范围（沙箱目录）当前的生效路径与说明
    paintThinkLevel(s.thinking_depth);
    paintPermModes(s.permission);
    // 输入区胶囊显示"本对话生效档位"：后端在 bootstrap/state 里给出 session_permission，
    // 拿不到时按"跟随默认"显示（新会话的初始状态）。
    paintPermPill(state.sessionPermission);

    applyTheme(!!s.dark_mode);   // ★ 明暗切换带过渡（首次加载不动画，见 applyTheme）
    // 注意：无边框模式的窗口控制按钮必须在"设置已加载后"决定是否显示，
    // 因此绑定放在这里（fillSettings 由 bootstrap / 保存设置 / 打开设置页触发），
    // 而不是 DOMContentLoaded —— 那时 settings 还是空的。
    bindWindowControls();
  }

  /** 应用明暗主题。**切换时做页级交叉淡入淡出**，而不是"啪一下"闪一下（负责人反馈"太闪了"）。

  为什么用 View Transitions：主题是 CSS 变量整体切换，逐元素加 transition 既贵又容易和
  hover/动画打架；页级交叉淡入淡出只需一行 API，且是浏览器原生合成（不掉帧）。
  边界：① 首次加载（还没画出来）**不动画**，只在用户主动切换时过渡；
        ② 用户开了"减少动态效果"（prefers-reduced-motion）直接切，不做动画；
        ③ WebView2 老版本没有这个 API → 直接切（功能不受影响）。 */
  let _themeApplied = false;
  // 主题连点的合并状态（见 toggleTheme）
  let _themeBurst = false;      // 本轮连点是否已经起过过渡
  let _themeIntent = null;      // 本轮目标主题（不读过渡进行中的 dataset，避免翻转错位）
  let _themeTimer = null;

  /** 把当前**意图**落到 DOM 上。做成函数而不是捕获具体值：View Transition 的
   *  update 回调是异步执行的，晚到时也必须画"最后一次点击"的目标态，不能把后点的覆盖回去。 */
  function paintThemeNow() {
    const dark = _themeIntent !== null
      ? _themeIntent
      : document.documentElement.dataset.theme === "dark";
    document.documentElement.dataset.theme = dark ? "dark" : "light";
  }

  /** 切换明暗：点下**立刻**切换视觉（不能有可感知延迟），但同一轮连点：
   *  ① 只起一次 View Transition（重叠的过渡会互相打断，观感抖动）；
   *  ② save_settings 用 220ms 防抖合并，只落最终态一次（避免多次磁盘写）。
   *  prefers-reduced-motion / 老 WebView2 无 startViewTransition 的降级与 applyTheme 一致。 */
  function toggleTheme() {
    const current = _themeIntent !== null
      ? _themeIntent
      : document.documentElement.dataset.theme === "dark";
    const dark = !current;
    _themeIntent = dark;
    state.settings.dark_mode = dark;
    if (!_themeBurst) {
      _themeBurst = true;
      const reduce = !!(window.matchMedia
        && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
      if (!_themeApplied || reduce || typeof document.startViewTransition !== "function") {
        paintThemeNow();
      } else {
        try {
          document.startViewTransition(paintThemeNow);
        } catch (err) {
          paintThemeNow();     // 过渡被拒（例：已有过渡在跑）→ 至少把主题切成对的
        }
      }
      _themeApplied = true;
    } else {
      // 连点窗口内：直接落最终态，不叠加第二个过渡
      paintThemeNow();
    }
    if (_themeTimer) clearTimeout(_themeTimer);
    _themeTimer = setTimeout(() => {
      _themeTimer = null;
      _themeBurst = false;
      _themeIntent = null;
      // 一轮连点只写一次盘，值取最终态
      call("save_settings", { dark_mode: document.documentElement.dataset.theme === "dark" });
    }, 220);
  }

  function applyTheme(dark, animate = true) {
    const html = document.documentElement;
    const next = dark ? "dark" : "light";
    const run = () => { html.dataset.theme = next; };
    const reduce = !!(window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    if (!animate || !_themeApplied || reduce || typeof document.startViewTransition !== "function") {
      run();
      _themeApplied = true;
      return;
    }
    try {
      document.startViewTransition(run);
    } catch (err) {
      run();          // 过渡被拒（例：已有过渡在跑）→ 至少把主题切成对的
    }
    _themeApplied = true;
  }

  async function persist(patch) {
    Object.assign(state.settings, patch);
    fillSettings();
    await call("save_settings", patch);
    // ★ 切换模型后"使用中"标记必须当场变（V3.6.7 修"点了没反应"）：旧实现整页重渲染
    //   模型接入页，会把用户填了一半的表单一起吞掉；V3.6.8 改成**标记级重绘**
    //   （paintUsage 更新绿点、paintModelTrigger 更新输入区触发器），语义一致且无副作用。
    if ("model" in patch) {
      // ★ 先就地更新"当前模型"的可用性判定（K）：刚在清单里点选的模型当然可用，
      //   否则切换的一瞬间 current_available 还是旧值，触发器会闪回「请选择模型」。
      if (state.catalog && state.catalog.tiers) {
        state.catalog.current = String(patch.model || "");
        state.catalog.current_available = !!placementOf(patch.model);
      }
      paintUsage(); paintModelTrigger();
      // 再向后台要一次**权威**清单（换模型会重建算力，本机档/可用性可能变化）；
      // 拿到后重绘一次，界面既不猜也不需要用户手动刷新。
      call("model_catalog").then((cat) => {
        if (cat && cat.tiers) { state.catalog = cat; paintUsage(); paintModelTrigger(); }
      });
    }
  }

  /* 顶栏「快速/思考」：视觉立即切换，落库防抖合并。
     为什么单独一条通道：persist() 会 fillSettings + 主题过渡 + 写配置，适合低频开关；
     快速来回点模式时，这些重活会排成队，按钮看起来像卡住。 */
  let _modeSaveTimer = null;
  let _modeSavePending = "";
  let _modeSaveBusy = false;

  function persistThinkMode(mode) {
    state.settings.think_mode = mode;
    _modeSavePending = mode;
    if (_modeSaveTimer) clearTimeout(_modeSaveTimer);
    _modeSaveTimer = setTimeout(flushThinkMode, 120);
  }

  async function flushThinkMode() {
    _modeSaveTimer = null;
    if (_modeSaveBusy) { _modeSaveTimer = setTimeout(flushThinkMode, 80); return; }
    const mode = _modeSavePending;
    if (!mode) return;
    _modeSavePending = "";
    _modeSaveBusy = true;
    try {
      const res = await call("save_settings", { think_mode: mode });
      if (res === false) toast("模式保存失败，请重试");
    } finally {
      _modeSaveBusy = false;
      if (_modeSavePending) flushThinkMode();
    }
  }

  async function ensureThinkModeSaved() {
    if (_modeSaveTimer) { clearTimeout(_modeSaveTimer); _modeSaveTimer = null; }
    while (_modeSaveBusy) await new Promise((r) => setTimeout(r, 20));
    if (_modeSavePending) await flushThinkMode();
  }

  /* ── 事件：来自 Python ───────────────────────────── */
  const onEvent = (event, payload) => {
    switch (event) {
      case "status": {
        // 显示层统一改写轮次前缀（BUG-0009）：状态行 / 浮条 / 工具行都看到「推理 N/M」，
        // 与顶栏的「快速 / 思考」模式名不再混用同一个词。
        const text = displayStatusText(String(payload || "").trim());
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
          // 空白中间结果（流式首块还没到）不摘等待指示：否则气泡会先空一下，
          // 又一次变成"没有任何内容"的观感
          const chunk = String(payload == null ? "" : payload);
          if (!chunk.trim()) break;
          const row = ensureStreamBubble();
          clearWaiting(row);                 // 升级成流式正文：同一个条目，不是再开一条
          const body = row.querySelector(".entry__body");
          body.innerHTML = md(chunk);
          scrollToEnd();
        }
        break;
      case "finished": {
        // ★ 用**本轮开始时的模式快照**（没有快照时退回当前设置）：标签必须反映这一轮
        //   实际用的模式，而不是此刻的设置值（组员评审 BUG-0006）。
        const modeNow = state.turnMode || state.settings.think_mode || "快速回答";
        const tag = modeNow.includes("深度") ? "思考" : "快速";
        finalizeBubble(payload || "", tag); setStatus(""); break;
      }
      case "error":
        if (state.streamBubble) finalizeBubble(payload || "出错了", "错误");
        // 没有流式气泡时也要落到画布上（entryEl：原先这里调的是一个不存在的 bubbleEl，
        // 一旦走到这条分支就是 JS ReferenceError，错误提示会被整个吞掉）
        else {
          const chat = $("chat");
          chat.appendChild(entryEl(md("⚠︎ " + (payload || "出错了")), "", chat.querySelectorAll(".entry").length + 1));
          scrollToEnd();
        }
        setStatus(""); break;
      case "need_confirm": showApproval(payload || {}); break;
      case "attachments": state.attachments = Array.isArray(payload) ? payload : []; renderChips(); break;
      case "trace":
        // 结构化过程轨迹：思考耗时 / 工具动作 / 关键判定（隐私网关、laya 风险等）。
        // 没有流式气泡时也要有可见落点——落到当前等待气泡里（appendTrace 会兜底现建），
        // 旧实现直接丢帧，"它在想还是在卡住"就完全看不出来。
        if (state.busy) appendTrace(payload || {});
        break;
      case "advisories": renderAdvisories(payload); break;
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
          // 本轮已结束：还开着的气泡说明终止事件没送到，按空回复口径收口
          // （避免"永远转圈"与"下一轮写进上一条气泡"两种症状）
          closeStaleBubble();
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
      case "window_state":
        // 原生最大化/还原/置顶事件驱动的状态推送：图标与高亮必须跟着真实窗口走
        paintWindowState(payload || null);
        break;
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
    // ★ V3.6.21：打包版首启提示「演示版本 / 内置自制中转站」（只弹一次）
    if (data.demo_notice) {
      toast(data.demo_notice, 9000);
      call("mark_demo_notice_seen");
    }
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
    // 审计快照走与审计页同一个渲染入口（BUG-0011）：bootstrap 返回的失败结果也要触发
    // 顶部横幅与"自动展开一次"，不能等到用户自己点开审计页才发现。
    // ★ 启动快照必须与审计页同一个条数（300）：旧实现只取 1 条，自动展开走的
    //   activateAuditPane() 又不会触发 PANE_LOADERS，于是右栏永远只有 1 条记录。
    call("audit_snapshot", 300).then((snap) => { if (snap) renderAudit(snap); });
    // 算力清单也在启动时预热一次：输入区触发器要显示「供应商 / 模型」，模型选择器也要按
    // 供应商分组——都不该等到"先打开一次设置页"才有数据（否则首屏触发器只有一个裸模型名）。
    call("model_catalog").then((cat) => {
      if (cat && cat.tiers) { state.catalog = cat; if (!state.prov) renderProv(cat); else paintModelTrigger(); }
    });
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
      // Enter 发送；Shift+Enter / Ctrl+Enter 换行（报告 P1-06：旧实现 Ctrl+Enter 会发送）
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) {
        e.preventDefault(); insertNewline($("mini-input")); return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.isComposing) {
        e.preventDefault(); miniSubmit();
      }
      if (e.key === "Escape") { e.preventDefault(); setMini(false); }
    });
    $("mini-input").addEventListener("input", (e) => autosizeInput(e.target));
    document.addEventListener("keydown", (e) => {
      // 全局 Esc：折叠态下展开（浮条只有一个输入框，不会与其它 Esc 语义冲突）
      if (e.key === "Escape" && miniOn()) { e.preventDefault(); setMini(false); }
    });

    $("input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing) {
        e.preventDefault(); insertNewline($("input")); return;
      }
      if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.isComposing) {
        e.preventDefault(); $("composer").requestSubmit();
      }
    });
    $("input").addEventListener("input", (e) => autosizeInput(e.target));

    $("btn-new-session").onclick = newSession;
    $("btn-kb-add").onclick = async () => {
      const addBtn = $("btn-kb-add");
      if (addBtn.disabled) return;
      addBtn.disabled = true;            // 一次入库没结束前不再发第二次（与窗口按钮同理）
      paintKbNotice("", "");             // 下一次入库开始：清掉上一条说明
      toast("正在入库（本地解析 + 逐块脱敏 + 向量化）…", 8000);
      const res = await call("kb_pick_and_add");
      if (!res) {
        // 桥接缺失/调用抛错：call() 已提示，这里只把可持久的结果说明补上
        paintKbNotice("入库失败：没有拿到入库结果（桥接调用失败）", "bad");
        await refreshKb();
        return;
      }
      const result = res.result || {};
      const total = Number(result.total != null ? result.total : (result.details || []).length);
      const failed = Number(res.failed != null ? res.failed : result.failed || 0);
      const success = Number(result.success != null ? result.success : Math.max(0, total - failed));
      if (res.ok === false) {
        // ★ 全部失败 / 前置条件不满足：如实说原因，绝不再弹「入库完成」
        const reason = res.error || res.summary || result.summary || "未知原因";
        toast("入库失败：" + reason, 6000);
        paintKbNotice("入库失败：" + reason, "bad");
      } else if (res.partial) {
        // 部分成功：成功/总数 + 失败文件与原因（最多列 3 条，其余给出数量）
        const badItems = (result.details || []).filter((d) => d && d.status === "failed");
        const lines = badItems.slice(0, 3).map(
          (d) => `${d.file || "文件"}：${d.error || "未说明原因"}`);
        if (badItems.length > 3) lines.push(`等 ${badItems.length} 个文件失败`);
        const head = `入库部分完成：成功 ${success}/${total}`;
        toast(head, 6000);
        paintKbNotice(head + (lines.length ? "\n" + lines.join("\n")
                                           : (res.summary ? "\n" + res.summary : "")), "warn");
      } else {
        const text = total > 0 ? `入库完成：成功 ${success}/${total}` : "入库完成";
        toast(text);
        paintKbNotice(text, "");
      }
      if (res.status) renderKb(res.status);
      else await refreshKb();
    };
    $("btn-kb-refresh").onclick = refreshKb;
    bindWindowControls();
    $("btn-attach").onclick = async () => {
      const added = await call("pick_files");
      // null = 桥接不存在或调用抛错，call() 已经 toast 过「桥接未就绪 / 调用失败」，别叠第二条
      if (added === null) return;
      if (!Array.isArray(added)) { toast("附件接口返回异常，请重试"); return; }
      // 空数组有两种可能：用户取消，或后端打开文件对话框失败（webview_app 捕获异常后返回 []）。
      // 以前这里直接落空，界面看起来就是"点了没反应"——空结果也必须给出可读反馈。
      if (!added.length) { toast("未添加附件（已取消，或文件选择器不可用）"); return; }
      const bad = added.filter((x) => x.error);
      if (bad.length) toast(`有 ${bad.length} 个附件解析失败：${bad[0].name}`);
      else toast(`已挂载 ${added.length} 个附件（本地解析，不联网）`);
      const list = await call("list_attachments");
      state.attachments = Array.isArray(list) ? list : state.attachments;
      renderChips();
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
      if (e.target.checked) {
        const sync = res.sync || {}, rep = sync.report || {};
        ctrlNote(sync.ok
          ? `已启用上报 · 每 ${res.auto_interval || 30} 秒自动同步（本批 ${rep.events || 0} 条）`
          : "已启用上报，但首次同步失败：" + (rep.error || "未知原因"),
          sync.ok ? "is-ok" : "is-bad");
      } else {
        ctrlNote("已停用上报（已下发的策略规则保留，可一键撤销）", "");
      }
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
    if (themeBtn2) themeBtn2.onclick = toggleTheme;
    $("btn-toggle-right").onclick = () => { autoCollapsedRight = false; setRightCollapsed(!rightCollapsed()); };
    $("btn-panel-collapse").onclick = () => { autoCollapsedRight = false; setRightCollapsed(true); };
    $("btn-sidebar").onclick = () => {
      const app = document.querySelector(".app");
      setLeftCollapsed(!app.classList.contains("is-left-collapsed"));
    };
    document.querySelectorAll(".ptab").forEach((tab) => {
      tab.onclick = () => openPane(tab.dataset.pane);
    });
    // ★ V3.6.21：整条顶栏/浮条都是拖动区（恢复"拖顶部移动窗口"），但按钮、输入框、链接
    //   不能被当成拖动把手——pywebview 的拖动监听挂在 body 的冒泡阶段，这里在区域容器上
    //   拦住交互控件的 mousedown，既保住点击/选中文本，又不触发窗口拖动。
    document.querySelectorAll(".pywebview-drag-region").forEach((region) => {
      region.addEventListener("mousedown", (ev) => {
        const t = ev.target;
        if (t instanceof Element
            && t.closest("button, a, input, textarea, select, label, [data-no-drag]")) {
          ev.stopPropagation();
        }
      });
    });
    // 工作流面板：事件委托（面板内容每次重绘，逐按钮绑定会丢）
    const wfRoot = $("panel");
    if (wfRoot) {
      wfRoot.addEventListener("click", (ev) => {
        const t = ev.target.closest(
          "[data-wf-start],[data-wf-next],[data-wf-approve],[data-wf-reject],[data-wf-export]");
        if (!t) return;
        if (t.dataset.wfStart) wfStart(t.dataset.wfStart);
        else if (t.dataset.wfNext) wfNext();
        else if (t.dataset.wfApprove) wfApprove(true);
        else if (t.dataset.wfReject) wfApprove(false);
        else if (t.dataset.wfExport) wfExport();
      });
    }
    $("btn-theme").onclick = toggleTheme;
    $("btn-allow").onclick = async () => { hideApproval(); await call("approve", true); };
    $("btn-deny").onclick = async () => { hideApproval(); await call("approve", false); };

    document.querySelectorAll("#mode-switch .seg__btn").forEach((btn) => {
      btn.onclick = () => {
        // ★ 连点必须"视觉立即跟随、落库合并"（V3.6.21，负责人实测：快速来回点会卡手）。
        //   旧实现每次点击都 await persist() → 整页 fillSettings + 主题过渡 + 一次
        //   save_settings；连点会排一队重活，看起来就是按钮点不动。
        document.querySelectorAll("#mode-switch .seg__btn").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        persistThinkMode(btn.dataset.mode);
      };
    });

    $("sw-privacy").onchange = (e) => persist({ privacy: e.target.checked });
    $("sw-script-exec").onchange = (e) => persist({ allow_script_exec: e.target.checked });
    $("sw-topmost").onchange = (e) => persist({ topmost: e.target.checked });
    // 可读写范围（沙箱目录）：选目录走原生目录选择器（桥 pick_directory），清除 = 写空串回默认。
    // 保存后立刻重绘态势条——"沙箱 <路径>"与这里必须是同一来源，不能一个显示设置值、一个显示环境变量。
    if ($("btn-pick-sandbox")) $("btn-pick-sandbox").onclick = async () => {
      const res = await call("pick_directory");
      if (!res || !res.ok) { toast((res && res.error) || "未能选择目录"); return; }
      await persist({ sandbox_dir: res.path });
      renderPosture(null);
      paintSandbox();
      toast("可读写范围已设为：" + res.path);
    };
    if ($("btn-clear-sandbox")) $("btn-clear-sandbox").onclick = async () => {
      if (!String((state.settings || {}).sandbox_dir || "").trim()) { toast("当前已是默认范围"); return; }
      await persist({ sandbox_dir: "" });
      renderPosture(null);
      paintSandbox();
      toast("已回到默认可读写范围");
    };
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
    // 思考强度（照 ZCode）：档位词在输入区，真正的控制在模型卡片底部的滑杆；
    // Ctrl+T 循环四档。四条路径（滑杆 / 快捷键 / 触发器 / 设置回填）都走同一份 paintThinkLevel。
    const thinkSel = $("think-select");
    if (thinkSel) {
      $("think-trigger").onclick = () => openThinkPanel();
      const slider = $("think-slider");
      const applyLevel = (idx) => {
        const i = Math.max(0, Math.min(THINK_LEVELS.length - 1, Number(idx) || 0));
        const lv = THINK_LEVELS[i];
        paintThinkLevel(lv.depth);
        return lv;
      };
      if (slider) {
        slider.oninput = () => applyLevel(slider.value);
        slider.onchange = () => {
          const lv = applyLevel(slider.value);
          persist({ thinking_depth: lv.depth });
          toast(`思考强度 · ${lv.label}（最多 ${lv.depth * 2} 轮推理）`);
        };
      }
      document.addEventListener("keydown", (e) => {
        if (!(e.ctrlKey || e.metaKey) || String(e.key || "").toLowerCase() !== "t") return;
        e.preventDefault();
        const cur = levelOf(state.settings.thinking_depth);
        const idx = Math.max(0, THINK_LEVELS.findIndex((l) => l.key === cur.key));
        const lv = THINK_LEVELS[(idx + 1) % THINK_LEVELS.length];
        paintThinkLevel(lv.depth);
        persist({ thinking_depth: lv.depth });
        toast(`思考强度 · ${lv.label}（最多 ${lv.depth * 2} 轮推理）`);
      });
    }
    // 上下文容量浮层：点顶栏「上下文 x/y」开合；点外部 / Esc 关闭。
    // 数字随一轮结束刷新（renderStats 里在浮层打开时重取，不新增轮询）。
    const ctxPop = $("ctx-pop");
    if (ctxPop) {
      $("stat-tokens").onclick = () => (ctxPop.hidden ? openCtxPop() : closeCtxPop());
      document.addEventListener("click", (e) => {
        if (!ctxPop.hidden && !e.target.closest("#ctx-pop") && !e.target.closest("#stat-tokens")) closeCtxPop();
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtxPop(); });
    }
    /* ── 模型选择器（两级：供应商 → 该供应商的模型；底部「管理模型」跳设置页）─────
       照 ZCode 的形态重做（V3.6.8）：**没有搜索框**。旧实现靠"输入非匹配名 + 回车＝手填
       模型名"来加内网模型——那条路已由底部「管理模型」承担（跳到「设置 → 模型接入」，
       在那里填 Base URL / 密钥 / 模型 ID / 标签）。能力没有丢，落点反而更明确：手输的名字
       没有地址与密钥，本来也跑不起来，只会存下一个连不上的模型。
       交互：↑↓ 列内移动、Enter（供应商列＝进入它；模型列＝选中并关闭）、←→ 切列、
       Esc 关闭、点浮层外关闭；默认展开当前模型所在的供应商，当前项带勾。 */
    const mSel = $("model-select"), mTrig = $("model-trigger"), mPop = $("model-pop"),
          mProvBox = $("mpick-providers"), mModelBox = $("mpick-models"),
          mHead = $("mpick-head");
    let mProvKey = "";                             // 当前供应商（主卡片展示它下面的模型）
    let mHl = -1;                                  // 当前可见列表里的高亮序号

    const groupsAll = () => {
      const out = [];
      ["local", "lan", "wan"].forEach((tier) =>
        providersOfTier(state.catalog, tier).forEach((g) => out.push(g)));
      return out;
    };
    const groupByKey = (key) => groupsAll().find((g) => g.key === key) || null;
    const ensureProv = () => {
      if (mProvKey && groupByKey(mProvKey)) return;
      const place = placementOf(activeModel()), groups = groupsAll();
      mProvKey = place ? place.key : (groups[0] ? groups[0].key : "");
    };
    const visibleItems = () => mProvBox.hidden
      ? [...mModelBox.querySelectorAll("[data-model]")]
      : [...mProvBox.querySelectorAll("[data-pkey]")];
    const paintHl = () => {
      const items = visibleItems();
      items.forEach((el, i) => el.classList.toggle("is-hl", i === mHl));
      if (items[mHl]) items[mHl].scrollIntoView({ block: "nearest" });
    };

    const closeProvPop = () => {
      mProvBox.hidden = true;
      mHead.setAttribute("aria-expanded", "false");
      mHl = -1;
    };
    const closeModelPop = () => {
      mPop.hidden = true;
      mTrig.setAttribute("aria-expanded", "false");
      // 思考强度入口打开的是同一张卡片：关闭时把它的 aria-expanded 一并复位。
      const thinkTrg = $("think-trigger");
      if (thinkTrg) thinkTrg.setAttribute("aria-expanded", "false");
      mSel.classList.remove("is-open");
      closeProvPop();
    };

    const renderHeader = () => {
      const g = groupByKey(mProvKey);
      $("mpick-prov-name").textContent = g ? g.provider : "选择供应商";
      $("mpick-api-badge").textContent = g ? (g.tier === "local" ? "本机" : "API") : "—";
    };

    const renderModelCol = () => {
      const g = groupByKey(mProvKey), cur = activeModel();
      if (!g) { mModelBox.innerHTML = `<div class="model-opt model-opt--empty">先选一个供应商</div>`; return; }
      if (!g.models.length) { mModelBox.innerHTML = `<div class="model-opt model-opt--empty">该供应商还没有模型</div>`; return; }
      mModelBox.innerHTML = g.models.map((m) => {
        const tags = (m.tags || []).slice(0, 2).map((t) => `<i class="mtag">${esc(t)}</i>`).join("");
        const meta = m.source === "ollama" ? "本机已安装"
          : (m.model_id && m.model_id !== m.name ? m.model_id : "");
        const on = m.name === cur;
        return `<button type="button" class="model-opt${on ? " is-cur" : ""}" role="option"
            aria-selected="${on}" data-model="${esc(m.name)}"
            title="${esc(m.name + (meta ? " · " + meta : ""))}">
          <span class="model-opt__name">${esc(m.name)}</span>
          ${meta ? `<span class="model-opt__meta">${esc(String(meta).slice(0, 30))}</span>` : ""}
          <span class="model-opt__tags">${tags}</span>
          ${on ? `<span class="model-opt__tick">✓</span>` : ""}
        </button>`;
      }).join("");
    };

    const renderProvCol = () => {
      ensureProv();
      const groups = groupsAll();
      let html = "", lastTier = "";
      groups.forEach((g) => {
        if (g.tier !== lastTier) {
          lastTier = g.tier;
          html += `<div class="mpick__gt"><span class="tier-dot tier-dot--${g.tier}"></span>
            <b>${esc(TIER_META[g.tier].label)}</b></div>`;
        }
        const on = g.key === mProvKey;
        html += `<button type="button" class="mpick__prov${on ? " is-open" : ""}" role="option"
            aria-selected="${on}" data-pkey="${esc(g.key)}" title="${esc(TIER_META[g.tier].boundary)}">
          <span class="tier-dot tier-dot--${g.tier}"></span>
          <span class="mpick__pname">${esc(g.provider)}</span>
          <span class="mpick__api">${g.tier === "local" ? "本机" : "API"}</span>
          <span class="mpick__pmeta">${g.models.length}</span>
          ${on ? `<span class="mpick__check">✓</span>` : ""}
        </button>`;
      });
      html += `<button type="button" class="mpick__manage" id="mpick-manage" data-manage="1">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="3"/><path d="M4 7h4M14 7h6M4 17h6M16 17h4"/></svg>
        管理模型
      </button>`;
      mProvBox.innerHTML = html;
    };

    const renderPick = () => {
      ensureProv();
      renderHeader();
      renderProvCol();
      renderModelCol();
      const total = groupsAll().reduce((n, g) => n + g.models.length, 0);
      if ($("model-hint")) {
        $("model-hint").textContent = total
          ? `共 ${total} 个可用模型 · 按数据边界分三档`
          : ((state.catalog && state.catalog.local_note)
             || "还没有可用模型：点「管理模型」手动添加，或先 `ollama pull <模型名>`");
      }
      const items = visibleItems();
      // 打开时**不预高亮**当前项：ZCode 的当前模型只用 ✓ 表示，键盘按下 ↑↓ 才出现高亮。
      if (mHl >= items.length) mHl = -1;
      paintHl();
    };

    const openProvPop = () => {
      renderProvCol();
      mProvBox.hidden = false;
      mHead.setAttribute("aria-expanded", "true");
      const provs = [...mProvBox.querySelectorAll("[data-pkey]")];
      mHl = Math.max(0, provs.findIndex((el) => el.dataset.pkey === mProvKey));
      paintHl();
    };
    const openModelPop = () => {
      ensureProv();
      mPop.hidden = false;
      mTrig.setAttribute("aria-expanded", "true");
      // 同一张卡片也承载思考强度：两个入口的展开状态保持一致（见 closeModelPop）。
      const thinkTrg = $("think-trigger");
      if (thinkTrg) thinkTrg.setAttribute("aria-expanded", "true");
      mSel.classList.add("is-open");
      closeProvPop();
      mHl = -1;
      renderPick();
      mPop.focus();
    };
    // 思考档位触发器要能打开同一张卡片并聚焦滑杆（见 openThinkPanel）
    window.__openModelPop = openModelPop;
    const pickModel = (name) => { if (name) persist({ model: name }); closeModelPop(); };

    const gotoManage = async () => {
      // 「管理模型」＝原"手输模型名"那条路的正式落点：去设置页填地址 / 密钥 / 模型 ID / 标签
      closeModelPop();
      const place = placementOf(activeModel());
      if (place) state.prov = { tier: place.tier, provider: place.provider, builtin: place.builtin };
      openSettings("model");
      renderProv(await call("model_catalog"));
    };

    mTrig.onclick = () => (mPop.hidden ? openModelPop() : closeModelPop());
    mHead.onclick = () => {
      if (mProvBox.hidden) openProvPop();
      else { closeProvPop(); renderPick(); }
    };
    mProvBox.onclick = (e) => {
      if (e.target.closest("[data-manage]")) { gotoManage(); return; }
      const btn = e.target.closest("[data-pkey]");
      if (!btn) return;
      mProvKey = btn.dataset.pkey;
      closeProvPop();
      renderPick();
      const first = mModelBox.querySelector("[data-model]");
      if (first) { try { first.focus(); } catch (_) {} }
    };
    mModelBox.onclick = (e) => {
      const btn = e.target.closest("[data-model]");
      if (btn) pickModel(btn.dataset.model);
    };
    mPop.onkeydown = (e) => {
      // ★ 卡片里有原生表单控件（#think-slider 是 range input）：方向键属于它们的
      //   默认交互。旧实现无条件 preventDefault，焦点在滑杆上按 ↑↓ 被吞掉，键盘不可用。
      //   这里只对输入类控件放行方向键，列表导航逻辑对其它元素保持不变。
      const tag = ((e.target && e.target.tagName) || "").toLowerCase();
      if ((e.key === "ArrowDown" || e.key === "ArrowUp")
          && (tag === "input" || tag === "textarea" || tag === "select")) return;
      const items = visibleItems();
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        if (!items.length) return;
        mHl = (mHl + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        paintHl();
      } else if (e.key === "ArrowRight") {
        if (mProvBox.hidden) { e.preventDefault(); openProvPop(); }
      } else if (e.key === "ArrowLeft") {
        if (!mProvBox.hidden) { e.preventDefault(); closeProvPop(); renderPick(); }
      } else if (e.key === "Enter") {
        e.preventDefault();
        const el = items[Math.max(0, mHl)];
        if (!el) return;
        if (!mProvBox.hidden) {
          mProvKey = el.dataset.pkey; closeProvPop(); renderPick();
        } else pickModel(el.dataset.model);
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (!mProvBox.hidden) { closeProvPop(); renderPick(); }
        else closeModelPop();
      }
    };
    document.addEventListener("click", (e) => {
      // ★ 用事件路径判断"点在外面"，而不是 `mSel.contains(e.target)`：点供应商时
      //   处理函数会同步重绘子菜单（innerHTML），**e.target 已经被摘出 DOM**，
      //   contains() 恒为 false —— 于是每次点供应商都会把弹层自己关掉。
      //   composedPath 在派发时固定，不受重绘影响。
      const path = typeof e.composedPath === "function" ? e.composedPath() : [];
      // 思考档位触发器也在"模型区域"里：点它打开同一张卡片，不能被这条外部点击
      // 监听器当成"点外面"立刻关掉（真机表现为"点高没反应"）。
      const thinkBox = $("think-select");
      const inside = path.length
        ? (path.includes(mSel) || path.includes(thinkBox))
        : (mSel.contains(e.target) || (thinkBox && thinkBox.contains(e.target)));
      if (!inside) closeModelPop();
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
