# -*- coding: utf-8 -*-
"""隐盾 · 思考链路（跳过深化 / 深化 / 总结）回归（V3.6.9）

真实事故（用户截图）：「我桌面有多少文件」→ 工具真的列了目录 → 却回一句
**⚠ 回答生成失败：UnboundLocalError: cannot access local variable 'deepen_prompt'
where it is not associated with a value**。

根因不是模型，是**缩进**：`deepen_prompt` 只在 `else`（"该继续深化"）分支里赋值，
而紧跟其后的 `messages.append(HumanMessage(content=deepen_prompt, ...))` 比 `else` 体
**浅一层**，落到了外面——于是走"已得到实质回答、跳过深化轮"那一支时，代码直落下来
引用一个从未赋过值的局部变量，必然崩。

更糟的是崩的**正是"模型答得好"的路径**：答案越像样、越会命中"跳过深化轮"，
于是表现成"回答了却报错"，看起来像模型不行。

本套件用桩模型（不发真请求、不需要 Ollama）把三条路径逐条走一遍：
  A. 跳过深化轮（本轮已有实质答案）—— 事故现场，必须不崩、且有正常回答；
  B. 真走深化轮（答案太薄）—— 深化指令必须真的追加进消息（桩模型里核验内容）；
  C. 总结轮（轮数够了但答案仍薄）—— 必须给出总结且落库；
另外用 AST 钉一条结构性约束：**凡是引用 `deepen_prompt` 的语句必须与它的赋值同层**，
防止以后再把这类行挪出作用域（这是本套件存在的直接理由）。

用法：python tests/test_deepen_path.py   （退出码 0 = 全部通过）
"""
import ast
import os
import sys
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import AIMessage                      # noqa: E402
from yindun.worker.agent_worker import WORKER_EVENTS, Worker        # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def _ai(content="", tool_calls=None):
    """真 AIMessage：Worker 内部对消息类型有判断（自造桩会被静默跳过）。"""
    return AIMessage(content=content, tool_calls=list(tool_calls or []))


class ScriptedLLM:
    """按脚本出牌；记下每次调用收到的消息（用于核验"深化指令到底追加没追加"）。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self._lock = threading.Lock()

    def invoke(self, messages):
        with self._lock:
            self.calls.append(list(messages))
            index = len(self.calls) - 1
        step = self.script[min(index, len(self.script) - 1)]
        return _ai(step.get("content", ""), step.get("tool_calls"))

    def bind_tools(self, tools):
        return self

    def texts(self):
        out = []
        for call in self.calls:
            for message in call:
                content = getattr(message, "content", "")
                if isinstance(content, str) and content.strip():
                    out.append(content)
        return out


class FakeTool:
    """最小工具桩：必须同时可调用（Worker 对非 BaseTool 对象走直接调用分支）。"""

    def __init__(self, name, result):
        self.name = name
        self._result = result

    def invoke(self, args):
        return self._result

    def __call__(self, args):
        return self._result


VERBOSE_ANSWER = ("桌面上一共 12 个文件，其中 5 张图片、4 个文档、3 个快捷方式。\n"
                  "- 图片：a.png、b.jpg、c.png、d.jpg、e.png\n"
                  "- 文档：报告.docx、笔记.md、合同.pdf、清单.xlsx\n"
                  "如果需要按类型整理，我可以继续。")
# ★ 带句末标点的短答（V3.6.11）：不带标点的「好的」会被"疑似被截断"先接管，
#   那样测到的是截断重生成、而不是深化轮（两条纠正都要能各自被覆盖到）。
THIN_ANSWER = "好的。"


def make_worker(llm, user_input, think_mode="深度思考", depth=3):
    worker = Worker()
    worker.llm = llm
    worker.tools_map = {"list_local_files": FakeTool("list_local_files",
                                                     "桌面共 12 个条目：a.png b.jpg c.png")}
    worker.user_input = user_input
    worker.messages_snapshot = []
    worker.think_mode = think_mode
    worker.think_depth = depth
    worker.privacy_shield = False          # 关掉脱敏，断言不被占位符干扰（不影响被测逻辑）
    worker.sandbox_path = str(ROOT)
    worker.attachment_fulltext = {}
    worker._kb_instance = None
    return worker


def run_worker(worker, timeout=30.0):
    events = []
    for topic in WORKER_EVENTS:
        worker.bus.subscribe(topic, (lambda t: (lambda payload: events.append((t, payload))))(topic))
    thread = threading.Thread(target=worker.run, daemon=True)
    started = time.time()
    thread.start()
    thread.join(timeout)
    return events, time.time() - started, thread.is_alive()


def of(events, topic):
    return [payload for name, payload in events if name == topic]


def reply_text(worker):
    """本轮最终给用户的回答（落库链尾的助手文本）。"""
    for message in reversed(getattr(worker, "result_messages", []) or []):
        if message.get("role") == "assistant" and str(message.get("content") or "").strip():
            return str(message["content"])
    return ""


print("=" * 78)
print("【A】事故现场：本轮已有实质答案 → 走「跳过深化轮」分支（旧代码在这里崩）")
print("=" * 78)
llm_a = ScriptedLLM([
    {"content": "先看一下桌面。", "tool_calls": [{"name": "list_local_files",
                                                 "args": {"target_directory": "桌面"},
                                                 "id": "call_1"}]},
    {"content": VERBOSE_ANSWER},          # 拿到工具结果后的实质回答
    {"content": VERBOSE_ANSWER},
])
# ★ 必须给足够大的思考深度（depth 8 → min_rounds 6）：这样"第 2 轮就给出实质答案"
#   才会真的进入 `round_count < min_rounds` 那个 if（事故现场就是 8 轮档）。
worker_a = make_worker(llm_a, "我桌面有多少文件", depth=8)
events_a, seconds_a, hung_a = run_worker(worker_a)

check("一轮跑完没卡住（<30s）", not hung_a, f"{seconds_a:.1f}s 仍在跑")
failed_a = [e for e in events_a if e[0] == "error"]
check("★ 不再出现「回答生成失败」（这是旧代码的 UnboundLocalError 现场）",
      not failed_a, str(failed_a[:1])[:200])
check("★ 没有把异常打进回答里", "UnboundLocalError" not in reply_text(worker_a)
      and "回答生成失败" not in reply_text(worker_a), reply_text(worker_a)[:120])
check("状态里明确说了「跳过深化轮」（这正是旧代码紧接着崩掉的那一步）",
      any("跳过深化轮" in str(s) for s in of(events_a, "status")),
      str(of(events_a, "status"))[:200])
check("最终回答就是模型那段实质回答（未被改写坏、也未变空）",
      "12 个文件" in reply_text(worker_a), reply_text(worker_a)[:120])
check("工具真的被调用过（不是没跑工具的空转）",
      any("列出目录文件" in str(s) or "list_local_files" in str(s) for s in of(events_a, "status")),
      str(of(events_a, "status"))[:200])
check("没有把深化指令追加给模型（跳过就该真跳过）",
      not any("继续完成用户最初的问题" in text for text in llm_a.texts()),
      str([t[:40] for t in llm_a.texts()])[:200])

print("\n" + "=" * 78)
print("【B】答案太薄 → 真的走深化轮（深化指令必须被追加，且照旧不崩）")
print("=" * 78)
llm_b = ScriptedLLM([
    {"content": "", "tool_calls": [{"name": "list_local_files",
                                    "args": {"target_directory": "桌面"},
                                    "id": "call_1"}]},
    {"content": THIN_ANSWER},             # 太薄 → 触发深化
    {"content": VERBOSE_ANSWER},          # 深化之后给出实质回答
    {"content": VERBOSE_ANSWER},
])
worker_b = make_worker(llm_b, "我桌面有多少文件", depth=8)
events_b, seconds_b, hung_b = run_worker(worker_b)
check("一轮跑完没卡住（<30s）", not hung_b, f"{seconds_b:.1f}s 仍在跑")
check("★ 深化指令真的追加给了模型（且**锚定用户原问题**，V3.6.10 起不是写死的分析模板）",
      any("继续完成用户最初的问题" in text for text in llm_b.texts()),
      
      str([t[:60] for t in llm_b.texts()])[:240])
check("深化之后拿到了实质回答", "12 个文件" in reply_text(worker_b), reply_text(worker_b)[:120])
check("状态里写了「深化」这一步（不是悄悄跳过）",
      any("深化" in str(s) for s in of(events_b, "status")), str(of(events_b, "status"))[:200])
check("没有 error 事件", not [e for e in events_b if e[0] == "error"],
      str([e for e in events_b if e[0] == "error"][:1])[:200])

print("\n" + "=" * 78)
print("【C】轮数够了但答案仍薄 → 走总结轮（也要给出可交付回答）")
print("=" * 78)
llm_c = ScriptedLLM([
    {"content": "", "tool_calls": [{"name": "list_local_files",
                                    "args": {"target_directory": "桌面"},
                                    "id": "call_1"}]},
    # 第 2 轮给薄答案（不带工具调用）：depth=1 → min_rounds=2，于是走「总结轮」——
    # 注意别再让它调一次同一个工具：同工具同参数会被"重复调用短路"复用上次结果，
    # 那会把这条用例变成在测另一条分支（第一版就踩了）。
    {"content": THIN_ANSWER},
    {"content": VERBOSE_ANSWER},          # 总结轮拿到的回答
    {"content": VERBOSE_ANSWER},
])
# 场景 C 只要"轮数已够、答案仍薄"，用最小深度（depth 1 → min_rounds 2）即落在总结分支
worker_c = make_worker(llm_c, "我桌面有多少文件", depth=1)
events_c, seconds_c, hung_c = run_worker(worker_c)
check("一轮跑完没卡住（<30s）", not hung_c, f"{seconds_c:.1f}s 仍在跑")
check("给出可交付回答（不空、不崩）", "12 个文件" in reply_text(worker_c), reply_text(worker_c)[:120])
check("状态里写了「总结」这一步",
      any("总结" in str(s) for s in of(events_c, "status")), str(of(events_c, "status"))[:200])
check("没有 error 事件", not [e for e in events_c if e[0] == "error"],
      str([e for e in events_c if e[0] == "error"][:1])[:200])

print("\n" + "=" * 78)
print("【D】结构性约束：引用 deepen_prompt 的语句必须与它的赋值同层（防再犯）")
print("=" * 78)
source = (ROOT / "yindun" / "worker" / "agent_worker.py").read_text(encoding="utf-8")
tree = ast.parse(source)


def _blocks(node):
    """收集函数里所有语句块（用于比对"赋值"与"使用"是否在同一块）。"""
    found = []
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.With,
                              ast.FunctionDef, ast.AsyncFunctionDef)):
            for field in ("body", "orelse", "finalbody", "handlers"):
                block = getattr(child, field, None)
                if isinstance(block, list):
                    found.append([n for n in block if not isinstance(n, ast.Expr)
                                  or not isinstance(getattr(n, "value", None), ast.Constant)])
    return found


assign_block = None
use_blocks = []
for node in ast.walk(tree):
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        continue
    for block in _blocks(node):
        ids = set()
        for statement in block:
            for sub in ast.walk(statement):
                if isinstance(sub, ast.Name) and sub.id == "deepen_prompt":
                    if isinstance(sub.ctx, ast.Store):
                        ids.add("store")
                    else:
                        ids.add("load")
        if "store" in ids:
            assign_block = block
        if "load" in ids and "store" not in ids:
            use_blocks.append(block)

check("找到了 `deepen_prompt` 的赋值位置", assign_block is not None)
check("★ 每处使用都与赋值在**同一个语句块**（旧代码少了这三个缩进空格，于是直落出去引用未赋值变量）",
      bool(assign_block) and all(block is assign_block for block in use_blocks),
      f"赋值块 {id(assign_block)}，使用块 {[id(b) for b in use_blocks]}")

print("\n" + "=" * 78)
if failures:
    print(f"❌ 思考链路回归失败：{len(failures)} 项")
    for item in failures:
        print("   ·", item)
    sys.exit(1)
print("✅ 思考链路回归通过（跳过深化 / 深化 / 总结 三条路径都不崩，且结构性约束在册）")
sys.exit(0)
