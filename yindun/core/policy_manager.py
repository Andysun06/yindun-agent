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

from yindun.core.permission import base_decision, stricter


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
    # 说明（V3.5.0 权限四档模型）：原先这里还有两条**类别级兜底**规则——
    #   delete_local_file → confirm、run_local_command → confirm。
    # 它们现在由 `core/permission.py` 的 MODE_POLICY 承担（档位决定基础判定）：
    #   · 执行命令在**四档中一律 confirm**（硬底线，写死在 MODE_POLICY，不可能被放开）；
    #   · 删除在 计划/确认/自主 三档 confirm，仅「完全访问」档在沙箱内自动放行。
    # 保留类别级规则会让高档位形同虚设（档位允许了、兜底规则又拦回去），故移交档位模型。
    # 上面这些**路径级**规则（系统目录 deny、凭据目录 confirm）是内核硬底线，任何档位都照旧生效。
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
                self._migrate_legacy_rules()
            except Exception:
                self._init_defaults()
        else:
            self._init_defaults()
            self.save()  # 首次自动写入配置文件

    # 旧版本把「删除 / 执行命令需审批」写成**类别级兜底规则**存进了 policy.json；
    # V3.5.0 起这两条由 core/permission.py 的档位模型承担（档位决定基础判定）。
    # 若不清掉存量副本，升级后的机器会出现"档位放开了、旧配置又拦回去"——
    # 高档位形同虚设，且用户看不出原因（配置文件是他从没打开过的东西）。
    LEGACY_CATEGORY_RULES = (
        {"path_pattern": "*", "operations": "delete_local_file"},
        {"path_pattern": "*", "operations": "run_local_command"},
    )

    def _migrate_legacy_rules(self) -> int:
        """升级迁移：丢掉已由档位模型接管的类别级兜底规则。返回移除条数。"""
        def is_legacy(rule: dict) -> bool:
            return any(
                str(rule.get("path_pattern")) == legacy["path_pattern"]
                and str(rule.get("operations")) == legacy["operations"]
                for legacy in self.LEGACY_CATEGORY_RULES
            )
        before = len(self._approval_rules)
        self._approval_rules = [r for r in self._approval_rules if not is_legacy(r)]
        removed = before - len(self._approval_rules)
        if removed:
            print(f"[Policy] 迁移：移除 {removed} 条已由权限档位接管的旧规则")
            self.save()
        return removed

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
            # 固定落点：_config_path 在初始化时钉死在应用配置目录，不接受外部传入路径
            policy_file.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def reload(self):
        """热加载策略配置"""
        self._load_or_init()

    # ── 审批检查 ────────────────────────────

    def check(self, tool_name: str, target_path: str) -> Literal["approve", "confirm", "deny"]:
        """检查操作是否需要审批（四档权限模型 + 只能收紧的规则）。

        判定分两层，顺序不能颠倒：
          ① **档位基础判定**（`core/permission.py`）：计划模式直接拒绝变更类操作；
             变更前确认 / 自主模式 / 完全访问 的差别在于"哪些要人工确认"。
             ★ 执行命令在**所有档位**都是 confirm —— 这是硬底线，写死在 MODE_POLICY 里。
          ② **规则只能在此基础上更严**：命中的第一条规则与基础判定取更严
             （deny > confirm > approve）。因此控制面与自定义规则**不可能**把
             档位已经要求的人工确认降级掉。

        参数：
            tool_name: 工具名（如 "create_local_file"）
            target_path: 目标路径

        返回：
            "approve" — 自动通过
            "confirm" — 需要人工审批
            "deny"   — 直接拒绝
        """
        normalized_path = self._normalize_path(target_path)

        # ① 档位基础判定（每次调用现读环境变量 → 设置一改立即生效）
        operation = PolicyManager.get_operation_category(tool_name)
        if operation not in ("read", "write", "delete", "execute"):
            operation = "write"          # 未知工具按"可能的变更"处理，绝不默认放行
        decision = base_decision(operation)

        # ② 规则只能收紧：第一条命中的规则与基础判定取更严（保持"首条命中优先"语义）
        for rule in self._approval_rules:
            if self._match_rule(rule, tool_name, normalized_path):
                decision = stricter(decision, str(rule.get("policy") or "confirm"))
                break

        return decision  # type: ignore[return-value]

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

    # ── 控制面下发规则（V3.5.0）─────────────────────────
    #  由企业控制面下发、经端点验签与"只收紧"合并后写入。三条设计约束：
    #   ① **可辨认**：每条规则带 source="control" 与服务端版本号，设置页能列出
    #      "哪些规则来自控制面"，不允许出现"来路不明的规则"；
    #   ② **幂等**：set_control_rules() 整体替换自己这一类规则，重复下发不会累积重复项；
    #   ③ **只收紧**：本层只接受 confirm / deny 与敏感路径（方向由 control/policy.py 的
    #      单调合并保证），内核不接受控制面把规则变得更松。
    CONTROL_SOURCE = "control"

    def get_control_rules(self) -> List[dict]:
        return [dict(r) for r in self._approval_rules
                if r.get("source") == self.CONTROL_SOURCE]

    def set_control_rules(self, rules: Optional[List[dict]] = None,
                          sensitive_paths: Optional[List[str]] = None,
                          policy_version: int = 0) -> int:
        """整体替换控制面规则（幂等）。返回写入的规则条数。

        rules 里每条的 policy 只允许 confirm / deny；出现别的值一律按 confirm 处理并忽略，
        因为"把规则放松"不在控制面的权限范围内。sensitive_paths 会各自生成一条
        "匹配即按 floor（默认 confirm）"的规则，插在**最前面**（审批规则是首条命中优先）。
        """
        if not hasattr(self, "_approval_rules"):
            self._load_or_init()
        self._approval_rules = [r for r in self._approval_rules
                                if r.get("source") != self.CONTROL_SOURCE]
        injected: List[dict] = []

        def _mk(pattern: str, policy: str) -> dict:
            return {"path_pattern": pattern, "operations": "*",
                    "policy": policy if policy in ("confirm", "deny") else "confirm",
                    "source": self.CONTROL_SOURCE, "from_control_version": int(policy_version or 0)}

        for pattern in (sensitive_paths or []):
            if isinstance(pattern, str) and pattern.strip():
                injected.append(_mk(pattern.strip(), "confirm"))
        for rule in (rules or []):
            if isinstance(rule, dict) and str(rule.get("path_pattern") or "").strip():
                injected.append(_mk(str(rule["path_pattern"]).strip(), str(rule.get("policy") or "confirm")))

        # 兜底规则（"审批下限"）：放在队尾，只有前面的规则都没命中时才生效
        floor_rule = None
        for rule in (rules or []):
            if isinstance(rule, dict) and rule.get("path_pattern") == "*" and rule.get("_floor"):
                floor_rule = _mk("*", str(rule.get("policy") or "confirm"))
        if floor_rule is None:
            for rule in (rules or []):
                if isinstance(rule, dict) and rule.get("_floor"):
                    floor_rule = _mk("*", str(rule.get("policy") or "confirm"))

        self._approval_rules = injected + self._approval_rules
        if floor_rule is not None:
            self._approval_rules.append(floor_rule)
        if sensitive_paths is not None:
            self._custom_sensitive_paths = [p for p in self._custom_sensitive_paths
                                            if p not in set(sensitive_paths)]
        self.save()
        return len(injected) + (1 if floor_rule else 0)

    def clear_control_rules(self) -> int:
        """移除全部控制面规则（断开控制面或运维显式撤销时调用）。返回移除条数。"""
        if not hasattr(self, "_approval_rules"):
            self._load_or_init()
        before = len(self._approval_rules)
        self._approval_rules = [r for r in self._approval_rules
                                if r.get("source") != self.CONTROL_SOURCE]
        removed = before - len(self._approval_rules)
        if removed:
            self.save()
        return removed

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
