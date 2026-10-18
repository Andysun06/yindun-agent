# -*- coding: utf-8 -*-
"""隐盾 · 统一日志出口（V3.6.1）

**为什么需要它**（来自开发记忆 §7 路线图 B-7 的证据）：
打包成 onefile exe 后**看不到任何输出**——现场排障时，`print` 的 172 处调用全部消失在
临时解压目录里，用户说"启动失败/回答不出来"，我们手上一条线索都没有。

设计原则（按这个顺序取舍）：
  1. **不改变既有行为**：日志失败绝不影响主流程（写不进去就退回控制台，不抛异常）；
  2. **落点固定**：`<APP_ROOT>/logs/yindun.log`，按大小轮转（默认 2MB × 3 份），
     exe 同目录、与配置/审计/会话一致（便携版"删目录即卸载"的语义不变）；
  3. **不出网、不泄密**：只写本机文件；`log_*` 输出前统一过一道**轻量掩码**
     （手机号/身份证/邮箱/长串密钥样式），避免业务代码把敏感值顺手打进日志；
  4. **零依赖**：只用标准库（logging + logging.handlers），打包不受影响。

用法::

    from yindun.utils.logger import setup_logging, get_logger
    setup_logging()                     # 进程入口调用一次（幂等）
    log = get_logger("workflow")        # 模块级出口
    log.info("采集完成：%s 行", n)       # 惰性格式化，级别不够时不做字符串拼接

命令行：`--log-level DEBUG|INFO|WARNING|ERROR`（默认 INFO）；`--log-dir <目录>` 可换落点；
环境变量 `YINDUN_LOG_LEVEL` / `YINDUN_LOG_DIR` 等效（命令行优先）。
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys
from pathlib import Path
from typing import Optional

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LEVEL = "INFO"
LOG_FILE_NAME = "yindun.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured_signature: tuple = ()
_log_dir_used: Optional[Path] = None

# 轻量掩码：只挡"最像凭据/个人标识"的形态。这里**不做**完整脱敏（那是隐私引擎的职责），
# 目的是防止业务代码把敏感值顺手写进日志——日志文件是长期留存的落盘面。
#
# ★ 安全边界（写清楚，别靠"应该没事"）：接管 print 意味着**任何模块的输出都会进日志文件**，
#   这是一条真实存在的落盘面。因此：
#     ① 掩码规则要在"不破坏可读性"的前提下尽量宽（见下：手机/身份证/邮箱/密钥/JWT/本机路径/长不透明串）；
#     ② 单行**限长**（`MAX_LINE_CHARS`），避免把整段文档/报文打印进日志；
#     ③ 允许显式关闭采集：环境变量 `YINDUN_LOG_CAPTURE_PRINTS=0`（涉密环境可按需关掉）；
#     ④ 日志只写本机文件、不出网；要更严就走隐私引擎的那套（本模块不复制它的职责）。
_MASK_RULES = (
    (re.compile(r"\b1[3-9]\d{9}\b"), "[手机]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[身份证]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "[邮箱]"),
    (re.compile(r"\b(?:sk|ghp|gho|api|token)[-_][A-Za-z0-9_-]{12,}\b", re.I), "[密钥]"),
    (re.compile(r"\beyJ[A-Za-z0-9._-]{20,}\b"), "[JWT]"),
    # 本机用户目录里的真实用户名（日志里写路径是常态，但用户名属于个人信息）
    (re.compile(r"([A-Za-z]:\\Users\\)[^\\/\s]+", re.I), r"\1[用户]"),
    (re.compile(r"(/home/)[^/\s]+"), r"\1[用户]"),
    # 长不透明串（32 位以上的十六进制/base64 形态）：多半是令牌、哈希原文、密钥材料
    (re.compile(r"\b[A-Za-z0-9+/_-]{32,}={0,2}\b"), "[长串]"),
)
# 单行上限（超过就截断并标注）：防止一次 print 把整篇文档写进日志
MAX_LINE_CHARS = 2000


def mask_log_text(text: str) -> str:
    """日志文本的轻量掩码（导出/断言都用同一个函数，避免两套口径）。"""
    out = str(text)
    for pattern, tag in _MASK_RULES:
        out = pattern.sub(tag, out)
    if len(out) > MAX_LINE_CHARS:
        out = out[:MAX_LINE_CHARS] + f"…（已截断，原长 {len(out)} 字符）"
    return out


# 「这句话像出错了吗」的启发式关键词。**只用在 print 桥上**（历史代码几百处 print，
# 逐个改成 log.* 风险大于收益；安全关键路径已显式改造）。想精确分级就用 log.*。
_ERROR_HINT = re.compile(
    r"失败|错误|异常|无法|不可用|告警|警告|超时|拒绝|Error|ERROR|Traceback|Exception")


class _MaskingFilter(logging.Filter):
    """给每条记录做掩码；异常栈同样过一遍（栈里也可能带路径与参数）。"""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = mask_log_text(record.getMessage())
            record.args = ()
            if record.exc_text:
                record.exc_text = mask_log_text(record.exc_text)
        except Exception:
            pass
        return True


def default_log_dir() -> Path:
    """默认落点：`<APP_ROOT>/logs`（打包后 = exe 同目录）。"""
    try:
        from yindun import APP_ROOT
        return Path(APP_ROOT) / "logs"
    except Exception:
        return Path.cwd() / "logs"


def resolve_log_dir(cli_dir: str = "") -> Path:
    return Path(cli_dir).expanduser() if cli_dir else Path(
        os.environ.get("YINDUN_LOG_DIR") or default_log_dir())


def resolve_level(cli_level: str = "") -> str:
    level = (cli_level or os.environ.get("YINDUN_LOG_LEVEL") or DEFAULT_LEVEL).strip().upper()
    return level if level in LEVELS else DEFAULT_LEVEL


def setup_logging(level: str = "", log_dir: str = "", console: bool = True) -> Path:
    """配置统一日志出口（幂等：同一落点+级别重复调用直接返回）。

    返回实际使用的日志目录；**任何异常都不会向外抛**——日志系统坏了不该拖垮应用，
    但要留下痕迹（退回控制台 handler，并在 stderr 上说一句）。
    """
    global _configured_signature, _log_dir_used
    resolved_level = resolve_level(level)
    target_dir = resolve_log_dir(log_dir)
    signature = (resolved_level, str(target_dir), bool(console))
    if _configured_signature == signature:
        return target_dir

    root = logging.getLogger("yindun")
    root.setLevel(getattr(logging, resolved_level))
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FMT, datefmt=_DATEFMT)
    masking = _MaskingFilter()
    attached_file = False
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            target_dir / LOG_FILE_NAME, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT,
            encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(masking)
        root.addHandler(file_handler)
        attached_file = True
        _log_dir_used = target_dir
    except Exception as exc:      # 只读盘 / 权限不足：退回控制台，不能拖垮应用
        print(f"[logger] 日志文件不可用（{type(exc).__name__}: {exc}），仅输出到控制台",
              file=sys.stderr)

    if console and sys.stderr is not None:
        # 打包成窗口程序（windowed）时 sys.stderr 可能是 None：那时**只能**写文件，
        # logging 的 StreamHandler(None) 会在 emit 时炸掉（被 logging 吞掉，但毫无意义）
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        stream.addFilter(masking)
        root.addHandler(stream)

    root.info("日志已就绪：级别 %s，落点 %s", resolved_level,
              (_log_dir_used / LOG_FILE_NAME) if attached_file else "(仅控制台)")
    _configured_signature = signature
    return target_dir


def get_logger(name: str = "") -> logging.Logger:
    """取一个模块级 logger（统一挂在 `yindun` 命名空间下）。"""
    return logging.getLogger("yindun" + (f".{name}" if name else ""))


def current_log_file() -> Optional[Path]:
    """当前日志文件路径（没配置或落盘不可用时返回 None）——界面/自检可以据此提示。"""
    return (_log_dir_used / LOG_FILE_NAME) if _log_dir_used else None


def capture_prints_enabled() -> bool:
    """是否允许把散装 print 收进日志（**默认关闭，需显式开启**）。

    ★ 为什么默认关：接管 `print` 等于让**任何模块的输出**都流进日志文件——这是一条额外的
      跨模块落盘面。对隐私产品来说，"多一条默认开启的明文落盘通道"是要慎重的事，
      所以默认只记录显式 `log.*`（安全关键路径——审计/沙箱/插件宿主/控制面——已全部改成 log.*），
      排障需要时由使用者显式打开：
        · 环境变量 `YINDUN_LOG_CAPTURE_PRINTS=1`（或在设置里由运维统一加），或
        · 命令行 `--log-capture-prints`（客户端/控制面入口都认）。
      打开后仍受掩码与单行限长约束；关着就是零采集，没有"偷偷记一把"。
    """
    value = os.environ.get("YINDUN_LOG_CAPTURE_PRINTS", "").strip().lower()
    return value in ("1", "true", "yes", "on")


def install_print_bridge() -> bool:
    """把内置 `print` 接到日志出口（**窗口模式专用**）。

    为什么还需要它：打包成窗口程序（PyInstaller windowed）时 `sys.stdout` / `sys.stderr` 是 **None**，
    `print` 什么都不做，于是 170+ 处 print 在现场依然不可见
    （实测打包后的客户端 `logs/yindun.log` 只有启动那两行）。
    这里改成替换 `builtins.print` 本身：有可用流就照原样输出（不改变开发体验），
    没有流（窗口模式）就按关键词分级写进日志。
    ★ 这是一条**跨模块的数据流**（任何模块的 print 都会进日志文件）：文本统一过掩码、单行限长，
      并可用 `YINDUN_LOG_CAPTURE_PRINTS=0` 整条关掉（见模块顶部的安全边界说明）。
    返回是否安装（重复调用 / 显式关闭时返回相应结果）。
    """
    import builtins
    if not capture_prints_enabled():
        return False
    if getattr(builtins, "_yindun_print_bridge", False):
        return True
    if _log_dir_used is None:
        return False
    original = builtins.print

    def _bridge(*args, sep=" ", end="\n", file=None, flush=False):     # noqa: A002
        stream = file if file is not None else getattr(sys, "stdout", None)
        if stream is not None:
            original(*args, sep=sep, end=end, file=stream, flush=flush)
            return
        try:
            text = sep.join(str(item) for item in args)
            for line in (text.splitlines() or [""]):
                if line.strip():
                    level = logging.WARNING if _ERROR_HINT.search(line) else logging.INFO
                    get_logger("console").log(level, "%s", line)
        except Exception:                 # 日志层永远不许拖垮业务
            pass

    _bridge._yindun_original = original      # 供测试/排障还原
    builtins.print = _bridge
    builtins._yindun_print_bridge = True
    return True


def _level_from_argv(argv) -> tuple:
    """从命令行解析 `--log-level` / `--log-dir` / `--log-capture-prints`（不引 argparse，保持轻量）。"""
    level = log_dir = ""
    args = list(argv or [])
    for i, item in enumerate(args):
        if item == "--log-level" and i + 1 < len(args):
            level = args[i + 1]
        elif item.startswith("--log-level="):
            level = item.split("=", 1)[1]
        elif item == "--log-dir" and i + 1 < len(args):
            log_dir = args[i + 1]
        elif item.startswith("--log-dir="):
            log_dir = item.split("=", 1)[1]
        elif item == "--log-capture-prints":
            # 显式要求采集散装 print（默认不采：见 capture_prints_enabled 的说明）
            os.environ["YINDUN_LOG_CAPTURE_PRINTS"] = "1"
    return level, log_dir


def setup_from_argv(argv=None, console: bool = True) -> Path:
    """入口统一调用：解析命令行/环境变量 → 配好日志 → 打包态顺带把散装 print 接进日志。

    只剩一条路（V3.6.7）：**打包态替换 builtins.print**。原先还并行养着一套"接管
    sys.stdout/sys.stderr"的 Tee，但它只在**有控制台**时才有流可接管——而交付的两个 exe
    都是无控制台形态（窗口态 sys.stdout 就是 None），那套代码在交付形态下永不生效；
    开发时从源码跑又本来就能在终端看见输出。删掉它以后边界更清楚：
      · 有控制台 → print 进终端（不重复落盘，日志里不出现两套口径）；
      · 无控制台 → 桥接按关键词分级写进日志（掩码与限长由日志层统一负责）。
    """
    level, log_dir = _level_from_argv(sys.argv[1:] if argv is None else argv)
    path = setup_logging(level=level, log_dir=log_dir, console=console)
    if getattr(sys, "frozen", False):
        install_print_bridge()
    return path
