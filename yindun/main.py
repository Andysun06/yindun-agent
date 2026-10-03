# -*- coding: utf-8 -*-
"""隐盾 — 🚀 全局点火总入口

界面：**Web 界面**（pywebview + WebView2，本地文件加载、不起本地端口）。

历史说明：V3.3.2 之前有两套界面（PySide6 桌面版 + Web 版）。视图层重构完成后，
Qt 界面已整体移除（`yindun/gui/`），本入口只负责启动 Web 界面。
若需要旧界面，可从 git 历史取回（`git log -- yindun/gui`）。
"""
import os
import sys

# 强制本地连接，绕过任何可能导致连接超时的代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"


def run_web_ui() -> int:
    """启动 Web 界面（pywebview + WebView2）。"""
    from yindun.app.webview_app import run_web_ui as _run
    _run()
    return 0


def main() -> int:
    for arg in sys.argv[1:]:
        if arg in ("--qt", "--qt-ui"):
            print("Qt 界面已移除（视图层重构完成）。当前仅提供 Web 界面；"
                  "如需旧界面请从 git 历史取回 yindun/gui/。")
            return 2
    return run_web_ui()


if __name__ == "__main__":
    sys.exit(main())
