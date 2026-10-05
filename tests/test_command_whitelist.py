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

# ── C 组：对抗性用例（红队/评审意见整改的回归锁定）──
#   · 盘符相对路径：C:evil.py 的解析依赖 per-drive CWD，曾可穿过绝对路径校验
#   · git 危险开关带值形态：--pager=calc / --open-files-in-pager=calc 曾可绕过等值匹配
ADVERSARIAL_BLOCK = [
    ("python 盘符相对路径", "python C:evil.py"),
    ("git pager 带值形态", "git grep --open-files-in-pager=calc x"),
    ("git 仓库指向带值形态", "git --work-tree=../other status"),
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

print("\n" + "-" * 78)
for name, cmd in ADVERSARIAL_BLOCK:
    out = _call(cmd).strip()
    blocked = ("拦截" in out) or ("拒绝" in out)
    print(f"  [{'✅ 已拦截' if blocked else '❌ 未拦截'}] {name}: {cmd}")
    if not blocked:
        failures.append(f"未拦截: {name} ({cmd}) -> {out[:120]}")

# ── D 组：脚本归属 / 符号链接逃逸 / 环境收敛（真实沙箱行为验证）──
print("\n" + "-" * 78)
print("【D 组】脚本必须在沙箱内（realpath 校验）/ 子进程环境收敛")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
import yindun.core.file_tools as _ft  # noqa: E402

_saved_sandbox = _ft.SANDBOX
_saved_perm = os.environ.get("PERMISSION_LEVEL")
_saved_allow = os.environ.get("ALLOW_SCRIPT_EXEC")
root_tmp = Path(tempfile.mkdtemp(prefix="yindun_cmd_adv_"))
sandbox_dir = root_tmp / "sandbox"
outside_dir = root_tmp / "outside"
sandbox_dir.mkdir(parents=True, exist_ok=True)
outside_dir.mkdir(parents=True, exist_ok=True)
# 落点受控：全部在测试自己创建的临时目录内（tempfile.mkdtemp 返回值拼出的路径）
outside_script = outside_dir / "evil.py"
outside_script.write_text("print('outside')\n", encoding="utf-8")
# 沙箱内正常脚本：验证环境里读不到内部变量（PERMISSION_LEVEL / SANDBOX_PATH 应被剥离）
leak_script = sandbox_dir / "leak.py"
leak_script.write_text(
    "import os\n"
    "print('PERM=' + str(os.environ.get('PERMISSION_LEVEL')))\n"
    "print('BOX=' + str(os.environ.get('SANDBOX_PATH')))\n",
    encoding="utf-8")

_ft.SANDBOX = str(sandbox_dir)
os.environ["PERMISSION_LEVEL"] = "完全控制 (读/写/列表)"
try:
    # D1 沙箱内脚本正常执行（realpath 替换后仍可运行），且环境已收敛
    out = _call("python leak.py")
    ok_run = out.startswith("✅")
    env_clean = ("PERM=None" in out) and ("BOX=None" in out)
    print(f"  [{'✅' if ok_run and env_clean else '❌'}] 沙箱内脚本可执行且环境收敛"
          f"（PERMISSION_LEVEL/SANDBOX_PATH 已剥离）")
    if not (ok_run and env_clean):
        failures.append(f"环境收敛失败: {out[:160]}")

    # D2 符号链接逃逸：沙箱内 link.py -> 沙箱外 evil.py，必须拦截
    link = sandbox_dir / "link.py"
    try:
        os.symlink(str(outside_script), str(link))
        out = _call("python link.py")
        blocked = ("拦截" in out) or ("拒绝" in out)
        print(f"  [{'✅ 已拦截' if blocked else '❌ 未拦截'}] 符号链接逃逸（link.py -> 沙箱外）")
        if not blocked:
            failures.append(f"符号链接逃逸未拦截: {out[:160]}")
    except OSError:
        print("  [ℹ️] 符号链接创建被系统拒绝（无创建权限）——跳过该用例，其余照常")

    # D3 不存在的脚本：拒绝且给出生箱内解析路径
    out = _call("python nothere.py")
    blocked = ("拦截" in out) or ("拒绝" in out)
    print(f"  [{'✅ 已拦截' if blocked else '❌ 未拦截'}] 不存在的脚本被拒绝")
    if not blocked:
        failures.append(f"不存在脚本未拦截: {out[:160]}")

    # D4 脚本内容预览（防盲签）：审批弹窗应能读到脚本内容，而不是只显示命令串
    from yindun.core.file_tools import preview_command_script
    pv = preview_command_script("python leak.py", str(sandbox_dir))
    ok_prev = ("import os" in pv) and ("PERM=" in pv)
    print(f"  [{'✅' if ok_prev else '❌'}] 脚本内容预览可用（审批前可见脚本正文，防盲签）")
    if not ok_prev:
        failures.append(f"脚本预览失败: {pv[:120]}")
    pv_missing = preview_command_script("python nothere.py", str(sandbox_dir))
    pv_plain = preview_command_script("echo hi", str(sandbox_dir))
    ok_prev_edge = (pv_missing == "(脚本不存在)") and (pv_plain == "")
    print(f"  [{'✅' if ok_prev_edge else '❌'}] 预览的边界形态正确（不存在→说明；非解释器命令→空）")
    if not ok_prev_edge:
        failures.append(f"预览边界失败: missing={pv_missing!r} plain={pv_plain!r}")
    # E 组：严格模式（评审意见整改）——ALLOW_SCRIPT_EXEC=0 时解释器整体禁用，只读命令保留
    os.environ["ALLOW_SCRIPT_EXEC"] = "0"
    out = _call("python leak.py")
    strict_blocked = ("拦截" in out) and ("严格模式" in out)
    print(f"  [{'✅ 已拦截' if strict_blocked else '❌ 未拦截'}] 严格模式：解释器执行被禁用（python）")
    if not strict_blocked:
        failures.append(f"严格模式未拦截 python: {out[:160]}")
    out = _call("echo strict-mode-ok")
    strict_git_ok = out.startswith("✅")
    print(f"  [{'✅ 仍放行' if strict_git_ok else '❌ 被误拦'}] 严格模式下只读命令仍可用（echo）")
    if not strict_git_ok:
        failures.append(f"严格模式误拦只读命令: {out[:160]}")
    os.environ["ALLOW_SCRIPT_EXEC"] = "1"
    out = _call("python leak.py")
    normal_ok = out.startswith("✅")
    print(f"  [{'✅' if normal_ok else '❌'}] 关闭严格模式后脚本恢复可执行（开关有效）")
    if not normal_ok:
        failures.append(f"严格模式开关恢复失败: {out[:160]}")

    # F 组：进程容器（Job Object，评审意见"强化执行隔离"的 OS 级部分）
    from yindun.core.file_tools import _run_contained
    rc_c, out_c, err_c, to_c = _run_contained(["echo", "job-ok"], str(sandbox_dir), None, 10)
    ok_job = (rc_c == 0) and ("job-ok" in out_c) and (not to_c)
    print(f"  [{'✅' if ok_job else '❌'}] 容器内命令正常执行（Job Object 绑定不影响功能）")
    if not ok_job:
        failures.append(f"容器执行失败: rc={rc_c} out={out_c[:80]!r} err={err_c[:80]!r}")
    rc_t, _, _, to_t = _run_contained(["python", "-c", "import time; time.sleep(20)"],
                                      str(sandbox_dir), None, 1)
    ok_kill = to_t is True
    print(f"  [{'✅' if ok_kill else '❌'}] 超时即终止整棵进程树（不再等待/留孤儿进程）")
    if not ok_kill:
        failures.append(f"容器超时终止失败: rc={rc_t} timed_out={to_t}")
finally:
    _ft.SANDBOX = _saved_sandbox
    if _saved_perm is None:
        os.environ.pop("PERMISSION_LEVEL", None)
    else:
        os.environ["PERMISSION_LEVEL"] = _saved_perm
    if _saved_allow is None:
        os.environ.pop("ALLOW_SCRIPT_EXEC", None)
    else:
        os.environ["ALLOW_SCRIPT_EXEC"] = _saved_allow

print("\n" + "=" * 78)
if failures:
    print(f"❌ 回归失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print(f"✅ 回归通过：{len(MUST_BLOCK)} 项高危入口 + {len(ADVERSARIAL_BLOCK)} 项对抗用例全部拦截，"
      f"{len(MUST_ALLOW)} 项只读用法正常放行；脚本归属/环境收敛/严格模式/进程容器全部符合预期")
sys.exit(0)
