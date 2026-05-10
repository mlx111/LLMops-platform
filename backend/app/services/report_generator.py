"""Report generator for evaluation runs. Produces Markdown + summary JSON."""

from collections import Counter

from sqlalchemy.orm import Session

from app.models.dataset import EvalCase
from app.models.run import EvalResult, EvalRun


def generate_report(db: Session, run_id: int) -> tuple[str, dict] | None:
    """Generate a Markdown report and summary JSON for a run."""
    run = db.get(EvalRun, run_id)
    if not run or run.status not in ("completed", "failed", "error"):
        return None

    results = db.query(EvalResult).filter(EvalResult.run_id == run_id).all()
    if not results:
        return None

    # ── Summary stats ──
    total = len(results)
    passed = sum(1 for r in results if r.status == "passed")
    failed = sum(1 for r in results if r.status == "failed")
    errored = sum(1 for r in results if r.status == "error")
    pass_rate = round(passed / total * 100, 1) if total else 0

    all_scores, all_latencies, all_tokens = _collect_metrics(results)

    avg_score = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0
    avg_latency = round(sum(all_latencies) / len(all_latencies), 1) if all_latencies else 0
    avg_tokens = round(sum(all_tokens) / len(all_tokens), 1) if all_tokens else 0

    # ── Per-case-type pass rate ──
    case_map = _resolve_cases(db, results)
    type_stats = _per_type_stats(results, case_map)

    # ── Failure distribution ──
    failure_counter: Counter[str] = Counter()
    for r in results:
        if r.status in ("failed", "error"):
            if r.failure_reason:
                for reason in r.failure_reason.split(";"):
                    reason = reason.strip()
                    if reason:
                        failure_counter[reason] += 1
            else:
                failure_counter["unknown"] += 1

    failure_dist = dict(failure_counter.most_common())

    # ── Best / Worst cases ──
    scored = _rank_cases(results, case_map)

    # ── Build report ──
    md = _render_markdown(
        run=run,
        total=total, passed=passed, failed=failed, errored=errored,
        pass_rate=pass_rate,
        avg_score=avg_score, avg_latency=avg_latency, avg_tokens=avg_tokens,
        type_stats=type_stats,
        failure_dist=failure_dist,
        scored_cases=scored,
    )

    summary = {
        "run_id": run.id,
        "run_name": run.name,
        "total_cases": total,
        "passed": passed,
        "failed": failed,
        "errored": errored,
        "pass_rate": pass_rate,
        "avg_score": avg_score,
        "avg_latency_ms": avg_latency,
        "avg_tokens": avg_tokens,
        "failure_distribution": failure_dist,
        "per_type_pass_rate": type_stats,
        "top_best_case_ids": [c["case_id"] for c in scored[:5]],
        "top_worst_case_ids": [c["case_id"] for c in scored[-5:]],
    }

    return md, summary


# ── Helpers ──

def _collect_metrics(results: list[EvalResult]):
    scores = []
    latencies = []
    tokens = []
    for r in results:
        latencies.append(r.latency_ms)
        tokens.append(r.input_tokens + r.output_tokens)
        if r.scores:
            for s in r.scores.values():
                if isinstance(s, dict) and "score" in s:
                    scores.append(s["score"])
    return scores, latencies, tokens


def _resolve_cases(db: Session, results: list[EvalResult]) -> dict[int, EvalCase]:
    case_ids = {r.case_id for r in results}
    cases = db.query(EvalCase).filter(EvalCase.id.in_(case_ids)).all()
    return {c.id: c for c in cases}


def _per_type_stats(results: list[EvalResult], case_map: dict[int, EvalCase]) -> dict:
    by_type: dict[str, dict[str, int]] = {}
    for r in results:
        case = case_map.get(r.case_id)
        ct = case.case_type if case else "unknown"
        entry = by_type.setdefault(ct, {"total": 0, "passed": 0})
        entry["total"] += 1
        if r.status == "passed":
            entry["passed"] += 1
    return {
        k: {"total": v["total"], "passed": v["passed"],
            "pass_rate": round(v["passed"] / v["total"] * 100, 1) if v["total"] else 0}
        for k, v in by_type.items()
    }


def _rank_cases(results: list[EvalResult], case_map: dict[int, EvalCase]) -> list[dict]:
    entries = []
    for r in results:
        case = case_map.get(r.case_id)
        case_input = case.input[:80] if case else "?"
        avg = _case_avg_score(r)
        entries.append({
            "case_id": r.case_id,
            "input_snippet": case_input,
            "status": r.status,
            "avg_score": round(avg, 4),
            "failure_reason": r.failure_reason,
            "latency_ms": r.latency_ms,
        })
    entries.sort(key=lambda e: e["avg_score"])
    return entries


def _case_avg_score(result: EvalResult) -> float:
    if not result.scores:
        return 0
    vals = [s["score"] for s in result.scores.values()
            if isinstance(s, dict) and "score" in s]
    return sum(vals) / len(vals) if vals else 0


def _render_markdown(
    run: EvalRun,
    total: int, passed: int, failed: int, errored: int,
    pass_rate: float,
    avg_score: float, avg_latency: float, avg_tokens: float,
    type_stats: dict,
    failure_dist: dict,
    scored_cases: list[dict],
) -> str:
    lines = []

    config = run.config_json or {}
    target_mode = config.get("target_mode", "unknown")
    eval_mode = config.get("evaluation_mode", "unknown")

    lines.append(f"# Evaluation Report: {run.name}")
    lines.append(f"")
    lines.append(f"**Run ID**: {run.id}  ")
    lines.append(f"**Dataset ID**: {run.dataset_id}  ")
    lines.append(f"**Target Mode**: {target_mode}  ")
    lines.append(f"**Evaluation Mode**: {eval_mode}  ")
    if target_mode != "live" or eval_mode != "live":
        lines.append(f"> ⚠️ This report contains demo/simulated data and does not reflect real system performance.")
    lines.append(f"**Created**: {run.created_at.isoformat()}  ")
    lines.append(f"**Finished**: {run.finished_at.isoformat() if run.finished_at else '-'}  ")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"| Metric | Value |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total Cases | {total} |")
    lines.append(f"| Passed | {passed} ({pass_rate}%) |")
    lines.append(f"| Failed | {failed} |")
    lines.append(f"| Errors | {errored} |")
    lines.append(f"| Average Score | {avg_score:.3f} |")
    lines.append(f"| Average Latency | {avg_latency:.0f}ms |")
    lines.append(f"| Average Tokens | {avg_tokens:.0f} |")
    lines.append("")

    # Per-type pass rate
    if type_stats:
        lines.append("## Pass Rate by Case Type")
        lines.append("")
        lines.append(f"| Type | Total | Passed | Pass Rate |")
        lines.append(f"|------|-------|--------|-----------|")
        for ct, st in sorted(type_stats.items()):
            lines.append(f"| {ct} | {st['total']} | {st['passed']} | {st['pass_rate']}% |")
        lines.append("")

    # Failure distribution
    if failure_dist:
        lines.append("## Failure Distribution")
        lines.append("")
        lines.append(f"| Reason | Count |")
        lines.append(f"|--------|-------|")
        for reason, count in failure_dist.items():
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    # Top worst cases
    worst = [c for c in scored_cases if c["status"] in ("failed", "error")][:10]
    if worst:
        lines.append("## Failure Cases (Worst First)")
        lines.append("")
        lines.append(f"| Case ID | Input | Score | Reason |")
        lines.append(f"|---------|-------|-------|--------|")
        for case in worst:
            reason = (case["failure_reason"] or "-")[:60]
            lines.append(
                f"| {case['case_id']} | {case['input_snippet']} | "
                f"{case['avg_score']:.3f} | {reason} |"
            )
        lines.append("")

    # Best cases
    best = [c for c in reversed(scored_cases) if c["status"] == "passed"][:5]
    if best:
        lines.append("## Top Performing Cases")
        lines.append("")
        lines.append(f"| Case ID | Input | Score | Latency |")
        lines.append(f"|---------|-------|-------|---------|")
        for case in best:
            lines.append(
                f"| {case['case_id']} | {case['input_snippet']} | "
                f"{case['avg_score']:.3f} | {case['latency_ms']}ms |"
            )
        lines.append("")

    lines.append("## Cost & Latency")
    lines.append("")
    lines.append(f"- **Average Latency**: {avg_latency:.0f}ms")
    lines.append(f"- **Average Tokens**: {avg_tokens:.0f}")
    lines.append(f"- **Total Cases**: {total}")
    lines.append("")

    # Suggestions
    total_failures = sum(failure_dist.values())
    if total_failures > 0:
        top_reason, top_count = max(failure_dist.items(), key=lambda x: x[1])
        pct = round(top_count / total_failures * 100, 1)
        lines.append("## Optimization Suggestions")
        lines.append("")

        if top_reason == "hallucination":
            lines.append(
                f"- **Hallucination** accounts for {pct}% of failures. "
                f"Consider enhancing retriever recall or tuning the prompt "
                f"to ground answers more strictly in retrieved context."
            )
        elif top_reason in ("retrieval_miss", "low_context_precision"):
            lines.append(
                f"- **{top_reason}** accounts for {pct}% of failures. "
                f"Review embedding model choice, chunk size, or reranker threshold."
            )
        elif top_reason == "timeout":
            lines.append(
                f"- **Timeout** accounts for {pct}% of failures. "
                f"Consider reducing max_tokens, switching to a faster model, "
                f"or increasing the timeout threshold."
            )
        elif top_reason == "high_cost":
            lines.append(
                f"- **High cost** accounts for {pct}% of failures. "
                f"Optimize prompt length and consider using a smaller model "
                f"for simpler queries."
            )
        elif top_reason in ("tool_selection_error", "tool_argument_error"):
            lines.append(
                f"- **{top_reason}** accounts for {pct}% of failures. "
                f"Review the tool definitions and add more few-shot examples "
                f"for correct tool selection and argument formatting."
            )
        else:
            lines.append(
                f"- The top failure category is **{top_reason}** ({pct}%). "
                f"Review individual failure cases for targeted improvements."
            )
        lines.append("")

    lines.append("---")
    lines.append("*Generated by LLMOps Evaluation Platform*")
    lines.append("")

    return "\n".join(lines)
