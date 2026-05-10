# LLMOps 评测平台 — 面试完全解析

## 一、项目概况

### 一句话描述
一个面向 RAG/Agent 系统的自动化评测与可观测性平台，支持**多模型横向对比、自动化评分、链路追踪、失败归因、版本回归检测**。

### 目标用户
- AI 应用开发者：想知道换了模型/提示词后效果是变好还是变差
- RAG 系统维护者：需要量化检索链路的质量（召回率、精度）
- 算法工程师：需要对比不同模型在同一批测试集上的表现

### 解决的核心问题
| 痛点 | 解决方案 |
|------|----------|
| 人工评测慢且主观 | 自动化 LLM-as-Judge 评分 + 规则评分双模式 |
| 效果变化不可见 | 版本对比页面 + 雷达图 + Case 级 diff |
| 失败原因不明确 | 9 种自动归因分类 |
| 调试困难 | Trace 链路追踪 + 步骤级耗时/Tokens |
| 模型选择无依据 | 多 Provider 横向评测 |

---

## 二、系统架构

```
┌──────────────────────────────────────────────────────┐
│                   Frontend (React + Ant Design)       │
│  Dashboard / Dataset / Experiment / RunResults        │
│  VersionCompare / TraceDetail / ReportView / Settings │
└──────────────────┬───────────────────────────────────┘
                   │ HTTP / SSE
┌──────────────────▼───────────────────────────────────┐
│              Backend (FastAPI + SQLAlchemy)           │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Datasets │ │ Versions │ │  Runs   │ │ Config │  │
│  │   CRUD   │ │   CRUD   │ │Create/   │ │ APIKey │  │
│  │          │ │          │ │Stream/   │ │ Mgmt   │  │
│  │          │ │          │ │Compare   │ │        │  │
│  └──────────┘ └──────────┘ └────┬─────┘ └────────┘  │
│  ┌──────────┐ ┌──────────┐     │                     │
│  │ Traces   │ │ Reports  │     │                     │
│  │ API      │ │   API    │     │                     │
│  └──────────┘ └──────────┘     │                     │
└────────────────────────────────┼─────────────────────┘
                                 │
┌────────────────────────────────▼─────────────────────┐
│              Evaluation Engine                        │
│  ┌──────────────────────────────────────────────┐    │
│  │  Celery / ThreadPoolExecutor                 │    │
│  │  ┌──────────┐  ┌──────────┐  ┌───────────┐  │    │
│  │  │_call_    │  │_run_demo │  │_run_      │  │    │
│  │  │target_   │  │(规则评分)│  │deepeval   │  │    │
│  │  │system    │  │          │  │(LLM评分)  │  │    │
│  │  └──────────┘  └──────────┘  └───────────┘  │    │
│  └──────────────────────────────────────────────┘    │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐             │
│  │ Tracer  │ │Classifier│ │ReportGen │             │
│  └──────────┘ └──────────┘ └──────────┘             │
│  ┌──────────┐ ┌──────────────────┐                  │
│  │  Redis  │ │  ValidatingHTTP  │                  │
│  │ Pub/Sub │ │  Adapter(SSRF)   │                  │
│  └──────────┘ └──────────────────┘                  │
└────────────────────────────────────────────────────┘
```

### 分层说明

**1. API 层（Routers）** — 8 个路由模块：
- `datasets.py`: 数据集 + 评测用例 CRUD + JSON 批量导入
- `versions.py`: 版本管理（prompt/model/retriever/agent 四类）
- `runs.py`: 评测运行创建、SSE 实时流、进度查询、对比
- `dashboard.py`: 统计 + 健康检查 + 日志查询
- `config_router.py`: Provider 配置 + API Key 管理
- `traces.py`: Trace 链路查询
- `reports.py`: 报告生成与查询

**2. 业务层（Services）**：
- `runner.py`: 评测引擎核心（规则评分 + DeepEval 评分）
- `classifier.py`: 失败归因分类器
- `tracer.py`: 链路追踪记录器（类似 Phoenix SpanKind）
- `url_safety.py`: SSRF 防护（自定义 TransportAdapter）
- `provider_models.py`: 各 Provider 模型列表拉取（含缓存）
- `report_generator.py`: Markdown 报告生成

**3. 任务层（Tasks）**：`celery_app.py` — 评测任务编排，Redis 可用时走 Celery，不可用时降级为同步线程

---

## 三、数据库设计（8 张表）

| 表 | 作用 | 核心字段 |
|----|------|---------|
| `datasets` | 数据集 | name, case_type, description |
| `eval_cases` | 测试用例 | input, reference_answer, expected_tool/args, reference_context_ids, tags, difficulty |
| `versions` | 版本配置 | version_type(prompt/model/retriever/agent), config_json |
| `eval_runs` | 评测运行 | status, total/passed/failed_cases, avg_score/latency/tokens, config_json |
| `eval_results` | 每个 case 的评测结果 | status, scores, failure_reason, latency/tokens |
| `traces` | Trace 链路头 | trace_id, user_input, model, status, total_latency/tokens |
| `trace_steps` | Trace 步骤 | step_name/type, parent_step_id, input/output_json, latency/tokens, error |
| `reports` | 生成的报告 | report_markdown, summary_json |

### 关键设计决策

**Q: 为什么 EvalResult 要有 (run_id, case_id) 唯一约束？**
> 防止同一个 Run 中同一个 Case 被重复评测。并发场景下 ThreadPoolExecutor 提交多个任务，如果没有唯一约束，可能产生重复结果行。

**Q: Trace 和 TraceStep 为什么用 trace_id（字符串）而不是 id（整数）做外键？**
> trace_id 用 UUID hex 生成，可以在创建 Trace 前就知道 ID，方便在调用链中传递。如果用自增 id，需要 flush 后才能拿到。

**Q: 为什么 Version 的 FK 用 ondelete="SET NULL"？**
> 版本被删除时，相关联的 Run 不应该被级联删除（业务上需要保留历史记录）。SET NULL 让 Run 的 version_id 置空，保留运行记录。这就是 TODO 中的#15 修复。

---

## 四、核心工作流：一次评测的完整生命周期

### Step 1: 创建 Run

```
用户前端 → POST /api/runs
  ├─ 校验 dataset 存在
  ├─ 校验 target_url（SSRF 检查）
  ├─ 创建 EvalRun 记录（status=pending）
  └─ 调用 run_evaluation(run.id)
```

### Step 2: 任务分发

```
run_evaluation()
  ├─ 尝试 Celery 异步执行
  │   └─ evaluate_run_task.delay(run_id)
  └─ Celery 不可用时降级
      └─ threading.Thread(target=run_evaluation_task)
```

### Step 3: 评测执行

```
run_evaluation_task(run_id)
  ├─ 1. 标记 run.status = "running"
  ├─ 2. 发布进度到 Redis Pub/Sub
  ├─ 3. 从 dataset 加载所有 cases
  ├─ 4. 为每个 case 创建 EvalResult（status=pending）
  ├─ 5. ThreadPoolExecutor 并发执行
  │   └─ _evaluate_single_case() 每个 case：
  │       ├─ Tracer.start_trace() ← 创建 Trace
  │       ├─ tracer.step("target_call") ← 调用目标系统
  │       │   └─ _call_target_system()
  │       │       ├─ 有 target_url → HTTP POST（SSRF 防护）
  │       │       └─ 无 target_url → _simulate_output（Demo）
  │       ├─ tracer.step("evaluation") ← LLM 评分
  │       │   └─ run_case_evaluation()
  │       │       ├─ 有 API Key → _run_deepeval（真实 LLM）
  │       │       └─ 无 API Key → _run_demo（规则评分）
  │       ├─ 评分未全通过 → _classify_failure()
  │       ├─ Tracer.end_trace()
  │       └─ 每次完成 → _publish_progress()
  ├─ 6. 汇总：avg_score / avg_latency / avg_tokens
  ├─ 7. run.status = "completed"
  └─ 8. 最终进度推送（SSE 连接收到完成信号）
```

### Step 4: 前端接收

```
SSE endpoint (GET /api/runs/{id}/stream)
  ├─ Redis 可用 → Redis Pub/Sub 实时推送
  └─ Redis 不可用 → DB 轮询（每秒）

前端 EventSource → 实时更新进度条
完成后自动加载结果列表
```

### 面试追问要点

**Q: SSE 怎么保证不丢消息？**（#13 修复）
> 先 pubsub.subscribe()，再 DB 轮询获取初始状态。这样 subscribe 到首次 get_message 之间不会漏掉消息。每次 get_message 超时后（1s），用 DB 轮询兜底。

**Q: 并发评测怎么保证线程安全？**
> 每个 _evaluate_single_case 内部创建独立的 DB Session（SessionLocal），不共享 session。这就是为什么函数内部重新 import SessionLocal 而不是用参数传入。

**Q: Token 怎么统计的？**
> 使用 tiktoken 库精确计数（o200k_base 用于 GPT-4o 系列，cl100k_base 用于 GPT-4/DeepSeek 等）。不可用时 fallback 到 len(text)//3 估算。

---

## 五、评测体系设计

### 评分模式

| 模式 | 条件 | 方法 | 适用场景 |
|------|------|------|---------|
| Demo 规则评分 | 无 API Key | 关键词重叠 / Bigram 重叠 / 精确匹配 | 快速验证、无 LLM 成本 |
| 真实 LLM 评分 | 有 API Key | DeepEval + LLM-as-Judge | 生产级评测 |

### 指标矩阵

| Case 类型 | 指标 | 说明 |
|-----------|------|------|
| qa | AnswerRelevancy, Correctness | 回答相关性和准确性 |
| rag | Faithfulness, AnswerRelevancy, ContextRecall, ContextPrecision | RAG 四维评估 |
| tool_calling | ToolCorrectness, ArgumentAccuracy | 工具选择和参数准确性 |
| multi_turn | TaskCompletion | 多轮任务完成度 |

### Demo 模式评分原理（面试高频）

Demo 模式不调用 LLM，而是用文本分析做近似评估：

```
Faithfulness:    输出词汇 ∩ 上下文词汇 / 输出词汇总数
AnswerRelevancy: 问题词汇 ∩ 回答词汇 / 问题词汇总数
Correctness:     回答与参考答案的 Bigram 重叠率
ContextRecall:   参考答案词汇 ∩ 上下文词汇 / 参考答案词汇总数
ToolCorrectness:  actual_tool == expected_tool ? 1.0 : 0.0
```

**Q: Demo 评分和真实 LLM 评分差距大吗？**
> Demo 模式只能做粗略估算（基于词汇重叠），无法理解语义。比如回答"太阳从西边升起"和"太阳从东边升起"关键词高度重叠，Demo 判断为高分，LLM 能识别出这是错的。Demo 模式适用于开发调试，生产评测建议使用真实 LLM 评分。

### 失败归因（9 种分类）

| 类别 | 触发条件 |
|------|---------|
| hallucination | Faithfulness < 0.5 且输出包含参考中不存在的信息 |
| retrieval_miss | ContextRecall < 0.5（期望有上下文但没召回） |
| low_context_precision | 召回上下文多但不相关（ContextPrecision < 0.3） |
| evidence_ignored | 召回好（Recall>0.6）但生成不基于上下文（Faithfulness<0.5） |
| tool_selection_error | 调用了错误的工具 |
| tool_argument_error | 工具选对了但参数错误 |
| timeout | 延迟 > 30s |
| high_cost | Token > 10000 |
| prompt_constraint_violation | 输出含拒绝回答标记或过短 |

---

## 六、安全设计（Phase 5 重点）

### 1. SSRF 防护 — 核心难点

```
攻击场景：用户配置 target_url = "http://192.168.1.1/admin"
          如果服务器直接请求这个地址，就能访问内网资源。

更隐蔽的攻击：DNS Rebinding
          第一次解析 → 合法 IP（通过校验）
          第二次解析 → 内网 IP（实际请求）
          两次解析结果不同 → 绕过校验
```

**解决方案：自定义 ValidatingHTTPAdapter**

```python
# URL校验 → DNS解析 → IP校验 → 替换URL中的host为IP → 设置Host头 → 发送请求
def send(self, request, **kwargs):
    resolved_ip, original_host = resolve_and_validate(request.url)
    request.headers["Host"] = original_host  # 保留原始Host头
    request.url = request.url.replace(f"://{original_host}", f"://{resolved_ip}", 1)
    return super().send(request, **kwargs)
```

关键点：**在 TCP 连接前锁定 IP**，DNS 只解析一次，消除 TOCTOU 窗口。

### 2. API Key 加密

```
Base64 编码 → Fernet 对称加密（cryptography 库）

变更理由：Base64 不是加密，数据库泄露直接暴露明文密钥。
Fernet 使用 AES-128-CBC + HMAC-SHA256，密钥存环境变量。
```

### 3. CORS 安全

```python
# 危险组合：allow_origins=["*"] + allow_credentials=True
# 浏览器拒绝这种配置（规范要求）
# 修复：通配符时移除 credentials，白名单时保留
if origins != ["*"]:
    cors_kwargs["allow_credentials"] = True
```

---

## 七、可观测性设计

### Trace 链路（类似 Phoenix / OpenTelemetry）

每条评测用例生成一个 Trace，包含两个步骤：

```
Trace: case_001
  ├── step: "target_call" (CHAIN)
  │     input: {"query": "问题"}
  │     output: {"output": "回答片段"}
  │     latency: 350ms
  │     tokens: 120
  │
  └── step: "evaluation" (LLM)
        input: {"provider": "deepseek", "model": "deepseek-chat"}
        output: {"scores": {"Faithfulness": 0.85, ...}}
        latency: 850ms
        tokens: 450
```

SpanKind 枚举（参考 Phoenix）：
`LLM, CHAIN, TOOL, RETRIEVER, RERANKER, EMBEDDING, AGENT`

### 前端 Trace 树

TraceTree 组件展示步骤瀑布图：
- 每行显示步骤名 + 耗时进度条 + Tokens + 状态标签
- 点击展开查看 input/output JSON
- 子步骤缩进显示（parent_step_id 构建层级）

### 进度推送

两种模式自动切换：
```
Redis 可用: Redis Pub/Sub → SSE → 前端 EventSource
Redis 不可用: DB 轮询（每秒） → SSE → 前端 EventSource
```

---

## 八、前端架构

### 技术栈
React 19 + TypeScript + Ant Design 6 + ECharts 6 + react-router-dom 7

### 页面结构

| 路由 | 页面 | 功能 |
|------|------|------|
| `/` | Dashboard | KPI 卡片 + 最近运行列表 |
| `/datasets` | DatasetManagement | 数据集 CRUD + Case 管理 + JSON 导入 |
| `/experiment` | ExperimentConfig | 选择数据集/模型/配置 target_url → 启动评测 |
| `/runs` | RunResults | Run 列表 + 结果详情弹窗（含得分/模式标签） |
| `/compare` | VersionCompare | 两轮 Run 的雷达图对比 + Case 级别 diff |
| `/traces/:id` | TraceDetail | Trace 瀑布图 + 步骤树 + Token 统计 |
| `/reports` | ReportView | Markdown 报告 + 失败饼图 |
| `/settings` | Settings | API Key 管理（5 种 Provider） |

### 全局状态/工具

- **Axios 拦截器**：统一处理 500/401/403/网络异常，弹出国际化提示
- **i18n**：中英文切换（localStorage 持久化）
- **ECharts 懒加载**：按需注册组件（Pie/Radar/Cartesian），减小 bundle

---

## 九、测试策略（85 个测试）

### 后端（70 个）

| 测试文件 | 数量 | 覆盖内容 |
|---------|------|---------|
| test_runner.py | 16 | Token 计数、Demo 评分（QA/RAG/Tool Calling）、并发 |
| test_classifier.py | 14 | 9 种失败分类 + 边缘情况 |
| test_tracer.py | 10 | Trace 创建/步骤/延迟/错误/层级 |
| test_call_target.py | 10 | 网络异常/超时/HTTP 错误/SSRF/回退 |
| test_sse.py | 4 | SSE headers/DB轮询/404/Redis PubSub |
| test_target_url.py | 5 | SSRF 防护验证 |
| test_runs_api.py | 2 | Run 创建 + compare 路由 |
| test_report_api.py | 4 | 报告生成/获取/列表 |
| test_provider_models_api.py | 3 | 模型列表/缓存/回退 |

### 前端（15 个）

| 组件 | 测试数 | 覆盖 |
|------|--------|------|
| ErrorBoundary | 2 | 正常渲染、异常捕获 |
| FailurePieChart | 2 | 空数据、图表渲染 |
| CompareTable | 2 | 空列表、数据行 |
| TraceTree | 6 | 空/渲染/状态标签/展开/子步骤 |
| AppLayout | 3 | 菜单/标题/语言切换 |

---

## 十、面试高频追问（附回答）

### 项目设计类

**Q: 为什么不用现成的评测框架（如 LangSmith、Phoenix）而要自己开发？**
> 评测框架通常只提供在线评测服务，本项目需要：
> 1. 离线批量评测 + Demo 模式（无 API Key 也能跑）
> 2. 自定义测试用例管理（Dataset/Version 体系）
> 3. 对接内部 RAG 系统（target_url 机制）
> 4. 深度集成的失败归因和报告生成

**Q: 支持哪些模型 Provider？怎么扩展新的？**
> 目前支持 DeepSeek、OpenAI、DashScope、Anthropic、Ollama。添加新 Provider 只需要在 PROVIDER_META 添加配置，在 provider_models.py 加一个 fetch 函数，在 runner.py 加模型实例化逻辑。

**Q: 怎么保证评测结果的可复现性？**
> 每个 Run 记录完整的 config_json（provider、model、target_url、concurrency），Version 管理记录每次 prompt/model/retriever 的版本快照，可以精确回溯某次评测的全部参数。

### 技术实现类

**Q: ThreadPoolExecutor 并发评测，数据库连接怎么管理的？**
> 每个线程创建独立的 SQLAlchemy Session（`SessionLocal()`），互不共享。这是 SQLite 下使用多线程的关键 — SQLite 默认不支持跨线程共享连接。

**Q: SSE 和 WebSocket 比有什么优缺点？**
> 优点：基于 HTTP 协议，兼容性好，不需要升级协议；前端用 EventSource API 即可，比 WebSocket 简单。缺点：单向（只能服务器→客户端）；浏览器对 EventSource 有连接数限制（通常 6 个）；不支持二进制消息。本场景只需要服务器推送进度，SSE 完全够用。

**Q: 为什么用 Celery 而不是直接用 ThreadPoolExecutor？**
> Celery 提供任务队列、重试、持久化、分布式执行等能力。当 Redis 可用时，评测任务可以交给 Celery Worker 异步执行，不阻塞 API 响应。Redis 不可用时自动降级为 ThreadPoolExecutor，保证基本可用。

**Q: Redis 不可用时降级到 DB 轮询，性能怎么样？**
> SSE 的 DB 轮询间隔 1 秒（之前是 0.1s，已修复），每个轮询做 3 次简单 count 查询，对 SQLite 压力很小。多客户端时各自独立轮询，但实测几十个客户端同时查看进度也不会有性能问题，因为 SQLite 的读并发表现良好。

### 调试排查类

**Q: 如果一个 Case 评分不准，你会从哪些角度排查？**
> 1. 先看 Trace 中的 target_call 输出是否正常返回
> 2. 看 evaluation step 的 scores 详情和 reason
> 3. 检查该 Case 是否有 API Key（是 Demo 评分还是真实 LLM 评分）
> 4. 看 classifier 给出的 failure_reason
> 5. 如果是 RAG Case，检查 reference_context_ids 是否正确配置

**Q: 如果需要评测流式输出（Streaming）的 RAG 系统，现在的架构支持吗？**
> 当前的 `_call_target_system` 是同步 HTTP POST 等待完整响应。如果要评测流式输出，需要改造为：发送请求后从 SSE/Stream 收集完整内容再评估。架构上可以加一个 `target_stream` 参数来区分流式和非流式。

**Q: Demo 评分和真实 LLM 评分的结果不一致怎么办？**
> 这是预期的。Demo 评分基于词汇重叠，本质是近似估算；DeepEval 的 LLM-as-Judge 能理解语义。建议做法：开发阶段用 Demo 快速迭代，上线前用真实 LLM 跑一次完整评测作为基准。

---

## 十一、可能的改进方向（面试可以说）

| 方向 | 说明 |
|------|------|
| **持续评测流水线** | 集成到 CI/CD，每次模型/知识库更新自动触发评测 |
| **多模态评测** | 支持图片/表格/代码的检索和生成质量评估 |
| **A/B 测试** | 同一批 Case 对两个不同配置同时评测，统计显著性检验 |
| **用户反馈闭环** | 收集实际用户的反馈（点赞/点踩），纳入评测指标 |
| **Prompt 版本管理** | 更细粒度的 Prompt Diff 和效果追踪 |
| **知识库覆盖率** | 评测当前知识库对常见问题的覆盖程度，发现知识盲区 |

---

## 附录：核心文件索引

| 文件路径 | 作用 | 关键行数 |
|---------|------|---------|
| `backend/app/main.py` | FastAPI 入口，CORS 配置，注册路由 | 58 行 |
| `backend/app/tasks/celery_app.py` | 评测任务编排，Celery + 降级 | 473 行 |
| `backend/app/services/runner.py` | 评测引擎，Demo + DeepEval 双模式 | 384 行 |
| `backend/app/services/classifier.py` | 9 种失败归因分类 | 166 行 |
| `backend/app/services/tracer.py` | Trace 链路追踪（SpanKind） | 118 行 |
| `backend/app/services/url_safety.py` | SSRF 防护（ValidatingHTTPAdapter） | 129 行 |
| `backend/app/services/provider_models.py` | Provider 模型列表 API | 229 行 |
| `backend/app/services/report_generator.py` | Markdown 报告生成 | 295 行 |
| `backend/app/routers/runs.py` | SSE 流、对比、CRUD | 340 行 |
| `backend/app/core/crypto.py` | Fernet 加密密钥管理 | 66 行 |
| `backend/app/seed.py` | Demo 数据自动导入 | 78 行 |
| `backend/app/models/` | 8 张 SQLAlchemy 模型定义 | 各 ~50 行 |
| `frontend/src/__tests__/components.test.tsx` | 15 个前端组件测试 | ~270 行 |
| `tests/` | 70 个后端测试（9 个文件） | 各 50-200 行 |
