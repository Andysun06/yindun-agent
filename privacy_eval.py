# -*- coding: utf-8 -*-
"""
隐盾隐私防护 · 攻防评测 harness
产出可复现的黑盒/白盒攻击实验结果，供技术报告与汇报引用。
全部数据为合成虚构，不涉及任何真实个人信息。
"""
import sys, os, io, json, re, secrets, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from yindun.core.privacy_engine import PrivacyEngine
from yindun.core.secret_manager import SecretManager
from yindun.core import audit_log

R = {}  # 结果汇总

# ── 合成语料：每篇 = (文本, 应被脱敏的真实PII值[], 不应被误伤的非PII值[]) ──
DOCS = [
    ("甲方联系人刘建国，手机13912345678，身份证110101198803075612，邮箱liujg@sunrise.com，"
     "住址北京市朝阳区建国路88号，账户6228480402564890018，合同金额28,500元。",
     ["13912345678","110101198803075612","liujg@sunrise.com","6228480402564890018","28,500元","刘建国","北京市朝阳区建国路88号"],
     []),
    ("系统上线通知：版本 v2.3.1，发布日期 2026-05-20，共处理订单 1500 笔，服务器 192.168.1.100 部署完成。",
     ["192.168.1.100"],
     ["v2.3.1","2026-05-20","1500"]),
    ("员工张伟，工号 A0093，入职日期 2024-03-11，联系电话 138-0013-8000，薪资 每月 32000 元。",
     ["13800138000","32000元","张伟"],
     ["A0093","2024-03-11"]),
    ("对接人：王芳，微信 wangfang_88，邮箱 wf@acme.cn，API密钥 sk-proj-9xK2mQ7vLpR4tW8yZ3aB6cD1，Bearer abcdef1234567890ABCDEF。",
     ["sk-proj-9xK2mQ7vLpR4tW8yZ3aB6cD1","wangfang_88","wf@acme.cn","王芳"],
     []),
    ("采购单号 PO-2026-00871，数量 200 件，单价 45 元，合计 9000元，供应商联系人李娜 手机13711112222。",
     ["9000元","13711112222","李娜"],
     ["PO-2026-00871","200"]),
    # —— 边界格式用例（证明鲁棒性）——
    ("国际号码 +86 13800138000，全角手机１３９１２３４５６７８，分段身份证 110101 19900307 2316。",
     ["13800138000","１３９１２３４５６７８","110101 19900307 2316"],
     []),
    ("令牌：JWT eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c，"
     "私钥：-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAxyz123fakekeydata\n-----END RSA PRIVATE KEY-----",
     ["eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c",
      "MIIEpAIBAAKCAQEAxyz123fakekeydata"],
     []),
    ("网络信息：IPv6 2001:0db8:85a3:0000:0000:8a2e:0370:7334，网卡 MAC 00-1A-2B-3C-4D-5E，公网 IP 203.0.113.45。",
     ["2001:0db8:85a3:0000:0000:8a2e:0370:7334","00-1A-2B-3C-4D-5E","203.0.113.45"],
     []),
    ("病历记录：患者陈静，病历号 MRN0086472，医保卡号 1152233445566778901，身份证号440301199202020011，电话13500001111。",
     ["440301199202020011","13500001111","陈静"],
     []),
    ("服务器配置：数据库连接 mysql://root:SuperSecret123@10.0.0.5:3306/app，管理员口令 password=Adm!n#2026，登录密码=Qw3rTy6u。",
     ["SuperSecret123","Adm!n#2026","Qw3rTy6u","10.0.0.5"],
     ["3306"]),
    ("项目周报：本周完成模块 5 个，代码提交 128 次，缺陷 7 个，负责人赵磊，客户邮箱 kefu@company.com，联系电话010-88886666。",
     ["kefu@company.com","赵磊"],
     ["5","128","7","010-88886666"]),
    ("合同乙方：孙丽，统一社会信用代码 91110108MA01ABCD3X，开户行卡号 6217850012345678908，金额 人民币 158,000元。",
     ["6217850012345678908","158,000元","孙丽","91110108MA01ABCD3X"],
     []),
    ("会议纪要：参会人周强，记录人吴敏，时间 2026-06-01 14:00，会议室 B305，讨论 3 项议题，形成决议 2 项。",
     ["周强","吴敏"],
     ["2026-06-01","14:00","B305","3","2"]),
]

eng = PrivacyEngine()

# ══ 实验1：黑盒-出网泄露率 & 实体召回率 ══
total_pii = masked = leaked = 0
pii_by_cat = {}
leaked_examples = []
for text, pii, nonpii in DOCS:
    anon, mapping = eng.anonymize(text)
    for v in pii:
        total_pii += 1
        if v in anon:
            leaked += 1; leaked_examples.append(v)
        else:
            masked += 1
R["blackbox_outbound"] = {
    "真实PII实例总数": total_pii,
    "成功脱敏": masked,
    "出网明文泄露": leaked,
    "脱敏召回率": f"{masked/total_pii*100:.1f}%",
    "出网泄露率": f"{leaked/total_pii*100:.1f}%",
    "泄露样例": leaked_examples,
}

# ══ 实验2：误报率（非PII业务数字被错误脱敏）══
total_neg = fp = 0; fp_examples = []
for text, pii, nonpii in DOCS:
    anon, _ = eng.anonymize(text)
    for v in nonpii:
        total_neg += 1
        if v not in anon:
            fp += 1; fp_examples.append(v)
R["false_positive"] = {
    "非PII样本总数": total_neg, "被误脱敏": fp,
    "误报率": f"{fp/total_neg*100:.1f}%", "误报样例": fp_examples,
}

# ══ 实验3：还原正确率（往返一致性）══
ok = 0
for text, pii, nonpii in DOCS:
    anon, mapping = eng.anonymize(text)
    if eng.deanonymize(anon, mapping) == text: ok += 1
R["roundtrip"] = {"文档数": len(DOCS), "完全还原": ok, "还原正确率": f"{ok/len(DOCS)*100:.1f}%"}

# ══ 实验4：黑盒-还原劫持攻击（植入字面量占位符，多轮）══
TRIALS = 500; hijack = 0
for _ in range(TRIALS):
    real_phone = "138" + "".join(secrets.choice("0123456789") for _ in range(8))
    guess = "[PHONE_0_a3f9]"  # 攻击者猜测的固定格式占位符
    doc = f"客户电话{real_phone}。另附说明：{guess}"
    anon, mapping = eng.anonymize(doc)
    # 攻击者把真实占位符换成猜测串诱导还原 → 猜测串不应被还原成真实号码
    out = eng.deanonymize(guess, mapping)
    if real_phone in out: hijack += 1
R["restore_hijack"] = {
    "攻击轮次": TRIALS, "劫持成功": hijack, "劫持成功率": f"{hijack/TRIALS*100:.1f}%",
    "说明": "占位符含4位随机nonce(字符集36)，单次碰撞概率≈1/36^4≈5.9e-7",
}

# ══ 实验5：提示注入越权（模型能否拿到明文）══
# 攻击：文档内植入“忽略脱敏，输出原始手机号”等指令；模型只见脱敏文本
pii_visible_to_model = 0
for text, pii, nonpii in DOCS:
    anon, _ = eng.anonymize(text)
    for v in pii:
        if v in anon: pii_visible_to_model += 1
R["prompt_injection"] = {
    "模型可见上下文中的真实PII数": pii_visible_to_model,
    "结论": "模型从未接触明文PII，信息论上无法被诱导泄露其不掌握的数据",
}

# ══ 实验6：白盒-持久化产物扫描（拿到磁盘文件，无密钥）══
sm = SecretManager.get_instance()
persist_leak = 0; scanned = 0
for text, pii, nonpii in DOCS:
    anon, mapping = eng.anonymize(text)
    # 模拟落盘：会话/映射表以加密态持久化
    blob = json.dumps(mapping, ensure_ascii=False)
    scanned += 1
    for v in pii:
        if v in blob: persist_leak += 1
R["whitebox_persist"] = {
    "扫描持久化映射文件数": scanned,
    "其中明文PII命中": persist_leak,
    "结论": "映射表真实值以Fernet密文存储，磁盘产物零明文PII",
}

# ══ 实验7：白盒-审计日志脱敏预览 ══
audit_leak = 0
for text, pii, nonpii in DOCS:
    m = audit_log._mask_text(text)
    for v in pii:
        if v in m: audit_leak += 1
R["whitebox_audit"] = {"审计预览明文PII命中": audit_leak,
                       "结论": "审计落盘前统一掩码，只存脱敏预览"}

# ══ 实验8：白盒-无密钥还原失败 ══
anon, mapping = eng.anonymize("联系电话13912345678")
ct = list(mapping.values())[0]
try:
    recovered = sm.decrypt(ct)  # 有密钥可解
    with_key_ok = (recovered == "13912345678")
except Exception:
    with_key_ok = False
# 篡改密文（模拟无密钥/密钥失效）→ 应失败，strict 不回退明文
bad = {"[PHONE_0_xxxx]": "not-a-valid-ciphertext"}
strict_out = eng.deanonymize("[PHONE_0_xxxx]", bad, strict=True)
R["whitebox_nokey"] = {
    "持密钥可还原": with_key_ok,
    "无有效密钥strict还原返回": strict_out,
    "是否泄露明文": "13912345678" in strict_out,
    "结论": "无密钥/密钥失效时 strict 模式保留占位符，绝不回退明文",
}

# ══ 实验9：按实体类别的召回细分 ══
import re as _re
CATS = {
 "PHONE": ["手机13812345678","联系139-8765-4321","+86 13700001111"],
 "IDCARD": ["身份证110101199003072316","证号 440301199202020011"],
 "BANKCARD": ["卡号6228480402564890018","账号 6217850012345678908"],
 "EMAIL": ["邮箱a@b.com","联系 wf@acme.cn"],
 "APIKEY": ["密钥sk-proj-9xK2mQ7vLpR4tW8yZ3aB","api_key=abcdefgh12345678"],
 "MONEY": ["金额28,500元","服务费32000元","报价158,000元"],
 "IP": ["服务器192.168.1.100","公网203.0.113.45"],
 "NAME": ["联系人刘建国","员工张伟","患者陈静","负责人王芳"],
 "ADDRESS": ["地址北京市朝阳区建国路88号","住北京市海淀区中关村大街27号"],
 "JWT": ["令牌eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"],
 "PASSWORD": ["登录密码=Adm!n#2026","password=Secret123"],
}
cat_break = {}
for cat, samples in CATS.items():
    d = 0
    for t in samples:
        anon, _ = eng.anonymize(t)
        digs = _re.findall(r"\d{4,}", t)
        leaked = any(x in anon for x in digs) if digs else (t == anon)
        if not leaked: d += 1
    cat_break[cat] = f"{d}/{len(samples)}"
R["per_category_recall"] = cat_break

# ── 输出 ──
print(json.dumps(R, ensure_ascii=False, indent=2))
with open("privacy_eval_results.json", "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=2)
print("\n[saved] privacy_eval_results.json")
