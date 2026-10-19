# -*- coding: utf-8 -*-
"""隐盾 · 附件与文档格式能力回归（V3.6.7）

为什么要有它：负责人问过「附件能不能正常上传各种格式并让模型获取理解，尤其是图片、文件」，
组员的审查报告又报了「知识库/附件弹窗打不开」。两条合起来暴露出**三件此前没人测过的事**：

  1. **对话框过滤器是"会抛异常的字符串"**：pywebview 只认分号分隔（`文档 (*.pdf;*.docx)`），
     空格分隔会被它的正则判为非法并直接抛错——表现是"点添加文档/附件，弹窗根本不出现"。
     这类"格式字符串"缺陷只有**过一遍它自己的校验器**才拦得住，肉眼看代码是看不出来的；
  2. **格式能力从来是"文档说了算"**：实测才发现图片/扫描件/pptx/老版 xls 根本读不到，
     而失败还会被当成"解析成功的内容"（`chars` 把失败提示算进去、模型收到那句提示当正文）；
  3. **附件一多就撑爆上下文**：单篇上限 3 万字，但没人管一轮注入的**总量**。

本套件把这三件事钉死，且**完全离线**（不依赖 Ollama、不发网络请求）：
  A. 过滤器必须过 pywebview 校验器（两个入口都用同一条生成逻辑）；
  B. 格式矩阵：文本家族/xlsx/docx/pptx/pdf 能出内容；图片/压缩包/二进制给出**可行动的原因**；
  C. 失败判定结构化：`error` 非空、`chars == 0`、模型上下文里出现原因而**不是**那句提示原文；
  D. 附件注入总量有预算，未全文注入的附件在上下文里仍有名字与检索指引。

用法：python tests/test_document_formats.py   （退出码 0 = 全部通过）
"""
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures = []
TMP = Path(tempfile.mkdtemp(prefix="yindun_fmt_test_"))


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ══════════════════════════════════════════════════════════════════════
# A. 文件对话框过滤器：必须过 pywebview 自己的校验器
# ══════════════════════════════════════════════════════════════════════
print("=" * 78)
print("【A】文件对话框过滤器（空格分隔会让弹窗直接打不开）")
print("=" * 78)
try:
    from webview.util import parse_file_type
except Exception as exc:                                            # pragma: no cover
    print(f"  ⚠ 取不到 pywebview 校验器（{exc}）；这条判据只能跳过——**跳过 ≠ 通过**")
    parse_file_type = None

from yindun.app.attachment import SUPPORTED_EXTS                     # noqa: E402
from yindun.app.webview_app import doc_file_types                    # noqa: E402

types = doc_file_types(SUPPORTED_EXTS)
check("两个入口共用同一条生成逻辑（附件与知识库不会再各写一套）",
      isinstance(types, tuple) and len(types) == 2 and types[1].startswith("所有文件"))
if parse_file_type is not None:
    ok_all = True
    for item in types:
        try:
            parse_file_type(item)
        except Exception as exc:                                     # noqa: BLE001
            ok_all = False
            check(f"过滤器合法：{item[:48]}…", False, f"{type(exc).__name__}: {exc}")
    check("生成的过滤器全部通过 pywebview 校验（分号分隔）", ok_all)
    # 反证：空格写法必须被判非法——万一有人"顺手改回空格"，这条会先红
    try:
        parse_file_type("文档 (*.pdf *.docx)")
        check("空格分隔确实会被判非法（说明这条断言有意义）", False, "pywebview 居然接受了空格写法")
    except Exception:
        check("空格分隔确实会被判非法（说明这条断言有意义）", True)
src = (ROOT / "yindun" / "app" / "webview_app.py").read_text(encoding="utf-8")
check("入口里不再有空格拼接过滤器的写法（死代码已清）",
      '".join(f"*.{' not in src.replace('";".join(f"*.', ''))

# ══════════════════════════════════════════════════════════════════════
# B/C. 格式矩阵 + 失败判定（离线造样例）
# ══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("【B】格式矩阵：能读的读到内容，读不了的给出可行动原因")
print("=" * 78)
samples = {
    "txt": TMP / "样例.txt", "md": TMP / "样例.md", "csv": TMP / "样例.csv",
    "json": TMP / "样例.json", "html": TMP / "样例.html", "log": TMP / "样例.log",
    "yaml": TMP / "样例.yaml", "xml": TMP / "样例.xml",
}
samples["txt"].write_text("甲方：张伟 电话 13812345678", encoding="utf-8")
samples["md"].write_text("# 标题\n**粗体** 正文", encoding="utf-8")
samples["csv"].write_text("姓名,金额\n张伟,1000", encoding="utf-8")
samples["json"].write_text(json.dumps({"k": "v", "n": 1}, ensure_ascii=False), encoding="utf-8")
samples["html"].write_text("<h1>标题</h1><p>正文</p>", encoding="utf-8")
samples["log"].write_text("2026-10-19 INFO selftest", encoding="utf-8")
samples["yaml"].write_text("key: value\nlist:\n  - a", encoding="utf-8")
samples["xml"].write_text("<root><item>内容</item></root>", encoding="utf-8")

made = []
try:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.append(["姓名", "金额"]); ws.append(["张伟", 1000])
    samples["xlsx"] = TMP / "样例.xlsx"; wb.save(samples["xlsx"])
except Exception as exc:                                            # noqa: BLE001
    print(f"  ⚠ 跳过 xlsx：{exc}")
try:
    import docx
    d = docx.Document(); d.add_paragraph("合同正文：甲方张伟 金额 120000"); 
    samples["docx"] = TMP / "样例.docx"; d.save(samples["docx"])
except Exception as exc:                                            # noqa: BLE001
    print(f"  ⚠ 跳过 docx：{exc}")
try:
    from pptx import Presentation
    pr = Presentation(); s = pr.slides.add_slide(pr.slide_layouts[5]); s.shapes.title.text = "季度汇报 Q3"
    samples["pptx"] = TMP / "样例.pptx"; pr.save(samples["pptx"])
except Exception as exc:                                            # noqa: BLE001
    print(f"  ⚠ 跳过 pptx：{exc}")
try:
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (400, 120), "white"); ImageDraw.Draw(im).text((15, 45), "SCAN 2026", fill="black")
    samples["png"] = TMP / "样例.png"; im.save(samples["png"])
except Exception as exc:                                            # noqa: BLE001
    print(f"  ⚠ 跳过 png：{exc}")
samples["zip"] = TMP / "样例.zip"
with zipfile.ZipFile(samples["zip"], "w") as z:
    z.writestr("inner.txt", "x")
samples["bin"] = TMP / "样例.xyz"
samples["bin"].write_bytes(b"\x00\x01\x02binary\x00")

import yindun.utils.document_parser as DP                                 # noqa: E402
is_fail = getattr(DP, "is_parse_failure", None)
check("解析器提供结构化失败判定（不再靠各处自己比字符串前缀）", callable(is_fail))
for ext in ("txt", "md", "csv", "json", "html", "log", "yaml", "xml", "xlsx", "docx", "pptx"):
    path = samples.get(ext)
    if not path or not path.exists():
        continue
    out = DP.extract_file_text(str(path)) or ""
    failed = is_fail(out) if is_fail else out.startswith("[文档解析")
    check(f"{ext} 能提取到内容（不是失败串）", not failed and len(out.strip()) > 0, out[:70])
if samples.get("pptx") and samples["pptx"].exists():
    ppt_txt = DP.extract_file_text(str(samples["pptx"])) or ""
    check("pptx 按页提取并标出页号（模型能引用「第 N 页」）",
          "第 1 页" in ppt_txt and "季度汇报" in ppt_txt, ppt_txt[:80])
for ext, why in (("zip", "压缩包"), ("bin", "二进制")):
    path = samples.get(ext)
    if not path or not path.exists():
        continue
    out = DP.extract_file_text(str(path)) or ""
    failed = is_fail(out) if is_fail else out.startswith("[文档解析")
    check(f"{ext}（{why}）被判定为失败且给出可行动原因",
          failed and ("解压" in out or "文本版" in out), out[:90])

# 图片：本机有 OCR 后端时**必须真的读出内容**（负责人最在意的一条）；
# 只有"一个后端都没有"（例如非 Windows 且没装 rapidocr）时才允许"如实说明需要 OCR 组件"。
from yindun.utils.ocr_engine import available_engines                     # noqa: E402
_engines = available_engines()
big_img = TMP / "扫描件.png"
try:
    from PIL import Image as _I, ImageDraw as _D, ImageFont as _F
    _font = None
    for _cand in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        if Path(_cand).exists():
            _font = _F.truetype(_cand, 34)
            break
    _im = _I.new("RGB", (900, 240), "white")
    _dd = _D.Draw(_im)
    _dd.text((30, 40), "甲方：张伟 联系电话：13812345678", fill="black", font=_font)
    _dd.text((30, 110), "合同编号 CONTRACT NO 2026-888", fill="black", font=_font)
    _im.save(big_img)
    img_ok = True
except Exception as _exc:                                                  # noqa: BLE001
    img_ok = False
    print(f"  ⚠ 造图片样例失败：{_exc}")
if img_ok:
    img_out = DP.extract_file_text(str(big_img)) or ""
    img_failed = is_fail(img_out) if is_fail else img_out.startswith("[文档解析")
    if _engines:
        squeezed = img_out.replace(" ", "")
        check(f"图片能真的读出文字（本机引擎：{'、'.join(_engines)}）",
              (not img_failed) and ("13812345678" in squeezed or "CONTRACT" in img_out.upper()),
              img_out[:140])
        check("识别结果里写明用的是哪个引擎（不让人猜）",
              "OCR" in img_out and any(e.split()[0] in img_out for e in _engines), img_out[:90])
    else:
        check("本机没有 OCR 后端时，图片给出可行动原因（而不是假装读到）",
              img_failed and "OCR" in img_out, img_out[:120])

# ══════════════════════════════════════════════════════════════════════
# C. 集成：附件的 chars/error 与"模型真正拿到什么"
# ══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("【C】集成链路：失败不再被当成内容（chars/error/模型上下文）")
print("=" * 78)
from yindun.app.attachment import build_attachment_context                # noqa: E402
from yindun.app.agent_service import AgentService                         # noqa: E402
from yindun.app.session_store import SessionStore                         # noqa: E402
from yindun.app.settings_store import SettingsStore                       # noqa: E402
import os                                                                 # noqa: E402

os.environ["SANDBOX_PATH"] = str(TMP)
os.environ["YINDUN_LOG_DIR"] = str(TMP / "logs")
svc = AgentService(settings=SettingsStore(path=TMP / "cfg.json"),
                   sessions=SessionStore(path=TMP / "sess.json"),
                   plugin_data_root=TMP / "plugins_data")
svc.initialize()
summaries = {}
for ext in ("txt", "docx", "pptx", "png", "zip"):
    path = samples.get(ext)
    if not path or not path.exists():
        continue
    for row in svc.attach_files([str(path)]):
        summaries[ext] = row
check("图片附件：error 有原因、chars 为 0（不再把失败提示算成内容）",
      summaries.get("png") is not None and summaries["png"]["chars"] == 0
      and bool(summaries["png"].get("error")), str(summaries.get("png")))
check("压缩包附件同样如实报错（而不是静默跳过）",
      summaries.get("zip") is not None and summaries["zip"]["chars"] == 0
      and bool(summaries["zip"].get("error")), str(summaries.get("zip")))
check("能读的附件仍然正常计入字数",
      all(summaries.get(e) and summaries[e]["chars"] > 0 and not summaries[e].get("error")
          for e in ("txt", "docx", "pptx")), str({k: v["chars"] for k, v in summaries.items()}))
ctx, snapshot = build_attachment_context("这些材料说了什么？", svc._attachments)
check("模型上下文里不再出现旧的失败提示原文（那句话会被模型当成文件内容）",
      "二进制/不可解码文件" not in ctx, ctx[:120])
check("失败附件在上下文里带上可读原因（模型知道「读不到，因为什么」）",
      "未能提取到文本" in ctx and ("OCR" in ctx or "解压" in ctx), ctx[:200])

# ══════════════════════════════════════════════════════════════════════
# D. 附件注入总量预算
# ══════════════════════════════════════════════════════════════════════
print("\n" + "=" * 78)
print("【D】附件注入总量预算（一篇篇都塞满会撑爆模型窗口）")
print("=" * 78)
big1 = TMP / "长文A.txt"; big1.write_text("A 段内容\n" + "填充" * 8000, encoding="utf-8")   # ≈3.2 万字
big2 = TMP / "长文B.txt"; big2.write_text("B 段内容\n" + "填充" * 8000, encoding="utf-8")
import importlib                                                          # noqa: E402
import yindun.app.attachment as ATT                                       # noqa: E402
importlib.reload(ATT)                                                     # 清掉上一段累积的快照
budget_ctx, _snap = ATT.build_attachment_context(
    "把这两篇都看看", [{"name": "长文A.txt", "text": big1.read_text(encoding="utf-8")},
                       {"name": "长文B.txt", "text": big2.read_text(encoding="utf-8")}])
total_budget = getattr(ATT, "MAX_TOTAL_DOC_CHARS", None)
check("单篇上限仍然在（超过就提示按段读）", getattr(ATT, "MAX_DOC_CHARS", 0) > 0)
check("有一轮注入的**总量**上限（不再只限单篇）", isinstance(total_budget, int) and total_budget > 0,
      str(total_budget))
if isinstance(total_budget, int) and total_budget > 0:
    body = budget_ctx.split("[人类当前实时提问]", 1)[0]
    check(f"两篇长文一起注入时总量不超预算（{total_budget} 字）", len(body) <= total_budget + 2000,
          f"实际 {len(body)} 字")
    check("未能全文注入的附件在上下文里仍有名字与检索指引（不会被悄悄丢掉）",
          budget_ctx.count("长文") >= 2 and "read_attachment_chunk" in budget_ctx,
          budget_ctx[-260:])
check("快照里保留的是原文（工具检索用）",
      all(len(v) > 0 for v in _snap.values()) and len(_snap) == 2, str({k: len(v) for k, v in _snap.items()}))

print("\n" + "=" * 78)
if failures:
    print(f"❌ 附件/文档格式回归失败：{len(failures)} 项")
    for item in failures:
        print("   ·", item)
    sys.exit(1)
print(f"✅ 附件与格式能力回归通过（临时目录 {TMP}）")
sys.exit(0)
