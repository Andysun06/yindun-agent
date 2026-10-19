# -*- coding: utf-8 -*-
"""隐盾 · 附件处理（解析 / 题号锚点 / 上下文拼接）

从 `gui/main_window.py` 迁出并统一到服务层：这些逻辑与界面无关，
Web 界面与 Qt 界面共用同一份实现（避免双实现漂移）。

包含：
- `parse_attachment`   ：离线文档解析（PDF/Word/Excel/TXT/MD/CSV，含扫描件 OCR 路径）
- `inject_question_anchors`：给试卷类文本注入 [Q<n>] 题号锚点，便于模型秒级定位
- `build_attachment_context`：把"本轮新附件 + 历史累积快照"拼成模型上下文，
  超长文档只给前 N 字并附"用 read_attachment_chunk 继续读"的提示

与界面无关：本模块不导入任何界面框架。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from yindun.core.context_budget import ATTACHMENT_TOTAL_CHARS
from yindun.utils.document_parser import is_parse_failure, parse_failure_reason

# 单篇文档注入上下文的字符上限（超出部分靠 read_attachment_chunk 工具按题号/关键词检索）
MAX_DOC_CHARS = 30000

# 单轮**全部**附件的注入总量上限：数字来自 core/context_budget.py（预算表是唯一出处）。
# 为什么要总量：单篇 30000 字 ≈ 30000 token（估算口径 CJK 1 字≈1 token），已经超过
# MODEL_WINDOW_TOKENS=16384 的整个窗口；再挂第二篇就会把模型侧直接顶爆，
# 表现为"回答变差/被截断"，而界面上只显示"已挂载 N 个附件"——用户看不出原因。
MAX_TOTAL_DOC_CHARS = ATTACHMENT_TOTAL_CHARS

# 文件选择对话框用的扩展名（★ 必须与 document_parser 真实支持的格式一一对应）：
# 对话框过滤器只显示这里列出的类型（pywebview 不会列出别的），少列一个 = 用户根本选不到；
# 多列一个"实际读不了"的 = 用户白挂一次、还以为是自己的文件有问题。
SUPPORTED_EXTS = [
    # 文档
    "pdf", "docx", "pptx", "xlsx", "xls",
    # 纯文本家族（解析器按 utf-8→gbk→… 自适应解码）
    "txt", "text", "md", "csv", "tsv", "json", "html", "htm", "log",
    "yaml", "yml", "xml", "ini", "conf", "rst",
    # 图片（走离线 OCR：有 OCR 组件就出字，没有就如实说明"需要 OCR 组件/请给文本版"）
    "png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp",
]
# 不进列表的格式（理由写在这里，避免下次又"顺手加上"）：
#   · doc：旧版 Word 是 OLE 二进制，内核没有解析器（会给出"另存为 .docx"的可行动说明），
#     列进对话框只会让用户以为选了就能读；
#   · 音频（mp3/wav/…）：转写依赖 FunASR + torch，客户端 exe 明确排除 torch，
#     交付形态下必然读不出内容，因此不进过滤器（解析器仍会给出如实说明）。


# ──────────────────────────────────────────────
# 题号锚点
# ──────────────────────────────────────────────
_CN_DIGITS = {
    "零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}

# 题号正则（行首，允许前导空白）；顺序：先"第N题"这类最明确的格式，再 N. / N、
_QUESTION_PATTERNS = [
    re.compile(r'^(\s*)(\d{1,3})\s*[.、)）]\s*(.+)$'),                                      # 1. xxx / 1、xxx
    re.compile(r'^(\s*)第\s*([一二三四五六七八九十百零\d]{1,4})\s*题\s*[.、:：)）]?\s*(.*)$'),   # 第5题 / 第五题
    re.compile(r'^(\s*)题目\s*([一二三四五六七八九十百零\d]{1,4})\s*[.、:：)）]?\s*(.*)$'),      # 题目5
    re.compile(r'^(\s*)Q\s*(\d{1,3})\s*[.、)）]?\s*(.+)$', re.IGNORECASE),                   # Q5 xxx
]

# 题号有效范围：过滤年份/金额/页码等误匹配（2026 之类的 4 位数天然被排除）
_MAX_QUESTION_NO = 200


def _cn_to_arabic(text: str) -> Optional[int]:
    """中文数字转阿拉伯数字（1-99）；纯数字串返回 None（交给调用方 int()）。"""
    if not text or text.isdigit():
        return None
    if "十" in text:
        parts = text.split("十")
        if len(parts) == 2:
            tens = _CN_DIGITS.get(parts[0], 1) if parts[0] else 1
            ones = _CN_DIGITS.get(parts[1], 0) if parts[1] else 0
            return tens * 10 + ones
    return _CN_DIGITS.get(text)


def inject_question_anchors(text: str) -> str:
    """给文本行首的题号注入 `[Q<n>]` 锚点（只处理行首，保留原缩进）。

    例：`  5. 下列哪个选项正确？` → `  [Q5] 5. 下列哪个选项正确？`
    这样模型在初始上下文里能直接定位题号，无需在长文本中模糊匹配。
    """
    if not text:
        return text
    out: List[str] = []
    for line in text.split("\n"):
        matched = False
        for pattern in _QUESTION_PATTERNS:
            m = pattern.match(line)
            if not m:
                continue
            indent, num_str = m.group(1), m.group(2)
            number = _cn_to_arabic(num_str)
            if number is None:
                try:
                    number = int(num_str)
                except ValueError:
                    continue
            if not (1 <= number <= _MAX_QUESTION_NO):
                continue
            out.append(f"{indent}[Q{number}] {line[len(indent):]}")
            matched = True
            break
        if not matched:
            out.append(line)
    return "\n".join(out)


# ──────────────────────────────────────────────
# 附件解析
# ──────────────────────────────────────────────
def parse_attachment(path: str) -> Dict[str, Any]:
    """解析单个附件为纯文本。返回 {name, path, text, chars, report, error}。

    ★ 报告与正文分离（红队复核整改）：解析附带的「隐私风险报告」里含**部分掩码**片段
    （如 138****5678 / 1101**********1234）——若混在正文里，这些片段不再是完整实体、
    脱敏引擎匹配不到，会直接进入模型上下文，与"模型全程只见占位符"的承诺冲突。
    因此报告单独存放在 `report` 字段（供界面/审计按需展示），`text` 只保留文档正文，
    送入模型与快照前统一走脱敏网关。

    ★★ 失败必须被判成失败（真机事故固化）：解析器把"读不到"编码成一段**非空字符串**
    （"[文档解析失败: …]"）返回，旧实现只看 `text.strip()` 是否为空 → 图片附件显示
    "已挂载 22 字 / error=None"、审计记为成功，而那 22 字其实是提示串，还被当成正文
    拼进了模型上下文。现在统一走 `is_parse_failure()`：失败时 `text` 置空、`chars=0`、
    原因进 `error`（界面直接显示 `error`，模型看到的是"这份附件读不出来，因为 X"）。
    """
    file_path = Path(path)
    result: Dict[str, Any] = {
        "name": file_path.name, "path": str(file_path), "text": "",
        "chars": 0, "report": "", "error": None,
    }
    if not file_path.is_file():
        result["error"] = "文件不存在"
        return result
    try:
        from yindun.utils.document_parser import extract_file_text_with_report
        combined = extract_file_text_with_report(str(file_path), scan_privacy=True) or ""
        marker = "【隐私风险报告】"
        if marker in combined:
            body, _, report = combined.partition(marker)
            body = body.rstrip()
            result["report"] = marker + report
        else:
            body = combined
        if is_parse_failure(body):
            # 失败提示不是正文：chars 记 0（此前把提示串的长度算成"已挂载字数"）
            result["text"] = ""
            result["chars"] = 0
            result["error"] = parse_failure_reason(body) or "未能提取到文本"
            return result
        result["text"] = body
        result["chars"] = len(body)
        if not result["text"].strip():
            result["error"] = "未能提取到文本（可能是纯图片/加密文档）"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[Attachment] 解析失败 {file_path.name}：{exc}")
    return result


def build_attachment_context(user_text: str,
                             new_files: Optional[List[Dict[str, Any]]] = None,
                             session_snapshot: Optional[Dict[str, str]] = None) -> Tuple[str, Dict[str, str]]:
    """拼接"附件上下文 + 用户提问"，并返回更新后的**累积快照**（供跨轮检索与持久化）。

    · 短文档（≤ MAX_DOC_CHARS）：注入题号锚点后整篇给出
    · 超长文档：只给前 MAX_DOC_CHARS 字 + 提示用 read_attachment_chunk 按题号/关键词/偏移继续读
    · 多篇文档：单轮注入**总量**受 MAX_TOTAL_DOC_CHARS 约束，超出预算的附件改为"只报名字 +
      检索指引"——模型永远知道有哪些附件（不会"悄悄少一个"），内容按预算给
    · 快照保留**原文**（不加锚点），保证工具检索时不受锚点污染
    · 读不出内容的附件**带上原因**（不是一句干巴巴的"未提取到文本"），模型才能如实回答
      "这份附件读不出来，因为 X"
    """
    snapshot: Dict[str, str] = dict(session_snapshot or {})
    contexts: List[str] = []
    injected_chars = 0          # 本轮已注入的附件正文字符总量（按预算约束）

    for item in new_files or []:
        name = item.get("name") or "未命名附件"
        full_text = item.get("text") or ""
        snapshot[name] = full_text          # 同名文件以最新一次为准
        if not full_text:
            reason = (item.get("error") or "").strip() or "未能提取到文本（原因未知，可能是纯图片或加密文档）"
            contexts.append(f"[离线附件环境上下文：{name}]\n（该附件未能提取到文本：{reason}）")
            continue

        remaining = MAX_TOTAL_DOC_CHARS - injected_chars
        single_cap = min(MAX_DOC_CHARS, max(remaining, 0))
        if len(full_text) <= single_cap:
            contexts.append(f"[离线附件环境上下文：{name}]\n{inject_question_anchors(full_text)}")
            injected_chars += len(full_text)
            continue

        # 预算用尽：不注入正文，但要**报名字 + 检索指引**（绝不能悄悄丢掉）
        if single_cap <= 0:
            contexts.append(
                f"[离线附件环境上下文：{name}]\n"
                f"（已挂载但未全文注入：该文档 {len(full_text)} 字符，本轮附件注入预算"
                f"（共 {MAX_TOTAL_DOC_CHARS} 字符）已用尽，正文未放入上下文。"
                f"需要时请调用工具检索：\n"
                f"  read_attachment_chunk(file='{name}', question='题号')  # 如 question='5'\n"
                f"  read_attachment_chunk(file='{name}', keyword='关键词')  # 模糊定位\n"
                f"  read_attachment_chunk(file='{name}', char_start=0)  # 从头读\n"
                f"）"
            )
            continue

        head = inject_question_anchors(full_text[:single_cap])
        injected_chars += single_cap
        contexts.append(
            f"[离线附件环境上下文：{name}]\n{head}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"【长文档分块提示】该文档总长 {len(full_text)} 字符，上方仅展示前 {single_cap} 字符"
            f"（本轮附件注入预算共 {MAX_TOTAL_DOC_CHARS} 字符）。"
            f"如需读取后续内容，请调用工具：\n"
            f"  read_attachment_chunk(file='{name}', question='题号')  # 如 question='5'\n"
            f"  read_attachment_chunk(file='{name}', keyword='关键词')  # 模糊定位\n"
            f"  read_attachment_chunk(file='{name}', char_start={single_cap})  # 继续读\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    if not contexts:
        return user_text, snapshot
    return "\n\n".join(contexts) + f"\n\n[人类当前实时提问]：{user_text}", snapshot


def normalize_paths(paths: Any) -> List[str]:
    """把对话框返回值（str / list / tuple）统一成路径列表，并去重保序。"""
    if not paths:
        return []
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    out: List[str] = []
    for item in paths:
        if not item:
            continue
        text = str(item)
        if text not in out:
            out.append(text)
    return out
