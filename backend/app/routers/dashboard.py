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
