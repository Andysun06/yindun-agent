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
import json
import os
import re
import sys
import threading
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
from yindun.app.webview_app import JsApi, WebApp  # noqa: E402
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
check("Shift+Enter / Ctrl+Enter 在浮条里都不发送（换行交给控件/显式插入）",
      'if (e.key === "Enter" && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.isComposing) {' in js
      and 'if (e.key === "Enter" && !e.isComposing) {' not in js
      and 'insertNewline($("mini-input"))' in js)
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
check("桥接层在保存设置后立即写窗口标志（切换走 UI 线程调度，失败退回直接赋值）",
      'window.on_top = bool(patch["topmost"])' in webview_src
      and 'setattr(window, "on_top", want)' in webview_src
      and "即时应用置顶失败" in webview_src)
check("界面既不声称「置顶需重启」、也不再挂「立即生效」标签（所有设置都即时生效，不必逐个声明）",
      "重启" not in html.split("btn-pin")[1][:120] and "立即生效" not in html
      and 'title="置顶 / 取消置顶"' in html)
check("切换后按实际结果提示（含失败时的诚实降级）",
      '"已置顶显示"' in js and "置顶切换失败" in js)
check("置顶/最大化按钮的状态显示（明暗主题各有取色，不写死颜色）",
      # V3.6.8：置顶与最大化的高亮统一由 paintWindowState() 用 window_state() 的真实状态校正
      # （旧的 paintPinState 只在本地取反记账，窗口被别的方式改过就显示错）。
      "function paintWindowState" in js and "call(\"window_state\")" in js
      and ".win-btn.is-active {" in css
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
      # 窗口从 900 放宽到 2000：save_settings 里现在还要处理 sandbox_dir（可读写范围）等
      # 新增设置项的专用入口，方法体变长是正常的；这里钉的是"确有立即下发"这件事，
      # 不是"下发调用必须出现在前 N 个字符内"。
      "def apply_runtime_env" in service_src and "os.environ[PERMISSION_ENV] = mode" in service_src
      and '"permission" in patch' in webview_src
      and "apply_runtime_env()" in webview_src.split("def save_settings")[1][:2000])
check("启动即下发（界面画出来之前工具层已按设置行事）",
      "apply_runtime_env()" in service_src.split("def initialize")[1][:600])
check("思考强度是模型卡片底部四档滑杆（低 / 中 / 高 / 最高），旧的 1–10 下拉已移除",
      'id="think-select"' in html and 'id="think-slider"' in html and 'id="mpick-think"' in html
      and "sel-depth" not in html and "sel-depth" not in js
      and all(f'key: "{lv}"' in js for lv in ("low", "mid", "high", "max")))
check("滑杆改动即落库、Ctrl+T 可循环（不再有独立下拉弹层）",
      "slider.onchange" in js and "openThinkPanel" in js
      and "think-pop" not in html and "think-pop" not in js)
check("输入区弹层都以「点击外部关闭」收尾（不靠用户猜怎么关）",
      js.count("!permPill.contains(e.target)") == 1
      and "if (!inside) closeModelPop();" in js and "const closeProvPop" in js)
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
# 判定行/通过态用 accent-50 做水洗底：深色主题若只继承浅色的 #EDF7F1，会变成一块白卡。
_dark_tokens = tokens.split('html[data-theme="dark"]')[1]
check("深色主题显式覆盖 accent-50 / accent-700（判定行与通过态不再变白卡）",
      "--accent-50:" in _dark_tokens and "--accent-700:" in _dark_tokens
      and 'html[data-theme="dark"] .trace--judge .trace__ico' in css)
check("输入框/文本域的焦点环都走同一个 token（不再各写一份 --accent-50）",
      css.count("var(--focus-ring)") >= 5
      and ".composer__box:focus-within { border-color: var(--border-2); box-shadow: 0 0 0 3px var(--focus-ring), 0 8px 24px rgba(0,0,0,.05); }" in css
      and "textarea:focus { border-color: var(--accent-500); box-shadow: 0 0 0 3px var(--focus-ring); }" in css)
check("审计面板打开期间每 2 秒自动刷新，且重绘保留滚动位置",
      "setInterval(refreshAuditLive, 2000)" in js
      and 'state.pane !== "audit"' in js
      and "list.scrollTop = top" in js
      and 'id="audit-live"' in html)
check("提示条（含置顶提示）颜色也来自 token，明暗各自取值",
      "--toast-bg:" in tokens and "--toast-fg:" in tokens
      and tokens.count("--toast-bg:") == 2 and "var(--toast-bg)" in css
      and 'html[data-theme="dark"] .toast' not in css)
check("设置页的窗口按钮固定在窗口右上角（原来跟着头部内边距走，看起来没对齐）",
      ".win-ctrls--head { position: fixed; top: 6px; right: 10px;" in css)
check("发送按钮箭头朝上（与 ZCode 新版一致，不再是纸飞机/右箭头）",
      'id="btn-send"' in html and 'M12 19V5M6 11l6-6 6 6' in html.split('id="btn-send"')[1][:260])

check("输入区有本对话权限胶囊（设置页那份是「新对话默认」）",
      'id="perm-pill"' in html and 'id="perm-pop"' in html
      and "function paintPermPill" in js and 'call("set_session_permission", mode)' in js)
check("下拉里含「跟随设置默认」一项（可清除本对话覆盖）",
      "跟随设置默认" in js and 'data-mode=""' in js)
check("切换本对话档位后同时刷新胶囊与安全态势条",
      # ★ 字段名必须是 `session_permission`（界面自动化回归抓出的真缺陷，已修）：
      #   内核返回的是 {ok, session_permission, default_permission, effective}，
      #   旧断言钉的 `res.permission` 永远是 undefined → 胶囊每次都退回默认档，
      #   用户选了档位却看不出生效。这里改成钉**正确的键**。
      "state.sessionPermission = res.session_permission || null" in js
      and js.count("paintPermPill(state.sessionPermission)") >= 2
      and "res.permission || null" not in js)
check("后端提供会话级档位接口（空值 = 清除覆盖）",
      "def set_session_permission" in service_src and "def effective_permission" in service_src
      and "def set_session_permission" in webview_src)
check("档位取值范围仍受内核约束（界面传什么都会被 resolve_mode 规范化）",
      "value = resolve_mode(mode) if str(mode or \"\").strip() else None" in service_src)

check("插件页是市场式布局（搜索 + 分类双列卡片），且**没有**顶部瓷贴条",
      'id="plugin-search"' in html and 'id="plugin-groups"' in html
      and "plugin-tiles" not in html and "market__tile" not in css and "market__tile" not in js
      and "function renderPlugins" in js and "market__grid" in css and "pcard" in css)
check("插件图标是画出来的拼图（不再拿插件名首字当图标）",
      "const PUZZLE_SVG" in js and "${PUZZLE_SVG}" in js and ".pcard__ico svg {" in css
      and "charAt(0)" not in js.split("function renderPlugins")[1][:4000])
check("卡片默认只给结论（图标/名称/一句话/开关），详情点开才展开",
      'class="pcard__more" data-more=' in js and 'class="pcard__detail" data-detail=' in js)
check("插件页不再编辑插件设置（卡片里没有文本编辑器，只有「打开设置」入口）",
      "data-cfg-open" not in js and "data-cfg-save" not in js
      and "data-open-settings" in js and "function openPluginSettings" in js
      and "先启用这个插件，它的设置才会出现" in js)
check("按 category 分组、未知分类落到「扩展」",
      "const CATEGORY_ORDER" in js and '"扩展"' in js.split("CATEGORY_ORDER")[1][:120])
check("托管配置（managed）在通用设置页里也不给裸文本编辑（留给专属界面接管）",
      "((p && p.config_files) || []).filter((f) => !f.managed)" in js
      and "managed" in (ROOT / "yindun/plugins/host.py").read_text(encoding="utf-8")
      and any(f.get("managed") for f in json.loads(
          (ROOT / "yindun/plugins/builtin/laya_risk/manifest.json").read_text(encoding="utf-8"))["config_files"]))
check("搜索框走客户端过滤（插件数量级不需要往返一次）",
      'if ($("plugin-search")) $("plugin-search").oninput = () => renderPlugins(state.plugins);' in js)

check("laya 的接入设置是**插件专属设置页**（启用后出现在设置栏，不是写死的静态页）",
      'data-set="laya"' not in html and "laya_risk: { render: renderLayaSettings" in js
      and "async function renderLayaSettings" in js and "function bindLayaDetail" in js)
check("带设置的插件启用后，设置栏出现它的专属栏目；停用即隐藏并退回插件页",
      'id="setnav-plugins"' in html and "function refreshPluginNav" in js
      and "function pluginSettingsEntries" in js and "p.enabled &&" in js
      and "该插件已停用，设置栏目已隐藏" in js and "refreshPluginNav();" in js)
check("设置页题头随插件而变（标题=插件名、副标题=它的说明）",
      "function pluginSettingsMeta" in js and "function renderPluginSettings" in js
      and "pset__tag" in css and 'id="plugin-settings-page"' in html)
check("laya 页按三档来源组织（本机 / 局域网 / 正则回退），与模型接入同一范式",
      "const LAYA_SOURCES" in js and all(k in js for k in ('local: { label', 'lan: { label', 'regex: { label'))
      and 'class="prov"' in js.split("async function renderLayaSettings")[1][:900])
check("探活与试打分是两个动作（「连上了」不等于「分级可用」）",
      '"laya_probe"' in js and '"laya_try"' in js
      and "试打分" in js and "服务有响应但无法解析分级结果" in
      (ROOT / "yindun/plugins/builtin/laya_risk/plugin.py").read_text(encoding="utf-8"))
check("laya 接入配置由设置页与插件共用同一份文件（不会各写一份）",
      "CONFIG_NAME = \"laya_source.json\"" in (ROOT / "yindun/app/laya_service.py").read_text(encoding="utf-8")
      and '"laya_source.json"' in (ROOT / "yindun/plugins/builtin/laya_risk/plugin.py").read_text(encoding="utf-8"))
check("仍然声明「laya 不参与放行判定」（亮点能力也不能越过安全不变量）",
      "不参与任何放行判定" in js.split("async function renderLayaSettings")[1][:1600])

# ── 8) 连点置顶的串行化（V3.6.12，组员评审 BUG-02）──
#    pywebview 对每个 js_api 调用新开线程；并发写 WinForms 的 TopMost 会把窗口写成
#    "未响应"。这一段用假窗口量**真实的最大并发进入数**，而不是 grep 一把锁。
print("\n" + "=" * 78)
print("【8】窗口动作串行化：连点不会并发写窗口属性")
print("=" * 78)


class _ConcurrencyWindow:
    """置顶属性写入时睡 0.15s，并记录"同时进入 setter 的线程数"。

    真机上的并发写就发生在这一层；用一个会睡的业务属性把竞态窗口放大，
    锁没生效时几乎必然观测到 max_active > 1。
    """

    def __init__(self, initial=False):
        self._on_top = bool(initial)
        self.entries = 0
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    @property
    def on_top(self):
        return self._on_top

    @on_top.setter
    def on_top(self, value):
        with self._guard:
            self.entries += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.15)
        self._on_top = bool(value)
        with self._guard:
            self.active -= 1


def _run_concurrent(fn, count=2):
    barrier = threading.Barrier(count)
    results = []

    def _worker():
        barrier.wait()
        results.append(fn())

    threads = [threading.Thread(target=_worker) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results


race_win = _ConcurrencyWindow()
holder["window"] = race_win
race_results = _run_concurrent(lambda: api.window_action("toggle_top"), 2)
check("两线程同时点置顶：两次都成功返回", len(race_results) == 2 and all(race_results),
      str(race_results))
check("★ 最大并发进入数必须为 1（锁确实串行化了整个分派）", race_win.max_active == 1,
      f"max_active={race_win.max_active} entries={race_win.entries}")
check("两次调用都真的落到窗口属性上", race_win.entries == 2, str(race_win.entries))

# 顺序调用 10 次仍全部成功（防止"为修并发把正常路径锁死"）
seq_win = _ConcurrencyWindow()
holder["window"] = seq_win
seq_results = [api.window_action("toggle_top") for _ in range(10)]
check("顺序连点 10 次仍全部成功", seq_results == [True] * 10, str(seq_results))
check("顺序 10 次恰好 10 次写入（没有丢点击、没有额外重复）",
      seq_win.entries == 10, str(seq_win.entries))
check("顺序 10 次（偶数）后窗口回到初始未置顶状态", seq_win.on_top is False,
      str(seq_win.on_top))
check("置顶偏好同步写回设置", svc.settings.get("topmost") is False,
      repr(svc.settings.get("topmost")))

# ── 9) UI 线程调度（WinForms Form.Invoke）──
#    真机上 set_on_top 最终是 form.TopMost = value；在工作线程里直接赋值属于跨线程
#    操作 WinForms 控件。这里注入一个假 instances，验证"找得到 Form 就走 Invoke"。
from webview.platforms import winforms as _wf  # noqa: E402

_old_instances = _wf.BrowserView.instances
_invoked = []


class _FakeForm:
    def __init__(self):
        self.invoked = 0

    def Invoke(self, callback):
        self.invoked += 1
        callback()


class _UidWindow:
    def __init__(self, uid):
        self.uid = uid
        self.on_top = False


form = _FakeForm()
try:
    _wf.BrowserView.instances = {"uid-probe": form}
    uid_win = _UidWindow("uid-probe")
    dispatched = JsApi._ui_thread_dispatch(uid_win, lambda: _invoked.append(1))
    check("找到 Form 时走 Invoke 并返回 True", dispatched is True, str(dispatched))
    check("回调确实在调度路径里被执行", _invoked == [1] and form.invoked == 1,
          f"{_invoked} invoked={form.invoked}")

    _wf.BrowserView.instances = {}
    no_instance = JsApi._ui_thread_dispatch(uid_win, lambda: _invoked.append(2))
    check("instances 里没有该 uid → 返回 False（调用方据此回退）",
          no_instance is False and _invoked == [1], f"{no_instance} {_invoked}")
finally:
    _wf.BrowserView.instances = _old_instances

# ── 10) UI 线程调度不可用 → 仍能完成切换（退回直接赋值 + 写回设置）──
_orig_dispatch = JsApi.__dict__["_ui_thread_dispatch"]
JsApi._ui_thread_dispatch = staticmethod(lambda window, callback: False)
try:
    fallback_win = _UidWindow("no-such-uid")
    holder["window"] = fallback_win
    fallback_ok = api.window_action("toggle_top")
finally:
    JsApi._ui_thread_dispatch = _orig_dispatch

check("调度不可用仍返回成功（退回直接赋值，不让用户点了没反应）",
      fallback_ok is True, str(fallback_ok))
check("退回直接赋值真的把窗口置顶了", fallback_win.on_top is True,
      str(fallback_win.on_top))
check("退回路径同样写回设置", svc.settings.get("topmost") is True,
      repr(svc.settings.get("topmost")))

# ── 11) 最大化 / 还原（V3.6.13，组员评审 BUG-0010）──
#   现场：点「最大化」铺满整个显示器（盖住任务栏）；再点「还原」没反应，只能重启。
#   根因：① 按 window.maximized 判断（后端不回填就永远 False）；
#         ② 异常时降级 toggle_fullscreen()——全屏必盖任务栏，且降级后 maximized 仍为 False，
#            下次点击又走 maximize → 表现为「还原失效」。
print("\n" + "=" * 78)
print("【11】最大化 / 还原：不降级全屏、自己记账、失败时按几何兜底")
print("=" * 78)


class _MaxFakeWindow(FakeWindow):
    """最大化/还原计数桩（记录是否被误用成全屏）。"""

    def __init__(self, x=100, y=200, w=1180, h=780):
        super().__init__(x, y, w, h)
        self.maximized = False
        self.max_calls = 0
        self.restore_calls = 0
        self.fullscreen_calls = 0

    def maximize(self):
        self.max_calls += 1

    def restore(self):
        self.restore_calls += 1

    def toggle_fullscreen(self):
        self.fullscreen_calls += 1


def _reset_max_state():
    api._saved_geometry = None
    api._maximized_by_us = False
    api._pre_maximize_geometry = None
    api._pre_maximize_bounds = None


class _BrokenMaxWindow(_MaxFakeWindow):
    def maximize(self):
        raise RuntimeError("backend has no maximize")

    def restore(self):
        raise RuntimeError("backend has no restore")


_reset_max_state()
broken = _BrokenMaxWindow()
holder["window"] = broken
_broken_ok = api.window_action("toggle_maximize")
check("maximize 抛异常时如实返回 False（不假装成功）", _broken_ok is False, str(_broken_ok))
check("★ 不再降级成全屏（全屏必盖住任务栏）", broken.fullscreen_calls == 0,
      f"toggle_fullscreen 调用 {broken.fullscreen_calls} 次")
check("失败后不记成「已最大化」（下次点击还能重试）", api._maximized_by_us is False,
      str(api._maximized_by_us))

_reset_max_state()
plain = _MaxFakeWindow()
holder["window"] = plain
_r1 = api.window_action("toggle_maximize")
_r2 = api.window_action("toggle_maximize")
check("第一次点击调用 maximize", _r1 is True and plain.max_calls == 1,
      f"{_r1} / max={plain.max_calls}")
check("★ 第二次点击无条件 restore（即使 maximized 属性根本没更新）",
      _r2 is True and plain.restore_calls == 1, f"{_r2} / restore={plain.restore_calls}")
check("最大化桩的 maximized 始终为 False（模拟事件未到）", plain.maximized is False)
check("全程没有退回全屏", plain.fullscreen_calls == 0, str(plain.fullscreen_calls))


class _StickyMaxWindow(_MaxFakeWindow):
    """maximize 后 maxmized=True，但 restore 不会回填（模拟 restored 事件丢失）。"""

    def maximize(self):
        self.max_calls += 1
        self.maximized = True

    def restore(self):
        self.restore_calls += 1


_reset_max_state()
sticky = _StickyMaxWindow()
holder["window"] = sticky
_s1 = api.window_action("toggle_maximize")
_s2 = api.window_action("toggle_maximize")
check("★ maximize 成功但 restored 事件没来，第二次仍能 restore",
      _s1 is True and _s2 is True and sticky.restore_calls == 1,
      f"{_s1}/{_s2} restore={sticky.restore_calls}")
check("我们自己记账已复位（不会第三次还去 restore）", api._maximized_by_us is False,
      str(api._maximized_by_us))


class _RestoreFailWindow(_MaxFakeWindow):
    """restore 抛异常，但 move/resize 可用：应回到最大化前的几何。"""

    def restore(self):
        self.restore_calls += 1
        raise RuntimeError("restore failed")


_reset_max_state()
restore_fail = _RestoreFailWindow()
holder["window"] = restore_fail
_rf1 = api.window_action("toggle_maximize")
_rf2 = api.window_action("toggle_maximize")
check("★ restore 失败时按最大化前几何兜底恢复一次",
      restore_fail.calls[-2:] == [("move", 100, 200), ("resize", 1180, 780, None)],
      str(restore_fail.calls))
check("几何兜底成功即视为已还原", _rf1 is True and _rf2 is True and api._maximized_by_us is False,
      f"{_rf1}/{_rf2} by_us={api._maximized_by_us}")


class _AllBrokenWindow(_MaxFakeWindow):
    """restore 与 move/resize 全失败：必须如实返回 False。"""

    def restore(self):
        raise RuntimeError("restore failed")

    def move(self, nx, ny):
        raise RuntimeError("move failed")


_reset_max_state()
all_broken = _AllBrokenWindow()
holder["window"] = all_broken
api.window_action("toggle_maximize")
_ab = api.window_action("toggle_maximize")
check("restore 与几何兜底都失败 → 返回 False（不假装已还原）", _ab is False, str(_ab))
check("兜底失败后仍记账为「已最大化」（下次点击继续尝试还原）",
      api._maximized_by_us is True, str(api._maximized_by_us))

# ── 11b) V3.6.21 手动按工作区最大化：写回 Bounds 才算还原 ──
class _BoundsForm:
    def __init__(self):
        self.Bounds = "maximized"

    def Invoke(self, callback):
        callback()


class _UidMaxWindow(_MaxFakeWindow):
    def __init__(self, uid):
        super().__init__()
        self.uid = uid


_reset_max_state()
bounds_form = _BoundsForm()
_wf.BrowserView.instances = {"uid-bounds": bounds_form}
try:
    api._pre_maximize_geometry = (100, 200, 1180, 780)
    api._pre_maximize_bounds = (100, 200, 1180, 780)
    api._maximized_by_us = True
    _b_ok = api._restore_from_maximize(_UidMaxWindow("uid-bounds"))
finally:
    _wf.BrowserView.instances = {}
check("手动最大化后还原：把最大化前的 Bounds 写回窗体",
      _b_ok is True and bounds_form.Bounds == (100, 200, 1180, 780),
      f"{_b_ok} / {bounds_form.Bounds}")
check("写回成功后才清空记账（不会留下脏状态）",
      api._maximized_by_us is False and api._pre_maximize_bounds is None,
      f"by_us={api._maximized_by_us} bounds={api._pre_maximize_bounds}")

_reset_max_state()
miss_win = _UidMaxWindow("uid-missing")
_wf.BrowserView.instances = {}
api._pre_maximize_geometry = (100, 200, 1180, 780)
api._pre_maximize_bounds = (100, 200, 1180, 780)
api._maximized_by_us = True
_miss_ok = api._restore_from_maximize(miss_win)
check("★ 找不到窗体时退回原生 restore，成功后同样清空 Bounds 记账",
      _miss_ok is True and miss_win.restore_calls == 1 and api._pre_maximize_bounds is None,
      f"{_miss_ok} restore={miss_win.restore_calls} bounds={api._pre_maximize_bounds}")

check("源码里不再调用 toggle_fullscreen 降级",
      ".toggle_fullscreen(" not in webview_src)
check("最大化/还原也调度到 UI 线程（与置顶同一套 _ui_thread_dispatch）",
      '_invoke_window_method(window, "maximize")' in webview_src
      and '_invoke_window_method(window, "restore")' in webview_src)
_reset_max_state()

# ── 12) P1-06：系统方式还原/最大化时同步自记账 ──
#   现场：用 Win+↓ / 双击顶栏还原后，按钮仍显示"已最大化"；再点还走 restore（空转）。
#   根因：_maximized_by_us 只在 _restore_from_maximize() 里清零，原生 restored 事件不改账；
#         window_state() 又用 `_maximized_by_us or window.maximized`，旧账永远压住真实状态。
print("\n" + "=" * 78)
print("【12】P1-06：原生 maximized/restored 事件同步记账（系统方式还原后状态正确）")
print("=" * 78)


class _StateAwareWindow(_MaxFakeWindow):
    """带 evaluate_js 的窗口桩：让 WebApp 的状态推送走正常路径而不是抛异常。"""

    def evaluate_js(self, *args, **kwargs):
        return True


webapp = WebApp(service=svc)
webapp._api = api

_reset_max_state()
native_win = _StateAwareWindow()
holder["window"] = native_win
webapp._window = native_win
_ok_max = api.window_action("toggle_maximize")
check("先由我们最大化（记账=True，window_state 报 True）",
      _ok_max is True and api._maximized_by_us is True
      and api.window_state()["maximized"] is True, str(api.window_state()))

# 模拟系统方式还原：原生 restored 事件到达（窗口属性同步变为 False）
native_win.maximized = False
webapp._on_window_native_state()
check("原生 restored 后记账清零", api._maximized_by_us is False,
      str(api._maximized_by_us))
check("★ 系统还原后 window_state()['maximized'] 必须为 False",
      api.window_state()["maximized"] is False, str(api.window_state()))

_max_before, _restore_before = native_win.max_calls, native_win.restore_calls
_next = api.window_action("toggle_maximize")
check("★ 再点按钮走 maximize（不空转 restore）",
      _next is True
      and native_win.max_calls == _max_before + 1
      and native_win.restore_calls == _restore_before,
      f"max={native_win.max_calls} restore={native_win.restore_calls}")

# 反向：用户用系统手段最大化 → 原生 maximized 事件 → 按钮也要显示"已最大化"
_reset_max_state()
native_win2 = _StateAwareWindow()
holder["window"] = native_win2
webapp._window = native_win2
native_win2.maximized = True
webapp._on_window_native_state(True)
check("原生 maximized 后记账为 True", api._maximized_by_us is True,
      str(api._maximized_by_us))
check("原生 maximized 后 window_state()['maximized'] 为 True",
      api.window_state()["maximized"] is True, str(api.window_state()))
check("再点按钮走 restore（不重复 maximize）",
      api.window_action("toggle_maximize") is True
      and native_win2.restore_calls == 1 and native_win2.max_calls == 0,
      f"max={native_win2.max_calls} restore={native_win2.restore_calls}")
_reset_max_state()

check("原生事件订阅按事件名显式传状态（不依赖 window.maximized 被回填）",
      '_bind_native_state("maximized", True)' in webview_src
      and '_bind_native_state("restored", False)' in webview_src)
check("window_state 以原生状态优先、记账兜底",
      "native_maximized = self._read_native_maximized(window)" in webview_src
      and "FormWindowState.Maximized" in webview_src)

# ── 13) P1-07：最大化前设置 MaximizedBounds（不盖任务栏）──
#   现状：frameless=True（FormBorderStyle.None）下裸调 window.maximize()，pywebview 只设
#   WindowState=Maximized；Windows 会把无边框窗体铺满整屏（含任务栏）。
print("\n" + "=" * 78)
print("【13】P1-07：最大化前设置 MaximizedBounds = Screen.WorkingArea")
print("=" * 78)
check("实现里给原生 Form 设置 MaximizedBounds 为当前屏幕 WorkingArea",
      "MaximizedBounds" in webview_src
      and "WorkingArea" in webview_src
      and "Screen.FromControl" in webview_src)
check("设置动作走现有 UI 线程调度（form.Invoke），失败只退回原最大化",
      "def _set_maximized_bounds" in webview_src
      and "设置 MaximizedBounds 失败（退回系统默认最大化）" in webview_src
      and "设置最大化边界失败（继续最大化）" in webview_src)
check("仍走原生 maximize（不改成 move/resize，也不用 toggle_fullscreen）",
      '_invoke_window_method(window, "maximize")' in webview_src
      and "window.move(" not in webview_src.split("def _toggle_maximize")[1].split("def _restore_from_maximize")[0]
      and ".toggle_fullscreen(" not in webview_src)

_reset_max_state()
_nobounds_win = _MaxFakeWindow()
holder["window"] = _nobounds_win
check("取不到 Form 时 _set_maximized_bounds 如实返回 False（不抛异常）",
      api._set_maximized_bounds(_nobounds_win) is False)

# 设置边界失败/抛异常都绝不能阻止最大化
_orig_set_bounds = JsApi._set_maximized_bounds


def _raise_bounds(self, window):
    raise RuntimeError("bounds unavailable")


JsApi._set_maximized_bounds = _raise_bounds
try:
    _reset_max_state()
    _bounds_fail_win = _MaxFakeWindow()
    holder["window"] = _bounds_fail_win
    _bounds_ok = api.window_action("toggle_maximize")
finally:
    JsApi._set_maximized_bounds = _orig_set_bounds
check("★ 设置 MaximizedBounds 抛异常时仍照常 maximize",
      _bounds_ok is True and _bounds_fail_win.max_calls == 1,
      f"{_bounds_ok} / max={_bounds_fail_win.max_calls}")
_reset_max_state()

# ── 14) P1-10：窗口动作失败原因如实回传（后端部分）──
#   前端新增 last_window_error()：mini / unsupported / restore_failed / ""。
print("\n" + "=" * 78)
print("【14】P1-10：last_window_error() 如实回传失败原因（bool 契约不变）")
print("=" * 78)
check("JsApi 暴露 last_window_error()", hasattr(api, "last_window_error"))

# ① 折叠态拒绝最大化 → "mini"
_reset_max_state()
holder["window"] = _MaxFakeWindow()
api._saved_geometry = (100, 200, 1180, 780)
_mini_ok = api.window_action("toggle_maximize")
check("折叠态拒绝最大化 → 'mini'",
      _mini_ok is False and api.last_window_error() == "mini",
      f"{_mini_ok} / {api.last_window_error()!r}")
api._saved_geometry = None

# ② maximize 不可用 → "unsupported"
_reset_max_state()
holder["window"] = _BrokenMaxWindow()
_unsup_ok = api.window_action("toggle_maximize")
check("maximize 抛异常/不可用 → 'unsupported'",
      _unsup_ok is False and api.last_window_error() == "unsupported",
      f"{_unsup_ok} / {api.last_window_error()!r}")

# ③ restore 与几何兜底都失败 → "restore_failed"
_reset_max_state()
holder["window"] = _AllBrokenWindow()
api.window_action("toggle_maximize")          # 先成功最大化
_rf_ok = api.window_action("toggle_maximize")  # 再还原：restore + move 都失败
check("restore 与几何兜底都失败 → 'restore_failed'",
      _rf_ok is False and api.last_window_error() == "restore_failed",
      f"{_rf_ok} / {api.last_window_error()!r}")

# ④ 成功 → ""，并且下一次动作开始时清空上一次原因
_reset_max_state()
holder["window"] = _MaxFakeWindow()
_ok_ok = api.window_action("toggle_maximize")
check("成功动作后 last_window_error() 为空串",
      _ok_ok is True and api.last_window_error() == "",
      f"{_ok_ok} / {api.last_window_error()!r}")
check("未知动作失败也给出原因（'unsupported'）",
      api.window_action("no_such_action") is False
      and api.last_window_error() == "unsupported",
      repr(api.last_window_error()))
_reset_max_state()

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：闪发折叠模式的几何、桥接契约与前端入口均符合约束")
sys.exit(0)
