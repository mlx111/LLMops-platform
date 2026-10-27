"""Resilience: one failed target turn must not discard the multi-turn case."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.services.tracer import Tracer
from app.models.trace import TraceStep
from app.tasks import celery_app


class FakeTurnCase:
    case_type = "multi_turn"
    input = "multi-turn task"
    reference_answer = "overall reference answer"
    expected_tool = None
    extra_metadata = None
    turns = [
        {"user": "turn one question", "reference": "turn one reference"},
        {"user": "turn two question", "reference": "turn two reference"},
    ]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    yield session
    session.close()


def test_turn_level_target_error_is_contained(db, monkeypatch):
    calls = []

    def flaky_target(case, config, query_override=None, session_id=None):
        calls.append(query_override)
        if query_override == "turn one question":
            raise ConnectionError("target down")
        return ("turn two reference answer", None, None, 5, 10, None, None)

    monkeypatch.setattr(celery_app, "_call_target_system", flaky_target)
    # Keep the test offline/deterministic: rule-based scoring only.
    monkeypatch.setattr(
        "app.services.llm_judge.resolve_judge_config", lambda: None
    )

    tracer = Tracer(db)
    trace_id = tracer.start_trace(run_id=1, case_id=1, user_input="task")

    outcome = celery_app._run_multi_turn_evaluation(
        case=FakeTurnCase(),
        config={"target_url": "http://target.example/evaluation"},
        tracer=tracer,
        trace_id=trace_id,
        session_id="sess-xyz",
        provider="demo",
        model=None,
    )
    tracer.end_trace(trace_id, status="failed")

    # Both turns were still driven — the session continued after the error.
    assert calls == ["turn one question", "turn two question"]
    assert outcome["has_target_errors"] is True
    assert outcome["target_meta"]["turn_errors"] == [
        {"turn": 1, "error": "target down"}
    ]

    # The trace survives and the failed turn span carries the error.
    steps = db.query(TraceStep).filter(TraceStep.trace_id_link == trace_id).all()
    call_steps = [s for s in steps if s.step_name == "target_call"]
    assert len(call_steps) == 2
    assert "target down" in (call_steps[0].error_message or "")
    assert call_steps[1].error_message is None
