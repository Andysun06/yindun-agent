# -*- coding: utf-8 -*-
"""隐盾 · laya 风险分级「多接入」回归测试（本机模型 / 局域网服务 / 正则回退）

为什么需要这个套件：
  laya 被定位成项目的亮点能力之一，它就必须**能被真实接入、能被验证**，而不是
  "本机正好装了就亮一下"。三级来源里最容易出问题的是局域网那一级——它有网络、
  有协议、有"连上了但用不了"的中间态。本套件用一个**本机起的 OpenAI 兼容桩服务**
  把这条链路真正跑一遍，并锁住四条不变量：
    A. **配置只认 http(s)**：地址栏不能变成任意本地/文件读取入口；
    B. **"连上"≠"可用"**：探活成功只证明服务活着，分级要能真解析出级别才算成功；
    C. **回退顺序与提示来源一致**：本机 → 局域网 → 正则，且每次都写明本次来源；
    D. **不静默失效**：配置坏了/地址不通，都要有可读的结论，不许一声不响什么都不发生。
  另外锁住"设置页与插件读同一份配置"（同一文件名 + 同一落点），这是两边不漂移的前提。

用法：python tests/test_laya_access.py   （退出码 0 = 全部通过）
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from yindun.app import laya_service as ls  # noqa: E402
from yindun.plugins.builtin.laya_risk import plugin as laya  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── 本机 OpenAI 兼容桩服务：/models 探活 + /chat/completions 打分 ──
class _Stub(BaseHTTPRequestHandler):
    reply = "0.82"
    require_auth = False
    fail_mode = ""          # "" / "500" / "garbage"

    def log_message(self, *args):     # 静音：测试输出只留结论
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                   # noqa: N802
        if self.require_auth and not self.headers.get("Authorization"):
            self._send(401, {"error": "unauthorized"})
            return
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"data": [{"id": "laya"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):                                  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.fail_mode == "500":
            self._send(500, {"error": "boom"})
            return
        if self.fail_mode == "garbage":
            self._send(200, {"choices": [{"message": {"content": "我不确定"}}]})
            return
        if self.path.rstrip("/").endswith("/chat/completions"):
            self._send(200, {"choices": [{"message": {"content": self.reply}}]})
        else:
            self._send(404, {"error": "not found"})


server = HTTPServer(("127.0.0.1", 0), _Stub)
PORT = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}/v1"

tmp = tempfile.TemporaryDirectory()
_prev_data_dir = laya.PLUGIN_DATA_DIR
laya.PLUGIN_DATA_DIR = tmp.name

# 配置读写走插件数据目录（与生产同一条通道：宿主把这些调用转给插件数据目录）
_CONFIG_FILE = Path(tmp.name) / ls.CONFIG_NAME
_RULES_FILE = Path(tmp.name) / ls.RULES_NAME
_RULES_FILE.write_text("高危|delete_local_file|删除本地文件\n", encoding="utf-8")


def _read(plugin_id, name):
    path = Path(tmp.name) / name
    if not path.exists():
        return {"ok": True, "text": "", "path": str(path), "exists": False}
    return {"ok": True, "text": path.read_text(encoding="utf-8"), "path": str(path), "exists": True}


def _write(plugin_id, name, text):
    path = Path(tmp.name) / name
    path.write_text(text, encoding="utf-8")      # 落点受控：测试临时目录
    return {"ok": True, "path": str(path)}


svc = ls.LayaService(read_config=_read, write_config=_write, runtime_lookup=lambda: {"last_source": ""})

# ── 1) 配置只认 http(s)、越界值回到安全默认 ──────────
print("=" * 78)
print("【1】接入配置：协议白名单与坏值兜底")
print("=" * 78)
check("file:// 之类协议被拒绝（地址栏不能变成任意本地读取入口）",
      ls.normalize_config({"endpoint": "file:///C:/secret.txt"})["endpoint"] == "")
check("ftp:// 同样被拒绝", ls.normalize_config({"endpoint": "ftp://10.0.0.1/x"})["endpoint"] == "")
check("http(s) 正常保留",
      ls.normalize_config({"endpoint": "https://10.0.0.8:9443/v1"})["endpoint"] == "https://10.0.0.8:9443/v1")
check("未知 source 回到默认档（本机优先），不产生第 4 种档位",
      ls.normalize_config({"source": "wan"})["source"] == "local")
check("超长字段被截断（不把长文本喂进配置）",
      len(ls.normalize_config({"endpoint": "http://" + "a" * 500})["endpoint"]) <= ls.ENDPOINT_MAX)

# ── 2) 探活：真连本机桩服务 ──────────────────────────
print("=" * 78)
print("【2】局域网连通性探活（真发只读请求）")
print("=" * 78)
ok = svc.probe_lan(BASE)
check("填了地址 → 探活成功，且如实回显 HTTP 状态", ok["ok"] is True and "200" in ok["detail"], str(ok))
bad = svc.probe_lan("http://127.0.0.1:1/v1")            # 端口 1 必然连不上
check("连不上 → 明确报错（不是静默失败）", bad["ok"] is False and bad["detail"].startswith("连不上"), str(bad))
check("地址为空 → 直接提示先填地址", svc.probe_lan("")["ok"] is False)

_Stub.require_auth = True
auth = svc.probe_lan(BASE)
_Stub.require_auth = False
check("服务活着但要凭据 → 说清是 401 而不是笼统失败（这也是一个有效结论）",
      auth["ok"] is False and "401" in auth["detail"], str(auth))

# ── 3) 试打分：'连上' ≠ '可用' ───────────────────────
print("=" * 78)
print("【3】试打分：必须真解析出级别才算可用")
print("=" * 78)
_Stub.reply = "0.82"
t1 = svc.try_lan({"endpoint": BASE, "model": "laya"})
check("概率型回复 → 解析出「高危」并回显原文片段",
      t1["ok"] is True and t1.get("level") == "高危" and "0.82" in t1["detail"], str(t1))
_Stub.reply = "低危"
t2 = svc.try_lan({"endpoint": BASE})
check("中文级别型回复 → 解析出「低危」", t2["ok"] is True and t2.get("level") == "低危", str(t2))
_Stub.fail_mode = "garbage"
t3 = svc.try_lan({"endpoint": BASE})
_Stub.fail_mode = ""
check("解析不出级别 → 判为不可用（不能把'有响应'当'分级可用'）",
      t3["ok"] is False and "无法解析" in t3["detail"], str(t3))
_Stub.fail_mode = "500"
t4 = svc.try_lan({"endpoint": BASE})
_Stub.fail_mode = ""
check("服务 500 → 如实报错", t4["ok"] is False and "500" in t4["detail"], str(t4))
check("没填地址时试打分也给出人话提示", svc.try_lan({})["ok"] is False)

# ── 4) 三级来源的选择与回退 ──────────────────────────
print("=" * 78)
print("【4】三级来源：本机 → 局域网 → 正则（顺序与提示来源一致）")
print("=" * 78)
cases = [
    ({"source": "local", "auto": True}, True, False, "local"),
    ({"source": "local", "auto": True}, False, True, "lan"),
    ({"source": "local", "auto": True}, False, False, "regex"),
    ({"source": "lan", "auto": True}, False, True, "lan"),
    ({"source": "regex", "auto": True}, True, True, "regex"),
]
for cfg, local_ok, lan_ok, expect in cases:
    got, label = ls._active_backend(cfg, local_ok, lan_ok)
    check(f"本机={'有' if local_ok else '无'} 局域网={'有' if lan_ok else '无'} source={cfg['source']} → {expect}",
          got == expect, f"{got} / {label}")
check("自动回退的落脚点会写进文案（用户能看出'现在是退到哪一级'）",
      "自动落到" in ls._active_backend({"source": "local", "auto": True}, False, True)[1])

# ── 5) 插件读的是同一份配置：来源与回退在插件里同样成立 ──
print("=" * 78)
print("【5】插件侧：一份配置、三级回退、提示写明来源")
print("=" * 78)
check("设置页与插件用的是同一个配置文件名（两边不漂移的前提）",
      ls.CONFIG_NAME == "laya_source.json" and "laya_source.json" in
      (ROOT / "yindun/plugins/builtin/laya_risk/manifest.json").read_text(encoding="utf-8"))

ctx = {"tool": "delete_local_file", "path": "D:\\项目\\合同.docx", "user_request": "整理文件"}


# 本机模型是否可用取决于**跑测试的机器**（本机可能真装了 laya，也可能没有），
# 所以这里显式注入条件，而不是依赖环境——否则同一份测试在不同机器上结论不同、
# 还可能因为真实模型首次加载等 25 秒把套件拖慢。
_prev_loader = laya._load_router


class _FakeRouter:
    """假 router：直接回一个概率，用来验证"本机模型可用"这条路径的提示与来源标注。"""

    def __init__(self, prob=0.91):
        self.prob = prob

    def predict(self, state, questions, model=None):
        # 问句键随生产代码演进（V3.5.8 起问的是 `harm`：动作危害，而不是"是否越权"）；
        # 桩必须跟着走，否则真实契约变了这里还在"通过"。
        return {"answers": {"harm": {"noul": self.prob}}}


def _with_cfg(cfg, fn, router=None):
    _CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    laya._cache["hash"] = None
    laya._load_router = (lambda: router) if router is not None else (lambda: None)
    try:
        return fn()
    finally:
        laya._load_router = _prev_loader


laya._ROUTER_FAILED = False      # 清掉真实环境可能留下的"加载失败"缓存，避免干扰本组用例
laya._ROUTER = None

# ① 本机可用 → 用模型打分，来源写明"本机 laya 模型"
graded_local = _with_cfg({"source": "local", "auto": True, "endpoint": BASE, "model": "laya"},
                         lambda: laya.advisory_for_approval(dict(ctx)), router=_FakeRouter())
check("本机模型可用时优先用本机，提示写明「laya 模型」",
      graded_local is not None and "laya 模型" in graded_local["text"], str(graded_local))
check("本机路径也记录来源（设置页据此显示最近一次分级）",
      "本机" in laya.last_source(), laya.last_source())

# ② 本机不可用 + 配了局域网 → 落到局域网并写明来源
graded = _with_cfg({"source": "local", "auto": True, "endpoint": BASE, "model": "laya"},
                   lambda: laya.advisory_for_approval(dict(ctx)))
check("本机不可用时落到局域网，提示写明「局域网 laya 服务」",
      graded is not None and "局域网" in graded["text"], str(graded))
check("最近来源被记录下来（设置页据此显示'最近一次分级'）",
      "局域网" in laya.last_source(), laya.last_source())

# ③ 局域网地址不通 → 回退正则，仍给出结论
graded2 = _with_cfg({"source": "local", "auto": True, "endpoint": "http://127.0.0.1:1/v1"},
                    lambda: laya.advisory_for_approval(dict(ctx)))
check("局域网不通时回退到正则规则，且提示写明来源",
      graded2 is not None and "正则" in graded2["text"], str(graded2))

# ④ 关掉自动回退且只认局域网 → 不假装（返回 None，而不是偷偷用正则）
graded3 = _with_cfg({"source": "lan", "auto": False, "endpoint": ""},
                    lambda: laya.advisory_for_approval(dict(ctx)))
check("显式关掉回退时不再偷偷降级（宁可不提示也不冒充来源）", graded3 is None, str(graded3))

# 配置坏了（非法 JSON）→ 回到默认，不抛异常、不静默
_CONFIG_FILE.write_text("{ 这不是 json", encoding="utf-8")
laya._cache["hash"] = None
status = svc.status()
check("配置损坏时状态页给出可读结论，且插件仍可用（回退正则）",
      isinstance(status, dict) and svc.config()["source"] in ls.SOURCES, str(status.get("config_error")))

# 规则回退必须仍然给出结论（不因模型缺失而静默）
graded4 = _with_cfg({"source": "regex", "auto": True}, lambda: laya.advisory_for_approval(dict(ctx)))
check("正则回退路径仍产生分级提示（不静默失效）",
      graded4 is not None and "正则" in graded4["text"], str(graded4))

# ── 6) 状态汇总字段齐备（界面按这些字段渲染）────────
print("=" * 78)
print("【6】状态汇总：界面需要什么就给什么")
print("=" * 78)
_Config_ok = {"source": "lan", "auto": True, "endpoint": BASE, "model": "laya"}
_CONFIG_FILE.write_text(json.dumps(_Config_ok, ensure_ascii=False), encoding="utf-8")
st = svc.status()
for key in ("source", "config", "local", "lan", "regex", "active_backend", "active_label", "last_used"):
    check(f"状态含字段 {key}", key in st, str(list(st.keys())))
check("状态里的局域网 detail 明确指出需要 OpenAI 兼容端点口径",
      "chat/completions" in st["lan"]["detail"] or st["lan"]["available"], str(st["lan"]))

# ── 7) 元数据：分类与"托管配置"标记 ─────────────────
print("=" * 78)
print("【7】插件元数据：市场页分组与托管配置标记")
print("=" * 78)
manifests = {}
for path in (ROOT / "yindun/plugins/builtin").glob("*/manifest.json"):
    manifests[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
check("六个内置插件都声明了 category（市场页按它分组）",
      all(m.get("category") for m in manifests.values()),
      str({k: v.get("category") for k, v in manifests.items()}))
check("分类取值都在界面支持的四类内",
      all(m["category"] in ("安全", "文档", "办公", "导出") for m in manifests.values()))
check("laya 的接入配置文件标了 managed（不在卡片里给裸文本编辑）",
      any(c.get("managed") for c in manifests["laya_risk"]["config_files"]))
from yindun.plugins.host import PluginHost  # noqa: E402
host = PluginHost()
host.discover()
public = next(p for p in host.list_plugins() if p["id"] == "laya_risk")
check("宿主把 category 透传到界面载荷", public.get("category") == "安全", str(public.get("category")))
check("宿主保留 config_files 的 managed 标记",
      any(c.get("managed") for c in public["config_files"]), str(public["config_files"]))
check("旧版 backend.txt 仍在清单里（兼容存量安装，不静默失效）",
      any(c["name"] == "backend.txt" for c in public["config_files"]))

server.shutdown()
laya.PLUGIN_DATA_DIR = _prev_data_dir
tmp.cleanup()

print("\n" + "=" * 78)
if failures:
    print(f"❌ 回归失败：{len(failures)} 项")
    for f in failures:
        print("   ·", f)
    sys.exit(1)
print("✅ 回归通过：三级接入（本机 / 局域网 / 正则）· 协议白名单 · 探活与真打分 · 回退顺序与来源标注 "
      "· 配置损坏不静默 · 元数据分类与托管标记")
sys.exit(0)
