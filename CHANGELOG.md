# 更新记录

## 2.3.0（2026-11-03）

- 方向三「规模化」落地：三身份角色鉴权（客户 / 员工 / 领导），UI 第八模块 M8。
- Bearer 令牌映射角色：`AUTH_TOKEN`（兼容映射领导）/ `AUTH_TOKEN_STAFF` / `AUTH_TOKEN_CUSTOMER`；未配置任何令牌 = 开放模式（本地 dev 全权限，M1–M7 行为不变）。
- 演示令牌（demo-token-*）仅 `APP_ENV=dev` + `ENABLE_DEMO_ROLE_TOKENS=1` 生效，prod 一律忽略；控制台可一键切换身份体验分级。
- 权限矩阵：对话与知识查询全员；内部运行数据（stats / runs / history）、知识库写操作、演练与评估面板限员工及以上；员工目录客户 403，员工仅见基本信息，领导见联系方式全字段。
- 新端点：`GET /api/whoami`（身份自查）、`GET /api/auth/identities`（演示身份清单）、`GET /api/staff`（员工目录分级视图）。
- 诚实边界：令牌为静态映射，无签发/续期/撤销；生产多副本应换短期 JWT + 网关，替换点是 `rbac.token_map()`。
- 质量口径：离线验收 13 组 92 项、UI 冒烟 408 项，全部通过。
- 文档：README 新增「界面预览」区，配 M1 / M2 / M3 / M5 / M7 / M8 六张真实运行截图（存于 `docs/screenshots/`）。

## 2.2.0（2026-11-03）

- 方向二「业务深化」落地：知识库 + 长期记忆 + agent 工具扩展，UI 第七模块 M7。
- store v3：新增 `kb_docs` / `kb_chunks` / `memories` 三张表（版本化迁移，旧库自动升级）。
- 本地 BM25 知识库：空行分段 + 400 字切块、中文 bigram 分词、k1=1.5/b=0.75 排序；`search_kb` 工具与 `/api/kb*` 端点同一实现。
- 长期记忆：`memory_save` / `memory_recall` 工具落库 SQLite，命中计数；每轮对话自动把最近 5 条记忆注入 system 块（前缀标记、删除即失效）。
- 工具白名单 4 → 7（新增 search_kb / memory_save / memory_recall）；FakeLLM 离线路由同步扩展。
- 控制台 M7 面板：文档入库 / 检索预演 / 示例知识 / 会话记忆管理（增删清空）。
- 质量口径：离线验收 12 组 79 项、UI 冒烟 376 项，全部通过。
- 真实 DeepSeek API 冒烟通过（staging + deepseek-chat）：鉴权、工具自主选择、落地回答、多轮上下文、真实 usage 成本计量全链路验证。

## 2.1.0（2026-11-03）

- 控制台 UI 六模块完成：M1 对话工作台 / M2 运行时状态 / M3 运行历史与对比 / M4 故障演练台 / M5 评估回归 / M6 交付视图。
- 静态资源无损拆分：`css/`（tokens / base / m1–m6）与 `js/`（app + registerPanel / m1–m6），无 CDN、无构建，语义与单文件版一致。
- store v2：新增 `eval_runs` 验收历史表（报告全文 + 分组摘要 + 时间索引），版本化迁移，旧库自动升级、崩溃幂等重放。
- M5 评估回归面板：控制台一键离线跑验收（子进程强制 FakeLLM、300s 超时、互斥），历史 Δ 对比自动判回归。
- M6 交付视图：版本 / 命令 / 交付物 / 证据 / 边界一屏全只读，任何环境可看，全部文件读取容错。
- 质量口径：离线验收 11 组 66 项、UI 冒烟 332 项、真实 uvicorn HTTP 冒烟 35 项，全部通过。

## 2.0.0

- 初始生产级 Agent 服务：ReAct 编排、4 个受限工具、超时/重试/熔断/降级、幂等键、限流与日预算、注入拦截与输出脱敏、JSON 结构化日志与 Prometheus 指标、SQLite WAL 存储、Docker 交付。
