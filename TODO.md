# LLMOps 评测与监控平台 - 待完成清单

> 以下问题基于 problem.md 逐项代码验证（2026-05-10）

---

## 一、高危（必须修复）

| # | 类型 | 文件 | 问题 | 现状 |
|---|------|------|------|------|
| 1 | 安全 | celery_app.py:416 + url_safety.py | **SSRF：** target_url 校验与请求间存在 DNS rebinding / TOCTOU 窗口 | ✅ 已修复 — `ValidatingHTTPAdapter` TCP 连接前锁定 IP |
| 2 | 安全 | apikey.py:33 | **API Key Base64 编码而非加密：** 数据库泄露时密钥直接暴露 | ✅ 已修复 — Fernet 对称加密替代 Base64 |
| 3 | 安全 | main.py:15 | **CORS 配置危险：** `allow_origins=["*"]` + `allow_credentials=True` | ✅ 已修复 — `*` 时移除 credentials；生产可配白名单 |
| 4 | 安全 | config_router.py + provider_models.py:99 | **第二条 SSRF 路径：** base_url 由用户写入后用于请求内网 | ✅ 已修复 — 写入时前置校验 + `_http_json_get` IP 锁定 |
| 5 | Bug | celery_app.py:243 + report_generator.py + frontend | **Demo 模式数据混入：** 报告不区分 mode，真实/模拟结果混合 | ✅ 已修复 — 报告加 mode 标注 + 前端 DEMO/LIVE 标签 |

## 二、中高危

| # | 类型 | 文件 | 问题 | 现状 |
|---|------|------|------|------|
| 6 | 设计 | provider_models.py:204 | **静默 Fallback：** 拉模型失败吞异常，返回默认列表，掩盖 Provider 故障 | ✅ 已修复 — 异常穿透到 API 层返回 502，不缓存
| 7 | 设计 | 多处 | **吞异常：** `_publish_progress`、`classify_with_llm` 等多处 `except Exception: pass`，故障隐藏 | ✅ 已修复 — 改为 logger.warning + exc_info；并发循环中标记 error |
| 8 | 工程 | main.py:23 | **无迁移体系：** `create_all()` 启动建表，Schema 变化无法回滚 | ✅ 已修复 — Alembic 初始化 + 初始迁移已生成并 apply |
| 9 | 设计 | dashboard.py:18 | **健康检查误报：** Redis 可达即判 celery_ready，未验证 worker 是否在线 | ✅ 已修复 — `inspect().ping()` 真实验证 worker 连通性 |
| 10 | 性能 | dashboard.py:34 | **日志全读内存：** `f.readlines()` 一次读入再切尾，大日志内存压力大 | ✅ 已修复 — 改用 `_tail_file()` 从文件末尾反向读取 |
| 11 | Bug | report_generator.py | **报告不区分 mode：** target_mode/evaluation_mode 未纳入报告 | ✅ 已修复 — 报告顶部标注 mode + Demo 警告 |
| 12 | 安全 | url_safety.py:30 | **DNS Rebinding 窗口：** 校验与请求分两次解析，结果可能不同 | ✅ 已修复 — `resolve_and_validate()` 锁定解析结果 |

## 三、中等

| # | 类型 | 文件 | 行号 | 问题 | 现状 |
|---|------|------|------|------|------|
| 13 | Bug | runs.py:294-308 | **SSE 丢消息窗口：** subscribe 到首次 get_message 之间可能丢消息，DB 轮询每 0.1s 无法完全弥补 | ✅ 已修复 — 先 subscribe 再 poll，消除丢失窗口 |
| 14 | 类型 | models/dataset.py:38 vs schemas/dataset.py:68 | **reference_context_ids 类型不一致：** Model 用 `list[str]`，EvalCaseOut 用裸 `list` | ✅ 已修复 |
| 15 | 设计 | versions.py:64-71 | **Version 删除级联不完整：** 有检查但无 DB 级约束，并发下可能触发 500 | ✅ 已修复 — FK 加 `ondelete="SET NULL"` + `IntegrityError` 兜底 |
| 16 | UX | Settings.tsx:55 | **保存后字段未清空：** 关闭再打开 Modal，旧 API Key 仍可见 | ✅ 已修复 — 打开/关闭 Modal 时始终 resetFields |
| 17 | UX | RunResults.tsx:144 | **详情弹窗无加载状态：** 异步加载时 Modal 直接打开显示空白 | ✅ 已修复 — loading 状态先于 modal open 设置 |
| 18 | UX | VersionCompare.tsx | **对比结果只显示 case_id：** 看不出具体是哪个 Case 变化 | ✅ 已修复 — CompareTable 已展示 input 字段 |
| 19 | 设计 | 6 个文件 | **Query.get() 旧接口：** SQLAlchemy 废弃接口，持续日志警告 | ✅ 已修复 — 全部替换为 `db.get(Model, id)` |
| 20 | 性能 | runs.py:298 | **SSE 轮询频繁：** 每 0.1s 查 DB，多客户端时开销大 | ✅ 已修复 — 移除 0.1s sleep；DB 回退仅在无 pubsub 消息时查询 |

## 四、测试覆盖缺口

| # | 场景 | 现状 |
|---|------|------|
| 21 | SSE `/runs/{id}/stream` 端点（含 Redis/DB 两种模式） | ✅ 4 个测试覆盖 headers / DB轮询 / 不存在 / Redis pubsub |
| 22 | `_call_target_system` 网络异常 / 超时处理 | ✅ 10 个测试覆盖连接错误 / 超时 / HTTP 错误 / SSRF / 回退 |
| 23 | Report 生成 API | ✅ 4 个测试覆盖生成 / 获取 / 列表 / 未完成时拒绝 |
| 24 | 创建 Run 时 target_url 传递 | ✅ 5 个测试覆盖 SSRF 防护：localhost / 私有 IP / file 协议 / 回环 / DNS |
| 25 | 前端组件渲染测试 | ✅ 15 个测试覆盖 ErrorBoundary / FailurePieChart / CompareTable / TraceTree / Layout |

## 五、已修复 / 不存在的问题

以下 problem.md 列出的问题**当前代码中已不存在**：

| # | 原描述 | 当前状态 |
|---|--------|----------|
| ~~2~~ | Token 双倍计算（eval_overhead 重复计入 input_tokens） | ❌ 变量不存在，计数正确 |
| ~~4~~ | `if False` 硬编码导致 total_tokens 永远 N/A | ❌ 无 `if False`，计算正常 |
| ~~5~~ | Dataset 校验查 EvalCase 表而非 Dataset 表 | ❌ 已正确使用 `db.get(Dataset, ...)` |
| ~~7~~ | API Key 明文拼入缓存 Key | ❌ 已用 SHA-256 哈希 |
| ~~8~~ | 全局缓存无锁 | ❌ `threading.Lock()` 已存在且正确使用 |
| ~~10~~ | SSE 按 status==='running' 过滤导致监听没挂上 | ❌ 当前不按 running 过滤 |
| ~~11~~ | "Add Case" 和 "Import JSON" 共享 Form 实例 | ❌ 三个独立 form |
| ~~12~~ | 外键缺少索引 | ❌ Trace.run_id/case_id/TraceStep.trace_id_link 均有 index |
| ~~13~~ | 测试用 sys.path.insert | ❌ 不存在 |
| ~~16~~ | EvalResult 缺少 (run_id, case_id) 唯一约束 | ❌ `UniqueConstraint` 已存在 |
| ~~17~~ | 错误截断 `str(e)[:200]` 过短 | ❌ 实际为 `[:1000]` |

---

## 已完成 (Phase 1)

- [x] FastAPI 后端 + SQLAlchemy + SQLite (8 张表全部定义)
- [x] Dataset / EvalCase CRUD API (含 JSON 导入)
- [x] Version 管理 API (prompt/model/retriever/agent 版本)
- [x] Run 管理 API (创建 + 进度 + 结果查询)
- [x] Dashboard Stats API
- [x] API Key 管理 API (5 种提供者: DeepSeek/OpenAI/DashScope/Anthropic/Ollama)
- [x] DeepEval Runner (Demo 规则评分 + 真实 LLM 评分双模式)
- [x] Celery 任务框架 (惰性初始化, Redis 不可用时自动降级)
- [x] React 前端骨架 (Vite + TypeScript + Ant Design)
- [x] Dashboard 页面 (KPI 卡片 + 最近 Run 列表)
- [x] Dataset/Case 管理页面 (CRUD + JSON 批量导入)
- [x] Experiment 配置页面 (模型选择 + API Key 设置 + 启动评测)
- [x] Run Results 页面 (Run 列表 + 详情弹窗含指标得分)
- [x] Settings 页面 (API Key 管理 + 提供者列表)
- [x] 真实 DeepSeek 评测验证通过 (4 项指标, 每项含自然语言评分理由)

---

## Phase 2: Trace 链路追踪

### 后端

- [x] **Trace 自动记录** (`services/tracer.py`)
  - 评测过程中自动创建 Trace 和 TraceStep
  - 记录步骤: target_call → evaluation (含 input/output/latency/tokens/error)
  - 参考 Phoenix 的 SpanKind 枚举: LLM / CHAIN / TOOL / RETRIEVER / RERANKER / EMBEDDING / AGENT

- [x] **Trace API** (`routers/traces.py`)
  - `GET /api/traces` - 列表 (按 run_id/status 筛选)
  - `GET /api/traces/{trace_id}` - 完整链路含 steps 树
  - `GET /api/runs/{id}/traces` - 某个 Run 的所有 Trace

### 前端

- [x] **Trace 详情页** (`pages/TraceDetail.tsx`)
  - 用户输入 → target_call → evaluation → Score
  - ECharts 步骤耗时瀑布图
  - Token 消耗统计
  - 错误堆栈展示

- [x] **TraceTree 组件** (`components/TraceTree.tsx`)
  - 树形展示嵌套步骤 (parent_step_id 构建层级)
  - 每步显示: step_name / step_type / latency / tokens / status
  - 点击展开查看 input/output JSON

---

## Phase 3: 版本对比

### 后端

- [x] **Run Compare API** (`routers/runs.py` 加 `/compare` 端点)
  - `GET /api/runs/compare?run1=X&run2=Y`
  - 返回:
    - 两个 Run 的各指标差异 (avg_score/faithfulness/recall/latency/cost)
    - Improved Cases 列表 (run2 比 run1 好的)
    - Regressed Cases 列表 (run2 比 run1 差的)
    - 自动生成的对比结论

### 前端

- [x] **版本对比页** (`pages/VersionCompare.tsx`)
  - 选两个 Run 的下拉框
  - 指标对比雷达图
  - Improved / Regressed Case 表格
  - 成本/延迟变化对比

- [x] **CompareTable 组件** (`components/CompareTable.tsx`)
  - 两列并排，高亮差异

---

## Phase 4: 失败归因 + 自动化报告

### 后端

- [x] **失败分类器** (`services/classifier.py`)
  - 规则分类:
    - `retrieval_miss` - reference_context_id 不在 Top-K
    - `low_context_precision` - 召回了大量无关内容
    - `evidence_ignored` - 证据被召回但 Faithfulness 低
    - `prompt_constraint_violation` - 格式/长度不符合要求
    - `tool_selection_error` - expected_tool != actual_tool
    - `tool_argument_error` - expected_args != actual_args
    - `hallucination` - Faithfulness < 阈值且回答包含检索中没有的信息
    - `timeout` - latency > 阈值
    - `high_cost` - tokens > 阈值
  - LLM Judge 补充 (调用 DeepEval 做归因)

- [x] **报告生成器** (`services/report_generator.py`)
  - Markdown 模板:
    - Summary (通过率、各指标平均分)
    - 各类型 Case 通过率
    - 失败原因分布 (饼图数据)
    - Top 退化 Case
    - Top 提升 Case
    - 成本变化 / 延迟变化
    - 优化建议 (LLM 生成)

- [x] **Report API** (`routers/reports.py`)
  - `POST /api/runs/{id}/report` - 生成报告
  - `GET /api/reports/{id}` - 查看报告 (markdown + summary_json)

### 前端

- [x] **报告查看页** (`pages/ReportView.tsx`)
  - Markdown 渲染
  - 失败分布饼图

- [x] **FailurePieChart 组件** (`components/FailurePieChart.tsx`)
  - ECharts 饼图: 失败原因分类占比

---

## 质量提升 (贯穿各 Phase)

### 评测 Runner

- [x] **对接真实 RAG/Agent 系统**
  - `_call_target_system` 通过 HTTP POST 调用目标系统 API
  - Frontend ExperimentConfig 支持配置 target_url / target_type / target_headers / target_timeout
  - 无 target_url 时自动 fallback 到 demo 模式

- [x] **真正的并发运行**
  - ThreadPoolExecutor 按 config_json.concurrent 并发执行 case

- [x] **评测进度实时推送**
  - SSE endpoint `GET /api/runs/{run_id}/stream` + Redis Pub/Sub
  - Frontend EventSource 替换 setInterval 轮询
  - Redis 不可用时自动降级为 DB 轮询

- [x] **Token 成本统计**
  - tiktoken 精确计数 (o200k_base / cl100k_base)
  - 统计 input_tokens / output_tokens 并展示

### 前端

- [x] **ECharts 图表** (Trace 瀑布图, Compare 雷达图, Failure 饼图)
  - Dashboard: KPI 卡片 + 最近 Run 列表
  - Trace: 步骤耗时瀑布图
  - Compare: 指标对比雷达图
  - Report: 失败原因饼图

- [x] **前端通过 Axios 错误拦截器统一处理异常**
  - client.ts 已配置全局 500/401/403 拦截 + 后端断连提示

- [x] **分页优化**
  - Ant Design Table 的 server-side pagination (Report 列表等)
  - 支持 limit/skip 参数

- [x] **Run 状态轮询**
  - SSE 实时推送替代定时轮询，完成后自动加载结果

- [x] **日志系统** (`services/logger.py`)
  - RotatingFileHandler + Console handler
  - 分级: DEBUG/INFO/WARNING/ERROR/CRITICAL
  - 日志查询 API: `GET /api/dashboard/logs?tail=100`

- [x] **sys.path hack 清理**
  - 移除 main.py 和 celery_app.py 中的 sys.path.insert

- [x] **failure_reason 扩容**
  - String(100) → Text，支持长错误信息

- [x] **测试覆盖**
  - 42 个测试: test_runner (15), test_classifier (13), test_tracer (10), test_runs_api (1), test_provider_models_api (3)

### 数据库

- [ ] **从 SQLite 升级到 PostgreSQL** (生产部署时)
  - 只需改 `database_url` 配置 + `pip install psycopg2`
  - 模型层无需修改 (SQLAlchemy 自动适配)

- [x] **Redis 启动后的 Celery 验证**
  - Celery Worker 成功连接 Redis，任务队列端到端跑通
  - Redis 不可用时自动降级为同步线程模式

### Demo 数据

- [x] **内置 Demo 评测集**
  - 20 条 QA Case (easy/medium/hard 三档)
  - 20 条 RAG Case (含 reference_context_ids)
  - 10 条 Tool Calling Case (含 expected_tool/expected_args)
  - 5 条 Multi-turn Case
  - JSON 文件位于 `demo_data/`，启动时自动 seed

---

## Phase 5: 安全加固 (2026-05-10)

### 后端

- [x] **SSRF 防护 — ValidatingHTTPAdapter** (`services/url_safety.py`)
  - 自定义 requests TransportAdapter，在 TCP 连接前解析 DNS、校验 IP
  - 锁定解析结果，消除 DNS rebinding / TOCTOU 攻击窗口
  - 替换 `celery_app.py` 中的普通 HTTPAdapter

- [x] **第二条 SSRF 路径修复** (`services/provider_models.py`, `routers/config_router.py`)
  - `_http_json_get` 函数注入 IP 校验和锁定（覆盖 urllib 路径）
  - `set_api_key` 写入 base_url 时前置校验

- [x] **API Key 加密存储** (`core/crypto.py`, `models/apikey.py`)
  - 新增 `core/crypto.py` — Fernet 对称加密密钥管理（自动生成 + 持久化到 `.env`）
  - `_encode`/`_decode` 从 Base64 替换为 `cryptography.fernet`
  - 新增依赖 `cryptography>=42.0.0`

- [x] **CORS 安全配置** (`main.py`, `config.py`)
  - 新增 `cors_origins` 配置项（逗号分隔，默认 `*`）
  - `*` 时自动移除 `allow_credentials=True`
  - 生产环境可从环境变量配置白名单

- [x] **Demo 模式数据隔离** (`services/report_generator.py`, `frontend`)
  - 报告顶部标注 target_mode / evaluation_mode
  - Demo 模式报告加警告横幅
  - 前端 Run 列表和详情弹窗增加 DEMO/LIVE 标签

- [x] **异常吞没清理** (`celery_app.py`, `classifier.py`)
  - `_publish_progress`、`classify_with_llm` 改为 `logger.warning(exc_info=True)`
  - 并发循环中未预期崩溃标记 EvalResult 为 error 状态

- [x] **Alembic 迁移体系** (`migrations/`, `main.py`)
  - 初始化 Alembic 并生成初始迁移
  - 移除 `main.py` 中的 `create_all()`

- [x] **健康检查修复** (`dashboard.py`)
  - `inspect().ping()` 真实验证 Celery worker 连通性

- [x] **日志内存优化** (`dashboard.py`)
  - `_tail_file()` 从文件末尾反向读取代替 `readlines()`

- [x] **静默 Fallback 修复** (`provider_models.py`, `config_router.py`)
  - 移除吞异常的 try-except，失败直接返回 502

- [x] **SSE 丢消息修复** (`runs.py`)
  - 先 subscribe 再 poll，消除消息丢失窗口
  - 移除冗余 0.1s sleep，DB 回退仅在无消息时查询

- [x] **类型注解修复** (`schemas/dataset.py`)
  - `reference_context_ids` 和 `tags` 裸 `list` → `list[str]`

- [x] **Version 删除级联** (`models/run.py`, `routers/versions.py`)
  - FK 加 `ondelete="SET NULL"` + `IntegrityError` 兜底

- [x] **Query.get() 迁移** (6 个路由文件)
  - 全部替换为 `db.get(Model, id)`

- [x] **前端 UX 修复** (`Settings.tsx`, `RunResults.tsx`)
  - Modal 字段在关闭时 reset
  - 详情弹窗 loading 先于 modal open
