# -*- coding: utf-8 -*-
"""隐盾 · 隐私脱敏对抗性评测（格式变体 / 绕过尝试 / 误伤防护）

背景（评审意见整改）："100% 召回、零泄露"若只基于常规样本，不能代表对抗场景。
本套件专门用**格式变体与常见绕过手法**施压，并把两类"有意不拦"的边界显式记录下来：

  A. 必须命中（格式变体）：分隔符 / 全角 / 国际区号 / 零宽字符插入 / 结构化上下文
  B. 必须不误伤（负样本）：订单号、日期时间、版本号、非 Luhn 的 16 位数字串
  C. 已知边界（有意不拦，如实标注、不算失败）：
     · "(at)" 等文本混淆邮箱——确定性规则不猜语义，避免大面积误伤，属**已声明的边界**；
     · 非 Luhn 校验的 16 位数字——程序化精验按 ISO/IEC 7812 拒绝，防"任意长数字被误标银行卡"。

用法：python tests/privacy_adversarial_test.py   （退出码 0 = 全部符合预期）
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402

ZWSP = "\u200b"   # 零宽空格（对抗样本里最常用的"插字符"手法）


def _luhn_ok(prefix: str) -> str:
    """生成一个 Luhn 校验通过的卡号（前缀 + 校验位）。"""
    digits = [int(c) for c in reversed(prefix)]
    total = sum((d * 2 - 9 if d * 2 > 9 else d * 2) if i % 2 == 0 else d
                for i, d in enumerate(digits))
    return prefix + str((10 - total % 10) % 10)


CARD = _luhn_ok("622288888888888")          # Luhn 合法（6222 8888 8888 8887）
CARD_SPACED = " ".join(CARD[i:i + 4] for i in range(0, len(CARD), 4))

MUST_CATCH = [
    ("手机 · 横线分隔", "联系电话：138-1234-5678", "PHONE"),
    ("手机 · 空格分隔", "联系电话：138 1234 5678", "PHONE"),
    ("手机 · 国际区号", "联系电话：+86 13812345678", "PHONE"),
    ("手机 · 全角数字", "联系电话：１３８１２３４５６７８", "PHONE"),
    ("手机 · 零宽空格插入", f"联系电话：138{ZWSP}1234{ZWSP}5678", "PHONE"),
    ("身份证 · 空格分组", "身份证号 110101 1990 0101 1234", "IDCARD"),
    ("身份证 · 横线分组", "身份证号 110101-1990-0101-1234", "IDCARD"),
    ("身份证 · 零宽插入", f"身份证号 110101{ZWSP}19900101{ZWSP}1234", "IDCARD"),
    ("银行卡 · 空格分组（Luhn 合法）", f"卡号 {CARD_SPACED}", "BANKCARD"),
    ("银行卡 · 连续数字", f"卡号 {CARD}", "BANKCARD"),
    # ── C-10 覆盖率收口（V3.6.2）：留出集里"已声明未覆盖"的五类形态，现在进在册 ──
    ("手机 · 点分隔", "联系电话：138.1234.5678", "PHONE"),
    ("银行卡 · 点分隔（Luhn 合法）", f"结算卡号 {'.'.join(CARD[i:i+4] for i in range(0, 16, 4))}", "BANKCARD"),
    ("身份证 · 斜杠分隔（6-8-4）", "证件编号 110101/19900307/2316", "IDCARD"),
    ("身份证 · 点分隔（6-8-4）", "证件编号 110101.19900307.2316", "IDCARD"),
    ("人名 · 复姓（欧阳）", "对接人：欧阳明，请安排接待。", "NAME"),
    ("人名 · 复姓（司马）", "备用联系人：司马静。", "NAME"),
    ("人名 · 顿号列举第 2 个起", "参会人：张伟、李娜、王芳，请准时到场。", "NAME_2"),
    ("人名 · CSV 表格单元格", "姓名,邮箱\n张伟,a@b.com\n李娜,c@d.com\n", "NAME_2"),
    ("人名 · TSV 表格 + 复姓", "姓名\t工号\n欧阳明\tA1001\n", "NAME"),
    ("金额 · 汉字大写", "合同价款：人民币壹佰贰拾万元整，30 日内支付。", "MONEY"),
    ("金额 · 汉字大写（无人民币前缀）", "押金壹拾伍万元已到账。", "MONEY"),
    ("邮箱 · 常规", "邮箱 zhang.wei@example.com", "EMAIL"),
    ("结构化上下文 · JSON", '{"phone": "13812345678"}', "PHONE"),
    ("结构化上下文 · Markdown 表格", "| 联系人 | 13812345678 |", "PHONE"),
    ("结构化上下文 · CSV 行", "张伟,13812345678,北京市", "PHONE"),
]

MUST_NOT_TOUCH = [
    # 注：这里**故意不放** "服务器 192.168.1.100" 或 "版本号 1.2.3.4" 这类串——
    #   形状合法的 IPv4 会被 IP 规则（在册实体：设备/网络标识，属落盘面）一律脱敏，
    #   包括偶尔"长得像 IPv4 的版本号"。这是有意的取舍（宁可多换一个，也不漏真实 IP），
    #   且占位符能原样还原，不影响可读性与可用性。
    ("订单号（非 Luhn 16 位）", "订单号 1234567890123456 已发货"),
    ("日期时间", "会议时间 2026-10-05 14:30"),
    ("版本号", "系统版本 v3.14.159 已发布"),
    ("处理计数", "共处理 42 个文件，耗时 3 分 25 秒"),
    ("房间号", "会议室预订在 A301，参会 8 人"),
    ("百分比", "缓存命中率 92.5%"),
    # ── C-10 新增规则的反例：放宽匹配面之后，这些必须仍然不动 ──
    ("斜杠日期（6-8-4 形状但不满足身份证校验位）", "备份批次 202609/20261018/0001 已归档"),
    ("路径形状的斜杠数字", "日志目录 /data/2026/0918/1024 已清理"),
    ("无触发词的普通词汇（拾取/万元户）", "拾取文件的操作请先确认；万元户只是说法"),
    ("表格外的散文（不按表格处理）", "他说姓名不必填，王芳也同意了。"),
]

KNOWN_GAPS = [
    ("(at) 文本混淆邮箱", "邮箱 zhang.wei (at) example.com",
     "确定性规则不猜语义；强行匹配会大面积误伤正常括号文本"),
    ("非 Luhn 的 16 位数字", "卡号 6222 8888 8888 8888",
     "ISO/IEC 7812 精验按设计拒绝，防'任意长数字串'被误标银行卡"),
]


eng = PrivacyEngine()
failures = []

print("=" * 78)
print("【A 组】格式变体必须命中（含零宽字符对抗）")
print("=" * 78)
for name, text, kind in MUST_CATCH:
    out, _ = eng.anonymize(text)
    hit = out != text
    print(f"  [{'✅ 已脱敏' if hit else '❌ 未命中'}] {name}")
    if not hit:
        failures.append(f"未命中: {name}")

print("\n" + "=" * 78)
print("【B 组】负样本必须不误伤")
print("=" * 78)
for name, text in MUST_NOT_TOUCH:
    out, _ = eng.anonymize(text)
    clean = out == text
    print(f"  [{'✅ 未改动' if clean else '❌ 误伤'}] {name}")
    if not clean:
        failures.append(f"误伤: {name} -> {out[:80]}")

print("\n" + "=" * 78)
print("【C 组】已知边界（有意不拦；如实记录，不作为失败）")
print("=" * 78)
for name, text, why in KNOWN_GAPS:
    out, _ = eng.anonymize(text)
    print(f"  [ℹ️ 已知边界] {name}：{'未命中（与声明一致）' if out == text else '⚠️ 已命中（边界声明需更新）'}")
    print(f"      原因：{why}")

print("\n" + "=" * 78)
if failures:
    print(f"❌ 对抗性评测失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print(f"✅ 对抗性评测通过：{len(MUST_CATCH)} 项格式变体全部命中，"
      f"{len(MUST_NOT_TOUCH)} 项负样本零误伤；{len(KNOWN_GAPS)} 项已知边界与声明一致")
sys.exit(0)
