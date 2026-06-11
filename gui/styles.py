# -*- coding: utf-8 -*-
"""
隐盾 V2.1.0 — 皮肤资产库
专门存储控制窗口半透明、现代下拉框、✔号复选框、以及按钮尺寸锁定的全局 QSS 样式
"""

# 绿色硬核打勾图标的内嵌数据流
CK = "data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTIiIGhlaWdodD0iMTIiIHZpZXdCb3g9IjAgMCAxMiAxMiIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMiA2TDUgOUwxMCAzIiBzdHJva2U9IndoaXRlIiBzdHJva2Utd2lkdGg9IjIiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg=="

GLOBAL_QSS = """
QWidget#mainWindow { background: transparent; }

/* 主体外壳圆角白卡片 */
QFrame#container {
    background: white; border: 1px solid rgba(0,0,0,18); border-radius: 22px;
}

/* 顶部无边框标题栏 */
QFrame#titleBar { background: transparent; border: none; border-top-left-radius: 22px; border-top-right-radius: 22px; }
QLabel#titleIcon { font-size: 16px; background: transparent; border: none; }
QLabel#titleText { color: #1a1a2e; font-size: 13px; font-weight: 700; background: transparent; border: none; }

/* 固定并统一顶部栏控制按钮尺寸 (24x24 黄金对齐) */
QPushButton#titleBtn {
    background: transparent; border: none; border-radius: 12px; font-size: 14px; color: #aaa;
    min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
}
QPushButton#titleBtn:hover { background: rgba(0,0,0,10); color: #666; }
QPushButton#closeBtn {
    background: transparent; border: none; border-radius: 12px; font-size: 15px; color: #bbb;
    min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
}
QPushButton#closeBtn:hover { background: #ff5f57; color: white; }

/* 聊天历史大厅 */
QTextBrowser#chatArea { background: #f5f6f8; border: none; }
QLabel#statusLabel { color: #8895a7; font-size: 11px; background: transparent; padding: 2px 0; border: none; }

/* 下置复合多模态控制台底座 */
QFrame#controlDock {
    background: #f0f2f5; border: none; border-top: 1px solid rgba(0,0,0,6);
    border-bottom-left-radius: 22px; border-bottom-right-radius: 22px;
}
QLineEdit#inputLine {
    background: white; border: 1.5px solid #e0e3ea; border-radius: 18px;
    padding: 9px 16px; font-size: 13px; color: #222;
}
QLineEdit#inputLine:focus { border-color: #07c160; background: #fff; }
QLineEdit#inputLine::placeholder { color: #b0b8c9; }

/* 绿色一键发射按钮 */
QPushButton#sendBtn {
    background: #07c160; color: white; border: none; border-radius: 18px;
    font-size: 15px; font-weight: bold; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
}
QPushButton#sendBtn:hover { background: #06ad56; }
QPushButton#sendBtn:pressed { background: #059a4c; }
QPushButton#sendBtn:disabled { background: #c5cde0; }

/* 挂载本地文件按钮 */
QPushButton#fileBtn {
    background: white; border: 1px solid #e0e3ea; border-radius: 12px;
    padding: 3px 10px; font-size: 11px; color: #666; min-height: 26px; max-height: 26px;
}
QPushButton#fileBtn:hover { border-color: #07c160; color: #07c160; }

/* ---- 现代高定下拉选择框 ---- */
QComboBox#modeSelector, QComboBox#settingCombo {
    background: white; border: 1.5px solid #e0e3ea; border-radius: 12px;
    padding: 4px 30px 4px 12px; font-size: 12px; color: #333;
    min-height: 30px; max-height: 30px;
}
QComboBox#modeSelector:hover, QComboBox#settingCombo:hover { border-color: #07c160; }
QComboBox#modeSelector:focus, QComboBox#settingCombo:focus { border-color: #07c160; }
QComboBox#modeSelector::drop-down, QComboBox#settingCombo::drop-down {
    border: none; width: 28px; subcontrol-position: center right;
}
QComboBox#modeSelector::down-arrow, QComboBox#settingCombo::down-arrow {
    image: url(data:image/svg+xml;base64,PHN2ZyB3aWR0aD0iMTAiIGhlaWdodD0iMTAiIHZpZXdCb3g9IjAgMCAxMCAxMCIgZmlsbD0ibm9uZSIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBkPSJNMSAzTDUgN0w5IDMiIHN0cm9rZT0iIzg4OTVhNyIgc3Ryb2tlLXdpZHRoPSIxLjUiIHN0cm9rZS1saW5lY2FwPSJyb3VuZCIgc3Ryb2tlLWxpbmVqb2luPSJyb3VuZCIvPjwvc3ZnPg==);
    width: 10px; height: 10px;
}
QComboBox#modeSelector QAbstractItemView, QComboBox#settingCombo QAbstractItemView {
    background: white; border: 1px solid #e8eaed; border-radius: 12px;
    padding: 4px; outline: none; font-size: 12px;
}
QComboBox#modeSelector QAbstractItemView::item, QComboBox#settingCombo QAbstractItemView::item {
    padding: 7px 12px; border-radius: 8px; min-height: 20px;
}
QComboBox QAbstractItemView::item:selected { background: #f0faf4; color: #07c160; }
QComboBox QAbstractItemView::item:hover { background: #f5f6f8; }

/* ---- 高定制隔离网关复选框样式（未选为空框，选中带打勾对齐✔） ---- */
QCheckBox#settingCheck { font-size: 12px; color: #444; spacing: 8px; background: transparent; }
QCheckBox#settingCheck::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 2px solid #cbd5e1; background: white;
}
QCheckBox#settingCheck::indicator:hover { border-color: #07c160; }
QCheckBox#settingCheck::indicator:checked {
    background: #07c160; border-color: #07c160;
    image: url(""" + CK + """);
}

/* ---- 🌟 优化：横向不透明度拖动条 (QSlider) 专属工业样式表 (已物理根治换行断裂缺陷) ---- */
QSlider#settingSlider::groove:horizontal {
    border: 1px solid #e0e3ea; height: 6px; background: #f1f5f9; border-radius: 3px;
}
QSlider#settingSlider::handle:horizontal {
    background: #07c160; width: 14px; height: 14px; margin: -4px 0; border-radius: 7px;
}
QSlider#settingSlider::handle:horizontal:hover { background: #06ad56; }

/* ---- 🌟 新增：外部模型管理微型按钮皮肤样式 ---- */
QPushButton#addModelBtn {
    background: #ffffff; border: 1.5px solid #e0e3ea; border-radius: 12px;
    color: #475569; font-size: 11px; font-weight: 600; padding: 0 10px;
    min-height: 30px; max-height: 30px;
}
QPushButton#addModelBtn:hover { border-color: #07c160; color: #07c160; background: #f0faf4; }

/* ---- 会话选择面板 ---- */
QWidget#sessionPage { background: #ffffff; }
QListWidget#sessionList {
    background: #f8f9fc; border: 1px solid #e5e7eb;
    border-radius: 10px; padding: 4px; outline: none;
}
QListWidget#sessionList::item {
    padding: 8px 10px; border-radius: 8px; color: #334155;
    font-size: 12px; border: none;
}
QListWidget#sessionList::item:selected {
    background: #e8f5e9; color: #1b5e20; font-weight: 600;
}
QListWidget#sessionList::item:hover {
    background: #f1f5f9;
}
QPushButton#sessionBtn {
    background: #f8f9fc; border: 1px solid #e5e7eb; border-radius: 8px;
    padding: 5px 10px; font-size: 11px; color: #475569; font-weight: 500;
}
QPushButton#sessionBtn:hover {
    border-color: #07c160; color: #07c160; background: #f0faf4;
}

/* ---- 高阶离线设置面板组 ---- */
QScrollArea#settingsScroll { background: transparent; border: none; }
QWidget#settingsPage { background: transparent; }
QLabel#settingsTitle { font-size: 16px; font-weight: 700; color: #1a1a2e; background: transparent; border: none; }
QGroupBox {
    font-size: 12px; font-weight: 600; color: #3a3a5c;
    border: 1px solid #e5e7eb; border-radius: 12px;
    margin-top: 12px; padding: 16px 12px 10px 12px; background: rgba(248,249,252,0.9);
}
QGroupBox::title { subcontrol-origin: margin; subcontrol-position: top left; padding: 0 8px; }
"""