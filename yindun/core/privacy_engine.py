# -*- coding: utf-8 -*-
"""
隐盾隐私脱敏引擎（强化版）

设计原则：
1. anonymize / deanonymize 接口签名与返回格式完全保持向后兼容
   - anonymize(text) -> (anonymized_text, mapping_dict)
   - deanonymize(text, mapping_dict) -> restored_text
2. 实体类型从 3 类扩展到 9 类（正则）+ 人名识别（轻量姓氏触发方案）
3. 映射表"用后即焚"：destroy() 主动清空
4. 审计统计：get_last_stats() 返回本轮脱敏类型计数，便于上层展示
5. 支持 strict 模式：deanonymize(strict=True) 时解密失败不回退明文，防止注入攻击

【安全增强：映射表加密存储】
- 映射表真实值采用 Fernet 对称加密存储（AES-128-CBC + HMAC-SHA256）
- 密钥由 SecretManager 统一管理，首次运行自动生成并持久化到 yindun/cache/.secret_key
- anonymize 写入 mapping 时对真实值 encrypt，占位符本身（如 [PHONE_0_a3f9]）保持明文，方便 LLM 识别
- 占位符含 4 位随机 nonce，消除固定可预测格式导致的"还原劫持"（原文字面量与映射占位符碰撞）
- deanonymize 时透明解密还原，对调用方完全无感
- 兼容旧版明文 mapping：decrypt 失败时回退为原值（strict=True 时不回退，直接跳过），保证已存盘的知识库数据不会因升级而损坏
"""
import re
import secrets

from yindun.core.secret_manager import SecretManager


# 占位符 nonce 字符集：小写字母 + 数字（不含 ']' 等易与占位符结构冲突的字符）
NONCE_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"

# 新格式占位符：[TYPE_N_nonce]（含 4 位随机 nonce，消除固定可预测的碰撞面）
# 旧格式占位符：[TYPE_N]（历史数据，deanonymize 时保持兼容）
_NEW_PLACEHOLDER_RE = re.compile(r"^\[(.+?)_(\d+)_([a-z0-9]{4})\]$")


def _generate_nonce(used: set, length: int = 4) -> str:
    """生成不与 used 集合重复的随机 nonce（保证同一批 anonymize 内唯一）。"""
    while True:
        nonce = "".join(secrets.choice(NONCE_CHARS) for _ in range(length))
        if nonce not in used:
            used.add(nonce)
            return nonce


class PrivacyEngine:
    """隐私脱敏引擎（强化版）

    覆盖实体类型：
        原有 3 类：PHONE / EMAIL / IDCARD
        新增 6 类（正则）：BANKCARD / IP / MONEY / APIKEY / WECHAT / ADDRESS
        新增 1 类（轻量）：NAME（姓氏表 + 上下文触发）
    """

    # ──────────────────────────────────────────
    # 0. 数据分级标签（参照 GB/T 35273 个人信息安全规范 + 办公场景实践）
    #    用于风险量化与审计展示，不影响脱敏逻辑
    # ──────────────────────────────────────────
    DATA_CLASSIFICATION = {
        "IDCARD":   "绝密",   # 身份证号
        "BANKCARD": "绝密",   # 银行卡号
        "PRIVATE_KEY": "绝密",   # PEM 私钥块
        "JWT":         "绝密",   # JWT token
        "APIKEY":   "机密",   # API密钥
        "PHONE":    "机密",   # 手机号
        "MEDICAL_RECORD":    "机密",   # 病历号
        "MEDICAL_INSURANCE": "机密",   # 医保卡号
        "BEARER":       "机密",   # Bearer token（API 凭证）
        "ACCESS_TOKEN": "机密",   # access_token / refresh_token
        "EMAIL":    "内部",   # 邮箱
        "ADDRESS":  "内部",   # 地址
        "WECHAT":   "内部",   # 微信号
        "IP":       "内部",   # IP地址
        "MONEY":    "内部",   # 金额（视场景可调）
        "NAME":     "内部",   # 人名
        "FILE_PATH":    "内部",   # 敏感文件路径
        "IDCARD15": "绝密",   # 15 位一代身份证（身份证号需人工核验）
        "PASSWORD": "绝密",   # 口令字段（password=...）
        "CONN_STRING": "绝密",   # 连接串内嵌密码
        "CLOUD_KEY": "绝密",   # 云平台密钥（GitHub/AWS/Slack/Firebase/SendGrid/Stripe）
        "CREDIT_CODE": "机密",   # 统一社会信用代码
        "PASSPORT": "机密",   # 护照 / 港澳通行证 / 台胞证
        "PLATE":    "机密",   # 车牌号
        "IPV6":     "内部",   # IPv6 地址
        "MAC":      "内部",   # 网卡 MAC 地址
        "SENSITIVE_PATH": "内部",   # 业务敏感路径
    }

    # 分级权重（用于风险评分）
    CLASSIFICATION_WEIGHT = {
        "绝密": 10,
        "机密": 5,
        "内部": 2,
        "公开": 0,
    }

    # 业务分组（按数据所属业务域归类，与风险分级是正交维度）
    # - 分级回答"多敏感"，分组回答"属于哪类业务数据"
    ENTITY_GROUPS = {
        "PII":   ["PHONE", "EMAIL", "IDCARD", "IDCARD15", "PASSPORT", "PLATE", "IPV6", "MAC", "NAME", "ADDRESS", "WECHAT", "IP"],  # WECHAT 改归 PII（个人社交账号）；IP 属个人/设备网络标识
        "PHI":   ["MEDICAL_RECORD", "MEDICAL_INSURANCE"],
        "财务":  ["BANKCARD", "CREDIT_CODE", "MONEY"],
        "密钥":  ["APIKEY", "PRIVATE_KEY", "JWT", "CLOUD_KEY", "PASSWORD", "CONN_STRING"],   # 高危密钥凭证
        "Token": ["BEARER", "ACCESS_TOKEN"],
        "路径":  ["FILE_PATH", "SENSITIVE_PATH"],
    }

    # 实体到分组的反向映射（自动生成，便于 O(1) 查询）
    ENTITY_TO_GROUP = {}
    for _grp, _entities in ENTITY_GROUPS.items():
        for _ent in _entities:
            ENTITY_TO_GROUP[_ent] = _grp
    del _grp, _entities, _ent

    # ──────────────────────────────────────────
    # 1. 正则实体规则表
    #    顺序很重要：先匹配长格式/严格格式，后匹配短格式，避免相互覆盖
    # ──────────────────────────────────────────
    # 需要 IGNORECASE 标志的实体（PRIVATE_KEY 单独走 DOTALL 分支）
    _IGNORECASE_KEYS = {"APIKEY", "MEDICAL_RECORD", "BEARER", "ACCESS_TOKEN", "FILE_PATH",
                        "PASSWORD", "CONN_STRING", "IPV6", "MAC", "SENSITIVE_PATH"}

    PATTERNS = {
        # === 原有 3 类（保持兼容） ===
        # (P1 加固) IDCARD：加 lookaround 边界，避免从更长数字串中截取 18 位误检
        # (A6) 允许 4 位分组的空格/连字符（如 "110101 19900307 2316"）
        "IDCARD": r"(?<![\dXx])[1-9]\d{5}[\s\-]?(?:19|20)\d{2}[\s\-]?(?:0[1-9]|1[0-2])[\s\-]?(?:0[1-9]|[12]\d|3[01])[\s\-]?\d{3}[\dXx](?![\dXx])",
        # (P1 加固) PHONE：加边界；支持 '-'/空格/全角'-' 分隔符分支与全角数字（１３８...）。
        # (A4) 支持 +86 / 0086 国际前缀（捕获组1 = 纯号码，前缀 +86 保留只脱敏号码）。
        # 命中后由 _normalize_digits + _is_valid_phone 程序化校验（精确 11 位），避免截取/漏检。
        "PHONE": r"(?<![\d])(?:\+?86[-\s]?|0086[-\s]?)?((?:1|１)[3-9３-９][\d０-９\s\-－]{2,13}[\d０-９])(?![\d])",
        # (A5) EMAIL 支持中文/国际化域名（如 li_si@公司.cn）
        "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]*[a-zA-Z\u4e00-\u9fa5]{2,}",

        # === 新增 6 类（正则） ===
        # 银行卡号：16-19 位连续数字，且以常见银行前缀开头
        # 前缀参考：62(银联)/4(Visa)/5(万事达)/30,36,38(大莱)/35(JCB)/37(运通)
        # (A6) 允许 4 位分组的空格/连字符（如 "6228 4804 0256 4890 018"）。
        # 注意：用 lookaround 替代 \b，因为 \b 在"中文+数字"交界处不触发
        "BANKCARD": r"(?<![\d])(?:62\d{2}(?:[\s\-]?\d{4}){3}[\s\-]?\d{0,3}"
                    r"|4\d{3}(?:[\s\-]?\d{4}){3}[\s\-]?\d{0,3}"
                    r"|5\d{3}(?:[\s\-]?\d{4}){3}"
                    r"|3[0-8]\d{2}(?:[\s\-]?\d{4}){2}[\s\-]?\d{0,6}"
                    r"|35\d{2}(?:[\s\-]?\d{4}){3}"
                    r"|37\d{2}(?:[\s\-]?\d{4}){3})(?![\d])",

        # === 新增证件/组织（A 组补充 + B 组新增） ===
        # (B1) 15 位一代身份证（含日期段校验，非裸 \d{15}）；命中后由
        # _validate_entity 校验"前文触发词 || 有效地区码"，避免误伤普通数字串
        "IDCARD15": r"(?<!\d)[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)",
        # (B2) 统一社会信用代码（GB32100-2015，18 位，含 登记管理部门/机构类别 字符集过滤）
        "CREDIT_CODE": r"(?<![A-Za-z0-9])[0-9A-HJ-NPQRTUWXY]{2}\d{6}[0-9A-HJ-NPQRTUWXY]{10}(?![A-Za-z0-9])",
        # (B3) 车牌号（民用/新能源/使领馆）；命中后由 _validate_entity 负向判定
        # 前文不含"型号|编号|订单|SKU"，避免误伤普通产品编号
        "PLATE": r"(?<![A-Za-z0-9])[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼]"
                 r"[A-HJ-NP-Z][A-HJ-NP-Z0-9]{4,6}(?![A-Za-z0-9])",
        # (B4) 护照/通行证/台胞证：第一段整体匹配；第二段带捕获组，仅换证件号码、保留前缀
        "PASSPORT": r"(?<![A-Za-z0-9])[EDGPGS][A-Z0-9]\d{7}(?![A-Za-z0-9])"
                    r"|(?:护照号?|通行证号?|台胞证)\s*[:：]?\s*([A-Za-z0-9]{8,10})",

        # IPv4 地址（前后不能是数字或点，避免误匹配版本号等）
        "IP": r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(?![\d.])",

        # (B5) IPv6 地址（至少 4 段，避免误伤 HH:MM:SS 时间如 14:30:25）
        "IPV6": r"(?<![A-Za-z0-9:])(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}(?![A-Za-z0-9:])",
        # (B6) 网卡 MAC 地址（xx:xx:xx:xx:xx:xx 或 xx-xx-xx-xx-xx-xx）
        "MAC": r"(?<![A-Za-z0-9])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![A-Za-z0-9])",

        # 金额：¥100,000 / 85万元 / 3.5亿 / 12.5万 / 价格 500元 / 25000元
        # 说明（第5步精修）：分支2 加左边界 (?<![\d.,]) 并【恢复裸"元"】。
        #   - 左边界阻止"转账金额 1250000 元"这类普通金额被从长数字串中间截取命中（E 组负样本零误伤）；
        #   - 纯数字无单位（如"1250000 已到账"）属有意取舍：加纯数字规则会误伤业务数字，故不匹配。
        # (C5) 新增分支3：4~10 位裸数字+元（25000元/100000元）——合同大额写法，
        #   原模式 \d{1,3}(?:,\d{3})* 元 对 5 位以上无逗号数字无有效起始位置（lookbehind 全挡），整体漏检。
        "MONEY": r"(?:¥|￥)\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?"
                 r"|(?<![\d.,])\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:万元|亿元|万元整|元|万|亿)"
                 r"|(?<![\d.,])\d{4,10}\s?元"
                 r"|(?i:\b(?:USD|CNY|RMB|EUR|GBP|JPY|HKD|RUB|KRW)\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?)",

        # API 密钥：sk-xxx / api_key=xxx / APIKEY: xxx
        # 用 lookaround 替代 \b，适配中文前缀场景（如"密钥sk-xxx"）
        # (P1 加固) APIKEY：sk- 分支字符集加入 -/_，覆盖 sk-proj-xxx 系列；api[_-]?key= 分支值字符集同样允许 -/_
        # (A1) sk- 分支最小长度由 20 降到 8，并显式支持 sk-proj- 前缀
        "APIKEY": r"(?<![A-Za-z0-9])sk-(?:proj-)?[A-Za-z0-9_-]{8,}(?![A-Za-z0-9])"
                  r"|api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_-]{16,}['\"]?",

        # (B7) 口令字段（password=...）；带捕获组，仅换口令值、保留字段名
        # (C3) 纳入中文标签（密码/口令/密码：），覆盖"登录密码=xxx"等中文办公场景
        # (C4) 值字符集排除中文标点（，。；：等），避免口令值吞掉后续整句
        "PASSWORD": r"(?i)(?:password|passwd|pwd|passphrase|登录密码|管理密码|访问密码|密码|口令)\s*[=:：]\s*['\"]?([^\s'\"，。；、！？（）【】《》\-—]{4,})['\"]?",
        # (B8) 连接串内嵌密码（user:password@）；带捕获组，仅换密码段
        "CONN_STRING": r"(?i)\b(?:mysql|postgres(?:ql)?|mongodb|redis|amqp|ftp|https?)://[^\s:/@]+:([^\s@/]{3,})@",
        # (B9) 云平台密钥前缀（GitHub/AWS/Slack/Firebase/SendGrid/Stripe）
        "CLOUD_KEY": r"(?<![A-Za-z0-9])(?:ghp_|gho_|ghu_|ghs_|ghr_|github_pat_)[A-Za-z0-9_]{16,}"
                     r"|AKIA[0-9A-Z]{16}"
                     r"|xox[baprs]-[A-Za-z0-9-]{10,}"
                     r"|AIza[0-9A-Za-z_\-]{35}"
                     r"|SG\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"
                     r"|sk_(?:live|test)_[A-Za-z0-9]{16,}",
        # (B10) 业务敏感路径（关键词判定，非后缀判定）：路径 + 业务敏感词
        "SENSITIVE_PATH": r"(?:[A-Za-z]:[\\/]|/home/|/data/|/var/|~/)[^\s，。；\"']*?"
                          r"(?:薪资|工资|薪酬|员工|人事|花名册|合同|客户|财务|账目|密码|口令|征信|病历|体检)[^\s，。；\"']*",

        # 微信号：需"微信"前缀触发，避免误伤（微信号格式：字母开头 6-20 位）
        "WECHAT": r"(?:微信|微信号|wechat|WeChat)\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{5,19})",

        # 地址：省市区+路+号 模式（覆盖最常见的中文办公地址）
        # (放宽) 省份为可选（支持"海淀区中关村大街1号"无省前缀）；
        # 必需出现"市/区/县/旗"行政区划（分支1），或【带数字】的门牌号（分支2），
        # 避免误伤"订单号/型号/座机号"这类以"号/街/路"结尾的普通业务词
        "ADDRESS": r"(?:(?:北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆|香港|澳门)(?:省|市|自治区|特别行政区)?)?"
                   r"(?:(?:[^\s，。、；]{2,8}(?:市|区|县|旗))"
                   r"(?:[^\s，。、；]{1,20}(?:路|街|道|巷|弄|大道)?)"
                   r"(?:[^\s，。、；]{0,15}(?:号|幢|栋|单元|室|大厦|广场|大楼))?"
                   r"|(?:[^\s，。、；]{2,15}(?:路|街|道|巷|弄))?"
                   r"(?:[^\s，。、；]{0,9}\d[^\s，。、；]{0,3}(?:号|幢|栋|单元|室)))",

        # === PHI 个人健康信息 ===
        # 病历号：常见前缀 MRN/BLH/病历号/病案号 + 数字
        # 格式：MRN123456 / 病历号 12345678 / 病案号：123456
        "MEDICAL_RECORD": r"(?i)(?:MRN|BLH|病历号|病案号|门诊号|住院号)\s*[:：]?\s*(\d{6,12})",

        # 医保卡号：20 位社会保障卡号（常以"医保"前缀触发）
        # 格式：医保卡号 12345678901234567890 / 医保号：1234567890123456789
        "MEDICAL_INSURANCE": r"(?:医保卡号?|医保号|社保卡号?|社保号)\s*[:：]?\s*(\d{15,20})",

        # === 密钥组（高危） ===
        # PEM 私钥块：-----BEGIN XXX PRIVATE KEY----- ... -----END XXX PRIVATE KEY-----
        # 覆盖 RSA/EC/OpenPGP/DSA 等常见私钥格式，整个块替换为一个占位符
        # 用 DOTALL 让 . 匹配换行，非贪婪匹配避免跨块
        "PRIVATE_KEY": r"-----BEGIN (?:RSA |EC |OPENPGP |DSA |ENCRYPTED )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENPGP |DSA |ENCRYPTED )?PRIVATE KEY-----",

        # JWT token：三段式 base64.base64.base64，第一段 header 以 eyJ 开头
        # 长度限制：每段至少 10 字符，避免误匹配普通 base64 字符串
        # 前后不能是字母数字和 ._-=，避免匹配 URL 中的片段
        "JWT": r"(?<![A-Za-z0-9._\-])eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}(?![A-Za-z0-9._\-])",

        # === Token 组（API 凭证） ===
        # Bearer token：Authorization: Bearer xxx / Bearer xxx
        # 捕获组只取 token 部分（不含 "Bearer " 前缀）
        # token 长度限制：默认 24+（高置信），应需求收紧到 8+（短 token 也识别，交由人工二次确认）
        # (A2) Bearer token 最小长度 20 → 8
        "BEARER": r"(?i)(?:Authorization\s*[:：]\s*Bearer\s+|Bearer\s+)([A-Za-z0-9_\-\.=]{8,})",

        # access_token / refresh_token：access_token=xxx / refresh_token: xxx
        # 捕获组只取 token 值（不含 key 名）
        # (A3) 最小长度由 16 降到 8，并纳入 token|secret 字面
        "ACCESS_TOKEN": r"(?i)(?:access_token|refresh_token|id_token|auth_token|token|secret)\s*[=:]\s*['\"]?([A-Za-z0-9_\-\.=]{8,})['\"]?",

        # === 路径组 ===
        # 敏感文件路径：覆盖常见密钥/配置/凭据文件
        # 格式：C:\Users\xxx\.ssh\id_rsa / /home/user/.env / ~/.aws/credentials
        # 用捕获组只取路径部分（不含前缀如"路径："）
        # 覆盖场景：
        #   - .ssh 目录（id_rsa / id_ed25519 / known_hosts）
        #   - 云厂商凭据（.aws/credentials / .config/gcloud）
        #   - 配置文件（.env / config.ini / application.yml）
        #   - Windows 和 Unix 路径风格都支持
        "FILE_PATH": r"(?:(?:[A-Za-z]:[\\/]|~[\\/]|\/home\/[^\/\s]+\/|\/etc\/|\/var\/)[^\s，。；\"']*?(?:\.ssh[\\/](?:id_rsa|id_ed25519|id_ecdsa|known_hosts|authorized_keys)|\.aws[\\/]credentials|\.config[\\/]gcloud|\.env|config\.ini|application\.yml|application\.properties|\.git-credentials|\.npmrc|\.pypirc))",
    }

    # ──────────────────────────────────────────
    # 1.1 命中后校验开关
    # ──────────────────────────────────────────
    # (P1 加固) IDCARD 可选增强：GB11643-1999 18 位校验位算法。
    # 默认关闭——既有测试数据（如 110101199001011234）校验位不合法，若强制校验会导致历史数据不命中；
    # 置 True 启用后，身份证正则命中后需通过 _idcard_valid 才记为命中。
    IDCARD_CHECKSUM = False

    # ──────────────────────────────────────────
    # 1.2 命中后程序化校验（正则粗筛 + 算法精验）
    # ──────────────────────────────────────────
    # 15 位身份证：前文触发词（用于 IDCARD15 上下文精验）
    _IDCARD15_TRIGGERS = ("身份证", "证件号", "证件号码", "身份证号", "居民身份", "身份证号码")
    # 15 位身份证：前两位地区码须落在有效省份码集合（避免误伤普通 15 位数字串）
    _PROVINCE_CODES = {"11", "12", "13", "14", "15", "21", "22", "23", "31", "32", "33",
                       "34", "35", "36", "37", "41", "42", "43", "44", "45", "46", "50",
                       "51", "52", "53", "54", "61", "62", "63", "64", "65", "71", "81",
                       "82", "91"}
    # 车牌号：前文含这些词时判为产品编号而非车牌（负向判定）
    _PLATE_BLOCK_WORDS = ("型号", "编号", "订单", "SKU", "sku", "批号")

    @staticmethod
    def _normalize_digits(text: str) -> str:
        """统一数字字符：全角数字→半角，其余非数字字符（分隔符等）剔除。"""
        return "".join(
            ch if '0' <= ch <= '9' else chr(ord(ch) - 0xFEE0)
            for ch in text
            if ('0' <= ch <= '9') or ('０' <= ch <= '９')
        )

    @staticmethod
    def _is_valid_phone(raw: str) -> bool:
        """PHONE 程序化精验：去除分隔符/全角转半角后必须恰为 11 位，且以 1[3-9] 开头。"""
        digits = PrivacyEngine._normalize_digits(raw)
        return len(digits) == 11 and digits[0] == "1" and digits[1] in "3456789"

    @staticmethod
    def _luhn_valid(number: str) -> bool:
        """Luhn 校验（ISO/IEC 7812）：银行卡号最后一位为校验位，用于过滤随机数字串。"""
        digits = PrivacyEngine._normalize_digits(number)
        if len(digits) < 13:
            return False
        total = 0
        for i, d in enumerate(reversed(digits)):
            d = int(d)
            if i % 2 == 1:
                d *= 2
                if d > 9:
                    d -= 9
            total += d
        return total % 10 == 0

    @staticmethod
    def _idcard_valid(idcard: str) -> bool:
        """GB11643-1999 18 位身份证校验位校验（可选增强，默认不启用）。"""
        digits = PrivacyEngine._normalize_digits(idcard)
        if len(digits) != 17:
            return False
        weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
        check_codes = "10X98765432"
        s = sum(int(d) * w for d, w in zip(digits, weights))
        expect = check_codes[s % 11]
        last = idcard[-1].upper() if idcard else ""
        return expect == last

    @staticmethod
    def _validate_entity(key: str, match: str, context: str = "", pos: int = -1) -> bool:
        """
        正则命中后的精验调度：
        - PHONE：_is_valid_phone（精确 11 位）
        - BANKCARD：先去分隔符归一化，再做 Luhn 校验
        - IDCARD：可选校验位（默认关闭）
        - IDCARD15：前文含身份证触发词，或前两位地区码为有效省份码（防御随机 15 位数字）
        - PLATE：前文含"型号|编号|订单|SKU"等词时为产品编号，判负
        """
        if key == "PHONE":
            return PrivacyEngine._is_valid_phone(match)
        if key == "BANKCARD":
            return PrivacyEngine._luhn_valid(PrivacyEngine._normalize_digits(match))
        if key == "IDCARD" and PrivacyEngine.IDCARD_CHECKSUM:
            return PrivacyEngine._idcard_valid(match)
        if key == "IDCARD15":
            if context and pos >= 0:
                pre = context[max(0, pos - 20):pos]
                if any(t in pre for t in PrivacyEngine._IDCARD15_TRIGGERS):
                    return True
            return match[:2] in PrivacyEngine._PROVINCE_CODES
        if key == "PLATE":
            if context and pos >= 0:
                pre = context[max(0, pos - 20):pos]
                if any(w in pre for w in PrivacyEngine._PLATE_BLOCK_WORDS):
                    return False
            return True
        return True

    # ──────────────────────────────────────────
    # 2. 人名识别（轻量方案）
    #    姓氏表 + 上下文触发词
    #    姓氏来源：古代《百家姓》前 100（按宋代政治排序）∪ 当代人口高频姓氏
    #    —— 仅用古百《百家姓》会漏掉"刘/徐/胡/郭/林"等当代大姓（刘按现代人口排第 4），
    #       故按第七次全国人口普查高频姓氏补全。
    # ──────────────────────────────────────────
    SURNAMES = {
        "赵", "钱", "孙", "李", "周", "吴", "郑", "王", "冯", "陈",
        "褚", "卫", "蒋", "沈", "韩", "杨", "朱", "秦", "尤", "许",
        "何", "吕", "施", "张", "孔", "曹", "严", "华", "金", "魏",
        "陶", "姜", "戚", "谢", "邹", "喻", "柏", "水", "窦", "章",
        "云", "苏", "潘", "葛", "奚", "范", "彭", "郎", "鲁", "韦",
        "昌", "马", "苗", "凤", "花", "方", "俞", "任", "袁", "柳",
        "酆", "鲍", "史", "唐", "费", "廉", "岑", "薛", "雷", "贺",
        "倪", "汤", "滕", "殷", "罗", "毕", "郝", "邬", "安", "常",
        "乐", "于", "时", "傅", "皮", "卞", "齐", "康", "伍", "余",
        "元", "卜", "顾", "孟", "平", "黄", "和", "穆", "萧", "尹",
        # —— 当代高频姓氏补全（七普数据，古《百家姓》前100未收录）——
        "刘", "徐", "胡", "郭", "林", "高", "梁", "宋", "邓", "曾",
        "蔡", "田", "董", "叶", "程", "丁", "卢", "姚", "钟", "谭",
        "陆", "汪", "石", "廖", "贾", "夏", "白", "熊", "邱", "江",
        "闫", "段", "侯", "龙", "邵", "黎", "毛", "龚", "万",
    }

    # 人名触发词：前文出现这些词时，才在后续窗口内匹配"姓+名"
    NAME_TRIGGERS = (
        "甲方", "乙方", "丙方", "丁方",
        "姓名", "名字", "叫", "联系人", "对接人", "经办人", "负责人",
        "先生", "女士", "经理", "主管", "总监", "总", "主任", "科长",
        "局长", "处长", "专员", "助理", "代表", "签字", "签署",
        "当事人", "原告", "被告", "证人", "客户", "供应商",
        # (C1) 补充的金融/合同/授权类触发词
        "签署人", "签约人", "开户人", "收款人", "付款人", "投保人", "被保险人", "受益人",
        "法定代表人", "授权代表", "经办人", "当事人",
        "监护人", "代理人", "担保人", "申请人",
    )

    # (C2) 人名弱规则开关："姓氏+2字"误伤率高，默认关闭。
    # 仅当显式置 True 时，才在句首/冒号后/顿号后位置启用弱规则。
    # 默认优先用 add_custom_names() 通讯录精确匹配解决。
    NAME_WEAK_MODE = False

    # ──────────────────────────────────────────
    # 3. 实例状态
    # ──────────────────────────────────────────
    def __init__(self):
        self._custom_names: set = set()  # 用户补充的已知人名
        self._last_stats: dict = {}      # 上次 anonymize 的统计
        self._mapping: dict = {}         # 当前映射表（供 destroy 使用）
        # 加密器：用于对 mapping 中真实值进行 Fernet 对称加密
        self._crypto = SecretManager.get_instance()

    # ──────────────────────────────────────────
    # 正向脱敏（接口完全保持兼容）
    # ──────────────────────────────────────────
    def anonymize(self, text: str):
        """
        【正向脱敏】
        将文本中的敏感实体提取出来存入映射表，并在原句中替换为占位符。

        返回格式（与原版完全一致）：
            (anonymized_text, mapping_dict)
            mapping_dict = {"[PHONE_0_a3f9]": "<base64 密文>", ...}   # 占位符含随机 nonce
            - 占位符（key）保持明文，方便 LLM 识别
            - 真实值（value）已通过 SecretManager.encrypt 加密为 base64 密文
            - deanonymize 时会透明解密还原，对调用方无感
        """
        if not text:
            self._last_stats = {}
            return text, {}

        mapping = {}
        counts = {}
        anonymized = text
        # 用于去重：同一真实值只分配一个占位符
        value_to_placeholder = {}
        # 同批 anonymize 内 nonce 去重（防止两个占位符使用相同 nonce）
        used_nonces = set()

        # === 阶段 1：正则实体脱敏 ===
        # 按固定顺序处理，避免相互干扰（D4）
        # 说明：
        #   - IDCARD15 必须在 BANKCARD 与 PHONE 之前（15 位证 3 位顺序码可能被误当成其它）
        #   - IPV6 必须在 IP 之前（否则 IPv6 中的数字段可能被 IPv4 规则截断）
        #   - CREDIT_CODE 必须在 BANKCARD 之后、PHONE 之前
        #   - 密钥组（JWT/PRIVATE_KEY/PASSWORD/CONN_STRING/CLOUD_KEY）放中后段，
        #     因为带字段名前缀（如 password=），不会与纯数字实体冲突
        #   - FILE_PATH/SENSITIVE_PATH/PLATE/PASSPORT/MAC 放最末尾，避免交叉覆盖
        regex_order = [
            "IDCARD", "IDCARD15", "BANKCARD", "CREDIT_CODE",
            "MEDICAL_RECORD", "MEDICAL_INSURANCE",  # PHI 组
            "PHONE", "EMAIL",
            "IPV6", "IP", "MONEY", "WECHAT", "ADDRESS",  # IPV6 必须在 IP 之前
            "PASSWORD", "CONN_STRING", "CLOUD_KEY", "APIKEY", "JWT", "PRIVATE_KEY",  # 密钥组
            "BEARER", "ACCESS_TOKEN",          # Token 组
            "FILE_PATH", "SENSITIVE_PATH", "PLATE", "PASSPORT", "MAC",  # 路径/证照组
        ]
        # 带捕获组的实体：只替换捕获组部分、保留前缀（D5）
        capture_keys = {
            "WECHAT", "MEDICAL_RECORD", "MEDICAL_INSURANCE",
            "BEARER", "ACCESS_TOKEN",
            "PHONE",          # 仅换号码段，保留 +86/0086 前缀
            "PASSWORD",       # 仅换口令值，保留字段名
            "CONN_STRING",    # 仅换连接串密码段
            "PASSPORT",       # 第二段（带"护照号"等前缀）仅换证件号码
        }
        for key in regex_order:
            pattern = self.PATTERNS[key]
            try:
                flags = re.DOTALL if key == "PRIVATE_KEY" else (
                    re.IGNORECASE if key in self._IGNORECASE_KEYS else 0
                )
                # 用快照精验上下文，避免循环内替换导致的索引偏移
                snapshot = anonymized
                matches = list(re.finditer(pattern, snapshot, flags))
                for m in matches:
                    full = m.group(0)
                    if key in capture_keys and m.lastindex and m.group(1):
                        target = m.group(1)
                    else:
                        target = full
                    # 程序化精验（含上下文精验：PHONE 位数 / BANKCARD Luhn /
                    # IDCARD15 地区码+触发词 / PLATE 负向词）
                    if not self._validate_entity(key, target, context=snapshot, pos=m.start()):
                        continue
                    if target in value_to_placeholder:
                        placeholder = value_to_placeholder[target]
                    else:
                        idx = counts.get(key, 0)
                        placeholder = f"[{key}_{idx}_{_generate_nonce(used_nonces)}]"
                        mapping[placeholder] = self._crypto.encrypt(target)
                        value_to_placeholder[target] = placeholder
                        counts[key] = idx + 1
                    # 只替换捕获组部分（capture 分支）或整个命中（非 capture 分支）
                    anonymized = anonymized.replace(target, placeholder, 1)
            except re.error:
                continue

        # === 阶段 2：人名脱敏（轻量方案） ===
        # 通过返回值重新赋值（Python 字符串不可变，无法在方法内改外部变量）
        anonymized = self._apply_name_anonymize(
            anonymized, mapping, counts, value_to_placeholder, used_nonces
        )

        self._mapping = mapping
        self._last_stats = dict(counts)
        return anonymized, mapping

    def _apply_name_anonymize(self, text, mapping, counts, value_to_placeholder, used_nonces):
        """
        人名脱敏：基于姓氏表 + 上下文触发词。
        触发词出现后，在其后 20 字符窗口内匹配"姓氏+1~2字"作为人名。
        """
        if not text:
            return text

        # 先处理用户补充的已知人名（精确匹配，优先级最高）
        for name in self._custom_names:
            if name in text and name not in value_to_placeholder:
                idx = counts.get("NAME", 0)
                placeholder = f"[NAME_{idx}_{_generate_nonce(used_nonces)}]"
                mapping[placeholder] = self._crypto.encrypt(name)
                value_to_placeholder[name] = placeholder
                counts["NAME"] = idx + 1
                text = text.replace(name, placeholder)

        # 再做触发词 + 姓氏匹配
        # 扫描文本，找到每个触发词的位置，在其后窗口内找人名
        result = text
        # 记录已识别的人名起始位置，避免重复匹配
        matched_spans = []

        for trigger in self.NAME_TRIGGERS:
            start = 0
            while True:
                pos = result.find(trigger, start)
                if pos < 0:
                    break
                # 在触发词后 20 字符窗口内扫描
                window_start = pos + len(trigger)
                window_end = min(len(result), window_start + 20)
                window = result[window_start:window_end]

                # 跳过触发词后的标点/空白
                offset = 0
                while offset < len(window) and window[offset] in " ：:，,、的\n\t":
                    offset += 1

                # 检查窗口内是否以姓氏开头
                if offset < len(window) and window[offset] in self.SURNAMES:
                    # 尝试匹配 2-3 字人名
                    name_candidate = self._extract_name(window, offset)
                    if name_candidate:
                        abs_start = window_start + offset
                        abs_end = abs_start + len(name_candidate)
                        # 检查是否与已匹配区间重叠
                        if not any(s <= abs_start < e or s < abs_end <= e for s, e in matched_spans):
                            if name_candidate not in value_to_placeholder:
                                idx = counts.get("NAME", 0)
                                placeholder = f"[NAME_{idx}_{_generate_nonce(used_nonces)}]"
                                mapping[placeholder] = self._crypto.encrypt(name_candidate)
                                value_to_placeholder[name_candidate] = placeholder
                                counts["NAME"] = idx + 1
                            else:
                                placeholder = value_to_placeholder[name_candidate]
                            # 执行替换
                            result = result[:abs_start] + placeholder + result[abs_end:]
                            # 调整后续匹配位置（替换后长度变化）
                            matched_spans.append((abs_start, abs_start + len(placeholder)))
                start = pos + 1

        return result

    def _extract_name(self, window: str, offset: int):
        """
        从 window[offset] 开始尝试提取人名。
        规则：姓氏 + 1~2 个汉字，但遇到标点/数字/字母停止。
        返回人名字符串或 None。
        """
        if offset >= len(window):
            return None
        # 收集姓氏后的连续汉字
        chars = []
        i = offset
        while i < len(window) and len(chars) < 3:
            ch = window[i]
            if '\u4e00' <= ch <= '\u9fa5':
                chars.append(ch)
                i += 1
            else:
                break
        if len(chars) < 2 or len(chars) > 3:
            return None
        # 过滤明显不是人名的组合（如"李明签约"中的"李明签" -> 应只取"李明"）
        # 只收录虚词和办公场景高频动词，避免误伤含这些字的合法人名
        stop_chars = {
            # 虚词
            "的", "是", "了", "和", "与", "在", "有", "为", "及", "或",
            "着", "过", "吗", "呢", "吧", "啊", "也", "都",
            # 合同/办公高频动作动词（避免"李明签""张伟约"等误识别）
            "签", "约", "见", "审", "批",
            "到", "去", "来", "说", "做", "写", "看", "听", "给", "请",
            "让", "被", "把", "对", "跟", "由", "向", "从", "往",
        }
        while chars and chars[-1] in stop_chars:
            chars.pop()
        if len(chars) < 2:
            return None
        return "".join(chars)

    # ──────────────────────────────────────────
    # 逆向还原（接口完全保持兼容）
    # ──────────────────────────────────────────
    def deanonymize(self, text: str, mapping: dict, strict: bool = False) -> str:
        """
        【逆向还原】
        将回复中的占位符替换回真实敏感数据。
        接口与原版完全一致。

        内部实现：mapping 中的值可能是加密密文（新版）或明文（旧版兼容）。
        对每个值尝试 decrypt 还原；解密失败则回退为原值，保证已存盘的旧数据不受影响。
        strict=True 时，decrypt 失败的占位符直接跳过，不回退明文（生产环境推荐）。
        strict=False 时，decrypt 失败回退为原值（兼容旧版知识库数据）。

        新旧格式兼容：
        - 新格式（[TYPE_N_nonce]，含 nonce）：value 必为 Fernet 密文，
          解密失败时无论 strict 与否都保留占位符、不写入密文垃圾（防注入/防误还原）。
        - 旧格式（[TYPE_N]，无 nonce）：解密失败时按 strict 语义处理，
          strict=False 回退为原值（兼容旧版明文 mapping），strict=True 跳过保留占位符。
        """
        if not text or not mapping:
            return text
        restored = text
        for placeholder, encrypted_value in mapping.items():
            try:
                original_value = self._crypto.decrypt(encrypted_value)
            except Exception:
                if strict:
                    continue
                # 新格式（含 nonce）占位符：value 必为 Fernet 密文，
                # 解密失败时保留占位符、不写回密文垃圾（默认模式同样如此）
                if _NEW_PLACEHOLDER_RE.match(placeholder):
                    continue
                # 旧格式（[TYPE_N] 无 nonce）兼容旧版明文 mapping
                original_value = encrypted_value
            restored = restored.replace(placeholder, original_value)
        return restored

    # ──────────────────────────────────────────
    # 新增：映射表用后即焚
    # ──────────────────────────────────────────
    def destroy(self):
        """主动清空引擎内部状态。"""
        self._mapping.clear()
        self._last_stats.clear()
        # 注意：anonymize 返回的 mapping 是独立 dict，调用方应自行处理
        # 这里只清引擎内部状态

    @staticmethod
    def secure_clear_mapping(mapping: dict):
        """清空传入的映射表字典（供调用方清理持有的 mapping 引用）。"""
        if isinstance(mapping, dict):
            mapping.clear()

    # ──────────────────────────────────────────
    # 新增：审计统计
    # ──────────────────────────────────────────
    def get_last_stats(self) -> dict:
        """返回上次 anonymize 的脱敏类型计数，如 {"PHONE": 1, "NAME": 2}。"""
        return dict(self._last_stats)

    # ──────────────────────────────────────────
    # 新增：数据分级统计
    # ──────────────────────────────────────────
    def get_last_classification(self) -> dict:
        """
        返回上次 anonymize 的分级统计。
        格式：{"绝密": {"IDCARD": 1, "BANKCARD": 1}, "机密": {"PHONE": 2}, "内部": {...}}
        没有命中的级别不出现。
        """
        result = {}
        for entity_type, count in self._last_stats.items():
            level = self.DATA_CLASSIFICATION.get(entity_type, "内部")
            result.setdefault(level, {})[entity_type] = count
        return result

    # ──────────────────────────────────────────
    # 新增：风险评分（加权求和）
    # ──────────────────────────────────────────
    def get_last_risk_score(self) -> int:
        """
        返回上次 anonymize 的风险评分（加权求和）。
        用于上层（审计日志/看板）量化本轮数据敏感程度。
        """
        score = 0
        for level, entities in self.get_last_classification().items():
            weight = self.CLASSIFICATION_WEIGHT.get(level, 0)
            score += weight * sum(entities.values())
        return score

    # ──────────────────────────────────────────
    # 新增：业务分组统计
    # ──────────────────────────────────────────
    def get_last_group_stats(self) -> dict:
        """
        返回上次 anonymize 的业务分组统计。
        格式：{"PII": {"PHONE": 1, "NAME": 2}, "财务": {"BANKCARD": 1}}
        没有命中的组不出现。
        用于审计日志按业务维度统计（"本轮检测到 3 条 PII、2 条财务数据"）。
        """
        result = {}
        for entity_type, count in self._last_stats.items():
            group = self.ENTITY_TO_GROUP.get(entity_type)
            if not group:
                # 新增实体未归类时告警（防止后续开发漏配分组）
                continue
            result.setdefault(group, {})[entity_type] = count
        return result

    # ──────────────────────────────────────────
    # 新增：补充已知人名（供上层导入通讯录等）
    # ──────────────────────────────────────────
    def add_custom_names(self, names):
        """补充已知人名列表，后续 anonymize 会优先精确匹配这些人名。"""
        if isinstance(names, str):
            names = [names]
        for n in names:
            if n and isinstance(n, str) and 2 <= len(n) <= 4:
                self._custom_names.add(n)


# ──────────────────────────────────────────
# 本地独立测试代码（保持原版可运行特性）
# ──────────────────────────────────────────
if __name__ == "__main__":
    engine = PrivacyEngine()

    print("=" * 60)
    print("【强化版 PrivacyEngine 测试】")
    print("=" * 60)

    raw_text = (
        "员工老王的邮箱是 test@qq.com，报销手机号是13988889999。"
        "甲方张伟签约金额85万元，乙方账号6228480402564890018，"
        "地址北京市海淀区中关村大街1号，对接微信zhangwei_88。"
        "理赔材料：病历号 MRN12345678，医保卡号 1234567890123456789。"
        "服务配置：API密钥 sk-abcdefghijklmnopqrstuvwxyz，"
        "JWT令牌 eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c，"
        "私钥块：\n-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAxyz123fakekey\n-----END RSA PRIVATE KEY-----，"
        "接口鉴权：Authorization: Bearer abcdefghij1234567890ABCDEFGHIJ1234567890，"
        "OAuth回调：access_token=ya29.a0AfH6SMBxyz123fakeaccess_token_value987，"
        "部署日志：加载密钥 C:\\Users\\admin\\.ssh\\id_rsa 成功，配置文件 /etc/app/.env 已读取。"
    )
    print("\n【1. 原始输入】:", raw_text)

    safe_text, secret_box = engine.anonymize(raw_text)
    print("\n【2. 脱敏后（发给AI的文本）】:", safe_text)
    # secret_box 的 value 现在是 base64 密文（以 gAAAAA 开头）
    print("\n【3. 本地密文保险箱（已加密）】:")
    for placeholder, cipher in secret_box.items():
        print(f"    {placeholder} -> {cipher}")
    print("\n【4. 脱敏统计】:", engine.get_last_stats())
    print("\n【4.1 数据分级】:", engine.get_last_classification())
    print("\n【4.2 风险评分】:", engine.get_last_risk_score())
    print("\n【4.3 业务分组统计】:", engine.get_last_group_stats())

    # 验证：即使 mapping 里是密文，deanonymize 也能正确还原
    name_placeholder = next(
        (p for p in secret_box if p.startswith("[NAME_")), None
    )
    name_display = engine.deanonymize(name_placeholder, secret_box) if name_placeholder else "(无人名)"
    ai_reply = f"已收到，{name_display}的合同金额已记录。"
    restored = engine.deanonymize(ai_reply, secret_box)
    print("\n【5. 最终还原（展示给用户的文本）】:", restored)

    # 6. 兼容性验证：用旧版明文 mapping 喂给 deanonymize，应同样能还原
    legacy_mapping = {"[PHONE_LEGACY_0]": "13800000000"}
    legacy_text = "电话是[PHONE_LEGACY_0]，请回拨。"
    legacy_restored = engine.deanonymize(legacy_text, legacy_mapping)
    print("\n【6. 旧版明文 mapping 兼容验证】:", legacy_restored)
    assert legacy_restored == "电话是13800000000，请回拨。", "旧版明文 mapping 兼容失败！"
    print("    ✓ 旧版明文 mapping 兼容通过（decrypt 失败回退为原值）")

    # 7. strict 模式验证：解密失败时跳过，不回退明文
    strict_mapping = {"[PHONE_STRICT_0]": "13800000000"}  # 明文，非密文
    strict_text = "电话是[PHONE_STRICT_0]，请回拨。"
    strict_restored = engine.deanonymize(strict_text, strict_mapping, strict=True)
    print("\n【6.1 strict 模式验证（解密失败应跳过）】:", strict_restored)
    assert strict_restored == "电话是[PHONE_STRICT_0]，请回拨。", "strict 模式未跳过解密失败项！"
    print("    ✓ strict 模式通过（占位符保留，未回退明文）")

    # 8. nonce 防劫持验证：原文含字面量占位符（与映射同格式）时不应被误还原
    print("\n【8. nonce 防劫持验证（原文含字面量占位符）】")
    nonce_text = "我的手机是13812345678，占位符样例[PHONE_0_a3f9]"
    anon_nonce, nonce_box = engine.anonymize(nonce_text)
    print("   脱敏后:", anon_nonce)
    assert "13812345678" not in anon_nonce, "真实手机号应被替换！"
    assert "[PHONE_0_a3f9]" in anon_nonce, "原文字面量占位符应保持不变！"
    ph_key = next(p for p in nonce_box if p.startswith("[PHONE_"))
    assert len(ph_key.split("_")) == 3, f"应为含 nonce 的新格式占位符: {ph_key}"
    restored_nonce = engine.deanonymize(anon_nonce, nonce_box)
    print("   还原后:", restored_nonce)
    assert restored_nonce == nonce_text, "还原后应与原文一致！"
    print("   ✓ 字面量占位符未被误还原（nonce 已将碰撞概率降为不可行）")

    # 9. 同批 nonce 去重验证
    print("\n【9. 同批 nonce 去重验证】")
    multi_text = "手机号13812345678，备用13987654321，工作13711112222"
    anon_multi, multi_box = engine.anonymize(multi_text)
    ph_list = [p for p in multi_box if p.startswith("[PHONE_")]
    nonces = [p[:-1].rsplit("_", 1)[1] for p in ph_list]
    print(f"   同一批 {len(ph_list)} 个 PHONE 占位符 nonce: {nonces}")
    assert len(nonces) == len(set(nonces)), "同批 nonce 不应重复！"
    print("   ✓ 同批 nonce 去重通过")

    # 10. 新格式占位符解密失败：不写入密文垃圾
    print("\n【10. 新格式占位符解密失败防护】")
    fail_text = "电话是[PHONE_0_a3f9]，请回拨。"
    fail_mapping = {"[PHONE_0_a3f9]": "not-a-valid-ciphertext"}
    fail_restored = engine.deanonymize(fail_text, fail_mapping)
    assert fail_restored == fail_text, "新格式解密失败不应写回密文！"
    fail_strict = engine.deanonymize(fail_text, fail_mapping, strict=True)
    assert fail_strict == fail_text, "strict 模式应保留占位符！"
    print("   ✓ 新格式解密失败时保留占位符，不写入密文垃圾（默认与 strict 一致）")

    engine.destroy()
    print("\n【7. 映射表已销毁】")
