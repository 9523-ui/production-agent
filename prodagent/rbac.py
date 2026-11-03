"""M8 角色与权限：三身份 Bearer 令牌映射 + 员工目录分级视图。

设计要点（诚实边界）：
- 角色只有三级：customer（客户）/ staff（员工）/ leader（领导），按 rank 单调授权。
- 令牌是「令牌 -> 角色」的静态映射，无签发/续期/撤销——单机演示口径，
  生产多副本应换成短期 JWT + 网关校验，替换点是 token_map()。
- 演示令牌（demo-token-*）只在 APP_ENV=dev 且 ENABLE_DEMO_ROLE_TOKENS=1 时进入映射，
  prod 一律忽略，绝不因配置失误把演示身份带进生产。
- 员工目录是演示数据（非真实人员）；权限规则是真实的：
  customer 403；staff 只见基本信息（姓名/职务/部门/办公地点），
  因此对领导条目即满足「员工只能查询到领导的基本信息」；leader 见全部字段。
"""
from __future__ import annotations

from .settings import Settings

ROLES = {
    "customer": {"label": "客户", "rank": 0, "desc": "产品对话面：聊天 / 知识查询 / 会话记忆"},
    "staff":    {"label": "员工", "rank": 1, "desc": "客服与运营：另可看内部运行数据、维护知识库"},
    "leader":   {"label": "领导", "rank": 2, "desc": "全部权限：含员工联系方式等敏感字段"},
}

DEMO_TOKENS = {
    "customer": "demo-token-customer",
    "staff":    "demo-token-staff",
    "leader":   "demo-token-leader",
}

# 员工目录（演示数据）：basic = 姓名/职务/部门/办公地点；sensitive = 联系方式
_DIRECTORY = (
    {"name": "王志远", "title": "运营总监",   "dept": "运营部", "office": "3F-301",
     "phone": "13801002001", "email": "wangzy@koda.example", "position": "leader"},
    {"name": "李慧敏", "title": "客服总监",   "dept": "客服部", "office": "3F-305",
     "phone": "13801002002", "email": "lihm@koda.example", "position": "leader"},
    {"name": "陈国栋", "title": "技术总监",   "dept": "技术部", "office": "4F-401",
     "phone": "13801002003", "email": "chengd@koda.example", "position": "leader"},
    {"name": "张晓明", "title": "客服主管",   "dept": "客服部", "office": "3F-310",
     "phone": "13901003001", "email": "zhangxm@koda.example", "position": "staff"},
    {"name": "刘洋",   "title": "客服专员",   "dept": "客服部", "office": "3F-312",
     "phone": "13901003002", "email": "liuy@koda.example", "position": "staff"},
    {"name": "赵倩",   "title": "客服专员",   "dept": "客服部", "office": "3F-312",
     "phone": "13901003003", "email": "zhaoq@koda.example", "position": "staff"},
    {"name": "孙浩",   "title": "售后专员",   "dept": "售后部", "office": "3F-315",
     "phone": "13901003004", "email": "sunh@koda.example", "position": "staff"},
)

_BASIC_FIELDS = ("name", "title", "dept", "office", "position")


def token_map(settings: Settings) -> dict[str, str]:
    """令牌 -> 角色映射。AUTH_TOKEN 兼容映射为 leader（沿用 M2 单令牌语义）。"""
    m: dict[str, str] = {}
    if settings.auth_token:
        m[settings.auth_token] = "leader"
    if settings.auth_token_staff:
        m[settings.auth_token_staff] = "staff"
    if settings.auth_token_customer:
        m[settings.auth_token_customer] = "customer"
    if settings.enable_demo_role_tokens and settings.app_env == "dev":
        for role, token in DEMO_TOKENS.items():
            m.setdefault(token, role)
    return m


def resolve_role(authorization: str | None, tokens: dict[str, str]) -> str | None:
    """Authorization 头 -> 角色；无头/未知令牌返回 None（由中间件决定 401 或开放模式）。"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    return tokens.get(authorization[len("Bearer "):])


def rank_of(role: str) -> int:
    return ROLES.get(role, {"rank": -1})["rank"]


def label_of(role: str) -> str:
    return ROLES.get(role, {"label": role})["label"]


def mask_token(token: str | None) -> str:
    if not token:
        return "<未配置>"
    return token[:10] + "***" if len(token) > 10 else "***"


def directory_for(role: str) -> tuple[str, list[dict]]:
    """员工目录分级视图：customer 一律 403（由路由层处理）；这里返回 (view, items)。"""
    if rank_of(role) >= ROLES["leader"]["rank"]:
        return "full", [dict(e) for e in _DIRECTORY]
    return "basic", [{k: e[k] for k in _BASIC_FIELDS} for e in _DIRECTORY]


def identities_payload(settings: Settings, tokens: dict[str, str]) -> dict:
    """演示身份清单：只在 dev + 开关开启时给出 demo_token 值，其余环境只给提示。"""
    demo_on = settings.enable_demo_role_tokens and settings.app_env == "dev"
    if not demo_on:
        return {
            "enabled": False,
            "identities": [],
            "hint": ("演示身份切换需 APP_ENV=dev 且 ENABLE_DEMO_ROLE_TOKENS=1；"
                     "生产环境请配置 AUTH_TOKEN / AUTH_TOKEN_STAFF / AUTH_TOKEN_CUSTOMER 真实令牌"),
        }
    configured = {"leader": bool(settings.auth_token),
                  "staff": bool(settings.auth_token_staff),
                  "customer": bool(settings.auth_token_customer)}
    return {
        "enabled": True,
        "identities": [
            {"role": r, "label": info["label"], "desc": info["desc"],
             "demo_token": DEMO_TOKENS[r],
             "real_token_configured": configured[r]}
            for r, info in ROLES.items()
        ],
        "hint": "演示令牌仅限本机 dev 验证鉴权链路，生产一律走真实令牌",
    }
