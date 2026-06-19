# -*- coding: utf-8 -*-
"""
隐盾 V3.1.4 — 📂 离线机密文档解析舱
专门处理离线 PDF、Word、Excel、文本文件及音频文件的全自动内容读取
音频转写使用 FunASR (Paraformer-large) — 中文高精度语音识别
"""
import os

_AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma")

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
            from PyPDF2 import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(filepath).pages)

        if name.endswith(".docx"):
            from docx import Document
            return "\n".join(p.text for p in Document(filepath).paragraphs)

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
        return f"[离线文档网关读取失败: {type(e).__name__} -> {e}]"


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
    """获取音频时长（秒）"""
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(filepath)
        if audio and audio.info:
            if hasattr(audio.info, "length"):
                return audio.info.length
            if hasattr(audio.info, "duration"):
                return audio.info.duration
    except:
        pass

    if ext == ".wav":
        try:
            import wave
            with wave.open(filepath, "rb") as wf:
                return wf.getnframes() / float(wf.getframerate())
        except:
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
