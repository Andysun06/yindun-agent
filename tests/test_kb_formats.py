# -*- coding: utf-8 -*-
"""隐盾 · 知识库导入格式与失败可见性回归（不依赖 Ollama / Chroma 服务）

为什么单独一套：知识库"入库"这条链路过去的真实事故不是"少收了几种格式"，而是
**把解析器的失败提示当成正文收了**——.png / .pptx / .zip / .doc、超过大小上限的文件、
纯图片 PDF，`extract_file_text` 返回的是一句"[文档解析失败: …]"提示，旧实现只看
"文本非空 + 不以 [文档解析失败 开头"，于是它们被计为"成功入库"，用户看到"入库完成"，
向量库里却只有那句提示语。这套用例钉住四件事：

  1. **接受范围与解析器能力一致**：解析器判失败（is_parse_failure）→ KB 必须记为失败；
     解析器给出正文 → KB 必须收（json/html/log/yaml/xml 这类不走白名单）。
  2. **失败可见**：批量入库逐条给出失败文件与可读原因（summary/details），不静默丢。
  3. **不出假成功**：任何"成功"入库的内容里都不能是解析器的失败提示语。
  4. **重复导入语义**：同名文档只保留一份（覆盖），且向量化失败时旧版本不能丢。

向量库用内存替身（FakeStore）：本套件**不需要 Ollama**，embedding 缺失走提示路径断言。
样例文件全部造在临时目录，不落仓库。用法：python tests/test_kb_formats.py（退出码 0 = 通过）
"""
import os
import sys
import tempfile
import zipfile

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

try:
    import langchain_text_splitters  # noqa: F401  （mask_and_split 的真实依赖）
    _HAVE_SPLITTER = True
except ImportError:
    _HAVE_SPLITTER = False

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


if not _HAVE_SPLITTER:
    print("[跳过] 未安装 langchain-text-splitters，知识库文本管线不可用；本套件不适用。")
    sys.exit(0)

from yindun.core.knowledge_base import KnowledgeBase  # noqa: E402
from yindun.utils import document_parser  # noqa: E402
from yindun.utils.document_parser import extract_file_text, is_parse_failure  # noqa: E402


# ──────────────────────────────────────────────
# 向量库替身：只实现 KB 真正用到的接口（add_texts / collection.get/delete/count）
# ──────────────────────────────────────────────
class FakeCollection:
    def __init__(self, store):
        self._store = store

    def get(self, where=None, include=None):
        rows = self._store.rows
        if where and "source" in where:
            rows = [r for r in rows if r["metadata"].get("source") == where["source"]]
        out = {"ids": [r["id"] for r in rows]}
        if include is None or "metadatas" in (include or []):
            out["metadatas"] = [dict(r["metadata"]) for r in rows]
        return out

    def delete(self, ids):
        wanted = set(ids or [])
        self._store.rows = [r for r in self._store.rows if r["id"] not in wanted]

    def count(self):
        return len(self._store.rows)


class FakeStore:
    """内存向量库替身：按 id upsert（与 Chroma 的 add_texts 一致），可注入失败。"""

    def __init__(self, fail_with=None):
        self.rows = []
        self.calls = 0
        self.fail_with = fail_with
        self._collection = FakeCollection(self)

    def add_texts(self, texts, metadatas, ids):
        self.calls += 1
        if self.fail_with is not None:
            raise self.fail_with
        index = {r["id"]: r for r in self.rows}
        for text, meta, cid in zip(texts, metadatas, ids):
            index[cid] = {"id": cid, "text": text, "metadata": meta}
        self.rows = list(index.values())

    def texts(self, source=None):
        return [r["text"] for r in self.rows
                if source is None or r["metadata"].get("source") == source]

    def sources(self):
        return sorted({r["metadata"].get("source") for r in self.rows})


def make_kb(tmp, store, name="kb"):
    """建一个用 FakeStore 的 KnowledgeBase：不初始化 Chroma / Ollama。"""
    kb = KnowledgeBase(persist_dir=os.path.join(tmp, name))
    kb._ensure_ready = lambda: None    # 绕过真实依赖与 embedding 初始化
    kb._embeddings = object()
    kb._vectorstore = store
    return kb


def make_samples(tmp):
    """造样例文件（全在临时目录）。返回 {简称: 路径}。"""
    s = {}
    base = os.path.join(tmp, "samples")
    os.makedirs(base, exist_ok=True)

    def write(name, text):
        path = os.path.join(base, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    s["txt"] = write("合同.txt", "项目联系人电话 13800138000，本文用于入库格式回归。" * 3)
    s["md"] = write("说明.md", "# 标题\n\n正文若干，用于测试 markdown。")
    s["csv"] = write("表.csv", "姓名,电话\n张伟,13800138000\n")
    s["json"] = write("配置.json", '{"name": "yindun", "note": "知识与配置"}')
    s["html"] = write("页面.html", "<html><body><p>离线页面正文</p></body></html>")
    s["log"] = write("运行.log", "2026-10-16 10:00:00 INFO 知识库启动\n")
    s["yaml"] = write("清单.yaml", "kb:\n  collection: yindun_kb\n")
    s["xml"] = write("数据.xml", "<root><item>离线数据</item></root>")
    s["empty"] = write("空文件.txt", "")
    s["blank"] = write("全空白.txt", "   \n\t\n")

    png = os.path.join(base, "截图.png")
    with open(png, "wb") as f:
        f.write(bytes.fromhex("89504e470d0a1a0a") + b"\x00\x00\x00\x0dIHDR" + b"\x00" * 32)
    s["png"] = png

    jpg = os.path.join(base, "照片.jpg")
    with open(jpg, "wb") as f:
        f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 48)
    s["jpg"] = jpg

    doc = os.path.join(base, "旧合同.doc")
    with open(doc, "wb") as f:
        f.write(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64)
    s["doc"] = doc

    zpath = os.path.join(base, "打包.zip")
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("inside.txt", "压缩包内文件")
    s["zip"] = zpath

    broken = os.path.join(base, "坏演示.pptx")
    with zipfile.ZipFile(broken, "w") as z:
        z.writestr("junk.xml", "<x/>")
    s["broken_pptx"] = broken

    mp3 = os.path.join(base, "录音.mp3")
    with open(mp3, "wb") as f:
        f.write(b"ID3\x03\x00\x00\x00" + b"\x00" * 64)
    s["mp3"] = mp3

    big = os.path.join(base, "超大.txt")
    with open(big, "w", encoding="utf-8") as f:
        f.write("x" * 8192)          # 尺寸上限在 §1 里临时改小，避免写 50MB 样例
    s["oversize"] = big

    s["dir"] = os.path.join(base, "子目录")
    os.makedirs(s["dir"], exist_ok=True)
    return s


tmp_root = tempfile.mkdtemp(prefix="kb_formats_")
samples = make_samples(tmp_root)

print("=" * 78)
print("【1】接受范围：KB 判定必须与解析器能力一致（不收失败提示、不挡可读文本）")
print("=" * 78)
# 尺寸上限临时改小：只为覆盖"超限拒绝"分支，不必真写 50MB 样例
_old_max = document_parser.MAX_FILE_SIZE
document_parser.MAX_FILE_SIZE = 4096
try:
    kb = make_kb(tmp_root, FakeStore(), "kb1")
    order = ["txt", "md", "csv", "json", "html", "log", "yaml", "xml",
             "empty", "blank", "oversize", "dir", "png", "jpg", "doc", "zip",
             "broken_pptx", "mp3"]
    accept_expected = {"txt", "md", "csv", "json", "html", "log", "yaml", "xml"}
    for key in order:
        path = samples[key]
        parser_text = extract_file_text(path)
        parser_failed = bool(is_parse_failure(parser_text)) or not (parser_text or "").strip()
        try:
            stats = kb.add_document(path)
            ok, reason, stored = True, "", kb._vectorstore.texts(os.path.basename(path))
        except Exception as exc:
            ok, reason, stored = False, f"{type(exc).__name__}: {exc}", []
        check(f"{key}: KB 判定与解析器一致（解析器{'失败' if parser_failed else '出正文'}→"
              f"KB {'失败' if not ok else '入库'}）", ok != parser_failed,
              f"ok={ok} parser_failed={parser_failed} reason={reason[:120]}")
        if ok:
            # 绝不能把解析器的失败提示当正文收进来
            check(f"{key}: 入库内容不是失败提示语",
                  all(not is_parse_failure(t) and t.strip() for t in stored),
                  str(stored)[:160])
        else:
            check(f"{key}: 失败原因可读（非空且交代下一步）", len(reason) >= 12, reason)
        if key in accept_expected:
            check(f"{key}: 解析器能读的格式必须收下", ok, reason)
        if not ok and key in {"oversize"}:
            check(f"{key}: 超限原因指出大小上限", "大小上限" in reason or "50MB" in reason, reason)
        if not ok and key in {"empty", "blank"}:
            check(f"{key}: 空文件原因明确", "没有解析出任何文本" in reason, reason)
        if not ok and key == "dir":
            check(f"{key}: 目录给出「不支持目录」的可读提示", "目录" in reason, reason)
finally:
    document_parser.MAX_FILE_SIZE = _old_max

# 目录样例还应有可行动说法；缺失路径必须报"文档不存在"
try:
    kb.add_document(os.path.join(tmp_root, "不存在.txt"))
    check("缺失路径报错", False, "未抛异常")
except FileNotFoundError:
    check("缺失路径报错", True)

print("\n" + "=" * 78)
print("【2】失败可见：批量入库逐条报数（哪些没进库、为什么）")
print("=" * 78)
store = FakeStore()
kb = make_kb(tmp_root, store, "kb2")
events = []
res = kb.add_documents([samples["txt"], samples["png"], samples["empty"], samples["dir"]],
                       progress_callback=lambda i, t, n, st, d: events.append((n, st, d)))
check("批量报数：成功 1 / 失败 3",
      res["total"] == 4 and res["success"] == 1 and res["failed"] == 3,
      str({k: res[k] for k in ("total", "success", "failed")}))
by_file = {d["file"]: d for d in res["details"]}
check("details 逐条给出失败原因",
      all(by_file[f].get("error") for f in ("截图.png", "空文件.txt", "子目录")),
      str(res["details"])[:240])
check("summary 一句话含失败文件名与原因",
      "失败 3" in res["summary"] and "截图.png" in res["summary"],
      res["summary"])
check("summary 里能看到原因（不只是计数）",
      "（" in res["summary"] and "失败 3" in res["summary"],
      res["summary"])
terminal = {n: st for n, st, _ in events if st in ("success", "failed")}
check("每个文件都有终态事件（无静默丢弃）",
      len(terminal) == 4, str(events))
check("只有能解析的文件进了库",
      store.sources() == ["合同.txt"], str(store.sources()))
check("库中无任何失败提示语",
      all(not is_parse_failure(t) for t in store.texts()), str(store.texts())[:200])
check("汇总的片段数只统计真正入库的",
      res["total_chunks"] == len(store.texts("合同.txt")), f"{res['total_chunks']}")

# 部分内容未解析（PDF 图片页等提示）：仍算成功，但必须带 warnings 如实告知
partial = os.path.join(tmp_root, "含图片页说明.txt")
with open(partial, "w", encoding="utf-8") as f:
    f.write("正文第一段。\n[本页为扫描图片/空白页，OCR 未识别到文字内容]\n正文第二段。\n")
store2 = FakeStore()
kb2b = make_kb(tmp_root, store2, "kb2b")
res2 = kb2b.add_documents([partial])
check("部分未解析：仍入库但带 warnings",
      res2["success"] == 1 and res2["details"][0].get("warnings"),
      str(res2["details"]))
check("部分未解析：summary 提醒（不伪装完整入库）",
      "部分内容未解析" in res2["summary"], res2["summary"])

print("\n" + "=" * 78)
print("【3】重复导入语义：同名覆盖、不产生重复块")
print("=" * 78)
store3 = FakeStore()
kb3 = make_kb(tmp_root, store3, "kb3")
first = kb3.add_document(samples["txt"])
again = kb3.add_document(samples["txt"])
check("同一文件重复导入不累积（覆盖语义）",
      len(store3.texts("合同.txt")) == first["chunks"] == again["chunks"],
      f"{first} {again} rows={len(store3.rows)}")
check("同名文档在 list_documents 里只有一篇",
      len(kb3.list_documents()) == 1, str(kb3.list_documents()))

# 同名不同目录 + 篇幅变化：长版本覆盖短版本，再导短版本不能留下长版本的尾部块
os.makedirs(os.path.join(tmp_root, "dir_a"), exist_ok=True)
os.makedirs(os.path.join(tmp_root, "dir_b"), exist_ok=True)
short = os.path.join(tmp_root, "dir_a", "report.txt")
long_ = os.path.join(tmp_root, "dir_b", "report.txt")
with open(short, "w", encoding="utf-8") as f:
    f.write("短版本内容")
with open(long_, "w", encoding="utf-8") as f:
    f.write("长版本内容，用于覆盖。" * 300)
kb3.add_document(short)
n_short = len(store3.texts("report.txt"))
kb3.add_document(long_)
n_long = len(store3.texts("report.txt"))
kb3.add_document(short)
n_back = len(store3.texts("report.txt"))
check("同名不同目录=覆盖（只保留最后一份）",
      n_long > n_short and n_back == n_short,
      f"short={n_short} long={n_long} back={n_back}")
check("覆盖后不留旧版本的尾部块",
      all("长版本内容" not in t for t in store3.texts("report.txt")),
      str(store3.texts("report.txt"))[:200])

# 同一批次里两条同名：后一条覆盖前一条，前一条必须挂"被覆盖"备注
store4 = FakeStore()
kb4 = make_kb(tmp_root, store4, "kb4")
batch = kb4.add_documents([short, long_])
check("同批同名：details 里能看到「被覆盖」备注",
      any(d.get("notes") for d in batch["details"]), str(batch["details"])[:240])
check("同批同名：最终只留一份",
      len(kb4.list_documents()) == 1 and len(store4.texts("report.txt")) == n_long,
      f"{kb4.list_documents()} rows={len(store4.rows)}")

print("\n" + "=" * 78)
print("【4】向量模型缺失 / 服务不可用：必须有可读提示（不依赖 Ollama）")
print("=" * 78)
# (a) 依赖未安装
kb5 = KnowledgeBase(persist_dir=os.path.join(tmp_root, "kb5"))
kb5._check_available = lambda: False
try:
    kb5.add_document(samples["txt"])
    check("依赖缺失给出安装指引", False, "未抛异常")
except RuntimeError as exc:
    check("依赖缺失给出安装指引", "pip install" in str(exc) and "知识库依赖未安装" in str(exc), str(exc))

# (b) Ollama 不在线（连接错误）→ 环境级：后续文件不再逐个尝试，但每个文件都报 failed
conn_exc = ConnectionError("Failed to connect to Ollama. Please check that Ollama is downloaded, "
                           "running and accessible.")
kb6 = make_kb(tmp_root, FakeStore(fail_with=conn_exc), "kb6")
res6 = kb6.add_documents([samples["txt"], samples["md"]])
check("Ollama 不可达：全部记为 failed",
      res6["success"] == 0 and res6["failed"] == 2, str(res6["summary"]))
check("Ollama 不可达：原因是可行动的中文",
      all("向量化失败" in d.get("error", "") and "Ollama" in d.get("error", "")
          for d in res6["details"]), str(res6["details"])[:200])
check("Ollama 不可达：后续文件注明不再逐个尝试",
      "不再逐个尝试" in res6["details"][1].get("error", ""), str(res6["details"][1]))
check("Ollama 不可达：库中没有写入任何内容", kb6._vectorstore.rows == [], str(kb6._vectorstore.rows))

# (c) 模型没拉取（404 not found）
model_exc = Exception('model "nomic-embed-text" not found, try pulling it first (status code: 404)')
kb7 = make_kb(tmp_root, FakeStore(fail_with=model_exc), "kb7")
res7 = kb7.add_documents([samples["txt"]])
check("模型未拉取：提示 ollama pull",
      "ollama pull nomic-embed-text" in res7["details"][0].get("error", ""),
      str(res7["details"][0]))

# (d) 非环境级向量化错误：只该文件失败，后续文件仍要尝试
flaky = FakeStore(fail_with=RuntimeError("disk I/O error"))
kb8 = make_kb(tmp_root, flaky, "kb8")
res8 = kb8.add_documents([samples["txt"], samples["md"]])
check("非环境级错误：不误判为全局故障（两个文件都被尝试）",
      flaky.calls == 2 and res8["failed"] == 2, f"calls={flaky.calls} {res8['summary']}")

print("\n" + "=" * 78)
print("【5】覆盖导入失败不丢旧版本（先写后删）")
print("=" * 78)
store9 = FakeStore()
kb9 = make_kb(tmp_root, store9, "kb9")
v1 = os.path.join(tmp_root, "versioned.txt")
with open(v1, "w", encoding="utf-8") as f:
    f.write("第一版内容：旧版本必须保留。" * 5)
kb9.add_document(v1)
kb9._vectorstore.fail_with = RuntimeError("simulated embedding outage")
with open(v1, "w", encoding="utf-8") as f:
    f.write("第二版内容：这次向量化会失败。" * 5)
try:
    kb9.add_document(v1)
    check("覆盖导入失败必须报错", False, "未抛异常")
except RuntimeError as exc:
    check("覆盖导入失败必须报错", "向量化失败" in str(exc), str(exc))
check("向量化失败后旧版本仍在库里（不静默丢数据）",
      any("第一版内容" in t for t in store9.texts("versioned.txt")),
      str(store9.texts("versioned.txt"))[:160])

print("\n" + "=" * 78)
print("【6】失败文案：假的/损坏的 Office 容器、加密文档要给「人话 + 原始异常名」")
print("=" * 78)


def run_single(path, name):
    """用内存替身跑一次 add_documents，返回 (结果, 库, 第一条 error)。"""
    store = FakeStore()
    kb = make_kb(tmp_root, store, name)
    res = kb.add_documents([path])
    err = res["details"][0].get("error", "") if res["details"] else ""
    return res, store, err


# (a) 负责人实测场景：.docx 其实是纯文本（旧版缺陷产出的"改后缀"文件）
fake_docx = os.path.join(tmp_root, "aaa.docx")
with open(fake_docx, "w", encoding="utf-8") as f:
    f.write("这其实是纯文本，只是被改成了 .docx 后缀。")
res_fake, store_fake, err_fake = run_single(fake_docx, "kb6a")
check("假 .docx：不计为成功",
      res_fake["success"] == 0 and res_fake["failed"] == 1,
      str({k: res_fake[k] for k in ("success", "failed")}))
check("假 .docx：文案说明「不是有效的 Word 文档」",
      "不是有效的 Word 文档" in err_fake, err_fake)
check("假 .docx：文案给出「另存为 .docx」的下一步",
      "另存为 .docx" in err_fake, err_fake)
check("假 .docx：原始异常名保留在括号里（不是主文案）",
      "PackageNotFoundError" in err_fake
      and "PackageNotFoundError" not in err_fake.split("（", 1)[0],
      err_fake)
check("假 .docx：库里没有写入任何内容", store_fake.rows == [], str(store_fake.rows))

# (b) 截断的 zip：前 4 字节是 PK\x03\x04，但内容不完整
trunc_docx = os.path.join(tmp_root, "截断文档.docx")
with open(trunc_docx, "wb") as f:
    f.write(b"PK\x03\x04" + b"\x00" * 40)
res_trunc, _, err_trunc = run_single(trunc_docx, "kb6b")
check("截断 zip：不计为成功", res_trunc["success"] == 0, str(res_trunc["success"]))
check("截断 zip：同样给出「不是有效的 Word 文档」与下一步",
      "不是有效的 Word 文档" in err_trunc and "另存为 .docx" in err_trunc,
      err_trunc)
check("截断 zip：原始异常名保留",
      "PackageNotFoundError" in err_trunc or "BadZipFile" in err_trunc,
      err_trunc)

# (c) 加密文档：zipfile 写加密条目不可行，给解析层打桩抛加密异常验证映射
enc_docx = os.path.join(tmp_root, "加密文档.docx")
with open(enc_docx, "wb") as f:
    f.write(b"not a real docx")
_orig_docx_parse = document_parser.DocxParser.parse


def _raise_encrypted(self, filepath, scan_privacy=False):
    raise RuntimeError("encrypted")


document_parser.DocxParser.parse = _raise_encrypted
try:
    res_enc, _, err_enc = run_single(enc_docx, "kb6c")
finally:
    document_parser.DocxParser.parse = _orig_docx_parse
check("加密文档：不计为成功", res_enc["success"] == 0, str(res_enc["success"]))
check("加密文档：文案说明「已加密」并给出解除密码的下一步",
      "加密" in err_enc and "解除密码" in err_enc, err_enc)
check("加密文档：保留原始异常名（RuntimeError）", "RuntimeError" in err_enc, err_enc)

# (d) 正常 .docx 仍能入库（回归，防误伤）
try:
    from docx import Document as _Document

    normal_docx = os.path.join(tmp_root, "正常文档.docx")
    _doc = _Document()
    _doc.add_paragraph("正常 Word 正文，联系人电话 13800138000。")
    _doc.save(normal_docx)
    res_docx, store_docx, _ = run_single(normal_docx, "kb6d")
    check("正常 .docx：仍成功入库", res_docx["success"] == 1,
          str(res_docx["summary"]))
    check("正常 .docx：正文进了库",
          any("正常 Word 正文" in t for t in store_docx.texts()),
          str(store_docx.texts())[:200])
except ImportError:
    print("  [跳过] 未安装 python-docx，正常 .docx 回归不适用")

# (e) 正常 .txt 仍能入库（回归，防误伤）
res_txt, store_txt, _ = run_single(samples["txt"], "kb6e")
check("正常 .txt：仍成功入库", res_txt["success"] == 1, str(res_txt["summary"]))
check("正常 .txt：正文进了库", store_txt.sources() == ["合同.txt"],
      str(store_txt.sources()))

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：\n   - " + "\n   - ".join(failures))
    sys.exit(1)
print("✅ 全部通过：导入格式判定与解析器能力一致；失败逐条可见；无假成功；同名覆盖不累积。")
sys.exit(0)
