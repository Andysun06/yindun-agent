# -*- coding: utf-8 -*-
"""协议适配器回归（V3.6.21）：Anthropic Messages / OpenAI Responses。

做法：起一个本地 mock 服务，按目标协议接收请求并回固定响应——这样能在**没有厂商 Key**
的情况下验证三件真东西：
  ① 我们发出去的请求体形状对（system / messages / tools / tool_result 的位置）；
  ② 把上游响应翻译成 LangChain 的 AIMessage（文本 + tool_calls）正确；
  ③ 错误（401/429）能翻译成可读的一句话，不静默。

诚实边界：协议一致性由本套件锁定；真实厂商端点仍需各自有效 Key 才能端到端验证。
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage  # noqa: E402
from yindun.app.chat_adapters import AnthropicChatModel, ResponsesChatModel  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


class _Tool:
    name = "list_local_files"
    description = "列出目录"
    args = {"target_directory": {"type": "string", "description": "目录"}}

    def invoke(self, args):
        return "ok"


def _server(handler_fn):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            status, payload = handler_fn(self.path, {k.lower(): v for k, v in self.headers.items()}, body)
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


# ── Anthropic Messages ──────────────────────────────────────
seen = {}


def _anthropic_handler(path, headers, body):
    seen["path"] = path
    seen["headers"] = headers
    seen["body"] = body
    return 200, {
        "id": "msg_1", "type": "message", "role": "assistant", "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "我先看一下目录。"},
            {"type": "tool_use", "id": "toolu_1", "name": "list_local_files",
             "input": {"target_directory": "E:\\yindun-agent"}},
        ],
    }


httpd, base = _server(_anthropic_handler)
try:
    model = AnthropicChatModel(base_url=base, api_key="sk-ant-test", model="claude-test",
                               max_tokens=1024).bind_tools([_Tool()])
    msgs = [SystemMessage(content="你是隐盾"),
            HumanMessage(content="看看目录"),
            AIMessage(content="", tool_calls=[{"name": "list_local_files",
                                               "args": {"target_directory": "E:\\a"},
                                               "id": "toolu_0"}]),
            ToolMessage(content="3 个文件", tool_call_id="toolu_0")]
    reply = model.invoke(msgs)
    check("Anthropic：请求打到 /v1/messages", seen.get("path") == "/v1/messages", str(seen.get("path")))
    check("Anthropic：使用 x-api-key + anthropic-version 头",
          seen["headers"].get("x-api-key") == "sk-ant-test"
          and seen["headers"].get("anthropic-version"), str(seen["headers"])[:120])
    b = seen["body"]
    check("Anthropic：system 单独成字段（不混进 messages）",
          b.get("system") == "你是隐盾" and all(m["role"] != "system" for m in b["messages"]), str(b)[:160])
    check("Anthropic：工具 schema 以 input_schema 下发",
          b["tools"][0]["name"] == "list_local_files" and "input_schema" in b["tools"][0],
          str(b.get("tools"))[:160])
    check("Anthropic：工具结果翻译为 tool_result（tool_use_id 对齐）",
          any(blk.get("type") == "tool_result" and blk.get("tool_use_id") == "toolu_0"
              for m in b["messages"] if isinstance(m.get("content"), list)
              for blk in m["content"]), str(b["messages"])[-200:])
    check("Anthropic：响应文本与 tool_calls 都被解析",
          reply.content == "我先看一下目录。"
          and len(reply.tool_calls or []) == 1
          and reply.tool_calls[0]["name"] == "list_local_files"
          and reply.tool_calls[0]["args"] == {"target_directory": "E:\\yindun-agent"}
          and reply.tool_calls[0]["id"] == "toolu_1",
          str(reply))
finally:
    httpd.shutdown()

# 错误翻译
def _err_handler(path, headers, body):
    return 429, {"error": {"type": "rate_limit_error", "message": "Number of requests exceeded"}}

httpd, base = _server(_err_handler)
try:
    model = AnthropicChatModel(base_url=base, api_key="k", model="m")
    try:
        model.invoke([HumanMessage(content="hi")])
        check("Anthropic：429 抛出可读异常", False, "没有抛异常")
    except RuntimeError as exc:
        check("Anthropic：429 抛出可读异常", "429" in str(exc) and "限流" in str(exc), str(exc)[:160])
finally:
    httpd.shutdown()

# ── OpenAI Responses ───────────────────────────────────────
seen2 = {}


def _responses_handler(path, headers, body):
    seen2["path"] = path
    seen2["headers"] = headers
    seen2["body"] = body
    return 200, {
        "id": "resp_1", "status": "completed",
        "output": [
            {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "目录里有 3 个文件。"}]},
            {"type": "function_call", "call_id": "call_9", "name": "list_local_files",
             "arguments": "{\"target_directory\": \"E:\\\\yindun-agent\"}"},
        ],
    }


httpd, base = _server(_responses_handler)
try:
    model = ResponsesChatModel(base_url=base, api_key="sk-test", model="gpt-5-test",
                               max_tokens=2048).bind_tools([_Tool()])
    msgs = [SystemMessage(content="你是隐盾"),
            HumanMessage(content="看看目录"),
            AIMessage(content="", tool_calls=[{"name": "list_local_files",
                                               "args": {"target_directory": "E:\\a"},
                                               "id": "call_8"}]),
            ToolMessage(content="3 个文件", tool_call_id="call_8")]
    reply = model.invoke(msgs)
    check("Responses：请求打到 /responses", seen2.get("path") == "/responses", str(seen2.get("path")))
    check("Responses：Bearer 鉴权头", seen2["headers"].get("authorization") == "Bearer sk-test",
          str(seen2["headers"])[:100])
    b = seen2["body"]
    check("Responses：instructions 承载 system",
          b.get("instructions") == "你是隐盾" and "max_output_tokens" in b, str(b)[:160])
    check("Responses：工具以 function 类型下发（parameters）",
          b["tools"][0]["type"] == "function" and "parameters" in b["tools"][0],
          str(b.get("tools"))[:160])
    check("Responses：工具结果翻译为 function_call_output（call_id 对齐）",
          any(it.get("type") == "function_call_output" and it.get("call_id") == "call_8"
              for it in b["input"]), str(b["input"])[-200:])
    check("Responses：输出文本与 function_call 都被解析",
          reply.content == "目录里有 3 个文件。"
          and len(reply.tool_calls or []) == 1
          and reply.tool_calls[0]["name"] == "list_local_files"
          and reply.tool_calls[0]["args"] == {"target_directory": "E:\\yindun-agent"}
          and reply.tool_calls[0]["id"] == "call_9",
          str(reply))
finally:
    httpd.shutdown()

# ── 工厂分流：api_format 选中对应实现 ──
from yindun.app.llm_factory import build_llm  # noqa: E402

_base_settings = {"model": "测试模型", "custom_models": {"测试模型": {
    "model_id": "m", "base_url": "http://127.0.0.1:9", "api_key": "k",
    "api_format": "anthropic", "tier": "wan"}}}
_llm, _tools, _models, _err = build_llm(_base_settings, [], reasoning=None)
check("工厂：anthropic 格式构建出 AnthropicChatModel",
      isinstance(_llm, AnthropicChatModel) and not _err, f"{type(_llm).__name__} / {_err}")
_base_settings["custom_models"]["测试模型"]["api_format"] = "responses"
_llm, _, _, _err = build_llm(_base_settings, [], reasoning=None)
check("工厂：responses 格式构建出 ResponsesChatModel",
      isinstance(_llm, ResponsesChatModel) and not _err, f"{type(_llm).__name__} / {_err}")
_base_settings["custom_models"]["测试模型"]["api_format"] = "ollama_native"
_base_settings["custom_models"]["测试模型"]["base_url"] = "http://127.0.0.1:11434"
_llm, _, _, _err = build_llm(_base_settings, [], reasoning=None)
check("工厂：ollama_native 构建出 ChatOllama",
      type(getattr(_llm, "bound", _llm)).__name__ == "ChatOllama" and not _err,
      f"{type(getattr(_llm, 'bound', _llm)).__name__} / {_err}")

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 协议适配器回归通过：Anthropic / Responses 的请求形状、工具翻译、响应解析与错误翻译均符合预期")
sys.exit(0)
