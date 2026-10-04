# -*- coding: utf-8 -*-
"""内置插件 · 扩展附件解析（邮件 / 网页）

覆盖内核 `document_parser` 不处理的几种格式：
  · .eml        —— 标准库 email 解析：取主题/发件人/收件人/日期 + 正文（纯文本优先，
                  只有 HTML 正文时退化为去标签文本）
  · .html/.htm  —— 去脚本/样式后取可见文字（含表格单元格间的空白收敛）
  · .mht        —— 按 MIME multipart 处理（等同 .eml 的多部分分支）

契约：`parse(context)` 返回 {"text", "note"}。宿主会按 max_chars 截断，
并且**内核随后照常做隐私扫描与脱敏**——插件只是把文件变成文本，不碰脱敏逻辑。

只用标准库（email / html.parser），不联网、不写盘、无第三方依赖。
"""
from __future__ import annotations

import email
from email import policy
from html.parser import HTMLParser
from pathlib import Path

PLUGIN_ID = "doc_extra"
PLUGIN_DATA_DIR = ""

SKIP_TAGS = {"script", "style", "head", "noscript", "template", "svg"}
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6",
              "table", "section", "article", "blockquote", "pre"}
CELL_TAGS = {"td", "th"}


class _TextExtractor(HTMLParser):
    """把 HTML 收敛成可读纯文本（保留块级换行与单元格制表符，便于模型理解表格）。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")
        elif tag in CELL_TAGS:
            self._parts.append("\t")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._skip_depth:
            return
        if data:
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        lines = []
        for line in raw.splitlines():
            cells = [cell.strip() for cell in line.split("\t")]
            joined = " | ".join(cell for cell in cells if cell)
            cleaned = " ".join(joined.split())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)


def _html_to_text(markup: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        # 解析器遇到畸形 HTML 时不抛错，退化为朴素去标签
        pass
    return parser.text()


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except Exception:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_message(data: bytes) -> tuple[str, str]:
    """解析 MIME 消息，返回 (头信息, 正文)。

    ★ 必须按**字节**解析：`message_from_string` 会把非 ASCII 正文变成字面 "\\uXXXX"
      转义（实测 Python 3.13），中文邮件正文会因此变成一堆乱码转义符。
    """
    message = email.message_from_bytes(data, policy=policy.default)
    headers = []
    for field, label in (("Subject", "主题"), ("From", "发件人"),
                         ("To", "收件人"), ("Cc", "抄送"), ("Date", "时间")):
        try:
            value = message.get(field)
        except Exception:
            value = None
        if value:
            headers.append(f"{label}：{value}")

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[str] = []
    try:
        for part in message.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            disposition = (part.get_content_disposition() or "")
            if filename and disposition == "attachment":
                attachments.append(filename)
                continue
            content_type = (part.get_content_type() or "").lower()
            if content_type == "text/plain":
                payload = part.get_content()
                if isinstance(payload, str):
                    plain_parts.append(payload)
            elif content_type in ("text/html", "application/xhtml+xml"):
                payload = part.get_content()
                if isinstance(payload, str):
                    html_parts.append(payload)
    except Exception:
        pass

    body = "\n".join(plain_parts).strip()
    if not body and html_parts:
        body = _html_to_text("\n".join(html_parts)).strip()
    if attachments:
        headers.append("附件：" + "、".join(attachments[:20]))
    return "\n".join(headers), body


def attachment_parser(context):
    """钩子入口（函数名必须与 manifest 里声明的钩子同名，宿主按名字查找）。"""
    ctx = context if isinstance(context, dict) else {}
    path = Path(str(ctx.get("path") or ""))
    ext = str(ctx.get("ext") or path.suffix.lstrip(".")).lower()
    if not path.is_file():
        return None
    try:
        data = path.read_bytes()
    except Exception:
        return None          # 读不出来就交给界面按"解析失败"提示，不臆造内容

    if ext in ("eml", "mht"):
        headers, body = _parse_message(data)
        parts = [part for part in (headers, body) if part]
        if not parts:
            return None
        return {"text": "\n\n".join(parts),
                "note": f"按邮件格式解析（{ext}）：{'含正文' if body else '仅头部'}，附件本身未展开"}

    text = _html_to_text(_decode_text(data))
    if not text:
        return None
    return {"text": text, "note": f"按网页格式解析（{ext}）：已去除脚本/样式，仅保留可见文字"}


# 便于人工阅读/单测时按语义名调用；宿主只认钩子同名函数
parse = attachment_parser
