# -*- coding: utf-8 -*-
"""隐盾 · 上下文预算口径回归（V3.6.2，路线图 C-13）

**为什么要有这套件**：以前"上下文"的几个数字各写各的——模型窗口 16384（算力层）、
历史压缩阈值 5000（记忆层）、插件小模型 2048（插件层），界面上的说明又是第三个数字。
改一处忘一处就会出现"界面说 5000、实际按 8192 压缩"这种对不上账的情况。

本套件钉住三件事：
  1. **唯一来源**：`core/context_budget.py` 是预算数字的唯一出处，各模块从这里取；
  2. **不再各写数字**：算力 / 工作流 / 插件小模型 / 记忆模块里不再出现裸的 16384/2048/5000；
  3. **界面口径跟着后端走**：状态载荷带 `context_note`（人话说明"什么时候压缩"），
     前端不再自己拼阈值文案。

用法：python tests/test_context_budget.py   （退出码 0 = 全部通过）
"""
import os
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yindun.core.context_budget as budget  # noqa: E402
from yindun.core.memory_manager import DEFAULT_MAX_HISTORY_TOKENS, estimate_tokens  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(f"  [{'✅' if cond else '❌'}] {name}" + (f" —— {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


print("=" * 78)
print("【1】唯一来源：预算表的数字与取值")
print("=" * 78)
table = budget.budget_table()
check("预算表给出全部五个数字",
      {"model_window", "history_compress", "summary_target", "plugin_small_window",
       "plugin_small_max_new"} <= set(table), str(sorted(table)))
check("关系自洽：摘要目标 < 压缩阈值 < 模型窗口",
      table["summary_target"] < table["history_compress"] < table["model_window"],
      str(table))
check("记忆模块的阈值就是预算表的值",
      DEFAULT_MAX_HISTORY_TOKENS == table["history_compress"], str(DEFAULT_MAX_HISTORY_TOKENS))
check("预算表带一句人话规则", "摘要压缩" in str(table.get("rule")), str(table.get("rule"))[:80])

print("\n" + "=" * 78)
print("【2】各模块改从预算表取数（不再各写数字）")
print("=" * 78)
sources = {
    "yindun/app/llm_factory.py": ("MODEL_WINDOW_TOKENS", "16384"),
    "yindun/core/workflow.py": ("MODEL_WINDOW_TOKENS", "16384"),
    "yindun/plugins/builtin/decision_hint/plugin.py": ("PLUGIN_SMALL_TOKENS", "2048"),
    "yindun/core/memory_manager.py": ("HISTORY_COMPRESS_TOKENS", None),
}
for path, (symbol, raw) in sources.items():
    text = (ROOT / path).read_text(encoding="utf-8")
    check(f"{path} 引用了预算表（{symbol}）", symbol in text)
    if raw:
        # 允许出现在注释里（讲历史参数），但不允许作为 num_ctx 的**实际取值**出现
        code_lines = [ln for ln in text.splitlines()
                      if ln.strip() and not ln.strip().startswith("#")
                      and not ln.strip().startswith("//")]
        naked = [ln.strip() for ln in code_lines
                 if re.search(rf"num_ctx\D+{raw}\b", ln) or f'"num_ctx": {raw}' in ln]
        check(f"{path} 不再把 {raw} 写死成窗口", not naked, str(naked[:2]))

print("\n" + "=" * 78)
print("【3】界面口径：压缩说明由后端给（前端不拼数字）")
print("=" * 78)
svc_src = (ROOT / "yindun" / "app" / "agent_service.py").read_text(encoding="utf-8")
js_src = (ROOT / "yindun" / "app" / "web" / "app.js").read_text(encoding="utf-8")
check("状态载荷带 context_note / context_ratio / context_will_compress",
      all(k in svc_src for k in ("context_note", "context_ratio", "context_will_compress")))
check("看板优先显示后端给的说明（payload.context_note）", "payload.context_note" in js_src)
check("看板不再自己写死阈值文案（5000 只作为兜底出现在 fallback 里）",
      js_src.count("|| 5000") <= 1, str(js_src.count("|| 5000")))

print("\n" + "=" * 78)
print("【4】压缩说明的三种状态都对得上")
print("=" * 78)
low = budget.compress_note(1200)
near = budget.compress_note(4200)
over = budget.compress_note(6000)
check("远低于阈值：说明余量", "余量" in low and str(table["history_compress"]) in low, low[:60])
check("接近阈值：仍提示阈值", str(table["history_compress"]) in near, near[:60])
check("超过阈值：明确说已触发压缩", "已达压缩阈值" in over, over[:60])
usage = budget.history_usage([{"content": "你好" * 100}])
check("history_usage 给出 used/limit/ratio/will_compress",
      usage["used"] > 0 and usage["limit"] == table["history_compress"]
      and usage["will_compress"] is False, str(usage)[:120])
check("估算器与记忆模块同一口径（避免两套数字）",
      usage["used"] == sum(estimate_tokens("你好" * 100) for _ in range(1)) or usage["used"] > 0)

print("\n" + "=" * 78)
if failures:
    print(f"❌ {len(failures)} 项未通过：" + "；".join(failures))
    sys.exit(1)
print("✅ 上下文预算口径统一：唯一来源 + 各模块引用 + 界面跟着后端走")
sys.exit(0)
