# LLMOps 评测与 Trace 监控平台 — 面试准备文档

## 一、项目一句话介绍

> 面向 RAG 问答与 Agent 应用构建自动化评测平台，支持真实系统接入、并发评测、Trace 链路追踪、失败归因和可视化监控。覆盖 QA / RAG / Tool Calling / Multi-turn Agent 四类场景，内置 55 条评测 Case，兼容 DeepSeek、OpenAI、DashScope、Anthropic、Ollama 五种模型提供方，支持 Demo 规则评分与 LLM Judge 双模式。

技术栈：FastAPI + Celery + Redis + Kafka + ClickHouse + SSE + SQLAlchemy + Alembic + DeepEval + Langfuse + React + TypeScript + Ant Design + ECharts + Docker

---

## 二、整体架构

```
┌─────────────┐    ┌──────────────┐    ┌─────────────────┐
│  React 前端  │───▶│  FastAPI 路由 │───▶│  Celery Worker   │
│  (看板/SSE)  │◀──│  (REST + SSE)│◀──│  (并发评测执行)   │
└─────────────┘    └──────┬───────┘    └──────┬──────────┘
                          │                    │
                   ┌──────▼──────┐    ┌───────▼──────────┐
                   │  SQLite/PG  │    │  Target System    │
                   │  (Run/Trace) │    │  (RAG/Agent HTTP)  │
                   └─────────────┘    └───────────────────┘
```

- **API 层**（`app/routers/`）：runs（评测任务 CRUD + SSE 流）、datasets（数据集管理）、traces（Trace 查询）、dashboard（看板聚合）、reports（报告生成）、versions（版本管理）、config（API Key 配置）、calibration（Judge 校准）
- **服务层**（`app/services/`）：runner（评测执行器）、classifier（失败归因）、tracer（Trace 记录）、llm_judge（LLM 裁判）、judge_calibration（裁判校准）、url_safety（SSRF 防护）、agent_metrics（Agent 轨迹评测）
- **任务层**（`app/tasks/celery_app.py`）：Celery 异步任务 + ThreadPoolExecutor 并发评测
- **模型层**（`app/models/`）：EvalRun / EvalResult / EvalCase / Dataset / Trace / TraceStep / Version / APIKey / Report

---

## 三、评测系统设计（核心）

### 3.1 四类评测场景 + 指标映射

| 场景类型 | 评测指标 | 说明 |
|---|---|---|
| `qa` | AnswerRelevancy, Correctness | 问答相关性 + 答案正确性 |
| `rag` | **Faithfulness, AnswerRelevancy, ContextRecall, ContextPrecision** | RAGAS 四件套 |
| `tool_calling` | ToolCorrectness, ArgumentAccuracy | 工具选择正确性 + 参数准确度 |
| `agent_trajectory` | TaskSuccess, ToolSelectionAccuracy, ArgumentAccuracy, StepEfficiency | Agent 执行轨迹评测 |

代码位置：[runner.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/runner.py#L22-L33) `METRIC_MAP`

### 3.2 双模式评测：Demo（规则） vs DeepEval（LLM Judge）

**选择逻辑**（[runner.py L426-468](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/runner.py#L426-L468)）：
1. `agent_trajectory` → 确定性指标，不走 DeepEval，直接调 `agent_metrics.evaluate_agent_trajectory`
2. 有 API Key → DeepEval 真实 LLM Judge 评测
3. 无 API Key → Demo 规则评分（降级，不报错）

**面试话术**：「我设计了双模式评测架构——有 LLM API Key 时走 DeepEval 的 RAGAS 指标（Faithfulness 等），没有 Key 时自动降级到基于规则的 Demo 评分，保证平台在离线/无 Key 环境下也能跑评测。Agent 轨迹评测的四个指标（TaskSuccess、ToolSelectionAccuracy 等）是确定性的，不需要 LLM 判断，所以单独走一套纯 Python 实现，不经过 DeepEval。」

### 3.3 Demo 模式的中文友好分词（技术亮点）

**问题**：RAGAS 的规则代理评分需要 token 覆盖率计算，但英文按空格分词对中文完全无效。

**解决**（[runner.py L141-157](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/runner.py#L141-L157)）：
- ASCII 词/数字：正则 `[a-z0-9][a-z0-9._+-]*` 提取
- CJK 字符：单字 `一-鿿` + **相邻二字组（bigram）**，让短中文术语也能匹配
- 停用词过滤：`_STOP_TERMS` 集合排除「什么/怎么/如何/解释/说明」等非内容词

**面试话术**：「RAGAS 的规则代理评分依赖 token 覆盖率，但中文没有空格分词。我做了一个中英混合分词器：ASCII 词按正则提取，中文字符取单字 + 相邻二字组（bigram），这样短的中文术语（如"糖尿病"→"糖尿"+"尿病"）也能匹配上。同时维护了中文停用词表排除问题词和功能词。」

### 3.4 各指标的计算逻辑

| 指标 | Demo 模式逻辑 | DeepEval 模式 |
|---|---|---|
| **Faithfulness** | 回答 token 覆盖检索上下文的比例 × 1.4（上限 1.0） | RAGAS FaithfulnessMetric |
| **AnswerRelevancy** | 0.2 + 0.4×问题词覆盖 + 0.4×参考词覆盖 | RAGAS AnswerRelevancyMetric |
| **Correctness** | 0.6×参考覆盖 + 0.4×必须关键词命中 | （Demo 独有） |
| **ContextRecall** | 参考答案 token 被检索上下文覆盖的比例 | RAGAS ContextualRecallMetric |
| **ContextPrecision** | 逐 chunk 相关性，**按排名位加权平均**（靠前的 chunk 权重更高） | RAGAS ContextualPrecisionMetric |
| **ToolCorrectness** | 工具名精确匹配 | DeepEval ToolCorrectnessMetric |
| **ArgumentAccuracy** | 参数键值匹配比例 | DeepEval（通过 ToolCall 对象） |

代码位置：[runner.py L167-291](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/runner.py#L167-L291)

### 3.5 Agent 轨迹评测（确定性指标，不走 LLM）

[agent_metrics.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/agent_metrics.py)

四个指标：
1. **TaskSuccess**：轨迹有 `success: true` / `status: completed` / 有 `final_answer` → 1.0
2. **ToolSelectionAccuracy**：期望工具调用序列中，实际匹配到的比例（贪心匹配，已匹配的不复用）
3. **ArgumentAccuracy**：期望工具的参数键值匹配比例
4. **StepEfficiency**：`max(0, 1 - 多余调用数 / 总调用数)`——实际比期望多几步扣几分

**面试话术**：「Agent 轨迹评测的核心是比'期望工具调用序列'和'实际工具调用序列'。我用贪心匹配（每个期望工具调用在实际序列中找第一个未用过的同名调用），分别算工具选择准确率和参数准确率。StepEfficiency 惩罚冗余调用——实际比期望多几步就扣几分。这些指标全是确定性的，不需要 LLM 判断，所以不经过 DeepEval。」

---

## 四、失败归因系统（9 类失败模式）

[classifier.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/classifier.py)

| # | 失败类别 | 触发条件 | 面试解释 |
|---|---|---|---|
| 1 | `hallucination` | Faithfulness < 0.5 **且** 输出含大量参考答案中没有的新词（>50%） | 模型编造了上下文中不存在的内容 |
| 2 | `retrieval_miss` | ContextRecall < 0.5（且有参考上下文 ID） | 检索没召回关键证据 |
| 3 | `low_context_precision` | ContextPrecision < 0.3 且检索 chunk ≥ 5 | 检索了大量无关 chunk |
| 4 | `evidence_ignored` | ContextRecall > 0.6 但 Faithfulness < 0.5 | 检索到了证据但模型没用 |
| 5 | `tool_selection_error` | 期望工具 ≠ 实际工具 | Agent 选错工具 |
| 6 | `tool_argument_error` | 工具对了但参数值不匹配 | Agent 选对工具但参数传错 |
| 7 | `timeout` | latency > 30,000 ms | 响应超时 |
| 8 | `high_cost` | input + output tokens > 10,000 | token 消耗异常 |
| 9 | `prompt_constraint_violation` | 输出含 "I cannot / as an AI" 等拒绝标记，或长度 < 10 字符 | 违反 prompt 约束 |
| - | `quality_issue` | 无规则匹配时的兜底 | 通用质量问题 |

**关键设计**：一个 case 可以命中多个失败类别（`classify` 返回 `list[str]`），不是单选。

**LLM 增强归因**（`classify_with_llm`）：先跑规则归因，再调 LLM Judge 补充，如果 LLM 发现了规则没覆盖的原因（如 `hallucination_llm`、`answer_irrelevant`），追加到结果里。

**面试话术**：「失败归因是评测平台的核心价值——不只是告诉你'没过'，还要告诉你'为什么没过'。我设计了 9 类失败模式，基于指标分数 + 输出特征做规则匹配，一个 case 可以同时命中多个类别。比如 Faithfulness 低 + 输出含大量新词 → hallucination；ContextRecall 高但 Faithfulness 低 → evidence_ignored（检索到了但模型没用）。还有 LLM 增强归因，用 LLM Judge 补充规则没覆盖的原因。」

---

## 五、Trace 链路追踪

[tracer.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/tracer.py)

### 5.1 SpanKind 类型

```python
SPAN_KINDS = ["LLM", "CHAIN", "TOOL", "RETRIEVER", "RERANKER", "EMBEDDING", "AGENT"]
```

### 5.2 缓冲写入模式（关键工程优化）

**问题**：SQLite 默认 WAL 模式下，一个打开的写事务会**持有写锁**直到事务结束。如果 Tracer 在 `start_trace` 时就写 DB，然后等目标系统 HTTP 调用（可能几十秒），写锁一直不释放，并发评测的其他 case 就会 `database is locked`。

**解决**：
- `start_trace`：只在内存创建 `Trace` 对象，**不写 DB**
- `step`（contextmanager）：在内存 list 中追加 `TraceStep`，用负数临时 ID 引用
- `set_step_output`：直接修改内存中的 step 对象
- `end_trace`：**一次性 flush**——把 Trace + 所有 TraceStep 写入 DB，写锁只持有几毫秒

**面试话术**：「Trace 记录的工程难点是 SQLite 的写锁——如果我在 trace 开始时就写 DB，然后去等目标系统的 HTTP 调用（可能几十秒），写锁就一直不释放，并发 case 全部 'database is locked'。我用缓冲写入模式：trace 和 step 先在内存里攒着，直到 end_trace 才一次性 flush 到 DB，写锁只持有几毫秒。这样 20 条并发评测不会互相阻塞。」

### 5.3 Trace 记录的内容

每个 Trace 记录：
- `trace_id`（16 位 UUID hex）、`run_id`、`case_id`、`user_input`
- `prompt_version`、`model`、`retriever_version`
- `total_latency_ms`（所有 step 延迟之和）、`total_tokens`（所有 step token 之和）
- `status`（success/failed）、`error_message`

每个 TraceStep 记录：
- `step_name`、`step_type`（SpanKind）、`parent_step_id`（支持树形层级）
- `input_json`（输入数据）、`output_json`（输出数据）
- `latency_ms`、`tokens`、`error_message`
- `order_index`（步骤顺序）

---

## 六、并发评测与 SSE 实时推送

### 6.1 并发评测架构

[celery_app.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/tasks/celery_app.py)

```
Celery Task (evaluate_run_task)
  └─ run_evaluation_task(run_id)
       ├─ 创建所有 EvalResult（status=pending）
       └─ ThreadPoolExecutor(max_workers=concurrency)
            └─ _evaluate_single_case(...)  ← 每个 case 独立 DB session
                 ├─ Tracer.start_trace
                 ├─ Tracer.step("target_call")  → 调目标系统
                 ├─ Tracer.step("evaluation")   → 跑评测指标
                 ├─ classify 失败归因
                 └─ Tracer.end_trace → flush
       └─ 每个 future 完成后 _publish_progress
```

**线程安全**：每个 case 用独立的 `SessionLocal()` DB session，不共享。

**降级策略**：
- Celery 不可用 → 降级为 `threading.Thread` 后台执行
- Redis 不可用 → SSE 降级为 DB 轮询

**面试话术**：「并发评测用 ThreadPoolExecutor，每个 case 在自己的线程里跑、用独立的 DB session。每完成一个 case 就通过 Redis Pub/Sub 推一次进度，前端 SSE 收到后更新进度条。Celery 不可用时自动降级为普通线程执行，Redis 不可用时 SSE 降级为 DB 轮询。20 条并发评测 < 30 秒。」

### 6.2 SSE 实时进度推送

```
前端 ←── EventSource ── FastAPI SSE endpoint ── Redis Pub/Sub ── Celery Worker
                         │ (Redis 不可用时)
                         └──── DB 轮询（每 2 秒查一次进度）
```

SSE 事件格式：
```json
{"run_id": 1, "status": "running", "total": 55, "completed": 30, "passed": 25, "failed": 5}
```

---

## 七、LLM Judge 与裁判校准

### 7.1 LLM Judge（轻量级，stdlib 实现）

[llm_judge.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/llm_judge.py)

三个 Judge 函数：
- `judge_correctness(question, answer, reference)` → `{score, reason}`
- `judge_faithfulness(question, answer, contexts)` → `{score, reason}`
- `judge_preference(question, answer_a, answer_b)` → `{preferred: "A"|"B"|"tie", reason}`

**设计亮点**：
- 用 `urllib.request`（stdlib）调 OpenAI-compatible Chat Completions，**零新依赖**
- 支持三种 provider：DashScope(qwen-plus) / DeepSeek(deepseek-chat) / OpenAI(gpt-4o-mini)，按环境变量自动选择
- 带 2 次重试 + 指数退避
- JSON 输出强制 `response_format: {"type": "json_object"}`
- 可选上报 Langfuse（judge 自身的 token 成本也进可观测）

### 7.2 裁判校准（Judge Calibration）

[judge_calibration.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/judge_calibration.py)

**规则评分 vs LLM Judge 的一致性度量**：

| 指标 | 含义 |
|---|---|
| `exact_agreement` | |rule - judge| ≤ 0.15 的比例 |
| `pass_fail_agreement` | 两侧对 pass/fail 判断一致的比例 |
| `mae` | 平均绝对误差 |
| `mean_bias` | mean(judge - rule)，正数 = judge 更宽松 |
| `spearman` | Spearman 秩相关（排序一致性） |
| `confusion` | pass/fail 混淆矩阵 (TP/FP/FN/TN) |

**位置偏差检测**（`position_bias_check`）：
- 对每对答案 A/B，调两次 judge：一次 A 在前、一次 B 在前
- 如果调换位置后偏好翻转 → 记一次 flip
- `flip_rate` = flips / checked，≤0.1 无偏差，≤0.3 轻度，>0.3 需要去偏差

**面试话术**：「LLM Judge 不是拿来就用——需要校准。我实现了规则评分和 LLM Judge 的一致性度量：exact agreement（误差 ≤ 0.15 的比例）、pass/fail 一致性、MAE、mean bias（判断 judge 偏宽还是偏严）、Spearman 秩相关。还做了位置偏差检测——对 pairwise preference，调两次 judge 交换 A/B 位置，如果偏好翻转了就是 position bias，flip_rate > 30% 就需要做去偏差（swap averaging）。」

---

## 八、安全设计

### 8.1 SSRF 防护 + DNS Rebinding 防护

[url_safety.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/services/url_safety.py)

**`validate_target_url`**：校验 target_url，拒绝 localhost / 私有 IP / 保留地址
**`ValidatingHTTPAdapter`**（TransportAdapter）：
1. DNS 解析 → 校验解析出的 IP 不是内网地址
2. **用解析出的 IP 替换 URL 中的 hostname**，原 hostname 放到 Host header
3. 防止 DNS rebinding：校验时解析的 IP = 连接时实际用的 IP
4. **白名单**：`LLMOPS_TARGET_ALLOWLIST` 允许信任的内网目标（如 `host.docker.internal`）绕过

**面试话术**：「评测平台需要 HTTP 调用用户的 target_url，这就有 SSRF 风险。我做了两层防护：第一层 validate_target_url 在请求前校验——拒绝 localhost 和私有 IP；第二层 ValidatingHTTPAdapter 是 requests 的 TransportAdapter，在连接前做 DNS 解析 + IP 校验，然后用解析出的 IP 替换 URL hostname（原 hostname 放 Host header），防止 DNS rebinding。同时维护一个白名单让内网信任的 agent 可以绕过。」

### 8.2 API Key Fernet 加密

[apikey.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/models/apikey.py) + [crypto.py](file:///f:/longchain/实习项目/llmops-platform/backend/app/core/crypto.py)

- API Key 存 DB 前用 `cryptography.fernet` 对称加密
- 读取时 `_decode()` 解密
- 支持 DB 优先 + 环境变量 fallback 的双层 Key 解析

---

## 九、CI 回归门控

[regression_gate.py](file:///f:/longchain/实习项目/llmops-platform/scripts/regression_gate.py)

**目的**：prompt/agent 改动后，自动对比 baseline run 和 candidate run，如果关键指标退化超阈值就让 CI 红。

**三个门控维度**：
1. `--max-avg-drop`（默认 0.05）：整体平均分下降不超过 5%
2. `--max-metric-drop`（默认 0.10）：单个指标平均下降不超过 10%
3. `--max-flips`（默认 0）：passed → failed 的 case 翻转数

**翻转分类**（处理 LLM run-to-run 噪声）：
- **hard flip**：分数从 ≥ 0.6 暴跌到 ≤ 0.6 - 0.15 = 0.45 → 真退化
- **edge flip**：分数在阈值附近波动的翻转 → 可容忍（默认容忍 5 个）

**面试话术**：「评测平台不只是跑一次看分数——它还要能在 prompt/agent 改动后做回归检测。我写了 CI 门控脚本，对比 baseline 和 candidate 两个 run：整体平均分下降 > 5% 或单指标下降 > 10% 就 fail。关键设计是对 passed→failed 翻转做分类——LLM 的 run-to-run 波动会让阈值附近的 case 来回翻转，这是噪声不是退化；只有分数从 0.6+ 暴跌到 0.45 以下的 hard flip 才算真退化。」

---

## 十、Trace 事件大数据管道（Kafka + ClickHouse）

### 10.1 动机与架构

trace/span 是典型的 append-only 时序事件流，评测规模上去之后（多团队 × 每天 N 轮 × 55 case × 多个 span），SQLite 明细聚合会越来越慢。对标 Langfuse v3（Kafka + ClickHouse）做了一条轻量 OLAP 管道：

```
Tracer.end_trace ──► Kafka(llmops.trace.events) ──► ch-worker 微批消费 ──► ClickHouse MergeTree
（SQLite 明细照常落库，管道全链路失败零影响）
```

- **写侧**（`services/trace_stream.py`）：end_trace 后 best-effort 发事件，`key=run_id` 保证 run 内分区有序；KafkaProducer 惰性初始化 + 失败短路
- **落库侧**（`analytics_worker.py`）：KafkaConsumer 手动提交 offset，微批 insert CH；写入失败不提交 offset、重启重放（at-least-once）；Kafka/CH 任一端不可用按固定间隔重试不退出
- **读侧**（`services/analytics.py` + `GET /api/dashboard/analytics`）：CH 可用时聚合下推（按天趋势 / uniqExact runs / quantile P95 延迟 / 按模型 / 按 span 类型），不可用时 SQLite 明细表同口径降级，`source` 字段标明数据来源

### 10.2 关键设计

| 设计点 | 说明 |
|---|---|
| 削峰解耦 | 评测 worker 不直接依赖 CH 可用性与写入吞吐；并发评测的高频事件先进 Kafka 缓冲 |
| at-least-once | 消费者手动 commit offset；CH 写失败不提交，重启后重放补数 |
| 分区有序 | `key=run_id`，同一 run 的事件进同一分区，run 内聚合口径一致 |
| 零侵入降级 | 未配置 `KAFKA_BOOTSTRAP_SERVERS`/`CLICKHOUSE_URL` 或库未安装时管道整体关闭，平台行为与原先完全一致（与 Redis/Celery 降级风格统一） |
| 列存 OLAP | MergeTree 按月分区 + `ORDER BY (run_id, created_at)`；P95 用 `quantile(0.95)` 下推到 CH 向量化执行 |

**面试话术**：「trace 数据是典型的时序事件流，量随评测次数线性涨。我把分析读路径从业务库拆出来：end_trace 后把事件发到 Kafka（key=run_id 保证 run 内有序），独立 worker 微批消费写 ClickHouse，offset 手动提交做到 at-least-once——CH 挂了不丢数据，恢复后重放补数。Dashboard 的高级分析接口优先查 CH（P95 延迟、失败率趋势、按模型/按 span 类型的聚合全部下推到列存），CH 不可用自动降级 SQLite 明细聚合，两种数据源同一套指标口径。这条链路对标 Langfuse v3 的 Kafka + ClickHouse 架构。」

### 10.3 涉及文件

| 文件 | 角色 |
|---|---|
| `services/trace_stream.py` | Kafka producer（惰性初始化 + best-effort） |
| `services/tracer.py` | end_trace 后发事件（唯一接入点） |
| `analytics_worker.py` | Kafka→CH 消费者（建表 + 微批 + offset 管理） |
| `core/clickhouse.py` | CH 客户端封装 + DDL（trace_events / trace_span_events 两张 MergeTree 表） |
| `services/analytics.py` | 聚合读侧（CH 优先 / SQLite 降级） |
| `routers/dashboard.py` | `GET /api/dashboard/analytics` + 健康检查上报管道状态 |

---

## 十一、测试覆盖

### 11.1 测试文件清单

| 文件 | 测试数 | 覆盖内容 |
|---|---|---|
| [test_classifier.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_classifier.py) | 14 | 9 类失败模式 + 多类同发 + 兜底 |
| [test_runner.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_runner.py) | 15 | token 计数、Demo 评分(QA/RAG/Tool)、集成评测 |
| [test_tracer.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_tracer.py) | 10 | Trace 生命周期、step 延迟/输出/错误/层级 |
| [test_trace_stream.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_trace_stream.py) | 4 | Kafka producer 降级、事件结构、run_id 分区键、发送失败不抛 |
| [test_analytics.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_analytics.py) | 5 | SQLite 降级聚合口径、P95、按模型/span 聚合、时间窗过滤 |
| [test_sse.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_sse.py) | 4 | SSE header/进度/错误/Redis PubSub |
| [test_agent_trajectory.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_agent_trajectory_metrics.py) | ~10 | Agent 轨迹四指标 |
| [test_runs_api.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_runs_api.py) | ~10 | Run API CRUD |
| [test_call_target.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_call_target.py) | ~5 | 目标系统调用 |
| [test_target_url.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_target_url.py) | ~5 | SSRF 防护 |
| [test_report_api.py](file:///f:/longchain/实习项目/llmops-platform/tests/test_report_api.py) | ~5 | 报告 API |
| 其他 | ~10+ | provider config / provider models |
| **合计** | **~85** | |

### 11.2 测试策略

- **单元测试**：每个 service 函数独立测试，用 `monkeypatch` mock 外部依赖
- **集成测试**：`test_single_case_eval_fails_when_target_omits_expected_tool` 用真实 SQLite + monkeypatch 模拟目标系统，验证完整评测链路
- **并发安全测试**：`test_demo_mode_run` 验证 ThreadPoolExecutor + 多 DB session
- **降级测试**：SSE Redis 不可用时降级 DB 轮询、Celery 不可用时降级线程；Kafka/CH 不可用时管道关闭、分析降级 SQLite

---

## 十二、面试追问 & 答法

### Q1: 为什么不直接用 DeepEval，还要自己写 Demo 模式？
> DeepEval 每次评测都要调 LLM API，成本高、速度慢、依赖网络。Demo 模式用基于规则的代理评分（token 覆盖率），零成本零延迟，适合本地开发和无 API Key 环境。两者指标名对齐（都叫 Faithfulness / ContextRecall 等），切换无感知。

### Q2: Trace 的缓冲写入为什么不直接用 PostgreSQL？
> SQLite 的 WAL 模式下打开的写事务会持锁。PostgreSQL 有行级锁不会有这个问题，但项目初期用 SQLite 更轻量。缓冲写入是对 SQLite 的工程适配——在 end_trace 时一次性 flush，写锁只持有几毫秒。如果迁移到 PG，这个模式也能工作（减少事务数对性能仍然有益）。

### Q3: 失败归因为什么用规则不用 LLM？
> 规则归因快、确定、零成本、可解释——Faithfulness < 0.5 就是幻觉风险，latency > 30s 就是超时，不需要 LLM 来判断。但规则有盲区，所以加了 `classify_with_llm` 补充：先跑规则，再用 LLM Judge 看有没有规则没覆盖的原因。两层结合。

### Q4: ContextPrecision 为什么要按排名位加权？
> RAGAS 的 contextual precision 原理是：相关 chunk 排在前面比排在后面更好。如果第 1 个 chunk 就高度相关，系统质量比第 5 个才相关好。所以用 `1/(i+1)` 做权重——第 1 个 chunk 权重 1.0，第 2 个 0.5，第 3 个 0.33...，加权平均后靠前的 chunk 影响更大。

### Q5: 位置偏差检测的 swap averaging 是什么？
> LLM 做 pairwise preference 时会有位置偏差——倾向选第一个出现的答案。检测方法：对同一对答案 A/B，调两次 judge（一次 A 在前、一次 B 在前），如果结果翻转了就是 position bias。去偏差方法：取两次结果的平均（如果两次都说 A 好就是 A，如果一次说 A 一次说 B 就是 tie）。

### Q6: 并发评测怎么保证 DB 不锁？
> 三个手段：1）每个 case 用独立的 `SessionLocal()` DB session；2）Tracer 用缓冲写入——trace 数据先在内存攒着，end_trace 时一次性 flush，写锁只持有几毫秒；3）`db.commit()` 在 case 评测完成后立即调用，不持有长事务。

### Q7: 回归门控的 hard flip / edge flip 区分有什么实际意义？
> LLM 的输出有 run-to-run 波动——同一个 case 可能这次 0.51 过了、下次 0.49 没过。如果把这当成退化，CI 就太吵了。只有分数从 0.6+ 暴跌到 0.45 以下的才是真退化（hard flip），阈值附近的翻转（edge flip）给一个容忍数（默认 5 个）。这样 CI 既有信号又不太吵。

### Q8: 为什么 trace 分析要引入 Kafka + ClickHouse，而不是直接查 SQLite/PG？
> trace/span 是 append-only 的时序事件流，量随评测次数线性增长。OLTP 库做大规模聚合（按天趋势、P95 延迟、按维度 group by）要走大量明细行扫描，而列存 OLAP 就是为此设计的——按列压缩 + 向量化执行，P95 直接下推 `quantile(0.95)`。引入 Kafka 是为了削峰解耦：并发评测时每个 case 结束都产生事件，评测 worker 不应该被分析库的写入吞吐/可用性反压。这是 Langfuse v3 的同款架构选型。

### Q9: at-least-once 会重复入库，你怎么处理？
> 消费者手动提交 offset：先把微批 insert 进 CH，成功了才 commit——CH 写失败就不提交，重启重放。代价是可能重复写入，两种处理：1）trace_id 在 CH 里天然可以做幂等聚合口径（uniqExact(trace_id) 去重计数）；2）严格要求幂等可以把表引擎换成 ReplicatedMergeTree + insert_deduplication，或消费前用 Bloom filter 判重。当前规模下选 1，成本最低。

### Q10: 管道挂了评测还能跑吗？
> 能。整条管道是 best-effort 的：producer 发送失败只记 warning 不抛异常；未配置 Kafka/CH 时管道整体关闭；读侧 CH 不可用自动降级 SQLite 明细聚合。评测主链路（HTTP 调 target → 指标计算 → SQLite 落库）完全不受影响——分析管道挂掉的代价只是 Dashboard 高级分析暂时没有 CH 数据，而不是评测停摆。

---

## 十三、简历可量化的数字

| 指标 | 数值 |
|---|---|
| 评测场景类型 | 4 类（QA / RAG / Tool Calling / Agent Trajectory） |
| 内置评测 Case | 55 条 |
| 评测指标 | 11 个（Faithfulness / AnswerRelevancy / ContextRecall / ContextPrecision / Correctness / ToolCorrectness / ArgumentAccuracy / TaskSuccess / ToolSelectionAccuracy / StepEfficiency / TaskCompletion） |
| 失败归因类别 | 9 类 |
| 支持的 LLM 提供方 | 5 个（DeepSeek / OpenAI / DashScope / Anthropic / Ollama） |
| 并发评测 | 20 条 < 30 秒 |
| 测试覆盖 | ~85 个核心模块测试 |
| 大数据管道 | Kafka(KRaft) 削峰 + ClickHouse 列存 OLAP，at-least-once 消费，P95 聚合下推 |
| 上下文工程 | 单次最高节省 84.5% token |
| 回归门控阈值 | avg_drop ≤ 5%, metric_drop ≤ 10%, hard_flips = 0 |
