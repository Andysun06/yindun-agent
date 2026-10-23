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
import time
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
# 审批弹窗给的是显示名（删除文件）；正则规则按内部工具名匹配——两个字段都要进 haystack。
CTX_DELETE_DISPLAY = {"tool": "删除文件", "tool_id": "delete_local_file",
                      "path": "E:/隐盾演示/客户名单演示.txt", "args": {},
                      "user_request": "把这个文件删除"}
CTX_WRITE = {"tool": "write_local_file", "path": "E:/notes/a.md",
             "args": {"content": "x"}, "user_request": "把要点写进笔记"}
CTX_READ = {"tool": "read_local_file", "path": "E:/notes/a.md", "args": {}, "user_request": "读一下笔记"}

# ── 1) 发现与契约 ────────────────────────────────
print("=" * 78)
print("【1】发现与契约（不装 laya 也必须可用）")
print("=" * 78)
host, enabled = new_host()
# 本机装没装 laya 不能影响这些断言的走向：先把后端钉死在正则；
# laya 路径在第【5】节用注入的假模块单独测（测试不依赖开发机环境）。
seed = TMP / "data" / "laya_risk"
seed.mkdir(parents=True, exist_ok=True)
(seed / "backend.txt").write_text("regex\n", encoding="utf-8")
plugins = {p["id"]: p for p in host.list_plugins()}
item = plugins.get("laya_risk")
check("内置插件 laya_risk 被发现", item is not None, str(list(plugins)))
check("requires 为空：没装 laya 也算依赖满足",
      item is not None and item["usable"] is True and not item["missing"], str(item))
check("挂在建议型钩子上（不参与判定）",
      item is not None and item["hooks"] == ["advisory_for_approval"], str(item))
check("声明了可编辑的规则文件（并新增「托管」的接入配置，仍在清单里可读）",
      item is not None
      and [f["name"] for f in item["config_files"]] == ["laya_source.json", "risk_rules.txt", "backend.txt"],
      str(item.get("config_files") if item else None))
check("laya 接入配置标为 managed（由设置页维护，不在卡片里给裸文本编辑）",
      item is not None and any(f.get("managed") for f in item["config_files"]
                               if f["name"] == "laya_source.json"))
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
check("提示标明来源是正则规则（后端钉在正则时不冒充模型）",
      bool(advs) and "正则规则" in advs[0]["text"], str(advs))
check("宿主补齐了 plugin_id 与 kind",
      bool(advs) and advs[0]["plugin_id"] == "laya_risk" and advs[0]["kind"] == "advisory",
      str(advs))
adv_disp = host.call_hook("advisory_for_approval", dict(CTX_DELETE_DISPLAY))
check("显示名「删除文件」+ 内部 tool_id 仍判高危（审批 payload 必须带 tool_id）",
      len(adv_disp) == 1 and adv_disp[0]["level"] == "warn"
      and "高危" in adv_disp[0]["text"], str(adv_disp))
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
check("backend=regex：强制正则分级（与本机是否装 laya 无关）",
      len(adv_b) == 1 and "正则规则" in adv_b[0]["text"], str(adv_b))
# 显式模拟"laya 不可用"：sys.modules 置 None 会让 import laya 抛 ImportError——
# 本机真装了 laya 时，测试也必须能验证"模型不可用就绝不偷偷拿正则冒充"
_saved_laya = sys.modules.get("laya", "MISSING")
sys.modules["laya"] = None
host.write_config_file("laya_risk", "backend.txt", "laya\n")
adv_c = host.call_hook("advisory_for_approval", dict(CTX_READ))
check("backend=laya 且模型不可用：不提示（绝不偷偷拿正则冒充模型）",
      adv_c == [], str(adv_c))
if _saved_laya == "MISSING":
    sys.modules.pop("laya", None)
else:
    sys.modules["laya"] = _saved_laya
host.write_config_file("laya_risk", "backend.txt", "auto\n")

# ★ 旧版 backend.txt 必须**语义等价**映射到新的三级接入（V3.5.3 起由 laya_source.json 主导）：
#   旧 `laya` 是"只用模型、不可用就不提示"（不回退），映射成自动回退等于偷偷改了老用户的行为。
from yindun.plugins.builtin.laya_risk import plugin as _laya_plugin  # noqa: E402


def _legacy_cfg(kind: str):
    """只留 backend.txt 时新三档解析出的 (source, auto)。临时数据目录隔离，不碰真实配置。"""
    with tempfile.TemporaryDirectory() as td:
        saved = _laya_plugin.PLUGIN_DATA_DIR
        _laya_plugin.PLUGIN_DATA_DIR = td
        try:
            (Path(td) / "backend.txt").write_text(kind + "\n", encoding="utf-8")
            cfg = _laya_plugin._source_config()
            return cfg["source"], cfg["auto"]
        finally:
            _laya_plugin.PLUGIN_DATA_DIR = saved


check("旧版 backend.txt 的语义被等价映射（laya=只用模型不回退 · regex=强制正则 · auto=自动回退）",
      _legacy_cfg("laya") == ("local", False)
      and _legacy_cfg("regex") == ("regex", True)
      and _legacy_cfg("auto") == ("local", True),
      f"{_legacy_cfg('laya')} / {_legacy_cfg('regex')} / {_legacy_cfg('auto')}")

# ── 5) laya 路径（注入假模块，验证接入后的行为） ──
print("\n" + "=" * 78)
print("【5】laya 已接入时：走模型打分；推理失败时回退正则")
print("=" * 78)
spec = importlib.util.spec_from_file_location(
    "laya_risk_direct", ROOT / "yindun" / "plugins" / "builtin" / "laya_risk" / "plugin.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
plugin.PLUGIN_DATA_DIR = str(TMP / "direct")   # 独立数据目录，与宿主副本互不干扰
# 先生成默认规则文件：用 regex 钉住，避免这一次真的去加载 laya（本机可能已安装）
(TMP / "direct").mkdir(parents=True, exist_ok=True)
(TMP / "direct" / "backend.txt").write_text("regex\n", encoding="utf-8")
plugin.advisory_for_approval(dict(CTX_DELETE))
(TMP / "direct" / "backend.txt").write_text("auto\n", encoding="utf-8")

fake = types.ModuleType("laya")
answers = {"p": 0.85}


class FakeRouter:
    """桩只替"模型返回什么"；问句键与 pin 的 checkpoint 也要跟着生产代码走
    （V3.5.8 起问句键是 harm、并显式传 model=multilingual，桩必须接受该参数，
    否则真实签名变化会被桩掩盖成"通过"）。"""

    def __init__(self):
        self.seen_models = []

    def predict(self, state, questions, model=None):
        self.seen_models.append(model)
        if "harm" not in questions:
            raise AssertionError("问句键应为 harm（V3.5.8 起问的是动作危害，不是越权）")
        if answers["p"] < 0:
            raise RuntimeError("模型炸了")
        return {"answers": {"harm": {"noul": answers["p"]}}, "routing": {"model": model or "auto"}}


fake.Router = FakeRouter
FakeRouter_inst = FakeRouter()
fake.Router = lambda *a, **k: FakeRouter_inst
sys.modules["laya"] = fake
plugin._ROUTER = None          # 清掉"之前探测失败"的缓存，模拟 laya 刚装上
plugin._ROUTER_FAILED = False
answers["p"] = 0.85
out = plugin.advisory_for_approval(dict(CTX_READ))   # 只读操作，但模型说危险 → 信模型
check("★ 固定使用 multilingual checkpoint（不钉的话纯 ASCII 输入会去用本机没下全的英文权重）",
      FakeRouter_inst.seen_models and all(m == "multilingual" for m in FakeRouter_inst.seen_models),
      str(FakeRouter_inst.seen_models))
check("接入 laya 后走模型、标注来源为 laya",
      out and "laya 模型" in out["text"] and out["level"] == "warn" and "高危" in out["text"], str(out))
# ★ 注意：这几条要用**内置规则未覆盖**的工具名（如 analyze_project）。
#   若还在 delete_local_file 上测，正则规则会判高危并压过模型结论（V3.5.9 的"取更严一侧"），
#   那测到的就不是"模型阈值"而是"合并规则"了。
CTX_FREE = {"tool": "analyze_project", "path": "D:\项目", "args": {}}
answers["p"] = 0.15
out = plugin.advisory_for_approval(dict(CTX_FREE))
check("概率落在中危区间（0.10~0.30）→ warn 中危（规则未覆盖时模型说了算）",
      out and out["level"] == "warn" and "中危" in out["text"], str(out))
answers["p"] = 0.02
out = plugin.advisory_for_approval(dict(CTX_FREE))
check("概率很低 → info 低危，且文案点明已知边界（低危不等于安全）",
      out and out["level"] == "info" and "低危" in out["text"] and "判别有限" in out["text"], str(out))
answers["p"] = 0.55
out = plugin.advisory_for_approval(dict(CTX_FREE))
check("概率 ≥0.30 → 高危（阈值取自开发集分布，不是拍的）",
      out and out["level"] == "warn" and "高危" in out["text"], str(out))
answers["p"] = -1.0
out = plugin.advisory_for_approval(dict(CTX_DELETE))
check("模型推理异常→回退正则（rm -rf 仍给高危，流程不断）",
      out and "正则规则" in out["text"] and out["level"] == "warn" and "高危" in out["text"], str(out))
class SlowRouter:   # 模拟首次前向的慢推理（实测本机 20~35s）
    def predict(self, state, questions, model=None):
        time.sleep(2)
        return {"answers": {"harm": {"noul": 0.99}}}
plugin._LAYA_WAIT = 0.5          # 测试内把内部等待压短
plugin._ROUTER = SlowRouter()
t0 = time.monotonic()
out = plugin.advisory_for_approval(dict(CTX_READ))
elapsed = time.monotonic() - t0
check("推理超时（模拟首次前向的慢）→主动回退正则，弹窗仍有提示、不拖到宿主超时",
      out and "正则规则" in out["text"] and elapsed < 1.5, f"{elapsed:.2f}s {out}")
plugin._LAYA_WAIT = 25.0
out2 = plugin.advisory_for_approval({"tool": "", "path": "", "args": {}})
check("空上下文安静返回 None", out2 is None, str(out2))

# ── 取更严的一侧（V3.5.9）：模型判低危但正则判高危时，提示**不能变松** ──
#   反直觉的旧行为：模型可用时正则根本不跑，于是"装了 laya"反而把删除类提示从高危降成中危/低危。
import tempfile as _tf, json as _json, importlib.util as _ilu  # noqa: E402
from pathlib import Path as _P  # noqa: E402
_tmp2 = _P(_tf.mkdtemp(prefix="laya_merge_"))
_spec2 = _ilu.spec_from_file_location("lp_merge", _P(plugin.__file__).resolve())
_lp2 = _ilu.module_from_spec(_spec2); _spec2.loader.exec_module(_lp2)
_lp2.PLUGIN_DATA_DIR = str(_tmp2)
(_tmp2 / "risk_rules.txt").write_text("高危|delete_local_file|删除本地文件" + chr(10), encoding="utf-8")
(_tmp2 / "laya_source.json").write_text(_json.dumps({"source": "local", "auto": True}), encoding="utf-8")


class _LowRouter:
    def predict(self, state, questions, model=None):
        return {"answers": {"harm": {"noul": 0.01}}}          # 模型判低危


_lp2._load_router = lambda: _LowRouter()
merged = _lp2.advisory_for_approval({"tool": "delete_local_file", "path": "D:/项目/说明.txt"})
check("★ 模型判低危但正则判高危 → 显示高危（装 laya 不让提示变松）",
      merged and merged["level"] == "warn" and "高危" in merged["text"], str(merged))
check("合并后的提示同时写明两路来源（正则规则 + 模型概率）",
      merged and "正则规则" in merged["text"] and "laya 模型" in merged["text"]
      and "0.01" in merged["text"], str(merged))
merged_benign = _lp2.advisory_for_approval({"tool": "read_local_file", "path": "D:/项目/说明.txt"})
check("正则没命中时仍以模型结论为主（只读→低危）",
      merged_benign and merged_benign["level"] == "info" and "低危" in merged_benign["text"],
      str(merged_benign))

print("\n" + "=" * 78)
if failures:
    print(f"❌ 失败 {len(failures)} 项：{failures}")
    raise SystemExit(1)
print("✅ laya 风险分级插件回归：全部通过")
