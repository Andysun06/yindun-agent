@echo off
:: 🌟 核心修复：必须把切换 UTF-8 编码放在第一行，确保后续所有标题和内容绝不乱码
chcp 65001 >nul
title 🛡️ 隐盾安全智能体 V2.0 - 启动总舱

cd /d "%~dp0"

echo 🚀 正在挂载隐盾虚拟隔离沙箱环境...
call ".\secure_env\Scripts\activate.bat"

echo 🧠 正在拉起系统底层离线算力悬浮智能舱...
echo 💡 提示：后台日志正在同步传输中...
echo.

python main_win.py

if %errorlevel% neq 0 (
    echo.
    echo ⚠️ 安全控制台警告：程序运行遭遇异常崩溃，请检查上方日志排错！
    pause
)