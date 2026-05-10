from pydantic import BaseModel
from datetime import datetime


class ReportOut(BaseModel):
    id: int
    run_id: int
    report_markdown: str
    summary_json: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportListOut(BaseModel):
    items: list[ReportOut]
    total: int


class DashboardStats(BaseModel):
    total_runs: int
    avg_pass_rate: float
    avg_latency_ms: float
    avg_tokens: float
    total_cases: int
