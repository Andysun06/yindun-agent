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
from yindun.app import agent_service  # noqa: E402  # 打桩探测出口用（见 ①.68）
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
check("默认权限档为「变更前确认」", settings.get("permission") == "ask", str(settings.get("permission")))
# ★ V3.6.8：默认模型**不再写死名字**（旧默认 "qwen3.5:4b" 在没装它的机器上会变成
#   "对着不存在的模型宣称已就绪"）。空串 = 未选择，首启由服务层按本机检测结果自动选。
check("默认模型是空串（未选择，不写死模型名）", settings.get("model") == "", repr(settings.get("model")))
check("默认可读写范围为空串（= 未配置，按环境变量 → 程序目录回退）",
      settings.get("sandbox_dir") == "", repr(settings.get("sandbox_dir")))
check("从未写过的键 was_saved=False（用于区分「用户显式选过」）",
      settings.was_saved("model") is False)
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
check("失败反馈可行动：越界被拒 → 指出改用可读写范围内的路径",
      ("可读写范围" in _act2) or ("沙箱" in _act2), _act2[:60])

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

# ── ①.5 模型返回空内容时：落库的消息链必须以"用户看到的那段文字"结尾 ──
#   真缺陷（V3.5.7，真机 E2E 逮到）：本机 4B 模型偶发返回空内容（实测 5 次里 1 次），
#   此时走"重新生成 → 抱歉提示"兜底，但提示只经 finished 事件送到界面，
#   而落库用的是消息链——链尾停在那条空助手消息上。表现：界面上有答复、重启后那一轮是空白。
class _EmptyLLM:
    """所有轮次都返回空内容：模拟实测到的"模型什么都没吐"（含 _direct_answer 的重试）。"""

    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return _AI(content="")

    def bind_tools(self, tools):
        return self


worker_empty = _Worker()
worker_empty.llm = _EmptyLLM()
worker_empty.tools_map = {}
worker_empty.user_input = "用一句话介绍你自己"
worker_empty.think_mode = "快速回答"
worker_empty.privacy_shield = False
worker_empty.sandbox_path = str(_SBX)
worker_empty.attachment_fulltext = {}
_empty_finished = []
worker_empty.bus.subscribe("finished", lambda p: _empty_finished.append(p))
worker_empty.run()                                  # 回答经 finished 事件送出（run() 本身不返回文本）
_empty_reply = str(_empty_finished[-1]) if _empty_finished else ""
_persisted = worker_empty.result_messages or []
check("模型全程空内容时仍给出可用回答（不返回空白，回答经 finished 事件送出）",
      bool(_empty_reply.strip()), repr(_empty_reply[:60]))
check("★ 落库的消息链不以空助手消息结尾（重启后不会看到空白那一轮）",
      bool(_persisted) and _persisted[-1].get("role") == "assistant"
      and bool(str(_persisted[-1].get("content") or "").strip()),
      str([(m.get("role"), len(str(m.get("content") or ""))) for m in _persisted[-3:]]))

# ── ①.6 思考开关跟着「快速 / 深度思考」走（V3.5.9，实测 42~92s → 0.7~3.1s）──
from yindun.app.llm_factory import reasoning_for_mode  # noqa: E402
check("快速回答 → 明确关掉模型思考（对思考型与非思考型模型都安全）",
      reasoning_for_mode("快速回答") is False, str(reasoning_for_mode("快速回答")))
check("深度思考 → 不传该参数（保持模型默认：思考型会推理，非思考型不受影响）",
      reasoning_for_mode("深度思考") is None, str(reasoning_for_mode("深度思考")))
check("未知/空模式 → 按「快速」处理（宁可快，也不要让用户白等）",
      reasoning_for_mode("") is False and reasoning_for_mode(None) is False)

# ── ①.65 算力不可用时必须说清"为什么"（V3.6.2，路线图 A-1 演示预案的第一号风险）──
#   旧实现只回 ready=false / error=null：界面上就"未就绪"三个字，用户不知道该检查什么。
_diag = service._diagnose_llm_unavailable()
check("算力未就绪时给出可照做的说明（而不是空白）", bool(str(_diag).strip()), _diag)
check("说明里指出该去检查算力端点或模型",
      any(word in _diag for word in ("Ollama", "算力", "模型")), _diag)

_dead = AgentService(settings=SettingsStore(path=TMP / "dead_cfg.json"),
                     sessions=SessionStore(path=TMP / "dead_sess.json"))
_dead.settings.set("model", "qwen3.5:4b")
_dead.settings.set("ollama_host", "http://127.0.0.1:59999")     # 该端口没人监听
_dead.settings.save()
_dead.initialize()
_dead_status = _dead.llm_status()
check("端点连不上时 llm_status 带出原因（不是 null）",
      _dead_status["ready"] is False and bool(_dead_status.get("error")), str(_dead_status)[:200])
check("原因里给出可执行动作（启动 Ollama / 换模型 / 重新检测）",
      any(word in str(_dead_status.get("error"))
          for word in ("ollama serve", "Ollama 已启动", "换成本机已装", "重新检测")),
      str(_dead_status.get("error"))[:160])

# ── ①.66 不可用说明必须**短**（V3.6.13，负责人截图"回复太臃肿"）──
#   旧文案把原因、连接错误、手动添加、serve、重新检测、换模型、pull 全堆在一条气泡里，
#   四五行字没有重点。这里钉死：一条原因 + 一条下一步，且不重复同一句。
import yindun.app.agent_service as _svc_mod                              # noqa: E402
_real_detect = _svc_mod.detect_chat_models
_svc_mod.detect_chat_models = lambda: []
_dead2 = AgentService(settings=SettingsStore(path=TMP / "dead2_cfg.json"),
                      sessions=SessionStore(path=TMP / "dead2_sess.json"))
_dead2.settings.set("model", "qwen3.5:2b")
_dead2.settings.set("ollama_host", "http://127.0.0.1:59999")
_dead2.settings.save()
_short = _dead2._diagnose_llm_unavailable(
    catalog={"local_note": "没有检测到可对话的本机模型。连不上本机 Ollama 服务（http://127.0.0.1:59999）：连接等待超时"})
_svc_mod.detect_chat_models = _real_detect
check("★ 模型不可用说明压成一行（≤ 60 字）且不再堆四五个动作",
      len(_short) <= 60 and _short.count("。") <= 2, f"{len(_short)}：{_short}")
check("★ 说明按真实原因只给一条下一步（这里是启动 Ollama + 重新检测）",
      "连不上" in _short and "重新检测" in _short and "ollama pull" not in _short, _short)
check("说明不再重复「没有检测到可对话的模型」两次",
      _short.count("没有检测到可对话") == 0, _short)


# ── ①.67 公网模型接入：地址归一化 + 多候选探测（V3.6.4，负责人接 GLM 一直失败的根因）──
#   实测：用户把**完整端点**（https://open.bigmodel.cn/api/paas/v4/chat/completions）
#   填进"接口地址"，探测拼 /models 变 404、保存后 chat 又拼成 …/chat/completions/chat/completions。
from yindun.app.llm_factory import normalize_base_url as _norm, probe_candidates as _cands  # noqa: E402

_bad = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
check("完整端点被还原成基础地址",
      _norm(_bad) == "https://open.bigmodel.cn/api/paas/v4", _norm(_bad))
check("带尾斜杠的完整端点同样还原",
      _norm(_bad + "/") == "https://open.bigmodel.cn/api/paas/v4", _norm(_bad + "/"))
check("标准 /v1 基础地址原样保留（不猜测、不乱补）",
      _norm("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1")
check("Ollama 默认地址原样保留", _norm("http://127.0.0.1:11434") == "http://127.0.0.1:11434")
check("误填 /models 也能还原", _norm("https://open.bigmodel.cn/api/paas/v4/models")
      == "https://open.bigmodel.cn/api/paas/v4")
_c = _cands(_bad)
check("探测候选含 /models，并在最后带真实 chat 兜底（只提供 chat 的服务也能判连通）",
      _c and _c[0][0].endswith("/models") and _c[-1][0].endswith("/chat/completions")
      and _c[-1][1] == "chat", str(_c))
check("候选都建立在归一化后的基础地址上（不会再出现 …/chat/completions/models）",
      all("/chat/completions/" not in u for u, _ in _c), str(_c))

# 保存时归一化：把完整端点存进来，落盘的必须是基础地址（否则 OpenAI 客户端会再拼一次）
_glm_name = "glm-归一化探针"
svc_settings = service.settings
_saved = service.add_custom_model(_glm_name, "glm-4-flash", _bad, "sk-test")
_models_now = dict(svc_settings.get("custom_models") or {})
check("保存时把完整端点归一化后再落盘",
      _saved.get("ok") and _models_now.get(_glm_name, {}).get("base_url")
      == "https://open.bigmodel.cn/api/paas/v4",
      str(_models_now.get(_glm_name, {}).get("base_url")))
service.remove_custom_model(_glm_name)
check("测试配置已清理", _glm_name not in (svc_settings.get("custom_models") or {}))

# ── ①.68 密钥「留空＝不改动」（V3.6.7 修真实缺陷）──────────────────────────
# 用户实测：公网模型点「保存」后密钥就消失了，接着「测试连接」报 401「密钥不被接受」。
# 根因两条：① 后端保存时**无条件用空串覆盖** api_key（界面却写着"留空则不改动"）；
#          ② 探测把输入框的空值直接当密钥发出去，不看该条目已存的密钥。
print("\n" + "=" * 78)
print("【①.68】密钥留空语义：保存不清空、探测回落已存密钥")
print("=" * 78)
_key_name = "wan-密钥留存探针"
service.add_custom_model(_key_name, "glm-4-flash", "https://example.invalid/api", FAKE_API_KEY, "wan")
_saved_again = service.add_custom_model(_key_name, "glm-4-flash", "https://example.invalid/api", "", "wan")
_now = dict(svc_settings.get("custom_models") or {}).get(_key_name, {})
check("留空再保存 → 内存里密钥仍在（不是被空串覆盖）",
      _now.get("api_key") == FAKE_API_KEY, repr(_now.get("api_key"))[:12])
check("返回值如实说明「沿用了已存密钥」", _saved_again.get("kept_key") is True,
      str(_saved_again.get("kept_key")))
_reloaded = SettingsStore(path=svc_cfg)      # 服务写的是 svc_config.json（不是工具用的 global_config.json）
_reloaded.load()
check("重新加载后仍是同一把密钥（落盘加密链没被打断）",
      _reloaded.get("custom_models", {}).get(_key_name, {}).get("api_key") == FAKE_API_KEY,
      repr(_reloaded.get("custom_models", {}).get(_key_name, {}).get("api_key"))[:12])
_public = [m for m in service.custom_models() if m.get("name") == _key_name][0]
check("列给界面的信息只说「有没有密钥」，不含明文",
      _public.get("has_key") is True and FAKE_API_KEY not in json.dumps(_public, ensure_ascii=False))

_name_new = "wan-新建留空探针"
service.add_custom_model(_name_new, "glm-4-flash", "https://example.invalid/api", "", "wan")
check("新建时留空就是空（不会凭空造一把密钥）",
      not (svc_settings.get("custom_models") or {}).get(_name_new, {}).get("api_key"))
service.add_custom_model(_key_name, "glm-4-flash", "https://example.invalid/api", "sk-rotated", "wan")
check("显式填入新密钥 → 正常覆盖（换密钥这条路没被修坏）",
      (svc_settings.get("custom_models") or {}).get(_key_name, {}).get("api_key") == "sk-rotated")
service.add_custom_model(_key_name, "glm-4-flash", "https://example.invalid/api", FAKE_API_KEY, "wan")

# 探测：留空时回落到已存密钥。拦掉真实网络调用，只断言"发出去的到底是哪一把密钥"
_seen_keys = []
_real_probe = agent_service.probe_endpoint
agent_service.probe_endpoint = lambda url, key="": (
    _seen_keys.append(key) or {"ok": True, "models": [], "count": 0})
try:
    service.probe_model_endpoint("https://example.invalid/api", "", _key_name)
    service.probe_model_endpoint("https://example.invalid/api", "sk-explicit", _key_name)
    service.probe_model_endpoint("https://example.invalid/api", "", "不存在的条目")
    service.probe_model_endpoint("https://example.invalid/api", "")
finally:
    agent_service.probe_endpoint = _real_probe
check("点「测试连接」但输入框留空 → 用该条目已存的密钥（不再拿空密钥撞 401）",
      _seen_keys[:1] == [FAKE_API_KEY], str([k[:10] for k in _seen_keys]))
check("输入框里填了密钥 → 就用填的（不被已存值覆盖）",
      _seen_keys[1:2] == ["sk-explicit"], str([k[:10] for k in _seen_keys]))
check("条目名对不上 / 没给名字 → 空密钥（不越界去取别的条目的密钥）",
      _seen_keys[2:] == ["", ""], str([k[:10] for k in _seen_keys]))
service.remove_custom_model(_key_name)
service.remove_custom_model(_name_new)
check("①.68 测试配置已清理",
      not any(n in (svc_settings.get("custom_models") or {}) for n in (_key_name, _name_new)))

# 桥接契约（V3.6.7）：界面「测试连接」现在传三个参数（地址 / 密钥 / 条目名）。
# pywebview 是按位置转发到 JsApi 的——少一个形参不会"少传"，而是当场 TypeError，
# 于是"测试连接"整个不可用。沙盘 mock 接受任意参数，所以这条只能在这里钉。
from yindun.app.webview_app import JsApi  # noqa: E402
import inspect as _inspect  # noqa: E402
_bridge_params = list(_inspect.signature(JsApi.probe_model_endpoint).parameters)
check("桥的 probe_model_endpoint 接受条目名（否则前端传第三个参数会直接抛错）",
      _bridge_params[:4] == ["self", "base_url", "api_key", "name"], str(_bridge_params))
_forwarded = []
_real_svc_probe = AgentService.probe_model_endpoint
AgentService.probe_model_endpoint = lambda self, url, key="", name="": (
    _forwarded.append((url, key, name)) or {"ok": True})
try:
    _api_inst = JsApi(service, {})
    _api_inst.probe_model_endpoint("https://example.invalid/api", "", "某条目")
finally:
    AgentService.probe_model_endpoint = _real_svc_probe
check("桥把三个参数原样转发给服务层（不是吞掉第三个）",
      _forwarded == [("https://example.invalid/api", "", "某条目")], str(_forwarded))

# 拖动区域契约（V3.6.4）：easy_drag 是"全局无筛选"，已在窗口层关掉；拖动只认标记区域
_webview_src = Path(__file__).resolve().parents[1].joinpath(
    "yindun", "app", "webview_app.py").read_text(encoding="utf-8")
_index_src = Path(__file__).resolve().parents[1].joinpath(
    "yindun", "app", "web", "index.html").read_text(encoding="utf-8")
check("窗口不再用全局 easy_drag（它连输入框里的拖动都会拖走整个窗口）",
      '"easy_drag": False' in _webview_src and '"easy_drag": True' not in _webview_src)
check("顶栏整条是拖动区域（V3.6.21 恢复：拖顶部即可移动窗口）",
      'class="band topbar pywebview-drag-region"' in _index_src)
check("闪发浮条整条是拖动区域（V3.6.21 恢复）",
      'class="minibar pywebview-drag-region"' in _index_src)
_app_src = Path(__file__).resolve().parents[1].joinpath(
    "yindun", "app", "web", "app.js").read_text(encoding="utf-8")
check("拖动区内的按钮/输入框被拦下（mousedown 不冒泡到 body，点击不会被当成拖窗口）",
      't.closest("button, a, input, textarea, select, label, [data-no-drag]")' in _app_src
      and "ev.stopPropagation()" in _app_src)
check("设置页只让标题块拖动，操作按钮不参与拖动",
      'class="setmain__head"' in _index_src
      and 'class="setmain__head pywebview-drag-region"' not in _index_src
      and '<div class="pywebview-drag-region">' in _index_src)
check("输入区没有被标成拖动区域（在输入框里拖动就是选文字）",
      'composer pywebview-drag-region' not in _index_src
      and 'id="input" class="pywebview-drag-region"' not in _index_src)


# ── ①.66 "别让 agent 成为短板"：提示词分层 + 反编造兜底（V3.6.3，真机实测驱动）──
#   实测背景（本机 qwen3.5:4b，A/B 对照）：把大段"附件规范"常驻在提示词里时，
#   一句"读一下沙箱里的 x.txt"它**一次工具都不调**，还回出"我会严格按照规范使用 function
#   call 格式"这种自说自话；精简基线后立刻正常调用。另外工具失败时它会**编造文件内容**
#   （问"读不存在的文件"，它交出一份上一轮文件的汇总报告）。
from yindun.worker import agent_worker as _aw  # noqa: E402

_base_prompt = _aw._SYSTEM_PROMPT
_attach_rules = _aw._ATTACHMENT_RULES
check("常驻提示词足够短（≤2000 字符，小模型的注意力有限）", len(_base_prompt) <= 2000,
      f"{len(_base_prompt)} 字符")
check("三条硬规则写在最前面（用历史 / 先调工具 / 失败如实说）",
      all(k in _base_prompt for k in ("对话历史里已经有的信息，直接用", "必须先调用工具",
                                      "严禁编造文件内容")), _base_prompt[:120])
check("附件规范已从常驻提示词移出（只在挂附件时追加）",
      "题号定位" not in _base_prompt and "长文档分块" not in _base_prompt
      and "题号定位" in _attach_rules)
check("附件规范里不再留『请重新挂载』这类可被套用的推脱话术",
      "请重新挂载《文件名》后再提问" not in _attach_rules
      and "先用 read_attachment_chunk 再取一次" in _attach_rules)

_guard_worker = _aw.Worker()
_dir_ok = _guard_worker._with_failure_directive("❌ 读取失败：文件不存在")
check("工具失败结果会追加『禁止编造』指令", "禁止编造文件内容" in _dir_ok)
check("失败指令幂等（重复追加不叠加）",
      _guard_worker._with_failure_directive(_dir_ok) == _dir_ok)
check("成功结果不加失败指令", "禁止编造" not in _guard_worker._with_failure_directive("✅ 读取成功：内容…"))

# 中文表格曾经因为"长度阈值按英文设"而整类漏判（>=8 字符），这里钉住
_cjk_table = "姓名        公司             电话\n张三        北京科技集团     138****5678\n李四        上海贸易中心     139****0123"
check("中文表格被识别为『内容块』（长度阈值按字符算）",
      _guard_worker._looks_like_content_dump(_cjk_table) is True)
check("普通叙述不算内容块",
      _guard_worker._looks_like_content_dump("这个文件里有三条记录。") is False)
_guard_worker.user_input = "读一下 客户名单.txt 里写了什么"
_fired = _guard_worker._guard_ungrounded_dump(_cjk_table, tool_success=False,
                                             tool_names=["read_local_file"])
check("反编造兜底：没有成功工具结果 + 回答在罗列内容 → 追加『未经核实』",
      "未经核实" in _fired)
check("反编造兜底不误伤：工具有成功结果时不加免责",
      "未经核实" not in _guard_worker._guard_ungrounded_dump(_cjk_table, True, ["read_local_file"]))
_guard_worker.user_input = "你好，随便聊聊"
check("反编造兜底不误伤：普通闲聊不加免责",
      "未经核实" not in _guard_worker._guard_ungrounded_dump(_cjk_table, False, []))

_worker_src = Path(__file__).resolve().parents[1].joinpath(
    "yindun", "worker", "agent_worker.py").read_text(encoding="utf-8")
check("宿主有『读文件』强制兜底（模型不调工具时由宿主真读，不让用户拿不到数据）",
      "_read_verb" in _worker_src and '"read_local_file" in self.tools_map' in _worker_src)
check("强制结果也会带失败指令（否则模型会拿上一轮内容顶包）",
      "_forced_failed" in _worker_src and "_forced_content" in _worker_src)


# ── ①.7 小模型"把工具调用写成文字"的两种形态：识别 + 一次重试 ──
class _ToolMapWorker(_Worker):
    def __init__(self):
        self.tools_map = {"search_knowledge_base": 1, "read_local_file": 2}


_w = _ToolMapWorker()
check("识别形态①伪调用行（实测 qwen3.5:4b 有知识库提示时会这么写）",
      _w._looks_like_text_tool_call('【知识库检索】search_knowledge_base(query="x", top_k=5)') is True)
check("识别形态②JSON 代码块里的 tool_calls",
      _w._looks_like_text_tool_call(
          "```json" + chr(10) + '{"tool_calls": [{"name": "read_local_file"}]}' + chr(10) + "```") is True)
check("正常回答不误判",
      _w._looks_like_text_tool_call("哈希链审计是…" + chr(10) + "1. 防篡改" + chr(10) + "2. 可追溯") is False)
check("长解释里举例不误判（长度上限挡掉）",
      _w._looks_like_text_tool_call("例如可以调用 read_local_file(a.txt) 读取。" * 30) is False)
check("只提工具名不误判（必须有调用括号）",
      _w._looks_like_text_tool_call("这个请求需要用到 search_knowledge_base 工具。") is False)

# ── ①.8 空回复兜底文案按场景分支（没有附件就不该劝人"换更小的 PDF"）──
worker_noatt = _Worker()
worker_noatt.llm = _EmptyLLM()
worker_noatt.tools_map = {}
worker_noatt.user_input = "用一句话介绍你自己"
worker_noatt.think_mode = "快速回答"
worker_noatt.privacy_shield = False
worker_noatt.sandbox_path = str(_SBX)
worker_noatt.attachment_fulltext = {}          # 没有附件
na_done = []
worker_noatt.bus.subscribe("finished", lambda p: na_done.append(p))
worker_noatt.run()
na_reply = str(na_done[-1]) if na_done else ""
check("无附件时不说「附件/OCR/PDF」那套话术（答非所问修掉了）",
      na_reply.strip() and "附件" not in na_reply and "PDF" not in na_reply, na_reply[:80])
check("无附件时如实说明原因（本机小模型偶发空回复）",
      "空回复" in na_reply or "没有给出回答" in na_reply, na_reply[:80])
check("★ 落库内容与界面收到的回答一致（库里存的就是屏上看到的）",
      str(_persisted[-1].get("content") or "").strip() == _empty_reply.strip(),
      f"库={str(_persisted[-1].get('content'))[:40]!r} 界面={_empty_reply[:40]!r}")

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
print("【8】可读写范围之外：按档位判定（ask 人工确认 / full 放行留痕 / plan 拒绝）")
print("=" * 78)
# 这一段锁住"沙箱从绝对墙降级为默认可读写范围"之后**必须由宿主守住**的东西：
#   · ask：越界必须真的弹人工确认，且弹窗里是完整真实路径；
#   · full：越界放行可以自动，但必须留审计 + 在工具结果里写明"在可读写范围之外"；
#   · plan：越界一律拒绝（且不弹确认——本档没有审批流）；
#   · 模型传入的"允许越界"参数必须被丢弃（放行只能由宿主策略层授予）。
from yindun.core import file_tools as _ft                                     # noqa: E402
from yindun.core import permission as _perm                                   # noqa: E402
from yindun.core.audit_log import AuditLog as _AuditLog                       # noqa: E402
from yindun.app.webview_app import JsApi as _JsApi                            # noqa: E402

RANGE = TMP / "range"
RANGE_IN = RANGE / "in"
RANGE_OUT = RANGE / "out"
RANGE_IN.mkdir(parents=True, exist_ok=True)
RANGE_OUT.mkdir(parents=True, exist_ok=True)
RANGE_FILE = RANGE_OUT / "range_probe.txt"
_saved_sbx_env2 = os.environ.get("SANDBOX_PATH")
_saved_perm_env2 = os.environ.get(_perm.PERMISSION_ENV)
os.environ["SANDBOX_PATH"] = str(RANGE_IN)
# ★ 审计落点隔离：本段会产生带**真实路径**的越界审计条目，写进仓库 audit_logs/ 会污染
#   "落盘敏感明文"扫描（隐私套件扫的就是那个目录）；测试数据不该进产品审计链。
_audit_store = _AuditLog()
_saved_audit_dir2 = _audit_store._storage_path
_audit_store._storage_path = TMP / "audit_logs"
SUBSTANTIVE = ("已按要求完成：\n1. 文件已创建\n2. 位置见工具返回\n3. 如需其它内容请告诉我")


class _CtxLLM:
    """脚本化模型：第一轮请求工具，之后给出成段回答；**记录每轮上下文**（断言工具结果用）。"""

    def __init__(self, tool_name, args, rounds=1, answer=SUBSTANTIVE):
        self.tool_name, self.args, self.rounds, self.answer = tool_name, args, rounds, answer
        self.seen = []

    def invoke(self, messages):
        self.seen.append(list(messages))
        if len(self.seen) <= self.rounds:
            return _AI(content="", tool_calls=[{"name": self.tool_name, "args": dict(self.args),
                                                "id": f"call_{len(self.seen)}"}])
        return _AI(content=self.answer)

    def bind_tools(self, tools):
        return self


def _tool_outputs(llm):
    """把模型"看到过"的工具结果文本收集起来（ToolMessage 的 content）。"""
    out = []
    for ctx in llm.seen:
        for msg in ctx:
            if type(msg).__name__ == "ToolMessage":
                out.append(str(getattr(msg, "content", "")))
    return out


def run_range_worker(llm, mode, approve=None, user_text=""):
    """驱动一次真实 Worker：深速模式（写工具不被快速模式拦截）+ 指定档位 + 指定沙箱。"""
    os.environ[_perm.PERMISSION_ENV] = mode
    worker = _Worker()
    worker.llm = llm
    worker.tools_map = {"create_local_file": _ft.create_local_file}
    worker.user_input = user_text or f"帮我在 {RANGE_OUT} 创建一个文件"
    worker.think_mode = "深度思考"
    worker.privacy_shield = False
    worker.sandbox_path = str(RANGE_IN)
    confirmations, statuses, finished = [], [], []

    def _on_confirm(payload):
        confirmations.append(payload)
        if approve is not None:            # 不点确认会等满 300 秒（测试里必须显式答复）
            worker.approve(approve)

    worker.bus.subscribe("need_confirm", _on_confirm)
    worker.bus.subscribe("status", lambda p: statuses.append(str(p)))
    worker.bus.subscribe("finished", lambda p: finished.append(str(p)))
    worker.run()
    return worker, confirmations, statuses, finished


def _ac_since(mark):
    entries = _AuditLog().get_entries({"event_type": "access_control"}) or []
    return entries[mark:]


# ① ask 档：越界写盘 → 弹确认（完整真实路径）→ 批准后放行，结果里写明越界
_ac0 = len(_AuditLog().get_entries({"event_type": "access_control"}) or [])
llm_ask = _CtxLLM("create_local_file", {"filename": RANGE_FILE.name, "content": "hi",
                                        "target_directory": str(RANGE_OUT)})
_w_ask, conf_ask, st_ask, fin_ask = run_range_worker(llm_ask, "ask", approve=True)
check("★ ask 档越界写盘触发人工确认（不是直接拒绝，也不是静默放行）", len(conf_ask) == 1, str(len(conf_ask)))
if conf_ask:
    _p = conf_ask[0]
    check("★ 审批弹窗里是完整真实路径（越界审批不能让人盲签）",
          os.path.normcase(str(_p.get("path"))) == os.path.normcase(str(RANGE_FILE)), str(_p.get("path")))
    check("审批载荷标出「这是范围外的操作」+ 当前范围（界面据此显性化）",
          _p.get("outside") is True and bool(_p.get("sandbox")), str(_p.get("outside")))
check("★ 人工批准后越界写盘真实落盘", RANGE_FILE.exists(), str(RANGE_FILE))
check("★ 越界放行在工具结果里写明「本次操作在可读写范围之外」（对话流里看得见）",
      any("本次操作在可读写范围之外" in t for t in _tool_outputs(llm_ask)),
      str(_tool_outputs(llm_ask))[:120])
_ac_ask = [e for e in _ac_since(_ac0)
           if (e.details or {}).get("outside_sandbox")]
check("★ 越界放行写入审计（工具名/真实路径/档位/判定）",
      any((e.details or {}).get("approved") and (e.details or {}).get("tool_name") == "create_local_file"
          and (e.details or {}).get("permission") == "ask" for e in _ac_ask),
      str([(e.details or {}).get("decision") for e in _ac_ask]))

# ② ask 档：驳回 → 不落盘（人工否决是真实的）
RANGE_FILE.unlink(missing_ok=True)
_ac1 = len(_AuditLog().get_entries({"event_type": "access_control"}) or [])
llm_rej = _CtxLLM("create_local_file", {"filename": RANGE_FILE.name, "content": "hi",
                                        "target_directory": str(RANGE_OUT)})
_w_rej, conf_rej, st_rej, fin_rej = run_range_worker(llm_rej, "ask", approve=False)
check("★ ask 档驳回越界操作：文件没被创建（人工否决不是装饰）",
      len(conf_rej) == 1 and not RANGE_FILE.exists())
check("★ 驳回后宿主给确定性答复（已按你的选择驳回 / 未改动）",
      any("已按你的选择驳回" in f and "也没有改动" in f for f in fin_rej),
      str(fin_rej)[:160])

# ③ full 档：越界写盘免确认放行 + 审计 + 结果标注
RANGE_FILE.unlink(missing_ok=True)
_ac2 = len(_AuditLog().get_entries({"event_type": "access_control"}) or [])
llm_full = _CtxLLM("create_local_file", {"filename": RANGE_FILE.name, "content": "hi",
                                         "target_directory": str(RANGE_OUT)})
_w_full, conf_full, st_full, fin_full = run_range_worker(llm_full, "full", approve=True)
check("★ full 档越界写盘免人工确认（「完全访问=允许写盘」在这里落到实处）",
      len(conf_full) == 0 and RANGE_FILE.exists(), f"confirm={len(conf_full)}")
check("★ full 档越界放行同样在结果里标注 + 状态栏可见（不静默）",
      any("本次操作在可读写范围之外" in t for t in _tool_outputs(llm_full))
      and any("可读写范围外操作" in s for s in st_full),
      str([s for s in st_full if "范围外" in s])[:120])
check("★ full 档越界放行也写审计（自动放行≠无痕）",
      any((e.details or {}).get("approved") and (e.details or {}).get("permission") == "full"
          for e in _ac_since(_ac2) if (e.details or {}).get("outside_sandbox")),
      str([(e.details or {}).get("permission") for e in _ac_since(_ac2)]))

# ④ plan 档：越界一律拒绝（且不弹确认）
RANGE_FILE.unlink(missing_ok=True)
llm_plan = _CtxLLM("create_local_file", {"filename": RANGE_FILE.name, "content": "hi",
                                         "target_directory": str(RANGE_OUT)})
_w_plan, conf_plan, st_plan, fin_plan = run_range_worker(llm_plan, "plan", approve=True)
check("★ plan 档越界写盘直接拒绝、且不弹人工确认",
      len(conf_plan) == 0 and not RANGE_FILE.exists(), f"confirm={len(conf_plan)}")
check("拒绝话术给出可行动的下一步（设置里改范围 / 换档位），不是干巴巴一句「禁止」",
      any(("可读写范围" in t) and ("sandbox_dir" in t or "档位" in t)
          for t in _tool_outputs(llm_plan) + st_plan),
      str(_tool_outputs(llm_plan))[:160])

# ⑤ 模型自授权：args 里塞 _outside_ok / outside_ok → 丢弃 + 留痕 + 仍然按策略判定
RANGE_FILE.unlink(missing_ok=True)
_probe_args = {"filename": RANGE_FILE.name, "content": "hi",
               "target_directory": str(RANGE_OUT), "_outside_ok": True, "outside_ok": True}
_w_tool = _Worker.__new__(_Worker)
_w_tool.status = type("_S", (), {"emit": lambda self, m: None})()
_clean = _Worker._sanitize_tool_args(_w_tool, "create_local_file", dict(_probe_args))
check("★ 模型传入的越界授权参数被丢弃（_outside_ok / outside_ok 都不留）",
      "_outside_ok" not in _clean and "outside_ok" not in _clean, str(sorted(_clean)))
llm_self = _CtxLLM("create_local_file", dict(_probe_args))
_w_self, conf_self, st_self, fin_self = run_range_worker(llm_self, "ask", approve=False)
check("★ 模型自授权无效：ask 档仍要人工确认（没有因为参数写了 True 就放行）",
      len(conf_self) == 1 and not RANGE_FILE.exists(), f"confirm={len(conf_self)}")
check("丢弃内部参数有可见提示（谁在试图越权是看得见的）",
      any("已丢弃模型传入的内部参数" in s for s in st_self),
      str([s for s in st_self if "丢弃" in s])[:120])

# ⑥ 宿主授予的令牌是"一次调用"的：worker 用完即撤
check("越界令牌用完即撤（工具调用结束后本线程不再持有放行）",
      _ft.is_outside_approved(str(RANGE_OUT)) is False)

# ⑦ 设置通道：sandbox_dir 保存即生效（界面目录选择器走的就是这条）
_dir_svc = AgentService(settings=SettingsStore(path=TMP / "dir_cfg.json"),
                        sessions=SessionStore(path=TMP / "dir_sess.json"),
                        plugin_data_root=TMP / "dp")
_dir_svc.initialize()                       # 会注册"读设置"的沙箱读取器（内核每次现读）
_api_dir = _JsApi(_dir_svc, {})
check("目录不存在时保存如实失败（不把可读写范围指到一个不存在的目录）",
      _api_dir.save_settings({"sandbox_dir": str(RANGE / "没有这个目录")}) is False)
_ac3 = len(_AuditLog().get_entries({"event_type": "access_control"}) or [])
_ok_dir = _api_dir.save_settings({"sandbox_dir": str(RANGE_OUT)})
check("★ 保存后 sandbox_root() 立即返回新目录（改完即生效，无需重启）",
      _ok_dir is True and os.path.normcase(_ft.sandbox_root()) == os.path.normcase(str(RANGE_OUT)),
      _ft.sandbox_root())
check("新范围内的写盘按沙箱内语义判定（不再需要确认）",
      _dir_svc.effective_permission()["effective"] in _perm.MODES
      and __import__("yindun.core.policy_manager", fromlist=["PolicyManager"]).PolicyManager()
      .check("create_local_file", str(RANGE_OUT / "a.txt")) in ("approve", "confirm"),
      "（档位不同则判定不同：达到 full 时才是 approve）")
check("改范围写审计（放宽可读写范围是有安全含义的动作）",
      any("可读写范围改为" in (e.message or "") for e in _ac_since(_ac3)),
      str([e.message for e in _ac_since(_ac3)])[:160])
check("清空 sandbox_dir 回到默认（环境变量 → 程序目录）",
      _api_dir.save_settings({"sandbox_dir": ""}) is True
      and os.path.normcase(_ft.sandbox_root()) == os.path.normcase(str(RANGE_IN)),
      _ft.sandbox_root())

# ⑧ 模型"桌面"路径解析与工具侧一致（两层判定不许错位）
check("★ 用户说「我的桌面有几张图片」→ 解析到系统桌面（与工具侧同一套语义）",
      str(_Worker._det("我的桌面有几张图片？") or "").endswith("Desktop"),
      str(_Worker._det("我的桌面有几张图片？")))

_perm.register_sandbox_settings_source(None)          # 注销：别把这台的设置带出测试
_audit_store._storage_path = _saved_audit_dir2        # 还原审计落点（测试数据不进产品链）
if _saved_sbx_env2 is None:
    os.environ.pop("SANDBOX_PATH", None)
else:
    os.environ["SANDBOX_PATH"] = _saved_sbx_env2
if _saved_perm_env2 is None:
    os.environ.pop(_perm.PERMISSION_ENV, None)
else:
    os.environ[_perm.PERMISSION_ENV] = _saved_perm_env2

print(chr(10) + "=" * 78)
print("【9】模型就绪必须与事实一致（不写死默认模型 / 不宣称不存在的模型）")
print("=" * 78)
# 打桩检测出口：这一段不依赖真 Ollama，验证的是"服务层怎么处理检测结果"。
from yindun.app import llm_factory as _llmf                                   # noqa: E402
from yindun.app.settings_store import DEFAULTS as _DEFAULTS                   # noqa: E402

check("默认配置里 model 是空串（不再写死 qwen3.5:4b）", _DEFAULTS.get("model") == "",
      repr(_DEFAULTS.get("model")))


def _patch_detect(local, chat):
    """把"本机模型检测"整体打桩（含 API 与硬盘两条来源）。"""
    def _fake_local(_host=""):
        return {"models": list(local), "note": "已检测到本机模型（打桩）" if local else "没有检测到可对话的本机模型（打桩）"}
    _llmf.detect_local_models = _fake_local
    _llmf.detect_chat_models = lambda: list(chat)
    _llmf.detect_ollama_models = lambda: list(chat)
    agent_service.detect_chat_models = lambda: list(chat)
    agent_service.detect_ollama_models = lambda: list(chat)


# ① 首启未选模型 + 本机有可对话模型 → 自动选第一个并落库
_svc_a = AgentService(settings=SettingsStore(path=TMP / "k_a.json"),
                      sessions=SessionStore(path=TMP / "k_a_sess.json"),
                      plugin_data_root=TMP / "k_a_pd")
_svc_a.initialize()
check("首启未选模型时配置为空（不凭空写模型名）", _svc_a.settings.get("model") == "")
_svc_a._warmup_llm = lambda announce=True: False          # 不打桩网络、不起预热线程
_patch_detect(["stub-model:1b", "stub-model:7b"], ["stub-model:1b", "stub-model:7b"])
_st_a = _svc_a.prepare_llm()
check("★ 首次启动自动选用检测到的第一个可对话模型并落库",
      _svc_a.settings.get("model") == "stub-model:1b", str(_svc_a.settings.get("model")))
check("自动选择后就绪状态为真（无需用户手动填）",
      _st_a.get("ready") is True and _st_a.get("model_available") is True, str(_st_a)[:160])
_reload_cfg = SettingsStore(path=TMP / "k_a.json"); _reload_cfg.load()
check("自动选择的模型**落库**（下次启动不必重选）", _reload_cfg.get("model") == "stub-model:1b")

# ② 配置了一个本机不存在的模型 → 明确报"不可用 + 原因"，绝不报 ready
_svc_b = AgentService(settings=SettingsStore(path=TMP / "k_b.json"),
                      sessions=SessionStore(path=TMP / "k_b_sess.json"),
                      plugin_data_root=TMP / "k_b_pd")
_svc_b.initialize()
_svc_b.settings.set("model", "本机没有的模型:9b")
_svc_b.settings.save()
_svc_b._warmup_llm = lambda announce=True: False
_patch_detect([], [])
_st_b = _svc_b.prepare_llm()
check("★ 配置的模型不在可用清单里 → ready=False（不再「模型不存在却报已就绪」）",
      _st_b.get("ready") is False and _st_b.get("model_available") is False, str(_st_b)[:160])
check("★ 原因可行动：点名模型 + 指出重新选择或 ollama pull",
      "本机没有的模型:9b" in str(_st_b.get("error")) and "ollama pull" in str(_st_b.get("error")),
      str(_st_b.get("error"))[:160])
check("不覆盖用户手选的模型（用户的选择优先，只如实报不可用）",
      _svc_b.settings.get("model") == "本机没有的模型:9b")
_errors = []
_sync_events = []
_svc_b.set_listener(lambda ev, payload: (_errors.append((ev, str(payload))),
                                         _sync_events.append((ev, payload))))
check("★ 不可用时发送被拒并如实报原因（绝不假装在跑）",
      _svc_b.send("你好") is False and any(ev == "error" and "本机没有的模型:9b" in text
                                           for ev, text in _errors),
      str(_errors)[:160])
check("★ 发送被拒时同步刷新算力状态（同屏不再一边「就绪」一边「连不上」）",
      any(ev == "llm_status" and isinstance(p, dict) and p.get("ready") is False
          for ev, p in _sync_events), str(_sync_events)[:200])

# ③ 一个可对话模型都检测不到 → 保持空（界面显示"请选择模型"）
_svc_c = AgentService(settings=SettingsStore(path=TMP / "k_c.json"),
                      sessions=SessionStore(path=TMP / "k_c_sess.json"),
                      plugin_data_root=TMP / "k_c_pd")
_svc_c.initialize()
# 先清掉内置演示模型，单独验证"什么都没有"时的空态（演示模型回退另有专门用例）
_svc_c.settings.set("custom_models", {})
_svc_c.settings.save()
_patch_detect([], [])
_st_c = _svc_c.prepare_llm()
check("检测不到可对话模型时保持空（不写死名字）",
      _svc_c.settings.get("model") == "" and _st_c.get("model") == "", str(_st_c.get("model")))
check("空模型的原因话术是「没有选择模型」（界面照此提示）",
      "没有选择模型" in str(_st_c.get("error")), str(_st_c.get("error"))[:160])

# ④ bootstrap 载荷带上算力清单关键字段（首屏就能诚实显示"请选择模型"）
_boot = _JsApi(_svc_c, {}).bootstrap()
_mc = _boot.get("model_catalog") or {}
check("★ bootstrap 带 model_catalog（current_available + local_note 供界面首屏判断）",
      _mc.get("current_available") is False and bool(_mc.get("local_note"))
      and isinstance(_mc.get("local_detected"), list), str(_mc)[:160])
check("bootstrap 的 settings 里带 sandbox_dir 与当前生效范围",
      "sandbox_dir" in (_boot.get("settings") or {}) and bool((_boot.get("settings") or {}).get("sandbox")),
      str(sorted((_boot.get("settings") or {}).keys()))[:160])

# ⑤ ★ 默认可读写范围不得被"启动一轮对话"挤成单根（真机探针逮到的坑）：
#   环境变量 SANDBOX_PATH 在内核解析顺序里是**硬覆盖**，服务层一旦把"主根（程序目录）"
#   写进去，默认集合（桌面/文档/下载）就没了——表现是"发一句话之后桌面又变成越界要确认"。
_saved_env3 = os.environ.pop("SANDBOX_PATH", None)
try:
    _svc_d = AgentService(settings=SettingsStore(path=TMP / "k_d.json"),
                          sessions=SessionStore(path=TMP / "k_d_sess.json"),
                          plugin_data_root=TMP / "k_d_pd")
    _svc_d.initialize()
    _svc_d.apply_runtime_env()
    check("★ apply_runtime_env 不把主根写进 SANDBOX_PATH（否则默认集合被硬覆盖挤掉）",
          os.environ.get("SANDBOX_PATH") is None, str(os.environ.get("SANDBOX_PATH")))
    _user_dirs = _perm.user_working_dirs()
    _roots_now = _perm.sandbox_roots()
    check("默认可读写范围 = 程序目录 + 存在的用户日常目录（桌面/文档/下载都在范围内）",
          all(any(os.path.normcase(r) == os.path.normcase(u) for r in _roots_now)
              for u in _user_dirs) if _user_dirs else True,
          f"{_roots_now} vs {_user_dirs}")
    _desktop = Path(os.path.expanduser("~")) / "Desktop"
    check("（这台机器上）桌面确实在默认范围内 —— 负责人要的「数桌面 / 在桌面建 txt」是本职活",
          (not _desktop.is_dir()) or _perm.is_within_sandbox(str(_desktop)), str(_desktop))
finally:
    if _saved_env3 is not None:
        os.environ["SANDBOX_PATH"] = _saved_env3

print(chr(10) + "=" * 78)
print("【7】强制删除：明确指令在任何 LLM 之前由宿主执行")
print("=" * 78)
from yindun.worker.agent_worker import Worker  # noqa: E402

_w = Worker()
_demo = TMP / "客户名单演示.txt"
_demo.write_text("演示数据", encoding="utf-8")
_w.tools_map = {"delete_local_file": object()}
_w.user_input = f"删除 {_demo}"
check("删除 + 存在的完整路径 → 识别为强制删除目标",
      _w._forced_delete_target() == str(_demo), _w._forced_delete_target())
_w.user_input = f"删除 {TMP / '不存在.txt'}"
check("文件不存在 → 不接管（交回工具层如实报错）", _w._forced_delete_target() == "")
_w.user_input = f"查看 {_demo}"
check("无删除动作词 → 不接管", _w._forced_delete_target() == "")

_w.user_input = f"删除 {_demo}"
_w._execute_with_approval = lambda name, args: "已驳回：敏感操作被人工拦截"
_reply = _w._run_forced_delete(str(_demo))
check("驳回后给确定性答复（不再让模型总结、不编造范围）",
      _reply.startswith("已按你的选择驳回删除") and "文件未改动" in _reply, _reply)

_w._execute_with_approval = lambda name, args: "✅ 物理删除成功！"
_reply2 = _w._run_forced_delete(str(_demo))
check("批准后给确定性成功答复并提示已审计",
      _reply2.startswith("已删除") and "审计" in _reply2, _reply2)

# 错误分档：内部异常不得因为文本里带 "timeout" 就被报成网络问题
_internal = Worker._translate_error(
    TypeError("_fake_timeout() got an unexpected keyword argument 'tool_id'"))
check("★ TypeError 报「内部错误」而非网络超时",
      "内部错误" in _internal and "请求超时" not in _internal, _internal[:80])
_timeout = Worker._translate_error(TimeoutError("timed out"))
check("真正的超时仍报「请求超时」", "请求超时" in _timeout, _timeout[:80])

# BUG-3：模型总结与工具事实相反时，最终回答附"工具核对"，以文件系统结果为准
_w._turn_write_facts = [("move_local_file", "✅ 已移动 5 个文件到「会议纪要」")]
_w.user_input = "整理会议纪要"
_fact_reply = _w._append_write_facts("已移动 1 项。")
check("★ 写操作附工具核对（5 个文件的事实压过模型的“1 项”）",
      "本次工具核对" in _fact_reply and "5 个文件" in _fact_reply, _fact_reply)

# BUG-4：用户没问隐私时，清掉模型自带的“隐私说明/占位符”尾巴
_privacy_tail = "会议纪要已整理好。\n\n隐私说明：系统会把 [PHONE_0_x] 在落盘时替换为真实数据。"
check("★ 内部隐私说明尾巴被清理",
      _w._strip_internal_privacy_notes(_privacy_tail) == "会议纪要已整理好。",
      _w._strip_internal_privacy_notes(_privacy_tail))
_w.user_input = "你们的占位符机制是什么"
check("用户主动问隐私时不清理", "隐私说明" in _w._strip_internal_privacy_notes(_privacy_tail))

_kb_left = _w._mask_unresolved_kb_tokens("结果 [KB_NAME_0_xtzr] 与普通编号 KB-2024")
check("★ 还原不了的 KB 占位符不再原样露给用户",
      "[未能还原的敏感片段]" in _kb_left and "KB_NAME" not in _kb_left
      and "KB-2024" in _kb_left, _kb_left)

# BUG-1：桥接层异常必须回可读原因，而不是让前端显示“未知原因”
from yindun.app.webview_app import JsApi  # noqa: E402

class _BoomService:
    def add_custom_model(self, *args, **kwargs):
        raise RuntimeError("模拟保存异常")

_api = JsApi(_BoomService(), {})
_bridge = _api.add_custom_model("n", "m", "https://example.invalid", "k")
check("★ 模型保存异常回传可读原因（不再是 None / 未知原因）",
      isinstance(_bridge, dict) and _bridge.get("ok") is False
      and "RuntimeError" in str(_bridge.get("error")), str(_bridge))

# BUG-1 的另一个可能根因：用户手工改坏 custom_models（列表）后永远保存不进去
_bad = AgentService(settings=SettingsStore(path=TMP / "bad_models.json"),
                    sessions=SessionStore(path=TMP / "bad_sessions.json"),
                    plugin_data_root=TMP / "bad_pd")
_bad.initialize()
_bad.settings.set("custom_models", ["broken"])
_r = _bad.add_custom_model("n", "m", "https://example.invalid/v1", "k")
check("★ custom_models 被改坏时仍能保存（坏配置降级为空表）",
      bool(_r.get("ok")) and isinstance(_bad.settings.get("custom_models"), dict), str(_r))

print(chr(10) + "=" * 78)
print("【7b】内置演示模型（公共中转，免 Key）")
print("=" * 78)
_demo_cfg = TMP / "demo_cfg.json"
_demo_sess = TMP / "demo_sess.json"
for _p in (_demo_cfg, _demo_sess):
    if _p.exists():
        _p.unlink()
_demo_svc = AgentService(settings=SettingsStore(path=_demo_cfg),
                         sessions=SessionStore(path=_demo_sess),
                         plugin_data_root=TMP / "demo_pd")
_demo_svc.initialize()
_demo_cm = _demo_svc.settings.get("custom_models") or {}
check("★ 初始化即登记内置演示模型（免 Key、公网档）",
      AgentService.DEMO_MODEL_NAME in _demo_cm
      and _demo_cm[AgentService.DEMO_MODEL_NAME].get("api_key") == ""
      and _demo_cm[AgentService.DEMO_MODEL_NAME].get("base_url", "").startswith("https://"),
      str(_demo_cm.get(AgentService.DEMO_MODEL_NAME))[:160])
# 幂等 + 不覆盖用户改过的同名条目
_demo_cm[AgentService.DEMO_MODEL_NAME]["base_url"] = "http://user-custom.example/v1"
_demo_svc.settings.set("custom_models", _demo_cm)
_demo_svc.ensure_demo_model()
check("重复调用幂等，且不覆盖用户改过的同名条目",
      (_demo_svc.settings.get("custom_models") or {})[AgentService.DEMO_MODEL_NAME]["base_url"]
      == "http://user-custom.example/v1", "")
# 旧地址自动升级：早期批次写入的 workers.dev 地址要跟随版本刷新为当前常量
_demo_svc.settings.set("custom_models", {AgentService.DEMO_MODEL_NAME: {
    "model_id": AgentService.DEMO_MODEL_ID,
    "base_url": "https://yindun-relay.944898918.workers.dev/api/ai",
    "api_key": "", "tier": "wan", "provider": "隐盾自建中转站",
    "api_format": "chat_completions", "demo_relay": True}})
_demo_svc.ensure_demo_model()
_up = (_demo_svc.settings.get("custom_models") or {})[AgentService.DEMO_MODEL_NAME]
check("★ 旧演示条目地址自动升级到当前地址（评委不会拿到不可达域名）",
      _up["base_url"] == AgentService.DEMO_MODEL_BASE and _up["provider"] == "隐盾",
      str(_up))
# 没有任何本机模型时，首启自动选演示模型（评委机器无 Ollama 的场景）
_demo_svc.settings.set("model", "")
_demo_svc.settings.set("custom_models", {AgentService.DEMO_MODEL_NAME: dict(_demo_cm[AgentService.DEMO_MODEL_NAME], base_url="http://x")})
def _demo_catalog():
    _cur = str(_demo_svc.settings.get("model") or "")
    _cm = _demo_svc.settings.get("custom_models") or {}
    return {"current_available": bool(_cur and _cur in _cm),
            "local_detected": [],
            "tiers": {"local": [], "lan": [], "wan": list(_cm.keys())},
            "local_note": "没有检测到本机模型"}


_demo_svc.model_catalog = _demo_catalog
_picked, _avail, _reason = _demo_svc._resolve_model_selection()
check("★ 本机无模型时自动选中演示模型（开箱可用）",
      _picked == AgentService.DEMO_MODEL_NAME and _avail is True, f"{_picked} / {_avail} / {_reason}")

print(chr(10) + "=" * 78)
print("【8】算力不可用时的后台自动重探（先开隐盾、后开 Ollama）")
print("=" * 78)
_re_svc = AgentService(settings=SettingsStore(path=TMP / "recheck_cfg.json"),
                       sessions=SessionStore(path=TMP / "recheck_sess.json"),
                       plugin_data_root=TMP / "recheck_pd")
_re_svc.initialize()
_re_svc._llm = None
_re_svc._llm_error = "连不上本机 Ollama"
_calls = {"prepare": 0}


def _fake_catalog():
    return {"current_available": True}


def _fake_prepare(announce=True):
    _calls["prepare"] += 1
    _re_svc._llm = object()          # 模拟"算力已建好"
    _re_svc._llm_error = ""
    return {"ready": True}


_re_svc.model_catalog = _fake_catalog
_re_svc.prepare_llm = _fake_prepare
_re_svc.schedule_llm_recheck(delay=0.05)
_deadline = time.time() + 3.0
while time.time() < _deadline and _re_svc._llm is None:
    time.sleep(0.05)
check("★ 探测到算力恢复后自动重建（无需重启客户端）",
      _re_svc._llm is not None and _calls["prepare"] >= 1,
      f"prepare={_calls['prepare']} llm={_re_svc._llm!r}")
time.sleep(0.3)
check("重探线程成功后自行退出（不会常驻空转）",
      not (_re_svc._recheck_thread and _re_svc._recheck_thread.is_alive()), "")
_re_svc.schedule_llm_recheck(delay=0.05)
check("已有算力时 schedule 直接返回（不重复起线程）",
      _re_svc._recheck_thread is None or not _re_svc._recheck_thread.is_alive(), "")

print(chr(10) + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：配置/会话加密落盘、工具消息保留、旧格式兼容、服务编排与事件转发、模型利用率与工具链均正常")
sys.exit(0)
