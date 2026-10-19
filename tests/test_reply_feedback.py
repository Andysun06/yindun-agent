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

from langchain_core.messages import AIMessage  # noqa: E402

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


def _ai(content="", tool_calls=None):
    """真 AIMessage（不是自造桩）：`SummarizableChatHistory.to_dict_list` 只认 langchain 的
    消息类型，自造桩会被静默跳过 —— 那样测到的"落库链"就不是线上那条链了。"""
    return AIMessage(content=content, tool_calls=list(tool_calls or []))


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
        return _ai(step.get("content", ""), step.get("tool_calls"))

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


def make_worker(llm, tools_map, user_input, fast_path=False, think_mode="快速回答"):
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
print("【0】事故复现条件：真实分类器把「我的桌面有几张图片？」判为「不需要工具」")
print("=" * 78)
_real = Worker()
_simple = _real._is_simple_question(DESKTOP_QUESTION)
print(f"  [ℹ️] _is_simple_question({DESKTOP_QUESTION!r}) = {_simple}"
      f"（True ＝ 会走直答快路径，这正是事故的入口）")
check("事故问题仍被判为简单问答（若这里失败，说明分类器已改，事故前提随之改变）",
      _simple is True, str(_simple))

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
_worker1 = make_worker(_llm, {"list_local_files": _list_tool}, DESKTOP_QUESTION, fast_path=True)
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
_worker2 = make_worker(ScriptedLLM([{"content": ""}]), {}, DESKTOP_QUESTION, fast_path=True)
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
print("【6】失败路径：错误必须变成用户可见的回复文本并落库（不再删掉用户提问）")
print("=" * 78)
_worker6 = make_worker(RaisingLLM(RuntimeError("Connection refused: 无法连接模型服务")),
                       {}, DESKTOP_QUESTION, fast_path=True)
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
_svc.settings.set("think_mode", "快速回答")

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
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：不再有空气泡 / 工具调用不再被吞 / 无进展会收尾 / 失败有回复且被持久化 / 等待期间有状态事件")
sys.exit(0)
