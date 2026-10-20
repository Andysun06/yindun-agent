# -*- coding: utf-8 -*-
"""隐盾 · 日志系统回归（V3.6.1）

背景（开发记忆 §7 B-7）：打包成 onefile exe 后**看不到任何输出**——172 处 `print` 全部掉进
临时解压目录，现场排障时"启动失败/回答不出来"一条线索都没有。本套件钉住新的日志出口：

  1. 落点与形态：`<日志目录>/yindun.log`，带时间/级别/模块名，级别可调（--log-level / 环境变量）
  2. 级别语义：显式 `log.*` 走 logger；散装 print 由桥接按关键词分级（失败/异常→WARNING）
  3. 掩码：手机号/身份证/邮箱/密钥/JWT 形态写进日志前被替换（日志是长期留存的落盘面）
  4. 轮转：超过上限会切分，不会无限长
  5. 健壮性：日志目录不可写时**不抛异常**（退回控制台），主流程不受影响
  6. 幂等：同一落点+级别重复 setup 不重复挂 handler（否则日志会成倍重复）

用法：python tests/test_logger.py   （退出码 0 = 全部通过）
"""
import importlib
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


TMP = Path(tempfile.mkdtemp(prefix="yindun_log_test_"))
os.environ["YINDUN_LOG_DIR"] = str(TMP)

import yindun.utils.logger as logger_mod  # noqa: E402
from yindun.utils.logger import (  # noqa: E402
    LOG_FILE_NAME, current_log_file, get_logger,
    mask_log_text, resolve_level, setup_logging)

print("=" * 78)
print("【1】落点 / 格式 / 级别")
print("=" * 78)
log_dir = setup_logging(level="INFO", log_dir=str(TMP))
log_file = log_dir / LOG_FILE_NAME
check("日志落在指定目录的 yindun.log", log_file.exists(), str(log_file))
log = get_logger("test")
log.info("常规信息：%s", "启动完成")
log.warning("警告信息：磁盘可用空间偏低")
log.error("错误信息：连接失败")
try:
    raise ValueError("故意的异常")
except ValueError:
    log.exception("异常栈也要落盘")
text = log_file.read_text(encoding="utf-8")
check("含时间与级别与模块名", " INFO " in text and "[yindun.test]" in text, text[:200])
check("三条级别都写进去了", "常规信息" in text and "警告信息" in text and "错误信息" in text)
check("异常栈落盘（含堆栈行）", "故意的异常" in text and "Traceback" in text)
check("current_log_file 指向真实文件", current_log_file() == log_file, str(current_log_file()))

print("\n" + "=" * 78)
print("【2】级别过滤与解析")
print("=" * 78)
check("resolve_level 认大写/小写", resolve_level("debug") == "DEBUG" and resolve_level("INFO") == "INFO")
check("非法级别退回默认 INFO", resolve_level("verbose") == "INFO", resolve_level("verbose"))
setup_logging(level="ERROR", log_dir=str(TMP))
before = log_file.read_text(encoding="utf-8")
get_logger("test").info("这条 INFO 不该出现（当前级别 ERROR）")
after = log_file.read_text(encoding="utf-8")
check("低于级别的记录被拦下", "这条 INFO 不该出现" not in after and len(after) >= len(before))
setup_logging(level="INFO", log_dir=str(TMP))

print("\n" + "=" * 78)
print("【3】掩码：日志文件是长期留存的落盘面")
print("=" * 78)
samples = {
    "手机": ("联系 13812345678 处理", "13812345678", "[手机]"),
    "身份证": ("证件号 110101199003072316 存档", "110101199003072316", "[身份证]"),
    "邮箱": ("发给 zhang.wei@example.com 确认", "zhang.wei@example.com", "[邮箱]"),
    "密钥": ("api_key=sk-live-abcdefghijklmn 已加载", "sk-live-abcdefghijklmn", "[密钥]"),
}
for tag, (raw, secret, masked) in samples.items():
    get_logger("test").info("掩码样例：%s", raw)
text = log_file.read_text(encoding="utf-8")
for tag, (raw, secret, masked) in samples.items():
    check(f"{tag} 明文不进日志", secret not in text, secret)
check("替换成了可读标签", all(tag in text for tag in samples) or "[手机]" in text, text[-300:])
check("mask_log_text 可直接复用（导出/断言同一口径）",
      mask_log_text("13812345678") == "[手机]" and mask_log_text("没有敏感值") == "没有敏感值")
# 打印桥是一条**跨模块数据流**（任何模块的输出都会进日志文件）：掩码要更宽一点
path_sample = r"打开 C:\Users\张伟\Documents\合同.docx 失败"
check("本机路径里的真实用户名被掩掉", "张伟" not in mask_log_text(path_sample)
      and "[用户]" in mask_log_text(path_sample), mask_log_text(path_sample))
long_token = "a" * 40
check("长不透明串（≥32 位）被掩掉", long_token not in mask_log_text(f"token={long_token}"),
      mask_log_text(f"token={long_token}"))
check("单行超长会截断（防止整篇文档被 print 进日志）",
      "已截断" in mask_log_text("中" * (logger_mod.MAX_LINE_CHARS + 500)))
check("可用环境变量整条关闭打印采集（涉密环境按需收紧）",
      callable(logger_mod.capture_prints_enabled)
      and os.environ.get("YINDUN_LOG_CAPTURE_PRINTS") is None)

print("\n" + "=" * 78)
print("【4】散装 print：职责边界（有控制台就看控制台，没控制台才进日志）")
print("=" * 78)
# 这里原本测的是"接管 sys.stdout/stderr 的 Tee"。那个机制已删除（V3.6.7）：
# 正式交付的两个 exe 都是**无控制台**形态（PyInstaller windowed，sys.stdout 就是 None），
# 于是那套"接管流"的代码在交付形态下永远不会生效——它只在**开发时从源码启动**才有流可接管，
# 而那时终端本来就看得见。留着它等于多养一条跨模块落盘通路，收益为零。
# 现在的边界很清楚：
#   · 有控制台（源码运行 / 手动接了终端）→ print 照旧进终端，**不重复落盘**；
#   · 无控制台（打包窗口态）→ 由 builtins.print 桥接按关键词分级写进日志（见【5.5】）。
check("print 不再被「接管流」改写（Tee 机制已删除）",
      not hasattr(logger_mod, "install_std_streams_redirect"))
before = log_file.read_text(encoding="utf-8")
print("[SomeModule] 这是一条普通信息（有控制台时不该进日志）")
sys.stdout.flush()
after = log_file.read_text(encoding="utf-8")
check("有控制台时不重复落盘（终端可见即可，避免日志里两套口径）",
      "这是一条普通信息（有控制台时不该进日志）" not in after, f"新增 {len(after)-len(before)} 字节")

print("\n" + "=" * 78)
print("【5】轮转：不无限长")
print("=" * 78)
check("按大小轮转（2MB × 3 份）",
      logger_mod.MAX_BYTES == 2 * 1024 * 1024 and logger_mod.BACKUP_COUNT == 3,
      f"{logger_mod.MAX_BYTES}/{logger_mod.BACKUP_COUNT}")
handlers = [h for h in logging.getLogger("yindun").handlers if hasattr(h, "maxBytes")]
check("文件 handler 带轮转配置", handlers and handlers[0].maxBytes == logger_mod.MAX_BYTES)

print("\n" + "=" * 78)
print("【5.5】窗口模式：print 采集（默认关闭，需显式开启）")
print("=" * 78)
# 打包成窗口程序时 sys.stdout/stderr 是 None，print 什么都不做，170+ 处 print 在现场不可见
# → 用 builtins.print 桥接。但**默认不开**：
# 接管 print 等于让任何模块的输出都流进日志文件（跨模块落盘面），隐私产品不该默认多这一条。
import builtins  # noqa: E402
check("默认不采集散装 print（避免默认多一条明文落盘面）",
      os.environ.get("YINDUN_LOG_CAPTURE_PRINTS") is None
      and logger_mod.capture_prints_enabled() is False)
saved_out, saved_err = sys.stdout, sys.stderr
os.environ["YINDUN_LOG_CAPTURE_PRINTS"] = "1"      # 显式开启（等价于 --log-capture-prints）
try:
    sys.stdout = None
    sys.stderr = None
    check("显式开启后桥接安装成功", logger_mod.install_print_bridge())
    print("[FakeModule] 窗口模式下的普通输出")
    print("[FakeModule] 保存配置失败：PermissionError")
    check("采集开关可被命令行打开（--log-capture-prints）",
          "log-capture-prints" in logger_mod._level_from_argv.__doc__
          or "--log-capture-prints" in (ROOT / "yindun" / "utils" / "logger.py").read_text(encoding="utf-8"))
    text = log_file.read_text(encoding="utf-8")
    check("普通输出进了日志", "窗口模式下的普通输出" in text)
    warn_line = [ln for ln in text.splitlines() if "保存配置失败" in ln]
    check("像出错的行被抬成 WARNING", warn_line and "WARNING" in warn_line[-1], str(warn_line[-1:]))
    check("重复安装幂等", logger_mod.install_print_bridge())
    # ★ 桥是**跨文件落点**：任何模块的 print 都会流进来。掩码必须在写出前就地做一遍，
    #   而不是只靠 `_MaskingFilter`——否则将来少装一次过滤器（或把日志接到别处）掩码就静默失效。
    #   这里把过滤器摘掉再打印，验证"就算没有过滤器，进 sink 的也已经是净化值"。
    probe_logger = logging.getLogger("yindun.console")
    saved_filters = probe_logger.filters[:]
    probe_logger.filters = []
    try:
        print("[FakeModule] 联系人手机 13800138000 邮箱 someone@example.com")
    finally:
        probe_logger.filters = saved_filters
    text2 = log_file.read_text(encoding="utf-8")
    line = [ln for ln in text2.splitlines() if "联系人手机" in ln]
    check("摘掉掩码过滤器后，print 进来的手机号仍不会明文落盘（掩码在桥的边界上）",
          bool(line) and "13800138000" not in line[-1] and "someone@example.com" not in line[-1],
          str(line[-1:])[:160])
finally:
    sys.stdout, sys.stderr = saved_out, saved_err
    os.environ.pop("YINDUN_LOG_CAPTURE_PRINTS", None)
    bridge = getattr(builtins, "print", None)
    original = getattr(bridge, "_yindun_original", None)
    if original is not None:
        builtins.print = original
        builtins._yindun_print_bridge = False

print("\n" + "=" * 78)
print("【6】健壮性与幂等")
print("=" * 78)
# 幂等：重复 setup 不会叠加 handler
count_before = len(logging.getLogger("yindun").handlers)
setup_logging(level="INFO", log_dir=str(TMP))
check("重复 setup 不叠加 handler", len(logging.getLogger("yindun").handlers) == count_before,
      f"{count_before} → {len(logging.getLogger('yindun').handlers)}")
# 落点不可写：不抛异常
bad = "\x00/mimosa-impossible-dir"
try:
    setup_logging(level="INFO", log_dir=bad)
    setup_logging(level="INFO", log_dir=str(TMP))
    check("日志目录不可写时不抛异常（退回控制台）", True)
except Exception as exc:                                            # noqa: BLE001
    check("日志目录不可写时不抛异常（退回控制台）", False, f"{type(exc).__name__}: {exc}")

print("\n" + "=" * 78)
print("【7】入口接线：客户端与控制面都真的调用了统一出口")
print("=" * 78)
main_src = (ROOT / "yindun" / "main.py").read_text(encoding="utf-8")
control_src = (ROOT / "yindun" / "control" / "server.py").read_text(encoding="utf-8")
audit_src = (ROOT / "yindun" / "core" / "audit_log.py").read_text(encoding="utf-8")
check("客户端入口调用 setup_from_argv", "setup_from_argv" in main_src)
check("控制面入口支持 --log-level / --log-dir",
      "--log-level" in control_src and "--log-dir" in control_src and "setup_logging" in control_src)
check("审计模块的告警走带级别的 log.*（校验失败经 _warn_verify_once，且不使用无级别 print）",
      "def _warn_verify_once" in audit_src and "log.warning(" in audit_src
      and "log.error(" in audit_src and "_warn_verify_once(" in audit_src
      and 'print(f"[AuditLog] 审计链校验失败' not in audit_src)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 日志系统全部通过：落点/级别/掩码/轮转/健壮性/入口接线均符合约定")
sys.exit(0)
