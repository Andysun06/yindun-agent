# -*- coding: utf-8 -*-
"""隐盾安全智能体 · 汇报 PPT 生成（python-pptx，13.33x7.5 16:9）"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.chart.data import CategoryChartData
from pptx.enum.chart import XL_CHART_TYPE
import copy

# ── 调色板（BACKGROUND → PRIMARY → ACCENT）──
BG_D   = "0B3D2E"   # 深林绿：封面/结尾背景
BG_L   = "F4F7F5"   # 冷白：内容页背景
PRIM   = "0E7A4F"   # 盾牌绿：结构/标题/图表
PRIM_D = "0A5C3B"
ACC    = "E8590C"   # 警示橙：唯一强调色，克制使用
TEXT   = "1A2332"
MUTED  = "6B7A72"
CARD   = "FFFFFF"
TINT   = "E7F0EB"   # 浅绿底纹（卡片/色带）
LINEC  = "D3DED8"
FONT   = "微软雅黑"

W, H, M = 13.333, 7.5, 0.7

prs = Presentation()
prs.slide_width = Inches(W)
prs.slide_height = Inches(H)
BLANK = prs.slide_layouts[6]

def slide(bg=BG_L):
    s = prs.slides.add_slide(BLANK)
    r = s.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, prs.slide_height)
    r.fill.solid(); r.fill.fore_color.rgb = RGBColor.from_string(bg)
    r.line.fill.background(); r.shadow.inherit = False
    return s

def _ea(run, name):
    rPr = run._r.get_or_add_rPr()
    for tag in ("a:ea", "a:cs"):
        e = rPr.find(qn(tag))
        if e is None:
            e = rPr.makeelement(qn(tag), {}); rPr.append(e)
        e.set("typeface", name)

def style(run, size, color, bold=False, font=FONT):
    f = run.font
    f.size = Pt(size); f.bold = bold; f.name = font
    f.color.rgb = RGBColor.from_string(color)
    _ea(run, font)

def box(s, x, y, w, h, anchor=MSO_ANCHOR.TOP):
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame; tf.word_wrap = True; tf.vertical_anchor = anchor
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    return tf

def para(tf, runs, size=18, color=TEXT, bold=False, align=PP_ALIGN.LEFT,
         space_after=0, space_before=0, first=False, line=None):
    p = tf.paragraphs[0] if first else tf.add_paragraph()
    p.alignment = align
    if space_after:  p.space_after = Pt(space_after)
    if space_before: p.space_before = Pt(space_before)
    if line:         p.line_spacing = line
    if isinstance(runs, str): runs = [(runs, {})]
    for text, ov in runs:
        r = p.add_run(); r.text = text
        style(r, ov.get("size", size), ov.get("color", color), ov.get("bold", bold), ov.get("font", FONT))
    return p

def rect(s, x, y, w, h, fill=None, line=None, lw=1.0, shape=MSO_SHAPE.RECTANGLE, radius=None):
    sp = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    if fill: sp.fill.solid(); sp.fill.fore_color.rgb = RGBColor.from_string(fill)
    else: sp.fill.background()
    if line: sp.line.color.rgb = RGBColor.from_string(line); sp.line.width = Pt(lw)
    else: sp.line.fill.background()
    sp.shadow.inherit = False
    if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
        try: sp.adjustments[0] = radius
        except Exception: pass
    return sp

def shadow(sp, blur=8, op=0.14):
    el = sp._element.spPr
    ef = el.makeelement(qn('a:effectLst'), {})
    sh = el.makeelement(qn('a:outerShdw'),
        {'blurRad': str(Emu(Pt(blur))), 'dist': str(Emu(Pt(2))),
         'dir': '5400000', 'rotWithShape': '0'})
    clr = el.makeelement(qn('a:srgbClr'), {'val': '0B2A1E'})
    a = el.makeelement(qn('a:alpha'), {'val': str(int(op*100000))})
    clr.append(a); sh.append(clr); ef.append(sh); el.append(ef)

def pic_card(s, path, ratio, x, y, w, cap=None, capw=None):
    """白底圆角卡片 + 内含图片（按比例），可选题注。返回卡片高度。"""
    pad = 0.14
    h = w / ratio + pad * 2
    card = rect(s, x, y, w, h, fill=CARD, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.03)
    shadow(card)
    iw = w - pad * 2
    ih = iw / ratio
    s.shapes.add_picture(path, Inches(x + pad), Inches(y + pad), Inches(iw), Inches(ih))
    if cap:
        tf = box(s, x, y + h + 0.06, capw or w, 0.4)
        para(tf, cap, size=11.5, color=MUTED, align=PP_ALIGN.CENTER, first=True)
    return h

def kicker(s, text, x=M, y=0.55, color=PRIM):
    tf = box(s, x, y, 8, 0.3)
    para(tf, text, size=13, color=color, bold=True, first=True)

def title(s, text, y=0.9, size=30, color=TEXT, x=M, w=None):
    tf = box(s, x, y, w or (W - 2*M), 0.8)
    para(tf, text, size=size, color=color, bold=True, first=True)

_pg = [1]  # 封面为第1页（不显示页码），内容页自动递增
def pagenum(s, n=None, dark=False):
    _pg[0] += 1
    tf = box(s, W-1.1, H-0.62, 0.6, 0.35)
    para(tf, f"{_pg[0]:02d}", size=11, color=("9FC7B4" if dark else MUTED), align=PP_ALIGN.RIGHT, first=True)

# ══════════════════ 1 · 封面 ══════════════════
s = slide(BG_D)
# 右侧装饰：同心圆（盾牌意象），克制
for i, (d, col) in enumerate([(4.6, PRIM_D), (3.3, PRIM), (2.0, "128A5B")]):
    c = rect(s, W-0.4-d/2, H/2-d/2, d, d, fill=col, shape=MSO_SHAPE.OVAL)
    c.fill.fore_color.rgb = RGBColor.from_string(col)
# 盾牌符号（圆角方块 + 勾）
sh = rect(s, W-1.05, H/2-0.55, 1.1, 1.1, fill=ACC, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.25)
tf = box(s, W-1.05, H/2-0.55, 1.1, 1.1, anchor=MSO_ANCHOR.MIDDLE)
para(tf, "盾", size=40, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)

tf = box(s, M, 1.7, 9.5, 0.4)
para(tf, "技术汇报 · 隐私计算桌面智能体", size=15, color="8FD3B4", bold=True, first=True)
tf = box(s, M, 2.25, 10.5, 1.15)
para(tf, "隐盾安全智能体", size=60, color="FFFFFF", bold=True, first=True)
tf = box(s, M, 3.75, 10.5, 0.7)
para(tf, "面向涉密办公场景的本地化隐私防护桌面智能体", size=22, color="CFE8DA", first=True)
# 底部信息条
rect(s, M, 6.15, 6.2, 0.02, fill="2E6B50")
tf = box(s, M, 6.35, 11, 0.6)
para(tf, [("Yindun Security Agent", {"bold": True, "color": "FFFFFF"}),
          ("    ·    V3.3    ·    LangChain · PySide6 · 本地/云端双算力    ·    2026", {"color": "8FD3B4"})],
     size=13, first=True)

# ══════════════════ 2 · 背景痛点 ══════════════════
s = slide()
kicker(s, "研究背景与动机")
title(s, "大模型进办公，但涉密数据不敢喂")
tf = box(s, M, 1.78, W-2*M, 0.6)
para(tf, [("合同、薪酬、病历、客户资料高度敏感，而大模型天然要求把数据送出本地边界。", {"color": MUTED}),
          ("  每一个环节都是泄露面：", {"color": TEXT, "bold": True})], size=16, first=True)
leaks = [
    ("01", "出 网", "敏感明文随 prompt 发往云端 API，离开本地即失控"),
    ("02", "落 盘", "会话记录、向量库、摘要把真实隐私写进磁盘"),
    ("03", "留 痕", "审计日志本为追溯，却反过来成为二次泄露源"),
]
y = 2.55
for num, hd, desc in leaks:
    rect(s, M, y, W-2*M, 1.15, fill=TINT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
    tf = box(s, M+0.35, y, 1.4, 1.15, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, num, size=44, color=ACC, bold=True, first=True)
    tf = box(s, M+1.9, y+0.18, 2.2, 0.8, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, hd, size=22, color=PRIM, bold=True, first=True)
    tf = box(s, M+4.1, y, W-4.1-M-0.4, 1.15, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, desc, size=15.5, color=TEXT, first=True)
    y += 1.35
pagenum(s, 2)

# ══════════════════ 3 · 关键挑战 ══════════════════
s = slide()
kicker(s, "研究背景与动机")
title(s, "做成一个可用系统，要跨四道坎")
chal = [
    ("可用 vs 不可见", "删掉敏感值模型就没法答——必须抹去真实值又保留语义结构，这是"
     "「占位符化」而非「删除」的设计动机"),
    ("召回 vs 精确", "中文敏感信息形态多变（全角/分段/国际前缀），既要高召回不漏，"
     "又不能误伤订单号、版本号等业务数字"),
    ("全链路一致", "脱敏只做入口没用：落盘会话、审计、向量库、输出还原任一环节旁路，整体即失效"),
    ("可证明性", "「隐私有效」不能靠宣称，需在明确威胁模型下以可复现的攻击实验来度量"),
]
y = 1.85
for i, (hd, d) in enumerate(chal):
    rect(s, M, y, W-2*M, 1.08, fill=TINT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
    tf = box(s, M+0.35, y, 0.9, 1.08, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, f"{i+1}", size=34, color=ACC, bold=True, first=True)
    tf = box(s, M+1.35, y+0.14, 3.0, 0.8, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, hd, size=18, color=PRIM, bold=True, first=True)
    tf = box(s, M+4.4, y, W-4.4-M-0.4, 1.08, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, d, size=13.5, color=TEXT, first=True, line=1.15)
    y += 1.24
pagenum(s)

# ══════════════════ 3 · 核心理念 ══════════════════
s = slide()
kicker(s, "核心理念")
title(s, "让大模型看不见敏感数据，却依然能干活")
# 三段流程 chevron
steps = [("脱敏", "真实数据 → 加密占位符", PRIM),
         ("推理", "模型只见占位符干活", PRIM_D),
         ("还原", "结果呈现时还原明文", ACC)]
cx, cw, cy, chh = M, (W-2*M-0.8)/3, 2.15, 1.7
for i, (t, d, col) in enumerate(steps):
    x = cx + i*(cw+0.4)
    sp = rect(s, x, cy, cw, chh, fill=col, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.1)
    shadow(sp)
    tf = box(s, x, cy+0.32, cw, 0.6, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, t, size=26, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)
    tf = box(s, x+0.2, cy+1.0, cw-0.4, 0.6)
    para(tf, d, size=13, color="EAF5EF", align=PP_ALIGN.CENTER, first=True)
    if i < 2:
        ar = rect(s, x+cw+0.05, cy+chh/2-0.18, 0.3, 0.36, fill=MUTED, shape=MSO_SHAPE.CHEVRON)
tf = box(s, M, 4.35, W-2*M, 1.4)
para(tf, "三条防线贯穿全链路", size=16, color=PRIM, bold=True, first=True, space_after=6)
for t in ["输入侧 · 隐私脱敏网关：数据进模型前，内存中完成实体识别与占位符化，映射表 Fernet 加密不出本地",
          "执行侧 · 沙箱 + 权限 + 白名单 + 熔断审批：物理副作用关进笼子，高危操作强制人工二次确认",
          "留痕侧 · HMAC 哈希链审计：全程可追溯、不可篡改，且审计自身只存脱敏预览"]:
    para(tf, [("▪  ", {"color": ACC, "bold": True}), (t, {"color": TEXT})], size=14, space_after=5)
pagenum(s, 3)

# ══════════════════ 4 · 系统架构 ══════════════════
s = slide()
kicker(s, "系统架构")
title(s, "三层解耦 · 安全内核独立可测")
pic_card(s, "docs/screenshots/00_架构图.png", 1.974, M+1.6, 1.7, W-2*M-3.2,
         cap="图 1  隐盾核心数据流与安全防线：脱敏网关位于输入/输出两侧，安全内核覆盖执行与审计全程")
pagenum(s, 4)

# ══════════════════ 5 · 隐私脱敏网关 ══════════════════
s = slide()
kicker(s, "核心创新 · 一")
title(s, "内存级隐私脱敏网关")
# 左：特性
tf = box(s, M, 1.75, 5.6, 4.6)
for hd, d in [("20+ 类敏感实体", "身份证 / 银行卡 / 私钥 / JWT / API密钥 / 口令 / 病历 / 金额 / 人名 / 地址 / IP / MAC …"),
              ("三级数据分级", "参照 GB/T 35273：绝密 / 机密 / 内部，并量化风险评分"),
              ("正则粗筛 + 算法精验", "手机号 11 位精验、银行卡 Luhn、身份证校验位，降误伤"),
              ("随机 nonce 防劫持", "占位符 [PHONE_0_a3f9]，杜绝文档内植入字面量"),
              ("Fernet + DPAPI 双层密钥", "映射表加密存储，主密钥按 Windows 用户账户包裹")]:
    para(tf, [(hd, {"bold": True, "color": PRIM, "size": 16}), ("   " + d, {"color": TEXT, "size": 13})],
         first=(hd == "20+ 类敏感实体"), space_after=11, line=1.15)
# 右：原文 vs 占位符（代码风卡片）
card = rect(s, 6.7, 1.75, 5.9, 4.4, fill="0F172A", shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.03)
shadow(card)
tf = box(s, 7.0, 2.0, 5.3, 4.0)
para(tf, "真实输入", size=12, color="7EE2A8", bold=True, first=True, space_after=3)
para(tf, "甲方联系人刘建国，电话13912345678，\n身份证号110101198803075612，月费28,500元，密钥 sk-proj-9xK2…",
     size=12.5, color="D7E3EE", space_after=12, line=1.25)
para(tf, "发给大模型（脱敏后）", size=12, color="F0B45C", bold=True, space_after=3)
para(tf, "甲方联系人[NAME_0]，电话[PHONE_0]，\n身份证号[IDCARD_0]，月费[MONEY_0]，密钥[APIKEY_0]",
     size=12.5, color="8AB8F5", space_after=12, line=1.25)
para(tf, "呈现给用户（还原后）", size=12, color="7EE2A8", bold=True, space_after=3)
para(tf, "→ 模型全程只接触占位符，真实值零出网", size=12.5, color="D7E3EE", first=False)
# 底部形式化定义条
rect(s, M, 6.35, W-2*M, 0.72, fill=TINT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.1)
tf = box(s, M+0.3, 6.35, W-2*M-0.6, 0.72, anchor=MSO_ANCHOR.MIDDLE)
para(tf, [("形式化：", {"bold": True, "color": PRIM}),
          ("A(x) ↦ (x̂, M)，M = {占位符 ↦ Enc", {"color": TEXT}),
          ("k", {"color": TEXT, "size": 9}),
          ("(真实值)}；保证 机密性(x̂无明文) · 可逆性(持钥还原=x) · 还原不可伪造(nonce) · 持久化机密(无钥不可解)", {"color": TEXT})],
     size=12.5, first=True)
pagenum(s, 5)

# ══════════════════ 6 · 脱敏实测效果 ══════════════════
s = slide()
kicker(s, "核心创新 · 一")
title(s, "一段真实合同，实测脱敏效果")
stats = [("10", "类实体\n一次命中", PRIM), ("0", "明文\n出网", ACC),
         ("51", "风险评分\n(加权)", PRIM_D), ("Fernet", "映射表\n加密存储", PRIM)]
bx, bw = M, (W-2*M-1.2)/4
for i, (n, lab, col) in enumerate(stats):
    x = M + i*(bw+0.4)
    card = rect(s, x, 1.85, bw, 2.15, fill=CARD, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.06); shadow(card)
    tf = box(s, x, 2.15, bw, 1.0, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, n, size=48 if len(n) <= 2 else 30, color=col, bold=True, align=PP_ALIGN.CENTER, first=True)
    tf = box(s, x, 3.25, bw, 0.7)
    for ln in lab.split("\n"):
        para(tf, ln, size=13, color=MUTED, align=PP_ALIGN.CENTER, first=(ln==lab.split(chr(10))[0]))
# 分级分布条
tf = box(s, M, 4.5, W-2*M, 0.4)
para(tf, "实体分级分布（实测计数）", size=15, color=TEXT, bold=True, first=True)
levels = [("绝密", 2, ACC), ("机密", 5, PRIM), ("内部", 4, PRIM_D)]
total = 11; bar_x = M; bar_w = W-2*M; bar_y = 5.05
for name, cnt, col in levels:
    seg = bar_w * cnt/total
    rect(s, bar_x, bar_y, seg-0.04, 0.55, fill=col)
    tf = box(s, bar_x, bar_y, seg, 0.55, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, f"{name} {cnt}", size=13, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)
    bar_x += seg
tf = box(s, M, bar_y+0.75, bar_w, 0.4)
para(tf, "身份证 · 银行卡 · 手机号 · 邮箱 · 地址 · 金额 · API密钥 · 口令 · 病历号 · 医保卡号 · 人名  ——  全部替换为加密占位符，向量库与审计零明文",
     size=12.5, color=MUTED, first=True)
pagenum(s, 6)

# ══════════════════ 攻击实验与结果 ══════════════════
s = slide()
kicker(s, "效果验证")
title(s, "隐私防护有效性：攻击实验与结果", size=27)
tf = box(s, M, 1.78, W-2*M, 0.35)
para(tf, "13 篇合成办公语料 · 41 个敏感实体实例（含国际前缀/全角/分段卡号/PEM/JWT/IPv6 等边界格式）· 17 个业务数字负样本 · 可一键复现",
     size=12.5, color=MUTED, first=True)
rows = [
    ("实验（威胁）", "指标", "结果"),
    ("黑盒 · 出网泄露", "脱敏召回率 / 明文泄露", "100%  /  0 例"),
    ("黑盒 · 误伤", "业务数字误报率", "0%（0/17）"),
    ("可用性 · 往返", "还原正确率", "100%（13/13）"),
    ("黑盒主动 · 还原劫持", "植入字面量占位符劫持成功率", "0 / 500 轮"),
    ("黑盒主动 · 提示注入", "模型可见上下文中的真实 PII 数", "0"),
    ("白盒 · 持久化扫描", "落盘文件明文 PII 命中", "0"),
    ("白盒 · 审计预览", "审计掩码后明文 PII 命中", "0"),
    ("白盒 · 无密钥还原", "strict 模式是否泄露明文", "否"),
]
tbl = s.shapes.add_table(len(rows), 3, Inches(M), Inches(2.15), Inches(W-2*M), Inches(3.7)).table
tbl.columns[0].width = Inches(3.4); tbl.columns[1].width = Inches(5.5); tbl.columns[2].width = Inches(W-2*M-8.9)
for ci, txt in enumerate(rows[0]):
    c = tbl.cell(0, ci); c.text = txt
    c.fill.solid(); c.fill.fore_color.rgb = RGBColor.from_string(PRIM)
    p = c.text_frame.paragraphs[0]; p.alignment = PP_ALIGN.CENTER if ci==2 else PP_ALIGN.LEFT
    for r in p.runs: style(r, 13, "FFFFFF", True)
for ri in range(1, len(rows)):
    for ci, txt in enumerate(rows[ri]):
        c = tbl.cell(ri, ci); c.text = txt
        c.fill.solid(); c.fill.fore_color.rgb = RGBColor.from_string("FFFFFF" if ri%2 else "F0F5F2")
        p = c.text_frame.paragraphs[0]
        p.alignment = PP_ALIGN.CENTER if ci==2 else PP_ALIGN.LEFT
        col = PRIM if ci==2 else TEXT
        for r in p.runs: style(r, 12, col, bold=(ci==2))
tf = box(s, M, 6.05, W-2*M, 1.0)
para(tf, [("关键结论：", {"bold": True, "color": ACC}),
          ("出网零明文 ⇒ 模型信息论上无法泄露它没见过的数据；nonce ⇒ 还原环节不可被植入劫持（碰撞率≈5.9×10⁻⁷）；Fernet+DPAPI ⇒ 拿走磁盘文件、无密钥也无法恢复明文。", {"color": TEXT})],
     size=13, first=True, line=1.2)
pagenum(s)

# ══════════════════ 7 · 纵深防御链 ══════════════════
s = slide()
kicker(s, "核心创新 · 二")
title(s, "物理副作用的四层纵深防御")
defs = [("路径沙箱", "realpath+commonpath 越界校验\n文件名 basename 化，杜绝 ../ 穿越"),
        ("权限分级", "完全控制 / 安全只读 / 彻底审计\n只读与审计模式断开写盘"),
        ("命令白名单", "仅 python/pip/git/echo\nshlex 解析 + shell=False，去注入"),
        ("熔断审批", "高危操作挂起后台线程\n强制人工二次确认，超时自动驳回")]
bx, bw = M, (W-2*M-1.2)/4
for i, (hd, d) in enumerate(defs):
    x = M + i*(bw+0.4)
    # 序号徽标
    rect(s, x+bw/2-0.28, 1.85, 0.56, 0.56, fill=PRIM if i<3 else ACC, shape=MSO_SHAPE.OVAL)
    tf = box(s, x+bw/2-0.28, 1.85, 0.56, 0.56, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, str(i+1), size=20, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)
    card = rect(s, x, 2.65, bw, 2.5, fill=CARD, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.06); shadow(card)
    tf = box(s, x+0.2, 2.95, bw-0.4, 0.6, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, hd, size=18, color=PRIM, bold=True, align=PP_ALIGN.CENTER, first=True)
    tf = box(s, x+0.22, 3.7, bw-0.44, 1.3)
    for ln in d.split("\n"):
        para(tf, ln, size=12.5, color=TEXT, align=PP_ALIGN.CENTER, first=(ln==d.split(chr(10))[0]), space_after=3, line=1.1)
    if i < 3:
        rect(s, x+bw+0.02, 3.7, 0.36, 0.4, fill=LINEC, shape=MSO_SHAPE.CHEVRON)
tf = box(s, M, 5.5, 11.4, 1.7)
para(tf, "实测拦截验证", size=15, color=PRIM, bold=True, first=True, space_after=5)
for t in ["tasklist 不在白名单 → 拒绝执行", "echo 命令含 > 重定向 / ../ 穿越 → 参数级拦截",
          "读取/写入 C:\\Windows → 越界判定拒绝", "安全只读模式下任何写盘 → 权限拦截"]:
    para(tf, [("✓  ", {"color": PRIM, "bold": True}), (t, {"color": TEXT})], size=13.5, space_after=3)
pagenum(s, 7)

# ══════════════════ 8 · 熔断审批 ══════════════════
s = slide()
kicker(s, "核心创新 · 二")
title(s, "高危操作：熔断挂起，人类安全员拍板")
tf = box(s, M, 1.8, 6.0, 4.4)
for hd, d in [("独立审批弹窗", "检测到越界写盘 / 删除 / 命令执行时，后台写盘线程强行阻塞，等待人工决策"),
              ("透明展示上下文", "弹窗展示申请工具与目标路径，杜绝“盲批”"),
              ("授权 / 驳回双通道", "驳回则工具不执行并如实反馈模型；授权才放行"),
              ("超时安全兜底", "5 分钟无响应自动按驳回处理，并关闭残留弹窗防误导"),
              ("审批入审计", "批准/驳回均记入哈希链，critical 级告警")]:
    para(tf, [(hd, {"bold": True, "color": PRIM, "size": 16}), ("   " + d, {"color": TEXT, "size": 13})],
         first=(hd == "独立审批弹窗"), space_after=12, line=1.15)
pic_card(s, "docs/screenshots/05_高危操作审批弹窗.png", 1.849, 7.05, 2.0, 5.55,
         cap="图 2  隐盾安全决策网关拦截弹窗（实测）")
pagenum(s, 8)

# ══════════════════ 9 · 审计黑匣子 ══════════════════
s = slide()
kicker(s, "核心创新 · 三")
title(s, "全链路审计黑匣子：可追溯 · 不可篡改")
pic_card(s, "docs/screenshots/03_审计面板.png", 1.146, M, 1.7, 5.6,
         cap="图 3  审计面板：统计概览 / 哈希链状态 / 事件筛选 / 一键导出")
tf = box(s, 6.85, 1.85, 5.8, 4.6)
for hd, d in [("HMAC-SHA256 哈希链", "每条日志哈希覆盖自身+前一条，篡改即校验失败并告警"),
              ("全事件覆盖", "LLM 输入输出 / 工具调用 / 脱敏还原 / 审批决策，逐条留痕"),
              ("审计自身不泄露", "落盘前统一掩码，只存脱敏预览与长度，杜绝二次泄露"),
              ("密钥与数据分离", "审计 HMAC 密钥存用户目录，与被保护日志不同盘"),
              ("报告导出", "JSON / HTML 一键导出，含哈希链可视化")]:
    para(tf, [(hd, {"bold": True, "color": PRIM, "size": 15.5}), ("   " + d, {"color": TEXT, "size": 12.5})],
         first=(hd == "HMAC-SHA256 哈希链"), space_after=11, line=1.12)
pagenum(s, 9)

# ══════════════════ 10 · 脱敏 RAG ══════════════════
s = slide()
kicker(s, "核心创新 · 四")
title(s, "脱敏 RAG：机密文档本地检索，向量库零明文")
flow = [("机密文档", "PDF/Word/Excel/TXT"), ("逐块脱敏", "入库前实体占位符化"),
        ("向量化入库", "只存占位符文本"), ("语义检索", "KB_ 前缀隔离"), ("还原呈现", "回答自动还原")]
fx, fw = M, (W-2*M-0.4*4)/5
for i, (t, d) in enumerate(flow):
    x = M + i*(fw+0.4)
    col = ACC if i == 2 else PRIM
    card = rect(s, x, 1.95, fw, 1.5, fill=col, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08); shadow(card)
    tf = box(s, x+0.1, 2.15, fw-0.2, 0.6, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, t, size=15, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)
    tf = box(s, x+0.08, 2.8, fw-0.16, 0.55)
    para(tf, d, size=10.5, color="EAF5EF", align=PP_ALIGN.CENTER, first=True)
    if i < 4:
        rect(s, x+fw+0.02, 2.55, 0.36, 0.36, fill=MUTED, shape=MSO_SHAPE.CHEVRON)
# 底部两个强调
rect(s, M, 3.95, W-2*M, 2.5, fill=TINT, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.05)
tf = box(s, M+0.5, 4.2, 5.6, 2.1)
para(tf, "为什么安全", size=16, color=PRIM, bold=True, first=True, space_after=6)
for t in ["文档在向量化前逐块过脱敏引擎", "向量库中只存占位符，真实隐私永不落盘",
          "即使接云端 API，检索片段也是脱敏态", "检索结果全局重命名，避免占位符冲突"]:
    para(tf, [("▪  ", {"color": ACC, "bold": True}), (t, {"color": TEXT})], size=13.5, space_after=5)
tf = box(s, 7.2, 4.25, 5.3, 2.1, anchor=MSO_ANCHOR.MIDDLE)
para(tf, "0", size=64, color=ACC, bold=True, align=PP_ALIGN.CENTER, first=True)
para(tf, "向量库实测残留真实敏感数据 = 0", size=14, color=TEXT, align=PP_ALIGN.CENTER)
para(tf, "（逐 chunk 扫描验证）", size=11, color=MUTED, align=PP_ALIGN.CENTER)
pagenum(s, 10)

# ══════════════════ 11 · 全链路演示 ══════════════════
s = slide()
kicker(s, "功能实测")
title(s, "全链路演示：脱敏 → 审批 → 写盘 → 还原", size=27)
pic_card(s, "docs/screenshots/02_隐私对话演示.png", 1.107, M, 1.7, 5.5,
         cap="图 4  实测：模型调用工具、审批放行、文件创建、回复还原")
tf = box(s, 6.75, 1.85, 5.9, 3.9)
para(tf, "演示指令", size=13, color=MUTED, bold=True, first=True, space_after=2)
para(tf, "“把客户张伟的手机号/身份证/邮箱保存到 客户信息.txt”", size=14, color=TEXT, bold=True, space_after=10, line=1.15)
for i, t in enumerate(["用户输入含 4 类 PII，网关先脱敏", "云端模型只收到占位符（零明文出网）",
                       "模型自主发起 create_local_file 工具调用", "策略引擎熔断 → 弹出人工审批",
                       "安全员授权 → 沙箱内文件真实创建", "回复中的占位符自动还原明文呈现"]):
    para(tf, [(f"{i+1}  ", {"color": ACC, "bold": True, "size": 15}), (t, {"color": TEXT, "size": 13.5})],
         space_after=6, line=1.1)
tf = box(s, 6.75, 5.95, 5.9, 0.5)
para(tf, "磁盘文件逐字校验一致 · 全程审计留痕", size=12.5, color=PRIM, bold=True, first=True)
pagenum(s, 11)

# ══════════════════ 12 · 安全对抗闭环 ══════════════════
s = slide()
kicker(s, "安全工程")
title(s, "红队 → 修复 → 回归：自我攻击式质量闭环")
# 左：pipeline
pipe = [("红队对抗测试", "20+ 攻击用例动态验证\n通读 15 个核心模块"),
        ("分级问题清单", "P0–P3 严重度分级\n含复现证据"),
        ("逐项修复加固", "15 项修复\n含调用方一致性"),
        ("回归测试固化", "用例沉淀为测试\n防复发")]
py = 1.85
for i, (t, d) in enumerate(pipe):
    col = ACC if i == 2 else PRIM
    rect(s, M, py, 0.5, 0.5, fill=col, shape=MSO_SHAPE.OVAL)
    tf = box(s, M, py, 0.5, 0.5, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, str(i+1), size=15, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)
    if i < 3: rect(s, M+0.23, py+0.5, 0.04, 0.62, fill=LINEC)
    tf = box(s, M+0.75, py-0.02, 5.0, 0.42, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, t, size=16, color=TEXT, bold=True, first=True)
    tf = box(s, M+0.75, py+0.46, 5.0, 0.6)
    for ln in d.split("\n"):
        para(tf, ln, size=12, color=MUTED, first=(ln==d.split(chr(10))[0]), line=1.05)
    py += 1.08
# 右：柱状图 修复项按严重级
cd = CategoryChartData()
cd.categories = ["P0 阻断", "P1 架构", "P2 缺陷"]
cd.add_series("修复项", (3, 5, 4))
gframe = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(7.0), Inches(1.9),
                            Inches(5.6), Inches(3.9), cd)
ch = gframe.chart
ch.has_legend = False; ch.has_title = True; ch.chart_title.text_frame.text = "本轮修复项分布（共 12 项）"
for r in ch.chart_title.text_frame.paragraphs[0].runs: style(r, 13, TEXT, True)
plot = ch.plots[0]; plot.has_data_labels = True
plot.data_labels.font.size = Pt(13); plot.data_labels.font.bold = True
plot.data_labels.font.color.rgb = RGBColor.from_string(PRIM_D)
ser = plot.series[0]
ser.format.fill.solid(); ser.format.fill.fore_color.rgb = RGBColor.from_string(PRIM)
try:
    from pptx.util import Pt as _Pt
    ser.points[0].format.fill.solid(); ser.points[0].format.fill.fore_color.rgb = RGBColor.from_string(ACC)
except Exception: pass
ch.category_axis.tick_labels.font.size = Pt(12)
ch.value_axis.visible = False
ch.value_axis.has_major_gridlines = False
tf = box(s, 7.0, 6.0, 5.6, 0.9)
para(tf, [("诚实披露：", {"bold": True, "color": ACC}), ("实测发现 7B 本地模型工具调用可靠性问题（幻觉谎称已保存），多层防御保证零实际写盘，已作为已知局限写入报告。", {"color": MUTED})],
     size=11.5, first=True, line=1.1)
pagenum(s, 12)

# ══════════════════ 13 · 测试质量 & 产品化 ══════════════════
s = slide()
kicker(s, "质量与交付")
title(s, "全量回归通过 · 一键安装包就绪")
# 左：测试套件
tf = box(s, M, 1.72, 6.2, 0.55)
para(tf, "5 个测试套件 · 40+ 断言 · 全量通过", size=14.5, color=PRIM, bold=True, first=True)
suites = [("隐私引擎", "实体/脱敏/还原/nonce/strict"), ("审计黑匣子", "哈希链/防篡改/调用一致性 14/14"),
          ("记忆管理", "摘要/跨轮还原/序列化 6/6"), ("工作流引擎", "执行/审批链/自定义/导出"),
          ("知识库管线", "入库/检索/向量库零明文验证")]
yy = 2.35
for name, d in suites:
    rect(s, M, yy, 6.0, 0.72, fill=CARD, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.1); 
    tf = box(s, M+0.3, yy, 2.4, 0.72, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, name, size=14.5, color=TEXT, bold=True, first=True)
    tf = box(s, M+2.75, yy, 2.7, 0.72, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, d, size=11.5, color=MUTED, first=True)
    tf = box(s, M+5.4, yy, 0.5, 0.72, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, "✓", size=18, color=PRIM, bold=True, first=True)
    yy += 0.86
# 右：安装包强调
card = rect(s, 7.15, 1.9, 5.45, 4.6, fill=PRIM_D, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.05); shadow(card)
tf = box(s, 7.45, 2.25, 4.85, 1.0, anchor=MSO_ANCHOR.MIDDLE)
para(tf, "99 MB", size=52, color="FFFFFF", bold=True, align=PP_ALIGN.CENTER, first=True)
tf = box(s, 7.45, 3.35, 4.85, 0.5)
para(tf, "一键安装包  ·  Windows 安装向导", size=15, color="8FD3B4", bold=True, align=PP_ALIGN.CENTER, first=True)
rect(s, 7.6, 3.95, 4.55, 0.02, fill="2E6B50")
tf = box(s, 7.45, 4.15, 4.85, 2.2)
for t in ["PySide6 + LangChain 全依赖自包含（PyInstaller）", "装到用户目录，无需管理员权限",
          "桌面/开始菜单快捷方式 + 卸载器", "随附《使用说明》：本地/云端算力两步接入"]:
    para(tf, [("▪  ", {"color": "F0B45C", "bold": True}), (t, {"color": "EAF5EF"})],
         size=13, first=(t.startswith("PySide6")), space_after=8, line=1.1)
pagenum(s, 13)

# ══════════════════ 14 · 总结与展望 ══════════════════
s = slide(BG_D)
tf = box(s, M, 0.9, W-2*M, 0.4)
para(tf, "总结与展望", size=14, color="8FD3B4", bold=True, first=True)
tf = box(s, M, 1.4, W-2*M, 1.0)
para(tf, "一套让大模型安全处理涉密办公数据的端到端方案", size=30, color="FFFFFF", bold=True, first=True)
# 回顾指标
recap = [("1.6万", "行 Python"), ("20+", "类实体脱敏"), ("4", "层纵深防御"), ("15", "项安全修复"), ("5", "套件回归通过")]
bx, bw = M, (W-2*M-0.4*4)/5
for i, (n, lab) in enumerate(recap):
    x = M + i*(bw+0.4)
    tf = box(s, x, 2.75, bw, 0.9, anchor=MSO_ANCHOR.MIDDLE)
    para(tf, n, size=30, color="F0B45C" if i in (1,3) else "7EE2A8", bold=True, align=PP_ALIGN.CENTER, first=True)
    tf = box(s, x, 3.65, bw, 0.4)
    para(tf, lab, size=13, color="CFE8DA", align=PP_ALIGN.CENTER, first=True)
rect(s, M, 4.35, W-2*M, 0.02, fill="2E6B50")
tf = box(s, M, 4.65, 6.2, 2.0)
para(tf, "下一步", size=15, color="7EE2A8", bold=True, first=True, space_after=6)
for t in ["工具调用结果二次核验，杜绝幻觉式假成功", "审计链头外部锚定，抵御整体重算哈希链",
          "策略引擎规则可视化编辑", "CI 接入 bandit/semgrep 静态安全扫描"]:
    para(tf, [("▸  ", {"color": "F0B45C", "bold": True}), (t, {"color": "EAF5EF"})], size=13.5, space_after=6)
tf = box(s, 7.3, 4.85, 5.3, 1.6, anchor=MSO_ANCHOR.MIDDLE)
para(tf, "让本地 AI", size=24, color="FFFFFF", bold=True, align=PP_ALIGN.RIGHT, first=True)
para(tf, "更安全 · 更智能 · 更可控", size=24, color="7EE2A8", bold=True, align=PP_ALIGN.RIGHT)
tf = box(s, M, 6.9, W-2*M, 0.4)
para(tf, "隐盾安全智能体 · Yindun Team · 2026    |    技术报告 · 仅供学习研究", size=11, color="6E9C86", first=True)

prs.save("隐盾安全智能体_项目汇报.pptx")
print("PPTX saved:", len(prs.slides.__iter__.__self__._sldIdLst), "slides")
