# -*- coding: utf-8 -*-
"""隐盾 — 🚀 全局点火总入口

界面选择（视图层重构的过渡期支持双界面）：
  · 默认        → PySide6 桌面界面（功能最全，含工作流/审计/知识库面板）
  · `--web`     → Web 界面（pywebview + WebView2；阶段 1 骨架，逐页迁移中）
  · `YINDUN_UI=web` 环境变量等价于 `--web`

Qt 相关导入放在函数内部（延迟导入）：Web 界面路径完全不依赖 PySide6，
这也为阶段 3 彻底移除 Qt 依赖留好了口子。
"""
import os
import sys

# 强制本地连接，绕过任何可能导致连接超时的代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"


def _use_web_ui(argv) -> bool:
    if "--web" in argv or "--web-ui" in argv:
        return True
    if "--qt" in argv:
        return False
    return (os.environ.get("YINDUN_UI", "").strip().lower() == "web")


def run_qt_ui() -> int:
    """PySide6 桌面界面（当前主力）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPalette
    from PySide6.QtWidgets import QApplication

    from yindun.gui.main_window import MainWindow
    from yindun.gui.styles import GLOBAL_QSS

    # 1. 无损硬件缩放抗锯齿
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("隐盾安全智能体")
    app.setStyle("Fusion")

    # 2. 全局主字体
    font = QFont()
    font.setFamily('Microsoft YaHei')
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    app.setFont(font)

    # 3. 调色板
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(245, 246, 248))
    pal.setColor(QPalette.Base, QColor(255, 255, 255))
    app.setPalette(pal)

    # 4. 全局 QSS 皮肤
    app.setStyleSheet(GLOBAL_QSS)

    # 5. 点火
    window = MainWindow()
    window.show()
    return app.exec()


def run_web_ui() -> int:
    """Web 界面（pywebview + WebView2，本地文件加载，不起本地端口）。"""
    from yindun.app.webview_app import run_web_ui as _run
    _run()
    return 0


def main() -> int:
    if _use_web_ui(sys.argv[1:]):
        return run_web_ui()
    return run_qt_ui()


if __name__ == "__main__":
    sys.exit(main())
