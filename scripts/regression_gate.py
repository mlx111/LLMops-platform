"""CI regression gate: compare a candidate run against a baseline run.

Exits non-zero when key metrics regress beyond thresholds, so a broken prompt
/ agent change turns CI red. Uses only the LLMOps HTTP API (stdlib urllib).

Two modes:
  1. Compare two existing runs:
       python regression_gate.py --baseline 10 --candidate 11
  2. Create a fresh candidate run from a dataset, wait for it, then compare:
       python regression_gate.py --baseline 10 --dataset-id 12 \
           --target-url http://host.docker.internal:8080/evaluation/target

Thresholds (any violation fails the gate):
  --max-avg-drop       max allowed drop of overall avg_score (default 0.05)
  --max-metric-drop    max allowed drop of any single metric avg (default 0.10)
  --max-flips          max allowed cases flipping passed -> failed (default 0)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

# Metrics that are "higher is better" quality scores.
QUALITY_METRICS = {
    "AnswerRelevancy", "Correctness", "Faithfulness",
    "ContextRecall", "ContextPrecision", "ToolCorrectness",
    "ArgumentAccuracy", "TaskCompletion", "TaskSuccess",
    "ToolSelectionAccuracy", "StepEfficiency",
}


def api(method: str, base: str, path: str, payload: dict | None = None, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"API error {method} {path}: {exc.code} {detail[:300]}") from exc


def wait_for_run(base: str, run_id: int, poll_interval: int = 10, timeout_s: int = 3600) -> dict:
    print(f"Waiting for run {run_id} to finish ...", flush=True)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = api("GET", base, f"/api/runs/{run_id}", timeout=30)
        status = run.get("status")
        if status in ("completed", "failed", "error"):
            print(f"Run {run_id} status: {status}")
            return run
        progress = api("GET", base, f"/api/runs/{run_id}/progress", timeout=30)
        print(f"  ... {progress.get('completed_cases', '?')}/{progress.get('total_cases', '?')} "
              f"(passed={progress.get('passed_cases', '?')}, failed={progress.get('failed_cases', '?')})",
              flush=True)
        time.sleep(poll_interval)
    raise SystemExit(f"Timed out waiting for run {run_id}")


def gate(base: str, baseline_id: int, candidate_id: int,
         max_avg_drop: float, max_metric_drop: float, max_flips: int,
         hard_flip_margin: float = 0.15, max_edge_flips: int = 5) -> int:
    cmp = api("GET", base,
              f"/api/runs/compare?run1={baseline_id}&run2={candidate_id}", timeout=60)
    diffs = cmp.get("metric_diffs", {})
    regressed = cmp.get("regressed_cases", [])
    r1, r2 = cmp.get("run1", {}), cmp.get("run2", {})

    print("=" * 72)
    print(f"Regression gate: baseline=run {baseline_id} ({r1.get('name', '')})")
    print(f"                 candidate=run {candidate_id} ({r2.get('name', '')})")
    print("=" * 72)
    print(f"{'metric':<24}{'baseline':>12}{'candidate':>12}{'delta':>10}")
    print("-" * 72)

    violations: list[str] = []

    # Overall avg score
    avg_delta = diffs.get("avg_score", 0.0) or 0.0
    print(f"{'avg_score':<24}{r1.get('avg_score', 0):>12.4f}{r2.get('avg_score', 0):>12.4f}{avg_delta:>+10.4f}")
    if avg_delta < -max_avg_drop:
        violations.append(f"avg_score dropped {avg_delta:+.4f} (threshold -{max_avg_drop})")

    # Per-metric quality scores
    for name in sorted(QUALITY_METRICS):
        if name in diffs:
            delta = diffs[name] or 0.0
            # baseline/candidate per-metric averages are not in the response; show delta.
            print(f"{name:<24}{'':>12}{'':>12}{delta:>+10.4f}")
            if delta < -max_metric_drop:
                violations.append(f"{name} dropped {delta:+.4f} (threshold -{max_metric_drop})")

    lat_delta = diffs.get("avg_latency_ms", 0.0) or 0.0
    print(f"{'avg_latency_ms':<24}{r1.get('avg_latency_ms', 0):>12.0f}{r2.get('avg_latency_ms', 0):>12.0f}{lat_delta:>+10.0f}")
    print(f"{'avg_tokens':<24}{r1.get('avg_tokens', 0):>12.0f}{r2.get('avg_tokens', 0):>12.0f}{diffs.get('avg_tokens', 0):>+10.0f}")

    # Status flips passed->failed. LLM answers fluctuate run-to-run, so a flip
    # right at the pass threshold is noise; a clearly-passing case collapsing
    # well below the threshold is a real regression.
    all_flips = [c for c in regressed
                 if c.get("run1_status") == "passed" and c.get("run2_status") == "failed"]
    hard_flips = [c for c in all_flips
                  if c["run1_score"] >= 0.6 and c["delta"] <= -hard_flip_margin]
    edge_flips = [c for c in all_flips if c not in hard_flips]
    print("-" * 72)
    print(f"Regressed cases: {len(regressed)}  |  passed->failed flips: {len(all_flips)}")
    print(f"  hard flips (clear pass -> deep fail): {len(hard_flips)} (allowed {max_flips})")
    print(f"  edge flips (threshold noise):          {len(edge_flips)} "
          f"(tolerated {max_edge_flips})")
    if len(hard_flips) > max_flips:
        violations.append(f"{len(hard_flips)} HARD case flips (clear pass -> deep fail), "
                          f"allowed {max_flips}")
    if len(edge_flips) > max_edge_flips:
        violations.append(f"{len(edge_flips)} edge flips (threshold noise) exceed {max_edge_flips} "
                          f"— possible systematic drift")

    if hard_flips:
        print("\nHard-flip cases (real regressions):")
        for c in sorted(hard_flips, key=lambda x: x["delta"])[:10]:
            print(f"  case {c['case_id']:>4} {c['run1_score']:.3f} -> {c['run2_score']:.3f} "
                  f"({c['delta']:+.3f})  {c['input'][:60]}")

    print("=" * 72)
    if violations:
        print("GATE FAILED — regressions detected:")
        for v in violations:
            print(f"  ✗ {v}")
        print("=" * 72)
        return 1
    print("GATE PASSED — no significant regression.")
    print("=" * 72)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--baseline", type=int, required=True, help="baseline run id")
    p.add_argument("--candidate", type=int, help="candidate run id (if not creating one)")
    p.add_argument("--dataset-id", type=int, help="dataset for a fresh candidate run")
    p.add_argument("--target-url", help="target system URL for the candidate run")
    p.add_argument("--name", default=f"ci-gate-candidate")
    p.add_argument("--concurrency", type=int, default=3)
    p.add_argument("--target-timeout", type=int, default=120)
    p.add_argument("--max-avg-drop", type=float, default=0.05)
    p.add_argument("--max-metric-drop", type=float, default=0.10)
    p.add_argument("--max-flips", type=int, default=0,
                   help="allowed HARD flips (clear pass collapsing to deep fail)")
    p.add_argument("--max-edge-flips", type=int, default=5,
                   help="tolerated threshold-edge flips (LLM run-to-run noise)")
    p.add_argument("--wait-timeout", type=int, default=3600)
    args = p.parse_args()

    candidate_id = args.candidate
    if candidate_id is None:
        if not args.dataset_id:
            p.error("either --candidate or --dataset-id is required")
        body = {
            "name": args.name,
            "dataset_id": args.dataset_id,
            "concurrency": args.concurrency,
            "target_timeout": args.target_timeout,
        }
        if args.target_url:
            body["target_url"] = args.target_url
            body["target_type"] = "rag"
        run = api("POST", args.base_url, "/api/runs", body, timeout=30)
        candidate_id = run["id"]
        print(f"Created candidate run {candidate_id}")
        wait_for_run(args.base_url, candidate_id, timeout_s=args.wait_timeout)

    return gate(args.base_url, args.baseline, candidate_id,
                args.max_avg_drop, args.max_metric_drop, args.max_flips,
                max_edge_flips=args.max_edge_flips)


if __name__ == "__main__":
    sys.exit(main())
