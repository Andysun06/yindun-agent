# -*- coding: utf-8 -*-
"""隐盾 · 插件宿主契约回归测试

覆盖（"万物皆插件"的安全约束）：
  1. 契约：钩子集合与分类；判定/放行类能力永不开放；内置能力插件齐备
  2. 发现与校验：manifest 缺字段 / 非法 id / 未知钩子 / 缺"管什么"的声明，一律拒绝
  3. 依赖检查：缺少 Python 包、缺少本地模型各自被识别为"未满足"并阻止启用
  4. 启停：状态通过回调持久化；依赖未满足时拒绝启用
  5. 调用安全：未启用不调用；插件抛异常不影响宿主与其它插件；按钩子上限截断超时
  6. 载荷净化：越界 / 超长 / 跨行 / 重叠 / 非法类型一律丢弃，实体类型强制加 PLUGIN_ 前缀
  7. 数据目录与配置：只能读写 manifest 声明的文件；路径穿越被拒；超限被拒
  8. 自检：钩子函数缺失必须被报出来（不允许"启用了却没反应"的静默失效）
  9. 服务层：启停写入设置与审计；能力同步结果可查

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

from yindun.plugins.host import (  # noqa: E402
    FORBIDDEN_HOOKS, HOOK_SPECS, HOOKS, PluginHost, _norm_recognizer,
)

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


# ── 1) 契约与内置插件 ────────────────────────────────
print("=" * 78)
print("【1】契约与内置能力插件")
print("=" * 78)
check("钩子集合与设计一致",
      set(HOOKS) == {"advisory_for_approval", "recognizer", "attachment_parser",
                     "workflow_template", "export_renderer"},
      f"实际 {sorted(HOOKS)}")
check("每个钩子都声明了类别（建议型 / 增强型）",
      all(HOOK_SPECS[name].kind in ("advisory", "additive") for name in HOOKS),
      str({k: v.kind for k, v in HOOK_SPECS.items()}))
check("不存在审批放行 / 脱敏豁免 / 审计写入类钩子",
      not any(k in HOOKS for k in FORBIDDEN_HOOKS) and not any(k in FORBIDDEN_HOOKS for k in HOOKS),
      f"实际 {sorted(HOOKS)}")
check("增强型钩子都带载荷净化器（返回值不会被照单全收）",
      all(HOOK_SPECS[name].normalize is not None
          for name in HOOKS if HOOK_SPECS[name].kind == "additive"),
      str({k: HOOK_SPECS[k].normalize is not None for k in HOOKS}))

enabled_state = {}
host = PluginHost(enabled_lookup=lambda pid: bool(enabled_state.get(pid)),
                  enabled_setter=lambda pid, on: enabled_state.__setitem__(pid, on),
                  user_dir=USER_DIR,
                  data_root=TMP / "data",
                  model_lister=lambda: ["qwen2.5:7b-instruct", "nomic-embed-text:latest"])
plugins = {p["id"]: p for p in host.list_plugins()}
builtin = plugins.get("decision_hint")
check("发现内置插件 审批决策提示", builtin is not None, str(list(plugins)))
check("内置插件默认关闭", builtin and builtin["enabled"] is False)
check("内置插件依赖满足且可用", builtin and builtin["usable"] is True, str(builtin))
for pid, hook in (("custom_dict", "recognizer"), ("doc_extra", "attachment_parser"),
                  ("office_wf", "workflow_template"), ("export_pack", "export_renderer")):
    item = plugins.get(pid)
    check(f"内置能力插件 {pid} 被发现且可用",
          item is not None and item["usable"] and hook in item["hooks"],
          str(item))
    check(f"内置能力插件 {pid} 报出能力清单",
          item is not None and any(c["hook"] == hook for c in item.get("capabilities", [])),
          str(item.get("capabilities") if item else None))
check("附件解析插件声明了负责的扩展名",
      sorted(plugins["doc_extra"]["file_exts"]) == ["eml", "htm", "html", "mht"],
      str(plugins["doc_extra"]["file_exts"]))
check("导出插件声明了导出类型与格式（且不占用内核保留的格式名）",
      plugins["export_pack"]["export_formats"] == {"audit": ["markdown"], "workflow": ["html_card"]}
      and not (set(plugins["export_pack"]["export_formats"]["workflow"]) & {"md", "html", "json", "docx"}),
      str(plugins["export_pack"]["export_formats"]))
check("导出格式带中文标签（界面菜单直接可读）",
      plugins["export_pack"]["format_labels"].get("html_card") == "卡片式 HTML",
      str(plugins["export_pack"]["format_labels"]))
check("词表插件声明了可编辑配置文件",
      [f["name"] for f in plugins["custom_dict"]["config_files"]] == ["words.txt"],
      str(plugins["custom_dict"]["config_files"]))

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
make_plugin("unknown_hook", {
    "id": "unknown_hook", "name": "未知钩子", "version": "1.0.0",
    "hooks": ["do_something_else"],
}, "def do_something_else(ctx):\n    return None\n")
make_plugin("no_field", {"name": "缺字段", "hooks": ["advisory_for_approval"]}, "")
make_plugin("bad_id", {"id": "非法 id!", "name": "x", "version": "1.0.0",
                       "hooks": ["advisory_for_approval"]},
            "def advisory_for_approval(ctx):\n    return None\n")
make_plugin("ext_parser_no_exts", {
    "id": "ext_parser_no_exts", "name": "没声明格式的解析插件", "version": "1.0.0",
    "hooks": ["attachment_parser"],
}, "def attachment_parser(ctx):\n    return None\n")
make_plugin("exporter_no_formats", {
    "id": "exporter_no_formats", "name": "没声明格式的导出插件", "version": "1.0.0",
    "hooks": ["export_renderer"],
}, "def export_renderer(ctx):\n    return None\n")
make_plugin("exporter_reserved", {
    "id": "exporter_reserved", "name": "想占用内核格式的导出插件", "version": "1.0.0",
    "hooks": ["export_renderer"],
    "export_formats": {"workflow": ["html"]},   # ← 内核保留名，必须拒绝
}, "def export_renderer(ctx):\n    return None\n")

host2 = PluginHost(enabled_lookup=lambda pid: False, enabled_setter=lambda pid, on: None,
                   user_dir=USER_DIR, data_root=TMP / "data2", model_lister=lambda: [])
listed = {p["id"]: p for p in host2.list_plugins()}
check("拒绝申请判定型能力的插件（理由写明边界）",
      listed.get("bad_hook", {}).get("error") and "永不开放" in listed["bad_hook"]["error"],
      str(listed.get("bad_hook")))
check("拒绝声明未知钩子的插件",
      listed.get("unknown_hook", {}).get("error") and "不支持的钩子" in listed["unknown_hook"]["error"],
      str(listed.get("unknown_hook")))
check("缺必填字段被拒绝",
      listed.get("no_field", {}).get("error") and "缺少必填字段" in listed["no_field"]["error"],
      str(listed.get("no_field")))
check("非法 id 被拒绝",
      listed.get("bad_id", {}).get("error") and "非法" in listed["bad_id"]["error"],
      str(listed.get("bad_id")))
check("解析插件没声明扩展名 → 拒绝（含糊的插件无法被安全调用）",
      listed.get("ext_parser_no_exts", {}).get("error")
      and "file_exts" in listed["ext_parser_no_exts"]["error"],
      str(listed.get("ext_parser_no_exts")))
check("导出插件没声明格式 → 拒绝",
      listed.get("exporter_no_formats", {}).get("error")
      and "export_formats" in listed["exporter_no_formats"]["error"],
      str(listed.get("exporter_no_formats")))
check("导出插件想占用内核保留格式名 → 拒绝（避免「内核兜底」变成「内核被顶替」）",
      listed.get("exporter_reserved", {}).get("error")
      and "内核保留" in listed["exporter_reserved"]["error"],
      str(listed.get("exporter_reserved")))
check("不可用插件无法启用", host2.set_enabled("bad_hook", True).get("ok") is False)

# 配置文件名的越界写法必须在加载时被丢掉
make_plugin("cfg_traversal", {
    "id": "cfg_traversal", "name": "配置越界", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
    "config_files": ["words.txt", "../evil.txt", "sub/dir.txt", "ok-name_1.md"],
}, "def advisory_for_approval(ctx):\n    return None\n")
host2.discover()
cfg_record = host2.get("cfg_traversal")
names = [item["name"] for item in (cfg_record.config_files if cfg_record else [])]
check("配置文件名的越界写法被丢弃（只留平台中立的简单文件名）",
      names == ["words.txt", "ok-name_1.md"], str(names))

# ── 3) 依赖检查 ────────────────────────────────────
print("\n" + "=" * 78)
print("【3】依赖检查（Python 包 / 本地模型）")
print("=" * 78)
make_plugin("need_pkg", {
    "id": "need_pkg", "name": "需要第三方包", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
    # 用一个必然不存在的包名：早先用 torch 做样例，本机装上 torch（laya 依赖）后
    # "缺少依赖"的前提就消失了——测试的前提不该依赖开发机装了什么。
    "requires": {"python": ["yd-no-such-pkg-xyz>=1.0"]},
}, "def advisory_for_approval(ctx):\n    return None\n")
make_plugin("need_model", {
    "id": "need_model", "name": "需要本地模型", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
    "requires": {"ollama_models": ["laya-decide*"]},
}, "def advisory_for_approval(ctx):\n    return None\n")

host3 = PluginHost(enabled_lookup=lambda pid: False, enabled_setter=lambda pid, on: None,
                   user_dir=USER_DIR, data_root=TMP / "data3",
                   model_lister=lambda: ["qwen2.5:7b-instruct"])
listed3 = {p["id"]: p for p in host3.list_plugins()}
check("缺少 Python 包被判为依赖未满足",
      any("yd-no-such-pkg-xyz" in m for m in listed3["need_pkg"]["missing"]), str(listed3["need_pkg"]["missing"]))
check("缺少本地模型被判为依赖未满足",
      any("laya-decide" in m for m in listed3["need_model"]["missing"]), str(listed3["need_model"]["missing"]))
check("依赖未满足时拒绝启用", host3.set_enabled("need_pkg", True).get("ok") is False)

# ── 4) 调用安全 ────────────────────────────────────
print("\n" + "=" * 78)
print("【4】调用：启用判定 / 异常隔离 / 超时截断")
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
    "timeout_seconds": 1,     # 插件可声明超时（默认 5s；调用本地模型的插件会声明更长）
}, "import time\ndef advisory_for_approval(ctx):\n    time.sleep(30)\n    return {'level': 'info', 'text': '不该被等到'}\n")
make_plugin("slow_recognizer", {
    "id": "slow_recognizer", "name": "会卡住的识别插件", "version": "1.0.0",
    "hooks": ["recognizer"],
    "timeout_seconds": 120,   # 声明 120s，但识别是内联路径：必须被钩子上限截断
}, "import time\ndef recognizer(ctx):\n    time.sleep(30)\n    return {'spans': []}\n")
make_plugin("missing_fn", {
    "id": "missing_fn", "name": "没写钩子函数的插件", "version": "1.0.0",
    "hooks": ["advisory_for_approval"],
}, "def something_else(ctx):\n    return {'text': '名字不对'}\n")

state4 = {}
host4 = PluginHost(enabled_lookup=lambda pid: bool(state4.get(pid)),
                   enabled_setter=lambda pid, on: state4.__setitem__(pid, on),
                   user_dir=USER_DIR, data_root=TMP / "data4", model_lister=lambda: [])

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
check("卡住的插件按声明的超时被跳过（1s 放弃，整体不超 3 秒）", elapsed < 3.0, f"实际 {elapsed:.1f}s")
check("插件声明的超时被记录（供界面展示）",
      float(host4.get("slow_plugin").to_public().get("timeout_seconds", 0)) == 1.0,
      str(host4.get("slow_plugin").to_public().get("timeout_seconds")))
check("未知钩子直接返回空", host4.call_hook("decide_everything", {}) == [])

# 内联路径（识别）不允许长时间阻塞：声明 120s 也必须被钩子上限截断
host4.set_enabled("slow_recognizer", True)
started = time.monotonic()
host4.call_hook("recognizer", {"text": "任意正文"})
rec_elapsed = time.monotonic() - started
check("识别钩子被硬上限截断（声明 120s 也不允许拖住脱敏路径）",
      rec_elapsed <= HOOK_SPECS["recognizer"].max_timeout + 1.0,
      f"实际 {rec_elapsed:.1f}s，上限 {HOOK_SPECS['recognizer'].max_timeout}s")

# 钩子函数缺失：不允许静默跳过
host4.set_enabled("missing_fn", True)
res5 = host4.call_hook("advisory_for_approval", {"tool": "读文件"})
check("钩子函数缺失时不会被当成正常返回（也不会污染其它插件的结果）",
      len(res5) == 1 and all(r.get("plugin_id") != "missing_fn" for r in res5), str(res5))

# 运行状态可见化：界面要能回答"启用后到底有没有在工作"
pub4 = {p["id"]: p for p in host4.list_plugins()}
rt_missing = pub4.get("missing_fn", {}).get("runtime", {})
check("调用失败会记进运行状态（界面能看到出错，而不是只显示'已启用'）",
      rt_missing.get("status") == "error" and rt_missing.get("calls", 0) >= 1, str(rt_missing))
check("未触发过的插件没有运行记录（界面显示'等待触发'）",
      not pub4.get("no_field", {}).get("runtime"), str(pub4.get("no_field", {})))
host4.set_enabled("missing_fn", False)

# ── 5) 载荷净化 ────────────────────────────────────
print("\n" + "=" * 78)
print("【5】载荷净化（脏数据不进内核）")
print("=" * 78)
rec = host.get("custom_dict")
text = "甲乙丙丁戊己庚辛"
clean = _norm_recognizer({"spans": [
    {"start": 0, "end": 2, "type": "TERM"},                       # 正常
    {"start": 0, "end": 2, "type": "TERM"},                       # 与上一条重叠 → 丢弃
    {"start": 0, "end": 99, "type": "TERM"},                      # 越界 → 丢弃
    {"start": 2, "end": 2, "type": "TERM"},                       # 空区间 → 丢弃
    {"start": 2, "end": 4, "type": "bad type!"},                  # 非法类型名 → 丢弃
    {"start": 2, "end": 4, "type": "OK_TERM", "level": "宇宙级"},  # 非法分级 → 回落到"机密"
    {"start": 4, "end": 6, "type": "OK_TERM", "level": "内部"},
]}, rec, {"text": text})
types = [s["type"] for s in (clean or {}).get("spans", [])]
check("净化后只留合法区间（重叠/越界/空区间/非法类型全部丢弃）",
      (clean or {}).get("count") == 3, str(clean))
check("实体类型被强制加 PLUGIN_ 前缀（不会与内核实体撞名）",
      all(t.startswith("PLUGIN_") for t in types) and "PLUGIN_OK_TERM" in types, str(types))
check("非法分级回落到「机密」（从严）",
      [s["level"] for s in clean["spans"] if s["type"] == "PLUGIN_OK_TERM"][0] == "机密",
      str(clean["spans"]))
check("空返回不产生载荷", _norm_recognizer({"spans": []}, rec, {"text": text}) is None)
check("返回值类型不对时不产生载荷", _norm_recognizer("随便一个字符串", rec, {"text": text}) is None)

# ── 6) 数据目录与可编辑配置 ────────────────────────
print("\n" + "=" * 78)
print("【6】插件数据目录与配置读写")
print("=" * 78)
write_res = host.write_config_file("custom_dict", "words.txt", "隐盾专项\nre:星海[0-9]{3}号\n")
check("可写入 manifest 声明的配置文件", write_res.get("ok") is True, str(write_res))
read_res = host.read_config_file("custom_dict", "words.txt")
check("可读回声明文件的内容",
      read_res.get("ok") and "隐盾专项" in read_res.get("text", ""), str(read_res))
check("未声明的文件名被拒绝（含路径穿越写法）",
      host.write_config_file("custom_dict", "../evil.txt", "x").get("ok") is False
      and host.read_config_file("custom_dict", "evil.txt").get("ok") is False)
check("超限内容被拒绝",
      host.write_config_file("custom_dict", "words.txt", "x" * (300 * 1024)).get("ok") is False)
data_dir = host.data_dir("custom_dict")
check("内置插件的数据目录落在 APP_ROOT 下（打包后只读解包目录不能写）",
      data_dir is not None and str(data_dir).startswith(str(TMP / "data")), str(data_dir))

# ── 7) 自检 ────────────────────────────────────────
print("\n" + "=" * 78)
print("【7】自检：钩子函数缺失必须被报出来")
print("=" * 78)
report = host4.selfcheck()
by_id = {item["id"]: item for item in report["plugins"]}
check("自检发现钩子函数缺失",
      by_id.get("missing_fn", {}).get("ok") is False
      and by_id["missing_fn"]["hooks"]["advisory_for_approval"]["ok"] is False,
      str(by_id.get("missing_fn")))
check("自检对正常插件给出通过",
      by_id.get("ok_plugin", {}).get("ok") is True, str(by_id.get("ok_plugin")))
builtin_report = {item["id"]: item for item in host.selfcheck()["plugins"]}
check("全部内置插件的钩子都真的有同名函数",
      all(item.get("ok") for item in builtin_report.values()), str(builtin_report))

# ── 8) 服务层集成 ──────────────────────────────────
print("\n" + "=" * 78)
print("【8】服务层：启停持久化 + 能力同步 + 审计留痕")
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
check("能力同步结果可查（停用后识别器不挂载、模板为空）",
      svc.plugin_sync_state().get("recognizer") is False
      and svc.plugin_sync_state().get("templates") == [],
      str(svc.plugin_sync_state()))
check("服务层自检可用", svc.plugin_selfcheck().get("ok") is True)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：钩子契约/校验/依赖/启停/调用安全/载荷净化/配置读写/自检/能力同步均符合约束")
sys.exit(0)
