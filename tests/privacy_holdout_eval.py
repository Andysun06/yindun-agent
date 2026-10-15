# -*- coding: utf-8 -*-
"""隐盾 · 隐私脱敏「留出集」评测（规则冻结后的独立测量）

★ 为什么要有这套件
  `tests/privacy_eval.py`（13 篇 / 41 实例）是**规则调优所用**的开发集——
  在它上面报"召回率"属于自证。本套件另建**留出集**：随机化生成、注入值与
  开发集不重合、形态按办公真实写法分布，用来给出一个**不依赖于调参过程**的测量。

★ 评测协议（写进技术报告的就是这份口径）
  1. 留出集由种子（默认 20261015）确定性地生成，生成器在仓库内，可一键复现；
  2. **规则冻结后一次性运行**，结果如实记录；若运行中发现缺陷，走
     "发现 → 修复 → 换新种子重跑" 的流程，两次数值都留档；
     不在同一份留出集上反复调参（那会把留出集退化成开发集）；
  3. 召回是**经验测量**，不是安全保证——本套件只设"不得低于已记录基线"的
     回归下限，不设"必须 X%"的门禁；
  4. 真正零容忍的是**正确性不变量**：往返逐字一致、不得出现部分掩码。

★ 口径定义
  · 在册形态（in_scope）：实体类型与写法均在引擎支持范围内 → 计入召回；
  · 未覆盖形态（declared_gap）：已知不支持或有意不拦的写法（顿号列举第 2 个起
    的人名、固话、(at) 混淆邮箱、非 Luhn 数字串、无触发词人名）——**单独统计
    并公开**，不计入在册召回，避免用"口径"掩盖，也避免用"未覆盖"拖低指标。

用法：
  python tests/privacy_holdout_eval.py                 # 默认＝已发布口径（种子 20261017，60 篇）
  python tests/privacy_holdout_eval.py --seed 777      # 换种子重跑（新留出集，会作为新一轮记入日志）
  python tests/privacy_holdout_eval.py --docs 80       # 加量
  python tests/privacy_holdout_eval.py --dump corpus.json   # 导出语料供人工核查（不记入评测日志）
  python tests/privacy_holdout_eval.py --no-log        # 只测不记（诊断用）
  python tests/privacy_holdout_eval.py --prune-log     # 清掉诊断性日志条目（空备注/tmp/临时导出核查）后退出

★ 默认种子与规模＝技术报告所引用的口径；改动默认值等同于换一份留出集，必须在日志与报告中留档。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402

DEFAULT_SEED = 20261017      # ← 报告所引用的留出集（规则冻结后首跑）
DEFAULT_DOCS = 60
_DIAGNOSTIC_NOTES = {"", "tmp", "tmp2", "临时导出核查"}
_RESULT_FILE = Path(__file__).resolve().parent / "privacy_holdout_results.json"

# ══════════════════════════════════════════════════════════════════
#  1. 取值生成器（全部随机，注入值与开发集不重合）
# ══════════════════════════════════════════════════════════════════

_PROVINCES = ["110101", "310104", "440305", "510107", "330106", "420111",
              "320105", "370102", "510104", "440106"]
_CITIES = ["北京市", "上海市", "深圳市", "成都市", "杭州市", "武汉市",
           "南京市", "济南市", "广州市", "西安市"]
_DISTRICTS = ["朝阳区", "海淀区", "南山区", "武侯区", "西湖区", "洪山区",
              "鼓楼区", "历下区", "天河区", "雁塔区"]
_ROADS = ["建国路", "中关村大街", "科技园路", "人民南路", "文三路", "珞喻路",
          "中山北路", "经十路", "体育西路", "高新路"]
_DOMAINS = ["example.com", "corpmail.cn", "163.com", "acme.cn", "sunrise.net", "公司.cn"]
_FULLWIDTH = str.maketrans("0123456789", "０１２３４５６７８９")


def _rand_digits(rng: random.Random, n: int, first_nonzero: bool = False) -> str:
    out = "".join(rng.choice("0123456789") for _ in range(n))
    if first_nonzero and out[0] == "0":
        out = rng.choice("123456789") + out[1:]
    return out


def _rand_alnum(rng: random.Random, n: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(rng.choice(alphabet) for _ in range(n))


def _luhn_check_digit(prefix: str) -> str:
    """由前缀求 Luhn 校验位（ISO/IEC 7812）。"""
    total = 0
    for j, ch in enumerate(reversed(prefix)):
        d = int(ch)
        if j % 2 == 0:          # 追加校验位后，这些位落在"从右数奇数位"→ 需倍增
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def gen_phone(rng, style):
    digits = "1" + rng.choice("3456789") + _rand_digits(rng, 9)
    if style == "plain":
        lit = digits
    elif style == "spaced":
        lit = f"{digits[:3]} {digits[3:7]} {digits[7:]}"
    elif style == "dashed":
        lit = f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    elif style == "fullwidth":
        lit = digits.translate(_FULLWIDTH)
    elif style == "cc86":
        lit = f"+86 {digits}"
    else:
        lit = f"0086-{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return lit, digits


def gen_idcard(rng):
    """生成 GB11643-1999 校验位合法的 18 位身份证号。"""
    body = (rng.choice(_PROVINCES) + rng.choice(["19", "20"])
            + _rand_digits(rng, 2, first_nonzero=True)
            + rng.choice([f"0{m}" for m in range(1, 10)] + ["10", "11", "12"])
            + rng.choice([f"0{d}" for d in range(1, 10)] + ["10", "20", "28"])
            + _rand_digits(rng, 3))
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    codes = "10X98765432"
    check = codes[sum(int(c) * w for c, w in zip(body, weights)) % 11]
    return body + check


def gen_bankcard(rng, style):
    """按真实发卡组织的前缀—长度关系生成（Luhn 合法）卡号。

    62xx 银联 / 4xxx Visa：16 或 19 位；5xxx 万事达：16 位；
    35xx JCB：16 或 19 位；37xx 运通：16 位。
    —— 19 位非银联卡在真实业务中存在（JCB 等），因此保留为常规样本。
    """
    prefix, lengths = rng.choice([
        ("6228", (16, 19)), ("6222", (16, 19)), ("6217", (16, 19)),
        ("4111", (16, 19)), ("5242", (16,)), ("3510", (16, 19)), ("3700", (16,)),
    ])
    total_len = rng.choice(lengths)
    body = prefix + _rand_digits(rng, total_len - len(prefix) - 1)
    card = body + _luhn_check_digit(body)
    if style == "grouped":
        lit = " ".join(card[i:i + 4] for i in range(0, len(card), 4))
    else:
        lit = card
    return lit, card


def gen_email(rng):
    return (f"{rng.choice(['zhang', 'li', 'wang', 'chen', 'zhao', 'sun', 'zhou'])}"
            f".{rng.choice(['wei', 'min', 'lei', 'na', 'qiang', 'jing'])}"
            f"{rng.randint(1, 99)}@{rng.choice(_DOMAINS)}")


def gen_money(rng):
    kind = rng.choice(["comma", "wan", "plain", "usd", "label"])
    if kind == "comma":
        return f"¥{rng.randint(1, 9)},{_rand_digits(rng, 3)},{_rand_digits(rng, 3)}.{_rand_digits(rng, 2)}"
    if kind == "wan":
        return f"{rng.randint(10, 999)}万元"
    if kind == "plain":
        return f"{rng.randint(10000, 999999)}元"
    if kind == "usd":
        return f"USD {rng.randint(1, 9)},{_rand_digits(rng, 3)}"
    return f"合同金额 {rng.randint(100000, 9999999)}"


def gen_name(rng, used=None):
    """生成人名；used 传入同文档已用集合 —— 同文档内不得重名。

    重名会污染"字面值是否残留"的判定（同一字面量出现两次时，脱敏一处另一处仍在，
    会被误判为漏检），因此从生成侧保证同一文档内人名唯一。
    """
    surnames = ["刘", "徐", "胡", "郭", "林", "高", "梁", "宋", "邓", "曾",
                "蔡", "田", "董", "叶", "程", "丁", "卢", "姚", "钟", "谭"]
    given = ["伟", "芳", "磊", "静", "强", "娜", "军", "洋", "勇", "艳",
             "建国", "秀英", "志强", "晓明", "雨欣", "国栋", "雅琴", "文博"]
    for _ in range(80):
        name = rng.choice(surnames) + rng.choice(given)
        if used is None:
            return name
        if name not in used:
            used.add(name)
            return name
    return name


def gen_address(rng):
    return (rng.choice(_CITIES) + rng.choice(_DISTRICTS) + rng.choice(_ROADS)
            + str(rng.randint(1, 888)) + "号")


def gen_ipv4(rng):
    return f"{rng.choice(['10', '172', '192', '203', '114'])}.{rng.randint(0, 255)}.{rng.randint(0, 255)}.{rng.randint(1, 254)}"


def gen_ipv6(rng):
    return ":".join(_rand_digits(rng, 4) for _ in range(4)) + ":0000:0000:" + _rand_digits(rng, 4)


def gen_mac(rng):
    """六组两位十六进制（MAC 形状）。"""
    hexd = "0123456789abcdefABCDEF"
    return ":".join("".join(rng.choice(hexd) for _ in range(2)) for _ in range(6))


def gen_jwt(rng):
    seg = lambda n: _rand_alnum(rng, n)          # noqa: E731
    return "eyJ" + seg(20) + "." + seg(24) + "." + seg(28)


def gen_apikey(rng, kind):
    if kind == "sk":
        return "sk-" + _rand_alnum(rng, 28)
    if kind == "proj":
        return "sk-proj-" + _rand_alnum(rng, 30)
    return _rand_alnum(rng, 24)


def gen_password(rng):
    return _rand_alnum(rng, rng.randint(8, 14))


def gen_medical(rng):
    return "MRN" + _rand_digits(rng, 8, first_nonzero=True)


def gen_insurance(rng):
    return _rand_digits(rng, 20, first_nonzero=True)


def gen_passport(rng):
    return rng.choice("EDGP") + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") + _rand_digits(rng, 7)


def gen_plate(rng):
    return (rng.choice("京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼")
            + rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ") + _rand_digits(rng, 5, first_nonzero=True))


def gen_credit_code(rng):
    alphabet = "0123456789ABCDEFGHJKLMNPQRTUWXY"
    return (rng.choice("19") + rng.choice("1239") + _rand_digits(rng, 6)
            + "".join(rng.choice(alphabet) for _ in range(10)))


def gen_wechat(rng):
    return rng.choice(["wx_", "weixin_", "c"]) + _rand_alnum(rng, rng.randint(7, 12)).lower()


def gen_filepath(rng):
    return "C:\\Users\\" + rng.choice(["tian", "chen", "liu", "zhao"]) + "\\.ssh\\id_rsa"


def gen_sensitive_path(rng):
    return ("D:\\" + rng.choice(["公司", "人事", "财务"])
            + "\\" + rng.choice(["薪资", "合同", "员工花名册", "客户台账"])
            + "\\2026" + rng.choice(["Q1", "Q2", "年报"]) + ".xlsx")


def gen_conn(rng):
    return f"mysql://root:{gen_password(rng)}@{gen_ipv4(rng)}:3306/app"


def gen_cloud_key(rng, kind):
    """云平台密钥：AWS 访问键为 AKIA + 16 位大写（真实长度），GitHub 令牌为 ghp_ + 长串。"""
    if kind == "aws":
        return "AKIA" + "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
                                for _ in range(16))
    return "ghp_" + "".join(rng.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
                            for _ in range(20))


def gen_pem(rng):
    head = "-----BEGIN " + "RSA " + "PRIVATE KEY-----"
    tail = "-----END " + "RSA " + "PRIVATE KEY-----"
    body = "\n".join(_rand_alnum(rng, 48) for _ in range(3))
    return head + "\n" + body + "\n" + tail


def gen_bearer(rng):
    return _rand_alnum(rng, 32) + "=="


def _luhn_valid(number: str) -> bool:
    """Luhn 校验（与引擎同口径）。"""
    digits = re.sub(r"\D", "", number)
    total = 0
    for i, d in enumerate(reversed(digits)):
        v = int(d)
        if i % 2 == 1:
            v *= 2
            if v > 9:
                v -= 9
        total += v
    return total % 10 == 0


def _non_luhn_digits(rng, n: int) -> str:
    """生成"数字长度像卡号、但 Luhn 不通过"的串——保证它是真负样本。"""
    while True:
        s = _rand_digits(rng, n, first_nonzero=True)
        if not _luhn_valid(s):
            return s


def _negatives(rng):
    """业务数字负样本：都不应被脱敏。"""
    return [
        ("订单号", "PO-" + str(rng.randint(2026, 2027)) + "-" + _rand_digits(rng, 5)),
        ("非 Luhn 长数字串", _non_luhn_digits(rng, 16)),
        ("日期", f"{rng.randint(2026, 2027)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"),
        ("版本号", f"v{rng.randint(1, 9)}.{rng.randint(10, 99)}.{rng.randint(100, 999)}"),
        ("数量", str(rng.randint(100, 9999)) + " 件"),
        ("百分比", f"{rng.randint(10, 99)}.{rng.randint(0, 9)}%"),
        ("会议室", "B" + str(rng.randint(100, 999))),
        ("时间", f"{rng.randint(8, 18)}:{rng.randint(10, 59)}"),
    ]


# ══════════════════════════════════════════════════════════════════
#  2. 文档模板（把实体放进真实办公语境）
# ══════════════════════════════════════════════════════════════════

def _item(literal, kind, canon=None, scope="in_scope"):
    return {"literal": literal, "type": kind, "canon": canon or literal, "scope": scope}


def tpl_contract(rng, n):
    """合同片段：甲乙方人名 + 金额 + 信用代码 + 地址 + 手机。"""
    used = set()
    name_a, name_b = gen_name(rng, used), gen_name(rng, used)
    phone_lit, phone_can = gen_phone(rng, rng.choice(["plain", "dashed", "cc86"]))
    money = gen_money(rng)
    addr = gen_address(rng)
    code = gen_credit_code(rng)
    text = (f"第 {n} 号采购合同\n"
            f"甲方（采购方）：{name_a}，联系电话：{phone_lit}，注册地址：{addr}。\n"
            f"乙方（供应方）：{name_b}，统一社会信用代码 {code}。\n"
            f"合同总价款：{money}，分两期支付，首期支付 40%。\n"
            f"签约地点：{rng.choice(_CITIES)}，签订日期 2026-0{rng.randint(1, 9)}-1{rng.randint(0, 9)}。\n")
    items = [_item(name_a, "NAME"), _item(name_b, "NAME"),
             _item(phone_lit, "PHONE", phone_can), _item(money, "MONEY"),
             _item(addr, "ADDRESS"), _item(code, "CREDIT_CODE")]
    return text, items


def tpl_payroll(rng, n):
    """薪酬表：员工姓名（带"姓名"字段标签）+ 银行卡 + 薪资 + 身份证。"""
    rows, items = [], []
    used = set()
    for _ in range(rng.randint(2, 3)):
        name = gen_name(rng, used)
        card_lit, card_can = gen_bankcard(rng, rng.choice(["plain", "grouped"]))
        idn = gen_idcard(rng)
        salary = f"{rng.randint(8000, 45000)}元"
        rows.append(f"姓名：{name}，卡号 {card_lit}，月薪 {salary}，身份证 {idn}")
        items += [_item(name, "NAME"), _item(card_lit, "BANKCARD", card_can),
                  _item(salary, "MONEY"), _item(idn, "IDCARD")]
    text = f"【薪酬发放明细 · 2026 年第 {n} 批】\n" + "\n".join(rows) + "\n合计发放 3 人。\n"
    return text, items


def tpl_ops(rng, n):
    """运维记录：IP / IPv6 / MAC / 连接串 / 口令 / 密钥 / 路径。"""
    ip = gen_ipv4(rng)
    ip6 = gen_ipv6(rng)
    mac = gen_mac(rng)
    conn = gen_conn(rng)
    pwd = gen_password(rng)
    key = gen_apikey(rng, "proj")
    path = gen_filepath(rng)
    text = (f"运维值班记录 #{n}\n"
            f"数据库主库地址 {ip}，IPv6 备用链路 {ip6}，网卡 {mac}。\n"
            f"应用侧连接串：{conn}\n"
            f"管理端口口令 password={pwd}，第三方接口密钥 {key}。\n"
            f"私钥存放位置：{path}（已纳入备份）。\n"
            f"本次变更完成于 {rng.randint(8, 18)}:{rng.randint(10, 59)}，工单 PO-2026-{_rand_digits(rng, 5)}。\n")
    items = [_item(ip, "IP"), _item(ip6, "IPV6"), _item(mac, "MAC"),
             _item(conn, "CONN_STRING"), _item(pwd, "PASSWORD"), _item(key, "APIKEY"),
             _item(path, "FILE_PATH")]
    return text, items


def tpl_medical(rng, n):
    """病历摘要：患者姓名 + 病历号 + 身份证 + 手机 + 医保卡号。"""
    name = gen_name(rng)
    mrn = gen_medical(rng)
    idn = gen_idcard(rng)
    phone_lit, phone_can = gen_phone(rng, rng.choice(["plain", "spaced"]))
    ins = gen_insurance(rng)
    text = (f"门诊病历摘要（第 {n} 次复诊）\n"
            f"患者：{name}，病历号 {mrn}。\n"
            f"身份证号 {idn}，联系电话 {phone_lit}，医保卡号 {ins}。\n"
            f"主诉：反复头痛 3 月余，建议复查。\n")
    items = [_item(name, "NAME"), _item(mrn, "MEDICAL_RECORD"), _item(idn, "IDCARD"),
             _item(phone_lit, "PHONE", phone_can), _item(ins, "MEDICAL_INSURANCE")]
    return text, items


def tpl_csv(rng, n):
    """CSV 台账：邮箱 + 手机 + 地址；姓名在表格体（与表头触发词不在同一行）。"""
    rows, items = [], []
    used = set()
    for _ in range(rng.randint(2, 3)):
        mail = gen_email(rng)
        phone_lit, phone_can = gen_phone(rng, "plain")
        addr = gen_address(rng)
        name = gen_name(rng, used)
        rows.append(f"{name},{mail},{phone_lit},{addr}")
        items += [_item(mail, "EMAIL"), _item(phone_lit, "PHONE", phone_can),
                  _item(addr, "ADDRESS"),
                  _item(name, "NAME_TABLE_CELL", scope="declared_gap")]
    text = f"姓名,邮箱,手机,地址\n" + "\n".join(rows) + "\n"
    return text, items


def tpl_json(rng, n):
    """JSON 配置片段：密钥 / token / 口令 / IP。"""
    key = gen_apikey(rng, "sk")
    token = gen_bearer(rng)
    pwd = gen_password(rng)
    ip = gen_ipv4(rng)
    text = (f'{{"service": "invoice-{n}",\n'
            f' "endpoint": "http://{ip}:8080/api",\n'
            f' "api_key": "{key}",\n'
            f' "access_token": "{token}",\n'
            f' "password": "{pwd}",\n'
            f' "timeout": 30, "retry": 3}}\n')
    items = [_item(key, "APIKEY"), _item(token, "BEARER"), _item(pwd, "PASSWORD"),
             _item(ip, "IP")]
    return text, items


def tpl_weekly(rng, n):
    """周报：参会人 + 邮箱 + 敏感路径 + 日期。"""
    name = gen_name(rng)
    mail = gen_email(rng)
    path = gen_sensitive_path(rng)
    text = (f"项目周报（第 {n} 周）\n"
            f"本周联系人 {name}，反馈邮箱 {mail}。\n"
            f"归档材料：{path}\n"
            f"共处理 {rng.randint(10, 300)} 个文件，命中率 {rng.randint(80, 99)}.5%，"
            f"评审会定于 2026-1{rng.randint(0, 2)}-2{rng.randint(0, 8)} 14:30 在 B{rng.randint(100, 999)}。\n")
    items = [_item(name, "NAME"), _item(mail, "EMAIL"), _item(path, "SENSITIVE_PATH")]
    return text, items


def tpl_mail(rng, n):
    """邮件正文：发件/收件人 + 手机 + 附件路径 + JWT。"""
    name = gen_name(rng)
    phone_lit, phone_can = gen_phone(rng, "dashed")
    jwt = gen_jwt(rng)
    text = (f"发件人：{name}\n收件人：财务共享中心\n"
            f"主题：关于第 {n} 期付款材料的说明\n"
            f"如材料有误请回电 {phone_lit}。附带的接口凭据为 {jwt}，"
            f"请在 24 小时内完成核验。\n")
    items = [_item(name, "NAME"), _item(phone_lit, "PHONE", phone_can), _item(jwt, "JWT")]
    return text, items


def tpl_idlist(rng, n):
    """人员名单：触发词后顿号列举（第 2 个起为已声明未覆盖形态）+ 证件。"""
    used = set()
    names = [gen_name(rng, used) for _ in range(3)]
    plate = gen_plate(rng)
    text = (f"项目例会纪要（第 {n} 期）\n"
            f"参会人：{names[0]}、{names[1]}、{names[2]}，随行车辆 {plate}，"
            f"行程 2026-09-2{rng.randint(0, 8)} 出发。\n")
    items = [_item(names[0], "NAME"), _item(plate, "PLATE"),
             _item(names[1], "NAME", scope="declared_gap"),
             _item(names[2], "NAME", scope="declared_gap")]
    return text, items


def tpl_gaps(rng, n):
    """已知未覆盖形态：固话 / (at) 混淆邮箱 / 非 Luhn 卡号 / 无触发词人名。"""
    landline = f"010-{_rand_digits(rng, 8, first_nonzero=True)}"
    obf = f"{rng.choice(['zhang.wei', 'li.na', 'wang.lei'])} (at) {rng.choice(_DOMAINS)}"
    cardlike = _non_luhn_digits(rng, 16)                     # 形似卡号但 Luhn 不过
    bare = gen_name(rng)
    text = (f"补充说明（第 {n} 条）\n"
            f"值班室固话 {landline}，外部联系邮箱 {obf}。\n"
            f"系统记录的卡面数字 {cardlike}（非 Luhn）。\n"
            f"本轮工作由{bare}、{gen_name(rng)}共同完成。\n")
    items = [_item(landline, "LANDLINE", scope="declared_gap"),
             _item(obf, "EMAIL_OBFUSCATED", scope="declared_gap"),
             _item(cardlike, "BANKCARD_LOOKALIKE", scope="declared_gap"),
             _item(bare, "NAME_BARE", scope="declared_gap")]
    return text, items


def tpl_onboarding(rng, n):
    """入职材料：护照 + 微信 + 口令 + 地址。"""
    name = gen_name(rng)
    passport = gen_passport(rng)
    wechat = gen_wechat(rng)
    pwd = gen_password(rng)
    addr = gen_address(rng)
    text = (f"入职材料登记（第 {n} 号）\n"
            f"姓名：{name}，护照号 {passport}，微信号 {wechat}。\n"
            f"门户账号口令：{pwd}（首次登录需修改），现居住地址 {addr}。\n")
    items = [_item(name, "NAME"), _item(passport, "PASSPORT"), _item(wechat, "WECHAT"),
             _item(pwd, "PASSWORD"), _item(addr, "ADDRESS")]
    return text, items


def tpl_cloudcred(rng, n):
    """云凭据清单：云平台密钥 + PEM 私钥块。"""
    aws = gen_cloud_key(rng, "aws")
    gh = gen_cloud_key(rng, "gh")
    pem = gen_pem(rng)
    text = (f"凭据交接清单 #{n}\n"
            f"对象存储访问键 {aws}\n代码仓库令牌 {gh}\n\n{pem}\n交接完毕后请作废。\n")
    items = [_item(aws, "CLOUD_KEY"), _item(gh, "CLOUD_KEY"), _item(pem, "PRIVATE_KEY")]
    return text, items


def tpl_name_variants(rng, n):
    """复姓人名（带角色触发词）——引擎姓氏表未收录复姓，属"声明支持但未实现"。"""
    compounds = ["欧阳", "司马", "上官", "诸葛", "东方", "司徒", "慕容", "皇甫"]
    picked = rng.sample(compounds, 2)
    names = [c + rng.choice(["明", "静", "伟", "娜", "磊"]) for c in picked]
    mail = gen_email(rng)
    text = (f"外部人员联络单（第 {n} 号）\n"
            f"对接人：{names[0]}，备用联系人：{names[1]}。\n"
            f"如需抄送请发至 {mail}。\n")
    items = [_item(names[0], "NAME_COMPOUND"), _item(names[1], "NAME_COMPOUND"),
             _item(mail, "EMAIL")]
    return text, items


def tpl_money_cn(rng, n):
    """汉字大写金额（合同常见写法）——引擎未实现该数值体系。"""
    cn = rng.choice(["壹拾伍万", "贰拾捌万陆仟", "叁拾万元", "壹佰贰拾万"])
    text = (f"付款条款（第 {n} 条）\n"
            f"合同价款：人民币{cn}元整，签署后 30 日内支付。\n"
            f"（小写：¥{rng.randint(100, 999)},{_rand_digits(rng, 3)},000.00）\n")
    items = [_item(cn + "元", "MONEY_CN", scope="declared_gap")]
    return text, items


def tpl_sep_variants(rng, n):
    """分隔符变体：点分隔手机/卡号、斜杠身份证——引擎未实现这些分隔符。"""
    phone = "1" + rng.choice("3456789") + _rand_digits(rng, 9)
    dotted = f"{phone[:3]}.{phone[3:7]}.{phone[7:]}"
    card = "6228" + _rand_digits(rng, 11)
    card += _luhn_check_digit(card)
    card_dotted = ".".join(card[i:i + 4] for i in range(0, len(card), 4))
    idn = gen_idcard(rng)
    slashed = idn[:6] + "/" + idn[6:14] + "/" + idn[14:]
    text = (f"系统导出记录（第 {n} 批）\n"
            f"联系人号码 {dotted}，结算卡号 {card_dotted}。\n"
            f"证件编号 {slashed}（历史系统格式）。\n")
    items = [_item(dotted, "PHONE_DOTTED", scope="declared_gap"),
             _item(card_dotted, "BANKCARD_DOTTED", scope="declared_gap"),
             _item(slashed, "IDCARD_SLASH", scope="declared_gap")]
    return text, items


TEMPLATES = [tpl_contract, tpl_payroll, tpl_ops, tpl_medical, tpl_csv, tpl_json,
             tpl_weekly, tpl_mail, tpl_idlist, tpl_gaps, tpl_onboarding, tpl_cloudcred,
             tpl_name_variants, tpl_money_cn, tpl_sep_variants]


def build_corpus(seed: int, docs: int):
    rng = random.Random(seed)
    corpus = []
    for i in range(docs):
        text, items = TEMPLATES[i % len(TEMPLATES)](rng, i + 1)
        negs = [v for _, v in _negatives(rng)]
        if i % 3 == 0:                      # 部分文档混入业务数字负样本
            text = text + "备注：" + "，".join(negs[:4]) + "。\n"
        corpus.append({"id": i + 1, "text": text, "items": items, "negatives": negs})
    return corpus


# ══════════════════════════════════════════════════════════════════
#  3. 度量
# ══════════════════════════════════════════════════════════════════

_FRAG_LEN = 8       # 部分掩码检测：≥8 位连续片段残留即判"被部分掩码"

# 仅对"取值即标识号"的类型做数字形态校验；金额/路径/邮箱等含数字但非标识号，
# 只做全字面校验（否则文档里偶合的同数字串会造成假告警报）。
_DIGIT_CHECK_TYPES = {"PHONE", "IDCARD", "IDCARD15", "BANKCARD", "MEDICAL_RECORD",
                      "MEDICAL_INSURANCE", "PASSPORT", "CREDIT_CODE", "MAC"}


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _caught(masked: str, digits: str, item: dict) -> tuple[bool, str]:
    """返回 (是否完整命中, 失败原因)。"""
    lit = item["literal"]
    if lit in masked:
        return False, "字面值残留"
    if item["type"] not in _DIGIT_CHECK_TYPES:
        return True, ""
    canon = re.sub(r"\D", "", item.get("canon") or "")
    if canon and canon in digits:
        return False, "归一化数字残留"
    if len(canon) >= _FRAG_LEN + 2:
        for i in range(0, len(canon) - _FRAG_LEN + 1):
            if canon[i:i + _FRAG_LEN] in digits:
                return False, f"部分掩码（{_FRAG_LEN} 位片段 {canon[i:i + _FRAG_LEN]} 残留）"
    return True, ""


def measure(corpus, engine):
    scope_total = {"in_scope": 0, "declared_gap": 0}
    scope_hit = {"in_scope": 0, "declared_gap": 0}
    per_type = {}
    fp_total = fp_hit = 0
    fp_skipped = []
    roundtrip_ok = roundtrip_bad = 0
    partial_mask = []
    misses = []
    residual_plaintext = []
    fp_examples = []

    for doc in corpus:
        text, items, negs = doc["text"], doc["items"], doc["negatives"]
        masked, mapping = engine.anonymize(text)
        digits = _digits_only(masked)

        # 往返一致性（正确性不变量）
        if engine.deanonymize(masked, mapping) == text:
            roundtrip_ok += 1
        else:
            roundtrip_bad += 1
            residual_plaintext.append(f"doc#{doc['id']} 往返不一致")

        for it in items:
            scope = it["scope"]
            scope_total[scope] += 1
            ok, why = _caught(masked, digits, it)
            key = f"{it['type']}"
            stat = per_type.setdefault(key, {"total": 0, "hit": 0})
            stat["total"] += 1
            if ok:
                scope_hit[scope] += 1
                stat["hit"] += 1
            else:
                misses.append(f"doc#{doc['id']} {it['type']}: {why}")
                if scope == "in_scope":
                    stat.setdefault("miss_in_scope", 0)
                    stat["miss_in_scope"] += 1
                if "部分掩码" in why:
                    partial_mask.append(f"doc#{doc['id']} {it['type']}: {why}")

        # 负样本：必须原样保留。
        # 若该负样本是本文档某个注入值的子串（例："8:57" 落在 IPv6 地址 4278:5770:… 内），
        # 它会随实体一起被替换 —— 这属于"不可测"，不是误伤，计入 skipped 而不计误伤。
        for neg in negs:
            if neg not in text:
                continue
            if any(neg in it["literal"] for it in items):
                fp_skipped.append(f"doc#{doc['id']}: {neg}（为注入值子串，不可测）")
                continue
            fp_total += 1
            if neg in masked:
                fp_hit += 1
            else:
                fp_examples.append(f"doc#{doc['id']}: {neg}")

    return {
        "scope_total": scope_total, "scope_hit": scope_hit, "per_type": per_type,
        "fp_total": fp_total, "fp_hit": fp_hit, "fp_examples": fp_examples,
        "fp_skipped": fp_skipped,
        "roundtrip_ok": roundtrip_ok, "roundtrip_bad": roundtrip_bad,
        "partial_mask": partial_mask, "misses": misses,
        "residual_plaintext": residual_plaintext,
    }


def _pct(hit, total):
    return "—" if total == 0 else f"{hit / total * 100:.1f}%"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--docs", type=int, default=DEFAULT_DOCS)
    ap.add_argument("--dump", type=str, default="")
    ap.add_argument("--note", type=str, default="", help="本轮测什么/改了什么（写入评测日志）")
    ap.add_argument("--no-log", action="store_true", help="不把本轮写入评测日志（诊断用）")
    ap.add_argument("--prune-log", action="store_true", help="清掉诊断性日志条目后退出")
    args = ap.parse_args()

    if args.prune_log:
        if not _RESULT_FILE.exists():
            print("ℹ️ 无结果文件")
            return
        data = json.loads(_RESULT_FILE.read_text(encoding="utf-8"))
        before = data.get("历史", [])
        data["历史"] = [h for h in before if h.get("备注", "") not in _DIAGNOSTIC_NOTES]
        _RESULT_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"日志清理：{len(before)} → {len(data['历史'])} 条")
        return

    corpus = build_corpus(args.seed, args.docs)

    # 留出性自检：注入的字面值不得出现在开发集语料里
    dev_corpus_text = (Path(__file__).resolve().parent / "privacy_eval.py").read_text(encoding="utf-8")
    overlap = [it["literal"] for d in corpus for it in d["items"]
               if len(it["literal"]) >= 8 and it["literal"] in dev_corpus_text]

    engine = PrivacyEngine()
    r = measure(corpus, engine)

    in_total, in_hit = r["scope_total"]["in_scope"], r["scope_hit"]["in_scope"]
    gap_total, gap_hit = r["scope_total"]["declared_gap"], r["scope_hit"]["declared_gap"]
    all_total, all_hit = in_total + gap_total, in_hit + gap_hit

    result = {
        "_protocol": {
            "开发集": "tests/privacy_eval.py（13 篇 / 41 实例，规则调优所用，仅作回归基线）",
            "留出集": "本脚本随机生成；规则冻结后一次性运行，数值如实记录",
            "重新基线条件": "若修复缺陷，须换新种子重跑并同时留档两次数值",
            "门槛说明": "召回为经验测量，不设百分比门禁；零容忍项为往返一致与部分掩码",
        },
        "留出集": {"种子": args.seed, "文档数": args.docs, "生成时间": datetime.now().strftime("%Y-%m-%d")},
        "留出性自检_与开发集重合实例": overlap,
        "在册形态": {"实例总数": in_total, "完整命中": in_hit, "召回": f"{in_hit}/{in_total}",
                     "召回率": _pct(in_hit, in_total)},
        "未覆盖形态": {"实例总数": gap_total, "命中": gap_hit, "召回": f"{gap_hit}/{gap_total}",
                       "召回率": _pct(gap_hit, gap_total)},
        "全量口径": {"实例总数": all_total, "完整命中": all_hit, "召回": f"{all_hit}/{all_total}",
                     "召回率": _pct(all_hit, all_total)},
        "按类型": {k: f"{v['hit']}/{v['total']}" for k, v in sorted(r["per_type"].items())},
        "负样本": {"总数": r["fp_total"], "被误伤": r["fp_total"] - r["fp_hit"],
                   "未误伤率": _pct(r["fp_hit"], r["fp_total"]),
                   "不可测（为注入值子串，已排除）": len(r["fp_skipped"]),
                   "误伤样例": r["fp_examples"][:10]},
        "往返一致性": {"文档数": args.docs, "逐字一致": r["roundtrip_ok"],
                       "不一致": r["roundtrip_bad"],
                       "正确率": _pct(r["roundtrip_ok"], args.docs)},
        "部分掩码检出": r["partial_mask"][:10],
        "未命中明细": r["misses"][:40],
    }

    # ── 评测日志：每次运行追加一条，形成可审计的留出集轮次记录 ──
    #    --dump / --no-log 属诊断性运行，不写日志、也不覆盖结果文件的主字段
    history = []
    if _RESULT_FILE.exists():
        try:
            history = json.loads(_RESULT_FILE.read_text(encoding="utf-8")).get("历史", [])
        except Exception:
            history = []
    if not (args.no_log or args.dump):
        history.append({
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "种子": args.seed, "文档数": args.docs,
            "在册形态": f"{in_hit}/{in_total}", "全量口径": f"{all_hit}/{all_total}",
            "备注": args.note,
        })
    result["历史"] = history

    if args.dump:
        Path(args.dump).write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[dump] 语料已导出：{args.dump}（诊断性运行：不写评测日志、不覆盖结果 JSON）")

    print("=" * 78)
    print("隐盾 · 隐私脱敏留出集评测（独立于调参语料的测量）")
    print("=" * 78)
    print(f"  种子 {args.seed} · 文档 {args.docs} 篇 · 注入实例 {all_total} 个"
          f"（在册 {in_total} + 未覆盖形态 {gap_total}）")
    print(f"  与开发集重合的注入值：{len(overlap)} 个（应为 0）")
    print("-" * 78)
    print(f"  【在册形态】  完整命中 {in_hit}/{in_total} = {_pct(in_hit, in_total)}")
    print(f"  【未覆盖形态】命中 {gap_hit}/{gap_total} = {_pct(gap_hit, gap_total)}"
          f"（已知不支持/有意不拦，单独公开）")
    print(f"  【全量口径】  完整命中 {all_hit}/{all_total} = {_pct(all_hit, all_total)}")
    print(f"  【负样本】    误伤 {r['fp_total'] - r['fp_hit']}/{r['fp_total']}")
    print(f"  【往返一致】  {r['roundtrip_ok']}/{args.docs}")
    print(f"  【部分掩码】  {len(r['partial_mask'])} 例")
    print("-" * 78)
    print("  按类型命中：")
    for k, v in sorted(r["per_type"].items()):
        flag = "" if v["hit"] == v["total"] else "   ← 未全中"
        print(f"    {k:<18} {v['hit']}/{v['total']}{flag}")
    if r["misses"]:
        print("-" * 78)
        print("  未命中明细（最多 40 条）：")
        for m in r["misses"][:40]:
            print("    ·", m)

    # ── 回归下限：与已记录基线比较（换种子则跳过）──
    exit_code = 0
    if r["roundtrip_bad"] or r["partial_mask"]:
        print("\n❌ 正确性不变量被破坏（往返不一致 / 部分掩码）——必须修复")
        exit_code = 1
    if _RESULT_FILE.exists():
        try:
            old = json.loads(_RESULT_FILE.read_text(encoding="utf-8"))
            if old.get("留出集", {}).get("种子") == args.seed and old.get("留出集", {}).get("文档数") == args.docs:
                floor = old["在册形态"]["完整命中"]
                if in_hit < floor:
                    print(f"\n❌ 在册召回低于已记录基线（{in_hit} < {floor}）——疑似回归")
                    exit_code = 1
                else:
                    print(f"\n✅ 在册召回不低于已记录基线（{in_hit} ≥ {floor}）")
            else:
                print("\nℹ️ 种子/规模已变化（新留出集）——不作基线比较，数值留档")
        except Exception as exc:                       # 基线文件损坏不应阻断测量
            print(f"\nℹ️ 基线文件读取失败（{exc}），本次仅记录数值")
    else:
        print("\nℹ️ 首次运行：本次数值将作为回归基线留档")

    if args.dump:
        print("\n[skip] 诊断性运行：未写入结果 JSON（主字段保持已发布口径）")
    else:
        _RESULT_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[saved] {_RESULT_FILE.name}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
