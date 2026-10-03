# -*- coding: utf-8 -*-
"""隐盾 — 🚀 全局点火总入口

界面选择：
  · 默认        → Web 界面（pywebview + WebView2；阶段 2 迁移完成，界面能力已对齐）
  · `--qt`      → 旧的 PySide6 桌面界面（保留为回退：自定义模型配置、健康扫描、
                  行为画像、工作流自定义等功能尚未迁移到 Web 界面）
  · `YINDUN_UI=qt` 环境变量等价于 `--qt`

Qt 相关导入放在函数内部（延迟导入）：默认路径完全不加载 PySide6，
因此打包时可以整体排除 Qt（exe 明显瘦身），Qt 代码只在显式 `--qt` 时才需要。
"""
import os
import sys

# 强制本地连接，绕过任何可能导致连接超时的代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"
os.environ["no_proxy"] = "localhost,127.0.0.1"


def _use_qt_ui(argv) -> bool:
    if "--qt" in argv or "--qt-ui" in argv:
        return True
    if "--web" in argv or "--web-ui" in argv:
        return False
    return (os.environ.get("YINDUN_UI", "").strip().lower() == "qt")


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
    if _use_qt_ui(sys.argv[1:]):
        return run_qt_ui()
    return run_web_ui()


if __name__ == "__main__":
    sys.exit(main())
