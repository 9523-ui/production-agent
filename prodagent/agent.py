"""koda Agent 编排：把 ReAct 内核包进接入层 / 韧性层 / 底座层。

与演示级 server.py 的关键差别：
1. 每次请求都有 run_id + trace_id，全过程落库（重启不丢）
2. 上游调用统一经过超时 / 重试 / 熔断，失败走降级而不是把异常抛给用户
3. 幂等键由数据库 UNIQUE 约束兜底，重复请求不会重复建单
4. 同会话串行锁 + 全局并发闸 + 单会话限流 + 日预算
5. 工具白名单在工具层强制校验，输出统一脱敏
6. 内部错误只记日志与 error_code，对外永远给固定文案
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field

from .guard import (DEGRADED_REPLY, GuardError, check_tool, scrub_output,
                    validate_input)
from .kb import KnowledgeBase, memory_score
from .logging_conf import get_logger, new_trace_id, set_trace_id
from .llm import BaseLLM, LLMResult
from .metrics import Metrics
from .resilience import (BudgetExceeded, BudgetLedger, CircuitBreaker,
                         CircuitOpen, ConcurrencyGate, Overloaded,
                         PermanentError, RateLimited, SlidingWindowLimiter,
                         TransientError, UpstreamTimeout,
                         call_with_resilience)
from .settings import Settings
from .store import Store

SYSTEM_PROMPT = (
    "你是电商客服 Agent。政策、时效、金额必须来自 search_faq 或 search_kb 的返回内容，"
    "两者都未命中时不要编造，改用 handoff_to_human。涉及具体订单先索要订单号再 lookup_order。"
    "用户表达的稳定偏好与关键事实用 memory_save 记住；回答个人化问题前先用 memory_recall 核实。"
    "不向用户暴露工具名、知识库编号等内部信息。回复控制在 150 字以内。"
)

TOOLS_SCHEMA = [
    {"type": "function", "function": {
        "name": "search_faq", "description": "检索售后政策知识库",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "lookup_order", "description": "按订单号查询订单与物流状态",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}},
                       "required": ["order_id"]}}},
    {"type": "function", "function": {
        "name": "create_ticket", "description": "为标准售后诉求创建工单",
        "parameters": {"type": "object", "properties": {
            "order_id": {"type": "string"}, "reason": {"type": "string"}},
            "required": ["order_id", "reason"]}}},
    {"type": "function", "function": {
        "name": "handoff_to_human", "description": "转人工客服",
        "parameters": {"type": "object", "properties": {"reason": {"type": "string"}},
                       "required": ["reason"]}}},
    # M7 知识与长期记忆（本地 BM25 / SQLite，接口与真模型一致）
    {"type": "function", "function": {
        "name": "search_kb", "description": "检索知识库文档（发票、会员、优惠等业务知识）",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "memory_save", "description": "记住该客户的稳定偏好或关键事实（长期记忆）",
        "parameters": {"type": "object", "properties": {
            "content": {"type": "string"},
            "kind": {"type": "string", "enum": ["fact", "preference"]}},
            "required": ["content"]}}},
    {"type": "function", "function": {
        "name": "memory_recall", "description": "按查询词回顾该客户的长期记忆",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                       "required": ["query"]}}},
]

_FAQ = [
    ("退货", "七天无理由退货：签收后 7 天内可申请，商品需保持完好，运费由买家承担。"),
    ("运费", "满 99 元包邮，未满 99 元运费 8 元；偏远地区另计。"),
    ("退款", "退款在仓库签收退货后 24 小时内发起，原路退回，到账时间以支付渠道为准。"),
    ("保修", "整机保修 12 个月，人为损坏不在保修范围内。"),
]

_DEMO_ORDERS = {
    "CS20261101007": "订单 CS20261101007：已发货，物流显示运输中，预计 11 月 5 日送达。",
    "CS20261101001": "订单 CS20261101001：已签收（11 月 1 日），状态正常。",
}


def _messages_chars(messages: list[dict]) -> int:
    """上下文字符预算的粗度量：content + tool_calls 参数串。"""
    total = 0
    for m in messages:
        total += len(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            total += len(str(tc.get("function", {}).get("arguments") or ""))
    return total


@dataclass
class ChatRequest:
    message: str
    session_id: str = "default"
    idempotency_key: str | None = None
    trace_id: str | None = None      # 由 HTTP 中间件注入；内部调用留空则每次新建


@dataclass
class ChatResult:
    reply: str
    run_id: str
    trace_id: str
    status: str
    degraded: bool
    cached: bool
    rounds: int
    cost_cny: float
    latency_ms: int
    error_code: str | None = None
    trace: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class ProductionAgent:
    # 每会话保留的最近 user 轮数：轮数裁剪的第一道闸（字符预算是第二道）
    _KEEP_USER_TURNS = 3
    # M7 长期记忆注入：标记前缀（刷新时按它识别旧注入块）、块字符上限
    _MEMORY_MARK = "【客户记忆】"
    _MAX_MEMORY_BLOCK_CHARS = 500

    def __init__(self, *, settings: Settings, llm: BaseLLM, store: Store,
                 metrics: Metrics, breaker: CircuitBreaker, gate: ConcurrencyGate,
                 limiter: SlidingWindowLimiter, budget: BudgetLedger):
        self.s = settings
        self.llm = llm
        self.store = store
        self.metrics = metrics
        self.breaker = breaker
        self.gate = gate
        self.limiter = limiter
        self.budget = budget
        self.kb = KnowledgeBase(store)   # M7：本地知识库（app.state.kb 复用同一实例）
        self.log = get_logger("agent")
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._history: dict[str, list[dict]] = {}
        self._session_touch: dict[str, float] = {}

    def _lock_for(self, sid: str) -> asyncio.Lock:
        return self._session_locks.setdefault(sid, asyncio.Lock())

    def reset_session(self, sid: str) -> None:
        """只清短期上下文；长期记忆与审计记录不删除。"""
        self._history.pop(sid, None)
        self._session_touch.pop(sid, None)
        lock = self._session_locks.get(sid)
        if lock is not None and not lock.locked():
            # 锁还被在途请求持有时先不收（下一轮再清），避免中途换锁破坏串行
            self._session_locks.pop(sid, None)

    def evict_expired(self, ttl_s: float) -> int:
        """内存会话上下文与锁的 TTL 淘汰（janitor 周期调用）。

        store.purge_expired 只清 sessions 表；_history / _session_locks 是
        进程内 dict，不淘汰就是无上限的内存泄漏。锁被持有的会话本轮跳过。
        """
        cutoff = time.time() - ttl_s
        evicted = 0
        for sid in list(self._session_touch):
            if self._session_touch[sid] > cutoff:
                continue
            lock = self._session_locks.get(sid)
            if lock is not None and lock.locked():
                continue
            self._history.pop(sid, None)
            self._session_touch.pop(sid, None)
            self._session_locks.pop(sid, None)
            evicted += 1
        return evicted

    def _trim(self, messages: list[dict]) -> list[dict]:
        """入史前三步收敛：补配对 → 按轮数裁剪 → 按字符预算裁剪。

        1. 补配对：异常中断可能留下 assistant(tool_calls) 没等到全部 tool
           结果——直接存回会让下一轮被真实上游 400 拒绝并误判 PERMANENT。
        2. keep_user：按 user 边界裁剪，绝不切断配对（原逻辑保留）。
        3. max_context_chars：字符预算兜底（token 的粗代理），从最老的
           完整 user 轮开始丢，至少保留最近 1 轮。
        """
        msgs = self._sanitize_pairs(messages)
        system = [msgs[0]] if msgs and msgs[0].get("role") == "system" else []
        body = msgs[len(system):]
        idxs = [i for i, m in enumerate(body) if m.get("role") == "user"]
        if len(idxs) > self._KEEP_USER_TURNS:
            body = body[idxs[-self._KEEP_USER_TURNS]:]
        while len(body) > 1:
            users = [i for i, m in enumerate(body) if m.get("role") == "user"]
            if len(users) <= 1:
                break
            if _messages_chars(system + body) <= self.s.max_context_chars:
                break
            body = body[users[1]:]
        return system + body

    @staticmethod
    def _sanitize_pairs(messages: list[dict]) -> list[dict]:
        """为中断遗留的 assistant.tool_calls 补配对 tool 占位消息。

        OpenAI 协议要求每个 tool_call_id 都有对应 role=tool 消息，否则整个
        messages 会被上游 400 拒绝；FakeLLM 不校验格式，此缺陷离线不可见。
        """
        answered = {m.get("tool_call_id") for m in messages
                    if m.get("role") == "tool" and m.get("tool_call_id")}
        out: list[dict] = []
        for m in messages:
            out.append(m)
            if m.get("role") == "assistant" and m.get("tool_calls"):
                for tc in m["tool_calls"]:
                    tc_id = tc.get("id")
                    if tc_id and tc_id not in answered:
                        out.append({"role": "tool", "tool_call_id": tc_id,
                                    "content": "[工具执行中断，本轮无结果]"})
                        answered.add(tc_id)
        return out

    def _tool_content(self, out: str) -> str:
        """工具结果进对话历史的第三道截断（trace [:300]、DB [2000] 之外）。

        超长结果（如真实知识库命中/外部接口返回）不截断会撑爆上下文预算，
        且历史里留一次全文，后续每轮都要重复付出成本。
        """
        limit = self.s.max_tool_result_chars
        if len(out) <= limit:
            return out
        return out[:limit] + f"…[结果过长已截断，原文 {len(out)} 字符]"

    def _memory_block(self, sid: str) -> str | None:
        """长期记忆 → system 注入块（最新在前，最多 5 条、500 字）。"""
        rows = self.store.list_memories(sid, limit=5)
        if not rows:
            return None
        text = self._MEMORY_MARK + "以下是该客户的长期记忆（最新在前），回复时可参考：\n" + \
            "\n".join(f"- {r['content']}" for r in rows)
        if len(text) > self._MAX_MEMORY_BLOCK_CHARS:
            text = text[:self._MAX_MEMORY_BLOCK_CHARS] + "…"
        return text

    def _refresh_memory_block(self, messages: list[dict], sid: str) -> None:
        """每轮重写注入块：先移除旧块（前缀匹配），再按当前记忆插入 index 1。

        记忆在上一轮之后可能被新增/删除/清空，注入必须跟着变——
        静态注入会让删除记忆后模型仍「记得」，这正是可观测性反例。
        """
        for i in range(len(messages) - 1, 0, -1):
            m = messages[i]
            if m.get("role") == "system" and \
                    str(m.get("content", "")).startswith(self._MEMORY_MARK):
                messages.pop(i)
        block = self._memory_block(sid)
        if block:
            messages.insert(1, {"role": "system", "content": block})

    async def _execute_tool(self, name: str, args: dict, run_id: str, sid: str) -> str:
        if name == "search_faq":
            q = str(args.get("query", ""))
            for key, answer in _FAQ:
                if key in q:
                    return answer
            return "[未命中] 知识库中没有该问题的答案"
        if name == "lookup_order":
            oid = str(args.get("order_id", ""))
            return _DEMO_ORDERS.get(oid, f"未查询到订单 {oid}")
        if name == "create_ticket":
            oid = str(args.get("order_id", ""))
            reason = str(args.get("reason", ""))[:200]
            if oid not in _DEMO_ORDERS:
                return f"建单失败：订单 {oid} 不存在或尚未核实"
            tid = self.store.create_ticket(run_id=run_id, session_id=sid,
                                           order_id=oid, reason=reason)
            return f"工单已创建，工单号 {tid}，专员将在 24 小时内跟进"
        if name == "handoff_to_human":
            return f"已转人工，原因：{str(args.get('reason', ''))[:100]}"
        # ---- M7 知识与长期记忆 ----
        if name == "search_kb":
            hits = self.kb.search(str(args.get("query", "")))
            if not hits:
                return "[未命中] 知识库暂无相关内容，请如实告知并考虑转人工"
            return "\n".join(
                f"【知识命中 {i}】{h['title']} · 相关度 {h['score']}：{h['chunk']}"
                for i, h in enumerate(hits, 1))
        if name == "memory_save":
            content = str(args.get("content", "")).strip()[:300]
            if not content:
                return "记忆内容为空，未保存"
            kind = args.get("kind")
            if kind not in ("fact", "preference"):
                kind = "fact"
            mid = self.store.add_memory(session_id=sid, content=content, kind=kind)
            return f"已记住（#{mid}）：{content}"
        if name == "memory_recall":
            query = str(args.get("query", ""))
            scored = []
            for row in self.store.memories_all_for(sid):
                score = memory_score(query, row["content"])
                if score > 0:
                    scored.append((score, row))
            if not scored:
                return "[无记忆] 该会话暂无相关长期记忆"
            scored.sort(key=lambda x: (-x[0], x[1]["id"]))
            top = scored[:3]
            self.store.bump_memory_hits([row["id"] for _, row in top])
            return "\n".join(f"【记忆 #{row['id']}】{row['content']}"
                             for _, row in top)
        return f"[工具未实现] {name}"

    def _finish(self, *, run_id: str, sid: str, trace_id: str, started: float,
                reply: str, status: str, degraded: bool, error_code: str | None,
                rounds: int, tin: int, tout: int, trace: list[dict],
                cached: bool = False) -> ChatResult:
        reply, redacted = scrub_output(reply)
        cost = (tin / 1000.0) * self.s.price_in_cny_per_1k + \
               (tout / 1000.0) * self.s.price_out_cny_per_1k
        if cost:
            self.budget.add(cost)
        latency = round((time.perf_counter() - started) * 1000)
        if not cached:
            self.store.finish_run(run_id, reply=reply, status=status,
                                  error_code=error_code, rounds=rounds,
                                  tokens_in=tin, tokens_out=tout,
                                  cost_cny=cost, latency_ms=latency,
                                  degraded=degraded)
        self.metrics.inc("status_" + status)
        self.metrics.inc("output_redacted", redacted)
        self.metrics.observe("chat", latency)
        self.log.info("chat_done", extra={
            "x_run_id": run_id, "x_session_id": sid, "x_status": status,
            "x_degraded": degraded, "x_rounds": rounds, "x_latency_ms": latency,
            "x_cost_cny": round(cost, 6), "x_cached": cached,
            "x_error_code": error_code,
        })
        return ChatResult(reply=reply, run_id=run_id, trace_id=trace_id,
                          status=status, degraded=degraded, cached=cached,
                          rounds=rounds, cost_cny=round(cost, 6),
                          latency_ms=latency, error_code=error_code, trace=trace)

    async def chat(self, req: ChatRequest) -> ChatResult:
        started = time.perf_counter()
        # HTTP 链路复用中间件注入的 ID；CLI / 评测 / 内部调用每次新建独立 ID，
        # 避免 contextvar 被复用导致多次请求在日志里无法区分。
        tid = req.trace_id or new_trace_id()
        set_trace_id(tid)
        sid = req.session_id or "default"
        trace: list[dict] = []

        # ① 接入层：输入校验（不合法请求不占上游资源）
        try:
            text = validate_input(req.message, self.s.max_message_chars)
        except GuardError as exc:
            self.metrics.inc("guard_rejected_" + exc.code)
            self.log.warning("guard_rejected", extra={"x_code": exc.code})
            run_id, _, _ = self.store.create_run(
                session_id=sid, trace_id=tid, request_text=str(req.message)[:200],
                model=self.llm.name, backend=self.llm.name,
                idempotency_key=req.idempotency_key)
            return self._finish(run_id=run_id, sid=sid, trace_id=tid, started=started,
                                reply=f"请求被拒绝：{exc}", status="rejected",
                                degraded=False, error_code=exc.code,
                                rounds=0, tin=0, tout=0, trace=trace)

        # ② 幂等：重复 key 直接返回历史结果，不重复执行工具
        run_id, is_new, existing = self.store.create_run(
            session_id=sid, trace_id=tid, request_text=text,
            model=self.s.model, backend=self.llm.name,
            idempotency_key=req.idempotency_key)
        if not is_new and existing:
            self.metrics.inc("idempotent_hit")
            return self._finish(
                run_id=run_id, sid=sid, trace_id=existing["trace_id"],
                started=started, reply=existing["reply_text"] or DEGRADED_REPLY,
                status=existing["status"], degraded=bool(existing["degraded"]),
                error_code=existing["error_code"], rounds=existing["rounds"] or 0,
                tin=0, tout=0, trace=[], cached=True)

        self.store.touch_session(sid)
        self._session_touch[sid] = time.time()

        # ③ 资源治理：限流 / 预算 / 并发闸
        try:
            self.limiter.check(sid)
            self.budget.check()
        except (RateLimited, BudgetExceeded) as exc:
            self.metrics.inc("throttled_" + type(exc).__name__)
            return self._finish(run_id=run_id, sid=sid, trace_id=tid, started=started,
                                reply="当前请求过于频繁或服务预算已用尽，请稍后再试。",
                                status="rejected", degraded=False,
                                error_code=type(exc).__name__,
                                rounds=0, tin=0, tout=0, trace=trace)
        try:
            async with self.gate:
                async with self._lock_for(sid):
                    return await self._loop(run_id, sid, tid, started, text, trace)
        except Overloaded as exc:
            self.metrics.inc("throttled_Overloaded")
            return self._finish(run_id=run_id, sid=sid, trace_id=tid, started=started,
                                reply=DEGRADED_REPLY, status="degraded", degraded=True,
                                error_code="OVERLOADED", rounds=0, tin=0, tout=0,
                                trace=trace)

    async def _loop(self, run_id: str, sid: str, tid: str, started: float,
                    text: str, trace: list[dict]) -> ChatResult:
        messages = self._history.setdefault(
            sid, [{"role": "system", "content": SYSTEM_PROMPT}])
        self._refresh_memory_block(messages, sid)   # M7：长期记忆每轮刷新注入
        messages.append({"role": "user", "content": text})
        tin = tout = 0
        rounds = 0
        status, degraded, error_code = "ok", False, None
        reply = ""

        try:
            finished = False
            for rnd in range(1, self.s.max_rounds + 1):
                rounds = rnd

                async def _call() -> LLMResult:
                    return await self.llm.complete(messages, TOOLS_SCHEMA)

                r = await call_with_resilience(
                    _call, timeout_s=self.s.request_timeout_s,
                    retry_max=self.s.retry_max, base_ms=self.s.retry_base_ms,
                    breaker=self.breaker)
                tin += r.tokens_in
                tout += r.tokens_out

                if r.is_final:
                    reply = r.content or "已为您处理。"
                    finished = True
                    break

                messages.append({
                    "role": "assistant", "content": r.content or "",
                    "tool_calls": [{"id": tc.id, "type": "function",
                                    "function": {"name": tc.name,
                                                 "arguments": json.dumps(
                                                     tc.args, ensure_ascii=False)}}
                                   for tc in r.tool_calls],
                })
                for seq, tc in enumerate(r.tool_calls, len(trace) + 1):
                    t0 = time.perf_counter()
                    try:
                        check_tool(tc.name)
                        out = await asyncio.wait_for(
                            self._execute_tool(tc.name, tc.args, run_id, sid),
                            timeout=self.s.tool_timeout_s)
                        ok = True
                    except GuardError as exc:
                        out, ok = f"[工具被拒绝] {exc}", False
                        self.metrics.inc("tool_blocked")
                    except asyncio.TimeoutError:
                        out, ok = "[工具超时]", False
                        self.metrics.inc("tool_timeout")
                    ms = round((time.perf_counter() - t0) * 1000)
                    trace.append({"tool": tc.name, "args": tc.args,
                                  "result": out[:300], "ok": ok, "ms": ms})
                    self.store.add_step(run_id, seq, tc.name, tc.args, out, ok, ms)
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": self._tool_content(out)})

            if not finished:
                reply, status = DEGRADED_REPLY, "degraded"
                degraded, error_code = True, "MAX_ROUNDS"

        except (CircuitOpen, UpstreamTimeout, TransientError) as exc:
            reply, status = DEGRADED_REPLY, "degraded"
            degraded, error_code = True, type(exc).__name__
            self.log.warning("upstream_degraded", extra={"x_error": type(exc).__name__})
        except PermanentError:
            reply, status = "上游配置异常，请联系管理员。", "error"
            degraded, error_code = True, "PERMANENT"
        except Exception as exc:  # 兜底：绝不把内部异常裸给调用方
            reply, status = "内部错误，已记录日志，请稍后重试。", "error"
            degraded, error_code = True, "INTERNAL"
            self.log.error("unhandled", extra={"x_error": f"{type(exc).__name__}: {exc}"})
        finally:
            self._history[sid] = self._trim(messages)

        return self._finish(run_id=run_id, sid=sid, trace_id=tid, started=started,
                            reply=reply, status=status, degraded=degraded,
                            error_code=error_code, rounds=rounds,
                            tin=tin, tout=tout, trace=trace)
