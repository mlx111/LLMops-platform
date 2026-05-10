"""Celery app and evaluation task."""

import datetime
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from celery import Celery

from app.config import settings
from app.services.logger import logger
from app.services.runner import _has_api_key, count_tokens
from app.services.url_safety import ValidatingHTTPAdapter, validate_target_url

celery_app = Celery(
    "llmops",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
)


def _publish_progress(run_id: int):
    """Publish run progress to Redis Pub/Sub."""
    try:
        from app.core.redis import get_redis
        from app.database import SessionLocal
        from app.models.run import EvalResult, EvalRun

        redis = get_redis()
        if redis is None:
            return

        db = SessionLocal()
        try:
            run = db.get(EvalRun, run_id)
            if not run:
                return
            completed = db.query(EvalResult.id).filter(
                EvalResult.run_id == run_id,
                EvalResult.status != "pending",
            ).count()
            passed = db.query(EvalResult.id).filter(
                EvalResult.run_id == run_id,
                EvalResult.status == "passed",
            ).count()
            failed = db.query(EvalResult.id).filter(
                EvalResult.run_id == run_id,
                EvalResult.status.in_(["failed", "error"]),
            ).count()
        finally:
            db.close()

        redis.publish(
            f"run:{run_id}:progress",
            json.dumps({
                "status": run.status,
                "total": run.total_cases,
                "completed": completed,
                "passed": passed,
                "failed": failed,
            }),
        )
    except Exception:
        logger.warning(f"Failed to publish progress for run {run_id}", exc_info=True)


def _evaluate_single_case(
    result_id: int,
    case_id: int,
    run_id: int,
    provider: str,
    model: str,
    case_input: str,
    case_type: str,
    reference_answer: str | None,
    reference_context_ids: list | None,
    expected_tool: str | None,
    expected_args: dict | None,
    config_json: dict | None = None,
) -> dict:
    """Evaluate a single case in its own DB session (thread-safe)."""
    from app.database import SessionLocal
    from app.models.dataset import EvalCase
    from app.models.run import EvalResult
    from app.services.runner import run_case_evaluation
    from app.services.tracer import Tracer

    db = SessionLocal()
    try:
        result = db.get(EvalResult, result_id)
        case = db.get(EvalCase, case_id)
        if not result or not case:
            return {"result_id": result_id, "status": "skipped"}

        tracer = Tracer(db)
        config = config_json or {}

        trace_id = tracer.start_trace(
            run_id=run_id,
            case_id=case_id,
            user_input=case_input,
            model=model,
        )

        target_url = config.get("target_url", "")
        with tracer.step(
            trace_id,
            "target_call" if target_url else "target_call (simulated)",
            "CHAIN",
            input_data={
                "query": case_input,
                "target_url": target_url or "(none - demo mode)",
                "case_type": case_type,
            },
        ) as step:
            actual_output, actual_tool, actual_args, target_latency, target_tokens = _call_target_system(case, config)
            tracer.set_step_output(
                step.id,
                {
                    "output": actual_output[:500],
                    "output_length": len(actual_output),
                    "tool_called": actual_tool,
                    "latency_ms": target_latency,
                },
                tokens=target_tokens,
            )

        with tracer.step(
            trace_id,
            "evaluation",
            "LLM",
            input_data={"provider": provider, "model": model, "case_type": case_type},
        ) as step:
            eval_result = run_case_evaluation(
                case_input=case_input,
                actual_output=actual_output,
                case_type=case_type,
                reference_answer=reference_answer,
                retrieval_context=reference_context_ids or [],
                expected_tool=expected_tool,
                actual_tool=actual_tool,
                expected_args=expected_args,
                actual_args=actual_args,
                provider=provider,
                model=model,
            )
            tracer.set_step_output(
                step.id,
                {"scores": eval_result.get("scores", {})},
                tokens=eval_result.get("input_tokens", 0) + eval_result.get("output_tokens", 0),
            )

        result.actual_output = eval_result["actual_output"]
        result.actual_tool = actual_tool
        result.actual_args = actual_args
        result.scores = eval_result["scores"]
        result.latency_ms = eval_result["latency_ms"]
        result.input_tokens = eval_result["input_tokens"]
        result.output_tokens = eval_result["output_tokens"]

        all_pass = all(score["success"] for score in eval_result["scores"].values())
        result.status = "passed" if all_pass else "failed"

        if not all_pass:
            result.failure_reason = _classify_failure(
                eval_result["scores"],
                case,
                latency_ms=eval_result["latency_ms"],
                input_tokens=eval_result["input_tokens"],
                output_tokens=eval_result["output_tokens"],
                actual_output=eval_result["actual_output"],
                actual_tool=(actual_tool or ""),
                actual_args=actual_args,
            )

        tracer.end_trace(trace_id, status=result.status)
        db.commit()

        logger.debug(
            f"Case {case_id} evaluated: status={result.status}, "
            f"target_latency={target_latency}ms, scores={list(eval_result['scores'].keys())}"
        )
        return {
            "result_id": result_id,
            "status": result.status,
            "latency_ms": result.latency_ms,
        }
    except Exception as exc:
        logger.exception(
            f"Case evaluation failed: result_id={result_id}, case_id={case_id}, error={str(exc)[:500]}"
        )
        try:
            result = db.get(EvalResult, result_id)
            if result:
                result.status = "error"
                result.failure_reason = str(exc)[:2000]
                db.commit()
        except Exception:
            db.rollback()
        return {"result_id": result_id, "status": "error", "error": str(exc)[:500]}
    finally:
        db.close()


def run_evaluation_task(run_id: int):
    """Core evaluation logic with concurrent case execution."""
    from app.database import SessionLocal
    from app.models.dataset import EvalCase
    from app.models.run import EvalResult, EvalRun
    from app.models.version import Version

    db = SessionLocal()
    try:
        run = db.get(EvalRun, run_id)
        if not run or run.status == "paused":
            return {"status": "skipped"}

        run.status = "running"
        db.commit()
        _publish_progress(run.id)

        provider = (run.config_json or {}).get("provider", "deepseek")
        model = (run.config_json or {}).get("model", "deepseek-chat")
        concurrency = (run.config_json or {}).get("concurrency", 5)
        target_url = (run.config_json or {}).get("target_url", "")

        if run.model_version_id:
            mv = db.get(Version, run.model_version_id)
            if mv and mv.config_json:
                if not run.config_json or "provider" not in (run.config_json or {}):
                    provider = mv.config_json.get("provider", provider)
                if not run.config_json or "model" not in (run.config_json or {}):
                    model = mv.config_json.get("model", model)

        config_json = dict(run.config_json or {})
        config_json["target_mode"] = "live" if target_url else "demo"
        config_json["evaluation_mode"] = "live" if _has_api_key(provider, model_override=model) else "demo"
        run.config_json = config_json
        db.commit()

        logger.info(
            f"Run {run_id} ('{run.name}') started: {run.total_cases} cases, "
            f"provider={provider}, model={model}"
            + (f", target_url={target_url}" if target_url else ", demo mode (no target_url)")
        )

        cases = db.query(EvalCase).filter(EvalCase.dataset_id == run.dataset_id).all()

        case_results: list[dict] = []
        for case in cases:
            result = EvalResult(
                run_id=run.id,
                case_id=case.id,
                status="pending",
                latency_ms=0,
                input_tokens=0,
                output_tokens=0,
            )
            db.add(result)
            db.flush()
            case_results.append({
                "result_id": result.id,
                "case_id": case.id,
                "case_input": case.input,
                "case_type": case.case_type,
                "reference_answer": case.reference_answer,
                "reference_context_ids": case.reference_context_ids,
                "expected_tool": case.expected_tool,
                "expected_args": case.expected_args,
            })
        db.commit()

        max_workers = max(1, min(concurrency, len(case_results)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(
                    _evaluate_single_case,
                    result_id=case_result["result_id"],
                    case_id=case_result["case_id"],
                    run_id=run.id,
                    provider=provider,
                    model=model,
                    case_input=case_result["case_input"],
                    case_type=case_result["case_type"],
                    reference_answer=case_result["reference_answer"],
                    reference_context_ids=case_result["reference_context_ids"],
                    expected_tool=case_result["expected_tool"],
                    expected_args=case_result["expected_args"],
                    config_json=config_json,
                ): case_result
                for case_result in case_results
            }
            for future in as_completed(futures):
                case_result = futures[future]
                try:
                    future.result()
                except Exception:
                    logger.exception(
                        f"Unhandled crash evaluating case {case_result.get('case_id')} "
                        f"(result_id={case_result.get('result_id')})"
                    )
                    db = SessionLocal()
                    try:
                        r = db.get(EvalResult, case_result["result_id"])
                        if r:
                            r.status = "error"
                            r.failure_reason = "unhandled crash"
                            db.commit()
                    except Exception:
                        db.rollback()
                    finally:
                        db.close()
                _publish_progress(run.id)

        results = db.query(EvalResult).filter(EvalResult.run_id == run.id).all()
        run.passed_cases = sum(1 for result in results if result.status == "passed")
        run.failed_cases = sum(1 for result in results if result.status in ("failed", "error"))

        all_scores = []
        total_latency = 0
        total_tokens = 0
        for result in results:
            total_latency += result.latency_ms
            total_tokens += result.input_tokens + result.output_tokens
            if result.scores:
                for score in result.scores.values():
                    if isinstance(score, dict) and "score" in score:
                        all_scores.append(score["score"])

        run.avg_score = round(sum(all_scores) / len(all_scores), 4) if all_scores else 0
        run.avg_latency_ms = round(total_latency / len(results), 1) if results else 0
        run.avg_tokens = round(total_tokens / len(results), 1) if results else 0
        run.status = "completed"
        run.finished_at = datetime.datetime.utcnow()
        db.commit()

        logger.info(
            f"Run {run_id} completed: {run.passed_cases}/{run.total_cases} passed, "
            f"avg_score={run.avg_score:.3f}, avg_latency={run.avg_latency_ms:.0f}ms"
        )
        _publish_progress(run.id)
        return {"status": "completed", "run_id": run.id}
    except Exception:
        logger.exception(f"Run {run_id} failed with exception")
        db.rollback()
        try:
            run = db.get(EvalRun, run_id)
            if run:
                run.status = "failed"
                db.commit()
        except Exception:
            pass
        raise
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=2, name="llmops.evaluate_run")
def evaluate_run_task(self, run_id: int):
    """Celery task: evaluate a run. Retries once on failure."""
    try:
        return run_evaluation_task(run_id)
    except Exception as exc:
        raise self.retry(exc=exc)


def run_evaluation(run_id: int):
    """Submit evaluation to Celery if available, else run in background thread."""
    try:
        evaluate_run_task.delay(run_id)
    except Exception:
        logger.warning("Celery unavailable, falling back to background thread")
        import threading

        thread = threading.Thread(target=run_evaluation_task, args=(run_id,), daemon=True)
        thread.start()


def _simulate_output(case) -> str:
    """Simulate actual output for demo / no-target-url fallback."""
    if case.reference_answer:
        return case.reference_answer
    return f"Answer to: {case.input[:80]}"


def _call_target_system(case, config: dict) -> tuple[str, str | None, dict | None, int, int]:
    """
    Call the target RAG/Agent system to get actual output.
    Returns (actual_output, actual_tool, actual_args, latency_ms, token_estimate).

    Falls back to _simulate_output if no target_url is configured.
    """
    import time as _time
    from urllib3.util.retry import Retry

    model = (config or {}).get("model")
    target_url = (config or {}).get("target_url")
    if not target_url:
        output = _simulate_output(case)
        return output, None, None, 0, count_tokens(output, model)

    validate_target_url(target_url)

    payload = {
        "query": case.input,
        "case_type": case.case_type,
    }
    if case.expected_tool:
        payload["tools"] = [case.expected_tool]
    if case.reference_context_ids:
        payload["context_ids"] = case.reference_context_ids

    headers = (config.get("target_headers") or {}).copy()
    timeout = config.get("target_timeout", 30)

    session = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[502, 503, 504])
    session.mount("http://", ValidatingHTTPAdapter(max_retries=retry))
    session.mount("https://", ValidatingHTTPAdapter(max_retries=retry))

    start = _time.time()
    response = session.post(
        target_url,
        json=payload,
        headers=headers,
        timeout=timeout,
        allow_redirects=False,
    )
    elapsed_ms = int((_time.time() - start) * 1000)
    response.raise_for_status()
    data = response.json()

    actual_output = data.get("answer") or data.get("output") or data.get("response") or ""
    actual_tool = data.get("tool_called") or data.get("tool")
    actual_args = data.get("tool_args") or data.get("arguments")
    total_tokens = count_tokens(str(payload), model) + count_tokens(actual_output, model)

    return actual_output, actual_tool, actual_args, elapsed_ms, total_tokens


def _classify_failure(
    scores: dict,
    case,
    latency_ms: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    actual_output: str = "",
    actual_tool: str = "",
    actual_args: dict | None = None,
) -> str | None:
    """Rule-based failure classification."""
    from app.services.classifier import classify

    reasons = classify(
        result_scores=scores,
        case=case,
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        actual_output=actual_output,
        actual_tool=actual_tool,
        actual_args=actual_args,
    )
    return ";".join(reasons) if reasons else None
