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
from pptx.enum.dml import MSO_LINE_DASH_STYLE
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
    text(s, W - 1.1, 7.16, 1.0, 0.22, f"{n:02d} / 10", 10, TAB, False, TIMES, PP_ALIGN.RIGHT)


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
pic(s, ASSETS / "demo_privacy_p.gif", 0.56, 1.98, 6.30, 3.94)
text(s, 0.56, 6.02, 6.30, 0.24, "完整流程：输入 → 发送 → 模型加载 → 回答还原", 10, MUTED)
rect(s, 7.10, 1.98, 5.65, 4.10, fill=GREEN_T)
text(s, 7.30, 2.12, 5.25, 0.32, "本地正则 + 精验，不调用模型", 16, GREEN, True)
text(s, 7.30, 2.55, 5.25, 1.05,
     "手机号看 11 位；银行卡过 Luhn；身份证过 GB11643；人名看姓氏表 ∩ 触发词窗口。",
     13, TEXT, line=1.35)
text(s, 7.30, 3.72, 5.25, 0.24, "用户看到", 11, MUTED, True)
text(s, 7.30, 3.97, 5.25, 0.30, "孙良硕 · 13700001111", 14, TEXT, True, TIMES)
text(s, 7.30, 4.38, 5.25, 0.24, "模型看到", 11, GREEN, True)
text(s, 7.30, 4.63, 5.25, 0.30, "[NAME_0_o6g8] · [PHONE_0_gm1z]", 12, GREEN, True, TIMES)
text(s, 7.30, 5.10, 5.25, 0.72,
     "映射表加密、不进模型上下文；回答回本机才还原。",
     12, MUTED, line=1.35)
rect(s, 0.56, 6.35, 12.22, 0.55, fill=GREEN)
text(s, 0.56, 6.47, 12.22, 0.30, "对已识别实体，出网明文 0 例；识别靠规则，不靠模型。", 14, WHITE, True, HEI, PP_ALIGN.CENTER)
pagenum(s, 4)
notes(s, "先看脱敏对话。用户输入的是姓名、手机号、身份证这些真值；发送前，本地规则先做正则粗筛，再用程序化校验器精验——手机号看位数，银行卡过 Luhn，身份证过校验位，人名看姓氏表和触发词窗口。这一步不调用任何模型。模型真正收到的是占位符；回答回来，本机再把占位符换回真值。映射表加密、不进模型上下文，所以模型服务方拿到的只是一段占位符文本。对已经识别出来的实体，出网明文是 0 例。")

# ── 5 三层判断审批 ─────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 2, "先判用途，再判操作，最后才放行",
       "三层回答三个不同问题；判定权始终在人，不在模型")
pic(s, ASSETS / "demo_approval_p.gif", 0.56, 1.98, 6.50, 4.06)
text(s, 0.56, 6.12, 6.50, 0.24, "完整流程：写文件请求 → 弹窗 → 授权 → 执行成功", 10, MUTED)
rect(s, 7.25, 1.98, 5.50, 4.45, fill=GREEN_T)
layers = [
    ("①", "用途红线", "内核确定性规则，不依赖模型",
     "收集个人信息 + 出售/牟利 → 模型和工具之前直接拒绝；外发/公开 → 顶部红色警示"),
    ("②", "意图预判", "内核规则 + 本地模型补充细节",
     "意图 / 数据 / 去向 / 用途；模型只能加重，不能减轻"),
    ("③", "操作风险", "laya 决策模型：本机 → 局域网 → 正则",
     "高危 / 中危 / 低危；只评动作本身，来源如实标注"),
]
y = 2.12
for num, head, sub, body in layers:
    text(s, 7.45, y, 0.36, 0.32, num, 16, GREEN, True, TIMES)
    text(s, 7.85, y - 0.02, 2.0, 0.30, head, 15, GREEN, True)
    text(s, 7.85, y + 0.30, 4.65, 0.24, sub, 10.5, GREEN_D, True)
    text(s, 7.85, y + 0.58, 4.65, 0.55, body, 11, TEXT, line=1.28)
    y += 1.22
hline(s, 7.45, 5.88, 5.05, LINE)
text(s, 7.45, 5.98, 5.05, 0.30, "用途触线时，「是否相符」不再发言。", 11.5, RED, True)
pagenum(s, 5)
notes(s, "审批弹窗有三层。第一层是用途红线，内核确定性规则，在模型和工具之前执行：收集个人信息去出售或牟利，直接拒绝；外发或公开，顶部红色警示。第二层是意图预判，回答想干什么、涉及什么数据、去哪、什么用途，模型只能加重不能减轻。第三层是操作风险，laya 按本机、局域网、正则三级给高/中/低危，并标明来源，只评动作本身。三层都只是建议，放行由人决定。")

# ── 6 审计黑匣子 ───────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 2, "审计不是“不可写”，是“可发现”",
       "HMAC-SHA256 哈希链 + 链锚点；每次打开面板都重新校验，不读缓存")
pic(s, ASSETS / "demo_audit_p.gif", 0.56, 1.98, 6.30, 3.94)
text(s, 0.56, 6.02, 6.30, 0.24, "完整流程：对话完成 → 打开审计 → 滚动查看事件", 10, MUTED)
rect(s, 7.10, 1.98, 5.65, 4.10, fill=GREEN_T)
pts = [
    ("打开即校验", "每次展开面板都重跑链校验与锚点检查；徽标不是缓存。"),
    ("每条可追溯", "工具调用、隐私检测/还原、审批决策都在链上；只存脱敏预览。"),
    ("六种删改可发现", "改中间、尾部截断、降级改写、删锚点等，均有回归用例。"),
]
y = 2.12
for head, body in pts:
    text(s, 7.30, y, 5.25, 0.30, head, 15, GREEN, True)
    text(s, 7.30, y + 0.34, 5.25, 0.55, body, 11, TEXT, line=1.3)
    y += 1.02
rect(s, 7.30, 5.12, 5.25, 0.78, fill=WARM)
text(s, 7.42, 5.20, 5.02, 0.62,
     "边界：能重写整台机器的攻击者超出防护目标。准确说法是“可发现”，不是“物理不可写”。",
     10, AMBER, line=1.28)
pagenum(s, 6)
notes(s, "审计不是普通日志。每次打开面板都会重新校验 HMAC 链和锚点，徽标不是缓存；现场可以直接点一次盾牌图标验证。每条工具调用、隐私检测、审批决策都在链上；审计里只存脱敏预览。改中间、尾部截断、降级改写、删锚点这些手法都能被发现。但能重写整台机器的人，我们挡不住，所以准确说是可发现，不是不可写。")

# ── 7 扩展与控制面 ─────────────────────────────────────
s = prs.slides.add_slide(prs.slide_layouts[6])
header(s, 2, "能力可以加，判定权不外包",
       "laya 决策模型快速适配；插件只做加法；控制面只能收紧")
pic(s, ASSETS / "demo_plugins_p.gif", 0.56, 1.98, 5.90, 3.69)
text(s, 0.56, 5.76, 5.90, 0.24, "插件页：能力清单、运行状态与启停", 10, MUTED)
rect(s, 6.70, 1.98, 6.05, 4.05, fill=GREEN_T)
text(s, 6.90, 2.10, 5.65, 0.30, "laya 决策模型 · 快速适配", 16, GREEN, True)
text(s, 6.90, 2.44, 5.65, 0.24, "近一个月走红的决策模型，我们第一时间接进项目", 11, GREEN_D, True)
text(s, 6.90, 2.76, 5.65, 0.72,
     "· 判断动作本身危不危险，输出高/中/低危\n· 本机优先 → 局域网 → 正则回退，来源标明",
     11, TEXT, line=1.28)
text(s, 6.90, 3.55, 5.65, 0.30, "插件只做加法", 15, GREEN, True)
text(s, 6.90, 3.87, 5.65, 0.56,
     "· 判定类钩子永久禁止：approve / mask / bypass / whitelist\n· 载荷净化 · 超时隔离 · 内核兜底 · 启停即收回",
     10.5, TEXT, line=1.25)
text(s, 6.90, 4.55, 5.65, 0.30, "控制面只收紧", 15, GREEN, True)
text(s, 6.90, 4.87, 5.65, 0.56,
     "· 只上报掩码事件与元数据\n· 策略只能收紧；默认关闭，每次上报都写本地审计",
     10.5, TEXT, line=1.25)
text(s, 6.90, 5.55, 5.65, 0.30, "laya 只做建议、不参与放行；适配层做成插件。", 10.5, GREEN_D, True)
rect(s, 0.56, 6.30, 12.22, 0.55, fill=GREEN)
text(s, 0.56, 6.42, 12.22, 0.30, "插件是“能力插座”，不是“权限后门”。", 15, WHITE, True, HEI, PP_ALIGN.CENTER)
pagenum(s, 7)
notes(s, "扩展性有三条边界。laya 是近一个月才火起来的决策模型，我们第一时间接进项目：用它判断动作本身危不危险，本机优先、局域网次之、正则兜底，来源如实标注；它只做建议、不参与放行。插件只有建议型和增强型能力，判定类钩子永久禁止，所以它是能力插座，不是权限后门。控制面只上报掩码事件与元数据，策略只能收紧，默认关闭。")

# ── 8 创新（一）：网关 + 用途 ───────────────────────────
def innovation_page(title, sub, items, page_no):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    header(s, 3, title, sub)
    for i, (num, head, body, ev, gif) in enumerate(items):
        x = 0.56 + i * 6.30
        y = 1.90
        rect(s, x, y, 5.90, 4.50, fill=GREEN_T2)
        rect(s, x, y, 0.08, 4.50, fill=GREEN)
        text(s, x + 0.22, y + 0.12, 0.38, 0.30, num, 16, GREEN, True, TIMES)
        text(s, x + 0.62, y + 0.10, 5.05, 0.30, head, 16, GREEN, True)
        text(s, x + 0.62, y + 0.46, 5.05, 0.46, body, 12.5, TEXT, line=1.25)
        gif_path = ASSETS / gif if gif else None
        if gif_path and gif_path.exists():
            pic(s, gif_path, x + 0.45, y + 1.02, 5.00, 3.12)
        else:
            ph = rect(s, x + 0.45, y + 1.02, 5.00, 3.12, fill=GREEN_T2,
                      line=RGBColor(0x9A, 0xB3, 0xA3), line_w=1.0)
            ph.line.dash_style = MSO_LINE_DASH_STYLE.DASH
            text(s, x + 0.45, y + 2.18, 5.00, 0.30, "演示 GIF 待补录", 15, MUTED, False, HEI, PP_ALIGN.CENTER)
            text(s, x + 0.45, y + 2.55, 5.00, 0.24, gif or "待补录", 10.5, TAB, False, TIMES, PP_ALIGN.CENTER)
        text(s, x + 0.45, y + 4.18, 5.05, 0.24, "证据：" + ev, 10.5, MUTED2)
    pagenum(s, page_no)
    return s

s = innovation_page(
    "四个创新（一）：网关与用途",
    "每一项配一段真实操作，先看它解决什么问题",
    [
        ("①", "内存级脱敏网关", "入口处完成替换：真值只在本机内存，模型上下文只有占位符", "审计里 LLM 输入只有 [NAME_0_x]", "06_网关占位符.gif"),
        ("②", "先判用途，再判操作", "用途触线时直接拒绝：不调用模型，也不给审批放行机会", "个人信息 + 出售/牟利 → 模型前拒绝", "07_用途红线拒绝.gif"),
    ],
    8,
)
notes(s, "第一个创新是内存级脱敏网关：模型物理上看不见真值，机密性是架构保证，不是识别率保证；对已识别实体，出网明文 0 例。第二个是先判用途再判操作：先问这件事该不该做，再问操作对不对得上；收集个人信息去出售，在模型和工具之前就拒绝。")

# ── 9 创新（二）：laya 适配 + 脱敏知识库 ───────────────
s = innovation_page(
    "四个创新（二）：分级与知识库",
    "laya 第一时间适配；知识库入库即脱敏",
    [
        ("③", "laya 决策模型：第一时间适配", "近一个月走红，我们第一时间接进项目", "本机→局域网→正则；只做建议、不参与放行", "08_laya决策模型.gif"),
        ("④", "脱敏知识库：入库即脱敏", "向量库只存占位符；检索标注“片段不是全文”", "入库即脱敏；向量库零明文", "demo_kb_p.gif"),
    ],
    9,
)
rect(s, 0.56, 6.52, 12.22, 0.42, fill=GREEN_T)
text(s, 0.72, 6.59, 11.90, 0.28,
     "场景：律所 / 医院 / 制造 / 政府外包　　边界：控制面演示级原型；识别率 264/280（经验值）",
     11, GREEN_D, True)
notes(s, "第三个创新是 laya 决策模型的快速适配：它是近一个月才火起来的决策模型，我们第一时间接进项目，用它判断动作本身危不危险；本机优先、局域网次之、正则兜底，只做建议、不参与放行。第四个是脱敏知识库：入库即逐块脱敏，向量库只存占位符，检索结果明确标注片段不是全文。")

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
