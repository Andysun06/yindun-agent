# -*- coding: utf-8 -*-
"""
隐盾 V2.0 — 🚀 全局点火总入口
只负责初始化应用程序环境、配置硬件级抗锯齿，并启动主舱体
"""
import sys
import os

# 强制本地连接，绕过任何可能导致连接超时的代理
os.environ["NO_PROXY"] = "localhost,127.0.0.1"

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette, QColor

# 🌟 跨模块导入：引入我们刚刚做好的前端主视窗与皮肤资产
from gui.main_window import MainWindow
from gui.styles import GLOBAL_QSS

def main():
    # 1. 强行注入无损硬件缩放抗锯齿策略，彻底消灭文字和圆角锯齿
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("隐盾V2.0")
    app.setStyle("Fusion")
    
    # 2. 全局主字体平滑度极致压榨 (适配 Windows 微软雅黑)
    font = QFont()
    font.setFamily('Microsoft YaHei')
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    app.setFont(font)
    
    # 3. 系统原生调色板基础调性融合
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(245, 246, 248))
    pal.setColor(QPalette.Base, QColor(255, 255, 255))
    app.setPalette(pal)
    
    # 4. 物理注入我们在第一步独立剥离出来的全局高定皮肤 QSS
    app.setStyleSheet(GLOBAL_QSS)
    
    # 5. 瞬间点火
    window = MainWindow()
    window.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())