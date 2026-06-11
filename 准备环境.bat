@echo off
title Yindun Security Agent Environment Setup
chcp 65001 >nul
cd /d "%~dp0"

echo ===================================================
echo [Yindun V2.1.0] Starting environment deployment self-check...
echo ===================================================
echo.
echo Step 1: Checking global Python installation status...
python --version >nul 2>&1
if %errorlevel% equ 0 goto PYTHON_OK

echo [ERROR] Python is not installed or not added to your system PATH!
echo [SOLUTION] Please download and install Python 3.10 or 3.11 from python.org.
echo Remember to check "Add Python to PATH" during installation.
echo.
pause
exit

:PYTHON_OK
echo [OK] Base Python environment confirmed.
echo.
echo Step 2: Detection of isolated virtual sandbox [secure_env]...
if exist ".\secure_env\Scripts\activate.bat" goto ENV_EXISTS

echo [WARNING] Isolated sandbox not found.
echo Deploying automatic setup...
echo [INFO] Creating independent Python virtual environment [secure_env]...
python -m venv secure_env
if %errorlevel% neq 0 goto ENV_FAILED
echo [OK] Virtual sandbox shell created successfully!
echo.

echo [INFO] Pulling core modules from Tsinghua high-speed mirror...
echo.
".\secure_env\Scripts\python.exe" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
:: 🌟 核心更新：在原有依赖基础上，追加安装 langchain-openai 库以支持外部自定义模型
".\secure_env\Scripts\pip.exe" install PySide6 langchain-ollama langchain-openai langchain-core PyPDF2 python-docx openpyxl tiktoken -i https://pypi.tuna.tsinghua.edu.cn/simple

if %errorlevel% neq 0 goto INSTALL_FAILED
echo.
echo [OK] All offline dependencies and security libraries deployed successfully!
goto ENV_DONE

:ENV_EXISTS
echo [OK] Independent virtual environment verified and locked.
goto ENV_DONE

:ENV_FAILED
echo [ERROR] Sandbox creation failed! Please check your directory write permissions.
pause
exit

:INSTALL_FAILED
echo.
echo [ERROR] Package download interrupted due to network error. Please re-run script!
pause
exit

:ENV_DONE
echo.
echo ===================================================
echo [SUCCESS] Configuration verified!
echo Now double-click [启动.bat] to run.
echo ===================================================
pause