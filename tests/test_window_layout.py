# -*- coding: utf-8 -*-
"""隐盾 · 闪发折叠模式（对话浮窗）回归测试

覆盖（旧 Qt 界面「极简闪发折叠模式」迁移后的行为约束）：
  1. 几何计算：折叠 = 底边对齐 + 水平居中 + 宽度下限；展开 = 精确还原折叠前几何
  2. 兜底：几何异常（0/负数）时退回默认展开尺寸，不让窗口"消失"
  3. 桥接契约：window_action("mini"/"restore_window") 真的动了窗口，
     并且**折叠前放宽 min_size、展开后恢复**（否则 420×60 会被最小尺寸卡住 → "点了没反应"）
  4. 取不到窗口几何时不乱动（宁可不折叠，也不要把窗口跳到错误位置）
  5. 前端契约：浮条元素与折叠按钮存在；闪发提交会先展开再发送

用法：python tests/test_window_layout.py   （退出码 0 = 全部通过）
"""
import os
import re
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.app import window_layout as wl  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── 1) 几何计算 ────────────────────────────────────
print("=" * 78)
print("【1】折叠 / 展开几何")
print("=" * 78)
x, y, w, h = 100, 200, 1180, 780
cx, cy, cw, ch = wl.collapse_to_mini(x, y, w, h)
check("折叠高度为浮条高度", ch == wl.MINI_H, f"{ch} vs {wl.MINI_H}")
check("折叠后宽度为默认浮条宽度", cw == wl.MINI_W_DEFAULT, str(cw))
check("折叠后水平居中", cx == x + (w - cw) // 2, f"{cx} vs {x + (w - cw) // 2}")
check("折叠后底边对齐（贴着原窗口底边）", cy + ch == y + h, f"{cy + ch} vs {y + h}")

narrow = wl.collapse_to_mini(0, 0, 400, 300, 100)
check("浮条宽度有下限（过窄没法输入）", narrow[2] == wl.MINI_W_MIN, str(narrow))

saved = (100, 200, 1180, 780)
check("展开精确还原折叠前的几何", wl.restore_from_mini(saved, 480, 920) == saved,
      str(wl.restore_from_mini(saved, 480, 920)))
no_saved = wl.restore_from_mini(None, 480, 920)
check("没有保存值时保持当前位置、用默认展开尺寸",
      no_saved == (480, 920, wl.EXPANDED_W, wl.EXPANDED_H), str(no_saved))
check("损坏的保存值不会被采用（尺寸为 0 时退回默认）",
      wl.restore_from_mini((1, 2, 0, 0), 480, 920)[2:] == (wl.EXPANDED_W, wl.EXPANDED_H),
      str(wl.restore_from_mini((1, 2, 0, 0), 480, 920)))
check("尺寸异常时 clamp 回默认展开尺寸",
      wl.clamp_geometry(10, 20, 0, 0)[2:] == (wl.EXPANDED_W, wl.EXPANDED_H),
      str(wl.clamp_geometry(10, 20, 0, 0)))

# 边缘改尺寸锚点语义（前端热区与后端 clamp 共享这套规则，先由纯函数锁住）
check("拖下边：只长高，顶边钉住",
      wl.resize_edge("s", 100, 100, 1180, 780, 0, 100) == (100, 100, 1180, 880))
check("拖左边：对侧（右）边钉住",
      wl.resize_edge("w", 100, 100, 1180, 780, -100, 0) == (0, 100, 1280, 780))
check("拖上边：底边钉住",
      wl.resize_edge("n", 100, 100, 1180, 780, 0, 50) == (100, 150, 1180, 730))
check("拖角（nw）：两个方向同时改",
      wl.resize_edge("nw", 100, 100, 1180, 780, -20, 30) == (80, 130, 1200, 750))
check("小于最小尺寸被 clamp（下边）",
      wl.resize_edge("s", 0, 0, 1180, 780, 0, -300) == (0, 0, 1180, 600))
check("clamp 时对侧锚点不漂移（左边往里推到底）",
      wl.resize_edge("w", 0, 0, 880, 600, 100, 0) == (0, 0, 880, 600))

# ── 2) 桥接契约（用假窗口验证真的调了 move/resize/min_size）──
print("\n" + "=" * 78)
print("【2】桥接契约：window_action('mini' / 'restore_window')")
print("=" * 78)


class FakeWindow:
    """最小可用的假窗口：记录 move/resize/min_size 调用。"""

    def __init__(self, x, y, w, h, min_size=(880, 600)):
        self.x, self.y, self.width, self.height = x, y, w, h
        self.min_size = min_size
        self.calls = []

    def move(self, nx, ny):
        self.calls.append(("move", nx, ny))
        self.x, self.y = nx, ny

    def resize(self, nw, nh, fix_point=None):
        # fix_point：边缘拖拽的单次原生 resize 锚点（pywebview ≥5）；
        # 记录它以便断言"拖哪条边就锚定对侧边"
        self.calls.append(("resize", nw, nh, fix_point))
        self.width, self.height = nw, nh


class BlindWindow(FakeWindow):
    """取不到几何的窗口（模拟后端不支持读取 x/y）。"""

    @property
    def x(self):
        return None

    @x.setter
    def x(self, _v):
        pass


from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402
from yindun.app.webview_app import JsApi  # noqa: E402
import tempfile  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="yindun_mini_test_"))
svc = AgentService(settings=SettingsStore(path=TMP / "cfg.json"),
                   sessions=SessionStore(path=TMP / "sess.json"),
                   plugin_data_root=TMP / "plugins_data")
svc.initialize()

holder = {}
api = JsApi(svc, holder)
win = FakeWindow(100, 200, 1180, 780)
holder["window"] = win

ok = api.window_action("mini")
check("折叠返回成功", ok is True)
check("折叠调用了 move + resize", [c[0] for c in win.calls] == ["move", "resize"], str(win.calls))
check("折叠后窗口尺寸=浮条尺寸", (win.width, win.height) == (wl.MINI_W_DEFAULT, wl.MINI_H),
      f"{win.width}x{win.height}")
check("折叠后底边对齐", win.y + win.height == 200 + 780, f"{win.y + win.height}")
check("折叠后水平居中", win.x == 100 + (1180 - wl.MINI_W_DEFAULT) // 2, str(win.x))

win.calls.clear()
ok = api.window_action("restore_window")
check("展开返回成功", ok is True)
check("展开调用了 move + resize", [c[0] for c in win.calls] == ["move", "resize"], str(win.calls))
check("展开精确还原折叠前几何", (win.x, win.y, win.width, win.height) == (100, 200, 1180, 780),
      f"{win.x},{win.y} {win.width}x{win.height}")

win.calls.clear()
holder["window"] = BlindWindow(0, 0, 800, 600)
ok = api.window_action("mini")
check("取不到窗口几何时不乱动（不折叠、不移动）", ok is True and win.calls == [], str(win.calls))
holder["window"] = win

check("未知动作返回 False", api.window_action("no_such_action") is False)

# ── 边缘改尺寸桥接（frameless 窗口的 resize 入口）──
# ★ 拖拽严重抖动的两个根因（均已修，这里锁住契约）：
#   ① 前端 clientX 相对视口原点，w/n 边拖拽会移动窗口本身 → 视口原点跟着动 →
#      clientX 混入"窗口位移"形成正反馈 → 改用 screenX/screenY（不随窗口移动变化）；
#   ② 旧版 move+resize 是两次 SetWindowPos，两次之间窗口位置与尺寸短暂不一致 →
#      改为**单次** resize(fix_point)（拖哪条边就锚定对侧边），一次原生调用完成定位+改尺寸。
from webview.window import FixPoint  # noqa: E402
holder["window"] = win2 = FakeWindow(100, 200, 1180, 780)
b = api.window_bounds()
check("window_bounds 返回当前几何", b == {"ok": True, "x": 100, "y": 200, "w": 1180, "h": 780}, str(b))
win2.calls.clear()
ok = api.window_resize_edge("e", 120, 0, 100, 200, 1180, 780)
check("右缘拖宽 120：单次 resize、不再 move（双调用是抖动来源之一）",
      ok is True and win2.calls == [("resize", 1300, 780, FixPoint.WEST)], str(win2.calls))
win2.calls.clear()
api.window_resize_edge("s", 0, -300, 100, 200, 1180, 780)
check("拖到小于最小尺寸 → 后端 clamp 到 880×600（前端绕不过去）",
      win2.calls[-1][:3] == ("resize", 1180, 600), str(win2.calls))
api.window_resize_edge("w", 400, 0, 100, 200, 1180, 780)
check("左缘拖拽：单次 resize 且锚定对侧（EAST，右边界不漂移）",
      win2.calls[-1] == ("resize", 880, 780, FixPoint.EAST), str(win2.calls[-1:]))
api.window_resize_edge("nw", -60, -40, 100, 200, 1180, 780)
check("角（nw）拖拽：锚定对侧角（EAST|SOUTH，右下双钉）",
      win2.calls[-1] == ("resize", 1240, 820, FixPoint.EAST | FixPoint.SOUTH), str(win2.calls[-1:]))
check("全程无 move 调用（单次原生 resize，双调用抖动根因已除）",
      not any(c[0] == "move" for c in win2.calls), str(win2.calls))
holder["window"] = BlindWindow(0, 0, 800, 600)
check("取不到几何时 window_bounds 明确失败（前端放弃拖拽，不乱动窗口）",
      api.window_bounds().get("ok") is False)
holder["window"] = win2

# ── 内网/远程 Ollama：地址可配置，且不做本机模型名对齐（不覆盖用户配置）──
prev_model = svc.settings.get("model")
prev_host = svc.settings.get("ollama_host") or "http://127.0.0.1:11434"
check("save_settings 允许保存 ollama_host（旧白名单会静默丢弃它）",
      api.save_settings({"ollama_host": "http://10.0.0.8:11434"}) is True
      and svc.settings.get("ollama_host") == "http://10.0.0.8:11434",
      str(svc.settings.get("ollama_host")))
time.sleep(0.3)   # 让 save_settings 触发的后台重建跑完，避免与下面的断言交错
svc.settings.set("ollama_host", "http://10.0.0.8:11434")
svc.settings.set("model", "intranet-model-xyz")
svc.prepare_llm()
check("非本机地址时跳过模型名对齐（内网模型名必须原样生效）",
      svc.settings.get("model") == "intranet-model-xyz", str(svc.settings.get("model")))
svc.settings.set("ollama_host", prev_host)
svc.settings.set("model", prev_model)
svc.prepare_llm()
check("本机地址时把设置同步到 OLLAMA_HOST（知识库/状态探测等组件统一跟随）",
      os.environ.get("OLLAMA_HOST") == prev_host, str(os.environ.get("OLLAMA_HOST")))

# 窗口最小尺寸必须按折叠态给（运行期改 min_size 不生效，见 window_layout 注释）
webview_src = (ROOT / "yindun" / "app" / "webview_app.py").read_text(encoding="utf-8")
check("创建窗口时 min_size 用折叠态尺寸（否则折叠会被静默卡住）",
      '"min_size": window_layout.MINI_MIN_SIZE' in webview_src
      and wl.MINI_MIN_SIZE == (wl.MINI_W_MIN, wl.MINI_H), str(wl.MINI_MIN_SIZE))
check("运行期不再假装能改 min_size（避免「看着有效其实无效」的写法）",
      "window.min_size =" not in webview_src)

# ── 3) 前端契约（浮条与折叠入口必须存在）────────────
print("\n" + "=" * 78)
print("【3】前端契约：浮条 / 折叠按钮 / 闪发提交顺序")
print("=" * 78)
html = (ROOT / "yindun" / "app" / "web" / "index.html").read_text(encoding="utf-8")
js = (ROOT / "yindun" / "app" / "web" / "app.js").read_text(encoding="utf-8")
css = (ROOT / "yindun" / "app" / "web" / "styles" / "app.css").read_text(encoding="utf-8")
for element_id in ("minibar", "mini-input", "btn-mini-send", "btn-mini-expand", "btn-mini"):
    check(f"界面元素存在：#{element_id}", f'id="{element_id}"' in html, "index.html 里找不到该元素")
check("浮条含闪发提示（告诉用户回车会展开并发送）",
      "闪发模式" in html and "自动展开" in html)
check("折叠态隐藏主界面、只显示浮条",
      "body.is-mini .app { display: none; }" in css and "body.is-mini .minibar" in css)
check("浮条有状态行（折叠时也能看出是否在推理）",
      'id="mini-status"' in html and "setMiniStatus" in js)
check("折叠按钮接上 setMini(true)", 'btn-mini").onclick = () => setMini(true)' in js)
check("闪发提交先展开再发送（与旧实现一致）",
      re.search(r"async function miniSubmit[\s\S]{0,600}?if \(miniOn\(\)\) await setMini\(false\);[\s\S]{0,120}?await sendText\(text\);", js)
      is not None)
check("折叠失败时界面不假装已折叠（回滚 + 提示）",
      "窗口折叠失败" in js and 'classList.toggle("is-mini", !on)' in js)
check("Esc 可展开", 'e.key === "Escape" && miniOn()' in js)
check("主输入与浮条共用同一个发送实现（避免两套行为漂移）",
      js.count("await sendText(text)") >= 2, f"sendText 调用次数 {js.count('await sendText(text)')}")

# ── 浮条输入的两处真机缺陷（用户实测反馈 → 修复 → 固化）──
# ① 用户反馈"闪发模式下按住 Shift+Enter 直接发送了"：单行 input 装不下换行符，
#    且 keydown 只判 Enter 不判 shiftKey。② "输入框内容不同步"：浮条与主输入框是两个控件，
#    没有任何草稿搬运，而展开分支还显式清空了浮条（折叠期间打的字被丢掉）。
check("浮条输入是多行控件（单行 input 装不下换行，Shift+Enter 会变成空操作）",
      '<textarea class="minibar__input" id="mini-input"' in html)
check("浮条输入框有高度上限（浮条窗口固定 60px，不能被文本撑破）",
      "max-height: 44px" in css and 'AUTOSIZE_MAX = { input: 180, "mini-input": 44 }' in js)
check("Shift+Enter 在浮条里不发送（交给控件默认行为插入换行）",
      'if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); miniSubmit(); }' in js
      and 'if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); miniSubmit(); }' not in js)
check("草稿双向搬运：折叠时主输入 → 浮条，展开时浮条 → 主输入",
      'carryDraft("mini-input", "input")' in js and 'carryDraft("input", "mini-input")' in js
      and '$("mini-input").value = ""' not in js, "展开分支不得再直接清空浮条（丢字根因）")
check("草稿搬运是「复制 + 清空来源」（来回折叠不会复制出两份草稿）",
      re.search(r"function carryDraft[\s\S]{0,400}?to\.value = from\.value[\s\S]{0,80}?from\.value = \"\"", js)
      is not None)
check("忙时不吞掉用户打的字（先判忙、再清空；旧写法相反）",
      (lambda body: body.index("state.busy") < body.index("clearDrafts()"))(
          js.split("async function miniSubmit")[1].split("\n  }")[0]))
check("发送后两份草稿一起清空（展开时不会把已发出的内容搬回来）",
      "clearDrafts();" in js.split("async function miniSubmit")[1][:400]
      and "clearDrafts();" in js.split('$("composer").addEventListener("submit"')[1][:600])
check("自动增高只有一套实现（浮条与主输入共用，避免上限规则各写一份）",
      "function autosizeInput" in js and js.count("autosizeInput(") >= 4
      and "el.style.height = Math.min(el.scrollHeight, 180)" not in js)

# ── 思考模式工具活动流（真机跑工具链才暴露的渲染缺陷回归）──
# entryEl 返回的是外层 .row，.entry__body 是 .entry 的子节点；
# 旧写法 entry.insertBefore(box, body) 必抛 NotFoundError —— 工具活动流从未渲染出来。
check("工具活动流插到 entry__body 的父节点（回归：旧写法必抛 NotFoundError）",
      "(body.parentElement || entry).insertBefore(box, body)" in js
      and "entry.insertBefore(box, body)" not in js)
check("活动流容器与样式存在（.entry__tools / .tool-line）",
      "entry__tools" in js and ".tool-line {" in css)

# ── 边缘改尺寸 + 三栏内容重排（本轮 bug 修复的回归护栏）──
check("边缘热区存在：8 个方向",
      'id="edge-zone"' in html and html.count("data-edge=") == 8)
check("热区仅在无边框模式显示、折叠浮条态不显示",
      'html[data-frameless="1"] body:not(.is-mini) .edge-zone { display: contents; }' in css)
check("热区 mousedown 吞掉事件（否则 pywebview easy_drag 会把『拖边』变成『拖窗』）",
      'el.addEventListener("mousedown", (e) => { e.stopPropagation(); });' in js)
check("边缘拖拽已绑定（位移交后端算，前端不复制锚点/clamp 规则）",
      "bindEdgeResize()" in js and 'call("window_bounds")' in js and 'call("window_resize_edge"' in js)
check("列内内容跟随栅格宽度（回归护栏：不得写死 248px/400px，否则拖宽时内容不重排、出现遮挡）",
      "width: 248px; flex: none" not in css and "width: 400px; flex: none" not in css
      and ".side--left > .sidebar { width: 100%" in css
      and ".side--right > .panel__inner { width: 100%" in css)
check("后端桥接方法齐备",
      "def window_bounds" in webview_src and "def window_resize_edge" in webview_src)

# ── 4) 三栏可拖宽（V3.4.0 新增）────────────────────
print("\n" + "=" * 78)
print("【4】三栏宽度：拖拽手柄 / 上下限 / 持久化 / 与折叠共存")
print("=" * 78)
check("两侧各有一个拖拽手柄（含无障碍语义）",
      html.count('class="col-resizer"') == 2
      and 'data-resize="left"' in html and 'data-resize="right"' in html
      and 'role="separator"' in html, str(html.count("col-resizer")))
check("手柄绝对定位、不占栅格轨道（否则会多出隐式列）",
      ".col-resizer {" in css and "position: absolute" in css and "z-index" in css)
check("列宽只由 JS 写行内样式，CSS 里不再有折叠态的列宽规则（两套来源会互相覆盖）",
      re.search(r"\.app\.is-(?:left|right)-collapsed[^{]*\{[^}]*grid-template-columns", css) is None,
      "CSS 里仍存在 .app.is-*-collapsed 的 grid-template-columns 规则")
check("存在明确的上下限与对话区保底",
      "centerMin: 420" in js and "min: 200, max: 420" in js and "min: 300, max: 640" in js)
check("拖拽监听挂在 window 上（指针移出手柄也不断）",
      'window.addEventListener("pointermove", onMove)' in js
      and "setPointerCapture(" not in js,
      "仍依赖 setPointerCapture 或未把 move/up 挂到 window")
check("拖动时关闭栅格过渡（否则手感发飘）", ".app.is-resizing { transition: none; }" in css)
check("宽度写入 localStorage 并在启动时恢复",
      'localStorage.setItem("yd_layout"' in js and "loadLayout()" in js)
check("双击复位 + 方向键微调", 'handle.addEventListener("dblclick"' in js
      and 'handle.addEventListener("keydown"' in js)
check("自动收起不写偏好（临时变窄不该被永久记住）",
      "setRightCollapsed(true, false)" in js and "persist = true" in js)
check("窗口变宽时自动把右栏放回来（只对自动收起生效）",
      "autoCollapsedRight" in js and "setRightCollapsed(false, false)" in js)
check("提供只读布局快照供自动化校验", "window.__ydLayout = () =>" in js)

# ── 5) 置顶即时生效（复测后的行为修正）──────────────
print("\n" + "=" * 78)
print("【5】置顶：运行时即时生效（不再要求重启）")
print("=" * 78)
check("桥接层在保存设置后立即写窗口标志",
      "window.on_top = new_value" in webview_src and "即时应用置顶失败" in webview_src)
check("界面既不声称「置顶需重启」、也不再挂「立即生效」标签（所有设置都即时生效，不必逐个声明）",
      "重启" not in html.split("btn-pin")[1][:120] and "立即生效" not in html
      and 'title="置顶 / 取消置顶"' in html)
check("切换后按实际结果提示（含失败时的诚实降级）",
      '"已置顶显示"' in js and "置顶切换失败" in js)
check("置顶按钮自身显示状态（明暗主题各有取色，不写死颜色）",
      "function paintPinState" in js and ".win-btn.is-active {" in css
      and "color: var(--accent-600)" in css.split(".win-btn.is-active {")[1][:120])
check("设置面板的置顶开关同样即时生效（断言随行式布局对齐，意图不变）",
      'id="sw-topmost"' in html and "重启" not in html.split('id="sw-topmost"')[0][-300:]
      and "save_settings" in webview_src
      and "topmost" in webview_src.split("def save_settings")[1][:600])

# ── 6) V3.5.1 界面契约：四档权限 / 四档思考 / 窗口按钮归位 / 无边框不再是开关 ──
print("\n" + "=" * 78)
print("【6】四档权限 / 四档思考 / 窗口按钮归位 / 无边框固定为产品形态")
print("=" * 78)
service_src = (ROOT / "yindun" / "app" / "agent_service.py").read_text(encoding="utf-8")
# 行为验证（不只看源码）：保存档位后环境变量必须立刻变，这就是"立即生效"的证据
_prev_perm = svc.settings.get("permission")
ok_plan = api.save_settings({"permission": "plan"})
check("保存档位 → 内核环境变量立刻更新（下一个操作即按新档位判定）",
      ok_plan is True and os.environ.get("YINDUN_PERMISSION") == "plan",
      str(os.environ.get("YINDUN_PERMISSION")))
check("档位键规范化后再下发（前端传的也是稳定键 plan/ask/auto/full）",
      svc.settings.get("permission") == "plan")
api.save_settings({"permission": "ask"})
check("严格模式开关同样即时下发（同一个出口，避免两套写法漂移）",
      os.environ.get("ALLOW_SCRIPT_EXEC") in ("0", "1"))
api.save_settings({"permission": _prev_perm, "allow_script_exec": svc.settings.get("allow_script_exec", True)})
check("权限控件改为四档分段选择器（旧的 3 选项下拉已移除）",
      'id="perm-modes"' in html and "sel-permission" not in html and "sel-permission" not in js)
check("四档按钮与 core/permission.py 的 MODES 一一对应",
      all(f'data-mode="{m}"' in html for m in ("plan", "ask", "auto", "full")))
check("选择器带无障碍语义（radiogroup + 每档一个 radio）",
      'id="perm-modes" role="radiogroup"' in html and html.count('class="permseg__btn"') == 4)
check("切换即落库（不再挂「立即生效」字样；「重启后生效」这类话也不允许出现）",
      'persist({ permission: mode })' in js and "立即生效" not in js and "重启后生效" not in js)
check("后端把档位立刻下发到内核环境变量（不是只写配置文件）",
      "def apply_runtime_env" in service_src and "os.environ[PERMISSION_ENV] = mode" in service_src
      and '"permission" in patch' in webview_src
      and "apply_runtime_env()" in webview_src.split("def save_settings")[1][:900])
check("启动即下发（界面画出来之前工具层已按设置行事）",
      "apply_runtime_env()" in service_src.split("def initialize")[1][:600])
check("思考强度是胶囊 + 下拉（低 / 中 / 高 / 最高），旧的 1–10 下拉已移除",
      'id="think-select"' in html and 'id="think-pop"' in html
      and "sel-depth" not in html and "sel-depth" not in js
      and all(f'"{lv}"' in js for lv in ("low", "mid", "high", "max"))
      and 'data-level="${lv.key}"' in js)
check("点开下拉才展开选项、点选项即落库（一个词 + 一个箭头，不占输入区）",
      "function openThinkPop" in js and "function closeThinkPop" in js
      and 'thinkTrig.onclick = () => (thinkPop.hidden ? openThinkPop() : closeThinkPop())' in js)
check("两个输入区下拉都以「点击外部关闭」收尾（不靠用户猜怎么关）",
      js.count("!thinkSel.contains(e.target)") == 1 and js.count("!permPill.contains(e.target)") == 1)
check("四档映射到 worker 的 thinking_depth 且旧数值能折算回档位",
      "THINK_LEVELS" in js and re.search(r'key: "max",\s*label: "最高",\s*depth: 10', js) is not None
      and "levelOf" in js)
check("窗口按钮（最小化 / 置顶 / 关闭）会随右栏展开移入右栏头部",
      'id="win-ctrls"' in html and "function placeWinCtrls" in js
      and "placeWinCtrls();" in js.split("function setRightCollapsed")[1][:400]
      and "placeWinCtrls()" in js.split("function openSettings")[1][:700]
      and "placeWinCtrls()" in js.split("function closeSettings")[1][:500])
check("设置视图下顶栏与右栏都隐藏，按钮改挂设置页头部（否则设置页里没有关闭按钮）",
      ".app.is-settings-open .band," in css and "setmain__head" in js.split("function placeWinCtrls")[1][:600])
check("按钮只在容器之间搬移、不重建（重建会丢 onclick 绑定）",
      "host.appendChild(box)" in js and "innerHTML" not in js.split("function placeWinCtrls")[1][:400])
check("无边框不再是设置项：开关行与持久化都移除，形态固定打开",
      "sw-frameless" not in html and "sw-frameless" not in js
      and 'document.documentElement.dataset.frameless = "1"' in js
      and '"frameless": True' in webview_src)

# ── 7) V3.5.3 界面契约：主题化高亮 / 窗口按钮贴角 / 本对话权限 / 插件市场 / laya 接入 ──
print("\n" + "=" * 78)
print("【7】主题化高亮 · 窗口按钮贴角 · 本对话权限 · 插件市场 · laya 接入")
print("=" * 78)
tokens = (ROOT / "yindun" / "app" / "web" / "styles" / "tokens.css").read_text(encoding="utf-8")

check("焦点环是**两套显式取值**（浅色水洗铺在深色底上会变成刺眼光晕）",
      "--focus-ring:" in tokens.split('html[data-theme="dark"]')[0]
      and "--focus-ring:" in tokens.split('html[data-theme="dark"]')[1])
check("输入框/文本域/开关的高亮都走同一个 token（不再各写一份 --accent-50）",
      css.count("var(--focus-ring)") >= 5
      and ".composer__box:focus-within { border-color: var(--accent-500); box-shadow: 0 0 0 3px var(--focus-ring); }" in css
      and "textarea:focus { border-color: var(--accent-500); box-shadow: 0 0 0 3px var(--focus-ring); }" in css)
check("提示条（含置顶提示）颜色也来自 token，明暗各自取值",
      "--toast-bg:" in tokens and "--toast-fg:" in tokens
      and tokens.count("--toast-bg:") == 2 and "var(--toast-bg)" in css
      and 'html[data-theme="dark"] .toast' not in css)
check("设置页的窗口按钮固定在窗口右上角（原来跟着头部内边距走，看起来没对齐）",
      ".win-ctrls--head { position: fixed; top: 6px; right: 10px;" in css)
check("发送按钮箭头朝右（旧路径的顶点在左侧，渲染出来像「◀」）",
      'id="btn-send"' in html and 'M4 12h15M12 6l6 6-6 6' in html.split('id="btn-send"')[1][:220])

check("输入区有本对话权限胶囊（设置页那份是「新对话默认」）",
      'id="perm-pill"' in html and 'id="perm-pop"' in html
      and "function paintPermPill" in js and 'call("set_session_permission", mode)' in js)
check("下拉里含「跟随设置默认」一项（可清除本对话覆盖）",
      "跟随设置默认" in js and 'data-mode=""' in js)
check("切换本对话档位后同时刷新胶囊与安全态势条",
      "state.sessionPermission = res.permission || null" in js
      and js.count("paintPermPill(state.sessionPermission)") >= 2)
check("后端提供会话级档位接口（空值 = 清除覆盖）",
      "def set_session_permission" in service_src and "def effective_permission" in service_src
      and "def set_session_permission" in webview_src)
check("档位取值范围仍受内核约束（界面传什么都会被 resolve_mode 规范化）",
      "value = resolve_mode(mode) if str(mode or \"\").strip() else None" in service_src)

check("插件页改成市场式布局（搜索 + 已安装瓷贴 + 分类双列卡片）",
      'id="plugin-search"' in html and 'id="plugin-tiles"' in html and 'id="plugin-groups"' in html
      and "function renderPlugins" in js and "market__grid" in css and "pcard" in css)
check("卡片默认只给结论（图标/名称/一句话/开关），详情点开才展开",
      'class="pcard__more" data-more=' in js and 'class="pcard__detail" data-detail=' in js)
check("按 category 分组、未知分类落到「扩展」",
      "const CATEGORY_ORDER" in js and '"扩展"' in js.split("CATEGORY_ORDER")[1][:120])
check("托管配置不在卡片里给裸文本编辑（避免把 JSON 当便签改坏）",
      "filter((f) => !f.managed)" in js and "managed" in (ROOT / "yindun/plugins/host.py").read_text(encoding="utf-8"))
check("搜索框走客户端过滤（插件数量级不需要往返一次）",
      'if ($("plugin-search")) $("plugin-search").oninput = () => renderPlugins(state.plugins);' in js)

check("laya 是独立的一级设置页（不是藏在插件卡片里的一个配置项）",
      'data-set="laya"' in html and "laya: [\"laya 风险分级\"" in js
      and "async function renderLayaPage" in js and "function bindLayaDetail" in js)
check("laya 页按三档来源组织（本机 / 局域网 / 正则回退），与模型接入同一范式",
      "const LAYA_SOURCES" in js and all(k in js for k in ('local: { label', 'lan: { label', 'regex: { label'))
      and 'class="prov"' in html.split('<div class="setpage" data-set="laya">')[1][:600])
check("探活与试打分是两个动作（「连上了」不等于「分级可用」）",
      '"laya_probe"' in js and '"laya_try"' in js
      and "试打分" in js and "服务有响应但无法解析分级结果" in
      (ROOT / "yindun/plugins/builtin/laya_risk/plugin.py").read_text(encoding="utf-8"))
check("laya 接入配置由设置页与插件共用同一份文件（不会各写一份）",
      "CONFIG_NAME = \"laya_source.json\"" in (ROOT / "yindun/app/laya_service.py").read_text(encoding="utf-8")
      and '"laya_source.json"' in (ROOT / "yindun/plugins/builtin/laya_risk/plugin.py").read_text(encoding="utf-8"))
check("仍然声明「laya 不参与放行判定」（亮点能力也不能越过安全不变量）",
      "不参与任何放行判定" in html.split('<div class="setpage" data-set="laya">')[1][:2200])

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：闪发折叠模式的几何、桥接契约与前端入口均符合约束")
sys.exit(0)
