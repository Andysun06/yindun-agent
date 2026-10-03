# -*- coding: utf-8 -*-
"""
隐盾安全内核 —— 命令白名单回归测试

背景（红队发现 → 修复 → 回归固化的安全工程闭环）：
  旧实现只做"命令名白名单 + 参数级拦截（shell 元字符 / ../ / 绝对路径）"，
  以下入口均可在参数不含任何 shell 元字符的情况下穿透为【任意代码执行】：
    · python -c "<内联代码>"      → 可读写任意文件、发起外部网络请求
    · python -m http.server       → 起服务暴露本地目录
    · pip install <包>            → 执行第三方安装脚本
    · git -c core.pager=... log   → 经 pager 间接执行命令
  修复方式：在命令名白名单之外增加"子命令/入口形态"级校验
  （python 仅允许执行沙箱内 .py 脚本；pip/git 仅允许只读子命令；git 拦截危险开关）。

说明：本测试为纯拦截验证。用例中的载荷一律使用无害内容（如 print(1)），
因为被测的安全属性是【入口形态本身被拒绝】——校验发生在任何用户代码执行之前，
载荷内容不参与判定，故无需也无法用真实攻击载荷来验证。

用法：python test_command_whitelist.py   （退出码 0 = 全部拦截成功）
"""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from yindun.core.file_tools import run_local_command

# ── A 组：必须被拦截的高危入口（参数中不含 shell 元字符）──
MUST_BLOCK = [
    ("python 内联代码 -c", 'python -c "print(1)"'),
    ("python 模块执行 -m", "python -m http.server 8000"),
    ("python 标准输入 -", "python -"),
    ("python 非脚本文件", "python 1.txt"),
    ("python 解释器开关前置", "python -u test.py"),
    ("pip 安装", "pip install some-package"),
    ("pip 按依赖清单安装", "pip install -r requirements.txt"),
    ("pip 下载", "pip download some-package -d ."),
    ("git 克隆", "git clone remote-repo.git"),
    ("git 拉取", "git fetch origin"),
    ("git pager 注入", "git -c core.pager=calc log"),
    ("git 仓库指向覆盖", "git --git-dir=../other log"),
    ("shell 重定向", "echo hi > out.txt"),
    ("路径穿越", "echo ../../etc/passwd"),
    ("绝对路径", "echo C:/Windows/win.ini"),
    ("非白名单命令", "tasklist"),
]

# ── B 组：调整后仍应放行的正常只读用法（避免"安全到不可用"）──
MUST_ALLOW = [
    ("echo 基础输出", "echo hello"),
    ("git 只读查看", "git status"),
    ("git 只读日志", "git log --oneline -n 3"),
]


def _call(cmd: str) -> str:
    return run_local_command.invoke({"command": cmd, "target_directory": "当前沙箱目录"})


print("=" * 78)
print("【命令白名单回归测试】高危入口必须拦截 / 只读用法必须放行")
print("=" * 78)

failures = []
for name, cmd in MUST_BLOCK:
    out = _call(cmd).strip()
    blocked = ("拦截" in out) or ("拒绝" in out)
    print(f"  [{'✅ 已拦截' if blocked else '❌ 未拦截'}] {name}: {cmd}")
    if not blocked:
        failures.append(f"未拦截: {name} ({cmd}) -> {out[:120]}")

print("\n" + "-" * 78)
for name, cmd in MUST_ALLOW:
    out = _call(cmd).strip()
    allowed = out.startswith("✅")
    print(f"  [{'✅ 已放行' if allowed else '❌ 被误拦'}] {name}: {cmd}")
    if not allowed:
        failures.append(f"误拦: {name} ({cmd}) -> {out[:120]}")

print("\n" + "=" * 78)
if failures:
    print(f"❌ 回归失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print(f"✅ 回归通过：{len(MUST_BLOCK)} 项高危入口全部拦截，{len(MUST_ALLOW)} 项只读用法正常放行")
sys.exit(0)
