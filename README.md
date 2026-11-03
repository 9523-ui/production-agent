# koda

> 项目名 **koda**；下文「生产级」仅作能力定位描述，不是名称。

一个**能上线、能运维、崩了能恢复**的 Agent 服务。不是第 N 个聊天机器人：
ReAct 内核只是其中一层，真正的工作是它外面那三层运行时壳。

```
① 接入层   鉴权 · 输入校验 · 限流 · 幂等键
② 韧性层   超时 · 指数退避重试 · 三态熔断 · 降级
③ 内核     ReAct 循环 · 受限工具 · 多轮上下文
④ 底座层   trace 落库 · JSON 日志 · 指标 · 回归门禁
```

在此之上按模块逐步接上**可视化控制台**（当前完成 M1 + M2 + M3 + M4 + M5 + M6 + M7 + M8）。

## 快速开始（零云 Key，全离线）

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt

# 跑 92 项验收
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe eval/run_acceptance.py

# 跑 M1–M8 界面冒烟（408 项）
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe eval/smoke_ui.py

# 起服务
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m prodagent.app
# → http://127.0.0.1:8010/       可视化控制台（M1 对话 + M2 运行时状态 + M3 运行历史 + M4 故障演练台 + M5 评估回归 + M6 交付视图 + M7 知识库与长期记忆 + M8 身份与权限）
# → http://127.0.0.1:8010/docs   OpenAPI 文档
```

默认 `LLM_BACKEND=fake`，使用确定性离线模型，任何机器都能复现。打开根路径即可使用控制台：
多轮对话、工具轨迹时间线、最近运行回放、幂等重放、注入拦截演示、深浅主题切换；
点顶栏「运行时」打开 M2 状态面板（熔断三态 / 预算 / 并发 / 限流 / 指标 / 配置快照），
点「运行历史」打开 M3（筛选 / 轨迹回放 / 双条对比），
点「故障演练」打开 M4（5 类受控故障注入 + 探测 + 自动复位，仅 dev 可用），
点「评估回归」打开 M5（一键离线跑 92 项验收、报告与坏例、历史与回归对比，仅 dev 可启动），
点「交付」打开 M6（版本 / 命令 / 交付物清单 / 验收证据 / 运行边界，一屏全只读，任何环境可看），
点「知识」打开 M7（知识库入库 / BM25 检索预演 / 会话长期记忆管理，业务功能任何环境可用），
点「身份」打开 M8（客户 / 员工 / 领导三身份一键切换、员工目录分级视图、权限矩阵，仅 dev 有演示令牌）。

接真实模型：

```bash
copy .env.example .env        # 填 DEEPSEEK_API_KEY，.env 已被忽略
# 或直接用环境变量
DEEPSEEK_API_KEY=xxx LLM_BACKEND=deepseek PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m prodagent.app
```

容器：

```bash
docker compose up --build     # 非 root、HEALTHCHECK、数据卷、优雅退出
```

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 可视化控制台（HTML + `/static/` 静态资源，`no-store`） |
| POST | `/api/chat` | 对话；支持 `session_id`、`idempotency_key` |
| POST | `/api/reset` | 清短期上下文（不动长期审计记录） |
| GET | `/api/meta` | UI 初始化快照：环境 / 后端 / 配置 / 工具白名单（不含密钥） |
| GET | `/api/runs` | 最近运行列表（M1 左栏，默认 20 条，上限 200） |
| GET | `/api/runs/{run_id}` | 回查单次 run 与全部工具步骤（轨迹回放 / M3 对比复用） |
| GET | `/api/history` | M3 运行历史：`status / session_id / error_code / degraded / q / hours / limit / offset`，返回 total + runs + facets |
| GET | `/api/drill/catalog` | M4 只读：故障白名单 / TTL 范围 / 安全边界（非 dev 返回 enabled=False） |
| GET | `/api/drill/status` | M4 只读：当前演练 / 倒计时 / 事件日志；惰性触发 TTL 自动复位 |
| POST | `/api/drill/start` | M4 写：`{kind, ttl_s?, session_id?}`，仅 `APP_ENV=dev`，否则 403 |
| POST | `/api/drill/probe` | M4 写：发一条受控探测请求，返回注入前后对比与预期判定 |
| POST | `/api/drill/stop` | M4 写：显式复位当前演练，状态还原 |
| GET | `/api/eval/catalog` | M5 只读：能否运行验收（非 dev 返回 enabled=False）、命令、超时、报告路径、安全边界 |
| GET | `/api/eval/status` | M5 只读：当前运行 / 最近一次摘要（exit_code、耗时、输出尾部）/ 事件日志 / 历史条数 / 回归对比 |
| GET | `/api/eval/report` | M5 只读：最新 `data/acceptance.json` 全文 + 摘要（文件缺失时容错为零值） |
| GET | `/api/eval/history` | M5 只读：验收运行历史（默认 10 条，eval_runs 表）；`{run_id}` 明细含报告全文 |
| POST | `/api/eval/run` | M5 写：起子进程离线跑 `eval/run_acceptance.py`（强制 FakeLLM，300s 超时），互斥 409，仅 `APP_ENV=dev` |
| GET | `/api/deliver/manifest` | M6 只读：交付清单（版本 / 命令 / 交付物存在性 / 验收证据 / 数据库口径 / 边界声明），任何环境可看 |
| GET | `/api/kb` | M7 知识库文档清单 + 统计（只读） |
| POST | `/api/kb` | M7 知识入库：`{title, content, source?}`（空行分段、400 字切块），返回 doc_id 与分块数 |
| GET | `/api/kb/search` | M7 BM25 检索预演：`q, k`（与 agent 工具 search_kb 同一实现） |
| DELETE | `/api/kb/{doc_id}` | M7 删除知识文档（含分块），404 容错 |
| GET | `/api/memory` | M7 某会话长期记忆列表（最新在前，只读） |
| DELETE | `/api/memory/{id}` / `?session_id=` | M7 删除单条 / 清空会话记忆 |
| GET | `/api/whoami` | M8 身份自查：当前角色 / 认证状态 / 鉴权模式 / 令牌掩码预览（豁免鉴权） |
| GET | `/api/auth/identities` | M8 演示身份清单：dev + 开关开启时下发 demo_token，其余环境只给提示（豁免鉴权） |
| GET | `/api/staff` | M8 员工目录分级视图：客户 403，员工仅基本信息，领导含联系方式 |
| GET | `/api/service` | 原根路径 JSON 服务信息 |
| GET | `/api/stats` | M2 运行时状态：落库统计 + 指标 + 预算 + 熔断（含冷却） + 并发 + 限流 + 进程 + 配置快照 |
| GET | `/healthz` | 存活探针（进程活着即 200） |
| GET | `/readyz` | 就绪探针（DB 可写且熔断未打开，否则 503） |
| GET | `/metrics` | Prometheus 文本格式 |
| GET | `/docs` | OpenAPI 文档 |

M8 分级鉴权：配置 `AUTH_TOKEN`（兼容，映射**领导**）/ `AUTH_TOKEN_STAFF` / `AUTH_TOKEN_CUSTOMER`
任一令牌后，除探针、文档、静态资源、`/api/meta`、`/api/whoami`、`/api/auth/identities`
外的端点需带 `Authorization: Bearer <token>`，角色按令牌映射（客户 / 员工 / 领导）。
未配置任何令牌 = 开放模式（本地 dev 全权限，M1–M7 行为不变）。
控制台顶栏有 token 输入框；dev 环境可开 `ENABLE_DEMO_ROLE_TOKENS=1` 在 M8 面板一键切换身份。
`APP_ENV=prod` 时**强制**要求 `AUTH_TOKEN`，缺了直接拒绝启动。

请求示例：

```bash
curl -X POST http://127.0.0.1:8010/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"怎么退货？","session_id":"u1","idempotency_key":"req-001"}'
```

## M1 可视化控制台

文件：`prodagent/static/`（`index.html` + `css/` 10 个样式文件 + `js/` 9 个脚本文件，按模块拆分；无 CDN、无构建步骤，`<script src>` 按序同步加载，语义与单文件版一致）。

| 区域 | 可交互内容 |
|---|---|
| 顶栏 | 环境 / 后端 / 模型徽标、`/readyz` 健康灯（10s 轮询）、AUTH_TOKEN 输入、深浅主题 |
| 左栏 | 会话 `session_id` 切换与新建、清空上下文、4 个快捷示例（点击即发送）、最近运行列表（点击回放轨迹）、工具白名单 |
| 中间 | 多轮对话气泡、Enter 发送 / Shift+Enter 换行、执行中计时、回复元信息徽标（status / 轮次 / 延迟 / 成本 / 错误码）、一键复制 |
| 右侧 | 本次运行的 trace 时间线：每一步工具名、参数、返回、成功/拒绝、耗时条；`run_id` / `trace_id` / `cached` 等审计字段 |
| 底部 | 运行时关键参数、最近 run_id 与延迟 |

可以现场演示的生产级行为：

- 勾「幂等重放」后连发两次相同内容 → 第二次 `cached=true`，`run_id` 不变，不重复建单。
- 点「注入攻击」示例 → 返回 `400 PROMPT_INJECTION`，轨迹为空，护栏命中可见。
- 点最近任意一条运行 → 从 SQLite 回查并回放该次工具轨迹（重启服务也还在）。
- 快捷示例分别触发 `search_faq` / `lookup_order` / `create_ticket`，右侧时间线逐步展开。

## M2 运行时状态面板

顶栏点「运行时」打开浮层（界面入口 `prodagent/static/index.html`，面板逻辑在 `static/js/m2.js`）。
数据源只有 `GET /api/stats`，**面板只读**，不会改动熔断、预算或任何运行时状态。
默认开启 2s 自动刷新，可关闭；也可「立即刷新」；Esc 或点遮罩关闭。
面板关闭时，顶栏的迷你状态灯与预算百分比也会随健康检查（10s）更新。

| 区块 | 内容 |
|---|---|
| 4 张 KPI 卡 | 熔断当前态 / 日预算 ¥spent ¥limit / 并发 in_flight limit / 请求成功率 |
| 熔断三态状态机 | SVG 画出 CLOSED → OPEN → HALF_OPEN → CLOSED 的 4 条转换边，当前态高亮；下方标注阈值、冷却剩余秒数与探测规则 |
| 预算 / 并发 / 限流仪表 | 三条进度条（≥70% 转黄、≥90% 转红），显示当前会话在 60s 滑动窗口内已用配额、活跃会话数与 Top 会话 |
| 指标计数器 | `status_ok` / `idempotent_hit` / `guard_rejected_PROMPT_INJECTION` / `throttled_*` / `tool_blocked` 等，按值降序并中文化 |
| 延迟分位 / 落库统计 | p50 / p95 / max、样本数、steps / sessions / tickets / 累计成本 |
| 运行时配置快照 | 15 项可调参数（超时、重试、熔断阈值与冷却、并发、配额、预算、单价、TTL），只读 |

现场演示：

- 打开面板 → 发一条消息 → 立刻刷新，落库 ok +1、限流窗口 +1、预算增长、`status_ok` +1、并发峰值 ≥1。
- 勾「幂等重放」连发两次 → `idempotent_hit` +1。
- 点「注入攻击」→ 计数器出现 `guard_rejected_PROMPT_INJECTION`，熔断仍为 CLOSED（护栏命中不算上游失败）。
- 配置 `AUTH_TOKEN` 后，面板请求自动带上控制台保存的 token。

后端为 M2 做的增量（**原有字段一个没删**，不破坏既有调用方与 A8 验收）：

- `BreakerSnapshot` 补 `threshold / cooldown_s / cooldown_remaining_s / since_open_s`，UI 不必反推配置与单调时钟。
- `BudgetLedger.snapshot()` 补 `remaining_cny / used_pct / exceeded`。
- `SlidingWindowLimiter.snapshot()` 新增：先清理过期，再返回活跃会话数、窗口内总请求、Top 会话。
- `/api/stats` 新增 `limiter / process / runtime / ui_module / ts`，`concurrency` 补 `acquire_timeout_s`。
- `runtime` 是显式白名单，不含 `api_key`、`auth_token`、`db_path` 等敏感字段。

## M3 运行历史与轨迹回放

顶栏点「运行历史」打开浮层。数据源是 `GET /api/history`（列表 / 筛选 / 分页 / facets）与
`GET /api/runs/{run_id}`（回放 / 对比），**全程只读**，不改动任何运行记录。

| 区域 | 可交互内容 |
|---|---|
| 筛选器 | 状态 / 会话 / 错误码（选项由 facets 自动生成）、是否降级、时间范围（1h / 24h / 全部）、关键词（请求或回复文本）、每页条数；「应用筛选」/「重置」/ 回车即查 |
| 列表 | 时间、run_id、会话、请求摘要、状态徽标、轮次、延迟；点行 → 右侧轨迹回放；勾选最多 2 条 → 对比 |
| 分页 | 上一页 / 下一页、第 N/M 页、每页条数；`limit` 服务端 clamp 1..200 |
| 单条回放 | 右侧显示 9 项元信息 + 完整工具时间线（seq / 工具名 / args / result / 成功失败 / 耗时）+ 请求与回复原文 |
| 双条对比 | 并排 A/B 两列，11 个字段（状态 / 会话 / 轮次 / 延迟 / 成本 / 降级 / 错误码 / 步数 / 工具链 / 请求 / 回复），**取值不同的行高亮**，底部给出差异项数 |

现场演示：

- 发几条不同结果的消息后打开面板 → 状态选 `rejected` 只剩被拦截的运行；错误码选 `PROMPT_INJECTION` 同理。
- 关键词填「退货」→ 只留请求文本含该词的运行；时间选「最近 1 小时」→ 排除更早记录。
- 勾选 1 条正常 + 1 条被拒 → 点「对比选中」→ 状态、错误码、步数、工具链等差异行变黄，直接看出两次运行差在哪。
- 点任意一行 → 右侧回放工具轨迹；M1 右侧 trace 面板与左栏「最近运行」**不受影响**，两者并存。
- 面板打开时再发一条消息，历史列表会自动刷新。

后端为 M3 做的增量（**原有端点与字段一个没删**）：

- `Store.query_runs()`：多条件筛选 + 总数 + 分页 + facets。用 `instr()` 做子串匹配（避免把 `%` / `_` 当 LIKE 通配符），全部参数化，`limit` clamp 1..200。
- `GET /api/history`：参数 `status / session_id / error_code / degraded / q / hours / limit / offset`，`hours` 换算成 `created_at >=` 下界；返回 `total / limit / offset / runs / facets`。
- `facets` 从全表统计（不受当前筛选影响），给下拉提供可选值：状态分布、Top 20 会话、错误码分布、降级分布。
- 回放与对比**复用** `GET /api/runs/{run_id}`，不新增任何写接口。

## M4 故障注入演练台

顶栏点「故障演练」打开浮层。与 M1–M3 的**只读视图**不同，本模块会临时改变运行时状态，
因此有三条硬边界，全部在 `prodagent/drill.py` 的 `DrillController` 里强制：

1. **仅 `APP_ENV=dev` 可用**：staging / prod 的全部写端点返回 `403 DRILL_DISABLED`（catalog / status 仍可只读查看）。
2. **故障类型白名单 + 单演练互斥**：只允许 `timeout / breaker / rate_limit / budget / injection` 五类；同时最多一个演练，启动新的会先复位旧的。
3. **TTL 自动复位**：每个演练 5–120s（默认 20s）后由后台 janitor 每秒检查自动复位；也可随时显式「复位」；进程退出前强制复位，状态绝不残留。

注入只调用运行时组件的公开方法（`on_failure()` / `check()` / `add()` / `reset()`）或替换 LLM 适配器引用，
不改任何模块的内部结构；`resilience.py` 只新增了 `SlidingWindowLimiter.reset()` 与 `BudgetLedger.reset()` 两个复位入口。

| 演练 | 注入方式 | 实测现象（真实验证，非预期值） |
|---|---|---|
| `timeout` 上游超时 | LLM 换成 `FaultLLM`，每次调用抛 `UpstreamTimeout` | 重试 2 次耗尽 → 降级回复，`error_code=UpstreamTimeout`，熔断失败计数 0→3 |
| `breaker` 熔断打开 | 连续 `on_failure()` 达阈值 | 请求被 `CircuitOpen` 直接拒绝，`/readyz` 变 503，M2 状态机高亮 OPEN |
| `rate_limit` 单会话限流 | 灌满独立会话 `__drill_rate_limit__` 的 60s 窗口 | 该会话 429 `RateLimited`，其他会话不受影响 |
| `budget` 日预算耗尽 | 把当日累计成本抬到限额 | 429 `BudgetExceeded`，M2 预算进度条变红 |
| `injection` 注入拦截（对照） | 零状态变更，仅发送注入样本 | 400 `PROMPT_INJECTION`，轨迹为空 |

面板交互：5 张演练卡片（标注「改状态 / 对照组」）、TTL 下拉（10/20/60s）、
「发一条探测请求」→ 注入前后 5 组状态对比（熔断 / 失败计数 / 成本 / LLM / 限流命中）+ 预期判定徽标、
演练事件日志（start / probe / stop / auto_reset / auto_stop 全记录）、当前演练倒计时、2s 自动刷新；
顶栏迷你灯在演练进行中变红。探测的「业务 HTTP 状态」与 `/api/chat` 的真实映射一致
（degraded 仍返回 200——降级是给了兜底回复，不是服务错误）。

## M5 评估回归面板

顶栏点「评估回归」打开浮层（`prodagent/evalpanel.py` 的 `EvalController` + `static/js/m5.js`，
复用 `app.js` 的通用面板注册器 `registerPanel`）。与 M4 一样会改变状态（起子进程），
边界同样硬：

1. **仅 `APP_ENV=dev` 可启动**：prod 全部写端点 `403 EVAL_DISABLED`（catalog / status / report / history 仍可只读）。
2. **子进程强制离线**：`LLM_BACKEND=fake` + `APP_ENV=dev` 注入子进程环境，绝无真实 key；300s 超时保护。
3. **互斥**：同一时刻最多一次验收运行，重复启动 `409 EVAL_RUNNING`；服务退出时 `close()` 取消未完成的任务。

| 区域 | 内容 |
|---|---|
| 状态条 | 运行中 Ns / 上次 N/M · exit k / 尚无记录；「运行验收」（带 `data-testid`）、「刷新」、2s 自动刷新开关 |
| 最新报告 | `data/acceptance.json` 摘要：通过数 / 总数大字、失败数与报告路径、每组 A1–A11 的通过 chip（全绿 / 变红） |
| 坏例归因 | 失败用例列表（组 / 名称），全绿时空态 |
| 运行输出 | 子进程 stdout/stderr 尾部（mono），运行中显示「运行中…」 |
| 历史表 | eval_runs 落库：编号 / 时间 / 结果 / Δ（与上一次比 ±N，回归红色）/ 耗时；点行展开当次失败用例 |

回归判定：最近两次运行 `failed` 变多或 `passed` 变少即判回归，标题栏出现 ⚠ 提示（含对比序号）。

现场演示：

- 打开面板 → 点「运行验收」→ 状态条切「运行中 Ns」、顶栏迷你灯变黄 → 约 2s 收敛 92/92 全绿，报告 / 历史 / 坏例区自动刷新（结束沿检测，不必手动刷新）。
- 连点两次「运行验收」→ 第二次 `409 EVAL_RUNNING`，输出区给出提示。
- 历史累积 ≥2 条后，状态条自动出现与上一次的 Δ 对比；人为制造失败（如改坏一条用例）再跑一次 → 标题 ⚠ 回归警示 + 坏例列表点名失败用例。
- 报告文件被删/损坏 → 面板显示零值摘要而非报错（`_read_report_file` 容错）。

后端为 M5 做的增量：

- `store.py` 迁移到 v2：`eval_runs` 表（passed / failed / total / source / exit_code / duration_ms / groups / report 全文 + 时间索引），走版本化 `MIGRATIONS` 机制，旧库自动升级、崩溃幂等重放。
- `EvalController`：`run() / status() / report() / history() / close()`，事件日志倒序 12 条；执行器可注入（验收 A10 用假执行器测，冒烟 S6 起真子进程再测一遍）。
- 6 个新端点（上表），全部写端点仅 dev。

## M6 交付视图

顶栏点「交付」打开浮层（`prodagent/deliverpanel.py` 的 `DeliverController` + `static/js/m6.js`，
同样复用 `registerPanel`）。它回答一个交付评审者站在陌生机器前的五个问题，
**全程只读**——这是它与 M4 / M5 最大的区别：

1. **无任何写端点**：面板只调 `GET /api/deliver/manifest`，不发起任何写请求。
2. **任何环境可看**：dev / staging / prod 一视同仁（对比：M4 写端点仅 dev、M5 启动仅 dev）。
3. **全部文件读取容错**：VERSION / CHANGELOG / 验收报告缺失时回退或给零值，绝不 500。

| 区域 | 内容 |
|---|---|
| 版本与运行环境 | 版本号（VERSION 文件，缺失回退内置号 + 来源标注）、最近变更行、APP_ENV / 后端 / 模型 / 鉴权 / 监听 |
| 交付命令 | 五条拿走就能跑的命令（验收 / 冒烟 / 起服务 / 容器 / 接真实模型），各带一句边界说明 |
| 交付物清单 | 11 个关键文件的存在性与大小（实时来自文件系统，缺谁一眼可见） |
| 数据与证据 | 数据库路径 / schema 版本 / WAL / 五类行数；最近一次验收摘要与落库历史 |
| 相关入口 | 控制台 / OpenAPI / meta / metrics / healthz / readyz 直达链接 |

现场演示：

- 打开面板 → 版本号读取 `VERSION` 文件（当前 2.3.0）并标注来源；删掉该文件再刷新 → 自动回退内置号，面板不报错（无需改代码）。
- 任何环境都能看：把 `APP_ENV` 切到 prod 重启，M4 / M5 的写按钮变灰，交付面板原样可开（验收 A11 / 冒烟 S7 都验过这条边界）。
- 故意删掉 `data/acceptance.json` → 证据区显示「尚未生成」零值摘要，面板不报错。

后端为 M6 做的增量：

- `DeliverController`：无状态装配器，`manifest()` 每次现算，密钥纪律与 meta 一致（清单序列化后无 `api_key` / `auth_token`）。
- 1 个新只读端点（上表）；`meta` / `stats` / 启动横幅的 UI 完成度标记统一升级为 M6。

## M7 知识库与长期记忆

顶栏点「知识」打开浮层（`prodagent/kb.py` + `static/js/m7.js`）。这是**业务深化**模块：
让 agent 回答 FAQ 之外的领域知识（发票 / 会员 / 优惠），并跨轮记住客户偏好。
三条边界与 M4 / M5 / M6 的刻意差异：

1. **知识与记忆是业务功能，不做 dev 门禁**：与 chat 同级，dev / staging / prod 均可用（对比：M4 写端点仅 dev、M5 启动仅 dev）。
2. **检索完全本地**：BM25（k1=1.5 / b=0.75）+ 中文 bigram 分词，无外部依赖、无向量库。
3. **诚实边界**：BM25 是词面匹配不是语义检索，同义改写无泛化；索引进程内缓存、写后全量重建，适合千级以下文档（docstring 已声明）。

| 区域 | 内容 |
|---|---|
| 知识库文档表 | 标题 / 来源 / 分块数 / 时间 / 删除；统计进状态 pill |
| 添加表单 | 标题 1–80 字、正文 1–8000 字（空行分段、400 字切块）；「载入示例知识」一键入库 2 篇演示文档 |
| 检索预演 | 输入查询实时 BM25 top-3（doc / 块号 / 相关度 / 命中原文），与 agent 工具同一实现 |
| 长期记忆表 | 当前会话（与左侧 session 输入联动）的记忆：内容 / 类型 / 命中次数 / 单条删除；「清空本会话记忆」 |

agent 侧新增 3 个白名单工具（4 → 7）：`search_kb`（检索知识库，未命中如实告知）、
`memory_save`（存偏好/事实，kind 归一 fact/preference）、`memory_recall`（按词评分取 top-3，命中计数）。
**每轮对话自动把最近 5 条记忆注入 system 块**（`【客户记忆】` 前缀、500 字上限、增删后下一轮即时刷新）。

现场演示：

- 点「载入示例知识」→ 入库 2 篇 → 检索预演输入「发票」→ top-1 即发票政策分块，相关度可见。
- 对话输入「记住我偏好顺丰快递」→ 轨迹显示 `memory_save` → 打开 M7 面板可见该记忆（类型 preference）。
- 新一轮对话（换 session 输入任意问题）→ agent 上下文已含注入的记忆块；删除该记忆后下一轮自动消失。
- prod 环境下 KB / memory 端点照常可用（冒烟 S8 与 drill 的 403 做过对照验证）。

后端为 M7 做的增量：

- `kb.py`：`KnowledgeBase`（add/delete/list/stats/search + 惰性索引重建）、`chunk_text` / `tokenize` / `memory_score` 纯函数。
- `store.py` 迁移到 v3：`kb_docs` / `kb_chunks` / `memories` 三张表（memories 带 kind / hits / 会话索引），走版本化 `MIGRATIONS`。
- 8 个新端点（上表）；`agent.py` 工具分发 + 记忆注入；FakeLLM 离线路由同步扩展（记忆词优先，防误路由到 FAQ）。

## M8 身份与权限（规模化 · 角色鉴权）

顶栏点「身份」或当前身份 pill 打开浮层（`prodagent/rbac.py` + `static/js/m8.js`）。这是**规模化**模块：
把单机演示口径的三身份分级鉴权做进服务与控制台。三条边界：

1. **令牌是静态映射**：Bearer 令牌 → 角色，无签发 / 续期 / 撤销；生产多副本应换短期 JWT + 网关，替换点是 `rbac.token_map()`（docstring 已声明）。
2. **未配置令牌 = 开放模式**：本地 dev 全权限，M1–M7 行为与全部既有验收完全不变。
3. **演示令牌只进 dev**：`ENABLE_DEMO_ROLE_TOKENS=1` 且 `APP_ENV=dev` 才生效，prod 一律忽略，绝不因配置失误把演示身份带进生产。

| 区域 | 内容 |
|---|---|
| 当前身份 | whoami 自查结果：角色 / 认证状态 / 鉴权模式 / 令牌掩码预览 / 环境 |
| 身份切换 | 客户 / 员工 / 领导 / 匿名四钮（demo 令牌写入 localStorage 立即生效）；顶栏 pill 实时显示当前身份 |
| 员工目录 | `/api/staff` 分级视图：客户 403；员工只见姓名/职务/部门/办公地点；领导另含电话 / 邮箱 |
| 权限矩阵 | 三身份 × 核心端点能力表（与后端门禁一一对应） |

权限规则（后端真实执行，非展示）：对话与知识查询全员可用；内部运行数据（`/api/stats` / `/api/runs` / `/api/history`）、
知识库写操作、演练与评估面板限**员工及以上**（403 ROLE_FORBIDDEN）；员工目录分级如上。

现场演示：

- dev 起服务时设 `ENABLE_DEMO_ROLE_TOKENS=1` → 打开 M8 面板 → 点「客户」→ 顶栏 pill 变「身份·客户」，M2 / M3 面板数据全部 403。
- 切「员工」→ 打开员工目录 → 联系方式列显示「仅领导可见」；切「领导」→ 电话 / 邮箱全部出现。
- 点「匿名」→ 令牌清除，受保护端点返回 401；对照开放模式（未配置令牌时匿名即全权限）。

后端为 M8 做的增量：

- `rbac.py`：`token_map` / `resolve_role` / `rank_of` / `label_of` / `mask_token` / `directory_for` / `identities_payload` 纯函数 + 演示目录数据。
- `app.py`：中间件按令牌映射角色（401 / 403 分层，演练与评估前缀整组限员工）；`_require_rank` 端点门禁；3 个新端点（whoami / identities / staff）。
- `settings.py`：`AUTH_TOKEN_STAFF` / `AUTH_TOKEN_CUSTOMER` / `ENABLE_DEMO_ROLE_TOKENS` 三个新配置（见上表）。

## UI 模块路线图

按以下顺序**一个一个实现**，每次只动一个模块、跑通后再进下一个：

| 模块 | 内容 | 状态 |
|---|---|---|
| M1 | 对话工作台 + 工具轨迹时间线 | 已完成 |
| M2 | 运行时状态面板（熔断 / 预算 / 并发 / 指标） | 已完成 |
| M3 | 运行历史与轨迹回放（列表 / 筛选 / 对比） | 已完成 |
| M4 | 故障注入演练台（超时 / 熔断 / 限流 / 预算 / 拦截） | 已完成 |
| M5 | 评估回归面板（用例 / 门禁 / 坏例归因） | 已完成 |
| M6 | 交付视图（文档 / 容器 / 部署说明） | 已完成 |
| M7 | 知识库与长期记忆（BM25 检索 / 会话记忆 / 工具扩展） | 已完成 |
| M8 | 身份与权限（客户 / 员工 / 领导分级鉴权 + 演示切换） | 已完成 |

## 配置项（全部来自环境变量）

| 变量 | 默认 | 说明 |
|---|---|---|
| `APP_ENV` | `dev` | `dev / staging / prod` |
| `LLM_BACKEND` | `fake` | `fake`（离线）/ `deepseek` |
| `DEEPSEEK_API_KEY` | 空 | 仅 deepseek 后端需要；不进日志/响应/数据库 |
| `AUTH_TOKEN` | 空 | prod 必填；配置后兼容映射为**领导**角色 |
| `AUTH_TOKEN_STAFF` | 空 | M8 员工角色令牌（三令牌任一配置即开启分级鉴权） |
| `AUTH_TOKEN_CUSTOMER` | 空 | M8 客户角色令牌 |
| `ENABLE_DEMO_ROLE_TOKENS` | `0` | M8 演示身份切换令牌，仅 dev 生效，prod 忽略 |
| `HOST` / `PORT` | `127.0.0.1` / `8010` | 监听地址 |
| `DB_PATH` | `data/prodagent.db` | SQLite 路径 |
| `REQUEST_TIMEOUT_S` | `20` | 单次上游调用超时 |
| `TOOL_TIMEOUT_S` | `5` | 单次工具执行超时 |
| `MAX_ROUNDS` | `5` | ReAct 最大轮数，超轮降级转人工 |
| `MAX_CONCURRENCY` | `8` | 全局并发闸 |
| `PER_SESSION_QUOTA` | `30` | 单会话 60s 内请求上限 |
| `DAILY_BUDGET_CNY` | `5` | 日成本预算 |
| `MAX_MESSAGE_CHARS` | `2000` | 输入长度上限 |
| `CIRCUIT_THRESHOLD` | `3` | 连续失败熔断阈值 |
| `CIRCUIT_COOLDOWN_S` | `8` | 熔断冷却，之后放一个 HALF_OPEN 探测 |
| `RETRY_MAX` | `2` | `0` 表示禁用重试（合法） |
| `RETRY_BASE_MS` | `120` | 指数退避基值，带抖动 |
| `PRICE_IN_CNY_PER_1K` / `PRICE_OUT_CNY_PER_1K` | `0.002` / `0.008` | **示例单价**，非真实计费 |

完整列表见 `.env.example`。

## 8 条生产级标准与验收

| # | 标准 | 验收组 |
|---|---|---|
| S1 | 配置与密钥分离、fail-fast、日志打码 | A1 |
| S2 | trace_id 贯穿、run/step 落库、结构化日志、指标 | A2 |
| S3 | 超时 / 重试 / 熔断 / 降级 | A3 A4 A5 |
| S4 | 幂等键、会话串行、并发闸 | A6 |
| S5 | 限流、日预算 | A6 |
| S6 | 注入拦截、工具白名单、输出脱敏 | A7 |
| S7 | 探针分离、鉴权、优雅退出、TTL 清理 | A8 |
| S8 | 离线评测集 + 退化门禁 | A8 A9 A10 A11 A12 A13 |

实跑结果：

- `compileall` → OK
- `eval/run_acceptance.py` → **PASS 92 / FAIL 0**（A1 4、A2 7、A3 3、A4 4、A5 2、A6 4、A7 4、A8 9、A9 9、A10 10、A11 10、A12 13、A13 13），报告写入 `data/acceptance.json`
- `eval/smoke_ui.py`（M1 静态页 / DOM id / `node --check` / 接口 / 交互链路 / M2 增量字段与熔断冷却验算 / M3 筛选分页对比 / M4 五类演练链路、边界守卫、互斥、自动复位与组件直接验算 / M5 面板静态、API 边界与真跑一次验收子进程 / M6 交付清单、只读边界、prod 可看与缺文件容错 / M7 知识库增删搜校验、chat 记忆链路与 prod 业务端点对照 / M8 三身份令牌映射、目录分级、客户门禁 403、员工放行、无令牌 401 与静态面板检查）→ **PASS 408 / FAIL 0**
- 真实 uvicorn 服务 HTTP 冒烟（M1 全链路 + M2 数据联动 + M3 历史筛选对比 + M4 五类演练与 TTL 自动复位实测，共 35 项）→ **PASS 35 / FAIL 0**
- 真实 DeepSeek API 冒烟（2026-11-03，staging + `deepseek-chat` + 独立临时库，服务起停单命令内完成）→ **2/2 成功**：工具自主选择、落地回答、多轮上下文、真实成本计量全链路验证

验收全程使用临时数据库与离线模型，不污染任何现有数据。

## 数据模型

`runs`（`idempotency_key` UNIQUE，含 trace_id / token / 成本 / 延迟 / 错误码 / 状态）、
`steps`（工具轨迹）、`sessions`（TTL）、`tickets`（幂等建单）、
`eval_runs`（M5 验收运行历史，含报告全文与分组摘要）、
`kb_docs` / `kb_chunks`（M7 知识库：文档 + 400 字分块）、
`memories`（M7 长期记忆：kind / hits / 会话索引）。SQLite 开启 WAL 与事务。
schema 走版本化迁移（当前 v3，`PRAGMA user_version`），旧库自动升级。

## 诚实声明

- `FakeLLM` 是确定性模拟器，**92 项验收证明的是运行时逻辑，不是模型质量**。不要把它当模型表现写进简历。
- 会话短期历史在进程内存（带会话锁与按 user 边界裁剪），单机够用；多副本需换 Redis，替换点是 `ProductionAgent._history`。
- SQLite + 同步连接适合单机/中小流量；多副本需换 PostgreSQL 异步驱动，`Store` 接口不变。
- 成本单价是示例值；真实计费要接上游 usage 并区分「估算 / 真实」。
- `DeepSeekLLM` **已用真实 Key 冒烟通过（2026-11-03，staging，deepseek-chat）**：两轮真实对话验证了鉴权、工具自主选择（`search_faq` / `lookup_order` 参数正确）、基于工具结果的落地回答、多轮上下文、按真实 usage 的成本计量；延迟 1.4–2.3s / 单轮成本约 0.004 元。冒烟命令见 M6 交付视图「接真实模型」一行。
- UI 八个模块（M1–M8）全部完成。当前界面为请求-响应式，非流式输出；M2 / M3 / M6 面板只读（M6 任何环境可看），M4 会按白名单临时改变运行时状态（仅 dev，自动复位），M5 起子进程离线跑验收（仅 dev，互斥），M7 知识与记忆是业务端点（任何环境可用，本地 BM25 非语义检索），M8 三身份鉴权为静态令牌映射（无签发/续期，生产多副本应换短期 JWT + 网关）。
- 尚未做：TLS 终止、多租户、真实 CI、OpenTelemetry 分布式 trace、灰度与回滚编排、向量语义检索、短期 JWT 鉴权。

## 目录

```
prodagent/
  settings.py      配置与密钥
  logging_conf.py  JSON 日志 + trace_id + 日志打码
  resilience.py    超时/重试/熔断/并发闸/限流/预算（含 M4 复位入口）
  guard.py         注入拦截 + 工具白名单（7 工具） + 输出脱敏
  drill.py         M4 故障注入演练台（白名单 / 互斥 / TTL 自动复位）
  evalpanel.py     M5 评估回归面板（dev 守卫 / 互斥 / 子进程离线验收 / 历史落库）
  deliverpanel.py  M6 交付视图（全只读清单 / 文件容错 / 任何环境可看）
  kb.py            M7 知识库（BM25 / 分块 / 惰性索引） + 记忆评分
  rbac.py          M8 三身份角色鉴权（令牌映射 / 分级目录 / 演示身份，仅 dev）
  store.py         SQLite WAL + 幂等 + 会话 TTL + 最近运行列表（版本化迁移 v3 + eval_runs + kb/memories）
  metrics.py       Prometheus 文本指标
  llm.py           FakeLLM / DeepSeekLLM
  agent.py         生产级编排（7 工具分发 + 记忆自动注入）
  app.py           FastAPI 装配 + 生命周期 + 静态控制台
  static/
    index.html     M1–M8 控制台骨架（无 CDN、无构建）
    css/           tokens / base / m1–m8 样式按模块拆分
    js/            app（公共 + registerPanel）+ m1–m8 面板逻辑按模块拆分
eval/
  cases.jsonl      回归用例
  run_acceptance.py 13 组 92 项验收（A9 上下文 / A10 评估面板 / A11 交付视图 / A12 知识与记忆 / A13 身份与权限）
  smoke_ui.py      M1–M8 界面与接口冒烟（408 项，含真跑一次验收子进程）
Dockerfile / docker-compose.yml / .env.example / requirements.txt
VERSION / CHANGELOG.md / README.md
```

## 与原有项目的关系

ReAct 内核设计、SYSTEM_PROMPT 思路、4 个受限工具来自 `aiagent/cs/`；
trace schema 与回归门禁思路来自 `agent-studio/`。本项目把它们收进**一个独立服务**，
原项目一行不改。详见 `../生产级Agent-标准差距与落地方案.md`。
