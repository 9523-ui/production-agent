"""LLM 适配器：离线确定性 FakeLLM + 可选 DeepSeek 实现。

生产级要点：
- 上游被抽象成接口，评测/灾备/本地开发可以完全脱离云 API 跑。
- 异常分类在这里完成（可重试 vs 不可重试），韧性层不猜错误类型。
- Key 只存在于进程内存，绝不写日志、响应或数据库。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .resilience import PermanentError, TransientError


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class LLMResult:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    model: str = "fake"

    @property
    def is_final(self) -> bool:
        return not self.tool_calls


class BaseLLM:
    name = "base"

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        raise NotImplementedError


class FakeLLM(BaseLLM):
    """确定性离线模型，用脚本队列驱动验收：可注入失败、超时、任意回复。

    script 元素支持：
      - Exception 实例 / 类：直接抛出
      - ("sleep", 秒数)：触发上游超时
      - LLMResult：直接返回
      - callable(messages, tools) -> LLMResult（可为 async）
    """
    name = "fake"

    def __init__(self, script: list | None = None):
        self.script: list = list(script or [])
        self.calls = 0

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        self.calls += 1
        if self.script:
            item = self.script.pop(0)
            if isinstance(item, tuple) and item and item[0] == "sleep":
                await asyncio.sleep(item[1])
                return LLMResult(content="不该走到这里")
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                out = item(messages, tools)
                if asyncio.iscoroutine(out):
                    out = await out
                return out
            if isinstance(item, LLMResult):
                return item
            raise TypeError(f"不支持的脚本项: {type(item)}")
        return self._default(messages)

    def _default(self, messages: list[dict]) -> LLMResult:
        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = str(m.get("content", ""))
                break
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        tin = sum(len(str(m.get("content", ""))) for m in messages) // 2

        if not has_tool_result:
            # M7 记忆/知识路由前置：记忆意图词更具体，优先于 FAQ 词防误路由
            # （例：「记住我上次退货的订单」是记忆诉求，不该走 search_faq）。
            if any(k in user_text for k in ("记住", "记一下", "偏好")):
                return LLMResult(tool_calls=[ToolCall(
                    "call_1", "memory_save",
                    {"content": user_text[:80],
                     "kind": "preference" if "偏好" in user_text else "fact"})],
                    tokens_in=tin, tokens_out=24, model="fake")
            if any(k in user_text for k in ("上次", "还记得", "记得我")):
                return LLMResult(tool_calls=[ToolCall(
                    "call_1", "memory_recall", {"query": user_text})],
                    tokens_in=tin, tokens_out=24, model="fake")
            if any(k in user_text for k in ("发票", "优惠", "会员", "积分")):
                return LLMResult(tool_calls=[ToolCall(
                    "call_1", "search_kb", {"query": user_text})],
                    tokens_in=tin, tokens_out=24, model="fake")
            if "订单" in user_text or "物流" in user_text or "CS2026" in user_text:
                return LLMResult(tool_calls=[ToolCall("call_1", "lookup_order",
                                                      {"order_id": "CS20261101007"})],
                                 tokens_in=tin, tokens_out=24, model="fake")
            if any(k in user_text for k in ("退货", "政策", "运费", "退款", "保修")):
                return LLMResult(tool_calls=[ToolCall("call_1", "search_faq",
                                                      {"query": user_text})],
                                 tokens_in=tin, tokens_out=24, model="fake")
            if any(k in user_text for k in ("补发", "换货", "工单", "破损")):
                return LLMResult(tool_calls=[ToolCall("call_1", "create_ticket",
                                                      {"order_id": "CS20261101007",
                                                       "reason": user_text[:40]})],
                                 tokens_in=tin, tokens_out=24, model="fake")
        return LLMResult(content="已为您核实，问题已记录并按标准售后流程处理。",
                         tokens_in=tin, tokens_out=32, model="fake")


class DeepSeekLLM(BaseLLM):
    name = "deepseek"

    def __init__(self, *, api_key: str, base_url: str, model: str, timeout_s: float):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout_s,
                                  max_retries=0)  # 重试交给韧性层统一管理
        self.model = model

    async def complete(self, messages: list[dict], tools: list[dict]) -> LLMResult:
        from openai import APIStatusError, APIConnectionError, APITimeoutError
        try:
            resp = await self.client.chat.completions.create(
                model=self.model, messages=messages, tools=tools or None,
                temperature=0.1,
            )
        except APIStatusError as exc:
            if exc.status_code in (401, 403, 400):
                raise PermanentError(f"上游拒绝 {exc.status_code}") from exc
            raise TransientError(f"上游 {exc.status_code}") from exc
        except (APIConnectionError, APITimeoutError) as exc:
            raise TransientError("上游连接/超时") from exc

        msg = resp.choices[0].message
        calls = []
        for tc in (msg.tool_calls or []):
            import json
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            calls.append(ToolCall(tc.id, tc.function.name, args))
        usage = getattr(resp, "usage", None)
        return LLMResult(
            content=msg.content or "",
            tool_calls=calls,
            tokens_in=int(getattr(usage, "prompt_tokens", 0) or 0),
            tokens_out=int(getattr(usage, "completion_tokens", 0) or 0),
            model=self.model,
        )
