"""配置与密钥：全部来自环境变量，启动时 fail-fast，日志只输出脱敏快照。

生产级要点：
- 代码里没有任何 Key；Key 不进日志、不进响应、不进数据库。
- prod 环境强制要求 AUTH_TOKEN，拒绝裸奔启动。
- 所有可调参数都是配置，不是散落在代码里的魔法数字。
"""
from __future__ import annotations

import os
from dataclasses import asdict, dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    app_env: str = "dev"                  # dev | staging | prod
    llm_backend: str = "fake"            # fake（离线确定性） | deepseek
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    auth_token: str = ""                 # 调用方 Bearer Token（映射为 leader 角色）
    auth_token_staff: str = ""           # M8：员工角色令牌
    auth_token_customer: str = ""        # M8：客户角色令牌
    enable_demo_role_tokens: bool = False  # M8：dev 专用演示身份令牌（prod 一律忽略）
    host: str = "127.0.0.1"
    port: int = 8010

    request_timeout_s: float = 20.0
    tool_timeout_s: float = 5.0
    max_rounds: int = 5
    max_concurrency: int = 8
    per_session_quota: int = 30
    session_ttl_s: int = 3600
    daily_budget_cny: float = 5.0
    max_message_chars: int = 2000
    max_context_chars: int = 24000        # 会话历史字符预算（token 的粗代理）
    max_tool_result_chars: int = 2000     # 工具结果进对话历史的截断长度

    circuit_threshold: int = 3
    circuit_cooldown_s: float = 8.0
    retry_max: int = 2
    retry_base_ms: int = 120

    price_in_cny_per_1k: float = 0.002
    price_out_cny_per_1k: float = 0.008
    db_path: str = "data/prodagent.db"
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, env: dict | None = None) -> "Settings":
        e = os.environ if env is None else env

        def s(k: str, d: str) -> str:
            return e.get(k, d)

        def b(k: str, d: bool) -> bool:
            raw = e.get(k)
            if raw is None:
                return d
            return str(raw).strip().lower() in ("1", "true", "yes", "on")

        def i(k: str, d: int) -> int:
            try:
                return int(e.get(k, str(d)))
            except ValueError as exc:
                raise ConfigError(f"{k} 必须是整数: {e.get(k)}") from exc

        def f(k: str, d: float) -> float:
            try:
                return float(e.get(k, str(d)))
            except ValueError as exc:
                raise ConfigError(f"{k} 必须是数字: {e.get(k)}") from exc

        obj = cls(
            app_env=s("APP_ENV", "dev"),
            llm_backend=s("LLM_BACKEND", "fake"),
            model=s("LLM_MODEL", "deepseek-chat"),
            base_url=s("LLM_BASE_URL", "https://api.deepseek.com"),
            api_key=s("DEEPSEEK_API_KEY", ""),
            auth_token=s("AUTH_TOKEN", ""),
            auth_token_staff=s("AUTH_TOKEN_STAFF", ""),
            auth_token_customer=s("AUTH_TOKEN_CUSTOMER", ""),
            enable_demo_role_tokens=b("ENABLE_DEMO_ROLE_TOKENS", False),
            host=s("HOST", "127.0.0.1"),
            port=i("PORT", 8010),
            request_timeout_s=f("REQUEST_TIMEOUT_S", 20.0),
            tool_timeout_s=f("TOOL_TIMEOUT_S", 5.0),
            max_rounds=i("MAX_ROUNDS", 5),
            max_concurrency=i("MAX_CONCURRENCY", 8),
            per_session_quota=i("PER_SESSION_QUOTA", 30),
            session_ttl_s=i("SESSION_TTL_S", 3600),
            daily_budget_cny=f("DAILY_BUDGET_CNY", 5.0),
            max_message_chars=i("MAX_MESSAGE_CHARS", 2000),
            max_context_chars=i("MAX_CONTEXT_CHARS", 24000),
            max_tool_result_chars=i("MAX_TOOL_RESULT_CHARS", 2000),
            circuit_threshold=i("CIRCUIT_THRESHOLD", 3),
            circuit_cooldown_s=f("CIRCUIT_COOLDOWN_S", 8.0),
            retry_max=i("RETRY_MAX", 2),
            retry_base_ms=i("RETRY_BASE_MS", 120),
            price_in_cny_per_1k=f("PRICE_IN_CNY_PER_1K", 0.002),
            price_out_cny_per_1k=f("PRICE_OUT_CNY_PER_1K", 0.008),
            db_path=s("DB_PATH", "data/prodagent.db"),
            log_level=s("LOG_LEVEL", "INFO"),
        )
        obj.validate()
        return obj

    def validate(self) -> None:
        if self.app_env not in ("dev", "staging", "prod"):
            raise ConfigError(f"APP_ENV 非法: {self.app_env}")
        if self.llm_backend not in ("fake", "deepseek"):
            raise ConfigError(f"LLM_BACKEND 非法: {self.llm_backend}")
        if self.llm_backend == "deepseek" and not self.api_key:
            raise ConfigError("LLM_BACKEND=deepseek 时必须提供 DEEPSEEK_API_KEY")
        if self.app_env == "prod" and not self.auth_token:
            raise ConfigError("prod 环境必须配置 AUTH_TOKEN，禁止无鉴权启动")
        if self.app_env == "prod" and self.host not in ("127.0.0.1", "0.0.0.0"):
            raise ConfigError("prod 环境 HOST 非法")
        for name in ("request_timeout_s", "tool_timeout_s", "circuit_cooldown_s",
                     "daily_budget_cny", "price_in_cny_per_1k", "price_out_cny_per_1k"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} 必须 > 0")
        for name in ("max_rounds", "max_concurrency", "per_session_quota",
                     "max_message_chars", "max_context_chars",
                     "max_tool_result_chars", "circuit_threshold"):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} 必须 > 0")
        # 0 是合法值：表示禁用重试（超时/单次调用场景）
        if self.retry_max < 0 or self.retry_max > 6:
            raise ConfigError("RETRY_MAX 需在 0..6 之间，过大有重试风暴风险")

    def redacted(self) -> dict:
        """可安全打印的配置快照：绝不输出真值。"""
        d = asdict(self)
        d["api_key"] = "***configured***" if self.api_key else "<未配置>"
        for tok in ("auth_token", "auth_token_staff", "auth_token_customer"):
            d[tok] = "***configured***" if d[tok] else "<未配置>"
        return d
