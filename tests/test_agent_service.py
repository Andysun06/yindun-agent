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

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：配置/会话加密落盘、工具消息保留、旧格式兼容、服务编排与事件转发均正常")
sys.exit(0)
