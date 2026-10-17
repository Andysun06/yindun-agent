# -*- coding: utf-8 -*-
"""隐盾 · laya 风险分级「接入」服务（本机模型 / 局域网服务 / 正则回退）

为什么把 laya 做成"接入"而不是插件里的一行配置：
  laya 在项目里的价值是**给人工审批补一条独立的判断线索**，它不该是"装了就用、
  没装就静默消失"的隐藏能力。同一件事在不同企业的可行形态并不一样：
      · 单机用户：本机装了 laya（pip install laya + torch）；
      · 企业内网：公司台上部署了一个 laya 推理服务，员工机器不装模型；
      · 涉密/离线：什么模型都不装，用本机正则规则兜底。
  所以这里按"数据边界"分成三级，配置、探测、状态都放在同一处，
  与「模型接入」的三档算力同一套心智模型（数据不出本机 / 不出企业 / 纯离线）。

职责边界（重要）：
  · 本模块**只管配置与探测**，怎么做分级仍由插件 `laya_risk` 负责（它在审批钩子里读同一份配置）；
  · 分级结果**只影响审批弹窗的提示文字**，永远不参与放行判定——判定权在内核，见开发记忆 §5。

配置落点：插件的 `laya_source.json`（走宿主既有的 plugin_config_read/write 通道，
不新开旁路）。文件不存在 = 用默认（source=auto：本机 → 局域网 → 正则）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

PLUGIN_ID = "laya_risk"
CONFIG_NAME = "laya_source.json"
RULES_NAME = "risk_rules.txt"

# 三级来源：local（本机模型）/ lan（局域网服务）/ regex（正则回退）
SOURCES = ("local", "lan", "regex")
SOURCE_LABELS = {
    "local": "本机 laya 模型",
    "lan": "局域网 laya 服务",
    "regex": "正则规则（回退）",
}
# auto = 按数据边界由近及远自动挑选（默认）；显式选某一级时不再自动回退到更外的边界
DEFAULT_CONFIG: Dict[str, Any] = {
    "source": "local",      # 想要哪一级（local / lan / regex）
    "auto": True,           # 该级不可用时是否自动落到下一级（local→lan→regex）
    "endpoint": "",         # 局域网服务地址，例如 http://10.0.0.8:8080/v1
    "model": "",            # 局域网服务上的模型名（可空）
    "timeout": 8,           # 局域网请求超时（秒）
}

ENDPOINT_MAX = 200
MODEL_MAX = 80


def _clamp_str(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def normalize_config(raw: Any) -> Dict[str, Any]:
    """把任意输入收敛成合法配置（越界值一律回到安全默认，不抛异常给界面）。

    只接受 http(s) 地址：局域网接入是"我方主动发起的只读/打分请求"，
    允许 file:// 之类的协议会把一个配置项变成任意本地读取入口。
    """
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        src = str(raw.get("source") or "").strip().lower()
        if src in SOURCES:
            cfg["source"] = src
        endpoint = _clamp_str(raw.get("endpoint"), ENDPOINT_MAX)
        if endpoint and not endpoint.lower().startswith(("http://", "https://")):
            endpoint = ""
        cfg["endpoint"] = endpoint
        cfg["model"] = _clamp_str(raw.get("model"), MODEL_MAX)
        if "auto" in raw:
            cfg["auto"] = bool(raw.get("auto"))
        try:
            cfg["timeout"] = max(2, min(int(raw.get("timeout") or DEFAULT_CONFIG["timeout"]), 60))
        except (TypeError, ValueError):
            pass
    return cfg


class LayaService:
    """laya 接入的读/写/探测。依赖注入 plugin_config_read/write 与插件运行时快照。

    注入而不是直接 import 插件：配置通道只有宿主那一条（见插件安全不变量），
    这里必须走同一扇门，否则"设置页写的"和"插件读的"会变成两份真相。
    """

    def __init__(self, read_config, write_config, runtime_lookup=None) -> None:
        self._read = read_config          # (plugin_id, name) -> {"ok", "text", "path"} | {"ok": False, "error"}
        self._write = write_config        # (plugin_id, name, text) -> {"ok", ...}
        self._runtime = runtime_lookup or (lambda: {})

    # ── 配置读写 ──────────────────────────────
    def config(self) -> Dict[str, Any]:
        res = self._read(PLUGIN_ID, CONFIG_NAME) or {}
        if not res.get("ok"):
            return dict(DEFAULT_CONFIG)
        try:
            return normalize_config(json.loads(res.get("text") or "{}"))
        except (ValueError, TypeError):
            # 文件被手改坏了：不静默用坏值，回到默认并让调用方在 detail 里看到原因
            return dict(DEFAULT_CONFIG)

    def config_error(self) -> str:
        res = self._read(PLUGIN_ID, CONFIG_NAME) or {}
        if not res.get("ok"):
            return ""
        try:
            json.loads(res.get("text") or "{}")
            return ""
        except (ValueError, TypeError) as exc:
            return f"配置文件无法解析（已按默认处理）：{exc}"

    def save(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        cfg = self.config()
        if isinstance(patch, dict):
            merged = dict(cfg)
            merged.update({k: v for k, v in patch.items() if k in DEFAULT_CONFIG})
            cfg = normalize_config(merged)
        text = json.dumps(cfg, ensure_ascii=False, indent=2)
        res = self._write(PLUGIN_ID, CONFIG_NAME, text) or {}
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error") or "写入插件配置失败", "config": cfg}
        return {"ok": True, "config": cfg, "detail": self._save_detail(cfg)}

    @staticmethod
    def _save_detail(cfg: Dict[str, Any]) -> str:
        label = SOURCE_LABELS.get(cfg["source"], cfg["source"])
        tail = "（该级不可用时自动回退到下一级）" if cfg.get("auto") else "（只使用该级，不自动回退）"
        return f"已保存：优先使用 {label}{tail}"

    # ── 探测（都做真事，不假装）──────────────────
    def probe_local(self) -> Dict[str, Any]:
        """本机是否装了 laya：与插件里的判断同口径（import 探测），只是不吃权重。"""
        detail, ok = _probe_local_laya()
        return {"ok": ok, "detail": detail, "available": ok}

    def probe_lan(self, endpoint: str = "") -> Dict[str, Any]:
        """局域网服务连通性：只读探活（GET /models），不发任何业务数据。"""
        ep = _clamp_str(endpoint, ENDPOINT_MAX) or self.config().get("endpoint", "")
        if not ep:
            return {"ok": False, "detail": "还没有填服务地址"}
        if not ep.lower().startswith(("http://", "https://")):
            return {"ok": False, "detail": "地址需要以 http:// 或 https:// 开头"}
        ok, detail = _http_get_json(ep.rstrip("/") + "/models", self.config().get("timeout", 8))
        return {"ok": ok, "detail": detail, "available": ok}

    def try_lan(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        """真发一次分级请求并把结果回显——"能不能连"和"连上能不能用"是两件事。"""
        merged = dict(self.config())
        if isinstance(cfg, dict):
            merged.update({k: v for k, v in cfg.items() if k in DEFAULT_CONFIG})
        merged = normalize_config(merged)
        ep = merged.get("endpoint") or ""
        if not ep:
            return {"ok": False, "detail": "还没有填服务地址"}
        from yindun.plugins.builtin.laya_risk.plugin import score_via_endpoint
        ok, detail, level = score_via_endpoint(
            ep, merged.get("model") or "", _SAMPLE_HAYSTACK, int(merged.get("timeout") or 8))
        if ok:
            suffix = f"，判为「{level}」" if level else "（回复里没有可识别的级别字样）"
            return {"ok": True, "detail": f"服务已返回分级结果{suffix}：{detail}", "level": level}
        return {"ok": False, "detail": detail}

    # ── 汇总状态（设置页一次拉全）──────────────
    def status(self) -> Dict[str, Any]:
        cfg = self.config()
        local = self.probe_local()
        lan_cfg = bool(cfg.get("endpoint"))
        runtime = self._runtime() or {}
        rules_path, rules_count = _rules_info(self._read)
        active, active_label = _active_backend(cfg, local["available"], lan_cfg)
        return {
            "source": cfg["source"],
            "config": cfg,
            "local": local,
            "lan": {"available": lan_cfg,
                    "endpoint": cfg.get("endpoint", ""),
                    "detail": (f"已配置 {cfg.get('endpoint')}" if lan_cfg
                               else "未配置：需要内网服务地址（OpenAI 兼容 /chat/completions）")},
            "regex": {"path": rules_path, "count": rules_count},
            "active_backend": active,
            "active_label": active_label,
            "last_used": runtime.get("last_source") or "",
            "plugin_runtime": runtime,
            "config_error": self.config_error(),
        }


# ── 内部实现（纯函数，便于单测）─────────────────────

# 试打分的样本文本：故意选一个"看起来就该是高风险"的操作，便于人一眼判断模型是否在工作
_SAMPLE_HAYSTACK = "delete_local_file 路径=D:\\项目资料\\客户合同.docx 参数=递归删除"


def _probe_local_laya() -> tuple:
    """本机 laya 可用性：与插件同一判据（能否 import laya），并给出可执行的原因。"""
    import importlib.util
    try:
        spec = importlib.util.find_spec("laya")
    except (ImportError, ValueError) as exc:
        return f"探测 laya 失败：{type(exc).__name__}: {exc}", False
    if spec is None:
        return "本机未安装 laya（pip install laya；它依赖 torch，装好即可用）", False
    origin = getattr(spec, "origin", "") or "已安装"
    return f"本机已安装 laya（{origin}）", True


def _rules_info(read_config) -> tuple:
    """正则规则文件的**落盘路径与有效条数**。

    ★ 走宿主给的配置通道读文件、自己数行，**不 import 插件模块**：
    早期实现 `from yindun.plugins.builtin.laya_risk.plugin import rules_overview` 有两个问题——
      ① 那是**包路径导入**，与宿主按文件路径导入的那一份是两个模块对象（状态不互通）；
      ② 插件在"没有注入数据目录"时会把默认规则**写到插件所在目录**，
         于是源码树里凭空冒出一个 risk_rules.txt（实测踩到，已把回退改成临时目录）。
    这里只做"读文件 + 数有效行"，属于文件级信息，不碰插件的运行时状态。
    """
    res = read_config(PLUGIN_ID, RULES_NAME) or {}
    if not res.get("ok"):
        return f"（规则文件不可读：{res.get('error') or '未知原因'}）", 0
    text = res.get("text") or ""
    count = sum(1 for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#"))
    path = str(res.get("path") or RULES_NAME)
    if not text.strip():
        # 文件还没生成（插件首次真正用到规则时才写默认规则）——如实说明，不显示成"0 条规则"让人误以为坏了
        return f"{path}（首次触发审批分级时写入默认规则）", 0
    return path, count


def _active_backend(cfg: Dict[str, Any], local_ok: bool, lan_ok: bool) -> tuple:
    """当前实际会用哪一级：与插件的选择逻辑同序（本机 → 局域网 → 正则）。"""
    src = cfg.get("source", "local")
    auto = bool(cfg.get("auto", True))
    if src == "local":
        if local_ok:
            return "local", SOURCE_LABELS["local"]
        if auto and lan_ok:
            return "lan", f"{SOURCE_LABELS['lan']}（本机 laya 不可用，已自动落到这一级）"
        return "regex", f"{SOURCE_LABELS['regex']}（本机 laya 不可用）"
    if src == "lan":
        if lan_ok:
            return "lan", SOURCE_LABELS["lan"]
        if auto:
            return "regex", f"{SOURCE_LABELS['regex']}（局域网服务未配置）"
        return "lan", f"{SOURCE_LABELS['lan']}（未配置，且已关掉自动回退）"
    return "regex", SOURCE_LABELS["regex"]


def _http_get_json(url: str, timeout: int = 8) -> tuple:
    """只读探活：GET 一个 JSON 端点。禁用重定向，避免被引到任意地址再读一次。

    为什么不用 requests：插件可能要跑在用户机器上，laya 接入是可选能力，
    这里用标准库即可，不额外拉依赖；行为（超时/状态码/是否重定向）全部自己控制。
    """
    import urllib.error
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):   # noqa: D102
            return None

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    try:
        with opener.open(req, timeout=max(2, min(int(timeout or 8), 60))) as resp:
            body = resp.read(4096).decode("utf-8", "replace")
        return True, f"已连通（HTTP {resp.status}）：{body[:160]}"
    except urllib.error.HTTPError as exc:
        # 401/403 说明服务活着但要凭据——这也是有效结论，如实说
        if exc.code in (401, 403):
            return False, f"服务可达但拒绝访问（HTTP {exc.code}）：请确认该内网服务是否允许本机调用"
        if exc.code == 404:
            return False, "服务可达但没有 /models 接口（可能不是 OpenAI 兼容服务）；可用「试打分」再验证一次"
        return False, f"服务返回 HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return False, f"连不上：{exc.reason}"
    except Exception as exc:
        return False, f"探测失败：{type(exc).__name__}: {exc}"
