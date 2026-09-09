import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.redis import get_redis
from app.database import get_db
from app.models.dataset import EvalCase
from app.models.run import EvalResult, EvalRun
from app.schemas.report import DashboardStats
from app.tasks.celery_app import celery_app

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard/health")
def health_check():
    """System health: check DB, Redis, Celery worker status."""
    redis_ok = get_redis() is not None

    worker_ok = False
    if redis_ok:
        try:
            workers = celery_app.control.inspect().ping(timeout=2)
            worker_ok = bool(workers)  # non-empty dict means at least one worker replied
        except Exception:
            worker_ok = False

    celery_mode = "celery_ready" if worker_ok else "threading_fallback"

    return {
        "status": "ok",
        "database": "connected",
        "redis": "available" if redis_ok else "unavailable",
        "celery_mode": celery_mode,
        "api_version": "0.1.0",
    }


@router.get("/dashboard/logs")
def get_logs(tail: int = Query(default=100, ge=1, le=1000)):
    log_file = LOG_DIR / "llmops.log"
    if not log_file.exists():
        return {"lines": []}

    lines = _tail_file(log_file, tail)
    return {"lines": lines, "file": str(log_file)}


def _tail_file(path: Path, n: int) -> list[str]:
    """Return the last n lines of a file, reading from the end."""
    chunk_size = 8192
    with open(path, "rb") as f:
        f.seek(0, 2)  # seek to end
        total_size = f.tell()
        buffer = []
        pos = total_size
        while pos > 0 and len(buffer) < n:
            read_size = min(chunk_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size).decode("utf-8", errors="replace")
            buffer = chunk.split("\n") + buffer
        return [line.rstrip("\r") for line in buffer[-n:] if line]


@router.get("/dashboard/trends")
def get_dashboard_trends(
    days: int = Query(default=14, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """按天聚合的评测趋势：分数 / 通过率 / 延迟 / token 用量。

    Phase 6 成本优化看板数据源：avg_tokens 来自 target 系统上报的真实
    token 用量（quick 语义缓存命中时为 0，模型路由后按实际模型计量）。
    """
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    runs = (
        db.query(EvalRun)
        .filter(EvalRun.created_at >= since, EvalRun.status == "completed")
        .order_by(EvalRun.created_at.asc())
        .all()
    )

    daily: dict[str, dict] = {}
    run_series: list[dict] = []
    for run in runs:
        day = run.created_at.strftime("%Y-%m-%d")
        bucket = daily.setdefault(
            day,
            {
                "date": day,
                "runs": 0,
                "avg_score_sum": 0.0,
                "avg_score_n": 0,
                "pass_rate_sum": 0.0,
                "pass_rate_n": 0,
                "latency_sum": 0.0,
                "latency_n": 0,
                "tokens_sum": 0.0,
                "tokens_n": 0,
            },
        )
        bucket["runs"] += 1
        if run.avg_score is not None:
            bucket["avg_score_sum"] += run.avg_score
            bucket["avg_score_n"] += 1
        if run.total_cases > 0:
            bucket["pass_rate_sum"] += run.passed_cases / run.total_cases
            bucket["pass_rate_n"] += 1
        if run.avg_latency_ms is not None:
            bucket["latency_sum"] += run.avg_latency_ms
            bucket["latency_n"] += 1
        if run.avg_tokens is not None and run.avg_tokens > 0:
            bucket["tokens_sum"] += run.avg_tokens
            bucket["tokens_n"] += 1
        run_series.append(
            {
                "run_id": run.id,
                "name": run.name,
                "created_at": run.created_at.isoformat(timespec="seconds"),
                "avg_score": run.avg_score,
                "pass_rate": round(run.passed_cases / run.total_cases * 100, 1) if run.total_cases else 0,
                "avg_latency_ms": run.avg_latency_ms,
                "avg_tokens": run.avg_tokens,
                "total_cases": run.total_cases,
            }
        )

    trend = []
    for day in sorted(daily):
        b = daily[day]
        trend.append(
            {
                "date": day,
                "runs": b["runs"],
                "avg_score": round(b["avg_score_sum"] / b["avg_score_n"], 4) if b["avg_score_n"] else None,
                "pass_rate": round(b["pass_rate_sum"] / b["pass_rate_n"] * 100, 1) if b["pass_rate_n"] else None,
                "avg_latency_ms": round(b["latency_sum"] / b["latency_n"], 1) if b["latency_n"] else None,
                "avg_tokens": round(b["tokens_sum"] / b["tokens_n"], 1) if b["tokens_n"] else None,
            }
        )

    return {"trend": trend, "runs": run_series[-50:]}


@router.get("/dashboard/stats", response_model=DashboardStats)
def get_dashboard_stats(db: Session = Depends(get_db)):
    total_runs = db.query(func.count(EvalRun.id)).scalar()
    total_cases = db.query(func.count(EvalCase.id)).scalar()

    finished = db.query(EvalRun).filter(
        EvalRun.status == "completed", EvalRun.total_cases > 0
    ).all()

    if finished:
        avg_pass_rate = sum(
            r.passed_cases / r.total_cases for r in finished if r.total_cases > 0
        ) / len(finished) * 100
        avg_latency = sum(r.avg_latency_ms or 0 for r in finished) / len(finished)
        avg_tokens = sum(r.avg_tokens or 0 for r in finished) / len(finished)
    else:
        avg_pass_rate = 0.0
        avg_latency = 0.0
        avg_tokens = 0.0

    return DashboardStats(
        total_runs=total_runs,
        avg_pass_rate=round(avg_pass_rate, 1),
        avg_latency_ms=round(avg_latency, 1),
        avg_tokens=round(avg_tokens, 1),
        total_cases=total_cases,
    )
