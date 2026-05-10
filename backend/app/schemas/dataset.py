from pydantic import BaseModel
from datetime import datetime


# ---------- Dataset ----------
class DatasetCreate(BaseModel):
    name: str
    description: str | None = None
    case_type: str = "qa"


class DatasetUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    case_type: str | None = None


class DatasetOut(BaseModel):
    id: int
    name: str
    description: str | None
    case_type: str
    created_at: datetime
    updated_at: datetime
    case_count: int = 0

    model_config = {"from_attributes": True}


class DatasetListOut(BaseModel):
    items: list[DatasetOut]
    total: int


# ---------- EvalCase ----------
class EvalCaseCreate(BaseModel):
    case_type: str = "qa"
    input: str
    reference_answer: str | None = None
    expected_tool: str | None = None
    expected_args: dict | None = None
    reference_context_ids: list[str] | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    extra_metadata: dict | None = None


class EvalCaseUpdate(BaseModel):
    case_type: str | None = None
    input: str | None = None
    reference_answer: str | None = None
    expected_tool: str | None = None
    expected_args: dict | None = None
    reference_context_ids: list[str] | None = None
    tags: list[str] | None = None
    difficulty: str | None = None
    extra_metadata: dict | None = None


class EvalCaseOut(BaseModel):
    id: int
    dataset_id: int
    case_type: str
    input: str
    reference_answer: str | None
    expected_tool: str | None
    expected_args: dict | None
    reference_context_ids: list[str] | None
    tags: list[str] | None
    difficulty: str | None
    extra_metadata: dict | None
    created_at: datetime

    model_config = {"from_attributes": True}


class EvalCaseListOut(BaseModel):
    items: list[EvalCaseOut]
    total: int


# ---------- Import ----------
class ImportCasesRequest(BaseModel):
    cases: list[EvalCaseCreate]
