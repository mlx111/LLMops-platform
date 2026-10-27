"""L2 Insight Agent：自动扫历史 run + 分类失败模式 + 聚类用户意图。

L1 → L2 演进：从「单 case 内 ReAct 找根因」升级为「跨 run 聚合 ReAct 找系统性失败模式」。
- L1 工具基于单个 case 的 turn_records 数据（内存）
- L2 工具基于真实 DB（EvalRun/EvalResult/EvalCase），扫历史 run 看趋势

自研 ReAct loop（Thought / Action / Action Input / Observation），复用 llm_judge._chat_completion，
零新依赖。失败兜底返回 `judge="agentic_insight_fallback"`，绝不抛异常。

工具集（闭包绑定 db Session）：
  - list_recent_runs: 列最近 N 天评测 run
  - get_run_summary: 拿某 run 总览（分数分布 / 失败 case 数）
  - list_failed_cases: 拿某 run 失败 case 列表（case_id / 失败类别 / 失败原因）
  - categorize_run_failures: 把某 run 的失败归到 9 类（复用 classifier.classify 口径）
  - cluster_user_intents: 聚类用户意图（规则版：分词 → 去停用词 → TF 排序 → top-8）
"""

from __future__ import annotations

import datetime
import json
import re
import time
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from app.models.dataset import EvalCase
from app.models.run import EvalResult, EvalRun
from app.services.classifier import classify
from app.services.llm_judge import (
    _chat_completion,
    _clamp01,
    _parse_json,
    _report_judge_to_langfuse,
)
from app.services.logger import logger


# ── ReAct 提示词（JSON-based ReAct 协议，同 L1） ──
AGENT_SYS_PROMPT = """你是 Agentic Insight 分析师。目标：扫历史评测 run，归纳系统性失败模式 + 聚类用户意图。

每轮严格输出一个 JSON 对象，两种格式之一：

格式1（调工具查数据）：
{"thought": "思考", "action": "工具名", "action_input": {...}}

格式2（最终答案）：
{"thought": "最终思考", "final_answer": {"failure_distribution": {"类别": 数量}, "top_failure_categories": [["类别", 数量]], "intent_clusters": ["意图1", "意图2"], "insight_summary": "中文总结，指出最近 N 天共 X 个 run，失败集中在 Y 类，建议..."}}

可用工具：
- list_recent_runs（列最近 N 天评测 run，action_input={"days": 7}）
- get_run_summary（拿某 run 总览，action_input={"run_id": N}）
- list_failed_cases（拿某 run 失败 case 列表，action_input={"run_id": N}）
- categorize_run_failures（把某 run 失败归到 9 类，action_input={"run_id": N}）
- cluster_user_intents（聚类某 run 用户意图，action_input={"run_id": N}）

9 类失败模式（classifier 口径）：
  hallucination / retrieval_miss / low_context_precision / evidence_ignored /
  tool_selection_error / tool_arguments_error / timeout / high_cost /
  prompt_constraint_violation / quality_issue（兜底类）

规则：
1. 第一轮必须先调 list_recent_runs 看有哪些 run
2. 至少对一个 run 调 categorize_run_failures + cluster_user_intents
3. 看完数据再给 final_answer，不要一开始就下结论
4. 最多 6 轮工具调用
5. 如果没有最近 run，final_answer 中 insight_summary 说明 "no recent runs"，其余字段空/默认值"""

MAX_ITER = 6


# ── 中文停用词（简化版，用于意图聚类） ──
_STOPWORDS = {
    "的", "是", "了", "在", "和", "与", "或", "及", "请", "我", "你", "他", "她", "它",
    "们", "这", "那", "有", "无", "为", "以", "对", "由", "从", "到", "向", "上", "下",
    "中", "等", "之", "其", "也", "而", "但", "则", "若", "如", "何", "什么", "怎么",
    "为什么", "如何", "哪", "哪些", "哪个", "可以", "能够", "需要", "应该", "要", "想",
    "会", "能", "能不", "能否", "吗", "呢", "吧", "啊", "呀", "么", "哦", "嗯", "哈", "啥",
    "一个", "一种", "这个", "那个", "这些", "那些", "就是", "还是", "或者", "但是", "因为",
    "所以", "如果", "虽然", "尽管", "已经", "正在", "将要", "可以", "可能", "应该", "必须",
}


def _tokenize_for_intent(text: str) -> list[str]:
    """混合分词：英文按空格/标点切，中文按 2-gram 切，去停用词。"""
    if not text:
        return []
    tokens: list[str] = []
    # 英文 token（≥2 字符）
    for w in re.split(r"[\s,，。、；;：:!！？?\.\?\(\)（）\"'`]+", text):
        if w and len(w) >= 2 and w.lower() not in _STOPWORDS:
            tokens.append(w.lower())
    # 中文 bigram
    cjk = re.sub(r"[^\u4e00-\u9fa5]", "", text)
    for i in range(len(cjk) - 1):
        bg = cjk[i : i + 2]
        if bg not in _STOPWORDS:
            tokens.append(bg)
    return tokens


# ── 工具集构造（闭包绑定 db Session） ──
def _build_tools(db: Session) -> dict:
    """构造工具集，闭包绑定 db Session。每个工具返回 JSON 字符串供 LLM 观察。"""

    def list_recent_runs(inp: dict) -> str:
        days = int(inp.get("days", 7) or 7)
        cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=days)
        runs = (
            db.query(EvalRun)
            .filter(EvalRun.created_at >= cutoff)
            .order_by(EvalRun.created_at.desc())
            .limit(20)
            .all()
        )
        if not runs:
            return json.dumps(
                {"runs": [], "hint": f"no runs in last {days} days"},
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "runs": [
                    {
                        "run_id": r.id,
                        "name": r.name,
                        "status": r.status,
                        "total_cases": r.total_cases,
                        "passed": r.passed_cases,
                        "failed": r.failed_cases,
                        "avg_score": r.avg_score,
                        "created_at": str(r.created_at),
                    }
                    for r in runs
                ]
            },
            ensure_ascii=False,
        )

    def get_run_summary(inp: dict) -> str:
        run_id = int(inp.get("run_id", 0) or 0)
        run = db.get(EvalRun, run_id)
        if not run:
            return json.dumps({"error": f"run {run_id} not found"}, ensure_ascii=False)
        results = db.query(EvalResult).filter(EvalResult.run_id == run_id).all()
        scores_list = []
        for r in results:
            if r.scores and isinstance(r.scores, dict):
                for name, sd in r.scores.items():
                    if isinstance(sd, dict) and "score" in sd:
                        scores_list.append(
                            {
                                "case_id": r.case_id,
                                "metric": name,
                                "score": sd["score"],
                                "status": r.status,
                            }
                        )
        return json.dumps(
            {
                "run_id": run.id,
                "name": run.name,
                "status": run.status,
                "total": run.total_cases,
                "passed": run.passed_cases,
                "failed": run.failed_cases,
                "avg_score": run.avg_score,
                "avg_latency_ms": run.avg_latency_ms,
                "scores_sample": scores_list[:20],
            },
            ensure_ascii=False,
        )

    def list_failed_cases(inp: dict) -> str:
        run_id = int(inp.get("run_id", 0) or 0)
        results = (
            db.query(EvalResult)
            .filter(
                EvalResult.run_id == run_id,
                EvalResult.status.in_(["failed", "error"]),
            )
            .limit(30)
            .all()
        )
        if not results:
            return json.dumps(
                {"failed_cases": [], "hint": f"no failed cases in run {run_id}"},
                ensure_ascii=False,
            )
        case_ids = [r.case_id for r in results]
        cases = (
            {c.id: c for c in db.query(EvalCase).filter(EvalCase.id.in_(case_ids)).all()}
            if case_ids
            else {}
        )
        return json.dumps(
            {
                "failed_cases": [
                    {
                        "case_id": r.case_id,
                        "status": r.status,
                        "user_input": (
                            cases.get(r.case_id).input[:200]
                            if cases.get(r.case_id)
                            else ""
                        ),
                        "failure_reason": (r.failure_reason or "")[:200],
                        "scores": r.scores,
                    }
                    for r in results
                ]
            },
            ensure_ascii=False,
        )

    def categorize_run_failures(inp: dict) -> str:
        run_id = int(inp.get("run_id", 0) or 0)
        results = db.query(EvalResult).filter(EvalResult.run_id == run_id).all()
        if not results:
            return json.dumps(
                {"failure_distribution": {}, "hint": f"no results in run {run_id}"},
                ensure_ascii=False,
            )
        case_ids = [r.case_id for r in results]
        cases = (
            {c.id: c for c in db.query(EvalCase).filter(EvalCase.id.in_(case_ids)).all()}
            if case_ids
            else {}
        )
        distribution: dict[str, int] = {}
        for r in results:
            case = cases.get(r.case_id)
            if not case:
                continue
            try:
                cats = classify(
                    result_scores=r.scores or {},
                    case=case,
                    latency_ms=r.latency_ms or 0,
                    input_tokens=r.input_tokens or 0,
                    output_tokens=r.output_tokens or 0,
                    actual_output=r.actual_output or "",
                    actual_tool=r.actual_tool or "",
                    actual_args=r.actual_args,
                )
            except Exception as exc:  # noqa: BLE001 - 单个 case 分类失败不能中断
                logger.warning(
                    "classify failed for case %s in run %s: %s",
                    r.case_id,
                    run_id,
                    str(exc)[:120],
                )
                cats = ["quality_issue"]
            for cat in cats:
                distribution[cat] = distribution.get(cat, 0) + 1
        return json.dumps(
            {"failure_distribution": distribution, "total_results": len(results)},
            ensure_ascii=False,
        )

    def cluster_user_intents(inp: dict) -> str:
        run_id = int(inp.get("run_id", 0) or 0)
        results = db.query(EvalResult).filter(EvalResult.run_id == run_id).all()
        if not results:
            return json.dumps(
                {"intent_clusters": [], "hint": f"no results in run {run_id}"},
                ensure_ascii=False,
            )
        case_ids = [r.case_id for r in results]
        cases = (
            db.query(EvalCase).filter(EvalCase.id.in_(case_ids)).all()
            if case_ids
            else []
        )
        term_freq: Counter = Counter()
        for case in cases:
            tokens = _tokenize_for_intent(case.input or "")
            term_freq.update(tokens)
        top_intents = [t for t, _ in term_freq.most_common(8)]
        return json.dumps(
            {
                "intent_clusters": top_intents,
                "total_cases": len(cases),
                "top_terms_with_freq": [
                    {"term": t, "freq": f} for t, f in term_freq.most_common(8)
                ],
            },
            ensure_ascii=False,
        )

    return {
        "list_recent_runs": list_recent_runs,
        "get_run_summary": get_run_summary,
        "list_failed_cases": list_failed_cases,
        "categorize_run_failures": categorize_run_failures,
        "cluster_user_intents": cluster_user_intents,
    }


# ── ReAct 解析（JSON-based，复用 L1 同款逻辑） ──
def _parse_response(response: str) -> dict | None:
    """解析 LLM 的 JSON ReAct 响应。

    返回三种情况：
      {"type": "action", "thought": ..., "tool": ..., "input": {...}}
      {"type": "final", "thought": ..., "final_answer": {...}}
      None（解析失败）
    """
    parsed = _parse_json(response)
    if not parsed or not isinstance(parsed, dict):
        return None
    thought = str(parsed.get("thought", ""))[:500]
    if "final_answer" in parsed:
        return {
            "type": "final",
            "thought": thought,
            "final_answer": parsed["final_answer"],
        }
    if "action" in parsed:
        tool_name = str(parsed["action"]).strip()
        tool_input = parsed.get("action_input") or parsed.get("input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        return {
            "type": "action",
            "thought": thought,
            "tool": tool_name,
            "input": tool_input,
        }
    return None


def _empty_final_answer() -> dict:
    """无数据时的空 final_answer 兜底。"""
    return {
        "failure_distribution": {},
        "top_failure_categories": [],
        "intent_clusters": [],
        "insight_summary": "no recent runs to analyze",
    }


# ── 主入口 ──
def agentic_insight(db: Session, days: int = 7) -> dict:
    """ReAct agent 版历史 run 洞察分析。

    返回 {
        failure_distribution, top_failure_categories, intent_clusters,
        insight_summary, trace, judge: "agentic_insight", iterations
    }。
    失败兜底返回 {judge: "agentic_insight_fallback", ...}，绝不抛异常。
    """
    tools = _build_tools(db)
    messages = [
        {"role": "system", "content": AGENT_SYS_PROMPT},
        {
            "role": "user",
            "content": (
                f"请分析最近 {days} 天的评测 run，给出系统性失败模式分布和用户意图聚类。"
                f"先调 list_recent_runs(days={days}) 看有哪些 run。"
            ),
        },
    ]

    trace_log: list[dict] = []  # ReAct 每轮步骤，供可观测
    start_ts = time.time()

    for iteration in range(MAX_ITER):
        response, usage = _chat_completion(messages, temperature=0.0)
        if not response:
            logger.warning(
                "Insight agent LLM call failed at iteration %d", iteration + 1
            )
            return {
                **_empty_final_answer(),
                "trace": trace_log,
                "judge": "agentic_insight_fallback",
                "iterations": iteration + 1,
                "elapsed_ms": int((time.time() - start_ts) * 1000),
                "error": "agent LLM unavailable",
            }

        trace_log.append({"iteration": iteration + 1, "thought_raw": response[:500]})

        parsed_resp = _parse_response(response)
        if parsed_resp is None:
            messages.append({"role": "assistant", "content": response})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "请严格输出 JSON 格式："
                        '{"thought":..., "action":..., "action_input":{...}} '
                        '或 {"thought":..., "final_answer":{...}}'
                    ),
                }
            )
            continue

        # 1. final_answer 分支
        if parsed_resp["type"] == "final":
            final = parsed_resp["final_answer"] or {}
            if not isinstance(final, dict):
                final = {}
            trace_log[-1]["thought"] = parsed_resp.get("thought", "")
            trace_log[-1]["final_answer"] = final

            failure_dist = final.get("failure_distribution") or {}
            if not isinstance(failure_dist, dict):
                failure_dist = {}
            # 排序取 top
            sorted_cats = sorted(
                failure_dist.items(),
                key=lambda kv: (kv[1] if isinstance(kv[1], (int, float)) else 0),
                reverse=True,
            )
            top_cats = [[k, v] for k, v in sorted_cats[:5]]
            intent_clusters = final.get("intent_clusters") or []
            if not isinstance(intent_clusters, list):
                intent_clusters = []

            result = {
                "failure_distribution": failure_dist,
                "top_failure_categories": top_cats,
                "intent_clusters": intent_clusters,
                "insight_summary": str(final.get("insight_summary", ""))[:800],
                "trace": trace_log,
                "judge": "agentic_insight",
                "iterations": iteration + 1,
                "elapsed_ms": int((time.time() - start_ts) * 1000),
            }
            _report_judge_to_langfuse(
                "agentic_insight",
                messages[-1].get("content", ""),
                json.dumps(result, ensure_ascii=False),
                usage,
            )
            logger.info(
                "Insight agent finished in %d iterations: %d categories, %d intents",
                iteration + 1,
                len(failure_dist),
                len(intent_clusters),
            )
            return result

        # 2. action 分支：调工具
        tool_name = parsed_resp["tool"]
        tool_input = parsed_resp["input"]
        tool_fn = tools.get(tool_name)
        if tool_fn is None:
            observation = (
                f"Error: tool '{tool_name}' not found. "
                f"Available: {list(tools.keys())}"
            )
        else:
            try:
                observation = tool_fn(tool_input)
            except Exception as exc:  # noqa: BLE001 - 工具调用失败不能中断 agent
                observation = f"Error: {str(exc)[:200]}"

        trace_log[-1]["action"] = tool_name
        trace_log[-1]["action_input"] = tool_input
        trace_log[-1]["observation"] = observation[:800]
        trace_log[-1]["thought"] = parsed_resp.get("thought", "")

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    # 超过 max_iter 还没 final，兜底
    logger.warning(
        "Insight agent exceeded %d iterations, falling back", MAX_ITER
    )
    return {
        **_empty_final_answer(),
        "trace": trace_log,
        "judge": "agentic_insight_timeout",
        "iterations": MAX_ITER,
        "elapsed_ms": int((time.time() - start_ts) * 1000),
        "error": "max iterations exceeded",
    }
