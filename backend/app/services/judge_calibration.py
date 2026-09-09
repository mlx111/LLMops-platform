"""Judge calibration: agreement between rule-based scores and LLM-judge scores,
plus position-bias measurement for pairwise preference judging.

Metrics
-------
- exact_agreement:   fraction of pairs where |rule - judge| <= score_tol
- pass_fail_agreement: fraction where both sides agree on pass/fail (>= pass_threshold)
- mae:               mean absolute score difference
- mean_bias:         mean(judge - rule); positive => judge is more lenient
- spearman:          Spearman rank correlation (rank ordering consistency)
- flip_rate:         (position bias) fraction of pairwise calls whose preferred
                     answer flips when A/B positions are swapped
"""

from __future__ import annotations

from app.services.logger import logger
from app.services.llm_judge import judge_preference


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based) for ties."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg_rank
        i = j + 1
    return ranks


def spearman(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if vx == 0 or vy == 0:
        return 0.0
    return cov / (vx ** 0.5 * vy ** 0.5)


def score_agreement(pairs: list[dict], score_tol: float = 0.15,
                    pass_threshold: float = 0.5) -> dict:
    """pairs: [{case_id, rule_score, judge_score, metric}].

    Entries with judge_score is None (judge call failed) are skipped.
    """
    valid = [p for p in pairs if p.get("judge_score") is not None and p.get("rule_score") is not None]
    n = len(valid)
    if n == 0:
        return {"n": 0, "exact_agreement": None, "pass_fail_agreement": None,
                "mae": None, "mean_bias": None, "spearman": None}

    exact = sum(1 for p in valid if abs(p["rule_score"] - p["judge_score"]) <= score_tol) / n
    pf = sum(
        1 for p in valid
        if (p["rule_score"] >= pass_threshold) == (p["judge_score"] >= pass_threshold)
    ) / n
    mae = sum(abs(p["rule_score"] - p["judge_score"]) for p in valid) / n
    bias = sum(p["judge_score"] - p["rule_score"] for p in valid) / n
    rho = spearman([p["rule_score"] for p in valid], [p["judge_score"] for p in valid])

    # Confusion matrix on pass/fail
    tp = sum(1 for p in valid if p["rule_score"] >= pass_threshold and p["judge_score"] >= pass_threshold)
    fn = sum(1 for p in valid if p["rule_score"] >= pass_threshold and p["judge_score"] < pass_threshold)
    fp = sum(1 for p in valid if p["rule_score"] < pass_threshold and p["judge_score"] >= pass_threshold)
    tn = sum(1 for p in valid if p["rule_score"] < pass_threshold and p["judge_score"] < pass_threshold)

    return {
        "n": n,
        "score_tol": score_tol,
        "pass_threshold": pass_threshold,
        "exact_agreement": round(exact, 4),
        "pass_fail_agreement": round(pf, 4),
        "mae": round(mae, 4),
        "mean_bias": round(bias, 4),
        "spearman": round(rho, 4),
        "confusion": {"rule_pass_judge_pass": tp, "rule_pass_judge_fail": fn,
                      "rule_fail_judge_pass": fp, "rule_fail_judge_fail": tn},
    }


def position_bias_check(questions_answers: list[dict], max_items: int = 10) -> dict:
    """Measure position bias of pairwise judging.

    questions_answers: [{question, answer_a, answer_bias_label...}]. For each
    item we ask the judge twice, swapping A/B in the second call. A consistent
    judge should map its preference back to the same *answer content* after the
    swap. A flip (or tie change) indicates position bias.

    Returns flip_rate in [0, 1]; lower is better.
    """
    items = questions_answers[:max_items]
    checked = 0
    flips = 0
    details = []
    for item in items:
        q, a, b = item["question"], item["answer_a"], item["answer_b"]
        r1 = judge_preference(q, a, b)
        r2 = judge_preference(q, b, a)  # swapped
        p1, p2 = r1.get("preferred"), r2.get("preferred")
        if p1 is None or p2 is None:
            details.append({"question": q[:60], "first": p1, "swapped": p2, "skipped": True})
            continue
        # After swap, preference for content A means: first call said A, second says B.
        consistent_content = (
            (p1 == "A" and p2 == "B") or
            (p1 == "B" and p2 == "A") or
            (p1 == "TIE" and p2 == "TIE")
        )
        checked += 1
        if not consistent_content:
            flips += 1
        details.append({"question": q[:60], "first": p1, "swapped": p2,
                        "consistent": consistent_content})
        logger.info(f"Position-bias probe: first={p1} swapped={p2} consistent={consistent_content}")
    flip_rate = round(flips / checked, 4) if checked else None
    return {
        "n": checked,
        "flips": flips,
        "flip_rate": flip_rate,
        "interpretation": (
            "no position bias detected" if flip_rate is not None and flip_rate <= 0.1
            else "mild position bias" if flip_rate is not None and flip_rate <= 0.3
            else "strong position bias — consider debiasing (swap averaging)"
        ),
        "details": details,
    }
