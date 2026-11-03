"""M6 交付视图：把「交付给谁看什么」收进一屏。

与 M4/M5 不同，本模块**全程只读**：不起子进程、不改任何状态、没有写端点。
它回答交付评审者站在陌生机器前会问的五个问题：

- 这是什么版本？（版本号来源 VERSION 文件，缺失时回退内置号）
- 怎么跑起来？（命令清单：验收 / 冒烟 / 起服务 / 容器 / 接真实模型）
- 交付物齐不齐？（README / Dockerfile / compose / .env.example / 验收脚本 清单）
- 验过什么？（离线验收最新摘要 + eval_runs 落库历史）
- 运行时边界？（环境 / 后端 / 鉴权口径 / 数据库 schema 版本与行数）

任何环境（含 prod）都可查看——只读视图没有 dev 限制，这也正是它和
M4 / M5 写端点最大的区别。所有文件读取都有容错：缺文件给 False / None，
绝不 500。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATH = ROOT / "VERSION"
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
REPORT_PATH = ROOT / "data" / "acceptance.json"

FALLBACK_VERSION = "2.3.0"

# 交付物清单：path → 给评审者看的一句话（存在性 + 大小都来自文件系统实况）
DELIVERY_FILES: tuple[tuple[str, str], ...] = (
    ("README.md", "项目说明：快速开始 / 端点 / 模块 / 验收口径"),
    ("Dockerfile", "容器镜像定义（非 root / HEALTHCHECK）"),
    ("docker-compose.yml", "一键容器化交付（数据卷 / 优雅退出）"),
    ("requirements.txt", "Python 依赖锁定"),
    (".env.example", "环境变量样例（密钥占位，不入库）"),
    ("eval/run_acceptance.py", "离线验收脚本（A1–A10）"),
    ("eval/smoke_ui.py", "UI 冒烟脚本（S1–S7）"),
    ("eval/cases.jsonl", "回归用例集"),
    ("data/acceptance.json", "最近一次验收报告（面板证据）"),
    ("VERSION", "版本号（交付视图读取）"),
    ("CHANGELOG.md", "变更记录（收口阶段维护）"),
)

COMMANDS: tuple[dict, ...] = (
    {"label": "离线验收", "note": "全程 FakeLLM，不碰真实 key",
     "command": "PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe eval/run_acceptance.py"},
    {"label": "UI 冒烟", "note": "静态页 + 接口 + 真跑一次验收子进程",
     "command": "PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe eval/smoke_ui.py"},
    {"label": "起服务", "note": "默认 LLM_BACKEND=fake，控制台在根路径",
     "command": "PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m prodagent.app"},
    {"label": "容器化", "note": "非 root + HEALTHCHECK + 数据卷",
     "command": "docker compose up --build"},
    {"label": "接真实模型", "note": "生产前在 staging 冒烟（见 README 诚实声明）",
     "command": "DEEPSEEK_API_KEY=xxx LLM_BACKEND=deepseek .venv/Scripts/python.exe -m prodagent.app"},
)


class DeliverController:
    """交付清单装配器：无状态、无锁、无副作用，每次请求现算。"""

    ui_module = "M6"

    def __init__(self, state):
        # 持有 app.state（.s Settings / .store），动态读取便于测试替换环境
        self._state = state

    # ---------- 内部工具 ----------
    @staticmethod
    def _read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def _version(self) -> tuple[str, str]:
        raw = self._read_text(VERSION_PATH)
        if raw:
            first = raw.splitlines()[0].strip()
            if first:
                return first, "VERSION"
        return FALLBACK_VERSION, "fallback"

    def _changelog_head(self) -> str | None:
        raw = self._read_text(CHANGELOG_PATH)
        if not raw:
            return None
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        return lines[1] if len(lines) > 1 else (lines[0] if lines else None)

    def _file_inventory(self) -> list[dict]:
        out = []
        for rel, desc in DELIVERY_FILES:
            p = ROOT / rel
            try:
                size = p.stat().st_size if p.exists() else None
            except OSError:
                size = None
            out.append({"path": rel, "desc": desc,
                        "exists": size is not None,
                        "size_bytes": size})
        return out

    def _evidence(self) -> dict:
        try:
            report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report = None
        summary = ({"passed": int(report.get("passed") or 0),
                    "failed": int(report.get("failed") or 0),
                    "total": int(report.get("total") or 0),
                    "report_available": True}
                   if isinstance(report, dict) else
                   {"passed": 0, "failed": 0, "total": 0,
                    "report_available": False})
        last = None
        try:
            rows = self._state.store.list_eval_runs(1)
            if rows:
                r = rows[0]
                last = {"id": r["id"], "passed": r["passed"],
                        "failed": r["failed"], "total": r["total"],
                        "exit_code": r["exit_code"],
                        "finished_at": r["created_at"],
                        "duration_ms": r["duration_ms"]}
            count = self._state.store.eval_runs_count()
        except Exception:
            count = 0
        summary["eval_runs_count"] = count
        summary["last_run"] = last
        return summary

    def _datastore(self) -> dict:
        s = self._state.s
        p = Path(s.db_path)
        if p.is_absolute():
            try:
                rel = str(p.relative_to(ROOT))
            except ValueError:
                rel = str(p)
        else:
            rel = str(p)
        st = self._state.store.stats()
        by_status = st.get("by_status") or {}
        return {
            "db_path": rel,
            "schema_version": self._state.store.schema_version,
            "wal": True,
            "counts": {
                "runs": sum(by_status.values()),
                "steps": st.get("steps", 0),
                "sessions": st.get("sessions", 0),
                "tickets": st.get("tickets", 0),
                "eval_runs": self._state.store.eval_runs_count(),
            },
        }

    # ---------- 对外操作 ----------
    def manifest(self) -> dict:
        """交付清单（全只读，任何环境可看，任何文件缺失都容错）。"""
        s = self._state.s
        version, version_source = self._version()
        return {
            "service": "koda",
            "version": version,
            "version_source": version_source,
            "changelog_head": self._changelog_head(),
            "ui_module": self.ui_module,
            "read_only": True,
            "env": {
                "app_env": s.app_env,
                "llm_backend": s.llm_backend,
                "model": s.model,
                # M8：三令牌任一配置（或 dev 演示令牌开启）即视为鉴权已启用
                "auth_required": bool(
                    s.auth_token or s.auth_token_staff or s.auth_token_customer
                    or getattr(self._state, "role_tokens", {})),
                "host": s.host,
                "port": s.port,
            },
            "guard": {
                "read_only": True,
                "write_endpoints": [],
                "note": "交付视图无任何写端点，dev / staging / prod 均可查看",
            },
            "datastore": self._datastore(),
            "commands": [dict(c) for c in COMMANDS],
            "delivery_files": self._file_inventory(),
            "evidence": self._evidence(),
            "links": {
                "console": "/", "openapi": "/docs", "meta": "/api/meta",
                "metrics": "/metrics", "healthz": "/healthz", "readyz": "/readyz",
            },
            "ts": round(time.time(), 3),
        }
