# -*- coding: utf-8 -*-
"""隐盾控制面 · 存储层（SQLite，Python 标准库，零额外依赖）。

存储原则：
- **只存掩码态内容**：入库的 message/details 已是端点上报道来的掩码文本；
  本层不做"解密"也不可能做（没有、也不该有端点的映射密钥）。
- **令牌只存哈希**：管理令牌与端点令牌均以 SHA-256 存储，明文仅在生成/登记时下发一次。
- **策略包原样留档**：每次下发都新增一条（scope + version + 包体 + 时间），便于事后追责
  "谁在什么时候要求端点变得更严"（历史不可被覆盖）。
- 连接按需创建并加锁：控制面是低并发工具，正确性优先于吞吐。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import policy as policy_mod
from .models import EndpointReport, is_high_severity
from .verify import ChainSnapshot, VerifyResult, snapshot_verdict


def _say(message: str) -> None:
    """控制端的"说给人听"出口：有控制台就打印，没有（窗口化 exe）就落日志。

    不直接复用 desktop.say() 是为了避免模块间互相依赖（store 是纯数据层）；
    两处判据完全一致：`sys.stdout` 与 `sys.stderr` 都为 None 就是无控制台。
    """
    text = str(message)
    if sys.stdout is not None or sys.stderr is not None:
        print(text)
        return
    try:
        from yindun.utils.logger import get_logger
        get_logger("control").info("%s", text)
    except Exception:                       # 日志未就绪也不能抛（令牌读取不该因此失败）
        pass

SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
  endpoint_id   TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  token_hash    TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT,
  version       TEXT DEFAULT '',
  platform      TEXT DEFAULT '',
  strict_mode   INTEGER DEFAULT 0,
  policy_version INTEGER DEFAULT 0,
  total_count   INTEGER DEFAULT 0,
  head_hash     TEXT DEFAULT '',
  legacy_prefix INTEGER DEFAULT 0,
  first_seen_at TEXT DEFAULT '',
  last_report_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  endpoint_id   TEXT NOT NULL,
  seq           INTEGER DEFAULT 0,
  ts            TEXT NOT NULL,
  event_type    TEXT NOT NULL,
  severity      TEXT NOT NULL,
  message       TEXT DEFAULT '',
  details_json  TEXT DEFAULT '{}',
  entry_hash    TEXT DEFAULT '',
  previous_hash TEXT DEFAULT '',
  received_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ep_seq ON events(endpoint_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_sev ON events(severity);
CREATE TABLE IF NOT EXISTS alerts (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  endpoint_id   TEXT NOT NULL,
  code          TEXT NOT NULL,
  detail        TEXT NOT NULL,
  severity      TEXT NOT NULL,
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
CREATE TABLE IF NOT EXISTS policies (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  scope         TEXT NOT NULL,
  version       INTEGER NOT NULL,
  bundle_json   TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policies_scope ON policies(scope, version);
CREATE TABLE IF NOT EXISTS usage (
  endpoint_id   TEXT NOT NULL,
  model         TEXT NOT NULL,
  scope         TEXT NOT NULL,
  calls         INTEGER DEFAULT 0,
  chars         INTEGER DEFAULT 0,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (endpoint_id, model)
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pair_codes (
  code        TEXT PRIMARY KEY,
  note        TEXT DEFAULT '',
  created_at  TEXT NOT NULL,
  expires_at  TEXT NOT NULL,
  used_at     TEXT DEFAULT '',
  endpoint_id TEXT DEFAULT ''
);
"""


ONLINE_WINDOW_SEC = 15 * 60      # 超过 15 分钟未上报即视为失联


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H:%M:%S")


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_data_dir() -> Path:
    try:
        from yindun import APP_ROOT          # 与打包形态一致：exe 旁目录
        return Path(APP_ROOT) / "control_data"
    except Exception:
        return Path(__file__).resolve().parents[2] / "control_data"


class ControlStore:
    """控制面存储。写操作跨线程串行化；连接按线程各自持有。

    ★ 踩坑记录（V3.5.0 实机冒烟发现）：最初用"单连接 + 写锁 + 读不加锁"，
      在串行 curl 下一切正常，但控制台仪表盘会**并行**发 4 个请求（Promise.all），
      多线程同时用一个 sqlite3 连接就读出 `sqlite3.InterfaceError: bad parameter or
      other API misuse`（表现为"登录后立刻被判 401"，因为 overview 抛异常）。
      现改为**线程局部连接**（每线程一条，`check_same_thread=False` 不再需要）+
      WAL 日志模式 + busy_timeout，写操作仍用锁串行化，避免 database is locked。
    """

    def __init__(self, path: Optional[Path] = None):
        self.dir = Path(path).parent if path else default_data_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = Path(path) if path else self.dir / "control.db"
        self._lock = threading.Lock()          # 只锁写，读走各自连接并发
        self._local = threading.local()
        with self._lock:
            conn = self._conn
            conn.executescript(SCHEMA)
            # ★ 迁移：策略行加 active 列（V3.6.2 C-14 策略回滚用）。
            #   历史行永不删除（审计可回溯），回滚只把"更新的那几版"标为 inactive；
            #   小库就地 ALTER，报错说明列已存在。
            try:
                conn.execute("ALTER TABLE policies ADD COLUMN active INTEGER DEFAULT 1")
            except sqlite3.OperationalError:
                pass
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.commit()

    @property
    def _conn(self) -> sqlite3.Connection:
        """当前线程的连接（惰性创建）。等价于"每线程一条连接"，无需跨线程共享。"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(str(self.path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return conn

    # ── 令牌 ──────────────────────────────────────────────
    def _get_setting(self, key: str) -> Optional[str]:
        row = self._conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_setting(self, key: str, value: str) -> None:
        self._conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._conn.commit()

    def ensure_admin_token(self) -> str:
        """首次运行时生成管理令牌，明文写入 control_data/admin_token.txt 并返回。

        只在下发时给一次明文（数据库里只有哈希）——这是"不落明文凭据"的常规做法；
        取回现场演示用的令牌请看该文件（它本身位于不入库的运行时目录）。
        """
        with self._lock:
            existing_hash = self._get_setting("admin_token_hash")
            if existing_hash:
                return self._read_token_file(existing_hash)
            token = secrets.token_urlsafe(24)
            self._set_setting("admin_token_hash", _sha256(token))
            (self.dir / "admin_token.txt").write_text(
                "# 隐盾控制面管理令牌（请妥善保管；数据库内只存哈希）\n"
                "# 下面一行就是令牌本身；本文件内容改变后请用 reset 重新生成。\n" + token + "\n",
                encoding="utf-8")
            return token

    def _read_token_file(self, expected_hash: str, name: str = "admin_token.txt") -> str:
        """从令牌文件里取回明文令牌：**只认第一个非注释行**，并与库里的哈希核对。

        ★ 这里曾经出过一个真事故（V3.5.5 修复）：旧实现直接 `read_text().strip()`，
        把注释头也当成了令牌的一部分，于是"第二次启动"时拿到的是带中文与换行的字符串。
        后果很隐蔽——服务照常起、也能鉴权失败得很有礼貌，但**控制台登录不了**：
        用这段字符串做 Authorization 头会直接抛
        `Failed to read the 'headers' property: String contains non-ISO-8859-1 code point`，
        页面只显示一句"无法连接控制面"，看不出是令牌的问题。
        现在的行为：取不到合法令牌就返回空串（调用方会提示"文件被删/被改，请重置"），
        绝不把一个"注定鉴权失败"的字符串交出去。
        """
        token_file = self.dir / name       # 管理令牌 / 只读令牌共用这套取回逻辑
        if not token_file.exists():
            return ""      # 已存在哈希但明文文件被删：只能重置
        try:
            lines = token_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            _say(f"[ControlStore] 读取令牌文件失败：{exc}")
            return ""
        for line in lines:
            text = line.strip().lstrip("\ufeff")
            if not text or text.startswith("#"):
                continue
            if secrets.compare_digest(_sha256(text), expected_hash):
                return text
            _say("[ControlStore] 令牌文件里的明文与库中哈希不一致——请重置管理令牌")
            return ""
        return ""

    def reset_admin_token(self) -> str:
        with self._lock:
            token = secrets.token_urlsafe(24)
            self._set_setting("admin_token_hash", _sha256(token))
            (self.dir / "admin_token.txt").write_text(
                "# 隐盾控制面管理令牌（请妥善保管；数据库内只存哈希）\n" + token + "\n",
                encoding="utf-8")
            return token

    def verify_admin(self, token: str) -> bool:
        stored = self._get_setting("admin_token_hash")
        return bool(stored) and secrets.compare_digest(stored, _sha256(token or ""))

    def policy_signing_key(self) -> str:
        """服务端级策略签名密钥（端点登记时下发同一把，用于验签策略包）。"""
        with self._lock:
            key = self._get_setting("policy_key")
            if key:
                return key
            key = secrets.token_urlsafe(32)
            self._set_setting("policy_key", key)
            return key

    def enroll(self, name: str, endpoint_id: str) -> Tuple[str, str]:
        """登记一个端点 → 返回 (端点令牌明文, 策略签名密钥)。令牌只此一次下发。"""
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._conn.execute(
                "INSERT INTO endpoints(endpoint_id,name,token_hash,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(endpoint_id) DO UPDATE SET token_hash=excluded.token_hash, name=excluded.name",
                (endpoint_id, name[:64], _sha256(token), _now()))
            self._conn.commit()
        return token, self.policy_signing_key()

    # ── 配对码（IT 发码 → 员工机自助接入，无需把管理令牌发给员工）──
    # ★ 自定义码的字符集与长度（V3.6.7）：要能"口头念给对面的同学"，所以只收 ASCII 字母数字与连字符，
    #   4–12 位。放宽到中文/符号会让"念出来"变成猜谜，也会在多端输入法之间产生同形字歧义。
    PAIR_CODE_RE = re.compile(r"^[A-Za-z0-9-]{4,12}$")

    def create_pair_code(self, ttl_minutes: int = 30, note: str = "",
                         code: str = "") -> Dict[str, Any]:
        """生成一张一次性配对码（默认 30 分钟有效、用后作废）。

        传 `code` 即**自定义**：适合"口头/手写在纸上转述"的场景（自动码是 6 位十六进制，
        念起来容易混）。自定义码同样受一次性 + 有效期约束，且**不允许覆盖已存在且仍可用的码**——
        否则两个同学拿着同一串码会互相踩（后生成的那张把前一张的状态盖掉）。
        """
        want = str(code or "").strip().upper()
        custom = bool(want)
        if custom and not self.PAIR_CODE_RE.match(want):
            return {"ok": False, "error": "自定义配对码只能是 4–12 位字母、数字或连字符"}
        ttl = max(5, min(int(ttl_minutes or 30), 24 * 60))
        created = datetime.now(timezone.utc).astimezone()
        expires = created + timedelta(minutes=ttl)
        with self._lock:
            if custom:
                row = self._conn.execute("SELECT * FROM pair_codes WHERE code=?", (want,)).fetchone()
                if row is not None and not row["used_at"] and _now() <= row["expires_at"]:
                    return {"ok": False, "error": f"配对码 {want} 还在有效期内，换一个或等它过期"}
            code_now = want or secrets.token_hex(3).upper()   # 6 位十六进制，便于口头/截图传达
            self._conn.execute(
                "INSERT OR REPLACE INTO pair_codes(code,note,created_at,expires_at,used_at,endpoint_id)"
                " VALUES(?,?,?,?,'','')",
                (code_now, str(note)[:120], created.strftime("%Y-%m-%dT%H:%M:%S"),
                 expires.strftime("%Y-%m-%dT%H:%M:%S")))
            self._conn.commit()
        return {"ok": True, "code": code_now, "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%S"),
                "ttl_minutes": ttl, "note": str(note)[:120], "custom": custom}

    def current_pair_code(self) -> Dict[str, Any]:
        """当前**还没用过、还没过期**的那张码（界面上要一直看得见，而不是弹一次 toast 就没了）。"""
        row = self._conn.execute(
            "SELECT * FROM pair_codes WHERE used_at='' AND expires_at>=? ORDER BY created_at DESC LIMIT 1",
            (_now(),)).fetchone()
        if row is None:
            return {"code": "", "expires_at": "", "state": "无可用配对码",
                    "ttl_minutes": 0, "custom": False}
        return {"code": row["code"], "expires_at": row["expires_at"], "state": "可用",
                "note": row["note"] or "", "ttl_minutes": 0,
                # 自动码是 6 位十六进制大写；与之不同就说明是自定义的（界面据此显示"自定义"标记）
                "custom": not (len(row["code"]) == 6 and all(
                    c in "0123456789ABCDEF" for c in row["code"]))}


    def consume_pair_code(self, code: str, endpoint_id: str) -> Tuple[bool, str]:
        """核销配对码。返回 (是否可用, 原因)。一次性 + 有效期双重约束。"""
        code = (code or "").strip().upper()
        if not code:
            return False, "缺少配对码"
        row = self._conn.execute("SELECT * FROM pair_codes WHERE code=?", (code,)).fetchone()
        if row is None:
            return False, "配对码不存在"
        if row["used_at"]:
            return False, f"配对码已被使用（{row['used_at']}）"
        if _now() > row["expires_at"]:
            return False, f"配对码已过期（{row['expires_at']}）"
        with self._lock:
            self._conn.execute("UPDATE pair_codes SET used_at=?, endpoint_id=? WHERE code=?",
                               (_now(), endpoint_id, code))
            self._conn.commit()
        return True, "ok"

    def list_pair_codes(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM pair_codes ORDER BY created_at DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        now = _now()
        return [{"code": r["code"], "note": r["note"], "created_at": r["created_at"],
                 "expires_at": r["expires_at"], "used_at": r["used_at"] or "",
                 "endpoint_id": r["endpoint_id"] or "",
                 "state": ("已使用" if r["used_at"] else ("已过期" if now > r["expires_at"] else "可用"))}
                for r in rows]

    def verify_endpoint(self, endpoint_id: str, token: str) -> bool:
        row = self._conn.execute("SELECT token_hash FROM endpoints WHERE endpoint_id=?",
                                 (endpoint_id,)).fetchone()
        return bool(row) and secrets.compare_digest(row["token_hash"], _sha256(token or ""))

    def has_endpoint(self, endpoint_id: str) -> bool:
        return self._conn.execute("SELECT 1 FROM endpoints WHERE endpoint_id=?",
                                  (endpoint_id,)).fetchone() is not None

    # ── 上报落库 ──────────────────────────────────────────
    def apply_report(self, report: EndpointReport, result: VerifyResult) -> Dict[str, int]:
        snap = result.new_snapshot or ChainSnapshot()
        with self._lock:
            if not self.has_endpoint(report.endpoint_id):
                # 未登记就上报：拒绝（调用方已拦），这里兜底不落数据
                return {"events": 0, "alerts": 0}
            for index, event in enumerate(report.events):
                # 序号是"该条在本地审计链里的第几条"，必须逐条递增——
                # 曾误用批首序号填充全部条目，界面上整批都显示同一个序号。
                self._conn.execute(
                    "INSERT INTO events(endpoint_id,seq,ts,event_type,severity,message,"
                    "details_json,entry_hash,previous_hash,received_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (report.endpoint_id, result.accepted_from_seq + index, event.ts, event.event_type,
                     event.severity, event.message, json.dumps(event.details, ensure_ascii=False),
                     event.entry_hash, event.previous_hash, _now()))
            alert_count = 0
            for alert in result.alerts:
                self._conn.execute(
                    "INSERT INTO alerts(endpoint_id,code,detail,severity,created_at) VALUES(?,?,?,?,?)",
                    (report.endpoint_id, alert.code, alert.detail, alert.severity, _now()))
                alert_count += 1
            caps = report.capabilities
            self._conn.execute(
                "UPDATE endpoints SET last_seen_at=?, version=?, platform=?, strict_mode=?, "
                "policy_version=?, total_count=?, head_hash=?, legacy_prefix=?, "
                "first_seen_at=CASE WHEN first_seen_at='' THEN ? ELSE first_seen_at END, "
                "last_report_at=? WHERE endpoint_id=?",
                (_now(), report.version, report.platform or caps.platform, int(caps.strict_mode),
                 caps.policy_version, snap.total_count, snap.head_hash, snap.legacy_prefix,
                 snap.first_seen_at, report.reported_at, report.endpoint_id))
            for row in report.usage:
                self._conn.execute(
                    "INSERT INTO usage(endpoint_id,model,scope,calls,chars,updated_at) "
                    "VALUES(?,?,?,?,?,?) ON CONFLICT(endpoint_id,model) DO UPDATE SET "
                    "calls=excluded.calls, chars=excluded.chars, scope=excluded.scope, "
                    "updated_at=excluded.updated_at",
                    (report.endpoint_id, row.model, row.scope, row.calls, row.chars, _now()))
            # 能力清单（模型/插件）也留档，供资产视角
            self._set_setting(f"caps:{report.endpoint_id}", json.dumps(
                {"models": caps.models, "external_models": caps.external_models,
                 "plugins": caps.plugins, "updated_at": _now()}, ensure_ascii=False))
            self._conn.commit()
        return {"events": len(report.events), "alerts": alert_count}

    def snapshot_of(self, endpoint_id: str) -> Optional[ChainSnapshot]:
        row = self._conn.execute(
            "SELECT total_count,head_hash,legacy_prefix,first_seen_at,last_report_at "
            "FROM endpoints WHERE endpoint_id=?", (endpoint_id,)).fetchone()
        if row is None:
            return None
        return ChainSnapshot(row["total_count"], row["head_hash"], row["legacy_prefix"],
                             row["first_seen_at"] or "", row["last_report_at"] or "")

    def capabilities_of(self, endpoint_id: str) -> Dict[str, Any]:
        raw = self._get_setting(f"caps:{endpoint_id}")
        if not raw:
            return {"models": [], "external_models": [], "plugins": [], "updated_at": ""}
        try:
            return json.loads(raw)
        except Exception:
            return {"models": [], "external_models": [], "plugins": [], "updated_at": ""}

    # ── 查询（界面用）────────────────────────────────────
    def overview(self) -> Dict[str, Any]:
        total_eps = self._conn.execute("SELECT COUNT(*) c FROM endpoints").fetchone()["c"]
        events = self._conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
        high = self._conn.execute(
            "SELECT COUNT(*) c FROM events WHERE severity IN ('critical','security')"
        ).fetchone()["c"]
        alerts = self._conn.execute(
            "SELECT COUNT(*) c FROM alerts WHERE severity<>'低' AND code NOT IN ('FIRST_REPORT','HIGH_EVENT')"
        ).fetchone()["c"]
        eps = self.list_endpoints()
        chain_bad = sum(1 for e in eps if e["chain_verdict"] in ("未上报", "链异常"))
        return {"endpoints": total_eps, "events": events, "high_events": high,
                "chain_alerts": alerts, "chain_bad": chain_bad,
                "generated_at": _now()}

    def list_endpoints(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM endpoints ORDER BY COALESCE(last_seen_at,'') DESC").fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            ep = row["endpoint_id"]
            bad = self._conn.execute(
                "SELECT COUNT(*) c FROM alerts WHERE endpoint_id=? AND severity<>'低' "
                "AND code NOT IN ('FIRST_REPORT','HIGH_EVENT')", (ep,)).fetchone()["c"]
            caps = self.capabilities_of(ep)
            snap = ChainSnapshot(row["total_count"], row["head_hash"], row["legacy_prefix"])
            verdict = snapshot_verdict(snap)
            if bad:
                verdict = "链异常"
            # 在线/失联：企业运维最需要的"这台机器还活着吗"（阈值 15 分钟）
            last_report = row["last_report_at"] or ""
            online = False
            if last_report:
                try:
                    online = (datetime.now(timezone.utc).astimezone()
                              - datetime.fromisoformat(last_report)).total_seconds() <= ONLINE_WINDOW_SEC
                except Exception:
                    online = False
            # 策略漂移：控制面当前版本 vs 端点自己报的已应用版本
            want = int(self.policy_for(ep).get("version", 0) or 0)
            have = int(row["policy_version"] or 0)
            drift = "已同步" if have >= want else f"待同步 v{have}→v{want}"
            out.append({
                "endpoint_id": ep, "name": row["name"], "version": row["version"],
                "platform": row["platform"], "last_seen_at": row["last_seen_at"] or "",
                "strict_mode": bool(row["strict_mode"]), "policy_version": row["policy_version"],
                "total_count": row["total_count"], "head_hash": row["head_hash"],
                "chain_verdict": verdict, "chain_alerts": bad,
                "first_seen_at": row["first_seen_at"] or "",
                "online": online, "last_report_at": last_report,
                "policy_want": want, "policy_have": have, "drift": drift,
                "models": caps.get("models", []),
                "external_models": caps.get("external_models", []),
                "plugins": caps.get("plugins", []),
                "caps_updated_at": caps.get("updated_at", ""),
                "policy": policy_mod.describe(self.policy_for(ep) or {}),
            })
        return out

    def list_events(self, endpoint_id: str = "", event_type: str = "", severity: str = "",
                    keyword: str = "", limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        where, params = ["1=1"], []
        if endpoint_id:
            where.append("endpoint_id=?"); params.append(endpoint_id)
        if event_type:
            where.append("event_type=?"); params.append(event_type)
        if severity:
            where.append("severity=?"); params.append(severity)
        if keyword:
            where.append("(message LIKE ? OR details_json LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        sql = ("SELECT * FROM events WHERE " + " AND ".join(where) +
               " ORDER BY id DESC LIMIT ? OFFSET ?")
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        rows = self._conn.execute(sql, params).fetchall()
        out = []
        for row in rows:
            try:
                details = json.loads(row["details_json"] or "{}")
            except Exception:
                details = {}
            out.append({"id": row["id"], "endpoint_id": row["endpoint_id"], "seq": row["seq"],
                        "ts": row["ts"], "event_type": row["event_type"],
                        "severity": row["severity"], "message": row["message"],
                        "details": details, "entry_hash": row["entry_hash"],
                        "received_at": row["received_at"]})
        return out

    def count_events(self, endpoint_id: str = "", event_type: str = "", severity: str = "",
                     keyword: str = "") -> int:
        where, params = ["1=1"], []
        if endpoint_id:
            where.append("endpoint_id=?"); params.append(endpoint_id)
        if event_type:
            where.append("event_type=?"); params.append(event_type)
        if severity:
            where.append("severity=?"); params.append(severity)
        if keyword:
            where.append("(message LIKE ? OR details_json LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        return self._conn.execute("SELECT COUNT(*) c FROM events WHERE " + " AND ".join(where),
                                  params).fetchone()["c"]

    def event_types(self) -> List[str]:
        return [r["event_type"] for r in self._conn.execute(
            "SELECT DISTINCT event_type FROM events ORDER BY event_type").fetchall()]

    def list_alerts(self, endpoint_id: str = "", limit: int = 50,
                    exclude_codes: Tuple[str, ...] = ("FIRST_REPORT", "HIGH_EVENT")) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM alerts WHERE 1=1"
        params: List[Any] = []
        if endpoint_id:
            sql += " AND endpoint_id=?"; params.append(endpoint_id)
        if exclude_codes:
            sql += " AND code NOT IN (%s)" % ",".join("?" * len(exclude_codes))
            params.extend(exclude_codes)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(int(limit), 200)))
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def usage_summary(self) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT model, scope, SUM(calls) calls, SUM(chars) chars, COUNT(DISTINCT endpoint_id) eps "
            "FROM usage GROUP BY model, scope ORDER BY calls DESC").fetchall()
        return [{"model": r["model"], "scope": r["scope"], "calls": r["calls"],
                 "chars": r["chars"], "endpoints": r["eps"]} for r in rows]

    # ── 统计分析（控制台"统计分析"页）──────────────────────
    def stats(self, days: int = 14) -> Dict[str, Any]:
        """按维度汇总事件：趋势 / 类型 / 级别 / 端点活跃度。

        为什么不由界面聚合：界面只有**当前页**的事件（列表是分页的），前端算出来的"总数"
        必然是错的——那是"看着有统计、数字却对不上"的经典坑。这里全部走 SQL 聚合。
        """
        days = max(1, min(int(days or 14), 90))
        # 趋势按天分桶：用 date(received_at) 而非 substr，带时区偏移的两种写法都能解析，
        # substr 会在 ts 与 received_at 格式不一致时切错位。
        rows = self._conn.execute(
            "SELECT date(received_at) d, COUNT(*) c, "
            "       SUM(CASE WHEN severity IN ('critical','security') THEN 1 ELSE 0 END) high "
            "FROM events GROUP BY d ORDER BY d").fetchall()
        by_day = {r["d"]: (r["c"], r["high"] or 0) for r in rows if r["d"]}
        # 补齐没有事件的日期：否则横轴被压缩，会被误读成"每天都在涨"
        today = datetime.now(timezone.utc).astimezone().date()
        daily: List[Dict[str, Any]] = []
        for offset in range(days - 1, -1, -1):
            day = (today - timedelta(days=offset)).isoformat()
            count, high = by_day.get(day, (0, 0))
            daily.append({"date": day, "count": count, "high": high})

        by_type = [{"event_type": r["event_type"], "count": r["c"]} for r in self._conn.execute(
            "SELECT event_type, COUNT(*) c FROM events GROUP BY event_type ORDER BY c DESC LIMIT 12").fetchall()]
        by_sev = {r["severity"]: r["c"] for r in self._conn.execute(
            "SELECT severity, COUNT(*) c FROM events GROUP BY severity").fetchall()}
        top_eps = [{"endpoint_id": r["endpoint_id"], "count": r["c"]} for r in self._conn.execute(
            "SELECT endpoint_id, COUNT(*) c FROM events GROUP BY endpoint_id ORDER BY c DESC LIMIT 10").fetchall()]
        totals = self._conn.execute(
            "SELECT COUNT(*) c, MIN(received_at) first_at, MAX(received_at) last_at FROM events").fetchone()
        return {
            "days": days,
            "daily": daily,
            "by_type": by_type,
            "by_severity": by_sev,
            "top_endpoints": top_eps,
            "total_events": totals["c"] or 0,
            "first_event_at": totals["first_at"] or "",
            "last_event_at": totals["last_at"] or "",
            "generated_at": _now(),
        }

    # ── 策略 ─────────────────────────────────────────────
    def set_policy(self, scope: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        """写入一条策略（scope='global' 或端点 id）。自动签名并递增版本。"""
        key = self.policy_signing_key()
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(version) v FROM policies WHERE scope=?", (scope,)).fetchone()
            version = int(row["v"] or 0) + 1
            body = {k: v for k, v in bundle.items() if k not in ("sig", "version", "issued_at")}
            body["version"] = version
            body["issued_at"] = _now()
            policy_mod.validate_keys(body)
            body["sig"] = policy_mod.sign(body, key)
            self._conn.execute(
                "INSERT INTO policies(scope,version,bundle_json,updated_at) VALUES(?,?,?,?)",
                (scope, version, json.dumps(body, ensure_ascii=False), _now()))
            self._conn.commit()
        return body

    def latest_policy(self, scope: str) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT bundle_json FROM policies WHERE scope=? ORDER BY version DESC LIMIT 1",
            (scope,)).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["bundle_json"])
        except Exception:
            return None

    def policy_for(self, endpoint_id: str) -> Dict[str, Any]:
        """端点实际会收到的包：全局策略与端点专属策略**按时间顺序累积合并**。

        语义：每次下发都是**增量收紧**（控制台表单"留空＝本条不下发"），因此端点生效的
        策略是"历史上每一次下发依次叠加"的结果，而不是"最后一版覆盖前面所有版"。
        合并本身同样只收紧，所以返回的包依旧是"只收紧"的。

        输出只包含**真正有约束的键**（不输出 null 占位）——否则端点侧的键类型校验会
        把服务端自己签的包判为非法（真踩过：`approval_floor: null` 不在合法枚举里）。
        """
        merged = policy_mod.empty_local()
        for scope in ("global", endpoint_id):
            rows = self._conn.execute(
                "SELECT bundle_json FROM policies WHERE scope=? AND COALESCE(active,1)=1 "
                "ORDER BY version ASC",
                (scope,)).fetchall()
            for row in rows:
                try:
                    bundle = json.loads(row["bundle_json"])
                except Exception:
                    continue
                merged = policy_mod.merge_tighten(merged, bundle)

        out: Dict[str, Any] = {"version": int(merged.get("policy_version", 0) or 0),
                               "issued_at": _now()}
        if merged.get("sensitive_paths"):
            out["sensitive_paths"] = merged["sensitive_paths"]
        if merged.get("approval_floor"):
            out["approval_floor"] = merged["approval_floor"]
        if merged.get("allowed_models") is not None:
            out["allowed_models"] = merged["allowed_models"]
        if merged.get("allowed_plugins") is not None:
            out["allowed_plugins"] = merged["allowed_plugins"]
        if merged.get("require_strict_mode"):
            out["require_strict_mode"] = True
        out["sig"] = policy_mod.sign(out, self.policy_signing_key())
        return out

    def policy_history(self, scope: str = "", limit: int = 30) -> List[Dict[str, Any]]:
        sql = ("SELECT scope,version,updated_at,COALESCE(active,1) AS active FROM policies")
        params: List[Any] = []
        if scope:
            sql += " WHERE scope=?"; params.append(scope)
        sql += " ORDER BY id DESC LIMIT ?"; params.append(max(1, min(int(limit), 100)))
        return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ── 策略回滚（V3.6.2，C-14）─────────────────────────────
    def revert_scope(self, scope: str, version: int) -> Dict[str, Any]:
        """把某个 scope 的生效集合回滚到 `version`（含）之前的下发。

        ★ 语义与边界（别把它当成"放宽"的按钮）：
          · **不删任何历史行**，只把 version > target 的行标为 `active=0` —— 审计仍可回溯
            "谁都下发过什么"；
          · 端点侧收到的是 `merge_tighten(本地, 远程)` 的**单调合并**，所以即便控制面把推荐
            改回更松的版本，端点**不会跟着放宽**——回滚只能影响"控制面推荐什么"。
            这条性质由 `tests/test_control_plane.py` 钉住。
        返回 `{"ok", "scope", "active_versions": [...], "inactive_versions": [...]}`。
        """
        scope = str(scope or "").strip() or "global"
        try:
            target = int(version)
        except Exception:
            return {"ok": False, "error": "version 必须是整数"}
        with self._lock:
            rows = self._conn.execute(
                "SELECT version FROM policies WHERE scope=? ORDER BY version ASC", (scope,)
            ).fetchall()
            versions = [int(r["version"]) for r in rows]
            if not versions:
                return {"ok": False, "error": f"scope={scope} 没有任何策略版本"}
            if target not in versions:
                return {"ok": False, "error": f"版本不存在：{scope} v{target}（现有 {versions}）"}
            self._conn.execute(
                "UPDATE policies SET active = CASE WHEN version<=? THEN 1 ELSE 0 END WHERE scope=?",
                (target, scope))
            self._conn.commit()
        return {"ok": True, "scope": scope, "reverted_to": target,
                "active_versions": [v for v in versions if v <= target],
                "inactive_versions": [v for v in versions if v > target]}

    # ── 备份（V3.6.2，C-14）────────────────────────────────
    def backup(self, dest_dir) -> Dict[str, Any]:
        """把控制面数据库与令牌文件备份到指定目录（SQLite 在线备份 API，读一致性有保证）。

        便携单文件形态下"数据库就是一个文件"，出问题没有回退手段——这条是生产化前提之一。
        备份文件名带时间戳；同时返回可读的路径与大小，便于纳入运维流程。
        """
        dest = Path(dest_dir)
        dest.mkdir(parents=True, exist_ok=True)
        # ★ 文件名带微秒 + 短随机后缀（V3.6.2）：只用"秒级时间戳"时，同一秒内的两次备份
        #   会**互相覆盖**——运维脚本连续备份会静默丢份，等于备份形同虚设（本套件实测踩到）。
        stamp = _now().replace(":", "").replace("-", "").replace("T", "_")[:21]
        target = dest / f"control_{stamp}_{secrets.token_hex(2)}.db"
        src = sqlite3.connect(str(self.path))
        try:
            dst = sqlite3.connect(str(target))
            try:
                with dst:
                    src.backup(dst)          # 在线备份：不阻塞读、不受 WAL 影响
            finally:
                dst.close()
        finally:
            src.close()
        token_src = self.dir / "admin_token.txt"
        if token_src.is_file():
            # 落点受控：备份目录由调用方指定（运维自己的备份盘/目录）
            (dest / f"admin_token_{stamp}.txt").write_text(
                token_src.read_text(encoding="utf-8"), encoding="utf-8")
        return {"ok": True, "path": str(target), "bytes": target.stat().st_size,
                "note": "数据库与令牌文件已备份（令牌文件里是明文令牌，请与备份同等保护）"}

    @staticmethod
    def prune_backups(dest_dir, keep: int = 5) -> int:
        """只保留最近 N 份备份（避免备份目录无限膨胀）。返回删除的份数。"""
        dest = Path(dest_dir)
        if not dest.is_dir():
            return 0
        files = sorted(dest.glob("control_*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        removed = 0
        for item in files[max(1, int(keep)):]:
            try:
                item.unlink()
                removed += 1
            except Exception:
                continue
        return removed

    # ── 只读令牌（V3.6.2，C-14 多角色）────────────────────────
    def ensure_viewer_token(self) -> str:
        """生成/取回**只读**账号令牌（控制台只读巡检用）：读写分离，写操作一律要管理令牌。"""
        with self._lock:
            existing = self._get_setting("viewer_token_hash")
            if existing:
                return self._read_token_file(existing, name="viewer_token.txt")
            token = secrets.token_urlsafe(24)
            self._set_setting("viewer_token_hash", _sha256(token))
            (self.dir / "viewer_token.txt").write_text(
                "# 隐盾控制面**只读**令牌（可查看态势/台账/资产/统计，不能下发策略或登记端点）\n"
                + token + "\n", encoding="utf-8")
            return token

    def verify_viewer(self, token: str) -> bool:
        if not token:
            return False
        expected = self._get_setting("viewer_token_hash")
        return bool(expected) and _sha256(token) == expected

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
