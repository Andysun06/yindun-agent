# -*- coding: utf-8 -*-
"""隐盾 · 内置插件逐个实测（每个插件：启用 → 真用一次 → 停用 → 能力收回）

为什么单独有这个套件：
  `test_plugins.py` 测的是**宿主契约**（发现/校验/隔离/超时），
  `test_plugin_capabilities.py` 按**能力**（识别/模板/解析/导出）分组测链路。
  两者都很硬，但都回答不了负责人问的那句"**六个插件你是不是每个都真跑过**"——
  按能力分组的测法里，一个插件可能只被"顺带"覆盖到（例如 doc_extra 只跑了 .eml，
  另三个声明的扩展名从没解析过；decision_hint 则只有"没模型时返回 None"，正向路径一次没测）。
  所以这里**以插件为单位**列一张表：每个插件单独走一遍
  「启用 → 用真实入口跑一次 → 停用 → 确认能力真的收回」，
  模型依赖的插件用**桩模型**把逻辑跑通（明确标注哪一段是桩，不是真模型）。

用途：`python tests/test_plugins_live.py`（退出码 0 = 六个插件逐个通过）

★ 已知的验证边界（如实写在最前面，不藏在细节里）：
  · decision_hint / laya_risk 的**真实模型推理**不在本套件内——它们需要本机 Ollama 与
    laya 权重；本套件用桩替身验证"提示生成 → 解析 → 分级 → 不当成判定"这段产品逻辑。
    真实模型链路请跑 `YINDUN_E2E=1 python tests/test_e2e_local.py`（需 Ollama 在线）。
  · 插件在 **exe 里的那份**与源码是否一致，由 `工具/校验`（本文件末尾）比对哈希确认。
"""
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp(prefix="yindun_plugins_live_"))
os.environ["SANDBOX_PATH"] = str(TMP / "sandbox")
(TMP / "sandbox").mkdir(parents=True, exist_ok=True)

from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402
from yindun.plugins.host import PluginHost  # noqa: E402

failures = []
BUILTIN = ROOT / "yindun" / "plugins" / "builtin"
ALL_PLUGINS = ["custom_dict", "decision_hint", "doc_extra", "office_wf", "export_pack", "laya_risk"]


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def new_service(tag: str) -> AgentService:
    root = TMP / tag
    root.mkdir(parents=True, exist_ok=True)
    svc = AgentService(settings=SettingsStore(path=root / "cfg.json"),
                       sessions=SessionStore(path=root / "sess.json"),
                       plugin_data_root=root / "plugins_data")
    svc.initialize()
    return svc


def live_host(tag: str):
    """单独建一个宿主（不碰 AgentService 的全局单例），只用来做启停与钩子直调。"""
    enabled = {}
    host = PluginHost(enabled_lookup=lambda pid: bool(enabled.get(pid)),
                      enabled_setter=lambda pid, on: enabled.__setitem__(pid, on),
                      data_root=TMP / tag)
    host.discover()
    return host


# ══ 0) 先确认六个插件都在场、都能被发现、默认都关着 ══════════
print("=" * 78)
print("【0】六个内置插件全部到场（发现 / 默认关闭 / 依赖满足）")
print("=" * 78)
host = live_host("discover")
listing = {p["id"]: p for p in host.list_plugins()}
check("发现的内置插件与预期一致（没有漏打包、没有多出来的）",
      set(listing) == set(ALL_PLUGINS), str(sorted(listing)))
for pid in ALL_PLUGINS:
    item = listing.get(pid) or {}
    check(f"{pid}：默认关闭 + 依赖满足 + 声明了钩子",
          item.get("enabled") is False and item.get("usable") is True
          and bool(item.get("hooks")), json.dumps(item, ensure_ascii=False)[:120])

# ══ 1) custom_dict：词表 → 脱敏链路 → 停用收回 ══════════════
print("=" * 78)
print("【1】custom_dict · 自定义敏感词表（recognizer）")
print("=" * 78)
svc1 = new_service("custom_dict")
from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402
sample = "关于星海计划的材料，编号 QP-2231，联系人 13812345678。"
# 基线必须在**启用之前**取：识别器一挂上，全局的 PrivacyEngine 立刻就能看见它
before, _ = PrivacyEngine().anonymize(sample)
check("未启用时自定义词按原文通过（内核不认识它，这是基线）",
      "星海计划" in before and "QP-2231" in before, before)

svc1.set_plugin_enabled("custom_dict", True)
svc1.plugin_config_write("custom_dict", "words.txt", "星海计划\nre:QP-\\d{4}\n")
anon, mapping = PrivacyEngine().anonymize(sample)
check("启用后自定义词被替换为占位符（明文不再进模型）",
      "星海计划" not in anon and "QP-2231" not in anon and "PLUGIN_CUSTOM_TERM" in anon, anon)
check("映射表里有对应的密文（还原仍只在本机）",
      any(k.startswith("[PLUGIN_CUSTOM_TERM") for k in mapping), str(list(mapping)[:3]))
check("内核原有实体照旧脱敏（插件只做加法，不是替换）",
      "13812345678" not in anon and "PHONE" in anon, anon)
svc1.set_plugin_enabled("custom_dict", False)
after, _ = PrivacyEngine().anonymize(sample)
check("停用后自定义词不再脱敏（能力真的被收回，不是界面关掉而已）",
      "星海计划" in after, after)
check("停用后内核实体仍照旧脱敏（收回的是插件能力，不是内核能力）",
      "13812345678" not in after, after)

# ══ 2) decision_hint：桩模型验证正向逻辑（真实模型见文件头边界说明）══
print("=" * 78)
print("【2】decision_hint · 审批决策提示（advisory_for_approval，桩模型）")
print("=" * 78)
sys.path.insert(0, str(BUILTIN / "decision_hint"))
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("_dh_plugin", BUILTIN / "decision_hint" / "plugin.py")
dh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dh)

CTX = {"model": "qwen3.5:4b", "user_request": "帮我把合同里的联系人整理出来",
       "tool": "delete_local_file", "path": "D:\\合同.docx", "args": {"filename": "合同.docx"}}

check("没有模型时安静返回 None（不打扰审批流程）",
      dh.advisory_for_approval(dict(CTX, model="")) is None)


class _StubLLM:
    """桩模型：只替掉"模型返回什么"，插件里 prompt→解析→分级→措辞这段逻辑是真的。"""
    reply = ""

    def invoke(self, _prompt):
        class _M:
            content = _StubLLM.reply
        return _M()


_real_chat_ollama = None
try:
    import langchain_ollama as _lo
    _real_chat_ollama = _lo.ChatOllama
    _lo.ChatOllama = lambda **_kw: _StubLLM()
except Exception as exc:
    check("stub 注入 langchain_ollama", False, str(exc))

_StubLLM.reply = '{"verdict": "相符", "reason": "与整理联系人的请求一致"}'
r1 = dh.advisory_for_approval(dict(CTX))
check("判定「相符」→ info 级提示，且文案写明是本地模型判断",
      r1 and r1["level"] == "info" and "相符" in r1["text"], str(r1))
_StubLLM.reply = '{"verdict": "不符", "reason": "用户只要求整理，未要求删除"}'
r2 = dh.advisory_for_approval(dict(CTX))
check("判定「不符」→ warn 级提示（越权/破坏性风险被抬到高级别）",
      r2 and r2["level"] == "warn" and "不符" in r2["text"], str(r2))
_StubLLM.reply = '{"verdict": "存疑", "reason": "缺少上下文"}'
r3 = dh.advisory_for_approval(dict(CTX))
check("判定「存疑」→ warn 级提示", r3 and r3["level"] == "warn" and "存疑" in r3["text"], str(r3))
# 模型爱加戏：带前后缀与代码块时也要能解析出来
_StubLLM.reply = '好的，结果如下：\n```json\n{"verdict": "不符|越权", "reason": "越权删除"}\n```'
r4 = dh.advisory_for_approval(dict(CTX))
check("模型输出带解释/代码块时仍能解析出结论（真实 7B 模型的常见形态）",
      r4 and "不符" in r4["text"], str(r4))
_StubLLM.reply = "今天天气不错"
r5 = dh.advisory_for_approval(dict(CTX))
check("完全解析不出结论时返回 None（宁可没提示，也不编一个）", r5 is None, str(r5))
# 解析器对"不符合"含"符合"这类中文坑的处理
check("解析器先判「不符」再判「相符」（『不符合』不会被误判成相符）",
      dh._verdict_from_text("不符合用户请求") == "不符", str(dh._verdict_from_text("不符合用户请求")))
check("提示只落在 level/text 两个字段（没有审批放行字段，接口层就没这个口子）",
      set(r2.keys()) == {"level", "text"}, str(set(r2.keys())))

if _real_chat_ollama is not None:
    import langchain_ollama as _lo2
    _lo2.ChatOllama = _real_chat_ollama

# ══ 3) doc_extra：三种声明格式真的都能解析 ══════════════════
print("=" * 78)
print("【3】doc_extra · 附件格式扩展（.eml / .html / .htm / .mht）")
print("=" * 78)
svc3 = new_service("doc_extra")
svc3.set_plugin_enabled("doc_extra", True)
mail = TMP / "doc_extra" / "往来邮件.eml"
mail.parent.mkdir(parents=True, exist_ok=True)
mail = TMP / "doc_extra" / "往来邮件.eml"
mail.parent.mkdir(parents=True, exist_ok=True)
# ★ 邮件类样本一律 write_bytes：Windows 上 write_text 会把 \n 再翻译一遍，
#   把规范 CRLF 变成 \r\r\n、做出"头体分离被破坏"的坏样本（第一次跑就踩了）
mail.write_bytes(("From: zhangsan@example.com\r\n"
                  "Subject: =?utf-8?B?5ZCI5ZCM56Gu6K6k?=\r\n"
                  "Content-Type: text/plain; charset=utf-8\r\n\r\n合同已确认，请安排付款。\r\n").encode("utf-8"))
page = TMP / "doc_extra" / "公告.html"
page.write_text("<html><head><title>内部公告</title><style>p{color:red}</style></head>"
                "<body><p>本周例会改到周四。</p><p>请知悉。</p></body></html>", encoding="utf-8")
htm = TMP / "doc_extra" / "说明.htm"
htm.write_text("<html><body><h1>说明</h1><div>仅正文可见。</div></body></html>", encoding="utf-8")
mht = TMP / "doc_extra" / "归档.mht"
mht.write_bytes(("From: somebody\r\nSubject: =?utf-8?B?5b2S5qGj5qGj?=\r\n"
                 "Content-Type: text/plain; charset=utf-8\r\n\r\n归档正文内容。\r\n").encode("utf-8"))
# 真实世界常见形态：**不声明 charset**（MIME 默认 us-ascii）而正文是中文。
# 修复前这种文件会被整段解码成 "�"——这是实测出来的真缺陷（见 _part_text 的注释）
nocharset = TMP / "doc_extra" / "未声明编码.eml"
nocharset.write_bytes(("From: a@b.com\r\nSubject: =?utf-8?B?5oql6ZSA5Y2V?=\r\n"
                       "\r\n本周报销已提交，请审阅。\r\n").encode("utf-8"))

svc3.attach_files([str(mail), str(page), str(htm), str(mht), str(nocharset)])
by = {item["name"]: item for item in svc3.list_attachments()}
texts = {item["name"]: (item.get("text") or "") for item in svc3._attachments}
check("五个附件都被接收（声明过的格式不再被内核拒绝）", len(by) == 5, str(sorted(by)))
for name, must in (("往来邮件.eml", "合同已确认"), ("公告.html", "本周例会改到周四"),
                   ("说明.htm", "仅正文可见"), ("归档.mht", "归档正文内容"),
                   ("未声明编码.eml", "本周报销已提交")):
    item = by.get(name) or {}
    check(f"{name}：解析出真正文「{must}」且来源标注为插件解析",
          must in texts.get(name, "") and str(item.get("via", "")).startswith("插件解析"),
          f"via={item.get('via')} text={texts.get(name, '')[:60]!r}")
check("未声明 charset 的中文正文不再变成乱码（★ 实测出来的缺陷，本次已修）",
      "\ufffd" not in texts.get("未声明编码.eml", ""), repr(texts.get("未声明编码.eml", "")[:60]))
check(".html 解析排除样式（不把 CSS 当正文塞给模型）",
      "color:red" not in texts.get("公告.html", ""), texts.get("公告.html", "")[:80])
check(".htm 的标签被剥掉（留下文字而不是 HTML 源码）",
      "<" not in texts.get("说明.htm", ""), texts.get("说明.htm", "")[:80])
svc3.set_plugin_enabled("doc_extra", False)
check("停用后可选扩展名里不再有 eml（能力跟着开关走）",
      "eml" not in svc3.supported_exts(), str(svc3.supported_exts()))

# ══ 4) office_wf：模板注册 → 高危步骤被内核强制审批 → 停用收回 ══
print("=" * 78)
print("【4】office_wf · 办公工作流模板包（workflow_template）")
print("=" * 78)
svc4 = new_service("office_wf")
svc4.set_plugin_enabled("office_wf", True)
tmpl = svc4.workflow_templates()
plugin_ids = [t["id"] for t in tmpl if t.get("source") == "plugin"]
check("两个模板都注册上了（代码安全体检 / 周报），且来源标注为插件",
      {"ext_office_code_audit", "ext_office_weekly_real"} <= set(plugin_ids), str(plugin_ids))
started = svc4.workflow_start("ext_office_code_audit", str(TMP / "sandbox"), "插件实测")
check("插件模板能真的创建实例", started.get("ok") is True, str(started))
steps = (started.get("status") or {}).get("steps") or []
forced = [s for s in steps if s.get("forced_approval")]
check("导出这类高危步骤即使插件写 auto，也被内核强制为人工审批",
      bool(forced), json.dumps(steps, ensure_ascii=False)[:200])
svc4.set_plugin_enabled("office_wf", False)
ids_after = [t["id"] for t in svc4.workflow_templates()]
check("停用后插件模板消失", "ext_office_code_audit" not in ids_after, str(ids_after))
check("内核模板不受影响（插件不能顶掉内核能力）",
      "wf_contract_review" in ids_after, str(ids_after))

# ══ 5) export_pack：两种格式真的出文件 → 停用后内核兜底 ═══════
print("=" * 78)
print("【5】export_pack · 导出格式扩展（审计 Markdown / 工作流卡片式 HTML）")
print("=" * 78)
svc5 = new_service("export_pack")
base = [f["format"] for f in svc5.audit_export_formats()]
svc5.set_plugin_enabled("export_pack", True)
with_plugin = {f["format"]: f["source"] for f in svc5.audit_export_formats()}
check("未启用时审计导出只有内核格式；启用后多出 markdown 且标注来源",
      base == ["json", "html"] and with_plugin.get("markdown") == "导出格式扩展",
      f"{base} → {with_plugin}")
p_md = svc5.export_audit("markdown")
text_md = Path(p_md).read_text(encoding="utf-8")
check("审计 Markdown 真的落盘、内容是报告（标题 + 表格 + 链完整性）",
      str(p_md).endswith(".md") and "#" in text_md and "|" in text_md and "链" in text_md,
      str(p_md))
from yindun.core.audit_log import AuditLog  # noqa: E402
_audit_dir = Path(getattr(AuditLog(), "_storage_path", "audit_logs"))
check("导出落点在审计目录内（插件无法借导出写到别处）",
      Path(p_md).resolve().is_relative_to(_audit_dir.resolve()), f"{p_md} vs {_audit_dir}")
wf = svc5.workflow_start("wf_contract_review", str(TMP / "sandbox"), "导出实测")
res_html = svc5.workflow_export(wf["instance_id"], "html_card")
p_html = res_html.get("path") if isinstance(res_html, dict) else res_html
check("工作流卡片式 HTML 真的落盘、是 HTML 不是纯文本",
      bool(p_html) and str(p_html).endswith(".html")
      and "<" in Path(p_html).read_text(encoding="utf-8"), str(res_html)[:160])
check("工作流导出也标注了渲染来源（内核 / 插件）",
      isinstance(res_html, dict) and "插件" in str(res_html.get("renderer", "")), str(res_html)[:120])
svc5.set_plugin_enabled("export_pack", False)
check("停用后审计导出回到内核格式（内核兜底不会被插件带走）",
      [f["format"] for f in svc5.audit_export_formats()] == ["json", "html"],
      str([f["format"] for f in svc5.audit_export_formats()]))

# ══ 6) laya_risk：桩模型给分级（真实模型见文件头边界说明）═════
print("=" * 78)
print("【6】laya_risk · 风险分级（advisory_for_approval，桩模型）")
print("=" * 78)
sys.path.insert(0, str(BUILTIN / "laya_risk"))
spec6 = importlib.util.spec_from_file_location("_laya_plugin", BUILTIN / "laya_risk" / "plugin.py")
layap = importlib.util.module_from_spec(spec6)
spec6.loader.exec_module(layap)
layap.PLUGIN_DATA_DIR = str(TMP / "laya_data")
Path(layap.PLUGIN_DATA_DIR).mkdir(parents=True, exist_ok=True)
(Path(layap.PLUGIN_DATA_DIR) / "risk_rules.txt").write_text("高危|delete_local_file|删除本地文件\n",
                                                          encoding="utf-8")
DANGER = {"tool": "delete_local_file", "path": "D:\\项目\\合同.docx", "user_request": "整理文件"}
SAFE = {"tool": "read_local_file", "path": "D:\\项目\\说明.txt", "user_request": "看一下说明"}


class _StubRouter:
    def __init__(self, prob):
        self.prob = prob

    def predict(self, _state, _questions):
        return {"answers": {"risk": {"noul": self.prob}}}


_real_loader = layap._load_router
layap._load_router = lambda: _StubRouter(0.91)
hi = layap.advisory_for_approval(dict(DANGER))
check("危险操作 + 桩模型高分 → 高危提示，且写明来源是 laya 模型",
      hi and hi["level"] == "warn" and "高危" in hi["text"] and "laya 模型" in hi["text"], str(hi))
layap._load_router = lambda: _StubRouter(0.05)
lo = layap.advisory_for_approval(dict(SAFE))
check("低分 → 低危 info 提示（模型说了算，不按正则加戏）",
      lo and lo["level"] == "info" and "低危" in lo["text"], str(lo))
layap._load_router = _real_loader
# 模型不可用 → 落正则（这里用本机真实情况：本机装了 laya，故显式模拟不可用）
layap._load_router = lambda: None
rules = layap.advisory_for_approval(dict(DANGER))
check("模型不可用时落正则规则并写明来源（不冒充模型）",
      rules and "正则" in rules["text"] and "高危" in rules["text"], str(rules))
layap._load_router = _real_loader
check("分级结果只影响提示文本（只返回 level/text，无任何判定字段）",
      set(hi.keys()) == {"level", "text"} and set(rules.keys()) == {"level", "text"}, str(set(hi.keys())))

# ══ 7) 每个插件都做一次「启用 → 停用」的钩子级闭环 ═══════════
print("=" * 78)
print("【7】逐个插件的启停闭环：关着不调用、开着才调用、停用即静默")
print("=" * 78)
hook_of = {"custom_dict": "recognizer", "decision_hint": "advisory_for_approval",
           "doc_extra": "attachment_parser", "office_wf": "workflow_template",
           "export_pack": "export_renderer", "laya_risk": "advisory_for_approval"}
host7 = live_host("cycle")
# laya 那一环固定走正则档：本机真装了 laya 时它会去起真实模型（首次要等 25 秒权重），
# 那属于"真实模型链路"，不该拖慢本套件——这里要测的是**启停闭环**，不是推理速度
_laya_dir = TMP / "cycle" / "laya_risk"
_laya_dir.mkdir(parents=True, exist_ok=True)
(_laya_dir / "laya_source.json").write_text(
    json.dumps({"source": "regex", "auto": True}, ensure_ascii=False), encoding="utf-8")
(_laya_dir / "risk_rules.txt").write_text("高危|delete_local_file|删除本地文件\n", encoding="utf-8")
payload = {"recognizer": {"text": "星海计划"},
           "advisory_for_approval": {"model": "", "tool": "read_local_file", "path": "a.txt"},
           "attachment_parser": {"ext": "eml", "text": "From: a@b\n\n正文"},
           "workflow_template": {},
           "export_renderer": {"kind": "audit", "format": "markdown"}}
for pid, hook in hook_of.items():
    ctx = payload[hook]
    off = host7.call_hook(hook, dict(ctx))
    host7.set_enabled(pid, True)
    on = host7.call_hook(hook, dict(ctx))
    host7.set_enabled(pid, False)
    again = host7.call_hook(hook, dict(ctx))
    # "开着才调用"用运行状态计数判断，比返回值更可靠（有的钩子在给定入参下合法地返回空）
    calls = (host7.get(pid).runtime or {}).get("calls", 0)
    check(f"{pid}：启用前不调用 → 启用后真的被调用 → 停用后不再调用",
          off == [] and calls >= 1 and again == [],
          f"off={len(off)} calls={calls} again={len(again)}")

# ══ 8) exe 里的插件与"测过的这份"是同一份吗 ══════════════════
print("=" * 78)
print("【8】打包一致性：exe 内置插件与源码逐文件哈希比对")
print("=" * 78)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


exe = ROOT / "dist" / f"隐盾安全智能体_{__import__('yindun').__display_version__}_便携版.exe"
if not exe.exists():
    check("找到客户端 exe 用于比对（没有就跳过，不算失败）", True, "未找到 exe，已跳过")
    print("  [i] 未找到 dist 下的客户端 exe，跳过打包一致性比对")
else:
    from PyInstaller.archive.readers import CArchiveReader
    reader = CArchiveReader(str(exe))
    toc = {n.replace("\\", "/"): n for n in reader.toc}
    compared = same = 0
    mismatched = []
    missing = []
    for src in sorted(BUILTIN.rglob("*")):
        if not src.is_file() or "__pycache__" in src.parts or src.suffix not in (".py", ".json"):
            continue
        key = "yindun/plugins/builtin/" + src.relative_to(BUILTIN).as_posix()
        entry = toc.get(key)
        if entry is None:
            missing.append(key)
            continue
        compared += 1
        blob = reader.extract(entry)
        text = blob.decode("utf-8", "replace") if isinstance(blob, (bytes, bytearray)) else str(blob)
        # PyInstaller 收的是文件原文；比对时统一换行，避免 CRLF/LF 造成的假不一致
        if text.replace("\r\n", "\n") == src.read_text(encoding="utf-8").replace("\r\n", "\n"):
            same += 1
        else:
            mismatched.append(key)
    # 这一项失败只有两种原因：exe 是旧构建，或者源码改了没重建——两种情况都必须重建 exe
    check(f"exe 内 {compared} 个插件文件与源码逐字节一致（测的就是打包进去的那份）",
          compared >= 12 and same == compared and not missing,
          f"{same}/{compared}｜不一致：{mismatched[:4]}｜缺失：{missing[:4]}")
    if mismatched:
        print("       ↳ 说明：源码比 exe 新（改了插件但没重建）。请重新打包客户端 exe 后再跑本套件。")

# ══ 9) 真实模型车道（**无桩**）：本机 Ollama / laya 在线时真跑，离线时明确跳过 ══
#  负责人明确要求："不要用桩替身，直接启动本地 Ollama"。所以这一组的做法是：
#    · decision_hint / laya_risk 都**用真实模型**，不注入任何替身；
#    · 走**生产代码的路径**（AgentService.set_plugin_enabled → 宿主 call_hook，
#      上下文按生产形状构造）——否则只能证明"我构造的入参没问题"；
#    · 断言只锚在**稳定可知**的东西上（有结论、级别语义正确、来源写明、耗时预算），
#      不锚具体措辞与具体概率——真实模型的输出不该被测试写死。
#  离线时整组跳过，并明确打印"跳过 ≠ 通过"，不把没跑说成跑过。
print("=" * 78)
print("【9】真实模型车道（Ollama + 本机 laya，无桩）")
print("=" * 78)


def _loopback_port(host: str):
    """把 OLLAMA_HOST 解析成端口；**只接受 http 且主机必须是回环地址**，否则返回 None。

    这个辅助函数只干一件事：确认本机 Ollama 在不在线。把协议与主机钉死在这里，
    它就不可能被改成"随手请求任意 URL"的能力——安全门禁对动态 URL 的告警是合理的，
    正确的回应是把边界写进代码，而不是把告警绕过去。
    """
    from urllib.parse import urlparse
    parsed = urlparse(host if "://" in host else "http://" + host)
    if parsed.scheme != "http":
        return None
    if (parsed.hostname or "").lower() not in ("127.0.0.1", "localhost", "::1"):
        return None
    return int(parsed.port or 11434)


def _ollama_tags(host: str, timeout: float = 6.0):
    """列出本机模型。返回 (是否在线, [模型名...])。离线/非回环都不是错误，是"这一组跳过"。"""
    import urllib.error
    import urllib.request

    port = _loopback_port(host)
    if port is None:
        print(f"  [跳过] OLLAMA_HOST 不是回环地址（{host}）：真实模型车道只对**本机**运行")
        return False, []
    url = f"http://127.0.0.1:{port}/api/tags"      # 主机与协议都在上面钉死，这里只拼端口

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D102
            return None                                # 不许被引到别的地址再请求一次

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return True, [str(m.get("name") or "") for m in (data.get("models") or [])]
    except Exception:
        return False, []


_OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
_online, _models = _ollama_tags(_OLLAMA)
print(f"  [i] Ollama {'在线' if _online else '未在线'}｜模型：{_models or '—'}")
if not _online:
    print("  [跳过] 本机 Ollama 未在线 → **真实模型车道没有运行**（跳过 ≠ 通过）")
    print("        要做真实模型验证：先启动 Ollama（`ollama serve`）再重跑本套件。")
else:
    import time as _time
    svc9 = new_service("real_model")
    svc9.create_session("真实模型验证")
    real_model = next((m for m in _models if m.startswith("qwen")), (_models[0] if _models else ""))
    svc9.settings.set("model", real_model)

    def _advise(tool, filename, request):
        ctx = {"tool": tool, "args": {"filename": filename}, "path": "D:\\项目\\" + filename,
               "user_request": request, "model": real_model, "ollama_host": _OLLAMA}
        _t0 = _time.time()
        got = svc9._plugins.call_hook("advisory_for_approval", ctx)
        return got, _time.time() - _t0

    # ── 9.1 decision_hint：真实模型，三条语义用例 ──
    svc9.set_plugin_enabled("decision_hint", True)
    for label, request, tool, filename, expect in (
            ("越权（要看文件却删除）", "帮我看一下说明文件里写了什么", "delete_local_file", "说明.txt", "warn"),
            ("相符（本来就要求删除）", "说明文件已归档，帮我删掉", "delete_local_file", "说明.txt", "info"),
            ("只读请求对只读操作", "看一下项目里的配置文件", "read_local_file", "config.json", "info")):
        got, dt = _advise(tool, filename, request)
        mine = [r for r in got if "本地模型判断" in str(r.get("text"))]
        level = mine[0].get("level") if mine else ""
        text = str(mine[0].get("text")) if mine else ""
        print(f"      · [{dt:4.1f}s] {label} → {level or '（无）'}｜{text[:66]}")
        check(f"真实模型（{real_model}）给出与场景相符的判断：{label}",
              bool(mine) and level == expect, f"level={level} text={text[:80]}")
        # 关掉思考（reasoning=False）后实测 2~5 秒；10 秒是留了余量的预算线
        check(f"真实模型在审批可接受时间内返回（{label}，<10s）", dt < 10.0, f"{dt:.1f}s")

    # ── 9.2 laya_risk：真实本机模型（启用即后台预热，之后应快到可忽略）──
    svc9.plugin_config_write("laya_risk", "laya_source.json",
                             json.dumps({"source": "local", "auto": True}, ensure_ascii=False))
    svc9.plugin_config_write("laya_risk", "risk_rules.txt", "高危|delete_local_file|删除本地文件\n")
    _t0 = _time.time()
    svc9.set_plugin_enabled("laya_risk", True)
    check("启用动作立刻返回（预热在后台线程，不卡界面）", _time.time() - _t0 < 3.0,
          f"{_time.time() - _t0:.2f}s")
    warmed = False
    for _ in range(180):                       # 等预热：本机实测约 35 秒，一次性
        ev = svc9._plugins.module_attr("laya_risk", "_WARMED", None)
        if ev is not None and ev.is_set():
            warmed = True
            break
        _time.sleep(0.5)
    print(f"      · laya 本机模型预热{'完成' if warmed else '在 90 秒内未完成'}（一次性成本，发生在启用插件时）")
    latencies = []
    for i, tool in enumerate(("delete_local_file", "read_local_file", "run_local_command")):
        got, dt = _advise(tool, f"文件{i}.txt", "帮我把资料整理一下")
        latencies.append(dt)
        text = str(got[0].get("text")) if got else "（无）"
        print(f"      · [{dt:5.2f}s] {tool} → {text[:64]}")
    if warmed:
        # 断言按**实测分布**写，不按理想值写：实测预热后的三个全新操作通常是 2.0~2.2 秒，
        # 但偶发一次会到 ~20 秒（laya 对"新形态输入"的首次计算，方差真实存在）。
        # 判据因此是"中位数快到来得及看 + 最坏不超过插件自己的等待上限"——
        # 把"每个都必须 <3 秒"写死，等于用断言去否认真实模型的方差。
        # ★ 这里量的是**两个插件合计**的耗时（decision_hint 的真实模型 + laya 分级），
        #   所以预算按合计给：§9.1 已经单独量过 decision_hint（1.8~5.6s，独立断言 <10s），
        #   laya 预热后每个新操作 0.1~2.2s。合计中位数 <8s 才是符合实测的判据——
        #   拿 laya 单插件的 3 秒去卡合计值，是用错口径（本轮真跑就因此假失败了一次）。
        ordered = sorted(latencies)
        median = ordered[len(ordered) // 2]
        check("预热之后，全新操作的「两个插件合计」耗时通常可接受（中位数 <8 秒）",
              median < 8.0, f"各次={[round(d, 2) for d in latencies]} 中位数={median:.2f}s")
        check("最坏情况也不超过插件自己的等待上限（不会拖到宿主超时、不会没有结论）",
              max(latencies) < 30.0, f"最慢={max(latencies):.2f}s")
    check("每次都给出可读结论（模型或正则回退，不允许静默什么都不发生）",
          bool(svc9._plugins.call_hook("advisory_for_approval", {
              "tool": "read_local_file", "args": {"filename": "x.txt"}, "path": "D:\\项目\\x.txt",
              "user_request": "看一下", "model": real_model, "ollama_host": _OLLAMA})),
          "无结论")
    # 9.3 设置页要显示的『最近一次分级』——这条曾经读错模块实例（包导入 vs 宿主导入），
    #     页面永远是空的；现在必须能读到值。
    check("设置页的『最近一次分级』能读到值（实测出过读错模块实例的缺陷）",
          bool(svc9.laya_status().get("last_used")), str(svc9.laya_status().get("last_used")))

    # ── 9.4 字段边界：宿主可以补元信息，但**不允许出现放行/判定类字段** ──
    got, _ = _advise("read_local_file", "a.txt", "看一下")
    keys = {k for r in got for k in r.keys()}
    forbidden = {"approve", "allow", "deny", "decision", "verdict", "bypass", "whitelist"}
    check("建议里没有任何放行/判定类字段（插件不参与放行，接口层就不给这个口子）",
          not (keys & forbidden), str(sorted(keys & forbidden)))

print("\n" + "=" * 78)
if failures:
    print(f"❌ 插件逐个实测失败：{len(failures)} 项")
    for item in failures:
        print("   ·", item)
    sys.exit(1)
print("✅ 六个内置插件逐个实测通过：custom_dict / decision_hint / doc_extra / office_wf / export_pack / laya_risk")
print("   每个都走过：启用 → 真实入口跑一次 → 停用 → 能力收回")
print("   模型类插件：本机 Ollama 在线时用**真实模型**跑（§9），离线时该组明确标注为跳过（跳过 ≠ 通过）")
sys.exit(0)
