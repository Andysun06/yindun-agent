# -*- coding: utf-8 -*-
"""生成《隐盾安全智能体汇报.pptx》正式答辩版。

设计语言**沿用原稿**：顶部四段导航、#005826 主绿、#EEF6F1 浅绿卡片、
黑体标题/正文、Times New Roman 导航与数字、楷体标语。
内容按评委意见重排：背景压缩、一页一个关键功能、真实截图/审批动图。

素材：docs/formal_assets/（V3.6.20 客户端实拍 + 审批弹窗动图）
输出：仓库根目录 隐盾安全智能体汇报.pptx（原稿备份 *_backup.pptx）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "docs" / "formal_assets"
BACKUP = ROOT / "隐盾安全智能体汇报_backup.pptx"
OUT = ROOT / "隐盾安全智能体汇报.pptx"

W, H = 13.333, 7.5
GREEN = RGBColor(0x00, 0x58, 0x26)
GREEN_D = RGBColor(0x00, 0x2C, 0x13)
GREEN_T = RGBColor(0xEE, 0xF6, 0xF1)
GREEN_T2 = RGBColor(0xF5, 0xF9, 0xF5)
GREEN_T3 = RGBColor(0xD6, 0xEB, 0xE0)
WARM = RGBColor(0xFC, 0xF8, 0xEE)
TEXT = RGBColor(0x1F, 0x23, 0x29)
MUTED = RGBColor(0x5B, 0x69, 0x75)
MUTED2 = RGBColor(0x6E, 0x78, 0x73)
TAB = RGBColor(0x8C, 0xA0, 0x91)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
RED = RGBColor(0xB0, 0x3A, 0x2E)
AMBER = RGBColor(0xB4, 0x78, 0x00)
LINE = RGBColor(0xD9, 0xE2, 0xDC)
HEI = "黑体"
TIMES = "Times New Roman"
KAI = "楷体"


def _no_style(shp):
    style = shp._element.find(qn("p:style"))
    if style is not None:
        shp._element.remove(style)


def _font(run, size, color, bold=False, font=HEI):
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


def rect(s, x, y, w, h, fill=GREEN_T, line=None, line_w=0.75):
    shp = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    _no_style(shp)
    shp.fill.solid(); shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line; shp.line.width = Pt(line_w)
    shp.shadow.inherit = False
    return shp


def text(s, x, y, w, h, value, size=16, color=TEXT, bold=False, font=HEI,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, line=None):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear(); tf.word_wrap = True
    tf.margin_left = 0; tf.margin_right = 0; tf.margin_top = 0; tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]; p.alignment = align
    if line: p.line_spacing = line
    r = p.add_run(); r.text = value
    _font(r, size, color, bold, font)
    return box


def rich(s, x, y, w, h, parts, align=PP_ALIGN.LEFT, line=1.2):
    box = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = box.text_frame
    tf.clear(); tf.word_wrap = True
    tf.margin_left = 0; tf.margin_right = 0; tf.margin_top = 0; tf.margin_bottom = 0
    p = tf.paragraphs[0]; p.alignment = align; p.line_spacing = line
    for value, size, color, bold, *rest in parts:
        r = p.add_run(); r.text = value
        _font(r, size, color, bold, rest[0] if rest else HEI)
    return box


def hline(s, x, y, w, color=LINE, width=0.75):
    shp = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(max(width / 72, 0.012)))
    _no_style(shp); shp.fill.solid(); shp.fill.fore_color.rgb = color
    shp.line.fill.background(); shp.shadow.inherit = False


def pic(s, path, x, y, w, h):
    iw, ih = Image.open(path).size
    k = min(w / iw, h / ih)
    nw, nh = iw * k, ih * k
    return s.shapes.add_picture(str(path), Inches(x + (w - nw) / 2), Inches(y + (h - nh) / 2),
                                Inches(nw), Inches(nh))


TABS = ["作品简介", "主要功能与目标", "设计与技术实现", "创新与应用前景"]


def tabs(s, active):
    for i, label in enumerate(TABS):
        x = i * 3.333
        rect(s, x, 0, 3.39, 0.72, fill=GREEN if i == active else WHITE)
        text(s, x + 0.08, 0.17, 3.22, 0.38, label, 18,
             WHITE if i == active else MUTED, i == active, TIMES, PP_ALIGN.CENTER)
    hline(s, 0, 0.72, W, LINE)


def header(s, active, title, subtitle):
    tabs(s, active)
    rect(s, 0.56, 1.0, 0.08, 0.47, fill=GREEN)
    text(s, 0.81, 0.94, 11.39, 0.53, title, 24, TEXT, True)
    text(s, 0.81, 1.56, 12.22, 0.33, subtitle, 16, MUTED)


def notes(s, value):
    s.notes_slide.notes_text_frame.text = value


def pagenum(s, n):
    text(s, W - 1.1, 7.16, 1.0, 0.22, f"{n:02d} / 09", 10, TAB, False, TIMES, PP_ALIGN.RIGHT)


prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)

# ── 1 封面（按原稿：居中标题/团队块，绿色横带，线在团队名下方）──
s = prs.slides.add_slide(prs.slide_layouts[6])
rect(s, 0, 1.22, W, 2.97, fill=GREEN)
text(s, 1.81, 1.72, 9.72, 0.92, "隐盾安全智能体", 48, WHITE, True, HEI, PP_ALIGN.CENTER)
text(s, 1.81, 2.78, 9.72, 0.47, "面向数据隐私安全的 AI 协同办公桌面智能体", 24, WHITE, True, HEI, PP_ALIGN.CENTER)
text(s, 1.81, 3.42, 9.72, 0.42, "让 AI 看得见任务，看不见隐私", 18, GREEN_T3, False, KAI, PP_ALIGN.CENTER)
hline(s, 3.87, 5.03, 5.59, GREEN, 0.9)
_box = s.shapes.add_textbox(Inches(3.33), Inches(4.44), Inches(6.67), Inches(3.02))
_tf = _box.text_frame
_tf.clear(); _tf.word_wrap = True
_tf.margin_left = 0; _tf.margin_right = 0; _tf.margin_top = 0; _tf.margin_bottom = 0
for i, (value, bold, line, space_after) in enumerate([
    ("星辰大海 团队", True, 1.5, 18),
    ("计算机科学与网络工程学院", False, 1.5, None),
    ("指导老师：王艳        队长：余齐政", False, 1.5, None),
    ("队员：孙良硕　薛晓晴　陈俊豪　张晶", False, 1.5, None),
    ("", False, 1.17, None),
    ("2026 年 10 月", True, 1.17, None),
]):
    p = _tf.paragraphs[0] if i == 0 else _tf.add_paragraph()
    p.alignment = PP_ALIGN.CENTER
    p.line_spacing = line
    if space_after:
        p.space_after = Pt(space_after)
    r = p.add_run(); r.text = value
    _font(r, 19, GREEN if i == 0 else (MUTED if i == 5 else TEXT), bold, HEI)
notes(s, "开场不念目录。第一句话：AI 越普及，用户越会把敏感数据交给智能体，我们做的是让这件事变得敢用。")

# ── 2 作品简介 ─────────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 0, "数据越敏感，越需要 AI；却越不能把数据交出去",
       "出网、落盘、留痕、执行——四个环节都是泄露面")
leaks = [
    ("出网面", "模型 API 把明文发往外部，\n云端日志长期留存"),
    ("落盘面", "缓存、运行日志与存档\n把明文写进本地磁盘"),
    ("留痕面", "会话、知识库、向量库\n沉淀明文，回溯时再暴露"),
    ("执行面", "模型未经确认就写删改文件，\n高危操作直接落地"),
]
for i, (head, body) in enumerate(leaks):
    x = 0.56 + (i % 2) * 3.13
    y = 2.17 + (i // 2) * 1.90
    rect(s, x, y, 2.92, 1.75, fill=GREEN_T)
    text(s, x + 0.18, y + 0.16, 2.56, 0.32, head, 20, GREEN, True)
    text(s, x + 0.18, y + 0.62, 2.58, 0.95, body, 14, TEXT, line=1.35)
rect(s, 7.22, 2.17, 5.56, 2.08, fill=GREEN)
text(s, 7.42, 2.30, 5.16, 0.30, "产品定位", 20, WHITE, True)
text(s, 7.42, 2.68, 5.16, 1.38,
     "面向涉密办公场景的本地化\n隐私防护桌面智能体\n数据进模型前脱敏，回答回本机还原",
     15, WHITE, False, line=1.32)
rect(s, 7.22, 4.47, 5.56, 1.58, fill=GREEN_T)
text(s, 7.42, 4.60, 5.16, 0.30, "目标用户", 20, GREEN, True)
for i, name in enumerate(["律所", "医院", "制造企业", "政府外包"]):
    text(s, 7.42 + (i % 2) * 2.55, 5.03 + (i // 2) * 0.45, 2.35, 0.32, "· " + name, 17, TEXT, False, TIMES)
rect(s, 0.56, 6.25, 12.22, 0.67, fill=GREEN_T)
text(s, 0.56, 6.40, 12.22, 0.32, "不是“少用 AI”，而是“放心用 AI”", 17, GREEN_D, True, HEI, PP_ALIGN.CENTER)
pagenum(s, 2)
notes(s, "我们做隐盾的初衷很现实：AI 越普及，用户越会把个人信息、敏感文件甚至企业核心数据交给智能体；一旦在传输、存储或处理中泄露、被滥用，损失难以估量。一个值得信赖的智能体不仅要聪明，更要让人敢用。我们把脱敏做进模型入口：数据进模型前换成占位符，回答回本机再还原。")

# ── 3 功能总览 ─────────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 1, "三组能力，四个目标：可用、不可见、可审计、可部署",
       "普通功能不单独占页；把时间留给能证明差异的三个关键动作")
groups = [
    ("01 对话与资料", ["脱敏对话：27 类实体替换为占位符", "脱敏知识库：入库即脱敏，向量库零明文", "检索结果标注“片段不是全文”"]),
    ("02 文件操作与审计", ["15 个文件/办公工具，覆盖桌面/文档/下载", "写盘、删除、命令执行前熔断审批", "工具调用、审批、还原全部写入哈希链"]),
    ("03 统一后台", ["控制面只汇总掩码事件与元数据", "策略只能收紧，上报默认关闭", "插件：能力可扩展，判定权不外包"]),
]
for i, (head, items) in enumerate(groups):
    x = 0.56 + i * 4.12
    rect(s, x, 2.10, 3.90, 3.30, fill=GREEN_T)
    text(s, x + 0.20, 2.25, 3.50, 0.32, head, 18, GREEN, True, TIMES)
    yy = 2.78
    for it in items:
        text(s, x + 0.20, yy, 3.50, 0.72, "· " + it, 14, TEXT, line=1.32)
        yy += 0.82
goals = [("可用", "2B/4B 量化模型 · CPU 可跑"), ("不可见", "已识别实体出网明文 0 例"),
         ("可审计", "工具 / 审批 / 还原全部留痕"), ("可部署", "两个免安装 exe")]
for i, (head, body) in enumerate(goals):
    x = 0.56 + i * 3.13
    rect(s, x, 5.70, 2.92, 1.42, fill=GREEN)
    text(s, x + 0.16, 5.82, 2.60, 0.30, head, 18, WHITE, True, TIMES)
    text(s, x + 0.16, 6.20, 2.62, 0.66, body, 13, GREEN_T3, line=1.25)
pagenum(s, 3)
notes(s, "功能按三组讲：对话与资料、文件操作与审计、统一后台。普通功能不单独占页，重点看后面三个关键动作。预期目标四个：可用——本机 2B/4B 量化模型，不需要 GPU；不可见——对已识别实体，出网明文 0 例；可审计——每次操作留痕；可部署——两个免安装 exe。")

# ── 4 脱敏对话 ─────────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 1, "模型全程看不见真值",
       "识别在本地、由规则完成；模型只负责推理，不负责判断哪些是隐私")
pic(s, ASSETS / "conv_ppt.png", 0.56, 2.02, 7.35, 2.67)
text(s, 0.56, 4.78, 7.35, 0.24, "真实客户端：发送后先过隐私网关，回答回到本机还原", 11, MUTED)
rect(s, 8.15, 1.98, 4.60, 4.45, fill=GREEN_T)
text(s, 8.35, 2.12, 4.20, 0.32, "本地正则 + 精验，不调用模型", 16, GREEN, True)
text(s, 8.35, 2.55, 4.20, 1.10,
     "手机号看 11 位；银行卡过 Luhn；身份证过 GB11643；人名看姓氏表 ∩ 触发词窗口。",
     13, TEXT, line=1.35)
text(s, 8.35, 3.70, 4.20, 0.24, "用户看到", 11, MUTED, True)
text(s, 8.35, 3.95, 4.20, 0.30, "孙良硕 · 13700001111", 14, TEXT, True, TIMES)
text(s, 8.35, 4.36, 4.20, 0.24, "模型看到", 11, GREEN, True)
text(s, 8.35, 4.61, 4.20, 0.30, "[NAME_0_o6g8] · [PHONE_0_gm1z]", 12, GREEN, True, TIMES)
text(s, 8.35, 5.08, 4.20, 0.90,
     "映射表加密、不进模型上下文；回答回本机才还原。",
     12, MUTED, line=1.35)
rect(s, 0.56, 6.25, 12.22, 0.67, fill=GREEN)
text(s, 0.56, 6.40, 12.22, 0.32, "对已识别实体，出网明文 0 例；识别靠规则，不靠模型。", 15, WHITE, True, HEI, PP_ALIGN.CENTER)
pagenum(s, 4)
notes(s, "先看脱敏对话。用户输入的是姓名、手机号、身份证这些真值；发送前，本地规则先做正则粗筛，再用程序化校验器精验——手机号看位数，银行卡过 Luhn，身份证过校验位，人名看姓氏表和触发词窗口。这一步不调用任何模型。模型真正收到的是占位符；回答回来，本机再把占位符换回真值。映射表加密、不进模型上下文，所以模型服务方拿到的只是一段占位符文本。对已经识别出来的实体，出网明文是 0 例。")

# ── 5 三层判断审批 ─────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 2, "先判用途，再判操作，最后才放行",
       "三层回答三个不同问题；判定权始终在人，不在模型")
pic(s, ASSETS / "approval_layers.gif", 0.56, 1.98, 4.40, 4.55)
text(s, 0.56, 6.70, 4.40, 0.24, "审批弹窗（演示数据）：先“正在研判”，再给出三层建议", 10, MUTED)
rect(s, 5.20, 1.98, 7.58, 4.55, fill=GREEN_T)
layers = [
    ("①", "用途红线", "内核确定性规则，不依赖模型",
     "收集个人信息 + 出售/牟利 → 模型和工具之前直接拒绝；+ 外发/公开 → 顶部红色警示"),
    ("②", "意图预判", "内核规则 + 本地模型补充细节",
     "意图 / 数据 / 去向 / 用途；模型只能加重，不能减轻；不可用就不硬凑结论"),
    ("③", "操作风险", "laya 三级：本机 → 局域网 → 正则",
     "高危 / 中危 / 低危；只评动作本身，不评价请求用途，来源如实标注"),
]
y = 2.16
for num, head, sub, body in layers:
    text(s, 5.42, y, 0.40, 0.34, num, 18, GREEN, True, TIMES)
    text(s, 5.86, y - 0.02, 2.3, 0.30, head, 17, GREEN, True)
    text(s, 5.86, y + 0.32, 6.55, 0.24, sub, 12, GREEN_D, True)
    text(s, 5.86, y + 0.60, 6.60, 0.62, body, 12.5, TEXT, line=1.3)
    y += 1.34
hline(s, 5.42, 6.10, 7.10, LINE)
text(s, 5.42, 6.22, 7.10, 0.28, "用途触线时，「是否相符」不再发言——避免给危险请求背书。", 12, RED, True)
pagenum(s, 5)
notes(s, "审批弹窗有三层。第一层是用途红线，内核确定性规则，在模型和工具之前执行：收集个人信息去出售或牟利，直接拒绝；外发或公开，顶部红色警示。第二层是意图预判，回答想干什么、涉及什么数据、去哪、什么用途，模型只能加重不能减轻。第三层是操作风险，laya 按本机、局域网、正则三级给高/中/低危，并标明来源，只评动作本身。三层都只是建议，放行由人决定。")

# ── 6 审计黑匣子 ───────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 2, "审计不是“不可写”，是“可发现”",
       "HMAC-SHA256 哈希链 + 链锚点；每次打开面板都重新校验，不读缓存")
pic(s, ASSETS / "audit_real_ppt.png", 0.56, 1.98, 3.95, 4.75)
text(s, 0.56, 6.78, 3.95, 0.24, "真实会话的审计面板：概览与链状态", 10, MUTED)
rect(s, 4.85, 1.98, 7.93, 4.55, fill=GREEN_T)
pts = [
    ("打开即校验", "每次展开面板都重跑 verify_chain_detail 与 anchor_health；徽标不是缓存。"),
    ("每条可追溯", "工具调用、隐私检测/还原、审批决策、模型输入输出都在链上；只存脱敏预览。"),
    ("六种删改可发现", "改中间、尾部截断、同长度改写、降级改写、删锚点、批内断链，均有回归用例。"),
]
y = 2.16
for head, body in pts:
    text(s, 5.05, y, 7.50, 0.30, head, 17, GREEN, True)
    text(s, 5.05, y + 0.34, 7.50, 0.58, body, 12.5, TEXT, line=1.32)
    y += 1.08
rect(s, 5.05, 5.52, 7.50, 0.82, fill=WARM)
text(s, 5.18, 5.62, 7.25, 0.62,
     "边界如实说：能重写整台机器的攻击者超出防护目标（等价用户本人）。准确说法是“可发现”，不是“物理不可写”。",
     11.5, AMBER, line=1.3)
pagenum(s, 6)
notes(s, "审计不是普通日志。每次打开面板都会重新校验 HMAC 链和锚点，徽标不是缓存；现场可以直接点一次盾牌图标验证。每条工具调用、隐私检测、审批决策都在链上；审计里只存脱敏预览。改中间、尾部截断、降级改写、删锚点这些手法都能被发现。但能重写整台机器的人，我们挡不住，所以准确说是可发现，不是不可写。")

# ── 7 扩展与控制面 ─────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 2, "能力可以加，判定权不外包",
       "laya 只做建议、插件只做加法、控制面只能收紧")
cols = [
    ("laya 三级", "按数据边界由近及远", [
        "本机 laya 模型：数据不出机器",
        "局域网 laya 服务：出机器不出内网",
        "正则规则：全离线，空规则如实说未给出结论",
        "每级写明来源；不可用自动回落",
    ]),
    ("插件只做加法", "7 个内置插件 · 5 类公开钩子", [
        "只能多识别、多解析、多导出、多模板",
        "判定类钩子永久禁止：approve / mask / bypass / whitelist / write_audit",
        "载荷净化 · 超时隔离 · 内核兜底 · 启停即收回",
    ]),
    ("控制面只收紧", "企业后台 · 演示级原型", [
        "只上报掩码事件与元数据",
        "策略只能收紧：路径并集、审批取更严、插件取交集",
        "默认关闭，每次上报成功/失败都写本地审计",
    ]),
]
for i, (head, sub, items) in enumerate(cols):
    x = 0.56 + i * 4.12
    rect(s, x, 2.05, 3.90, 3.95, fill=GREEN_T)
    text(s, x + 0.20, 2.20, 3.50, 0.30, head, 18, GREEN, True)
    text(s, x + 0.20, 2.54, 3.50, 0.24, sub, 11.5, GREEN_D, True)
    yy = 2.95
    for it in items:
        text(s, x + 0.20, yy, 3.50, 0.66, "· " + it, 12.5, TEXT, line=1.28)
        yy += 0.74
rect(s, 0.56, 6.22, 12.22, 0.67, fill=GREEN)
text(s, 0.56, 6.37, 12.22, 0.32, "插件是“能力插座”，不是“权限后门”。", 17, WHITE, True, HEI, PP_ALIGN.CENTER)
pagenum(s, 7)
notes(s, "扩展性有三条边界。laya 按本机、局域网、正则三级，每级标明来源，只做建议。插件只有建议型和增强型能力，判定类钩子永久禁止，所以它是能力插座，不是权限后门。控制面只上报掩码事件与元数据，策略只能收紧，默认关闭；它是管理能力，不是新的泄露面。")

# ── 8 创新与前景 ───────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 3, "四个创新，四个能落地的场景",
       "每一项都给一句可验证的证据；边界也如实说")
inno = [
    ("①", "内存级脱敏网关", "机密性是架构保证，不依赖识别率", "已识别实体出网明文 0 例"),
    ("②", "先判用途，再判操作", "先问“该不该做”，再问“对不对得上”", "个人信息 + 出售/牟利 → 模型前拒绝"),
    ("③", "laya 三级 + 插件只做加法", "安全能力可按数据边界扩展，判定权永不外包", "7 个插件；判定类钩子永久禁止"),
    ("④", "可验证交付", "测试通过不是安全证明，但每一步都能复核", "39 个套件；verify_archive 0 问题"),
]
for i, (num, head, body, ev) in enumerate(inno):
    x = 0.56 + (i % 2) * 6.05
    y = 1.95 + (i // 2) * 2.12
    rect(s, x, y, 5.75, 1.95, fill=GREEN_T2)
    rect(s, x, y, 0.08, 1.95, fill=GREEN)
    text(s, x + 0.22, y + 0.14, 0.40, 0.32, num, 18, GREEN, True, TIMES)
    text(s, x + 0.66, y + 0.12, 4.90, 0.32, head, 18, GREEN, True)
    text(s, x + 0.66, y + 0.52, 4.90, 0.48, body, 14.5, TEXT, line=1.25)
    text(s, x + 0.66, y + 1.20, 4.90, 0.36, "证据：" + ev, 12, MUTED2)
rect(s, 0.56, 6.35, 12.22, 0.58, fill=GREEN_T)
rich(s, 0.72, 6.44, 11.90, 0.36, [
    ("场景：", 13, GREEN, True), ("律所 / 医院 / 制造 / 政府外包", 13, TEXT, False),
    ("      边界：", 13, GREEN, True), ("控制面演示级原型；识别率 264/280（经验值）", 13, TEXT, False),
])
pagenum(s, 8)
notes(s, "最后是四个创新。第一，内存级脱敏网关，机密性是架构保证，不是识别率保证；对已识别实体，出网明文 0 例。第二，先判用途再判操作，收集个人信息去出售，在模型和工具之前就拒绝。第三，laya 三级加插件只做加法：能力可以扩展，判定权不外包。第四，可验证交付：39 个套件、双 exe，打包后校验资产等于源码。场景先做律所、医院、制造和政府外包；边界也如实说，控制面还是演示级原型，识别率是经验值。")

# ── 9 结尾（按原稿：居中标题/标语/三项数据）────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
rect(s, 0, 1.94, W, 2.92, fill=GREEN)
text(s, 1.81, 2.62, 9.72, 0.83, "敬请各位老师批评指正", 40, WHITE, True, HEI, PP_ALIGN.CENTER)
text(s, 1.81, 3.78, 9.72, 0.39, "隐私保护不该是使用约定，而应是架构约束", 18, GREEN_T3, False, KAI, PP_ALIGN.CENTER)
stats = [("识别覆盖", "27 类敏感实体"), ("架构承诺", "已识别实体 0 明文出网"), ("当前版本", "V3.6.20 · 2026.10")]
for i, (k, v) in enumerate(stats):
    x = 1.67 + i * 3.33
    text(s, x, 5.44, 3.20, 0.26, k, 14, MUTED, False, TIMES, PP_ALIGN.CENTER)
    text(s, x, 5.76, 3.20, 0.34, v, 17, GREEN, True, TIMES, PP_ALIGN.CENTER)
text(s, 1.81, 6.67, 9.72, 0.30, "隐盾 · 面向数据隐私安全的 AI 协同办公桌面智能体", 12, MUTED, False, TIMES, PP_ALIGN.CENTER)
notes(s, "隐私保护不该是使用约定，而应是架构约束。谢谢各位评委。")

prs.save(OUT)
print("saved", OUT, "slides", len(prs.slides))
