# -*- coding: utf-8 -*-
"""隐盾 · 本机模型检测回归（V3.6.7）

为什么要有它：负责人把便携版拷到同学机器上，对方本机只有 `qwen3.5:27b`，界面却显示
`qwen3.5:4b`（本机根本没有），模型设置里本机列表**空的**、也没法手动加本机模型。
根因有三层，这个套件逐个钉住：

  1. **只扫硬盘**：旧实现 `rglob(~/.ollama/models/manifests)`。模型放在别的盘（`OLLAMA_MODELS`）、
     装在别的用户目录、跑在容器里，或 exe 以管理员身份运行（`~` 变成管理员目录）→ 恒空。
     现在**硬盘 + `GET /api/tags` 双源**：服务自己知道它有哪些模型。
  2. **回环请求被系统代理劫持**：机器上开着 Clash 之类时，`HTTP_PROXY` 会把 `127.0.0.1:11434`
     也发给代理 → 连不上 → 检测恒空。所以直连必须显式 `ProxyHandler({})`。
  3. **地址可能带 `/v1`**：Ollama 的原生 API 在根（`/api/tags`），`…/v1/api/tags` 是 404，
     要能自动退回根再问一次。

另外两条"诚实性"约定也在本套件里：
  · 向量/嵌入模型（如 `nomic-embed-text`）**不能**进"本机模型"清单，但要如实计数；
  · 列表为空时**必须给出人话原因**（连不上 / 一个都没有 / 只有向量模型），
    并且 `model_catalog()["current_available"]` 必须如实为 False——界面据此显示"请选择模型"，
    而不是把一个本机不存在的名字当作用中模型（这条正是负责人看到的症状）。

用法：python tests/test_local_model_detect.py   （退出码 0 = 全部通过）
离线：所有 HTTP 都打桩，不依赖 Ollama、不发网络请求。
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.app import llm_factory as LF  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("=" * 78)
print("【1】直连必须绕开系统代理（同学习惯开 Clash，回环也会被代理劫走）")
print("=" * 78)
# 纯结构断言、不发真实网络请求（探测路径真发请求会被安全门禁合理拦下，没必要）。
# 机制说明：`ProxyHandler.__init__` 只为映射里出现过的协议挂 `xxx_open` 方法，而
# `OpenerDirector.add_handler` 只收"至少贡献一个方法"的 handler —— 于是
# `ProxyHandler({})` 的效果是**一个代理 handler 都不注册**，请求走直连；
# 而默认 `build_opener()` 会读 `HTTP_PROXY` 并注册代理 handler。
_bogus = "http://127.0.0.1:9"          # 9 = discard 端口，故意指向一个不存在的代理
_saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY",
                                         "http_proxy", "https_proxy")}


def _proxy_entries(opn):
    out = []
    for h in opn.handlers:
        proxies = getattr(h, "proxies", None)
        if proxies:
            out.append(dict(proxies))
    return out


try:
    for k in _saved:
        os.environ[k] = _bogus
    check("（前置）设了 HTTP_PROXY 时，默认 opener 确实会被代理接管",
          bool(_proxy_entries(urllib.request.build_opener())),
          str(_proxy_entries(urllib.request.build_opener())))
    check("★ 直连 opener 不带任何代理 handler（回环请求不会被发给代理）",
          not _proxy_entries(LF._direct_opener()), str(_proxy_entries(LF._direct_opener())))
finally:
    for k, v in _saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
opener = LF._direct_opener()
check("直连 opener 仍具备 HTTP 处理能力（不是把 handler 全扔了）",
      any(type(h).__name__ == "HTTPHandler" for h in opener.handlers),
      str([type(h).__name__ for h in opener.handlers]))

print("\n" + "=" * 78)
print("【2】/api/tags 的地址构造：`…/v1` 要退回根")
print("=" * 78)
seen = []


def _fake_http(url, timeout=1.5):
    seen.append(url)
    return json.dumps({"models": [{"name": "qwen3.5:27b"},
                                  {"name": "nomic-embed-text:latest"}]})


real_http = LF._loopback_direct_http
LF._loopback_direct_http = _fake_http
try:
    models, note = LF.detect_ollama_models_via_api("http://127.0.0.1:11434/v1")
    check("填了 `…/v1` 时先问根上的 /api/tags（而不是 /v1/api/tags）",
          seen and seen[0].endswith(":11434/api/tags"), str(seen))
    check("服务返回了两个模型（对话 + 向量）", len(models) == 2, str(models))
    check("说明里如实写了从服务读取", "Ollama 服务" in note, note)
finally:
    LF._loopback_direct_http = real_http

print("\n" + "=" * 78)
print("【3】双源合并：API 优先、去重、向量模型不进对话清单但要计数")
print("=" * 78)
LF.detect_chat_models = lambda: ["qwen2.5:7b"]          # 硬盘源
LF._loopback_direct_http = lambda url, timeout=1.5: json.dumps(
    {"models": [{"name": "qwen3.5:27b"}, {"name": "qwen2.5:7b"},
                {"name": "nomic-embed-text:latest"}]})
try:
    info = LF.detect_local_models("http://127.0.0.1:11434")
    check("合并结果含 API 里的对话模型", "qwen3.5:27b" in info["models"], str(info["models"]))
    check("同一模型只出现一次（API 优先、硬盘补漏）",
          info["models"].count("qwen2.5:7b") == 1, str(info["models"]))
    check("向量/嵌入模型不进对话清单", "nomic-embed-text:latest" not in info["models"],
          str(info["models"]))
    check("说明里如实提一句有几个向量模型（不静默丢）", "向量" in info["note"], info["note"])
finally:
    LF._loopback_direct_http = real_http

print("\n" + "=" * 78)
print("【4】空 / 失败时的原因（必须是人话，能照着做）")
print("=" * 78)
LF.detect_chat_models = lambda: []
LF._loopback_direct_http = lambda url, timeout=1.5: json.dumps({"models": []})
try:
    info = LF.detect_local_models("http://127.0.0.1:11434")
    check("服务有响应但一个模型都没有 → 说明里点明「一个模型都没有」",
          "一个模型都没有" in info["note"], info["note"])
    check("空列表时给出下一步（手动添加本机模型）", "手动添加本机模型" in info["note"], info["note"])
finally:
    LF._loopback_direct_http = real_http


def _boom(url, timeout=1.5):
    raise ConnectionRefusedError("[WinError 10061] 目标计算机积极拒绝")


LF._loopback_direct_http = _boom
try:
    info = LF.detect_local_models("http://127.0.0.1:11434")
    check("连不上时说明里写清「连不上本机 Ollama 服务」", "连不上本机 Ollama 服务" in info["note"],
          info["note"])
    check("连不上时带上异常类型（排障要用）", "ConnectionRefusedError" in info["note"], info["note"])
finally:
    LF._loopback_direct_http = real_http

print("\n" + "=" * 78)
print("【5】model_catalog：current_available / local_note 如实传给界面")
print("=" * 78)
LF.detect_local_models = lambda host="": {
    "models": ["qwen3.5:27b"], "note": "已检测到本机模型（来自 Ollama 服务）",
    "via_api": ["qwen3.5:27b"], "via_disk": []}
try:
    cat_ok = LF.model_catalog({"model": "qwen3.5:27b", "ollama_host": "http://127.0.0.1:11434"})
    check("配置的就是检测到的模型 → current_available=True", cat_ok["current_available"] is True,
          str(cat_ok["current_available"]))
    cat_bad = LF.model_catalog({"model": "qwen3.5:4b", "ollama_host": "http://127.0.0.1:11434"})
    check("★ 配置了一个本机不存在的模型 → current_available=False（界面据此显示「请选择模型」）",
          cat_bad["current_available"] is False, str(cat_bad["current_available"]))
    check("local_note 原样带给界面（空列表时界面要能说出原因）",
          cat_bad["local_note"] == "已检测到本机模型（来自 Ollama 服务）", cat_bad["local_note"])
    cat_custom = LF.model_catalog({"model": "我的内网", "ollama_host": "http://127.0.0.1:11434",
                                   "custom_models": {"我的内网": {"model_id": "qwen2.5:14b",
                                                                  "base_url": "http://10.0.0.8:8000/v1",
                                                                  "api_key": "k"}}})
    check("自定义（局域网/公网/手动本机）条目也算「可用」",
          cat_custom["current_available"] is True, str(cat_custom["current_available"]))
    cat_empty = LF.model_catalog({"model": ""})
    check("模型为空（未选择）→ current_available=False，界面显示「请选择模型」而不是某个名字",
          cat_empty["current_available"] is False)
finally:
    del LF.detect_local_models

print("\n" + "=" * 78)
if failures:
    print(f"❌ 本机模型检测回归失败：{len(failures)} 项")
    for item in failures:
        print("   ·", item)
    sys.exit(1)
print("✅ 本机模型检测回归通过（双源 / 绕代理 / /v1 回退 / 向量过滤 / 空态原因 / current_available）")
sys.exit(0)
