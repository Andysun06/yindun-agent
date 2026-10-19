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
  stats:     { title: "统计分析", sub: "趋势、类型分布、级别构成与端点活跃度（服务端聚合）" },
};
const SEV_LABEL = { info: "提示", warning: "警告", critical: "严重", security: "安全" };
const TYPE_LABEL = {
  tool_call: "工具调用", tool_result: "工具结果", llm_input: "模型输入", llm_output: "模型输出",
  privacy_sensitive: "隐私事件", access_control: "访问控制", session_start: "会话开始",
  session_end: "会话结束", workflow_step_executed: "工作流步骤", chain_restart: "链重启",
  control_report: "控制面上报",
};

// 自动刷新的作用范围（**行为不变**：默认开、10 秒一拍、只对这两页生效）。
// 提成常量是为了一处定义、三处引用（定时器 / 顶栏文案 / 页脚说明），
// 免得将来改了范围却留下一句过时的界面说明——那比没有说明更糟。
const AUTO_MS = 10000;
const AUTO_PAGES = ["overview", "endpoints"];

const state = {
  base: "", token: "", page: "overview", timer: null, endpoints: [],
  filters: { endpoint_id: "", event_type: "", severity: "", q: "" }, offset: 0, total: 0,
  // 策略页的"点选式"状态：presets 走 DOM（复选框），模型/插件因含"手动补一个"而需记住
  policyPick: { models: [], plugins: [] }, policyCands: { models: new Map(), plugins: new Map() },
  lastDispatch: null,                       // 上次逐台下发的结果（成功/失败都要如实显示）
  lastRenderAt: 0, autoNextAt: 0, tick: null,
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
// 服务端时间戳是**本地朴素时间**（无时区后缀），这里产出同格式的字符串，
// 让 mock 与真实后端的数据长得一模一样（Date.parse 对无时区字符串按本地时间解释）。
const isoLocal = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000)
  .toISOString().slice(0, 19);
function fmtCountdown(ms) {
  if (!(ms > 0)) return "已过期";
  const s = Math.floor(ms / 1000);
  return `剩 ${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}
function toast(msg, ms = 2600) {
  const el = $("toast");
  el.textContent = msg;
  el.hidden = false;
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.hidden = true; }, ms);
}
function setStatus(kind, text, note) {
  const el = $("status");
  el.className = "status" + (kind ? " is-" + kind : "");
  el.querySelector("em").textContent = text;
  // "已连接"三个字对管理员说明不了"连的是什么、连上意味着什么"，所以状态点旁边
  // 常显一句人话（窄窗口下由样式表隐藏，避免顶栏被挤开）。
  const noteEl = $("status-note");
  if (noteEl) noteEl.textContent = note || STATUS_NOTE[kind] || "";
}
// 状态点旁的常显说明：说清"这个连接是什么"，而不是让用户去猜。
const STATUS_NOTE = {
  live: "· 控制面在线，数据取自控制面本地汇总（不含明文、不代理模型流量）",
  err: "· 与控制面的连接断了：请确认控制面进程还在运行",
  "": "· 尚未连接：填好控制面地址与管理令牌后开始",
};
function sevBadge(sev) {
  const cls = ["critical", "security"].includes(sev) ? "bad" : (sev === "warning" ? "warn" : "idle");
  return `<span class="sev sev--${esc(sev)}">${esc(SEV_LABEL[sev] || sev)}</span>`;
}
function chainBadge(verdict) {
  const cls = verdict === "已锚定" ? "ok" : (verdict === "链异常" ? "bad" : "idle");
  return `<span class="badge badge--${cls}">${esc(verdict)}</span>`;
}

/* ── 自制下拉框 ─────────────────────────────────────────
   为什么保留原生 <select>：页面逻辑里到处是 `$("f-ep").value` 与 `el.onchange = fire`。
   把控件换成自定义结构就得把「取值」与「事件」全部改写，多改一处就多一处
   「界面显示的条件 ≠ 实际发出的请求」的风险。所以做法是：
     · 原生 select 留着（只做**值容器**），用 .selectnative 视觉隐藏；
     · 旁边渲染一个同尺寸的自绘按钮 + 弹层 + 列表（纯 CSS，无第三方库）；
     · 选中后写回 select.value 并派发**原生 change 事件** → 页面逻辑一行都不用改。
   ─────────────────────────────────────────────────────── */
const CARET = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>`;

function closeAllDropdowns(except) {
  document.querySelectorAll(".dd").forEach((dd) => {
    if (dd === except) return;
    dd.querySelector(".dd__list").hidden = true;
    dd.querySelector(".dd__btn").setAttribute("aria-expanded", "false");
  });
}

function enhanceSelect(sel) {
  sel.dataset.dd = "1";
  // .selectnative 负责"视觉退场"（透明铺满 .dd、不接鼠标），值语义一份不少
  sel.classList.add("selectnative");
  const dd = document.createElement("div");
  dd.className = "dd";
  const btn = document.createElement("button");
  btn.type = "button"; btn.className = "dd__btn";
  btn.setAttribute("aria-haspopup", "listbox");
  btn.setAttribute("aria-expanded", "false");
  const text = document.createElement("span");
  const list = document.createElement("ul");
  list.className = "dd__list"; list.hidden = true; list.setAttribute("role", "listbox");
  const sync = () => {
    const opt = sel.options[sel.selectedIndex];
    text.textContent = opt ? opt.textContent : "";
    list.querySelectorAll(".dd__opt").forEach((li) => {
      li.setAttribute("aria-selected", li.dataset.value === sel.value ? "true" : "false");
    });
  };
  const build = () => {
    list.innerHTML = Array.from(sel.options).map((o) =>
      `<li class="dd__opt" role="option" data-value="${esc(o.value)}">${esc(o.textContent)}</li>`).join("");
    sync();
  };
  const pick = (value) => {
    sel.value = value;
    sync(); close();
    // 原生事件：页面里的 el.onchange 照旧被触发，筛选/下发逻辑无需感知控件已被换皮
    sel.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const open = () => {
    closeAllDropdowns(dd);
    build(); list.hidden = false; btn.setAttribute("aria-expanded", "true");
    const cur = list.querySelector(".dd__opt[aria-selected='true']") || list.querySelector(".dd__opt");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  };
  const close = () => { list.hidden = true; btn.setAttribute("aria-expanded", "false"); };
  btn.onclick = (e) => { e.stopPropagation(); list.hidden ? open() : close(); };
  list.onclick = (e) => {
    const li = e.target.closest(".dd__opt");
    if (li) { e.stopPropagation(); pick(li.dataset.value); }
  };
  btn.onkeydown = (e) => {
    const opts = Array.from(list.querySelectorAll(".dd__opt"));
    const at = opts.findIndex((li) => li.classList.contains("is-active"));
    if (e.key === "Escape") { close(); return; }
    if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
      e.preventDefault();
      if (list.hidden) { open(); return; }
      const step = e.key === "ArrowUp" ? -1 : 1;
      if (e.key === "Enter" && at >= 0) { pick(opts[at].dataset.value); return; }
      const next = opts[Math.min(Math.max(at + step, 0), opts.length - 1)] || opts[0];
      opts.forEach((li) => li.classList.remove("is-active"));
      next.classList.add("is-active"); next.scrollIntoView({ block: "nearest" });
    }
  };
  sel.parentNode.insertBefore(dd, sel);
  btn.appendChild(text);
  btn.insertAdjacentHTML("beforeend", CARET);   // CARET 是本文件的静态字符串，不含任何外部数据
  dd.appendChild(btn);
  dd.appendChild(list); dd.appendChild(sel);
  build();
  // 程序化改值（如"重置筛选"）也要让按钮上的字跟着变
  sel.addEventListener("input", sync);
}

function enhanceSelects(root) {
  (root || document).querySelectorAll("select.select").forEach((sel) => {
    if (!sel.dataset.dd) enhanceSelect(sel);
  });
}
document.addEventListener("click", () => closeAllDropdowns());

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
  // 桌面外壳（双击 exe 开窗口）会把令牌放在 fragment 带进来：本机窗口免手抄。
  // 用 fragment 而非 query 是为了不进服务器日志、不随 Referer 外发；
  // 取到后立刻从地址栏抹掉，只留在 sessionStorage（关窗即失效）。
  if (!state.token && location.hash.startsWith("#token=")) {
    try {
      const fromUrl = decodeURIComponent(location.hash.slice("#token=".length));
      if (fromUrl) {
        state.token = fromUrl;
        state.base = location.origin;
        sessionStorage.setItem("yd.token", fromUrl);
        sessionStorage.setItem("yd.base", location.origin);
        history.replaceState(null, "", location.pathname);
      }
    } catch (e) { /* 解析失败就退回手输，不静默 */ }
  }
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
  if ($("btn-export")) $("btn-export").onclick = async () => {
    // 带鉴权 fetch 后再触发下载：令牌不进 URL（不进历史与日志）
    try {
      const res = await fetch(state.base + "/api/v1/export?format=csv",
        { headers: { Authorization: "Bearer " + state.token } });
      if (!res.ok) throw new Error("HTTP " + res.status);
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (res.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)"?/)?.[1] || "yindun-audit.csv";
      document.body.appendChild(a); a.click(); a.remove();
      toast("已导出掩码审计（CSV，Excel 可直接打开）");
    } catch (e) { toast("导出失败：" + e.message); }
  };
  $("btn-theme").onclick = toggleTheme;
  $("auto-refresh").onchange = (e) => { e.target.checked ? startTimer() : stopTimer(); };
  applyTheme(localStorage.getItem("yd.theme") || "light");
  go("overview");
  startTimer();
  if (!state.tick) state.tick = setInterval(paintRefreshStatus, 1000);   // 页脚倒计时（不发请求）
  paintRefreshStatus();
}
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  applyTheme(next); localStorage.setItem("yd.theme", next);
}
function applyTheme(t) { document.documentElement.dataset.theme = t; }
/* ── 自动刷新：行为不变，只是**能看懂了** ─────────────────
   负责人问："顶部这个开关和'自动'是什么意思"——它是一个每 10 秒拉一次数据的开关，
   但原来说明只藏在 tooltip 里（要悬停才知道），而且"对哪些页生效"没有任何说明。
   现在的做法（行为一个字节都没改：默认开、10 秒、只对总览与端点资产生效）：
     · 开关文字写成「自动刷新 10 秒 / 自动刷新 关」；
     · 页脚常显"上次刷新 / 下一次刷新的时间"（当前页不生效时如实说明"本页不自动刷新"）。
   ─────────────────────────────────────────────────────── */
function autoCovers(page) { return AUTO_PAGES.includes(page || state.page); }
function startTimer() {
  stopTimer();
  state.autoNextAt = Date.now() + AUTO_MS;
  state.timer = setInterval(() => {
    state.autoNextAt = Date.now() + AUTO_MS;
    if (autoCovers()) render();              // 只对总览/资产页拉数据（现状不变）
    else paintRefreshStatus();               // 其它页只更新页脚说明，不发请求
  }, AUTO_MS);
  paintRefreshStatus();
}
function stopTimer() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null; state.autoNextAt = 0;
  paintRefreshStatus();
}
function paintAutoLabel() {
  const el = $("auto-refresh-text");
  const box = $("auto-refresh");
  if (el) el.textContent = box && box.checked ? "自动刷新 10 秒" : "自动刷新 关";
  if (box) box.title = `每 ${AUTO_MS / 1000} 秒自动刷新当前页；只对「态势总览」「端点资产」生效，`
    + "其它页面保持不动。关掉它不影响手动刷新（R 或点刷新按钮）。";
}
function paintRefreshStatus() {
  paintAutoLabel();
  const el = $("foot-stamp");
  if (!el) return;
  const stamp = state.lastRenderAt ? new Date(state.lastRenderAt).toLocaleTimeString("zh-CN") : "—";
  let tail;
  if (!state.timer) tail = "自动刷新已关（点「刷新」或按 R 可手动拉取）";
  else if (!autoCovers()) tail = "本页不自动刷新（只对态势总览、端点资产生效）";
  else {
    const left = Math.max(0, Math.round((state.autoNextAt - Date.now()) / 1000));
    tail = `下一次 ${new Date(state.autoNextAt).toLocaleTimeString("zh-CN")}（约 ${left} 秒后）`;
  }
  el.textContent = `${MOCK ? "预览数据 · " : ""}上次刷新 ${stamp} · ${tail}`;
}

function bindKeys() {
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.metaKey || e.ctrlKey) return;
    // 自绘下拉框展开时，↑↓/Enter/数字键都属于它，不能顺带触发"1..6 切页"
    if (e.target.closest && e.target.closest(".dd")) return;
    if (e.key === "/") { e.preventDefault(); const box = $("f-q"); if (box) box.focus(); }
    if (e.key === "r") render(true);
    const page = Object.keys(PAGES)[Number(e.key) - 1];
    if (page) go(page);
  });
}

/* ── 窗口按钮（只有无边框桌面窗口里才有桥）──────────────
   桥是 pywebview 注入的 window.pywebview.api（见 yindun/control/desktop.py · WindowBridge）。
   拿不到桥就**整组隐藏**：浏览器里这些按钮点了没反应，留着比没有更糟。 */
function bindWindowControls() {
  const groups = document.querySelectorAll(".winctrl");
  if (!groups.length) return;
  // 按钮上的 data-win 是短标签（top/min/max/close），桥认的是动作名——这里显式映射。
  // 不做"直接转发 data-win"，否则桥收到 "min" 只认识 "minimize"，会当成未知动作拒绝。
  // 四个按钮的顺序与客户端一致：置顶 / 最小化 / 最大化 / 关闭（V3.6.7）。
  const ACTION = { top: "toggle_topmost", min: "minimize", max: "toggle_maximize", close: "close" };
  const wait = setInterval(() => {
    if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.window_action) return;
    clearInterval(wait);
    groups.forEach((g) => { g.hidden = false; });
    // 拖动区类名 = pywebview 官方的 .pywebview-drag-region（DRAG_REGION_SELECTOR 默认值）。
    // 早先用的是自造类名 .pwdrag：pywebview 压根不认，顶栏拖不动（负责人实测）。
    const BARS = ".pywebview-drag-region";
    // 桌面窗口里拖动区不该顺带选中顶栏文字（浏览器里保留正常文本选择）
    document.querySelectorAll(BARS).forEach((bar) => { bar.style.userSelect = "none"; });
    const maxBtn = document.querySelector('.winctrl__btn[data-win="max"]');
    const topBtns = document.querySelectorAll('.winctrl__btn[data-win="top"]');
    // 图标状态一律以**桥返回的真实状态**为准（不靠"点一下就翻转"记账）：
    // 最大化→切还原图标；置顶→按钮点亮（实际窗口不在最前面时绝不能显示成已置顶）。
    const paint = (st) => {
      if (!st) return;
      if (maxBtn) maxBtn.classList.toggle("is-max", !!st.maximized);
      topBtns.forEach((b) => b.classList.toggle("is-on", !!st.topmost));
    };
    // 启动时把本窗口的置顶偏好**应用一次**（记在本地存储里：控制台窗口每次都是新开的，
    // 不恢复的话用户每次都得重新点）。偏好只在拿到桥之后应用，浏览器形态下自然跳过。
    try {
      const want = localStorage.getItem("yd.topmost") === "1";
      if (want) window.pywebview.api.window_action("toggle_topmost").then(paint).catch(() => {});
    } catch (e) { /* 存储不可用时按未置顶处理，不影响其它按钮 */ }
    document.querySelectorAll(".winctrl__btn").forEach((b) => {
      b.onclick = async (e) => {
        e.stopPropagation();
        const act = ACTION[b.dataset.win];
        if (!act) return;                    // 未知按钮不转发（前端多一个按钮不该等于桥多一个能力）
        try {
          const st = await window.pywebview.api.window_action(act);
          paint(st);
          // 置顶是"窗口级偏好"，记进本地存储，下次打开控制台自动恢复（失败的不记）
          if (act === "toggle_topmost" && st && st.ok) {
            try { localStorage.setItem("yd.topmost", st.topmost ? "1" : "0"); } catch (e) { /* 忽略 */ }
          }
        } catch (err) { toast("窗口操作失败：" + (err && err.message ? err.message : err)); }
      };
    });
    // 双击顶栏 = 最大化/还原（Windows 用户的肌肉记忆，缺了会被当成"窗口卡住"）
    document.querySelectorAll(BARS).forEach((bar) => {
      bar.ondblclick = (e) => {
        if (e.target.closest(".winctrl, button, input, .switch, a")) return;
        window.pywebview.api.window_action("toggle_maximize").then(paint).catch(() => {});
      };
      // pywebview 的拖动是挂在 body 上的 mousedown 监听 + 从事件目标向上找拖动区：
      // 点在按钮上也会被判定成"按住顶栏"，于是点「刷新/配对码」时窗口会跟着鼠标走。
      // 拖动区内**交互控件**上的 mousedown 就地拦下（只影响 pywebview 的这条监听，
      // 不拦 click，按钮照常工作）。
      bar.addEventListener("mousedown", (e) => {
        if (e.target.closest("button, input, .switch, a, select, textarea")) e.stopPropagation();
      });
    });
  }, 120);
}

/* ── 配对码浮窗（V3.6.7）─────────────────────────────────
   负责人要求：配对码要能"拿到手、转述出去"，所以是模态浮窗而不是一条 9 秒的 toast。
   对接的接口（未就绪/未知时逐项降级成可读提示，绝不静默失败）：
     GET  /api/v1/pair-code  → {code, expires_at, ttl_minutes, custom}
     POST /api/v1/pair-code   {ttl_minutes?, note?, code?}   （带 code 即自定义，4–12 位 [A-Za-z0-9-]）
     GET  /api/v1/network    → {listen_host, listen_port, external, lan_urls, primary_url, firewall_hint}
     POST /api/v1/listen      {host:"127.0.0.1"|"0.0.0.0"} → {ok, listen_host, lan_urls, note}
   ─────────────────────────────────────────────────────── */
const PAIR_TTL_MINUTES = 30;
const CODE_RE = /^[A-Za-z0-9-]{4,12}$/;
let pairTimer = null;

function pairMsg(text, kind = "") {
  const el = $("pair-msg");
  if (!el) return;
  el.hidden = !text;
  el.className = "pair__note" + (kind ? " pair__note--" + kind : "");
  el.textContent = text || "";
}

function paintPairCode(data) {
  if (pairTimer) { clearInterval(pairTimer); pairTimer = null; }
  const ok = !!(data && data.code);
  $("pair-code").textContent = ok ? String(data.code) : "—";
  // 码旁边的小标签：服务端给的 state（可用/已过期/无可用配对码）+ 自定义标记
  const chips = [];
  if (ok && data.custom) chips.push("自定义");
  if (data && data.state) chips.push(String(data.state));
  $("pair-state").hidden = !chips.length;
  $("pair-state").textContent = chips.join(" · ");
  const paintMeta = () => {
    if (!ok) { $("pair-meta").textContent = (data && data.state) || "还没有可用配对码"; return; }
    if (!data.expires_at) { $("pair-meta").textContent = "有效期未知（服务端未给到期时间）"; return; }
    const left = Date.parse(String(data.expires_at).replace(" ", "T")) - Date.now();
    $("pair-meta").textContent = `到期 ${fmtTime(data.expires_at)} · ${fmtCountdown(left)}`;
  };
  paintMeta();
  if (ok && data.expires_at) pairTimer = setInterval(paintMeta, 1000);
}

async function loadPairCode() {
  try {
    const data = await api("/api/v1/pair-code");
    paintPairCode(data);
    // 有码但为空（服务端 state="无可用配对码"）也要说人话，不能只显示一个「—」
    pairMsg(data && data.code ? ""
      : "当前没有有效配对码：点「重新生成」生成一个（一次性、用后作废），"
        + "再把它和上面的地址一起给对方。");
  } catch (e) {
    paintPairCode(null);
    pairMsg(`配对码接口不可用：${e.message}。`
      + "若控制面是较早版本，可直接点「重新生成」再试；生成不了也不影响已有端点上报。", "bad");
  }
}

function paintNetwork(net) {
  const urls = net.lan_urls || [];
  const url = net.primary_url || urls[0] || "";
  const box = $("pair-url");
  box.textContent = url || "—";
  box.dataset.url = url;                       // 复制按钮取这里（文本被 ellipsis 截断也不影响复制）
  $("pair-lan").checked = !!net.external;
  $("pair-listen").textContent = net.external
    ? `对外监听 ${net.listen_host}:${net.listen_port}`
    : `仅本机 ${net.listen_host}:${net.listen_port}`;
  $("pair-lan-note").textContent = net.external
    ? (urls.length > 1 ? `局域网地址：${urls.join("　")}` : "局域网内的机器可用上面的地址打开控制台。")
    : "当前只有本机能访问；开启开关后局域网内可访问。";
  if (net.firewall_hint) $("pair-fw").textContent = net.firewall_hint;
}

async function loadNetwork() {
  try {
    paintNetwork(await api("/api/v1/network"));
  } catch (e) {
    $("pair-url").textContent = "—（读不到监听状态）";
    $("pair-url").dataset.url = "";
    $("pair-listen").textContent = "未知";
    $("pair-lan").checked = false;
    $("pair-lan").disabled = true;
    $("pair-fw").textContent = `监听状态接口不可用：${e.message}。`
      + "可先确认控制面监听的端口，本机浏览器直接打开 http://127.0.0.1:<端口>/。";
  }
}

async function toggleLan(e) {
  const on = e.target.checked;
  e.target.disabled = true;
  try {
    const res = await api("/api/v1/listen", { method: "POST", body: { host: on ? "0.0.0.0" : "127.0.0.1" } });
    if (res && res.ok === false) throw new Error(res.note || "监听地址未改变");
    await loadNetwork();                       // 以服务端回报为准（不自作主张改界面）
    pairMsg(res.note || (on ? "已切换为对外监听：首次会弹 Windows 防火墙询问，请允许。"
                            : "已改回仅本机监听。"));
  } catch (err) {
    e.target.checked = !on;                    // 切失败就退回真实状态，不做假切换
    pairMsg(`切换监听地址失败：${err.message}`, "bad");
  } finally {
    e.target.disabled = false;
  }
}

async function copyText(text, label) {
  const value = String(text || "").trim();
  if (!value || value === "—") return toast("没有可复制的内容");
  try {
    if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error("no-clipboard");
    await navigator.clipboard.writeText(value);
    toast(`${label}已复制到剪贴板`);
  } catch (e) {
    // 降级：剪贴板 API 在非安全上下文/被拒时不可用，退到"选中 + execCommand"，
    // 再不行就明确告诉用户手工复制（绝不谎报"已复制"）。
    let ok = false;
    const tmp = document.createElement("textarea");
    tmp.value = value;
    tmp.setAttribute("readonly", "");
    tmp.style.position = "fixed";
    tmp.style.opacity = "0";
    document.body.appendChild(tmp);
    tmp.select();
    try { ok = document.execCommand("copy"); } catch (err) { ok = false; }
    tmp.remove();
    toast(ok ? `${label}已复制到剪贴板` : `${label}剪贴板不可用：已选中文本，请按 Ctrl+C`);
  }
}

function openPairModal() {
  $("pair-modal").hidden = false;
  $("pair-custom").hidden = true;
  pairMsg("");
  loadPairCode();
  loadNetwork();
  const btn = $("pair-copy");
  if (btn && btn.focus) btn.focus();
}

function closePairModal() {
  $("pair-modal").hidden = true;
  if (pairTimer) { clearInterval(pairTimer); pairTimer = null; }
}

async function submitPairCode(payload, okMsg) {
  try {
    const res = await api("/api/v1/pair-code", { method: "POST", body: payload });
    // 真实后端的 POST 回包不带 custom（只有 GET 带），自定义标记按请求体自己补上，
    // 否则"刚提交的自定义码"在界面上会被当成随机码。
    paintPairCode(Object.assign({}, res, { custom: !!payload.code || !!(res && res.custom) }));
    pairMsg(okMsg);
    return true;
  } catch (e) {
    pairMsg(`生成失败：${e.message}`, "bad");
    return false;
  }
}

function bindPairModal() {
  if (!$("pair-modal")) return;
  $("btn-pair-code").onclick = openPairModal;
  $("pair-close").onclick = closePairModal;
  $("pair-modal").querySelector(".modal__scrim").onclick = closePairModal;
  $("pair-regen").onclick = () => submitPairCode(
    { ttl_minutes: PAIR_TTL_MINUTES, note: "控制台生成" },
    `已生成新码，${PAIR_TTL_MINUTES} 分钟内有效、用后作废。`);
  $("pair-custom-toggle").onclick = () => {
    const form = $("pair-custom");
    form.hidden = !form.hidden;
    if (!form.hidden) $("pair-custom-code").focus();
  };
  $("pair-custom-cancel").onclick = () => { $("pair-custom").hidden = true; pairMsg(""); };
  $("pair-custom").onsubmit = async (e) => {
    e.preventDefault();
    const code = $("pair-custom-code").value.trim().toUpperCase();
    if (!CODE_RE.test(code)) {
      pairMsg("自定义码只能是 4–12 位字母、数字或连字符。", "bad");
      return;
    }
    if (await submitPairCode({ code, ttl_minutes: PAIR_TTL_MINUTES, note: "控制台自定义" },
                             `已改用自定义码 ${code}。`)) {
      $("pair-custom").hidden = true;
      $("pair-custom-code").value = "";
    }
  };
  $("pair-copy").onclick = () => copyText($("pair-code").textContent, "配对码 ");
  $("pair-copy-url").onclick = () => copyText($("pair-url").dataset.url, "控制台地址 ");
  $("pair-lan").onchange = toggleLan;
  // Esc 关浮窗（输入框里的 Esc 交给输入框自身，不误关）
  document.addEventListener("keydown", (e) => {
    const open = !$("pair-modal").hidden;
    if (open && e.key === "Escape" && !(e.target.closest && e.target.closest(".dd"))) closePairModal();
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
    else if (state.page === "stats") await renderStats(box);
    else await renderUsage(box);
    // ★ 页面归属记账必须**每个页面都写**（V3.6.7 修真实缺陷）：此前只有事件台账在渲染时
    //   写了 dataset.page，别的页面不写 → "事件台账 → 端点资产 → 回事件台账"时它以为旧结构还在、
    //   跳过重建，直接去操作已经不存在的元素 → 空指针（实测报错：
    //   加载失败：Cannot set properties of null (setting 'textContent')）。
    box.dataset.page = state.page;
    box.dataset.painted = "1";
    enhanceSelects(box);      // 每次重建页面后接管下拉框（原生 select 仍是值容器）
    setStatus("live", MOCK ? "预览数据" : "已连接");
    state.lastRenderAt = Date.now();
    paintRefreshStatus();     // 页脚：上次刷新时间 + 下一次刷新/本页不自动刷新（如实说明）
  } catch (e) {
    if (e.unauthorized) return showAuth("令牌无效或已过期，请重新输入");
    setStatus("err", "连接异常");
    box.innerHTML = `<div class="err-box">加载失败：${esc(e.message)}</div>`;
    paintRefreshStatus();
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
          <td>
            <span class="badge badge--${r.online ? "ok" : "idle"}">${r.online ? "在线" : "失联"}</span>
            ${chainBadge(r.chain_verdict)}${r.chain_alerts ? ` <span class="chip">${r.chain_alerts} 告警</span>` : ""}
            ${r.drift && r.drift !== "已同步" ? ` <span class="chip" title="控制面已下发新策略，端点尚未取走">${esc(r.drift)}</span>` : ""}
          </td>
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
async function renderEvents(box, force = false) {
  const f = state.filters;
  if (force || !box.dataset.painted || box.dataset.page !== "events") {
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
  if (!$("events-host") || !$("f-total")) {
    // 页面结构已不在（切走又切回、或别的页面覆盖了 content）→ 重建后再加载，
    // 而不是对着 null 设 textContent 崩掉。
    await renderEvents($("content"), true);
    return;
  }
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

/* ── 页面：策略下发（点选式；负责人反馈"控制方式学习成本太高了"）──────
   五处自由文本/单选改成点选：预设复选框、三档分段、多选 chip、多选端点、折叠说明区。
   **语义一点都没变**（这是安全策略，改的是表达方式、不是行为），逐条对照：

     · 敏感路径：预设勾选＝**替用户把 glob 写出来**，展开成同样的字符串数组，走同一条
       `bundle.sensitive_paths` 通路 → 服务端/内核零改动，不可能引入新语义；
       "自定义路径（高级）"折叠区保留原有多行 glob 输入能力，两者可并用（并集）；
     · 审批下限：仍只有 ""/confirm/deny 三个值（不改变 / 至少人工确认 / 一律拒绝）；
     · 允许模型 / 插件：候选来自端点上报的用量，另留"手动补一个"入口（原来逗号分隔能填的
       名字，现在同样能填）。**一个都不选＝本条不下发**——不写成空数组，因为空数组在端点
       是"全部禁用"的收紧语义，界面不擅自替用户引入；
     · 下发范围：后端一次只收一个 scope（`{scope, bundle}`，scope 为 "global" 或端点 id），
       多选端点时前端**逐台**签发并如实汇总（成功 N 台 / 失败清单，绝不只报成功）；
     · 提交体仍然**只带真选了的键**（没选的键一个都不发）。

   ★ 界面展示什么、提交什么，都出自同一个 `policyPreview()`：杜绝"屏幕上写的"与
     "实际发出去的"不是一回事——那是安全策略界面最不能犯的错。
   ─────────────────────────────────────────────────────── */

// 关键词 →〔任意盘符 + UNC 共享〕两种写法。内核用 fnmatch（`*` 连子目录一起命中，
// 路径先做 `\`→`/` 归一），所以 `*:/薪资*` 同时盖住 D:/薪资/10月/工资表.xlsx 与
// D:/2026/薪资说明.md；`//*/合同*` 盖住 \\fileserver\share\合同\… 这类企业共享。
const pathGlobs = (kw) => [`*:/${kw}*`, `//*/${kw}*`];

const PATH_PRESETS = [
  { group: "人事与薪酬", items: [
    { id: "payroll", label: "薪酬 / 工资表",
      effect: "命中工资单、奖金、个税等薪酬材料：这些路径下的读取或写入都要人工审批。",
      globs: [...pathGlobs("薪资"), ...pathGlobs("工资"), ...pathGlobs("薪酬"), ...pathGlobs("奖金")] },
    { id: "hr", label: "人事档案 / 简历",
      effect: "命中人事档案、员工资料与简历：涉及员工个人信息的目录都要人工审批。",
      globs: [...pathGlobs("人事"), ...pathGlobs("简历"), ...pathGlobs("员工档案")] },
  ]},
  { group: "财务与合同", items: [
    { id: "finance", label: "财务 / 报销",
      effect: "命中财务、报销、发票与税务材料：这类目录下的操作都要人工审批。",
      globs: [...pathGlobs("财务"), ...pathGlobs("报销"), ...pathGlobs("发票"), ...pathGlobs("税务")] },
    { id: "contract", label: "合同 / 法务",
      effect: "命中合同与法务材料：合同文本的读取或改写都要人工审批。",
      globs: [...pathGlobs("合同"), ...pathGlobs("法务")] },
  ]},
  { group: "客户与经营", items: [
    { id: "customer", label: "客户资料 / 供应商",
      effect: "命中客户、供应商与报价资料：客户与合作伙伴数据都要人工审批。",
      globs: [...pathGlobs("客户"), ...pathGlobs("供应商"), ...pathGlobs("报价")] },
  ]},
  { group: "研发与密钥", items: [
    { id: "source", label: "源代码 / 密钥材料",
      effect: "命中源码、私钥、密钥与凭据目录：研发资产与密钥材料都要人工审批。",
      globs: [...pathGlobs("源码"), ...pathGlobs("私钥"), ...pathGlobs("密钥"), ...pathGlobs("凭据")] },
  ]},
  { group: "医疗与证件", items: [
    { id: "medical", label: "病历 / 健康",
      effect: "命中病历、体检与健康记录：这类个人健康信息都要人工审批。",
      globs: [...pathGlobs("病历"), ...pathGlobs("体检"), ...pathGlobs("健康")] },
    { id: "idcard", label: "身份证 / 证件扫描件",
      effect: "命中身份证、护照与证件材料：证件类材料都要人工审批。",
      globs: [...pathGlobs("身份证"), ...pathGlobs("证件"), ...pathGlobs("护照")] },
  ]},
];
// 三档即内核 APPROVAL_RANK 的三个语义（approve 是"自动放行"，控制面**无权**下发，
// 所以这里没有这一档）：值域与原下拉框逐字相同。
const FLOOR_OPTIONS = [
  { value: "", label: "不改变", desc: "本条不下发审批下限，端点保持现状" },
  { value: "confirm", label: "至少人工确认", desc: "confirm：每次操作都要人工点确认后才能继续" },
  { value: "deny", label: "一律拒绝", desc: "deny：应急熔断，所有操作直接拒绝" },
];
const FLOOR_LABEL = { confirm: "至少人工确认（confirm）", deny: "一律拒绝（deny）" };

function presetGlobs(ids) {
  const out = [];
  PATH_PRESETS.forEach((g) => g.items.forEach((it) => {
    if (ids.includes(it.id)) it.globs.forEach((p) => out.push(p));
  }));
  return out;
}
function pickedPresetIds() {
  return Array.from(document.querySelectorAll("#p-presets input[data-preset]:checked"))
    .map((el) => el.dataset.preset);
}
function customPaths() {
  const el = $("p-adv-paths");
  return el ? el.value.split("\n").map((s) => s.trim()).filter(Boolean) : [];
}
function pickedList(kind) { return (state.policyPick[kind] || []).slice(); }

/** 把各端点上报的能力清单汇成候选：值 → 哪些端点用过/启用了几台。
 *  候选值原样来自服务端（渲染时一律 esc()），前端只做去重与计数。 */
function policyCandidates(eps) {
  const models = new Map();
  const plugins = new Map();
  (eps || []).forEach((e) => {
    const who = e.name || e.endpoint_id;
    const ext = new Set(e.external_models || []);
    new Set([...(e.models || []), ...(e.external_models || [])]).forEach((raw) => {
      const name = String(raw || "").trim();
      if (!name) return;
      if (!models.has(name)) models.set(name, { who: [], external: false });
      const row = models.get(name);
      if (!row.who.includes(who)) row.who.push(who);
      if (ext.has(raw)) row.external = true;   // 公网模型：允许它＝允许出网调用，界面上标出来
    });
    (e.plugins || []).forEach((p) => {
      const id = String((p && p.id) || "").trim();
      if (!id) return;
      if (!plugins.has(id)) plugins.set(id, { who: [], enabled: 0, version: "" });
      const row = plugins.get(id);
      if (!row.who.includes(who)) row.who.push(who);
      if (p && p.enabled) row.enabled += 1;
      if (p && p.version) row.version = String(p.version);
    });
  });
  return { models, plugins };
}

/** 本次要下发什么：界面展示与提交体都取这里（唯一口径）。 */
function policyPreview() {
  const ids = pickedPresetIds();
  const paths = presetGlobs(ids).concat(customPaths());
  const floorEl = document.querySelector('input[name="p-floor"]:checked');
  const globalEl = $("p-scope-global");
  const globalOn = !!(globalEl && globalEl.checked);
  const eps = Array.from(document.querySelectorAll(".p-scope-ep:checked"))
    .map((el) => ({ scope: el.value, label: el.dataset.label || el.value }));
  const targets = globalOn ? [{ scope: "global", label: "全部端点（全局策略）" }] : eps;
  return {
    presetIds: ids, paths, floor: floorEl ? floorEl.value : "",
    models: pickedList("models"), plugins: pickedList("plugins"),
    strict: !!($("p-strict") && $("p-strict").checked),
    globalOn, targets,
    scopeText: globalOn ? "全部端点（全局策略）"
      : (targets.length ? `逐台下发：${targets.map((t) => t.label).join("、")}（共 ${targets.length} 台）`
                        : "未选择"),
  };
}

function paintPolicyPreview() {
  const box = $("p-preview");
  if (!box) return;
  const pv = policyPreview();
  const chips = (arr, empty) => arr.length
    ? arr.map((v) => `<code>${esc(v)}</code>`).join("")
    : `<em>${esc(empty)}</em>`;
  const rows = [
    `<b>敏感路径</b>${pv.paths.length
        ? `共 <b>${pv.paths.length}</b> 条：${chips(pv.paths, "")}`
        : `<em>未选（本条不下发）</em>`}`,
    `<b>审批下限</b>${pv.floor ? esc(FLOOR_LABEL[pv.floor] || pv.floor) : "<em>不改变</em>"}`,
    `<b>允许模型</b>${pv.models.length ? `共 ${pv.models.length} 个：${chips(pv.models, "")}`
                                        : "<em>未选（本条不下发）</em>"}`,
    `<b>允许插件</b>${pv.plugins.length ? `共 ${pv.plugins.length} 个：${chips(pv.plugins, "")}`
                                        : "<em>未选（本条不下发）</em>"}`,
    `<b>严格模式</b>${pv.strict ? "强制开启" : "<em>保持现状</em>"}`,
    `<b>下发范围</b>${esc(pv.scopeText)}`,
  ];
  box.innerHTML = rows.map((r) => `<div class="preview__row">${r}</div>`).join("");
  const cnt = $("p-path-count");
  if (cnt) {
    cnt.textContent = pv.presetIds.length
      ? `已选 ${pv.presetIds.length} 个场景 · ${pv.paths.length} 条`
      : (pv.paths.length ? `仅自定义 · ${pv.paths.length} 条` : "未选");
  }
  const btn = $("p-submit");
  if (btn) {
    btn.textContent = pv.globalOn ? "签发并下发（全部端点）"
      : (pv.targets.length ? `逐台签发并下发（${pv.targets.length} 台）` : "签发并下发");
  }
}

function presetLibraryHtml() {
  return PATH_PRESETS.map((g) => {
    const n = g.items.reduce((sum, it) => sum + it.globs.length, 0);
    return `<div class="presets__group">
      <div class="presets__gt">${esc(g.group)}<em>每个场景展开 4–8 条 glob，本组共 ${n} 条</em></div>
      ${g.items.map((it) => `
        <label class="preset">
          <input type="checkbox" data-preset="${esc(it.id)}">
          <span class="preset__body">
            <b>${esc(it.label)}</b>
            <span class="preset__effect">${esc(it.effect)}</span>
            <span class="preset__globs">${it.globs.map((p) => `<code>${esc(p)}</code>`).join("")}</span>
          </span>
        </label>`).join("")}
    </div>`;
  }).join("");
}

function pickAreaHtml(kind) {
  const cands = state.policyCands[kind] || new Map();
  const picked = pickedList(kind);
  const rows = Array.from(cands.entries()).sort((a, b) =>
    b[1].who.length - a[1].who.length || String(a[0]).localeCompare(String(b[0])));
  if (!rows.length) {
    return `<p class="hint">还没有端点上报${kind === "models" ? "模型" : "插件"}清单：
      可以先在下面手动补，或等端点下一次上报后再来点选。</p>`;
  }
  return `<div class="picks">${rows.map(([name, info]) => `
    <button type="button" class="pick" data-pick="${kind}" data-value="${esc(name)}"
            aria-pressed="${picked.includes(name) ? "true" : "false"}"
            title="来自：${esc(info.who.join("、"))}">
      <span>${esc(name)}</span>
      ${kind === "models" && info.external ? `<em class="pick__tag">外部</em>` : ""}
      <em>${kind === "models" ? `${info.who.length} 台用过` : `${info.enabled}/${info.who.length} 台启用`}</em>
    </button>`).join("")}</div>`;
}

function pickedAreaHtml(kind) {
  const picked = pickedList(kind);
  const what = kind === "models" ? "模型" : "插件";
  const body = picked.length
    ? picked.map((v) => `<span class="picked__item">${esc(v)}<button type="button" class="picked__x"
        data-unpick="${kind}" data-value="${esc(v)}" aria-label="移除 ${esc(v)}"
        title="从本次允许集里移除">×</button></span>`).join("")
    : `<span class="hint">一个都不选＝本条不下发（不会写成空数组——那在端点是"全部禁用"的收紧语义）。</span>`;
  return `<div class="picked"><span class="picked__lead">本次允许${what}（共 ${picked.length} 个）：</span>${body}</div>`;
}

function paintPicks(kind) {
  const host = $(kind === "models" ? "p-models-host" : "p-plugins-host");
  if (host) host.innerHTML = pickAreaHtml(kind) + pickedAreaHtml(kind);
}

function togglePick(kind, value) {
  const list = state.policyPick[kind];
  const at = list.indexOf(value);
  if (at >= 0) list.splice(at, 1); else list.push(value);
  paintPicks(kind); paintPolicyPreview();
}
function removePick(kind, value) {
  state.policyPick[kind] = state.policyPick[kind].filter((v) => v !== value);
  paintPicks(kind); paintPolicyPreview();
}

function bindPickArea(kind, hostId, inputId, addId) {
  const host = $(hostId);
  if (host) {
    // 事件挂在容器上（内容会整体重绘，元素级监听会随之丢失）
    host.onclick = (e) => {
      const pick = e.target.closest("button.pick");
      if (pick && pick.dataset.pick === kind) return togglePick(kind, pick.dataset.value);
      const x = e.target.closest("button.picked__x");
      if (x && x.dataset.unpick === kind) return removePick(kind, x.dataset.value);
    };
  }
  const input = $(inputId);
  const add = () => {
    if (!input) return;
    const v = (input.value || "").trim();
    if (!v) return toast("先填一个名字再点「加入」");
    if (v.length > 96) return toast("名字太长：服务端上限 96 字符");
    const list = state.policyPick[kind];
    if (list.includes(v)) { input.value = ""; return toast("已经在本次允许集里了"); }
    list.push(v); input.value = "";
    paintPicks(kind); paintPolicyPreview();
    toast(`已加入：${v}`);
  };
  if (input) input.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); add(); } };
  const btn = $(addId);
  if (btn) btn.onclick = add;
  paintPicks(kind);
}

function bindPolicyForm() {
  const redraw = () => paintPolicyPreview();
  document.querySelectorAll("#p-presets input[data-preset]").forEach((el) => { el.onchange = redraw; });
  const ta = $("p-adv-paths");
  if (ta) ta.oninput = redraw;
  document.querySelectorAll('input[name="p-floor"]').forEach((el) => { el.onchange = redraw; });
  const strict = $("p-strict");
  if (strict) strict.onchange = redraw;
  bindPickArea("models", "p-models-host", "p-models-add", "p-models-add-btn");
  bindPickArea("plugins", "p-plugins-host", "p-plugins-add", "p-plugins-add-btn");
  const g = $("p-scope-global");
  const epBoxes = Array.from(document.querySelectorAll(".p-scope-ep"));
  if (g) g.onchange = () => {
    // "全部端点"与逐台互斥：全局是"所有端点 + 以后新接入的"，与逐台混选会重复签发两种范围
    if (g.checked) epBoxes.forEach((el) => { el.checked = false; });
    redraw();
  };
  epBoxes.forEach((el) => {
    el.onchange = () => {
      if (el.checked && g) g.checked = false;
      // 逐台一个都不剩时回到默认档"全部端点"（界面上看得见，不会静默变成空范围）
      if (!el.checked && !epBoxes.some((x) => x.checked) && g) g.checked = true;
      redraw();
    };
  });
  $("p-submit").onclick = submitPolicy;
  // ★ 首屏必须把两个候选区画出来（V3.6.7 补）：`paintPicks` 原本只在"点选 / 移除 / 手动补"时才被调用，
  //   于是**刚进页面时两个候选区是空的**——整块"点选式"看起来像坏了（用户只看到两个空框）。
  //   候选来自已上报端点的实际用量，首屏就该看得见；预览也一并按当前状态画一次。
  paintPicks("models");
  paintPicks("plugins");
  redraw();
}

function dispatchResultHtml() {
  const r = state.lastDispatch;
  if (!r) return "";
  const bad = r.fail.length;
  return `<div class="block" id="p-result">
    <div class="block__head"><h3>上次下发结果</h3>
      <p>${bad ? `成功 ${r.ok.length} 台 / 失败 ${bad} 台 —— 失败的目标**没有**收到本次策略`
              : `成功 ${r.ok.length}/${r.total}${r.total > 1 ? " 台" : ""}，全部成功`}</p></div>
    <ul class="res-list">
      ${r.ok.map((x) => `<li class="res-list__ok">${esc(x.label)} → 已签发
        v${esc(x.version == null ? "—" : x.version)}</li>`).join("")}
      ${r.fail.map((x) => `<li class="res-list__bad">${esc(x.label)} → 失败：${esc(x.error)}</li>`).join("")}
    </ul>
    ${bad ? `<p class="hint">失败项可稍后重试；「策略历史」里只有成功那几次的版本记录。</p>` : ""}
  </div>`;
}

async function renderPolicy(box) {
  const [eps, pol] = await Promise.all([api("/api/v1/endpoints"), api("/api/v1/policy")]);
  state.endpoints = eps.endpoints || [];
  state.policyCands = policyCandidates(state.endpoints);
  state.policyPick = { models: [], plugins: [] };   // 每次进页面都是干净的（与旧版"下发后即清空"一致）
  box.innerHTML = `<section><div class="cols">
    <div>
      <div class="block">
        <div class="block__head"><h3>新策略</h3>
          <p>点选即可，不需要写表达式；一个都不选的项＝本条不下发</p></div>

        <div class="form-row">
          <label>追加敏感路径 <span class="chip" id="p-path-count">未选</span></label>
          <p class="hint">勾一个场景＝替你把对应 glob 写进策略，与端点本地规则取<b>并集</b>。</p>
          <div class="presets" id="p-presets">${presetLibraryHtml()}</div>
          <details class="fold" id="p-adv">
            <summary>自定义路径（高级）：确实要自己写 glob 时用
              <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary>
            <textarea class="textarea" id="p-adv-paths" spellcheck="false"
              placeholder="每行一条 glob，例如：&#10;D:/薪资/*&#10;//fileserver/share/合同/*"></textarea>
            <p class="hint">留空＝不加自定义条目；与上面勾选的场景取<b>并集</b>。
              <code>*</code> 连子目录一起命中（写法与内核一致）。</p>
          </details>
        </div>

        <div class="form-row">
          <label>审批下限</label>
          <div class="seg" id="p-floor" role="radiogroup" aria-label="审批下限">
            ${FLOOR_OPTIONS.map((o) => `
              <label class="seg__opt" title="${esc(o.desc)}">
                <input type="radio" name="p-floor" value="${esc(o.value)}"${o.value === "" ? " checked" : ""}>
                <span>${esc(o.label)}</span>
              </label>`).join("")}
          </div>
          <p class="hint">只能<b>提高</b>严格度：不改变 ＜ 至少人工确认 ＜ 一律拒绝；端点更严时保持端点设置。</p>
        </div>

        <div class="form-row">
          <label>允许使用的模型</label>
          <p class="hint">候选来自端点上报用量（括号＝几台用过；"外部"为公网模型）。
            点选＝加入允许集，与端点本地取<b>交集</b>。</p>
          <div class="pick-host" id="p-models-host"></div>
          <div class="inline manual">
            <input class="input" id="p-models-add" spellcheck="false"
                   placeholder="手动补一个（候选里没有的模型名也能填，回车或点「加入」）">
            <button class="btn btn--sm" type="button" id="p-models-add-btn">加入</button>
          </div>
        </div>

        <div class="form-row">
          <label>允许启用的插件</label>
          <p class="hint">同样取<b>交集</b>：不在列表内的插件在端点侧会被停用（只缩不扩）。</p>
          <div class="pick-host" id="p-plugins-host"></div>
          <div class="inline manual">
            <input class="input" id="p-plugins-add" spellcheck="false"
                   placeholder="手动补一个插件 id（回车或点「加入」）">
            <button class="btn btn--sm" type="button" id="p-plugins-add-btn">加入</button>
          </div>
        </div>

        <div class="form-row">
          <label class="switch"><input type="checkbox" id="p-strict"><span></span>
            <em>强制严格模式（端点禁用解释器，仅留只读命令）</em></label>
        </div>

        <div class="form-row">
          <label>下发范围（可多选）</label>
          <label class="scope scope--all"><input type="checkbox" id="p-scope-global" checked>
            <span><b>全部端点</b>（现有与以后新接入的机器都取到）</span></label>
          ${state.endpoints.length ? `
            <div class="scope__sep">或逐台下发（后端一次只接受一个范围，前端会<b>逐台</b>签发并汇总结果）</div>
            <div class="scope__grid">${state.endpoints.map((e) => `
              <label class="scope"><input type="checkbox" class="p-scope-ep" value="${esc(e.endpoint_id)}"
                data-label="${esc(e.name)}"><span>${esc(e.name)}<em>${esc(e.endpoint_id)}</em></span></label>`).join("")}
            </div>` : `<p class="hint">还没有端点接入：现在只能签发全局策略。</p>`}
        </div>

        <div class="form-row">
          <label for="p-note">备注</label>
          <input class="input" id="p-note" placeholder="例：Q4 合规要求，收紧薪资目录">
        </div>

        <div class="submit-row">
          <button class="btn btn--primary" id="p-submit">签发并下发（全部端点）</button>
          <span class="hint">下发前请核对下面「本次将下发」。</span>
        </div>
        <div class="preview" id="p-preview"></div>
        <p class="hint">本页只能<b>收紧</b>：路径取并集、审批取更严、允许集取交集；没有放宽 / 豁免 / 放行的通路。</p>
      </div>

      ${dispatchResultHtml()}

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
        <details class="denied-fold" id="p-denied">
          <summary>控制面无法下发的项：豁免 / 放行 / 白名单都不在通路里（6 类）
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg></summary>
          <div class="denied">
            <ul><li>anonymize</li><li>exempt</li><li>bypass</li><li>auto_approve</li>
              <li>whitelist</li><li>disable_audit</li></ul>
            <p>脱敏豁免、审批放行、白名单放行、审计开关属<b>内核判定权</b>，控制面没有任何通路可以下发。
               端点侧会拒收含这些键的策略包，并按"只能收紧"的单调规则合并——即使控制面被攻破，也只能让端点管得更严。</p>
          </div>
        </details>
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

  bindPolicyForm();
  paintPolicyPreview();
}

async function submitPolicy() {
  const pv = policyPreview();
  const bundle = {};                       // 只带真选了的键：没选＝本条不下发（与原实现一致）
  if (pv.paths.length) bundle.sensitive_paths = pv.paths;
  if (pv.floor) bundle.approval_floor = pv.floor;
  if (pv.models.length) bundle.allowed_models = pv.models;
  if (pv.plugins.length) bundle.allowed_plugins = pv.plugins;
  if (pv.strict) bundle.require_strict_mode = true;
  const note = ($("p-note").value || "").trim();
  if (note) bundle.note = note;
  if (!Object.keys(bundle).length) return toast("至少要选一项再下发（一个都不选＝什么都不发）");
  if (!pv.targets.length) return toast("请选择下发范围：全部端点，或至少勾选一台端点");

  const btn = $("p-submit");
  btn.disabled = true;
  const ok = [], fail = [];
  try {
    // 后端一次只接受一个 scope（{scope, bundle}），所以多选端点＝**逐台**签发。
    // 串行而不是并发：本地小服务串行更好读日志，失败清单的顺序也稳定。
    for (const t of pv.targets) {
      try {
        const res = await api("/api/v1/policy", { method: "POST", body: { scope: t.scope, bundle } });
        ok.push({ label: t.label, version: res && res.version, summary: res && res.summary });
      } catch (e) {
        fail.push({ label: t.label, error: (e && e.message) ? e.message : String(e) });
      }
    }
  } finally {
    btn.disabled = false;
  }
  state.lastDispatch = { ok, fail, total: pv.targets.length, at: Date.now() };
  // 提示条同样如实报数：有失败就绝不写成"下发成功"
  const brief = (ok[0] && ok[0].summary)
    ? " · " + Object.entries(ok[0].summary).map(([k, v]) => k + "：" + v).join("；") : "";
  if (pv.targets.length === 1 && ok.length === 1) {
    toast(`已签发 v${ok[0].version}（${pv.targets[0].label}）${brief}`, 4600);
  } else if (fail.length) {
    toast(`逐台下发完成：成功 ${ok.length} 台 / 失败 ${fail.length} 台`
      + `（失败：${fail.map((f) => f.label).join("、")}，详见结果区）`, 8000);
  } else {
    toast(`逐台下发完成：成功 ${ok.length}/${pv.targets.length} 台，全部成功${brief}`, 5200);
  }
  render(true);
}

/* ── 页面：统计分析（服务端聚合，界面只画）─────────────────
   为什么要有这一页：总览给的是"当下几个数"，但管理员真正要看的是**走向**——
   这周比上周多还是少、哪类事件在涨、哪台机器最活跃。图表全部手绘 SVG：
   控制台是零依赖原型，引一个图表库只为画柱子和横条不划算，且离线可用是本项目的底线。 */

function sparkBars(daily) {
  // 趋势：每日一根柱，高风险部分叠成第二种颜色。高度按最大值归一化（全 0 时给一个基线）
  const max = Math.max(1, ...daily.map((d) => d.count));
  const W = 100 / Math.max(1, daily.length);          // 百分比宽度，随容器自适应
  return `<div class="chart" role="img" aria-label="近 ${daily.length} 天事件趋势">
    ${daily.map((d, i) => {
      const h = Math.round((d.count / max) * 100);
      const hi = d.count ? Math.round((d.high / d.count) * h) : 0;
      return `<div class="chart__col" style="left:${(i * W).toFixed(3)}%;width:${W.toFixed(3)}%"
                   title="${d.date}：${d.count} 条（高风险 ${d.high} 条）">
        <span class="chart__bar" style="height:${h}%"><i style="height:${hi}%"></i></span>
      </div>`;
    }).join("")}
    <div class="chart__axis"><span>${daily[0] ? daily[0].date.slice(5) : ""}</span>
      <span>峰值 ${max} 条/天</span>
      <span>${daily.length ? daily[daily.length - 1].date.slice(5) : ""}</span></div>
  </div>`;
}

function hBars(items, labelFn, total) {
  // 横条排行：宽度按占最大项的比例（不是占总数的比例）——排行榜要看相对差距
  const max = Math.max(1, ...items.map((i) => i.count));
  return `<div class="hbars">${items.map((i) => `
    <div class="hbar">
      <span class="hbar__name" title="${esc(labelFn(i))}">${esc(labelFn(i))}</span>
      <span class="hbar__track"><i style="width:${Math.round((i.count / max) * 100)}%"></i></span>
      <span class="hbar__num">${num(i.count)}${total ? `<em>${Math.round((i.count / total) * 100)}%</em>` : ""}</span>
    </div>`).join("")}</div>`;
}

async function renderStats(box) {
  const [st, eps] = await Promise.all([api("/api/v1/stats?days=14"), api("/api/v1/endpoints")]);
  const daily = st.daily || [];
  const week = daily.slice(-7).reduce((a, d) => a + d.count, 0);
  const prevWeek = daily.slice(0, 7).reduce((a, d) => a + d.count, 0);
  const total = st.total_events || 0;
  const sev = st.by_severity || {};
  const highCount = (sev.critical || 0) + (sev.security || 0);
  const epName = {};
  (eps.endpoints || []).forEach((e) => { epName[e.endpoint_id] = e.name || e.endpoint_id; });
  const delta = prevWeek ? Math.round(((week - prevWeek) / prevWeek) * 100) : (week ? 100 : 0);

  box.innerHTML = `<section>
    <div class="metrics">
      <div class="metric"><div class="metric__label">近 7 天事件</div>
        <div class="metric__value">${num(week)}<small>条</small></div></div>
      <div class="metric ${delta > 0 ? "metric--alert" : "metric--ok"}">
        <div class="metric__label">环比上周</div>
        <div class="metric__value">${delta > 0 ? "+" : ""}${delta}<small>%</small></div></div>
      <div class="metric"><div class="metric__label">累计事件</div>
        <div class="metric__value">${num(total)}<small>条</small></div></div>
      <div class="metric ${highCount ? "metric--alert" : "metric--ok"}">
        <div class="metric__label">其中高风险</div>
        <div class="metric__value">${num(highCount)}<small>条</small></div></div>
    </div>

    <div class="block">
      <div class="block__head"><h3>事件趋势（近 ${st.days || 14} 天）</h3>
        <p>柱高＝当日事件数，深色段＝其中高风险；${st.first_event_at ? `数据自 ${esc(st.first_event_at.slice(0, 16))} 起` : "尚无数据"}</p></div>
      ${total ? sparkBars(daily) : emptyState("还没有事件", "端点上报后这里会出现趋势")}
    </div>

    <div class="two-col">
      <div class="block">
        <div class="block__head"><h3>事件类型分布</h3><p>按条数排序（前 12 类）</p></div>
        ${(st.by_type || []).length
          ? hBars(st.by_type, (i) => TYPE_LABEL[i.event_type] || i.event_type, total)
          : emptyState("还没有数据", "")}
      </div>
      <div class="block">
        <div class="block__head"><h3>级别构成</h3><p>提示 / 警告 / 严重（安全）</p></div>
        ${Object.keys(sev).length ? `<div class="sev-list">${Object.entries(sev)
          .sort((a, b) => b[1] - a[1]).map(([k, v]) => `
          <div class="sev-row"><span class="badge badge--${k === "critical" || k === "security" ? "bad"
            : (k === "warning" ? "warn" : "idle")}">${esc(SEV_LABEL[k] || k)}</span>
            <span class="hbar__track"><i class="sev-${esc(k)}" style="width:${total ? Math.round((v / total) * 100) : 0}%"></i></span>
            <span class="hbar__num">${num(v)}</span></div>`).join("")}</div>`
          : emptyState("还没有数据", "")}
      </div>
    </div>

    <div class="block">
      <div class="block__head"><h3>端点活跃度</h3><p>上报事件最多的工作站（前 10）</p></div>
      ${(st.top_endpoints || []).length ? `<div class="table-wrap"><table class="grid">
        <thead><tr><th>端点</th><th>标识</th><th class="num">事件数</th><th class="num">占比</th></tr></thead>
        <tbody>${st.top_endpoints.map((t) => `<tr>
          <td>${esc(epName[t.endpoint_id] || t.endpoint_id)}</td>
          <td class="mono">${esc(t.endpoint_id)}</td>
          <td class="num">${num(t.count)}</td>
          <td class="num">${total ? Math.round((t.count / total) * 100) : 0}%</td></tr>`).join("")}</tbody>
      </table></div>` : emptyState("还没有端点上报", "员工机在「设置 → 企业控制面」配对后即开始上报")}
    </div>
  </section>`;
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
      当前内核事件未标注模型名，故这里显示的是<b>端点级聚合</b>。
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
  stats: (() => {
    // 近 14 天样例趋势（含一个高峰日与两天零上报的周末，便于看图形是否可信）
    const counts = [12, 18, 9, 0, 0, 24, 31, 27, 44, 38, 0, 0, 29, 41];
    const daily = counts.map((c, i) => ({
      date: new Date(Date.now() - (counts.length - 1 - i) * 86400000).toISOString().slice(0, 10),
      count: c * 3, high: c > 35 ? 2 : (c > 20 ? 1 : 0),
    }));
    return {
      days: 14, daily,
      by_type: [{ event_type: "tool_call", count: 268 }, { event_type: "tool_result", count: 241 },
                { event_type: "llm_output", count: 186 }, { event_type: "llm_input", count: 174 },
                { event_type: "privacy_sensitive", count: 96 }, { event_type: "control_report", count: 62 },
                { event_type: "access_control", count: 31 }, { event_type: "workflow_step_executed", count: 18 }],
      by_severity: { info: 902, warning: 128, critical: 5, security: 1 },
      top_endpoints: [{ endpoint_id: "ep-fin-27", count: 216 }, { endpoint_id: "ep-hr-04", count: 132 },
                      { endpoint_id: "ep-dev-11", count: 70 }],
      total_events: 1036, first_event_at: "2026-10-03T09:14:22", last_event_at: "2026-10-16T09:11:30",
      generated_at: "2026-10-16T09:12:00",
    };
  })(),
  endpoints: { endpoints: [
    { endpoint_id: "ep-fin-27", name: "财务部-27号机", version: "3.4.6", platform: "Windows-11",
      last_seen_at: "2026-10-16T09:11:30", strict_mode: false, policy_version: 3, total_count: 216,
      online: true, last_report_at: "2026-10-16T09:11:30", policy_want: 3, policy_have: 3, drift: "已同步",
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
      online: false, last_report_at: "2026-10-16T08:58:41", policy_want: 3, policy_have: 2, drift: "待同步 v2→v3",
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
  "pair-code": (() => ({ code: "A1B2C3", ttl_minutes: 30, custom: false,
                         expires_at: isoLocal(new Date(Date.now() + 30 * 60000)) }))(),
  // 局域网接入（#mock）：地址用示例网段，开启开关后 primary_url 才变成局域网地址——
  // 与真实后端一致：监听在 127.0.0.1 时只有本机能访问。
  network: { listen_host: "127.0.0.1", listen_port: 8787, external: false,
             lan_urls: ["http://192.168.96.148:8787/", "http://10.0.4.21:8787/"],
             primary_url: "http://127.0.0.1:8787/",
             firewall_hint: "首次开启时 Windows 会弹出防火墙询问，请选择「允许访问」。" },
  policy: { history: [
    { scope: "global", version: 3, updated_at: "2026-10-15T17:22:00" },
    { scope: "ep-hr-04", version: 5, updated_at: "2026-10-15T19:40:00" },
    { scope: "global", version: 2, updated_at: "2026-10-14T09:05:00" }] },
  usage: { usage: [
    { model: "(端点合计)", scope: "local", calls: 512, chars: 1284400, endpoints: 3 },
    { model: "(端点合计)", scope: "external", calls: 38, chars: 96500, endpoints: 1 }] },
};
/* #mock 沙盘：只为实现"预览模式下界面能被自动化测试完整走一遍"。
   GET/POST 的差别按接口契约复现（配对码重新生成、自定义、监听地址切换）。 */
function mockCode() {
  return Math.random().toString(16).slice(2, 8).toUpperCase();
}
function mockNetwork() {
  const external = MOCK_DATA.network.listen_host === "0.0.0.0";
  const urls = MOCK_DATA.network.lan_urls;
  return Object.assign({}, MOCK_DATA.network, {
    external,
    primary_url: external ? urls[0] : `http://127.0.0.1:${MOCK_DATA.network.listen_port}/`,
  });
}
// 与 yindun/control/policy.py · describe() 同一套人话（预览模式的提示条要与真机一致）
function mockDescribe(bundle) {
  const out = {};
  if ((bundle.sensitive_paths || []).length) out["敏感路径"] = `追加 ${bundle.sensitive_paths.length} 条`;
  if (bundle.approval_floor) {
    out["审批下限"] = { confirm: "必须人工确认", deny: "一律拒绝" }[bundle.approval_floor] || "";
  }
  if (bundle.allowed_models) out["允许模型"] = bundle.allowed_models.join("、") || "（空＝全部禁用）";
  if (bundle.allowed_plugins) out["允许插件"] = bundle.allowed_plugins.join("、") || "（空＝全部禁用）";
  if (bundle.require_strict_mode) out["严格模式"] = "强制开启（禁用解释器）";
  if (bundle.note) out["备注"] = String(bundle.note);
  return out;
}
async function mockApi(path, options = {}) {
  const route = path.split("?")[0];
  const key = route.replace("/api/v1/", "");
  const body = options.body || {};
  if (route.startsWith("/api/v1/events")) {
    const rows = MOCK_DATA.events.events;
    return { events: rows, total: rows.length, types: [...new Set(rows.map((r) => r.event_type))],
             limit: 100, offset: 0 };
  }
  if (key === "pair-code") {
    if ((options.method || "GET") === "POST") {
      const ttl = Math.max(5, Math.min(Number(body.ttl_minutes) || 30, 1440));
      MOCK_DATA["pair-code"] = {
        code: String(body.code || "").trim().toUpperCase() || mockCode(),
        ttl_minutes: ttl, custom: !!body.code,
        expires_at: isoLocal(new Date(Date.now() + ttl * 60000)),
      };
    }
    return MOCK_DATA["pair-code"];
  }
  if (key === "network") return mockNetwork();
  // 策略下发：按真实服务端口径复现（每个 scope 各自递增版本 + 历史追加 + summary 人话），
  // 这样"勾预设 → 逐台下发 → 策略历史里出现新版本"在预览模式下也能完整走一遍。
  if (key === "policy") {
    if ((options.method || "GET") === "POST") {
      const scope = String(body.scope || "global");
      const versions = MOCK_DATA.policy.history.filter((h) => h.scope === scope).map((h) => h.version);
      const version = Math.max(0, ...versions) + 1;
      MOCK_DATA.policy.history.unshift({ scope, version, updated_at: isoLocal(new Date()),
                                         active: 1 });
      return { scope, version, sig: "mock0000", summary: mockDescribe(body.bundle || {}) };
    }
    return MOCK_DATA.policy;
  }
  if (key === "listen") {
    const host = body.host === "0.0.0.0" ? "0.0.0.0" : "127.0.0.1";
    MOCK_DATA.network.listen_host = host;
    const net = mockNetwork();
    return { ok: true, listen_host: host, lan_urls: net.lan_urls,
             note: host === "0.0.0.0"
               ? "已切换为对外监听（预览数据；真实环境首次会触发 Windows 防火墙询问）。"
               : "已改回仅本机监听。" };
  }
  return MOCK_DATA[key] || {};
}

/* ── 启动 ─────────────────────────────────────────────── */
// 窗口按钮要在**登录之前**就绪：无边框窗口的登录页同样需要能拖、能关
bindWindowControls();
// 配对码浮窗同理：绑定放在模块初始化处（不是 start() 里），登录/重连不会重复挂监听
bindPairModal();
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
