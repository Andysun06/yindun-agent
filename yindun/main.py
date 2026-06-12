# -*- coding: utf-8 -*-
"""
隐盾 V2.2.0 — 🚀 全局点火总入口
只负责初始化应用程序环境、配置硬件级抗锯齿，并启动主舱体
"""
import sys
import os

# 强制本地连接，绕过任何可能导致连接超时的代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette, QColor

from yindun.gui.main_window import MainWindow
from yindun.gui.styles import GLOBAL_QSS


def main():
    # 1. 强行注入无损硬件缩放抗锯齿策略
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("隐盾V3.1.4demo")
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


if __name__ == "__main__":
    sys.exit(main())
