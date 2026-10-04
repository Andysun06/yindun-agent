# -*- coding: utf-8 -*-
"""隐盾 · laya 风险分级插件回归测试

覆盖（"可选接入 laya、不接入回退正则"的安全约束）：
  1. 契约：内置插件被发现；requires 为空（没装 laya 也能启用）；声明可编辑规则文件
  2. 正则回退分级：删除类→高危 warn；写入类→中危 warn；只读→低危 info；规则为空→如实说"未给出结论"
  3. 规则可编辑：首次使用自动生成默认规则；热改立即生效；非法行跳过并告警、其余照常
  4. laya 路径（stub 假模块）：概率→级别；推理异常→安静回退正则、不阻断审批
  5. 宿主链路：启用→call_hook 出提示（kind=advisory、带 plugin_id）；停用→立刻收回

用法：python tests/test_laya_risk.py   （退出码 0 = 全部通过）
"""
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.plugins.host import PluginHost  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def new_host():
    enabled = {}
    host = PluginHost(enabled_lookup=lambda pid: bool(enabled.get(pid)),
                      enabled_setter=lambda pid, on: enabled.__setitem__(pid, on),
                      user_dir=TMP / "plugins",
                      data_root=TMP / "data",
                      model_lister=lambda: ["qwen2.5:7b-instruct"])
    return host, enabled


TMP = Path(tempfile.mkdtemp(prefix="yindun_laya_test_"))
CTX_DELETE = {"tool": "run_local_command", "path": "",
              "args": {"command": "rm -rf /data/backups"}, "user_request": "帮我清理一下磁盘"}
CTX_WRITE = {"tool": "write_local_file", "path": "E:/notes/a.md",
             "args": {"content": "x"}, "user_request": "把要点写进笔记"}
CTX_READ = {"tool": "read_local_file", "path": "E:/notes/a.md", "args": {}, "user_request": "读一下笔记"}

# ── 1) 发现与契约 ────────────────────────────────
print("=" * 78)
print("【1】发现与契约（不装 laya 也必须可用）")
print("=" * 78)
host, enabled = new_host()
plugins = {p["id"]: p for p in host.list_plugins()}
item = plugins.get("laya_risk")
check("内置插件 laya_risk 被发现", item is not None, str(list(plugins)))
check("requires 为空：没装 laya 也算依赖满足",
      item is not None and item["usable"] is True and not item["missing"], str(item))
check("挂在建议型钩子上（不参与判定）",
      item is not None and item["hooks"] == ["advisory_for_approval"], str(item))
check("声明了可编辑的规则与后端文件",
      item is not None and [f["name"] for f in item["config_files"]] == ["risk_rules.txt", "backend.txt"],
      str(item.get("config_files") if item else None))
check("内置插件默认关闭", item is not None and item["enabled"] is False)

# ── 2) 宿主链路：启用→出提示，停用→收回 ──────────
print("\n" + "=" * 78)
print("【2】宿主链路：advisory 进弹窗、跟着开关走")
print("=" * 78)
res = host.set_enabled("laya_risk", True)
check("未装 laya 也能启用（正则回退就是设计行为）", res.get("ok") is True, str(res))
advs = host.call_hook("advisory_for_approval", dict(CTX_DELETE))
check("启用后出分级提示（高危）", len(advs) == 1 and advs[0]["level"] == "warn"
      and "高危" in advs[0]["text"], str(advs))
check("提示标明来源是正则规则（没装 laya 绝不冒充模型）",
      bool(advs) and "正则规则" in advs[0]["text"], str(advs))
check("宿主补齐了 plugin_id 与 kind",
      bool(advs) and advs[0]["plugin_id"] == "laya_risk" and advs[0]["kind"] == "advisory",
      str(advs))
adv_r = host.call_hook("advisory_for_approval", dict(CTX_READ))
check("只读操作提示低危（info）", len(adv_r) == 1 and adv_r[0]["level"] == "info"
      and "低危" in adv_r[0]["text"], str(adv_r))
adv_w = host.call_hook("advisory_for_approval", dict(CTX_WRITE))
check("写入操作提示中危（warn）", len(adv_w) == 1 and adv_w[0]["level"] == "warn"
      and "中危" in adv_w[0]["text"], str(adv_w))
host.set_enabled("laya_risk", False)
check("停用后立刻收回（advisory 不再出现）",
      host.call_hook("advisory_for_approval", dict(CTX_DELETE)) == [], str(host.call_hook("advisory_for_approval", dict(CTX_DELETE))))
host.set_enabled("laya_risk", True)

# ── 3) 规则可编辑（经宿主配置通道写入） ──────────
print("\n" + "=" * 78)
print("【3】规则可编辑：默认自带、热改生效、坏行不拖垮好行")
print("=" * 78)
first = host.read_config_file("laya_risk", "risk_rules.txt")
check("首次调用后自动生成了默认规则（带注释、开箱即用）",
      first["ok"] and first["exists"] and "rm -rf" in first["text"], str(first)[:120])
wr = host.write_config_file("laya_risk", "risk_rules.txt",
                            "高危|delete_local_file|代号删除\n中危|backup\n危险|这一行级别非法\n高危|re:[非法正则|坏行\n")
check("写规则文件成功", wr["ok"] is True, str(wr))
adv_x = host.call_hook("advisory_for_approval",
                       {"tool": "delete_local_file", "path": "", "args": {}, "user_request": ""})
check("新规则热改立即生效（命中「代号删除」）",
      len(adv_x) == 1 and "高危" in adv_x[0]["text"] and "代号删除" in adv_x[0]["text"], str(adv_x))
check("非法行被跳过而非报错（其余规则照常）", len(adv_x) == 1, str(adv_x))
host.write_config_file("laya_risk", "risk_rules.txt", "")
adv_empty = host.call_hook("advisory_for_approval", dict(CTX_READ))
check("规则清空时如实说「未给出结论」，不编造安全",
      len(adv_empty) == 1 and "未给出结论" in adv_empty[0]["text"], str(adv_empty))

# ── 4) 自检 ─────────────────────────────────────
print("\n" + "=" * 78)
print("【4】自检：钩子函数齐备")
print("=" * 78)
sc = host.selfcheck()
laya_check = {p["id"]: p for p in sc["plugins"]}.get("laya_risk")
check("自检通过（advisory_for_approval 函数存在）",
      laya_check is not None and laya_check["ok"] is True, str(laya_check))

# ── 4.5) 后端选择 backend.txt ────────────────────
print("\n" + "=" * 78)
print("【4.5】backend.txt：regex 强制只用正则；laya 只用模型（不可用则不提示）")
print("=" * 78)
host.write_config_file("laya_risk", "backend.txt", "regex\n")
adv_b = host.call_hook("advisory_for_approval", dict(CTX_READ))
check("backend=regex：本机没装 laya 也照样给正则分级",
      len(adv_b) == 1 and "正则规则" in adv_b[0]["text"], str(adv_b))
host.write_config_file("laya_risk", "backend.txt", "laya\n")
adv_c = host.call_hook("advisory_for_approval", dict(CTX_READ))
check("backend=laya 且模型不可用：不提示（绝不偷偷拿正则冒充模型）",
      adv_c == [], str(adv_c))
host.write_config_file("laya_risk", "backend.txt", "auto\n")

# ── 5) laya 路径（注入假模块，验证接入后的行为） ──
print("\n" + "=" * 78)
print("【5】laya 已接入时：走模型打分；推理失败时回退正则")
print("=" * 78)
spec = importlib.util.spec_from_file_location(
    "laya_risk_direct", ROOT / "yindun" / "plugins" / "builtin" / "laya_risk" / "plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
plugin.PLUGIN_DATA_DIR = str(TMP / "direct")   # 独立数据目录，与宿主副本互不干扰
plugin.advisory_for_approval(dict(CTX_DELETE))  # 先生成默认规则文件

fake = types.ModuleType("laya")
answers = {"p": 0.85}


class FakeRouter:
    def predict(self, state, questions):
        if answers["p"] < 0:
            raise RuntimeError("模型炸了")
        return {"answers": {"risk": {"noul": answers["p"]}}, "routing": {"model": "fake"}}


fake.Router = FakeRouter
sys.modules["laya"] = fake
plugin._ROUTER = None          # 清掉"之前探测失败"的缓存，模拟 laya 刚装上
plugin._ROUTER_FAILED = False
answers["p"] = 0.85
out = plugin.advisory_for_approval(dict(CTX_READ))   # 只读操作，但模型说危险 → 信模型
check("接入 laya 后走模型、标注来源为 laya",
      out and "laya 模型" in out["text"] and out["level"] == "warn" and "高危" in out["text"], str(out))
answers["p"] = 0.15
out = plugin.advisory_for_approval(dict(CTX_DELETE))
check("模型概率低时给 info 低危（模型说了算，不按正则加戏）",
      out and out["level"] == "info" and "低危" in out["text"], str(out))
answers["p"] = -1.0
out = plugin.advisory_for_approval(dict(CTX_DELETE))
check("模型推理异常→回退正则（rm -rf 仍给高危，流程不断）",
      out and "正则规则" in out["text"] and out["level"] == "warn" and "高危" in out["text"], str(out))
out2 = plugin.advisory_for_approval({"tool": "", "path": "", "args": {}})
check("空上下文安静返回 None", out2 is None, str(out2))

print("\n" + "=" * 78)
if failures:
    print(f"❌ 失败 {len(failures)} 项：{failures}")
    raise SystemExit(1)
print("✅ laya 风险分级插件回归：全部通过")
