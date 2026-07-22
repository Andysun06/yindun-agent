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
"""
import re


class PrivacyEngine:
    """隐私脱敏引擎（强化版）

    覆盖实体类型：
        原有 3 类：PHONE / EMAIL / IDCARD
        新增 6 类（正则）：BANKCARD / IP / MONEY / APIKEY / WECHAT / ADDRESS
        新增 1 类（轻量）：NAME（姓氏表 + 上下文触发）
    """

    # ──────────────────────────────────────────
    # 1. 正则实体规则表
    #    顺序很重要：先匹配长格式/严格格式，后匹配短格式，避免相互覆盖
    # ──────────────────────────────────────────
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
                   r"(?:[^\s，。、；]{2,15}(?:路|街|道|巷|弄|号|幢|栋|单元|室|大厦|广场|大厦|大楼))",
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
        "局长", "处长", "科长", "专员", "助理", "代表", "签字", "签署",
        "当事人", "原告", "被告", "证人", "客户", "供应商",
    )

    # 人名正则：姓氏 + 1-2 个汉字（不含标点和空白）
    _NAME_RE = re.compile(r"([\u4e00-\u9fa5]{2,3})")

    # ──────────────────────────────────────────
    # 3. 实例状态
    # ──────────────────────────────────────────
    def __init__(self):
        self._custom_names: set = set()  # 用户补充的已知人名
        self._last_stats: dict = {}      # 上次 anonymize 的统计
        self._mapping: dict = {}         # 当前映射表（供 destroy 使用）

    # ──────────────────────────────────────────
    # 正向脱敏（接口完全保持兼容）
    # ──────────────────────────────────────────
    def anonymize(self, text: str):
        """
        【正向脱敏】
        将文本中的敏感实体提取出来存入映射表，并在原句中替换为占位符。

        返回格式（与原版完全一致）：
            (anonymized_text, mapping_dict)
            mapping_dict = {"[PHONE_0]": "13812345678", ...}
        """
        if not text:
            self._last_stats = {}
            return text, {}

        mapping = {}
        counts = {}
        anonymized = text
        # 用于去重：同一真实值只分配一个占位符
        value_to_placeholder = {}

        def _replace(matches_set, key):
            """对一组去重后的匹配值分配占位符并替换。"""
            for match in matches_set:
                if match in value_to_placeholder:
                    # 已分配过占位符，直接复用（保证同值同占位符）
                    placeholder = value_to_placeholder[match]
                    anonymized_local = anonymized.replace(match, placeholder)
                    return anonymized_local
                idx = counts.get(key, 0)
                placeholder = f"[{key}_{idx}]"
                mapping[placeholder] = match
                value_to_placeholder[match] = placeholder
                counts[key] = idx + 1

        # === 阶段 1：正则实体脱敏 ===
        # 按固定顺序处理，避免相互干扰
        # 顺序：IDCARD -> BANKCARD -> PHONE -> EMAIL -> IP -> APIKEY -> MONEY -> WECHAT -> ADDRESS
        # 注意：IDCARD 必须在 PHONE 之前（身份证后 11 位可能误匹配手机号）
        #       BANKCARD 必须在 PHONE 之前（银行卡中间可能含手机号片段）
        regex_order = [
            "IDCARD", "BANKCARD", "PHONE", "EMAIL",
            "IP", "APIKEY", "MONEY", "WECHAT", "ADDRESS",
        ]
        for key in regex_order:
            pattern = self.PATTERNS[key]
            try:
                flags = re.IGNORECASE if key == "APIKEY" else 0
                # WECHAT 需要捕获组（只替换微信号本身，不替换"微信:"前缀）
                if key == "WECHAT":
                    for m in re.finditer(pattern, anonymized, flags):
                        full = m.group(0)
                        wx = m.group(1) if m.lastindex and m.group(1) else full
                        if wx in value_to_placeholder:
                            placeholder = value_to_placeholder[wx]
                            anonymized = anonymized.replace(wx, placeholder, 1)
                            continue
                        idx = counts.get(key, 0)
                        placeholder = f"[{key}_{idx}]"
                        mapping[placeholder] = wx
                        value_to_placeholder[wx] = placeholder
                        counts[key] = idx + 1
                        # 只替换微信号部分，保留"微信:"前缀
                        anonymized = anonymized.replace(wx, placeholder, 1)
                else:
                    matches = list(set(re.findall(pattern, anonymized, flags)))
                    for match in matches:
                        if match in value_to_placeholder:
                            placeholder = value_to_placeholder[match]
                            anonymized = anonymized.replace(match, placeholder)
                        else:
                            idx = counts.get(key, 0)
                            placeholder = f"[{key}_{idx}]"
                            mapping[placeholder] = match
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
                mapping[placeholder] = name
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
                                mapping[placeholder] = name_candidate
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
    def deanonymize(self, text: str, mapping: dict) -> str:
        """
        【逆向还原】
        将回复中的占位符替换回真实敏感数据。
        接口与原版完全一致。
        """
        if not text or not mapping:
            return text
        restored = text
        for placeholder, original_value in mapping.items():
            restored = restored.replace(placeholder, original_value)
        return restored

    # ──────────────────────────────────────────
    # 新增：映射表用后即焚
    # ──────────────────────────────────────────
    def destroy(self):
        """主动清空当前映射表，避免敏感数据长时间驻留内存。"""
        self._mapping.clear()
        # 注意：anonymize 返回的 mapping 是独立 dict，调用方应自行处理
        # 这里只清引擎内部状态

    # ──────────────────────────────────────────
    # 新增：审计统计
    # ──────────────────────────────────────────
    def get_last_stats(self) -> dict:
        """返回上次 anonymize 的脱敏类型计数，如 {"PHONE": 1, "NAME": 2}。"""
        return dict(self._last_stats)

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
    )
    print("\n【1. 原始输入】:", raw_text)

    safe_text, secret_box = engine.anonymize(raw_text)
    print("\n【2. 脱敏后（发给AI的文本）】:", safe_text)
    print("\n【3. 本地密文保险箱】:", secret_box)
    print("\n【4. 脱敏统计】:", engine.get_last_stats())

    ai_reply = f"已收到，{engine.deanonymize('[NAME_0]', secret_box)}的合同金额已记录。"
    restored = engine.deanonymize(ai_reply, secret_box)
    print("\n【5. 最终还原（展示给用户的文本）】:", restored)

    engine.destroy()
    print("\n【6. 映射表已销毁】")
