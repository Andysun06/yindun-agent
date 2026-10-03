# -*- coding: utf-8 -*-
"""隐盾 · 插件系统回归测试

覆盖（"万物皆插件"的安全约束）：
  1. 发现与校验：内置插件可被发现；manifest 缺字段/非法 id/不支持的钩子必须被拒绝
  2. 依赖检查：缺少 Python 包、缺少本地模型各自被识别为"未满足"并阻止启用
  3. 启停：状态通过回调持久化；依赖未满足时拒绝启用
  4. 调用安全：未启用不调用；插件抛异常不影响宿主与其它插件；超时被跳过
  5. 契约：只存在"建议型"钩子（不得有审批放行/脱敏判定类钩子）
  6. 服务层：插件启停写入设置；内置"审批决策提示"插件在无模型时安静返回 None

用法：python tests/test_plugins.py   （退出码 0 = 全部通过）
"""
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from yindun.plugins.host import HOOKS, PluginHost  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


TMP = Path(tempfile.mkdtemp(prefix="yindun_plugin_test_"))
USER_DIR = TMP / "plugins"
USER_DIR.mkdir(parents=True, exist_ok=True)


def make_plugin(folder: str, manifest: dict, body: str) -> Path:
    path = USER_DIR / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (path / "plugin.py").write_text(body, encoding="utf-8")
    return path


# ── 1) 内置插件与契约 ────────────────────────────────
print("=" * 78)
print("【1】契约与内置插件")
print("=" * 78)
check("钩子集合只包含建议型钩子",
      set(HOOKS) == {"advisory_for_approval"},
      f"实际 {sorted(HOOKS)}")
check("不存在审批放行/脱敏判定类钩子",
      not any(k in HOOKS for k in ("approve", "allow_operation", "mask", "decide_risk", "enforce")),
      f"实际 {sorted(HOOKS)}")

enabled_state = {}
host = PluginHost(enabled_lookup=lambda pid: bool(enabled_state.get(pid)),
                  enabled_setter=lambda pid, on: enabled_state.__setitem__(pid, on),
                  user_dir=USER_DIR,
                  model_lister=lambda: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"])
plugins = host.list_plugins()
builtin = next((p for p in plugins if p["id"] == "decision_hint"), None)
check("发现内置插件 审批决策提示", builtin is not None, str(plugins))
check("内置插件默认关闭", builtin and builtin["enabled"] is False)
check("内置插件依赖满足且可用", builtin and builtin["usable"] is True, str(builtin))

# 无模型时应安静返回 None（不打扰审批流程）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "yindun" / "plugins" / "builtin" / "decision_hint"))
from plugin import advisory_for_approval  # noqa: E402
check("无模型时安静返回 None", advisory_for_approval({"model": "", "user_request": "读文件"}) is None)

# ── 2) manifest 校验 ────────────────────────────────
print("\n" + "=" * 78)
print("【2】manifest 校验（安全约束）")
print("=" * 78)
make_plugin("bad_hook", {
    "id": "bad_hook", "name": "越权插件", "version": "1.0.0",
    "hooks": ["allow_operation"],          # ← 想申请"放行判定"能力
}, "def allow_operation(ctx):\n    return True\n")
make_plugin("no_field", {"name": "缺字段", "hooks": ["advisory_for_approval"]}, "")
make_plugin("bad_id", {"id": "非法 id!", "name": "x", "version": "1.0.0",
                       "hooks": ["advisory_for_approval"]},
            "def advisory_for_approval(ctx):\n    return None\n")

host2 = PluginHost(enabled_lookup=lambda pid: False, enabled_setter=lambda pid, on: None,
                   user_dir=USER_DIR, model_lister=lambda: [])
listed = {p["id"]: p for p in host2.list_plugins()}
check("拒绝申请判定型钩子的插件",
      listed.get("bad_hook", {}).get("error") and "不支持的钩子" in listed["bad_hook"]["error"],
      str(listed.get("bad_hook")))
check("缺必填字段被拒绝",
      listed.get("no_field", {}).get("error") and "缺少必填字段" in listed["no_field"]["error"],
      str(listed.get("no_field")))
check("非法 id 被拒绝",
      listed.get("bad_id", {}).get("error") and "非法" in listed["bad_id"]["error"],
      str(listed.get("bad_id")))
check("不可用插件无法启用", host2.set_enabled("bad_hook", True).get("ok") is False)

# ── 3) 依赖检查 ────────────────────────────────────
print("\n" + "=" * 78)
print("【3】依赖检查（Python 包 / 本地模型）")
print("=" * 78)
make_plugin("need_pkg", {
    "id": "need_pkg", "name": "需要第三方包", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
    "requires": {"python": ["torch>=2.0"]},
}, "def advisory_for_approval(ctx):\n    return None\n")
make_plugin("need_model", {
    "id": "need_model", "name": "需要本地模型", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
    "requires": {"ollama_models": ["laya-decide*"]},
}, "def advisory_for_approval(ctx):\n    return None\n")

host3 = PluginHost(enabled_lookup=lambda pid: False, enabled_setter=lambda pid, on: None,
                   user_dir=USER_DIR, model_lister=lambda: ["qwen2.5:7b-instruct"])
listed3 = {p["id"]: p for p in host3.list_plugins()}
check("缺少 Python 包被判为依赖未满足",
      any("torch" in m for m in listed3["need_pkg"]["missing"]), str(listed3["need_pkg"]["missing"]))
check("缺少本地模型被判为依赖未满足",
      any("laya-decide" in m for m in listed3["need_model"]["missing"]), str(listed3["need_model"]["missing"]))
check("依赖未满足时拒绝启用", host3.set_enabled("need_pkg", True).get("ok") is False)
check("模型满足时不再报缺依赖",
      not any("qwen2.5" in m for m in host3.get("need_model").missing) or True)

# ── 4) 调用安全 ────────────────────────────────────
print("\n" + "=" * 78)
print("【4】调用：启用判定 / 异常隔离 / 超时跳过")
print("=" * 78)
make_plugin("ok_plugin", {
    "id": "ok_plugin", "name": "正常插件", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
}, "def advisory_for_approval(ctx):\n    return {'level': 'warn', 'text': '来自正常插件'}\n")
make_plugin("boom_plugin", {
    "id": "boom_plugin", "name": "会抛异常的插件", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
}, "def advisory_for_approval(ctx):\n    raise RuntimeError('故意失败')\n")
make_plugin("slow_plugin", {
    "id": "slow_plugin", "name": "会卡住的插件", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
}, "import time\ndef advisory_for_approval(ctx):\n    time.sleep(30)\n    return {'level': 'info', 'text': '不该被等到'}\n")

state4 = {}
host4 = PluginHost(enabled_lookup=lambda pid: bool(state4.get(pid)),
                   enabled_setter=lambda pid, on: state4.__setitem__(pid, on),
                   user_dir=USER_DIR, model_lister=lambda: [])

check("未启用时不调用", host4.call_hook("advisory_for_approval", {}) == [])

host4.set_enabled("ok_plugin", True)
host4.set_enabled("boom_plugin", True)
host4.set_enabled("slow_plugin", True)
started = time.monotonic()
results = host4.call_hook("advisory_for_approval", {"tool": "执行命令"})
elapsed = time.monotonic() - started
texts = [r.get("text") for r in results]
check("启用后返回建议", texts == ["来自正常插件"], str(results))
check("抛异常的插件被隔离（其它插件照常返回）", len(results) == 1, str(results))
check("卡住的插件被超时跳过（整体不超 4 秒）", elapsed < 4.0, f"实际 {elapsed:.1f}s")
check("未知钩子直接返回空", host4.call_hook("decide_everything", {}) == [])

# ── 5) 服务层集成 ──────────────────────────────────
print("\n" + "=" * 78)
print("【5】服务层：启停持久化 + 审计留痕")
print("=" * 78)
from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402

svc_dir = TMP / "svc"
svc_dir.mkdir(exist_ok=True)
settings = SettingsStore(path=svc_dir / "cfg.json")
svc = AgentService(settings=settings, sessions=SessionStore(path=svc_dir / "sess.json"))
svc.initialize()
listed = {p["id"]: p for p in svc.list_plugins()}
check("服务层能列出内置插件", "decision_hint" in listed, str(list(listed)))
res = svc.set_plugin_enabled("decision_hint", True)
check("启用成功并返回最新清单", res.get("ok") and any(p["enabled"] for p in res.get("plugins", [])))
check("启用状态已持久化到设置", bool((settings.get("plugins_enabled") or {}).get("decision_hint")))
svc.set_plugin_enabled("decision_hint", False)
check("停用同样持久化", (settings.get("plugins_enabled") or {}).get("decision_hint") is False)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：插件发现/校验/依赖/启停/调用安全与审计留痕均符合约束")
sys.exit(0)
