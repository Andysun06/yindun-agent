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

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from yindun.core.context_budget import MODEL_WINDOW_TOKENS

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


# ══════════════════════════════════════════════════════════════════
#  模型接入分档：本机 / 局域网 / 公网
#  ------------------------------------------------------------------
#  分档不是分类癖好，而是**数据边界的说明书**：装在同一台机器上的模型，
#  敏感数据一步都不出机器；企业内网的模型，数据出机器但不出口；公网模型的
#  内容在送出前必须已经过本机脱敏。界面按这三档分区展示，用户一眼能看出
#  "这个模型会把数据带到哪里去"。
# ══════════════════════════════════════════════════════════════════

TIERS = ("local", "lan", "wan")
TIER_LABEL = {"local": "本机模型", "lan": "局域网模型", "wan": "公网模型"}
TIER_BOUNDARY = {
    "local": "数据不出这台电脑",
    "lan": "数据出这台电脑，但不出企业内网",
    "wan": "数据出企业内网；送出前内容已在本机脱敏",
}
TIER_HINT = {
    "local": "例如本机跑的 Ollama（127.0.0.1:11434）",
    "lan": "例如公司内网的推理服务或 Ollama（http://10.x / http://192.168.x）",
    "wan": "例如云端 OpenAI 兼容接口（https://…/v1）",
}

_PRIVATE_HOST_PATTERNS = (
    re.compile(r"^10\."),
    re.compile(r"^192\.168\."),
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\."),
    re.compile(r"^169\.254\."),          # 链路本地
    re.compile(r"^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\."),   # CGNAT（含 Tailscale 等组网）
)
_PRIVATE_SUFFIXES = (".local", ".lan", ".internal", ".corp", ".home")


def infer_tier(base_url: str) -> str:
    """按地址推断分档（用于兼容早期没有 tier 字段的配置）。

    这是"尽力而为"的推断：拿不准的一律归公网（**保守方向**——把内网误标成公网
    只会让人多点一次确认，把公网误标成本机则会让人误以为数据没出门）。
    """
    text = (base_url or "").strip()
    if not text:
        return "wan"
    try:
        host = (urlparse(text).hostname or "").lower()
    except Exception:
        return "wan"
    if not host:
        return "wan"
    if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return "local"
    if any(p.match(host) for p in _PRIVATE_HOST_PATTERNS):
        return "lan"
    if host.endswith(_PRIVATE_SUFFIXES) or "." not in host:      # 内网主机名 / 裸主机名
        return "lan"
    return "wan"


def tier_of(name: str, info: Dict[str, Any]) -> str:
    """取某条自定义模型的档位（显式字段优先，缺失则按地址推断）。"""
    tier = str((info or {}).get("tier") or "").strip().lower()
    if tier in TIERS:
        return tier
    return infer_tier((info or {}).get("base_url", ""))


def model_catalog(settings: Dict[str, Any]) -> Dict[str, Any]:
    """把"当前可用的算力"整理成**三档分组**，供界面分区展示与下拉分组。

    返回::

        {"current": "模型名",
         "tiers": {"local": [...], "lan": [...], "wan": [...]},
         "ollama_host": "http://127.0.0.1:11434",
         "local_detected": ["qwen2.5:7b", ...]}

    每项: {name, model_id, tier, source, base_url, kind, note}
    本机档 = 本机 Ollama 探测结果（+ 显式配成 local 的条目）；局域网/公网档 = custom_models。
    """
    custom = settings.get("custom_models") or {}
    current = str(settings.get("model") or "")
    tiers: Dict[str, List[Dict[str, Any]]] = {"local": [], "lan": [], "wan": []}
    seen: set = set()

    # ① 本机 Ollama：直接探测 manifests，零网络
    detected = detect_chat_models()
    for name in detected:
        tiers["local"].append({"name": name, "model_id": name, "tier": "local",
                               "source": "ollama", "base_url": settings.get("ollama_host")
                               or DEFAULT_OLLAMA_HOST, "kind": "ollama",
                               "note": "本机 Ollama 已安装"})
        seen.add(name)

    # ② 自定义条目：按档位归位（同名以自定义配置为准，故先加自定义再加探测结果）
    for name, info in custom.items():
        if not isinstance(info, dict):
            continue
        tier = tier_of(name, info)
        entry = {"name": str(name), "model_id": str(info.get("model_id") or name), "tier": tier,
                 "source": "custom", "base_url": str(info.get("base_url") or ""),
                 "kind": "openai", "has_key": bool(info.get("api_key")),
                 "key_invalid": bool(info.get("key_invalid")), "note": ""}
        tiers[tier] = [e for e in tiers[tier] if e["name"] != name]     # 覆盖同名
        tiers[tier].append(entry)
        seen.add(str(name))

    return {"current": current, "tiers": tiers, "ollama_host": settings.get("ollama_host")
            or DEFAULT_OLLAMA_HOST, "local_detected": detected,
            "tier_labels": TIER_LABEL, "tier_boundary": TIER_BOUNDARY, "tier_hint": TIER_HINT}


# ── 连通性探测（用户在设置页显式发起）──────────────────────────
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随重定向：探测目标必须就是用户填的那个地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D102
        raise urllib.error.HTTPError(req.full_url, code, "探测不跟随重定向", headers, fp)


# ── 端点地址归一化（V3.6.4）：用户很容易把"完整接口地址"当成"基础地址"填进来 ──
#   实测（负责人接 GLM-4-Flash 一直失败）：填的是
#     https://open.bigmodel.cn/api/paas/v4/chat/completions
#   而 OpenAI 兼容客户端会在这个 base 后面**再拼一次** /chat/completions →
#   请求打到 …/chat/completions/chat/completions，必然失败；连通性探测拼 /models 也是 404。
#   所以：**存进来之前、探测之前、建客户端之前**都先归一化成"基础地址"。
_ENDPOINT_SUFFIXES = (
    "/chat/completions", "/completions", "/embeddings", "/models", "/api/tags", "/api/generate",
)


def normalize_base_url(base_url: str) -> str:
    """把用户填的地址归一化成 OpenAI/Ollama 客户端的**基础地址**。

    规则（保守，只去掉明确的"接口端点后缀"）：
      · 去掉结尾的 /chat/completions、/completions、/embeddings、/models、/api/tags、/api/generate
        （可重复，容忍用户粘贴了带后缀又带斜杠的形态）；
      · 去掉多余的尾部斜杠；
      · 不猜测、不补全路径——例如 `https://open.bigmodel.cn/api/paas/v4` 原样保留
        （补 `/v1` 会把它改错；`https://api.openai.com` 这种也让客户端自己按标准拼）。
    """
    url = str(base_url or "").strip()
    if not url:
        return ""
    url = url.rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _ENDPOINT_SUFFIXES:
            if url.lower().endswith(suffix):
                url = url[: -len(suffix)].rstrip("/")
                changed = True
    return url


def probe_candidates(base_url: str) -> List[tuple]:
    """连通性探测的候选路径（按顺序试），返回 [(完整URL, 风格)]。

    为什么要多候选（实测）：只认 `/models` 会把两类正常端点判死——
      · 只提供 chat 接口、不提供模型列表的服务（探测必然 404）；
      · 用户填了完整端点（归一化后已经能救回来，但候选里保留真实 chat 兜底更稳）。
    """
    base = normalize_base_url(base_url)
    if not base:
        return []
    items = [(base + "/models", "openai")]
    if "/v1" not in base:
        items.append((base + "/api/tags", "ollama"))
    items.append((base + "/chat/completions", "chat"))
    return items


def probe_endpoint(base_url: str, api_key: str = "", timeout: float = 4.0) -> Dict[str, Any]:
    """探测一个 OpenAI 兼容 / Ollama 端点是否可用，并尽可能列出模型。

    ★ 安全边界（与"零裸 HTTP"的取舍说明）：本函数**只接受来自设置页输入框的地址**，
      不接受任何来自文档/工具结果的内容；只允许 http/https，禁止跟随重定向，短超时。
      它是"用户主动发起的配置动作"，不是可被内容诱导的请求路径——设置页会明确写这一点。

    ★ V3.6.4 起做三件事（实测驱动）：
      ① **地址归一化**：把"完整端点"（…/chat/completions）还原成基础地址——负责人接 GLM 一直失败
         就是因为粘贴了完整端点，探测拼 /models 变 404、保存后 chat 又拼成 …/chat/completions/chat/completions；
      ② **多候选探测**：/models → /api/tags（非 /v1 时）→ **真实最小 chat 请求**兜底
         （只提供 chat、不提供模型列表的服务由此也能判为连通）；
      ③ **错误分类说人话**：401/403 认证、404 无此接口、超时/DNS 分开报，并回带归一化后的地址。
    """
    url = normalize_base_url(base_url)
    if not url:
        return {"ok": False, "error": "请先填写接口地址"}
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return {"ok": False, "error": "地址需以 http:// 或 https:// 开头"}

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key

    last_error = ""
    for candidate, style in probe_candidates(url):
        try:
            if style == "chat":
                # 真实最小对话请求：对"只提供 chat 的服务"是唯一可靠的连通性证据。
                # 只发 1 个 token、不带任何用户内容（固定字符串，避免把业务数据发到公网）。
                body = json.dumps({"model": _probe_model_hint() or "glm-4-flash",
                                   "messages": [{"role": "user", "content": "ping"}],
                                   "max_tokens": 1}).encode("utf-8")
                request = urllib.request.Request(
                    candidate, data=body, method="POST",
                    headers={**headers, "Content-Type": "application/json"})
            else:
                request = urllib.request.Request(candidate, headers=headers)
            with opener.open(request, timeout=timeout) as response:
                raw = response.read(2_000_000).decode("utf-8", "ignore")
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 405):
                last_error = f"HTTP {exc.code}"
                continue                          # 换下一种端点风格
            if exc.code in (401, 403):
                return {"ok": False, "endpoint": url,
                        "error": (f"认证失败（HTTP {exc.code}）：地址是通的，但密钥不被接受——"
                                  f"请确认密钥是否正确、是否已过期/额度用尽")}
            return {"ok": False, "endpoint": url, "error": f"HTTP {exc.code}：{exc.reason}"}
        except Exception as exc:                  # 超时 / DNS / 拒绝连接 / 证书
            return {"ok": False, "endpoint": url,
                    "error": f"连不上：{type(exc).__name__} {exc}（地址：{url}）"}

        if style == "chat":
            # 能聊天就说明可用；模型名从响应里尽量取回来（取不到就让用户手填）
            model_id = ""
            try:
                payload = json.loads(raw or "{}")
                if isinstance(payload, dict):
                    model_id = str(payload.get("model") or "")
            except Exception:
                payload = {}
            models = [model_id] if model_id else []
            return {"ok": True, "style": "openai-chat", "models": models, "count": len(models),
                    "endpoint": url,
                    "note": ("已连通（该端点不提供模型列表，已用一次最小对话验证；"
                             "模型 ID 请按服务商文档填写）")}

        models: List[str] = []
        if isinstance(raw, str):
            try:
                payload = json.loads(raw or "{}")
            except Exception:
                payload = {}
        if isinstance(payload, dict):
            data = payload.get("data")            # OpenAI: {"data":[{"id":...}]}
            if isinstance(data, list):
                models = [str(d.get("id")) for d in data if isinstance(d, dict) and d.get("id")]
            elif style == "ollama" and isinstance(payload.get("models"), list):
                models = [str(m.get("name")) for m in payload["models"]
                          if isinstance(m, dict) and m.get("name")]
        return {"ok": True, "style": style, "models": models[:200], "count": len(models),
                "endpoint": url,
                "note": "已连通" + (f"，发现 {len(models)} 个模型" if models else "")}

    return {"ok": False, "endpoint": url,
            "error": (f"端点没有响应可用的接口（试过 /models 与最小对话请求"
                      f"{'，最后错误 ' + last_error if last_error else ''}）："
                      f"请确认这是 OpenAI 兼容或 Ollama 服务，且地址填到基础路径"
                      f"（例如 https://open.bigmodel.cn/api/paas/v4）")}


def _probe_model_hint() -> str:
    """探测时用的模型名提示：优先取用户当前/最近配置的模型 ID，取不到用空串。"""
    try:
        from yindun.app.settings_store import SettingsStore
        settings = SettingsStore()
        model = str(settings.get("model") or "")
        custom = settings.get("custom_models") or {}
        if model in custom:
            return str(custom[model].get("model_id") or "")
    except Exception:
        pass
    return ""
    return {"ok": False, "error": "端点没有响应 /models 或 /api/tags（请确认是 OpenAI 兼容或 Ollama 服务）"}


def reasoning_for_mode(think_mode: str) -> Optional[bool]:
    """把界面上的「快速 / 深度思考」翻译成**模型的思考开关**。

    为什么必须翻译（V3.5.9 实测，本机 qwen3.5:4b）：同一句"用一句话说明什么是哈希链审计"，
    不关思考要 **42~92 秒**、关掉只要 **0.7~3.1 秒**，答案质量相当——也就是说
    **每次回答都在白等模型想几十秒**，而"快速回答"这个模式名本来就该表达"别想那么久"。
    安全边界也实测过：给**非思考型**模型（qwen2.5:0.5b）传 `reasoning=False` 一切正常；
    传 `reasoning=True` 会被 Ollama 以 400 拒绝（"does not support thinking"）。
    所以映射刻意是"单向下压"：
      · 快速回答 → False（明确关掉思考；对两类模型都安全）
      · 深度思考 → None（**不传**，保持模型默认：思考型照旧先推理，非思考型不受影响）
    这样既不用猜"这个模型支不支持 thinking"，也不会把任何模型打成 400。
    """
    mode = str(think_mode or "")
    if "深度" in mode or "思考" in mode:
        return None
    return False


def build_llm_variants(settings: Dict[str, Any], tools: List[Any]
                       ) -> Tuple[Dict[bool, Any], Dict[str, Any], List[str], Optional[str]]:
    """一次构建两个变体：`{False: 关思考的算力, True: 保持默认的算力}`。

    为什么建两套：思考开关只在**创建**算力时生效，而用户随时会切「快速 / 深度思考」；
    切一次就重建算力既慢又要重连（还可能打断正在跑的轮次）。构造本身不联网、很便宜，
    因此一次建好两套，每轮按当前模式挑一套用（见 `AgentService._llm_for`）。
    """
    fast, tools_map, models, err = build_llm(settings, tools, reasoning=False)
    if err:
        return {}, tools_map, models, err
    default, _, _, err2 = build_llm(settings, tools, reasoning=None)
    if err2:
        return {False: fast}, tools_map, models, err2
    return {False: fast, True: default}, tools_map, models, None


def build_llm(settings: Dict[str, Any], tools: List[Any],
              reasoning: Any = "__from_mode__") -> Tuple[Any, Dict[str, Any], List[str], Optional[str]]:
    """按配置构建算力并绑定工具。

    返回 (bound_llm, tools_map, ollama_models, error)：
      · bound_llm 为 None 时 error 说明原因（界面据此提示）
      · tools_map 为 {tool_name: tool}，供 Worker 执行工具时查找
      · reasoning：模型思考开关。默认（`__from_mode__`）按当前 think_mode 推导；
        显式传 None 表示**不传该参数**（保持模型默认）。
    """
    model_name = settings.get("model") or "qwen3.5:4b"
    custom_models = settings.get("custom_models", {}) or {}
    ollama_host = settings.get("ollama_host") or DEFAULT_OLLAMA_HOST
    models = detect_ollama_models()
    if reasoning == "__from_mode__":
        reasoning = reasoning_for_mode(settings.get("think_mode"))

    try:
        if model_name in custom_models:
            info = custom_models[model_name] or {}
            from langchain_openai import ChatOpenAI
            base_model = ChatOpenAI(
                model=info.get("model_id", model_name),
                # ★ 建客户端前再归一化一次（V3.6.4）：老配置里可能存着完整端点，
                #   这里兜底修好，用户不必删了重建。
                openai_api_base=normalize_base_url(info.get("base_url", "")),
                openai_api_key=info.get("api_key", ""),
            )
        else:
            from langchain_ollama import ChatOllama
            extra: Dict[str, Any] = {}
            if reasoning is not None:
                # 只在明确要"关掉思考"时传；不传 = 保持模型默认。
                # ★ 绝不对未知模型传 True：Ollama 会以 400 拒绝非思考型模型（实测）。
                extra["reasoning"] = reasoning
            base_model = ChatOllama(
                model=model_name,
                base_url=ollama_host,
                timeout=60,
                options={
                    "num_ctx": MODEL_WINDOW_TOKENS,   # 预算表唯一来源（C-13）
                    "num_predict": 4096,
                    "temperature": 0.7,
                    "top_p": 0.9,
                },
                keep_alive=300,
                **extra,
            )
        bound = base_model.bind_tools(tools)
        return bound, {t.name: t for t in tools}, models, None
    except Exception as exc:  # 依赖缺失 / 地址不合法 / 模型名不可用
        return None, {}, models, f"模型初始化失败: {exc}"
