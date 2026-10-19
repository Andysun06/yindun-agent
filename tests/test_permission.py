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

# ── 3.5) 可读写范围（沙箱）降级为"默认可读写范围"：越界按档位判定 + 必留痕 ──
print("-" * 78)
print("【3.5】越界判定：plan 拒绝 / ask 人工确认 / auto·full 放行（留审计 + 结果标注）")
print("-" * 78)
import tempfile  # noqa: E402

from yindun.core.audit_log import AuditLog  # noqa: E402

_rng_tmp = Path(tempfile.mkdtemp(prefix="yindun_perm_range_"))
_rng_in = _rng_tmp / "box"
_rng_out = _rng_tmp / "outside"
_rng_in.mkdir(parents=True, exist_ok=True)
_rng_out.mkdir(parents=True, exist_ok=True)
_saved_sbx_env = os.environ.get("SANDBOX_PATH")
os.environ["SANDBOX_PATH"] = str(_rng_in)          # 本段用自己的临时范围，退出时还原

# ★ 审计落点也要隔离：本段会产生"越界访问"审计条目并带**真实路径**（临时目录路径），
#   写进仓库 audit_logs/ 会污染"落盘敏感明文"扫描（隐私套件扫的就是这个目录）——
#   测试数据不该进产品审计链。同一条链的内存条目照常可断言，退出时还原落点。
_audit_probe = AuditLog()
_saved_audit_dir = _audit_probe._storage_path
_audit_probe._storage_path = _rng_tmp / "audit_logs"

def range_probe(**kw):
    return ft.create_local_file.invoke(dict({"filename": "probe.txt", "content": "hi"}, **kw))

try:
    # ① 策略层：越界 × 四档（同一张档位矩阵，取更严）
    OUTSIDE_EXPECT = {"plan": "deny", "ask": "confirm", "auto": "approve", "full": "approve"}
    for mode, expect in OUTSIDE_EXPECT.items():
        set_mode(mode)
        d = PolicyManager().check("create_local_file", str(_rng_out / "a.txt"))
        check(f"{perm.LABELS[mode]}：越界写盘判定 = {expect}（不是「一律拒绝」）", d == expect, str(d))
    set_mode("plan")
    check("plan 档越界是 deny 而不是 confirm —— 本档没有审批流，不该弹确认",
          PolicyManager().check("create_local_file", str(_rng_out / "a.txt")) == "deny")
    check("plan 档越界读也被拒（计划模式不越界访问）",
          PolicyManager().check("list_local_files", str(_rng_out)) == "deny")
    check("越界执行命令在任何一档都不是 approve（命令的硬底线不被越界规则放宽）",
          all(PolicyManager().check("run_local_command", str(_rng_out)) != "approve"
              for _ in [set_mode(m) for m in perm.MODES]))

    # ② 硬地板：越界 + 系统目录/凭据类，任何档位都不松
    for mode in perm.MODES:
        set_mode(mode)
        check(f"{perm.LABELS[mode]}：越界到系统目录仍直接拒绝",
              PolicyManager().check("create_local_file", "C:\\Windows\\System32\\x.txt") == "deny")
    set_mode("full")
    check("完全访问 + 越界到凭据目录仍需确认（地板与档位无关）",
          PolicyManager().check("modify_local_file", "C:\\Users\\Andy\\.ssh\\id_rsa") == "confirm")

    # ③ 工具层：没有宿主令牌 → 越界一律拒绝（不静默回退），且话术可行动
    #    · 读操作：四档都要回「可读写范围之外」（以前是"静默回退到默认目录"，最误导）
    for mode in perm.MODES:
        set_mode(mode)
        r_read = ft.list_local_files.invoke({"target_directory": str(_rng_out)})
        check(f"{perm.LABELS[mode]}：越界读被拒且不静默回退",
              r_read.startswith("❌") and "可读写范围之外" in r_read, r_read[:40])
    #    · 变更操作：计划模式由档位硬闸先拦（本档不该有审批流），其余三档回越界话术
    for mode in perm.MODES:
        set_mode(mode)
        r = range_probe(target_directory=str(_rng_out))
        if mode == "plan":
            check("计划模式：越界写盘被档位硬闸拦下（只读分析，不产生任何变更）",
                  r.startswith("❌") and "权限拦截" in r, r[:40])
        else:
            check(f"{perm.LABELS[mode]}：工具直调（无宿主令牌）越界被拒",
                  r.startswith("❌") and "可读写范围之外" in r, r[:40])
    check("拒绝话术给出可行动的下一步（设置项 sandbox_dir / 换档位）",
          ("sandbox_dir" in r) and ("可读写范围" in r), r[:80])
    check("被拒的越界调用没有留下文件（拒绝是真实的，不是「说给模型听」的）",
          not (_rng_out / "probe.txt").exists())

    # ④ 模型自授权：参数里塞"允许越界"必须被丢弃（语言层参数是模型可写的）
    set_mode("full")
    r_self = range_probe(target_directory=str(_rng_out), _outside_ok=True, outside_ok=True)
    check("★ 模型塞 _outside_ok=True 不能自授权（工具层只认宿主令牌）",
          r_self.startswith("❌") and not (_rng_out / "probe.txt").exists(), r_self[:40])

    # ⑤ 宿主授予令牌 → 放行，且结果里带"在可读写范围之外"标注 + 审计有条目
    _ac_before = len(AuditLog().get_entries({"event_type": "access_control"}))
    ft.grant_outside_access(str(_rng_out))
    try:
        r_ok = range_probe(target_directory=str(_rng_out))
    finally:
        ft.revoke_outside_grants()
    check("★ 宿主授予令牌后越界写盘放行并真实落盘",
          r_ok.startswith("✅") and (_rng_out / "probe.txt").exists(), r_ok[:60])
    check("★ 放行结果里明确写着「本次操作在可读写范围之外：<路径>」（不允许静默放行）",
          "本次操作在可读写范围之外" in r_ok and str(_rng_out) in r_ok, r_ok[:120])
    _ac_entries = AuditLog().get_entries({"event_type": "access_control"})
    _ac_new = _ac_entries[-max(1, len(_ac_entries) - _ac_before):] if _ac_entries else []
    _hit = [e for e in _ac_new
            if (e.details or {}).get("outside_sandbox") and (e.details or {}).get("approved")
            and (e.details or {}).get("tool_name") == "create_local_file"]
    check("★ 越界放行写入了 access_control 审计（含工具名/真实路径/档位/判定）",
          bool(_hit), str([(e.details or {}).get("decision") for e in _ac_new]))
    if _hit:
        d = _hit[-1].details or {}
        check("审计详情含真实路径、当前范围与档位",
              os.path.normcase(d.get("path", "")) == os.path.normcase(os.path.realpath(str(_rng_out)))
              and bool(d.get("sandbox_root"))
              and d.get("permission") == "full" and d.get("decision") == "outside_allowed",
              str(d))
    check("令牌用完即撤：撤销后同一次会话再调仍然被拒",
          (ft.revoke_outside_grants() or True) and range_probe(target_directory=str(_rng_out)).startswith("❌"))

    # ⑥ realpath 兜底不放松：目录内符号链接指向外部时，落点校验仍拒绝（与档位无关）
    _link_out = _rng_tmp / "link_out"          # 范围之外的另一个目录
    _link_out.mkdir(exist_ok=True)
    try:
        _link = _rng_in / "escape"              # 指向范围外的链接
        os.symlink(str(_link_out), str(_link), target_is_directory=True)
        set_mode("full")
        ft.grant_outside_access(str(_rng_in))
        try:
            # 写入 escape/x.txt：base_dir 在范围内（有令牌），但 realpath 落到范围外 → 必须拒绝
            r_link = ft.create_local_file.invoke(
                {"filename": "inner.txt", "content": "x",
                 "target_directory": str(_rng_in / "escape")})
        finally:
            ft.revoke_outside_grants()
        check("符号链接逃逸仍被拒（越界放行只覆盖用户点的那个目录，不覆盖链接指向处）",
              r_link.startswith("❌"), r_link[:80])
    except (OSError, NotImplementedError):
        print("  [ℹ️] 符号链接创建被系统拒绝（Windows 需要开发者模式/管理员）——跳过该用例")

    # ⑦ 可读写范围改配置后立即生效（设置键 sandbox_dir > 环境变量）
    _cfg_dir = _rng_tmp / "configured"
    _cfg_dir.mkdir(exist_ok=True)
    _env_before = os.environ.get("SANDBOX_PATH")
    perm.register_sandbox_settings_source(lambda: str(_cfg_dir))
    try:
        set_mode("full")
        check("★ 设置里的 sandbox_dir 优先于环境变量（sandbox_root 立即返回它）",
              os.path.normcase(ft.sandbox_root()) == os.path.normcase(str(_cfg_dir)),
              f"{ft.sandbox_root()} != {_cfg_dir}")
        check("新范围**内**的写盘按沙箱内语义判定（完全访问档 = 自动放行，无需确认）",
              PolicyManager().check("create_local_file", str(_cfg_dir / "a.txt")) == "approve")
        check("旧范围（原环境变量目录）此时变成「范围之外」：档位判定随之变化",
              PolicyManager().check("create_local_file", str(_rng_out / "a.txt")) == "approve"
              and PolicyManager().check("run_local_command", str(_rng_out)) != "approve")
        r_cfg = ft.create_local_file.invoke(
            {"filename": "cfg.txt", "content": "ok", "target_directory": str(_cfg_dir)})
        check("工具侧同样立即跟随新范围（不再认旧环境变量）",
              r_cfg.startswith("✅") and (_cfg_dir / "cfg.txt").exists(), r_cfg[:60])
    finally:
        perm.register_sandbox_settings_source(None)      # 注销：不影响后续用例
    check("注销后回到环境变量解析（读取器是「每次现读」，不是快照）",
          os.path.normcase(ft.sandbox_root()) == os.path.normcase(str(_rng_in)),
          ft.sandbox_root())
finally:
    _audit_probe._storage_path = _saved_audit_dir      # 还原审计落点（别把测试数据留在产品链里）
    if _saved_sbx_env is None:
        os.environ.pop("SANDBOX_PATH", None)
    else:
        os.environ["SANDBOX_PATH"] = _saved_sbx_env
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

# ── 4c) 会话级档位：设置页那份是「新对话默认」，每个会话可单独覆盖 ──
print("-" * 78)
print("会话级档位：覆盖优先于默认 · 空值回到默认 · 切会话即换档（不串味）")
print("-" * 78)
from yindun.app.session_store import SessionStore  # noqa: E402

with tempfile.TemporaryDirectory() as tmp2:
    store = SessionStore(Path(tmp2) / "sessions.json")
    a = store.create("只读会话")
    b = store.create("动手会话")
    check("新会话默认不覆盖（get_permission 为 None = 跟随设置默认）",
          store.get_permission(a) is None and store.get_permission(b) is None)
    check("按会话写档位", store.set_permission(a, "plan") and store.set_permission(b, "full"))
    check("各会话互不影响（只读的那个不会因为另一个放开了而放开）",
          store.get_permission(a) == "plan" and store.get_permission(b) == "full")
    check("空值 = 清除覆盖、回到跟随默认", store.set_permission(a, None) and store.get_permission(a) is None)
    check("对不存在的会话写入明确失败（不静默创建）", store.set_permission("no-such-session", "plan") is False)
    store.set_permission(a, "auto")

    # 落盘 → 重新加载：覆盖值必须还在（重启后不能悄悄回到默认）
    store.save()
    store2 = SessionStore(Path(tmp2) / "sessions.json")
    store2.load()
    check("覆盖值加密落盘并能在重新加载后读回",
          store2.get_permission(a) == "auto" and store2.get_permission(b) == "full",
          f"{store2.get_permission(a)} / {store2.get_permission(b)}")

    # 与内核的衔接：生效档位 = 会话覆盖 ?? 设置默认
    class _Settings:
        data = {"permission": "ask"}

        def get(self, key, default=None):
            return self.data.get(key, default)

        def set(self, key, value):
            self.data[key] = value

    from yindun.app.agent_service import AgentService  # noqa: E402
    svc = AgentService.__new__(AgentService)      # 只验证档位解析，不启动整个服务
    svc.settings = _Settings()
    svc.sessions = store2
    svc.current_session_id = lambda: a
    check("生效档位取会话覆盖（auto），不是设置默认（ask）",
          svc.effective_permission()["effective"] == "auto", str(svc.effective_permission()))
    svc.current_session_id = lambda: "no-such-session"
    check("没有会话时回落到设置默认（不给「无会话」状态放行）",
          svc.effective_permission()["effective"] == "ask", str(svc.effective_permission()))

print("\n" + "=" * 78)
if failures:
    print(f"❌ 回归失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print("✅ 回归通过：四档矩阵 / 旧值兼容 / 硬底线（执行命令恒需确认 + 内核路径地板 + 敏感路径加固）"
      "/ 立即生效 / 控制面只能收紧 / 界面矩阵与内核一致")
sys.exit(0)
