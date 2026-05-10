from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.report import Report
from app.models.run import EvalRun
from app.schemas.report import ReportListOut, ReportOut
from app.services.report_generator import generate_report

router = APIRouter(prefix="/api", tags=["reports"])


@router.post("/runs/{run_id}/report", response_model=ReportOut)
def create_report(run_id: int, db: Session = Depends(get_db)):
    run = db.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run.status not in ("completed", "failed", "error"):
        raise HTTPException(400, "Run is not finished yet")

    result = generate_report(db, run_id)
    if not result:
        raise HTTPException(400, "No results to generate report from")

    md, summary = result

    report = Report(
        run_id=run_id,
        report_markdown=md,
        summary_json=summary,
    )
    db.add(report)
    db.commit()
    db.refresh(report)

    return report


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: int, db: Session = Depends(get_db)):
    report = db.get(Report, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    return report


@router.get("/reports", response_model=ReportListOut)
def list_reports(
    run_id: int | None = Query(None),
    skip: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    q = db.query(Report)
    if run_id:
        q = q.filter(Report.run_id == run_id)
    total = q.count()
    items = q.order_by(Report.created_at.desc()).offset(skip).limit(limit).all()
    return ReportListOut(items=items, total=total)


@router.get("/runs/{run_id}/reports", response_model=ReportListOut)
def get_run_reports(run_id: int, db: Session = Depends(get_db)):
    run = db.get(EvalRun, run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    items = (
        db.query(Report)
        .filter(Report.run_id == run_id)
        .order_by(Report.created_at.desc())
        .all()
    )
    return ReportListOut(items=items, total=len(items))
