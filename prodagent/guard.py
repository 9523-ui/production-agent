"""安全护栏：输入校验、提示注入拦截、工具白名单、输出脱敏。

生产级要点：
- 白名单与校验必须在工具层，不能只写在 prompt 里求模型「请不要删表」。
- 输出统一过一遍脱敏，防止模型把上游 Key 或用户手机号复述给调用方。
"""
from __future__ import annotations

import re


class GuardError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


_INJECTION_PATTERNS = [
    re.compile(r"忽略(以上|前面|上面|之前).{0,6}(指令|提示|要求|规则)", re.I),
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|prompts)", re.I),
    re.compile(r"(输出|打印|显示|告诉我).{0,8}(system\s*prompt|系统提示词|系统指令)", re.I),
    re.compile(r"(reveal|print|show)\s+(your\s+)?(system\s+)?prompt", re.I),
    re.compile(r"(输出|给我|打印).{0,6}(api\s*key|密钥|token)", re.I),
    re.compile(r"(扮演|切换为).{0,6}(开发者|管理员|无限制|developer\s*mode)", re.I),
]

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_RE = re.compile(r"sk-[A-Za-z0-9]{12,}")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_ID_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")

# M7 追加三个知识/记忆工具：检索本地知识库、写长期记忆、读长期记忆。
ALLOWED_TOOLS = frozenset({
    "search_faq", "lookup_order", "create_ticket", "handoff_to_human",
    "search_kb", "memory_save", "memory_recall",
})


def validate_input(text: str, max_chars: int) -> str:
    """返回规范化文本；拒绝时抛带错误码的 GuardError。"""
    if not isinstance(text, str):
        raise GuardError("INVALID_TYPE", "message 必须是字符串")
    cleaned = _CONTROL_RE.sub("", text).strip()
    if not cleaned:
        raise GuardError("EMPTY_MESSAGE", "消息不能为空")
    if len(cleaned) > max_chars:
        raise GuardError("MESSAGE_TOO_LONG", f"消息超过 {max_chars} 字上限")
    for pat in _INJECTION_PATTERNS:
        if pat.search(cleaned):
            raise GuardError("PROMPT_INJECTION", "检测到提示注入模式，已拒绝")
    return cleaned


def check_tool(name: str) -> None:
    if name not in ALLOWED_TOOLS:
        raise GuardError("TOOL_NOT_ALLOWED", f"工具 {name} 不在白名单内")


def scrub_output(text: str) -> tuple[str, int]:
    """对最终回复做脱敏，返回 (脱敏文本, 命中次数)。"""
    hits = 0

    def sub_factory(replacement: str):
        def _sub(m):
            nonlocal hits
            hits += 1
            return replacement
        return _sub

    text = _SECRET_RE.sub(sub_factory("sk-***"), text)
    text = _PHONE_RE.sub(sub_factory("138********"), text)
    text = _ID_RE.sub(sub_factory("******************"), text)
    return text, hits


DEGRADED_REPLY = "服务暂时不可用，已为您转接人工客服，请稍后重试或留下联系方式。"
