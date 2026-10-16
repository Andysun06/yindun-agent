# -*- coding: utf-8 -*-
"""隐盾 · AI 权限四档回归测试（计划模式 / 变更前确认 / 自主模式 / 完全访问）

为什么需要这个套件：
  权限档位是「用户以为自己在控制什么」和「实际会发生什么」之间的接缝。历史上这个接缝
  有两处漏：① 档位名散落在界面文案与工具函数里的中文串，改一处漏一处；② 档位只作用于
  「写盘」，删除与执行命令的判定另有一套写法，两者会漂移。
  现在档位收敛到 core/permission.py 一处（唯一事实来源），本套件锁住三件事：
    A. 四档对「读 / 写 / 删除 / 执行」四类操作的判定矩阵，与文档/界面矩阵完全一致；
    B. **硬底线**：任何档位下，执行命令都要人工确认、内核路径地板与本机敏感路径照旧拦截；
    C. **立即生效**：档位从环境变量现读，改完下一个操作即按新档位判定（不需要重启）；
       控制面下发的规则只能在本档位基础上**继续收紧**，不能反向放宽。
  另附「界面矩阵 = 内核策略」的一致性检查：界面上的对照表是给用户看的承诺，
  承诺与实现漂移就是安全事故，所以用测试把两者钉在一起（对比 index.html 的 data-decision）。

用法：python tests/test_permission.py   （退出码 0 = 全部通过）
"""
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.core import file_tools as ft  # noqa: E402
from yindun.core import permission as perm  # noqa: E402
from yindun.core.policy_manager import SANDBOX_ROOT, PolicyManager  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def sandbox(name):
    """沙箱内路径（PolicyManager 把沙箱内视为可控范围）"""
    return str(Path(SANDBOX_ROOT) / name)


_saved_env = os.environ.get(perm.PERMISSION_ENV)
_saved_legacy = os.environ.get(perm.LEGACY_ENV)


def set_mode(mode):
    os.environ[perm.PERMISSION_ENV] = mode


def restore_env():
    if _saved_env is None:
        os.environ.pop(perm.PERMISSION_ENV, None)
    else:
        os.environ[perm.PERMISSION_ENV] = _saved_env
    if _saved_legacy is None:
        os.environ.pop(perm.LEGACY_ENV, None)
    else:
        os.environ[perm.LEGACY_ENV] = _saved_legacy


# 期望矩阵：与 core/permission.py 头部文档表、界面「四档能力对照」表三方一致
EXPECTED = {
    "plan": {"read": "approve", "write": "deny", "delete": "deny", "execute": "deny"},
    "ask": {"read": "approve", "write": "confirm", "delete": "confirm", "execute": "confirm"},
    "auto": {"read": "approve", "write": "approve", "delete": "confirm", "execute": "confirm"},
    "full": {"read": "approve", "write": "approve", "delete": "approve", "execute": "confirm"},
}
DECISION_TEXT = {"approve": "允许", "confirm": "每次确认", "deny": "禁止"}
OP_TEXT = {"read": "读取 / 搜索", "write": "新建 / 修改文件", "delete": "删除文件", "execute": "执行命令"}


# ── 1) 档位解析与兼容 ──────────────────────────────
print("=" * 78)
print("【1】档位解析：稳定键 + 旧中文档位名兼容 + 空值兜底")
print("=" * 78)
check("四档稳定键齐备且顺序即「由紧到松」", perm.MODES == ("plan", "ask", "auto", "full"), str(perm.MODES))
check("每档都有中文名与说明（界面直接取用，不另写一套文案）",
      all(m in perm.LABELS and m in perm.HINTS and m in perm.SHORT for m in perm.MODES))
for legacy, expect in (("彻底审计 (仅对话)", "plan"), ("安全只读 (仅读/列表)", "plan"),
                       ("完全控制 (读/写/列表)", "ask"), ("", "ask"), (None, "ask"), ("没见过的档位", "ask")):
    check(f"旧值/脏值 {legacy!r} → {expect}（默认落在最稳的确认档，不放宽）",
          perm.resolve_mode(legacy) == expect, perm.resolve_mode(legacy))
for mode in perm.MODES:
    check(f"新键 {mode} 原样解析（界面与内核用同一个键）", perm.resolve_mode(mode) == mode)

# ── 2) 判定矩阵 ────────────────────────────────────
print("=" * 78)
print("【2】四档 × 四类操作：判定矩阵（内核唯一事实来源）")
print("=" * 78)
for mode, ops in EXPECTED.items():
    set_mode(mode)
    got = {op: perm.base_decision(op) for op in ops}
    check(f"{perm.LABELS[mode]}（{mode}）矩阵一致", got == ops, f"{got} != {ops}")
    check(f"{perm.LABELS[mode]}：档位由环境变量现读（permission_mode 一致）",
          perm.permission_mode() == mode, perm.permission_mode())

check("越严越优先的合成顺序：approve < confirm < deny",
      perm.stricter("approve", "confirm") == "confirm" and perm.stricter("confirm", "deny") == "deny"
      and perm.stricter("deny", "approve") == "deny")
check("未知操作类别按「变更类」从严处理（不给未知操作自动放行）",
      perm.base_decision("some_new_op") in ("confirm", "deny"))
set_mode("plan")        # 上面循环停在 full，硬闸断言必须先回到最紧档
check("plan 模式下变更类被硬闸拦下（返回原因文本，工具层直接回显给用户）",
      bool(perm.gate_reason("write", "创建文件")) and bool(perm.gate_reason("delete", "删除文件"))
      and bool(perm.gate_reason("execute", "执行命令")))
set_mode("ask")
check("非 plan 档不再由硬闸拦截（交给策略引擎的审批流）",
      perm.gate_reason("write", "创建文件") == "" and perm.gate_reason("execute", "执行命令") == "")

# ── 3) 硬底线：任何档位都不放开 ─────────────────────
print("=" * 78)
print("【3】硬底线：执行命令恒需确认 + 内核路径地板 + 敏感路径加固")
print("=" * 78)
pm = PolicyManager()
EXEC_EXPECT = {"plan": "deny", "ask": "confirm", "auto": "confirm", "full": "confirm"}
for mode in perm.MODES:
    set_mode(mode)
    d = pm.check("run_local_command", sandbox("整理.bat"))
    check(f"{perm.LABELS[mode]}：执行命令判定 = {EXEC_EXPECT[mode]}（绝不是自动放行）",
          d == EXEC_EXPECT[mode], str(d))
check("执行命令在四档中没有任何一档是 approve（硬底线的机器可判定形式）",
      all(pm.check("run_local_command", sandbox("x.bat")) != "approve" for _ in [set_mode(m) for m in perm.MODES]))

set_mode("full")
check("完全访问档下，沙箱内写盘自动放行（这才是「完全访问」的意义）",
      pm.check("create_local_file", sandbox("a.txt")) == "approve")
check("完全访问档下，沙箱内删除自动放行",
      pm.check("delete_local_file", sandbox("a.txt")) == "approve")
for bad in ("C:\\Windows\\System32\\drivers\\etc\\hosts", "/etc/passwd", "/sys/kernel/x"):
    d = pm.check("create_local_file", bad)
    check(f"内核路径地板：完全访问档也直接拒绝 {bad}", d == "deny", str(d))
for sensitive in ("C:\\Users\\Andy\\.ssh\\id_rsa", "C:\\Users\\Andy\\.aws\\credentials",
                  "D:\\proj\\.env", "D:\\keys\\id_rsa.pub"):
    d = pm.check("modify_local_file", sensitive)
    check(f"本机敏感路径：完全访问档仍需确认（{Path(sensitive).name}）", d == "confirm", str(d))
restore_env()

# ── 4) 立即生效 + 规则只能收紧 ──────────────────────
print("=" * 78)
print("【4】立即生效（无需重启）与「控制面只能收紧」")
print("=" * 78)
set_mode("plan")
check("切到 plan：下一次调用即被档位硬闸拦下",
      "权限拦截" in ft.create_local_file.invoke({"filename": "x.txt", "content": "hi"}),
      ft.create_local_file.invoke({"filename": "x.txt", "content": "hi"})[:40])
set_mode("full")
_r = ft.delete_local_file.invoke({"filename": "不存在_perm_suite_probe.txt"})
check("切到 full：下一次调用不再被档位硬闸拦（进入正常的策略/存在性判定）",
      "权限拦截" not in _r, _r[:60])
set_mode("auto")
check("档位改一个环境变量即可生效（没有缓存层，permission_mode 现读现算）",
      perm.permission_mode() == "auto" and perm.base_decision("write") == "approve")

# 控制面规则：deny 能压过本档 approve，approve 不能放宽本档 deny
pm2 = PolicyManager()
set_mode("full")
before = pm2.check("create_local_file", sandbox("b.txt"))
pm2.set_control_rules([{"path_pattern": "**\\b.txt", "operations": "*", "policy": "deny"}], policy_version=7)
tightened = pm2.check("create_local_file", sandbox("b.txt"))
pm2.clear_control_rules()
set_mode("plan")
loosened = pm2.check("create_local_file", sandbox("c.txt"))
check("完全访问档：控制面 deny 规则能把它压成拒绝（只能更严）",
      before == "approve" and tightened == "deny", f"{before} → {tightened}")
check("计划模式：控制面 approve 规则不能把本档的拒绝放宽（方向单向）",
      loosened == "deny", str(loosened))
check("撤销控制面规则后回到本档判定",
      pm2.check("create_local_file", sandbox("b.txt")) == "deny")
restore_env()

# ── 4b) 存量配置迁移：旧类别级兜底规则不得把高档位拦回去 ──
print("-" * 78)
print("存量升级：policy.json 里的旧「删除/执行命令需审批」规则应被档位模型接管")
print("-" * 78)
import json  # noqa: E402
import tempfile  # noqa: E402

with tempfile.TemporaryDirectory() as tmp:
    legacy_cfg = Path(tmp) / "policy.json"
    legacy_cfg.write_text(json.dumps({
        "approval_rules": [
            {"path_pattern": "*", "operations": "delete_local_file", "policy": "confirm"},
            {"path_pattern": "*", "operations": "run_local_command", "policy": "confirm"},
            {"path_pattern": "C:\\Windows\\*", "operations": "*", "policy": "deny"},
        ]
    }, ensure_ascii=False), encoding="utf-8")   # 落点受控：临时目录，测试结束即删
    migrated = PolicyManager.__new__(PolicyManager)      # 不走单例，独立构造一份
    migrated._config_path = Path(tmp)
    migrated._approval_rules = []
    migrated._classification_rules = []
    migrated._anonymize_rules = {}
    migrated._custom_sensitive_paths = []
    migrated._load_or_init()
    ops = [r.get("operations") for r in migrated._approval_rules]
    check("旧类别级规则被迁移移除（否则高档位形同虚设）",
          "delete_local_file" not in ops and "run_local_command" not in ops, str(ops))
    check("路径级硬底线规则原样保留（迁移只动被档位接管的那两条）",
          any(r.get("path_pattern") == "C:\\Windows\\*" and r.get("policy") == "deny"
              for r in migrated._approval_rules))

# ── 5) 界面矩阵 = 内核策略（承诺与实现不许漂移）──────
print("=" * 78)
print("【5】界面「四档能力对照」表 = core/permission.py 的策略矩阵")
print("=" * 78)
html = (ROOT / "yindun" / "app" / "web" / "index.html").read_text(encoding="utf-8")
table = re.search(r'<table class="permmatrix" id="perm-matrix">([\s\S]*?)</table>', html)
check("界面上存在四档能力对照表（#perm-matrix）", table is not None)
if table:
    body = table.group(1)
    thead = re.search(r"<thead>([\s\S]*?)</thead>", body)
    head = re.findall(r"<th>([^<]+)</th>", thead.group(1))   # 只取表头（行首格也是 <th>，不能混入）
    check("表头列出四档中文名（顺序 = MODES 由紧到松）",
          head[1:] == [perm.LABELS[m] for m in perm.MODES], str(head))
    for op, label in OP_TEXT.items():
        row = re.search(r'data-op="%s">([\s\S]*?)</tr>' % op, body)
        check(f"对照表含「{label}」行", row is not None)
        if not row:
            continue
        cells = re.findall(r'data-decision="(\w+)"[^>]*>([^<]+)<', row.group(1))
        expect = [(EXPECTED[m][op], DECISION_TEXT[EXPECTED[m][op]]) for m in perm.MODES]
        check(f"「{label}」行四档判定与内核一致（{cells}）", cells == expect, f"{cells} != {expect}")

# 文案同源：界面 perm-hint 与内核 HINTS 讲的是同一件事（避免两套说法）
js = (ROOT / "yindun" / "app" / "web" / "app.js").read_text(encoding="utf-8")
for mode in perm.MODES:
    check(f"界面说明文案「{perm.LABELS[mode]}」与内核 HINTS 一致",
          perm.HINTS[mode] in js, perm.HINTS[mode])

print("\n" + "=" * 78)
if failures:
    print(f"❌ 回归失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print("✅ 回归通过：四档矩阵 / 旧值兼容 / 硬底线（执行命令恒需确认 + 内核路径地板 + 敏感路径加固）"
      "/ 立即生效 / 控制面只能收紧 / 界面矩阵与内核一致")
sys.exit(0)
