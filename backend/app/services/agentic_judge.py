"""L1 Agentic Judge：把单轮 LLM-judge 升级为 ReAct agent，多轮调工具找根因+修复建议。

L0 → L1 演进：从「单轮 prompt → 打分」升级为「ReAct agent 多轮探查 → 根因+修复建议」。
自研 ReAct loop（Thought / Action / Action Input / Observation），复用 llm_judge._chat_completion，
零新依赖。失败兜底回退到单轮 judge_correctness。

工具集（基于 turn_records 数据，不强依赖 DB / EvalCase）：
  - inspect_turns: 看所有轮的 user/answer/reference
  - find_low_scoring_turns: 找低分轮
  - compare_answer_reference: 详细对比某轮 answer vs reference
  - categorize_failure: 9 类失败归因（answer 空 / 答非所问 / 部分正确 / 幻觉 等）
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.llm_judge import (
    _chat_completion,
    _clamp01,
    _parse_json,
    _report_judge_to_langfuse,
)
from app.services.logger import logger


# ── ReAct 提示词（JSON-based ReAct 协议，比文本 ReAct 更适配 JSON 模式 LLM） ──
AGENT_SYS_PROMPT = """你是 Agentic 评测裁判。目标：评估多轮对话质量，找根因，给修复建议。

每轮严格输出一个 JSON 对象，两种格式之一：

格式1（调工具查数据）：
{"thought": "思考", "action": "工具名", "action_input": {...}}

格式2（最终答案）：
{"thought": "最终思考", "final_answer": {"overall_score": 0-1, "root_cause": "中文根因", "fix_suggestion": "中文修复建议", "failure_category": "9类之一"}}

可用工具：
- inspect_turns（看所有轮的 user/answer/reference，action_input={}）
- find_low_scoring_turns（找低分轮，action_input={}）
- compare_answer_reference（详细对比某轮 answer vs reference，action_input={"turn": N}）
- categorize_failure（把某轮失败归到 9 类之一，action_input={"turn": N}）

9 类失败模式：
  empty_answer / irrelevant_answer / partial_correct / hallucination /
  context_lost / tool_misuse / format_violation / timeout / quality_issue

规则：
1. 第一轮必须先调 inspect_turns 或 find_low_scoring_turns 查数据
2. 看完数据再给 final_answer，不要一开始就下结论
3. 最多 5 轮工具调用"""

MAX_ITER = 5


# ── 工具集构造（闭包绑定 turn_records + per_turn_scores） ──
def _build_tools(turn_records: list[dict], per_turn_scores: list[float]) -> dict:
    """构造工具集，闭包绑定数据。"""

    def inspect_turns(_input: dict) -> str:
        return json.dumps(
            [
                {
                    "turn": i + 1,
                    "user": (r.get("user") or "")[:200],
                    "answer": (r.get("answer") or "")[:200],
                    "reference": (r.get("reference") or "")[:200],
                }
                for i, r in enumerate(turn_records)
            ],
            ensure_ascii=False,
        )

    def find_low_scoring_turns(_input: dict) -> str:
        low = [(i + 1, float(s)) for i, s in enumerate(per_turn_scores) if float(s) < 0.5]
        return json.dumps(
            {"low_scoring_turns": low, "all_scores": per_turn_scores},
            ensure_ascii=False,
        )

    def compare_answer_reference(inp: dict) -> str:
        turn = int(inp.get("turn", 1))
        if 1 <= turn <= len(turn_records):
            r = turn_records[turn - 1]
            return json.dumps(
                {
                    "turn": turn,
                    "user": r.get("user", ""),
                    "answer": r.get("answer", ""),
                    "reference": r.get("reference") or "(无参考)",
                },
                ensure_ascii=False,
            )
        return json.dumps({"error": f"turn {turn} out of range 1-{len(turn_records)}"}, ensure_ascii=False)

    def categorize_failure(inp: dict) -> str:
        turn = int(inp.get("turn", 1))
        if not (1 <= turn <= len(turn_records)):
            return json.dumps({"error": f"turn {turn} out of range"}, ensure_ascii=False)
        r = turn_records[turn - 1]
        answer = (r.get("answer") or "").strip()
        reference = (r.get("reference") or "").strip()
        user = (r.get("user") or "").strip()

        # 简化版 9 类归因（规则版，零 LLM 调用）
        if not answer:
            return json.dumps({"category": "empty_answer", "hint": "answer 为空"}, ensure_ascii=False)
        if reference and user and any(k in reference for k in user.split()[:3]) is False:
            pass  # 弱信号，跳过
        # 答非所问：answer 长度远小于 reference 的 1/3 或不含 reference 关键词
        if reference:
            ref_keywords = [w for w in re.split(r"[\s,，。、]+", reference) if len(w) >= 2]
            hit = sum(1 for k in ref_keywords if k in answer)
            ratio = hit / max(len(ref_keywords), 1)
            if ratio < 0.1:
                return json.dumps({"category": "irrelevant_answer", "hint": f"answer 几乎不含 reference 关键词（命中率 {ratio:.2f}）"}, ensure_ascii=False)
            if ratio < 0.5:
                return json.dumps({"category": "partial_correct", "hint": f"answer 部分覆盖 reference（命中率 {ratio:.2f}）"}, ensure_ascii=False)
            # answer 含 reference 之外的关键实体（幻觉弱信号）
            ans_tokens = set(re.split(r"[\s,，。、]+", answer))
            ref_tokens = set(ref_keywords)
            extra = ans_tokens - ref_tokens - {w for w in ans_tokens if len(w) < 2}
            if len(extra) > len(ref_tokens) * 0.5 and len(extra) > 10:
                return json.dumps({"category": "hallucination", "hint": f"answer 含大量 reference 之外的实体（{len(extra)} 个）"}, ensure_ascii=False)
        return json.dumps({"category": "quality_issue", "hint": "未匹配明确失败模式"}, ensure_ascii=False)

    return {
        "inspect_turns": inspect_turns,
        "find_low_scoring_turns": find_low_scoring_turns,
        "compare_answer_reference": compare_answer_reference,
        "categorize_failure": categorize_failure,
    }


# ── ReAct 解析（JSON-based） ──
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
        return {"type": "final", "thought": thought, "final_answer": parsed["final_answer"]}
    if "action" in parsed:
        tool_name = str(parsed["action"]).strip()
        tool_input = parsed.get("action_input") or parsed.get("input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        return {"type": "action", "thought": thought, "tool": tool_name, "input": tool_input}
    return None


# ── 主入口 ──
def agentic_judge_multi_turn(
    turn_records: list[dict[str, Any]],
    per_turn_scores: list[float],
    task_overview: str = "",
) -> dict:
    """ReAct agent 版多轮评测裁判。

    返回 {
        overall_score, root_cause, fix_suggestion, failure_category,
        trace, judge: "agentic", iterations
    }。
    失败兜底返回 {overall_score: 0.5, root_cause: "agent unavailable", judge: "fallback"}。
    """
    if not turn_records:
        return {
            "overall_score": 0.0,
            "root_cause": "no turn records",
            "fix_suggestion": "",
            "failure_category": "no_data",
            "trace": [],
            "judge": "agentic",
            "iterations": 0,
        }

    tools = _build_tools(turn_records, per_turn_scores)
    messages = [
        {"role": "system", "content": AGENT_SYS_PROMPT},
        {
            "role": "user",
            "content": (
                f"任务概述: {task_overview}\n"
                f"共 {len(turn_records)} 轮对话，per_turn_scores={per_turn_scores}。\n"
                "请用工具查数据，给出 overall_score + root_cause + fix_suggestion + failure_category。"
            ),
        },
    ]

    trace_log: list[dict] = []  # ReAct 每轮步骤，供可观测

    for iteration in range(MAX_ITER):
        response, usage = _chat_completion(messages, temperature=0.0)
        if not response:
            logger.warning("Agentic judge LLM call failed at iteration %d", iteration + 1)
            return {
                "overall_score": 0.5,
                "root_cause": "agent LLM unavailable",
                "fix_suggestion": "回退单轮 judge_correctness",
                "failure_category": "agent_failed",
                "trace": trace_log,
                "judge": "agentic_fallback",
                "iterations": iteration + 1,
            }

        trace_log.append({"iteration": iteration + 1, "thought_raw": response[:500]})

        # 解析 JSON ReAct 响应
        parsed_resp = _parse_response(response)
        if parsed_resp is None:
            # 解析失败，提示 LLM 严格按 JSON 格式输出
            messages.append({"role": "assistant", "content": response})
            messages.append(
                {
                    "role": "user",
                    "content": "请严格输出 JSON 格式：{\"thought\":..., \"action\":..., \"action_input\":{...}} 或 {\"thought\":..., \"final_answer\":{...}}",
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
            result = {
                "overall_score": _clamp01(final.get("overall_score")),
                "root_cause": str(final.get("root_cause", ""))[:500],
                "fix_suggestion": str(final.get("fix_suggestion", ""))[:500],
                "failure_category": str(final.get("failure_category", "unknown"))[:100],
                "trace": trace_log,
                "judge": "agentic",
                "iterations": iteration + 1,
            }
            _report_judge_to_langfuse(
                "agentic_multi_turn",
                messages[-1].get("content", ""),
                json.dumps(result, ensure_ascii=False),
                usage,
            )
            logger.info(
                "Agentic judge finished in %d iterations: score=%.2f, category=%s",
                iteration + 1,
                result["overall_score"],
                result["failure_category"],
            )
            return result

        # 2. action 分支：调工具
        tool_name = parsed_resp["tool"]
        tool_input = parsed_resp["input"]
        tool_fn = tools.get(tool_name)
        if tool_fn is None:
            observation = f"Error: tool '{tool_name}' not found. Available: {list(tools.keys())}"
        else:
            try:
                observation = tool_fn(tool_input)
            except Exception as exc:  # noqa: BLE001 - 工具调用失败不能中断 agent
                observation = f"Error: {str(exc)[:200]}"

        trace_log[-1]["action"] = tool_name
        trace_log[-1]["action_input"] = tool_input
        trace_log[-1]["observation"] = observation[:500]
        trace_log[-1]["thought"] = parsed_resp.get("thought", "")

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    # 超过 max_iter 还没 final，兜底
    logger.warning("Agentic judge exceeded %d iterations, falling back", MAX_ITER)
    return {
        "overall_score": 0.5,
        "root_cause": "agent max iterations exceeded",
        "fix_suggestion": "考虑回退到单轮 judge 或扩展 MAX_ITER",
        "failure_category": "agent_timeout",
        "trace": trace_log,
        "judge": "agentic_timeout",
        "iterations": MAX_ITER,
    }
