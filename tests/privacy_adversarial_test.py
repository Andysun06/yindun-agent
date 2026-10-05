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
    ("邮箱 · 常规", "邮箱 zhang.wei@example.com", "EMAIL"),
    ("结构化上下文 · JSON", '{"phone": "13812345678"}', "PHONE"),
    ("结构化上下文 · Markdown 表格", "| 联系人 | 13812345678 |", "PHONE"),
    ("结构化上下文 · CSV 行", "张伟,13812345678,北京市", "PHONE"),
]

MUST_NOT_TOUCH = [
    ("订单号（非 Luhn 16 位）", "订单号 1234567890123456 已发货"),
    ("日期时间", "会议时间 2026-10-05 14:30"),
    ("版本号", "系统版本 v3.14.159 已发布"),
    ("处理计数", "共处理 42 个文件，耗时 3 分 25 秒"),
    ("房间号", "会议室预订在 A301，参会 8 人"),
    ("百分比", "缓存命中率 92.5%"),
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
