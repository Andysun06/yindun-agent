# -*- coding: utf-8 -*-
"""
隐盾 V3.x — 📂 离线机密文档解析舱
专门处理离线 PDF、Word、PPT、Excel、文本、图片及音频文件的全自动内容读取
音频转写使用 FunASR (Paraformer-large) — 中文高精度语音识别
扫描件 PDF / 图片使用 RapidOCR (ONNX) — 离线中文 OCR

★ 解析结果的两种形态（务必分清，见 PARSE_FAIL_PREFIXES）：
  成功 → 文档正文；失败 → **一段明确的、可行动的说明文本**。
  失败是以"字符串"形式返回的（历史设计，非异常），因此判定必须走 `is_parse_failure()`
  而不是"text 是否为空"——否则失败提示会被下游当成"已解析出的正文"送进模型上下文。
"""
import os
import re
import zipfile
from typing import Optional

_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")

# 图片：走 OCR 分支（本机装了可用的 OCR 引擎就出字，没装就如实说明原因）
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")

# 纯文本家族：按 utf-8 → gbk → … 自适应解码即可读取（扩展名清单与 app/attachment.SUPPORTED_EXTS 对齐，
# 否则会出现"文件对话框里选不到、但其实读得了"的错配）
_TEXT_EXTS = (".txt", ".text", ".md", ".csv", ".tsv", ".json", ".html", ".htm",
              ".log", ".yaml", ".yml", ".xml", ".ini", ".conf", ".rst")

# 已知但内核不解析的二进制格式 → 给"下一步怎么做"（而不是笼统一句"已跳过"）
_UNSUPPORTED_BINARY_ACTIONS = {
    ".doc": "旧版 Word（.doc，OLE 二进制）内核读不了；请在 Word/WPS 里「另存为 .docx」后重新上传",
    ".zip": "压缩包（.zip）的内容不会被解析；请先解压，再上传其中的文档",
    ".rar": "压缩包（.rar）的内容不会被解析；请先解压，再上传其中的文档",
    ".7z": "压缩包（.7z）的内容不会被解析；请先解压，再上传其中的文档",
    ".tar": "压缩包（.tar）的内容不会被解析；请先解压，再上传其中的文档",
    ".gz": "压缩包（.gz）的内容不会被解析；请先解压，再上传其中的文档",
    ".eml": "邮件（.eml）需启用「扩展附件解析」插件后才能读取；或把邮件正文复制成 .txt 上传",
    ".msg": "Outlook 邮件（.msg）需启用「扩展附件解析」插件后才能读取；或把邮件正文复制成 .txt 上传",
}

# ──────────────────────────────────────────────
# 解析失败的**结构化判定**（唯一出处）
# ──────────────────────────────────────────────
# 为什么要把这件事收敛成一个常量 + 一个判定函数：解析器把"失败"编码成一段非空字符串返回，
# 下游只要按"text 非空 = 解析成功"处理，就会出现真实事故——图片/PPTX 附件在界面上显示
# "已挂载 22 字"、审计记为成功，而这 22 字其实是"[文档解析: …已跳过]"，还被当成正文拼进了
# 模型上下文，模型读到的"文件内容"就是这句提示。所以：
#   · 新增失败提示一律以 PARSE_FAIL_PREFIXES 里的前缀开头；
#   · 判定只调用 is_parse_failure()，禁止各处再写 startswith 各判一套。
PARSE_FAIL_PREFIXES = (
    "[文档解析:",           # 通用失败/跳过（如 "[文档解析: 二进制/不可解码文件…]"）
    "[文档解析失败:",       # 解析抛错
    "[文档解析拒绝:",       # 资源预检拒绝（超大文件 / zip 炸弹 / 页数超限）
    "[PDF 全文无可提取文字",  # PDF 既无文字层也 OCR 不出
)

# 解析失败提示的统一说明片段（拼接用，避免各处各写一份安装指引）
OCR_INSTALL_HINT = (
    "本机没有可用的离线 OCR 引擎：Python 3.13 无法安装 rapidocr-onnxruntime（该包仅支持 <3.13），"
    "可选装支持 3.13 的 rapidocr（模型随包自带、离线可用）；"
    "或把图片/扫描件转成可复制的文本、PDF 版再上传"
)

FUNASR_INSTALL_HINT = (
    "语音转写需要 FunASR（依赖 torch，客户端 exe 明确排除该依赖），当前环境未安装 funasr；"
    "可提供该音频的文字稿（txt/docx/PDF）后上传"
)


def is_parse_failure(text) -> bool:
    """解析结果是否为**失败提示**（而不是文档正文）。

    与 `not text` 的区别：空串表示"没有内容"，失败提示表示"有明确原因、需要用户处理"。
    两者都不能当正文用，但只有后者能告诉用户/模型"为什么读不到"。
    """
    if not text:
        return False
    return text.lstrip().startswith(PARSE_FAIL_PREFIXES)


def parse_failure_reason(text) -> str:
    """把失败提示整理成一句给界面/模型看的原因说明（去掉 "[xxx: …]" 外壳）。"""
    if not is_parse_failure(text):
        return ""
    reason = text.strip()
    # 失败提示可能被"隐私风险报告"等后缀拼接，只看第一段
    reason = reason.split("\n", 1)[0].strip()
    for prefix in PARSE_FAIL_PREFIXES:
        if reason.startswith(prefix):
            reason = reason[len(prefix):].strip()
            break
    return reason.strip("[] ").strip()

# ── 资源预检上限（防恶意文档耗尽内存）──────────────────────
MAX_FILE_SIZE = 50 * 1024 * 1024      # 文件大小上限：50MB
MAX_PDF_PAGES = 200                   # PDF 页数上限
MAX_PDF_PAGE_SIZE = 20000             # 页面任一边尺寸上限（pt），超限跳过该页 OCR 渲染
MAX_ZIP_ENTRIES = 10000               # zip 容器条目数上限
MAX_ZIP_RATIO = 100                   # 解压后大小 / 压缩前大小 比值上限


# ──────────────────────────────────────────────
# 离线 OCR 引擎（PDF 扫描件 + 独立图片共用）
# ──────────────────────────────────────────────
# 两代包名都要认：
#   · rapidocr_onnxruntime（1.4.4 及以前）—— 包元数据写明 requires_python <3.13，
#     在 Python 3.13 上**装不上**（这是"扫描件在 3.13 上读不到"的根因，别再往它上面使劲）；
#   · rapidocr（2.x/3.x）—— 支持 3.13，且检测/识别模型随 wheel 自带（32MB），全程离线。
# 谁在环境里就用谁；一个都没有时返回 None + 明确原因，由调用方如实告知用户。
_OCR_ENGINE_MODULES = ("rapidocr_onnxruntime", "rapidocr")
_OCR_STATE = {"tried": False, "engine": None, "name": "", "reason": ""}


def _ocr_engine():
    """懒加载 OCR 引擎。返回 (engine, 名称)；不可用时返回 (None, 原因说明)。

    懒加载的理由：OCR 初始化要加载 ONNX 模型（秒级、占内存），而大多数附件根本不需要 OCR。
    """
    if not _OCR_STATE["tried"]:
        _OCR_STATE["tried"] = True
        for module in _OCR_ENGINE_MODULES:
            try:
                mod = __import__(module, fromlist=["RapidOCR"])
                _OCR_STATE["engine"] = mod.RapidOCR()
                _OCR_STATE["name"] = module
                break
            except Exception as exc:      # 未安装 / 装不上 / 初始化失败，一律尝试下一个
                _OCR_STATE["reason"] = f"{module}: {type(exc).__name__}"
                print(f"[文档解析] OCR 引擎 {module} 不可用：{type(exc).__name__}: {exc}")
    if _OCR_STATE["engine"] is None:
        return None, OCR_INSTALL_HINT
    return _OCR_STATE["engine"], _OCR_STATE["name"]


def _ocr_lines_from_result(out) -> list:
    """把不同版本 OCR 引擎的返回值归一成"文本行列表"。

    两代 API 形状不同（这就是必须归一的原因）：
      · rapidocr_onnxruntime 1.x：`(result, elapsed)`，result 是 [[box, text, score], …]；
      · rapidocr 2.x/3.x        ：直接返回 RapidOCROutput，文本在 `.txts`。
    纯函数、无副作用，便于单测覆盖两种形状（3.13 上装不了 1.x，但 3.11 的 CI 装得上）。
    """
    if out is None:
        return []
    # rapidocr 2.x/3.x：带 .txts 的结果对象
    txts = getattr(out, "txts", None)
    if txts:
        return [line for line in txts if line]
    # rapidocr_onnxruntime 1.x：(result, elapsed)
    if isinstance(out, tuple) and len(out) == 2 and isinstance(out[1], (int, float)):
        rows = out[0] or []
        lines = []
        for item in rows:
            if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
                lines.append(str(item[1]))
        return lines
    # 少数版本会把 RapidOCROutput 再包一层元组/列表
    if isinstance(out, (list, tuple)) and len(out) == 1:
        return _ocr_lines_from_result(out[0])
    return []


def _ocr_ndarray(image):
    """把图片路径 / 字节 / PIL.Image / numpy 数组统一成 OCR 引擎最通用的入参（BGR ndarray）。

    为什么不直接把原始对象丢给引擎：两代引擎支持的入参不完全一样
    （1.x 对 bytes 的兼容时好时坏），而 numpy 数组是两边都稳的公共形状。
    """
    import numpy as np
    if isinstance(image, np.ndarray):
        return image
    if isinstance(image, (bytes, bytearray)):
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(bytes(image)))
    elif isinstance(image, str):
        from PIL import Image
        img = Image.open(image)
    else:                                     # 已是 PIL.Image
        img = image
    rgb = img.convert("RGB")
    return np.array(rgb)[:, :, ::-1]          # RGB → BGR（OpenCV 约定）


def _ocr_image(image) -> list:
    """对一张图片（路径/字节/PIL/ndarray）执行 OCR，返回文本行列表；引擎不可用返回 []。"""
    engine, _name = _ocr_engine()
    if engine is None:
        return []
    try:
        return [line for line in _ocr_lines_from_result(engine(_ocr_ndarray(image))) if line]
    except Exception as exc:
        print(f"[文档解析] OCR 识别失败：{type(exc).__name__}: {exc}")
        return []


# ──────────────────────────────────────────────
# PDF 解析器：PyMuPDF 主文字 + pdfplumber 表格 + RapidOCR 扫描件回退
# ──────────────────────────────────────────────
class PdfParser:
    """PDF 文档解析器
    - PyMuPDF (fitz) 提取主文字，按页输出
    - pdfplumber 专门抽表格，转 Markdown 格式
    - 扫描件（无文字层）自动渲染图片 → 离线 OCR 中文识别
    """

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
        """兼容旧调用点：返回引擎实例或 None（不可用时）。"""
        engine, _name = _ocr_engine()
        return engine

    @staticmethod
    def _ocr_page(page) -> str:
        """
        将 PyMuPDF 页面对象渲染为图片并执行 OCR 识别。
        返回识别到的纯文本（按行拼接，已去重空白）。
        若 OCR 引擎不可用或识别失败，返回空字符串。
        """
        if _ocr_engine()[0] is None:
            return ""
        try:
            # 渲染为高分辨率图片（DPI=300 兼顾精度与速度）
            pix = page.get_pixmap(dpi=300)
            lines = _ocr_image(pix.tobytes("png"))
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
                            # OCR 不可用或未识别到内容：给出准确提示（"未安装"与"没识别到"要分清）
                            if _ocr_engine()[0] is None and not ocr_unavailable_warned:
                                text_parts.append(
                                    f"[本页为扫描图片，文字层为空；无法识别其中文字。{OCR_INSTALL_HINT}]"
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
                            "[本页文字层为空；PyMuPDF 未安装，无法渲染页面做 OCR。"
                            "请运行: pip install PyMuPDF]"
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
            if _ocr_engine()[0] is None:
                return f"[PDF 全文无可提取文字。{OCR_INSTALL_HINT}]"
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
# PPTX 解析器：按页提取标题 / 文本框 / 表格 / 备注
# ──────────────────────────────────────────────
class PptxParser:
    """PPTX 演示文稿解析器

    为什么页号必须显式标出来：演示文稿的实际用法就是"看第 N 页"——用户会问
    "第 3 页那三个指标是多少"。输出里每页都有 `--- 第 N 页 ---` 锚点，
    模型回答时才能给出来源页号，而不是含糊地说"文档里提到"。

    提取范围：页标题、正文文本框、表格（转 Markdown）、演讲者备注（往往是真正的讲解内容），
    并统计内嵌图片数量（图片本身不解析，需要 OCR，如实标注）。
    """

    def __init__(self):
        self._scanner = None  # 懒加载，避免无扫描需求时初始化开销

    def _get_scanner(self):
        """懒加载 PrivacyScanner"""
        if self._scanner is None:
            from yindun.utils.privacy_scanner import PrivacyScanner
            self._scanner = PrivacyScanner()
        return self._scanner

    def parse(self, filepath: str, scan_privacy: bool = False) -> str:
        try:
            from pptx import Presentation
        except ImportError:
            raise RuntimeError("未安装 python-pptx，请运行: pip install python-pptx")

        # PPTX 同样是 zip 容器：先做 zip 炸弹预检，再交给 python-pptx
        rejected = _check_zip_bomb(filepath)
        if rejected:
            return rejected

        prs = Presentation(filepath)
        parts = []
        all_scan_results = []
        page_no = 0

        for slide in prs.slides:
            page_no += 1
            parts.append(f"--- 第 {page_no} 页 ---")
            page_lines = []
            image_count = 0

            # 标题单独标出来：模型引用"第 N 页的标题"时最有用
            title_shape = slide.shapes.title
            # 用 shape_id 判断"这个形状是不是标题"：python-pptx 每次访问 shapes 都会新建代理对象，
            # `is` 比较永远不相等，会导致标题被输出两遍（白占上下文）
            title_id = title_shape.shape_id if title_shape is not None else None
            if title_shape is not None and title_shape.has_text_frame:
                title = title_shape.text_frame.text.strip()
                if title:
                    page_lines.append(f"【标题】{title}")

            for shape in slide.shapes:
                try:
                    if shape.has_text_frame:
                        text = shape.text_frame.text.strip()
                        # 标题已在上面单独输出，避免重复占上下文
                        if text and shape.shape_id != title_id:
                            page_lines.append(text)
                    elif getattr(shape, "has_table", False) and shape.has_table:
                        md = _table_frame_to_markdown(shape.table)
                        if md:
                            page_lines.append(md)
                    elif _is_picture_shape(shape):
                        image_count += 1
                except Exception as exc:
                    # 单个形状解析失败不该毁掉整页（PPT 里常有异常形状）
                    print(f"[文档解析] 第 {page_no} 页形状解析跳过：{type(exc).__name__}: {exc}")

            # 演讲者备注：讲解场合的真内容常常只在这里
            try:
                if slide.has_notes_slide:
                    notes = slide.notes_slide.notes_text_frame.text.strip()
                    if notes:
                        page_lines.append(f"【备注】{notes}")
            except Exception as exc:
                print(f"[文档解析] 第 {page_no} 页备注解析跳过：{type(exc).__name__}: {exc}")

            if image_count > 0:
                page_lines.append(f"[本页含 {image_count} 张图片，未自动解析]")

            parts.extend(page_lines)
            if scan_privacy and page_lines:
                results = self._get_scanner().scan("\n".join(page_lines), page=str(page_no),
                                                   line_start=1, source_type="page")
                all_scan_results.extend(results)
            parts.append("")

        if page_no == 0:
            return "[文档解析失败: 该 PPTX 里没有任何幻灯片（文件可能已损坏）]"

        text = "\n".join(parts).strip()
        if scan_privacy and all_scan_results:
            text = text + "\n\n" + self._get_scanner().generate_report(all_scan_results)
        return text


def _is_picture_shape(shape) -> bool:
    """形状是否为图片。用枚举比较而不是比较字符串：`str(shape_type)` 的写法跨版本会变。"""
    try:
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        return shape.shape_type == MSO_SHAPE_TYPE.PICTURE
    except Exception:
        return False


def _table_frame_to_markdown(table) -> str:
    """python-pptx Table 对象转 Markdown（与 docx/excel 的行列口径一致）。"""
    md = []
    for row_idx, row in enumerate(table.rows):
        cells = []
        for cell in row.cells:
            value = " ".join(part.strip() for part in (cell.text or "").splitlines() if part.strip())
            cells.append(value)
        if not any(cells):
            continue
        md.append("| " + " | ".join(cells) + " |")
        if row_idx == 0:
            md.append("| " + " | ".join("---" for _ in cells) + " |")
    return "\n".join(md)


# ──────────────────────────────────────────────
# 图片解析器：离线 OCR（引擎缺失时如实说明，不假装支持）
# ──────────────────────────────────────────────
def _extract_image(filepath: str, scan_privacy: bool = False) -> str:
    """图片里的文字：能 OCR 就出字，不能就给出**可行动**的原因。

    ★ 为什么必须有这条分支：图片/扫描件是用户最常上传的一类附件，旧实现走"未知扩展名兜底"
      被 `_looks_binary` 判成二进制直接跳过——用户看到的是"已挂载，22 字"，实际一个字都没读到。
    """
    engine, engine_name = _ocr_engine()
    ext = os.path.splitext(filepath)[1].lower()
    if engine is None:
        return f"[文档解析失败: 该文件是图片（{ext}），其中的文字需要离线 OCR 组件才能识别；{OCR_INSTALL_HINT}]"

    lines = _ocr_image(filepath)
    if not lines:
        return (f"[文档解析失败: 已用离线 OCR（{engine_name}）识别该图片（{ext}），未识别到文字。"
                "请确认图片里确有文字、且文字足够清晰（截图通常可以，照片/倾斜/过小字识别率会明显下降）]")

    text = "\n".join(lines).strip()
    result = f"[图片 OCR 识别内容（引擎 {engine_name}，可能存在识别误差）]\n{text}"
    if scan_privacy:
        from yindun.utils.privacy_scanner import PrivacyScanner
        scanner = PrivacyScanner()
        results = scanner.scan(text, page="1", line_start=1, source_type="page")
        if results:
            result = result + "\n\n" + scanner.generate_report(results)
    return result


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
    支持：TXT/MD/CSV/TSV/JSON/HTML/LOG/YAML/XML/INI 等文本、PDF、DOCX、PPTX、XLSX、XLS、
    图片（PNG/JPG/…，需 OCR 组件）、常见音频格式（需 FunASR）。
    """
    try:
        # 文件大小预检：超上限直接拒绝，不进入任何解析路径
        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            return f"[文档解析拒绝: 文件超过 {MAX_FILE_SIZE // (1024 * 1024)}MB 大小上限]"
        return _parse_by_type(filepath, scan_privacy=False)
    except Exception as e:
        # 区分文档类型给出更友好的错误信息
        ext = os.path.splitext(filepath)[1].lower().lstrip('.')
        return f"[文档解析失败: {ext or '未知'} -> {type(e).__name__}: {e}]"


def _read_text_file(filepath) -> str:
    """读取文本文件，自动尝试多种编码；先检测二进制特征避免硬解码"""
    if _looks_binary(filepath):
        return _unsupported_binary_message(os.path.splitext(filepath)[1].lower())
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

    支持 PDF/DOCX/PPTX/XLSX/XLS/文本类/图片（OCR）。
    音频文件不支持隐私扫描（转写文本已含在结果中，但不附加报告）。
    """
    try:
        # 文件大小预检：超上限直接拒绝，不进入任何解析路径
        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            return f"[文档解析拒绝: 文件超过 {MAX_FILE_SIZE // (1024 * 1024)}MB 大小上限]"
        return _parse_by_type(filepath, scan_privacy=scan_privacy)
    except Exception as e:
        # 扫描失败时回退到纯文本模式，并附加错误提示
        try:
            base_text = extract_file_text(filepath)
            if is_parse_failure(base_text):
                # 本来就是"读不到"的失败说明：不要再叠一层"隐私扫描失败"把原因淹没
                return base_text
            return base_text + f"\n\n[隐私扫描失败，已回退纯文本模式: {type(e).__name__}: {e}]"
        except Exception:
            ext = os.path.splitext(filepath)[1].lower().lstrip('.')
            return f"[文档解析失败: {ext or '未知'} -> {type(e).__name__}: {e}]"


def _scan_and_append_report(text: str, scan_privacy: bool) -> str:
    """对正文做隐私扫描并附加报告。

    ★ 失败提示不扫描：失败串是"说明"，不是文档内容——扫它既无意义，
    又可能把"未发现敏感信息"之类的报告文本拼到失败提示后面，污染失败判定。
    """
    if not scan_privacy or not text or not text.strip() or is_parse_failure(text):
        return text
    from yindun.utils.privacy_scanner import PrivacyScanner
    scanner = PrivacyScanner()
    results = scanner.scan(text, page="1", line_start=1, source_type="page")
    if not results:
        return text
    return text + "\n\n" + scanner.generate_report(results)


def _parse_by_type(filepath, scan_privacy: bool) -> str:
    """按扩展名分派解析器（两个对外入口共用这一份，避免"一处补了另一处忘了"的漂移）。"""
    name = filepath.lower()
    ext = os.path.splitext(name)[1]

    if name.endswith(_AUDIO_EXTS):
        return _transcribe_audio(filepath)

    if name.endswith(_IMAGE_EXTS):
        return _extract_image(filepath, scan_privacy=scan_privacy)

    if ext in _TEXT_EXTS:
        return _scan_and_append_report(_read_text_file(filepath), scan_privacy)

    if name.endswith(".pdf"):
        return PdfParser().parse(filepath, scan_privacy=scan_privacy)

    if name.endswith(".docx"):
        return DocxParser().parse(filepath, scan_privacy=scan_privacy)

    if name.endswith(".pptx"):
        return PptxParser().parse(filepath, scan_privacy=scan_privacy)

    if name.endswith(".xlsx"):
        return _extract_excel(filepath, scan_privacy=scan_privacy)

    if name.endswith(".xls"):
        return _extract_xls(filepath, scan_privacy=scan_privacy)

    # 未知/不支持的扩展名：先检测二进制特征，避免把二进制当文本硬解码
    if _looks_binary(filepath):
        return _unsupported_binary_message(ext)
    return _scan_and_append_report(_read_text_file(filepath), scan_privacy)


def _unsupported_binary_message(ext: str) -> str:
    """二进制且内核不支持的格式：给出**可行动**的说明（旧实现只丢一句"已跳过"，
    用户既不知道读没读到，模型还会把这句话当文件内容）。"""
    action = _UNSUPPORTED_BINARY_ACTIONS.get(ext)
    if action:
        return f"[文档解析失败: {action}]"
    return (f"[文档解析失败: 该文件（{ext or '无扩展名'}）是二进制内容，不是可解析的文本/文档格式，"
            "无法提取文字；请提供文本版或 PDF 版]")


def _extract_excel(filepath, scan_privacy: bool = False) -> str:
    """解析 Excel 文件，可选隐私扫描。

    ★ 只走 openpyxl（.xlsx）。.xls 是 OLE/BIFF 二进制，openpyxl 读不了
    （会抛 InvalidFileException），必须转 xlrd——见 `_extract_xls`。

    scan_privacy=False（默认）时仅返回文本；
    scan_privacy=True 时按工作表独立扫描（page=工作表名，line_start=1），
    并在文本末尾追加隐私风险报告。
    """
    if filepath.lower().endswith(".xls"):
        # 向后兼容：老调用方把 .xls 也丢给本函数
        return _extract_xls(filepath, scan_privacy=scan_privacy)

    import openpyxl

    # zip 炸弹预检（.xlsx 是 zip 容器）
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


def _extract_xls(filepath, scan_privacy: bool = False) -> str:
    """解析旧版 Excel（.xls，OLE/BIFF）。

    ★ 为什么必须单独一条分支：openpyxl 只能读 .xlsx（zip 容器），对 .xls 会直接抛
    InvalidFileException——也就是说"支持 xls"曾经只是支持列表里的一句话，实际读不到。
    xlrd 2.x 反过来只支持 .xls（BIFF），两者正好互补，且 xlrd 已在 requirements.lock 锁定。
    """
    try:
        import xlrd
    except ImportError:
        return ("[文档解析失败: 读取旧版 Excel（.xls）需要 xlrd 组件，当前环境未安装；"
                "请运行: pip install xlrd，或把文件另存为 .xlsx 后上传]")

    scanner = None
    if scan_privacy:
        from yindun.utils.privacy_scanner import PrivacyScanner
        scanner = PrivacyScanner()
    all_scan_results = []

    try:
        wb = xlrd.open_workbook(filepath)
    except Exception as exc:
        return (f"[文档解析失败: .xls 文件无法解析（{type(exc).__name__}: {exc}）；"
                "文件可能已损坏或并非真正的 .xls 格式，请在 Excel/WPS 里另存为 .xlsx 后重试]")

    lines = []
    for sheet in wb.sheets():
        lines.append(f"=== 工作表: {sheet.name} ===")
        for row_idx in range(sheet.nrows):
            cells = [_xls_cell_text(v, wb, sheet, row_idx, col)
                     for col, v in enumerate(sheet.row_values(row_idx))]
            row_str = " | ".join(cells)
            lines.append(row_str)
            if scan_privacy and row_str.strip() and scanner is not None:
                results = scanner.scan(row_str, page=sheet.name, line_start=row_idx + 1, source_type="sheet")
                all_scan_results.extend(results)

    result = "\n".join(lines)
    if scan_privacy and all_scan_results and scanner is not None:
        result = result + "\n\n" + scanner.generate_report(all_scan_results)
    return result


def _xls_cell_text(value, workbook, sheet, row_idx: int, col_idx: int) -> str:
    """xlrd 单元格值 → 文本。

    xlrd 把整数也读成 float（1.0 而不是 1），直接把 float 印出来会让模型看到一堆 ".0"，
    因此整数值去掉小数点；日期单元格还原成 YYYY-MM-DD（否则是 45000 这种序列号）。
    """
    if value is None or value == "":
        return ""
    import xlrd
    if sheet.cell_type(row_idx, col_idx) == xlrd.XL_CELL_DATE:
        try:
            parts = xlrd.xldate_as_tuple(value, workbook.datemode)
            return f"{parts[0]:04d}-{parts[1]:02d}-{parts[2]:02d}"
        except Exception:
            return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _funasr_available() -> bool:
    """FunASR 是否可用。

    ★ 必须先探测再输出：旧实现无论有没有引擎都先打印「【音频转写内容】/【语音转写文本】」
    两个表头，再把"请安装 funasr"的安装指引塞在"转写文本"下面——看起来像"转写成功但没内容"，
    用户和模型都会被误导（模型可能据此编造音频内容）。现在引擎缺失时直接给失败说明。
    """
    try:
        import funasr  # noqa: F401
        return True
    except Exception:
        return False


def _transcribe_audio(filepath):
    """
    音频文件转写：使用 FunASR Paraformer-large 进行语音转文字
    中文高精度，带标点恢复

    FunASR 依赖 torch，而客户端 exe 明确排除 torch（见 tools/packaging/yindun_agent_portable.spec），
    因此交付形态下本分支走"如实说明"而不是假装转写。
    """
    fname = os.path.basename(filepath)
    fsize = os.path.getsize(filepath)
    fsize_mb = fsize / (1024 * 1024)
    ext = os.path.splitext(filepath)[1].lower()

    if not _funasr_available():
        return (f"[文档解析失败: 该文件是音频（{ext}，{fsize_mb:.1f} MB）。{FUNASR_INSTALL_HINT}]")

    duration_sec = _get_audio_duration(filepath, ext)
    duration_str = _format_duration(duration_sec) if duration_sec else "未知"

    try:
        transcript = _funasr_transcribe(filepath)
    except Exception as exc:
        return (f"[文档解析失败: 该文件是音频（{ext}，时长 {duration_str}），FunASR 转写失败："
                f"{type(exc).__name__}: {exc}]")

    if not (transcript and transcript.strip()):
        return (f"[文档解析失败: 已用 FunASR 转写该音频（{ext}，时长 {duration_str}），"
                "未识别到语音内容（可能是纯音乐、静音或音量过低），没有可读文本]")

    lines = []
    lines.append("=" * 60)
    lines.append("【音频转写内容】")
    lines.append(f"文件名: {fname}")
    lines.append(f"格式: {ext[1:].upper() if ext.startswith('.') else ext.upper()} | 时长: {duration_str} | 大小: {fsize_mb:.1f} MB")
    lines.append("=" * 60)
    lines.append("")
    lines.append("【语音转写文本】")
    lines.append(transcript.strip())
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
        # 引擎缺失由 _transcribe_audio 统一给出失败说明（这里返回空串，避免在
        # "【语音转写文本】"标题下塞一段安装指引，看起来像"转写成功但没内容"）
        return ""
    except Exception as e:
        # 引擎缺失由 _transcribe_audio 统一给出失败说明，这里不应再抛到外层（见下）
        raise RuntimeError(f"语音转写出错: {type(e).__name__}: {e}") from e


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
