# -*- coding: utf-8 -*-
"""隐盾 · 控制台界面回归（V3.6.7 起，本次扩充四条界面缺陷的回归位）

为什么要有它：控制台是**唯一**给 IT 管理员看的界面，却一直没有界面自动化——
改动只靠"手工开窗口点一遍"。V3.6.7 修的两个真实缺陷都是这个缺口漏出来的：
  · 页面往返访问后崩（事件台账 → 端点资产 → 回事件台账 → "加载失败：Cannot set
    properties of null"）：渲染器的"页面归属记账"不完整，重进时跳过了重建；
  · 无边框窗口没有窗口按钮：登录页既拖不动也关不掉。
本次又补了四条负责人反馈的界面缺陷（本套件新增 B7~B10 + A3 源码级约定）：
  · 宽窗口右半边空白（1180px 封顶）；顶栏拖不动（用了自造拖动类名，pywebview 不认）；
  · 「数据边界」四段长文挤在 212px 侧栏里；配对码只弹一条 toast，转述不出去。
这四条都只有真实 DOM/GUI 才暴露，所以本套件分三段：

  A 段（纯 Python，不需要 GUI）：真的执行 `desktop.open_window()`——把一个假 webview
     模块注入 sys.modules，断言它拿到的创建参数是"无边框 + 关闭全局拖动 + 挂了动作桥"，
     并逐条验证 WindowBridge 的动作分派（含"未知动作必须拒绝"）。不用 grep 源码，
     因为 grep 只能证明"字写在那儿"，证明不了"真的传下去了"。
  A3 段（纯 Python）：只做**静态约定**的钉死——拖动区类名必须与 pywebview 的
     DRAG_REGION_SELECTOR 一致、app.js 引用的浮窗 id 必须真的存在于 index.html。
  B 段（Playwright + 真实 Chromium）：打开控制台 #mock 沙盘，钉死
     ① 六个页面两两往返访问都不报错（上面那个真实缺陷的回归位）
     ② 自绘窗口按钮：没有桥就隐藏、有桥就出现、点了真的调用桥、最大化图标跟着状态切
     ③ 自绘下拉框：原生 select 仍是值容器（value + change 事件照旧派发）
     ④ 自制滚动条：真实量出滚动条宽度就是样式表里写的宽度
     ⑤ 宽窗口内容铺满视口 / 窄窗口不横向溢出（量真实几何）
     ⑥ 拖动区域用官方类名、双击顶栏最大化、点按钮不触发拖动
     ⑦ 数据边界默认收起、展开含全部关键声明
     ⑧ 配对码浮窗：码/倒计时/复制/自定义码/局域网地址与开关（全部走 #mock）

用法：python tests/test_control_ui.py        （退出码 0 = 全部通过）
依赖：pip install playwright && python -m playwright install chromium
      （没有 playwright 时 B 段跳过并打印"未验证"，A 段照跑）
"""
import re
import sys
import threading
import time
import types
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONSOLE_WEB = ROOT / "yindun" / "control" / "web"

from yindun.control import desktop  # noqa: E402

failures = []
LIMIT_SECONDS = 150


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ══════════════════════════════════════════════════════════════════════
# A 段 · 桌面外壳与窗口动作桥（假 webview，真执行）
# ══════════════════════════════════════════════════════════════════════
class _FakeEvent:
    """pywebview 的事件对象支持 `event += handler`（__iadd__），这里照抄那个协议。"""

    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self):
        for h in self.handlers:
            h()


class _FakeEvents:
    def __init__(self):
        self.maximized = _FakeEvent()
        self.minimized = _FakeEvent()
        self.restored = _FakeEvent()


class _FakeWindow:
    def __init__(self):
        self.events = _FakeEvents()
        self.calls = []

    def minimize(self):
        self.calls.append("minimize")

    def maximize(self):
        self.calls.append("maximize")

    def restore(self):
        self.calls.append("restore")

    def destroy(self):
        self.calls.append("destroy")


def _fake_webview():
    mod = types.ModuleType("webview")
    mod.settings = {}
    captured = {}

    def create_window(title, url, **kwargs):
        captured.update(kwargs)
        captured["title"] = title
        captured["url"] = url
        return captured["window"]

    def start(*args, **kwargs):
        captured["started"] = True

    mod.create_window = create_window
    mod.start = start
    mod.captured = captured
    return mod


def part_a() -> None:
    print("\n" + "=" * 78)
    print("【A】桌面外壳：无边框窗口 + 窗口动作桥（真的执行 open_window）")
    print("=" * 78)

    # ── A1 桥的能力边界：没窗口时一切动作都不崩、且明确拒绝 ──
    bridge = desktop.WindowBridge()
    st = bridge.window_state()
    check("无窗口时 window_state 报 available=False", st.get("available") is False, str(st))
    res = bridge.window_action("close")
    check("无窗口时动作被拒且带原因", res.get("ok") is False and res.get("reason") == "no-window", str(res))
    res = bridge.window_action("`rm -rf /`")
    check("未识别动作一律拒绝（不猜最像的那个）",
          res.get("ok") is False and res.get("reason") == "unknown-action", str(res))
    check("桥不暴露任何数据读写方法（只动窗口）",
          all(not hasattr(bridge, n) for n in ("read", "query", "token", "open", "eval")))

    # ── A2 三个动作真的落到窗口对象上 ──
    win = _FakeWindow()
    bridge = desktop.WindowBridge(win)
    bridge.bind(win)
    bridge.window_action("minimize")
    check("最小化 → window.minimize()", win.calls == ["minimize"], str(win.calls))
    r1 = bridge.window_action("toggle_maximize")
    check("最大化 → window.maximize() 且回传 maximized=True",
          win.calls[-1] == "maximize" and r1.get("maximized") is True, f"{win.calls} / {r1}")
    r2 = bridge.window_action("toggle_maximize")
    check("再点一次 → window.restore() 且回传 maximized=False",
          win.calls[-1] == "restore" and r2.get("maximized") is False, f"{win.calls} / {r2}")
    bridge.window_action("close")
    check("关闭 → window.destroy()", win.calls[-1] == "destroy", str(win.calls))

    # ── A2.6 置顶（V3.6.7：与端点端同一套四按钮：置顶 / 最小化 / 最大化 / 关闭）──
    check("桥的动作清单正好四个（多一个都等于多一份窗口能力）",
          tuple(desktop.WindowBridge.ACTIONS) == ("toggle_topmost", "minimize",
                                                  "toggle_maximize", "close"),
          str(desktop.WindowBridge.ACTIONS))
    top_win = _FakeWindow()
    top_bridge = desktop.WindowBridge(top_win)
    top_bridge.bind(top_win)
    r_on = top_bridge.window_action("toggle_topmost")
    check("置顶：写 window.on_top=True 且状态回传 topmost=True",
          getattr(top_win, "on_top", None) is True and r_on.get("topmost") is True, str(r_on))
    check("置顶状态能被 window_state() 读到（按钮据此点亮）",
          top_bridge.window_state().get("topmost") is True, str(top_bridge.window_state()))
    r_off = top_bridge.window_action("toggle_topmost")
    check("再点一次取消置顶：on_top=False 且回传 topmost=False",
          getattr(top_win, "on_top", None) is False and r_off.get("topmost") is False, str(r_off))

    class _TopFailWindow(_FakeWindow):
        """模拟"原生置顶失败"：属性写入直接抛异常。"""

        def __setattr__(self, name, value):
            if name == "on_top":
                raise RuntimeError("模拟原生置顶失败")
            super().__setattr__(name, value)

    fail_win = _TopFailWindow()
    fail_bridge = desktop.WindowBridge(fail_win)
    fail_bridge.bind(fail_win)
    r_fail = fail_bridge.window_action("toggle_topmost")
    check("置顶失败时如实回 ok=False（不假装成功，界面才不会亮着骗人）",
          r_fail.get("ok") is False and r_fail.get("reason") == "RuntimeError", str(r_fail))

    # ── A2.7 连点串行化（V3.6.12，与客户端同一处缺陷：并发写 WinForms TopMost 会假死）──
    class _SlowTopWindow(_FakeWindow):
        """置顶写入睡 0.15s，并记录同时进入 setter 的线程数（把竞态窗口放大）。"""

        def __init__(self):
            super().__init__()
            self._on_top = False
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

    slow_win = _SlowTopWindow()
    slow_bridge = desktop.WindowBridge(slow_win)
    slow_bridge.bind(slow_win)
    barrier = threading.Barrier(2)
    race = []

    def _click_top():
        barrier.wait()
        race.append(slow_bridge.window_action("toggle_topmost"))

    threads = [threading.Thread(target=_click_top) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    check("控制端两线程同时点置顶：两次都成功返回",
          len(race) == 2 and all(r.get("ok") is True for r in race), str(race))
    check("★ 控制端窗口动作最大并发进入数必须为 1（_win_lock 串行化）",
          slow_win.max_active == 1,
          f"max_active={slow_win.max_active} entries={slow_win.entries}")
    check("两次点按都真的落到窗口属性上，最终状态回到未置顶",
          slow_win.entries == 2 and slow_win.on_top is False
          and slow_bridge.window_state().get("topmost") is False,
          f"entries={slow_win.entries} on_top={slow_win.on_top}")

    # ── A3 状态也能被原生事件同步（用户用系统快捷键还原时，图标不能停在"最大化"）──
    win.events.restored.fire()
    check("原生 restored 事件把状态同步回 False",
          bridge.window_state().get("maximized") is False, str(bridge.window_state()))
    win.events.maximized.fire()
    check("原生 maximized 事件把状态同步为 True",
          bridge.window_state().get("maximized") is True, str(bridge.window_state()))

    # ── A4 老版本 pywebview 没有 maximize/restore 时退回全屏，不抛给用户 ──
    class _OldWindow:
        def __init__(self):
            self.calls = []
            self.events = _FakeEvents()

        def toggle_fullscreen(self):
            self.calls.append("toggle_fullscreen")

    old = _OldWindow()
    old_bridge = desktop.WindowBridge(old)
    r = old_bridge.window_action("toggle_maximize")
    check("缺 maximize 的版本退回 toggle_fullscreen（不静默失败）",
          old.calls == ["toggle_fullscreen"] and r.get("ok") is True, f"{old.calls} / {r}")

    # ── A5 open_window 真正传下去的参数 ──
    fake = _fake_webview()
    fake.captured["window"] = _FakeWindow()
    real_webview = sys.modules.get("webview")
    sys.modules["webview"] = fake
    try:
        ok = desktop.open_window("http://127.0.0.1:8787/", title="隐盾控制台")
    finally:
        if real_webview is not None:
            sys.modules["webview"] = real_webview
        else:
            sys.modules.pop("webview", None)
    cap = fake.captured
    check("open_window 成功返回（走的是真代码路径）", ok is True, str(cap))
    check("窗口无边框（frameless=True）", cap.get("frameless") is True, str(cap.get("frameless")))
    check("关掉 easy_drag（否则在输入框里按下也会拖走整个窗口）",
          cap.get("easy_drag") is False, str(cap.get("easy_drag")))
    check("挂了窗口动作桥（js_api=WindowBridge）", isinstance(cap.get("js_api"), desktop.WindowBridge),
          type(cap.get("js_api")).__name__)
    check("拖动只认 .pywebview-drag-region 区域（区域内任意位置可拖）",
          fake.settings.get("DRAG_REGION_DIRECT_TARGET_ONLY") is False, str(dict(fake.settings)))
    check("窗口尺寸与最小尺寸仍按管理员可用性给（不是无边就一味缩小）",
          cap.get("width") == 1280 and cap.get("min_size") == (900, 600), str(cap))
    check("pywebview 循环真的被启动了", cap.get("started") is True)


# ══════════════════════════════════════════════════════════════════════
# B 段 · 真实 Chromium 里的控制台界面
# ══════════════════════════════════════════════════════════════════════
class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def start_static_server() -> tuple:
    handler = partial(_QuietHandler, directory=str(CONSOLE_WEB))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


BRIDGE_STUB = """
window.__winCalls = [];
window.pywebview = { api: { window_action: (a) => {
  window.__winCalls.push(a);
  const maximized = a === "toggle_maximize" ? !window.__winMax : window.__winMax;
  window.__winMax = maximized;
  // 置顶同理：真实状态由桥维护并回传（前端只按回传值点亮按钮）
  const topmost = a === "toggle_topmost" ? !window.__winTop : window.__winTop;
  window.__winTop = topmost;
  return { ok: true, action: a, maximized, topmost };
} } };
"""


def part_a2() -> None:
    """无控制台形态（窗口化 exe）：启动信息不许静默丢失，启动失败不许静默无反应。"""
    print("\n" + "=" * 78)
    print("【A2】无控制台（窗口化 exe）下的信息出口")
    print("=" * 78)

    # ── A2-1 has_console / say：把两个流都置 None，模拟 PyInstaller windowed 形态 ──
    # 日志先按生产路径配好（控制面 main() 也在最前面 setup_logging）——否则"落进日志"无从断言
    import tempfile
    from yindun.utils.logger import setup_logging
    setup_logging(level="INFO", log_dir=str(Path(tempfile.mkdtemp(prefix="yindun_ctrlui_log_"))),
                  console=False)
    saved_out, saved_err = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        check("两个流都是 None 时判定为「无控制台」", desktop.has_console() is False)
        desktop.say("【A2】这行必须落进日志而不是消失")
        desktop.stdout_banner("127.0.0.1", 8787, "C:/demo/control_data",
                              "C:/demo/control_data/admin_token.txt", token="tok-demo", external=True)
        # notify 在没有 ctypes.windll 的环境（本测试机若是 Windows 会真弹窗，故用假模块拦住）
        fake_ctypes = types.ModuleType("ctypes")

        class _FakeUser32:
            def __init__(self):
                self.calls = []

            def MessageBoxW(self, hwnd, text, title, flags):
                self.calls.append((text, title, flags))
                return 1

        fake_ctypes.windll = types.SimpleNamespace(user32=_FakeUser32())
        real_ctypes = sys.modules.get("ctypes")
        sys.modules["ctypes"] = fake_ctypes
        try:
            shown = desktop.notify("隐盾控制台启动失败", "端口被占用", error=True)
        finally:
            if real_ctypes is not None:
                sys.modules["ctypes"] = real_ctypes
            else:
                sys.modules.pop("ctypes", None)
        check("无控制台时启动失败走系统弹窗（不是静默退出）", shown is True)
        msg, title, flags = fake_ctypes.windll.user32.calls[-1]
        check("弹窗带标题与原因，并用错误图标", "启动失败" in title and "端口被占用" in msg and flags == 0x10,
              f"{title} / {msg[:40]} / {flags}")
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err

    log_text = ""
    try:
        from yindun.utils.logger import current_log_file
        path = current_log_file()
        log_text = path.read_text(encoding="utf-8") if path and path.exists() else ""
    except Exception:
        pass
    check("无控制台时的 say() 内容确实落进日志（不是丢掉）",
          "这行必须落进日志而不是消失" in log_text, log_text[-160:] if log_text else "(无日志文件)")
    check("启动横幅在无控制台下也落进日志（含监听到的数据目录）",
          "C:/demo/control_data" in log_text and "监听" in log_text)

    # ── A2-2 main() 的兜底：启动抛异常也必须弹窗 + 非 0 退出码 ──
    from yindun.control import server as server_mod
    real_run = server_mod._run

    def _boom(argv=None):
        raise OSError("[WinError 10048] 通常每个套接字地址只允许使用一次")

    server_mod._run = _boom
    try:
        sys.stdout = None
        sys.stderr = None
        fake_ctypes = types.ModuleType("ctypes")
        fake_ctypes.windll = types.SimpleNamespace(user32=_FakeUser32())

        class _FakeUser32:
            def __init__(self):
                self.calls = []

            def MessageBoxW(self, hwnd, text, title, flags):
                self.calls.append((text, title, flags))
                return 1

        fake_ctypes.windll = types.SimpleNamespace(user32=_FakeUser32())
        real_ctypes = sys.modules.get("ctypes")
        sys.modules["ctypes"] = fake_ctypes
        try:
            code = server_mod.main(["--port", "8787"])
        finally:
            if real_ctypes is not None:
                sys.modules["ctypes"] = real_ctypes
            else:
                sys.modules.pop("ctypes", None)
    finally:
        server_mod._run = real_run
        sys.stdout, sys.stderr = saved_out, saved_err
    check("启动失败返回非 0 退出码", code == 2, str(code))
    box = fake_ctypes.windll.user32.calls
    check("启动失败弹窗说明原因 + 给出可照做的建议 + 日志路径",
          bool(box) and "10048" in box[-1][0] and "端口" in box[-1][0] and "日志" in box[-1][0],
          str(box[-1][:2])[:200] if box else "(没有弹窗)")

    # ── A2-4 单实例：端口上有活服务时拒绝重复启动 ──
    # 背景：Windows 上 SO_REUSEADDR 允许两个进程绑同一端口，双击两次 exe 会出现
    # "两个实例抢连接、用户一直看着旧版本界面"（V3.6.7 实测撞到：8787 上有旧实例）。
    import socket
    from yindun.control import server as server_mod2
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(4)
    busy_port = listener.getsockname()[1]
    try:
        check("端口上有活服务时判定为占用", server_mod2.port_in_use("127.0.0.1", busy_port) is True)
        idle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        idle.bind(("127.0.0.1", 0))
        idle_port = idle.getsockname()[1]
        idle.close()
        check("端口空着时不误判（否则正常双击都起不来）",
              server_mod2.port_in_use("127.0.0.1", idle_port) is False)
        check("端口 0（系统分配）不参与占用判定", server_mod2.port_in_use("127.0.0.1", 0) is False)
        check("对外监听地址按回环探测（0.0.0.0 也能探到本机监听者）",
              server_mod2.port_in_use("0.0.0.0", busy_port) is True)

        sys.stdout = None
        sys.stderr = None
        fake_ctypes = types.ModuleType("ctypes")
        _fake_u = _FakeUser32()
        fake_ctypes.windll = types.SimpleNamespace(user32=_fake_u)
        real_ctypes = sys.modules.get("ctypes")
        sys.modules["ctypes"] = fake_ctypes
        try:
            dup_code = server_mod2.main(["--port", str(busy_port)])
            dup_calls = list(_fake_u.calls)
        finally:
            if real_ctypes is not None:
                sys.modules["ctypes"] = real_ctypes
            else:
                sys.modules.pop("ctypes", None)
            # 断言前先把流恢复：否则 check() 的输出也进不了屏幕，这两条会"静默通过"
            sys.stdout, sys.stderr = saved_out, saved_err
        check("重复双击时拒绝启动并给出非 0 退出码（不是静默起第二个实例）",
              dup_code == 3, str(dup_code))
        check("弹窗说清「已经在运行」与「怎么处理」",
              bool(dup_calls) and "已经在运行" in dup_calls[-1][1]
              and "任务管理器" in dup_calls[-1][0],
              str(dup_calls[-1][:2])[:200] if dup_calls else "(无弹窗)")
    finally:
        sys.stdout, sys.stderr = saved_out, saved_err
        listener.close()

    # ── A2-3 打包开关：exe 必须是无控制台形态（这条只能在构建时生效，故钉在配置里）──
    spec = (ROOT / "tools" / "packaging" / "yindun_control.spec").read_text(encoding="utf-8")
    check("控制端 spec 关掉了控制台（console=False，双击不再弹黑命令行）",
          "console=False" in spec and "console=True" not in spec)
    check("控制端 spec 仍是单文件（onefile：EXE 直接带 binaries/datas、没有 COLLECT）",
          "COLLECT(" not in spec)
    build = (ROOT / "tools" / "packaging" / "build_control.py").read_text(encoding="utf-8")
    check("构建脚本的说明与真实形态一致（不再写「双击弹控制台窗口打印」）",
          "控制台窗口打印监听地址" not in build)


def part_a3_static() -> None:
    """源码级约定（不需要浏览器也能跑）：拖动区类名、浮窗 id 接线。

    为什么单列一段：B 段要装 playwright，而这两条约定**与浏览器无关**——
    拖动类名写错（自造 .pwdrag）时页面照样能打开，只有真拖窗口才发现；
    浮窗里少一个 id 会让某颗按钮变成死按钮。两条都值得在无 GUI 环境也钉住。
    """
    print("\n" + "=" * 78)
    print("【A3】控制台前端源码约定（拖动区类名 / 浮窗 id 接线）")
    print("=" * 78)
    html = (CONSOLE_WEB / "index.html").read_text(encoding="utf-8")
    js = (CONSOLE_WEB / "app.js").read_text(encoding="utf-8")
    css = (CONSOLE_WEB / "styles.css").read_text(encoding="utf-8")

    check("主界面顶栏用 pywebview 官方拖动类名（无边框窗口才拖得动）",
          re.search(r'<header class="topbar pywebview-drag-region"', html) is not None)
    check("登录页顶部条同样用官方拖动类名（登录前也要能拖窗口）",
          re.search(r'<div class="auth__bar pywebview-drag-region"', html) is not None)
    check("类名与 pywebview 默认选择器一致（DRAG_REGION_SELECTOR = .pywebview-drag-region）",
          ".pywebview-drag-region" in css and "pywebview-drag-region" in js)
    check("没有残留的自造拖动类 .pwdrag（HTML 里没有这个类名）",
          re.search(r'class="[^"]*\bpwdrag\b', html) is None)
    check("app.js/styles.css 不再把 .pwdrag 当选择器用（不留死代码）",
          '".pwdrag"' not in js and "'.pwdrag'" not in js and ".pwdrag {" not in css
          and ".pwdrag." not in css)
    # 报告 P1-04：只保留内容区/侧栏各自滚动，避免外层再滚一遍把整页滚走
    check("控制台只保留内容区/侧栏滚动（html/body 与 shell 不滚）",
          "html,body { height:100%; overflow:hidden; }" in css
          and "height:100%; overflow:hidden; }" in css)
    # 报告 P1-03：只读令牌可登录，界面上标明角色而不是当成坏令牌
    check("只读令牌可登录（whoami + 角色徽标 + 隐藏策略入口）",
          "/api/v1/whoami" in js and 'id="role-badge"' in html
          and ".is-viewer" in css and "state.role" in js)

    check("配对码浮窗是模态结构（role=dialog + aria-modal）",
          'id="pair-modal"' in html and 'role="dialog"' in html and 'aria-modal="true"' in html)
    used = set(re.findall(r'\$\("(pair-[a-z-]+)"\)', js))
    missing = sorted(i for i in used if f'id="{i}"' not in html)
    check(f"app.js 里引用的 {len(used)} 个 pair-* 元素在 index.html 里都存在（没有死按钮）",
          not missing, str(missing))
    # 真实后端 GET /api/v1/pair-code 在"没有有效码"时回的是 code:''（不是 404），
    # 界面必须说人话，而不是只显示一个「—」；接口缺失（404/网络）走 --bad 提示。
    check("没有可用配对码时给出可读提示（不是只显示「—」）",
          "当前没有有效配对码" in js and "还没有可用配对码" in js)
    check("接口缺失/读不到监听状态时给出可读提示（不静默失败）",
          "配对码接口不可用" in js and "监听状态接口不可用" in js)
    check("浮窗里没有写死产品版本号当数据（版本由服务端注入）", "{{VERSION}}" not in js)


def part_b() -> str:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:                                            # noqa: BLE001
        print(f"\n  ⚠ 未安装 playwright，B 段跳过（**跳过 ≠ 通过**）：{exc}")
        return "skipped"

    httpd, port = start_static_server()
    url = f"http://127.0.0.1:{port}/index.html#mock"
    print(f"\n控制台沙盘：{url}")
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(headless=True)
            except Exception as exc:                                    # noqa: BLE001
                print(f"  ⚠ Chromium 起不来，B 段跳过（先跑 python -m playwright install chromium）：{exc}")
                return "skipped"
            try:
                run_pages(browser, url)
                run_layout(browser, url)
                run_auth_screen(browser, url)
                run_content_width(browser, url)
                run_drag_region(browser, url)
                run_boundary(browser, url)
                run_pair_modal(browser, url)
            finally:
                browser.close()
    finally:
        httpd.shutdown()
        httpd.server_close()
    return "ran"


def run_pages(browser, url: str) -> None:
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page_errors, console_errors = [], []
    page.on("pageerror", lambda e: page_errors.append(str(e)))
    page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

    page.goto(url, wait_until="load")
    page.wait_for_selector("#content .metrics, #content section", timeout=15000)
    page.wait_for_timeout(300)

    # ── B1 六个页面两两往返：任何一次都不许出现错误框 ──
    print("\n" + "=" * 78)
    print("【B1】页面往返访问（本次真实缺陷的回归位：切走再切回不许崩）")
    print("=" * 78)
    pages = ["overview", "events", "endpoints", "policy", "usage", "stats"]
    order = pages + list(reversed(pages)) + ["events", "endpoints", "events", "policy", "events"]
    bad = []
    for name in order:
        page.click(f'.nav__item[data-page="{name}"]')
        page.wait_for_timeout(260)
        err_box = page.eval_on_selector_all("#content .err-box", "els => els.map(e => e.textContent)")
        painted = page.eval_on_selector("#content", "el => el.dataset.page")
        if err_box or painted != name:
            bad.append(f"{name}: err={err_box} painted={painted}")
    check(f"往返访问 {len(order)} 次（含重复回访）都不出现错误框", not bad, "; ".join(bad[:3]))
    check("往返后无未捕获异常", not page_errors, "; ".join(page_errors[:2]))

    # ── B2 自绘窗口按钮 ──
    print("\n" + "=" * 78)
    print("【B2】自绘窗口按钮（有桥才显示；点了真的调用桥）")
    print("=" * 78)
    check("浏览器形态（无 pywebview 桥）按钮整组隐藏",
          page.eval_on_selector_all(".winctrl", "els => els.every(e => e.hidden)"))
    groups = page.eval_on_selector_all(".winctrl", "els => els.length")
    check("真按钮集齐**四枚**（置顶/最小化/最大化/关闭）× 登录页与主界面",
          all(page.eval_on_selector_all(f'.winctrl__btn[data-win="{k}"]', "e => e.length") == groups
              for k in ("top", "min", "max", "close")),
          f"组数 {groups}")
    order = page.eval_on_selector_all("#winctrl .winctrl__btn", "els => els.map(e => e.dataset.win)")
    check("四个按钮的顺序是 置顶→最小化→最大化→关闭（与端点端一致）",
          order == ["top", "min", "max", "close"], str(order))
    check("每个按钮都有可读的 title/aria-label（自绘按钮没有系统提示，别让用户猜）",
          page.eval_on_selector_all("#winctrl .winctrl__btn",
                                    "els => els.every(e => e.title && e.getAttribute('aria-label'))"))
    check("拖动区域用的是官方类名 .pywebview-drag-region（无边框窗口才拖得动）",
          page.eval_on_selector_all(".pywebview-drag-region", "els => els.length") >= 2,
          str(page.eval_on_selector_all(".pywebview-drag-region", "els => els.length")))

    page2 = browser.new_page(viewport={"width": 1366, "height": 900})
    page2.add_init_script(BRIDGE_STUB)
    page2.goto(url, wait_until="load")
    page2.wait_for_selector("#content section", timeout=15000)
    page2.wait_for_timeout(500)                       # 等桥探测轮询（120ms 一拍）
    check("桌面窗口形态（桥就位）按钮出现",
          page2.eval_on_selector_all(".winctrl", "els => els.every(e => !e.hidden)"))
    page2.click('#winctrl .winctrl__btn[data-win="min"]')
    page2.wait_for_timeout(120)
    page2.click('#winctrl .winctrl__btn[data-win="max"]')
    page2.wait_for_timeout(120)
    calls = page2.evaluate("() => window.__winCalls")
    check("点最小化/最大化真的调用了桥（动作名与服务端约定一致）",
          calls == ["minimize", "toggle_maximize"], str(calls))
    # 置顶（V3.6.7）：动作名、点亮状态、以及"记住这个偏好"
    page2.click('#winctrl .winctrl__btn[data-win="top"]')
    page2.wait_for_timeout(150)
    top_calls = page2.evaluate("() => window.__winCalls")
    check("点置顶调的是 toggle_topmost（不是前端自己翻转图标）",
          top_calls[-1] == "toggle_topmost", str(top_calls))
    check("置顶后按钮点亮（且只有真的置顶才亮——状态取自桥返回值）",
          page2.eval_on_selector('.winctrl__btn[data-win="top"]', "el => el.classList.contains('is-on')"))
    check("置顶偏好写进本地存储（下次打开控制台自动恢复）",
          page2.evaluate("() => localStorage.getItem('yd.topmost')") == "1",
          str(page2.evaluate("() => localStorage.getItem('yd.topmost')")))
    page2.click('#winctrl .winctrl__btn[data-win="top"]')
    page2.wait_for_timeout(150)
    check("再点一次取消置顶：按钮熄灭 + 偏好记为关",
          not page2.eval_on_selector('.winctrl__btn[data-win="top"]', "el => el.classList.contains('is-on')")
          and page2.evaluate("() => localStorage.getItem('yd.topmost')") == "0")
    check("最大化后图标切到还原态（按钮不会一直显示'最大化'）",
          page2.eval_on_selector('.winctrl__btn[data-win="max"]', "el => el.classList.contains('is-max')"))
    page2.click('#winctrl .winctrl__btn[data-win="max"]')
    page2.wait_for_timeout(120)
    check("再点一次回到还原态（图标与状态同步）",
          not page2.eval_on_selector('.winctrl__btn[data-win="max"]', "el => el.classList.contains('is-max')"))
    page2.click('#winctrl .winctrl__btn[data-win="close"]')
    page2.wait_for_timeout(120)
    check("关闭按钮走的是 close 动作（不在前端擅自关窗口）",
          page2.evaluate("() => window.__winCalls.slice(-1)[0]") == "close",
          str(page2.evaluate("() => window.__winCalls")))
    page2.close()

    # ── B3 自绘下拉框 ──
    print("\n" + "=" * 78)
    print("【B3】自绘下拉框（原生 select 仍是值容器：value 与 change 照旧）")
    print("=" * 78)
    page.click('.nav__item[data-page="events"]')
    page.wait_for_timeout(320)
    check("三个筛选下拉都换成了自绘控件",
          page.eval_on_selector_all("#content .dd", "e => e.length") == 3,
          str(page.eval_on_selector_all("#content .dd", "e => e.length")))
    check("原生 select 仍在（没被替换掉，值语义不变）",
          all(page.query_selector(f"#{i}") is not None for i in ("f-ep", "f-type", "f-sev")))
    check("原生 select 视觉隐藏（opacity 0，且不吃鼠标）",
          page.eval_on_selector("#f-sev", "el => getComputedStyle(el).opacity") == "0" and
          page.eval_on_selector("#f-sev", "el => getComputedStyle(el).pointerEvents") == "none",
          page.eval_on_selector("#f-sev", "el => getComputedStyle(el).opacity + '/' + "
                                          "getComputedStyle(el).pointerEvents"))
    check("弹层默认收起", page.eval_on_selector_all(".dd__list", "els => els.every(e => e.hidden)"))

    sev_dd = "#content .dd:has(> #f-sev)"
    page.evaluate("""() => { window.__changes = 0;
      document.getElementById('f-sev').addEventListener('change', () => { window.__changes++; }); }""")
    page.click(f"{sev_dd} .dd__btn")
    page.wait_for_timeout(160)
    check("点按钮才展开", not page.eval_on_selector(f"{sev_dd} .dd__list", "el => el.hidden"))
    check("其它下拉仍收起（同时只开一个）",
          page.eval_on_selector_all("#content .dd__list", "els => els.filter(e => !e.hidden).length") == 1,
          str(page.eval_on_selector_all("#content .dd__list", "els => els.filter(e => !e.hidden).length")))
    check("展开的正是被点的那个（弹层挂在同一个 .dd 里，不会串位置）",
          page.eval_on_selector(f"{sev_dd} .dd__btn", "el => el.getAttribute('aria-expanded')") == "true")
    opts = page.eval_on_selector_all(f"{sev_dd} .dd__list .dd__opt",
                                     "els => els.map(e => e.textContent.trim())")
    check("级别下拉四项与内核口径一致（提示/警告/严重/安全）",
          opts == ["全部级别", "提示", "警告", "严重", "安全"], str(opts))
    page.click(f"{sev_dd} .dd__list .dd__opt >> text=严重")
    page.wait_for_timeout(200)
    check("选值写回原生 select（filter 逻辑读到的就是它）",
          page.eval_on_selector("#f-sev", "el => el.value") == "critical",
          page.eval_on_selector("#f-sev", "el => el.value"))
    check("派发原生 change 事件（页面 onchange 照旧触发）",
          page.evaluate("() => window.__changes") == 1,
          str(page.evaluate("() => window.__changes")))
    check("按钮上的字跟着变", page.eval_on_selector(f"{sev_dd} .dd__btn span",
                                                    "el => el.textContent") == "严重")
    check("选完自动收起", page.eval_on_selector(f"{sev_dd} .dd__list", "el => el.hidden"))
    check("按钮上记着当前选中项（无障碍可读）",
          page.eval_on_selector(f"{sev_dd} .dd__opt[aria-selected='true']",
                                "el => el.textContent.trim()") == "严重")
    page.click('.nav__item[data-page="policy"]')
    page.wait_for_timeout(320)
    # ── 策略页的"点选式"（负责人反馈：手写 glob/逗号分隔的学习成本太高）──
    # 这里钉的是**界面与语义**：勾场景＝替用户写 glob、首屏就要看得见候选、多选端点逐台签发。
    # 提交出去的真实请求体（预设展开成哪些 glob）由真机用例核对过：实测 body 为
    #   {"scope":"ep-verify2","bundle":{"sensitive_paths":[8 条工资相关 glob],"approval_floor":"confirm"}}
    # 且端点侧 `GET /api/v1/policy?endpoint_id=…` 能读回同一份、带签名。
    pres = page.eval_on_selector_all("#p-presets input[data-preset]", "els => els.map(e => e.dataset.preset)")
    check("敏感路径给了预设场景库（不再是让人手写 glob 的空文本域）",
          len(pres) >= 6 and page.query_selector("#p-presets .presets__group") is not None, str(pres))
    check("每个场景都写明会拦到什么（展开后的 glob 可见）",
          page.eval_on_selector_all("#p-presets .preset__globs code", "els => els.length") >= 8)
    page.click('#p-presets input[data-preset="payroll"]')
    page.wait_for_timeout(200)
    chip = page.inner_text("#p-path-count")
    pv = page.inner_text("#p-preview")
    check("勾场景后当场给出条数（不是让人自己数 glob）",
          "已选 1 个场景" in chip and "条" in chip, f"{chip!r}")
    check("预览里能看到展开后的实际 glob（提交的就是这些）",
          "/薪资" in pv and pv.count("*") >= 4, pv[:80])
    page.click("#p-adv summary")
    page.wait_for_timeout(150)
    page.fill("#p-adv-paths", "D:/自定义目录/*")
    page.wait_for_timeout(200)
    check("高级里仍可自定义 glob，且与预设并集（两者都进预览）",
          "D:/自定义目录/*" in page.inner_text("#p-preview"))
    page.click('#p-floor label:has(input[value="confirm"])')
    page.wait_for_timeout(200)
    check("审批下限是三段式点选（不改变 / 至少人工确认 / 一律拒绝）",
          page.eval_on_selector_all("#p-floor label.seg__opt", "els => els.length") == 3
          and "至少人工确认" in page.inner_text("#p-preview"))
    check("模型/插件候选区首屏就画出来（空候选时也要有可读说明，不是两个白框）",
          page.query_selector("#p-models-host .pick, #p-models-host .hint") is not None
          and page.query_selector("#p-plugins-host .pick, #p-plugins-host .hint") is not None)
    page.fill("#p-models-add", "qwen3.5:4b"); page.click("#p-models-add-btn")
    page.wait_for_timeout(200)
    check("手动补一个模型也能进允许集（候选里没有的名字照样能填）",
          "qwen3.5:4b" in page.inner_text("#p-preview"))
    picks_before = page.eval_on_selector_all("#p-models-host .picked__lead", "els => els.map(e=>e.textContent)")
    check("已选项有可见清单（能看出本次会下发什么）", bool(picks_before), str(picks_before)[:80])
    # 下发范围：全部端点与逐台互斥，逐台清空时自动回到"全部端点"
    page.click("#p-scope-global")
    page.wait_for_timeout(120)
    page.check(".p-scope-ep")
    page.wait_for_timeout(120)
    eps_checked = page.eval_on_selector_all(".p-scope-ep:checked", "els => els.length")
    check("勾了某台端点后「全部端点」自动取消（不会同时签两种范围）",
          eps_checked >= 1 and not page.is_checked("#p-scope-global"), f"checked={eps_checked}")
    page.uncheck(".p-scope-ep")
    page.wait_for_timeout(120)
    check("逐台一个都不剩时回到「全部端点」（界面上不会静默变成空范围）",
          page.is_checked("#p-scope-global"))
    deny_sum = page.inner_text("#content details.denied-fold > summary")
    check("「控制面无法下发的项」收在折叠里，摘要就说清是「只收紧」的边界",
          "无法下发的项" in deny_sum, deny_sum[:60])
    page.click("#content details.denied-fold > summary")
    page.wait_for_timeout(250)
    deny_txt = page.inner_text("#content details.denied-fold")
    check("展开后六类禁发键与边界说明都在册（一字未删，只是收起来了）",
          all(k in deny_txt for k in ("anonymize", "exempt", "bypass", "auto_approve", "whitelist",
                                      "disable_audit")) and "脱敏豁免" in deny_txt, deny_txt[:80])
    # ★ 顶栏「自动」到底是什么：负责人问过"这个开关和自动是什么意思"——文案得自己说清楚
    auto_text = page.inner_text("#auto-refresh-text")
    check("自动刷新开关自解释（文案写明周期，不用悬停猜）", "10" in auto_text and "秒" in auto_text, auto_text)
    page.click("#auto-refresh-switch")      # 原生 checkbox 被样式隐藏（opacity:0），要点可见的开关本体
    page.wait_for_timeout(250)
    check("关掉后文案如实变（并说明还能手动刷新）",
          "关" in page.inner_text("#auto-refresh-text"), page.inner_text("#auto-refresh-text"))
    page.click("#auto-refresh-switch")
    page.wait_for_timeout(250)
    check("开启后页脚能看到刷新节奏（上次/下次），本页不生效时也如实说明",
          "刷新" in page.inner_text("#foot-stamp"), page.inner_text("#foot-stamp")[:60])
    # ★ 开关（严格模式）：曾被 `.form-row > label{display:block}` 压成 0 宽——行内盒子上 width 无效，
    #   用户看到的是"一条看不出来的灰边"。这里量真实几何 + 真点一下，钉住"开关是开关"。
    sw = page.evaluate("""() => {
      const s = document.querySelector('.form-row .switch');
      const t = s.querySelector('span');
      const r = t.getBoundingClientRect();
      return { w: Math.round(r.width), h: Math.round(r.height),
               before: s.querySelector('input').checked };
    }""")
    check("严格模式开关的轨道是正常尺寸（不是 0 宽的一条线）",
          sw["w"] >= 28 and sw["h"] >= 16, str(sw))
    page.click("#content .form-row .switch")
    page.wait_for_timeout(300)
    sw2 = page.evaluate("""() => {
      const s = document.querySelector('.form-row .switch');
      const t = s.querySelector('span');
      return { checked: s.querySelector('input').checked,
               bg: getComputedStyle(t).backgroundColor,
               knob: getComputedStyle(t, '::after').transform };
    }""")
    check("点一下真的切换：勾选态改变 + 轨道变强调色 + 滑块位移",
          sw2["checked"] != sw["before"] and sw2["bg"] == "rgb(22, 161, 99)"
          and "14" in sw2["knob"], str(sw2))

    # ── B4 自制滚动条 ──
    print("\n" + "=" * 78)
    print("【B4】自制滚动条")
    print("=" * 78)
    page.click('.nav__item[data-page="events"]')
    page.wait_for_timeout(320)
    # 为什么不去量"内容宽 - 可视宽"：Windows/Chromium 的 **overlay 滚动条**（无头模式下默认开）
    # 根本不占布局宽度，量的永远是 0——拿它当判据会得出"样式没生效"的错误结论（本套件初版就踩过）。
    # 改为读**伪元素的计算样式**：这是浏览器真正用来绘制滚动条的那份值，
    # 既能证明规则被应用、又能证明它跟着主题令牌走（而不是写死的浅灰）。
    bar = page.evaluate("""() => {
      const el = document.querySelector('.content');
      const bar = getComputedStyle(el, '::-webkit-scrollbar');
      const thumb = getComputedStyle(el, '::-webkit-scrollbar-thumb');
      const button = getComputedStyle(el, '::-webkit-scrollbar-button');
      const cs = getComputedStyle(document.documentElement);
      return { w: bar.width, track: bar.backgroundColor, thumb: thumb.backgroundColor,
               radius: thumb.borderRadius, clip: thumb.backgroundClip, button: button.display,
               token: cs.getPropertyValue('--border-2').trim(),
               firefox: getComputedStyle(el).scrollbarWidth,
               theme: document.documentElement.dataset.theme };
    }""")
    check("滚动条宽度 = 样式表里定的 10px", bar["w"] == "10px", str(bar))
    check("轨道透明（不要 Windows 那块灰底）",
          bar["track"] in ("rgba(0, 0, 0, 0)", "transparent"), str(bar["track"]))
    check("滑块圆头 + 内缩（与主产品同一套观感）",
          bar["radius"] == "999px" and bar["clip"] == "content-box", f"{bar['radius']} / {bar['clip']}")
    check("上下箭头按钮已去掉（Windows 原生外观的最后一块）", bar["button"] == "none", str(bar["button"]))
    check("Firefox 侧也声明了（scrollbar-width: thin）", bar["firefox"] == "thin", str(bar["firefox"]))

    def _hex_to_rgb(value: str) -> str:
        v = value.lstrip("#")
        return "rgb(%d, %d, %d)" % tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))

    check("浅色下滑块颜色取自主题令牌 --border-2（不是写死的灰）",
          bar["thumb"] == _hex_to_rgb(bar["token"]), f"{bar['thumb']} vs {bar['token']}")
    page.click("#btn-theme")
    page.wait_for_timeout(260)
    dark = page.evaluate("""() => {
      const el = document.querySelector('.content');
      const t = getComputedStyle(el, '::-webkit-scrollbar-thumb').backgroundColor;
      const token = getComputedStyle(document.documentElement).getPropertyValue('--border-2').trim();
      return { thumb: t, token, theme: document.documentElement.dataset.theme };
    }""")
    check("深色下滑块跟着令牌变（切换主题不留浅色滚动条）",
          dark["theme"] == "dark" and dark["thumb"] == _hex_to_rgb(dark["token"])
          and dark["thumb"] != bar["thumb"],
          f"{bar['thumb']} → {dark['thumb']} / token {dark['token']}")
    page.click("#btn-theme")
    page.wait_for_timeout(200)

    print("\n" + "=" * 78)
    check("全过程无 JS 运行时错误", not page_errors, "; ".join(page_errors[:2]))
    real_console = [m for m in console_errors if "favicon" not in m]
    check("无控制台报错（favicon 除外）", not real_console, "; ".join(real_console[:2]))


def run_layout(browser, url: str) -> None:
    """矮窗口下的布局：侧栏底部不许被裁掉，整页不许跟着滚。

    「数据边界」块（本次起是"标题 + 主结论 + 收起细节"）在这一段只验**能不能滚到**，
    内容完整性由 run_boundary 单独验。
    """
    print("\n" + "=" * 78)
    print("【B5】矮窗口布局（侧栏可滚、整页不滚）")
    print("=" * 78)
    for height in (1000, 800, 620):
        page = browser.new_page(viewport={"width": 1360, "height": height})
        page.goto(url, wait_until="load")
        page.wait_for_selector("#content section", timeout=15000)
        page.wait_for_timeout(500)
        m = page.evaluate("""() => {
          const rail = document.querySelector('.rail');
          const main = document.querySelector('.main');
          return { railH: rail.clientHeight, mainH: main.clientHeight,
                   bodyScrolls: document.documentElement.scrollHeight > window.innerHeight + 1,
                   vh: window.innerHeight };
        }""")
        check(f"{height}px 高：侧栏与主区都正好等于视口高（grid 行被约束住）",
              m["railH"] == height and m["mainH"] == height, str(m))
        check(f"{height}px 高：整页不出现「外层滚动」（滚动发生在栏内而不是 body）",
              not m["bodyScrolls"], str(m))
        if height == 620:
            page.evaluate("() => { const r = document.querySelector('.rail'); r.scrollTop = r.scrollHeight; }")
            page.wait_for_timeout(200)
            tail = page.evaluate("""() => {
              const box = document.querySelector('#boundary');
              const r = box.getBoundingClientRect();
              return { title: document.querySelector('.boundary__head').textContent.trim(),
                       bottom: Math.round(r.bottom), vh: window.innerHeight };
            }""")
            check("窗口不够高时侧栏能滚到底（数据边界可见，不是被裁掉）",
                  tail["bottom"] <= tail["vh"] + 1, str(tail))
        page.close()


def run_auth_screen(browser, url: str) -> None:
    """登录页：无边框窗口下第一屏也要能拖、能关。"""
    print("\n" + "=" * 78)
    print("【B6】登录页的拖动区与窗口按钮")
    print("=" * 78)
    base = url.split("/index.html")[0] + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(base, wait_until="load")
    page.wait_for_selector("#auth:not([hidden])", timeout=10000)
    page.wait_for_timeout(300)
    check("登录页有拖动区域（否则无边框窗口第一屏就拖不动）",
          page.query_selector(".auth__bar.pywebview-drag-region") is not None)
    check("登录页的窗口按钮默认隐藏（浏览器里点了没反应，就不该显示）",
          page.eval_on_selector(".auth__bar .winctrl", "el => el.hidden"))
    page.close()

    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.add_init_script(BRIDGE_STUB)
    page.goto(base, wait_until="load")
    page.wait_for_selector("#auth:not([hidden])", timeout=10000)
    page.wait_for_timeout(500)
    check("桌面窗口形态下登录页按钮出现",
          page.eval_on_selector(".auth__bar .winctrl", "el => !el.hidden"))
    page.click('.auth__bar .winctrl__btn[data-win="min"]')
    page.wait_for_timeout(150)
    check("登录页的最小化按钮真的调到桥",
          page.evaluate("() => window.__winCalls") == ["minimize"],
          str(page.evaluate("() => window.__winCalls")))
    page.close()


def run_content_width(browser, url: str) -> None:
    """【B7】内容用满宽度（宽窗口）/ 不横向溢出（窄窗口）——本次修。"""
    print("\n" + "=" * 78)
    print("【B7】内容宽度（宽窗口铺满、窄窗口不溢出）")
    print("=" * 78)
    for width, height in ((1920, 1000), (1600, 900)):
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="load")
        page.wait_for_selector("#content .metrics", timeout=15000)
        page.wait_for_timeout(400)
        m = page.evaluate("""() => {
          const sec = document.querySelector('#content section');
          const box = document.querySelector('#content');
          const table = document.querySelector('#content .table-wrap table');
          const metrics = document.querySelector('.metrics');
          const card = document.querySelector('.metric');
          return { secW: Math.round(sec.getBoundingClientRect().width),
                   metricsW: Math.round(metrics.getBoundingClientRect().width),
                   cardW: Math.round(card.getBoundingClientRect().width),
                   tableW: table ? Math.round(table.getBoundingClientRect().width) : 0,
                   valuePx: parseFloat(getComputedStyle(document.querySelector('.metric__value')).fontSize),
                   vw: window.innerWidth,
                   docOverflow: document.documentElement.scrollWidth - window.innerWidth,
                   boxOverflow: box.scrollWidth - box.clientWidth };
        }""")
        ratio = m["secW"] / m["vw"]
        check(f"{width}px 宽：内容宽度 ≥ 视口 80%（不再是 1180px 封顶、右半边空白）",
              ratio >= 0.8, f"{m['secW']}/{m['vw']} = {ratio:.2f}")
        check(f"{width}px 宽：内容真的比旧的 1180px 上限宽", m["secW"] > 1180, str(m["secW"]))
        check(f"{width}px 宽：台账表格铺满内容区（越宽越好读）",
              m["tableW"] >= m["secW"] - 2, f"table={m['tableW']} section={m['secW']}")
        check(f"{width}px 宽：指标条与内容同宽（右边缘对齐，不留空档）",
              abs(m["metricsW"] - m["secW"]) <= 2, f"metrics={m['metricsW']} section={m['secW']}")
        check(f"{width}px 宽：宽屏下指标卡不空荡（内边距与字号抬了一档）",
              m["valuePx"] >= 34 if width >= 1680 else m["valuePx"] <= 30,
              f"valuePx={m['valuePx']} cardW={m['cardW']}")
        check(f"{width}px 宽：内容区与整页都没有横向溢出",
              m["boxOverflow"] <= 1 and m["docOverflow"] <= 1, str(m))
        page.close()

    for width, height in ((1024, 768), (1000, 800)):
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url, wait_until="load")
        page.wait_for_selector("#content section", timeout=15000)
        page.wait_for_timeout(300)
        bad = []
        for name in ("overview", "events", "endpoints", "policy", "usage", "stats"):
            # 窄窗口下 <1080px 时侧栏整条隐藏（导航按钮不可见），用 DOM click 切页——
            # 这里要验的是**布局宽度**，不是"能不能点导航"。
            page.evaluate("(n) => document.querySelector(`.nav__item[data-page=\"${n}\"]`).click()", name)
            page.wait_for_timeout(220)
            over = page.evaluate("""() => {
              const box = document.querySelector('#content');
              return { doc: document.documentElement.scrollWidth - window.innerWidth,
                       box: box.scrollWidth - box.clientWidth };
            }""")
            if over["doc"] > 1 or over["box"] > 1:
                bad.append(f"{name}: {over}")
        check(f"{width}px 宽（<1080px）：六个页面都没有横向溢出（窄窗口不出现左右拖动条）",
              not bad, "; ".join(bad[:3]))
        page.close()


def run_drag_region(browser, url: str) -> None:
    """【B8】拖动区域：官方类名 + 双击最大化 + 点按钮不触发拖动（本次修）。"""
    print("\n" + "=" * 78)
    print("【B8】顶栏拖动（负责人实测拖不动的回归位）")
    print("=" * 78)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.goto(url, wait_until="load")
    page.wait_for_selector("#content section", timeout=15000)
    page.wait_for_timeout(300)
    check("主界面顶栏带官方拖动类名 .pywebview-drag-region",
          page.eval_on_selector(".main .topbar",
                                "el => el.classList.contains('pywebview-drag-region')"))
    check("页面上没有任何元素带自造类名 pwdrag（旧类名已彻底下线）",
          page.eval_on_selector_all("[class*=pwdrag]", "els => els.length") == 0)
    check("拖动区里仍是可交互的顶栏（按钮/开关都在，拖窗不等于占满整条）",
          page.eval_on_selector(".main .topbar", "el => !!el.querySelector('button') && "
                                                 "!!el.querySelector('.switch')"))
    page.close()

    base = url.split("/index.html")[0] + "/"
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(base, wait_until="load")
    page.wait_for_selector("#auth:not([hidden])", timeout=10000)
    check("登录页顶部条带官方拖动类名（登录前也能拖窗口）",
          page.eval_on_selector(".auth__bar",
                                "el => el.classList.contains('pywebview-drag-region')"))
    page.close()

    page = browser.new_page(viewport={"width": 1366, "height": 900})
    page.add_init_script(BRIDGE_STUB)
    page.goto(url, wait_until="load")
    page.wait_for_selector("#content section", timeout=15000)
    page.wait_for_timeout(500)                       # 等桥探测轮询（120ms 一拍）
    check("桌面窗口形态：拖动区禁用了文本选中（拖动不会顺手选中顶栏文字）",
          page.eval_on_selector(".main .topbar", "el => getComputedStyle(el).userSelect") == "none")
    calls0 = len(page.evaluate("() => window.__winCalls"))
    page.dblclick(".topbar__title")
    page.wait_for_timeout(220)
    calls = page.evaluate("() => window.__winCalls")
    check("双击顶栏空白处 = 最大化/还原（Windows 肌肉记忆）",
          calls[-1:] == ["toggle_maximize"] and len(calls) == calls0 + 1, str(calls))
    # 交互控件上的 mousedown 不能落到 body（pywebview 的拖动监听挂在 body 上）——
    # 早先它会把"点刷新/点配对码"也当成按住顶栏，窗口跟着鼠标走。
    page.evaluate("""() => { window.__md = [];
      document.body.addEventListener('mousedown', (e) => window.__md.push(e.target.id || e.target.tagName)); }""")
    page.click("#btn-refresh")
    page.wait_for_timeout(200)
    md_button = page.evaluate("() => window.__md.slice()")
    check("点顶栏按钮：mousedown 被就地拦下（不会触发窗口拖动）",
          md_button == [], str(md_button))
    page.evaluate("() => { window.__md = []; }")
    page.click(".topbar__title")
    page.wait_for_timeout(200)
    md_bar = page.evaluate("() => window.__md.slice()")
    check("点顶栏空白处：mousedown 照常传到底（拖动区仍然可拖）",
          any(x in ("page-title", "page-sub", "topbar__title", "H2", "P", "DIV") for x in md_bar),
          str(md_bar))
    page.close()


def run_boundary(browser, url: str) -> None:
    """【B9】数据边界：一句标题 + 一句主结论 + 默认收起的细节（本次重排）。"""
    print("\n" + "=" * 78)
    print("【B9】数据边界（不再把四段长文挤进 212px 侧栏）")
    print("=" * 78)
    page = browser.new_page(viewport={"width": 1360, "height": 800})
    page.goto(url, wait_until="load")
    page.wait_for_selector("#content section", timeout=15000)
    page.wait_for_timeout(300)
    d = page.evaluate("""() => {
      const more = document.querySelector('#boundary-more');
      const lead = document.querySelector('.boundary__lead');
      const list = more.querySelector('.boundary__list');
      return { tag: more.tagName.toLowerCase(), open: more.open,
               boxH: Math.round(document.querySelector('#boundary').getBoundingClientRect().height),
               // ★ 不能用 getBoundingClientRect() 判断"收起时看不见"：Chromium 把 closed details
               //   的内容按 content-visibility 隐藏，**布局盒仍在**（实测 151px），
               //   量的高度会得出"还没收起"的错误结论。checkVisibility() 才是用户视角。
               listVisible: list.checkVisibility ? list.checkVisibility() : true,
               lead: lead.textContent, leadB: getComputedStyle(lead.querySelector('b')).display,
               head: document.querySelector('.boundary__head').textContent.trim(),
               mark: document.querySelector('.boundary__mark').textContent.trim(),
               railW: Math.round(document.querySelector('.rail').getBoundingClientRect().width) };
    }""")
    check("细节用原生 <details> 且默认收起", d["tag"] == "details" and d["open"] is False, str(d))
    check("收起时细节列表对用户不可见、边界块只占一小块（侧栏不再被长文挤满）",
          d["listVisible"] is False and d["boxH"] < 150, str(d))
    check("醒目标题「数据边界」+ ? 气泡就位", "数据边界" in d["head"] and d["mark"] == "?", str(d))
    check("主结论一句话常显且保留关键事实（掩码事件 / 链头 / 元数据 / 不多一个字）",
          all(k in d["lead"] for k in ("控制面只看到", "掩码事件", "链头", "元数据", "不多一个字")),
          d["lead"])
    check("主结论里的 <b> 仍是行内强调（句子不会被拆成一行一个词）", d["leadB"] == "inline", d["leadB"])
    check("侧栏宽度仍是 212px 量级（重排没有偷偷加宽侧栏）", 200 <= d["railW"] <= 240, str(d["railW"]))

    page.click("#boundary-more > summary")
    page.wait_for_timeout(250)
    state = page.evaluate("""() => {
      const more = document.querySelector('#boundary-more');
      const list = more.querySelector('.boundary__list');
      return { open: more.open,
               boxH: Math.round(document.querySelector('#boundary').getBoundingClientRect().height),
               listVisible: list.checkVisibility ? list.checkVisibility() : true,
               text: list.textContent };
    }""")
    check("点开后细节真的展开（open=true、内容可见、边界块明显变高）",
          state["open"] is True and state["listVisible"] is True and state["boxH"] > d["boxH"] + 100,
          str(state))
    claims = {"占位符": "敏感路径落盘前已掩码", "不经过控制面": "提示词与回答不走控制面",
              "DPAPI": "映射密钥在端点由 DPAPI 保管", "无法还原明文": "控制面不能解密",
              "只能收紧": "策略单向", "脱敏豁免": "豁免没有通路", "审批放行": "放行没有通路",
              "下发通路": "明确写下 没有任何下发通路"}
    for kw, desc in claims.items():
        check(f"展开后保留关键声明：{desc}（含「{kw}」）", kw in state["text"], state["text"][:80])
    page.close()

    # 矮窗口：展开后滚到底仍能看到最后一句话（不能被裁在视口外）
    page = browser.new_page(viewport={"width": 1360, "height": 620})
    page.goto(url, wait_until="load")
    page.wait_for_selector("#content section", timeout=15000)
    page.click("#boundary-more > summary")
    page.wait_for_timeout(250)
    page.evaluate("() => { const r = document.querySelector('.rail'); r.scrollTop = r.scrollHeight; }")
    page.wait_for_timeout(200)
    tail = page.evaluate("""() => {
      const li = document.querySelectorAll('.boundary__list li');
      const r = li[li.length - 1].getBoundingClientRect();
      return { text: li[li.length - 1].textContent.slice(0, 8), bottom: Math.round(r.bottom),
               vh: window.innerHeight };
    }""")
    check("620px 高：展开后滚到底，最后一条声明完整可见（没有被裁掉）",
          tail["bottom"] <= tail["vh"] + 1, str(tail))
    page.close()


def run_pair_modal(browser, url: str) -> None:
    """【B10】配对码浮窗：码 / 倒计时 / 复制 / 自定义 / 局域网（全部走 #mock）。"""
    print("\n" + "=" * 78)
    print("【B10】配对码浮窗（不再只是一条 toast）")
    print("=" * 78)
    page = browser.new_page(viewport={"width": 1366, "height": 900})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url, wait_until="load")
    page.wait_for_selector("#content section", timeout=15000)
    page.wait_for_timeout(300)
    check("浮窗默认不显示（不挡界面）", page.eval_on_selector("#pair-modal", "el => el.hidden"))
    page.click("#btn-pair-code")
    page.wait_for_timeout(500)
    check("点顶栏「配对码」打开浮窗", page.eval_on_selector("#pair-modal", "el => !el.hidden"))
    code = page.eval_on_selector("#pair-code", "el => el.textContent.trim()")
    check("浮窗里显示当前配对码（#mock 数据）", code == "A1B2C3", code)
    check("配对码是大字（≥32px：屋内念、拍照、截图都看得清）",
          page.eval_on_selector("#pair-code", "el => parseFloat(getComputedStyle(el).fontSize)") >= 32,
          page.eval_on_selector("#pair-code", "el => getComputedStyle(el).fontSize"))
    meta1 = page.eval_on_selector("#pair-meta", "el => el.textContent")
    check("显示到期时间与有效期倒计时", "到期" in meta1 and "剩 " in meta1, meta1)
    page.wait_for_timeout(1300)
    meta2 = page.eval_on_selector("#pair-meta", "el => el.textContent")
    check("倒计时真的在走（不是一张静态图）",
          meta2 != meta1 or "已过期" in meta2, f"{meta1} → {meta2}")
    btns = page.evaluate("""() => ['pair-regen', 'pair-custom-toggle', 'pair-copy'].map((id) => {
      const el = document.getElementById(id);
      return el && el.offsetParent !== null ? el.textContent.trim() : null; })""")
    check("三颗按钮在册可见：[重新生成] / [自定义…] / [复制]",
          btns == ["重新生成", "自定义…", "复制配对码"], str(btns))
    page.click("#pair-copy")
    page.wait_for_timeout(400)
    tst = page.eval_on_selector("#toast", "el => ({ hidden: el.hidden, text: el.textContent })")
    check("点「复制」给出明确反馈（成功=已复制；剪贴板被拒=提示手工复制，不谎报）",
          not tst["hidden"] and "复制" in tst["text"], str(tst))

    page.click("#pair-custom-toggle")
    page.wait_for_timeout(200)
    check("「自定义…」展开输入行", page.eval_on_selector("#pair-custom", "el => !el.hidden"))
    page.fill("#pair-custom-code", "1!x")
    page.click("#pair-custom-submit")
    page.wait_for_timeout(250)
    msg = page.evaluate("""() => ({ text: document.getElementById('pair-msg').textContent,
                                    bad: document.getElementById('pair-msg').classList.contains('pair__note--bad'),
                                    code: document.getElementById('pair-code').textContent.trim() })""")
    check("非法自定义码被拦下并说明规则（4–12 位 [A-Za-z0-9-]，不是静默失败）",
          "4–12" in msg["text"] and msg["bad"], str(msg))
    check("非法输入没有改掉当前码", msg["code"] == code, str(msg))
    page.fill("#pair-custom-code", "MY-CODE-9")
    page.click("#pair-custom-submit")
    page.wait_for_timeout(450)
    cust = page.evaluate("""() => {
      const chip = document.getElementById('pair-state');
      return { code: document.getElementById('pair-code').textContent.trim(),
               chipHidden: chip.hidden, chipText: chip.textContent,
               formHidden: document.getElementById('pair-custom').hidden };
    }""")
    check("自定义码提交成功并显示出来（走 #mock 的 POST /pair-code）",
          cust["code"] == "MY-CODE-9", str(cust))
    check("自定义码带标记（不会与随机码混淆）+ 提交后输入行收起",
          (not cust["chipHidden"]) and "自定义" in cust["chipText"] and cust["formHidden"],
          str(cust))
    page.click("#pair-regen")
    page.wait_for_timeout(450)
    code2 = page.eval_on_selector("#pair-code", "el => el.textContent.trim()")
    check("「重新生成」换出新码且格式合规（4–12 位 [A-Za-z0-9-]）",
          code2 != "MY-CODE-9" and re.fullmatch(r"[A-Za-z0-9-]{4,12}", code2) is not None, code2)

    lan = page.evaluate("""() => {
      const secs = document.querySelectorAll('.pair__sec');
      return { url: document.getElementById('pair-url').textContent.trim(),
               attr: document.getElementById('pair-url').dataset.url || '',
               on: document.getElementById('pair-lan').checked,
               chip: document.getElementById('pair-listen').textContent,
               note: document.getElementById('pair-lan-note').textContent,
               fw: document.getElementById('pair-fw').textContent,
               sec: secs[0].textContent,
               steps: [...document.querySelectorAll('.pair__steps li')].map((e) => e.textContent) };
    }""")
    check("局域网区显示可复制的地址（未开启时只有本机回环）",
          lan["url"] == "http://127.0.0.1:8787/" and lan["attr"] == lan["url"], str(lan))
    check("开关默认关（不默认对外监听）+ 当前监听状态如实显示",
          lan["on"] is False and "仅本机" in lan["chip"], str(lan))
    check("有防火墙提示（首次会弹 Windows 询问）", "防火墙" in lan["fw"], lan["fw"])
    check("有不带 TLS、仅在可信局域网使用的安全说明 + 用后作废",
          "TLS" in lan["sec"] and "可信局域网" in lan["sec"] and "作废" in lan["sec"], lan["sec"][:120])
    check("「别人怎么用」三步说清楚（地址+码 → 浏览器打开 → 输入码）",
          len(lan["steps"]) == 3 and "地址和配对码" in lan["steps"][0]
          and "浏览器" in lan["steps"][1] and "输入配对码" in lan["steps"][2], str(lan["steps"]))
    page.click(".pair__row .switch")
    page.wait_for_timeout(600)
    lan2 = page.evaluate("""() => ({
      url: document.getElementById('pair-url').textContent.trim(),
      on: document.getElementById('pair-lan').checked,
      chip: document.getElementById('pair-listen').textContent,
      note: document.getElementById('pair-lan-note').textContent })""")
    check("打开开关 → 地址切到局域网地址（走 #mock 的 /listen + /network）",
          lan2["on"] is True and lan2["url"] == "http://192.168.96.148:8787/", str(lan2))
    check("监听状态与说明同步为「对外监听」（开关与显示联动）",
          "对外监听" in lan2["chip"] and "局域网" in lan2["note"], str(lan2))
    page.click(".pair__row .switch")
    page.wait_for_timeout(600)
    lan3 = page.evaluate("""() => ({ url: document.getElementById('pair-url').textContent.trim(),
                                     on: document.getElementById('pair-lan').checked })""")
    check("再关回去回到仅本机（开关是双向的，不会卡在对外）",
          lan3["on"] is False and "127.0.0.1" in lan3["url"], str(lan3))

    page.keyboard.press("Escape")
    page.wait_for_timeout(250)
    check("Esc 关闭浮窗", page.eval_on_selector("#pair-modal", "el => el.hidden"))
    check("浮窗交互全程无 JS 运行时错误", not errors, "; ".join(errors[:2]))
    page.close()


def main() -> int:
    started = time.time()
    print("=" * 78)
    print("隐盾 · 控制台界面回归（V3.6.7）")
    print("=" * 78)
    part_a()
    part_a2()
    part_a3_static()
    status = part_b()
    print("\n" + "=" * 78)
    if status == "skipped":
        print("⚠ B 段（浏览器界面）未运行：缺 playwright，界面部分**未被验证**")
    if failures:
        print(f"❌ 控制台界面回归失败：{len(failures)} 项")
        for item in failures:
            print("   ·", item)
        return 1
    used = time.time() - started
    if used > LIMIT_SECONDS and status == "ran":
        print(f"⚠ 用时 {used:.0f}s，超过 {LIMIT_SECONDS}s 预算（不影响判定，供留意）")
    print(f"✅ 控制台界面回归通过（{used:.0f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
