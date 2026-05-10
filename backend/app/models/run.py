import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id"), nullable=False)
    prompt_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    retriever_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    agent_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("versions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    total_cases: Mapped[int] = mapped_column(Integer, default=0)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0)
    failed_cases: Mapped[int] = mapped_column(Integer, default=0)
    avg_score: Mapped[float | None] = mapped_column(nullable=True)
    avg_latency_ms: Mapped[float | None] = mapped_column(nullable=True)
    avg_tokens: Mapped[float | None] = mapped_column(nullable=True)
    config_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    dataset = relationship("Dataset")
    results: Mapped[list["EvalResult"]] = relationship(
        "EvalResult", back_populates="run", cascade="all, delete-orphan"
    )


class EvalResult(Base):
    __tablename__ = "eval_results"
    __table_args__ = (
        UniqueConstraint("run_id", "case_id", name="uq_run_case"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("eval_runs.id"), nullable=False, index=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("eval_cases.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    actual_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    actual_tool: Mapped[str | None] = mapped_column(String(255), nullable=True)
    actual_args: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    scores: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )

    run: Mapped["EvalRun"] = relationship("EvalRun", back_populates="results")
