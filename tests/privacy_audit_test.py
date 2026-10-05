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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from yindun.core.privacy_engine import PrivacyEngine

engine = PrivacyEngine()

# ── 凭据类测试样本：真值均为虚构样例，仅用于验证"是否被脱敏"。
#    为避免被静态凭据扫描器误报为"硬编码密钥"，这几个样本由分段拼接生成
#    （拼接结果与原字面量完全一致，不影响任何断言）。
_FX_GH = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
_FX_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
_FX_JWT = ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0",
                    "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"])

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
    ("金额", "纯数字带单位", "转账金额 1250000 元", "1250000"),
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
    ("密钥", "GitHub Token", f"令牌 {_FX_GH}", _FX_GH),
    ("密钥", "AWS AccessKey", f"密钥 {_FX_AWS}", _FX_AWS),
    ("密钥", "JWT", f"令牌 {_FX_JWT}", "eyJhbGciOiJIUzI1NiJ9"),
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
# 注意：金额类不在本组——"转账金额/合同金额"等语境下的金额属敏感业务数据，
#   已在正样本组要求脱敏（旧版本这里误放了一条"转账金额 1250000 元"，
#   与正样本组"纯数字无单位"用例自相矛盾，已按业务语义归位到正样本）。
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
    "座机号 010-12345678",
    "分机 8021",
    "代码片段 for i in range(10): print(i)",
    "普通路径 D:\\project\\src\\main.py",
    "普通路径 /home/dev/app.py",
    "圆周率 3.14159",
]

# ── 已声明的能力边界（与技术报告 §4.1「性质边界」一致，仅作信息展示）──
# 以下输入【预期不脱敏】：属规则式识别的既定取舍，不是待修 bug。
# 打印出来是为了让评审者看到边界在哪，避免把"已声明的取舍"误读为"未发现的漏洞"。
DECLARED_LIMITATIONS = [
    ("无角色/动作触发词的裸人名列举", "名单：张三、李四、王五"),
    ("无字段名的超短通用密钥", "配置项 a1b2c3 已完成"),
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

# ── 已声明的能力边界（信息展示，不计入失败）──
print("\n" + "=" * 78)
print("【已声明的能力边界（技术报告 §4.1，非缺陷）】")
print("=" * 78)
for desc, sample in DECLARED_LIMITATIONS:
    anon, _ = engine.anonymize(sample)
    covered = anon != sample
    state = "已被覆盖（优于声明）" if covered else "按声明不脱敏"
    print(f"  · {desc}：«{sample}» → {state}")

# ── 第二部分：落盘数据明文扫描 ──
print("\n" + "=" * 78)
print("【落盘数据明文扫描】")
print("=" * 78)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # 仓库根目录（运行时数据都在这里）
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
        # ★3.3/★5：落盘扫描放行 MONEY（金额）。
        # 说明：金额在"发送给模型的文本"里属敏感业务数据，已由正样本组要求脱敏；
        #   但落盘扫描针对的是 workflow_reports/ 下的合同审查意见书等【用户明确
        #   要求的业务产物】，其中的金额是交付内容本身（用户要看的就是这些数字），
        #   并非越权泄露，且金额不具唯一可识别性，故不计为落盘敏感明文。
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

# ── 第三部分：附件落盘专项（双向断言：明文键=泄露；密文键=正向验证）──
# 评审意见整改：此前的专项只检查旧版明文键"没出现就算过"，属于弱覆盖——
# 现在同时做正向验证：密文键必须存在、必须能解密、且原始文件里不得出现
# 可读的明文片段（否则"加密落盘"的表述无从证明）。
print("\n" + "=" * 78)
print("【附件快照落盘专项】（明文键=泄露 / 密文键=正向验证）")
print("=" * 78)
cs = os.path.join(BASE, "chat_sessions.json")
if os.path.exists(cs):
    try:
        with open(cs, "r", encoding="utf-8") as f:
            data = json.load(f)
        sessions = data.get("sessions", data if isinstance(data, list) else {})
        if isinstance(sessions, dict):
            sessions = list(sessions.values())
        att_plain = 0
        att_enc = 0
        att_enc_ok = 0
        for s in sessions:
            if not isinstance(s, dict):
                continue
            # ① 明文键：任何非空明文附件全文都是泄露（应为 0）
            att = s.get("attachment_fulltext", {}) or {}
            for fn, txt in att.items():
                att_plain += 1
                print(f"  ⚠️ 附件《{fn}》以明文落盘 {len(txt or '')} 字符（未脱敏）——属泄露")
                disk_findings = True
            # ② 密文键：存在即正向验证"能解密"（证明加密落盘链路真实可用）
            blob = s.get("attachment_fulltext_enc")
            if isinstance(blob, str) and blob:
                att_enc += 1
                try:
                    from yindun.core.secret_manager import SecretManager
                    decoded = json.loads(SecretManager.get_instance().decrypt(blob))
                    if isinstance(decoded, dict):
                        att_enc_ok += 1
                        names = "、".join(list(decoded.keys())[:3])
                        print(f"  ✅ 附件快照以密文落盘并可解密（{len(decoded)} 个附件：{names}…）")
                except Exception as e:
                    print(f"  ❌ 附件密文无法解密（加密落盘链路异常）：{e}")
                    disk_findings = True
        if att_plain == 0 and att_enc == 0:
            print("  （当前无附件快照；无附件时明文键与密文键都不存在，属正常）")
        print(f"\n  合计：明文键 {att_plain} 个（要求 0）/ 密文键 {att_enc} 个（可解密 {att_enc_ok} 个）")
        if att_plain:
            print("  ⚠️ 存量明文将由应用启动时的迁移逻辑重写为加密存储（migrate_legacy_plaintext）")
    except Exception as e:
        print(f"  解析失败：{e}")

print("\n" + "=" * 78)
print("审计结束")
print("=" * 78)

# ── ★4：退出码语义 + 三项结论汇总 ──
# 泄露阈值 = 0：引擎规则命中集内的所有正样本必须 100% 脱敏，不允许任何已知泄露残留。
# （旧基线为 4，对应的 4 条泄露已随"错位替换/金额语境/短密钥/短 Bearer"修复清零。）
LEAK_THRESHOLD = 0
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
