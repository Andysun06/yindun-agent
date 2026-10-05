# -*- coding: utf-8 -*-
"""隐盾 · 真实格式文档评测（PDF / DOCX / XLSX / EML / HTML / CSV / TXT / MD）

评审意见整改："扩大真实文档与对抗性测试集"——此前语料以纯文本合成为主。
本套件在临时目录生成 8 种**真实格式**的含密文档，走**产品真实管线**
（AgentService.attach_files：内核解析器 + 插件解析器 → 脱敏网关 → 会话落盘）验证：

  1. 解析成功；「隐私风险报告」与正文分离（报告里的部分掩码片段不得进入模型上下文）；
  2. 送入模型前敏感实体全部替换为占位符（完整字面值 + 纯数字形态都不得残留）；
  3. 还原往返与原文逐字一致；
  4. 业务数字负样本（订单号/版本号）零误伤；
  5. 会话文件落盘无明文（含手机号/身份证/邮箱/卡号的全部形态）。

说明：文档为程序化生成（非真实业务文档），但解析/脱敏/落盘链路全部真实；
"零泄露"结论仍只对本评测集成立，不泛化为真实环境保证。

用法：python tests/privacy_docs_eval_test.py   （退出码 0 = 全部通过）
"""
import csv
import email.message
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402
from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def _luhn_ok(prefix: str) -> str:
    digits = [int(c) for c in reversed(prefix)]
    total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 == 0 else d
                for i, d in enumerate(digits))
    return prefix + str((10 - total % 10) % 10)


CARD = _luhn_ok("622288888888888")          # Luhn 合法
PHONE_LITERAL, PHONE_DIGITS = "138-1234-5678", "13812345678"
ID_LITERAL = "110101199001011234"
EMAIL = "zhang.wei@example.com"
NEGATIVES = ("1234567890123456", "v3.14.159")
ENTITY_LITERALS = (PHONE_LITERAL, ID_LITERAL, EMAIL, CARD)
ENTITY_DIGITS = (PHONE_DIGITS, ID_LITERAL, CARD)

LINES = [
    "员工信息台账",
    f"手机：{PHONE_LITERAL}",
    f"身份证：{ID_LITERAL}",
    f"邮箱：{EMAIL}",
    f"银行卡：{CARD}",
    f"订单号：{NEGATIVES[0]}",
    f"版本：{NEGATIVES[1]}",
]

TMP = Path(tempfile.mkdtemp(prefix="yindun_docs_eval_"))
engine = PrivacyEngine()


def build_docs() -> dict:
    """生成 8 种真实格式的含密文档（全部落在测试临时目录内）。"""
    docs = {}
    body = "\n".join(LINES)

    p = TMP / "doc.txt"; p.write_text(body, encoding="utf-8"); docs["txt"] = p
    p = TMP / "doc.md"; p.write_text("# 台账\n\n" + body, encoding="utf-8"); docs["md"] = p
    p = TMP / "doc.csv"
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for line in LINES:
            w.writerow(["字段", line])
    docs["csv"] = p
    p = TMP / "doc.html"
    p.write_text("<html><body>" + "".join(f"<p>{l}</p>" for l in LINES) + "</body></html>",
                 encoding="utf-8")
    docs["html"] = p
    p = TMP / "doc.eml"
    msg = email.message.EmailMessage()
    msg["Subject"] = "员工台账"
    msg["From"] = "hr@corp.cn"
    msg["To"] = "audit@corp.cn"
    msg.set_content(body)
    p.write_bytes(msg.as_bytes())
    docs["eml"] = p

    import docx
    d = docx.Document()
    for line in LINES:
        d.add_paragraph(line)
    p = TMP / "doc.docx"; d.save(str(p)); docs["docx"] = p

    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    for line in LINES:
        ws.append(["字段", line])
    p = TMP / "doc.xlsx"; wb.save(str(p)); docs["xlsx"] = p

    import pymupdf
    pdf = pymupdf.open()
    page = pdf.new_page()
    y = 90
    for line in LINES:
        page.insert_text((72, y), line, fontname="china-s")
        y += 26
    p = TMP / "doc.pdf"; pdf.save(str(p)); pdf.close(); docs["pdf"] = p
    return docs


print("=" * 78)
print("【真实格式文档评测】解析 → 报告分离 → 脱敏 → 还原 → 落盘")
print("=" * 78)
docs = build_docs()

svc = AgentService(settings=SettingsStore(path=TMP / "cfg.json"),
                   sessions=SessionStore(path=TMP / "sess.json"),
                   plugin_data_root=TMP / "plugins_data")
svc.initialize()
svc.set_plugin_enabled("doc_extra", True)     # eml / html 由插件解析（与真实启停一致）

for fmt, path in docs.items():
    svc.clear_attachments()
    added = svc.attach_files([str(path)])
    rec = dict(svc._attachments[-1]) if svc._attachments else {}   # 完整记录（含正文/报告）
    text = rec.get("text") or ""
    ok_parse = (not added or added[0].get("error") is None) and bool(text.strip())
    check(f"[{fmt}] 解析成功（真实解析链路）", ok_parse,
          f"error={rec.get('error')} chars={len(text)}")
    if not ok_parse:
        continue

    check(f"[{fmt}] 隐私风险报告与正文分离（部分掩码片段不进模型）",
          "【隐私风险报告】" not in text)

    masked, mapping = engine.anonymize(text)
    digits_only = re.sub(r"\D", "", masked)
    leaked = [x for x in ENTITY_LITERALS if x in masked] + \
             [x for x in ENTITY_DIGITS if x in digits_only]
    check(f"[{fmt}] 脱敏后无任何实体明文（字面值 + 纯数字形态）", not leaked, str(leaked))

    restored = engine.deanonymize(masked, mapping)
    check(f"[{fmt}] 还原往返与原文逐字一致", restored == text,
          f"{len(restored)} vs {len(text)}")

    neg_ok = all(n in masked for n in NEGATIVES)
    check(f"[{fmt}] 业务数字负样本零误伤（订单号 / 版本号）", neg_ok)

# ── 送模型链路：模型输入不得含实体明文，也不得含部分掩码片段 ──
captured = {}


class CapturingLLM:
    def invoke(self, messages):
        captured["messages"] = messages
        return type("M", (), {"content": "已汇总。"})()

    def bind_tools(self, tools):
        return self


svc._llm = CapturingLLM()
svc._tools_map = {}
svc.send("请把台账里的联系方式和卡号汇总一下（不要输出原文）")
deadline = time.time() + 60
while time.time() < deadline and svc.busy:
    time.sleep(0.05)
flatten = " ".join(str(getattr(m, "content", m)) for m in captured.get("messages", []))
model_clean = not any(x in flatten for x in ENTITY_LITERALS + ENTITY_DIGITS)
check("送入模型的上下文无实体明文", model_clean)
check("模型上下文无部分掩码片段（138****5678 / 1101**********1234）",
      ("138****5678" not in flatten) and ("1101**********1234" not in flatten))

raw_session = (TMP / "sess.json").read_text(encoding="utf-8")
disk_clean = not any(x in raw_session for x in ENTITY_LITERALS + ENTITY_DIGITS + NEGATIVES)
check("会话文件落盘无实体明文（8 种格式全部）", disk_clean)

print("\n" + "=" * 78)
if failures:
    print(f"❌ 真实格式文档评测失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print(f"✅ 真实格式文档评测通过：{len(docs)} 种格式（pdf/docx/xlsx/eml/html/csv/txt/md）"
      f"解析/脱敏/还原/落盘全部符合预期")
sys.exit(0)
