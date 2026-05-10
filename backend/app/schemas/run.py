from pydantic import BaseModel
from datetime import datetime


class RunCreate(BaseModel):
    name: str
    dataset_id: int
    provider: str | None = None
    model: str | None = None
    prompt_version_id: int | None = None
    model_version_id: int | None = None
    retriever_version_id: int | None = None
    agent_version_id: int | None = None
    concurrency: int = 5
    target_url: str | None = None
    target_type: str | None = None
    target_headers: dict | None = None
    target_timeout: int = 30


class RunOut(BaseModel):
    id: int
    name: str
    dataset_id: int
    prompt_version_id: int | None
    model_version_id: int | None
    retriever_version_id: int | None
    agent_version_id: int | None
    status: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    avg_score: float | None
    avg_latency_ms: float | None
    avg_tokens: float | None
    config_json: dict | None
    created_at: datetime
    finished_at: datetime | None

    model_config = {"from_attributes": True}


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class RunProgress(BaseModel):
    run_id: int
    status: str
    total_cases: int
    completed_cases: int
    passed_cases: int
    failed_cases: int


class EvalResultOut(BaseModel):
    id: int
    run_id: int
    case_id: int
    status: str
    actual_output: str | None
    actual_tool: str | None
    actual_args: dict | None
    scores: dict | None
    failure_reason: str | None
    latency_ms: int
    input_tokens: int
    output_tokens: int
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalResultListOut(BaseModel):
    items: list[EvalResultOut]
    total: int


class RunCompareOut(BaseModel):
    run1: RunOut
    run2: RunOut
    metric_diffs: dict
    improved_cases: list[dict]
    regressed_cases: list[dict]
    summary: str
