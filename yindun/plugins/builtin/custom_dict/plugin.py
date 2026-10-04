# -*- coding: utf-8 -*-
"""内置插件 · 自定义敏感词表（识别增强 / recognizer）

作用：把"内核正则覆盖不到、但本单位确实敏感"的词补进脱敏范围——
项目代号、内部称谓、专用术语、客户名等。

契约（由宿主与内核共同保证，改这个文件前先读 host.py 的 HOOK_SPECS）：
  · 入口函数名 = 钩子名：本文件必须定义 `recognizer(context)`；
  · `recognizer(context)` 接收 {"text": 原始正文}，返回**追加**的命中区间；
  · 内核会复核每个区间（越界 / 超长 / 跨行 / 重叠 / 超量一律丢弃），
    因此这里写错了最多是"这一条没生效"，不会造成泄露，也不可能让内容免于脱敏；
  · 返回的 type 会被强制加 PLUGIN_ 前缀，不会与内核实体类型撞名。

词表文件由宿主注入：`PLUGIN_DATA_DIR/words.txt`（内置插件指向 <APP_ROOT>/plugins_data/，
所以可在设置页直接编辑并立即生效，重启不丢）。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

PLUGIN_ID = "custom_dict"          # 宿主注入的真实值会覆盖它
PLUGIN_DATA_DIR = ""

ENTITY_TYPE = "CUSTOM_TERM"        # 内核最终记为 PLUGIN_CUSTOM_TERM
ENTITY_LEVEL = "机密"              # 未标注级别的词按"机密"对待（从严）
MAX_RULES = 2000
MAX_PATTERN_CHARS = 80

DEFAULT_WORDS = """# 自定义敏感词表 · 每行一条
# 1) 普通词：按字面匹配，命中后整词替换为占位符
# 2) 正则：以 re: 开头，例如 re:项目[0-9]{3}
# 3) # 开头为注释；本文件默认只有注释，因此默认不产生任何命中
#
# 示例（去掉行首的 # 即可生效）：
# 隐盾专项
# 星海计划
# re:[A-Z]{2,4}-[0-9]{3,6}
"""

_lock = threading.Lock()
_cache = {"stamp": None, "rules": []}


def _words_path() -> Path:
    base = Path(PLUGIN_DATA_DIR) if PLUGIN_DATA_DIR else Path(__file__).resolve().parent
    return base / "words.txt"


def _ensure_file(path: Path) -> None:
    """首次使用时写入带注释的示例文件，让用户在设置页一眼看懂格式。"""
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_WORDS, encoding="utf-8")   # 落点受控：插件自己的数据目录内
    except Exception:
        pass          # 数据目录不可写时降级为"无词表"，不影响主流程


def _load_rules():
    """读取词表并编译规则。

    ★ 失败绝不静默：词表读不出来时必须留下告警——否则界面显示"已启用、识别器已挂载"，
      实际却一条都没脱敏，对安全产品这是最糟的失败方式。
    ★ 失败结果绝不进缓存：缓存只存"成功读到的内容"，避免一次瞬时读取失败
      把后续所有调用都钉死在"无规则"上（Windows 上文件被短暂占用就会触发）。
    """
    path = _words_path()
    _ensure_file(path)
    try:
        stamp = path.stat().st_mtime_ns
    except Exception as exc:
        print(f"[custom_dict] 词表不可用（{path}）：{type(exc).__name__}: {exc}")
        return []
    with _lock:
        if _cache["stamp"] == stamp:
            return _cache["rules"]
    text = ""
    for attempt in (1, 2):                     # 文件可能被其它进程短暂占用，重试一次
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            break
        except Exception as exc:
            if attempt == 2:
                print(f"[custom_dict] 词表读取失败（{path}）：{type(exc).__name__}: {exc}"
                      f" —— 本次不做自定义脱敏")
                return []
            time.sleep(0.05)
    rules = []
    skipped = 0
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#") or len(rules) >= MAX_RULES:
            continue
        if item[:3].lower() == "re:":
            pattern = item[3:].strip()
            if not pattern or len(pattern) > MAX_PATTERN_CHARS:
                skipped += 1
                continue
            try:
                rules.append(("re", re.compile(pattern)))
            except re.error:
                skipped += 1
                continue
        else:
            if len(item) > MAX_PATTERN_CHARS:
                skipped += 1
                continue
            rules.append(("word", item))
    if skipped:
        print(f"[custom_dict] 词表有 {skipped} 条规则被跳过（正则非法 / 超长），其余规则照常生效")
    with _lock:
        _cache["stamp"], _cache["rules"] = stamp, rules
    return rules


def recognizer(context):
    """钩子入口（函数名必须与 manifest 里声明的钩子同名，宿主按名字查找）。"""
    text = context.get("text") if isinstance(context, dict) else None
    if not isinstance(text, str) or not text:
        return None
    rules = _load_rules()
    if not rules:
        return None
    spans = []
    for kind, rule in rules:
        if len(spans) >= 200:
            break
        if kind == "word":
            start = text.find(rule)
            while start != -1 and len(spans) < 200:
                spans.append({"start": start, "end": start + len(rule),
                              "type": ENTITY_TYPE, "level": ENTITY_LEVEL})
                start = text.find(rule, start + len(rule))
        else:
            for match in list(rule.finditer(text))[:200]:
                if match.end() > match.start():
                    spans.append({"start": match.start(), "end": match.end(),
                                  "type": ENTITY_TYPE, "level": ENTITY_LEVEL})
    return {"spans": spans} if spans else None


# 便于人工阅读/单测时按语义名调用；宿主只认钩子同名函数
recognize = recognizer
