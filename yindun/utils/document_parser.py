# -*- coding: utf-8 -*-
"""
隐盾 V3.x — 📂 离线机密文档解析舱
专门处理离线 PDF、Word、Excel、文本文件及音频文件的全自动内容读取
音频转写使用 FunASR (Paraformer-large) — 中文高精度语音识别
PDF 扫描件使用 RapidOCR (ONNX) — 离线中文 OCR
"""
import os
import re
import zipfile
from typing import Optional

_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")

# ── 资源预检上限（防恶意文档耗尽内存）──────────────────────
MAX_FILE_SIZE = 50 * 1024 * 1024      # 文件大小上限：50MB
MAX_PDF_PAGES = 200                   # PDF 页数上限
MAX_PDF_PAGE_SIZE = 20000             # 页面任一边尺寸上限（pt），超限跳过该页 OCR 渲染
MAX_ZIP_ENTRIES = 10000               # zip 容器条目数上限
MAX_ZIP_RATIO = 100                   # 解压后大小 / 压缩前大小 比值上限


# ──────────────────────────────────────────────
# PDF 解析器：PyMuPDF 主文字 + pdfplumber 表格 + RapidOCR 扫描件回退
# ──────────────────────────────────────────────
class PdfParser:
    """PDF 文档解析器
    - PyMuPDF (fitz) 提取主文字，按页输出
    - pdfplumber 专门抽表格，转 Markdown 格式
    - 扫描件（无文字层）自动渲染图片 → RapidOCR 离线中文识别
    """

    # OCR 引擎懒加载（首次使用时初始化，避免无 OCR 需求时的启动开销）
    _ocr_engine = None
    _ocr_init_failed = False  # 标记 OCR 初始化是否已失败（避免每页重复报错）

    def __init__(self):
        self._scanner = None  # 懒加载，避免无扫描需求时初始化开销

    def _get_scanner(self):
        """懒加载 PrivacyScanner"""
        if self._scanner is None:
            from yindun.utils.privacy_scanner import PrivacyScanner
            self._scanner = PrivacyScanner()
        return self._scanner

    @classmethod
    def _get_ocr_engine(cls):
        """懒加载 RapidOCR 引擎。返回引擎实例或 None（不可用时）。"""
        if cls._ocr_engine is not None:
            return cls._ocr_engine
        if cls._ocr_init_failed:
            return None
        try:
            from rapidocr_onnxruntime import RapidOCR
            cls._ocr_engine = RapidOCR()
            return cls._ocr_engine
        except Exception:
            cls._ocr_init_failed = True
            return None

    @staticmethod
    def _ocr_page(page) -> str:
        """
        将 PyMuPDF 页面对象渲染为图片并执行 OCR 识别。
        返回识别到的纯文本（按行拼接，已去重空白）。
        若 OCR 引擎不可用或识别失败，返回空字符串。
        """
        engine = PdfParser._get_ocr_engine()
        if engine is None:
            return ""
        try:
            # 渲染为高分辨率图片（DPI=300 兼顾精度与速度）
            pix = page.get_pixmap(dpi=300)
            img_bytes = pix.tobytes("png")
            # RapidOCR 接受文件路径或字节；字节需包装为类文件对象
            result, _elapsed = engine(img_bytes)
            if not result:
                return ""
            # result 是 list of [box, text, score]，只取 text
            lines = [item[1] for item in result if item and len(item) >= 2 and item[1]]
            return "\n".join(lines).strip()
        except Exception:
            return ""

    def parse(self, filepath: str, scan_privacy: bool = False):
        text_parts = []
        table_parts = []
        all_scan_results = []  # 收集所有页的隐私扫描结果
        had_text = False  # 是否抽到过任何文字（含 OCR）
        had_ocr_fallback = False  # 是否走过 OCR 回退
        ocr_unavailable_warned = False  # 是否已提示 OCR 不可用

        # 1) PyMuPDF 抽文字（缺失时回退到 PyPDF2）；空页自动 OCR 回退
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(filepath)
            # 页数预检：超上限拒绝整份，避免逐页渲染耗尽内存
            if doc.page_count > MAX_PDF_PAGES:
                doc.close()
                return f"[文档解析拒绝: PDF 页数超过上限 {MAX_PDF_PAGES}]"
            for i, page in enumerate(doc, start=1):
                page_text = page.get_text("text") or ""
                text_parts.append(f"--- 第 {i} 页 ---")
                if page_text.strip():
                    had_text = True
                    text_parts.append(page_text.strip())
                    # 隐私扫描
                    if scan_privacy:
                        page_results = self._get_scanner().scan(page_text.strip(), page=str(i), line_start=1, source_type="page")
                        all_scan_results.extend(page_results)
                else:
                    # 文字层为空 → 尝试 OCR 回退（先做页面尺寸预检，防止超大页面渲染耗尽内存）
                    if page.rect.width > MAX_PDF_PAGE_SIZE or page.rect.height > MAX_PDF_PAGE_SIZE:
                        text_parts.append(f"[第 {i} 页尺寸过大，已跳过 OCR]")
                    else:
                        ocr_text = self._ocr_page(page)
                        if ocr_text:
                            had_text = True
                            had_ocr_fallback = True
                            text_parts.append(ocr_text)
                            # OCR 结果也扫描
                            if scan_privacy:
                                ocr_results = self._get_scanner().scan(ocr_text, page=str(i), line_start=1, source_type="page")
                                all_scan_results.extend(ocr_results)
                        else:
                            # OCR 不可用或未识别到内容：给出准确提示
                            if self._ocr_init_failed and not ocr_unavailable_warned:
                                text_parts.append(
                                    "[本页为扫描图片，文字层为空；OCR 引擎未安装，无法识别图片文字。"
                                    "请运行: pip install rapidocr-onnxruntime]"
                                )
                                ocr_unavailable_warned = True
                            else:
                                text_parts.append(
                                    "[本页为扫描图片/空白页，OCR 未识别到文字内容]"
                                )
                text_parts.append("")
            doc.close()
        except ImportError:
            # PyMuPDF 未装，回退到 PyPDF2（兜底，无 OCR 能力）
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(filepath)
                # 页数预检：超上限拒绝整份
                if len(reader.pages) > MAX_PDF_PAGES:
                    return f"[文档解析拒绝: PDF 页数超过上限 {MAX_PDF_PAGES}]"
                for i, page in enumerate(reader.pages, start=1):
                    page_text = page.extract_text() or ""
                    text_parts.append(f"--- 第 {i} 页 ---")
                    if page_text.strip():
                        had_text = True
                        text_parts.append(page_text.strip())
                        # 隐私扫描
                        if scan_privacy:
                            page_results = self._get_scanner().scan(page_text.strip(), page=str(i), line_start=1, source_type="page")
                            all_scan_results.extend(page_results)
                    else:
                        text_parts.append(
                            "[本页文字层为空；PyMuPDF 未安装，无法 OCR 识别扫描图片。"
                            "请运行: pip install PyMuPDF rapidocr-onnxruntime]"
                        )
                    text_parts.append("")
            except ImportError:
                raise RuntimeError("未安装 PyMuPDF 或 PyPDF2，请运行: pip install PyMuPDF")
            except Exception as e:
                raise RuntimeError(f"PyPDF2 解析失败: {type(e).__name__} -> {e}")
        except Exception as e:
            raise RuntimeError(f"PyMuPDF 解析失败: {type(e).__name__} -> {e}")

        # 2) pdfplumber 抽表格
        try:
            import pdfplumber
            with pdfplumber.open(filepath) as pdf:
                # 页数预检：超上限拒绝整份
                if len(pdf.pages) > MAX_PDF_PAGES:
                    return f"[文档解析拒绝: PDF 页数超过上限 {MAX_PDF_PAGES}]"
                for i, page in enumerate(pdf.pages, start=1):
                    tables = page.extract_tables() or []
                    for j, tbl in enumerate(tables, start=1):
                        if not tbl:
                            continue
                        md = self._table_to_markdown(tbl)
                        table_parts.append(f"第 {i} 页 表格 {j}\n{md}\n")
                        # 表格内容隐私扫描
                        if scan_privacy:
                            table_results = self._get_scanner().scan(
                                md, page=f"第{i}页 表格{j}", line_start=1, source_type="table"
                            )
                            all_scan_results.extend(table_results)
        except ImportError:
            # pdfplumber 未装不算致命，表格部分跳过即可
            pass
        except Exception:
            # 表格抽取失败不阻断主文字
            pass

        # 3) 扫描件兜底：所有页均无文字且无表格 → 明确告知（不再编造"建议 OCR"）
        if not had_text and not table_parts:
            if self._ocr_init_failed:
                return (
                    "[PDF 全文无可提取文字，OCR 引擎未安装。"
                    "请运行: pip install rapidocr-onnxruntime 后重试]"
                )
            return (
                "[PDF 全文无可提取文字，OCR 也未识别到内容。"
                "可能是空白页或图片质量过差，建议人工核对原文]"
            )

        result = "\n".join(text_parts)
        if had_ocr_fallback:
            # 在结果头部插入 OCR 处理标记，让下游 LLM 知道本文档含 OCR 识别内容
            result = "[本文档部分页面通过 OCR 识别，可能存在识别误差]\n\n" + result
        if table_parts:
            result += "\n【表格数据】\n" + "\n".join(table_parts)
        # ★★★ 题号修复后处理：把 PDF 抽取时被换行打散的题号还原
        # 让下游 _inject_question_anchors 能稳定匹配行首题号
        result = self._fix_question_numbering(result)
        # 隐私风险报告拼接
        if scan_privacy and all_scan_results:
            report = self._get_scanner().generate_report(all_scan_results)
            result = result + "\n\n" + report
        return result

    @staticmethod
    def _fix_question_numbering(text: str) -> str:
        """
        修复 PDF 抽取时题号被换行打散的问题。

        常见 PDF 抽取错误模式：
        1. 题号独占一行，题干在下一行：
           "1.\n下列哪个选项正确？"  →  "1. 下列哪个选项正确？"
        2. 选项 A/B/C/D 独占一行，选项内容在下一行：
           "A.\n三角函数"  →  "A. 三角函数"
        3. 题号与题干之间被多余空格分开：
           "1.    下列..."  →  "1. 下列..."
        4. "第N题" 与题干分行：
           "第5题\n阅读理解"  →  "第5题 阅读理解"

        注意：
        - 只合并"行首编号 + 空行/换行 + 内容"的模式
        - 不破坏正常排版（如表格、代码块）
        - 题号范围限制 1-100，避免误匹配年份/金额
        """
        if not text:
            return text

        lines = text.split('\n')
        out_lines = []
        i = 0
        n = len(lines)

        # 题号独占一行的模式（行首 + 编号 + 标点 + 仅空白）
        # 支持：1. / 1、 / 1) / 1） / 第5题 / 题目5 / Q5
        q_solo_patterns = [
            re.compile(r'^(\s*)(\d{1,3})\s*[.、)）]\s*$'),                       # 1. / 1、 / 1)
            re.compile(r'^(\s*)第\s*([一二三四五六七八九十百零\d]{1,4})\s*题\s*[.、:：)）]?\s*$'),  # 第5题
            re.compile(r'^(\s*)题目\s*([一二三四五六七八九十百零\d]{1,4})\s*[.、:：)）]?\s*$'),     # 题目5
            re.compile(r'^(\s*)Q\s*(\d{1,3})\s*[.、)）]?\s*$', re.IGNORECASE),    # Q5
        ]
        # 选项独占一行的模式（A. / B、 / C) / D））
        opt_solo_pattern = re.compile(r'^(\s*)([A-Da-d])\s*[.、)）]\s*$')

        # 中文数字转阿拉伯（与 main_window._cn_to_arabic 保持一致逻辑）
        cn_digits = {'零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
                     '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}

        def cn_to_arabic(s):
            if not s:
                return None
            if s.isdigit():
                try:
                    return int(s)
                except ValueError:
                    return None
            if '十' in s:
                parts = s.split('十')
                if len(parts) == 2:
                    tens = cn_digits.get(parts[0], 1) if parts[0] else 1
                    ones = cn_digits.get(parts[1], 0) if parts[1] else 0
                    return tens * 10 + ones
            return cn_digits.get(s)

        def is_question_solo(line):
            """若该行是题号独占行，返回 (题号数字, 原始编号字符串)，否则返回 None"""
            for pat in q_solo_patterns:
                m = pat.match(line)
                if not m:
                    continue
                num_str = m.group(2)
                num = cn_to_arabic(num_str)
                if num is None:
                    continue
                if not (1 <= num <= 100):
                    continue
                return num, num_str
            return None

        while i < n:
            cur = lines[i]
            # 模式 1：题号独占行 + 下一行有内容 → 合并
            q_info = is_question_solo(cur)
            if q_info and i + 1 < n:
                next_line = lines[i + 1].strip()
                # 跳过空行/页眉标记/表格标记，找到真正的内容行
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n:
                    next_content = lines[j].strip()
                    # 避免合并到下一个题号（如 "1.\n2." 这种异常情况）
                    if next_content and not is_question_solo(lines[j]) and not next_content.startswith('---') and not next_content.startswith('【') and not next_content.startswith('第 ') and not next_content.startswith('表格'):
                        # 保留原缩进，合并题号与内容
                        indent_match = re.match(r'^(\s*)', cur)
                        indent = indent_match.group(1) if indent_match else ""
                        # 提取题号标点（. 、 ) 等）
                        punct_match = re.search(r'([.、)）])\s*$', cur)
                        punct = punct_match.group(1) if punct_match else "."
                        # 提取题号数字部分
                        num_part_match = re.search(r'(\d{1,3}|第\s*[一二三四五六七八九十百零\d]{1,4}\s*题|题目\s*[一二三四五六七八九十百零\d]{1,4}|Q\s*\d{1,3})', cur, re.IGNORECASE)
                        if num_part_match:
                            num_part = num_part_match.group(1).strip()
                            # 标准化为 "数字." 格式（保留原始编号但加标点）
                            # 对"第N题"格式不加额外标点
                            if '第' in num_part or '题目' in num_part or num_part.lower().startswith('q'):
                                merged = f"{indent}{num_part} {next_content}"
                            else:
                                merged = f"{indent}{num_part}{punct} {next_content}"
                            out_lines.append(merged)
                            # 跳过已合并的行（包括中间的空行）
                            i = j + 1
                            continue

            # 模式 2：选项独占行 + 下一行有内容 → 合并
            opt_m = opt_solo_pattern.match(cur)
            if opt_m and i + 1 < n:
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n:
                    next_content = lines[j].strip()
                    if next_content and not opt_solo_pattern.match(lines[j]) and not next_content.startswith('---') and not next_content.startswith('【'):
                        indent = opt_m.group(1)
                        opt_letter = opt_m.group(2).upper()
                        punct_match = re.search(r'([.、)）])\s*$', cur)
                        punct = punct_match.group(1) if punct_match else "."
                        merged = f"{indent}{opt_letter}{punct} {next_content}"
                        out_lines.append(merged)
                        i = j + 1
                        continue

            # 模式 3：题号与题干在同一行但被多余空格分开 → 压缩空格
            # "1.    下列..."  →  "1. 下列..."
            # 只处理行首，避免误伤正文
            m_compress = re.match(r'^(\s*)(\d{1,3})\s*([.、)）])\s{2,}(\S.*)$', cur)
            if m_compress:
                indent = m_compress.group(1)
                num = m_compress.group(2)
                punct = m_compress.group(3)
                rest = m_compress.group(4)
                try:
                    num_val = int(num)
                    if 1 <= num_val <= 100:
                        out_lines.append(f"{indent}{num}{punct} {rest}")
                        i += 1
                        continue
                except ValueError:
                    pass

            # 默认：原样保留
            out_lines.append(cur)
            i += 1

        return '\n'.join(out_lines)

    @staticmethod
    def _table_to_markdown(table) -> str:
        """二维列表转 Markdown 表格"""
        if not table:
            return ""
        # 过滤空行
        rows = [r for r in table if any(c for c in (r or []) if str(c).strip())]
        if not rows:
            return ""
        md = []
        # 表头
        header = rows[0]
        md.append("| " + " | ".join(str(c).replace("\n", " ").strip() if c else "" for c in header) + " |")
        md.append("| " + " | ".join("---" for _ in header) + " |")
        # 数据行
        for row in rows[1:]:
            md.append("| " + " | ".join(str(c).replace("\n", " ").strip() if c else "" for c in row) + " |")
        return "\n".join(md)


# ──────────────────────────────────────────────
# Word 解析器：python-docx body 顺序遍历
# ──────────────────────────────────────────────
class DocxParser:
    """DOCX 文档解析器
    - 按 body 元素顺序遍历，保留段落与表格的相对位置
    - 表格转 Markdown 格式
    - 统计内嵌图片数量
    """

    def __init__(self):
        self._scanner = None  # 懒加载，避免无扫描需求时初始化开销

    def _get_scanner(self):
        """懒加载 PrivacyScanner"""
        if self._scanner is None:
            from yindun.utils.privacy_scanner import PrivacyScanner
            self._scanner = PrivacyScanner()
        return self._scanner

    def parse(self, filepath: str, scan_privacy: bool = False):
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ImportError:
            raise RuntimeError("未安装 python-docx，请运行: pip install python-docx")

        # zip 炸弹预检：条目数或解压比异常直接拒绝，防止一次性加载 DOM 撑爆内存
        rejected = _check_zip_bomb(filepath)
        if rejected:
            return rejected

        doc = Document(filepath)
        body = doc.element.body
        parts = []
        all_scan_results = []  # 收集所有段落/表格的隐私扫描结果
        table_idx = 0
        para_idx = 0  # 非空段落计数器（仅非空段落递增，扫描时 page=str(para_idx)）
        image_count = 0

        # 遍历 body 下的所有子元素，保留原始顺序
        for child in body.iterchildren():
            if child.tag == qn('w:p'):
                # 段落
                # 找到对应的 Paragraph 对象以获取纯文本
                # 通过 xml 元素匹配 Paragraph
                text = "".join(t.text or "" for t in child.iter(qn('w:t')))
                if text.strip():
                    parts.append(text)
                    para_idx += 1
                    # 隐私扫描（page=段落号）
                    if scan_privacy:
                        results = self._get_scanner().scan(text, page=str(para_idx), line_start=1, source_type="paragraph")
                        all_scan_results.extend(results)
                # 统计段落内嵌图片 (w:drawing / pic:pic)
                for _ in child.iter(qn('w:drawing')):
                    image_count += 1
            elif child.tag == qn('w:tbl'):
                # 表格
                table_idx += 1
                # 从 doc.tables 中按顺序找到第 table_idx 个表格
                if table_idx <= len(doc.tables):
                    tbl = doc.tables[table_idx - 1]
                    md = self._table_to_markdown(tbl)
                    parts.append(f"表格 {table_idx}\n{md}")
                    # 隐私扫描（page 用 "表格N" 标识）
                    if scan_privacy:
                        results = self._get_scanner().scan(md, page=f"表格{table_idx}", line_start=1, source_type="table")
                        all_scan_results.extend(results)

        if image_count > 0:
            parts.append(f"[文档含 {image_count} 张内嵌图片，未自动解析]")

        # 隐私风险报告拼接
        if scan_privacy and all_scan_results:
            report = self._get_scanner().generate_report(all_scan_results)
            return "\n".join(parts) + "\n\n" + report
        return "\n".join(parts)

    @staticmethod
    def _table_to_markdown(table) -> str:
        """python-docx Table 对象转 Markdown"""
        rows = table.rows
        if not rows:
            return ""
        md = []
        # 表头
        header = [cell.text.strip().replace("\n", " ") for cell in rows[0].cells]
        md.append("| " + " | ".join(header) + " |")
        md.append("| " + " | ".join("---" for _ in header) + " |")
        # 数据行
        for row in rows[1:]:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            md.append("| " + " | ".join(cells) + " |")
        return "\n".join(md)


# ──────────────────────────────────────────────
# 资源预检工具：zip 炸弹 / 二进制文件检测
# ──────────────────────────────────────────────
def _check_zip_bomb(filepath) -> Optional[str]:
    """预检 zip 容器（DOCX/XLSX），识别 zip 炸弹。

    仅读取 zip 中央目录（不实际解压），统计条目数与估算解压比：
    - 条目数 > MAX_ZIP_ENTRIES
    - 解压后总大小 / 压缩后总大小 > MAX_ZIP_RATIO
    命中任一条件返回拒绝提示字符串；正常返回 None。
    """
    try:
        with zipfile.ZipFile(filepath) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                return "[文档解析拒绝: 疑似 zip 炸弹（条目过多/压缩比异常）]"
            compressed = sum(i.compress_size for i in infos)
            decompressed = sum(i.file_size for i in infos)
            if compressed > 0 and decompressed / compressed > MAX_ZIP_RATIO:
                return "[文档解析拒绝: 疑似 zip 炸弹（条目过多/压缩比异常）]"
    except Exception:
        # 预检失败不阻断正式解析（交由正式解析器报错）
        pass
    return None


def _looks_binary(filepath, sample_size: int = 1024) -> bool:
    """检测文件头是否含二进制特征：
    - 含 null 字节（b"\\x00"）
    - 异常控制字符（排除文本常用空白 \\t\\n\\r\\v\\f）占比过高
    仅统计控制字符而不统计高位字节，避免误伤 UTF-8 中文文本。
    """
    try:
        with open(filepath, "rb") as f:
            header = f.read(sample_size)
    except OSError:
        return False
    if not header:
        return False
    if b"\x00" in header:
        return True
    control = sum(1 for b in header if b < 0x09 or 0x0E <= b < 0x20)
    return control / len(header) > 0.3


def extract_file_text(filepath):
    """
    自适应跨平台离线文件文本提取矩阵
    支持：TXT, MD, CSV, PDF, DOCX, XLSX, XLS, 及常见音频格式（MP3/WAV/FLAC/M4A/AAC/OGG/OPUS/WMA）
    """
    name = filepath.lower()
    try:
        # 文件大小预检：超上限直接拒绝，不进入任何解析路径
        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            return f"[文档解析拒绝: 文件超过 {MAX_FILE_SIZE // (1024 * 1024)}MB 大小上限]"

        if name.endswith(_AUDIO_EXTS):
            return _transcribe_audio(filepath)

        if name.endswith((".txt", ".md", ".csv")):
            return _read_text_file(filepath)

        if name.endswith(".pdf"):
            return PdfParser().parse(filepath)

        if name.endswith(".docx"):
            return DocxParser().parse(filepath)

        if name.endswith((".xlsx", ".xls")):
            return _extract_excel(filepath, scan_privacy=False)  # 默认不扫描，保持向后兼容

        # 未知扩展名兜底：先检测二进制特征，避免把恶意二进制当文本硬解码
        if _looks_binary(filepath):
            return "[文档解析: 二进制/不可解码文件，已跳过]"
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    except Exception as e:
        # 区分文档类型给出更友好的错误信息
        ext = os.path.splitext(filepath)[1].lower().lstrip('.')
        return f"[文档解析失败: {ext or '未知'} -> {type(e).__name__}: {e}]"


def _read_text_file(filepath) -> str:
    """读取文本文件，自动尝试多种编码；先检测二进制特征避免硬解码"""
    if _looks_binary(filepath):
        return "[文档解析: 二进制/不可解码文件，已跳过]"
    for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
        try:
            with open(filepath, "r", encoding=enc) as f:
                return f.read()
        except:
            continue
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def extract_file_text_with_report(filepath, scan_privacy: bool = True) -> str:
    """
    统一入口：解析文件 + 可选隐私风险报告。

    与 extract_file_text 的区别：
    - extract_file_text：只返回纯文本（向后兼容）
    - extract_file_text_with_report：默认 scan_privacy=True，返回"文本 + 隐私风险报告"

    支持 PDF/DOCX/XLSX/XLS/TXT/MD/CSV。
    音频文件不支持隐私扫描（转写文本已含在结果中，但不附加报告）。
    """
    try:
        # 文件大小预检：超上限直接拒绝，不进入任何解析路径
        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            return f"[文档解析拒绝: 文件超过 {MAX_FILE_SIZE // (1024 * 1024)}MB 大小上限]"

        name = filepath.lower()

        # 音频文件：不扫描，直接走原逻辑
        if name.endswith(_AUDIO_EXTS):
            return _transcribe_audio(filepath)

        # TXT/MD/CSV：读取后扫描
        if name.endswith((".txt", ".md", ".csv")):
            text = _read_text_file(filepath)
            if scan_privacy:
                from yindun.utils.privacy_scanner import PrivacyScanner
                scanner = PrivacyScanner()
                results = scanner.scan(text, page="1", line_start=1, source_type="page")
                if results:
                    report = scanner.generate_report(results)
                    text = text + "\n\n" + report
            return text

        # PDF
        if name.endswith(".pdf"):
            return PdfParser().parse(filepath, scan_privacy=scan_privacy)

        # DOCX
        if name.endswith(".docx"):
            return DocxParser().parse(filepath, scan_privacy=scan_privacy)

        # Excel
        if name.endswith((".xlsx", ".xls")):
            return _extract_excel(filepath, scan_privacy=scan_privacy)

        # 其他文件：按文本处理
        text = _read_text_file(filepath)
        if scan_privacy:
            from yindun.utils.privacy_scanner import PrivacyScanner
            scanner = PrivacyScanner()
            results = scanner.scan(text, page="1", line_start=1, source_type="page")
            if results:
                report = scanner.generate_report(results)
                text = text + "\n\n" + report
        return text
    except Exception as e:
        # 扫描失败时回退到纯文本模式，并附加错误提示
        try:
            base_text = extract_file_text(filepath)
            return base_text + f"\n\n[隐私扫描失败，已回退纯文本模式: {type(e).__name__}: {e}]"
        except Exception:
            ext = os.path.splitext(filepath)[1].lower().lstrip('.')
            return f"[文档解析失败: {ext or '未知'} -> {type(e).__name__}: {e}]"


def _extract_excel(filepath, scan_privacy: bool = False) -> str:
    """解析 Excel 文件，可选隐私扫描。

    scan_privacy=False（默认）时仅返回文本，与原内联逻辑完全等价；
    scan_privacy=True 时按工作表独立扫描（page=工作表名，line_start=1），
    并在文本末尾追加隐私风险报告。
    """
    import openpyxl

    # zip 炸弹预检（仅 .xlsx 为 zip 容器；.xls 为 OLE 二进制非 zip，跳过）
    if filepath.lower().endswith(".xlsx"):
        rejected = _check_zip_bomb(filepath)
        if rejected:
            return rejected

    scanner = None
    if scan_privacy:
        from yindun.utils.privacy_scanner import PrivacyScanner
        scanner = PrivacyScanner()

    all_scan_results = []

    wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
    lines = []
    for ws in wb.worksheets:
        lines.append(f"=== 工作表: {ws.title} ===")
        for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            row_str = " | ".join(str(c) if c else "" for c in row)
            lines.append(row_str)
            if scan_privacy and row_str.strip():
                # 每行单独扫描，line=row_idx 精确定位，跨工作表不会冲突
                results = scanner.scan(row_str, page=ws.title, line_start=row_idx, source_type="sheet")
                all_scan_results.extend(results)

    wb.close()

    result = "\n".join(lines)
    if scan_privacy and all_scan_results:
        report = scanner.generate_report(all_scan_results)
        result = result + "\n\n" + report
    return result


def _transcribe_audio(filepath):
    """
    音频文件转写：使用 FunASR Paraformer-large 进行语音转文字
    中文高精度，带标点恢复
    """
    fname = os.path.basename(filepath)
    fsize = os.path.getsize(filepath)
    fsize_mb = fsize / (1024 * 1024)
    ext = os.path.splitext(filepath)[1].lower()

    duration_sec = _get_audio_duration(filepath, ext)
    duration_str = _format_duration(duration_sec) if duration_sec else "未知"

    lines = []
    lines.append("=" * 60)
    lines.append("【音频转写内容】")
    lines.append(f"文件名: {fname}")
    lines.append(f"格式: {ext[1:].upper() if ext.startswith('.') else ext.upper()} | 时长: {duration_str} | 大小: {fsize_mb:.1f} MB")
    lines.append("=" * 60)
    lines.append("")

    transcript = _funasr_transcribe(filepath)

    if transcript and transcript.strip():
        lines.append("【语音转写文本】")
        lines.append(transcript.strip())
    else:
        lines.append("[注] 未能识别到语音内容")
        lines.append("")
        lines.append("如需查看该音频的详细技术参数，请另询「该音频的元数据/技术参数」")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def _funasr_transcribe(filepath):
    """
    使用 FunASR（Paraformer-large）进行中文语音转写
    — 中文识别精度高
    — 自动恢复标点符号
    — 支持长音频分段处理
    """
    try:
        from funasr import AutoModel

        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

        model_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
        os.makedirs(model_dir, exist_ok=True)

        # 加载 Paraformer-large（中文高精度）
        model = AutoModel(
            model="paraformer-zh",
            vad_model="fsmn-vad",
            punc_model="ct-punc",
            disable_update=True,
        )

        # 执行转写
        result = model.generate(
            input=filepath,
            batch_size_s=300,
        )

        # 收集结果（支持多段）
        transcript_lines = []
        if result and isinstance(result, list):
            for item in result:
                text = item.get("text", "").strip()
                if text:
                    transcript_lines.append(text)

        if transcript_lines:
            return "\n".join(transcript_lines)
        return ""

    except ImportError:
        return _build_install_guide("funasr")
    except Exception as e:
        return f"[语音转写出错: {type(e).__name__}: {e}]"


def _build_install_guide(pkg_name):
    """生成依赖安装指引"""
    lines = []
    lines.append(f"[需要安装 {pkg_name}]")
    lines.append("未检测到语音转写库，无法进行语音转写。")
    lines.append("")
    lines.append("请运行以下命令安装：")
    lines.append("")
    lines.append("  pip install funasr")
    lines.append("  pip install modelscope")
    lines.append("  pip install huggingface_hub")
    lines.append("")
    lines.append("安装后首次运行会自动下载模型（约 220MB）")
    lines.append("模型存储位置：yindun/models/")
    lines.append("")
    lines.append("如下载较慢，可设置环境变量使用国内镜像：")
    lines.append("  set HF_ENDPOINT=https://hf-mirror.com")
    return "\n".join(lines)


def _get_audio_duration(filepath, ext):
    """获取音频时长（秒）。仅支持 WAV 格式通过 wave 标准库读取。"""
    if ext == ".wav":
        try:
            import wave
            with wave.open(filepath, "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except Exception:
            pass
    return None


def _format_duration(seconds):
    """格式化时长为可读字符串"""
    if not seconds:
        return "未知"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m}m {s}s"
    elif m > 0:
        return f"{m}m {s}s"
    else:
        return f"{s}s"


def _format_timestamp(seconds):
    """格式化时间戳为 MM:SS 格式"""
    if seconds is None:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
