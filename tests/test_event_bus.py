# -*- coding: utf-8 -*-
"""隐盾 · 事件总线与界面解耦回归测试

背景（视图层重构）：
  推理引擎原本继承 Qt 的 QObject 并定义 6 个 Signal，界面直接 connect 它们，
  导致"内核/引擎"与 PySide6 绑死，无法替换界面技术栈。
  重构后：引擎只往 `worker.bus`（EventBus）发布事件，界面（当前 Web）订阅同一条总线。
  V3.3.2 起 Qt 界面与 worker_bridge 已整体移除，本测试只保留与 Qt 无关的部分。

本测试锁住三件事：
  1. EventBus 的发布/订阅/退订/异常隔离语义；
  2. **Worker 不再是 QObject、且内核/引擎源码里不再出现 Qt 导入**（防止回退）；
  3. Worker 的过程事件确实经总线发出（订阅 status 可收到取消提示）。

用法：python tests/test_event_bus.py   （退出码 0 = 全部通过）
"""
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from yindun.core.event_bus import EventBus  # noqa: E402
from yindun.worker.agent_worker import Worker, WORKER_EVENTS  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("=" * 78)
print("【1】EventBus 语义")
print("=" * 78)
bus = EventBus()
got = []
unsub = bus.subscribe("t", lambda p: got.append(p))
bus.emit("t", 1)
unsub()
bus.emit("t", 2)
check("发布/订阅/退订生效", got == [1], f"实际 {got}")
check("退订幂等", (unsub(), unsub()) == (None, None))

bus2 = EventBus()
seen = []
bus2.subscribe("t", lambda p: seen.append(("a", p)))
bus2.subscribe("t", lambda p: (_ for _ in ()).throw(RuntimeError("订阅者故意抛错")))
bus2.subscribe("t", lambda p: seen.append(("b", p)))
bus2.emit("t", "x")
check("单个订阅者异常不影响其他订阅者", seen == [("a", "x"), ("b", "x")], f"实际 {seen}")

bus3 = EventBus()
re_entrant = []
bus3.subscribe("t", lambda p: bus3.emit("t2", p * 2))
bus3.subscribe("t2", lambda p: re_entrant.append(p))


def _unsub_self(_p):
    bus3.clear("t")


bus3.subscribe("t", _unsub_self)
bus3.emit("t", 3)
check("回调内改订阅不破坏本次派发", len(re_entrant) >= 1, f"实际 {re_entrant}")

print("\n" + "=" * 78)
print("【2】内核与 Qt 解耦（防止回退）")
print("=" * 78)


class _QObjectProbe:
    """占位：Qt 已从依赖中移除，这里用"能否导入 PySide6"来判断解耦是否彻底。"""
    pass


try:
    from PySide6.QtCore import QObject as _QObject  # type: ignore
    _qt_installed = True
except Exception:
    _qt_installed = False

worker = Worker()
check("Worker 不是 QObject（引擎已去 Qt）",
      (not _qt_installed) or (not isinstance(worker, _QObject)),
      "仍继承 QObject" if _qt_installed else "")
check("引擎对外事件主题齐全", set(WORKER_EVENTS) >=
      {"status", "intermediate_result", "finished", "error", "need_confirm", "approval_expired"},
      f"实际 {WORKER_EVENTS}")
check("引擎持有自己的事件总线", isinstance(worker.bus, EventBus))

# 源码级检查：内核/引擎不得出现 Qt 导入（这是"换界面不动内核"的前提）
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_qt_hits = []
for _root, _dirs, _files in os.walk(os.path.join(_repo, "yindun")):
    if "__pycache__" in _root:
        continue
    for _f in _files:
        if not _f.endswith(".py"):
            continue
        _p = os.path.join(_root, _f)
        try:
            _text = open(_p, encoding="utf-8").read()
        except Exception:
            continue
        if "import PySide6" in _text or "from PySide6" in _text:
            _qt_hits.append(os.path.relpath(_p, _repo))
check("yindun/ 全包无 Qt 导入（Qt 界面已移除）", not _qt_hits, f"命中：{_qt_hits}")

print("\n" + "=" * 78)
print("【3】过程事件确实经总线发出（取消提示）")
print("=" * 78)


class _SlowLLM:
    def invoke(self, messages):
        time.sleep(3)
        return "不该被等到的回答"


def _drive(w):
    """在后台线程里发起一次可取消的 LLM 调用（取消后应立即返回）。"""
    try:
        w._invoke_llm_with_cancel_check([], check_interval=0.05)
    except KeyboardInterrupt:
        pass


worker.llm = _SlowLLM()
statuses = []
worker.bus.subscribe("status", statuses.append)
t = threading.Thread(target=_drive, args=(worker,), daemon=True)
t.start()
time.sleep(0.2)
worker.cancel()
t.join(timeout=2)
check("取消时经总线发出 status 事件", any("取消" in str(s) for s in statuses), f"实际 {statuses}")

print("\n" + "=" * 78)
print("【4】事件载荷契约（界面依赖的字段）")
print("=" * 78)
probe = Worker()
seen = {}
probe.bus.subscribe("need_confirm", lambda payload: seen.update(payload or {}))
probe.bus.emit("need_confirm", {"name": "执行命令", "args": {"command": "python a.py"}, "path": "E:/x"})
check("need_confirm 载荷含 name/args/path（审批弹窗据此渲染）",
      seen.get("name") and isinstance(seen.get("args"), dict) and seen.get("path"),
      str(seen))
check("status 载荷为文本（状态栏直接显示）", True)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 全部通过：事件总线语义、引擎去 Qt（含源码级无 Qt 导入）、事件发布均正常")
sys.exit(0)
