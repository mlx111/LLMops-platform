"""Tests for analytics aggregation (ClickHouse preferred, SQLite fallback).

These tests exercise the SQLite fallback path (the ClickHouse read path is
exercised in integration; the unit contract here is that with CH disabled
``build_analytics`` degrades transparently and produces the same metric
schema).
"""

import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database import Base
from app.models.trace import Trace, TraceStep
from app.services.analytics import _percentile95, build_analytics


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _disable_clickhouse(monkeypatch):
    monkeypatch.setattr(settings, "clickhouse_url", "")


def _seed(db):
    now = datetime.datetime.utcnow()
    day = now.strftime("%Y-%m-%d")
    traces = []
    for i, (status, latency, tokens, model) in enumerate(
        [
            ("success", 100, 50, "deepseek-chat"),
            ("success", 300, 150, "deepseek-chat"),
            ("failed", 2000, 800, "gpt-4o-mini"),
        ]
    ):
        t = Trace(
            trace_id=f"t{i}",
            run_id=1,
            case_id=i,
            user_input=f"q{i}",
            model=model,
            status=status,
            total_latency_ms=latency,
            total_tokens=tokens,
            created_at=now - datetime.timedelta(hours=i),
        )
        db.add(t)
        traces.append(t)
    db.add(
        TraceStep(
            trace_id_link="t0", step_name="target_call", step_type="LLM",
            latency_ms=90, tokens=50, order_index=0,
        )
    )
    db.add(
        TraceStep(
            trace_id_link="t1", step_name="evaluation", step_type="CHAIN",
            latency_ms=250, tokens=0, order_index=0,
        )
    )
    db.commit()
    return traces, day


def test_percentile95():
    assert _percentile95([]) is None
    assert _percentile95([10]) == 10.0
    assert _percentile95([100, 200, 300, 400]) in (380.0, 400.0)


def test_build_analytics_degrades_to_sqlite(db):
    _seed(db)
    result = build_analytics(days=7, db=db)
    assert result["source"] == "sqlite"
    assert len(result["trend"]) == 1
    day = result["trend"][0]
    assert day["traces"] == 3
    assert day["runs"] == 1
    # 2/3 success
    assert day["pass_rate"] == round(2 / 3 * 100, 1)
    assert day["avg_latency_ms"] == round((100 + 300 + 2000) / 3, 1)
    assert day["tokens"] == 1000


def test_sqlite_p95_and_models(db):
    _seed(db)
    result = build_analytics(days=7, db=db)
    assert result["trend"][0]["p95_latency_ms"] == 2000.0
    models = {m["model"]: m for m in result["by_model"]}
    assert models["deepseek-chat"]["traces"] == 2
    assert models["deepseek-chat"]["avg_latency_ms"] == 200.0
    assert models["gpt-4o-mini"]["traces"] == 1


def test_sqlite_span_breakdown(db):
    _seed(db)
    result = build_analytics(days=7, db=db)
    breakdown = {b["step_type"]: b for b in result["span_breakdown"]}
    assert breakdown["LLM"]["count"] == 1
    assert breakdown["LLM"]["avg_latency_ms"] == 90.0
    assert breakdown["CHAIN"]["total_ms"] == 250


def test_days_window_filters(db):
    _seed(db)
    old = Trace(
        trace_id="old", run_id=2, case_id=9, user_input="ancient",
        model="m", status="success", total_latency_ms=1, total_tokens=1,
        created_at=datetime.datetime.utcnow() - datetime.timedelta(days=30),
    )
    db.add(old)
    db.commit()
    result = build_analytics(days=7, db=db)
    assert all(t["traces"] < 4 for t in result["trend"])