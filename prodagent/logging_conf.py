"""结构化 JSON 日志 + trace_id 贯穿 + 日志侧密钥打码。

生产级要点：
- 每行一条 JSON，可被 ELK / Loki 直接采集，不靠正则解析人话日志。
- trace_id 用 contextvars，跨 async 任务自动传递（不用手动层层传参）。
- formatter 兜底打码：即便某个开发者不小心把 Key 打进日志，也只会落 `sk-***`。
"""
from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid

_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="-")

_SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{12,}|ghp_[A-Za-z0-9]{16,}|Bearer\s+[A-Za-z0-9._-]{20,})")


def new_trace_id() -> str:
    return uuid.uuid4().hex[:16]


def set_trace_id(value: str) -> None:
    _trace_id.set(value)


def get_trace_id() -> str:
    return _trace_id.get()


def scrub(text: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group().split("-")[0] + "-***", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(time.time(), 3),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": _trace_id.get(),
            "msg": scrub(record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key.startswith("x_"):
                payload[key[2:]] = value
        if record.exc_info:
            payload["exc"] = scrub(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure(level: str = "INFO") -> logging.Logger:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    return logging.getLogger("prodagent")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger("prodagent." + name)
