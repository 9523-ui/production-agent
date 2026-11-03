"""持久化层：SQLite WAL + 事务 + 幂等唯一键 + 会话 TTL。

生产级要点：
- 所有 run / step / session 落库，重启不丢，替代原来重启清零的内存计数器。
- 幂等靠数据库 UNIQUE 约束，不靠「应用层先查再写」的竞态窗口。
- 审计字段齐全：trace_id、model、backend、token、成本、延迟、错误码、时间。
- 结构演进走版本化迁移：MIGRATIONS 单向追加，PRAGMA user_version 记录进度，
  禁止修改已发布的历史脚本（基线脚本全部 IF NOT EXISTS，旧库重放幂等安全）。

诚实声明：SQLite + 同步连接适合单机/中小流量；多副本部署必须换 PostgreSQL
并使用异步驱动，接口保持不变（替换本文件即可）。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
import uuid

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY,
    idempotency_key TEXT UNIQUE,
    session_id      TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    request_text    TEXT NOT NULL,
    reply_text      TEXT,
    status          TEXT NOT NULL,
    error_code      TEXT,
    model           TEXT,
    backend         TEXT,
    rounds          INTEGER DEFAULT 0,
    tokens_in       INTEGER DEFAULT 0,
    tokens_out      INTEGER DEFAULT 0,
    cost_cny        REAL DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    degraded        INTEGER DEFAULT 0,
    created_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_runs_status  ON runs(status);

CREATE TABLE IF NOT EXISTS steps (
    step_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    tool       TEXT NOT NULL,
    args_json  TEXT NOT NULL,
    result     TEXT NOT NULL,
    ok         INTEGER DEFAULT 1,
    ms         INTEGER DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, seq);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    turns      INTEGER DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    ticket_id  TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    session_id TEXT NOT NULL,
    order_id   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    created_at REAL NOT NULL
);
"""

# v2：M5 评估回归面板。每跑一次验收落一行，报告全文存 report_json，
# 分组通过率冗余存 groups_json（面板列表页不必逐条反序列化大 JSON）。
EVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS eval_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  REAL NOT NULL,
    source      TEXT NOT NULL DEFAULT 'api',
    exit_code   INTEGER,
    duration_ms INTEGER DEFAULT 0,
    passed      INTEGER NOT NULL,
    failed      INTEGER NOT NULL,
    total       INTEGER NOT NULL,
    groups_json TEXT NOT NULL DEFAULT '{}',
    report_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_runs_time ON eval_runs(created_at);
"""

# v3：M7 知识库与长期记忆。kb_chunks 存分块正文（BM25 索引在进程内），
# memories 按 session_id 隔离——长期记忆：会话重置（reset）不删，
# 只能显式删除/清空，这正是「长期」的语义边界。
KB_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    source      TEXT,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS kb_chunks (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id  INTEGER NOT NULL,
    seq     INTEGER NOT NULL,
    chunk   TEXT NOT NULL,
    FOREIGN KEY (doc_id) REFERENCES kb_docs(id)
);
CREATE INDEX IF NOT EXISTS idx_kb_chunks_doc ON kb_chunks(doc_id);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    kind        TEXT NOT NULL DEFAULT 'fact',
    content     TEXT NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id, id);
"""

_log = logging.getLogger(__name__)

# 版本化迁移：只允许追加 (version, sql) 条目，禁止改写已发布脚本。
# v1 = 基线四表；v0 = 旧库（建表时未记版本），重放基线幂等安全。
MIGRATIONS: list[tuple[int, str]] = [
    (1, SCHEMA),
    (2, EVAL_SCHEMA),
    (3, KB_SCHEMA),
]
SCHEMA_VERSION = MIGRATIONS[-1][0]


class Store:
    def __init__(self, path: str):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def _migrate(self) -> None:
        """按序应用未执行的迁移，PRAGMA user_version 记录进度。

        每个迁移独立提交后写版本号：中途崩溃最多留下「表已建但版本未写」，
        下次启动重放幂等脚本即可收敛，不会出现半套 schema。
        """
        current = self._conn.execute("PRAGMA user_version").fetchone()[0]
        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            self._conn.executescript(sql)
            self._conn.execute(f"PRAGMA user_version={int(version)}")
            self._conn.commit()
            _log.info("store schema migrated to v%s", version)
        self.schema_version = self._conn.execute("PRAGMA user_version").fetchone()[0]

    def create_run(self, *, session_id: str, trace_id: str, request_text: str,
                   model: str, backend: str, idempotency_key: str | None) -> tuple[str, bool, dict | None]:
        """返回 (run_id, is_new, 已有结果)。重复 key 由 UNIQUE 约束兜底。"""
        run_id = uuid.uuid4().hex[:16]
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO runs
                   (run_id, idempotency_key, session_id, trace_id, request_text,
                    status, model, backend, created_at)
                   VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?)
                   ON CONFLICT(idempotency_key) DO NOTHING""",
                (run_id, idempotency_key, session_id, trace_id,
                 request_text, model, backend, now),
            )
            self._conn.commit()
            if cur.rowcount == 0 and idempotency_key:
                row = self._conn.execute(
                    "SELECT * FROM runs WHERE idempotency_key=?", (idempotency_key,)
                ).fetchone()
                return row["run_id"], False, dict(row)
            return run_id, True, None

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            return dict(row) if row else None

    def list_runs(self, limit: int = 20) -> list[dict]:
        """最近运行列表（UI 侧栏 / 轨迹回放）。只读，显式列字段避免误传内部列。"""
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """SELECT run_id, session_id, trace_id, request_text, reply_text,
                          status, error_code, rounds, latency_ms, cost_cny,
                          degraded, created_at
                   FROM runs ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def query_runs(self, *, status: str | None = None,
                   session_id: str | None = None,
                   error_code: str | None = None,
                   degraded: bool | None = None,
                   q: str | None = None,
                   since_ts: float | None = None,
                   limit: int = 50, offset: int = 0) -> dict:
        """M3 运行历史：多条件筛选 + 总数 + 分页 + 下拉 facets。

        - 用 instr() 做子串匹配，避免 LIKE 把用户输入里的 % / _ 当通配符。
        - 所有条件参数化；limit clamp 1..200，offset >= 0。
        - facets 从全表统计（不受当前筛选影响），供 UI 渲染下拉选项。
        """
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        where: list[str] = []
        args: list = []
        if status:
            where.append("status = ?"); args.append(status)
        if session_id:
            where.append("session_id = ?"); args.append(session_id)
        if error_code:
            where.append("error_code = ?"); args.append(error_code)
        if degraded is not None:
            where.append("degraded = ?"); args.append(1 if degraded else 0)
        if q:
            where.append("(instr(request_text, ?) > 0 OR "
                         "instr(COALESCE(reply_text, ''), ?) > 0)")
            args.extend([q, q])
        if since_ts is not None:
            where.append("created_at >= ?"); args.append(float(since_ts))
        clause = (" WHERE " + " AND ".join(where)) if where else ""

        cols = ("run_id, session_id, trace_id, request_text, reply_text, "
                "status, error_code, rounds, latency_ms, cost_cny, "
                "degraded, created_at")
        with self._lock:
            total = self._conn.execute(
                "SELECT COUNT(*) FROM runs" + clause, args).fetchone()[0]
            rows = self._conn.execute(
                f"SELECT {cols} FROM runs{clause} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                args + [limit, offset]).fetchall()
            status_rows = self._conn.execute(
                "SELECT status, COUNT(*) n FROM runs GROUP BY status ORDER BY n DESC").fetchall()
            session_rows = self._conn.execute(
                "SELECT session_id, COUNT(*) n FROM runs GROUP BY session_id "
                "ORDER BY n DESC LIMIT 20").fetchall()
            code_rows = self._conn.execute(
                "SELECT error_code, COUNT(*) n FROM runs "
                "WHERE error_code IS NOT NULL AND error_code != '' "
                "GROUP BY error_code ORDER BY n DESC").fetchall()
            deg_rows = self._conn.execute(
                "SELECT degraded, COUNT(*) n FROM runs GROUP BY degraded").fetchall()
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "runs": [dict(r) for r in rows],
            "facets": {
                "status": {r["status"]: r["n"] for r in status_rows},
                "sessions": [{"session_id": r["session_id"], "n": r["n"]}
                             for r in session_rows],
                "error_codes": {r["error_code"]: r["n"] for r in code_rows},
                "degraded": {("yes" if r["degraded"] else "no"): r["n"]
                             for r in deg_rows},
            },
        }

    def finish_run(self, run_id: str, *, reply: str, status: str, error_code: str | None,
                   rounds: int, tokens_in: int, tokens_out: int,
                   cost_cny: float, latency_ms: int, degraded: bool) -> None:
        with self._lock:
            self._conn.execute(
                """UPDATE runs SET reply_text=?, status=?, error_code=?, rounds=?,
                   tokens_in=?, tokens_out=?, cost_cny=?, latency_ms=?, degraded=?
                   WHERE run_id=?""",
                (reply, status, error_code, rounds, tokens_in, tokens_out,
                 round(cost_cny, 6), latency_ms, 1 if degraded else 0, run_id),
            )
            self._conn.commit()

    def add_step(self, run_id: str, seq: int, tool: str, args: dict,
                 result: str, ok: bool, ms: int) -> None:
        import json
        with self._lock:
            self._conn.execute(
                "INSERT INTO steps(run_id,seq,tool,args_json,result,ok,ms) VALUES(?,?,?,?,?,?,?)",
                (run_id, seq, tool, json.dumps(args, ensure_ascii=False),
                 result[:2000], 1 if ok else 0, ms),
            )
            self._conn.commit()

    def steps_for(self, run_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq,tool,args_json,result,ok,ms FROM steps WHERE run_id=? ORDER BY seq",
                (run_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def touch_session(self, session_id: str) -> int:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT INTO sessions(session_id,turns,created_at,updated_at)
                   VALUES(?,1,?,?)
                   ON CONFLICT(session_id) DO UPDATE SET
                     turns=turns+1, updated_at=excluded.updated_at""",
                (session_id, now, now),
            )
            self._conn.commit()
            return self._conn.execute(
                "SELECT turns FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()["turns"]

    def create_ticket(self, *, run_id: str, session_id: str,
                      order_id: str, reason: str) -> str:
        """工单号用毫秒+随机后缀，UNIQUE 兜底并发重复建单。"""
        ticket_id = "T" + str(int(time.time() * 1000)) + uuid.uuid4().hex[:4].upper()
        with self._lock:
            self._conn.execute(
                "INSERT INTO tickets(ticket_id,run_id,session_id,order_id,reason,created_at) VALUES(?,?,?,?,?,?)",
                (ticket_id, run_id, session_id, order_id, reason, time.time()),
            )
            self._conn.commit()
        return ticket_id

    def count_tickets(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]

    # ---------- M5 评估回归历史 ----------

    def add_eval_run(self, *, passed: int, failed: int, total: int,
                     source: str = "api", exit_code: int | None = None,
                     duration_ms: int = 0, groups: dict | None = None,
                     report: dict | None = None) -> int:
        """落一条验收运行记录，返回自增 id。"""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO eval_runs
                   (created_at, source, exit_code, duration_ms, passed, failed,
                    total, groups_json, report_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), source, exit_code, int(duration_ms),
                 int(passed), int(failed), int(total),
                 json.dumps(groups or {}, ensure_ascii=False),
                 json.dumps(report, ensure_ascii=False) if report is not None else None),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_eval_runs(self, limit: int = 10) -> list[dict]:
        """最近 N 次验收（列表页）。report_json 不返回，点开单条再看。"""
        limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, created_at, source, exit_code, duration_ms,
                          passed, failed, total, groups_json
                   FROM eval_runs ORDER BY id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["groups"] = json.loads(d.pop("groups_json") or "{}")
            except json.JSONDecodeError:
                d["groups"] = {}
            out.append(d)
        return out

    def get_eval_run(self, run_id: int) -> dict | None:
        """单条验收记录，含完整报告（失败项明细在 report.checks 里）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM eval_runs WHERE id=?", (int(run_id),)
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        try:
            d["groups"] = json.loads(d.pop("groups_json") or "{}")
        except json.JSONDecodeError:
            d["groups"] = {}
        raw = d.pop("report_json")
        try:
            d["report"] = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            d["report"] = None
        return d

    def eval_runs_count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0]

    # ---------- M7 知识库 ----------

    def add_kb_doc(self, *, title: str, source: str | None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO kb_docs(title, source, created_at) VALUES(?,?,?)",
                (title, source, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def add_kb_chunks(self, doc_id: int, chunks: list[str]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO kb_chunks(doc_id, seq, chunk) VALUES(?,?,?)",
                [(doc_id, i, c) for i, c in enumerate(chunks)],
            )
            self._conn.commit()

    def delete_kb_doc(self, doc_id: int) -> bool:
        """先删分块再删文档（FK 约束顺序），返回是否删到。"""
        with self._lock:
            self._conn.execute("DELETE FROM kb_chunks WHERE doc_id=?", (int(doc_id),))
            cur = self._conn.execute("DELETE FROM kb_docs WHERE id=?", (int(doc_id),))
            self._conn.commit()
            return cur.rowcount > 0

    def list_kb_docs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT d.id, d.title, d.source, d.created_at,
                          (SELECT COUNT(*) FROM kb_chunks c WHERE c.doc_id = d.id) chunk_count
                   FROM kb_docs d ORDER BY d.id DESC"""
            ).fetchall()
            return [dict(r) for r in rows]

    def kb_chunks_all(self) -> list[tuple[int, int, str]]:
        """BM25 索引重建用：全量分块 (doc_id, seq, chunk)。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id, seq, chunk FROM kb_chunks ORDER BY doc_id, seq"
            ).fetchall()
            return [(r["doc_id"], r["seq"], r["chunk"]) for r in rows]

    # ---------- M7 长期记忆 ----------

    def add_memory(self, *, session_id: str, content: str, kind: str = "fact") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO memories(session_id, kind, content, created_at) VALUES(?,?,?,?)",
                (session_id, kind, content, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_memories(self, session_id: str, limit: int = 50) -> list[dict]:
        """最新在前（id DESC）——注入上下文与 UI 展示共用这个顺序。"""
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, content, hits, created_at FROM memories "
                "WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def memories_all_for(self, session_id: str) -> list[dict]:
        """memory_recall 评分用：最多取 200 条防大会话拖慢检索。"""
        return self.list_memories(session_id, limit=200)

    def delete_memory(self, memory_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE id=?", (int(memory_id),)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def clear_memories(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM memories WHERE session_id=?", (session_id,)
            )
            self._conn.commit()
            return cur.rowcount

    def bump_memory_hits(self, ids: list[int]) -> None:
        """recall 命中计数 +1（观察面数据，不参与排序权重）。"""
        if not ids:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE memories SET hits = hits + 1 WHERE id=?",
                [(int(i),) for i in ids],
            )
            self._conn.commit()

    def count_memories(self, session_id: str) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM memories WHERE session_id=?", (session_id,)
            ).fetchone()[0]

    def stats(self) -> dict:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) n, AVG(latency_ms) avg_ms FROM runs GROUP BY status"
            ).fetchall()
            total_cost = self._conn.execute("SELECT COALESCE(SUM(cost_cny),0) FROM runs").fetchone()[0]
            steps = self._conn.execute("SELECT COUNT(*) FROM steps").fetchone()[0]
            sessions = self._conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        return {
            "by_status": {r["status"]: r["n"] for r in rows},
            "avg_latency_ms": {r["status"]: round(r["avg_ms"] or 0, 1) for r in rows},
            "total_cost_cny": round(total_cost, 6),
            "steps": steps,
            "sessions": sessions,
            "tickets": self.count_tickets(),
        }

    def purge_expired(self, ttl_s: int) -> int:
        cutoff = time.time() - ttl_s
        with self._lock:
            cur = self._conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
            self._conn.commit()
            return cur.rowcount

    def ready(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def close(self) -> None:
        with self._lock:
            self._conn.close()
