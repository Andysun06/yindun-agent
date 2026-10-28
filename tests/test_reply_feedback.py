# -*- coding: utf-8 -*-
"""隐盾 · "有没有回复 / 有没有在思考" 反馈回归测试（真机事故固化）

事故（owner 反馈 + 现场证据）：
  · 客户端问「我的桌面有几张图片？」，等了好久既没有回答，也没有"思考中"之类的动画；
  · 助手气泡是**空的**；重开客户端后会话里只剩用户那句提问（回复整个消失）。
  · dist/chat_sessions.json 里那一轮确实只有 user 一条；dist 审计链该轮只有 llm_input、
    没有任何 llm_output；dist 知识库 0 篇文档 → 该问题被判为"简单问答"，走了直答快路径。

根因（本套件要钉住的行为）：
  1. 直答快路径只取模型消息的 `content`；而实测 qwen3.5:4b 对这类需要真实世界信息的问题会返回
     `content='' + tool_calls=[list_local_files(...)]` —— 工具调用被**静默丢掉**，回答是空串；
  2. 空串直接经 finished 发到界面，前端 finalizeBubble('') 就渲染成一个**空气泡**；
     落库那条空助手消息在下次加载时被丢弃 → 会话里只剩提问；
  3. 直答路径一条 intermediate_result 都不发，而前端聊天区的"等待动画"只挂在这条通道上，
     于是整段生成期间对话区没有任何"在思考"的迹象（trace 也没有落点，被丢帧）；
  4. 工具推理循环的"连续 2 轮失败就放弃"只认**抛异常**的工具调用：工具正常返回警告
     （如"⚠️ 指定路径超出安全沙箱"）不算失败，模型可以把 6 轮全用完；达到最大轮次时若
     总结为空串，旧实现也会把空串当回答返回；
  5. 失败分支旧写法 `result_messages = messages_snapshot`（本轮之前的历史）→ 服务层落库时
     把用户刚发的那条提问一起覆盖掉，而失败说明只经 error 事件闪一下（重启后什么都没有）。

本套件全部用**假模型 / 假工具**（不依赖网络与 Ollama），用固定判定把"直答快路径"这条车道
钉死，避免分类器以后一改变成在测另一条路。

用法：python tests/test_reply_feedback.py   （退出码 0 = 全部通过）
"""
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402
from yindun.core.policy_manager import SANDBOX_ROOT  # noqa: E402
from yindun.worker.agent_worker import WORKER_EVENTS, Worker  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── 假零件（形状与 langchain 消息/工具一致，避免真模型与真工具）──────────────
DESKTOP_QUESTION = "我的桌面有几张图片？"      # 事故原话


def _ai(content="", tool_calls=None, done_reason=None):
    """真 AIMessage（不是自造桩）：`SummarizableChatHistory.to_dict_list` 只认 langchain 的
    消息类型，自造桩会被静默跳过 —— 那样测到的"落库链"就不是线上那条链了。

    done_reason="length" 复现 Ollama 的单次生成长度上限（V3.6.13）。
    """
    meta = {"done_reason": done_reason} if done_reason else {}
    return AIMessage(content=content, tool_calls=list(tool_calls or []),
                     response_metadata=meta)


def _tool_call(name, args, cid="call_1"):
    return {"name": name, "args": dict(args), "id": cid}


class ScriptedLLM:
    """按脚本出牌：依次返回 script 里的 {content, tool_calls}；用完后一直返回最后一张。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen = []          # 每次调用收到的消息（查"宿主到底把什么喂给了模型"用）

    def invoke(self, messages):
        self.calls += 1
        self.seen.append(list(messages))
        step = self.script[min(self.calls - 1, len(self.script) - 1)]
        return _ai(step.get("content", ""), step.get("tool_calls"), step.get("done_reason"))

    def bind_tools(self, tools):
        return self


class RaisingLLM:
    """算力不可达/认证失败：invoke 直接抛异常（复现"错误没被呈现"的现场）。"""

    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise self.exc

    def bind_tools(self, tools):
        return self


class RepeatingToolCallLLM:
    """每一轮都用**新参数**再要一次同一个工具，content 始终为空（避免同参数短路命中）。"""

    def __init__(self, tool_name, base_dir):
        self.tool_name = tool_name
        self.base_dir = base_dir
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        args = {"target_directory": f"{self.base_dir}{os.sep}d{self.calls}"}
        return _ai("", [_tool_call(self.tool_name, args, f"call_{self.calls}")])

    def bind_tools(self, tools):
        return self


class FakeTool:
    """最小工具桩：★ 必须同时可调用——Worker 对非 BaseTool 的对象走"直接调用"分支，
    只实现 invoke 的桩会被当成普通对象返回 repr（工具其实没跑）。"""

    def __init__(self, name, fn):
        self.name = name
        self._fn = fn
        self.calls = []

    def invoke(self, args):
        self.calls.append(dict(args or {}))
        return self._fn(args)

    def __call__(self, args):
        self.calls.append(dict(args or {}))
        return self._fn(args)


class FastPathWorker(Worker):
    """把"简单问答 + 知识库为空"固定下来：直答快路径是本套件的被测对象。

    为什么固定判定：真实分类器是一组关键词启发式（"我的桌面有几张图片？"含问号、
    不含工具类关键词 → 判为简单问答），以后一改就可能让这些用例悄悄测到另一条车道。
    """

    def _is_simple_question(self, text):
        return True

    def _get_knowledge_base(self):
        return None        # 事故现场就是"知识库 0 篇文档"（也就顺带免掉 Chroma 依赖）


def make_worker(llm, tools_map, user_input, fast_path=False, think_mode="深度思考"):
    worker = FastPathWorker() if fast_path else Worker()
    worker.llm = llm
    worker.tools_map = tools_map
    worker.user_input = user_input
    worker.messages_snapshot = []
    worker.think_mode = think_mode
    worker.privacy_shield = False        # 关掉脱敏：断言不被占位符干扰（不影响被测逻辑）
    worker.sandbox_path = str(SANDBOX_ROOT)
    worker.attachment_fulltext = {}
    worker._kb_instance = None
    return worker


def run_worker(worker, timeout=30.0):
    """跑一轮推理；返回 (事件列表, 耗时, 是否到超时还没结束)。

    ★ "是否卡住"本身也是被测行为：真机事故就是"等了好久没回复"，
    所以这里绝不允许无限等待——到点即判失败，而不是让套件挂着。
    """
    events = []
    for topic in WORKER_EVENTS:
        worker.bus.subscribe(topic, (lambda t: (lambda payload: events.append((t, payload))))(topic))
    thread = threading.Thread(target=worker.run, daemon=True)
    t0 = time.time()
    thread.start()
    thread.join(timeout)
    return events, time.time() - t0, thread.is_alive()


def all_of(events, topic):
    return [payload for topic_name, payload in events if topic_name == topic]


def last_of(events, topic):
    values = all_of(events, topic)
    return values[-1] if values else None


def tail_is_assistant_text(worker, text):
    """落库链尾是不是"用户看到的那段文字"（不是空助手消息）。"""
    messages = worker.result_messages or []
    if not messages:
        return False
    last = messages[-1]
    return (last.get("role") == "assistant"
            and bool(str(last.get("content") or "").strip())
            and str(last.get("content") or "").strip() == str(text or "").strip())


print("=" * 78)
print("【0】分类器口径：需要真实世界信息的问题必须判为「需要工具」")
print("=" * 78)
_real = Worker()
_simple = _real._is_simple_question(DESKTOP_QUESTION)
print(f"  [ℹ️] _is_simple_question({DESKTOP_QUESTION!r}) = {_simple}"
      f"（False ＝ 会进入工具循环，不再走不执行工具的直答车道）")
check("★ 事故问题被判为「需要工具」（分类器已修，不再把它送进直答快路径）",
      _simple is False, str(_simple))

print("\n" + "=" * 78)
print("【1】直答路径不能吞掉模型的工具调用（事故主因）")
print("=" * 78)
_list_tool = FakeTool("list_local_files",
                      lambda args: "✅ 成功读取目录！当前目标沙箱 [sandbox] 内的文件有：a.txt, b.txt")
_llm = ScriptedLLM([
    # ① 直答这一枪：模型不改口回答，而是要求调工具（实测形态：content 为空）
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)})]},
    # ② 转入工具推理后第 1 轮：再要一次工具
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_2")]},
    # ③ 收尾：拿到工具结果后给结论
    {"content": "沙箱目录里有 a.txt、b.txt，没有图片文件；你的桌面不在我的沙箱内，我读不到。"},
])
_worker1 = make_worker(_llm, {"list_local_files": _list_tool}, DESKTOP_QUESTION, think_mode="深度思考")
_events1, _elapsed1, _stuck1 = run_worker(_worker1)
_fin1 = last_of(_events1, "finished")
check("一轮对话必须在有限时间内结束（不会让用户无限等）", not _stuck1, f"耗时 {_elapsed1:.1f}s 仍未结束")
check("不再出现空气泡：finished 一定有文字",
      bool(str(_fin1 or "").strip()), repr(_fin1))
check("模型要的工具真的被执行了（调用不再被静默丢弃）",
      len(_list_tool.calls) >= 1, f"工具的 invoke 次数={len(_list_tool.calls)}")
check("直答未成时明确告知用户「转工具推理」（过程可见）",
      any("工具" in str(p) for p in all_of(_events1, "status")), str(all_of(_events1, "status"))[:200])
check("落库链尾＝用户看到的那段文字（不是空助手消息）",
      tail_is_assistant_text(_worker1, _fin1),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker1.result_messages or [])[-3:]]))

print("\n" + "=" * 78)
print("【2】直答路径全程空产出：给可读说明 + 落库 + 有进度事件（「思考中」有东西可显示）")
print("=" * 78)
_worker2 = make_worker(ScriptedLLM([{"content": ""}]), {}, "介绍一下巴黎", fast_path=True)
_events2, _elapsed2, _stuck2 = run_worker(_worker2)
_fin2 = last_of(_events2, "finished")
_statuses2 = all_of(_events2, "status")
_inter2 = all_of(_events2, "intermediate_result")
check("模型全程空内容 → 仍给出可读回答（不是空白）",
      bool(str(_fin2 or "").strip()), repr(_fin2))
check("有进度事件：status（状态行）与 intermediate_result（聊天区等待/渐进通道）都发过",
      bool(_statuses2) and bool(_inter2),
      f"status={_statuses2[:3]} / intermediate={_inter2[:3]}")
check("进度事件发生在 finished 之前（不是事后补发的马后炮）",
      bool(_events2) and [t for t, _ in _events2].index("intermediate_result")
      < [t for t, _ in _events2].index("finished"),
      str([t for t, _ in _events2]))
check("落库链尾非空（重启后不会看到空白那一轮）", tail_is_assistant_text(_worker2, _fin2),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker2.result_messages or [])]))

print("\n" + "=" * 78)
print("【3】回归：直答正常出文字时行为不变（不多跑一次模型，落库与界面一致）")
print("=" * 78)
_text_answer = "巴黎是法国的首都，塞纳河穿城而过。"
_llm3 = ScriptedLLM([{"content": _text_answer}])
_worker3 = make_worker(_llm3, {}, "介绍一下巴黎", fast_path=True)
_events3, _elapsed3, _stuck3 = run_worker(_worker3)
_fin3 = last_of(_events3, "finished")
check("正常回答原样送达", str(_fin3 or "").strip() == _text_answer, repr(_fin3))
check("直答只调用一次模型（不因兜底多跑）", _llm3.calls == 1, f"invoke={_llm3.calls}")
check("落库链尾＝该回答", tail_is_assistant_text(_worker3, _fin3),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker3.result_messages or [])]))

# ★ 状态行前缀必须跟着当前模式走（V3.6.13，负责人截图：选了「思考」却显示「[快速回答]」）
_llm3b = ScriptedLLM([{"content": "思考模式下的直答。"}])
_worker3b = make_worker(_llm3b, {}, "介绍一下巴黎", think_mode="深度思考")
_events3b, _, _ = run_worker(_worker3b)
_status3b = [str(x) for x in all_of(_events3b, "status")]
check("★ 工作模式的直答状态行写「[工作]」，不再冒充「[对话]」",
      any("[工作] 直接作答" in s for s in _status3b)
      and not any("[对话]" in s for s in _status3b), str(_status3b))
_llm3c = ScriptedLLM([{"content": "快速模式下的直答。"}])
_worker3c = make_worker(_llm3c, {}, "介绍一下巴黎", think_mode="快速回答")
_events3c, _, _ = run_worker(_worker3c)
_status3c = [str(x) for x in all_of(_events3c, "status")]
check("对话模式写「[对话]」（不是一刀切改名）",
      any("[对话] 直接作答" in s for s in _status3c), str(_status3c))

print("\n" + "=" * 78)
print("【4】无进展收敛：工具「正常返回警告」的打转不再跑满轮次，如实收尾")
print("=" * 78)
# 声明式失败（真机原话）：工具没抛异常，只回一句越界警告——旧实现不把它算失败，
# 于是模型可以一直要工具、一直撞墙，用户一直在等。
_warn_tool = FakeTool("list_local_files",
                      lambda args: "⚠️ 指定路径超出安全沙箱，已回退到默认目录。")
_llm4 = ScriptedLLM([{"content": "", "tool_calls": [
    _tool_call("list_local_files", {"target_directory": str(SANDBOX_ROOT)})]}])
_worker4 = make_worker(_llm4, {"list_local_files": _warn_tool}, "读一下这个文件并总结")
_events4, _elapsed4, _stuck4 = run_worker(_worker4)
_fin4 = last_of(_events4, "finished")
check("有限时间内结束", not _stuck4, f"耗时 {_elapsed4:.1f}s 仍未结束")
check("★ 无进展两轮就收尾（不再把 6 轮全撞完）", _llm4.calls < 6, f"invoke={_llm4.calls}")
check("收尾文案如实说明「没能取得有效结果」并给下一步",
      bool(str(_fin4 or "").strip()) and "没能取得有效结果" in str(_fin4), repr(str(_fin4)[:120]))
check("放弃时发状态事件（用户能看到是「停了」而不是一直转）",
      any("[放弃]" in str(p) for p in all_of(_events4, "status")),
      str(all_of(_events4, "status"))[:200])
check("落库链尾非空", tail_is_assistant_text(_worker4, _fin4),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker4.result_messages or [])]))

print("\n" + "=" * 78)
print("【5】达到最大轮次且总结为空：仍给可读说明（旧实现会把空串当回答）")
print("=" * 78)
_empty_tool = FakeTool("list_local_files", lambda args: "")     # 工具"成功"但什么都没返回
_llm5 = RepeatingToolCallLLM("list_local_files", str(SANDBOX_ROOT))
_worker5 = make_worker(_llm5, {"list_local_files": _empty_tool}, "读一下这个文件并总结")
_events5, _elapsed5, _stuck5 = run_worker(_worker5)
_fin5 = last_of(_events5, "finished")
check("有限时间内结束", not _stuck5, f"耗时 {_elapsed5:.1f}s 仍未结束")
check("走完「轮次上限 + 总结」这条路径（invoke=7）", _llm5.calls == 7, f"invoke={_llm5.calls}")
check("★ 总结为空时不发空串：换成如实说明",
      bool(str(_fin5 or "").strip()) and "没有拿到可用的结果" in str(_fin5), repr(str(_fin5)[:120]))
check("落库链尾非空", tail_is_assistant_text(_worker5, _fin5),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker5.result_messages or [])]))

print("\n" + "=" * 78)
print("【5.5】清点类问句（「桌面有多少文件」）：宿主自己列一次并给出准确数字")
print("=" * 78)
# 真实事故（负责人实测，2b 模型）：「我桌面有多少文件」既没有绝对路径、也没有「列出/看看」
# 这类动词，宿主的强制工具分支全都没命中 → 弱模型自己发挥（回了一段「我无法访问桌面」）。
# 现在补上「清点意图 + 位置词」的触发，并且**连数字都由宿主数准**再交给模型
# （2b 对着一长串文件名会数错：同一次提问两次运行给出 48 与 61）。
_count_dir = Path(SANDBOX_ROOT) / "count_probe"
_count_dir.mkdir(parents=True, exist_ok=True)
for _i in range(7):
    (_count_dir / f"f{_i}.txt").write_text("x", encoding="utf-8")
(_count_dir / "sub").mkdir(exist_ok=True)

_worker55 = make_worker(ScriptedLLM([{"content": "好的，我看看。"}]),
                        {"list_local_files": FakeTool("list_local_files",
                                                      lambda args: "f0.txt f1.txt …（清单）")},
                        "这个目录里有多少文件？")
_worker55.sandbox_path = str(_count_dir)
_events55, _elapsed55, _stuck55 = run_worker(_worker55)
check("有限时间内结束", not _stuck55, f"耗时 {_elapsed55:.1f}s 仍未结束")
_sent55 = "\n".join(str(getattr(m, "content", ""))
                    for call in _worker55.llm.seen for m in call)
check("★ 宿主自己列了目录（不再指望弱模型主动调工具）",
      any("清点目录" in str(p) for p in all_of(_events55, "status")),
      str(all_of(_events55, "status"))[:200])
check("★ 交给模型的是宿主数准的数字（7 个文件、1 个子目录）",
      "文件 7 个" in _sent55 and "子目录 1 个" in _sent55, _sent55[-200:] or "（模型没收到清点结果）")
check("清点结果里提醒了「以这组数字为准」", "以这组数字为准" in _sent55, _sent55[-160:] or "")

_worker55b = make_worker(ScriptedLLM([{"content": "这是一段示例代码。"}]),
                         {"list_local_files": FakeTool("list_local_files", lambda args: "x")},
                         "帮我写一段代码生成几张图片的说明")
_events55b, _, _ = run_worker(_worker55b)
check("创作型请求不被劫持（写代码 + 几张图片 ≠ 清点文件）",
      not [p for p in all_of(_events55b, "status") if "清点目录" in str(p)],
      str(all_of(_events55b, "status"))[:160])

print("\n" + "=" * 78)
print("【5.6】自纠错：没调工具就宣布「我读不到 / 没权限」→ 叫回来重做（对任何问法生效）")
print("=" * 78)
# 负责人质疑：「不提关键词就不能调工具了吗，主流 agent 怎么做？」
# 主流是原生 function calling + tool_choice 强制；但 **Ollama 不支持 tool_choice**，
# 于是 harness 层补一条**按症状触发**的纠错：判据看模型答成了什么样（推诿话术），
# 而不是猜用户的问法 —— 这样"桌面有多少文件""下载里存了啥"这类不带动作动词的问法同样被救回来。
_WAFFLE = "我无法直接访问您的桌面（Desktop）文件夹。根据当前权限范围，我只能查看以下目录：…"
_llm56 = ScriptedLLM([
    {"content": _WAFFLE},                                   # 第 1 轮：不调工具、只讲权限（事故原话）
    {"content": "", "tool_calls": [                         # 第 2 轮：被纠正后真的去列目录
        {"name": "list_local_files", "args": {"target_directory": "系统物理桌面"}, "id": "call_1"}]},
    {"content": "桌面上一共 12 个条目：3 张图片、9 个文档。"},   # 第 3 轮：拿到真实清单后作答
])
_worker56 = make_worker(_llm56, {"list_local_files": FakeTool("list_local_files",
                                                             lambda args: "a.png b.jpg c.docx")},
                        "我桌面有什么", think_mode="深度思考")   # ★ 特意用**不含任何动作动词**的问法
_events56, _elapsed56, _stuck56 = run_worker(_worker56)
_fin56 = last_of(_events56, "finished")
check("有限时间内结束", not _stuck56, f"耗时 {_elapsed56:.1f}s 仍未结束")
check("★ 识别出推诿话术后要求模型重做（状态里可见）",
      any("[纠正]" in str(p) and "要求它改用工具真做" in str(p)
          for p in all_of(_events56, "status")),
      str(all_of(_events56, "status"))[:200])
check("★ 纠正之后工具真的被调用了（问法里没有任何'列出/查看'之类动词）",
      _llm56.calls >= 3 and any("list_local_files" in str(t) for t in _llm56.seen[1]),
      f"invoke={_llm56.calls}")
check("纠正指令里点名了工具与用法（不是空喊'请重试'）",
      any("list_local_files" in str(getattr(m, "content", "")) for m in _llm56.seen[-1]),
      str([str(getattr(m, 'content', ''))[:40] for m in _llm56.seen[-1]])[:200])
check("最终回答是拿到工具结果之后的那段（不是推诿那段）",
      _fin56 == "桌面上一共 12 个条目：3 张图片、9 个文档。", repr(str(_fin56)[:120]))

# 负例：正常闲聊不该被这条纠正打扰（否则每句寒暄都要多跑一轮）
_llm57 = ScriptedLLM([{"content": "你好，我是隐盾，可以帮你脱敏、整理文件与做本地检索。"}])
_worker57 = make_worker(_llm57, {"list_local_files": FakeTool("list_local_files", lambda a: "x")},
                        "你好")
_events57, _, _ = run_worker(_worker57)
check("闲聊不会被自纠错误伤（只跑一轮、无纠正状态、无工具调用）",
      _llm57.calls == 1
      and not [p for p in all_of(_events57, "status") if "一次工具都没调用" in str(p)],
      f"invoke={_llm57.calls} status={str(all_of(_events57, 'status'))[:120]}")

print("\n" + "=" * 78)
print("【5.7】假成功：声称「已成功创建」但一次工具都没调 → 纠正 + 事实核对（负责人实测）")
print("=" * 78)
# 原始症状（截图 + 审计链双重确认）：用户说「我帮aaa.txt桌面创建文件」，模型回
# 「文件 "aaa.txt" 已成功创建在你的桌面上。」——审计里那轮**没有任何 tool_call**，
# 桌面上也没有文件；而且"没弹审批"（审批只在真的执行工具时才弹）。
_FAKE_OK = '文件 "aaa.txt" 已成功创建在你的桌面上。'
_prev_perm57 = os.environ.get("YINDUN_PERMISSION")
os.environ["YINDUN_PERMISSION"] = "full"     # 深度模式下写工具会走审批；满权档直接执行
_llm58 = ScriptedLLM([
    {"content": _FAKE_OK},                                   # 假成功
    {"content": "", "tool_calls": [                          # 被纠正后真做
        {"name": "create_local_file", "args": {"filename": "aaa.txt", "content": "hi"}, "id": "c1"}]},
    {"content": "已在桌面创建 aaa.txt（内容：hi）。"},
])
_worker58 = make_worker(_llm58, {"create_local_file": FakeTool("create_local_file",
                                                              lambda a: "✅ 物理创建成功")},
                        "帮我在桌面创建一个 aaa.txt",
                        # ★ 写类请求在快速模式下会被**前置拦截**（BUG-0007）；这里要测的
                        #   是"模型假成功"的自纠错，所以必须走允许写操作的思考模式。
                        think_mode="深度思考")
_events58, _, _stuck58 = run_worker(_worker58)
_fin58 = last_of(_events58, "finished")
check("有限时间内结束", not _stuck58)
check("★ 识别「已完成但没调工具」并要求改用工具真做",
      any("[纠正]" in str(p) and "已经做完" in str(p) for p in all_of(_events58, "status")),
      str(all_of(_events58, "status"))[:200])
check("纠正之后 create_local_file 真的被调用了",
      any("create_local_file" in str(t) for t in _llm58.seen[1]),
      str([str(getattr(m, 'content', ''))[:30] for m in _llm58.seen[1]])[:160])
check("最终回答是工具执行后的如实说明（不再是假成功）",
      str(_fin58).startswith("已在桌面创建 aaa.txt（内容：hi）。")
      and "本次工具核对" in str(_fin58)
      and "创建文件" in str(_fin58), repr(str(_fin58)[:160]))

_llm59 = ScriptedLLM([{"content": _FAKE_OK}])                # 纠正也不改口：压根不支持工具调用
_worker59 = make_worker(_llm59, {"create_local_file": FakeTool("create_local_file", lambda a: "x")},
                        "帮我在桌面创建一个 aaa.txt", think_mode="深度思考")
_events59, _, _ = run_worker(_worker59)
_fin59 = str(last_of(_events59, "finished") or "")
check("★ 模型始终不调工具时，最终回答里带「事实核对：本轮一次工具都没有调用」",
      "⚠️ 事实核对" in _fin59 and "并没有真的执行" in _fin59, repr(_fin59[-200:]))
check("事实核对里说明「审批也不会弹」（正是负责人遇到的现象）",
      "审批也不会弹" in _fin59, repr(_fin59[-160:]))
check("事实核对给了下一步（换支持工具调用的模型）",
      "function calling" in _fin59 and "模型接入" in _fin59, repr(_fin59[-160:]))
if _prev_perm57 is None:
    os.environ.pop("YINDUN_PERMISSION", None)
else:
    os.environ["YINDUN_PERMISSION"] = _prev_perm57

print("\n" + "=" * 78)
print("【6】失败路径：错误必须变成用户可见的回复文本并落库（不再删掉用户提问）")
print("=" * 78)
_worker6 = make_worker(RaisingLLM(RuntimeError("Connection refused: 无法连接模型服务")),
                       {}, DESKTOP_QUESTION, think_mode="深度思考")
_events6, _elapsed6, _stuck6 = run_worker(_worker6)
_err6 = last_of(_events6, "error")
check("有错误事件且文案可读（指明是连不上模型服务）",
      bool(str(_err6 or "").strip()) and "无法连接" in str(_err6), repr(str(_err6)[:120]))
_chain6 = _worker6.result_messages or []
check("★ 失败也落库：链里有用户提问 + 失败说明（旧实现会把提问整条覆盖掉）",
      any(m.get("role") == "user" for m in _chain6)
      and _chain6[-1].get("role") == "assistant"
      and str(_chain6[-1].get("content") or "").strip() == str(_err6 or "").strip(),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in _chain6[-3:]]))

print("\n" + "=" * 78)
print("【7】服务层端到端（假算力，不联网）：事件 → 落库 → 重启后仍能读到")
print("=" * 78)
TMP = Path(tempfile.mkdtemp(prefix="yindun_reply_test_"))
_svc = AgentService(settings=SettingsStore(path=TMP / "cfg.json"),
                    sessions=SessionStore(path=TMP / "sess.json"))
_svc.initialize()
_svc.settings.set("think_mode", "深度思考")

# 7.1 正常回答：状态/渐进事件齐备，回复落库且重启后还在
_ok_text = "沙箱目录里有 a.txt 和 b.txt，没有图片文件。"
_ok_llm = ScriptedLLM([{"content": _ok_text}])
_svc._llm = _ok_llm
_svc._llm_variants = {False: _ok_llm, True: _ok_llm}
_svc._tools_map = {}
_events7 = []
_svc.set_listener(lambda event, payload: _events7.append((event, payload)))
check("服务层接受发送", _svc.send(DESKTOP_QUESTION) is True)
_deadline = time.time() + 30
while time.time() < _deadline and not any(e in ("finished", "error") for e, _ in _events7):
    time.sleep(0.05)
while _svc.busy and time.time() < _deadline:
    time.sleep(0.05)
_fin7 = next((p for e, p in _events7 if e == "finished"), None)
check("服务层转发 finished（回答送达界面）", bool(str(_fin7 or "").strip()), repr(_fin7))
check("等待期间有状态/渐进事件（「思考中」有东西可显示）",
      any(e == "status" for e, _ in _events7) and any(e == "intermediate_result" for e, _ in _events7),
      str([e for e, _ in _events7])[:200])
_sid = _svc.current_session_id()
_msgs7 = _svc.sessions.get_messages(_sid) or []
_assist7 = [m for m in _msgs7 if m.get("role") == "assistant"]
check("会话里写入了这条回复（且与界面文本一致）",
      bool(_assist7) and str(_assist7[-1].get("content") or "").strip() == str(_fin7 or "").strip(),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in _msgs7]))
_svc.sessions.save()
_reloaded = SessionStore(path=TMP / "sess.json")
_reloaded.load()
_reload_msgs = [m for m in (_reloaded.get_messages(_sid) or []) if m.get("role") == "assistant"]
check("★ 重启后那一轮还在、且非空（空助手消息会被加载器丢弃，这里必须能读到）",
      bool(_reload_msgs) and bool(str(_reload_msgs[-1].get("content") or "").strip()),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in _reload_msgs]))

# 7.2 算力失败：错误变成会话里的回复文本（重开客户端也看得到）
_events8 = []
_svc.set_listener(lambda event, payload: _events8.append((event, payload)))
_fail_llm = RaisingLLM(RuntimeError("401 Unauthorized: invalid api key"))
_svc._llm = _fail_llm
_svc._llm_variants = {False: _fail_llm, True: _fail_llm}
check("服务层接受第二次发送", _svc.send("再问一次：我的桌面有几张图片？") is True)
_deadline = time.time() + 30
while time.time() < _deadline and not any(e in ("finished", "error") for e, _ in _events8):
    time.sleep(0.05)
while _svc.busy and time.time() < _deadline:
    time.sleep(0.05)
_err8 = next((p for e, p in _events8 if e == "error"), None)
check("算力认证失败 → 用户可见的失败说明（直说密钥问题）",
      bool(str(_err8 or "").strip()) and "API 密钥" in str(_err8), repr(str(_err8)[:120]))
_msgs8 = _svc.sessions.get_messages(_sid) or []
check("★ 失败本轮也留下「提问 + 说明」（旧实现会把提问一起删掉，重启后那一轮消失）",
      len(_msgs8) >= 4
      and _msgs8[-2].get("role") == "user"
      and _msgs8[-1].get("role") == "assistant"
      and str(_msgs8[-1].get("content") or "").strip() == str(_err8 or "").strip(),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in _msgs8[-4:]]))

print("\n" + "=" * 78)

print("\n" + "=" * 78)
print("【5.8】截断：只吐 2-3 个字就「已完成」→ 重生成一次 + 如实标注（组员评审 BUG-0003）")
print("=" * 78)
# 原始症状：同一段输入，回答只输出「我需要」/「您好」/「为了」，气泡却标「已完成」；
# 审计佐证模型侧 content_length 就是 2~3。**静默半句话 + 已完成** 是本次要根治的。
_llm60 = ScriptedLLM([
    {"content": "我需要"},                                   # 被截断的开头
    {"content": "这份客户资料包含 6 类敏感信息：姓名、手机、邮箱、身份证、地址、统一社会信用代码。"
                "其中电话与身份证已在本机脱敏后再送模型，回答返回时自动还原。"},
])
_worker60 = make_worker(_llm60, {}, "讲讲这份客户资料的要点与风险")
_events60, _, _stuck60 = run_worker(_worker60)
_fin60 = str(last_of(_events60, "finished") or "")
check("有限时间内结束", not _stuck60)
check("★ 识别「疑似被截断」并要求重写完整回答",
      any("[纠正]" in str(p) and "截断" in str(p) for p in all_of(_events60, "status")),
      str(all_of(_events60, "status"))[:200])
check("最终回答是重生成后的完整内容（不再是半句话）",
      "6 类敏感信息" in _fin60 and not _fin60.strip().startswith("我需要"),
      repr(_fin60[:90]))
check("重生成只做一次（invoke 计数=2，不会反复追问）", _llm60.calls == 2, f"invoke={_llm60.calls}")

_llm61 = ScriptedLLM([{"content": "我需要"}])                # 重生成后仍然断句
_worker61 = make_worker(_llm61, {}, "讲讲这份客户资料的要点与风险")
_events61, _, _ = run_worker(_worker61)
_fin61 = str(last_of(_events61, "finished") or "")
check("★ 重生成后仍被截断时，如实标注（不假装已完成）",
      "⚠️ 提示" in _fin61 and "被截断" in _fin61, repr(_fin61[-160:]))
check("标注里给了下一步（再发一次 / 换更大模型）",
      "再发一次" in _fin61 and "更大的模型" in _fin61, repr(_fin61[-160:]))
check("寒暄不受影响（「谢谢」这类短答不该被判截断）",
      not Worker._looks_truncated_answer("好的", "谢谢")
      and not Worker._looks_truncated_answer("42", "1+1 等于几"), "")


print("\n" + "=" * 78)
print("【5.9】审批文案不再自相矛盾：只说「需要你确认（本档位要求）」，不做风险定性")
print("=" * 78)
# 负责人实测现场：同一轮里「插件说紧张 + 风险分级说低危 + 状态行说🚨敏感操作」三行打架。
# 这行状态的职责只是"当前档位要求你确认一下"，风险高低由风险分级那行负责——别抢它的活。
import os as _os                                                        # noqa: E402
_prev_perm = _os.environ.get("YINDUN_PERMISSION")
_os.environ["YINDUN_PERMISSION"] = "ask"          # 变更前确认档：写盘类会走 confirm 分支
try:
    _worker59b = make_worker(ScriptedLLM([{"content": "ok"}]), {}, "建个文件")
    _statuses = []
    _worker59b.status.subscribe("status", _statuses.append) if hasattr(_worker59b.status, "subscribe") \
        else None
    _worker59b.bus.subscribe("status", _statuses.append)
    _worker59b._request_approval = lambda *a, **k: False          # 直接驳回：不阻塞、只看文案
    _tmp_target = str(SANDBOX_ROOT)
    _result = _worker59b._execute_with_approval("create_local_file",
                                                {"filename": "审批文案探针.txt",
                                                 "target_directory": _tmp_target})
    _joined = " | ".join(str(s) for s in _statuses)
    check("★ 审批状态行说「需要你确认」并点明原因（档位要求）",
          "需要你确认" in _joined and "档位要求人工审批" in _joined, _joined[:200])
    check("★ 不再出现「🚨 敏感操作」这种风险定性（避免与风险分级那行打架）",
          "敏感操作" not in _joined, _joined[:200])
    check("被驳回时如实返回驳回结果（不静默）", "已驳回" in str(_result) or "驳回" in str(_result),
          str(_result)[:120])
finally:
    if _prev_perm is None:
        _os.environ.pop("YINDUN_PERMISSION", None)
    else:
        _os.environ["YINDUN_PERMISSION"] = _prev_perm

print("\n" + "=" * 78)
print("【5.10】落盘内容必须还原掩码（跨轮占位符也要），还原不了就点破")
print("=" * 78)
# 负责人实测：让智能体把客户信息写进 aaa.docx，打开文件却是 `[PHONE_0_h5ko]`——
# 写盘参数以前只拿**本轮**的 box 去还原，上一轮掩码过的占位符一个都换不回来
# （界面上那段回答是还原过的，于是「用户看到的」与「落盘的」不一致）。
from yindun.core.privacy_engine import PrivacyEngine as _PE                 # noqa: E402
from yindun.core import file_tools as _FT                                    # noqa: E402
from pathlib import Path as _Path                                            # noqa: E402

_prev_perm2 = _os.environ.get("YINDUN_PERMISSION")
_os.environ["YINDUN_PERMISSION"] = "full"       # 让写盘真的执行（默认档是"变更前确认"，会走审批）
_eng_pw = _PE()
_anon_text, _cross_turn_map = _eng_pw.anonymize("联系电话 13713068560")       # 模拟"上一轮"的掩码
_ph = next(iter(_cross_turn_map))
_llm52 = ScriptedLLM([
    {"content": "", "tool_calls": [{"name": "create_local_file",
                                    "args": {"filename": "客户信息.txt",
                                             "content": f"客户信息\n电话：{_ph}",
                                             "target_directory": str(SANDBOX_ROOT)},
                                    "id": "c1"}]},
    {"content": "已把客户信息写入文件。"},
])
_worker52 = make_worker(_llm52, {"create_local_file": _FT.create_local_file}, "把客户信息记到文件里",
                        think_mode="深度思考")     # 写盘类工具在快速档会被拦，这里要真写
_worker52.privacy_shield = True      # ★ 要测的就是"网关开启时落盘内容必须还原"
_worker52._box_mapping = dict(_cross_turn_map)        # ★ 跨轮映射：本轮 box 里没有这条
_events52, _, _stuck52 = run_worker(_worker52)
_written = _Path(SANDBOX_ROOT) / "客户信息.txt"
check("有限时间内结束", not _stuck52)
check("★ 跨轮占位符在落盘时被还原成真值（文件里不是 [PHONE_…]）",
      _written.exists() and "13713068560" in _written.read_text(encoding="utf-8")
      and "[PHONE_" not in _written.read_text(encoding="utf-8"),
      _written.read_text(encoding="utf-8")[:120] if _written.exists() else "（没写出文件）")
if _written.exists():
    _written.unlink()

_llm53 = ScriptedLLM([
    {"content": "", "tool_calls": [{"name": "create_local_file",
                                    "args": {"filename": "坏映射.txt",
                                             "content": "电话：[PHONE_9_zzzz]",
                                             "target_directory": str(SANDBOX_ROOT)},
                                    "id": "c1"}]},
    {"content": "已写入。"},
])
_worker53 = make_worker(_llm53, {"create_local_file": _FT.create_local_file}, "把信息写进文件",
                        think_mode="深度思考")
_worker53.privacy_shield = True
_events53, _, _ = run_worker(_worker53)
_fin53 = str(last_of(_events53, "finished") or "")
check("★ 映射里没有的占位符：回答里点破「有 N 处未能还原」",
      "未能还原" in _fin53 and "占位符" in _fin53, repr(_fin53[-180:]))
check("状态里也提示了未还原（过程可见，不只藏在回答里）",
      any("未能还原" in str(p) for p in all_of(_events53, "status")),
      str(all_of(_events53, "status"))[-200:])
_bad = _Path(SANDBOX_ROOT) / "坏映射.txt"
if _bad.exists():
    _bad.unlink()
if _prev_perm2 is None:
    _os.environ.pop("YINDUN_PERMISSION", None)
else:
    _os.environ["YINDUN_PERMISSION"] = _prev_perm2

print("\n" + "=" * 78)
print("【5.11】快速模式 + 写类意图：前置拦截（不调模型）+ 空产出诊断（BUG-0007/0008）")
print("=" * 78)
# 现场（V3.6.11 组员评审 BUG-0007）：权限「变更前确认」+ 快速模式，发
# 「在当前沙箱目录创建 approval_probe.txt 并写入 alpha/beta/gamma」→ 模型 4/4 返回空
# content + 无 tool_calls → 用户只得到一句"小模型偶发空回复"，审计 0 条 tool_call。
# 根因：快速模式拦写操作的提示只在**模型真的发起工具调用之后**才出现。现在前置到进入模型之前。
_intent = Worker()
_write_positives = [
    ("创建 + 带扩展名文件", "在当前沙箱目录创建 approval_probe.txt 并写入 alpha/beta/gamma"),
    ("删除 + 文档", "把桌面上的报告.docx 删除"),
    ("新建文件夹", "在沙箱里新建文件夹 backup"),
    ("移动 + 目录名词", "把照片挪到备份文件夹"),
    ("运行 + 脚本", "运行这个脚本"),
    ("修改 + 绝对路径", r"修改 E:\yindun\demo.txt"),
    # P0-02：征求许可仍是写请求（旧实现把"可以吗"当咨询豁免 → 漏拦）
    ("征求许可 + 落盘对象", "帮我把文件保存到桌面，可以吗"),
    ("落盘位置（无文件名）", "把这份纪要保存到桌面"),
]
for _label, _text in _write_positives:
    check(f"写意图正例：{_label}", _intent._looks_like_write_request(_text) is True, _text)
_write_negatives = [
    ("只读清点", "读一下桌面有几个文件"),
    ("只读分析", "分析这个目录"),
    ("咨询：怎么创建", "怎么创建一个文件"),
    ("创作：写一段代码", "帮我写一段代码生成几张图片的说明"),
    ("创作：写个函数", "写个函数打印 hello"),
    # P0-02：过去时追问是在问历史，不是写请求（旧实现按"动作词+名词"误拦）
    ("过去时：何时被删除", "这份文件是什么时候被删除的？"),
    ("过去时：谁修改的", "这个文档是谁修改的"),
    ("过去时：谁删除了", "上次是谁删除了这个文件"),
    # 咨询语义里仍带"能否"时，靠"怎么"识别为咨询（不能因为去掉"能否"而误拦）
    ("咨询：能否告诉我怎么做", "能否告诉我怎么创建文件"),
]
for _label, _text in _write_negatives:
    check(f"写意图负例：{_label}", _intent._looks_like_write_request(_text) is False, _text)

_WRITE_REQ = "在当前沙箱目录创建 approval_probe.txt 并写入 alpha/beta/gamma"
_llm_w = ScriptedLLM([{"content": "这一句绝不该被使用"}])
_worker_w = make_worker(_llm_w, {}, _WRITE_REQ, fast_path=True, think_mode="快速回答")
_events_w, _elapsed_w, _stuck_w = run_worker(_worker_w)
_fin_w = str(last_of(_events_w, "finished") or "")
check("★ 前置拦截：一次模型都没有调用", _llm_w.calls == 0, f"invoke={_llm_w.calls}")
check("★ 不是故障：不发 error 事件", not all_of(_events_w, "error"), str(all_of(_events_w, "error"))[:120])
check("有 status + finished（用户看得到限制说明，而不是一直转圈）",
      bool(all_of(_events_w, "status")) and bool(_fin_w), str(all_of(_events_w, "status"))[:160])
check("★ 说明点明：模式限制 + 怎么切 + 切后弹审批",
      "对话" in _fin_w and "工作" in _fin_w and "审批" in _fin_w, _fin_w[:160])
check("前置拦截的说明也落库（刷新/重启后仍能看到）",
      tail_is_assistant_text(_worker_w, _fin_w),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker_w.result_messages or [])[-3:]]))
check("前置拦截没有产生工具调用（审计里也不会有 tool_call）",
      _worker_w.tool_call_attempts == 0 and _worker_w.tool_call_count == 0,
      f"attempts={_worker_w.tool_call_attempts} count={_worker_w.tool_call_count}")

_llm_ro = ScriptedLLM([{"content": "桌面上一共有 3 个文件。"}])
_worker_ro = make_worker(_llm_ro, {}, "读一下桌面有几个文件", think_mode="深度思考")
_events_ro, _, _ = run_worker(_worker_ro)
_fin_ro = str(last_of(_events_ro, "finished") or "")
check("★ 只读请求不被误拦（照常调用模型并回答）",
      _llm_ro.calls == 1 and _fin_ro == "桌面上一共有 3 个文件。",
      f"invoke={_llm_ro.calls} reply={_fin_ro[:60]!r}")

# 空产出兜底：不再是"每 5 次一次"的运气论，而是可核对的诊断
check("★ 通用空产出说明可执行（测试连接 / 切工作 / 换模型）",
      "测试连接" in _fin2 and "工作" in _fin2 and "换一个模型" in _fin2, _fin2[:180])
check("★ 不再写「偶发空回复 / 每 5 次」这类与当前模型无关的统计",
      "偶发空回复" not in _fin2 and "每 5 次" not in _fin2, _fin2[:180])

# 写类意图的空产出：必须解释"为什么没弹审批"
_llm_wf = ScriptedLLM([{"content": ""}])
_worker_wf = make_worker(_llm_wf, {}, _WRITE_REQ, think_mode="深度思考")
_events_wf, _elapsed_wf, _stuck_wf = run_worker(_worker_wf)
_fin_wf = str(last_of(_events_wf, "finished") or "")
check("写类请求空产出：说明本轮没有工具调用、所以不会弹审批",
      "没有产生任何工具调用" in _fin_wf and "不会弹出审批" in _fin_wf, _fin_wf[:200])
check("写类请求空产出：给出「测试连接」与「切到工作」的下一步",
      "测试连接" in _fin_wf and "工作" in _fin_wf, _fin_wf[:200])
check("写类请求空产出：落库链尾＝该说明",
      tail_is_assistant_text(_worker_wf, _fin_wf),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_worker_wf.result_messages or [])[-3:]]))

# BUG-0008：兜底文案不是模型输出，审计必须能看出"模型空产出"
from yindun.core.audit_log import AuditLog as _AuditLog                     # noqa: E402
_audit = _AuditLog()
_llm_outputs = _audit.get_entries({"event_type": "llm_output"})
_last_out = _llm_outputs[-1] if _llm_outputs else None
check("★ 审计：兜底文案不计为模型输出（content_length=0）",
      _last_out is not None and _last_out.details.get("content_length") == 0,
      str(_last_out.details if _last_out else None))
check("★ 审计：带 empty_output=True 可区分标记",
      _last_out is not None and _last_out.details.get("empty_output") is True,
      str(_last_out.details if _last_out else None))
check("审计：preview 看得出「模型本轮没有产出任何内容」",
      _last_out is not None and "没有产出" in str(_last_out.details.get("preview") or ""),
      str(_last_out.details.get("preview") if _last_out else None))
check("正常回答不会被误标为兜底", getattr(_worker3, "_reply_is_fallback", None) is False,
      str(getattr(_worker3, "_reply_is_fallback", None)))

# ── V3.6.13：长回答被单次生成上限截断（负责人实测停在「（最好、平均」）──────
# 判据不是标点，而是模型返回的 done_reason=length：自动从断点续写一次，
# 两段拼成完整回答；续写仍到上限才如实标注。
class NoKBWorker(Worker):
    def _get_knowledge_base(self):
        return None


_llm_len = ScriptedLLM([
    {"content": "第一部分：先澄清定义与约束条件。", "done_reason": "length"},
    {"content": "第二部分：结论是计数排序在特定条件下可达到 O(n)。"},
])
_worker_len = NoKBWorker()
_worker_len.llm = _llm_len
_worker_len.tools_map = {}
_worker_len.user_input = "研究一下快速排序的复杂度"
_worker_len.messages_snapshot = []
_worker_len.think_mode = "深度思考"
_worker_len.privacy_shield = False
_worker_len.sandbox_path = str(SANDBOX_ROOT)
_worker_len.attachment_fulltext = {}
_worker_len._kb_instance = None
_events_len, _, _ = run_worker(_worker_len)
_fin_len = str(last_of(_events_len, "finished") or "")
check("★ done_reason=length 会自动续写（而不是把半句话当完整回答）",
      _llm_len.calls == 2, f"invoke={_llm_len.calls}")
check("★ 续写后的两段被拼成完整回答",
      "第一部分" in _fin_len and "第二部分" in _fin_len, _fin_len[:200])
check("续写成功时不再额外提示「被截断」", "单次生成长度上限" not in _fin_len, _fin_len[-120:])
check("续写拼接结果已落库", tail_is_assistant_text(_worker_len, _fin_len))

_llm_len2 = ScriptedLLM([
    {"content": "第一段：", "done_reason": "length"},
    {"content": "第二段仍没写完", "done_reason": "length"},
])
_worker_len2 = NoKBWorker()
_worker_len2.llm = _llm_len2
_worker_len2.tools_map = {}
_worker_len2.user_input = "研究一下快速排序的复杂度"
_worker_len2.messages_snapshot = []
_worker_len2.think_mode = "深度思考"
_worker_len2.privacy_shield = False
_worker_len2.sandbox_path = str(SANDBOX_ROOT)
_worker_len2.attachment_fulltext = {}
_worker_len2._kb_instance = None
_events_len2, _, _ = run_worker(_worker_len2)
_fin_len2 = str(last_of(_events_len2, "finished") or "")
check("★ 续写后仍到上限：如实标注并给出「继续」",
      "单次生成长度上限" in _fin_len2 and "继续" in _fin_len2, _fin_len2[-160:])
check("仍到上限时两段都不丢", "第一段" in _fin_len2 and "第二段" in _fin_len2, _fin_len2[:160])

# ── V3.6.13：通用知识题不该被拽去检索知识库 ──
check("知识库门控：算法/研究类问题不走知识库",
      not _worker_len._looks_like_kb_question("有没有时间复杂度 O(n) 的排序算法，请你研究"),
      "误判为知识库问题")
check("知识库门控：本机资料类问题照常走知识库",
      _worker_len._looks_like_kb_question("帮我查一下合同里的付款条款") and
      _worker_len._looks_like_kb_question("公司报销制度是怎么规定的"))
# P0-03：弱信号（我们公司/公司的/文件里）遇上通用知识指向时不得强制检索；
# 反向补齐"报销/流水/账单/台账"这类真实业务对象。
for _text in ("我们公司的产品适合用哪种架构？",
              "Python 里怎么读取文件里的内容",
              "公司的一般纳税人和小规模纳税人有什么区别",
              "Excel 怎么做报表",
              "什么是数据流水线"):
    check(f"知识库门控：通用题不误触发 · {_text[:16]}",
          not _worker_len._looks_like_kb_question(_text), "误判为知识库问题")
for _text in ("上个月报销了多少",
              "把上个月的流水整理一下",
              "台账里记了几笔账单"):
    check(f"知识库门控：业务对象照常检索 · {_text[:16]}",
          _worker_len._looks_like_kb_question(_text), "该查本机资料却没判为知识库问题")

print("\n" + "=" * 78)
print("【6】P0-04：续写轮返回工具调用时，已生成前缀不能丢")
print("=" * 78)
class NoKBWorker(Worker):
    def _get_knowledge_base(self):
        return None


_list_ok = FakeTool("list_local_files", lambda args: "目录里有 a.txt、b.txt")
_llm_prefix = ScriptedLLM([
    {"content": "前缀甲：第一章已经写完。", "done_reason": "length"},
    # 续写轮没写正文，而是要求调工具（旧实现在这里把前缀丢在 _limit_prefix 里）
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_p1")]},
    {"content": "后缀乙：第二章接着写完。"},
])
_w_prefix = NoKBWorker()
_w_prefix.llm = _llm_prefix
_w_prefix.tools_map = {"list_local_files": _list_ok}
_w_prefix.user_input = "研究一下快速排序的复杂度"
_w_prefix.messages_snapshot = []
_w_prefix.think_mode = "深度思考"
_w_prefix.think_depth = 2
_w_prefix.privacy_shield = False
_w_prefix.sandbox_path = str(SANDBOX_ROOT)
_w_prefix.attachment_fulltext = {}
_w_prefix._kb_instance = None
_events_prefix, _elapsed_prefix, _stuck_prefix = run_worker(_w_prefix)
_fin_prefix = str(last_of(_events_prefix, "finished") or "")
check("续写轮工具调用后仍继续推理", _llm_prefix.calls == 3, f"invoke={_llm_prefix.calls}")
check("★ 已生成前缀没有被丢弃",
      "前缀甲" in _fin_prefix and "后缀乙" in _fin_prefix, _fin_prefix[:200])
check("前缀 + 工具 + 后文都落库", tail_is_assistant_text(_w_prefix, _fin_prefix))

# 续写轮工具调用后模型再也不给正文：兜底文案出现时前缀也必须保留
_llm_prefix2 = ScriptedLLM([
    {"content": "前缀丙：这一段不能丢。", "done_reason": "length"},
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_p2")]},
    {"content": ""},
])
_w_prefix2 = NoKBWorker()
_w_prefix2.llm = _llm_prefix2
_w_prefix2.tools_map = {"list_local_files": _list_ok}
_w_prefix2.user_input = "研究一下快速排序的复杂度"
_w_prefix2.messages_snapshot = []
_w_prefix2.think_mode = "深度思考"
_w_prefix2.privacy_shield = False
_w_prefix2.sandbox_path = str(SANDBOX_ROOT)
_w_prefix2.attachment_fulltext = {}
_w_prefix2._kb_instance = None
_events_prefix2, _, _ = run_worker(_w_prefix2)
_fin_prefix2 = str(last_of(_events_prefix2, "finished") or "")
check("★ 兜底/空产出路径也不丢前缀", "前缀丙" in _fin_prefix2, _fin_prefix2[:200])

print("\n" + "=" * 78)
# ── V3.6.18：直答快路径也要处理 done_reason=length（报告 P0-04 同类）──
def _direct_worker(llm, question="介绍一下常见的排序算法"):
    w = NoKBWorker()
    w.llm = llm
    w.tools_map = {}
    w.user_input = question
    w.messages_snapshot = []
    w.think_mode = "深度思考"
    w.privacy_shield = False
    w.sandbox_path = str(SANDBOX_ROOT)
    w.attachment_fulltext = {}
    w._kb_instance = None
    return w

_llm_d1 = ScriptedLLM([
    {"content": "第一段：冒泡排序的复杂度是 O(n²)。", "done_reason": "length"},
    {"content": "第二段：快速排序平均 O(n log n)。"},
])
_w_d1 = _direct_worker(_llm_d1)
_ev_d1, _, _ = run_worker(_w_d1)
_fin_d1 = str(last_of(_ev_d1, "finished") or "")
check("★ 直答路径 done_reason=length 也会自动续写",
      _llm_d1.calls == 2 and "第一段" in _fin_d1 and "第二段" in _fin_d1,
      f"calls={_llm_d1.calls} reply={_fin_d1[:160]!r}")
check("直答续写成功时不提示「被截断」", "单次生成长度上限" not in _fin_d1, _fin_d1[-120:])

_llm_d2 = ScriptedLLM([
    {"content": "第一段：很长的铺垫。", "done_reason": "length"},
    {"content": "第二段：仍然没写完。", "done_reason": "length"},
    {"content": "精炼版：冒泡 O(n²)、快排平均 O(n log n)、归并稳定 O(n log n)。"},
])
_w_d2 = _direct_worker(_llm_d2)
_ev_d2, _, _ = run_worker(_w_d2)
_fin_d2 = str(last_of(_ev_d2, "finished") or "")
check("★ 续写仍到上限 → 请模型重写一份不超上限的精炼版（用户要求）",
      _llm_d2.calls == 3 and "精炼版" in _fin_d2 and "按单次生成上限压缩" in _fin_d2,
      f"calls={_llm_d2.calls} reply={_fin_d2[:180]!r}")

print("\n" + "=" * 78)
print("【7】P1-11：审批超时停止本轮；驳回后同轮不自动重试；检索目标按语义展示")
print("=" * 78)
_write_tool = FakeTool("create_local_file", lambda args: "✅ 已创建")
_llm_timeout = ScriptedLLM([
    {"content": "", "tool_calls": [_tool_call(
        "create_local_file",
        {"filename": "timeout_probe.txt", "content": "x", "target_directory": str(SANDBOX_ROOT)},
        "call_t1")]},
    {"content": "这一句说明超时后还在继续推理（不应该出现）"},
])
_w_timeout = make_worker(_llm_timeout, {"create_local_file": _write_tool},
                         "在当前沙箱目录创建 timeout_probe.txt",
                         think_mode="深度思考")
_approvals = []


def _fake_timeout(name, args, path, timeout=300.0, display_path="", tool_id=""):
    _approvals.append((name, path, display_path))
    _w_timeout._approval_outcome = "timeout"
    return False


_w_timeout._request_approval = _fake_timeout
_events_timeout, _elapsed_timeout, _stuck_timeout = run_worker(_w_timeout)
_fin_timeout = str(last_of(_events_timeout, "finished") or "")
check("★ 超时只询问一次（不会再次弹同一审批）", len(_approvals) == 1, str(_approvals))
check("★ 超时后不再进入下一轮模型推理", _llm_timeout.calls == 1, f"invoke={_llm_timeout.calls}")
check("★ 超时后工具没有执行", _write_tool.calls == [], str(_write_tool.calls))
check("★ 回答如实说明超时并已停止", "超时" in _fin_timeout and "已停止" in _fin_timeout,
      _fin_timeout[:200])

# 明确驳回：宿主立即停止整轮并给确定性答复（不再让模型说“我失败了”）
_llm_reject = ScriptedLLM([
    {"content": "", "tool_calls": [
        _tool_call("create_local_file",
                   {"filename": "reject_probe.txt", "content": "x",
                    "target_directory": str(SANDBOX_ROOT)}, "call_r1"),
        _tool_call("create_local_file",
                   {"filename": "reject_probe2.txt", "content": "x",
                    "target_directory": str(SANDBOX_ROOT)}, "call_r2"),
    ]},
])
_reject_tool = FakeTool("create_local_file", lambda args: "✅ 已创建")
_w_reject = make_worker(_llm_reject, {"create_local_file": _reject_tool},
                        "在当前沙箱目录创建 reject_probe.txt",
                        think_mode="深度思考")
_reject_approvals = []


def _fake_reject(name, args, path, timeout=300.0, display_path="", tool_id=""):
    _reject_approvals.append(name)
    _w_reject._approval_outcome = "rejected"
    return False


_w_reject._request_approval = _fake_reject
_events_reject, _, _ = run_worker(_w_reject)
_fin_reject = str(last_of(_events_reject, "finished") or "")
check("★ 驳回后同轮内不再弹第二次审批", len(_reject_approvals) == 1, str(_reject_approvals))
check("驳回后工具确实没有执行", _reject_tool.calls == [], str(_reject_tool.calls))
check("★ 驳回后给确定性答复（已按你的选择驳回 / 未执行 / 未改动）",
      "已按你的选择驳回" in _fin_reject and "未执行" in _fin_reject
      and "也没有改动" in _fin_reject, _fin_reject[:200])
check("★ 驳回后不再进入下一轮模型推理", _llm_reject.calls == 1, f"invoke={_llm_reject.calls}")
_reject_tool_ids = [m.get("tool_call_id") for m in (_w_reject.result_messages or [])
                    if m.get("role") == "tool"]
check("★ 同轮剩余 tool_calls 都有 ToolMessage（协议完整）",
      {"call_r1", "call_r2"} <= set(_reject_tool_ids), str(_reject_tool_ids))

# 知识库检索的审批/状态目标应显示检索词，而不是项目目录（P1-11 附带问题）
_dp = _worker_len._display_target_path(
    "search_knowledge_base", {"query": "上个月报销"}, str(SANDBOX_ROOT))
check("知识库检索的目标显示为检索词", "检索词" in _dp and "报销" in _dp, _dp)
_dp2 = _worker_len._display_target_path("create_local_file", {}, r"D:\x\a.txt")
check("写类工具仍显示真实路径", _dp2 == r"D:\x\a.txt", _dp2)
_dp3 = _worker_len._display_target_path(
    "create_local_file", {"filename": "周报.txt"}, str(SANDBOX_ROOT))
# ── V3.6.21：工具消息链净化（孤儿 tool / 缺失结果不再让上游 400）──
from yindun.worker.agent_worker import Worker as _W  # noqa: E402
_chain = _W._sanitize_tool_chain([
    HumanMessage(content="hi"),
    ToolMessage(content="孤儿工具结果", tool_call_id="nope"),
])
check("孤儿工具结果被转成普通上下文（不再触发 role=tool 400）",
      all(not isinstance(m, ToolMessage) for m in _chain) and "孤儿工具结果" in str(_chain[-1].content),
      str([type(m).__name__ for m in _chain]))
_chain2 = _W._sanitize_tool_chain([
    AIMessage(content="", tool_calls=[{"name": "list_local_files", "args": {}, "id": "c1"}]),
    HumanMessage(content="下一句"),
])
check("缺失的工具结果被补占位（序列完整）",
      any(isinstance(m, ToolMessage) and getattr(m, "tool_call_id", "") == "c1" for m in _chain2),
      str([(type(m).__name__, getattr(m, "tool_call_id", None)) for m in _chain2]))
check("★ 写类工具审批目标拼上文件名（完整路径，防盲签）",
      str(_dp3).endswith("周报.txt") and str(_dp3) != str(SANDBOX_ROOT), _dp3)

# ── V3.6.21：高思考档的"最低思考轮数"不能把已经答对的短答案带偏 ──
print("\n" + "=" * 78)
print("【8】高思考档：完整回答优先于最低轮数（不再强制深化）")
print("=" * 78)

_list_tool = FakeTool("list_local_files", lambda args: "📁 目录里有 3 个文件")
# 注意：深度思考下会先有一次"直答预判"调用（判断要不要用工具），随后才进 ReAct 循环；
# 因此脚本是：预判→要工具、ReAct 第 1 轮→再要工具、第 2 轮→完整短答案。
_llm_done = ScriptedLLM([
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_p0")]},
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_d1")]},
    {"content": "周六上午照常开诊。"},          # 短，但完整（句末标点）——第一轮就答对了
])
_w_done = make_worker(_llm_done, {"list_local_files": _list_tool},
                      "周六上午开不开诊？", think_mode="深度思考")
_w_done.think_depth = 10                        # 最低轮数 = max(2, 8) = 8
_ev_done, _, _ = run_worker(_w_done)
_fin_done = str(last_of(_ev_done, "finished") or "")
check("★ 完整短答案不再被强制深化（工具轮之后直接收尾）",
      _llm_done.calls == 3 and "照常开诊" in _fin_done,
      f"calls={_llm_done.calls} reply={_fin_done[:120]!r}")
check("状态里明确写了「跳过深化轮」",
      any("跳过深化轮" in str(p) for ev, p in _ev_done if ev == "status"),
      str([p for ev, p in _ev_done if ev == "status"])[:160])

# 反例：过渡语（没说完）仍应触发一次深化，避免"该深入时过早收尾"
_llm_thin = ScriptedLLM([
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_p1")]},
    {"content": "", "tool_calls": [_tool_call("list_local_files",
                                              {"target_directory": str(SANDBOX_ROOT)}, "call_t1")]},
    {"content": "我这就去查一下"},
    {"content": "目录里共有 3 个文件：a.txt、b.txt、c.txt。"},
])
_w_thin = make_worker(_llm_thin, {"list_local_files": _list_tool},
                      "目录里有多少文件？", think_mode="深度思考")
_w_thin.think_depth = 10
_ev_thin, _, _ = run_worker(_w_thin)
_fin_thin = str(last_of(_ev_thin, "finished") or "")
check("过渡语仍会触发一次深化（不会把没答完的当完成）",
      _llm_thin.calls >= 4 and "3 个文件" in _fin_thin,
      f"calls={_llm_thin.calls} reply={_fin_thin[:120]!r}")

print()
print("=" * 78)
print("【9】对话 / 工作：默认工作；对话不调工具；工作直答不吞工具调用")
print("=" * 78)
from yindun.app.settings_store import DEFAULTS as _DEFAULTS
check("★ 新装默认模式＝工作（深度思考）",
      _DEFAULTS.get("think_mode") == "深度思考", repr(_DEFAULTS.get("think_mode")))
check("Worker 默认模式＝工作", Worker().think_mode == "深度思考", repr(Worker().think_mode))

_llm_chat = ScriptedLLM([{"content": "这段不该被使用"}])
_w_chat = make_worker(_llm_chat, {}, DESKTOP_QUESTION, think_mode="快速回答")
_ev_chat, _, _ = run_worker(_w_chat)
_fin_chat = str(last_of(_ev_chat, "finished") or "")
check("★ 对话模式遇到需要工具的问题：不调用模型", _llm_chat.calls == 0,
      f"invoke={_llm_chat.calls}")
check("★ 对话模式如实引导切「工作」",
      "对话" in _fin_chat and "工作" in _fin_chat, _fin_chat[:120])

_llm_dt = ScriptedLLM([
    {"content": "我来列一下目录。",
     "tool_calls": [_tool_call("list_local_files",
                               {"target_directory": str(SANDBOX_ROOT)}, "c_dt")]},
    {"content": "目录里有两个文件。"},
])
_tool_dt = FakeTool("list_local_files", lambda args: "a.txt b.txt")
_w_dt = make_worker(_llm_dt, {"list_local_files": _tool_dt}, "介绍一下巴黎",
                    fast_path=True, think_mode="深度思考")
_ev_dt, _, _ = run_worker(_w_dt)
_fin_dt = str(last_of(_ev_dt, "finished") or "")
check("★ 工作模式直答带开场白 + 工具调用：工具真的执行",
      len(_tool_dt.calls) >= 1, f"tool={len(_tool_dt.calls)}")
check("★ 最终回答是工具后的结论，不是开场白",
      "目录里有两个文件" in _fin_dt and "我来列一下" not in _fin_dt,
      _fin_dt[:120])

_tool_read = FakeTool("read_local_file", lambda args: "✅ 内容：hello")
_w_read = make_worker(ScriptedLLM([{"content": "读到了 hello。"}]),
                      {"read_local_file": _tool_read},
                      "读取桌面上的 1.md 内容", think_mode="深度思考")
_ev_read, _, _ = run_worker(_w_read)
from yindun.core.file_tools import _resolve_target_dir as _rtd_t
_expected_dir = _rtd_t("桌面")
check("★ 按用户说的位置定位（桌面），不再一律用程序目录",
      bool(_tool_read.calls)
      and _tool_read.calls[0].get("target_directory") == _expected_dir,
      str(_tool_read.calls[:1]))
check("★ 传给工具的是纯文件名 1.md",
      bool(_tool_read.calls) and _tool_read.calls[0].get("filename") == "1.md",
      str(_tool_read.calls[:1]))

print()
print("=" * 78)
print("【10】承诺型开场白 / 工具结果已就绪但总结为空（V3.6.22 负责人实测）")
print("=" * 78)
# 现场（工作模式，连问「1.md内容是什么」）：
#   第 1 轮 —— 模型只回「我来读取一下桌面上的 1.md。」（content 非空、tool_calls 为空），
#              旧实现把这句承诺当最终回答展示，用户什么内容都没拿到；
#   第 2 轮 —— 强制读取已执行、模型总结返回空串，旧实现落到
#              「模型这一轮没有给出内容，可以再问一次或换个说法」；
#   第 3 轮 —— 才正常读取并回答。
# 下面三条把这两个入口钉住，并确认「对话」模式不被工具纠正打扰。

# ── 场景 A：第 1 次只给承诺（无工具） → 纠正一次 → 第 2 次给出正常答案 ──
# 问法刻意选「查看桌面的说明」（含工具信号词 → 不进直答快路径；又没有路径/扩展名
# → 不触发宿主的强制读取分支），这样测到的就是 ReAct 循环里"无 tool_calls"那一段。
_llm_promise = ScriptedLLM([
    {"content": "我来查看一下桌面上的说明文件。"},          # 承诺：content 非空、无 tool_calls
    {"content": "桌面上的说明文件写着：请勿外传。"},        # 被纠正后给出真正的内容
])
_w_promise = make_worker(_llm_promise,
                         {"read_local_file": FakeTool("read_local_file", lambda a: "✅ 说明"),
                          "list_local_files": FakeTool("list_local_files", lambda a: "说明.txt")},
                         "查看桌面的说明", think_mode="深度思考")
_ev_promise, _elapsed_promise, _stuck_promise = run_worker(_w_promise)
_fin_promise = str(last_of(_ev_promise, "finished") or "")
_status_promise = [str(p) for p in all_of(_ev_promise, "status")]
check("A1 有限时间内结束", not _stuck_promise, f"耗时 {_elapsed_promise:.1f}s 仍未结束")
check("A2 ★ 最终答复不是承诺句（承诺没有被当最终答案）",
      "请勿外传" in _fin_promise
      and not _fin_promise.strip().startswith("我来查看")
      and "我来查看一下桌面上的说明文件" not in _fin_promise,
      repr(_fin_promise[:120]))
check("A3 ★ 模型被纠正至少一次（多跑了一轮生成）",
      _llm_promise.calls >= 2, f"invoke={_llm_promise.calls}")
check("A4 ★ 状态里能看到「承诺纠正」提示",
      any("[纠正]" in s and "承诺" in s for s in _status_promise),
      str(_status_promise)[:220])
_sent_promise = "\n".join(str(getattr(m, "content", "")) for m in _llm_promise.seen[-1])
check("A5 纠正指令里点名了可用工具与参数（不是空喊「请重试」）",
      "read_local_file" in _sent_promise and "target_directory" in _sent_promise,
      _sent_promise[-200:])
check("A6 落库链尾＝用户看到的那段文字",
      tail_is_assistant_text(_w_promise, _fin_promise),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_w_promise.result_messages or [])]))

# ── 场景 B：强制工具结果已在手、模型总结为空 → 宿主直接呈现结果（不发「无内容」）──
_tool_read_b = FakeTool("read_local_file", lambda a: "✅ 内容：hello from 1.md")
_llm_empty_after_tool = ScriptedLLM([{"content": ""}])       # 无论问几次都返回空串
_w_empty_after_tool = make_worker(
    _llm_empty_after_tool, {"read_local_file": _tool_read_b},
    "读取桌面上的 1.md 内容", think_mode="深度思考")
_ev_b, _elapsed_b, _stuck_b = run_worker(_w_empty_after_tool)
_fin_b = str(last_of(_ev_b, "finished") or "")
check("B1 有限时间内结束", not _stuck_b, f"耗时 {_elapsed_b:.1f}s 仍未结束")
check("B2 ★ 强制读取确实执行了", bool(_tool_read_b.calls), str(_tool_read_b.calls[:1]))
check("B3 ★ 最终答复包含工具结果正文（内容早就读到了，不该再说没内容）",
      "hello from 1.md" in _fin_b, repr(_fin_b[:160]))
check("B4 ★ 不再出现「模型这一轮没有给出内容 / 没有给出回答」",
      "没有给出内容" not in _fin_b and "没有给出回答" not in _fin_b,
      repr(_fin_b[:160]))
check("B5 宿主答复说明了「已读取」并保留工具原文",
      "已读取文件" in _fin_b and "✅ 内容：hello from 1.md" in _fin_b, repr(_fin_b[:160]))
check("B6 落库链尾非空且与界面一致", tail_is_assistant_text(_w_empty_after_tool, _fin_b),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in (_w_empty_after_tool.result_messages or [])]))

# ── 场景 B2：工具结果本身是失败标记 → 如实说明失败原因 ──
_tool_read_fail = FakeTool("read_local_file", lambda a: "❌ 文件不存在：1.md")
_w_tool_fail = make_worker(ScriptedLLM([{"content": ""}]),
                           {"read_local_file": _tool_read_fail},
                           "读取桌面上的 1.md 内容", think_mode="深度思考")
_ev_bf, _, _ = run_worker(_w_tool_fail)
_fin_bf = str(last_of(_ev_bf, "finished") or "")
check("B7 ★ 失败结果如实呈现（说明没能完成 + 失败原因），不包装成成功",
      "没能完成" in _fin_bf and "文件不存在：1.md" in _fin_bf,
      repr(_fin_bf[:160]))

# ── 场景 C：「对话」模式不受影响（承诺句不触发工具纠正）──
_promise_chat = "我来读取一下相关资料。"
_llm_chat_promise = ScriptedLLM([{"content": _promise_chat}])
_w_chat_promise = make_worker(_llm_chat_promise, {},
                              "介绍一下巴黎", think_mode="快速回答")
_ev_c, _, _ = run_worker(_w_chat_promise)
_fin_c = str(last_of(_ev_c, "finished") or "")
_status_c = [str(p) for p in all_of(_ev_c, "status")]
check("C1 ★ 对话模式不触发工具纠正（没有「[纠正] … 承诺」状态）",
      not [s for s in _status_c if "[纠正]" in s and "承诺" in s], str(_status_c)[:200])
check("C2 对话模式行为保持原样（只调模型一次）",
      _llm_chat_promise.calls == 1, f"invoke={_llm_chat_promise.calls}")
check("C3 对话模式原样返回该句（没有被工具兜底改写）",
      _fin_c.strip() == _promise_chat, repr(_fin_c[:120]))

if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：不再有空气泡 / 工具调用不再被吞 / 无进展会收尾 / 失败有回复且被持久化 / 等待期间有状态事件")
sys.exit(0)


