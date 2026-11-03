"""M5 评估回归面板：把离线验收从终端搬进控制台。

与 M2/M3 的只读视图不同，本模块会**启动子进程跑 ``eval/run_acceptance.py``**，
因此有两条硬边界：

1. 仅 ``APP_ENV=dev`` 可用；其他环境的 ``POST /api/eval/run`` 返回 403。
2. 同一时刻只允许一次验收在跑（互斥，409 EVAL_RUNNING）；子进程强制
   ``LLM_BACKEND=fake`` 离线，绝不碰真实 key，与验收脚本自身的口径一致。

数据流：子进程写 ``data/acceptance.json`` → 控制器读回 → 摘要落库
``eval_runs``（store v2）→ 面板轮询 ``/api/eval/status`` 拉最新结果与历史，
和上一条对比即可判断「回归了没有」。历史行存报告全文，失败项明细可回查。
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "acceptance.json"

RUN_TIMEOUT_S = 300          # 子进程硬超时：验收 92 项离线秒级，300s 是保险丝
OUTPUT_TAIL_CHARS = 2000     # 面板能看到的子进程输出尾部长度


class EvalError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class EvalController:
    """评估状态机：idle → running → done/error，全程加锁。

    ``runner`` 可注入（测试用假执行器验编排，不起子进程）；
    默认实现用 ``asyncio.to_thread + subprocess.run``，Windows 事件循环安全。
    """

    ui_module = "M5"

    def __init__(self, state, runner=None):
        # 持有 Starlette app.state，属性每次动态读取（便于测试替换 app_env）
        self._state = state
        self._lock = threading.RLock()
        self._running = False
        self._started_at: float | None = None
        self._last: dict | None = None
        self._task: asyncio.Task | None = None
        self._log: list[dict] = []
        self._runner = runner or self._subprocess_runner

    # ---------- 边界 ----------
    def env_allowed(self) -> bool:
        return self._state.s.app_env == "dev"

    def catalog(self) -> dict:
        enabled = self.env_allowed()
        return {
            "enabled": enabled,
            "app_env": self._state.s.app_env,
            "ui_module": self.ui_module,
            "reason": "" if enabled else
                      "评估执行仅在 APP_ENV=dev 可用，当前环境已禁用写端点",
            "command": f"{os.path.basename(sys.executable)} "
                       f"eval{os.sep}run_acceptance.py",
            "runner_env": {"LLM_BACKEND": "fake", "APP_ENV": "dev",
                           "LOG_LEVEL": "WARNING"},
            "offline": True,
            "timeout_s": RUN_TIMEOUT_S,
            "report_path": "data/acceptance.json",
            "history_table": "eval_runs (store v2)",
            "expect": {
                "ok": "exit 0，92/92 通过，面板总分与分组全部绿",
                "regression": "failed > 0 或 passed 比上一条历史少 → 红色标记",
            },
            "guard": {
                "only_dev": True,
                "exclusive": True,
                "offline": True,
                "write_endpoints": ["POST /api/eval/run"],
            },
        }

    # ---------- 内部工具 ----------
    def _append(self, event: str, detail: str) -> None:
        self._log.append({"ts": round(time.time(), 3), "event": event,
                          "detail": detail})
        if len(self._log) > 40:
            del self._log[: len(self._log) - 40]

    def _require(self) -> None:
        if not self.env_allowed():
            raise EvalError("EVAL_DISABLED",
                            f"评估执行仅允许 APP_ENV=dev，当前为 {self._state.s.app_env}")

    def _read_report_file(self) -> dict | None:
        try:
            return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _summarize(report: dict | None) -> dict:
        if not isinstance(report, dict):
            return {"passed": 0, "failed": 0, "total": 0, "groups": {},
                    "report_available": False}
        groups = report.get("groups") or {}
        return {
            "passed": int(report.get("passed") or 0),
            "failed": int(report.get("failed") or 0),
            "total": int(report.get("total") or 0),
            "groups": groups if isinstance(groups, dict) else {},
            "report_available": True,
        }

    # ---------- 默认执行器：子进程真跑离线验收 ----------
    async def _subprocess_runner(self):
        env = os.environ.copy()
        env.update({"LLM_BACKEND": "fake", "APP_ENV": "dev",
                    "LOG_LEVEL": "WARNING", "PYTHONIOENCODING": "utf-8"})

        def _blocking() -> tuple[int, str, int]:
            t0 = time.monotonic()
            try:
                r = subprocess.run(
                    [sys.executable, str(ROOT / "eval" / "run_acceptance.py")],
                    cwd=str(ROOT), env=env, capture_output=True, text=True,
                    timeout=RUN_TIMEOUT_S, encoding="utf-8", errors="replace")
                out = (r.stdout or "") + (r.stderr or "")
                return r.returncode, out, int((time.monotonic() - t0) * 1000)
            except subprocess.TimeoutExpired as exc:
                return 124, f"子进程超时（{RUN_TIMEOUT_S}s）: {exc}", \
                    int((time.monotonic() - t0) * 1000)

        rc, out, duration_ms = await asyncio.to_thread(_blocking)
        report = self._read_report_file()
        return rc, report, duration_ms, out[-OUTPUT_TAIL_CHARS:]

    # ---------- 对外操作 ----------
    def run(self, source: str = "api") -> dict:
        """启动一次验收（立即返回，进度看 status）。互斥，重复启动 409。"""
        self._require()
        with self._lock:
            if self._running:
                raise EvalError("EVAL_RUNNING", "已有一次验收在跑，等它结束再启动")
            self._running = True
            self._started_at = time.time()
            self._append("start", f"source={source}")
        loop = asyncio.get_running_loop()
        self._task = loop.create_task(self._execute(source))
        return {"status": self.status()}

    async def _execute(self, source: str) -> None:
        try:
            rc, report, duration_ms, tail = await self._runner()
        except Exception as exc:  # 执行器异常也绝不外抛，转成一条失败记录
            rc, report, duration_ms, tail = (
                None, None, 0, f"runner error: {type(exc).__name__}: {exc}")
        summary = self._summarize(report)
        with self._lock:
            self._running = False
            db_id = None
            try:
                db_id = self._state.store.add_eval_run(
                    passed=summary["passed"], failed=summary["failed"],
                    total=summary["total"], source=source, exit_code=rc,
                    duration_ms=duration_ms, groups=summary["groups"],
                    report=report)
            except Exception as exc:
                tail = (tail + f" | eval_runs 落库失败: {exc}")[-OUTPUT_TAIL_CHARS:]
            self._last = {
                "id": db_id, "source": source, "exit_code": rc,
                "duration_ms": duration_ms, "finished_at": round(time.time(), 3),
                "output_tail": tail, **summary,
            }
            event = "done" if rc == 0 else "error"
            self._append(event,
                         f"exit={rc} · {summary['passed']}/{summary['total']} · "
                         f"{duration_ms}ms")

    def status(self) -> dict:
        with self._lock:
            out = {
                "enabled": self.env_allowed(),
                "app_env": self._state.s.app_env,
                "ui_module": self.ui_module,
                "running": self._running,
                "started_at": self._started_at,
                "last": dict(self._last) if self._last else None,
                "log": list(reversed(self._log[-12:])),
                "ts": round(time.time(), 3),
            }
            if self._running and self._started_at:
                out["elapsed_s"] = round(time.time() - self._started_at, 1)
        out["history_count"] = self._state.store.eval_runs_count()
        out["regression"] = self.regression()
        return out

    def regression(self) -> dict | None:
        """最近两条历史对比：failed 变多或 passed 变少即视为回归。"""
        rows = self._state.store.list_eval_runs(2)
        if len(rows) < 2:
            return None
        last, prev = rows[0], rows[1]
        return {
            "last_id": last["id"], "prev_id": prev["id"],
            "last_passed": last["passed"], "prev_passed": prev["passed"],
            "delta_passed": last["passed"] - prev["passed"],
            "delta_failed": last["failed"] - prev["failed"],
            "regressed": last["failed"] > prev["failed"] or last["passed"] < prev["passed"],
        }

    def report(self) -> dict:
        """最新报告文件 + 最近一次执行摘要（文件是 run_acceptance.py 的产物）。"""
        report = self._read_report_file()
        with self._lock:
            last = dict(self._last) if self._last else None
        try:
            rel = str(REPORT_PATH.relative_to(ROOT))
        except ValueError:  # 测试或自定义路径不在项目根下时，退回绝对路径
            rel = str(REPORT_PATH)
        return {
            "report": report,
            "report_path": rel,
            "report_available": report is not None,
            "summary": self._summarize(report),
            "last": last,
            "regression": self.regression(),
            "fetched_at": round(time.time(), 3),
        }

    def history(self, limit: int = 10) -> list[dict]:
        return self._state.store.list_eval_runs(limit)

    def get_history(self, run_id: int) -> dict | None:
        return self._state.store.get_eval_run(run_id)

    def close(self) -> None:
        """进程退出兜底：取消在途评估任务，绝不把子进程留到下一次启动。"""
        with self._lock:
            task, self._task = self._task, None
            cancelled = self._running
            self._running = False
            self._started_at = None
            if cancelled:
                self._append("shutdown_cancel", "在途评估任务已取消")
        if task is not None and not task.done():
            task.cancel()
