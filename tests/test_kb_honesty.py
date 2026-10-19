# -*- coding: utf-8 -*-
"""隐盾 · 知识库「诚实性」回归（V3.6.12，组员评审 BUG-01）

背景：`AgentService.kb_add()` 过去无条件返回 `ok=True`（即使 `result.success == 0`），
界面因此提示"入库完成"而库其实是空的；`KnowledgeBase.is_available()` 只检查 Python
依赖，Ollama 没起也显示"就绪"。这套用例只断言**服务层如何解释真实结果**，不联网、
不依赖 Ollama / Chroma：

  1. 全部失败 → ok=False，error 指向第一条失败原因，summary 原样带回
  2. 部分成功 → ok=True + partial=True + failed>0
  3. 全部成功 → ok=True + partial=False
  4. embedding 不可用 → 入库前直接拒绝（ok=False），给出 ollama pull 下一步，
     且**根本不调用** add_documents
  5. kb_status() 带 embedding_ready 字段；不可用时点明 embed 模型名
  6. KnowledgeBase.embedding_ready() 真探活：在/不在/抛异常/30 秒缓存

用法：python tests/test_kb_honesty.py   （退出码 0 = 全部通过）
"""
import os
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from yindun.app.agent_service import AgentService  # noqa: E402
from yindun.app.session_store import SessionStore  # noqa: E402
from yindun.app.settings_store import SettingsStore  # noqa: E402
from yindun.core.knowledge_base import KnowledgeBase  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


TMP = tempfile.mkdtemp(prefix="yindun_kb_honesty_")


class FakeKB:
    """最小 KB 替身：只实现 kb_add / kb_status 真正调用的接口，并记录调用次数。"""

    def __init__(self, result=None, available=True, embedding_ready=True,
                 embed_model="nomic-embed-text"):
        self._result = result or {}
        self._available = available
        self._embedding_ready = embedding_ready
        self.embed_model = embed_model
        self.add_calls = 0
        self.embedding_checks = 0

    def is_available(self):
        return self._available

    def embedding_ready(self, *args, **kwargs):
        self.embedding_checks += 1
        return self._embedding_ready

    def add_documents(self, paths):
        self.add_calls += 1
        return self._result

    def get_stats(self):
        return {"documents": 0, "chunks": 0}

    def list_documents(self):
        return []


def make_service():
    svc = AgentService(settings=SettingsStore(path=os.path.join(TMP, "cfg.json")),
                       sessions=SessionStore(path=os.path.join(TMP, "sess.json")),
                       plugin_data_root=os.path.join(TMP, "plugins_data"))
    svc.initialize()
    return svc


FAIL_SUMMARY = "失败 2 个：a.txt（解析失败：不支持该格式）；b.txt（解析失败：文件为空）"
FAIL_DETAILS = [
    {"file": "a.txt", "status": "failed", "error": "解析失败：不支持该格式"},
    {"file": "b.txt", "status": "failed", "error": "解析失败：文件为空"},
]

print("=" * 78)
print("【1】全部失败：绝不返回 ok=True（假成功是 BUG-01 的核心）")
print("=" * 78)
svc = make_service()
kb_all_fail = FakeKB({
    "total": 2, "success": 0, "failed": 2, "total_chunks": 0,
    "summary": FAIL_SUMMARY, "details": FAIL_DETAILS,
})
svc._kb = kb_all_fail
res = svc.kb_add(["a.txt", "b.txt"])
check("success=0 → ok 必须为 False", res.get("ok") is False, str(res)[:220])
check("error 指向第一条失败原因", res.get("error") == "解析失败：不支持该格式",
      repr(res.get("error")))
check("summary 原样带回（人话统计不丢）", res.get("summary") == FAIL_SUMMARY,
      repr(res.get("summary"))[:160])
check("failed 计数如实", res.get("failed") == 2, str(res.get("failed")))
check("partial 为 False（一条都没成功）", res.get("partial") is False, str(res.get("partial")))
check("确实调用了 add_documents（不是被别的分支短路）", kb_all_fail.add_calls == 1,
      str(kb_all_fail.add_calls))

print("\n" + "=" * 78)
print("【2】部分成功：ok=True 但必须标 partial + failed")
print("=" * 78)
kb_partial = FakeKB({
    "total": 2, "success": 1, "failed": 1, "total_chunks": 4,
    "summary": "成功 1 个、失败 1 个：b.txt（解析失败：文件为空）",
    "details": [
        {"file": "a.txt", "status": "success", "chunks": 4},
        {"file": "b.txt", "status": "failed", "error": "解析失败：文件为空"},
    ],
})
svc._kb = kb_partial
res = svc.kb_add(["a.txt", "b.txt"])
check("部分成功 → ok=True", res.get("ok") is True, str(res)[:220])
check("partial=True（界面必须提示不是全部入库）", res.get("partial") is True, str(res.get("partial")))
check("failed 计数如实", res.get("failed") == 1, str(res.get("failed")))
check("summary 存在", bool(res.get("summary")), repr(res.get("summary")))
check("成功时 error 为空（不制造噪音）", not res.get("error"), repr(res.get("error")))

print("\n" + "=" * 78)
print("【3】全部成功：ok=True 且 partial=False")
print("=" * 78)
kb_ok = FakeKB({
    "total": 2, "success": 2, "failed": 0, "total_chunks": 8,
    "summary": "成功入库 2 个文档（8 个片段）", "details": [],
})
svc._kb = kb_ok
res = svc.kb_add(["a.txt", "b.txt"])
check("全部成功 → ok=True", res.get("ok") is True, str(res)[:220])
check("partial=False", res.get("partial") is False, str(res.get("partial")))
check("failed=0", res.get("failed") == 0, str(res.get("failed")))

print("\n" + "=" * 78)
print("【4】embedding 不可用：入库前直接拒绝 + 可执行提示 + 不碰 add_documents")
print("=" * 78)
kb_no_embed = FakeKB({"total": 1, "success": 1, "failed": 0}, embedding_ready=False)
svc._kb = kb_no_embed
res = svc.kb_add(["a.txt"])
check("embedding 不可用 → ok=False", res.get("ok") is False, str(res)[:220])
err = str(res.get("error") or "")
check("error 含可执行的下一步（ollama pull）", "ollama pull" in err, err)
check("★ 没有调用 add_documents（不制造「尝试过」的假象）", kb_no_embed.add_calls == 0,
      f"add_calls={kb_no_embed.add_calls}")
check("确实做了 embedding 探活", kb_no_embed.embedding_checks >= 1,
      str(kb_no_embed.embedding_checks))

print("\n" + "=" * 78)
print("【5】kb_status：embedding_ready 与 available 是两件事")
print("=" * 78)
svc._kb = FakeKB({}, available=True, embedding_ready=False,
                 embed_model="nomic-embed-text")
st = svc.kb_status()
check("kb_status 返回 embedding_ready 字段", "embedding_ready" in st, str(sorted(st)))
check("依赖已装时 available=True", st.get("available") is True, str(st)[:160])
check("embedding 不可用 → embedding_ready=False", st.get("embedding_ready") is False,
      str(st.get("embedding_ready")))
check("error 点明 embed 模型名（用户知道该 pull 哪个）",
      "nomic-embed-text" in str(st.get("error") or ""), str(st.get("error")))

svc._kb = FakeKB({}, available=True, embedding_ready=True,
                 embed_model="nomic-embed-text")
st_ready = svc.kb_status()
check("embedding 真的在线 → embedding_ready=True", st_ready.get("embedding_ready") is True,
      str(st_ready)[:160])
check("就绪时 error 为空", not st_ready.get("error"), repr(st_ready.get("error")))

print("\n" + "=" * 78)
print("【6】KnowledgeBase.embedding_ready：真探活 + 30 秒缓存")
print("=" * 78)
from yindun.app import llm_factory as _llmf  # noqa: E402

kb_real = KnowledgeBase(persist_dir=os.path.join(TMP, "kb_probe"))
kb_real._available = True          # 绕过依赖检查，只验探活逻辑

_calls = {"n": 0}
_real_detect = _llmf.detect_ollama_models_via_api


def _install_detect(fn):
    _llmf.detect_ollama_models_via_api = fn


try:
    # (a) 模型在 → True
    _install_detect(lambda host="": (["nomic-embed-text:latest", "llama3:8b"],
                                    "已从 Ollama 服务读取"))
    kb_real._embedding_ready_cache = None
    check("模型在 → True", kb_real.embedding_ready() is True)

    # (b) 模型不在 → False
    _install_detect(lambda host="": (["llama3:8b"], "只有对话模型"))
    kb_real._embedding_ready_cache = None
    check("embed 模型不在 → False", kb_real.embedding_ready() is False)

    # (c) 探测抛异常 → False（不把异常漏给界面）
    def _boom(host=""):
        raise RuntimeError("connection reset")

    _install_detect(_boom)
    kb_real._embedding_ready_cache = None
    check("探活抛异常 → False", kb_real.embedding_ready() is False)

    # (d) 30 秒缓存内不重复探测
    def _counted(host=""):
        _calls["n"] += 1
        return (["nomic-embed-text:latest"], "stub")

    _install_detect(_counted)
    kb_real._embedding_ready_cache = None
    first = kb_real.embedding_ready(cache_seconds=30.0)
    second = kb_real.embedding_ready(cache_seconds=30.0)
    check("缓存内两次调用都返回 True", first is True and second is True,
          f"{first}/{second}")
    check("★ 30 秒缓存内只探测一次", _calls["n"] == 1, f"实际探测 {_calls['n']} 次")

    # (e) 缓存过期 → 重新探测（用 0 秒缓存把"过期"变成可断言）
    kb_real.embedding_ready(cache_seconds=0.0)
    check("缓存过期后重新探测", _calls["n"] == 2, f"实际探测 {_calls['n']} 次")
finally:
    _install_detect(_real_detect)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：\n   - " + "\n   - ".join(failures))
    sys.exit(1)
print("✅ 全部通过：知识库入库不再假成功；embedding 可用性真实可查且可行动。")
sys.exit(0)
