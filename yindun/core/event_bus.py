# -*- coding: utf-8 -*-
"""隐盾 · 极简线程安全事件总线

用途：让**内核与推理引擎彻底与界面无关**。
`core/`、`utils/`、`worker/` 不再依赖 Qt，只往总线上发事件；
界面（当前 PySide6，后续 Web）通过订阅总线把事件翻译成自己的 UI 表现。

设计取舍：
- 同步派发：调用 `emit` 的线程直接执行订阅者。界面侧若需要跨线程投递，
  由界面自己的机制完成（Qt 信号会自动排队；Web 桥接自行切到主线程）。
- 快照派发：派发前复制订阅者列表，订阅者在回调里退订/新订都不会破坏本次派发。
- 异常隔离：单个订阅者抛错不影响其他订阅者，也不影响发布方（内核不能被界面拖死）。
- 无第三方依赖、无全局状态：每个可发布对象持有自己的总线（`Worker.bus`）。
"""
from __future__ import annotations

import threading
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional


class EventBus:
    """发布/订阅总线。

    用法::

        bus = EventBus()
        unsub = bus.subscribe("status", lambda text: print(text))
        bus.emit("status", "正在思考…")
        unsub()                      # 退订（subscribe 返回的取消函数）

    主题（topic）约定见 `yindun/worker/agent_worker.py` 顶部的 WORKER_EVENTS：
    status / intermediate_result / finished / error / need_confirm / approval_expired。
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, topic: str, handler: Callable[[Any], None]) -> Callable[[], None]:
        """订阅主题，返回一个"退订"函数（幂等，可安全重复调用）。"""
        with self._lock:
            self._subscribers[topic].append(handler)

        def _unsubscribe() -> None:
            self.unsubscribe(topic, handler)

        return _unsubscribe

    def unsubscribe(self, topic: str, handler: Callable[[Any], None]) -> None:
        with self._lock:
            handlers = self._subscribers.get(topic)
            if not handlers:
                return
            try:
                handlers.remove(handler)
            except ValueError:
                pass

    def emit(self, topic: str, payload: Any = None) -> None:
        """同步派发事件；订阅者异常被隔离。"""
        with self._lock:
            handlers = list(self._subscribers.get(topic, ()))
        for handler in handlers:
            try:
                handler(payload)
            except Exception as exc:  # 界面/桥接的问题不该影响推理主流程
                print(f"[EventBus] 订阅者处理 {topic} 失败：{exc}")

    def clear(self, topic: Optional[str] = None) -> None:
        """清空订阅（不传 topic 则清空全部）。"""
        with self._lock:
            if topic is None:
                self._subscribers.clear()
            else:
                self._subscribers.pop(topic, None)

    def subscriber_count(self, topic: str) -> int:
        with self._lock:
            return len(self._subscribers.get(topic, ()))
