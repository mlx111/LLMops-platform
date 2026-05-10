"""Failure classifier for evaluation results. Rule-based + LLM Judge supplement."""

from app.models.dataset import EvalCase
from app.services.logger import logger


# ── Thresholds ──
LATENCY_THRESHOLD_MS = 30_000
TOKEN_THRESHOLD = 10_000
FAITHFULNESS_THRESHOLD = 0.5
CONTEXT_PRECISION_THRESHOLD = 0.3
MIN_RETRIEVED_FOR_PRECISION = 5


def classify(result_scores: dict, case: EvalCase, latency_ms: int = 0,
            input_tokens: int = 0, output_tokens: int = 0,
            actual_output: str = "", actual_tool: str = "",
            actual_args: dict | None = None) -> list[str]:
    """Classify a failed result into one or more failure categories."""
    reasons = []

    scores = _flatten_scores(result_scores)

    # 1. hallucination
    faithfulness = scores.get("Faithfulness")
    if faithfulness is not None and faithfulness < FAITHFULNESS_THRESHOLD:
        if actual_output and _has_hallucination_markers(actual_output, case):
            reasons.append("hallucination")

    # 2. retrieval_miss
    if case.reference_context_ids and "ContextRecall" in scores:
        recall = scores["ContextRecall"]
        if recall is not None and recall < 0.5:
            reasons.append("retrieval_miss")

    # 3. low_context_precision
    if case.reference_context_ids and len(case.reference_context_ids) >= MIN_RETRIEVED_FOR_PRECISION:
        precision = scores.get("ContextPrecision")
        if precision is not None and precision < CONTEXT_PRECISION_THRESHOLD:
            reasons.append("low_context_precision")

    # 4. evidence_ignored
    faithfulness = scores.get("Faithfulness", 1.0)
    recall = scores.get("ContextRecall", 0)
    if recall is not None and recall > 0.6 and faithfulness is not None and faithfulness < FAITHFULNESS_THRESHOLD:
        reasons.append("evidence_ignored")

    # 5. tool_selection_error
    if case.expected_tool and case.expected_tool != actual_tool:
        reasons.append("tool_selection_error")

    # 6. tool_argument_error
    if case.expected_tool and case.expected_args:
        if actual_tool == case.expected_tool and actual_args:
            if not _args_match(actual_args, case.expected_args):
                reasons.append("tool_argument_error")

    # 7. timeout
    if latency_ms > LATENCY_THRESHOLD_MS:
        reasons.append("timeout")

    # 8. high_cost
    total_tokens = input_tokens + output_tokens
    if total_tokens > TOKEN_THRESHOLD:
        reasons.append("high_cost")

    # 9. prompt_constraint_violation
    if actual_output and _has_format_violation(actual_output, case):
        reasons.append("prompt_constraint_violation")

    # Fallback: if nothing matched, tag as quality_issue
    if not reasons:
        reasons.append("quality_issue")

    logger.debug(f"Failure classification: case_id={case.id}, reasons={reasons}")
    return reasons


def classify_with_llm(result_scores: dict, case: EvalCase, provider: str = "deepseek",
                      model: str = "deepseek-chat") -> list[str]:
    """Enrich rule-based classification with LLM Judge attribution."""
    rule_reasons = classify(result_scores, case)

    try:
        from app.services.runner import run_case_evaluation
        llm_result = run_case_evaluation(
            case_input=case.input,
            actual_output=case.reference_answer or "",
            case_type="qa",
            reference_answer=case.reference_answer,
            provider=provider,
            model=model,
        )
        llm_scores = llm_result.get("scores", {})
        llm_reason = _extract_llm_reasons(llm_scores)
        if llm_reason and llm_reason not in rule_reasons:
            rule_reasons.append(llm_reason)
    except Exception:
        logger.warning("LLM-based failure classification failed", exc_info=True)

    return rule_reasons


# ── Helpers ──

def _flatten_scores(scores: dict) -> dict[str, float]:
    out = {}
    for name, val in (scores or {}).items():
        if isinstance(val, dict) and "score" in val:
            out[name] = val["score"]
        elif isinstance(val, (int, float)):
            out[name] = val
    return out


def _has_hallucination_markers(output: str, case: EvalCase) -> bool:
    """Check if output contains content not in reference or context (simple heuristic)."""
    ref_words = set((case.reference_answer or "").lower().split())
    out_words = set(output.lower().split())
    if not ref_words:
        return False
    novel = out_words - ref_words
    return len(novel) / max(len(out_words), 1) > 0.5


def _args_match(actual: dict, expected: dict) -> bool:
    keys = set(expected.keys())
    if not keys:
        return True
    return all(
        str(actual.get(k, "")).strip().lower() == str(expected[k]).strip().lower()
        for k in keys
    )


def _has_format_violation(output: str, case: EvalCase) -> bool:
    """Check for obvious format/length constraint violations."""
    if not output:
        return False
    output_lower = output.lower()
    # Common constraint violation patterns
    markers = [
        "i cannot", "i'm unable", "i am unable",
        "as an ai", "i apologize", "sorry,",
    ]
    if any(m in output_lower for m in markers):
        return True
    if len(output) < 10:
        return True
    return False


def _extract_llm_reasons(scores: dict) -> str | None:
    """Extract failure reason from LLM judge scores."""
    for name, val in (scores or {}).items():
        if isinstance(val, dict):
            reason = val.get("reason", "")
            if reason and not val.get("success", True):
                if "hallucination" in reason.lower():
                    return "hallucination_llm"
                if "irrelevant" in reason.lower():
                    return "answer_irrelevant"
                if "incorrect" in reason.lower():
                    return "answer_incorrect"
    return None
