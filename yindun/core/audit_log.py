from yindun import APP_ROOT
# -*- coding: utf-8 -*-
"""
隐盾审计日志模块 - 全链路审计黑匣子
基于 HMAC-SHA256 哈希链的不可篡改审计日志系统
"""
import json
import hmac
import hashlib
import html as _html
import os
import re
import secrets
import threading
from pathlib import Path
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional
from enum import Enum

from yindun.utils.logger import get_logger

# 审计的异常/告警一律走统一日志出口（V3.6.1）：打包成 exe 后 print 会掉进临时解压目录，
# 现场排障时"审计链校验失败"这类关键线索必须留在 <APP_ROOT>/logs/yindun.log 里。
log = get_logger("audit")

# 审计 HMAC 密钥缓存（模块级，进程内只需加载一次）
_HMAC_KEY = b""
_HMAC_KEY_LOADED = False


def _audit_key_file() -> Path:
    """审计 HMAC 密钥存储位置。

    与 SecretManager 保持一致：存放在【用户目录】而非项目目录，
    避免"密钥与它要保护的审计日志同盘"导致防篡改能力被绕过。
    """
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        return base / "Yindun" / ".audit_hmac_key"
    return Path.home() / ".config" / "yindun" / ".audit_hmac_key"


def _anchor_dirs() -> List[Path]:
    """链锚点的**候选落点**（按优先级，越靠前越优先）。

    为什么要有回退：首选位置（用户目录）在某些现场是**只读**的（组策略收权、
    AppData 被同步/加锁、便携版从只读介质运行），此时写锚点会持续报
    `[Errno 13] Permission denied`；若因此干脆不写锚点，防篡改（尾部截断 / 降级改写）
    就事实上失效，而用户毫不知情 —— 这是 BUG-0002。宁可换一个位置写，也不能不写。

      1) 密钥所在的用户目录（首选：与密钥同处，删锚点得先攻破用户目录）；
      2) 程序（exe）目录下的 cache（与审计目录同机、但**不是**审计目录本身）；
      3) 用户主目录下的隐藏目录（便携版在只读介质上运行时仍能留下锚点）。

    实际落在哪个位置必须**如实上报**（见 `AuditLog.anchor_health()`），不能默默换地方。
    """
    candidates = [_audit_key_file().parent, Path(APP_ROOT) / "cache", Path.home() / ".yindun_anchors"]
    out: List[Path] = []
    seen = set()
    for directory in candidates:
        marker = str(directory)
        if marker in seen:
            continue
        seen.add(marker)
        out.append(directory)
    return out


def _get_hmac_key() -> Optional[bytes]:
    """加载审计 HMAC 密钥（32 字节）；首次运行自动生成并持久化到用户目录。

    ★ 安全加固（第5步收尾）：密钥由 yindun/cache/.audit_hmac_key 迁移至用户目录。
    迁移必须【复制同一把钥匙】而非重新生成——换了密钥会导致历史审计链校验全部失败。

    加载失败不静默：打印错误并返回 None，由调用方显式降级。
    密钥绝不明文写入 audit_chain.json 本身。
    """
    global _HMAC_KEY, _HMAC_KEY_LOADED
    if _HMAC_KEY_LOADED:
        return _HMAC_KEY or None
    key_file = _audit_key_file()
    legacy_file = Path(__file__).resolve().parent.parent / "cache" / ".audit_hmac_key"
    try:
        if key_file.exists():
            data = key_file.read_bytes()
        elif legacy_file.exists():
            # 旧位置存在 → 迁移（复制原钥匙，保持历史审计链可校验）
            data = legacy_file.read_bytes()
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(data)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            log.info("审计 HMAC 密钥已迁移至：%s", key_file)
            log.info("旧文件可手动删除：%s", legacy_file)
        else:
            data = secrets.token_bytes(32)
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key_file.write_bytes(data)
            try:
                os.chmod(key_file, 0o600)
            except OSError:
                pass
            print(f"[AuditLog] 审计 HMAC 密钥已生成：{key_file}")
        if len(data) < 32:
            raise ValueError("审计 HMAC 密钥长度不足 32 字节")
        _HMAC_KEY = data
    except Exception as e:
        log.error("审计 HMAC 密钥加载失败：%s", e)
        _HMAC_KEY = b""
        return None
    _HMAC_KEY_LOADED = True
    return _HMAC_KEY


def _canonical(entry: "AuditEntry", previous_hash: str) -> bytes:
    """审计条目的规范化字节串（compute_hash 与 verify_chain 共用，保证一致）"""
    return json.dumps({
        "timestamp": entry.timestamp,
        "event_type": entry.event_type,
        "severity": entry.severity,
        "message": entry.message,
        "details": entry.details,
        "session_id": entry.session_id,
        "previous_hash": previous_hash
    }, ensure_ascii=False, sort_keys=True).encode('utf-8')


class AuditEventType(Enum):
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    LLM_INPUT = "llm_input"
    LLM_OUTPUT = "llm_output"
    PRIVACY_SENSITIVE = "privacy_sensitive"
    ACCESS_CONTROL = "access_control"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    WORKFLOW_STEP_EXECUTED = "workflow_step_executed"
    CHAIN_RESTART = "chain_restart"   # 审计链重启标记（密钥轮换后重开新链）
    CONTROL_REPORT = "control_report"  # 控制面上报/取策略：本机留痕"什么离开了这台机器"（V3.5.0）


class AuditSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    SECURITY = "security"


class AuditEntry:
    def __init__(self, event_type: AuditEventType, severity: AuditSeverity,
                 message: str, details: dict = None, session_id: str = None):
        self.timestamp = datetime.now().isoformat()
        self.event_type = event_type.value
        self.severity = severity.value
        self.message = message
        self.details = details or {}
        self.session_id = session_id
        self.previous_hash = ""
        self.entry_hash = ""
        self.legacy = False     # 旧版无 HMAC 条目标记

    def compute_hash(self, previous_hash: str = "") -> str:
        self.previous_hash = previous_hash
        canonical = _canonical(self, previous_hash)
        key = _get_hmac_key()
        if key is None:
            # 密钥缺失：无法计算 HMAC，条目置空哈希（校验将失败），显式告警而非静默
            log.error("审计 HMAC 密钥缺失，无法计算哈希，条目将无法通过校验")
            self.entry_hash = ""
            return self.entry_hash
        self.entry_hash = hmac.new(key, canonical, hashlib.sha256).hexdigest()
        return self.entry_hash

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "severity": self.severity,
            "message": self.message,
            "details": self.details,
            "session_id": self.session_id,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash,
            "hash_algo": "hmac-sha256"
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'AuditEntry':
        entry = cls(
            event_type=AuditEventType(data.get("event_type")),
            severity=AuditSeverity(data.get("severity")),
            message=data.get("message", ""),
            details=data.get("details", {}),
            session_id=data.get("session_id")
        )
        entry.timestamp = data.get("timestamp", "")
        entry.previous_hash = data.get("previous_hash", "")
        entry.entry_hash = data.get("entry_hash", "")
        # 旧版（无 hash_algo=hmac-sha256 标记）→ 兼容读取，标记 legacy
        entry.legacy = data.get("hash_algo") != "hmac-sha256"
        return entry


def _format_audit_time(value: Any) -> str:
    """把审计时间从机器格式（ISO T + 微秒）转成员工能读的 `YYYY-MM-DD HH:MM:SS`。"""
    text = str(value or "").strip().replace("T", " ")
    if "." in text:
        text = text.split(".", 1)[0]
    return text


def _audit_html_row(entry) -> str:
    """渲染一行审计 HTML：所有字段先转义（防导出报告被用户输入注入脚本）。"""
    sev_raw = str(entry.severity)
    severity_class = ("severity-" + sev_raw
                      if sev_raw in ("info", "warning", "critical", "security")
                      else "severity-info")
    etype = _html.escape(str(entry.event_type))
    sev = _html.escape(sev_raw)
    ts = _html.escape(_format_audit_time(entry.timestamp))
    # ★ 渲染时再掩码一次（V3.6.21）：升级前写入的旧条目只做过当时的掩码规则，
    #   非 Luhn 形似卡号会以明文留在历史日志里；导出/展示不能把旧账本再泄一次。
    msg = _html.escape(_mask_text(str(entry.message)))
    details_obj = _mask_details(entry.details if isinstance(entry.details, dict) else {})
    details = _html.escape(_mask_text(json.dumps(details_obj, ensure_ascii=False)))
    return f"""<tr>
                    <td>{ts}</td>
                    <td><span class="event-type">{etype}</span></td>
                    <td><span class="severity {severity_class}">{sev}</span></td>
                    <td>{msg}</td>
                    <td class="details" title="{details}">{details}</td>
                </tr>"""


def _mask_text(text: str) -> str:
    """对任意文本中的敏感实体做掩码（审计落盘前统一调用）。

    直接复用 PrivacyEngine 完整脱敏管线（正则粗筛 + 算法精验 + 人名识别），
    使审计预览的防护强度与主链路一致——此前弱正则版会漏掉人名与需精验的实体。
    """
    if not text or not isinstance(text, str):
        return text
    try:
        from yindun.core.privacy_engine import PrivacyEngine
        masked, _ = PrivacyEngine().anonymize(text)
        # ★ 审计展示层的额外保险（V3.6.21）：15 位以上的连续数字即便没通过 Luhn，
        #   也可能是卡号/证件号；审计不该成为"二次泄露源"。保留前 6 后 4，中间打码。
        masked = re.sub(r"(?<!\d)(\d{6})\d{6,}(\d{4})(?!\d)",
                        lambda m: m.group(1) + "******" + m.group(2), masked)
        return masked
    except Exception:
        return text


def _mask_details(obj):
    """递归对 details 结构中的字符串做掩码"""
    if isinstance(obj, str):
        return _mask_text(obj)
    if isinstance(obj, dict):
        return {k: _mask_details(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mask_details(v) for v in obj]
    return obj


class AuditLog:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized') and self._initialized:
            return
        self._entries: List[AuditEntry] = []
        self._storage_path = APP_ROOT / "audit_logs"
        self._storage_path.mkdir(exist_ok=True)
        self._current_session_id = None
        # 并发安全锁（RLock：add_entry 会在持锁状态下调用 _save_logs）
        self._lock = threading.RLock()
        # 锚点运行态：最近一次写入结果（含实际落点/是否回退/是否降级）与最近读到的落点，
        # 供 anchor_health() 如实上报"锚点在哪、是否生效"。
        self._anchor_write_state: Dict[str, Any] = {}
        self._anchor_write_signature = None
        self._anchor_read_path: Optional[Path] = None
        self._load_logs()
        self._initialized = True

    def _load_logs(self):
        log_file = self._storage_path / "audit_chain.json"
        if log_file.exists():
            try:
                with open(log_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for entry_data in data:
                        entry = AuditEntry.from_dict(entry_data)
                        self._entries.append(entry)
            except Exception as e:
                log.error("审计日志加载失败：%s", e)

    # ── 链锚点：防"整链改写 + 尾部截断" ────────────────
    # 为什么需要它：哈希链只能证明"中间没被改"，防不住两种攻击——
    #   ① 降级改写：把条目里的 hash_algo 抹掉（legacy 标记是**从文件里读的**），
    #      再用无密钥的 sha256 重算整链，校验就会"通过"；
    #   ② 尾部截断：删掉最后若干条（或整份文件），剩下的链依然自洽。
    # 锚点把"最后一次已知的条数、链头、历史 legacy 前缀长度"用 HMAC 固定下来。
    #
    # ★ 锚点放在**密钥旁边**（用户目录），不放在审计目录里：与密钥同一个理由——
    #   锚点若与它要保护的审计日志同盘，攻击者删掉锚点就能让"截断"重新不可发现，等于白做。
    #   文件名带审计目录指纹：同一台机器上可能有多份便携副本，各自的链互不干扰。
    #   残余局限（如实记录）：能同时改写用户目录的攻击者（≈本机已被完全控制）
    #   仍可连锚点一起重做——任何纯本地审计都挡不住这一层。
    _ANCHOR_PREFIX = "audit_chain.anchor."
    _ANCHOR_SUFFIX = ".json"

    def _anchor_fingerprint(self) -> str:
        """审计目录的路径指纹（同一台机器上的多份便携副本据此各用各的锚点）。"""
        return hashlib.sha256(str(self._storage_path).encode("utf-8")).hexdigest()[:8]

    def _anchor_candidate_paths(self) -> List[Path]:
        """当前链在各候选落点上的锚点文件路径（按优先级）。"""
        name = f"{self._ANCHOR_PREFIX}{self._anchor_fingerprint()}{self._ANCHOR_SUFFIX}"
        return [directory / name for directory in _anchor_dirs()]

    def _anchor_path(self) -> Path:
        """首选锚点落点（密钥旁边）。实际落点可能因回退而不同，见 `anchor_health()`。"""
        return self._anchor_candidate_paths()[0]

    @staticmethod
    def _anchor_payload(count: int, head: str, legacy_prefix: int) -> str:
        """锚点被签名的内容（字段固定、顺序固定，避免 JSON 序列化差异导致校验漂移）。"""
        return json.dumps({"count": int(count), "head": str(head),
                           "legacy_prefix": int(legacy_prefix)},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _anchor_target_usable(path: Path) -> bool:
        """这个锚点落点现在**能不能写**（非破坏性判断，绝不碰真锚点、也不创建目录）。

        为什么不直接试写：直接试写会"顺手"把一条尚未校验过的链锚定下来，
        等于把"锚点缺失"这条告警自己抹掉 —— 那正是要防的事。所以只做无副作用检查：
          · 落点是不是被一个**目录**占住了（此时 write_text 必然 Errno 13）；
          · 现有落点文件是否可写（只读文件占位 → 不可写）；
          · 落点不存在时，最近一个已存在的祖先目录是否可写（**不实际创建**目录）。
        """
        try:
            if path.exists():
                if path.is_dir():
                    return False                       # 落点被目录占住 → 一定写不进
                return os.access(str(path), os.W_OK)
        except OSError:
            return False
        probe = path.parent
        while True:
            try:
                if probe.exists():
                    return os.access(str(probe), os.W_OK)
            except OSError:
                return False
            parent = probe.parent
            if parent == probe:
                return False                            # 走到盘根都不存在（如不存在的盘符）
            probe = parent

    def _anchor_writable_anywhere(self) -> bool:
        """是否还有任何一个候选落点可写（用来区分"锚点真的没了"与"根本写不进去"）。"""
        return any(self._anchor_target_usable(path) for path in self._anchor_candidate_paths())

    def _read_anchor(self) -> Optional[dict]:
        """读取并校验锚点（按候选落点优先级遍历）。

        返回：
          · None                        —— 所有候选落点都没有锚点；
          · {"tampered": True, ...}     —— 锚点存在但**不可信**（签名不符 / 内容损坏）；
          · {"unreadable": True, ...}   —— 锚点存在但**读不了**（权限 / 落点被占）：
                                           这属于环境问题，不能当成"被篡改"吓唬用户。
          · {"count","head","legacy_prefix","path"} —— 校验通过的锚点（path = 实际落点）。

        多个候选都有效时取**条数最大**的那个（最不容易被"退回到更早状态"绕过）；
        读到的落点记在 self._anchor_read_path，供界面如实展示"锚点在哪"。
        """
        key = _get_hmac_key()
        best: Optional[dict] = None
        problems: List[dict] = []
        for path in self._anchor_candidate_paths():
            try:
                if not path.exists():
                    continue
            except OSError:
                continue
            try:
                if path.is_dir():
                    raise IsADirectoryError(f"锚点落点被目录占住：{path}")
                raw = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.warning("锚点文件无法读取：%s: %s", type(exc).__name__, exc)
                problems.append({"unreadable": True, "path": str(path),
                                 "reason": f"锚点文件无法读取：{exc}"})
                continue
            try:
                data = json.loads(raw)
            except Exception as exc:
                log.warning("锚点文件解析失败：%s: %s", type(exc).__name__, exc)
                problems.append({"tampered": True, "path": str(path),
                                 "reason": f"锚点文件损坏：{exc}"})
                continue
            if key is None:
                problems.append({"tampered": True, "path": str(path),
                                 "reason": "密钥缺失，无法校验锚点"})
                continue
            payload = self._anchor_payload(data.get("count", -1), data.get("head", ""),
                                           data.get("legacy_prefix", 0))
            expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(str(data.get("mac", "")), expected):
                problems.append({"tampered": True, "path": str(path),
                                 "reason": "锚点签名不匹配（可能被改写）"})
                continue
            found = {"count": int(data.get("count", 0)), "head": str(data.get("head", "")),
                     "legacy_prefix": int(data.get("legacy_prefix", 0)), "path": str(path)}
            if best is None or found["count"] > best["count"]:
                best = found
        if best is not None:
            self._anchor_read_path = Path(best["path"])
            return best
        if problems:
            return problems[0]
        return None

    def _find_foreign_anchor(self) -> Optional[dict]:
        """找**旧路径指纹**的锚点，且它必须与当前链自洽 —— 这是合法迁移/拷贝的证据。

        不能只凭"存在别的锚点文件"就判迁移：同一台机器上可以有多份便携副本，
        它们的锚点本来就落在同一个用户目录里互不干扰。只有那个旧锚点的
        （条数, 链头）能对上当前链，才说明"这条链是从那个路径迁过来的"。
        """
        key = _get_hmac_key()
        if key is None:
            return None
        mine = {path.name for path in self._anchor_candidate_paths()}
        for directory in _anchor_dirs():
            try:
                if not directory.is_dir():
                    continue
                candidates = sorted(directory.glob(f"{self._ANCHOR_PREFIX}*{self._ANCHOR_SUFFIX}"))
            except OSError:
                continue
            for path in candidates:
                if path.name in mine:
                    continue
                try:
                    if path.is_dir():
                        continue
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                payload = self._anchor_payload(data.get("count", -1), data.get("head", ""),
                                               data.get("legacy_prefix", 0))
                expected = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(str(data.get("mac", "")), expected):
                    continue                       # 别的副本的锚点 / 被改写 → 不作证据
                count = int(data.get("count", 0))
                head = str(data.get("head", ""))
                if len(self._entries) < count:
                    continue                       # 连条数都对不上 → 不是这条链的
                if count and self._entries[count - 1].entry_hash != head:
                    continue                       # 锚点位置那条对不上 → 不是这条链的
                return {"path": str(path), "count": count,
                        "fingerprint": path.name[len(self._ANCHOR_PREFIX):-len(self._ANCHOR_SUFFIX)]}
        return None

    def _classify_anchor_absent(self, read_problem: Optional[dict] = None) -> dict:
        """锚点"找不到"时的定性。把原先笼统的"锚点缺失但链非空"拆成四类：

          · empty    —— 还没有任何审计条目（首次运行）：静默，无需告警；
          · migrated —— 当前路径没有锚点，但**另一路径的旧锚点与本链自洽**：
                        这是合法迁移/拷贝，提示 + 一键重建，不按可疑处理；
          · degraded —— 锚点缺失且**所有候选位置都写不进去**（含锚点读不了的情形）：
                        如实报"锚点未生效（防篡改能力降级）"，并指出该怎么修；
          · missing  —— 锚点真的不见了而链非空：维持现状的可疑告警（可用「重建锚点」确认）。
        """
        if not self._entries:
            return {"state": "empty", "anchor": "absent", "reason": ""}
        foreign = self._find_foreign_anchor()
        if foreign:
            return {"state": "migrated", "anchor": "migrated",
                    "reason": ("检测到审计目录已迁移（当前路径下锚点缺失，但发现另一路径 "
                               f"{Path(foreign['path']).parent} 的旧锚点与本链一致，"
                               f"旧指纹 {foreign['fingerprint']}）—— 这是合法迁移，"
                               "点「重建锚点」确认接受当前链即可恢复校验"),
                    "migration_source": foreign["path"]}
        if not self._anchor_writable_anywhere():
            reason = ("锚点未生效（防篡改能力降级）：锚点缺失，且所有候选位置都写不进去 —— "
                      "尾部截断与降级改写将无法被发现；请检查用户目录 / 程序目录的写权限")
            if read_problem:
                reason += f"（{read_problem.get('reason')}）"
            return {"state": "degraded", "anchor": "degraded", "reason": reason}
        return {"state": "missing", "anchor": "absent",
                "reason": ("锚点缺失但审计链非空 —— 审计目录可能被整体替换、锚点被删除，"
                           "或这是首次升级（可在审计面板点「重建锚点」确认接受当前链）")}

    def _set_anchor_write_state(self, state: dict) -> dict:
        """记录锚点写入状态；**仅在状态发生变化时**记日志（避免每条审计刷一行 ERROR）。"""
        previous = getattr(self, "_anchor_write_state", None)
        self._anchor_write_state = state
        signature = (bool(state.get("ok")), str(state.get("dir") or ""), bool(state.get("degraded")))
        if signature == getattr(self, "_anchor_write_signature", None):
            return state
        self._anchor_write_signature = signature
        if state.get("degraded"):
            log.error("审计链锚点未生效（防篡改能力降级：尾部截断 / 降级改写将无法被发现）：%s",
                      state.get("reason"))
        elif state.get("fell_back"):
            log.warning("审计链锚点已回退到备用位置：%s（首选位置不可写）", state.get("path"))
        elif previous is not None and not previous.get("ok"):
            log.info("审计链锚点写入已恢复：%s", state.get("path"))
        return state

    def _write_anchor(self) -> dict:
        """按当前链状态刷新锚点（追加条目后调用）；首选落点不可写时**按候选顺序回退**。

        写入结果（含"锚点如今落在哪、是否降级"）记进 `_anchor_write_state`，
        由 `anchor_health()` 交给界面如实展示 —— 回退与失败都不能只留在日志里。
        """
        key = _get_hmac_key()
        if key is None:
            return self._set_anchor_write_state(
                {"ok": False, "degraded": True, "path": "", "dir": "",
                 "reason": "密钥缺失，锚点无法写入（防篡改能力降级）"})
        count = len(self._entries)
        head = self._entries[-1].entry_hash if count else ""
        legacy_prefix = 0
        for entry in self._entries:            # 只有"开头的连续 legacy 段"才算历史前缀
            if not entry.legacy:
                break
            legacy_prefix += 1
        payload = self._anchor_payload(count, head, legacy_prefix)
        mac = hmac.new(key, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        body = json.dumps({"count": count, "head": head, "legacy_prefix": legacy_prefix, "mac": mac},
                          ensure_ascii=False, indent=2)
        errors: List[str] = []
        for index, path in enumerate(self._anchor_candidate_paths()):
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(body, encoding="utf-8")
            except Exception as exc:
                errors.append(f"{path.parent}：{type(exc).__name__}: {exc}")
                continue
            return self._set_anchor_write_state(
                {"ok": True, "degraded": False, "fell_back": index > 0,
                 "path": str(path), "dir": str(path.parent), "reason": ""})
        return self._set_anchor_write_state(
            {"ok": False, "degraded": True, "fell_back": False, "path": "", "dir": "",
             "reason": "锚点未生效（防篡改能力降级）：所有候选位置都写不进去 —— " + "；".join(errors)})

    def _anchor_active_path(self) -> Optional[Path]:
        """锚点**实际**落在哪个候选路径（优先最近一次写入/读取到的那个）。"""
        write_state = getattr(self, "_anchor_write_state", None) or {}
        if write_state.get("path"):
            return Path(write_state["path"])
        read_path = getattr(self, "_anchor_read_path", None)
        if read_path:
            return Path(read_path)
        for path in self._anchor_candidate_paths():
            try:
                if path.exists():
                    return path
            except OSError:
                continue
        return None

    def anchor_health(self) -> dict:
        """锚点健康度：锚点是否生效、落在哪、是否需要重建（界面直接展示，不让人猜）。"""
        detail = self.verify_chain_detail()
        state = str(detail.get("state") or "unknown")
        write_state = getattr(self, "_anchor_write_state", None) or {}
        active = self._anchor_active_path()
        fell_back = bool(write_state.get("fell_back"))
        if not fell_back and active is not None:
            try:
                fell_back = active != self._anchor_path()
            except Exception:
                fell_back = False
        return {
            "state": state,
            "valid": bool(detail.get("valid")),
            "degraded": state == "degraded",
            "fallback": fell_back,
            "dir": str(active.parent) if active else "",
            "path": str(active) if active else "",
            "reason": str(detail.get("reason") or ""),
            "migration_source": str(detail.get("migration_source") or ""),
        }

    def reanchor(self, reason: str = "") -> dict:
        """显式重建锚点（用于迁移/拷贝审计目录后的确认动作）。

        只在"链自洽但锚点缺失"时才有意义：这是把当前链作为新的信任起点。
        调用方（界面）必须让用户明确知道"确认之后，此前的截断将无法再被发现"。
        """
        detail = self.verify_chain_detail(allow_missing_anchor=True)
        if not detail.get("valid"):
            return {"ok": False, "error": detail.get("reason") or "链本身不自洽，拒绝重建锚点"}
        write_state = self._write_anchor()
        if not write_state.get("ok"):
            # 重建也写不进去 → 如实回报"降级"，绝不假装成功
            return {"ok": False, "error": write_state.get("reason") or "锚点写入失败（防篡改能力降级）"}
        try:
            self.add_entry(AuditEventType.ACCESS_CONTROL, AuditSeverity.WARNING,
                           "审计链锚点已重建（确认接受当前链为新的信任起点）",
                           {"reason": reason or "", "count": len(self._entries)})
        except Exception:
            pass
        return {"ok": True, "count": len(self._entries),
                "anchor_path": str(write_state.get("path") or ""),
                "anchor_dir": str(write_state.get("dir") or "")}

    def _save_logs(self):
        log_file = self._storage_path / "audit_chain.json"
        tmp_file = log_file.with_suffix(".json.tmp")
        try:
            with self._lock:
                # 先写临时文件，再原子替换，避免写一半崩溃留下损坏文件
                # 落点固定：log_file/tmp_file 均由 _storage_path（应用审计目录）派生
                tmp_file.write_text(
                    json.dumps([e.to_dict() for e in self._entries],
                               ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(tmp_file, log_file)
                # 链落盘与锚点刷新必须成对发生：否则新写入的条目会被下一次校验当成"截断"
                self._write_anchor()
        except Exception as e:
            log.error("审计日志保存失败：%s", e)

    def set_session_id(self, session_id: str):
        self._current_session_id = session_id

    def add_entry(self, event_type: AuditEventType, severity: AuditSeverity,
                  message: str, details: dict = None):
        try:
            # ★ 落盘前统一掩码：message 与 details 都必须过脱敏管线。
            #   此前只掩码 details，message 原样落盘 —— 调用方一旦把路径/参数写进 message
            #   （工作流步骤审计就是这么做的），敏感明文就会进入审计日志，审计反成泄露源。
            details = _mask_details(details)
            message = _mask_text(message) if isinstance(message, str) else message
            with self._lock:
                entry = AuditEntry(
                    event_type=event_type,
                    severity=severity,
                    message=message,
                    details=details,
                    session_id=self._current_session_id
                )
                prev_hash = self._entries[-1].entry_hash if self._entries else ""
                entry.compute_hash(prev_hash)
                self._entries.append(entry)
                self._save_logs()
                return entry
        except Exception as e:
            log.error("审计条目写入失败：%s", e)
        return None

    def log_tool_call(self, tool_name: str, tool_args: dict, target_path: str = ""):
        return self.add_entry(
            AuditEventType.TOOL_CALL,
            AuditSeverity.INFO,
            f"工具调用: {tool_name}",
            {
                "tool_name": tool_name,
                "tool_args": tool_args,
                "target_path": target_path
            }
        )

    def log_tool_result(self, tool_name: str, success: bool, result: str = ""):
        return self.add_entry(
            AuditEventType.TOOL_RESULT,
            AuditSeverity.WARNING if not success else AuditSeverity.INFO,
            f"工具执行{'成功' if success else '失败'}: {tool_name}",
            {
                "tool_name": tool_name,
                "success": success,
                "result": result[:500] if result else ""
            }
        )

    def log_llm_input(self, content: str, has_privacy: bool = False, preview: str = ""):
        """记录 LLM 输入。

        参数：
            content: 输入内容（脱敏后）
            has_privacy: 是否经过隐私脱敏
            preview: 可选的内容预览（前200字符，脱敏态），便于审计回看
        """
        details = {
            "content_length": len(content),
            "has_privacy": has_privacy
        }
        if preview:
            details["preview"] = preview[:200]
        return self.add_entry(
            AuditEventType.LLM_INPUT,
            AuditSeverity.INFO,
            f"LLM输入{'(含隐私脱敏)' if has_privacy else ''}",
            details
        )

    def log_llm_output(self, content: str, token_count: int = 0, preview: str = "",
                       empty_output: bool = False):
        """记录 LLM 输出。

        参数：
            content: 输出内容
            token_count: token 数（可选）
            preview: 可选的内容预览（前200字符），便于审计回看
            empty_output: 本轮模型**没有产出任何内容**（文字与工具调用都没有）。
                产品兜底文案不是模型输出，必须记 content_length=0 并带上这个标记，
                否则审计无法区分"模型真说了这些"与"系统替模型解释了一句"
                （组员评审 BUG-0008：兜底文案曾被当成 104 字的模型输出）。
                默认 False，保持既有调用方语义不变。
        """
        details = {
            "content_length": len(content),
            "token_count": token_count
        }
        if empty_output:
            details["empty_output"] = True
        if preview:
            details["preview"] = preview[:200]
        return self.add_entry(
            AuditEventType.LLM_OUTPUT,
            AuditSeverity.INFO,
            "LLM输出",
            details
        )

    def log_privacy_batch(self, stats: dict, action: str = "detected"):
        """批量记录隐私敏感事件，避免每个实体一条日志。

        参数：
            stats: {实体类型: 数量}，如 {"PHONE": 2, "NAME": 1}
            action: "detected"(检测到) / "anonymized"(已脱敏) / "restored"(已还原)
        """
        if not stats:
            return None
        severity = AuditSeverity.SECURITY if action == "detected" else AuditSeverity.INFO
        action_cn = {"detected": "检测到", "anonymized": "已脱敏", "restored": "已还原"}.get(action, action)
        return self.add_entry(
            AuditEventType.PRIVACY_SENSITIVE,
            severity,
            f"隐私事件[{action_cn}]: {dict(stats)}",
            {
                "stats": dict(stats),
                "total": sum(stats.values()),
                "action": action
            }
        )

    def log_privacy_sensitive(self, entity_type: str, entity_value: str, action: str = "detected"):
        return self.add_entry(
            AuditEventType.PRIVACY_SENSITIVE,
            AuditSeverity.CRITICAL if action == "leak" else AuditSeverity.SECURITY,
            f"隐私敏感事件: {entity_type} {action}",
            {
                "entity_type": entity_type,
                "entity_value": entity_value,
                "action": action
            }
        )

    def log_access_control(self, action: str, path: str, approved: bool):
        return self.add_entry(
            AuditEventType.ACCESS_CONTROL,
            AuditSeverity.CRITICAL if not approved else AuditSeverity.SECURITY,
            f"访问控制{'通过' if approved else '拒绝'}: {action} {path}",
            {
                "action": action,
                "path": path,
                "approved": approved
            }
        )

    def log_workflow_step(self, workflow_name: str, step_name: str,
                          tool_name: str, success: bool, error: str = ""):
        """记录工作流步骤执行结果。"""
        return self.add_entry(
            AuditEventType.WORKFLOW_STEP_EXECUTED,
            AuditSeverity.INFO if success else AuditSeverity.WARNING,
            f"工作流步骤{'成功' if success else '失败'}: {step_name}",
            {
                "workflow_name": workflow_name,
                "step_name": step_name,
                "tool_name": tool_name,
                "success": success,
                "error": error
            }
        )

    def log_workflow_approval(self, workflow_name: str, step_name: str,
                              approved: bool, reviewer: str = "", comment: str = ""):
        """记录工作流步骤审批结果。"""
        return self.add_entry(
            AuditEventType.ACCESS_CONTROL,
            AuditSeverity.SECURITY if approved else AuditSeverity.CRITICAL,
            f"工作流审批{'通过' if approved else '拒绝'}: {step_name}",
            {
                "workflow_name": workflow_name,
                "step_name": step_name,
                "approved": approved,
                "reviewer": reviewer,
                "comment": comment
            }
        )

    def log_session_start(self, session_id: str):
        self.set_session_id(session_id)
        return self.add_entry(
            AuditEventType.SESSION_START,
            AuditSeverity.INFO,
            f"会话开始: {session_id}",
            {"session_id": session_id}
        )

    def log_session_end(self):
        entry = self.add_entry(
            AuditEventType.SESSION_END,
            AuditSeverity.INFO,
            f"会话结束: {self._current_session_id}",
            {"session_id": self._current_session_id}
        )
        self._current_session_id = None
        return entry

    def verify_chain(self) -> bool:
        """链完整性（兼容旧调用方）。判定细节见 verify_chain_detail()。"""
        return bool(self.verify_chain_detail().get("valid"))

    def _warn_verify_once(self, reason: str, message: str, level: str = "warning") -> None:
        """进程内去重地记一条校验告警（V3.6.13，第三轮评审附带观察）。

        现场问题：同一次启动里同一条校验失败告警连续出现 4 次——多个消费者
        （审计面板 / 顶栏 / 状态查询）各自 verify、各自 log，日志被同一句话刷屏。
        规则：**同一 reason 只在状态变化时记一次**；校验成功时清掉记账，所以
        "好了又坏"仍会重新告警（绝不吞掉结果变化）。
        """
        key = (str(level), str(reason or message))
        if getattr(self, "_last_verify_warning", None) == key:
            return
        self._last_verify_warning = key
        if level == "error":
            log.error("%s", message)
        else:
            log.warning("%s", message)

    def verify_chain_detail(self, allow_missing_anchor: bool = False) -> dict:
        """完整校验审计链，返回结论与**人话原因**（界面直接展示，不让人猜）。

        判定规则（比旧实现严格，且规则本身可被测试锁住）：
          1. 密钥缺失 → 无效（没有密钥就无法证明任何条目未被篡改）；
          2. 锚点签名不匹配 → 无效（锚点被改写）；
          3. 逐条重算哈希：HMAC 条目用 HMAC，legacy 条目用旧 sha256；
          4. ★ legacy 条目只允许出现在**锚点固定的历史前缀**内。
             旧实现只要文件里写着 legacy 就用弱校验 —— 而 legacy 标记是从文件里读的，
             攻击者抹掉 hash_algo 再重算整链即可"通过校验"（降级改写）。现在这样做会因
             "legacy 条数超过锚定的历史前缀"被判无效；
          5. 锚点存在时：条数少于锚点记录 → 尾部被截断；锚点位置那条的哈希对不上 → 尾部被替换；
          6. 锚点缺失但链非空 → 不再一律当可疑：先按 `_classify_anchor_absent()` 细分为
             迁移（旧路径指纹的锚点仍与本链自洽 → 提示迁移 + 一键重建）、
             降级（所有候选位置都写不进去 → 如实报"锚点未生效"，不算被篡改）、
             可疑缺失（锚点真的不见了且还能写 → 维持现状告警）。
             `allow_missing_anchor=True` 供 reanchor 自身使用（此时跳过第 6 条）。

        返回的 detail 里带 `state` 字段（ok / empty / migrated / degraded / missing /
        tampered / no_key / accepted），界面据此区分"迁移待重建"与"校验未通过"。
        """
        detail = {"valid": False, "total": len(self._entries), "legacy": 0,
                  "legacy_prefix": 0, "anchor": "none", "state": "ok", "reason": ""}
        key = _get_hmac_key()
        if key is None:
            detail["state"] = "no_key"
            detail["reason"] = "密钥缺失，无法校验审计链（无法证明条目未被篡改）"
            self._warn_verify_once(detail["reason"], "审计链无法校验（密钥缺失）")
            return detail

        anchor = self._read_anchor()
        read_problem = None
        if anchor is not None and anchor.get("tampered"):
            detail["anchor"] = "tampered"
            detail["state"] = "tampered"
            detail["reason"] = anchor.get("reason") or "锚点不可信"
            self._warn_verify_once(detail["reason"],
                                   f"审计链校验失败：{detail['reason']}")
            return detail
        if anchor is not None and anchor.get("unreadable"):
            # 锚点读不了（权限 / 落点被占）：先当"没有锚点"，由分类器决定是降级还是可疑缺失
            read_problem = anchor
            anchor = None

        if anchor is None and self._entries and not allow_missing_anchor:
            verdict = self._classify_anchor_absent(read_problem)
            detail["state"] = verdict["state"]
            detail["anchor"] = verdict["anchor"]
            detail["reason"] = verdict["reason"]
            if verdict.get("migration_source"):
                detail["migration_source"] = verdict["migration_source"]
            if verdict["state"] == "degraded":
                self._warn_verify_once(
                    detail["reason"],
                    f"审计链锚点未生效（防篡改能力降级）：{detail['reason']}",
                    level="error")
            elif verdict["state"] == "migrated":
                self._warn_verify_once(
                    detail["reason"],
                    f"审计链校验：检测到审计目录迁移（待重建锚点）—— {verdict['reason']}")
            else:
                self._warn_verify_once(detail["reason"],
                                   f"审计链校验失败：{detail['reason']}")
            return detail

        legacy_total = sum(1 for e in self._entries if e.legacy)
        detail["legacy"] = legacy_total
        allowed_legacy = int(anchor.get("legacy_prefix", 0)) if anchor else 0
        detail["legacy_prefix"] = allowed_legacy
        detail["anchor"] = "verified" if anchor else "absent"

        # 逐条重算哈希（legacy 条目仍按旧算法重算，但"能用弱校验"的资格受锚点限制）
        prev_hash = ""
        for index, entry in enumerate(self._entries):
            canonical = _canonical(entry, prev_hash)
            if entry.legacy:
                if index >= allowed_legacy:
                    detail["reason"] = (f"第 {index + 1} 条出现未受保护的旧版条目（超出锚定的历史前缀 "
                                        f"{allowed_legacy} 条）—— 疑似抹掉 HMAC 标记后重算整链")
                    self._warn_verify_once(detail["reason"],
                                           f"审计链校验失败：{detail['reason']}")
                    return detail
                expected_hash = hashlib.sha256(canonical).hexdigest()
            else:
                expected_hash = hmac.new(key, canonical, hashlib.sha256).hexdigest()
            if entry.entry_hash != expected_hash:
                detail["reason"] = f"第 {index + 1} 条哈希不匹配（该条内容被改动）"
                self._warn_verify_once(detail["reason"],
                                       f"审计链校验失败：{detail['reason']}")
                return detail
            prev_hash = entry.entry_hash

        if anchor:
            if len(self._entries) < int(anchor.get("count", 0)):
                detail["reason"] = (f"条目数少于锚点记录（现有 {len(self._entries)} 条 / 锚点 "
                                    f"{anchor.get('count')} 条）—— 尾部被截断")
                self._warn_verify_once(detail["reason"],
                                       f"审计链校验失败：{detail['reason']}")
                return detail
            anchor_count = int(anchor.get("count", 0))
            if anchor_count > 0 and self._entries[anchor_count - 1].entry_hash != anchor.get("head"):
                detail["reason"] = "锚点位置的链头不匹配 —— 尾部条目被替换或重排"
                self._warn_verify_once(detail["reason"],
                                       f"审计链校验失败：{detail['reason']}")
                return detail

        detail["valid"] = True
        # ★ 校验成功即清掉告警记账：下次再失败（结果变化）仍会照常告警，不被去重吞掉。
        self._last_verify_warning = None
        if anchor is None:
            if self._entries:
                # 仅 reanchor 路径会走到这里：显式确认后由调用方写入锚点
                detail["anchor"] = "accepted_without_anchor"
                detail["state"] = "accepted"
            else:
                # 首次运行（链还空着）：静默，不产生任何告警
                detail["anchor"] = "absent"
                detail["state"] = "empty"
        else:
            detail["anchor"] = "verified"
            detail["state"] = "ok"
        if legacy_total:
            detail["reason"] = (f"链自洽，但含 {legacy_total} 条旧版（无 HMAC）历史条目，"
                                f"其真实性弱于 HMAC 条目")
        return detail

    def get_entries(self, filters: dict = None) -> List[AuditEntry]:
        result = self._entries[:]
        if filters:
            if "event_type" in filters:
                result = [e for e in result if e.event_type == filters["event_type"]]
            if "severity" in filters:
                result = [e for e in result if e.severity == filters["severity"]]
            if "session_id" in filters:
                result = [e for e in result if e.session_id == filters["session_id"]]
            if "start_time" in filters:
                result = [e for e in result if e.timestamp >= filters["start_time"]]
            if "end_time" in filters:
                result = [e for e in result if e.timestamp <= filters["end_time"]]
        return result

    def get_stats(self) -> dict:
        stats = {
            "total_entries": len(self._entries),
            "by_type": {},
            "by_severity": {},
            "by_session": {},
            "chain_valid": self.verify_chain()
        }
        for entry in self._entries:
            stats["by_type"][entry.event_type] = stats["by_type"].get(entry.event_type, 0) + 1
            stats["by_severity"][entry.severity] = stats["by_severity"].get(entry.severity, 0) + 1
            if entry.session_id:
                stats["by_session"][entry.session_id] = stats["by_session"].get(entry.session_id, 0) + 1
        return stats

    def export_report(self, format: str = "json", filters: dict = None) -> str:
        entries = self.get_entries(filters)
        if format == "json":
            report = {
                "generated_at": datetime.now().isoformat(),
                "chain_valid": self.verify_chain(),
                "stats": self.get_stats(),
                "entries": [e.to_dict() for e in entries]
            }
            return json.dumps(report, ensure_ascii=False, indent=2)
        elif format == "html":
            stats = self.get_stats()
            type_options = "".join(
                f'<option value="{_html.escape(t, quote=True)}">{_html.escape(t)}</option>'
                for t in sorted({str(e.event_type) for e in entries}))
            sev_options = "".join(
                f'<option value="{_html.escape(v, quote=True)}">{_html.escape(v)}</option>'
                for v in sorted({str(e.severity) for e in entries}))
            html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="Content-Security-Policy"
          content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:">
    <title>隐盾审计报告</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 0; padding: 20px; background: #f8fafc; }}
        .header {{ background: linear-gradient(135deg, #07c160, #06ad56); color: white; padding: 24px; border-radius: 12px; margin-bottom: 20px; }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .header p {{ opacity: 0.9; margin: 8px 0 0; }}
        .stats-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 20px; }}
        .stat-card {{ background: white; padding: 16px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .stat-card .label {{ color: #64748b; font-size: 12px; }}
        .stat-card .value {{ font-size: 24px; font-weight: bold; color: #1e293b; }}
        .stat-card.valid .value {{ color: #07c160; }}
        .stat-card.invalid .value {{ color: #ef4444; }}
        .table-container {{ background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #e2e8f0; }}
        th {{ background: #f1f5f9; font-weight: 600; color: #475569; font-size: 12px; }}
        td {{ font-size: 13px; color: #334155; }}
        .severity {{ padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; }}
        .severity-info {{ background: #dbeafe; color: #1e40af; }}
        .severity-warning {{ background: #fef3c7; color: #d97706; }}
        .severity-critical {{ background: #fee2e2; color: #dc2626; }}
        .severity-security {{ background: #e0e7ff; color: #6366f1; }}
        .event-type {{ padding: 4px 10px; border-radius: 12px; font-size: 11px; font-weight: 600; background: #f1f5f9; color: #475569; }}
        .details {{ max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: monospace; font-size: 12px; }}
        .filter-bar {{ display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin: 0 0 16px; padding: 12px 16px; background: white; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
        .filter-bar input, .filter-bar select {{ padding: 8px 10px; border: 1px solid #cbd5e1; border-radius: 6px; font-size: 13px; color: #1e293b; background: #fff; }}
        .filter-bar input {{ min-width: 260px; flex: 1; }}
        .filter-bar .count {{ color: #64748b; font-size: 12px; margin-left: auto; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🛡️ 隐盾安全审计报告</h1>
        <p>生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
    <div class="stats-grid">
        <div class="stat-card"><div class="label">总日志数</div><div class="value">{stats['total_entries']}</div></div>
        <div class="stat-card valid"><div class="label">链条完整性</div><div class="value">{'✅ 有效' if stats['chain_valid'] else '❌ 无效'}</div></div>
        <div class="stat-card"><div class="label">工具调用</div><div class="value">{stats['by_type'].get('tool_call', 0)}</div></div>
        <div class="stat-card"><div class="label">隐私事件</div><div class="value">{stats['by_type'].get('privacy_sensitive', 0)}</div></div>
    </div>
    <div class="filter-bar">
        <input id="audit-q" type="search" placeholder="搜索消息 / 详情（例如：删除、文件名）">
        <select id="audit-type"><option value="">全部事件类型</option>{type_options}</select>
        <select id="audit-sev"><option value="">全部严重程度</option>{sev_options}</select>
        <span class="count" id="audit-count"></span>
    </div>
    <div class="table-container">
        <table>
            <thead>
                <tr><th>时间</th><th>事件类型</th><th>严重程度</th><th>消息</th><th>详情</th></tr>
            </thead>
            <tbody>
"""
            for entry in entries:
                html += _audit_html_row(entry)
            html += """</tbody></table></div>
<script>
(function () {
  const rows = Array.from(document.querySelectorAll('tbody tr'));
  const q = document.getElementById('audit-q');
  const fType = document.getElementById('audit-type');
  const fSev = document.getElementById('audit-sev');
  const count = document.getElementById('audit-count');
  function apply() {
    const needle = (q.value || '').trim().toLowerCase();
    let shown = 0;
    rows.forEach(function (row) {
      const text = row.textContent.toLowerCase();
      const type = row.children[1].textContent;
      const sev = row.children[2].textContent;
      const ok = (!needle || text.indexOf(needle) >= 0)
        && (!fType.value || type === fType.value)
        && (!fSev.value || sev === fSev.value);
      row.style.display = ok ? '' : 'none';
      if (ok) shown++;
    });
    count.textContent = '显示 ' + shown + ' / ' + rows.length + ' 条';
  }
  [q, fType, fSev].forEach(function (el) {
    el.addEventListener('input', apply);
    el.addEventListener('change', apply);
  });
  apply();
})();
</script>
</body></html>"""
            return html
        return ""

    def save_report(self, format: str = "json", filename: str = None) -> str:
        content = self.export_report(format)
        if not filename:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"audit_report_{timestamp}.{format}"
        # 只取文件名、丢弃任何目录成分：即使调用方传入 "../x" 也只会落回审计目录内
        filepath = self._storage_path / Path(filename).name
        filepath.write_text(content, encoding='utf-8')
        return str(filepath)

    def save_rendered(self, content: str, filename: str) -> str:
        """保存**外部渲染好**的报告内容（如插件贡献的导出格式）到审计目录。

        与 save_report 的区别：内容不是内核生成的，内核只负责落点与文件名清洗。
        落点固定为审计目录、文件名只保留 basename——导出功能无法写到别处。
        """
        name = Path(str(filename or "")).name
        if not name or ".." in name:
            name = f"audit_report_plugin_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        filepath = self._storage_path / name
        filepath.write_text(str(content), encoding='utf-8')
        return str(filepath)