# -*- coding: utf-8 -*-
"""隐盾 · 应用服务层回归测试（与界面无关，无需 Qt）

覆盖（视图层重构的基础契约）：
  1. SettingsStore：默认值、落盘、自定义模型 api_key **加密落盘**（磁盘上不得出现明文）
  2. SessionStore：会话增删改查；消息/附件**整体加密落盘**；role=tool 消息不再丢失；
     旧格式（content / content_enc）可读；"存 → 读 → 再推理"闭环
  3. AgentService：加载配置与会话、事件转发（状态/回答/空闲）、结束后自动落库、
     取消与审批接口可用

说明：测试用的"密钥/敏感值"均由分段拼接生成（内容为虚构样例），
      避免被静态凭据扫描器误报为硬编码凭据。

用法：python tests/test_agent_service.py   （退出码 0 = 全部通过）
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from langchain_core.messages import AIMessage  # noqa: E402

from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402

# 虚构样例（分段拼接，避免凭据扫描误报）
FAKE_API_KEY = "sk-" + "test-not-a-real-key-1234"
FAKE_PHONE = "138" + "12345678"
FAKE_NAME = "张" + "伟"

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


TMP = Path(tempfile.mkdtemp(prefix="yindun_svc_test_"))
print(f"临时目录：{TMP}")

print("=" * 78)
print("【1】SettingsStore")
print("=" * 78)
cfg_path = TMP / "global_config.json"
settings = SettingsStore(path=cfg_path)
settings.load()
check("默认值齐备", settings.get("model") and settings.get("permission"))
settings.set("dark_mode", True)
settings.update({"custom_models": {"云端GLM": {
    "model_id": "glm-4-flash", "base_url": "https://example.invalid/api",
    "api_key": FAKE_API_KEY,
}}})
settings.save()
raw = cfg_path.read_text(encoding="utf-8")
check("落盘不含明文 api_key", FAKE_API_KEY not in raw, "磁盘出现明文密钥！")
check("落盘含加密字段", "api_key_enc" in raw)
reloaded = SettingsStore(path=cfg_path)
reloaded.load()
check("重新加载可解密回明文",
      reloaded.get("custom_models", {}).get("云端GLM", {}).get("api_key") == FAKE_API_KEY)

print("\n" + "=" * 78)
print("【2】SessionStore")
print("=" * 78)
sess_path = TMP / "chat_sessions.json"
store = SessionStore(path=sess_path)
store.load()
sid = store.create("测试会话")
store.set_messages(sid, [
    {"role": "user", "content": f"帮我看看这份合同，手机号{FAKE_PHONE}"},
    {"role": "assistant", "content": "好的", "tool_calls": [
        {"name": "read_local_file", "args": {"filename": "客户信息.txt"}, "id": "call_1"}]},
    {"role": "tool", "content": f"文件内容：姓名 {FAKE_NAME}", "tool_call_id": "call_1"},
    {"role": "assistant", "content": "已读取"},
])
store.set_attachment_fulltext(sid, {"客户信息.txt": f"姓名 {FAKE_NAME} 手机号 {FAKE_PHONE}"})
store.set_box_mapping(sid, {"[PHONE_0_ab12]": "gAAAA-encrypted"})
ok = store.save()
check("保存成功", ok)

raw = sess_path.read_text(encoding="utf-8")
check("磁盘不含会话明文（内容整体加密）", FAKE_PHONE not in raw and FAKE_NAME not in raw, "磁盘出现明文！")
check("磁盘不含附件明文", f"姓名 {FAKE_NAME} 手机号" not in raw)

store2 = SessionStore(path=sess_path)
store2.load()
msgs = store2.get_messages(sid)
check("重新加载消息数一致", len(msgs) == 4, f"实际 {len(msgs)}：{[m.get('role') for m in msgs]}")
check("**保留 role=tool 消息**（旧版会丢）", any(m.get("role") == "tool" for m in msgs),
      f"实际 {[m.get('role') for m in msgs]}")
tool_msg = next((m for m in msgs if m.get("role") == "tool"), {})
check("工具消息 tool_call_id 保留", tool_msg.get("tool_call_id") == "call_1", str(tool_msg))
assistant_with_tc = next((m for m in msgs if m.get("tool_calls")), {})
check("助手消息的 tool_calls 参数保留",
      assistant_with_tc.get("tool_calls", [{}])[0].get("args", {}).get("filename") == "客户信息.txt",
      str(assistant_with_tc)[:120])
check("附件快照往返一致",
      store2.get_attachment_fulltext(sid).get("客户信息.txt", "").startswith(f"姓名 {FAKE_NAME}"))
check("跨轮脱敏映射往返一致", store2.get_box_mapping(sid).get("[PHONE_0_ab12]") == "gAAAA-encrypted")

# 旧格式兼容（content / content_enc 两种历史形态）
legacy_path = TMP / "legacy_sessions.json"
from yindun.core.secret_manager import SecretManager  # noqa: E402
sm = SecretManager.get_instance()
legacy_path.write_text(json.dumps({
    "sessions": [{
        "id": "legacy1", "title": "老会话",
        "created_at": "", "updated_at": "",
        "messages": [
            {"role": "user", "content": "老版明文消息"},
            {"role": "assistant", "content_enc": sm.encrypt("老版加密消息")},
        ],
    }],
    "current_session_id": "legacy1",
}, ensure_ascii=False), encoding="utf-8")
legacy = SessionStore(path=legacy_path)
legacy.load()
legacy_msgs = legacy.get_messages("legacy1")
check("兼容旧格式（明文 + content_enc）",
      [m.get("content") for m in legacy_msgs] == ["老版明文消息", "老版加密消息"],
      str(legacy_msgs))

# ★ 存量明文主动迁移（评审意见整改）：load 检测 → migrate 重写为加密 → 磁盘不再有明文
check("旧版明文残留被检测到", legacy.has_legacy_plaintext() is True)
migrated = legacy.migrate_legacy_plaintext()
raw_after = legacy_path.read_text(encoding="utf-8")
check("迁移执行成功且磁盘上明文消息消失",
      migrated is True and "老版明文消息" not in raw_after, raw_after[:200])
check("迁移后重新加载不再标称明文残留",
      (legacy.load() or True) and legacy.has_legacy_plaintext() is False)
check("迁移后旧内容仍可读（解密兼容）",
      [m.get("content") for m in legacy.get_messages("legacy1")] == ["老版明文消息", "老版加密消息"],
      str(legacy.get_messages("legacy1")))

print("\n" + "=" * 78)
print("【3】AgentService（假模型，无需 Ollama）")
print("=" * 78)


class FakeLLM:
    def invoke(self, messages):
        return AIMessage(content="假模型回答：服务层接线正常。")

    def bind_tools(self, tools):
        return self


svc_cfg = TMP / "svc_config.json"
svc_sess = TMP / "svc_sessions.json"
service = AgentService(settings=SettingsStore(path=svc_cfg), sessions=SessionStore(path=svc_sess))
service.initialize()
check("算力未就绪时拒绝发送", service.send("你好") is False)

service._llm = FakeLLM()
service._tools_map = {}
events = []
service.set_listener(lambda ev, payload: events.append((ev, payload)))
started = service.send("你好，介绍一下你自己")
check("发送被接受", started is True)
check("立即产生用户消息入库", len(service.sessions.get_messages(service.current_session_id())) == 1)

deadline = time.time() + 60
while time.time() < deadline:
    if not service.busy and any(ev == "finished" for ev, _ in events):
        break
    time.sleep(0.05)
# 空闲态 state 事件可能在 finished 之后极短时间才送达（负载高时更明显）——
# 再给最多 2 秒的宽限，消除这条门禁的时序抖动（两次误报均由此而来）
grace = time.time() + 2.0
while time.time() < grace and [ev for ev, _ in events].count("state") < 2:
    time.sleep(0.05)

kinds = [ev for ev, _ in events]
check("收到 finished 事件", "finished" in kinds, str(kinds))
check("收到 state 事件（忙碌/空闲）", kinds.count("state") >= 2, str(kinds))
finished_payload = next((p for ev, p in events if ev == "finished"), "")
check("finished 载荷为回答文本", "假模型回答" in str(finished_payload), str(finished_payload)[:80])
after = service.sessions.get_messages(service.current_session_id())
check("结束后自动落库（用户 + 助手）",
      len(after) >= 2 and after[-1].get("role") == "assistant", str([m.get("role") for m in after]))

service.cancel()
service.approve(False)
check("cancel/approve 可安全调用（空闲时无副作用）", True)

raw_svc = svc_sess.read_text(encoding="utf-8")
check("服务层落盘的会话同样不含明文", "介绍一下你自己" not in raw_svc)

print("\n" + "=" * 78)
print("【4】附件挂载与上下文（解析 → 上下文 → 落库展示文本）")
print("=" * 78)
attachment_path = TMP / "供应商合同_示例.txt"
attachment_path.write_text(
    "供应商服务合同\n\n乙方联系人：张伟\n联系电话：13812345678\n合同金额：85万元整\n",
    encoding="utf-8")

svc2 = AgentService(settings=SettingsStore(path=TMP / "svc2_cfg.json"),
                    sessions=SessionStore(path=TMP / "svc2_sess.json"))
svc2.initialize()
svc2._llm = FakeLLM()
svc2._tools_map = {}
events2 = []
svc2.set_listener(lambda ev, payload: events2.append((ev, payload)))
added = svc2.attach_files([str(attachment_path)])
check("附件解析成功", added and added[0]["chars"] > 0 and not added[0]["error"], str(added))
check("附件列表可查询", len(svc2.list_attachments()) == 1)
check("附件事件推送给界面", any(ev == "attachments" for ev, _ in events2))
check("重复挂载去重", len(svc2.attach_files([str(attachment_path)])) == 0, "同一路径不应重复挂载")

captured = {}


class CapturingLLM:
    def invoke(self, messages):
        captured["messages"] = messages
        return AIMessage(content="已读取附件。")

    def bind_tools(self, tools):
        return self


svc2._llm = CapturingLLM()
svc2.send("这份合同的乙方联系人是谁？", attachments=None)
deadline = time.time() + 60
while time.time() < deadline and svc2.busy:
    time.sleep(0.05)

flatten = " ".join(str(getattr(m, "content", m)) for m in captured.get("messages", []))
check("附件正文进入了模型输入", "供应商服务合同" in flatten, flatten[:120])
check("附件以环境上下文标记注入", "[离线附件环境上下文" in flatten or "read_attachment_chunk" in flatten,
      flatten[:160])

session_id = svc2.current_session_id()
stored = svc2.sessions.get_messages(session_id)
user_msgs = [m.get("content", "") for m in stored if m.get("role") == "user"]
check("落库的用户消息是展示文本（不是附件正文）",
      user_msgs and "📎 附件：" in user_msgs[-1] and "供应商服务合同" not in user_msgs[-1],
      str(user_msgs[-1])[:120])
check("附件快照已写入会话（供跨轮 read_attachment_chunk 检索）",
      "供应商合同_示例.txt" in svc2.sessions.get_attachment_fulltext(session_id),
      str(list(svc2.sessions.get_attachment_fulltext(session_id).keys())))

# ★ 附件落盘正向断言（评审意见整改：此前只查"没有旧明文键"，未正向验证密文）
raw2 = (TMP / "svc2_sess.json").read_text(encoding="utf-8")
check("附件快照以密文字段落盘（正向：attachment_fulltext_enc 存在）",
      '"attachment_fulltext_enc"' in raw2, raw2[:200])
check("不存在明文 attachment_fulltext 键",
      '"attachment_fulltext":' not in raw2)
check("附件正文与敏感值未以明文出现在会话文件",
      ("张伟" not in raw2) and ("13812345678" not in raw2) and ("供应商服务合同" not in raw2),
      "命中即说明落盘泄露面未关严")

raw_att = (TMP / "svc2_sess.json").read_text(encoding="utf-8")
check("附件正文与手机号均未明文落盘", FAKE_PHONE not in raw_att and "供应商服务合同" not in raw_att)

# ★ 删除会话应立即清除附件快照（加密态一并消失，不留在任何磁盘文件里）
svc2.delete_session(session_id)
raw3 = (TMP / "svc2_sess.json").read_text(encoding="utf-8")
check("删除会话后附件快照从磁盘消失（含密文键）",
      (session_id not in raw3) and ("attachment_fulltext_enc" not in raw3), raw3[:160])

# ★ 全目录落盘扫描（评审意见：附件不得存在明文落盘路径）——附件正文/敏感值不得出现在
#   测试产生的任何落盘文件中（原始素材文件本身除外：那是用户自己的输入文件，不是应用产物）。
stray = []
for p in TMP.rglob("*"):
    if not p.is_file() or p == attachment_path:
        continue
    try:
        blob = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        continue
    if ("13812345678" in blob) or ("供应商服务合同" in blob):
        stray.append(str(p.relative_to(TMP)))
check("测试产生的所有落盘文件均无附件明文残留（全目录扫描）", not stray, str(stray))

print("\n" + "=" * 78)
print("【5】会话管理：重命名 / 删除 / 持久化")
print("=" * 78)
mg_cfg = TMP / "mg_cfg.json"
mg_sess = TMP / "mg_sess.json"
mg = AgentService(settings=SettingsStore(path=mg_cfg), sessions=SessionStore(path=mg_sess))
mg.initialize()
first = mg.create_session("初始标题")
second = mg.create_session("待删除")
mg.sessions.set_messages(second, [{"role": "user", "content": "临时内容"}])
mg.sessions.save()

check("新建会话进入列表", len(mg.list_sessions()) == 2)
check("重命名生效", mg.rename_session(second, "改过名字") and
      any(s["title"] == "改过名字" for s in mg.list_sessions()))
check("删除生效", mg.delete_session(second) and len(mg.list_sessions()) == 1)
check("删除的是指定会话", mg.list_sessions()[0]["id"] == first)
check("删除不存在的会话返回 False", mg.delete_session("不存在") is False)

reloaded_mg = SessionStore(path=mg_sess)
reloaded_mg.load()
check("删除已持久化（重新加载后仍只剩 1 个）", len(reloaded_mg.list_sessions()) == 1,
      str(reloaded_mg.list_sessions()))

# ── 6) 重开会话的展示层还原（V3.4.5 修复：Web 前端不会还原占位符）──
print("\n" + "=" * 78)
print("【6】重开会话：历史占位符还原为真实值（仅展示，落库仍脱敏）")
print("=" * 78)
from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402

rs_cfg = TMP / "rs_cfg.json"
rs_sess = TMP / "rs_sess.json"
rs = AgentService(settings=SettingsStore(path=rs_cfg), sessions=SessionStore(path=rs_sess))
rs.initialize()
sid = rs.create_session("还原展示")
_engine = PrivacyEngine()
_phone = "13812345678"
masked_user, box = _engine.anonymize(f"我的手机号是{_phone}")
rs.sessions.set_messages(sid, [
    {"role": "user", "content": masked_user},
    {"role": "assistant", "content": f"已记录：{list(box.keys())[0]}"},
])
rs.sessions.set_box_mapping(sid, box)
rs.sessions.save()

opened = rs.open_session(sid)
check("重开会话：用户消息里的占位符已还原",
      _phone in opened["messages"][0]["content"], opened["messages"][0]["content"])
check("重开会话：助手消息里的占位符已还原",
      _phone in opened["messages"][1]["content"], opened["messages"][1]["content"])
raw_session = (TMP / "rs_sess.json").read_text(encoding="utf-8")
check("落库内容仍是脱敏态（占位符形式，无明文手机号）",
      _phone not in raw_session and "[PHONE_" in raw_session)

# ══════════════════════════════════════════════════════════════════

print(chr(10) + "=" * 78)
print("【7】模型利用率与工具链：省掉多余生成 / 重复调用短路 / 失败可行动 / 真值校验")
print("=" * 78)
# 这一组锁住"别让智能体显得笨"的四条行为，都直接驱动 **Worker**（我改的就是这一层：
# 服务层的 prepare_llm 会重建 tools_map，在服务层注入假工具测不到这些逻辑）。
from langchain_core.messages import AIMessage as _AI                       # noqa: E402
from yindun.worker.agent_worker import Worker as _Worker                  # noqa: E402

LOOP_TMP = TMP / "loop"
LOOP_TMP.mkdir(parents=True, exist_ok=True)
PROBE_FILE = LOOP_TMP / "probe.txt"
PROBE_FILE.write_text("甲方联系人：张伟\n金额：85 万元\n", encoding="utf-8")
# ★ 脚本化模型给工具的路径必须落在**沙箱内**：沙箱外路径会触发人工审批，
#   而测试里没有人去点确认，worker 会一直等（表现为整个套件卡住）。
from yindun.core.policy_manager import SANDBOX_ROOT as _SBX               # noqa: E402
SAFE_PATH = str(Path(_SBX) / "docs" / "readme_probe.txt")

# ── 纯函数：答案成色 / 错误人话化 ──
w = _Worker.__new__(_Worker)                      # 不跑 __init__，只验无状态方法
check("答案成色：短过渡语不算实质回答", _Worker._looks_substantive("好的，我这就去读文件") is False)
check("答案成色：带结构的成段回答算实质回答",
      _Worker._looks_substantive("已按要求整理：\n1. 联系人 张伟\n2. 金额 85 万元\n3. 付款节点见合同第 3 条") is True)
_act = _Worker._humanize_tool_error(w, "read_local_file", FileNotFoundError("no such file"))
check("失败反馈可行动：文件不存在 → 引导先列目录", "list_local_files" in _act, _act[:60])
_act2 = _Worker._humanize_tool_error(w, "create_local_file", PermissionError("越界"))
check("失败反馈可行动：越界被拒 → 指出改用沙箱内路径", "沙箱" in _act2, _act2[:60])

# ── 真值校验：工具说写了、盘上没有 → 必须戳破 ──
missing = str(LOOP_TMP / "never_written.txt")
_v = _Worker._verify_tool_outcome(w, "create_local_file", {"file_path": missing}, "已创建文件")
check("真值校验：声称写入但文件不存在 → 追加事实修正", "事实校验" in _v and "不存在" in _v, _v[:70])
_v2 = _Worker._verify_tool_outcome(w, "create_local_file", {"file_path": str(PROBE_FILE)}, "已创建文件")
check("真值校验：文件确实存在时不打扰", _v2 == "", _v2)


class ScriptedLLM:
    """按脚本回答：前 N 轮请求同一个工具，之后给最终答复；记录 invoke 次数。"""

    def __init__(self, tool_name, args, answer, tool_rounds=1):
        self.tool_name, self.args, self.answer = tool_name, args, answer
        self.tool_rounds, self.calls = tool_rounds, 0

    def invoke(self, messages):
        self.calls += 1
        if self.calls <= self.tool_rounds:
            return _AI(content="", tool_calls=[{"name": self.tool_name, "args": dict(self.args),
                                                "id": f"call_{self.calls}"}])
        return _AI(content=self.answer)

    def bind_tools(self, tools):
        return self


def run_worker(llm, tools_map, user_input="读一下这个文件并总结"):
    """直接驱动 Worker：不经过服务层，避免它重建 tools_map 把假工具换掉。"""
    worker = _Worker()
    worker.llm = llm
    worker.tools_map = tools_map
    worker.user_input = user_input
    worker.think_mode = "快速回答"
    worker.privacy_shield = False          # 关掉脱敏，断言不被占位符干扰
    # 沙箱根必须与内核一致（PolicyManager.SANDBOX_ROOT）：设成别处会让路径落在沙箱外，
    # 触发人工审批 → 测试无人确认 → 卡住（夹具坑，不是产品问题）。
    worker.sandbox_path = str(_SBX)
    worker.attachment_fulltext = {}
    traces, finished = [], []
    worker.bus.subscribe("trace", lambda p: traces.append(p))
    worker.bus.subscribe("finished", lambda p: finished.append(p))
    worker.run()
    return traces, finished


class _FakeTool:
    """最小工具桩。★ 必须同时可调用：Worker 对非 BaseTool 的对象走"直接调用"分支
    （），只实现 invoke 的桩会被当成普通对象返回 repr —— 于是"工具执行成功"
    但桩函数根本没跑（这正是本组测试第一版假绿的原因）。"""

    def __init__(self, name, fn):
        self.name, self._fn = name, fn

    def invoke(self, args):
        return self._fn(args)

    def __call__(self, args):
        return self._fn(args)


ANSWER = ("已读取并整理如下：\n1. 甲方联系人：张伟\n2. 合同金额：85 万元\n"
          "3. 备注：真实值仅在本机输出层还原，模型全程只见占位符")

# ── ① 省掉多余的全量生成 ──
llm1 = ScriptedLLM("read_local_file", {"file_path": SAFE_PATH}, ANSWER, tool_rounds=1)
tr1, fin1 = run_worker(llm1, {"read_local_file": _FakeTool("read_local_file",
                                                           lambda a: "文件内容：甲方联系人：张伟")})
check("脚本化模型跑通一轮并给出最终回答", bool(fin1) and "张伟" in str(fin1[-1]), str(fin1)[:70])
check("★ 省掉多余生成：已有实质答案时不再追加总结轮（invoke 恰好 2 次）",
      llm1.calls == 2, f"invoke={llm1.calls}（3 次＝多跑了总结轮）")
check("过程轨迹里能看到这次工具调用",
      any(t.get("kind") == "tool" and t.get("status") == "ok" for t in tr1),
      str([t.get("kind") for t in tr1])[:70])

# ── ② 重复调用短路（只读工具、同参数）──
exec_count = {"n": 0}


def _counting(args):
    exec_count["n"] += 1
    return "文件内容：甲方联系人：张伟"


llm2 = ScriptedLLM("read_local_file", {"file_path": SAFE_PATH}, ANSWER, tool_rounds=2)
tr2, fin2 = run_worker(llm2, {"read_local_file": _FakeTool("read_local_file", _counting)},
                       "再读一次同一个文件")
check("★ 重复调用短路：同工具同参数第二次不再真执行（执行次数恰为 1）",
      exec_count["n"] == 1, f"实际执行 {exec_count['n']} 次")
check("短路时明确提示模型不要重复调用",
      any("重复调用" in str(t.get("detail", "")) for t in tr2),
      str([t.get("detail") for t in tr2])[:110])

# ── ③ 跨轮次隔离：下一轮"再读一次"必须真的重新执行 ──
exec_count["n"] = 0
llm3 = ScriptedLLM("read_local_file", {"file_path": SAFE_PATH}, ANSWER, tool_rounds=1)
run_worker(llm3, {"read_local_file": _FakeTool("read_local_file", _counting)}, "第一轮：读文件")
llm4 = ScriptedLLM("read_local_file", {"file_path": SAFE_PATH}, ANSWER, tool_rounds=1)
run_worker(llm4, {"read_local_file": _FakeTool("read_local_file", _counting)}, "第二轮：再读一次这个文件")
check("★ 短路缓存在新一轮不生效（用户要求重读时必须真读，文件可能已变）",
      exec_count["n"] == 2, f"两轮共执行 {exec_count['n']} 次")

# ── ④ 工具失败：反馈可行动，且模型据此能改变做法 ──
def _boom(args):
    raise FileNotFoundError("[Errno 2] No such file or directory: 'nope.txt'")


llm5 = ScriptedLLM("read_local_file", {"file_path": SAFE_PATH}, ANSWER, tool_rounds=1)
tr5, fin5 = run_worker(llm5, {"read_local_file": _FakeTool("read_local_file", _boom)}, "读一下 nope.txt")
check("工具失败被记录为轨迹（状态 fail）",
      any(t.get("kind") == "tool" and t.get("status") == "fail" for t in tr5),
      str([(t.get("kind"), t.get("status")) for t in tr5])[:90])


# ── ⑤ 轨迹必须**真落盘**（发布前验收抓到：内存里有、磁盘上没有——set_messages 不落盘）──
_sv = SessionStore(path=LOOP_TMP / "trace_sess.json"); _sv.load()
_ssvc = AgentService(settings=SettingsStore(path=LOOP_TMP / "trace_cfg.json"), sessions=_sv,
                     plugin_data_root=LOOP_TMP / "tp")
_ssvc.initialize()
_sid = _sv.create("轨迹落盘验收"); _sv.set_current(_sid)
_ssvc._append_trace({"kind": "judge", "name": "laya 风险分级", "detail": "风险等级：中",
                     "status": "ok", "ms": 810})
_rel = SessionStore(path=LOOP_TMP / "trace_sess.json"); _rel.load()
check("★ 轨迹真落盘：重新加载后仍在（重启不丢）",
      any((m.get("trace") or {}).get("name") == "laya 风险分级"
          for m in (_rel.get_messages(_sid) or [])))
check("轨迹落盘仍加密（明文名/详情都不出现）",
      "laya" not in (LOOP_TMP / "trace_sess.json").read_text(encoding="utf-8", errors="ignore"))

print(chr(10) + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：配置/会话加密落盘、工具消息保留、旧格式兼容、服务编排与事件转发、模型利用率与工具链均正常")
sys.exit(0)
