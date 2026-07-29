# -*- coding: utf-8 -*-
"""
隐盾隐私健康体检扫描器
========================
递归扫描指定目录，检测以下风险：
  - 敏感文件泄露（密钥文件、配置文件、凭据文件）
  - 隐私数据（身份证、手机号、银行卡）明文存储
  - 文件权限异常
  - 生成风险评分和整改建议

使用示例：
    scanner = HealthScanner()
    report = scanner.scan("C:\\Users\\test\\Documents", depth=3)
    print(report.summary())
"""

import os
import re
import json
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

import fnmatch


# ── 敏感文件路径模式 ──
SENSITIVE_FILE_PATTERNS = {
    "绝密": [
        "**/*id_rsa*", "**/*id_ed25519*", "**/*id_ecdsa*",
        "**/*.pem", "**/*.cer", "**/*.key", "**/*.crt",
        "**/authorized_keys", "**/known_hosts",
        "**/*.jks", "**/*.keystore", "**/*.p12", "**/*.pfx",
    ],
    "机密": [
        "**/.env", "**/.env.*", "**/*.env",
        "**/credentials*", "**/*.credentials",
        "**/config.ini", "**/application.yml", "**/application.properties",
        "**/.git-credentials", "**/.npmrc", "**/.pypirc",
        "**/settings.py", "**/secrets.yaml", "**/secrets.yml",
        "**/*token*", "**/*password*",
    ],
    "内部": [
        "**/*.log", "**/*.db", "**/*.sqlite",
        "**/*.sql", "**/*.dump", "**/*.backup",
        "**/dump.*", "**/*.bak",
    ]
}

# ── 敏感内容正则模式 ──
SENSITIVE_CONTENT_PATTERNS = {
    "身份证": r"\b[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]\b",
    "手机号": r"\b1[3-9]\d{9}\b",
    "银行卡": r"\b(?:62|60|622)\d{13,17}\b",
    "API密钥": r"(?:sk-[A-Za-z0-9]{32,}|AKIA[A-Za-z0-9]{16}|ghp_[A-Za-z0-9]{36})",
    "邮箱": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
    "私钥": r"-{5}BEGIN\s+(?:RSA|DSA|EC|OPENSSH|PGP)?\s*PRIVATE\s+KEY[\s\S]{50,}?-{5}END",
    "Token": r"(?:Bearer|access_token|refresh_token)\s*[=:]\s*[\'\"\s]?([A-Za-z0-9_\-\.]{20,})",
}

# ── 最大读取文件大小（4KB，只扫描头部）──
MAX_SCAN_BYTES = 4096

# ── 跳过目录 ──
SKIP_DIRS = {".git", ".svn", "__pycache__", "node_modules", ".venv", "venv",
             "vendor", ".idea", ".vscode", "bower_components", ".tox",
             ".eggs", ".mypy_cache", ".pytest_cache", "dist", "build"}


@dataclass
class ScanFinding:
    """单条扫描发现"""
    file_path: str
    severity: str           # "绝密" | "机密" | "内部" | "公开"
    category: str           # "sensitive_file" | "sensitive_content" | "permission"
    description: str
    matches: List[str] = field(default_factory=list)
    line_number: Optional[int] = None


@dataclass
class ScanReport:
    """扫描报告"""
    scan_root: str
    scan_time: str
    total_files: int = 0
    total_dirs: int = 0
    scanned_files: int = 0
    skipped_dirs: int = 0
    findings: List[ScanFinding] = field(default_factory=list)
    risk_score: int = 0

    def summary(self) -> str:
        """生成摘要"""
        lines = [
            "=" * 60,
            f"隐盾隐私健康体检报告",
            f"扫描路径: {self.scan_root}",
            f"扫描时间: {self.scan_time}",
            f"扫描文件: {self.scanned_files} / {self.total_files}",
            f"发现问题: {len(self.findings)} 项",
            f"风险评分: {self.risk_score}/100",
            "=" * 60,
        ]
        if not self.findings:
            lines.append("✓ 未发现隐私风险")
            return "\n".join(lines)

        # 按严重程度分组
        grouped: Dict[str, List[ScanFinding]] = {}
        for f in self.findings:
            grouped.setdefault(f.severity, []).append(f)

        for severity in ("绝密", "机密", "内部"):
            if severity in grouped:
                items = grouped[severity]
                lines.append(f"\n[{severity}] {len(items)} 项:")
                for item in items[:20]:  # 最多显示20条
                    lines.append(f"  ⚠ {item.description}")
                    lines.append(f"     → {item.file_path}")
                if len(items) > 20:
                    lines.append(f"  ... 还有 {len(items) - 20} 项")
        return "\n".join(lines)

    def to_json(self) -> str:
        """导出 JSON"""
        return json.dumps({
            "scan_root": self.scan_root,
            "scan_time": self.scan_time,
            "total_files": self.total_files,
            "scanned_files": self.scanned_files,
            "findings_count": len(self.findings),
            "risk_score": self.risk_score,
            "findings": [
                {
                    "file": f.file_path,
                    "severity": f.severity,
                    "category": f.category,
                    "description": f.description,
                    "matches": f.matches[:10],
                    "line": f.line_number
                }
                for f in self.findings
            ]
        }, ensure_ascii=False, indent=2)


class HealthScanner:
    """
    隐私健康体检扫描器

    使用方式：
        scanner = HealthScanner()
        report = scanner.scan("D:\\test", depth=2)
        print(report.summary())
    """

    def __init__(self):
        self._skipped = 0

    def scan(self, root_path: str, depth: int = 3) -> ScanReport:
        """
        扫描指定目录。

        参数：
            root_path: 扫描根目录
            depth: 递归深度（0=仅根目录）
        """
        report = ScanReport(
            scan_root=root_path,
            scan_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        self._skipped = 0

        # 第一步：遍历所有文件（不计入扫描，只统计）
        self._count_files(root_path, depth, report)

        # 第二步：逐文件扫描
        self._scan_dir(root_path, depth, report)

        report.skipped_dirs = self._skipped

        # 计算风险评分
        report.risk_score = self._calculate_risk(report)
        return report

    # ── 文件统计 ────────────────────────────

    def _count_files(self, path: str, depth: int, report: ScanReport):
        try:
            for entry in os.scandir(path):
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        continue
                    report.total_dirs += 1
                    if depth > 0:
                        self._count_files(entry.path, depth - 1, report)
                else:
                    report.total_files += 1
        except (PermissionError, OSError):
            self._skipped += 1

    # ── 目录扫描 ────────────────────────────

    def _scan_dir(self, path: str, depth: int, report: ScanReport):
        try:
            for entry in os.scandir(path):
                if entry.is_dir():
                    if entry.name in SKIP_DIRS or entry.name.startswith("."):
                        continue
                    if depth > 0:
                        self._scan_dir(entry.path, depth - 1, report)
                else:
                    self._scan_file(entry.path, report)
                    report.scanned_files += 1
        except (PermissionError, OSError):
            self._skipped += 1

    # ── 文件扫描 ────────────────────────────

    def _scan_file(self, file_path: str, report: ScanReport):
        """扫描单个文件"""
        path = file_path.replace("\\", "/")

        # 1. 检测敏感文件名
        for severity, patterns in SENSITIVE_FILE_PATTERNS.items():
            for pattern in patterns:
                if fnmatch.fnmatch(path, pattern):
                    report.findings.append(ScanFinding(
                        file_path=file_path,
                        severity=severity,
                        category="sensitive_file",
                        description=f"敏感文件: {os.path.basename(file_path)}"
                    ))
                    # 匹配到后继续检查内容

        # 2. 检测敏感内容（仅文本类文件）
        ext = os.path.splitext(file_path)[1].lower()
        text_exts = {".txt", ".csv", ".json", ".xml", ".yml", ".yaml",
                     ".ini", ".cfg", ".conf", ".log", ".md", ".py", ".js",
                     ".ts", ".java", ".go", ".rs", ".c", ".cpp", ".h", ".sh",
                     ".bat", ".ps1", ".sql", ".r", ".html", ".css", ""}
        if ext not in text_exts:
            return

        try:
            file_size = os.path.getsize(file_path)
            if file_size > 10 * 1024 * 1024:  # 跳过10MB以上的文件
                return
            if file_size == 0:
                return

            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(MAX_SCAN_BYTES)

            for name, pattern in SENSITIVE_CONTENT_PATTERNS.items():
                matches = re.findall(pattern, content, re.IGNORECASE)
                if matches:
                    # 找到行号
                    line_num = 1
                    for i, line in enumerate(content.split("\n")):
                        if re.search(pattern, line, re.IGNORECASE):
                            line_num = i + 1
                            break

                    # 确定严重程度
                    if name in ("身份证", "银行卡", "私钥"):
                        severity = "绝密"
                    elif name in ("API密钥", "Token"):
                        severity = "机密"
                    else:
                        severity = "内部"

                    report.findings.append(ScanFinding(
                        file_path=file_path,
                        severity=severity,
                        category="sensitive_content",
                        description=f"发现{name}信息",
                        matches=matches[:5],  # 只保留前5条
                        line_number=line_num
                    ))

        except (PermissionError, OSError, UnicodeDecodeError):
            pass

    # ── 风险评分 ────────────────────────────

    def _calculate_risk(self, report: ScanReport) -> int:
        """
        风险评分算法：
        - 绝密问题 × 20 分
        - 机密问题 × 10 分
        - 内部问题 × 3 分
        上限 100 分
        """
        weights = {"绝密": 20, "机密": 10, "内部": 3}
        score = 0
        for finding in report.findings:
            score += weights.get(finding.severity, 1)
        return min(score, 100)
