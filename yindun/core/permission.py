# -*- coding: utf-8 -*-
r"""隐盾 · AI 权限档位（四档，单一事实来源）

═══ 设计（为什么是这四档，以及底线在哪）═══

| 档位 | 只读 | 写盘/创建 | 删除 | 执行命令 |
|---|---|---|---|---|
| **计划模式** plan | ✅ 自动（沙箱内） | ⛔ 直接拒绝 | ⛔ 直接拒绝 | ⛔ 直接拒绝 |
| **变更前确认** ask | ✅ 自动 | 🙋 人工确认 | 🙋 人工确认 | 🙋 人工确认 |
| **自主模式** auto | ✅ 自动 | ✅ 自动（沙箱内） | 🙋 人工确认 | 🙋 人工确认 |
| **完全访问** full | ✅ 自动 | ✅ 自动（沙箱内） | ✅ 自动（沙箱内） | 🙋 人工确认 |

★ **硬底线（任何档位都不放开）**：
  1. **执行命令永远需要人工确认** —— 命令执行是策略层控制 + 进程容器，不是 OS 级隔离，
     自动放行的风险量级与文件操作不同。想少打扰就少用命令，而不是把这条关掉。
  2. **内核路径地板与敏感路径**（`C:\Windows\*`、`/etc/*`、`.ssh`/`.aws`/`.env`/`id_rsa`
     等）与档位无关，任何档位都不可关闭或放宽（见 policy_manager 的默认规则）；
  3. 控制面下发的规则**只能在本档位基础上继续收紧**（deny 覆盖 confirm 覆盖 approve）。

★ **可读写范围（沙箱）= 有多个根的"默认范围"，不是"绝对墙"**（V3.6.8）：
  历史实现把沙箱当成硬边界——越界一律拒绝（读操作还静默回退），结果是"开了完全访问
  也什么都做不了"（数桌面文件、在桌面建 txt 全被挡），与设置页四档能力表自相矛盾，
  用户体验就是"这个智能体没用"。
  ① **默认可读写范围 = 应用根目录 + 用户桌面 / 文档 / 下载**（`sandbox_roots()`，
     注册表取真实路径以兼容 OneDrive 重定向）；用户显式配置 `sandbox_dir` 时**只认它**。
  ② 范围之外的操作按**档位**判定（`OUTSIDE_FLOOR`）：
    | 档位 | 可读写范围之外的操作 |
    |---|---|
    | 计划模式 plan | ⛔ 直接拒绝 |
    | 变更前确认 ask | 🙋 每次人工确认（弹窗显示完整真实路径） |
    | 自主模式 auto | ✅ 自动放行（留审计） |
    | 完全访问 full | ✅ 自动放行（留审计） |
  **一次都没有静默**：越界判定写入审计（事件类型 access_control，含工具名/真实路径/
  档位/判定结果），并在工具结果里写明「本次操作在可读写范围之外：<路径>」。
  真正的墙仅剩两条，且与档位无关：① 内核路径地板与凭据类路径（上面的硬底线 2）；
  ② 写入前的 realpath+commonpath 落点校验（符号链接/junction 逃逸仍然拒绝）。
  放行必须由宿主策略层授予，模型传入的任何"允许越界"参数都会被丢弃（见 agent_worker）。

★ **立即生效**：档位取值每次调用时从环境变量读取（`permission_mode()`），
  因此设置页一改就作用于下一次工具调用，**不需要重启**。

★ **向后兼容**：旧版中文档位名（彻底审计 / 安全只读 / 完全控制）与旧数值/空值都能解析：
  彻底审计、安全只读 → plan（只读分析）；完全控制 → ask（变更前确认）。
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

# ── 环境变量：设置页写入、工具函数每次调用读取（→ 立即生效）──
PERMISSION_ENV = "YINDUN_PERMISSION"
LEGACY_ENV = "PERMISSION_LEVEL"          # 旧变量（中文档位名），仍可解析

MODES = ("plan", "ask", "auto", "full")

LABELS: Dict[str, str] = {
    "plan": "计划模式",
    "ask": "变更前确认",
    "auto": "自主模式",
    "full": "完全访问",
}
SHORT: Dict[str, str] = {
    "plan": "计划",
    "ask": "确认",
    "auto": "自主",
    "full": "完全",
}
HINTS: Dict[str, str] = {
    "plan": "只读分析、给出计划；不写盘、不删除、不执行命令",
    "ask": "读操作自动放行；写盘 / 删除 / 执行命令前逐次人工确认（默认，最稳）",
    "auto": "沙箱内的创建与写入自动放行；删除与执行命令仍逐次确认",
    "full": "沙箱内的创建 / 写入 / 删除自动放行；执行命令仍逐次确认（底线不可越）",
}

# 旧中文档位名 → 新档位
_LEGACY_MAP = {
    "彻底审计": "plan",
    "安全只读": "plan",
    "完全控制": "ask",
}

DEFAULT_MODE = "ask"

# 操作类别 → 各档位的基础判定（"approve" 自动放行 / "confirm" 人工确认 / "deny" 直接拒绝）
MODE_POLICY: Dict[str, Dict[str, str]] = {
    "plan": {"read": "approve", "write": "deny",    "delete": "deny",    "execute": "deny"},
    "ask":  {"read": "approve", "write": "confirm", "delete": "confirm", "execute": "confirm"},
    "auto": {"read": "approve", "write": "approve", "delete": "confirm", "execute": "confirm"},
    "full": {"read": "approve", "write": "approve", "delete": "approve", "execute": "confirm"},
}

# 判定强度：用于"只收紧"的取更严运算（控制面规则 / 插件规则都只能往上加）
_RANK = {"approve": 0, "confirm": 1, "deny": 2}

# 越界判定下限：目标在「可读写范围」之外时，本档位最多能放松到什么程度。
# 语义是**下限**（与档位基础判定取更严者）：例如完全访问档的越界写盘 = stricter(approve, approve)
# = 自动放行；而越界执行命令 = stricter(confirm, approve) = 仍要确认；计划模式一律 deny。
# ★ 这张表是"越界不再静默"的唯一依据：任何一档都不是"无判定"，只是判定的严格度不同。
OUTSIDE_FLOOR: Dict[str, str] = {
    "plan": "deny",       # 计划模式不越界访问：拒绝（且不弹确认，本档没有审批流）
    "ask":  "confirm",    # 变更前确认：每次人工确认，弹窗显示完整真实路径
    "auto": "approve",    # 自主模式：放行（写审计 + 工具结果标注）
    "full": "approve",    # 完全访问：放行（写审计 + 工具结果标注）
}

# 界面用的越界语义文案（单一来源）：四档「可读写范围之外」一行，
# 与 HINTS 同款"内核给文案、界面直接取用"的做法，避免界面另写一套说法而漂移。
# ★ 注意：HINTS 的四条文案已被 tests/test_permission.py 与 web/app.js 钉成一致；
#   本表是 V3.6.8 新增的第五行语义，界面接入时直接用它（不要把两处文案各改一半）。
OUTSIDE_HINTS: Dict[str, str] = {
    "plan": "可读写范围之外：禁止访问（计划模式不越界）",
    "ask": "可读写范围之外：每次人工确认（弹窗显示完整路径）",
    "auto": "可读写范围之外：自动放行（留审计并在结果中标注）",
    "full": "可读写范围之外：自动放行（留审计并在结果中标注）",
}


def resolve_mode(value: Optional[str]) -> str:
    """把任意来源的档位值解析成四档之一（含旧中文档位名与空值兜底）。"""
    text = str(value or "").strip()
    if text in MODES:
        return text
    for legacy, mode in _LEGACY_MAP.items():
        if legacy in text:
            return mode
    lowered = text.lower()
    if lowered in MODES:
        return lowered
    return DEFAULT_MODE


def permission_mode() -> str:
    """当前生效档位。**每次调用都读环境变量** —— 设置一改，下一次工具调用即生效。"""
    value = os.environ.get(PERMISSION_ENV)
    if value:
        return resolve_mode(value)
    return resolve_mode(os.environ.get(LEGACY_ENV))


def stricter(a: str, b: str) -> str:
    """取两个判定中更严的那个（approve < confirm < deny）。"""
    return a if _RANK.get(a, 1) >= _RANK.get(b, 1) else b


def base_decision(operation: str, mode: Optional[str] = None) -> str:
    """档位对某类操作的基础判定。operation ∈ read/write/delete/execute。"""
    table = MODE_POLICY.get(mode or permission_mode(), MODE_POLICY[DEFAULT_MODE])
    return table.get(operation, "confirm")


def outside_decision(operation: str, mode: Optional[str] = None) -> str:
    """目标在「可读写范围」之外时，本档位的最终判定（基础判定与越界下限取更严者）。

    为什么要把"越界"折进同一张判定表：越界不是"另一种拒绝"，只是**同一类操作在不同
    范围上的严格度差异**。把它做成一个函数、一个返回值，审批层、工具层、审计层读到的
    就是同一个判定（不会出现"策略说放行、工具又自己拒了"的两套真相）。
    """
    current = mode or permission_mode()
    floor = OUTSIDE_FLOOR.get(current, OUTSIDE_FLOOR[DEFAULT_MODE])
    return stricter(base_decision(operation, current), floor)


# ──────────────────────────────────────────
# 可读写范围（沙箱根）：设置 > 环境变量 > 应用根目录
# ──────────────────────────────────────────
SANDBOX_ENV = "SANDBOX_PATH"      # 旧协议：环境变量（测试/脚本仍可只设它）
SETTINGS_KEY = "sandbox_dir"      # 设置键名（settings_store.DEFAULTS 里的键，界面按这个键读写）

# 设置读取器：由**应用层**在启动时注入（core 不反向 import app 层，保持"内核与界面无关"）。
# 契约：无参可调用对象，返回字符串；空串 = 未配置（继续按环境变量回退）。
# 之所以允许返回空串而不是 None：设置里"清空"就是这个语义，测试里也不用手写 None 分支。
_sandbox_settings_source = None


def register_sandbox_settings_source(source) -> None:
    """注入"设置里的可读写范围"读取器（应用层启动时注册一次；传 None 注销）。"""
    global _sandbox_settings_source
    _sandbox_settings_source = source


def configured_sandbox_dir() -> str:
    """设置里配置的可读写范围（未配置/读取失败 → 空串）。读取失败不抛：宁可回退也不要
    因为配置层异常让所有文件工具不可用（回退到的是**旧行为**，不是新放宽）。"""
    source = _sandbox_settings_source
    if source is None:
        return ""
    try:
        return str(source() or "").strip()
    except Exception:
        return ""


def user_working_dirs() -> List[str]:
    """用户天天在用的目录：**桌面 / 文档 / 下载**（只收真实存在的）。

    为什么默认范围要包含它们：隐盾是**桌面**智能体，用户让它"数数桌面上有几个文件"
    "在桌面建个 txt"是本职活儿。旧实现的默认可读写范围是应用根目录，于是这些日常操作
    全被判成越界（'变更前确认'档要逐个弹窗、'计划模式'直接拒绝），用户体验就是
    "什么都做不了"。

    路径解析（顺序即优先级）：
      ① Windows 注册表 `Explorer\\User Shell Folders` —— 能拿到**被 OneDrive 重定向后**
         的真实桌面/文档/下载（`~/Desktop` 那种拼法在重定向机器上是错的）；
      ② 标准位置兜底（`~/Desktop`、`~/Documents`、`~/Downloads`）；
    读不到的**不收**——不存在的目录进范围只会让设置页列出点进去就报错的路径。
    """
    found: List[str] = []

    def _add(candidate) -> None:
        if not candidate:
            return
        try:
            absolute = os.path.abspath(os.path.expandvars(str(candidate)))
        except Exception:
            return
        if os.path.isdir(absolute) and absolute not in found:
            found.append(absolute)

    if os.name == "nt":
        try:
            import winreg
            key_path = (r"Software\Microsoft\Windows\CurrentVersion"
                        r"\Explorer\User Shell Folders")
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                for value_name in ("Desktop", "Personal",
                                   "{374DE290-123F-4565-9164-39C4925E467B}"):
                    try:
                        raw, _kind = winreg.QueryValueEx(key, value_name)
                        _add(raw)
                    except OSError:
                        continue
        except OSError:
            pass

    home = os.path.expanduser("~")
    for fallback in ("Desktop", "Documents", "Downloads"):
        _add(os.path.join(home, fallback))
    return found


def sandbox_roots() -> List[str]:
    """当前「可读写范围」的**全部**根（V3.6.8 起默认可有多个）。

    解析顺序：**设置里的 sandbox_dir（配置了就只认它，替换默认集合）> 环境变量
    SANDBOX_PATH > 默认集合（应用根目录 + 用户桌面/文档/下载）**。
    用户显式指定范围时**不再叠加**桌面等目录：他说"只准动这个目录"就照做。
    """
    configured = configured_sandbox_dir()
    if configured:
        return [os.path.abspath(configured)]
    environment = os.environ.get(SANDBOX_ENV)
    if environment:
        return [os.path.abspath(environment)]
    roots = [os.path.abspath(str(_APP_ROOT()))]
    for directory in user_working_dirs():
        if directory not in roots:
            roots.append(directory)
    return roots


def sandbox_scope_text(separator: str = "、") -> str:
    """范围的人话表述（提示词 / 工具话术 / 界面提示共用一处，避免口径不一致）。"""
    return separator.join(sandbox_roots())


def sandbox_root() -> str:
    """**主根**：显式配置（设置/环境变量）优先，否则应用根目录。

    用途：新文件的默认落点、旧引用兼容、界面上的"当前工作目录"。
    ★ 判定"是否越界"请用 `is_within_sandbox` / `sandbox_roots` —— 范围可能是多个根
      （默认还包含桌面/文档/下载），拿主根去比会把桌面误判成越界。
    """
    return sandbox_roots()[0]


def _APP_ROOT():
    """应用根目录的惰性取用：避免 permission 在导入期就依赖 yindun 包的初始化顺序。"""
    from yindun import APP_ROOT
    return APP_ROOT


def is_within_sandbox(path: str, root: Optional[str] = None) -> bool:
    """路径是否在可读写范围之内（先 realpath 破解符号链接/junction，再 commonpath 归一化比较）。

    传了 `root` 就只比这一个根（调用方明确指定时）；否则比**范围的全部根**
    （默认含应用根目录与用户桌面/文档/下载，见 `sandbox_roots`）。
    """
    if not path:
        return False
    try:
        real = os.path.normcase(os.path.realpath(str(path)))
    except (TypeError, ValueError):
        return False
    bases = [root] if root else sandbox_roots()
    for base in bases:
        try:
            normalized = os.path.normcase(os.path.realpath(str(base)))
            if os.path.commonpath([normalized, real]) == normalized:
                return True
        except (ValueError, TypeError):
            continue
    return False


def gate_reason(operation: str, tool_display: str = "") -> str:
    """工具入口的**硬闸**：只在"计划模式"下拦下变更类操作，返回拦截话术；放行则返回空串。

    为什么只在这里拦 plan：其余三档的差别是"要不要人工确认"，那是审批层的事
    （`policy_manager.check`）。工具内部再判一次会造成两套真相。
    """
    if permission_mode() != "plan" or operation == "read":
        return ""
    what = {"write": "写盘 / 创建 / 修改文件", "delete": "删除文件", "execute": "执行命令"}.get(operation, "变更操作")
    name = f"（{tool_display}）" if tool_display else ""
    return (f"❌ 权限拦截：当前为【计划模式】{name}，本档位只做只读分析、不产生任何变更——"
            f"因此{what}已被拒绝。若确实需要动手，请在设置页把权限档位切到「变更前确认」或更高。")
