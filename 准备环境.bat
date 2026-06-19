@echo off
title Yindun V3.2 - Setup
cd /d "%~dp0"

echo ===================================================
echo  [Yindun V3.2] Environment Deployment Self-Check
echo ===================================================
echo.

echo [1/3] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found in PATH.
    echo Please install Python 3.10+ from https://www.python.org/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)
python --version
echo [OK] Python OK.
echo.

echo [2/3] Creating virtual environment [secure_env]...
if exist ".\secure_env\Scripts\python.exe" goto :ENV_OK

python -m venv secure_env
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create virtual environment.
    pause
    exit /b 1
)
echo [OK] Virtual environment created.

:ENV_OK
echo [OK] Virtual environment ready.
echo.

echo [3/3] Installing dependencies...
".\secure_env\Scripts\python.exe" -m pip install --upgrade pip -q
".\secure_env\Scripts\python.exe" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/3] Installing audio dependencies...
".\secure_env\Scripts\python.exe" -m pip install editdistance-s onnxruntime sentencepiece soundfile librosa scipy numpy torch transformers accelerate hydra-core jaconv jamo jieba kaldiio omegaconf oss2 tensorboardX umap-learn -i https://pypi.tuna.tsinghua.edu.cn/simple

echo [3/3] Installing funasr and modelscope...
".\secure_env\Scripts\python.exe" -m pip install funasr modelscope --no-deps -i https://pypi.tuna.tsinghua.edu.cn/simple

if %errorlevel% neq 0 (
    echo.
    echo [WARN] Tsinghua mirror failed, trying default PyPI...
    ".\secure_env\Scripts\python.exe" -m pip install -r requirements.txt
    ".\secure_env\Scripts\python.exe" -m pip install editdistance-s onnxruntime sentencepiece soundfile librosa scipy numpy torch transformers accelerate hydra-core jaconv jamo jieba kaldiio omegaconf oss2 tensorboardX umap-learn
    ".\secure_env\Scripts\python.exe" -m pip install funasr modelscope --no-deps
    if %errorlevel% neq 0 (
        echo [ERROR] Installation failed. Check your network connection.
        pause
        exit /b 1
    )
)
echo.
echo [OK] All dependencies installed.
echo.
echo ===================================================
echo  [SUCCESS] Environment ready!
echo  Double-click launch.bat to start.
echo ===================================================
pause
