# -*- coding: utf-8 -*-
"""
隐盾 —— 推理取消语义回归测试

背景（红队发现 → 修复 → 回归固化）：
  旧实现用 `with ThreadPoolExecutor(...)` 包装 llm.invoke()，并在 with 块内抛
  KeyboardInterrupt 表示取消。但 with 退出会执行 shutdown(wait=True)，会一直
  阻塞到在途的模型请求自然结束——"0.5 秒取消"只是 UI 假象：用户看到界面立刻复位，
  后台仍在占满算力，worker 线程也无法及时退出，取消后立刻重发消息会出现两个
  并发的 LLM 调用。

  修复方式：显式 shutdown(wait=False, cancel_futures=True)，取消分支立即返回。

本测试用"慢速假 LLM"验证取消是否真的立即返回：
  假 LLM 每次 invoke 阻塞 5 秒；0.3 秒后触发 cancel()，
  断言调用在 2 秒内以 KeyboardInterrupt 返回（旧实现需约 5 秒）。

用法：python test_worker_cancel.py   （退出码 0 = 通过）
"""
import os
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from yindun.worker.agent_worker import Worker  # noqa: E402

FAKE_LLM_SECONDS = 5.0
CANCEL_AFTER = 0.3
MAX_ACCEPTABLE = 2.0


class SlowFakeLLM:
    """假 LLM：invoke 阻塞 FAKE_LLM_SECONDS 秒，用于测量取消的真实响应时间。"""

    def invoke(self, messages):
        time.sleep(FAKE_LLM_SECONDS)
        return "（慢速假模型的回答）"


def main() -> int:
    _app = QCoreApplication.instance() or QCoreApplication([])

    print("=" * 78)
    print("【推理取消语义回归测试】慢速模型下取消必须立即返回")
    print("=" * 78)

    worker = Worker()
    worker.llm = SlowFakeLLM()

    started = threading.Event()
    outcome = {}
    elapsed_holder = {}

    def _run():
        started.set()
        t0 = time.monotonic()
        try:
            worker._invoke_llm_with_cancel_check([], check_interval=0.1)
            outcome["result"] = "returned"
        except KeyboardInterrupt:
            outcome["result"] = "cancelled"
        except Exception as exc:  # noqa: BLE001
            outcome["result"] = f"error: {type(exc).__name__}: {exc}"
        elapsed_holder["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    started.wait(timeout=2)
    time.sleep(CANCEL_AFTER)
    worker.cancel()
    t.join(timeout=FAKE_LLM_SECONDS + 3)

    elapsed = elapsed_holder.get("elapsed")
    result = outcome.get("result")
    print(f"  假模型单次生成耗时: {FAKE_LLM_SECONDS:.1f}s")
    print(f"  取消触发时刻: {CANCEL_AFTER:.1f}s")
    print(f"  实际返回方式: {result}")
    print(f"  实际返回耗时: {elapsed:.2f}s" if elapsed is not None else "  实际返回耗时: 线程未退出（超时）")

    ok = (result == "cancelled") and (elapsed is not None) and (elapsed < MAX_ACCEPTABLE)
    print("\n" + "=" * 78)
    if ok:
        print(f"✅ 通过：取消在 {elapsed:.2f}s 内返回（阈值 {MAX_ACCEPTABLE}s），"
              "未等待模型生成结束 (wait=False 生效)")
        return 0
    print(f"❌ 失败：取消语义未生效（期望 {MAX_ACCEPTABLE}s 内以 KeyboardInterrupt 返回）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
