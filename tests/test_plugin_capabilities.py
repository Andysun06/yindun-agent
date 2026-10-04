# -*- coding: utf-8 -*-
"""隐盾 · 插件能力端到端回归测试（"万物皆插件"落地验收）

本套件验证的不是"宿主能不能发现插件"，而是**四项能力真的接进了产品链路**：
  1. recognizer       → 补充识别的实体真的进入脱敏（且停用后立刻收回）
  2. workflow_template→ 插件模板真的能注册，高危步骤被内核强制人工审批
  3. attachment_parser→ 插件真的能解析内核不认识的格式，且内核原生格式不被顶替
  4. export_renderer  → 插件真的能渲染导出格式，内核格式始终兜底
外加：能力跟着开关走（停用即收回）、能力变更写入审计。

用法：python tests/test_plugin_capabilities.py   （退出码 0 = 全部通过）
"""
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

TMP = Path(tempfile.mkdtemp(prefix="yindun_cap_test_"))
os.environ["SANDBOX_PATH"] = str(TMP / "sandbox")
(TMP / "sandbox").mkdir(parents=True, exist_ok=True)

from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402
from yindun.core.audit_log import AuditLog  # noqa: E402
from yindun.core.privacy_engine import (  # noqa: E402
    PrivacyEngine, clear_extra_recognizers, list_extra_recognizers,
)

failures = []


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


# ── 1) recognizer：补充识别真的进了脱敏 ──────────────
print("=" * 78)
print("【1】识别增强：插件词表 → 脱敏 → 还原闭环")
print("=" * 78)
clear_extra_recognizers()
svc = new_service("recognizer")
secret = "隐盾专项"
sample = f"会议纪要：{secret} 推进情况，联系人 13800138000，身份证 110101199003072316。"

before, _ = PrivacyEngine().anonymize(sample)
check("未启用插件时，自定义词按原文通过（内核不认识它）", secret in before, before)

res = svc.set_plugin_enabled("custom_dict", True)
check("启用识别插件成功", res.get("ok") is True, str(res))
check("启用后识别器挂到内核扩展点", list_extra_recognizers() == ["plugins"], str(list_extra_recognizers()))
svc.plugin_config_write("custom_dict", "words.txt", f"{secret}\nre:星海[0-9]{{3}}号\n")
read_back = svc.plugin_config_read("custom_dict", "words.txt")
check("词表写入成功且可读回（后面几条都依赖它）",
      read_back.get("ok") is True and secret in (read_back.get("text") or ""), str(read_back))

anon, mapping = PrivacyEngine().anonymize(sample)
check("自定义词被替换为占位符（不再以明文进入模型）", secret not in anon, anon)
check("占位符带 PLUGIN_ 前缀（与内核实体不撞名）", "PLUGIN_CUSTOM_TERM" in anon, anon)
re_anon, _ = PrivacyEngine().anonymize("星海013号项目立项。")
check("正则条目同样生效（re: 星海013号）", "PLUGIN_CUSTOM_TERM" in re_anon, re_anon)
check("内核原有实体照旧脱敏（只增不减）",
      "13800138000" not in anon and "110101199003072316" not in anon, anon)
# 统计要读同一个引擎实例（新实例的统计是空的——这是引擎既有语义，不是缺陷）
engine = PrivacyEngine()
anon2, map2 = engine.anonymize(sample)
check("插件实体计入脱敏统计（审计可见）",
      engine.get_last_stats().get("PLUGIN_CUSTOM_TERM", 0) > 0, str(engine.get_last_stats()))
check("插件实体按「机密」从严分级（未声明分级时不轻视它）",
      engine.get_last_classification().get("机密", {}).get("PLUGIN_CUSTOM_TERM", 0) > 0,
      str(engine.get_last_classification()))
check("插件识别归入「插件识别」分组（审计里一眼看出是多识别出来的）",
      "插件识别" in engine.get_last_group_stats(), str(engine.get_last_group_stats()))
restored = engine.deanonymize(anon2, map2, strict=True)
check("本机还原闭环（原文一字不差地回来）", restored == sample, restored)

# 只增不减：注册识别器不会让内容"免于脱敏"
check("注册识别器后，原本会被脱敏的实体没有被放过",
      "13800138000" not in anon2 and secret not in anon2, anon2)

# 越界区间由内核复核丢弃（插件写坏了也不会造成泄露）
from yindun.core.privacy_engine import register_extra_recognizer  # noqa: E402
register_extra_recognizer("bad_ranges", lambda text: [
    {"start": 0, "end": 10 ** 6, "type": "BAD"},          # 越界
    {"start": -5, "end": 3, "type": "BAD"},               # 负坐标
    {"start": 1, "end": 2, "type": "BAD", "level": "x"},  # 正常范围（类型/分级会被收敛）
])
bad_anon, _ = PrivacyEngine().anonymize("甲乙丙丁")
check("越界/负坐标区间被内核丢弃，只有合法区间被替换",
      bad_anon.count("PLUGIN_") == 1, bad_anon)

svc.set_plugin_enabled("custom_dict", False)
check("停用后识别器被摘掉（能力跟着开关走）",
      "plugins" not in list_extra_recognizers(), str(list_extra_recognizers()))
after, _ = PrivacyEngine().anonymize(sample)
check("停用后自定义词不再脱敏（不是「界面关了、实际还在跑」）", secret in after, after)

# 词表读不出来时必须留痕，不能静默"假装已生效"
svc.set_plugin_enabled("custom_dict", True)
words_file = svc._plugins.data_dir("custom_dict") / "words.txt"
words_file.unlink()
words_file.mkdir()                     # 用一个同名目录制造"读不出来"
import contextlib  # noqa: E402
_log = io.StringIO()
with contextlib.redirect_stdout(_log):
    loud = svc._plugins.call_hook("recognizer", {"text": sample})
check("词表读不出来时明确告警（而不是静默不脱敏）",
      loud == [] and "custom_dict" in _log.getvalue(), _log.getvalue()[:200])
words_file.rmdir()
svc.set_plugin_enabled("custom_dict", False)
clear_extra_recognizers()

# ── 2) workflow_template：插件模板 + 内核强制审批 ────
print("\n" + "=" * 78)
print("【2】工作流模板：插件贡献 → 内核校验 → 强制审批")
print("=" * 78)
svc2 = new_service("workflow")
svc2.set_plugin_enabled("office_wf", True)
templates = {t["id"]: t for t in svc2.workflow_templates()}
check("插件模板已注册", "ext_office_code_audit" in templates and "ext_office_weekly_real" in templates,
      str(list(templates)))
check("插件模板标注了来源（界面据此显示徽标）",
      templates["ext_office_code_audit"]["source"] == "plugin"
      and templates["ext_office_code_audit"]["plugin_id"] == "office_wf",
      str(templates["ext_office_code_audit"]))
check("内核模板仍在（插件不能顶掉内核能力）",
      templates["wf_contract_review"]["source"] == "builtin"
      and templates["wf_security_check"]["source"] == "builtin", str(templates["wf_contract_review"]))

started = svc2.workflow_start("ext_office_code_audit", str(TMP), "测试体检")
check("插件模板能创建实例", started.get("ok") is True, str(started))
steps = {s["name"]: s for s in started["status"]["steps"]}
check("路径变量按插件声明写入上下文（project_path）",
      "project_path" in (started.get("context") or {}), str(started.get("context")))
export_step = steps.get("导出报告")
check("高危步骤（导出文件）即使插件声明 auto，也被内核强制为人工审批",
      export_step and export_step["approval_type"] == "manual" and export_step["forced_approval"] is True,
      str(export_step))
check("非高危步骤保持插件声明的自动执行",
      steps["分析项目结构"]["approval_type"] == "auto", str(steps["分析项目结构"]))
check("实例状态带出插件来源（报告里也能看出是谁贡献的模板）",
      started["status"].get("source_plugin") == "office_wf", str(started["status"].get("source_plugin")))

engine = svc2._workflow()
check("插件不能覆盖内核模板",
      engine.register_external_template({"id": "wf_contract_review", "name": "x",
                                         "steps": [{"tool_name": "read_local_file"}]},
                                        "evil_plugin").get("ok") is False)
check("未知工具的模板被整条拒绝",
      engine.register_external_template({"id": "ext_bad", "name": "x",
                                         "steps": [{"tool_name": "not_a_tool"}]},
                                        "evil_plugin").get("ok") is False)
check("只能卸载插件模板（内核模板不受影响）",
      engine.unregister_external_template("wf_contract_review") is False
      and engine.unregister_external_template("ext_office_code_audit") is True)

svc2.set_plugin_enabled("office_wf", False)
ids = [t["id"] for t in svc2.workflow_templates()]
check("停用插件后它的模板立刻消失", "ext_office_code_audit" not in ids and "ext_office_weekly_real" not in ids,
      str(ids))
check("内核模板不受插件停用影响", {"wf_contract_review", "wf_security_check"} <= set(ids), str(ids))

# ── 3) attachment_parser：插件解析 + 内核兜底 ────────
print("\n" + "=" * 78)
print("【3】附件解析：内核不认识的格式交给插件，内核原生格式不被顶替")
print("=" * 78)
svc3 = new_service("attach")
mail = TMP / "往来邮件.eml"
mail.write_text("From: 张三 <a@b.com>\nSubject: 材料\nMIME-Version: 1.0\n"
                "Content-Type: text/plain; charset=utf-8\n\n请查收 隐盾专项 材料。\n", encoding="utf-8")
plain = TMP / "说明.txt"
plain.write_text("普通文本附件", encoding="utf-8")

added = svc3.attach_files([str(mail), str(plain)])
by_name = {item["name"]: item for item in svc3.list_attachments()}
check("停用插件时可选扩展名里没有它声明的格式",
      "eml" not in svc3.supported_exts(), str(svc3.supported_exts()))
check("停用插件时 .eml 由内核兜底解析（不报错、有文本）",
      added and by_name["往来邮件.eml"]["chars"] > 0
      and by_name["往来邮件.eml"]["via"] == "内核解析",
      str(by_name.get("往来邮件.eml")))

svc3.clear_attachments()
svc3.set_plugin_enabled("doc_extra", True)
check("启用后插件声明的扩展名出现在可选列表里", "eml" in svc3.supported_exts(), str(svc3.supported_exts()))
svc3.attach_files([str(mail), str(plain)])
by_name = {item["name"]: item for item in svc3.list_attachments()}
check("启用插件后 .eml 由插件解析，并标明解析来源",
      by_name["往来邮件.eml"]["via"].startswith("插件解析"), str(by_name["往来邮件.eml"]))
check("插件解析出的正文是真正的正文（不是原始 MIME 源码）",
      "请查收" in (svc3._attachments[0].get("text") or "")
      and "MIME-Version" not in (svc3._attachments[0].get("text") or ""),
      (svc3._attachments[0].get("text") or "")[:120])
check("内核原生格式（.txt）仍以内核为准，不被插件顶替",
      by_name["说明.txt"]["via"] == "内核解析", str(by_name["说明.txt"]))
check("插件只被自己声明的扩展名唤醒",
      all(not (item.get("via", "").startswith("插件解析") and item["name"].endswith(".txt"))
          for item in by_name.values()), str(by_name))

# ── 4) export_renderer：插件渲染 + 内核兜底 ─────────
print("\n" + "=" * 78)
print("【4】导出格式：插件渲染，内核格式始终兜底")
print("=" * 78)
svc4 = new_service("export")
base_formats = [f["format"] for f in svc4.audit_export_formats()]
check("未启用插件时，审计导出只有内核格式", base_formats == ["json", "html"], str(base_formats))
check("未启用插件时，工作流导出只有内核格式",
      [f["format"] for f in svc4.workflow_export_formats()] == ["md", "html", "docx"],
      str(svc4.workflow_export_formats()))

svc4.set_plugin_enabled("export_pack", True)
audit_formats = {f["format"]: f for f in svc4.audit_export_formats()}
check("插件格式出现在审计导出清单里，并标注来源",
      "markdown" in audit_formats and audit_formats["markdown"]["source"] == "导出格式扩展",
      str(audit_formats))
check("内核格式没有被插件替换掉",
      audit_formats["json"]["source"] == "内核" and audit_formats["html"]["source"] == "内核",
      str(audit_formats))
path = svc4.export_audit("markdown")
check("插件格式真的能导出成文件", isinstance(path, str) and path.endswith(".md"), path)
rendered = Path(path).read_text(encoding="utf-8") if Path(path).is_file() else ""
check("导出内容像一份报告（标题 + 表格 + 链完整性）",
      "隐盾安全审计报告" in rendered and "|" in rendered and "审计链完整性" in rendered,
      rendered[:120])
check("导出落点在审计目录内（插件无法借导出写到别处）",
      Path(path).parent.name == Path(AuditLog()._storage_path).name, path)

wf_started = svc4.workflow_start("wf_contract_review", str(TMP), "导出测试")
instance_id = wf_started.get("instance_id")
check("插件格式名带中文标签（界面菜单直接可读）",
      any(f["format"] == "html_card" and "卡片式 HTML" in f["label"]
          for f in svc4.workflow_export_formats()),
      str(svc4.workflow_export_formats()))
html_path = svc4.workflow_export(instance_id, "html_card")
check("工作流导出可用插件格式（卡片式 HTML）",
      html_path.get("ok") is True and html_path.get("renderer", "").startswith("插件"), str(html_path))
html_text = Path(html_path.get("path", "")).read_text(encoding="utf-8") if html_path.get("ok") else ""
check("插件渲染的工作流报告是完整 HTML",
      html_text.startswith("<!DOCTYPE html>") and "</html>" in html_text, html_text[:80])
md_path = svc4.workflow_export(instance_id, "md")
check("内核格式仍然可用（插件停用也不影响导出）",
      md_path.get("ok") is True and md_path.get("renderer") == "内核", str(md_path))

svc4.set_plugin_enabled("export_pack", False)
check("停用插件后它的格式从清单消失",
      [f["format"] for f in svc4.audit_export_formats()] == ["json", "html"],
      str(svc4.audit_export_formats()))
check("停用插件后内核格式照旧可用", svc4.export_audit("json").endswith(".json"), svc4.export_audit("json"))

# ── 5) 能力变更留痕 ────────────────────────────────
print("\n" + "=" * 78)
print("【5】能力变更留痕（启停与词表改动都写审计）")
print("=" * 78)
entries = AuditLog().get_entries() or []
messages = [str(getattr(e, "message", "") or "") for e in entries]
check("插件启用写入审计", any("插件启用：custom_dict" in m for m in messages), str(messages[-5:]))
check("插件配置（词表）改动写入审计",
      any("插件配置更新：custom_dict/words.txt" in m for m in messages), str(messages[-5:]))
details = []
for entry in entries:
    detail = getattr(entry, "details", None)
    if isinstance(detail, dict) and detail.get("plugin_id") == "custom_dict":
        details.append(detail)
check("审计详情带出能力同步结果（模板注册数 / 识别器是否生效）",
      any("recognizer_active" in d or "templates_registered" in d for d in details), str(details[:2]))

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：识别/模板/解析/导出四项能力真的接进了产品链路，且能力跟着开关走")
sys.exit(0)
