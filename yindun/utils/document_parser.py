# -*- coding: utf-8 -*-
"""
隐盾 V3.x — 📂 离线机密文档解析舱
专门处理离线 PDF、Word、Excel、文本文件及音频文件的全自动内容读取
音频转写使用 FunASR (Paraformer-large) — 中文高精度语音识别
"""
import os

_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")


# ──────────────────────────────────────────────
# PDF 解析器：PyMuPDF 主文字 + pdfplumber 表格
# ──────────────────────────────────────────────
class PdfParser:
    """PDF 文档解析器
    - PyMuPDF (fitz) 提取主文字，按页输出
    - pdfplumber 专门抽表格，转 Markdown 格式
    - 扫描件（无文字）返回明确提示
    """

    def parse(self, filepath: str) -> str:
        text_parts = []
        table_parts = []
        had_text = False  # 是否抽到过任何文字

        # 1) PyMuPDF 抽文字（缺失时回退到 PyPDF2）
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(filepath)
            for i, page in enumerate(doc, start=1):
                page_text = page.get_text("text") or ""
                text_parts.append(f"--- 第 {i} 页 ---")
                if page_text.strip():
                    had_text = True
                    text_parts.append(page_text.strip())
                else:
                    text_parts.append("[本页无可提取文字]")
                text_parts.append("")
            doc.close()
        except ImportError:
            # PyMuPDF 未装，回退到 PyPDF2（兜底）
            try:
                from PyPDF2 import PdfReader
                reader = PdfReader(filepath)
                for i, page in enumerate(reader.pages, start=1):
                    page_text = page.extract_text() or ""
                    text_parts.append(f"--- 第 {i} 页 ---")
                    if page_text.strip():
                        had_text = True
                        text_parts.append(page_text.strip())
                    else:
                        text_parts.append("[本页无可提取文字]")
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
                for i, page in enumerate(pdf.pages, start=1):
                    tables = page.extract_tables() or []
                    for j, tbl in enumerate(tables, start=1):
                        if not tbl:
                            continue
                        md = self._table_to_markdown(tbl)
                        table_parts.append(f"第 {i} 页 表格 {j}\n{md}\n")
        except ImportError:
            # pdfplumber 未装不算致命，表格部分跳过即可
            pass
        except Exception:
            # 表格抽取失败不阻断主文字
            pass

        # 3) 扫描件兜底
        if not had_text and not table_parts:
            return "[PDF 无可提取文字，可能是扫描件，建议 OCR]"

        result = "\n".join(text_parts)
        if table_parts:
            result += "\n【表格数据】\n" + "\n".join(table_parts)
        return result

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

    def parse(self, filepath: str) -> str:
        try:
            from docx import Document
            from docx.oxml.ns import qn
        except ImportError:
            raise RuntimeError("未安装 python-docx，请运行: pip install python-docx")

        doc = Document(filepath)
        body = doc.element.body
        parts = []
        table_idx = 0
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

        if image_count > 0:
            parts.append(f"[文档含 {image_count} 张内嵌图片，未自动解析]")

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


def extract_file_text(filepath):
    """
    自适应跨平台离线文件文本提取矩阵
    支持：TXT, MD, CSV, PDF, DOCX, XLSX, XLS, 及常见音频格式（MP3/WAV/FLAC/M4A/AAC/OGG/OPUS/WMA）
    """
    name = filepath.lower()
    try:
        if name.endswith(_AUDIO_EXTS):
            return _transcribe_audio(filepath)

        if name.endswith((".txt", ".md", ".csv")):
            for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
                try:
                    with open(filepath, "r", encoding=enc) as f:
                        return f.read()
                except:
                    continue
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                return f.read()

        if name.endswith(".pdf"):
            return PdfParser().parse(filepath)

        if name.endswith(".docx"):
            return DocxParser().parse(filepath)

        if name.endswith((".xlsx", ".xls")):
            import openpyxl
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"=== 工作表: {ws.title} ===")
                for row in ws.iter_rows(values_only=True):
                    lines.append(" | ".join(str(c) if c else "" for c in row))
            wb.close()
            return "\n".join(lines)

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    except Exception as e:
        # 区分文档类型给出更友好的错误信息
        ext = os.path.splitext(filepath)[1].lower().lstrip('.')
        return f"[文档解析失败: {ext or '未知'} -> {type(e).__name__}: {e}]"


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
