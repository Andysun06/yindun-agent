# -*- coding: utf-8 -*-
"""隐盾控制面 · 桌面外壳（双击即用：把控制台开成一个原生窗口，而不是让你抄地址）

为什么要有这一层：
  控制面原本是"命令行起服务 + 浏览器打开地址 + 手抄 32 位管理令牌"，
  对 IT 管理员来说这套动作本身就是门槛——负责人实测反馈"控制端看不到界面"，
  真相是双击 exe 只弹了个黑控制台。管理员要的是**双击就有一个可操作的控制台**。

三种启动形态（同一套服务，只是承载方式不同）：
  · 桌面窗口（默认）：pywebview 开一个原生窗口指向本机控制台，并把令牌带进去自动登录；
  · 浏览器（--browser）：不给窗口，改为自动用默认浏览器打开地址（无 pywebview 时的兜底）；
  · 纯服务（--no-window）：只起 HTTP 服务（给"装在服务器上被别人访问"的场景）。

★ 令牌边界：自动登录只发生在**本机回环**的窗口里——地址栏参数只在本机窗口出现，
  页面拿到后立刻从地址栏抹掉并只存 sessionStorage（关窗即失效）。
  远程用户仍然必须手输令牌；对外监听时的警告照旧打印。
"""
from __future__ import annotations

import threading
from typing import Optional, Tuple
from urllib.parse import quote

WINDOW_TITLE = "隐盾控制台 · 企业侧集中管理"


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
        print("[控制面] 令牌不是单行 ASCII，已忽略（请在控制台里手工输入）")
        return ""
    return text


def open_window(url: str, title: str = WINDOW_TITLE) -> bool:
    """用 pywebview 打开控制台窗口。返回 False 表示这条路不可用（调用方应兜底）。

    单例 + 阻塞：pywebview 的 GUI 循环必须在主线程，因此这里会阻塞到窗口关闭。
    """
    try:
        import webview
    except Exception as exc:          # 冻结环境缺 WebView2 运行时会走到这里
        print(f"[控制面] 无法打开桌面窗口（{type(exc).__name__}: {exc}），改用浏览器")
        return False
    try:
        window = webview.create_window(title, url, width=1280, height=820,
                                       min_size=(900, 600))
        webview.start(debug=bool(__import__("os").environ.get("YINDUN_WEB_DEBUG")))
        return window is not None
    except Exception as exc:
        print(f"[控制面] 桌面窗口启动失败（{type(exc).__name__}: {exc}），改用浏览器")
        return False


def open_browser(url: str) -> bool:
    """兜底：用系统默认浏览器打开控制台。"""
    try:
        import webbrowser
        return bool(webbrowser.open(url))
    except Exception as exc:
        print(f"[控制面] 打开浏览器失败：{exc}")
        return False


def serve_in_background(httpd) -> threading.Thread:
    """把 HTTP 服务放到后台线程（窗口模式下主线程要留给 GUI 循环）。"""
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.3},
                              daemon=True, name="yindun-control-http")
    thread.start()
    return thread


def stdout_banner(host: str, port: int, data_dir, token_file, token: str = "",
                  external: bool = False) -> None:
    """启动信息。令牌只在**未开窗口**时打印：窗口模式已经自动登录，没必要再往终端吐一次。"""
    print("=" * 74)
    print("隐盾控制面 · 企业侧集中管理")
    print("=" * 74)
    print(f"  监听       http://{host}:{port}")
    print(f"  数据目录   {data_dir}")
    if token:
        print(f"  管理令牌   {token_file}")
        print(f"            {token}")
    if external:
        print("  ⚠ 已对外监听：本原型**不带 TLS**，请务必挂在 TLS 反向代理之后，并限制来源 IP。")
    print("  数据边界   控制面只接收掩码事件、链头与元数据；映射密钥在端点（DPAPI 绑定），")
    print("            控制面架构上无法还原明文。策略只能收紧，不能放行。")
    print("=" * 74)


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
