@echo off
title Yindun V2.2.0
cd /d "%~dp0"

echo ===================================================
echo   Yindun V2.2.0 - Launch
echo ===================================================
echo.

if not exist ".\secure_env\Scripts\python.exe" (
    echo [ERROR] Virtual environment [secure_env] not found.
    echo Please run setup.bat first to set up the environment.
    echo.
    pause
    exit /b 1
)

echo Activating virtual environment...
call ".\secure_env\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
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
