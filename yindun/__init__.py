# -*- coding: utf-8 -*-
"""
隐盾安全智能体 (Yindun Security Agent)
本地离线多模型安全合规智能助手
"""

import os as _os
import sys as _sys
from pathlib import Path as _Path

# ★ 本地回环地址必须绕过系统代理（Clash/V2Ray 等会劫持发往 127.0.0.1 的请求，
#   导致 langchain-ollama/httpx 调用本地 Ollama 时返回 502）。
#   httpx 的 trust_env 会读取 Windows 注册表系统代理，必须用 NO_PROXY 环境变量显式豁免。
_os.environ["NO_PROXY"] = _os.environ.get("NO_PROXY", "127.0.0.1,localhost")
_os.environ["no_proxy"] = _os.environ.get("no_proxy", "127.0.0.1,localhost")

# ★ 应用根目录：源码运行时 = 项目根；PyInstaller 打包后 = exe 所在目录。
#   配置 / 会话 / 审计日志 / 密钥等可写数据一律放在 APP_ROOT 下，
#   保证打包版（exe）与源码版路径语义一致。
if getattr(_sys, "frozen", False):
    APP_ROOT = _Path(_sys.executable).resolve().parent
else:
    APP_ROOT = _Path(__file__).resolve().parents[1]

__version__ = "3.3.0"
__display_version__ = "V3.3"
__author__ = "Yindun Team"
