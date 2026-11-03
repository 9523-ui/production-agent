"""HTTP 层：装配、鉴权、trace_id 中间件、健康检查、指标、静态控制台、优雅退出。

启动:  python -m prodagent.app
访问:  http://127.0.0.1:8010/       UI 控制台（M1 对话 + M2 运行时状态 + M3 运行历史 + M4 故障演练台 + M5 评估回归 + M6 交付视图 + M7 知识库与长期记忆 + M8 身份与权限）
       http://127.0.0.1:8010/docs   OpenAPI 文档
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, JSONResponse, PlainTextResponse,
                               Response)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agent import ChatRequest, ProductionAgent
from .deliverpanel import DeliverController
from .drill import DrillController, DrillError
from .evalpanel import EvalController, EvalError
from .guard import ALLOWED_TOOLS
from .llm import DeepSeekLLM, FakeLLM
from .logging_conf import configure, get_logger, new_trace_id, set_trace_id
from .metrics import Metrics
from .resilience import (BudgetLedger, CircuitBreaker, ConcurrencyGate,
                         SlidingWindowLimiter)
from .rbac import identities_payload, label_of, mask_token, rank_of, \
    resolve_role, token_map
from .settings import ConfigError, Settings
from .store import Store

_EXEMPT = {"/", "/favicon.ico", "/api/meta", "/api/whoami",
           "/api/auth/identities", "/healthz", "/readyz",
           "/metrics", "/openapi.json", "/docs"}
_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)


def _build(settings: Settings, llm=None) -> dict:
    store = Store(settings.db_path)
    return {
        "settings": settings,
        "store": store,
        "metrics": Metrics(),
        "breaker": CircuitBreaker(settings.circuit_threshold, settings.circuit_cooldown_s),
        "gate": ConcurrencyGate(settings.max_concurrency),
        "limiter": SlidingWindowLimiter(settings.per_session_quota, 60.0),
        "budget": BudgetLedger(settings.daily_budget_cny),
        "llm": llm or (FakeLLM() if settings.llm_backend == "fake" else
                       DeepSeekLLM(api_key=settings.api_key, base_url=settings.base_url,
                                   model=settings.model,
                                   timeout_s=settings.request_timeout_s)),
    }


def _agent(state: dict) -> ProductionAgent:
    return ProductionAgent(
        settings=state["settings"], llm=state["llm"], store=state["store"],
        metrics=state["metrics"], breaker=state["breaker"], gate=state["gate"],
        limiter=state["limiter"], budget=state["budget"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        # fail-fast：配置错误不允许以半可用状态启动
        raise RuntimeError(f"配置校验失败: {exc}") from exc
    configure(settings.log_level)
    log = get_logger("app")
    state = _build(settings)
    log.info("starting", extra={"x_config": settings.redacted()})
    app.state.s = settings
    app.state.store = state["store"]
    app.state.metrics = state["metrics"]
    app.state.breaker = state["breaker"]
    app.state.gate = state["gate"]
    app.state.budget = state["budget"]
    app.state.limiter = state["limiter"]
    app.state.started_at = time.time()
    # M8：令牌 -> 角色映射（演示令牌仅 dev + 开关开启时进入）
    app.state.role_tokens = token_map(settings)
    app.state.agent = _agent(state)
    app.state.log = log
    app.state.drill = DrillController(app.state)
    app.state.eval = EvalController(app.state)
    app.state.deliver = DeliverController(app.state)
    # M7：知识库与 agent 共用同一 Store；控制器无自身后台任务
    app.state.kb = app.state.agent.kb

    async def janitor():
        while True:
            await asyncio.sleep(60)
            n = await asyncio.to_thread(state["store"].purge_expired,
                                        settings.session_ttl_s)
            # 同一个 TTL 口径清内存侧：_history / _session_locks / touch 时间戳
            m = app.state.agent.evict_expired(settings.session_ttl_s)
            if n or m:
                log.info("sessions_purged", extra={
                    "x_count": n, "x_context_evicted": m})

    async def drill_janitor():
        """M4：每秒惰性检查 TTL，即使没人打开面板也能自动复位故障状态。"""
        while True:
            await asyncio.sleep(1)
            app.state.drill.tick()

    task = asyncio.create_task(janitor())
    drill_task = asyncio.create_task(drill_janitor())
    try:
        yield
    finally:
        task.cancel()
        drill_task.cancel()
        for t in (task, drill_task):
            with contextlib.suppress(asyncio.CancelledError):
                await t
        # 退出前强制复位，保证演练状态绝不残留到下一次启动
        app.state.drill.reset_all()
        # 在途评估任务同样取消，避免子进程跨生命周期存活
        app.state.eval.close()
        # 优雅退出：等在途请求收敛，最多 5 秒
        deadline = time.monotonic() + 5.0
        while state["gate"].in_flight > 0 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        log.info("shutdown", extra={"x_in_flight": state["gate"].in_flight})
        state["store"].close()


app = FastAPI(title="koda", version="2.3.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _current_role(request: Request) -> str:
    """当前请求角色。

    - 未配置任何令牌 = 开放模式（本地 dev 默认）：匿名即全权限（leader），
      保持与 M1–M7 行为完全兼容（既有验收/冒烟全部不配令牌直跑）。
    - 配置了令牌：非豁免路径由中间件写入 role；豁免路径自查令牌头，
      无有效令牌时为 anonymous（rank 最低，仅能访问对全角色开放的端点）。
    """
    tokens: dict = getattr(request.app.state, "role_tokens", {})
    if not tokens:
        return "leader"
    role = getattr(request.state, "role", None)
    if role:
        return role
    return resolve_role(request.headers.get("Authorization"), tokens) or "anonymous"


def _require_rank(request: Request, min_rank: int):
    """角色门禁：rank 不足返回 403 JSONResponse，通过返回 None。"""
    role = _current_role(request)
    if rank_of(role) >= min_rank:
        return None
    return JSONResponse({
        "error": f"当前身份「{label_of(role)}」无权访问该资源",
        "error_code": "ROLE_FORBIDDEN", "role": role,
    }, status_code=403)


@app.middleware("http")
async def context_middleware(request: Request, call_next):
    tid = request.headers.get("X-Request-ID") or new_trace_id()
    set_trace_id(tid)
    request.state.trace_id = tid
    t0 = time.perf_counter()
    settings: Settings | None = getattr(request.app.state, "s", None)
    path = request.url.path
    # M8 鉴权：配置了任意角色令牌即开启；未配置 = 开放模式（本地 dev 默认，全权限）。
    # AUTH_TOKEN 沿用单令牌语义并映射为 leader；旧调用方无需改动。
    tokens: dict = getattr(request.app.state, "role_tokens", {})
    if tokens and settings and path not in _EXEMPT and not path.startswith("/static/"):
        role = resolve_role(request.headers.get("Authorization"), tokens)
        if role is None:
            return JSONResponse({"error": "unauthorized", "trace_id": tid},
                                status_code=401)
        request.state.role = role
        # M8：内部运维面板（故障演练 / 评估）员工及以上（原有 dev 门禁不变）
        if path.startswith(("/api/drill/", "/api/eval/")) \
                and rank_of(role) < rank_of("staff"):
            return JSONResponse(
                {"error": "该面板仅限内部员工使用", "error_code": "ROLE_FORBIDDEN",
                 "role": role}, status_code=403)
    try:
        response = await call_next(request)
    except Exception as exc:
        get_logger("http").error("unhandled", extra={
            "x_error": f"{type(exc).__name__}: {exc}", "x_path": path})
        response = JSONResponse({"error": "internal_error", "trace_id": tid},
                                status_code=500)
    response.headers["X-Trace-Id"] = tid
    response.headers["X-Response-Time-Ms"] = str(round((time.perf_counter() - t0) * 1000))
    return response


class ChatIn(BaseModel):
    message: str = Field(description="用户消息")
    session_id: str = Field(default="default")
    idempotency_key: str | None = Field(default=None)


class ResetIn(BaseModel):
    session_id: str = "default"


@app.post("/api/chat")
async def chat(req: ChatIn, request: Request):
    result = await app.state.agent.chat(
        ChatRequest(message=req.message, session_id=req.session_id,
                    idempotency_key=req.idempotency_key,
                    trace_id=getattr(request.state, "trace_id", None)))
    body = result.as_dict()
    if result.status == "rejected":
        code = 400 if result.error_code in (
            "EMPTY_MESSAGE", "MESSAGE_TOO_LONG", "PROMPT_INJECTION", "INVALID_TYPE") else 429
        return JSONResponse(body, status_code=code)
    if result.status == "error":
        return JSONResponse(body, status_code=503)
    return body


@app.post("/api/reset")
async def reset(req: ResetIn):
    app.state.agent.reset_session(req.session_id)
    return {"status": "ok", "session_id": req.session_id}


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str, request: Request):
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    run = app.state.store.get_run(run_id)
    if not run:
        return JSONResponse({"error": "run_not_found"}, status_code=404)
    return {"run": run, "steps": app.state.store.steps_for(run_id)}


@app.get("/api/runs")
async def list_runs(limit: int = 20, request: Request = None):
    """最近运行列表：M1 侧栏使用。只读，不暴露内部异常。"""
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    return {"runs": app.state.store.list_runs(limit)}


@app.get("/api/history")
async def history(status: str | None = None, session_id: str | None = None,
                  error_code: str | None = None, degraded: bool | None = None,
                  q: str | None = None, hours: float | None = None,
                  limit: int = 50, offset: int = 0, request: Request = None):
    """M3 运行历史：多条件筛选 + 分页 + 下拉选项（全只读）。

    与 /api/runs 并存：那个是 M1 左栏的最近 N 条；本端点服务于 M3 历史视图。
    回放与对比复用 GET /api/runs/{run_id}，不新增写接口。
    """
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    since = time.time() - hours * 3600.0 if hours and hours > 0 else None
    return app.state.store.query_runs(
        status=status, session_id=session_id, error_code=error_code,
        degraded=degraded, q=q, since_ts=since, limit=limit, offset=offset)


@app.get("/api/meta")
async def meta():
    """UI 初始化所需的不含密钥的运行时快照。"""
    s: Settings = app.state.s
    return {
        "service": "koda",
        "version": "2.3.0",
        "ui_module": "M8",
        "app_env": s.app_env,
        "llm_backend": s.llm_backend,
        "model": s.model,
        "max_rounds": s.max_rounds,
        "request_timeout_s": s.request_timeout_s,
        "tool_timeout_s": s.tool_timeout_s,
        "max_concurrency": s.max_concurrency,
        "per_session_quota": s.per_session_quota,
        "daily_budget_cny": s.daily_budget_cny,
        "retry_max": s.retry_max,
        "circuit_threshold": s.circuit_threshold,
        "allowed_tools": sorted(ALLOWED_TOOLS),
        "auth_required": bool(getattr(app.state, "role_tokens", {})),
    }


def _runtime_snapshot(s: Settings) -> dict:
    """M2 面板用的配置白名单：只给可调参数，绝不输出 api_key / auth_token 真值。"""
    return {
        "max_rounds": s.max_rounds,
        "request_timeout_s": s.request_timeout_s,
        "tool_timeout_s": s.tool_timeout_s,
        "max_concurrency": s.max_concurrency,
        "per_session_quota": s.per_session_quota,
        "daily_budget_cny": s.daily_budget_cny,
        "price_in_cny_per_1k": s.price_in_cny_per_1k,
        "price_out_cny_per_1k": s.price_out_cny_per_1k,
        "circuit_threshold": s.circuit_threshold,
        "circuit_cooldown_s": s.circuit_cooldown_s,
        "retry_max": s.retry_max,
        "retry_base_ms": s.retry_base_ms,
        "max_message_chars": s.max_message_chars,
        "session_ttl_s": s.session_ttl_s,
        "auth_required": bool(getattr(app.state, "role_tokens", {})),
    }


@app.get("/api/stats")
async def stats(request: Request = None):
    """M2 运行时状态：一次拉齐熔断 / 预算 / 并发 / 限流 / 指标 / 落库统计。

    原有字段全部保留，只做增量扩展，不破坏既有调用方与 A8 验收。
    M8：内部运行数据，客户身份不可见（403 ROLE_FORBIDDEN）。
    """
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    s: Settings = app.state.s
    gate = app.state.gate
    return {
        "store": app.state.store.stats(),
        "metrics": app.state.metrics.snapshot(),
        "budget": app.state.budget.snapshot(),
        "breaker": app.state.breaker.snapshot().__dict__,
        "concurrency": {
            "in_flight": gate.in_flight,
            "peak": gate.peak,
            "limit": gate.limit,
            "acquire_timeout_s": gate.acquire_timeout_s,
        },
        "limiter": app.state.limiter.snapshot(),
        "process": {
            "uptime_s": round(time.time() - app.state.started_at, 1),
            "started_at": round(app.state.started_at, 3),
            "app_env": s.app_env,
            "llm_backend": s.llm_backend,
            "model": s.model,
        },
        "runtime": _runtime_snapshot(s),
        "ui_module": "M8",
        "ts": round(time.time(), 3),
    }


# ===== M4 故障注入演练台 =====
# 与 M1–M3 的只读视图不同：这里的写端点会临时改变运行时状态。
# 硬边界：仅 APP_ENV=dev 可用；故障类型白名单；单演练互斥；TTL 自动复位。
class DrillStartIn(BaseModel):
    kind: str = Field(description="故障类型，取值见 /api/drill/catalog")
    ttl_s: float | None = Field(default=None)
    session_id: str | None = Field(default=None)


class DrillProbeIn(BaseModel):
    message: str | None = Field(default=None)
    session_id: str | None = Field(default=None)


class DrillStopIn(BaseModel):
    drill_id: str | None = Field(default=None)


def _drill_guard():
    """非 dev 环境一律 403；放在每个写端点第一行，避免绕过。"""
    if not app.state.drill.env_allowed():
        return JSONResponse(
            {"error": "drill_disabled", "error_code": "DRILL_DISABLED",
             "app_env": app.state.s.app_env},
            status_code=403)
    return None


@app.get("/api/drill/catalog")
async def drill_catalog():
    """只读：演练白名单与安全边界（非 dev 也可查看，返回 enabled=False）。"""
    return app.state.drill.catalog()


@app.get("/api/drill/status")
async def drill_status():
    """只读：当前演练 / 倒计时 / 事件日志；顺带惰性触发 TTL 自动复位。"""
    return app.state.drill.status()


@app.post("/api/drill/start")
async def drill_start(req: DrillStartIn):
    blocked = _drill_guard()
    if blocked:
        return blocked
    try:
        return app.state.drill.start(req.kind, req.ttl_s, req.session_id)
    except DrillError as exc:
        return JSONResponse({"error": str(exc), "error_code": exc.code},
                            status_code=400)


@app.post("/api/drill/probe")
async def drill_probe(req: DrillProbeIn):
    blocked = _drill_guard()
    if blocked:
        return blocked
    try:
        return await app.state.drill.probe(req.message, req.session_id)
    except DrillError as exc:
        return JSONResponse({"error": str(exc), "error_code": exc.code},
                            status_code=400)


@app.post("/api/drill/stop")
async def drill_stop(req: DrillStopIn):
    blocked = _drill_guard()
    if blocked:
        return blocked
    try:
        return app.state.drill.stop(req.drill_id)
    except DrillError as exc:
        return JSONResponse({"error": str(exc), "error_code": exc.code},
                            status_code=400)


# ---- M5 评估回归面板 ----
# 只读端点查看验收状态 / 最新报告 / 历史与回归对比；唯一写端点 POST /api/eval/run
# 在子进程里离线跑 eval/run_acceptance.py（强制 fake backend）。
# 硬边界：仅 APP_ENV=dev；一次只能跑一个；子进程 300s 保险丝。
def _eval_guard():
    """非 dev 环境一律 403；放在写端点第一行，避免绕过。"""
    if not app.state.eval.env_allowed():
        return JSONResponse(
            {"error": "eval_disabled", "error_code": "EVAL_DISABLED",
             "app_env": app.state.s.app_env},
            status_code=403)
    return None


@app.get("/api/eval/catalog")
async def eval_catalog():
    """只读：评估面板能力与安全边界（非 dev 也可查看，返回 enabled=False）。"""
    return app.state.eval.catalog()


@app.get("/api/eval/status")
async def eval_status():
    """只读：当前运行状态 + 最近一次结果 + 历史条数 + 回归对比。"""
    return app.state.eval.status()


@app.get("/api/eval/report")
async def eval_report():
    """只读：data/acceptance.json 最新报告 + 摘要（文件由验收脚本维护）。"""
    return app.state.eval.report()


@app.get("/api/eval/history")
async def eval_history(limit: int = 10):
    """只读：最近 N 次验收落库记录（列表页不含报告全文）。"""
    return {"items": app.state.eval.history(limit)}


@app.get("/api/eval/history/{run_id}")
async def eval_history_detail(run_id: int):
    item = app.state.eval.get_history(run_id)
    if item is None:
        return JSONResponse(
            {"error": "eval run not found", "error_code": "EVAL_RUN_NOT_FOUND",
             "run_id": run_id},
            status_code=404)
    return item


@app.post("/api/eval/run")
async def eval_run():
    blocked = _eval_guard()
    if blocked:
        return blocked
    try:
        return app.state.eval.run()
    except EvalError as exc:
        code = 409 if exc.code == "EVAL_RUNNING" else 400
        return JSONResponse({"error": str(exc), "error_code": exc.code},
                            status_code=code)


# ---- M6 交付视图 ----
# 全只读交付清单：版本 / 命令 / 交付物 / 证据 / 运行时边界。
# 没有任何写端点，dev / staging / prod 都可以看。
@app.get("/api/deliver/manifest")
async def deliver_manifest():
    return app.state.deliver.manifest()


# ===== M7 知识库与长期记忆 =====
# 业务能力端点（不是演练台）：知识入库 / 检索 / 记忆管理与 /api/chat 同级，
# dev / staging / prod 均可用——这与 drill / eval 的「仅 dev」门禁是刻意不同：
# 知识与记忆是产品功能，故障注入与验收执行才是危险操作。
class KbDocIn(BaseModel):
    title: str = Field(description="文档标题，1-80 字")
    content: str = Field(description="正文，1-8000 字，空行分段自动切块")
    source: str | None = Field(default=None, description="来源标注，如 policy-2026-11")


@app.get("/api/kb")
async def kb_list():
    """知识库文档清单 + 统计（只读）。"""
    return {"items": app.state.kb.list_docs(), "stats": app.state.kb.stats()}


@app.post("/api/kb")
async def kb_add(req: KbDocIn, request: Request = None):
    # M8：知识管理是内部操作——客户身份只读（GET / 检索开放），写操作需员工及以上
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    title = req.title.strip()
    content = req.content.strip()
    if not (1 <= len(title) <= 80):
        return JSONResponse({"error": "title 须为 1-80 字",
                             "error_code": "KB_INVALID"}, status_code=400)
    if not (1 <= len(content) <= 8000):
        return JSONResponse({"error": "content 须为 1-8000 字",
                             "error_code": "KB_INVALID"}, status_code=400)
    doc_id, chunks = app.state.kb.add_doc(
        title, content, (req.source or "").strip() or None)
    return {"doc_id": doc_id, "chunks": chunks}


@app.get("/api/kb/search")
async def kb_search(q: str = "", k: int = 3):
    """检索预演：与 agent 工具 search_kb 同一 BM25 实现。"""
    q = (q or "").strip()
    if not q:
        return JSONResponse({"error": "q 不能为空",
                             "error_code": "KB_INVALID"}, status_code=400)
    k = max(1, min(int(k), 10))
    return {"query": q, "hits": app.state.kb.search(q, k)}


@app.delete("/api/kb/{doc_id}")
async def kb_delete(doc_id: int, request: Request = None):
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    if not app.state.kb.delete_doc(doc_id):
        return JSONResponse({"error": "doc not found",
                             "error_code": "KB_DOC_NOT_FOUND",
                             "doc_id": doc_id}, status_code=404)
    return {"deleted": doc_id}


@app.get("/api/memory")
async def memory_list(session_id: str = "default", limit: int = 50):
    """某会话的长期记忆（最新在前，只读）。"""
    return {"items": app.state.store.list_memories(
        session_id or "default", limit)}


@app.delete("/api/memory/{memory_id}")
async def memory_delete(memory_id: int):
    if not app.state.store.delete_memory(memory_id):
        return JSONResponse({"error": "memory not found",
                             "error_code": "MEMORY_NOT_FOUND",
                             "memory_id": memory_id}, status_code=404)
    return {"deleted": memory_id}


@app.delete("/api/memory")
async def memory_clear(session_id: str):
    session_id = (session_id or "").strip()
    if not session_id:
        return JSONResponse({"error": "session_id 不能为空",
                             "error_code": "MEMORY_INVALID"}, status_code=400)
    cleared = app.state.store.clear_memories(session_id)
    return {"cleared": cleared, "session_id": session_id}


# ===== M8 身份与权限（规模化·角色鉴权）=====
# 三身份：客户 / 员工 / 领导，Bearer 令牌映射角色（AUTH_TOKEN 兼容映射为领导）。
# 演示令牌仅 APP_ENV=dev + ENABLE_DEMO_ROLE_TOKENS=1 时生效，prod 一律忽略。
# 权限矩阵：对话与知识查询全员可用；内部运行数据（runs/history/stats）与
# 知识库写操作需员工及以上；员工目录客户不可见，员工只见基本信息，
# 领导见全部字段（联系方式等敏感字段）。
@app.get("/api/whoami")
async def whoami(request: Request):
    """当前身份自查（豁免鉴权：无令牌时如实报告开放模式）。"""
    tokens: dict = getattr(app.state, "role_tokens", {})
    header = request.headers.get("Authorization") or ""
    token = header[len("Bearer "):] if header.startswith("Bearer ") else ""
    if tokens:
        role = tokens.get(token) or "anonymous"
        authenticated = role != "anonymous"
    else:
        # 开放模式（未配置任何令牌，本地 dev 默认）：匿名即全权限
        role, authenticated = "leader", False
    return {
        "service": "koda",
        "role": role,
        "role_label": label_of(role),
        "authenticated": authenticated,
        "auth_required": bool(tokens),
        "token_preview": mask_token(token) if token else "<未携带>",
        "app_env": app.state.s.app_env,
        "ts": round(time.time(), 3),
    }


@app.get("/api/auth/identities")
async def auth_identities():
    """演示身份清单（豁免鉴权）：dev 演示模式下返回 demo_token 供 UI 一键切换。"""
    s: Settings = app.state.s
    return identities_payload(s, getattr(app.state, "role_tokens", {}))


@app.get("/api/staff")
async def staff_directory(request: Request = None):
    """员工目录：分级视图——客户 403；员工仅基本信息；领导含联系方式。"""
    denied = _require_rank(request, rank_of("staff"))
    if denied:
        return denied
    from .rbac import directory_for
    view, items = directory_for(_current_role(request))
    return {"view": view, "items": items, "count": len(items)}


@app.get("/healthz")
async def healthz():
    return {"status": "alive", "ts": round(time.time(), 3)}


@app.get("/readyz")
async def readyz():
    store_ok = app.state.store.ready()
    breaker_state = app.state.breaker.snapshot().state
    ready = store_ok and breaker_state != "open"
    payload = {"status": "ready" if ready else "not_ready",
               "store": store_ok, "breaker": breaker_state,
               "draining": app.state.gate.in_flight}
    return JSONResponse(payload, status_code=200 if ready else 503)


@app.get("/metrics")
async def metrics():
    return PlainTextResponse(app.state.metrics.render(),
                             media_type="text/plain; version=0.0.4")


@app.get("/api/service")
async def service_info():
    return {"service": "koda", "version": "2.3.0",
            "ui": "/", "docs": "/docs", "health": "/healthz", "ready": "/readyz",
            "metrics": "/metrics", "backend": app.state.s.llm_backend,
            "kb_docs": len(app.state.kb.list_docs())}


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


@app.get("/")
async def root():
    # 单文件页面全部内联；no-store 避免改完界面后浏览器仍展示旧缓存
    return FileResponse(
        _STATIC_DIR / "index.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


def main():
    import uvicorn
    os.environ.setdefault("LLM_BACKEND", "fake")
    s = Settings.from_env()
    print(f"koda 控制台 → http://{s.host}:{s.port}/")
    print(f"OpenAPI 文档        → http://{s.host}:{s.port}/docs")
    print(f"backend={s.llm_backend}  app_env={s.app_env}  ui_module=M8")
    uvicorn.run(app, host=s.host, port=s.port, log_level=s.log_level.lower(),
                timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
