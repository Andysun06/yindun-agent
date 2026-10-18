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
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 仓库根目录

from yindun.worker.agent_worker import Worker  # noqa: E402
from yindun.core.privacy_engine import PrivacyEngine  # noqa: E402

# 注意：重构后 Worker 已与 Qt 解耦（不再是 QObject），因此本测试无需 QApplication/Qt 环境。

FAKE_LLM_SECONDS = 5.0
CANCEL_AFTER = 0.3
MAX_ACCEPTABLE = 2.0


class SlowFakeLLM:
    """假 LLM：invoke 阻塞 FAKE_LLM_SECONDS 秒，用于测量取消的真实响应时间。"""

    def invoke(self, messages):
        time.sleep(FAKE_LLM_SECONDS)
        return "（慢速假模型的回答）"


class StreamingFakeLLM:
    """流式假模型：分块吐文本、块间等待；用于验证真流式链路（V3.6.2）。

    块只要支持 `+` 合并与 `.content` 取文本即可（与 langchain 的 AIMessageChunk 同形状）。
    """

    class Chunk:
        def __init__(self, text):
            self.content = text

        def __add__(self, other):
            return StreamingFakeLLM.Chunk(self.content + getattr(other, "content", ""))

    def __init__(self, pieces, delay=0.05, fail_after=None):
        self.pieces = pieces
        self.delay = delay
        self.fail_after = fail_after
        self.emitted = 0

    def stream(self, messages):
        for idx, piece in enumerate(self.pieces):
            if self.fail_after is not None and idx >= self.fail_after:
                raise RuntimeError("假流式错误：连接中断")
            time.sleep(self.delay)
            self.emitted += 1
            yield StreamingFakeLLM.Chunk(piece)

    def invoke(self, messages):        # 回落路径（正常不应被用到）
        return StreamingFakeLLM.Chunk("".join(self.pieces))


def test_streaming_emits_partials() -> bool:
    """流式链路：逐块推送 + 合并结果完整 + 半个占位符扣住不推。"""
    print("\n" + "=" * 78)
    print("【真流式】生成中即推送（首字延迟），合并结果与整段调用一致")
    print("=" * 78)
    worker = Worker()
    worker._box_mapping = {}
    pieces = ["合同金额为 ", "85", " 万元，", "请联系", " [NAME_0_ab", "cd] 确认。"]
    worker.llm = StreamingFakeLLM(pieces, delay=0.02)
    seen = []
    worker.bus.subscribe("intermediate_result", lambda text: seen.append(text))

    merged = worker._invoke_llm_streaming([], check_interval=0.05, throttle_ms=0)
    joined = "".join(pieces)
    ok = True

    print(f"  收到渐进推送 {len(seen)} 次（共 {len(pieces)} 块）")
    for idx, text in enumerate(seen, 1):
        print(f"    [{idx}] {text!r}")
    if len(seen) < 2:
        print("  ❌ 推送次数不足：没有体现「边生成边出字」")
        ok = False
    if not seen or seen[-1] != joined:
        print(f"  ❌ 最后一次推送应与完整文本一致：{seen[-1]!r} != {joined!r}")
        ok = False
    if any("[NAME_0_ab" in text and "[NAME_0_abcd]" not in text for text in seen):
        print("  ❌ 推了半个占位符（应扣住等补齐）")
        ok = False
    if getattr(merged, "content", "") != joined:
        print(f"  ❌ 合并结果不完整：{getattr(merged, 'content', '')!r}")
        ok = False
    print("  ✅ 通过" if ok else "  ❌ 失败")
    return ok


def test_streaming_restores_placeholders() -> bool:
    """流式文本要先还原占位符再推给界面（模型看到占位符，用户该看到真值）。"""
    print("\n" + "=" * 78)
    print("【真流式】推送内容已还原占位符（不把 [NAME_…] 暴露给用户）")
    print("=" * 78)
    worker = Worker()
    engine = PrivacyEngine()
    masked, mapping = engine.anonymize("合同金额 85万元，联系人张伟。")
    placeholder = next((k for k, _ in mapping.items() if "NAME" in k), "")
    split_at = masked.find(placeholder) + 3 if placeholder else len(masked) // 2
    worker.llm = StreamingFakeLLM([masked[:split_at], masked[split_at:]], delay=0.02)
    worker._box_mapping = mapping
    seen = []
    worker.bus.subscribe("intermediate_result", lambda text: seen.append(text))
    worker._invoke_llm_streaming([], check_interval=0.05, throttle_ms=0)

    joined = "".join(seen)
    ok = True
    print(f"  模型输出（脱敏后）：{masked!r}")
    print(f"  界面看到的最后一次：{(seen[-1] if seen else '')!r}")
    if placeholder and placeholder in joined:
        print("  ❌ 占位符原样推给了用户（应还原成真值）")
        ok = False
    if "张伟" not in joined:
        print("  ❌ 还原后的真值没出现")
        ok = False
    print("  ✅ 通过" if ok else "  ❌ 失败")
    return ok


def test_streaming_cancel_still_fast() -> bool:
    """流式下的取消同样要"立即返回"，不能等模型吐完。"""
    print("\n" + "=" * 78)
    print("【真流式】取消语义不变：生成中途取消立即返回")
    print("=" * 78)
    worker = Worker()
    worker._box_mapping = {}
    worker.llm = StreamingFakeLLM(["块"] * 200, delay=0.1)      # 约 20 秒才吐完
    outcome, holder = {}, {}

    def _run():
        t0 = time.monotonic()
        try:
            worker._invoke_llm_streaming([], check_interval=0.1, throttle_ms=0)
            outcome["result"] = "returned"
        except KeyboardInterrupt:
            outcome["result"] = "cancelled"
        except Exception as exc:                                    # noqa: BLE001
            outcome["result"] = f"error: {type(exc).__name__}"
        holder["elapsed"] = time.monotonic() - t0

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.5)
    worker.cancel()
    t.join(timeout=6)
    elapsed, result = holder.get("elapsed"), outcome.get("result")
    ok = result == "cancelled" and elapsed is not None and elapsed < 2.0
    print(f"  返回方式 {result}，耗时 {(elapsed if elapsed is not None else -1):.2f}s（阈值 2.0s）")
    print("  ✅ 通过" if ok else "  ❌ 失败：流式下的取消没有立即生效")
    return ok


def test_streaming_error_is_reported() -> bool:
    """流式过程中的异常要照常抛给上层（由上层翻译成友好文案），不能静默吞掉。"""
    print("\n" + "=" * 78)
    print("【真流式】生成中途出错要如实抛出（不假装成功）")
    print("=" * 78)
    worker = Worker()
    worker._box_mapping = {}
    worker.llm = StreamingFakeLLM(["前一半", "后一半"], delay=0.02, fail_after=1)
    try:
        worker._invoke_llm_streaming([], check_interval=0.05, throttle_ms=0)
        print("  ❌ 失败：异常被吞掉了")
        return False
    except RuntimeError as exc:
        print(f"  ✅ 通过：异常照常抛出（{exc}）")
        return True


def main() -> int:
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
    else:
        print(f"❌ 失败：取消语义未生效（期望 {MAX_ACCEPTABLE}s 内以 KeyboardInterrupt 返回）")

    # ── 真流式（V3.6.2，路线图 C-12）──
    ok = test_streaming_emits_partials() and ok
    ok = test_streaming_restores_placeholders() and ok
    ok = test_streaming_cancel_still_fast() and ok
    ok = test_streaming_error_is_reported() and ok
    print("\n" + "=" * 78)
    print("✅ 全部通过：取消语义 + 真流式（渐进推送/占位符还原/取消/异常）"
          if ok else "❌ 有未通过项，见上")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
