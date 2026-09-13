from yindun import APP_ROOT
# -*- coding: utf-8 -*-
"""
隐盾权限策略管理器 — 可配置的访问控制与数据分级规则
=====================================================
支持：
  - 审批规则：路径模式 + 操作类型 → 自动通过 / 需审批 / 拒绝
  - 数据分级规则：路径模式 → 绝密/机密/内部/公开
  - 脱敏规则开关：按实体类型控制是否脱敏
  - 持久化到 JSON，支持热加载

使用示例：
    pm = PolicyManager()
    pm.check("create_local_file", "D:\\sensitive\\secret.txt")  # → "approve"/"confirm"/"deny"
    pm.classify_path("C:\\Users\\admin\\.ssh\\id_rsa")  # → "绝密"
"""

import json
import fnmatch
import os
from pathlib import Path
from typing import Literal, Optional, Dict, List


# ── 操作类型映射 ──
READ_OPERATIONS = {"read_local_file", "search_in_files", "analyze_project", "list_local_files"}
WRITE_OPERATIONS = {"create_local_file", "modify_local_file", "replace_in_file"}
DELETE_OPERATIONS = {"delete_local_file"}
EXEC_OPERATIONS = {"run_local_command"}

# 操作类型 → 风险等级
OP_RISK = {
    "read": 1,
    "write": 2,
    "delete": 3,
    "execute": 4
}

# ── 默认策略 ──
DEFAULT_APPROVAL_RULES = [
    # 敏感系统目录 → 拒绝
    {"path_pattern": "C:\\Windows\\*", "operations": "*", "policy": "deny"},
    {"path_pattern": "C:\\Windows\\System32\\*", "operations": "*", "policy": "deny"},
    {"path_pattern": "/etc/*", "operations": "*", "policy": "deny"},
    {"path_pattern": "/sys/*", "operations": "*", "policy": "deny"},
    # 敏感配置/密钥文件 → 需审批
    {"path_pattern": "**\\.ssh\\**", "operations": "*", "policy": "confirm"},
    {"path_pattern": "**\\.aws\\**", "operations": "*", "policy": "confirm"},
    {"path_pattern": "**\\.env", "operations": "*", "policy": "confirm"},
    {"path_pattern": "**\\id_rsa*", "operations": "*", "policy": "confirm"},
    {"path_pattern": "**\\credentials*", "operations": "*", "policy": "confirm"},
    # 删除/执行操作 → 需审批
    {"path_pattern": "*", "operations": "delete_local_file", "policy": "confirm"},
    {"path_pattern": "*", "operations": "run_local_command", "policy": "confirm"},
]

DEFAULT_CLASSIFICATION_RULES = [
    {"path_pattern": "**\\.ssh\\*", "level": "绝密"},
    {"path_pattern": "**\\.aws\\*", "level": "绝密"},
    {"path_pattern": "**\\id_rsa*", "level": "绝密"},
    {"path_pattern": "**\\.env", "level": "机密"},
    {"path_pattern": "**\\config\\**", "level": "机密"},
    {"path_pattern": "**\\credentials*", "level": "机密"},
    {"path_pattern": "**\\Documents\\**", "level": "内部"},
    {"path_pattern": "**\\Desktop\\**", "level": "内部"},
    {"path_pattern": "**\\Downloads\\**", "level": "公开"},
]

DEFAULT_ANONYMIZE_RULES = {
    "PHONE": True,
    "EMAIL": True,
    "IDCARD": True,
    "BANKCARD": True,
    "APIKEY": True,
    "PRIVATE_KEY": True,
    "JWT": True,
    "BEARER": True,
    "ACCESS_TOKEN": True,
    "IP": True,
    "ADDRESS": True,
    "WECHAT": True,
    "NAME": True,
    "MONEY": False,
    "MEDICAL_RECORD": True,
    "MEDICAL_INSURANCE": True,
    "FILE_PATH": False,
}

# 用户自定义库（用户额外添加的敏感路径模式）
DEFAULT_CUSTOM_SENSITIVE_PATHS = []

# ── 免审批白名单（fail-closed 下仅以下"只读"工具在沙箱内才自动放行）──
# 其余所有未命中规则的调用一律退回人工审批（confirm），不再默认 approve。
APPROVE_READ_WHITELIST = {
    "list_local_files",
    "read_local_file",
    "search_in_files",
    "analyze_project",
}

# 沙箱根目录：优先读环境变量 SANDBOX_PATH，否则回退到项目根目录
SANDBOX_ROOT = os.path.abspath(
    os.environ.get(
        "SANDBOX_PATH",
        str(APP_ROOT),
    )
)


class PolicyManager:
    """
    权限策略管理器（单例）

    策略优先级：自定义规则 > 默认规则 > 安全回退
    """

    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, config_dir: str = None):
        if hasattr(self, "_initialized") and self._initialized:
            return
        self._config_path = Path(config_dir) if config_dir else (
            APP_ROOT / "config"
        )
        self._config_path.mkdir(exist_ok=True)

        # 内部状态
        self._approval_rules: List[dict] = []
        self._classification_rules: List[dict] = []
        self._anonymize_rules: Dict[str, bool] = {}
        self._custom_sensitive_paths: List[str] = []

        self._load_or_init()
        self._initialized = True

    # ── 持久化 ──────────────────────────────

    def _load_or_init(self):
        policy_file = self._config_path / "policy.json"
        if policy_file.exists():
            try:
                with open(policy_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._approval_rules = data.get("approval_rules", DEFAULT_APPROVAL_RULES)
                self._classification_rules = data.get("classification_rules", DEFAULT_CLASSIFICATION_RULES)
                self._anonymize_rules = data.get("anonymize_rules", DEFAULT_ANONYMIZE_RULES)
                self._custom_sensitive_paths = data.get("custom_sensitive_paths", [])
            except Exception:
                self._init_defaults()
        else:
            self._init_defaults()
            self.save()  # 首次自动写入配置文件

    def _init_defaults(self):
        self._approval_rules = list(DEFAULT_APPROVAL_RULES)
        self._classification_rules = list(DEFAULT_CLASSIFICATION_RULES)
        self._anonymize_rules = dict(DEFAULT_ANONYMIZE_RULES)
        self._custom_sensitive_paths = list(DEFAULT_CUSTOM_SENSITIVE_PATHS)

    def save(self):
        """保存策略到磁盘"""
        policy_file = self._config_path / "policy.json"
        try:
            data = {
                "approval_rules": self._approval_rules,
                "classification_rules": self._classification_rules,
                "anonymize_rules": self._anonymize_rules,
                "custom_sensitive_paths": self._custom_sensitive_paths
            }
            with open(policy_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def reload(self):
        """热加载策略配置"""
        self._load_or_init()

    # ── 审批检查 ────────────────────────────

    def check(self, tool_name: str, target_path: str) -> Literal["approve", "confirm", "deny"]:
        """
        检查操作是否需要审批。

        参数：
            tool_name: 工具名（如 "create_local_file"）
            target_path: 目标路径

        返回：
            "approve" — 自动通过
            "confirm" — 需要人工审批
            "deny"   — 直接拒绝
        """
        normalized_path = self._normalize_path(target_path)

        # 按规则列表顺序逐条匹配（优先级：deny > confirm > approve，先命中先返回）
        for rule in self._approval_rules:
            if self._match_rule(rule, tool_name, normalized_path):
                return rule["policy"]  # type: ignore

        # 白名单免审批：仅"只读"白名单工具且路径在沙箱内时才自动放行
        if tool_name in APPROVE_READ_WHITELIST and PolicyManager._within_sandbox(target_path):
            return "approve"

        # 无规则命中且不在白名单：一律人工审批（fail-closed，不再默认放行）
        return "confirm"

    def _match_rule(self, rule: dict, tool_name: str, path: str) -> bool:
        """检查单条规则是否匹配"""
        # 路径匹配
        pattern = rule.get("path_pattern", "*")
        if not self._fnmatch_path(normalize_path_for_match(pattern), path):
            return False
        # 操作匹配
        ops = rule.get("operations", "*")
        if ops == "*":
            return True
        if isinstance(ops, str):
            return tool_name == ops
        if isinstance(ops, list):
            return tool_name in ops
        return False

    def _fnmatch_path(self, pattern: str, path: str) -> bool:
        """跨平台 fnmatch 路径匹配"""
        # 统一路径分隔符
        p = pattern.replace("\\", "/")
        t = path.replace("\\", "/")
        return fnmatch.fnmatch(t, p)

    # ── 路径分级 ────────────────────────────

    def classify_path(self, path: str) -> str:
        """
        对路径进行数据分级。

        返回："绝密" | "机密" | "内部" | "公开"
        """
        normalized = self._normalize_path(path)
        for rule in self._classification_rules:
            pattern = normalize_path_for_match(rule.get("path_pattern", "*"))
            if self._fnmatch_path(pattern, normalized):
                return rule["level"]  # type: ignore
        return "公开"

    # ── 脱敏规则查询 ────────────────────────

    def is_anonymized(self, entity_type: str) -> bool:
        """查询指定实体类型是否需要脱敏"""
        return self._anonymize_rules.get(entity_type, True)

    def set_anonymize(self, entity_type: str, enabled: bool):
        """设置指定实体类型的脱敏开关"""
        self._anonymize_rules[entity_type] = enabled
        self.save()

    def get_all_anonymize_rules(self) -> Dict[str, bool]:
        return dict(self._anonymize_rules)

    # ── 敏感路径管理 ────────────────────────

    def add_sensitive_path(self, pattern: str):
        """添加自定义敏感路径模式"""
        normalized = pattern.replace("\\", "/")
        if normalized not in self._custom_sensitive_paths:
            self._custom_sensitive_paths.append(normalized)
            self.save()

    def remove_sensitive_path(self, pattern: str):
        """移除自定义敏感路径模式"""
        normalized = pattern.replace("\\", "/")
        if normalized in self._custom_sensitive_paths:
            self._custom_sensitive_paths.remove(normalized)
            self.save()

    def get_sensitive_paths(self) -> List[str]:
        return list(self._custom_sensitive_paths)

    def is_sensitive_path(self, path: str) -> bool:
        """检查路径是否匹配自定义敏感模式"""
        normalized = self._normalize_path(path)
        for pattern in self._custom_sensitive_paths:
            if self._fnmatch_path(pattern, normalized):
                return True
        # 也检查默认分类规则中"绝密"和"机密"级别的路径
        level = self.classify_path(path)
        return level in ("绝密", "机密")

    # ── 审批规则增删改 ──────────────────────

    def get_approval_rules(self) -> List[dict]:
        return list(self._approval_rules)

    def get_classification_rules(self) -> List[dict]:
        return list(self._classification_rules)

    # ── 工具函数 ────────────────────────────

    @staticmethod
    def _normalize_path(path: str) -> str:
        """规范化路径：统一分隔符、小写、去除尾部斜杠"""
        if not path:
            return ""
        p = os.path.normpath(path).replace("\\", "/")
        return p.rstrip("/")

    @staticmethod
    def _within_sandbox(path: str) -> bool:
        """判定路径是否位于沙箱根目录内（先解析符号链接/junction，再做 commonpath 归一化比较）"""
        try:
            base = os.path.normcase(os.path.realpath(SANDBOX_ROOT))
            real = os.path.normcase(os.path.realpath(path))
            return os.path.commonpath([base, real]) == base
        except (ValueError, TypeError):
            return False

    @staticmethod
    def get_operation_category(tool_name: str) -> str:
        """获取操作类型分类"""
        if tool_name in READ_OPERATIONS:
            return "read"
        if tool_name in WRITE_OPERATIONS:
            return "write"
        if tool_name in DELETE_OPERATIONS:
            return "delete"
        if tool_name in EXEC_OPERATIONS:
            return "execute"
        return "unknown"

    @staticmethod
    def get_operation_risk(tool_name: str) -> int:
        """获取操作风险等级 (1-4)"""
        cat = PolicyManager.get_operation_category(tool_name)
        return OP_RISK.get(cat, 0)


def normalize_path_for_match(pattern: str) -> str:
    """将策略中的路径模式转换为可匹配的格式"""
    return pattern.replace("\\", "/")
