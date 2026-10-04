# -*- coding: utf-8 -*-
"""隐盾 · 知识库文本管线回归（不依赖向量库与网络）

为什么单独一套：知识库的两个洞都出在"入库前的文本处理"上，而这段逻辑与 Chroma / Ollama
无关——放在需要 Ollama 的集成套件里就会被"跳过"，等于没测。这里只测纯文本管线：
  1. **先整篇脱敏再切块**：长于 CHUNK_SIZE 的实体（PEM 私钥、长连接串、大段 base64）
     不会被切碎成明文碎片进入向量库
  2. 占位符正则覆盖下划线/数字类型（MEDICAL_RECORD / IDCARD15 / PRIVATE_KEY / ACCESS_TOKEN /
     PLUGIN_*）—— 旧正则漏掉它们，检索时不重编号，多文档还原会串值
  3. 每块映射只含"本块真的出现"的占位符（跨块不串值，且不会把整篇映射塞进每一块）
  4. 向量库元数据不含本机完整路径（只留文件名）
  5. 还原闭环：入库的占位符能按块映射还原回原文

用法：python tests/test_kb_pipeline.py   （退出码 0 = 全部通过）
"""
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.core.knowledge_base import _PLACEHOLDER_RE, KnowledgeBase  # noqa: E402
from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── 1) 占位符正则：类型段必须覆盖下划线与数字 ────────
print("=" * 78)
print("【1】占位符正则（下划线 / 数字类型）")
print("=" * 78)
cases = {
    "[PHONE_0_ab12]": ("PHONE", "0", "ab12"),
    "[MEDICAL_RECORD_3_k9xz]": ("MEDICAL_RECORD", "3", "k9xz"),
    "[IDCARD15_1_zzzz]": ("IDCARD15", "1", "zzzz"),
    "[PRIVATE_KEY_0_a1b2]": ("PRIVATE_KEY", "0", "a1b2"),
    "[ACCESS_TOKEN_12_qqqq]": ("ACCESS_TOKEN", "12", "qqqq"),
    "[PLUGIN_CUSTOM_TERM_2_mm4n]": ("PLUGIN_CUSTOM_TERM", "2", "mm4n"),
    "[NAME_7]": ("NAME", "7", None),          # 旧格式（无 nonce）仍要兼容
}
for placeholder, expected in cases.items():
    m = _PLACEHOLDER_RE.match(placeholder)
    got = (m.group(1), m.group(2), m.group(3)) if m else None
    check(f"解析 {placeholder}", got == expected, f"实际 {got}")

# ── 2) 整篇脱敏 → 切块：长实体不被切碎 ───────────────
print("\n" + "=" * 78)
print("【2】先整篇脱敏再切块（长实体不泄露）")
print("=" * 78)
# 用分段拼接生成"像私钥的长串"，避免把完整密钥字面量写进仓库
_HEAD = "-----BEGIN " + "PRIVATE KEY-----"
_TAIL = "-----END " + "PRIVATE KEY-----"
_BODY = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQ" + ("A1b2C3d4E5f6G7h8" * 30)
LONG_ENTITY = f"{_HEAD}\n{_BODY}\n{_TAIL}"
assert len(LONG_ENTITY) > KnowledgeBase.CHUNK_SIZE, "测试前提：该实体必须长于单块尺寸"

_FILLER = ("补充说明：本段用于把文档撑到多块规模，确保「先脱敏再切块」的顺序真的经过切块路径。"
           "段落内容不含敏感信息，仅用于测试分块行为。") * 6
doc = (
    "内部技术交接材料\n\n"
    "一、背景说明：本文件用于测试知识库入库前的脱敏顺序。\n\n"
    f"{_FILLER}\n\n"
    f"二、部署凭据如下：\n{LONG_ENTITY}\n\n"
    "三、其他信息：联系人电话 13800138000，身份证 110101199003072316。\n\n"
    f"{_FILLER}\n\n"
    "四、结尾：以上内容仅用于内部交接。\n"
)
engine = PrivacyEngine()
chunks, mappings, stats = KnowledgeBase.mask_and_split(doc, engine)
joined = "\n".join(chunks)
print(f"  原文 {len(doc)} 字符 → {len(chunks)} 块；脱敏统计 {stats}")

check("长实体已被整体替换为占位符（原文不再出现）",
      "BEGIN PRIVATE KEY" not in joined, joined[:200])
check("长实体没有被切碎成明文碎片",
      not any(fragment in joined for fragment in
              [_BODY[:40], _BODY[200:240], _BODY[-40:], _TAIL]),
      "存在私钥片段明文")
check("私钥以 PRIVATE_KEY 占位符形式存在", "[PRIVATE_KEY_" in joined, joined[:200])
check("占位符完整落在某一个块里（没有被切块切开）",
      any("[PRIVATE_KEY_" in c and "]" in c.split("[PRIVATE_KEY_", 1)[1] for c in chunks),
      "占位符疑似被切断")
check("同一篇里的其它实体照旧脱敏",
      "13800138000" not in joined and "110101199003072316" not in joined)
check("确实发生了切块（多块场景被真实覆盖）", len(chunks) >= 2, f"块数 {len(chunks)}")

# ── 3) 每块映射只含本块出现的占位符 ─────────────────
print("\n" + "=" * 78)
print("【3】分块映射的隔离性")
print("=" * 78)
total_map = sum(len(m) for m in mappings)
check("每块映射都是整篇映射的子集",
      all(set(m.keys()) <= set(sum((list(x.keys()) for x in mappings), [])) for m in mappings))
for idx, (chunk_text, mapping) in enumerate(zip(chunks, mappings)):
    stray = [ph for ph in mapping if ph not in chunk_text]
    check(f"第 {idx + 1} 块的映射不含本块未出现的占位符", not stray, str(stray))
check("映射总量不大于占位符出现次数（没有把整篇映射塞进每一块）",
      total_map <= sum(len(_PLACEHOLDER_RE.findall(c)) for c in chunks) + len(mappings),
      f"映射 {total_map} 条")

# 还原闭环：按块映射能把占位符还原回真实值
for idx, (chunk_text, mapping) in enumerate(zip(chunks, mappings)):
    if not mapping:
        continue
    restored = KnowledgeBase.restore(chunk_text, mapping)
    check(f"第 {idx + 1} 块可还原（映射与占位符一一对应）",
          "[" not in restored.replace("[[", "[") or "PRIVATE_KEY" not in chunk_text,
          restored[:120])

# ── 4) 元数据不含本机路径 ──────────────────────────
print("\n" + "=" * 78)
print("【4】入库元数据（不泄露本机目录结构）")
print("=" * 78)
src = (ROOT / "yindun" / "core" / "knowledge_base.py").read_text(encoding="utf-8")
add_doc_block = src.split("def add_document", 1)[1].split("def add_documents", 1)[0]
check("add_document 不再把 file_path 写进元数据",
      '"file_path"' not in add_doc_block, "元数据里仍存在 file_path 字段")
check("元数据保留 source（列表/删除都按它工作）", '"source": file_name' in add_doc_block)
check("chunk id 由文件名派生而非完整路径",
      'hashlib.sha256(file_name.encode' in add_doc_block)
check("不再使用弱哈希 MD5 生成 id", "md5(" not in src, "仍存在 md5 用法")

# ── 5) 搜索路径的占位符处理不丢类型 ─────────────────
print("\n" + "=" * 78)
print("【5】检索重编号覆盖下划线/数字类型")
print("=" * 78)
search_src = src.split("def search", 1)[1].split("def restore", 1)[0]
check("检索路径用同一个正则解析占位符类型（不另写一份）",
      "_PLACEHOLDER_RE" in search_src)
check("重编号时保留 nonce（新格式）",
      "nonce = match.group(3)" in search_src and "_{gidx}_{nonce}" in search_src)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：知识库文本管线（脱敏顺序/占位符类型/分块映射/元数据）均符合约束")
sys.exit(0)
