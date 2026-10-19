# -*- coding: utf-8 -*-
"""隐盾 · 上下文预算表（V3.6.2，路线图 C-13）

**为什么要有这张表**：以前"上下文"这几个数字各写各的——模型窗口 `num_ctx=16384`（算力层）、
历史摘要阈值 `DEFAULT_MAX_HISTORY_TOKENS=5000`（记忆层）、插件小模型的 `num_ctx=2048`（插件层），
界面上的说明又是第三个数字。改一处忘一处，就会出现"界面说 5000、实际 8192"这种对不上账的情况。

现在全部收敛到这里：**所有模块从这里取数，界面也从这里取说明文字**。
`tests/test_context_budget.py` 会钉住"模块里不再各写数字"这条不变量。

口径说明（怎么读这张表）：
  · `MODEL_WINDOW_TOKENS`：一次请求能塞给模型的**总窗口**（提示词 + 历史 + 工具结果 + 回答）。
  · `HISTORY_COMPRESS_TOKENS`：**历史**部分的压缩阈值——超过就把早期消息摘要成一条（不是丢弃）。
    它只是窗口的一部分，剩余给系统提示、工具结果与本次回答。
  · `PLUGIN_SMALL_TOKENS`：插件里的小模型（审批提示、laya 分级）只做**短判定**，
    给它们小窗口是为了快，不是为了省内存。
  · `SUMMARY_TARGET_TOKENS`：摘要本身的目标长度（压缩后保留多少）。
"""
from __future__ import annotations

from typing import Dict, List

# ── 预算数字（唯一来源）────────────────────────────────
MODEL_WINDOW_TOKENS = 16384          # 主模型：提示 + 历史 + 工具结果 + 回答
HISTORY_COMPRESS_TOKENS = 5000       # 历史超此值触发摘要压缩
SUMMARY_TARGET_TOKENS = 900          # 摘要压缩后的目标长度
PLUGIN_SMALL_TOKENS = 2048           # 插件小模型（审批提示 / laya 分级）的窗口
PLUGIN_SMALL_MAX_NEW = 64            # 插件小模型的输出上限（只出短结论）

# 单轮注入模型的**附件正文总量**上限（字符，见 app/attachment.build_attachment_context）。
# 取值理由（不拍脑袋）：窗口 MODEL_WINDOW_TOKENS=16384 里，系统提示（常驻基线约 1.2k 字）
# 与历史（压缩阈值 5000 token）已占去大半，只把约 1/4 留给附件正文，剩下的才够放工具结果
# 与本次回答。按全项目唯一的估算口径 estimate_tokens（CJK 1 字 ≈ 1 token），
# 1/4 窗口 ≈ 4096 字；取 4000 作为"中文正文"的保守上限。
# 为什么要这个总量：单篇 30000 字的注入上限本身已经超过整个窗口，再挂第二篇必然把模型
# 侧顶爆（表现为回答退化/被截断），而界面上只显示"已挂载 N 个附件"，用户看不出原因。
ATTACHMENT_TOTAL_CHARS = 4000


def history_usage(messages: List[dict], estimate=None) -> Dict[str, object]:
    """给界面用：当前历史占了多少、阈值多少、还差多少触发压缩。

    `estimate` 允许调用方注入估算器（默认用 memory_manager 的共享估算器，
    避免"看板一套、记忆一套"的老问题）。
    """
    if estimate is None:
        from yindun.core.memory_manager import estimate_tokens as estimate
    used = sum(estimate(str(m.get("content", ""))) for m in (messages or []))
    limit = HISTORY_COMPRESS_TOKENS
    ratio = round(used / limit, 3) if limit else 0
    return {
        "used": used,
        "limit": limit,
        "ratio": ratio,
        "remaining": max(0, limit - used),
        "will_compress": used >= limit,
        "note": compress_note(used),
    }


def compress_note(used: int) -> str:
    """人话说明"什么时候会压缩"——界面上直接显示这句。"""
    limit = HISTORY_COMPRESS_TOKENS
    if used >= limit:
        return (f"本次历史约 {used} token，已达压缩阈值 {limit}："
                f"更早的对话会被摘要成一条（内容保留要点，不会直接丢弃）")
    return (f"本次历史约 {used} token / 阈值 {limit}；"
            f"到 {limit} 就会把更早的对话摘要压缩（约压到 {SUMMARY_TARGET_TOKENS} token），"
            f"当前还有约 {max(0, limit - used)} token 余量")


def budget_table() -> Dict[str, object]:
    """给界面/自检用的完整预算表（一个接口拿全，不再各处拼）。"""
    return {
        "model_window": MODEL_WINDOW_TOKENS,
        "history_compress": HISTORY_COMPRESS_TOKENS,
        "summary_target": SUMMARY_TARGET_TOKENS,
        "plugin_small_window": PLUGIN_SMALL_TOKENS,
        "plugin_small_max_new": PLUGIN_SMALL_MAX_NEW,
        "attachment_total_chars": ATTACHMENT_TOTAL_CHARS,
        "rule": ("历史累计超过 history_compress 就摘要压缩早期消息；"
                 "插件小模型只做短判定，用独立的小窗口；"
                 "单轮附件正文合计不超过 attachment_total_chars，超出部分改给检索指引"),
    }
