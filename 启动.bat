@echo off
title Yindun V3.4.5
cd /d "%~dp0"

echo ===================================================
echo   Yindun V3.4.5 - Launch
echo ===================================================
echo.

rem 优先使用项目虚拟环境；不存在则回退系统 Python（依赖：pip install -r requirements.txt）
rem 直接调用 venv 的 python.exe，不依赖 activate.bat（venv 目录移动/改名后激活脚本里的路径会失效）
set "PYTHON_CMD=python"
if exist ".\secure_env\Scripts\python.exe" (
    echo Using virtual environment...
    set "PYTHON_CMD=.\secure_env\Scripts\python.exe"
) else (
    echo [INFO] Virtual environment not found, using system Python.
    echo        If dependencies are missing, run: pip install -r requirements.txt
)

echo Starting application...
echo.
%PYTHON_CMD% run.py

if %errorlevel% neq 0 (
    echo.
    echo ===================================================
    echo  [ERROR] Application exited with code %errorlevel%.
    echo  Check the traceback above for details.
    echo ===================================================
    pause
)
