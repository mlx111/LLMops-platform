from pydantic import BaseModel
from datetime import datetime


class TraceStepOut(BaseModel):
    id: int
    step_name: str
    step_type: str
    parent_step_id: int | None
    step_key: str | None = None
    parent_key: str | None = None
    input_json: dict | None
    output_json: dict | None
    latency_ms: int
    tokens: int | None
    error_message: str | None
    order_index: int

    model_config = {"from_attributes": True}


class TraceOut(BaseModel):
    id: int
    trace_id: str
    run_id: int | None
    case_id: int | None
    user_input: str
    session_id: str | None = None
    prompt_version: str | None
    model: str | None
    retriever_version: str | None
    status: str
    total_latency_ms: int
    total_tokens: int
    error_message: str | None
    created_at: datetime
    steps: list[TraceStepOut] = []

    model_config = {"from_attributes": True}


class TraceListOut(BaseModel):
    items: list[TraceOut]
    total: int
