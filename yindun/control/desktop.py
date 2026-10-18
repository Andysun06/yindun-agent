# -*- coding: utf-8 -*-
"""隐盾控制面 · 桌面外壳（双击即用：把控制台开成一个原生窗口，而不是让你抄地址）

为什么要有这一层：
  控制面原本是"命令行起服务 + 浏览器打开地址 + 手抄 32 位管理令牌"，
  对 IT 管理员来说这套动作本身就是门槛——负责人实测反馈"控制端看不到界面"，
  真相是双击 exe 只弹了个黑控制台。管理员要的是**双击就有一个可操作的控制台**。
  V3.6.7 起连那个黑控制台也去掉了：exe 是无控制台形态，双击直接就是控制台窗口本身。

三种启动形态（同一套服务，只是承载方式不同）：
  · 桌面窗口（默认）：pywebview 开一个原生窗口指向本机控制台，并把令牌带进去自动登录；
  · 浏览器（--browser）：不给窗口，改为自动用默认浏览器打开地址（无 pywebview 时的兜底）；
  · 纯服务（--no-window）：只起 HTTP 服务（给"装在服务器上被别人访问"的场景）。

★ 无控制台形态下的话怎么说（V3.6.7）：窗口化 exe 里 `sys.stdout` / `sys.stderr` 都是 None，
  `print` 等于把话扔进黑洞。所以启动信息走 `say()`（无控制台时落 logs/yindun.log），
  启动失败走 `notify()`（弹系统对话框写清原因与日志路径）——**不做"双击没反应"的静默失败**。

★ 令牌边界：自动登录只发生在**本机回环**的窗口里——地址栏参数只在本机窗口出现，
  页面拿到后立刻从地址栏抹掉并只存 sessionStorage（关窗即失效）。
  远程用户仍然必须手输令牌；对外监听时的警告照旧打印。
"""
from __future__ import annotations

import sys
import threading
from typing import Optional, Tuple
from urllib.parse import quote

WINDOW_TITLE = "隐盾控制台 · 企业侧集中管理"


def has_console() -> bool:
    """当前进程有没有可写的控制台。

    为什么要问这个：控制端 exe **不再带黑命令行窗口**（V3.6.7，用户要求"不希望服务端还有个命令行"），
    PyInstaller 的 windowed 形态下 `sys.stdout` / `sys.stderr` 都是 None——此时 `print` 是**看得见才怪**，
    启动信息、失败原因都会凭空消失。凡是要"说给人听"的话都走 `say()` / `notify()`，不要直接 print。
    """
    return not (sys.stdout is None and sys.stderr is None)


def say(message: str) -> None:
    """有控制台就打印；没有（窗口化 exe / 无控制台运行）就写进日志，绝不静默丢掉。"""
    text = str(message)
    if has_console():
        try:
            print(text)
            return
        except Exception:                       # 控制台被关/编码异常 → 退回日志
            pass
    try:
        from yindun.utils.logger import get_logger
        get_logger("control").info("%s", text)
    except Exception:
        pass


def notify(title: str, message: str, error: bool = False) -> bool:
    """没有控制台时用系统弹窗把话说清楚（返回是否真的弹了）。

    窗口化 exe 最怕的失败模式是"双击了，什么都没发生"——用户不知道是没启动、还是启动了但没窗口。
    所以凡是"启动没成功""窗口打不开"这类必须让人知道的事，都弹一次原生对话框（内容含日志路径）。
    """
    if has_console():
        say(f"[{title}] {message}")
        return False
    try:
        import ctypes
        flags = 0x10 if error else 0x40            # MB_ICONERROR / MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(None, str(message), str(title), flags)
        return True
    except Exception:
        return False


def log_file_hint() -> str:
    """日志路径提示（弹窗/横幅里都带上：没有控制台时这是唯一的现场线索）。"""
    try:
        from yindun.utils.logger import current_log_file
        path = current_log_file()
        return str(path) if path else "logs/yindun.log"
    except Exception:
        return "logs/yindun.log"


class WindowBridge:
    """窗口动作桥（自绘标题栏按钮 → 原生窗口调用）。

    ★ 能力边界：这个桥**只动窗口本身**——最小化/最大化/关闭/读一下最大化状态。
      它不读控制面数据、不接受令牌、不接受路径或 URL 参数，因此即使页面被改坏，
      通过它拿到的最大能力也只是"动一下这个窗口"。控制面的鉴权仍在 HTTP 层，
      和这里无关（漏配桥不会放宽任何权限）。

    ★ 为什么要自己写按钮：无边框窗口（frameless）没有系统标题栏，不给按钮就关不掉、
      也最小化不了——那不是"好看"，是**功能缺失**。
    """

    ACTIONS = ("minimize", "toggle_maximize", "close")

    def __init__(self, window=None) -> None:
        self._window = window
        self._maximized = False

    # ── 由 open_window 调用 ───────────────────────
    def bind(self, window) -> None:
        """绑定真实窗口对象，并订阅最大化/还原事件以跟踪状态。"""
        self._window = window
        for evt_name, flag in (("maximized", True), ("restored", False), ("minimized", False)):
            try:
                event = getattr(window.events, evt_name, None)
                if event is not None:
                    event += (lambda _w=None, f=flag: self._mark(f))
            except Exception:
                continue

    def _mark(self, flag: bool) -> None:
        self._maximized = bool(flag)

    def window_state(self) -> dict:
        """按钮要用的状态：有没有窗口（决定要不要显示按钮）、当前是否最大化。"""
        try:
            return {"available": self._window is not None, "maximized": bool(self._maximized)}
        except Exception:
            return {"available": False, "maximized": False}

    def window_action(self, action: str) -> dict:
        """执行窗口动作。返回 {ok, action, maximized, reason}，前端据此切图标。

        未识别的动作一律拒绝（返回 ok=False），不做"猜一个最像的动作"——
        自绘按钮只有三个，出现第四个说明前端被改过，此时静默执行更危险。
        """
        act = str(action or "").strip().lower()
        if act not in self.ACTIONS:
            return {"ok": False, "action": act, "maximized": bool(self._maximized),
                    "reason": "unknown-action"}
        window = self._window
        if window is None:
            return {"ok": False, "action": act, "maximized": False, "reason": "no-window"}
        try:
            if act == "minimize":
                window.minimize()
            elif act == "toggle_maximize":
                self._toggle_maximize(window)
            else:
                window.destroy()
            return {"ok": True, "action": act, "maximized": bool(self._maximized)}
        except Exception as exc:
            say(f"[控制面] 窗口动作 {act} 失败：{type(exc).__name__}: {exc}")
            return {"ok": False, "action": act, "maximized": bool(self._maximized),
                    "reason": type(exc).__name__}

    def _toggle_maximize(self, window) -> None:
        """最大化/还原。pywebview 在 Windows 上提供 maximize/restore；
        个别版本缺失时退回 toggle_fullscreen（仍是"占满屏幕"，只是走全屏路径）。"""
        want_max = not self._maximized
        fn = getattr(window, "maximize" if want_max else "restore", None)
        if callable(fn):
            fn()
            self._mark(want_max)
            return
        toggler = getattr(window, "toggle_fullscreen", None)
        if callable(toggler):
            toggler()
            self._mark(want_max)
            return
        raise RuntimeError("当前 pywebview 版本没有最大化能力")



def console_url(host: str, port: int, token: str = "") -> str:
    """控制台地址。host 为 0.0.0.0/:: 时用 127.0.0.1 访问——监听地址本身不可访问。"""
    view_host = host if host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    url = f"http://{view_host}:{int(port)}/"
    clean = usable_token(token)
    if clean:
        # 令牌放在 fragment（#）而非 query：fragment 不会进服务器日志、也不随 Referer 外发
        url += "#token=" + quote(clean, safe="")
    return url


def usable_token(token: str) -> str:
    """把令牌收拾成"能放进 HTTP 头"的形态：去空白、只留单行 ASCII。

    为什么要有这道关：HTTP 头只能承载 ISO-8859-1，含中文/换行的字符串会让浏览器
    直接抛 "non-ISO-8859-1 code point"，页面只会显示"无法连接"。令牌来自文件读取，
    而文件是可能被手工编辑的——在这里挡一道，比让用户对着"连不上"猜要诚实得多。
    """
    text = str(token or "").strip()
    if "\n" in text or "\r" in text or any(ord(ch) > 127 for ch in text):
        say("[控制面] 令牌不是单行 ASCII，已忽略（请在控制台里手工输入）")
        return ""
    return text


def open_window(url: str, title: str = WINDOW_TITLE) -> bool:
    """用 pywebview 打开控制台窗口。返回 False 表示这条路不可用（调用方应兜底）。

    单例 + 阻塞：pywebview 的 GUI 循环必须在主线程，因此这里会阻塞到窗口关闭。

    无边框（V3.6.7）：窗口不带系统标题栏，改由控制台页面自己的顶栏承担拖动与
    最小化/最大化/关闭——与端点端（客户端）形态一致，两端看起来是一套产品。
    """
    try:
        import webview
    except Exception as exc:          # 冻结环境缺 WebView2 运行时会走到这里
        say(f"[控制面] 无法打开桌面窗口（{type(exc).__name__}: {exc}），改用浏览器")
        return False
    try:
        bridge = WindowBridge()
        window_kwargs = {"width": 1280, "height": 820, "min_size": (900, 600),
                         "frameless": True, "easy_drag": False, "js_api": bridge}
        try:
            # 与端点端同一套拖动机制：只有带 .pywebview-drag-region 的元素能拖动窗口，
            # 区域内任意位置都可拖（不是只有直接命中的那一个元素）。
            webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False
        except Exception:
            pass
        window = webview.create_window(title, url, **window_kwargs)
        bridge.bind(window)
        webview.start(debug=bool(__import__("os").environ.get("YINDUN_WEB_DEBUG")))
        return window is not None
    except Exception as exc:
        say(f"[控制面] 桌面窗口启动失败（{type(exc).__name__}: {exc}），改用浏览器")
        return False


def open_browser(url: str) -> bool:
    """兜底：用系统默认浏览器打开控制台。"""
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception as exc:
        say(f"[控制面] 打开浏览器失败：{exc}")
        return False


def serve_in_background(httpd) -> threading.Thread:
    """把 HTTP 服务放到后台线程（窗口模式下主线程要留给 GUI 循环）。"""
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.3},
                              daemon=True, name="yindun-control-http")
    thread.start()
    return thread


def stdout_banner(host: str, port: int, data_dir, token_file, token: str = "",
                  external: bool = False) -> None:
    """启动信息。令牌只在**未开窗口**时打印：窗口模式已经自动登录，没必要再往终端吐一次。

    窗口化 exe（无控制台）下这些信息通过 `say()` 落进日志文件——管理员看不到黑窗，
    但排障时要能查到"这次监听在哪个地址、数据目录在哪"。
    """
    no_console = not has_console()
    if no_console:
        say(f"日志文件   {log_file_hint()}")
    say("=" * 74)
    say("隐盾控制面 · 企业侧集中管理")
    say("=" * 74)
    say(f"  监听       http://{host}:{port}")
    say(f"  数据目录   {data_dir}")
    if token:
        say(f"  管理令牌   {token_file}")
        say(f"            {token}")
    if external:
        say("  ⚠ 已对外监听：本原型**不带 TLS**，请务必挂在 TLS 反向代理之后，并限制来源 IP。")
    say("  数据边界   控制面只接收掩码事件、链头与元数据；映射密钥在端点（DPAPI 绑定），")
    say("            控制面架构上无法还原明文。策略只能收紧，不能放行。")
    say("=" * 74)


def plan_launch(host: str, want_window: str) -> Tuple[str, str]:
    """决定承载方式：("window"|"browser"|"server", 说明)。

    规则：显式 --no-window → server；显式 --window/--browser → 照办；
    默认（auto）→ 非回环监听时只起服务（服务器场景不该弹窗口），回环则开窗口。
    单独抽出来是为了能在测试里直接断言这套决策，不必真的开窗口。
    """
    want = str(want_window or "auto").lower()
    if want == "no":
        return "server", "纯服务模式（--no-window）"
    if want == "browser":
        return "browser", "浏览器模式（--browser）"
    if want == "yes":
        return "window", "桌面窗口模式（--window）"
    if host not in ("127.0.0.1", "localhost", "::1", ""):
        return "server", "监听非回环地址，默认不弹窗口（如需请显式 --window）"
    return "window", "桌面窗口模式（默认）"
