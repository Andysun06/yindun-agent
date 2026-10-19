# -*- coding: utf-8 -*-
"""隐盾 · 图片文字识别（离线 OCR）后端选择

为什么要单独一个模块：负责人问「图片能不能让模型理解」，而图片里的文字是**唯一**能在
"模型只见占位符"这条承诺下送进模型的形态（原始像素不离机）。OCR 引擎选型做了取舍实测：

  · **Windows 自带 OCR（WinRT）**：`winrt-*` 命名空间包合计约 **4.3 MB**，用系统已安装的
    语言包（实测本机 en-US / zh-Hans-CN），纯 CPU、一张 A4 截图约 **0.05 秒**；
  · `rapidocr`（自带 PP-OCRv6 离线模型，中文精度略高）：要拖 **opencv 112MB + 模型 30MB**，
    整包约 +150MB —— 与"尽量别增体积"直接冲突，因此**只在用户自己装了它时才用**（不随包发）。

所以这里的优先级是：**Windows 内置 OCR 优先 → 装了 rapidocr 就用它 → 都没有就如实说"需要 OCR 组件"**，
绝不假装读到了内容（这条是本项目的硬约定：读不出来要给出可行动原因）。

★ 隐私边界：图片**不落盘**——WinRT 那条走 `InMemoryRandomAccessStream`（内存流）送进引擎，
  不写临时文件。识别出的文字随后照常进脱敏网关，与其它文本附件同一条管线。
"""
from __future__ import annotations

import re
import sys
from typing import Optional, Tuple

# Windows OCR 单张图的最大边长（超过要缩小，否则引擎直接拒绝）
_WINRT_MAX_DIM = 2000

# CJK 区间：统一表意文字 / 中日韩标点 / 全角字符
_CJK = r"\u4e00-\u9fff\u3400-\u4dbf\u3000-\u303f\uff00-\uffef"


def normalize_ocr_text(text: str) -> str:
    """整理 OCR 输出的空格。

    为什么必须做：Windows OCR（中文模型）会在**每个汉字之间**插空格——"甲方：张伟" 出成
    "甲 方 ： 张 伟"。原样送进模型既费 token 又干扰理解；而"拉丁词之间的空格"必须保留
    （"CONTRACT NO" 不能粘成 "CONTRACTNO"）。所以规则是**只**处理与 CJK 相邻的空格，
    外加一条很窄的特例：`1 3812345678` 这种"单位数与长数字被切开"的形态（实测 Windows OCR
    会把电话号码的第一位单独切开）。不做"所有数字之间的空格都合并"——表格里两列数字
    "100 200" 粘成 "100200" 是错的。
    """
    out = str(text or "")
    if not out:
        return out
    out = re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", out)      # 汉字 汉字
    out = re.sub(rf"(?<=[{_CJK}])\s+(?=[0-9A-Za-z])", "", out)    # 汉字 数字/字母
    out = re.sub(rf"(?<=[0-9A-Za-z])\s+(?=[{_CJK}])", "", out)    # 数字/字母 汉字
    out = re.sub(r"(?<!\d)(\d)\s(?=\d{4,})", r"\1", out)          # 单位数 + 长数字（电话号码被切开）
    return out


def _png_bytes(image_bytes: bytes, max_dim: int = _WINRT_MAX_DIM) -> bytes:
    """把任意图片规范成 OCR 引擎好处理的 PNG（必要时等比缩小）。

    Windows OCR 对超长边的图会直接报错；用 PIL（既有依赖）缩一下比让引擎拒了更可控。
    也顺带统一格式：JPEG/BMP/TIFF/WebP 都变成 PNG，后续只走一条路径。
    """
    try:
        import io

        from PIL import Image
        with Image.open(io.BytesIO(image_bytes)) as im:
            im = im.convert("RGB")
            w, h = im.size
            longest = max(w, h)
            if longest > max_dim:
                scale = max_dim / float(longest)
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))))
            buf = io.BytesIO()
            im.save(buf, format="PNG")
            return buf.getvalue()
    except Exception:
        return image_bytes          # 缩不了就原样交给引擎（它自己会判断能不能读）


def _ocr_winrt(png: bytes) -> Tuple[str, str]:
    """Windows 自带 OCR。返回 (文本, 说明)；失败返回 ("", 原因)。"""
    if sys.platform != "win32":
        return "", "非 Windows 平台"
    try:
        import asyncio

        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream
    except Exception as exc:                                        # noqa: BLE001
        return "", f"未安装 Windows OCR 组件（{type(exc).__name__}）"

    async def _run() -> str:
        engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            for tag in ("zh-Hans-CN", "zh-CN", "en-US"):
                try:
                    engine = OcrEngine.try_create_from_language(Language(tag))
                except Exception:                                   # noqa: BLE001
                    engine = None
                if engine is not None:
                    break
        if engine is None:
            return ""
        stream = InMemoryRandomAccessStream()                        # ★ 内存流：图片不落盘
        writer = DataWriter(stream)
        writer.write_bytes(png)
        await writer.store_async()
        await writer.flush_async()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    try:
        text = asyncio.run(_run())
    except Exception as exc:                                        # noqa: BLE001
        return "", f"Windows OCR 调用失败（{type(exc).__name__}: {exc}）"
    if not text.strip():
        return "", "Windows OCR 没识别到文字（图片可能是纯图形/分辨率过低）"
    return text, "Windows 内置 OCR"


def _ocr_rapidocr(image_bytes: bytes) -> Tuple[str, str]:
    """rapidocr（自带离线模型）。只在用户自己装了的时候可用——随包会多约 150MB。"""
    for mod in ("rapidocr", "rapidocr_onnxruntime"):
        try:
            mod_obj = __import__(mod, fromlist=["RapidOCR"])
        except Exception:                                           # noqa: BLE001
            continue
        try:
            import io

            import numpy as np
            from PIL import Image
            with Image.open(io.BytesIO(image_bytes)) as im:
                arr = np.array(im.convert("RGB"))
            out = mod_obj.RapidOCR()(arr)
            txts = getattr(out, "txts", None)
            if txts is None and isinstance(out, tuple):
                txts = out[1]
            if txts:
                lines = [t if isinstance(t, str) else (t[1] if len(t) > 1 else "")
                         for t in txts]
                return "\n".join(x for x in lines if x), f"{mod} 离线模型"
        except Exception as exc:                                    # noqa: BLE001
            return "", f"{mod} 调用失败（{type(exc).__name__}: {exc}）"
    return "", "未安装 rapidocr / rapidocr_onnxruntime"


def available_engines() -> list:
    """当前环境有哪些 OCR 后端可用（给界面/日志与自检用，不真正识别）。"""
    out = []
    if sys.platform == "win32":
        try:
            from winrt.windows.media.ocr import OcrEngine     # noqa: F401
            out.append("Windows 内置 OCR")
        except Exception:                                       # noqa: BLE001
            pass
    for mod in ("rapidocr", "rapidocr_onnxruntime"):
        try:
            __import__(mod)
            out.append(mod)
        except Exception:                                       # noqa: BLE001
            pass
    return out


def ocr_image(image_bytes: bytes, path_hint: str = "") -> Tuple[str, str]:
    """识别图片里的文字。返回 (文本, 引擎说明)；识别不了时文本为空、说明里写清原因。

    调用方（`document_parser`）负责把空结果翻成用户能读懂的失败原因；这里只负责
    "尽力而为 + 说清用的是谁 / 为什么用不了"。
    """
    if not image_bytes:
        return "", "图片内容为空"
    png = _png_bytes(image_bytes)
    text, how = _ocr_winrt(png)
    if text:
        return normalize_ocr_text(text), how
    winrt_why = how
    text2, how2 = _ocr_rapidocr(image_bytes)
    if text2:
        return normalize_ocr_text(text2), how2
    engines = available_engines()
    why = ("；".join(x for x in (winrt_why, how2) if x) or "没有可用的 OCR 引擎")
    return "", why + (f"（当前可用引擎：{'、'.join(engines)}）" if engines else "")


def ocr_path(path: str) -> Tuple[str, str]:
    """按路径识别图片。读文件失败也走"说明原因"这条路，不抛给上层。"""
    try:
        with open(path, "rb") as fh:                                # noqa: PTH123（只读，路径由调用方校验过沙箱）
            return ocr_image(fh.read(), path)
    except Exception as exc:                                        # noqa: BLE001
        return "", f"读取图片失败（{type(exc).__name__}: {exc}）"
