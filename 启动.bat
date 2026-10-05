@echo off
title Yindun V3.4.4
cd /d "%~dp0"

echo ===================================================
echo   Yindun V3.4.4 - Launch
echo ===================================================
echo.

rem 优先使用项目虚拟环境；不存在则回退系统 Python（依赖：pip install -r requirements.txt）
if exist ".\secure_env\Scripts\python.exe" (
    echo Activating virtual environment...
    call ".\secure_env\Scripts\activate.bat"
) else (
    echo [INFO] Virtual environment not found, using system Python.
    echo        If dependencies are missing, run: pip install -r requirements.txt
)

echo Starting application...
echo.
python run.py

if %errorlevel% neq 0 (
    echo.
    echo ===================================================
    echo  [ERROR] Application exited with code %errorlevel%.
    echo  Check the traceback above for details.
    echo ===================================================
    pause
)
