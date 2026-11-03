"""轻量指标：计数器 + 延迟分桶，输出 Prometheus 文本格式。

不引入 prometheus_client：指标本身很简单，标准库足够，且 /metrics 端点
在离线环境也能直接 curl 验证。
"""
from __future__ import annotations

import threading


class Metrics:
    def __init__(self, max_samples: int = 2000):
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._latencies: dict[str, list[float]] = {}
        self.max_samples = max_samples

    def inc(self, name: str, value: float = 1.0) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0.0) + value

    def observe(self, name: str, ms: float) -> None:
        with self._lock:
            bucket = self._latencies.setdefault(name, [])
            bucket.append(round(ms, 2))
            if len(bucket) > self.max_samples:
                del bucket[: len(bucket) - self.max_samples]

    def snapshot(self) -> dict:
        with self._lock:
            out = {"counters": dict(self._counters), "latency": {}}
            for name, vals in self._latencies.items():
                if vals:
                    s = sorted(vals)
                    out["latency"][name] = {
                        "count": len(s),
                        "p50_ms": s[len(s) // 2],
                        "p95_ms": s[min(len(s) - 1, int(len(s) * 0.95))],
                        "max_ms": s[-1],
                    }
            return out

    def render(self) -> str:
        with self._lock:
            lines = ["# HELP prodagent_requests_total Total requests by outcome.",
                     "# TYPE prodagent_requests_total counter"]
            for name, value in sorted(self._counters.items()):
                safe = name.replace("-", "_").replace(".", "_")
                lines.append(f"prodagent_requests_total{{outcome=\"{safe}\"}} {value:g}")
            lines.append("# HELP prodagent_latency_ms Request latency in ms.")
            lines.append("# TYPE prodagent_latency_ms summary")
            for name, vals in sorted(self._latencies.items()):
                if not vals:
                    continue
                s = sorted(vals)
                safe = name.replace("-", "_")
                lines.append(f"prodagent_latency_ms{{quantile=\"0.5\",phase=\"{safe}\"}} {s[len(s)//2]:g}")
                lines.append(f"prodagent_latency_ms{{quantile=\"0.95\",phase=\"{safe}\"}} {s[min(len(s)-1,int(len(s)*0.95))]:g}")
                lines.append(f"prodagent_latency_ms_sum{{phase=\"{safe}\"}} {sum(s):g}")
                lines.append(f"prodagent_latency_ms_count{{phase=\"{safe}\"}} {len(s)}")
            return "\n".join(lines) + "\n"
