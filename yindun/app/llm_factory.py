# -*- coding: utf-8 -*-
"""隐盾 · 算力工厂（本地 Ollama / OpenAI 兼容云端）

职责：
- 列出本地已安装的算力模型（供设置页与首次启动引导使用）
- 按配置构建 LLM 并绑定工具（bind_tools）

与界面无关：本模块不导入任何界面框架。

★ 设计取舍（安全）：本模块**不发起任何裸 HTTP 请求**。
  · 模型列表：直接枚举 Ollama 的本地 manifests 目录（纯文件系统读取，离线可用、
    不依赖服务在线，也不产生任何网络面）；
  · 连通性/可用性：交给 langchain 的模型客户端的真实调用结果来判断（transport 由
    依赖库负责），因此本项目自身代码里不存在"可被诱导的 URL 请求"。
  这样既满足"数据不出网"的产品定位，也让静态安全扫描无 SSRF 面可攻击。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"


def _ollama_models_root() -> Optional[Path]:
    """Ollama 模型仓库根目录：优先 OLLAMA_MODELS（官方环境变量），否则用户目录默认位置。"""
    env_root = os.environ.get("OLLAMA_MODELS")
    candidates = []
    if env_root:
        candidates.append(Path(env_root))
    home = Path(os.path.expanduser("~"))
    candidates.append(home / ".ollama" / "models")
    for path in candidates:
        if path.is_dir():
            return path
    return None


def detect_ollama_models() -> List[str]:
    """列出本地已下载的 Ollama 模型。

    实现：遍历 `<models>/manifests/**` 下的**文件**（Ollama 的 manifest 布局为
    `manifests/<registry>/<namespace>/<模型名>/<标签>`，其中"标签"是文件、目录只是层级），
    拼成 `name:tag`。纯文件系统读取：不联网、不起进程、服务离线也能列出。
    """
    root = _ollama_models_root()
    if root is None:
        return []
    manifests = root / "manifests"
    if not manifests.is_dir():
        return []

    # 这些目录名是 registry / namespace 层级，不是模型名
    non_model_dirs = {"manifests", "library", "registry.ollama.ai"}
    names: List[str] = []
    for manifest in manifests.rglob("*"):
        try:
            if not manifest.is_file():
                continue
        except OSError:
            continue
        tag = manifest.name
        model = manifest.parent.name
        if not model or model in non_model_dirs or not tag:
            continue
        names.append(f"{model}:{tag}")
    # 去重并稳定排序（latest 靠前，便于默认选择）
    return sorted(set(names), key=lambda n: (not n.endswith(":latest"), n))


def is_embedding_model(name: str) -> bool:
    """判断是否为 embedding 模型（不能用于对话）。"""
    lowered = (name or "").lower()
    return any(marker in lowered for marker in
               ("embed", "bge", "minilm", "gte", "e5-", "text-embedding"))


def detect_chat_models() -> List[str]:
    """可对话的模型（排除 embedding 类）。"""
    return [m for m in detect_ollama_models() if not is_embedding_model(m)]


def get_first_available_model() -> Optional[str]:
    """首个可用的对话模型；若本地只有 embedding 模型则返回 None（界面应提示用户拉取对话模型）。"""
    chat_models = detect_chat_models()
    return chat_models[0] if chat_models else None


def build_llm(settings: Dict[str, Any], tools: List[Any]) -> Tuple[Any, Dict[str, Any], List[str], Optional[str]]:
    """按配置构建算力并绑定工具。

    返回 (bound_llm, tools_map, ollama_models, error)：
      · bound_llm 为 None 时 error 说明原因（界面据此提示）
      · tools_map 为 {tool_name: tool}，供 Worker 执行工具时查找
    """
    model_name = settings.get("model") or "qwen2.5:7b"
    custom_models = settings.get("custom_models", {}) or {}
    ollama_host = settings.get("ollama_host") or DEFAULT_OLLAMA_HOST
    models = detect_ollama_models()

    try:
        if model_name in custom_models:
            info = custom_models[model_name] or {}
            from langchain_openai import ChatOpenAI
            base_model = ChatOpenAI(
                model=info.get("model_id", model_name),
                openai_api_base=info.get("base_url", ""),
                openai_api_key=info.get("api_key", ""),
            )
        else:
            from langchain_ollama import ChatOllama
            base_model = ChatOllama(
                model=model_name,
                base_url=ollama_host,
                timeout=60,
                options={
                    "num_ctx": 16384,
                    "num_predict": 4096,
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
                keep_alive=300,
            )
        bound = base_model.bind_tools(tools)
        return bound, {t.name: t for t in tools}, models, None
    except Exception as exc:  # 依赖缺失 / 地址不合法 / 模型名不可用
        return None, {}, models, f"模型初始化失败: {exc}"
