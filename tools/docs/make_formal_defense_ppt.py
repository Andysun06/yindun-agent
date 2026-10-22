# -*- coding: utf-8 -*-
"""生成《隐盾安全智能体汇报.pptx》正式答辩版（8 页 · 科研报告风）。

素材：_tmp_assets/ 下的真实客户端截图与审批动图（由 V3.6.20 exe / 同一套 Web UI 采集）。
输出：仓库根目录 隐盾安全智能体汇报.pptx（原稿已备份为 *_backup.pptx）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs" / "formal_assets"
OUT = ROOT / "隐盾安全智能体汇报.pptx"

W, H, M = 13.333, 7.5, 0.62
NAVY = RGBColor(0x16, 0x32, 0x4F)
NAVY_2 = RGBColor(0x24, 0x4A, 0x70)
TEXT = RGBColor(0x1F, 0x29, 0x33)
MUTED = RGBColor(0x5B, 0x6B, 0x7C)
LINE = RGBColor(0xD8, 0xDE, 0xE6)
TINT = RGBColor(0xF3, 0xF6, 0xF9)
GREEN = RGBColor(0x16, 0xA1, 0x63)
GREEN_D = RGBColor(0x0A, 0x5C, 0x38)
GREEN_T = RGBColor(0xE9, 0xF5, 0xEE)
RED = RGBColor(0xC4, 0x36, 0x2F)
AMBER = RGBColor(0xA9, 0x76, 0x1B)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
FONT = "微软雅黑"
MONO = "Consolas"


def set_run(run, size, color=TEXT, bold=False, font=FONT):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = color
    run.font.name = font
    rpr = run._r.get_or_add_rPr()
    ea = rpr.find(qn("a:ea"))
    if ea is None:
        from lxml import etree
        ea = etree.SubElement(rpr, qn("a:ea"))
    ea.set("typeface", font)


def text(slide, x, y, w, h, s, size=14, color=TEXT, bold=False, font=FONT,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, line=None):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    if line:
        p.line_spacing = line
    r = p.add_run()
    r.text = s
    set_run(r, size, color, bold, font)
    return box


def rich(slide, x, y, w, h, parts, size=14, align=PP_ALIGN.LEFT, line=1.15):
    """parts: [(text, color, bold, font?)]"""
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear()
    tf.word_wrap = True
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = line
    for part in parts:
        s, color, bold = part[0], part[1], part[2]
        font = part[3] if len(part) > 3 else FONT
        r = p.add_run()
        r.text = s
        set_run(r, size, color, bold, font)
    return box


def hline(slide, x, y, w, color=LINE, width=0.75):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                 Inches(w), Inches(max(width / 72.0, 0.012)))
    _no_style(shp)
    shp.fill.solid(); shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    shp.shadow.inherit = False


def vline(slide, x, y, h, color=LINE, width=0.75):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                 Inches(max(width / 72.0, 0.012)), Inches(h))
    _no_style(shp)
    shp.fill.solid(); shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    shp.shadow.inherit = False


def _no_style(shp):
    """WPS/PowerPoint 会按 p:style 的 effectRef 补阴影；显式去掉样式引用。"""
    style = shp._element.find(qn("p:style"))
    if style is not None:
        shp._element.remove(style)


def rect(slide, x, y, w, h, fill=None, line=None, line_w=0.75):
    shp = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    _no_style(shp)
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(line_w)
    shp.shadow.inherit = False
    return shp


def circle_num(slide, x, y, n, color=GREEN, d=0.28):
    shp = slide.shapes.add_shape(MSO_SHAPE.OVAL, Inches(x), Inches(y), Inches(d), Inches(d))
    _no_style(shp)
    shp.fill.solid()
    shp.fill.fore_color.rgb = color
    shp.line.fill.background()
    shp.shadow.inherit = False
    tf = shp.text_frame
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = MSO_ANCHOR.MIDDLE
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.CENTER
    r = p.add_run()
    r.text = str(n)
    set_run(r, 9, WHITE, True, MONO)


def pic_contain(slide, path, x, y, w, h):
    iw, ih = Image.open(path).size
    s = min(w / iw, h / ih)
    nw, nh = iw * s, ih * s
    return slide.shapes.add_picture(str(path), Inches(x + (w - nw) / 2), Inches(y + (h - nh) / 2),
                                    Inches(nw), Inches(nh))


def slide_base(prs, section, title, subtitle=None, dark=False):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    bg = NAVY if dark else WHITE
    s.background.fill.solid()
    s.background.fill.fore_color.rgb = bg
    fg = WHITE if dark else NAVY
    sub = RGBColor(0xC9, 0xD8, 0xE6) if dark else MUTED
    text(s, M, 0.34, 8.0, 0.28, section, 11, GREEN if not dark else RGBColor(0x8F, 0xD3, 0xB4), True)
    text(s, M, 0.62, W - 2 * M, 0.62, title, 27, fg, True)
    if subtitle:
        text(s, M, 1.25, W - 2 * M, 0.38, subtitle, 14.5, sub)
    hline(s, M, 7.02, W - 2 * M, RGBColor(0x2C, 0x4A, 0x68) if dark else LINE)
    text(s, M, 7.10, 6.0, 0.22, "隐盾安全智能体 · 正式答辩", 9, RGBColor(0x8F, 0xA3, 0xB8) if dark else MUTED)
    text(s, W - M - 1.0, 7.10, 1.0, 0.22, f"{len(prs.slides):02d} / 08", 9,
         RGBColor(0x8F, 0xA3, 0xB8) if dark else MUTED, align=PP_ALIGN.RIGHT)
    return s


def notes(slide, s):
    slide.notes_slide.notes_text_frame.text = s


prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)

# ── 1 封面 ─────────────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
s.background.fill.solid(); s.background.fill.fore_color.rgb = NAVY
text(s, 0.95, 1.45, 11.6, 0.35, "庆园杯 · 正式答辩", 13, RGBColor(0x8F, 0xD3, 0xB4), True)
text(s, 0.95, 1.90, 11.8, 0.95, "隐盾安全智能体", 54, WHITE, True)
text(s, 0.95, 2.95, 11.6, 0.55, "面向数据隐私安全的 AI 协同办公桌面智能体", 24, RGBColor(0xD6, 0xE4, 0xF0))
text(s, 0.95, 3.72, 11.6, 0.45, "让 AI 看得见任务，看不见隐私", 18, RGBColor(0x8F, 0xD3, 0xB4), True)
hline(s, 0.95, 4.52, 4.2, RGBColor(0x8F, 0xD3, 0xB4), 1.0)
text(s, 0.95, 4.78, 6.8, 1.35,
     "星辰大海 团队 · 计算机科学与网络工程学院\n指导老师：王艳     队长：余齐政\n队员：孙良硕  薛晓晴  陈俊豪  张晶",
     13, RGBColor(0xC9, 0xD8, 0xE6), line=1.45)
text(s, 0.95, 6.55, 5.0, 0.3, "2026 年 10 月 · V3.6.20", 11, RGBColor(0x8F, 0xA3, 0xB8), font=MONO)
text(s, 7.55, 6.55, 4.8, 0.3, "0 例明文出网  ·  264/280 留出集  ·  39 个套件", 10,
     RGBColor(0x8F, 0xD3, 0xB4), font=MONO, align=PP_ALIGN.RIGHT)
notes(s, "开场不念目录。第一句话：AI 越普及，用户越会把敏感数据交给智能体，我们做的是让这件事变得敢用。")

# ── 2 作品简介 ─────────────────────────────────────────
s = slide_base(prs, "01  作品简介", "AI 越普及，越要让人敢用",
               "不是少用 AI，而是让数据在进入模型之前就失去明文")
steps = [
    ("用户原文", "姓名、手机号、身份证\n都是真值", WHITE, TEXT),
    ("本地规则脱敏", "正则粗筛 + 程序化精验\n不调用任何模型", GREEN_T, GREEN_D),
    ("模型推理", "本地 Ollama 或云端 API\n只见 [NAME_0_x] 等占位符", TINT, NAVY),
    ("本机还原", "回答里的占位符\n换回真值再显示", WHITE, TEXT),
]
x0, gap, box_w = M, 0.34, 2.72
for i, (head, body, fill, color) in enumerate(steps):
    x = x0 + i * (box_w + gap)
    rect(s, x, 1.95, box_w, 1.70, fill=fill, line=LINE if fill == WHITE else None)
    text(s, x + 0.16, 2.08, box_w - 0.32, 0.3, head, 13.5, color, True)
    text(s, x + 0.16, 2.48, box_w - 0.32, 0.95, body, 11.5, TEXT if fill == WHITE else color, line=1.35)
    if i < 3:
        text(s, x + box_w + 0.05, 2.55, 0.26, 0.3, "→", 17, MUTED)
# red boundary line: plaintext stays on this side
hline(s, M, 3.90, 5.55, RED, 1.4)
text(s, M, 3.98, 5.55, 0.28, "明文只在本机：脱敏前与还原后", 10.5, RED, True)
hline(s, M + 6.3, 3.90, 3.2, RED, 1.4)
text(s, M + 6.3, 3.98, 3.2, 0.28, "模型侧无明文", 10.5, RED, True)
facts = [
    ("识别", "27 类实体；正则粗筛 + 校验位/位数/姓氏窗口精验"),
    ("还原", "映射表 Fernet 加密、主密钥 DPAPI 保护，不进模型上下文"),
    ("目标", "律所 / 医院 / 制造企业 / 政府外包：既要用 AI，又不能交原文"),
]
for i, (k, v) in enumerate(facts):
    x = M + i * 4.03
    text(s, x, 4.62, 3.75, 0.26, k, 11, GREEN_D, True)
    text(s, x, 4.92, 3.80, 0.62, v, 11.5, TEXT, line=1.3)
hline(s, M, 5.95, W - 2 * M, LINE)
text(s, M, 6.10, 11.9, 0.36, "一句话：不是少用 AI，而是让模型只看见占位符。", 16, NAVY, True)
text(s, M, 6.52, 11.9, 0.28, "识别靠规则，还原在本机，审计留痕。", 11.5, MUTED)
notes(s, "我们做隐盾的初衷很现实：AI 越普及，用户越会把个人信息、敏感文件甚至企业核心数据交给智能体；一旦在传输、存储或处理中泄露、被滥用，损失难以估量。一个值得信赖的智能体不仅要聪明，更要让人敢用。我们把脱敏做进模型入口：数据进模型前换成占位符，回答回本机再还原。")

# ── 3 功能总览 ─────────────────────────────────────────
s = slide_base(prs, "02  主要功能与预期目标", "三组能力，四个目标：可用、不可见、可审计、可部署",
               "普通功能不单独占页；把时间留给能证明差异的三个关键动作")
cols = [
    ("01", "对话与资料", ["脱敏对话：27 类实体在内存中替换为占位符", "脱敏知识库：入库即脱敏，向量库零明文", "检索结果标注“片段不是全文”"]),
    ("02", "文件操作与审计", ["15 个文件/办公工具，默认可读写范围覆盖桌面/文档/下载", "写盘、删除、命令执行前熔断审批", "每次工具调用、审批、还原都写入哈希链"]),
    ("03", "统一后台", ["企业控制面：只汇总掩码事件与元数据", "策略只能收紧，端点上报默认关闭", "插件体系：能力可扩展，判定权不外包"]),
]
for i, (num, head, items) in enumerate(cols):
    x = M + i * 4.10
    if i:
        vline(s, x - 0.24, 1.85, 3.55)
    text(s, x, 1.85, 0.6, 0.3, num, 12, GREEN, True, font=MONO)
    text(s, x, 2.18, 3.65, 0.36, head, 16, NAVY, True)
    yy = 2.70
    for it in items:
        text(s, x, yy, 3.55, 0.62, "· " + it, 11.5, TEXT, line=1.3)
        yy += 0.78
# goals band
rect(s, M, 5.62, W - 2 * M, 1.10, fill=TINT, line=None)
goals = [("可用", "2B/4B 量化模型 · CPU 可跑"), ("不可见", "已识别实体出网明文 0 例"),
         ("可审计", "工具 / 审批 / 还原全部留痕"), ("可部署", "两个免安装 exe")]
for i, (k, v) in enumerate(goals):
    x = M + 0.20 + i * 3.02
    text(s, x, 5.76, 2.75, 0.26, k, 12, GREEN_D, True)
    text(s, x, 6.06, 2.80, 0.30, v, 10.5, TEXT)
notes(s, "功能按三组讲：对话与资料、文件操作与审计、统一后台。普通功能不单独占页，重点看后面三个关键动作。预期目标四个：可用——本机 2B/4B 量化模型，不需要 GPU；不可见——对已识别实体，出网明文 0 例；可审计——每次操作留痕；可部署——两个免安装 exe。")

# ── 4 脱敏对话 ─────────────────────────────────────────
s = slide_base(prs, "02  主要功能 · 脱敏对话", "模型全程看不见真值",
               "识别在本地、由规则完成；模型只负责推理，不负责判断哪些是隐私")
text(s, M, 1.80, 5.15, 0.32, "本地正则 + 程序化精验，不调用模型", 15, GREEN_D, True)
text(s, M, 2.20, 5.15, 0.72,
     "手机号看 11 位；银行卡过 Luhn；身份证过 GB11643 校验位；人名看“姓氏表 ∩ 触发词窗口”。",
     12.5, TEXT, line=1.35)
text(s, M, 3.02, 5.15, 1.05,
     "规则可扩展：自定义敏感词表（字面词 + 正则）、自定义姓名/通讯录、行业词表插件。项目代号、客户名单、内部术语都能加。",
     12.5, TEXT, line=1.35)
# two strips
rect(s, M, 4.28, 5.10, 0.72, fill=TINT, line=None)
text(s, M + 0.14, 4.40, 4.85, 0.22, "用户看到", 9.5, MUTED, True)
text(s, M + 0.14, 4.61, 4.85, 0.26, "孙良硕  ·  13700001111", 13, TEXT, True, font=MONO)
rect(s, M, 5.08, 5.10, 0.72, fill=GREEN_T, line=None)
text(s, M + 0.14, 5.20, 4.85, 0.22, "模型看到", 9.5, GREEN_D, True)
text(s, M + 0.14, 5.41, 4.85, 0.26, "[NAME_0_o6g8]  ·  [PHONE_0_gm1z]", 13, GREEN_D, True, font=MONO)
text(s, M, 6.10, 5.15, 0.62,
     "映射表不进模型上下文；回答回到本机才还原。对已识别实体，出网明文 0 例。",
     11.5, MUTED, line=1.3)
# right: screenshots
pic_contain(s, ASSETS / "conv_ppt.png", 6.15, 1.72, 6.55, 2.52)
text(s, 6.15, 4.32, 6.55, 0.24, "图 1  真实客户端：发送后先过隐私网关，回答回到本机还原", 9, MUTED)
rect(s, 6.15, 4.72, 6.55, 1.42, fill=TINT, line=None)
text(s, 6.32, 4.84, 6.20, 0.22, "审计预览 · 模型输入", 9.5, MUTED, True)
text(s, 6.32, 5.10, 6.20, 0.30, "乙方联系人为 [NAME_0_o6g8]，联系电话 [PHONE_0_gm1z]", 12, GREEN_D, True, font=MONO)
text(s, 6.32, 5.50, 6.20, 0.22, "只有占位符，不含真值", 9.5, MUTED)
text(s, 6.15, 6.24, 6.55, 0.22, "图 2  审计预览：模型输入/输出只有占位符", 9, MUTED)
notes(s, "先看脱敏对话。用户输入的是姓名、手机号、身份证这些真值；发送前，本地规则先做正则粗筛，再用程序化校验器精验——手机号看位数，银行卡过 Luhn，身份证过校验位，人名看姓氏表和触发词窗口。这一步不调用任何模型。模型真正收到的是占位符；回答回来，本机再把占位符换回真值。映射表加密、不进模型上下文，所以模型服务方拿到的只是一段占位符文本。对已经识别出来的实体，出网明文是 0 例。")

# ── 5 三层判断审批 ─────────────────────────────────────
s = slide_base(prs, "03  设计方案及技术实现 · 审批", "先判用途，再判操作，最后才放行",
               "三层回答三个不同问题；判定权始终在人，不在模型")
pic_contain(s, ASSETS / "approval_layers.gif", M, 1.72, 4.55, 5.12)
text(s, M, 6.90, 4.55, 0.22, "图 3  审批弹窗界面（演示数据）：先“正在研判”，再给出用途红线与建议", 8.5, MUTED)
layers = [
    ("1", "用途红线", "内核确定性规则，不依赖模型", "收集个人信息 + 出售/牟利 → 模型和工具之前直接拒绝；+ 外发/公开 → 弹窗顶部红色警示"),
    ("2", "意图预判", "内核规则 + 本地模型补充细节", "意图 / 数据 / 去向 / 用途；模型只能加重，不能减轻；模型不可用就不硬凑结论"),
    ("3", "操作风险", "laya 三级：本机 → 局域网 → 正则", "高危 / 中危 / 低危；只评动作本身，不评价请求用途，来源如实标注"),
]
y = 1.78
for n, head, sub, body in layers:
    circle_num(s, 5.55, y + 0.02, n)
    text(s, 5.95, y, 2.2, 0.30, head, 14, NAVY, True)
    text(s, 5.95, y + 0.32, 6.65, 0.26, sub, 10.5, GREEN_D, True)
    text(s, 5.95, y + 0.62, 6.70, 0.72, body, 11.5, TEXT, line=1.32)
    y += 1.45
hline(s, 5.55, 6.28, 7.10, LINE)
text(s, 5.55, 6.40, 7.10, 0.45,
     "用途触线时，「是否相符」不再发言——避免用“相符”给危险请求背书。",
     11, RED, True)
notes(s, "审批弹窗有三层。第一层是用途红线，内核确定性规则，在模型和工具之前执行：收集个人信息去出售或牟利，直接拒绝；外发或公开，顶部红色警示。第二层是意图预判，回答想干什么、涉及什么数据、去哪、什么用途，模型只能加重不能减轻。第三层是操作风险，laya 按本机、局域网、正则三级给高/中/低危，并标明来源，只评动作本身。三层都只是建议，放行由人决定。")

# ── 6 审计黑匣子 ───────────────────────────────────────
s = slide_base(prs, "03  设计方案及技术实现 · 审计", "审计不是“不可写”，是“可发现”",
               "HMAC-SHA256 哈希链 + 链锚点；每次打开面板都重新校验，不读缓存")
pic_contain(s, ASSETS / "audit_real_ppt.png", M, 1.72, 4.05, 5.10)
text(s, M, 6.90, 4.05, 0.22, "图 4  真实会话的审计面板：概览与链状态", 8.5, MUTED)
pts = [
    ("打开即校验", "每次展开审计面板都重跑 verify_chain_detail 与 anchor_health；徽标不是缓存结果。"),
    ("每条可追溯", "工具调用、隐私检测/还原、审批决策、模型输入输出都在链上；只存脱敏预览。"),
    ("六种删改可发现", "改中间条目、尾部截断、同长度改写、降级改写、删锚点、批内断链，均有回归用例。"),
]
y = 1.80
for head, body in pts:
    text(s, 4.95, y, 7.65, 0.30, head, 14, NAVY, True)
    text(s, 4.95, y + 0.34, 7.65, 0.62, body, 11.5, TEXT, line=1.32)
    y += 1.15
rect(s, 4.95, 5.55, 7.55, 0.92, fill=RGBColor(0xFD, 0xF7, 0xEA), line=None)
text(s, 5.10, 5.68, 7.25, 0.66,
     "边界如实说：能重写整台机器的攻击者超出防护目标（等价用户本人）。纯本地审计挡不住这一层，所以准确说法是“可发现”，不是“物理不可写”。",
     10.5, AMBER, line=1.3)
notes(s, "审计不是普通日志。每次打开面板都会重新校验 HMAC 链和锚点，徽标不是缓存；现场可以直接点一次盾牌图标验证。每条工具调用、隐私检测、审批决策都在链上；审计里只存脱敏预览。改中间、尾部截断、降级改写、删锚点这些手法都能被发现。但能重写整台机器的人，我们挡不住，所以准确说是可发现，不是不可写。")

# ── 7 扩展与控制面 ─────────────────────────────────────
s = slide_base(prs, "03  设计方案及技术实现 · 扩展", "能力可以加，判定权不外包",
               "laya 只做建议、插件只做加法、控制面只能收紧")
cols = [
    ("laya 三级", "按数据边界由近及远", [
        "本机 laya 模型：数据不出机器",
        "局域网 laya 服务：出机器不出内网",
        "正则规则：全离线，规则空则如实说未给出结论",
        "每级写明来源；模型不可用自动回落，不冒充“模型结论”",
    ]),
    ("插件只做加法", "7 个内置插件 · 5 类公开钩子", [
        "只能多识别、多解析、多导出、多模板",
        "approve / mask / bypass / whitelist / write_audit 等判定类钩子永久禁止",
        "载荷净化 · 超时隔离 · 内核兜底 · 启停即收回",
    ]),
    ("控制面只收紧", "企业后台 · 演示级原型", [
        "只上报掩码事件与元数据，提示词与回答不经过控制面",
        "策略只能收紧：敏感路径并集、审批下限取更严、插件取交集",
        "默认关闭；每次上报成功/失败都写本地审计",
    ]),
]
for i, (head, sub, items) in enumerate(cols):
    x = M + i * 4.10
    if i:
        vline(s, x - 0.24, 1.82, 3.95)
    text(s, x, 1.82, 3.65, 0.30, head, 15, NAVY, True)
    text(s, x, 2.16, 3.65, 0.24, sub, 10.5, GREEN_D, True)
    yy = 2.58
    for it in items:
        text(s, x, yy, 3.55, 0.70, "· " + it, 11, TEXT, line=1.28)
        yy += 0.78
hline(s, M, 5.92, W - 2 * M, LINE)
text(s, M, 6.08, 8.4, 0.42, "插件是“能力插座”，不是“权限后门”。", 18, GREEN_D, True)
text(s, M, 6.52, 11.9, 0.28, "能力可以扩展，安全边界不外包：脱敏、审批、审计、沙箱的判定始终在内核。", 11.5, MUTED)
notes(s, "扩展性有三条边界。laya 按本机、局域网、正则三级，每级标明来源，只做建议。插件只有建议型和增强型能力，判定类钩子永久禁止，所以它是能力插座，不是权限后门。控制面只上报掩码事件与元数据，策略只能收紧，默认关闭；它是管理能力，不是新的泄露面。")

# ── 8 创新与前景 ───────────────────────────────────────
s = slide_base(prs, "04  创新及应用前景", "四个创新，四个能落地的场景",
               "每一项都给一句可验证的证据；边界也如实说", dark=True)
inno = [
    ("01", "内存级脱敏网关", "机密性是架构保证，不依赖识别率", "已识别实体出网明文 0 例"),
    ("02", "先判用途，再判操作", "审批不再只问“对不对得上”，先问“该不该做”", "个人信息 + 出售/牟利 → 模型前拒绝"),
    ("03", "laya 三级 + 插件只做加法", "安全能力可按数据边界扩展，判定权永不外包", "7 个插件；判定类钩子永久禁止"),
    ("04", "可验证交付", "测试通过不是安全证明，但每一步都能复核", "39 个套件；verify_archive 0 问题；双 exe"),
]
y = 1.78
for num, head, body, ev in inno:
    text(s, M, y, 0.5, 0.30, num, 12, RGBColor(0x8F, 0xD3, 0xB4), True, font=MONO)
    text(s, M + 0.55, y - 0.03, 3.35, 0.30, head, 14, WHITE, True)
    text(s, M + 0.55, y + 0.31, 6.45, 0.26, body, 10.5, RGBColor(0xC9, 0xD8, 0xE6))
    text(s, M + 0.55, y + 0.61, 6.45, 0.26, "证据：" + ev, 10, RGBColor(0x8F, 0xD3, 0xB4))
    y += 1.12
vline(s, 7.75, 1.78, 4.35, RGBColor(0x2C, 0x4A, 0x68))
text(s, 8.05, 1.78, 4.55, 0.28, "应用场景", 11, RGBColor(0x8F, 0xD3, 0xB4), True)
text(s, 8.05, 2.12, 4.60, 0.86, "律所：合同审查 / 卷宗归档\n医院：病历摘要 / 科研整理", 11, WHITE, line=1.4)
text(s, 8.05, 3.05, 4.60, 0.86, "制造：供应商合同 / 报价单\n政府外包：涉密文档流转", 11, WHITE, line=1.4)
text(s, 8.05, 4.02, 4.55, 0.28, "边界", 11, RGBColor(0x8F, 0xD3, 0xB4), True)
text(s, 8.05, 4.35, 4.60, 1.15,
     "控制面是演示级原型：RBAC / TLS / 多租户是下一步；识别率是经验值（留出集 264/280），未覆盖 16 例公开。",
     10, RGBColor(0xC9, 0xD8, 0xE6), line=1.35)
hline(s, M, 6.10, W - 2 * M, RGBColor(0x2C, 0x4A, 0x68))
text(s, M, 6.25, 11.9, 0.45, "隐私保护不该是使用约定，而应是架构约束。", 20, WHITE, True)
text(s, W - M - 2.4, 6.72, 2.4, 0.22, "V3.6.20 · 2026.10", 9, RGBColor(0x8F, 0xA3, 0xB8), font=MONO, align=PP_ALIGN.RIGHT)
notes(s, "最后是四个创新。第一，内存级脱敏网关，机密性是架构保证，不是识别率保证；对已识别实体，出网明文 0 例。第二，先判用途再判操作，收集个人信息去出售，在模型和工具之前就拒绝。第三，laya 三级加插件只做加法：能力可以扩展，判定权不外包。第四，可验证交付：39 个套件、双 exe，打包后校验资产等于源码。场景先做律所、医院、制造和政府外包；边界也如实说，控制面还是演示级原型，识别率是经验值。")

prs.save(OUT)
print("saved", OUT, "slides", len(prs.slides))
