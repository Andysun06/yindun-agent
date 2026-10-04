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

    def resize(self, nw, nh):
        self.calls.append(("resize", nw, nh))
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
check("界面不再声称「置顶需重启」",
      "置顶（立即生效）" in html and "重启生效）" not in html.split("btn-pin")[1][:80])
check("切换后按实际结果提示（含失败时的诚实降级）",
      "已置顶显示（立即生效）" in js and "置顶切换失败" in js)
check("设置面板的置顶开关同样即时生效",
      "立即生效）</div>" in html and "save_settings" in webview_src
      and "topmost" in webview_src.split("def save_settings")[1][:600])

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：闪发折叠模式的几何、桥接契约与前端入口均符合约束")
sys.exit(0)
