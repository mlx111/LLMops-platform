from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models.trace import Trace, TraceStep
from app.models.run import EvalRun
from app.schemas.trace import TraceListOut, TraceOut, TraceStepOut

router = APIRouter(prefix="/api", tags=["traces"])


def _build_trace_out(trace: Trace) -> TraceOut:
    steps = sorted(trace.steps, key=lambda s: s.order_index)
    tree = _build_step_tree(steps)
    return TraceOut(
        id=trace.id,
        trace_id=trace.trace_id,
        run_id=trace.run_id,
        case_id=trace.case_id,
        user_input=trace.user_input,
        prompt_version=trace.prompt_version,
        model=trace.model,
        retriever_version=trace.retriever_version,
        status=trace.status,
        total_latency_ms=trace.total_latency_ms,
        total_tokens=trace.total_tokens,
        error_message=trace.error_message,
        created_at=trace.created_at,
        steps=[TraceStepOut.model_validate(s) for s in tree],
    )


def _build_step_tree(steps: list[TraceStep]) -> list[TraceStep]:
    """Sort steps so children follow their parents (pre-order)."""
    step_map = {s.id: s for s in steps}
    root_steps = [s for s in steps if s.parent_step_id is None]
    result = []

    def walk(s):
        result.append(s)
        for child in step_map.values():
            if child.parent_step_id == s.id:
                walk(child)

    for root in sorted(root_steps, key=lambda s: s.order_index):
        walk(root)
    return result


@router.get("/traces", response_model=TraceListOut)
def list_traces(
    run_id: int | None = Query(None),
    status: str | None = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(Trace).options(joinedload(Trace.steps))
    if run_id:
        q = q.filter(Trace.run_id == run_id)
    if status:
        q = q.filter(Trace.status == status)
    total = q.count()
    items = q.order_by(Trace.created_at.desc()).offset(skip).limit(limit).all()
    return TraceListOut(items=[_build_trace_out(t) for t in items], total=total)


@router.get("/traces/{trace_id}", response_model=TraceOut)
def get_trace(trace_id: str, db: Session = Depends(get_db)):
    trace = (
        db.query(Trace)
        .options(joinedload(Trace.steps))
        .filter(Trace.trace_id == trace_id)
        .first()
    )
    if not trace:
        raise HTTPException(404, "Trace not found")
    return _build_trace_out(trace)


@router.get("/runs/{run_id}/traces", response_model=TraceListOut)
def get_run_traces(
    run_id: int,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    run = db.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    q = db.query(Trace).options(joinedload(Trace.steps)).filter(Trace.run_id == run_id)
    total = q.count()
    items = q.order_by(Trace.created_at.desc()).offset(skip).limit(limit).all()
    return TraceListOut(items=[_build_trace_out(t) for t in items], total=total)
