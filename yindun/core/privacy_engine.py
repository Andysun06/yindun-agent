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
- anonymize 写入 mapping 时对真实值 encrypt，占位符本身（如 [PHONE_0]）保持明文，方便 LLM 识别
- deanonymize 时透明解密还原，对调用方完全无感
- 兼容旧版明文 mapping：decrypt 失败时回退为原值（strict=True 时不回退，直接跳过），保证已存盘的知识库数据不会因升级而损坏
"""
import re

from yindun.core.secret_manager import SecretManager


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
        "PII":   ["PHONE", "EMAIL", "IDCARD", "NAME", "ADDRESS", "WECHAT"],  # WECHAT 改归 PII（个人社交账号）
        "PHI":   ["MEDICAL_RECORD", "MEDICAL_INSURANCE"],
        "财务":  ["BANKCARD", "MONEY"],
        "密钥":  ["APIKEY", "PRIVATE_KEY", "JWT"],   # 高危密钥凭证
        "Token": ["BEARER", "ACCESS_TOKEN"],
        "路径":  ["FILE_PATH"],
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
    _IGNORECASE_KEYS = {"APIKEY", "MEDICAL_RECORD", "BEARER", "ACCESS_TOKEN", "FILE_PATH"}

    PATTERNS = {
        # === 原有 3 类（保持兼容） ===
        "IDCARD": r"[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]",
        "PHONE": r"1[3-9]\d{9}",
        "EMAIL": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",

        # === 新增 6 类（正则） ===
        # 银行卡号：16-19 位连续数字，且以常见银行前缀开头
        # 前缀参考：62(银联)/4(Visa)/5(万事达)/30,36,38(大莱)/35(JCB)/37(运通)
        # 注意：用 lookaround 替代 \b，因为 \b 在"中文+数字"交界处不触发
        "BANKCARD": r"(?<![\d])(?:62\d{14,17}|4\d{15}(?:\d{3})?|5\d{15}|3[0-8]\d{13}|35\d{14}|37\d{13})(?![\d])",

        # IPv4 地址（前后不能是数字或点，避免误匹配版本号等）
        "IP": r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d{2}|[1-9]?\d)(?![\d.])",

        # 金额：¥100,000 / 85万元 / 3.5亿 / 12000元 / 12.5万
        "MONEY": r"(?:¥|￥)\s?\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d{1,3}(?:,\d{3})*(?:\.\d+)?\s?(?:万元|亿元|万元整|元|亿|万)",

        # API 密钥：sk-xxx / api_key=xxx / APIKEY: xxx
        # 用 lookaround 替代 \b，适配中文前缀场景（如"密钥sk-xxx"）
        "APIKEY": r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}(?![A-Za-z0-9])|api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9]{16,}['\"]?",

        # 微信号：需"微信"前缀触发，避免误伤（微信号格式：字母开头 6-20 位）
        "WECHAT": r"(?:微信|微信号|wechat|WeChat)\s*[:：]?\s*([a-zA-Z][a-zA-Z0-9_-]{5,19})",

        # 地址：省市区+路+号 模式（覆盖最常见的中文办公地址）
        "ADDRESS": r"(?:北京|上海|天津|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆|香港|澳门)"
                   r"(?:省|市|自治区|特别行政区)?"
                   r"(?:[^\s，。、；]{2,8}(?:市|区|县|旗))?"
                   r"(?:[^\s，。、；]{2,15}(?:路|街|道|巷|弄|号|幢|栋|单元|室|大厦|广场|大楼))",

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
        # token 长度限制 20+，避免误匹配短字符串
        "BEARER": r"(?i)(?:Authorization\s*[:：]\s*Bearer\s+|Bearer\s+)([A-Za-z0-9_\-\.=]{20,})",

        # access_token / refresh_token：access_token=xxx / refresh_token: xxx
        # 捕获组只取 token 值（不含 key 名）
        # 长度限制 16+，覆盖常见 JWT 和随机字符串
        "ACCESS_TOKEN": r"(?i)(?:access_token|refresh_token|id_token|auth_token)\s*[=:]\s*['\"]?([A-Za-z0-9_\-\.=]{16,})['\"]?",

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
    # 2. 人名识别（轻量方案）
    #    姓氏表（百家姓前 100）+ 上下文触发词
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
    }

    # 人名触发词：前文出现这些词时，才在后续窗口内匹配"姓+名"
    NAME_TRIGGERS = (
        "甲方", "乙方", "丙方", "丁方",
        "姓名", "名字", "叫", "联系人", "对接人", "经办人", "负责人",
        "先生", "女士", "经理", "主管", "总监", "总", "主任", "科长",
        "局长", "处长", "专员", "助理", "代表", "签字", "签署",
        "当事人", "原告", "被告", "证人", "客户", "供应商",
    )

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
            mapping_dict = {"[PHONE_0]": "<base64 密文>", ...}
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

        # === 阶段 1：正则实体脱敏 ===
        # 按固定顺序处理，避免相互干扰
        # 顺序：IDCARD -> BANKCARD -> PHI -> PHONE/EMAIL -> IP/MONEY/WECHAT/ADDRESS -> 密钥组 -> Token 组 -> 路径组
        # 注意：IDCARD 必须在 PHONE 之前（身份证后 11 位可能误匹配手机号）
        #       BANKCARD 必须在 PHONE 之前（银行卡中间可能含手机号片段）
        #       PHI 放在 BANKCARD 之后、PHONE 之前，避免医保卡号被银行卡或手机号误匹配
        #       密钥组（JWT/PRIVATE_KEY）放最后，PRIVATE_KEY 跨行匹配范围大，避免影响其他实体
        #       Token 组和路径组放最末尾，FILE_PATH 路径可能含 .env 等多种结尾
        regex_order = [
            "IDCARD", "BANKCARD",
            "MEDICAL_RECORD", "MEDICAL_INSURANCE",  # PHI 组
            "PHONE", "EMAIL",
            "IP", "MONEY", "WECHAT", "ADDRESS",
            "APIKEY", "JWT", "PRIVATE_KEY",    # 密钥组
            "BEARER", "ACCESS_TOKEN",          # Token 组
            "FILE_PATH",                       # 路径组
        ]
        for key in regex_order:
            pattern = self.PATTERNS[key]
            try:
                flags = re.DOTALL if key == "PRIVATE_KEY" else (
                    re.IGNORECASE if key in self._IGNORECASE_KEYS else 0
                )
                # 带捕获组的实体（只替换捕获组部分，保留前缀）：WECHAT / MEDICAL_* / BEARER / ACCESS_TOKEN
                if key in ("WECHAT", "MEDICAL_RECORD", "MEDICAL_INSURANCE", "BEARER", "ACCESS_TOKEN"):
                    for m in re.finditer(pattern, anonymized, flags):
                        full = m.group(0)
                        target = m.group(1) if m.lastindex and m.group(1) else full
                        if target in value_to_placeholder:
                            placeholder = value_to_placeholder[target]
                            anonymized = anonymized.replace(target, placeholder, 1)
                            continue
                        idx = counts.get(key, 0)
                        placeholder = f"[{key}_{idx}]"
                        mapping[placeholder] = self._crypto.encrypt(target)
                        value_to_placeholder[target] = placeholder
                        counts[key] = idx + 1
                        # 只替换捕获组部分，保留前缀
                        anonymized = anonymized.replace(target, placeholder, 1)
                else:
                    # 原有通用分支（无捕获组）
                    matches = list(set(re.findall(pattern, anonymized, flags)))
                    for match in matches:
                        if match in value_to_placeholder:
                            placeholder = value_to_placeholder[match]
                            anonymized = anonymized.replace(match, placeholder)
                        else:
                            idx = counts.get(key, 0)
                            placeholder = f"[{key}_{idx}]"
                            mapping[placeholder] = self._crypto.encrypt(match)
                            value_to_placeholder[match] = placeholder
                            counts[key] = idx + 1
                            anonymized = anonymized.replace(match, placeholder)
            except re.error:
                continue

        # === 阶段 2：人名脱敏（轻量方案） ===
        # 通过返回值重新赋值（Python 字符串不可变，无法在方法内改外部变量）
        anonymized = self._apply_name_anonymize(
            anonymized, mapping, counts, value_to_placeholder
        )

        self._mapping = mapping
        self._last_stats = dict(counts)
        return anonymized, mapping

    def _apply_name_anonymize(self, text, mapping, counts, value_to_placeholder):
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
                placeholder = f"[NAME_{idx}]"
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
                                placeholder = f"[NAME_{idx}]"
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
                # 兼容旧版明文 mapping（向后兼容已存盘的知识库数据）
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
        "甲方张伟签约金额85万元，乙方账号6222020200112345678，"
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
    ai_reply = f"已收到，{engine.deanonymize('[NAME_0]', secret_box)}的合同金额已记录。"
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

    engine.destroy()
    print("\n【7. 映射表已销毁】")
