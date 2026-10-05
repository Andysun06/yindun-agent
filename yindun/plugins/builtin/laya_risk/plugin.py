# -*- coding: utf-8 -*-
"""内置插件 · laya 风险分级（advisory_for_approval）

做一件事：人工审批弹窗出现时，给待审批操作补一行"风险分级"提示
（高危 / 中危 / 低危），供人类在几秒内决定放行还是拦截。

两条路径，自动选择，提示里写明本次来源：
  · 本机装了 laya 决策模型（pip install laya，依赖 torch）→ 用模型给
    "该操作是否可能破坏数据 / 越权 / 不可逆"打一个概率分，按概率分级；
  · 没装（或模型加载/推理失败）→ 自动回退到**正则规则分级**：规则文件
    risk_rules.txt 在设置页可编辑、改动立即生效，默认自带常用规则开箱即用。
  正则路径绝不冒充模型结果；模型路径不吞掉正则路径的告警。

为什么做成插件而不是内核能力：
  · laya 是重依赖（torch + 权重），不是每个用户都装；内核保持精简，按需启用；
  · 它只是建议（advisory 钩子）：只影响弹窗提示，绝不参与放行判定
    ——判定类钩子被宿主 FORBIDDEN_HOOKS 永久禁止（见开发记忆 §5 安全不变量）。

数据边界：模型推理与规则匹配全在本机，数据不出本机；
唯一联网点是 laya 首次使用时从 Hugging Face 下载权重（涉密环境可预先离线导入
~/.cache/huggingface/ 的模型目录）。首次推理可能因下载而很慢——插件内部只等
25 秒（远小于宿主 60s 硬超时），到点主动落正则规则给提示，下载继续在后台进行，
权重就绪后的下一次审批自动用上模型。
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ID = "laya_risk"          # 宿主注入的真实值会覆盖它
PLUGIN_DATA_DIR = ""

# ── laya 模型（可选接入；加载只做一次，失败后不再重试导入）──
_ROUTER_LOCK = threading.Lock()
_ROUTER: Any = None
_ROUTER_FAILED = False

# noul = P(true)：一次前向回答"是否有破坏性/越权/不可逆风险"
_QUESTIONS = {
    "risk": {
        "type": "noul",
        "instructions": "这个待执行操作是否可能破坏数据、超出用户原话请求的范围，或产生不可逆后果？",
    },
}
_HIGH, _MID = 0.6, 0.3          # 概率 → 级别阈值
_LABEL = {"高危": "warn", "中危": "warn", "低危": "info"}

# ── 正则规则（回退路径）──
DEFAULT_RULES = """\
# laya 风险分级 · 正则回退规则（未接入 laya 模型时使用）
# 每行一条：级别|正则|说明   （级别=高危/中危/低危；# 开头是注释；说明里不要用竖线）
# 匹配对象是"工具名 + 路径 + 参数"的拼接文本，大小写不敏感。
# 默认规则只产生弹窗提示文字，不改变任何放行判定；按本单位情况增删改即可。

# —— 高危：破坏性、不可逆、系统级变更 ——
高危|delete_local_file|删除本地文件
高危|re:rm\\s+(-[a-zA-Z]+\\s+)*-[a-zA-Z]*[rf]\\s|rm -rf 递归强删
高危|re:\\b(del|rmdir)\\s+/|del /s 批量删除
高危|re:\\b(shred|wipe)\\b|粉碎删除
高危|re:\\bmkfs\\b|格式化文件系统
高危|re:\\bdd\\s+if=|裸盘写入
高危|re:diskpart|磁盘分区操作
高危|re:format\\s+[a-zA-Z]:\\s|格式化磁盘
高危|re:\\breg\\s+delete\\b|删除注册表项
高危|re:\\bnet\\s+user\\b|修改系统用户
高危|re:taskkill|强杀进程
高危|re:curl[^|]*\\|\\s*(ba)?sh|管道执行远程脚本
高危|re:\\bchmod\\s+777\\b|放开文件权限
高危|re:DROP\\s+(TABLE|DATABASE)|删除数据库表

# —— 中危：写入、覆盖、外发 ——
中危|write_local_file|写入本地文件
中危|overwrite_local_file|覆盖写文件
中危|re:\\bmv\\s+|move-item|移动改名
中危|re:\\b(curl|wget|invoke-webrequest)\\b|网络请求下载
中危|re:\\b(scp|rsync|ftp)\\b|向外传输文件
中危|re:git\\s+push|推送到远端仓库
中危|re:crontab|计划任务

# —— 低危：只读操作 ——
低危|read_local_file|读取文件
低危|list_local_files|列目录
低危|search_in_files|搜索文件
"""

_RANK = {"高危": 3, "中危": 2, "低危": 1}
_MAX_RULES = 500
_MAX_PATTERN_CHARS = 160

_lock = threading.Lock()
_cache: Dict[str, Any] = {"hash": None, "rules": None}


def _data_dir() -> Path:
    base = Path(PLUGIN_DATA_DIR) if PLUGIN_DATA_DIR else Path(__file__).resolve().parent
    return base


def _rules_path() -> Path:
    return _data_dir() / "risk_rules.txt"


def _backend_path() -> Path:
    return _data_dir() / "backend.txt"


def _ensure_backend() -> None:
    """首次使用时写入带注释的默认选择文件（auto = 有 laya 用 laya，否则正则）。"""
    try:
        p = _backend_path()
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("# 分级后端：auto / laya / regex（只取第一个词，# 开头是注释）\nauto\n",
                         encoding="utf-8")   # 落点受控：插件自己的数据目录内
    except Exception:
        pass


def _backend_choice() -> str:
    _ensure_backend()
    try:
        for line in _backend_path().read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return line.lower()
    except Exception:
        pass
    return "auto"


def _ensure_file(path: Path) -> None:
    """首次使用时写入带注释的默认规则，设置页一眼看懂格式、开箱即有分级。"""
    try:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(DEFAULT_RULES, encoding="utf-8")   # 落点受控：插件自己的数据目录内
    except Exception:
        pass          # 数据目录不可写时降级为"无自定义规则"，不影响主流程


def _parse_rules(text: str) -> Tuple[List[Tuple[int, Any, str]], int]:
    """级别|正则|说明 → [(权重, 匹配器, 说明)]。以 re: 开头按正则，否则按包含匹配。"""
    rules: List[Tuple[int, Any, str]] = []
    skipped = 0
    for line in text.splitlines():
        item = line.strip()
        if not item or item.startswith("#") or len(rules) >= _MAX_RULES:
            continue
        parts = item.split("|")
        level = parts[0].strip()
        if level not in _RANK or len(parts) < 2:
            skipped += 1
            continue
        # 说明取最后一个竖线之后的部分，中间整体还原为模式（允许正则里含竖线）
        pattern = "|".join(parts[1:-1]).strip() if len(parts) >= 3 else (parts[1].strip() or "")
        desc = parts[-1].strip() if len(parts) >= 3 else ""
        if not pattern:
            skipped += 1
            continue
        if len(pattern) > _MAX_PATTERN_CHARS:
            skipped += 1
            continue
        if pattern[:3].lower() == "re:":
            try:
                matcher = re.compile(pattern[3:], re.IGNORECASE)
            except re.error:
                skipped += 1
                continue
        else:
            matcher = pattern.lower()          # 包含匹配：存小写字串，分级时判断
        rules.append((_RANK[level], matcher, desc or pattern[:24]))
    return rules, skipped


def _load_rules() -> List[Tuple[int, Any, str]]:
    """读取规则文件并编译。★失败绝不静默、失败结果绝不进缓存（同 custom_dict 的教训）。

    ★缓存按**内容哈希**失效，而不是文件 mtime：Windows 文件时间戳粒度约 15.6ms，
    同一时间片内的两次写入会拿到相同的 mtime——"清空规则/改规则"后仍拿旧规则分级
    （实测踩到：连续两次写规则文件，第二次不生效）。规则文件很小（宿主上限 256KB），
    每次读取+哈希的成本可以忽略。
    """
    path = _rules_path()
    _ensure_file(path)
    text = None
    for attempt in (1, 2):                 # 文件可能被其它进程短暂占用，重试一次
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            break
        except Exception as exc:
            if attempt == 2:
                print(f"[laya_risk] 规则读取失败（{path}）：{type(exc).__name__}: {exc} —— 本次无规则可用")
                return []
            time.sleep(0.05)
    digest = hashlib.sha256((text or "").encode("utf-8")).hexdigest()
    with _lock:
        if _cache["hash"] == digest and _cache["rules"] is not None:
            return _cache["rules"]
    rules, skipped = _parse_rules(text or "")
    if skipped:
        print(f"[laya_risk] 规则有 {skipped} 条被跳过（级别非法 / 正则非法 / 超长），其余规则照常生效")
    with _lock:
        _cache["hash"], _cache["rules"] = digest, rules
    return rules


def _build_haystack(context: Dict[str, Any]) -> str:
    tool = str(context.get("tool") or "")
    path = str(context.get("path") or "")
    args = context.get("args") or {}
    try:
        brief = json.dumps(
            {k: (str(v)[:200] if not isinstance(v, (int, float, bool)) else v)
             for k, v in list(args.items())[:6]}, ensure_ascii=False)
    except Exception:
        brief = str(args)[:400]
    if brief in ("{}", "[]", "''", '""'):
        brief = ""
    hay = f"{tool} {path} {brief}".strip()
    return "" if hay in ("{}", "[]") else hay[:4000]


# ── laya 路径 ──
_LAYA_WAIT = 25.0   # 模型推理的内部等待上限：必须显著小于宿主钩子超时，
                    # 否则首次下载权重会把整次 advisory 拖成超时、弹窗反而一条提示都没有

def _load_router() -> Any:
    """尝试加载一次 laya；没装/失败则永久回退正则（不反复重试重依赖导入）。"""
    global _ROUTER, _ROUTER_FAILED
    if _ROUTER is not None or _ROUTER_FAILED:
        return _ROUTER
    with _ROUTER_LOCK:
        if _ROUTER is None and not _ROUTER_FAILED:
            try:
                from laya import Router
                _ROUTER = Router()
                print("[laya_risk] laya 决策模型已接入")
            except Exception as exc:
                _ROUTER_FAILED = True
                print(f"[laya_risk] laya 未接入（{type(exc).__name__}: {exc}）——本插件将用正则规则分级")
    return _ROUTER


def _grade_with_laya(router: Any, haystack: str, context: Dict[str, Any]) -> Optional[Dict[str, str]]:
    request = str(context.get("user_request") or "")[:300]
    state = f"用户请求：{request or '（本轮没有可对照的用户原话）'}\n待执行操作：{haystack}"
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            result = router.predict(state, _QUESTIONS)
            box["p"] = float(result["answers"]["risk"]["noul"])
        except Exception as exc:
            box["err"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(_LAYA_WAIT)
    if worker.is_alive():
        # 典型场景：首次使用正在下载权重。让它在后台继续，本次主动落正则——
        # 弹窗仍有提示，且绝不出现"宿主超时、整条 advisory 消失"。
        print(f"[laya_risk] laya 推理 {_LAYA_WAIT:.0f}s 未完成（首次可能正在下载权重），本次用正则规则分级")
        return None
    if "err" in box:
        print(f"[laya_risk] laya 推理失败，本次改用正则规则：{box['err']}")
        return None
    p = box["p"]
    level = "高危" if p >= _HIGH else ("中危" if p >= _MID else "低危")
    return {"level": _LABEL[level],
            "text": f"风险分级（laya 模型）：{level} · 破坏性/越权概率 {p:.2f}"}


# ── 正则路径 ──
def _grade_with_rules(haystack: str) -> Optional[Dict[str, str]]:
    rules = _load_rules()
    if not rules:
        # 规则为空＝文件不可用或用户清空了：如实说明，不编造"安全"结论
        return {"level": "info",
                "text": "风险分级（正则规则）：规则文件为空或不可用，未给出结论"}
    best: Tuple[int, Any, str] = (0, None, "")
    for rule in rules:
        rank, matcher, desc = rule
        if rank <= best[0]:
            continue
        hit = matcher.search(haystack) if hasattr(matcher, "search") else (matcher in haystack.lower())
        if hit:
            best = rule
    if best[0] == 0:
        return {"level": "info", "text": "风险分级（正则规则）：低危 · 未命中风险特征"}
    level = "高危" if best[0] == 3 else ("中危" if best[0] == 2 else "低危")
    return {"level": _LABEL[level], "text": f"风险分级（正则规则）：{level} —— {best[2]}"}


def advisory_for_approval(context: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """钩子入口：backend=auto 时先试 laya 模型、不可用/失败则回退正则规则；
    backend=regex 强制只用正则；backend=laya 只用模型（模型不可用则不提示）。"""
    if not isinstance(context, dict):
        return None
    choice = _backend_choice()
    haystack = _build_haystack(context)
    if not haystack:
        return None
    if choice != "regex":
        router = _load_router()
        if router is not None:
            graded = _grade_with_laya(router, haystack, context)
            if graded is not None:
                return graded
        if choice == "laya":
            return None      # 只用模型：模型不可用时不假装、不兜底
    return _grade_with_rules(haystack)
