/* ==========================================================================
   隐盾控制台 · 前端逻辑（原生 ES，无构建步骤、无第三方依赖）
   --------------------------------------------------------------------------
   安全相关约定：
     · 管理令牌只放在 sessionStorage（关标签页即清除），不写 localStorage；
     · 服务端返回的一切内容都视为不可信，渲染前一律 esc() 转义（防 XSS）；
     · 不做任何本地计算派生"结论"——链状态、告警、策略摘要都由服务端判定，
       前端只负责呈现，避免"界面显示的和实际判定的不是一回事"。
   预览：#mock 打开时用内置样例数据渲染（明显标注"预览数据"），便于离线看设计。
   ========================================================================== */

const $ = (id) => document.getElementById(id);
const MOCK = location.hash.startsWith("#mock");
const PAGES = {
  overview:  { title: "态势总览", sub: "端点上报的掩码事件与链状态" },
  events:    { title: "事件台账", sub: "按端点 / 类型 / 级别检索上报事件" },
  endpoints: { title: "端点资产", sub: "每台装了隐盾的机器：版本、能力、链状态" },
  policy:    { title: "策略下发", sub: "只能收紧：敏感路径取并集、审批取更严、允许集取交集" },
  usage:     { title: "模型用量", sub: "调用计数与交互体量（内容不经过控制面）" },
};
const SEV_LABEL = { info: "提示", warning: "警告", critical: "严重", security: "安全" };
const TYPE_LABEL = {
  tool_call: "工具调用", tool_result: "工具结果", llm_input: "模型输入", llm_output: "模型输出",
  privacy_sensitive: "隐私事件", access_control: "访问控制", session_start: "会话开始",
  session_end: "会话结束", workflow_step_executed: "工作流步骤", chain_restart: "链重启",
  control_report: "控制面上报",
};

const state = {
  base: "", token: "", page: "overview", timer: null, endpoints: [],
  filters: { endpoint_id: "", event_type: "", severity: "", q: "" }, offset: 0, total: 0,
  policyDraft: { sensitive_paths: "", approval_floor: "", allowed_models: "", allowed_plugins: "",
                 require_strict_mode: false, note: "" },
};

/* ── 工具 ─────────────────────────────────────────────── */
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const num = (n) => Number(n || 0).toLocaleString("zh-CN");
const shortHash = (h) => (h ? String(h).slice(0, 12) + (String(h).length > 12 ? "…" : "") : "—");
function fmtTime(ts) {
  const s = String(ts || "");
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2})(:\d{2})?/);
  return m ? `${m[2]}-${m[3]} ${m[4]}${m[5] || ""}` : (s || "—");
}
function relTime(ts) {
  const t = Date.parse(String(ts || "").replace(" ", "T"));
  if (!t) return "";
  const d = Math.round((Date.now() - t) / 1000);
  if (d < 60) return "刚刚";
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`;
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`;
  return `${Math.floor(d / 86400)} 天前`;
}
function toast(msg, ms = 2600) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, ms);
}
function setStatus(kind, text) {
  const el = $("status");
  el.className = "status" + (kind ? " is-" + kind : "");
  el.querySelector("em").textContent = text;
}
function sevBadge(sev) {
  const cls = ["critical", "security"].includes(sev) ? "bad" : (sev === "warning" ? "warn" : "idle");
  return `<span class="sev sev--${esc(sev)}">${esc(SEV_LABEL[sev] || sev)}</span>`;
}
function chainBadge(verdict) {
  const cls = verdict === "已锚定" ? "ok" : (verdict === "链异常" ? "bad" : "idle");
  return `<span class="badge badge--${cls}">${esc(verdict)}</span>`;
}

/* ── 与后端通信 ───────────────────────────────────────── */
async function api(path, options = {}) {
  if (MOCK) return mockApi(path, options);
  const res = await fetch(state.base + path, {
    method: options.method || "GET",
    headers: Object.assign({ Authorization: "Bearer " + state.token },
                           options.body ? { "Content-Type": "application/json" } : {}),
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) { const e = new Error("未授权"); e.unauthorized = true; throw e; }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ── 鉴权 ─────────────────────────────────────────────── */
function showAuth(message = "") {
  $("shell").hidden = true;
  $("auth").hidden = false;
  const err = $("auth-err");
  err.hidden = !message;
  err.textContent = message;
  $("auth-url").value = state.base || (location.protocol.startsWith("http") ? location.origin : "http://127.0.0.1:8787");
  $("auth-token").focus();
}
async function boot() {
  if (MOCK) {
    state.base = location.origin; state.token = "mock";
    $("auth").hidden = true; $("shell").hidden = false;
    toast("预览模式（#mock）：界面使用样例数据，未连接控制面");
    return start();
  }
  state.base = sessionStorage.getItem("yd.base") || "";
  state.token = sessionStorage.getItem("yd.token") || "";
  if (!state.base || !state.token) return showAuth();
  try { await api("/api/v1/overview"); start(); }
  catch (e) { showAuth(e.unauthorized ? "令牌无效或已过期，请重新输入" : `无法连接控制面：${e.message}`); }
}
function start() {
  $("auth").hidden = true; $("shell").hidden = false;
  bindKeys();
  document.querySelectorAll(".nav__item").forEach((b) => {
    b.onclick = () => go(b.dataset.page);
  });
  $("btn-refresh").onclick = () => render(true);
  $("btn-theme").onclick = toggleTheme;
  $("auto-refresh").onchange = (e) => { e.target.checked ? startTimer() : stopTimer(); };
  applyTheme(localStorage.getItem("yd.theme") || "light");
  go("overview");
  startTimer();
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next); localStorage.setItem("yd.theme", next);
}
function applyTheme(t) { document.documentElement.dataset.theme = t; }
function startTimer() { stopTimer(); state.timer = setInterval(() => {
  if (["overview", "endpoints"].includes(state.page)) render(); }, 10000); }
function stopTimer() { if (state.timer) clearInterval(state.timer); state.timer = null; }

function bindKeys() {
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.metaKey || e.ctrlKey) return;
    if (e.key === "/") { e.preventDefault(); const box = $("f-q"); if (box) box.focus(); }
    if (e.key === "r") render(true);
    const page = Object.keys(PAGES)[Number(e.key) - 1];
    if (page) go(page);
  });
}

/* ── 路由 ─────────────────────────────────────────────── */
function go(page) {
  state.page = page;
  document.querySelectorAll(".nav__item").forEach((b) =>
    b.classList.toggle("is-active", b.dataset.page === page));
  $("page-title").textContent = PAGES[page].title;
  $("page-sub").textContent = PAGES[page].sub;
  render();                    // 切页不弹提示（只有手动/点刷新才提示，避免噪声）
}
async function render(showStamp = false) {
  const box = $("content");
  if (!box.dataset.painted) box.innerHTML = `<div class="skel" style="width:60%;margin:20px 0"></div>
    <div class="skel" style="width:90%;margin:20px 0"></div><div class="skel" style="width:40%"></div>`;
  try {
    if (state.page === "overview") await renderOverview(box);
    else if (state.page === "events") await renderEvents(box);
    else if (state.page === "endpoints") await renderEndpoints(box);
    else if (state.page === "policy") await renderPolicy(box);
    else await renderUsage(box);
    box.dataset.painted = "1";
    setStatus("live", MOCK ? "预览数据" : "已连接");
    $("foot-stamp").textContent = (MOCK ? "预览数据 · " : "") + new Date().toLocaleTimeString("zh-CN");
  } catch (e) {
    if (e.unauthorized) return showAuth("令牌无效或已过期，请重新输入");
    setStatus("err", "连接异常");
    box.innerHTML = `<div class="err-box">加载失败：${esc(e.message)}</div>`;
  }
  if (showStamp) toast("已刷新");
}

/* ── 页面：态势总览 ───────────────────────────────────── */
async function renderOverview(box) {
  const [ov, eps, alerts, events] = await Promise.all([
    api("/api/v1/overview"), api("/api/v1/endpoints"),
    api("/api/v1/alerts?limit=8"), api("/api/v1/events?limit=12"),
  ]);
  const rows = eps.endpoints || [];
  const live = rows.filter((r) => r.last_seen_at).length;
  box.innerHTML = `<section>
    <div class="metrics">
      <div class="metric"><div class="metric__label">在管端点</div>
        <div class="metric__value">${num(ov.endpoints)}<small>台</small></div></div>
      <div class="metric"><div class="metric__label">已汇集事件</div>
        <div class="metric__value">${num(ov.events)}<small>条</small></div></div>
      <div class="metric ${ov.high_events ? "metric--alert" : ""}"><div class="metric__label">高风险事件</div>
        <div class="metric__value">${num(ov.high_events)}<small>条</small></div></div>
      <div class="metric ${ov.chain_alerts ? "metric--alert" : "metric--ok"}">
        <div class="metric__label">链告警</div>
        <div class="metric__value">${num(ov.chain_alerts)}<small>项</small></div></div>
    </div>

    <div class="block">
      <div class="block__head"><h3>审计链锚定状态</h3>
        <p>条数只增不减、链头逐次留痕；改写或截断会被发现</p></div>
      ${rows.length ? `<div class="table-wrap"><table class="grid">
        <thead><tr><th>端点</th><th>状态</th><th class="num">链长</th><th>链头</th>
          <th>首次锚定</th><th>最后上报</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td>${esc(r.name)}<div class="ep__id">${esc(r.endpoint_id)}</div></td>
          <td>${chainBadge(r.chain_verdict)}${r.chain_alerts ? ` <span class="chip">${r.chain_alerts} 告警</span>` : ""}</td>
          <td class="num">${num(r.total_count)}</td>
          <td class="mid" title="${esc(r.head_hash)}">${esc(shortHash(r.head_hash))}</td>
          <td class="mid">${esc(fmtTime(r.first_seen_at))}</td>
          <td class="mid">${esc(fmtTime(r.last_seen_at))} <span class="chip">${esc(relTime(r.last_seen_at))}</span></td>
        </tr>`).join("")}</tbody></table></div>`
        : emptyState("还没有端点上报", "在端点侧运行控制面上报器即可接入")}
    </div>

    <div class="block">
      <div class="block__head"><h3>链与高危告警</h3><p>链异常与高风险操作在同一视图呈现</p></div>
      ${(alerts.alerts || []).length ? `<div class="table-wrap"><table class="grid">
        <thead><tr><th>时间</th><th>端点</th><th>告警</th><th>说明</th></tr></thead>
        <tbody>${alerts.alerts.map((a) => `<tr class="${a.severity !== "低" ? "is-high" : ""}">
          <td class="mid">${esc(fmtTime(a.created_at))}</td>
          <td class="mid">${esc(a.endpoint_id)}</td>
          <td>${sevBadge(a.severity === "低" ? "info" : "critical")}</td>
          <td>${esc(a.detail)}</td></tr>`).join("")}</tbody></table></div>`
        : emptyState("没有链告警", "链自洽且未被删改时不会产生告警")}
    </div>

    <div class="block">
      <div class="block__head"><h3>最近事件</h3><p>内容为端点脱敏后的掩码态</p>
        <span class="spacer"></span>
        <button class="btn btn--sm" onclick="document.querySelector('.nav__item[data-page=events]').click()">查看全部</button></div>
      ${eventTable(events.events || [], false)}
    </div>

    <p class="hint" style="color:var(--text-3);font-size:var(--fs-meta);line-height:1.8">
      已上报 ${num(ov.events)} 条事件 · 覆盖 ${live}/${num(ov.endpoints)} 台有上报记录的端点 ·
      控制面不含明文（端点侧掩码 + 密钥 DPAPI 绑定）
    </p>
  </section>`;
}

/* ── 页面：事件台账 ───────────────────────────────────── */
function eventTable(events, withPager = true) {
  if (!events.length) return emptyState("没有匹配的事件", "换个筛选条件试试");
  return `<div class="table-wrap"><table class="grid">
    <thead><tr><th>时间</th><th>端点</th><th>类型</th><th>级别</th><th>消息（掩码态）</th><th class="num">序号</th></tr></thead>
    <tbody>${events.map((e) => `<tr class="${["critical", "security"].includes(e.severity) ? "is-high" : ""}">
      <td class="mid">${esc(fmtTime(e.ts))}</td>
      <td class="mid">${esc(e.endpoint_id)}</td>
      <td>${esc(TYPE_LABEL[e.event_type] || e.event_type)}</td>
      <td>${sevBadge(e.severity)}</td>
      <td>${esc(e.message || "—")}</td>
      <td class="num">${num(e.seq)}</td></tr>`).join("")}</tbody></table></div>`;
}
async function renderEvents(box) {
  const f = state.filters;
  if (!box.dataset.painted || box.dataset.page !== "events") {
    state.endpoints = (await api("/api/v1/endpoints")).endpoints || [];
    const types = (await api("/api/v1/events?limit=1")).types || [];
    box.dataset.page = "events";
    box.innerHTML = `<section>
      <div class="filters">
        <select class="select" id="f-ep"><option value="">全部端点</option>
          ${state.endpoints.map((e) => `<option value="${esc(e.endpoint_id)}">${esc(e.name)}</option>`).join("")}</select>
        <select class="select" id="f-type"><option value="">全部类型</option>
          ${types.map((t) => `<option value="${esc(t)}">${esc(TYPE_LABEL[t] || t)}</option>`).join("")}</select>
        <select class="select" id="f-sev"><option value="">全部级别</option>
          ${Object.entries(SEV_LABEL).map(([k, v]) => `<option value="${esc(k)}">${esc(v)}</option>`).join("")}</select>
        <input class="input" id="f-q" placeholder="搜索消息 / 明细（按 / 聚焦）" value="${esc(f.q)}">
        <span class="spacer"></span>
        <span class="chip" id="f-total">—</span>
      </div>
      <div id="events-host"></div>
      <div style="display:flex;justify-content:center;margin-top:16px">
        <button class="btn" id="btn-more">加载更多</button>
      </div>
    </section>`;
    const bind = (id, key, immediate) => {
      const el = $(id);
      const fire = () => { state.filters[key] = el.value; state.offset = 0;
        if (key === "q") { clearTimeout(el._t); el._t = setTimeout(loadEvents, 260); } else loadEvents(); };
      el.onchange = fire; el.oninput = fire;
    };
    bind("f-ep", "endpoint_id"); bind("f-type", "event_type");
    bind("f-sev", "severity"); bind("f-q", "q", false);
    $("btn-more").onclick = () => { state.offset += 100; loadEvents(true); };
    await loadEvents();
    return;
  }
  await loadEvents();
}
async function loadEvents(append = false) {
  const q = new URLSearchParams(Object.assign({ limit: 100, offset: append ? state.offset : 0 }, state.filters));
  Object.keys(state.filters).forEach((k) => { if (!state.filters[k]) q.delete(k); });
  const data = await api("/api/v1/events?" + q.toString());
  state.total = data.total || 0;
  $("f-total").textContent = `共 ${num(state.total)} 条`;
  const host = $("events-host");
  if (append) host.insertAdjacentHTML("beforeend", eventTable(data.events || []).replace(/^<div class="table-wrap">|<\/div>$/g, ""));
  else host.innerHTML = eventTable(data.events || []);
  $("btn-more").hidden = (state.offset + 100) >= state.total;
}

/* ── 页面：端点资产 ───────────────────────────────────── */
async function renderEndpoints(box) {
  const data = await api("/api/v1/endpoints");
  const rows = data.endpoints || [];
  box.innerHTML = `<section>${rows.length ? `<div class="eps">${rows.map((r) => `
    <article class="ep">
      <div class="ep__head">
        <span class="ep__name">${esc(r.name)}</span>
        ${chainBadge(r.chain_verdict)}
      </div>
      <div class="ep__id" style="margin-bottom:12px">${esc(r.endpoint_id)}</div>
      <dl>
        <dt>版本</dt><dd>${esc(r.version || "—")} · ${esc(r.platform || "—")}</dd>
        <dt>链长</dt><dd>${num(r.total_count)} <span class="chip">${esc(shortHash(r.head_hash))}</span></dd>
        <dt>最后上报</dt><dd>${esc(fmtTime(r.last_seen_at))} <span class="chip">${esc(relTime(r.last_seen_at))}</span></dd>
        <dt>策略版本</dt><dd>v${num(r.policy_version)}</dd>
      </dl>
      <div class="ep__tags">
        ${r.strict_mode ? `<span class="badge badge--ok">严格模式</span>` : `<span class="badge badge--idle">标准模式</span>`}
        ${(r.models || []).slice(0, 4).map((m) => `<span class="chip">${esc(m)}</span>`).join("")}
        ${(r.external_models || []).length ? `<span class="badge badge--warn">含外部模型 ${(r.external_models || []).length}</span>` : ""}
        ${(r.plugins || []).filter((p) => p.enabled).map((p) => `<span class="chip">插件 ${esc(p.id)}</span>`).join("")}
      </div>
      ${Object.keys(r.policy || {}).length ? `<div class="kv" style="margin-top:12px">
        ${Object.entries(r.policy).map(([k, v]) => `<span>${esc(k)} <b>${esc(v)}</b></span>`).join("")}</div>` : ""}
    </article>`).join("")}</div>`
    : emptyState("还没有端点", "在端点侧配置并启用上报后，这里会出现它的资产与链状态")}</section>`;
}

/* ── 页面：策略下发 ───────────────────────────────────── */
async function renderPolicy(box) {
  const [eps, pol] = await Promise.all([api("/api/v1/endpoints"), api("/api/v1/policy")]);
  state.endpoints = eps.endpoints || [];
  const d = state.policyDraft;
  box.innerHTML = `<section><div class="cols">
    <div>
      <div class="block">
        <div class="block__head"><h3>新策略</h3><p>填哪几项就下发哪几项；留空表示"本条不下发"</p></div>
        <div class="form-row">
          <label for="p-paths">追加敏感路径（每行一条，glob）</label>
          <textarea class="textarea" id="p-paths" placeholder="D:\\薪资\\*&#10;/data/财务/*">${esc(d.sensitive_paths)}</textarea>
          <p class="hint">与端点本地规则取<b>并集</b>——只会让更多路径被判为敏感，不会减少。</p>
        </div>
        <div class="form-row">
          <label for="p-floor">审批下限</label>
          <select class="select" id="p-floor">
            <option value="">不改变</option>
            <option value="confirm">至少人工确认（confirm）</option>
            <option value="deny">一律拒绝（deny）</option>
          </select>
          <p class="hint">只能<b>提高</b>严格度；端点本地更严时保持端点自身的更严设置。</p>
        </div>
        <div class="form-row">
          <label for="p-models">允许使用的模型（逗号分隔，留空＝不下发）</label>
          <input class="input" id="p-models" placeholder="qwen2.5:7b, qwen3.5:4b">
          <p class="hint">与端点本地允许集取<b>交集</b>：填写后端点只能用这里列出的模型。</p>
        </div>
        <div class="form-row">
          <label for="p-plugins">允许启用的插件（逗号分隔，留空＝不下发）</label>
          <input class="input" id="p-plugins" placeholder="custom_dict, doc_extra">
          <p class="hint">同样取<b>交集</b>；不在列表内的插件在端点侧会被停用。</p>
        </div>
        <div class="form-row">
          <label class="switch"><input type="checkbox" id="p-strict"><span></span>
            <em>强制严格模式（端点禁用解释器，仅留只读命令）</em></label>
        </div>
        <div class="form-row">
          <label for="p-scope">下发范围</label>
          <select class="select" id="p-scope">
            <option value="global">全部端点（全局策略）</option>
            ${state.endpoints.map((e) => `<option value="${esc(e.endpoint_id)}">仅 ${esc(e.name)}</option>`).join("")}
          </select>
        </div>
        <div class="form-row">
          <label for="p-note">备注</label>
          <input class="input" id="p-note" placeholder="例：Q4 合规要求，收紧薪资目录" value="${esc(d.note)}">
        </div>
        <button class="btn btn--primary" id="p-submit">签发并下发</button>
      </div>

      <div class="block">
        <div class="block__head"><h3>策略历史</h3><p>每次下发都留档，不可覆盖</p></div>
        ${(pol.history || []).length ? `<div class="table-wrap"><table class="grid">
          <thead><tr><th class="num">版本</th><th>范围</th><th>时间</th></tr></thead>
          <tbody>${pol.history.map((h) => `<tr><td class="num">v${num(h.version)}</td>
            <td class="mid">${esc(h.scope)}</td><td class="mid">${esc(fmtTime(h.updated_at))}</td></tr>`).join("")}
          </tbody></table></div>` : emptyState("还没有下发过策略", "下发后端点会在下一次上报时取走并生效")}
      </div>
    </div>

    <div>
      <div class="block">
        <div class="denied">
          <h4>控制面无法下发的项</h4>
          <ul><li>anonymize</li><li>exempt</li><li>bypass</li><li>auto_approve</li>
            <li>whitelist</li><li>disable_audit</li></ul>
          <p>脱敏豁免、审批放行、白名单放行、审计开关属<b>内核判定权</b>，控制面没有任何通路可以下发。
             端点侧会拒收含这些键的策略包，并按"只能收紧"的单调规则合并——即使控制面被攻破，也只能让端点管得更严。</p>
        </div>
      </div>
      <div class="block">
        <div class="block__head"><h3>端点当前生效策略</h3></div>
        ${state.endpoints.length ? state.endpoints.filter((e) => Object.keys(e.policy || {}).length)
          .map((e) => `<div style="margin-bottom:12px"><div class="ep__id">${esc(e.name)}</div>
            <div class="kv">${Object.entries(e.policy).map(([k, v]) => `<span>${esc(k)} <b>${esc(v)}</b></span>`).join("")}</div></div>`).join("")
          || `<p class="hint">端点尚未取走任何策略。</p>` : `<p class="hint">还没有端点。</p>`}
      </div>
    </div>
  </div></section>`;

  $("p-submit").onclick = submitPolicy;
}
async function submitPolicy() {
  const lines = $("p-paths").value.split("\n").map((s) => s.trim()).filter(Boolean);
  const models = $("p-models").value.split(",").map((s) => s.trim()).filter(Boolean);
  const plugins = $("p-plugins").value.split(",").map((s) => s.trim()).filter(Boolean);
  const bundle = {};
  if (lines.length) bundle.sensitive_paths = lines;
  if ($("p-floor").value) bundle.approval_floor = $("p-floor").value;
  if (models.length) bundle.allowed_models = models;
  if (plugins.length) bundle.allowed_plugins = plugins;
  if ($("p-strict").checked) bundle.require_strict_mode = true;
  if ($("p-note").value.trim()) bundle.note = $("p-note").value.trim();
  if (!Object.keys(bundle).length) return toast("至少要填一项再下发");
  try {
    const res = await api("/api/v1/policy", { method: "POST", body: { scope: $("p-scope").value, bundle } });
    toast(`已签发 v${res.version} · ${Object.entries(res.summary || {}).map(([k, v]) => k + "：" + v).join("；")}`, 4200);
    render(true);
  } catch (e) { toast("下发被拒绝：" + e.message, 5200); }
}

/* ── 页面：模型用量 ───────────────────────────────────── */
async function renderUsage(box) {
  const [usage, eps] = await Promise.all([api("/api/v1/usage"), api("/api/v1/endpoints")]);
  const inv = {};
  (eps.endpoints || []).forEach((e) => (e.models || []).forEach((m) => {
    inv[m] = inv[m] || { local: 0, external: 0 };
    (e.external_models || []).includes(m) ? inv[m].external++ : inv[m].local++;
  }));
  const rows = usage.usage || [];
  box.innerHTML = `<section>
    <div class="block">
      <div class="block__head"><h3>调用计数与交互体量</h3>
        <p>口径：模型输出事件数 = 调用次数；LLM 事件 content_length 之和 = 交互体量</p></div>
      ${rows.length ? `<div class="table-wrap"><table class="grid">
        <thead><tr><th>模型</th><th>范围</th><th class="num">端点</th><th class="num">调用次数</th>
          <th class="num">交互体量（字符）</th></tr></thead>
        <tbody>${rows.map((r) => `<tr>
          <td>${esc(r.model)}</td>
          <td>${r.scope === "external" ? `<span class="badge badge--warn">外部模型</span>`
            : (r.scope === "mixed" ? `<span class="badge badge--idle">混合</span>`
            : `<span class="badge badge--ok">本机模型</span>`)}</td>
          <td class="num">${num(r.endpoints)}</td>
          <td class="num">${num(r.calls)}</td>
          <td class="num">${num(r.chars)}</td></tr>`).join("")}</tbody></table></div>`
        : emptyState("还没有用量数据", "端点上报后这里会显示调用计数与体量")}
    </div>

    <div class="block">
      <div class="block__head"><h3>模型资产清单</h3><p>端点可用模型（本机 / 外部）</p></div>
      ${Object.keys(inv).length ? `<div class="table-wrap"><table class="grid">
        <thead><tr><th>模型</th><th>类别</th><th class="num">覆盖端点数</th></tr></thead>
        <tbody>${Object.entries(inv).map(([m, c]) => `<tr><td class="mid">${esc(m)}</td>
          <td>${c.external ? `<span class="badge badge--warn">外部</span>` : `<span class="badge badge--ok">本机</span>`}</td>
          <td class="num">${num(c.local + c.external)}</td></tr>`).join("")}</tbody></table></div>`
        : emptyState("端点尚未上报模型清单", "")}
    </div>

    <p class="hint" style="color:var(--text-3);font-size:var(--fs-meta);line-height:1.8">
      控制面<b>不代理模型流量</b>：提示词与回答从不经过这里，看到的只是计数与体量。
      当前内核的 LLM 审计事件未标注模型名，故端点上报告的是<b>端点级聚合</b>；
      按模型细分需要内核在 LLM 事件里带上模型名（已列入路线图）。
    </p>
  </section>`;
}

function emptyState(title, sub) {
  return `<div class="empty">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16v12H4zM4 7l8 6 8-6"/></svg>
    <div>${esc(title)}</div>${sub ? `<div style="margin-top:6px">${esc(sub)}</div>` : ""}</div>`;
}

/* ── 预览数据（仅 #mock）───────────────────────────────── */
const MOCK_DATA = {
  overview: { endpoints: 3, events: 418, high_events: 6, chain_alerts: 1, generated_at: "2026-10-16T09:12:00" },
  endpoints: { endpoints: [
    { endpoint_id: "ep-fin-27", name: "财务部-27号机", version: "3.4.6", platform: "Windows-11",
      last_seen_at: "2026-10-16T09:11:30", strict_mode: false, policy_version: 3, total_count: 216,
      head_hash: "9f2c7a41d5e83b06c1a94f27de8b3051", chain_verdict: "已锚定", chain_alerts: 0,
      first_seen_at: "2026-10-14T08:02:11", models: ["qwen2.5:7b", "qwen3.5:4b", "gpt-4o"],
      external_models: ["gpt-4o"], plugins: [{ id: "custom_dict", version: "1.0.0", enabled: true }],
      policy: { 敏感路径: "追加 3 条", 审批下限: "必须人工确认" } },
    { endpoint_id: "ep-hr-04", name: "人力资源部-04号机", version: "3.4.6", platform: "Windows-11",
      last_seen_at: "2026-10-16T09:09:02", strict_mode: true, policy_version: 5, total_count: 132,
      head_hash: "31b8e0a7c4d29f5618b7e3a0c6d5f942", chain_verdict: "已锚定", chain_alerts: 0,
      first_seen_at: "2026-10-13T14:20:00", models: ["qwen2.5:7b"],
      external_models: [], plugins: [], policy: { 严格模式: "强制开启（禁用解释器）" } },
    { endpoint_id: "ep-rd-11", name: "研发部-11号机", version: "3.4.5", platform: "Windows-10",
      last_seen_at: "2026-10-16T08:58:41", strict_mode: false, policy_version: 2, total_count: 70,
      head_hash: "7d5c1e93b0af268d4c31e8795a0b6f24", chain_verdict: "链异常", chain_alerts: 2,
      first_seen_at: "2026-10-15T10:30:00", models: ["qwen2.5:7b"], external_models: [], plugins: [],
      policy: {} }] },
  alerts: { alerts: [
    { created_at: "2026-10-16T08:58:41", endpoint_id: "ep-rd-11", severity: "高",
      detail: "总条数少于上次锚定 —— 尾部被截断（本次 70 条 < 上次锚定 82 条）" },
    { created_at: "2026-10-16T08:58:41", endpoint_id: "ep-rd-11", severity: "高",
      detail: "链头与上次锚定不一致（条数未变）—— 同长度替换" },
    { created_at: "2026-10-16T09:11:30", endpoint_id: "ep-fin-27", severity: "高",
      detail: "access_control · 拒绝写入沙箱外路径 [SENSITIVE_PATH_0_7c1a]" }] },
  events: { events: [
    { ts: "2026-10-16T09:11:30", endpoint_id: "ep-fin-27", event_type: "tool_call", severity: "info",
      message: "read_file [FILE_PATH_0_a31f]", seq: 216, id: 418 },
    { ts: "2026-10-16T09:10:02", endpoint_id: "ep-fin-27", event_type: "llm_input", severity: "info",
      message: "模型输入（已脱敏）· 预 预览：甲方 [NAME_0_4b7c] 联系电话 [PHONE_0_9d2e]", seq: 215, id: 417 },
    { ts: "2026-10-16T09:09:02", endpoint_id: "ep-hr-04", event_type: "access_control", severity: "critical",
      message: "审批被拒绝：写入 [SENSITIVE_PATH_0_5f80]", seq: 132, id: 416 },
    { ts: "2026-10-16T09:08:44", endpoint_id: "ep-hr-04", event_type: "privacy_sensitive", severity: "warning",
      message: "检出 3 类敏感实体（PHONE/IDCARD/BANKCARD），已替换为占位符", seq: 131, id: 415 },
    { ts: "2026-10-16T09:05:10", endpoint_id: "ep-fin-27", event_type: "workflow_step_executed", severity: "info",
      message: "合同审查 · 步骤 2 完成", seq: 214, id: 414 },
    { ts: "2026-10-16T08:58:41", endpoint_id: "ep-rd-11", event_type: "control_report", severity: "warning",
      message: "控制面上报异常：HTTP 401 端点令牌无效", seq: 70, id: 413 }] },
  policy: { history: [
    { scope: "global", version: 3, updated_at: "2026-10-15T17:22:00" },
    { scope: "ep-hr-04", version: 5, updated_at: "2026-10-15T19:40:00" },
    { scope: "global", version: 2, updated_at: "2026-10-14T09:05:00" }] },
  usage: { usage: [
    { model: "(端点合计)", scope: "local", calls: 512, chars: 1284400, endpoints: 3 },
    { model: "(端点合计)", scope: "external", calls: 38, chars: 96500, endpoints: 1 }] },
};
async function mockApi(path) {
  const key = path.split("?")[0].replace("/api/v1/", "");
  if (path.startsWith("/api/v1/events")) {
    const rows = MOCK_DATA.events.events;
    return { events: rows, total: rows.length, types: [...new Set(rows.map((r) => r.event_type))],
             limit: 100, offset: 0 };
  }
  return MOCK_DATA[key] || {};
}

/* ── 启动 ─────────────────────────────────────────────── */
$("auth-form").onsubmit = async (e) => {
  e.preventDefault();
  state.base = $("auth-url").value.trim().replace(/\/$/, "");
  state.token = $("auth-token").value.trim();
  try {
    await fetch(state.base + "/api/v1/overview", { headers: { Authorization: "Bearer " + state.token } })
      .then((r) => { if (!r.ok) throw new Error(r.status === 401 ? "令牌无效" : "HTTP " + r.status); });
    sessionStorage.setItem("yd.base", state.base);
    sessionStorage.setItem("yd.token", state.token);
    start();
  } catch (err) { showAuth("连接失败：" + err.message); }
};
boot();
