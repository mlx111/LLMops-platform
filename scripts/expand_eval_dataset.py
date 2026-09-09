"""Expand the LLMOps evaluation dataset to 200+ cases across five case types.

Generates cases tailored to the paper-web Agentic RAG system (real tool
names, AI/ML/agent research domain) and either writes JSON files under
demo_data/expanded/ or pushes them to a running LLMOps instance.

Case mix (210 total):
    qa               50  — domain knowledge (quick agent, no retrieval)
    rag              50  — retrieval / research questions
    tool_calling     50  — exact tool + args expectation (real tools)
    agent_trajectory 50  — multi-step research tasks (trajectory metrics)
    multi_turn       10  — coherent multi-turn guides

Usage:
    python scripts/expand_eval_dataset.py                # write JSON only
    python scripts/expand_eval_dataset.py --push         # import to LLMOps
    python scripts/expand_eval_dataset.py --push --url http://localhost:8000
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parents[1] / "demo_data" / "expanded"
LLMOPS_URL = "http://localhost:8000"


# ───────────────────────── QA (50) ─────────────────────────

# (question, reference keywords that a correct answer should contain, difficulty)
QA_TOPICS = [
    ("什么是 Transformer 架构？它相比 RNN 有什么优势？",
     ["attention", "自注意力", "并行", "长距离"], "easy"),
    ("解释一下自注意力机制（self-attention）是怎么计算的？",
     ["query", "key", "value", "softmax"], "medium"),
    ("什么是 RAG（检索增强生成）？它解决了大模型的什么问题？",
     ["检索", "外部知识", "幻觉", "参数"], "easy"),
    ("Agentic RAG 和传统朴素 RAG 有什么区别？",
     ["智能体", "多步", "自主", "工具调用", "规划"], "medium"),
    ("什么是大语言模型的上下文窗口（context window）？",
     ["token", "长度", "限制", "注意力"], "easy"),
    ("解释什么是提示词工程（prompt engineering）？",
     ["提示", "指令", "示例", "few-shot"], "easy"),
    ("什么是上下文工程（context engineering）？它和提示词工程有何不同？",
     ["上下文", "记忆", "检索", "组织", "压缩"], "medium"),
    ("大模型微调（fine-tuning）有哪些常见方法？",
     ["全参数", "LoRA", "adapter", "指令微调", "RLHF"], "medium"),
    ("什么是 embedding（嵌入）？它在 RAG 里起什么作用？",
     ["向量", "语义", "相似度", "检索"], "easy"),
    ("向量数据库（如 Milvus）在 RAG 系统里的作用是什么？",
     ["向量", "存储", "相似度检索", "索引"], "medium"),
    ("什么是重排序（reranking）？为什么检索后要 rerank？",
     ["相关性", "排序", "精度", "cross-encoder"], "medium"),
    ("解释 LLM 智能体（agent）的核心组成部分。",
     ["规划", "记忆", "工具", "行动", "LLM"], "easy"),
    ("什么是 ReAct 模式？",
     ["推理", "行动", "观察", "reason", "act"], "medium"),
    ("什么是多智能体系统（multi-agent system）？",
     ["多个", "协作", "分工", "supervisor", "通信"], "medium"),
    ("supervisor 编排模式和主从智能体有什么关系？",
     ["主管", "调度", "子智能体", "handoff", "分派"], "hard"),
    ("什么是 MCP（Model Context Protocol）？",
     ["协议", "工具", "上下文", "标准化", "server"], "medium"),
    ("MCP 支持哪两种主要的传输方式？",
     ["stdio", "http", "传输"], "medium"),
    ("什么是 MCP Host？它和 MCP Server 有什么区别？",
     ["客户端", "连接", "发现工具", "server", "提供"], "hard"),
    ("什么是函数调用（function calling / tool calling）？",
     ["工具", "参数", "结构化", "调用"], "easy"),
    ("LLM 产生幻觉（hallucination）的原因有哪些？",
     ["编造", "参数知识", "缺乏证据", "概率"], "medium"),
    ("什么是思维链（chain-of-thought）提示？",
     ["中间步骤", "推理", "逐步"], "easy"),
    ("解释检索结果的 chunking（分块）策略为什么重要。",
     ["块大小", "语义", "上下文", "切分"], "medium"),
    ("什么是混合检索（hybrid search）？",
     ["稠密", "稀疏", "关键词", "向量", "结合"], "medium"),
    ("BM25 和向量检索各有什么优缺点？",
     ["关键词", "语义", "稀疏", "稠密", "精确匹配"], "hard"),
    ("什么是父块检索（parent document retrieval）？",
     ["父块", "子块", "上下文", "粒度"], "hard"),
    ("LLM 评测里常用的基准有哪些？",
     ["benchmark", "AgentBench", "SWE-bench", "GAIA", "MMLU"], "medium"),
    ("什么是 tau-bench？它评测智能体的什么能力？",
     ["工具", "多轮", "任务完成", "真实场景"], "hard"),
    ("GAIA 基准为什么被认为对智能体很难？",
     ["多步", "推理", "工具", "网页", "综合"], "hard"),
    ("SWE-bench 评测的是什么任务？",
     ["软件", "代码", "issue", "修复", "仓库"], "medium"),
    ("RAGAS 评测框架包含哪些核心指标？",
     ["faithfulness", "context", "precision", "recall", "relevancy"], "hard"),
    ("faithfulness（忠实度）指标衡量什么？",
     ["忠于", "上下文", "编造", "依据"], "medium"),
    ("什么是 human-in-the-loop（人在环路）？",
     ["人工", "审批", "确认", "干预", "高风险"], "medium"),
    ("智能体工具调用为什么需要 guardrails（护栏）？",
     ["注入", "越权", "拦截", "安全", "校验"], "medium"),
    ("什么是 prompt injection（提示词注入）攻击？",
     ["恶意指令", "忽略", "劫持", "绕过"], "easy"),
    ("LangGraph 是什么？它适合构建什么应用？",
     ["图", "状态", "节点", "边", "智能体", "工作流"], "medium"),
    ("deepagents 库提供了哪些智能体能力？",
     ["子智能体", "记忆", "技能", "工具", "文件"], "hard"),
    ("智能体的记忆（memory）通常分哪几类？",
     ["短期", "长期", "工作记忆", "持久"], "medium"),
    ("什么是文本的 tokenization？为什么要数 token？",
     ["分词", "子词", "计费", "上下文长度", "BPE"], "easy"),
    ("温度参数 temperature 对 LLM 输出有什么影响？",
     ["随机性", "创造性", "概率", "采样"], "easy"),
    ("top-p 和 temperature 采样有什么区别？",
     ["核采样", "累积概率", "随机", "词汇"], "hard"),
    ("什么是结构化输出（structured output）？",
     ["JSON", "schema", "字段", "可靠解析"], "medium"),
    ("解释智能体的自我修复（self-healing）循环。",
     ["校验", "修复", "重试", "降级", "参数"], "hard"),
    ("CrewAI、AutoGen 这类框架解决什么问题？",
     ["多智能体", "协作", "编排", "角色"], "medium"),
    ("什么是工具的 schema 校验？",
     ["参数", "类型", "必填", "范围", "校验"], "medium"),
    ("为什么中文文本检索/评测不能简单按空格分词？",
     ["中文", "没有空格", "分词", "字符", "词语"], "hard"),
    ("arXiv 是什么？学术检索为什么常用它？",
     ["预印本", "论文", "学术", "开放"], "easy"),
    ("BibTeX 在论文写作里的作用是什么？",
     ["引用", "参考文献", "格式", "citation"], "easy"),
    ("什么是 LLM-as-a-judge？它有什么优缺点？",
     ["模型评分", "成本", "偏差", "一致", "灵活"], "hard"),
    ("评测智能体时，任务成功率（task success）怎么判断？",
     ["完成", "最终答案", "目标达成", "轨迹"], "medium"),
    ("什么是轨迹评测（trajectory evaluation）？它和只看最终答案有何不同？",
     ["步骤", "工具调用", "过程", "中间", "路径"], "hard"),
]


def build_qa_cases() -> list[dict]:
    cases = []
    for i, (q, keywords, diff) in enumerate(QA_TOPICS):
        cases.append({
            "case_type": "qa",
            "input": q,
            "reference_answer": "参考要点：" + "、".join(keywords),
            "tags": ["domain-knowledge", diff, f"qa-{i+1:03d}"],
            "difficulty": diff,
            "extra_metadata": {"mode": "quick", "must_include_keywords": keywords,
                               "source": "expanded-qa"},
        })
    return cases


# ───────────────────────── RAG (50) ─────────────────────────

RAG_SUBJECTS = [
    "大语言模型智能体（LLM agent）的评测基准",
    "Agentic RAG 的高级技术方案",
    "上下文工程 Context Engineering 的最佳实践",
    "多智能体编排框架 LangGraph 与 AutoGen 的对比",
    "MCP Model Context Protocol 生态与工具发现",
    "检索增强生成中的重排序 reranking 技术",
    "向量检索与混合检索 hybrid search 的效果",
    "大模型幻觉检测与缓解方法",
    "智能体记忆 memory 管理机制",
    "prompt injection 攻击与防御",
    "self-RAG 与 corrective RAG 自适应检索",
    "LLM 函数调用 function calling 的可靠性",
    "RAG 系统的评测指标 faithfulness 与 context recall",
    "大模型微调 LoRA 与全参数微调的取舍",
    "思维链 chain-of-thought 对推理任务的影响",
    "embedding 模型在中文检索中的表现",
    "智能体工具调用的自愈与容错",
    "human-in-the-loop 在智能体系统中的应用",
    "SWE-bench 与代码智能体的进展",
    "tau-bench 与真实场景任务型智能体评测",
    "GAIA 基准与通用 AI 助手能力",
    "LangGraph checkpoint 与状态持久化",
    "deepagents 子智能体隔离上下文的优势",
    "RAG 分块 chunking 策略对答案质量的影响",
    "父块检索 parent-document retrieval 的实践",
]

RAG_TEMPLATES = [
    ("请联网检索并总结「{s}」的最新进展，给出关键要点。", ["检索", "总结", "要点"]),
    ("帮我调研一下 {s}，需要查阅资料后给出结构化小结。", ["调研", "资料", "小结"]),
    ("关于{s}，请检索相关资料并说明核心方法与适用场景。", ["核心方法", "适用场景"]),
]


def build_rag_cases() -> list[dict]:
    cases = []
    idx = 0
    for s in RAG_SUBJECTS:
        for tmpl, kws in RAG_TEMPLATES:
            if idx >= 50:
                break
            q = tmpl.format(s=s)
            cases.append({
                "case_type": "rag",
                "input": q,
                "reference_answer": "参考要点：" + "、".join(kws + [s.split("（")[0].split(" ")[0]]),
                "reference_context_ids": [f"ctx_{idx+1:03d}"],
                "tags": ["research", "web-retrieval", f"rag-{idx+1:03d}"],
                "difficulty": "hard",
                "extra_metadata": {"mode": "deep", "must_include_keywords": kws,
                                   "source": "expanded-rag"},
            })
            idx += 1
        if idx >= 50:
            break
    return cases


# ───────────────────────── tool_calling (50) ─────────────────────────

def build_tool_cases() -> list[dict]:
    cases: list[dict] = []
    n = 0

    def add(question, tool, args, keywords, diff="medium"):
        nonlocal n
        n += 1
        cases.append({
            "case_type": "tool_calling",
            "input": question,
            "reference_answer": "应调用 " + tool + " 完成任务：" + "、".join(keywords),
            "expected_tool": tool,
            "expected_args": args,
            "tags": ["tool-calling", diff, f"tc-{n:03d}"],
            "difficulty": diff,
            "extra_metadata": {"source": "expanded-tool-calling"},
        })

    # time (8) — get_current_time
    time_q = [
        ("现在几点了？", {"timezone": "Asia/Shanghai"}),
        ("帮我查一下当前时间", {"timezone": "Asia/Shanghai"}),
        ("现在是北京时间几点？", {"timezone": "Asia/Shanghai"}),
        ("今天日期是多少？", {"timezone": "Asia/Shanghai"}),
        ("告诉我现在的时间", {"timezone": "Asia/Shanghai"}),
        ("查一下上海现在几点", {"timezone": "Asia/Shanghai"}),
        ("现在什么时间了？", {"timezone": "Asia/Shanghai"}),
        ("给我看下当前的日期和时间", {"timezone": "Asia/Shanghai"}),
    ]
    for q, a in time_q:
        add(q, "get_current_time", a, ["时间", "time"], "easy")

    # web_search (12)
    web_q = [
        ("搜索一下 LangGraph 的最新版本特性", "LangGraph 最新版本特性"),
        ("帮我网上查一查大模型智能体的发展趋势", "大模型智能体发展趋势"),
        ("搜索 MCP 协议的官方文档", "MCP Model Context Protocol 官方文档"),
        ("查一下 2025 年 RAG 技术的新进展", "2025 RAG 技术新进展"),
        ("帮我搜索 deepagents 这个库的用法", "deepagents 库用法"),
        ("网上检索 AutoGen 多智能体框架", "AutoGen 多智能体框架"),
        ("搜索一下 RAGAS 评测框架怎么用", "RAGAS 评测框架使用"),
        ("帮我查 prompt injection 的防御方法", "prompt injection 防御方法"),
        ("搜索 CrewAI 框架的介绍", "CrewAI 框架介绍"),
        ("查一下向量数据库 Milvus 的最新功能", "Milvus 向量数据库功能"),
        ("帮我搜索智能体 self-healing 相关实践", "LLM agent self-healing 实践"),
        ("检索 LLM agent benchmark 的最新排名", "LLM agent benchmark 排名"),
    ]
    for q, query_arg in web_q:
        add(q, "web_search", {"query": query_arg}, ["搜索", "search"], "medium")

    # academic_search_papers (10)
    acad_q = [
        ("帮我检索 transformer attention 相关的学术论文", "transformer attention mechanism"),
        ("搜一下 retrieval augmented generation 的论文", "retrieval augmented generation"),
        ("查找 large language model agent 方向的论文", "large language model agent"),
        ("检索 multi-agent collaboration 相关文献", "multi-agent collaboration LLM"),
        ("帮我找 prompt injection 防御的研究论文", "prompt injection defense"),
        ("搜索 chain of thought reasoning 的经典论文", "chain of thought reasoning"),
        ("查一下 RAG evaluation 相关的学术研究", "RAG evaluation faithfulness"),
        ("检索 context window extension 的论文", "context window extension LLM"),
        ("帮我找 tool learning for language models 的文献", "tool learning language models"),
        ("搜索 self-reflective RAG 的研究", "self-reflective corrective RAG"),
    ]
    for q, query_arg in acad_q:
        add(q, "academic_search_papers", {"query": query_arg},
            ["论文", "paper"], "medium")

    # mcp calculator (6)
    calc_q = [
        ("请用 calculator 计算 128 加 256 的结果", {"expression": "128 + 256"}),
        ("调用 calculator 算一下 (15 + 27) * 3", {"expression": "(15 + 27) * 3"}),
        ("用计算器算 1024 除以 16", {"expression": "1024 / 16"}),
        ("calculator 帮我算 99 * 99 等于多少", {"expression": "99 * 99"}),
        ("请计算 (100 - 37) + 58 的值", {"expression": "(100 - 37) + 58"}),
        ("用 calculator 求 2 的 10 次方", {"expression": "2 ** 10"}),
    ]
    for q, a in calc_q:
        add(q, "mcp__demo-tools__calculator", a, ["计算", "calculator"], "medium")

    # mcp text_stats (4)
    ts_q = [
        ("用 text_stats 统计 hello world 的字符数", {"text": "hello world"}),
        ("调用 text_stats 统计 MCP host dynamic tools 这段文本", {"text": "MCP host dynamic tools"}),
        ("帮我统计字符串 agent harness 有多少个字符", {"text": "agent harness"}),
        ("text_stats 分析一下 langgraph supervisor 的文本长度", {"text": "langgraph supervisor"}),
    ]
    for q, a in ts_q:
        add(q, "mcp__demo-tools__text_stats", a, ["统计", "字符"], "medium")

    # send_email (4) — HITL
    mail_q = [
        ("使用 send_email 给 a@example.com 发邮件，主题是会议提醒", {"to": "a@example.com", "subject": "会议提醒"}),
        ("帮我用 send_email 给 boss@company.com 发一封周报邮件", {"to": "boss@company.com", "subject": "周报"}),
        ("调用 send_email 给 mentor@example.com 发送项目进展", {"to": "mentor@example.com", "subject": "项目进展"}),
        ("用 send_email 给 team@example.com 发通知邮件", {"to": "team@example.com", "subject": "通知"}),
    ]
    for q, a in mail_q:
        add(q, "send_email", a, ["邮件", "email", "审批"], "hard")

    # retrieve_knowledge (6)
    rag_k_q = [
        "根据知识库文档，说明项目里上下文工程的设计重点是什么？",
        "检索文档中关于评估指标的内容并总结",
        "从已上传文档里查找 RAG 架构相关章节",
        "知识库中有没有关于多智能体分工的说明？",
        "根据文档列出最重要的三条评估指标",
        "检索文档里关于 MCP Host 的实现说明",
    ]
    for i, q in enumerate(rag_k_q):
        add(q, "retrieve_knowledge", {"query": q.replace("？", "").replace("?", "")[:50]},
            ["检索", "知识库", "文档"], "medium")

    return cases[:50]


# ───────────────────────── agent_trajectory (50) ─────────────────────────

TRAJ_TASKS = [
    ("请联网调研大语言模型多智能体系统中 supervisor 编排模式的典型架构，检索后总结要点。",
     "multi", ["task"]),
    ("调研 2024-2025 年 Agentic RAG 的高级技术方案（self-RAG、corrective RAG、adaptive RAG），需要检索资料。",
     "multi", ["task"]),
    ("请检索 LLM agent 的权威评测基准（AgentBench、tau-bench、GAIA、SWE-bench）并对比维度。",
     "multi", ["task"]),
    ("联网检索 Model Context Protocol（MCP）的设计目标、传输方式和工具生态，给出技术小结。",
     "deep", ["web_search"]),
    ("调研上下文工程 Context Engineering 相对 prompt engineering 的新方法与工程实践。",
     "multi", ["task"]),
    ("搜索 LangGraph、CrewAI、AutoGen 三个框架在多智能体编排上的分工方式并对比。",
     "deep", ["web_search"]),
    ("请检索并分析 RAG 系统中检索结果重排序 reranking 的主流方法。",
     "deep", ["web_search"]),
    ("联网调研大模型幻觉的检测方法，给出工程上可落地的缓解手段。",
     "deep", ["web_search"]),
    ("检索并总结智能体长期记忆（long-term memory）的实现方案。",
     "deep", ["web_search"]),
    ("帮我调研 prompt injection 攻击在工具调用场景下的防御措施。",
     "deep", ["web_search"]),
]

TRAJ_SIMPLE = [
    ("帮我搜索一下 AI 智能体性能评估的常用指标。", "deep", ["web_search"]),
    ("现在几点了？", "quick", ["get_current_time"]),
    ("查一下今天的日期。", "quick", ["get_current_time"]),
    ("什么是 RAG？简要说明。", "quick", []),
    ("用 calculator 计算 (48 + 52) * 3。", "deep", ["mcp__demo-tools__calculator"]),
    ("用 text_stats 统计 harness engineering 的字符数。", "deep", ["mcp__demo-tools__text_stats"]),
    ("检索 transformer 相关学术论文。", "deep", ["academic_search_papers"]),
    ("搜索 deepagents 库的 GitHub 仓库。", "deep", ["web_search"]),
    ("请检索知识库中关于评估指标的内容。", "deep", ["retrieve_knowledge"]),
    ("调研大模型微调 LoRA 的原理。", "deep", ["web_search"]),
]


def _expected_research_steps(mode: str, question: str) -> list[dict]:
    """Realistic expected tool-call sequence for a research task.

    Deep research legitimately needs multiple retrieval rounds, so the
    reference trajectory lists 2-4 tool calls (otherwise StepEfficiency would
    penalise the agent for doing its job).

    multi mode: supervisor hands off via deepagents' task() into *isolated*
    sub-agents — those tool calls do not surface on the top-level trajectory,
    so we assert only successful task completion (no top-level tool expectation).
    """
    academic = any(k in question for k in ["论文", "paper", "基准", "benchmark", "学术", "文献"])
    if mode == "multi":
        names = []
    elif mode == "harness":
        names = ["web_search", "web_search"] + (["academic_search_papers"] if academic else [])
    else:  # deep
        names = ["web_search", "web_search"] + (["academic_search_papers"] if academic else [])
    steps = [{"type": "tool_call", "tool_name": n, "tool_args": {}} for n in names]
    steps.append({"type": "final", "content": "answer"})
    return steps


def build_trajectory_cases() -> list[dict]:
    cases: list[dict] = []
    idx = 0
    # research tasks (10, repeated across modes to reach volume with variety)
    for q, mode, tools in TRAJ_TASKS:
        steps = _expected_research_steps(mode, q)
        cases.append({
            "case_type": "agent_trajectory",
            "input": q,
            "reference_answer": json.dumps({"steps": steps, "success": True}, ensure_ascii=False),
            "tags": [mode, "trajectory", "research", f"traj-{idx+1:03d}"],
            "difficulty": "hard",
            "extra_metadata": {"mode": mode, "source": "expanded-trajectory"},
        })
        idx += 1
    for q, mode, tools in TRAJ_SIMPLE:
        steps = [{"type": "tool_call", "tool_name": t, "tool_args": {}} for t in tools]
        steps.append({"type": "final", "content": "answer"})
        cases.append({
            "case_type": "agent_trajectory",
            "input": q,
            "reference_answer": json.dumps({"steps": steps, "success": True}, ensure_ascii=False),
            "tags": [mode, "trajectory", "simple", f"traj-{idx+1:03d}"],
            "difficulty": "easy" if mode == "quick" else "medium",
            "extra_metadata": {"mode": mode, "source": "expanded-trajectory"},
        })
        idx += 1

    # Expand research tasks across multiple modes for volume (deep/multi/harness)
    extra_modes = ["deep", "multi", "harness"]
    for q, mode, tools in TRAJ_TASKS:
        for em in extra_modes:
            if idx >= 50:
                break
            steps = _expected_research_steps(em, q)
            cases.append({
                "case_type": "agent_trajectory",
                "input": f"[{em}模式] " + q,
                "reference_answer": json.dumps({"steps": steps, "success": True}, ensure_ascii=False),
                "tags": [em, "trajectory", "research", f"traj-{idx+1:03d}"],
                "difficulty": "hard",
                "extra_metadata": {"mode": em, "source": "expanded-trajectory"},
            })
            idx += 1
        if idx >= 50:
            break
    return cases[:50]


# ───────────────────────── multi_turn (10) ─────────────────────────

MULTI_TURN = [
    "Turn 1: 用户问如何搭建 Python 虚拟环境。Turn 2: 问如何在其中安装依赖包。Turn 3: 问如何导出 requirements.txt。评测助手是否跨三轮给出连贯完整的指引。",
    "Turn 1: 用户问什么是 RAG。Turn 2: 追问 RAG 为什么能减少幻觉。Turn 3: 再问如何评测一个 RAG 系统。评测回答是否层层递进且前后呼应。",
    "Turn 1: 用户想了解 MCP 是什么。Turn 2: 问 MCP Host 和 Server 的区别。Turn 3: 问如何接入一个外部 MCP server。评测是否保持上下文连贯。",
    "Turn 1: 用户问怎么搜索学术论文。Turn 2: 让助手针对 RAG 方向检索论文。Turn 3: 要求整理 BibTeX 引用。评测多步任务是否衔接。",
    "Turn 1: 用户问智能体和普通聊天机器人的区别。Turn 2: 追问多智能体协作的价值。Turn 3: 问什么时候该用多智能体。评测论证是否一致。",
    "Turn 1: 用户问现在几点。Turn 2: 追问明天同一时间开会怎么提醒。Turn 3: 让助手帮忙发邮件通知。评测工具能力是否递进。",
    "Turn 1: 用户问 embedding 是什么。Turn 2: 追问向量数据库怎么用。Turn 3: 问混合检索为什么更好。评测知识是否连贯。",
    "Turn 1: 用户问如何写文献综述。Turn 2: 让助手检索相关论文。Turn 3: 要求总结研究趋势。评测跨轮任务完整性。",
    "Turn 1: 用户问 prompt injection 是什么。Turn 2: 追问工具调用时怎么防御。Turn 3: 问 guardrails 如何落地。评测安全话题连贯性。",
    "Turn 1: 用户问 LangGraph 是什么。Turn 2: 追问怎么实现人工审批节点。Turn 3: 问状态如何持久化恢复。评测技术深度递进。",
]


def build_multi_turn_cases() -> list[dict]:
    return [
        {
            "case_type": "multi_turn",
            "input": q,
            "reference_answer": "应跨多轮保持上下文连贯、逐步深入并完整回应每一轮诉求。",
            "tags": ["multi-turn", "coherence", f"mt-{i+1:03d}"],
            "difficulty": "hard",
            "extra_metadata": {"source": "expanded-multi-turn"},
        }
        for i, q in enumerate(MULTI_TURN)
    ]


DATASETS = [
    ("mypaperweb-qa-expanded", "qa", build_qa_cases),
    ("mypaperweb-rag-expanded", "rag", build_rag_cases),
    ("mypaperweb-toolcalling-expanded", "tool_calling", build_tool_cases),
    ("mypaperweb-trajectory-expanded", "agent_trajectory", build_trajectory_cases),
    ("mypaperweb-multiturn-expanded", "multi_turn", build_multi_turn_cases),
]


def _request(method: str, url: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {url} -> {exc.code}: {detail[:300]}") from exc


def push(base: str) -> None:
    total = 0
    for name, ctype, builder in DATASETS:
        cases = builder()
        # Idempotent: drop an existing dataset of the same name, then recreate.
        ds = _request("GET", f"{base}/api/datasets", timeout=15)
        existing = [d for d in ds.get("items", []) if d.get("name") == name]
        if existing:
            ds_id = existing[0]["id"]
            try:
                _request("DELETE", f"{base}/api/datasets/{ds_id}", timeout=30)
            except RuntimeError as exc:
                print(f"  WARN: could not delete existing dataset {ds_id}: {exc}")
        created = _request("POST", f"{base}/api/datasets",
                           {"name": name, "description": f"Expanded {ctype} cases for paper-web",
                            "case_type": ctype}, timeout=15)
        ds_id = created["id"]
        _request("POST", f"{base}/api/datasets/{ds_id}/import", {"cases": cases}, timeout=120)
        print(f"  {name} ({ctype}): {len(cases)} cases -> dataset {ds_id}")
        total += len(cases)
    print(f"Total imported: {total} cases")


def write_files() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for name, ctype, builder in DATASETS:
        cases = builder()
        path = OUT_DIR / f"{ctype}_expanded.json"
        path.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  wrote {len(cases)} cases -> {path.name}")
        total += len(cases)
    print(f"Total written: {total} cases to {OUT_DIR}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true")
    parser.add_argument("--url", default=LLMOPS_URL)
    args = parser.parse_args()

    if args.push:
        push(args.url.rstrip("/"))
    else:
        write_files()
    return 0


if __name__ == "__main__":
    sys.exit(main())
