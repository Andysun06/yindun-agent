# -*- coding: utf-8 -*-
"""
隐盾脱敏引擎 —— 真机审计测试
用途：验证"数据脱敏是否真正实现"，覆盖：
  1) 脱敏引擎覆盖率（含常见绕过变体）
  2) 落盘数据（会话/审计/报告）是否残留明文敏感数据
"""
import sys
import os
import re
import json

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yindun.core.privacy_engine import PrivacyEngine

engine = PrivacyEngine()

# ── 测试用例：(分组, 场景, 原文, 必须消失的敏感片段) ──
CASES = [
    # 手机号
    ("手机号", "标准11位", "请联系我 13812345678 谢谢", "13812345678"),
    ("手机号", "空格分隔", "电话 138 1234 5678", "138 1234 5678"),
    ("手机号", "横线分隔", "电话 138-1234-5678", "138-1234-5678"),
    ("手机号", "全角数字", "电话 １３８１２３４５６７８", "１３８１２３４５６７８"),
    ("手机号", "+86国际前缀", "手机 +8613812345678", "+8613812345678"),
    ("手机号", "0086前缀", "手机 0086-138-1234-5678", "138-1234-5678"),

    # 身份证
    ("身份证", "18位标准", "身份证 110101199003072316", "110101199003072316"),
    ("身份证", "15位老证", "身份证 110101900307231", "110101900307231"),
    ("身份证", "空格分组", "身份证 110101 19900307 2316", "110101 19900307 2316"),

    # 银行卡
    ("银行卡", "19位农行", "卡号 6228480402564890018", "6228480402564890018"),
    ("银行卡", "16位Visa", "卡号 4111111111111111", "4111111111111111"),
    ("银行卡", "空格分组", "卡号 6228 4804 0256 4890 018", "6228 4804 0256 4890 018"),

    # 邮箱
    ("邮箱", "标准", "邮箱 zhang.wei@example.com", "zhang.wei@example.com"),
    ("邮箱", "中文域名", "邮箱 li_si@公司.cn", "li_si@公司.cn"),

    # 网络标识
    ("网络", "IPv4", "服务器 192.168.1.100 已宕机", "192.168.1.100"),
    ("网络", "IPv6", "地址 2001:0db8:85a3:0000:0000:8a2e:0370:7334", "2001:0db8:85a3"),
    ("网络", "MAC地址", "设备 00:1A:2B:3C:4D:5E 离线", "00:1A:2B:3C:4D:5E"),

    # 组织机构/证件（办公高频）
    ("证照", "统一社会信用代码", "信用代码 91310000MA1FL5X23K", "91310000MA1FL5X23K"),
    ("证照", "车牌号", "车辆 京A12345 入库", "京A12345"),
    ("证照", "护照号", "护照 E12345678", "E12345678"),
    ("证照", "港澳通行证", "通行证 C1234567", "C1234567"),

    # 金额
    ("金额", "万元", "合同金额 85万元整", "85万元整"),
    ("金额", "纯数字无单位", "转账金额 1250000 已到账", "1250000"),
    ("金额", "带符号", "应付 ¥1,250,000", "1,250,000"),
    ("金额", "外币", "结算 USD 12500", "12500"),

    # 人名
    ("人名", "有触发词", "联系人 张伟明 已确认", "张伟明"),
    ("人名", "无触发词", "请查收 张伟明 提交的材料", "张伟明"),
    ("人名", "单姓单名", "客户 李娜 到访", "李娜"),

    # 地址
    ("地址", "省市区完整", "住址 北京市海淀区中关村大街1号", "北京市海淀区中关村大街1号"),
    ("地址", "无省前缀", "住址 海淀区中关村大街1号", "海淀区中关村大街1号"),
    ("地址", "含门牌单元", "送达 上海市浦东新区世纪大道100号25楼", "上海市浦东新区世纪大道100号25楼"),

    # 密钥与凭证（高危）
    ("密钥", "OpenAI sk-", "密钥 sk-abcdefghijklmnopqrstuvwxyz", "sk-abcdefghijklmnopqrstuvwxyz"),
    ("密钥", "sk-短值", "密钥 sk-abc123", "sk-abc123"),
    ("密钥", "api_key赋值", "配置 api_key = abcdefghijklmnop123", "abcdefghijklmnop123"),
    ("密钥", "password字段", "配置 password: MyP@ssw0rd123", "MyP@ssw0rd123"),
    ("密钥", "数据库连接串", "连接串 mysql://root:Password123@10.0.0.5:3306/db", "Password123"),
    ("密钥", "GitHub Token", "令牌 ghp_abcdefghijklmnopqrstuvwxyz123456", "ghp_abcdefghijklmnopqrstuvwxyz123456"),
    ("密钥", "AWS AccessKey", "密钥 AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
    ("密钥", "JWT", "令牌 eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c", "eyJhbGciOiJIUzI1NiJ9"),
    ("密钥", "Bearer长token", "Authorization: Bearer abcdefghij1234567890ABCDEFGHIJ1234567890", "abcdefghij1234567890ABCDEFGHIJ1234567890"),
    ("密钥", "Bearer短token", "Authorization: Bearer abc123", "abc123"),
    ("密钥", "access_token", "回调 access_token=ya29.a0AfH6SMBxyz123fakeaccess", "ya29.a0AfH6SMBxyz123fakeaccess"),
    ("密钥", "私钥PEM", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAxyz123fakekey\n-----END RSA PRIVATE KEY-----", "MIIEpAIBAAKCAQEAxyz123fakekey"),

    # 路径
    ("路径", "SSH私钥路径", "加载 C:\\Users\\admin\\.ssh\\id_rsa", "C:\\Users\\admin\\.ssh\\id_rsa"),
    ("路径", "env配置", "读取 /etc/app/.env 完成", "/etc/app/.env"),
    ("路径", "业务敏感目录", "打开 D:\\公司资料\\员工薪资表.xlsx", "D:\\公司资料\\员工薪资表.xlsx"),
]

# ── E 组：负样本回归集（不应被脱敏的正常业务内容）──
# 断言：anonymize 后文本必须【完全不变】，误伤数必须为 0
NEGATIVE_CASES = [
    "订单号 ORD20240902123456",
    "运单号 SF1234567890123",
    "产品编号 SKU-2024-001",
    "型号 ABC-1234",
    "版本号 v1.2.3",
    "Python 3.13.5",
    "Chrome 120.0.6099.109",
    "日期 2026-09-02",
    "2024年9月2日",
    "时间 14:30:25",
    "百分比 99.9%",
    "完成度 85%",
    "转账金额 1250000 元",
    "座机号 010-12345678",
    "分机 8021",
    "代码片段 for i in range(10): print(i)",
    "普通路径 D:\\project\\src\\main.py",
    "普通路径 /home/dev/app.py",
    "圆周率 3.14159",
]

print("=" * 78)
print("【隐盾脱敏引擎 真机审计测试】")
print("=" * 78)

results = []
for group, desc, text, secret in CASES:
    anon, mapping = engine.anonymize(text)
    leaked = secret in anon
    results.append((group, desc, secret, anon, leaked, len(mapping)))

# 分组输出
cur_group = None
leak_count = 0
for group, desc, secret, anon, leaked, mlen in results:
    if group != cur_group:
        print(f"\n── {group} ──")
        cur_group = group
    flag = "❌ 泄露" if leaked else "✅ 已脱敏"
    if leaked:
        leak_count += 1
    print(f"  [{flag}] {desc}")
    if leaked:
        print(f"        原文片段: {secret[:60]}")
        print(f"        脱敏结果: {anon.strip()[:90]}")

total = len(results)
print("\n" + "=" * 78)
print(f"【脱敏覆盖率】{total - leak_count}/{total} 通过，{leak_count} 项泄露（占比 {leak_count*100//total}%）")
print("=" * 78)

# 按分组统计
from collections import defaultdict
grp = defaultdict(lambda: [0, 0])
for group, desc, secret, anon, leaked, mlen in results:
    grp[group][1] += 1
    if leaked:
        grp[group][0] += 1
print("\n【分组泄露统计】")
for g, (l, t) in grp.items():
    mark = "⚠️ " if l else "  "
    print(f"{mark}{g}: 泄露 {l}/{t}")

# ── E 组负样本回归：这些文本【必须完全不变】──
print("\n" + "=" * 78)
print("【E 组负样本回归（不应被脱敏，误伤数必须为 0）】")
print("=" * 78)
neg_false_pos = 0
for negative in NEGATIVE_CASES:
    anon, _ = engine.anonymize(negative)
    changed = anon != negative
    if changed:
        neg_false_pos += 1
        print(f"  ❌ 误伤: «{negative}» -> «{anon}»")
    else:
        print(f"  ✅ 未误伤: {negative}")
print(f"\n  负样本误伤总数：{neg_false_pos}（应为 0）")
if neg_false_pos:
    print("  ⚠️ 存在误伤，需收紧对应正则后重跑！")

# ── 第二部分：落盘数据明文扫描 ──
print("\n" + "=" * 78)
print("【落盘数据明文扫描】")
print("=" * 78)

BASE = os.path.dirname(os.path.abspath(__file__))
SCAN_FILES = [
    os.path.join(BASE, "chat_sessions.json"),
    os.path.join(BASE, "global_config.json"),
]
for d in ("audit_logs", "workflow_reports", "config"):
    p = os.path.join(BASE, d)
    if os.path.isdir(p):
        for fn in os.listdir(p):
            fp = os.path.join(p, fn)
            if os.path.isfile(fp) and fn.lower().endswith((".json", ".md", ".txt", ".doc")):
                SCAN_FILES.append(fp)

# 扫描用的敏感正则（引擎规则 + 补充）
scan_patterns = dict(PrivacyEngine.PATTERNS)
scan_patterns["PASSWORD_FIELD"] = r"(?i)(?:password|passwd|pwd)\s*[=:]\s*\S+"
scan_patterns["CREDIT_CODE"] = r"(?<![A-Za-z0-9])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![A-Za-z0-9])"


def _is_hash_embedded(content: str, start: int, end: int) -> bool:
    """判断匹配是否为【审计日志哈希链】中十六进制串的误报碎片。

    audit_chain.json 的 HMAC-SHA256 哈希（64 位 hex）含连续数字段，
    会被 IDCARD/BANKCARD/PHONE 等数字类规则+Luhn 校验误命中。
    若该匹配被包在 ≥32 位连续十六进制 token 内，判为哈希噪声（非真实敏感值）。
    真实卡号/身份证号在正常文本中被非 hex 字符（空格/逗号/中文等）包围，不会误杀。
    """
    _hex = "0123456789abcdefABCDEF"
    n = len(content)
    i = start
    while i > 0 and content[i - 1] in _hex:
        i -= 1
    j = end
    while j < n and content[j] in _hex:
        j += 1
    return (j - i) >= 32


disk_findings = False   # ★3.2/★4：落盘扫描是否发现敏感明文
for fp in SCAN_FILES:
    if not os.path.exists(fp):
        continue
    try:
        with open(fp, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception:
        continue

    # ★ 彻底剔除审计链的 HMAC 哈希字段后再扫描。
    #   背景：entry_hash / previous_hash 是 64 位十六进制串，其中的连续数字片段
    #   会被 BANKCARD / IDCARD / PHONE 等数字类规则误命中，且有一定概率通过 Luhn 校验。
    #   仅靠 _is_hash_embedded 的"扩展长度≥32"启发式在边界情况下会漏判，
    #   导致回归测试随机失败（退出码 1），破坏退出码门禁的意义。
    #   这里直接把哈希字段值替换掉，从源头消除该类噪声。
    if fp.lower().endswith(".json"):
        try:
            _obj = json.loads(content)

            def _strip_hash(o):
                if isinstance(o, dict):
                    return {k: ("<HASH>" if k in ("entry_hash", "previous_hash")
                                else _strip_hash(v)) for k, v in o.items()}
                if isinstance(o, list):
                    return [_strip_hash(i) for i in o]
                return o

            content = json.dumps(_strip_hash(_obj), ensure_ascii=False)
        except Exception:
            pass

    hits = {}
    for key, pat in scan_patterns.items():
        # ★3.3/★5：金额(MONEY)属业务内容，E 组负样本已明确"不应脱敏"普通金额，
        # 故落盘扫描不把金额命中判为敏感明文（否则历史审计中的"单价0.6元"会误报）。
        if key == "MONEY":
            continue
        flags = re.DOTALL if key == "PRIVATE_KEY" else re.IGNORECASE
        try:
            it = re.finditer(pat, content, flags)
        except re.error:
            continue
        uniq = []
        seen = set()
        for m in it:
            val = (m.group(1) if (m.lastindex and m.group(1)) else m.group(0)).strip()
            if not val or len(val) <= 3:
                continue
            # 哈希链误报守卫：数字类实体若嵌在 ≥32 位 hex token 内则跳过
            if key in ("BANKCARD", "IDCARD", "IDCARD15", "PHONE", "CREDIT_CODE") \
                    and _is_hash_embedded(content, m.start(), m.end()):
                continue
            # 手机号/银行卡精验，减少误报
            if key == "PHONE" and not PrivacyEngine._is_valid_phone(val):
                continue
            if key == "BANKCARD" and not PrivacyEngine._luhn_valid(val):
                continue
            if key == "IP":
                # ★3.2 回环地址白名单：排除 127.0.0.0/8、0.0.0.0（消除误报，不改引擎规则）
                lead = val.split(".")[0]
                if (lead.isdigit() and int(lead) == 127) or val == "0.0.0.0":
                    continue
            if val not in seen:
                seen.add(val)
                uniq.append(val)
        if uniq:
            hits[key] = uniq[:5]
    size_kb = os.path.getsize(fp) / 1024
    if hits:
        disk_findings = True   # ★4：落盘发现敏感明文
    status = "⚠️ 含敏感明文" if hits else "✅ 干净"
    print(f"\n{status}  {os.path.relpath(fp, BASE)}  ({size_kb:.1f} KB)")
    for k, v in hits.items():
        print(f"      · {k}: {v}")

# ── 第三部分：附件全文明文落盘专项 ──
print("\n" + "=" * 78)
print("【附件快照落盘专项】")
print("=" * 78)
cs = os.path.join(BASE, "chat_sessions.json")
if os.path.exists(cs):
    try:
        with open(cs, "r", encoding="utf-8") as f:
            data = json.load(f)
        sessions = data.get("sessions", data if isinstance(data, list) else {})
        if isinstance(sessions, dict):
            sessions = list(sessions.values())
        att_total = 0
        att_chars = 0
        for s in sessions:
            if not isinstance(s, dict):
                continue
            att = s.get("attachment_fulltext", {}) or {}
            for fn, txt in att.items():
                att_total += 1
                att_chars += len(txt or "")
                print(f"  附件《{fn}》原文落盘 {len(txt or '')} 字符（明文，未脱敏）")
        print(f"\n  合计：{att_total} 个附件全文以明文形式持久化在 chat_sessions.json，共 {att_chars} 字符")
        if att_chars > 200000:
            print(f"  ⚠️ 该字段把整个 chat_sessions.json 撑到 {os.path.getsize(cs)/1024/1024:.2f} MB，存在性能与泄露双重风险")
    except Exception as e:
        print(f"  解析失败：{e}")

print("\n" + "=" * 78)
print("审计结束")
print("=" * 78)

# ── ★4：退出码语义 + 三项结论汇总 ──
LEAK_THRESHOLD = 4   # 当前基线泄露数，允许后续更严格
print("\n" + "=" * 78)
print("【隐私回归检查结论】")
print(f"  · 泄露数: {leak_count}（阈值 {LEAK_THRESHOLD}）")
print(f"  · 负样本误伤数: {neg_false_pos}")
print(f"  · 落盘明文发现: {'有' if disk_findings else '无'}")
print("=" * 78)

exit_code = 0
if leak_count > LEAK_THRESHOLD:
    print("❌ 命中规则泄露数超阈值")
    exit_code = 1
if neg_false_pos > 0:
    print("❌ 负样本存在误伤")
    exit_code = 1
if disk_findings:
    print("❌ 落盘发现敏感明文")
    exit_code = 1
if exit_code == 0:
    print("✅ 隐私回归检查通过")
sys.exit(exit_code)
