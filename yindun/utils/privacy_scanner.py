# -*- coding: utf-8 -*-
"""
隐盾隐私扫描器（位置化版）

复用 PrivacyEngine.PATTERNS 正则规则，对解析后的文本做位置化敏感数据扫描。
与 PrivacyEngine 的区别：
- PrivacyEngine.anonymize：脱敏 + 加密映射表（用于发给 LLM）
- PrivacyScanner.scan：只检测 + 输出位置报告（用于风险报告展示）

位置信息由调用方提供（页码/段落号/工作表名/行号），扫描器不关心文本来源。
"""
import re
from yindun.core.privacy_engine import PrivacyEngine


class PrivacyScanResult:
    """单条敏感数据命中记录"""
    __slots__ = ("entity_type", "group", "classification", "page", "line", "snippet", "matched_text", "source_type")

    def __init__(self, entity_type, group, classification, page, line, snippet, matched_text, source_type: str = "page"):
        self.entity_type = entity_type      # 实体类型：PHONE/EMAIL/IDCARD/...
        self.group = group                  # 业务分组：PII/PHI/财务/密钥/Token/路径
        self.classification = classification  # 风险分级：绝密/机密/内部
        self.page = page                    # 页码（PDF）/ 段落号（Word）/ 工作表名（Excel）
        self.line = line                    # 行号（页内/段落内）
        self.snippet = snippet              # 命中上下文片段（前后各 20 字）
        self.matched_text = matched_text    # 命中的敏感数据原文（用于报告展示，不脱敏）
        self.source_type = source_type      # 来源类型：page/paragraph/sheet/table，用于报告措辞适配

    def to_dict(self) -> dict:
        return {
            "entity_type": self.entity_type,
            "group": self.group,
            "classification": self.classification,
            "page": self.page,
            "line": self.line,
            "snippet": self.snippet,
            "matched_text": self.matched_text,
            "source_type": self.source_type,
        }


class PrivacyScanner:
    """位置化隐私扫描器（只读检测，不脱敏、不加密）"""

    # 扫描顺序（与 PrivacyEngine.anonymize 的 regex_order 保持一致）
    # 关键：IDCARD/BANKCARD 必须在 PHONE 之前，避免身份证/银行卡的数字子串被误识别为手机号
    _SCAN_ORDER = [
        "IDCARD", "BANKCARD",
        "MEDICAL_RECORD", "MEDICAL_INSURANCE",
        "PHONE", "EMAIL",
        "IP", "MONEY", "WECHAT", "ADDRESS",
        "APIKEY", "JWT",          # PRIVATE_KEY 单独走 DOTALL，不在此列表
        "BEARER", "ACCESS_TOKEN",
        "FILE_PATH",
    ]

    def __init__(self):
        # 复用 PrivacyEngine 的规则表和分类信息
        self._patterns = PrivacyEngine.PATTERNS
        self._groups = PrivacyEngine.ENTITY_TO_GROUP
        self._classification = PrivacyEngine.DATA_CLASSIFICATION
        self._ignorecase_keys = PrivacyEngine._IGNORECASE_KEYS

    def scan(self, text: str, page: str = "1", line_start: int = 1, source_type: str = "page") -> list:
        """
        扫描一段文本，返回命中的敏感数据列表。

        参数：
            text: 待扫描文本（通常是某一页/某一段落的内容）
            page: 位置标识（页码/段落号/工作表名，由调用方提供）
            line_start: 该段文本在原文中的起始行号（用于精确定位）
            source_type: 来源类型，用于报告措辞适配
                - "page": PDF 页码，报告显示"第X页"
                - "paragraph": Word 段落，报告显示"第X段"
                - "sheet": Excel 工作表，报告显示"[工作表名] 第X行"
                - "table": Word/PDF 表格，报告显示"[表格N]"

        返回：list[PrivacyScanResult]
        """
        if not text:
            return []

        results = []
        lines = text.split("\n")

        # 按行扫描，记录行号
        for line_idx, line in enumerate(lines):
            current_line = line_start + line_idx
            # 本行已命中区间，避免同一子串被多条规则重复命中
            # （如身份证号后 11 位被 PHONE 误命中）
            occupied = []
            # 按 _SCAN_ORDER 顺序遍历正则规则
            for entity_type in self._SCAN_ORDER:
                pattern = self._patterns[entity_type]
                try:
                    flags = re.IGNORECASE if entity_type in self._ignorecase_keys else 0

                    # 带捕获组的实体：取捕获组内容作为 matched_text
                    if entity_type in ("WECHAT", "MEDICAL_RECORD", "MEDICAL_INSURANCE", "BEARER", "ACCESS_TOKEN"):
                        for m in re.finditer(pattern, line, flags):
                            if self._overlaps(occupied, m.start(), m.end()):
                                continue
                            occupied.append((m.start(), m.end()))
                            matched = m.group(1) if m.lastindex and m.group(1) else m.group(0)
                            snippet = self._build_snippet(
                                line, m.start(), m.end(),
                                entity_type=entity_type,
                                masked_text=self._mask_sensitive(entity_type, matched),
                            )
                            results.append(PrivacyScanResult(
                                entity_type=entity_type,
                                group=self._groups.get(entity_type, "未分组"),
                                classification=self._classification.get(entity_type, "内部"),
                                page=page,
                                line=current_line,
                                snippet=snippet,
                                matched_text=self._mask_sensitive(entity_type, matched),
                                source_type=source_type,
                            ))
                    else:
                        # 无捕获组实体：整段匹配
                        for m in re.finditer(pattern, line, flags):
                            matched = m.group(0)
                            # (P1 加固) 复用引擎的命中后精验（PHONE 位数 / BANKCARD Luhn / IDCARD 可选校验位）
                            if not self._is_valid(entity_type, matched):
                                continue
                            if self._overlaps(occupied, m.start(), m.end()):
                                continue
                            occupied.append((m.start(), m.end()))
                            snippet = self._build_snippet(
                                line, m.start(), m.end(),
                                entity_type=entity_type,
                                masked_text=self._mask_sensitive(entity_type, matched),
                            )
                            results.append(PrivacyScanResult(
                                entity_type=entity_type,
                                group=self._groups.get(entity_type, "未分组"),
                                classification=self._classification.get(entity_type, "内部"),
                                page=page,
                                line=current_line,
                                snippet=snippet,
                                matched_text=self._mask_sensitive(entity_type, matched),
                                source_type=source_type,
                            ))
                except re.error:
                    continue

        # PRIVATE_KEY 跨行扫描（单独处理，用 DOTALL）
        self._scan_private_key(text, page, line_start, results, source_type=source_type)

        return results

    def _scan_private_key(self, text, page, line_start, results, source_type: str = "page"):
        """PRIVATE_KEY 跨行匹配，单独处理"""
        pattern = self._patterns["PRIVATE_KEY"]
        for m in re.finditer(pattern, text, re.DOTALL):
            # 计算起始行号
            start_line = line_start + text[:m.start()].count("\n")
            end_line = line_start + text[:m.end()].count("\n")
            # 跨行 snippet 取首行 + 省略号；单行内联密钥不能暴露密钥体，一律掩码
            first_line = text[m.start():m.end()].split("\n")[0]
            masked_line = self._mask_sensitive("PRIVATE_KEY", first_line)
            snippet = masked_line[:40] + "...（跨多行）" if len(masked_line) > 40 else masked_line + "...（跨多行）"
            results.append(PrivacyScanResult(
                entity_type="PRIVATE_KEY",
                group=self._groups.get("PRIVATE_KEY", "密钥"),
                classification=self._classification.get("PRIVATE_KEY", "绝密"),
                page=page,
                line=start_line,  # 记起始行
                snippet=snippet,
                matched_text="[PEM 私钥块]",  # 不展示完整密钥内容，避免报告本身泄露
                source_type=source_type,
            ))

    @staticmethod
    def _overlaps(occupied, start, end) -> bool:
        """检查 [start, end) 是否与已命中区间重叠"""
        for s, e in occupied:
            if s < end and start < e:
                return True
        return False

    @staticmethod
    def _is_valid(entity_type: str, text: str) -> bool:
        """复用 PrivacyEngine 的命中后精验（PHONE 位数 / BANKCARD Luhn / IDCARD 可选校验位）。"""
        if entity_type == "PHONE":
            return PrivacyEngine._is_valid_phone(text)
        if entity_type == "BANKCARD":
            return PrivacyEngine._luhn_valid(text)
        if entity_type == "IDCARD" and PrivacyEngine.IDCARD_CHECKSUM:
            return PrivacyEngine._idcard_valid(text)
        return True

    def _build_snippet(self, line, start, end, entity_type=None, masked_text=None, context=20) -> str:
        """构建命中上下文片段（前后各 context 字符）。

        命中段以掩码替换；随后对整段再做一次兜底掩码，确保上下文中的其他
        敏感值（手机号/邮箱/密钥等）也不以明文形式进入报告。
        """
        snippet_start = max(0, start - context)
        snippet_end = min(len(line), end + context)
        prefix = "..." if snippet_start > 0 else ""
        suffix = "..." if snippet_end < len(line) else ""
        before = line[snippet_start:start]
        after = line[end:snippet_end]
        masked = masked_text if masked_text is not None else line[start:end]
        combined = prefix + before + masked + after + suffix
        return self._mask_remaining_sensitive(combined)

    def _mask_remaining_sensitive(self, text: str) -> str:
        """对片段中剩余可识别的敏感值做掩码（命中段已掩码，不会二次匹配）"""
        for entity_type in self._SCAN_ORDER + ["PRIVATE_KEY"]:
            pattern = self._patterns[entity_type]
            try:
                flags = re.IGNORECASE if entity_type in self._ignorecase_keys else 0
                if entity_type == "PRIVATE_KEY":
                    flags |= re.DOTALL
                compiled = re.compile(pattern, flags)
                text = compiled.sub(
                    lambda m: self._mask_sensitive(entity_type, m.group(0))
                    if self._is_valid(entity_type, m.group(0))
                    else m.group(0),
                    text,
                )
            except re.error:
                continue
        return text

    @staticmethod
    def _mask_fixed(text: str, head: int, tail: int) -> str:
        """保留前 head 后 tail 字符，中段以 * 掩码"""
        if len(text) <= head + tail:
            return text[:head] + "***"
        return text[:head] + "*" * (len(text) - head - tail) + text[-tail:]

    @staticmethod
    def _mask_sensitive(entity_type: str, text: str) -> str:
        """对命中实体做掩码处理，避免报告/导出 JSON 泄露敏感明文

        覆盖全部扫描实体类型：手机号/邮箱/密钥/证件/地址等一律以掩码形式呈现，
        任一报告片段不含完整敏感值。
        """
        if not text:
            return text
        # 私钥：整块占位，不展示任何片段
        if entity_type == "PRIVATE_KEY":
            return "[PEM 私钥块]"
        # JWT 三段式：只保留首段前 5 字符
        if entity_type == "JWT":
            parts = text.split(".")
            if len(parts) == 3:
                return parts[0][:5] + "***.***.***"
            return text[:5] + "***"
        # 身份证/银行卡：保留前4后4（已有逻辑）
        if entity_type in ("IDCARD", "BANKCARD"):
            return PrivacyScanner._mask_fixed(text, 4, 4)
        # 手机号：保留前3后4（138****1234）；先规范化（去分隔符/全角转半角）再掩码，
        # 保证 139-8888-9999 / １３８... 也能按 前3后4 格式脱敏
        if entity_type == "PHONE":
            normalized = PrivacyEngine._normalize_digits(text)
            return PrivacyScanner._mask_fixed(normalized, 3, 4)
        # 邮箱：保留首字符 + 域名（a***@example.com）
        if entity_type == "EMAIL":
            if "@" in text:
                local, domain = text.split("@", 1)
                head = local[0] if local else "x"
                return f"{head}***@{domain}"
            return PrivacyScanner._mask_fixed(text, 2, 2)
        # API 密钥 / Token：保留前4后4
        if entity_type in ("APIKEY", "BEARER", "ACCESS_TOKEN"):
            return PrivacyScanner._mask_fixed(text, 4, 4)
        # 其余类型（地址/姓名/医疗号/IP/金额/微信/路径等）及未知兜底：保留首尾各2
        return PrivacyScanner._mask_fixed(text, 2, 2)

    def generate_report(self, results: list) -> str:
        """
        生成人类可读的隐私风险报告（Markdown 格式）。
        用于 GUI 展示和审计日志归档。
        """
        if not results:
            return "【隐私风险报告】未检测到敏感数据 ✓"

        # 按业务分组归类
        by_group = {}
        for r in results:
            by_group.setdefault(r.group, []).append(r)

        # 按风险分级排序（绝密 > 机密 > 内部）
        level_order = {"绝密": 0, "机密": 1, "内部": 2, "公开": 3}
        by_group = dict(sorted(by_group.items(),
                               key=lambda x: min(level_order.get(r.classification, 9) for r in x[1])))

        lines = []
        lines.append("【隐私风险报告】")
        lines.append(f"共检测到 {len(results)} 处敏感数据，分布于 {len(by_group)} 个业务分组")
        lines.append("")

        # 摘要统计
        lines.append("## 风险摘要")
        level_count = {}
        for r in results:
            level_count[r.classification] = level_count.get(r.classification, 0) + 1
        for level in ["绝密", "机密", "内部", "公开"]:
            if level in level_count:
                lines.append(f"- {level}：{level_count[level]} 处")
        lines.append("")

        # 按分组详列
        for group, items in by_group.items():
            lines.append(f"## {group} 组（{len(items)} 处）")
            for r in items:
                # 按 source_type 选择位置措辞
                if r.source_type == "page":
                    loc_text = f"第{r.page}页 第{r.line}行"
                elif r.source_type == "paragraph":
                    loc_text = f"第{r.page}段 第{r.line}行"
                elif r.source_type == "sheet":
                    loc_text = f"[{r.page}] 第{r.line}行"
                elif r.source_type == "table":
                    loc_text = f"[{r.page}] 第{r.line}行"
                else:
                    loc_text = f"{r.page} 第{r.line}行"
                lines.append(f"- [{r.classification}] {loc_text} | {r.entity_type}")
                lines.append(f"  内容：{r.snippet}")
            lines.append("")

        lines.append("---")
        lines.append("建议：对涉及绝密/机密数据的文档，发送给 AI 前请确认已启用脱敏引擎。")
        return "\n".join(lines)


if __name__ == "__main__":
    # 独立测试
    scanner = PrivacyScanner()
    test_text = (
        "员工老王邮箱 test@qq.com，手机 13988889999。\n"
        "签约金额 85万元，银行卡 6228480402564890018。\n"
        "身份证号 110101199003071234，IP 192.168.1.100。"
    )
    results = scanner.scan(test_text, page="1", line_start=1)
    print(scanner.generate_report(results))
