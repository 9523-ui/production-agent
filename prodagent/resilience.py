"""韧性层：超时 / 重试退避 / 熔断 / 降级 / 限流 / 预算。

刻意只用标准库实现，不引入 tenacity / circuitbreaker / prometheus_client：
依赖越少，别人 clone 后越容易跑起来；面试时也更能讲清状态机本身。
"""
from __future__ import annotations

import asyncio
import random
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


class TransientError(RuntimeError):
    """可重试错误：5xx / 429 / 连接抖动。"""


class PermanentError(RuntimeError):
    """不可重试错误：401 / 400 / 参数非法。重试只会放大故障。"""


class UpstreamTimeout(TransientError):
    pass


class CircuitOpen(RuntimeError):
    pass


class Overloaded(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class RateLimited(RuntimeError):
    pass


@dataclass
class BreakerSnapshot:
    state: str
    failures: int
    opened_at: float | None
    # M2 运行时面板：把状态机的"为什么 / 还要等多久"一起暴露，UI 不必反推配置
    threshold: int = 0
    cooldown_s: float = 0.0
    cooldown_remaining_s: float = 0.0
    since_open_s: float | None = None


class CircuitBreaker:
    """三态熔断：CLOSED -> OPEN -> HALF_OPEN -> CLOSED。

    连续失败达阈值即打开；冷却期内直接拒绝（不再打上游）；
    冷却结束放一个探测请求，成功则闭合，失败则重新计时。
    """

    CLOSED, OPEN, HALF_OPEN = "closed", "open", "half_open"

    def __init__(self, threshold: int = 3, cooldown_s: float = 8.0):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self.state = self.CLOSED
        self.failures = 0
        self.opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self.state == self.OPEN:
                assert self.opened_at is not None
                if time.monotonic() - self.opened_at < self.cooldown_s:
                    return False
                self.state = self.HALF_OPEN
                return True
            return True

    def on_success(self) -> None:
        with self._lock:
            self.state = self.CLOSED
            self.failures = 0
            self.opened_at = None

    def on_failure(self) -> None:
        with self._lock:
            self.failures += 1
            if self.state == self.HALF_OPEN or self.failures >= self.threshold:
                self.state = self.OPEN
                self.opened_at = time.monotonic()

    def snapshot(self) -> BreakerSnapshot:
        with self._lock:
            remaining = 0.0
            since_open = None
            if self.state == self.OPEN and self.opened_at is not None:
                since_open = round(time.monotonic() - self.opened_at, 2)
                remaining = round(max(0.0, self.cooldown_s - since_open), 2)
            return BreakerSnapshot(
                state=self.state, failures=self.failures, opened_at=self.opened_at,
                threshold=self.threshold, cooldown_s=self.cooldown_s,
                cooldown_remaining_s=remaining, since_open_s=since_open)


async def call_with_resilience(fn, *, timeout_s: float, retry_max: int,
                               base_ms: int, breaker: CircuitBreaker):
    """带超时 + 指数退避抖动 + 熔断的单次上游调用。

    - 只对 TransientError 重试；PermanentError 立刻抛出，避免重试风暴。
    - 超时归类为 TransientError，但受总轮次上限约束。
    - 熔断打开时不再打上游，直接 CircuitOpen，由调用方走降级。
    """
    last: Exception | None = None
    for attempt in range(retry_max + 1):
        if not breaker.allow():
            raise CircuitOpen("上游熔断已打开，冷却中")
        try:
            result = await asyncio.wait_for(fn(), timeout=timeout_s)
        except asyncio.TimeoutError as exc:
            last = UpstreamTimeout(f"上游超时 >{timeout_s}s")
            breaker.on_failure()
        except TransientError as exc:
            last = exc
            breaker.on_failure()
        except PermanentError:
            raise
        except Exception as exc:  # 未知异常不盲目重试
            raise PermanentError(f"{type(exc).__name__}: {exc}") from exc
        else:
            breaker.on_success()
            return result
        if attempt < retry_max:
            delay = (base_ms / 1000.0) * (2 ** attempt) * (0.8 + 0.4 * random.random())
            await asyncio.sleep(delay)
    raise last if last else TransientError("重试耗尽")


class ConcurrencyGate:
    """全局并发上限；等待 acquire 超时即视为过载，不让请求无限堆积。"""

    def __init__(self, limit: int, acquire_timeout_s: float = 2.0):
        self._sem = asyncio.Semaphore(limit)
        self.limit = limit
        self.acquire_timeout_s = acquire_timeout_s
        self.in_flight = 0
        self.peak = 0

    async def __aenter__(self):
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=self.acquire_timeout_s)
        except asyncio.TimeoutError as exc:
            raise Overloaded(f"并发已满 (limit={self.limit})") from exc
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        return self

    async def __aexit__(self, *exc_info):
        self.in_flight -= 1
        self._sem.release()
        return False


class SlidingWindowLimiter:
    """按 session 的滑动窗口限流，防止单会话刷爆上游。"""

    def __init__(self, max_calls: int, window_s: float = 60.0):
        self.max_calls = max_calls
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.max_calls:
                raise RateLimited(f"会话 {key} 超过 {self.max_calls} 次/{self.window_s:.0f}s")
            q.append(now)

    def count(self, key: str) -> int:
        with self._lock:
            return len(self._hits[key])

    def snapshot(self, top_n: int = 8) -> dict:
        """M2 运行时面板：滑动窗口的当前占用（先清理过期，再统计）。"""
        now = time.monotonic()
        with self._lock:
            active: dict[str, int] = {}
            for key, q in list(self._hits.items()):
                while q and now - q[0] > self.window_s:
                    q.popleft()
                if q:
                    active[key] = len(q)
                else:
                    self._hits.pop(key, None)
            ranked = sorted(active.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n]
            return {
                "max_calls": self.max_calls,
                "window_s": self.window_s,
                "active_sessions": len(active),
                "total_hits": sum(active.values()),
                "by_session": [{"session_id": k, "hits": v} for k, v in ranked],
            }

    def reset(self, key: str | None = None) -> int:
        """M4 演练复位：清空指定会话（或全部）的窗口，返回含未过期命中的条数。

        滑动窗口本身无法"撤销"命中，因此演练控制器需要一个显式复位入口。
        该方法只被 dev 环境的故障注入调用，生产请求路径不会触碰。
        注意 defaultdict 的 count() 访问会留下空 deque，这里只统计非空窗口。
        """
        with self._lock:
            if key is None:
                n = sum(1 for q in self._hits.values() if q)
                self._hits.clear()
                return n
            hit = bool(self._hits.get(key))
            self._hits.pop(key, None)
            return 1 if hit else 0


class BudgetLedger:
    """按自然日累计成本，超限拒绝新请求（不中断已在途请求）。"""

    def __init__(self, daily_limit_cny: float):
        self.daily_limit = daily_limit_cny
        self.spent = 0.0
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            if self.spent >= self.daily_limit:
                raise BudgetExceeded(
                    f"日预算已用尽 {self.spent:.4f}/{self.daily_limit:.4f} CNY")

    def add(self, cny: float) -> float:
        with self._lock:
            self.spent += cny
            return self.spent

    def snapshot(self) -> dict:
        with self._lock:
            used = self.spent / self.daily_limit if self.daily_limit > 0 else 0.0
            return {
                "spent_cny": round(self.spent, 6),
                "limit_cny": self.daily_limit,
                "remaining_cny": round(max(0.0, self.daily_limit - self.spent), 6),
                "used_pct": round(min(100.0, used * 100.0), 2),
                "exceeded": self.spent >= self.daily_limit,
            }

    def reset(self, value: float = 0.0) -> float:
        """M4 演练复位：把当日累计成本恢复到给定值（默认清零），返回恢复后的值。"""
        with self._lock:
            self.spent = max(0.0, float(value))
            return self.spent
