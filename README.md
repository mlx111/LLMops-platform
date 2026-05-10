# LLMOps 评测与监控平台

大模型评测与监控平台，支持多 provider（DeepSeek / OpenAI / DashScope / Anthropic / Ollama）的评测运行、链路追踪、版本对比和自动化报告。

## 快速启动

### Windows

双击 `start-windows.bat`，或命令行执行：

```bash
start-windows.bat
```

脚本会自动：
1. 在 `backend/.venv` 创建 Python 虚拟环境并安装依赖
2. 在 `frontend/node_modules` 安装前端依赖
3. 启动后端 (http://127.0.0.1:8000)
4. 启动前端 (http://127.0.0.1:5173)

### Linux / macOS

```bash
./start.sh
```

停止服务按 `Ctrl+C` 即可。

---

## 使用指南

### 1. 首页仪表盘

打开 http://127.0.0.1:5173，首页显示：
- **统计卡片**：总运行次数、平均通过率、平均延迟、数据集数量
- **趋势图表**：通过率趋势（折线图）、平均分数（柱状图）、延迟与 Token 消耗
- **最近运行**：最近的运行记录列表

### 2. 数据集管理

路径：左侧菜单 **数据集与用例**

创建和管理测试数据集：

1. 点击 **创建数据集**，填写名称、描述、选择用例类型（qa / rag / tool_calling / multi_turn）
2. 点击数据集的 **添加用例** 进入用例管理
3. **添加用例**：填写输入、参考答案、难度、标签
4. **导入用例**：粘贴 JSON 批量导入（格式见下方）

#### JSON 导入格式

```json
[
  {
    "case_type": "qa",
    "input": "什么是机器学习？",
    "reference_answer": "机器学习是...",
    "difficulty": "easy",
    "tags": ["AI", "基础"]
  }
]
```

demo 数据在 `demo_data/` 目录下，可直接导入测试。

### 3. 实验配置

路径：左侧菜单 **实验配置**

这是运行评测的核心页面。

#### Provider 选择

五个支持的 provider：

| Provider | 需要 API Key | 默认模型 | 模型获取方式 |
|---|---|---|---|
| DeepSeek | 是 | deepseek-chat | 实时拉取 |
| OpenAI | 是 | gpt-4o-mini | 实时拉取 |
| DashScope (通义千问) | 是 | qwen-plus | 内建模型列表 |
| Anthropic Claude | 是 | claude-haiku | 实时拉取 |
| Ollama (本地) | 否 | qwen2.5:7b | 调用本地 API |

操作步骤：

1. **选择 Provider**：点击 provider 卡片
2. **配置 API Key**：如果 provider 需要 API Key，点击 **Manage Keys** 跳转到设置页配置，未配置时 Start Run 按钮被禁用
3. **获取模型**：点击 Model 下拉框旁的 **获取模型** 按钮，拉取可用模型列表并选择
4. **填写运行名称**：例如 "DeepSeek 基线测试"
5. **选择数据集**：选择要使用的测试数据集
6. **并发数**：同时运行的评测线程数（1 / 3 / 5 / 10）
7. **目标系统（可选）**：如果要对接自己的 RAG/Agent 系统，填写 URL 和系统类型。不填则使用内置的规则评分模式
8. 点击 **Start Run** 开始评测

#### 评测进度

运行开始后会显示进度条：
- **通过 / 失败 / 剩余** 用例数量
- 完成后自动刷新最近运行列表

### 4. 运行结果

路径：左侧菜单 **运行结果**

查看所有评测运行记录：

- **列表**：名称、状态、通过/总数、平均分、延迟
- **详情**：点击 **Details** 查看每个用例的详细评分结果
- **追踪**：点击 **Traces** 查看链路追踪数据（需后端支持）
- **报告**：运行完成后点击 **Report** 生成分析报告

### 5. 链路追踪

路径：在运行结果中点击 **Traces** 进入

展示评测的详细内部步骤：

- **追踪列表**：选择要查看的链路
- **瀑布图**：各步骤耗时可视化
- **步骤树**：按层级展示每一步的输入/输出/耗时/Token 数
- **用户输入**：原始输入内容

### 6. 版本对比

路径：左侧菜单 **版本对比**

对比两次评测运行的差异：

1. 选择 **Baseline（运行 1）** 和 **Candidate（运行 2）**
2. 自动加载对比结果：
   - **对比摘要**：整体结论
   - **改进用例**：运行 2 比运行 1 得分更高的用例
   - **回退用例**：运行 2 比运行 1 得分更低的用例
   - **指标雷达图**：多维度评分对比
   - **指标差异表**：各项指标的量化差异

### 7. 报告

路径：左侧菜单 **报告**

自动生成的分析报告：

1. 在下拉框选择已完成运行
2. 点击 **Generate** 生成报告
3. 报告以 Markdown 格式呈现，包含失败分布饼图

### 8. 设置

路径：左侧菜单 **设置**

#### API Key 管理

配置各 provider 的 API Key：

1. 点击 **添加密钥**，选择 provider 并填入 API Key
2. Base URL 可选（通常使用默认地址）
3. 已配置的 provider 显示绿色 ✓ 标记
4. 可更新或删除已有密钥

> Ollama 无需 API Key，系统会自动将其标记为已配置。

---

## 中英文切换

页面右上角有语言切换下拉框，支持中文和 English 切换，所有 UI 文字和 Ant Design 组件语言会同步切换，偏好保存在浏览器本地存储中。

---

## 技术架构

```
frontend/ (React + Vite + TypeScript + Ant Design + ECharts)
  └── API / SSE ──► backend/ (FastAPI + SQLAlchemy + Celery)
                     ├── 路由: datasets, versions, runs, traces, reports, config, dashboard
                     ├── 服务: runner, tracer, classifier, report_generator, provider_models
                     ├── 任务: celery_app (Redis / 线程回退)
                     └── 数据库: SQLite (8 张表)
```

### 数据库表

| 表名 | 用途 |
|---|---|
| datasets | 数据集 |
| eval_cases | 评测用例 |
| versions | 模型/Prompt 版本 |
| eval_runs | 评测运行 |
| eval_results | 评测结果 |
| api_keys | Provider API Key |
| traces | 链路追踪 |
| trace_steps | 追踪步骤 |
| reports | 报告 |

### API 文档

启动后端后访问 http://127.0.0.1:8000/docs 查看 Swagger 接口文档。
