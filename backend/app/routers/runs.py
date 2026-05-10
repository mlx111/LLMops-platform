import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.redis import get_async_redis
from app.database import SessionLocal, get_db
from app.models.dataset import Dataset, EvalCase
from app.models.run import EvalResult, EvalRun
from app.schemas.run import (
    EvalResultListOut,
    RunCompareOut,
    RunCreate,
    RunListOut,
    RunOut,
    RunProgress,
)
from app.services.url_safety import validate_target_url


def _validate_target_url(url: str | None) -> None:
    """Validate target URL to prevent SSRF attacks."""
    try:
        validate_target_url(url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


router = APIRouter(prefix="/api", tags=["runs"])


@router.get("/runs", response_model=RunListOut)
def list_runs(
    dataset_id: int | None = Query(None),
    status: str | None = Query(None),
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(EvalRun)
    if dataset_id:
        q = q.filter(EvalRun.dataset_id == dataset_id)
    if status:
        q = q.filter(EvalRun.status == status)
    total = q.count()
    items = q.order_by(EvalRun.created_at.desc()).offset(skip).limit(limit).all()
    return RunListOut(items=items, total=total)


@router.post("/runs", response_model=RunOut)
def create_run(body: RunCreate, db: Session = Depends(get_db)):
    dataset = db.get(Dataset, body.dataset_id)
    if not dataset:
        raise HTTPException(404, "Dataset not found")

    total = db.query(func.count(EvalCase.id)).filter(EvalCase.dataset_id == body.dataset_id).scalar()
    config_json = {"concurrency": body.concurrency}
    if body.provider:
        config_json["provider"] = body.provider
    if body.model:
        config_json["model"] = body.model
    if body.target_url:
        _validate_target_url(body.target_url)
        config_json["target_url"] = body.target_url
        config_json["target_type"] = body.target_type or "rag"
        config_json["target_timeout"] = body.target_timeout
        if body.target_headers:
            config_json["target_headers"] = body.target_headers

    run = EvalRun(
        name=body.name,
        dataset_id=body.dataset_id,
        prompt_version_id=body.prompt_version_id,
        model_version_id=body.model_version_id,
        retriever_version_id=body.retriever_version_id,
        agent_version_id=body.agent_version_id,
        total_cases=total,
        config_json=config_json,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    from app.tasks.celery_app import run_evaluation

    run_evaluation(run.id)
    return run


@router.get("/runs/compare", response_model=RunCompareOut)
def compare_runs(
    run1: int = Query(...),
    run2: int = Query(...),
    db: Session = Depends(get_db),
):
    r1 = db.get(EvalRun, run1)
    r2 = db.get(EvalRun, run2)
    if not r1:
        raise HTTPException(404, f"Run {run1} not found")
    if not r2:
        raise HTTPException(404, f"Run {run2} not found")

    results1 = {
        r.case_id: r
        for r in db.query(EvalResult).filter(EvalResult.run_id == run1).all()
    }
    results2 = {
        r.case_id: r
        for r in db.query(EvalResult).filter(EvalResult.run_id == run2).all()
    }

    def aggregate_metrics(results_map: dict) -> dict[str, float]:
        totals: dict[str, list[float]] = {}
        for result in results_map.values():
            if not result.scores:
                continue
            for name, score_detail in result.scores.items():
                if isinstance(score_detail, dict) and "score" in score_detail:
                    totals.setdefault(name, []).append(score_detail["score"])
        return {key: round(sum(values) / len(values), 4) for key, values in totals.items()}

    metrics1 = aggregate_metrics(results1)
    metrics2 = aggregate_metrics(results2)

    all_metric_names = set(metrics1.keys()) | set(metrics2.keys())
    metric_diffs = {
        "avg_score": round((r2.avg_score or 0) - (r1.avg_score or 0), 4),
        "avg_latency_ms": round((r2.avg_latency_ms or 0) - (r1.avg_latency_ms or 0), 1),
        "avg_tokens": round((r2.avg_tokens or 0) - (r1.avg_tokens or 0), 1),
    }
    for name in all_metric_names:
        metric_diffs[name] = round(metrics2.get(name, 0) - metrics1.get(name, 0), 4)

    def case_avg_score(result: EvalResult) -> float:
        if not result.scores:
            return 0
        values = [score["score"] for score in result.scores.values() if isinstance(score, dict) and "score" in score]
        return sum(values) / len(values) if values else 0

    common_cases = set(results1.keys()) & set(results2.keys())
    case_inputs: dict[int, str] = {}
    if common_cases:
        for case in db.query(EvalCase).filter(EvalCase.id.in_(common_cases)).all():
            case_inputs[case.id] = case.input[:100]

    improved: list[dict] = []
    regressed: list[dict] = []
    for case_id in common_cases:
        run1_score = case_avg_score(results1[case_id])
        run2_score = case_avg_score(results2[case_id])
        delta = round(run2_score - run1_score, 4)
        entry = {
            "case_id": case_id,
            "input": case_inputs.get(case_id, "?"),
            "run1_score": round(run1_score, 4),
            "run2_score": round(run2_score, 4),
            "delta": delta,
            "run1_status": results1[case_id].status,
            "run2_status": results2[case_id].status,
        }
        if delta > 0:
            improved.append(entry)
        elif delta < 0:
            regressed.append(entry)

    improved.sort(key=lambda item: item["delta"], reverse=True)
    regressed.sort(key=lambda item: item["delta"])

    total_common = len(common_cases)
    if total_common > 0:
        pct_improved = round(len(improved) / total_common * 100, 1)
        pct_regressed = round(len(regressed) / total_common * 100, 1)
        pct_unchanged = round(100 - pct_improved - pct_regressed, 1)
        score_change = metric_diffs.get("avg_score", 0)
        trend = "improved" if score_change > 0 else "declined" if score_change < 0 else "unchanged"
        summary = (
            f"Comparing '{r2.name}' against '{r1.name}': "
            f"average score {trend} by {abs(score_change):.4f}. "
            f"{pct_improved}% cases improved, {pct_regressed}% regressed, "
            f"{pct_unchanged}% unchanged "
            f"(based on {total_common} shared cases)."
        )
    else:
        summary = f"No common cases between '{r1.name}' and '{r2.name}'."

    return RunCompareOut(
        run1=r1,
        run2=r2,
        metric_diffs=metric_diffs,
        improved_cases=improved,
        regressed_cases=regressed,
        summary=summary,
    )


@router.get("/runs/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs/{run_id}/progress", response_model=RunProgress)
def get_run_progress(run_id: int, db: Session = Depends(get_db)):
    run = db.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    completed = db.query(func.count(EvalResult.id)).filter(EvalResult.run_id == run_id).scalar()
    return RunProgress(
        run_id=run.id,
        status=run.status,
        total_cases=run.total_cases,
        completed_cases=completed,
        passed_cases=run.passed_cases,
        failed_cases=run.failed_cases,
    )


def _poll_progress(run_id: int, db: Session | None = None) -> dict | None:
    """Read current progress from DB. Returns None if run not found."""
    own_session = False
    if db is None:
        db = SessionLocal()
        own_session = True
    try:
        run = db.get(EvalRun, run_id)
        if not run:
            return None
        result_count = db.query(func.count(EvalResult.id)).filter(EvalResult.run_id == run_id).scalar()
        passed = db.query(func.count(EvalResult.id)).filter(
            EvalResult.run_id == run_id,
            EvalResult.status == "passed",
        ).scalar()
        failed = db.query(func.count(EvalResult.id)).filter(
            EvalResult.run_id == run_id,
            EvalResult.status.in_(["failed", "error"]),
        ).scalar()
        return {
            "run_id": run.id,
            "status": run.status,
            "total": run.total_cases,
            "completed": result_count,
            "passed": passed,
            "failed": failed,
        }
    finally:
        if own_session:
            db.close()


def _is_finished(status: str) -> bool:
    return status in ("completed", "failed", "error")


CHANNEL = "run:{run_id}:progress"


@router.get("/runs/{run_id}/stream")
async def stream_run_progress(run_id: int):
    """SSE endpoint: Redis Pub/Sub with DB-polling fallback."""
    redis = await get_async_redis()

    if redis is None:
        async def db_generator():
            while True:
                payload = _poll_progress(run_id)
                if payload is None:
                    yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
                    return
                yield f"data: {json.dumps(payload)}\n\n"
                if _is_finished(payload["status"]):
                    return
                await asyncio.sleep(1)

        return StreamingResponse(
            db_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )

    async def redis_generator():
        pubsub = redis.pubsub()
        await pubsub.subscribe(CHANNEL.format(run_id=run_id))

        # Poll AFTER subscribe so no window between getting state
        # and listening for live updates.
        current = _poll_progress(run_id)
        if current is None:
            yield f"data: {json.dumps({'error': 'Run not found'})}\n\n"
            return
        yield f"data: {json.dumps(current)}\n\n"
        if _is_finished(current["status"]):
            await pubsub.unsubscribe()
            await pubsub.close()
            return

        try:
            while True:
                msg = await pubsub.get_message(timeout=1.0, ignore_subscribe_messages=True)
                if msg and msg["type"] == "message":
                    yield f"data: {msg['data']}\n\n"
                    data = json.loads(msg["data"])
                    if _is_finished(data.get("status", "")):
                        return
                else:
                    # DB poll fallback only when no pubsub message arrived
                    current = _poll_progress(run_id)
                    if current and _is_finished(current["status"]):
                        yield f"data: {json.dumps(current)}\n\n"
                        return
        finally:
            await pubsub.unsubscribe()
            await pubsub.close()

    return StreamingResponse(
        redis_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{run_id}/results", response_model=EvalResultListOut)
def list_run_results(
    run_id: int,
    skip: int = 0,
    limit: int = 100,
    status: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(EvalResult).filter(EvalResult.run_id == run_id)
    if status:
        q = q.filter(EvalResult.status == status)
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return EvalResultListOut(items=items, total=total)
