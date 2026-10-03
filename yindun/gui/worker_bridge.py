# -*- coding: utf-8 -*-
"""隐盾 · 推理引擎 ↔ Qt 界面 的桥接器

背景：为支持视图层重构，推理引擎（`yindun/worker/agent_worker.py`）已经**去 Qt 化**，
只往 `worker.bus`（EventBus）发布事件。当前 PySide6 界面通过本桥接器把总线事件
重新变成 Qt 信号，从而在不改动任何界面逻辑的前提下完成解耦；后续 Web 界面
会直接订阅同一条总线（见开发记忆 §8 技术栈评估）。

线程语义：桥接器在主线程创建（QObject 归属主线程）。推理线程调用 `bus.emit`
时同步触发本类的 `signal.emit`，Qt 依据**接收者的线程归属**自动排队投递回主线程 ——
与原实现（信号直接从工作线程发出）行为一致。
"""
from PySide6.QtCore import QObject, Signal

from yindun.worker.agent_worker import WORKER_EVENTS

# 总线主题 → Qt 信号（顺序与 WORKER_EVENTS 一致）
_TOPIC_TO_SIGNAL = {
    "status": "status",
    "intermediate_result": "intermediate_result",
    "finished": "finished",
    "error": "error",
    "need_confirm": "need_confirm",
    "approval_expired": "approval_expired",
}


class WorkerQtBridge(QObject):
    """把 `Worker.bus` 上的事件转成 Qt 信号，供现有界面订阅。"""

    finished = Signal(str)
    error = Signal(str)
    status = Signal(str)
    need_confirm = Signal(dict)
    approval_expired = Signal()
    intermediate_result = Signal(str)

    def __init__(self, worker, parent=None):
        super().__init__(parent)
        self._worker = worker
        self._unsubscribers = []
        for topic, signal_name in _TOPIC_TO_SIGNAL.items():
            if topic not in WORKER_EVENTS:
                continue
            signal = getattr(self, signal_name)
            self._unsubscribers.append(
                worker.bus.subscribe(
                    topic, lambda payload, sig=signal: sig.emit(payload)
                )
            )

    def detach(self) -> None:
        """退订全部总线订阅（幂等）。旧轮次取消后调用，避免继续向界面投递事件。"""
        while self._unsubscribers:
            try:
                self._unsubscribers.pop()()
            except Exception:
                pass
