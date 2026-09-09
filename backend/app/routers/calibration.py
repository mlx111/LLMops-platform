"""Judge calibration API.

Re-scores finished run results with an LLM judge and compares against the
rule-based (demo) scores already stored, reporting agreement rates and
position bias — the standard diagnostics for LLM-as-a-judge reliability.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.database import SessionLocal
from app.models.dataset import EvalCase
from app.models.run import EvalResult, EvalRun
from app.services.judge_calibration import position_bias_check, score_agreement
from app.services.llm_judge import judge_available, judge_correctness

router = APIRouter(prefix="/api/calibration", tags=["calibration"])

# Rule metrics we can cross-check with the correctness judge.
_RULE_METRIC = {
    "qa": "Correctness",
    "rag": "AnswerRelevancy",
    "tool_calling": None,
    "multi_turn": "TaskCompletion",
    "agent_trajectory": None,
}


@router.get("/status")
def judge_status() -> dict:
    return {"judge_available": judge_available()}


@router.post("/run/{run_id}")
def calibrate_run(run_id: int, limit: int = Query(20, ge=1, le=100)) -> dict:
    """Re-score up to *limit* qa/rag/multi_turn results of a finished run with
    the LLM judge, then compute rule-vs-judge agreement and position bias."""
    if not judge_available():
        raise HTTPException(
            status_code=400,
            detail="No LLM judge configured. Set DASHSCOPE_API_KEY/DEEPSEEK_API_KEY/OPENAI_API_KEY.",
        )

    db = SessionLocal()
    try:
        run = db.get(EvalRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        results = (
            db.query(EvalResult, EvalCase)
            .join(EvalCase, EvalResult.case_id == EvalCase.id)
            .filter(EvalResult.run_id == run_id)
            .all()
        )

        pairs: list[dict] = []
        answer_pool: list[dict] = []
        for result, case in results:
            metric = _RULE_METRIC.get(case.case_type)
            scores = result.scores or {}
            if not metric or metric not in scores or not result.actual_output:
                continue
            rule_score = scores[metric].get("score") if isinstance(scores[metric], dict) else None
            if rule_score is None:
                continue
            judged = judge_correctness(case.input, result.actual_output, case.reference_answer or "")
            if judged.get("score") is None:
                continue
            pairs.append({
                "case_id": case.id,
                "case_type": case.case_type,
                "metric": metric,
                "rule_score": round(float(rule_score), 4),
                "judge_score": round(float(judged["score"]), 4),
                "rule_reason": (scores[metric] or {}).get("reason", "")[:200],
                "judge_reason": judged.get("reason", ""),
            })
            answer_pool.append({
                "question": case.input,
                "answer": result.actual_output,
                "score": float(rule_score),
            })
            if len(pairs) >= limit:
                break

        if not pairs:
            raise HTTPException(status_code=400, detail="No comparable results found in this run.")

        agreement = score_agreement(pairs)

        # Position bias: pair a high-score answer with a low-score answer on the
        # same question domain; judge twice with swapped A/B positions.
        answer_pool.sort(key=lambda x: x["score"])
        low = answer_pool[: len(answer_pool) // 2]
        high = answer_pool[len(answer_pool) // 2:]
        probe_items = []
        for lo, hi in zip(low, high):
            probe_items.append({
                "question": hi["question"],
                "answer_a": hi["answer"],
                "answer_b": lo["answer"],
            })
        bias = position_bias_check(probe_items, max_items=min(8, len(probe_items)))

        return {
            "run_id": run_id,
            "run_name": run.name,
            "judge_provider": "llm",
            "agreement": agreement,
            "position_bias": bias,
            "per_case": pairs,
        }
    finally:
        db.close()
