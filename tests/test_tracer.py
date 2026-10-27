"""Tests for Trace recorder (buffered flush model).

Note: traces/steps are buffered in memory and only persisted at
``end_trace`` (a single short write transaction), so tests must call
``end_trace`` before querying the database.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.tracer import Tracer
from app.models.trace import Trace, TraceStep


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


def test_start_trace_returns_trace_id(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=1, case_id=10, user_input="Hello", model="gpt-4o")
    assert trace_id is not None
    assert len(trace_id) == 16  # uuid4 hex[:16]

    tracer.end_trace(trace_id)

    # Trace persisted in DB after end_trace (buffered flush)
    trace = db.query(Trace).filter(Trace.trace_id == trace_id).first()
    assert trace is not None
    assert trace.run_id == 1
    assert trace.case_id == 10
    assert trace.user_input == "Hello"
    assert trace.model == "gpt-4o"


def test_start_trace_records_session_id(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(
        run_id=1, case_id=11, user_input="multi-turn", session_id="llmops-mt-1-11-ab12"
    )
    tracer.end_trace(trace_id)

    trace = db.query(Trace).filter(Trace.trace_id == trace_id).first()
    assert trace.session_id == "llmops-mt-1-11-ab12"


def test_step_records_latency(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=2, case_id=20, user_input="Q")

    with tracer.step(trace_id, "retrieval", "RETRIEVER", input_data={"query": "Q"}) as step:
        pass  # Fast step

    tracer.end_trace(trace_id)

    # Step persisted
    db_step = db.query(TraceStep).filter(TraceStep.trace_id_link == trace_id).first()
    assert db_step is not None
    assert db_step.step_name == "retrieval"
    assert db_step.step_type == "RETRIEVER"
    assert db_step.latency_ms >= 0
    assert db_step.input_json == {"query": "Q"}


def test_step_set_output(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=3, case_id=30, user_input="Q")

    with tracer.step(trace_id, "llm_generation", "LLM") as step:
        tracer.set_step_output(step.id, {"output": "Answer"}, tokens=42)

    tracer.end_trace(trace_id)

    db_step = db.query(TraceStep).filter(TraceStep.trace_id_link == trace_id).first()
    assert db_step.output_json == {"output": "Answer"}
    assert db_step.tokens == 42


def test_step_error_is_recorded_in_buffer(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=4, case_id=40, user_input="Q")

    with pytest.raises(ValueError, match="boom"):
        with tracer.step(trace_id, "failing_step", "CHAIN"):
            raise ValueError("boom")

    # The failed step is recorded on the in-memory buffer even though the
    # caller never reaches end_trace; production error handling decides
    # whether to flush.
    assert len(tracer._pending_steps) == 1
    buffered = tracer._pending_steps[0]
    assert buffered.step_name == "failing_step"
    assert "boom" in (buffered.error_message or "")
    assert buffered.latency_ms >= 0


def test_step_unknown_type_defaults_to_chain(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=5, case_id=50, user_input="Q")

    with tracer.step(trace_id, "custom", "INVALID_TYPE"):
        pass

    tracer.end_trace(trace_id)

    db_step = db.query(TraceStep).filter(TraceStep.trace_id_link == trace_id).first()
    assert db_step.step_type == "CHAIN"


def test_step_order_index(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=6, case_id=60, user_input="Q")

    with tracer.step(trace_id, "step_1", "CHAIN"):
        pass
    with tracer.step(trace_id, "step_2", "CHAIN"):
        pass
    with tracer.step(trace_id, "step_3", "CHAIN"):
        pass

    tracer.end_trace(trace_id)

    steps = db.query(TraceStep).filter(TraceStep.trace_id_link == trace_id).order_by(TraceStep.order_index).all()
    assert len(steps) == 3
    assert steps[0].order_index == 0
    assert steps[1].order_index == 1
    assert steps[2].order_index == 2


def test_end_trace_aggregates_totals(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=7, case_id=70, user_input="Q", model="gpt-4o")

    with tracer.step(trace_id, "s1", "CHAIN") as step:
        tracer.set_step_output(step.id, {}, tokens=100)
    with tracer.step(trace_id, "s2", "LLM") as step:
        tracer.set_step_output(step.id, {}, tokens=200)

    tracer.end_trace(trace_id, status="success")

    trace = db.query(Trace).filter(Trace.trace_id == trace_id).first()
    assert trace.status == "success"
    assert trace.total_tokens == 300
    assert trace.total_latency_ms >= 0


def test_end_trace_with_error(db):
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=8, case_id=80, user_input="Q")

    tracer.end_trace(trace_id, status="failed", error_message="timeout")

    trace = db.query(Trace).filter(Trace.trace_id == trace_id).first()
    assert trace.status == "failed"
    assert trace.error_message == "timeout"


def test_end_trace_nonexistent_trace_does_not_crash(db):
    tracer = Tracer(db)
    # Should not raise
    tracer.end_trace("nonexistent_id", status="success")


def test_logical_keys_resolve_to_parent_after_flush(db):
    """Multi-turn hierarchy: children link via parent_key while buffered;
    parent_step_id is resolved to the real PK at flush time."""
    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=9, case_id=90, user_input="Q")

    with tracer.step(trace_id, "Turn 1", "CHAIN", step_key="turn_1"):
        with tracer.step(trace_id, "target_call", "CHAIN", parent_key="turn_1"):
            pass
        with tracer.step(trace_id, "evaluation", "LLM", parent_key="turn_1") as t_eval:
            tracer.set_step_output(t_eval.id, {"scores": {}}, tokens=7)

    tracer.end_trace(trace_id)

    steps = (
        db.query(TraceStep)
        .filter(TraceStep.trace_id_link == trace_id)
        .order_by(TraceStep.order_index)
        .all()
    )
    assert len(steps) == 3
    parent = next(s for s in steps if s.step_key == "turn_1")
    children = [s for s in steps if s.parent_key == "turn_1"]
    assert {s.step_name for s in children} == {"target_call", "evaluation"}
    assert all(s.parent_step_id == parent.id for s in children)
    # Parent span must not double-count its children in the trace totals.
    trace = db.query(Trace).filter(Trace.trace_id == trace_id).first()
    assert trace.total_tokens == 7
