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

/* 圆润胶囊滚动条 - 适配主界面 (10px 完美胶囊, 主题色弱化把手) */
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 10px 2px 10px 2px; border: none; border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: rgba(0,0,0,16); min-height: 36px; border-radius: 5px;
    margin: 0 1px; border: none;
}
QScrollBar::handle:vertical:hover { background: rgba(0,0,0,28); }
QScrollBar::handle:vertical:pressed { background: rgba(7,193,96,0.55); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; background: transparent; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }

QScrollBar:horizontal {
    background: transparent; height: 10px; margin: 2px 10px 2px 10px; border: none; border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: rgba(0,0,0,16); min-width: 36px; border-radius: 5px;
    margin: 1px 0; border: none;
}
QScrollBar::handle:horizontal:hover { background: rgba(0,0,0,28); }
QScrollBar::handle:horizontal:pressed { background: rgba(7,193,96,0.55); }

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

/* ---- 设置面板按钮 ---- */
QPushButton#settingsCancel {
    background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
    padding: 6px 14px; font-size: 12px; color: #64748b; font-weight: 600;
}
QPushButton#settingsCancel:hover { background: #f1f5f9; color: #334155; }
QPushButton#settingsSave {
    background: #3b82f6; color: #000000; border: 1px solid #2563eb; border-radius: 8px;
    padding: 8px 24px; font-size: 13px; font-weight: 600;
}
QPushButton#settingsSave:hover { background: #2563eb; }

/* ---- 外部模型管理微型按钮皮肤样式 ---- */
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
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 0 8px; color: #1e293b;
}
QGroupBox QLabel {
    color: #334155; font-size: 12px; background: transparent;
}
QRadioButton {
    color: #334155; font-size: 12px; background: transparent; spacing: 6px;
}
QRadioButton::indicator {
    width: 14px; height: 14px; border-radius: 7px;
    border: 2px solid #cbd5e1; background: white;
}
QRadioButton::indicator:checked {
    background: #3b82f6; border-color: #3b82f6;
}
"""

# ---- 深色模式 QSS ----
DARK_QSS = """
QWidget#mainWindow { background: transparent; }

/* 主体外壳圆角深色卡片 */
QFrame#container {
    background: #1e1e2e; border: 1px solid rgba(255,255,255,12); border-radius: 22px;
}

/* 顶部无边框标题栏 */
QFrame#titleBar { background: transparent; border: none; border-top-left-radius: 22px; border-top-right-radius: 22px; }
QLabel#titleIcon { font-size: 16px; background: transparent; border: none; }
QLabel#titleText { color: #e0e0e0; font-size: 13px; font-weight: 700; background: transparent; border: none; }

/* 标题栏按钮 */
QPushButton#titleBtn {
    background: transparent; border: none; border-radius: 12px; font-size: 14px; color: #888;
    min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
}
QPushButton#titleBtn:hover { background: rgba(255,255,255,10); color: #ccc; }
QPushButton#closeBtn {
    background: transparent; border: none; border-radius: 12px; font-size: 15px; color: #999;
    min-width: 24px; max-width: 24px; min-height: 24px; max-height: 24px;
}
QPushButton#closeBtn:hover { background: #ff5f57; color: white; }

/* 聊天历史大厅 */
QTextBrowser#chatArea { background: #1a1a2e; border: none; color: #e0e0e0; }
QLabel#statusLabel { color: #aaa; font-size: 11px; background: transparent; padding: 2px 0; border: none; }

/* 圆润胶囊滚动条 - 深色模式 (10px 完美胶囊) */
QScrollBar:vertical {
    background: transparent; width: 10px; margin: 10px 2px 10px 2px; border: none; border-radius: 5px;
}
QScrollBar::handle:vertical {
    background: rgba(255,255,255,14); min-height: 36px; border-radius: 5px;
    margin: 0 1px; border: none;
}
QScrollBar::handle:vertical:hover { background: rgba(255,255,255,22); }
QScrollBar::handle:vertical:pressed { background: rgba(59,130,246,0.65); }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; background: transparent; border: none; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; border: none; }

QScrollBar:horizontal {
    background: transparent; height: 10px; margin: 2px 10px 2px 10px; border: none; border-radius: 5px;
}
QScrollBar::handle:horizontal {
    background: rgba(255,255,255,14); min-width: 36px; border-radius: 5px;
    margin: 1px 0; border: none;
}
QScrollBar::handle:horizontal:hover { background: rgba(255,255,255,22); }
QScrollBar::handle:horizontal:pressed { background: rgba(59,130,246,0.65); }

/* 下置复合多模态控制台底座 */
QFrame#controlDock {
    background: #252540; border: none; border-top: 1px solid rgba(255,255,255,6);
    border-bottom-left-radius: 22px; border-bottom-right-radius: 22px;
}
QLineEdit#inputLine {
    background: #2a2a3e; border: 1.5px solid #3a3a50; border-radius: 18px;
    padding: 9px 16px; font-size: 13px; color: #e0e0e0;
}
QLineEdit#inputLine:focus { border-color: #3b82f6; background: #303050; }
QLineEdit#inputLine::placeholder { color: #666; }

/* 发送按钮 */
QPushButton#sendBtn {
    background: #3b82f6; color: white; border: none; border-radius: 18px;
    font-size: 15px; font-weight: bold; min-width: 38px; max-width: 38px; min-height: 38px; max-height: 38px;
}
QPushButton#sendBtn:hover { background: #2563eb; }
QPushButton#sendBtn:pressed { background: #1d4ed8; }
QPushButton#sendBtn:disabled { background: #3a3a50; }

/* 挂载本地文件按钮 */
QPushButton#fileBtn {
    background: #2a2a3e; border: 1px solid #3a3a50; border-radius: 12px;
    padding: 3px 10px; font-size: 11px; color: #bbb; min-height: 26px; max-height: 26px;
}
QPushButton#fileBtn:hover { border-color: #3b82f6; color: #3b82f6; }

/* 下拉选择框 */
QComboBox#modeSelector, QComboBox#settingCombo {
    background: #2a2a3e; border: 1.5px solid #3a3a50; border-radius: 12px;
    padding: 4px 30px 4px 12px; font-size: 12px; color: #e0e0e0;
    min-height: 30px; max-height: 30px;
}
QComboBox#modeSelector:hover, QComboBox#settingCombo:hover { border-color: #3b82f6; }
QComboBox#modeSelector:focus, QComboBox#settingCombo:focus { border-color: #3b82f6; }
QComboBox#modeSelector::drop-down, QComboBox#settingCombo::drop-down {
    border: none; width: 28px; subcontrol-position: center right;
}
QComboBox#modeSelector QAbstractItemView, QComboBox#settingCombo QAbstractItemView {
    background: #2a2a3e; border: 1px solid #3a3a50; border-radius: 12px;
    padding: 4px; outline: none; font-size: 12px;
}
QComboBox#modeSelector QAbstractItemView::item, QComboBox#settingCombo QAbstractItemView::item {
    padding: 7px 12px; border-radius: 8px; min-height: 20px; color: #e0e0e0;
}
QComboBox QAbstractItemView::item:selected { background: #1a3a5c; color: #60a5fa; }
QComboBox QAbstractItemView::item:hover { background: #303050; }

/* 复选框 */
QCheckBox#settingCheck { font-size: 12px; color: #ccc; spacing: 8px; background: transparent; }
QCheckBox#settingCheck::indicator {
    width: 16px; height: 16px; border-radius: 4px;
    border: 2px solid #555; background: #2a2a3e;
}
QCheckBox#settingCheck::indicator:hover { border-color: #3b82f6; }
QCheckBox#settingCheck::indicator:checked {
    background: #3b82f6; border-color: #3b82f6;
    image: url(""" + CK + """);
}

/* 设置面板按钮 */
QPushButton#settingsCancel {
    background: #2a2a3e; border: 1px solid #3a3a50; border-radius: 8px;
    padding: 6px 14px; font-size: 12px; color: #888; font-weight: 600;
}
QPushButton#settingsCancel:hover { background: #303050; color: #ccc; }
QPushButton#settingsSave {
    background: #3b82f6; color: #ffffff; border: 1px solid #2563eb; border-radius: 8px;
    padding: 8px 24px; font-size: 13px; font-weight: 600;
}
QPushButton#settingsSave:hover { background: #2563eb; }

/* 新增外部按钮 */
QPushButton#addModelBtn {
    background: #2a2a3e; border: 1.5px solid #3a3a50; border-radius: 12px;
    color: #bbb; font-size: 11px; font-weight: 600; padding: 0 10px;
    min-height: 30px; max-height: 30px;
}
QPushButton#addModelBtn:hover { border-color: #3b82f6; color: #3b82f6; background: #1a2a4e; }

/* 会话选择面板 */
QWidget#sessionPage { background: #1a1a2e; }
QListWidget#sessionList {
    background: #222240; border: 1px solid #3a3a50;
    border-radius: 10px; padding: 4px; outline: none;
}
QListWidget#sessionList::item {
    padding: 8px 10px; border-radius: 8px; color: #ccc;
    font-size: 12px; border: none;
}
QListWidget#sessionList::item:selected {
    background: #1a3a5c; color: #60a5fa; font-weight: 600;
}
QListWidget#sessionList::item:hover {
    background: #303050;
}
QPushButton#sessionBtn {
    background: #222240; border: 1px solid #3a3a50; border-radius: 8px;
    padding: 5px 10px; font-size: 11px; color: #bbb; font-weight: 500;
}
QPushButton#sessionBtn:hover {
    border-color: #3b82f6; color: #3b82f6; background: #1a2a4e;
}

/* 设置面板 */
QScrollArea#settingsScroll { background: transparent; border: none; }
QWidget#settingsPage { background: transparent; }
QLabel#settingsTitle { font-size: 16px; font-weight: 700; color: #e0e0e0; background: transparent; border: none; }
QGroupBox {
    font-size: 12px; font-weight: 600; color: #ccc;
    border: 1px solid #3a3a50; border-radius: 12px;
    margin-top: 12px; padding: 16px 12px 10px 12px; background: rgba(30,30,50,0.9);
}
QGroupBox::title {
    subcontrol-origin: margin; subcontrol-position: top left;
    padding: 0 8px; color: #e0e0e0;
}
QGroupBox QLabel {
    color: #ccc; font-size: 12px; background: transparent;
}
QRadioButton {
    color: #ccc; font-size: 12px; background: transparent; spacing: 6px;
}
QRadioButton::indicator {
    width: 14px; height: 14px; border-radius: 7px;
    border: 2px solid #555; background: #2a2a3e;
}
QRadioButton::indicator:checked {
    background: #3b82f6; border-color: #3b82f6;
}
"""