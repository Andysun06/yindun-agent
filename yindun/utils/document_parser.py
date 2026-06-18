# -*- coding: utf-8 -*-
"""
隐盾 V3.1.4 — 📂 离线机密文档解析舱
专门处理离线 PDF、Word、Excel、文本文件及音频文件的全自动内容读取
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
        # 0. 音频文件：优先进行语音转写（STT），元数据仅作简要说明
        if name.endswith(_AUDIO_EXTS):
            return _transcribe_audio(filepath)

        # 1. 普通文本流格式 (.txt / .md / .csv) — 支持多重字符编码自适应探测
        if name.endswith((".txt", ".md", ".csv")):
            for enc in ["utf-8", "gbk", "gb2312", "latin-1"]:
                try:
                    with open(filepath, "r", encoding=enc) as f: 
                        return f.read()
                except: 
                    continue
            # 若全部失败，启动柔性兜底读取，强行替换非法字符
            with open(filepath, "r", encoding="utf-8", errors="replace") as f: 
                return f.read()
                
        # 2. 离线标准 PDF 格式解析舱
        if name.endswith(".pdf"):
            from PyPDF2 import PdfReader
            return "\n".join(p.extract_text() or "" for p in PdfReader(filepath).pages)
            
        # 3. 离线微软 Word 格式解析舱
        if name.endswith(".docx"):
            from docx import Document
            return "\n".join(p.text for p in Document(filepath).paragraphs)
            
        # 4. 离线微软 Excel 电子表格解析舱 (支持多工作表全透视扫描)
        if name.endswith((".xlsx", ".xls")):
            import openpyxl
            # 以只读且读取公式计算结果的方式安全打开
            wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(f"=== 工作表: {ws.title} ===")
                for row in ws.iter_rows(values_only=True): 
                    # 用管道符 | 拼装每一行单元格的数据，保持数据表格结构
                    lines.append(" | ".join(str(c) if c else "" for c in row))
            wb.close()
            return "\n".join(lines)
            
        # 5. 无法识别的后缀，默认按普通文本流进行强行脱敏兜底读取
        with open(filepath, "r", encoding="utf-8", errors="replace") as f: 
            return f.read()
            
    except Exception as e: 
        return f"[离线文档网关读取失败: {type(e).__name__} -> {e}]"


def _transcribe_audio(filepath):
    """
    音频文件转写：使用 faster-whisper 进行语音转文字（STT）
    返回转写文本，附带简要元数据说明
    """
    fname = os.path.basename(filepath)
    fsize = os.path.getsize(filepath)
    fsize_mb = fsize / (1024 * 1024)
    ext = os.path.splitext(filepath)[1].lower()

    # 先获取基本元数据（简短，不喧宾夺主）
    duration_sec = _get_audio_duration(filepath, ext)
    duration_str = _format_duration(duration_sec) if duration_sec else "未知"

    lines = []
    lines.append("=" * 60)
    lines.append(f"【音频转写内容】")
    lines.append(f"文件名: {fname}")
    lines.append(f"格式: {ext[1:].upper() if ext.startswith('.') else ext.upper()} | 时长: {duration_str} | 大小: {fsize_mb:.1f} MB")
    lines.append("=" * 60)
    lines.append("")

    # 优先使用 faster-whisper 进行语音转写
    transcript = _whisper_transcribe(filepath)
    
    if transcript and transcript.strip():
        lines.append("【语音转写文本】")
        lines.append(transcript.strip())
    else:
        lines.append("[注] 未能识别到语音内容，可能原因：")
        lines.append("  1. 音频为纯音乐无人声")
        lines.append("  2. 音频为静音或噪声")
        lines.append("  3. faster-whisper 模型未正确加载")
        lines.append("")
        lines.append("如需查看该音频的详细技术参数，请另询「该音频的元数据/技术参数」")

    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def _get_audio_duration(filepath, ext):
    """获取音频时长（秒）"""
    # 尝试 mutagen
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
    
    # WAV 兜底
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


def _whisper_transcribe(filepath, language=None):
    """
    使用 faster-whisper 进行语音转文字
    支持中文、英文及多语言自动检测
    """
    try:
        from faster_whisper import WhisperModel
        import socket
        
        socket.setdefaulttimeout(30)
        
        fsize = os.path.getsize(filepath)
        duration = _get_audio_duration(filepath, os.path.splitext(filepath)[1].lower()) or 0
        
        if duration < 300:
            model_size = "small"
        elif duration < 1800:
            model_size = "base"
        else:
            model_size = "tiny"
        
        model_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")
        os.makedirs(model_dir, exist_ok=True)
        
        # 设置国内镜像（优先使用）
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
        # 禁用 Windows 符号链接警告（不影响功能）
        os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
        
        model = None
        last_error = None
        
        # 尝试加载模型（自动从镜像下载）
        try:
            model = WhisperModel(
                model_size,
                device="cpu",
                compute_type="int8",
                download_root=model_dir,
            )
        except Exception as e:
            last_error = f"下载失败: {type(e).__name__}: {e}"
        
        # 如果下载失败，检查本地缓存
        if model is None:
            local_model_path = os.path.join(model_dir, f"faster-whisper-{model_size}")
            if os.path.exists(local_model_path):
                try:
                    model = WhisperModel(
                        local_model_path,
                        device="cpu",
                        compute_type="int8",
                    )
                except Exception as e:
                    last_error = f"本地模型加载失败: {type(e).__name__}: {e}"
        
        if model is None:
            return _build_model_download_failed_message(model_size, model_dir, last_error)
        
        segments, info = model.transcribe(
            filepath,
            language=language,
            beam_size=5,
            vad_filter=True,
        )
        
        transcript_lines = []
        for segment in segments:
            text = segment.text.strip()
            if text:
                start_time = _format_timestamp(segment.start)
                transcript_lines.append(f"[{start_time}] {text}")
        
        return "\n".join(transcript_lines)
        
    except ImportError:
        return f"[需要安装 faster-whisper]\n未检测到 faster-whisper 库，无法进行语音转写。\n\n请运行以下命令安装：\npip install faster-whisper\n\n安装后会自动下载模型（约 75MB - 488MB，根据模型大小而定）。"
    except Exception as e:
        return f"[语音转写出错: {type(e).__name__}: {e}]\n\n如问题持续，请检查：\n1. 音频文件是否损坏\n2. faster-whisper 模型是否正确下载\n3. 系统内存是否充足"


def _build_model_download_failed_message(model_size, model_dir, last_error):
    """构建模型下载失败的友好提示信息"""
    lines = []
    lines.append("=" * 60)
    lines.append("【模型下载失败】")
    lines.append("=" * 60)
    lines.append(f"所需模型: {model_size}.ct2")
    lines.append(f"目标目录: {model_dir}")
    lines.append("")
    lines.append("错误信息:")
    lines.append(f"  {last_error}")
    lines.append("")
    lines.append("=" * 60)
    lines.append("【解决方案】")
    lines.append("=" * 60)
    lines.append("")
    lines.append("请手动下载模型并放置到以下目录：")
    lines.append(f"  {model_dir}")
    lines.append("")
    lines.append("模型下载地址（选择任意一个可用的）：")
    lines.append("")
    lines.append(f"1. https://hf-mirror.com/guillaumekln/{model_size}.ct2")
    lines.append(f"2. https://huggingface.co/guillaumekln/{model_size}.ct2")
    lines.append("")
    lines.append("下载步骤：")
    lines.append("  1. 打开上面的链接")
    lines.append("  2. 点击右侧的 \"Files and versions\"")
    lines.append("  3. 下载所有文件（包括 config.json, vocab.txt 等）")
    lines.append(f"  4. 在 {model_dir} 下创建 {model_size}.ct2 文件夹")
    lines.append("  5. 将所有下载的文件放入该文件夹")
    lines.append("  6. 重新挂载音频文件")
    lines.append("")
    lines.append("模型大小参考：")
    lines.append("  tiny.ct2: 约 75 MB")
    lines.append("  base.ct2: 约 140 MB")
    lines.append("  small.ct2: 约 488 MB")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def _format_timestamp(seconds):
    """格式化时间戳为 MM:SS 格式"""
    if seconds is None:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
