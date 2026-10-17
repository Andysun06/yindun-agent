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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PLUGIN_ID = "laya_risk"          # 宿主注入的真实值会覆盖它
PLUGIN_DATA_DIR = ""

# ── laya 模型（可选接入；加载只做一次，失败后不再重试导入）──
_ROUTER_LOCK = threading.Lock()
_ROUTER: Any = None
_ROUTER_FAILED = False

# ── 问句与阈值：**按实测重新选过**（V3.5.8），不是拍的 ──
# 实测（本机 laya 0.3.27 + multilingual checkpoint，2026-10-17）：
#   · 原来那句中文复合问句（"破坏数据、超出请求范围、或不可逆"）语义上要模型同时判三件事，
#     对"用户只要求看文件、却要删除"这种明显越权只给 0.0019——**方向都是反的**；
#     且它每次都要走"复合判断"，数值几乎不随动作的危险程度变化。
#   · 换成"动作本身是否破坏/不可逆"这一句（英文、按 laya 的问句约定用反引号指名状态字段
#     `action`），可读性明显变好：只读类 ≤0.008（开发集 4 例 + 留出集 4 例全对），
#     破坏类出现 0.11 / 0.18 / 0.66 / 0.89 这样的高值。
#   · ★ 但必须如实记录它的**能力边界**：留出集整体判别 5/8——
#     `del /s /q`、`diskpart clean`、`format D:` 这类**命令行破坏性动作会被判成低危**。
#     所以 laya 的模型分级只能当"额外一缕线索"，**不能替代**正则规则与人工判断；
#     正则路径照旧把删除类判高危，两条提示一起显示，来源各自标注。
#   · 由此确定分工：**"动作危险不危险"问 laya；"是否超出用户请求"交给 decision_hint（LLM）**
#     ——后者用真实模型实测三条语义用例全部判对，而 laya 这条路测下来是反的。
_QUESTIONS = {
    "harm": {
        "type": "noul",
        "instructions": "Would executing `action` destroy or irreversibly change data?",
    },
}
# 阈值取自开发集分布（只读 0.0004~0.0106 ｜ 破坏性 0.1057~0.6581）：
#   0.10 分界把只读与"删除文件"分开；0.30 以上是"删整份资料 / drop database"这一档。
_HIGH, _MID = 0.30, 0.10
# 固定用多语言 checkpoint：本机**英文 checkpoint 权重没下全**（缺 5 个文件），
# 而 laya 的路由是按"文本里拉丁字母占比"自动选的——纯 ASCII 状态（例如英文文件名、
# 或没有用户原话时）会被路由到英文 checkpoint，然后卡在下载上直到超时。
# 钉死之后行为不再随输入里有没有汉字而变（这是"结果不稳定"的一个真实来源）。
_LAYA_MODEL = "multilingual"
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
    """插件数据目录：宿主会注入 PLUGIN_DATA_DIR（生产路径永远走这条）。

    ★ 没有注入时（离机调试、别的模块直接 import 这个文件）**不要**回退到插件所在目录：
    那会把默认规则写进安装目录 / 源码树——实测踩到过（源码树里冒出一个 risk_rules.txt）。
    回退到系统临时目录：可写、一次性、不污染仓库与安装位置，功能照常可用。
    """
    if PLUGIN_DATA_DIR:
        return Path(PLUGIN_DATA_DIR)
    import tempfile
    base = Path(tempfile.gettempdir()) / "yindun_laya_plugin"
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
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

# 预热协作：宿主在启用插件时会在后台调 preload()，而第一次真实审批可能恰好落在
# 预热进行中。此时**不要另起一条冷推理**（两条冷推理互相争抢只会更慢），
# 而是等预热完成后直接用已经热好的模型——所以这里用一个事件把两边串起来。
_WARM_LOCK = threading.Lock()
_WARMING = threading.Event()      # 预热正在进行
_WARMED = threading.Event()       # 预热已完成（无论成功失败，避免重复等）

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
    # 注意：这里**不等**预热。实测（V3.5.7）等待只会白等满本插件的上限、
    # 让这次审批更晚给出结论——剩下的半秒钟谁也没省下。
    # 预热的价值在"之后每一次审批"：等模型热好，一次推理只要 0.2 秒。
    #
    # ★ 状态改成**字典**（V3.5.8）：laya 的问句约定是用反引号指名状态字段（`action`），
    #   状态为 dict 时它会被序列化成 JSON，字段名与问句才能对上。早期把"请求+操作"
    #   拼成一整段中文字符串，问句里没有任何字段名可指——模型看到的是一个无归属的陈述句。
    #   用户原话仍然带在状态里（`request`）：虽然实测它对"是否相符"无能为力，
    #   但动作的危险程度会受请求措辞影响，留着更贴近真实上下文。
    request = str(context.get("user_request") or "").strip()[:300]
    state = {
        "action": haystack,
        "request": request or "（本轮没有可对照的用户原话）",
    }
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            # 钉死 checkpoint：不钉的话，纯 ASCII 输入会被路由到本机**没下全**的英文权重上，
            # 然后卡在下载里直到超时（实测），表现成"同样的操作有时快有时慢、有时没结论"。
            result = router.predict(state, _QUESTIONS, model=_LAYA_MODEL)
            box["p"] = float(result["answers"]["harm"]["noul"])
        except TypeError:
            # 老版本 laya 没有 model 参数：退回不钉路由，但把这件事实说清楚
            try:
                result = router.predict(state, _QUESTIONS)
                box["p"] = float(result["answers"]["harm"]["noul"])
                box["unpinned"] = True
            except Exception as exc:
                box["err"] = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            box["err"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(_LAYA_WAIT)
    if worker.is_alive():
        # 让它在后台继续，本次主动落正则——弹窗仍有提示，且绝不出现"宿主超时、整条 advisory 消失"。
        # ★ 提示语要说实测原因（V3.5.7）：本机实测 laya 本机模型对**每个新操作**约需 20~25 秒
        #   （同一操作第二次会命中插件内部缓存，0.2 秒），不是"正在下载权重"——权重早就下好了，
        #   把耗时说成下载是给自己找借口，也会让用户以为是临时现象。
        print(f"[laya_risk] laya 模型推理超过 {_LAYA_WAIT:.0f}s 上限（本机实测每个新操作约 20~25s），本次用正则规则分级")
        return None
    if "err" in box:
        print(f"[laya_risk] laya 推理失败，本次改用正则规则：{box['err']}")
        return None
    p = box["p"]
    level = "高危" if p >= _HIGH else ("中危" if p >= _MID else "低危")
    # 文案要如实：① 写明这是"动作的危险程度"（不是"是否越权"——那是 decision_hint 的活）；
    # ② 把概率原样给出来，人能看到它是"一缕线索"而不是判决；
    # ③ 低危时点明已知边界：命令行破坏性动作本机实测会被判低（留出集 5/8），
    #    否则"低危"两个字会被当成"安全"——那正是安全产品最不该给的错觉。
    tail = "（laya 对命令行破坏性动作判别有限，请以正则规则与人工判断为准）" if level == "低危" else ""
    return {"level": _LABEL[level],
            "text": f"风险分级（laya 模型）：{level} · 动作危害概率 {p:.2f}{tail}"}


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


# ── 局域网 laya 服务（V3.5.3 新增的一级接入）──
# 企业内网可以把 laya 部署成一个服务，员工机器不装模型也能拿到分级。
# 接口口径**如实公开**：OpenAI 兼容的 /chat/completions，回复里需要有 0–1 的概率
# 或「高危/中危/低危」字样；解析不出就报错回退正则——绝不把"连上了"当成"分级可用"。
_LAN_SYSTEM = ("你是操作风险分级器。判断待执行操作是否可能破坏数据、超出用户请求范围或不可逆。"
               "只回答一个 0 到 1 之间的小数（越大越危险），不要解释。")
_LEVEL_WORDS = (("高危", 1.0), ("中危", 0.5), ("低危", 0.0))


def _source_config() -> Dict[str, Any]:
    """读取接入配置（与 app/laya_service.py 写的是同一份文件，单一事实来源）。

    读不到/坏了就回到默认（本机优先、自动回退），并在提示里说明来源——
    不让"配置坏了"变成"能力静默消失"。
    """
    path = _data_dir() / "laya_source.json"
    default = {"source": "local", "auto": True, "endpoint": "", "model": "", "timeout": 8}
    try:
        raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(raw, dict):
            return default
    except Exception:
        # 兼容旧版：只有 backend.txt（auto/laya/regex）时按它映射。
        # ★ 语义必须等价：旧版 `laya` 是"只用模型，模型不可用就不提示"（**不回退**），
        #   映射成 auto=True 会让它在模型缺失时偷偷回退正则——那等于改了老用户的行为。
        legacy = _backend_choice()
        if legacy == "regex":
            return dict(default, source="regex", auto=True)
        if legacy == "laya":
            return dict(default, source="local", auto=False)
        return dict(default, source="local", auto=True)
    out = dict(default)
    src = str(raw.get("source") or "").strip().lower()
    if src in ("local", "lan", "regex"):
        out["source"] = src
    ep = str(raw.get("endpoint") or "").strip()
    out["endpoint"] = ep if ep.lower().startswith(("http://", "https://")) else ""
    out["model"] = str(raw.get("model") or "").strip()[:80]
    out["auto"] = bool(raw.get("auto", True))
    try:
        out["timeout"] = max(2, min(int(raw.get("timeout") or 8), 60))
    except (TypeError, ValueError):
        pass
    return out


def score_via_endpoint(endpoint: str, model: str, haystack: str,
                       timeout: int = 8) -> Tuple[bool, str, str]:
    """向局域网服务发一次分级请求。返回 (是否成功, 说明, 级别)。

    成功 = HTTP 通 + 回复里能解析出概率或级别；只有 HTTP 通不算成功——
    那只是"服务活着"，不是"分级可用"。
    """
    import urllib.error
    import urllib.request

    url = endpoint.rstrip("/") + "/chat/completions"
    payload = {
        "model": model or "laya",
        "messages": [{"role": "system", "content": _LAN_SYSTEM},
                     {"role": "user", "content": haystack[:1500]}],
        "temperature": 0,
        "max_tokens": 32,
    }
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D102
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json"})
    try:
        with opener.open(req, timeout=max(2, min(int(timeout or 8), 60))) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            return False, f"服务拒绝访问（HTTP {exc.code}）：确认该内网服务是否允许本机调用", ""
        return False, f"服务返回 HTTP {exc.code}", ""
    except urllib.error.URLError as exc:
        return False, f"连不上：{exc.reason}", ""
    except Exception as exc:
        return False, f"请求失败：{type(exc).__name__}: {exc}", ""

    try:
        parsed = json.loads(body)
        text = str((parsed.get("choices") or [{}])[0].get("message", {}).get("content") or "")
    except Exception:
        text = body
    level, prob = _parse_level(text)
    if level is None:
        return False, f"服务有响应但无法解析分级结果（回复片段：{text[:80] or '空'}）", ""
    return True, f"{text.strip()[:80] or '(空回复)'}", level


def _parse_level(text: str) -> Tuple[Optional[str], Optional[float]]:
    """从服务回复里解析级别：优先中文级别词，其次 0–1 的小数。"""
    text = str(text or "")
    for word, prob in _LEVEL_WORDS:
        if word in text:
            return word, prob
    match = re.search(r"(?<![\d.])(0(?:\.\d+)?|1(?:\.0+)?)(?![\d.])", text)
    if match:
        prob = float(match.group(1))
        level = "高危" if prob >= _HIGH else ("中危" if prob >= _MID else "低危")
        return level, prob
    return None, None


def _grade_with_endpoint(haystack: str, context: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """局域网分级：失败一律返回 None（交给调用方回退正则并写明原因）。"""
    request = str(context.get("user_request") or "")[:300]
    state = f"用户请求：{request or '（本轮没有可对照的用户原话）'}\n待执行操作：{haystack}"
    ok, detail, level = score_via_endpoint(cfg.get("endpoint", ""), cfg.get("model", ""),
                                           state, int(cfg.get("timeout") or 8))
    if not ok or not level:
        print(f"[laya_risk] 局域网分级不可用，本次改用正则规则：{detail}")
        _remember_source("")
        return None
    _remember_source("局域网 laya 服务")
    return {"level": _LABEL[level], "text": f"风险分级（局域网 laya 服务）：{level}"}


# ── 最近一次分级的来源（设置页用它回答"到底有没有在工作"）──
_LAST_SOURCE = ""


def _remember_source(name: str) -> None:
    global _LAST_SOURCE
    _LAST_SOURCE = name


def last_source() -> str:
    return _LAST_SOURCE


def rules_overview() -> Tuple[str, int]:
    """给设置页用：规则文件路径 + 已载入条数（不触发任何分级）。"""
    path = _data_dir() / "risk_rules.txt"
    try:
        count = len(_load_rules())
    except Exception:
        count = 0
    return str(path), count


def preload() -> None:
    """宿主在**启用本插件时**（后台线程）调用：把本机 laya 模型先热起来。

    实测（V3.5.7）：laya 的 Router 构造只要 0.1 秒，但**首次前向**要 17~25 秒
    （之后同一进程内 0.2 秒）。插件自己的等待上限是 25 秒，于是"第一次审批"
    总是耗满上限、退化成正则规则——用户看到的第一次分级根本没有模型参与。
    这里先跑一次最小推理把热路径走通，第一次真实审批就能拿到模型结论。

    期间若有真实审批进来，`_grade_with_laya()` 会**等这次预热**而不是另起一条冷推理
    （两条冷推理互相争抢只会更慢）。

    只在配了本机模型档时预热；预热失败一律吞掉（不影响插件启用），
    也不做任何对外请求（局域网档的探测是用户显式点「测试连接」才做的事）。
    """
    try:
        cfg = _source_config()
        if cfg.get("source") not in ("local", "auto"):
            _WARMED.set()
            return
        router = _load_router()
        if router is None:
            _WARMED.set()
            return
        with _WARM_LOCK:
            if _WARMED.is_set():
                return
            _WARMING.set()
        t0 = time.time()
        router.predict({"action": "read_local_file 示例.txt"}, _QUESTIONS, model=_LAYA_MODEL)
        print(f"[laya_risk] 本机模型预热完成（{time.time() - t0:.1f}s），首次审批即可用模型分级")
    except Exception as exc:
        print(f"[laya_risk] 预热失败（不影响启用，首次审批会回退正则）：{type(exc).__name__}: {exc}")
    finally:
        _WARMING.clear()
        _WARMED.set()


def advisory_for_approval(context: Dict[str, Any]) -> Optional[Dict[str, str]]:
    """钩子入口（三级来源，按数据边界由近及远）：

      source=local（默认）：本机 laya → 局域网（若配置了）→ 正则
      source=lan          ：局域网 → 正则
      source=regex        ：只用正则
      auto=false          ：只用所选那一级，不可用时不假装（返回 None）
    无论走哪条路径，提示里都写明**本次来源**，绝不冒充。
    """
    if not isinstance(context, dict):
        return None
    cfg = _source_config()
    choice = cfg["source"]
    auto = cfg["auto"]
    haystack = _build_haystack(context)
    if not haystack:
        return None

    if not auto:
        # 用户显式关掉回退：只用所选那一级；不可用就什么都不提示，而不是偷偷换成正则
        if choice == "regex":
            return _grade_with_rules(haystack)
        if choice == "local":
            router = _load_router()
            return _grade_with_laya(router, haystack, context) if router is not None else None
        if cfg.get("endpoint"):
            return _grade_with_endpoint(haystack, context, cfg)
        return None

    if choice in ("local", "lan"):
        router = _load_router() if choice == "local" else None
        graded = None
        if router is not None:
            graded = _grade_with_laya(router, haystack, context)
            if graded is not None:
                _remember_source("本机 laya 模型")
        if graded is None and cfg.get("endpoint"):
            graded = _grade_with_endpoint(haystack, context, cfg)
        if graded is not None:
            return _merge_with_rules(graded, haystack)
    return _grade_with_rules(haystack)


def _merge_with_rules(graded: Dict[str, str], haystack: str) -> Dict[str, str]:
    """把模型结论与**确定性正则规则**取更严的一侧（V3.5.9）。

    为什么必须合并（这条是实现层的一个反直觉缺陷）：模型可用时，正则规则**根本没跑**——
    于是"装了 laya"反而把提示放松了：删除文件在正则路径是「高危」，在模型路径可能是「中危」甚至「低危」。
    对安全产品来说这是反的：**多装一个模型不该让提示变松**。
    现在的规则很简单、可预测：
      · 正则判「高危」（破坏性/系统级动作）→ 以它为准，模型结论附在后面当补充；
      · 其余情况 → 以模型结论为主（它更细，带概率）；
      · 正则没命中 → 保持模型结论原样。
    注：两者都只影响**提示文字**，不参与任何放行判定。
    """
    rules = _grade_with_rules(haystack) or {}
    if rules.get("level") == "warn" and "高危" in str(rules.get("text", "")):
        model_note = ""
        if graded.get("text"):
            # 只说"另一路信号"给了什么，不重复整句
            if "动作危害概率" in graded["text"]:
                prob = graded["text"].split("动作危害概率", 1)[-1].strip().split("（")[0].strip()
                model_note = f"；laya 模型：动作危害概率 {prob}"
            else:
                model_note = f"；laya 模型：{graded['text'].split('：', 1)[-1][:40]}"
        return {"level": "warn", "text": rules["text"] + model_note}
    return graded

