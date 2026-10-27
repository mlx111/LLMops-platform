"""Multi-turn conversation evaluation.

A genuine multi-turn case is driven turn-by-turn against the target system
under ONE shared session_id, so the target's own conversation memory is
exercised. Three layered metrics separate distinct failure modes:

- PerTurnCorrectness — mean per-turn answer correctness vs that turn's
  reference. A low score means individual turns are answered wrongly.
- ContextualConsistency — do later turns carry forward the entities,
  constraints and decisions established in earlier turns (coreference, no
  contradictions)? A low score with high per-turn correctness means the
  model answers each turn in isolation: the classic "lost context" failure.
- TaskCompletion — does the final (or accumulated) output complete the whole
  task described by the overall reference answer?

Rule-based mode works offline with zero API keys; LLM-judge mode uses the
platform's self-contained OpenAI-compatible judge (app.services.llm_judge).
"""

from __future__ import annotations

import time
from typing import Any

from app.services.logger import logger
from app.services.runner import _demo_correctness, _tokenize, count_tokens


def _content_terms(text: str) -> set[str]:
    """Content-bearing terms (ASCII words / CJK bigrams), mirroring runner."""
    return {t for t in _tokenize(text or "") if len(t) >= 2}


def _rule_contextual_consistency(turn_records: list[dict[str, Any]]) -> dict:
    """Expected-entity carry-over rate across turns.

    For turn i (i>=2), the terms that SHOULD carry forward are those present
    in BOTH the previous turn's user+answer AND the current turn's reference
    answer (denominator). Scoring against all previous-turn terms would punish
    the model for not repeating one-off details (OS commands, examples) that a
    coherent reply naturally leaves behind. A turn with no reference-expected
    carry terms is skipped; if none remain, the metric is neutral (N/A).
    """
    carry_scores: list[float] = []
    per_turn: list[float] = []
    for i in range(1, len(turn_records)):
        prev_terms = _content_terms(
            (turn_records[i - 1].get("user") or "")
            + " " + (turn_records[i - 1].get("answer") or "")
        )
        cur_ref_terms = _content_terms(turn_records[i].get("reference") or "")
        expected_carry = prev_terms & cur_ref_terms
        if not expected_carry:
            continue
        cur_answer_terms = _content_terms(turn_records[i].get("answer") or "")
        carried = len(expected_carry & cur_answer_terms) / len(expected_carry)
        carry_scores.append(carried)
        per_turn.append(round(carried, 3))

    if not carry_scores:
        return {"score": 0.7, "reason": "No reference-expected carry terms; consistency N/A", "success": True}

    score = round(sum(carry_scores) / len(carry_scores), 4)
    return {
        "score": score,
        "reason": f"Expected-entity carry-over per turn: {per_turn}; avg={score:.2f}",
        "success": score >= 0.4,
    }


def _final_text(turn_records: list[dict[str, Any]]) -> str:
    return "\n".join(str(r.get("answer") or "") for r in turn_records)


def _build_transcript(turn_records: list[dict[str, Any]]) -> str:
    lines = []
    for i, r in enumerate(turn_records, start=1):
        lines.append(f"[Turn {i}] User: {r.get('user') or ''}")
        lines.append(f"[Turn {i}] Assistant: {r.get('answer') or '(空)'}")
    return "\n".join(lines)


def _score_entry(score: float | None, reason: str, threshold: float, fallback: dict | None = None) -> dict:
    if score is None:
        if fallback is not None:
            return fallback
        return {"score": 0.0, "reason": reason or "judge unavailable", "success": False}
    score = max(0.0, min(1.0, float(score)))
    return {"score": round(score, 4), "reason": reason, "success": score >= threshold}


def evaluate_multi_turn(
    turn_records: list[dict[str, Any]],
    task_overview: str = "",
    overall_reference: str | None = None,
    use_llm_judge: bool = False,
    model: str | None = None,
) -> dict:
    """Score a driven multi-turn conversation.

    ``turn_records``: [{"user": str, "answer": str, "reference": str|None}].
    """
    start = time.time()
    turn_details: list[dict[str, Any]] = []

    # ── 1. Per-turn correctness ──
    per_turn_scores: list[float] = []
    if use_llm_judge:
        from app.services.llm_judge import (
            judge_contextual_consistency,
            judge_correctness,
            judge_available,
        )
        use_llm_judge = judge_available()

    for i, rec in enumerate(turn_records, start=1):
        reference = rec.get("reference")
        answer = rec.get("answer") or ""
        user = rec.get("user") or ""
        llm_entry = None
        if use_llm_judge and reference:
            judged = judge_correctness(user, answer, reference)
            if judged["score"] is not None:
                llm_entry = _score_entry(judged["score"], judged["reason"], 0.5)
        rule_entry = _demo_correctness(answer, reference)
        entry = llm_entry or rule_entry
        per_turn_scores.append(float(entry["score"]))
        turn_details.append({
            "turn": i,
            "score": entry["score"],
            "reason": entry["reason"],
            "judge": "llm" if llm_entry else "rule",
        })

    avg_turn = round(sum(per_turn_scores) / len(per_turn_scores), 4) if per_turn_scores else 0.0
    per_turn_entry = {
        "score": avg_turn,
        "reason": "Per-turn scores: "
        + ", ".join(f"turn{d['turn']}={d['score']:.2f}" for d in turn_details)
        + f"; avg={avg_turn:.2f}",
        "success": avg_turn >= 0.5,
    }

    # ── 2. Contextual consistency ──
    rule_consistency = _rule_contextual_consistency(turn_records)
    if use_llm_judge and len(turn_records) >= 2:
        judged = judge_contextual_consistency(_build_transcript(turn_records), task_overview)
        consistency_entry = _score_entry(
            judged["score"], judged["reason"], 0.4, fallback=rule_consistency
        )
    else:
        consistency_entry = rule_consistency

    # ── 3. Overall task completion ──
    final_answer = _final_text(turn_records)
    rule_completion = _demo_correctness(final_answer, overall_reference or "")
    if use_llm_judge and overall_reference:
        judged = judge_correctness(task_overview, final_answer, overall_reference)
        completion_entry = _score_entry(
            judged["score"], judged["reason"], 0.5, fallback=rule_completion
        )
    else:
        completion_entry = rule_completion

    elapsed = int((time.time() - start) * 1000)
    input_text = "\n".join(
        [str(r.get("user") or "") for r in turn_records]
        + [str(r.get("reference") or "") for r in turn_records]
        + [task_overview, overall_reference or ""]
    )

    scores = {
        "PerTurnCorrectness": per_turn_entry,
        "ContextualConsistency": consistency_entry,
        "TaskCompletion": completion_entry,
    }

    # ── 4. Agentic root cause analysis（L1 Agentic Judge，多轮调工具找根因） ──
    # L0→L1 演进：单轮 LLM-judge 只打分；ReAct agent 多轮调工具查 trace/对比/归类，
    # 输出根因+修复建议。失败兜底回退，绝不中断评测主链路。
    agentic_analysis = None
    if use_llm_judge:
        try:
            from app.services.agentic_judge import agentic_judge_multi_turn
            agentic_analysis = agentic_judge_multi_turn(
                turn_records=turn_records,
                per_turn_scores=per_turn_scores,
                task_overview=task_overview,
            )
        except Exception as exc:  # noqa: BLE001 - agentic 失败不能中断评测
            logger.warning("Agentic judge failed: %s", str(exc)[:200])
            agentic_analysis = {
                "overall_score": 0.5,
                "root_cause": f"agentic judge error: {str(exc)[:200]}",
                "fix_suggestion": "",
                "failure_category": "agent_error",
                "judge": "agentic_error",
                "trace": [],
                "iterations": 0,
            }
    logger.info(
        "Multi-turn evaluated: turns=%d, per_turn=%.2f, consistency=%.2f, completion=%.2f",
        len(turn_records), avg_turn,
        float(consistency_entry["score"]), float(completion_entry["score"]),
    )
    return {
        "scores": scores,
        "turn_details": turn_details,
        "latency_ms": elapsed,
        "input_tokens": count_tokens(input_text, model),
        "output_tokens": count_tokens(final_answer, model),
        "agentic_analysis": agentic_analysis,
    }
