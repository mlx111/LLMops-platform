from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.dataset import Dataset, EvalCase
from app.models.run import EvalRun


def test_create_run_persists_selected_provider_and_model_and_dispatches_via_runner(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "test_llmops.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    dataset = Dataset(name="QA Dataset", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    session.add(
        EvalCase(
            dataset_id=dataset.id,
            case_type="qa",
            input="What is LLMOps?",
            reference_answer="A platform for operating LLM systems.",
        )
    )
    session.commit()
    session.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    scheduled_run_ids: list[int] = []

    def fake_run_evaluation(run_id: int):
        scheduled_run_ids.append(run_id)

    class FailIfThreadUsed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("create_run should dispatch via run_evaluation(), not threading.Thread")

    monkeypatch.setattr("app.tasks.celery_app.run_evaluation", fake_run_evaluation)
    monkeypatch.setattr("threading.Thread", FailIfThreadUsed)


    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.post(
            "/api/runs",
            json={
                "name": "OpenAI Baseline",
                "dataset_id": 1,
                "provider": "openai",
                "model": "gpt-4o-mini",
                "concurrency": 3,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["config_json"] == {
        "concurrency": 3,
        "provider": "openai",
        "model": "gpt-4o-mini",
    }
    assert "avg_tokens" in response.json()
    assert "avg_token_cost" not in response.json()
    assert scheduled_run_ids == [response.json()["id"]]


def test_compare_endpoint_is_not_shadowed_by_run_id_route(tmp_path: Path):
    db_path = tmp_path / "test_compare_route.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    dataset = Dataset(name="Compare Dataset", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    case = EvalCase(
        dataset_id=dataset.id,
        case_type="qa",
        input="What is LLMOps?",
        reference_answer="A platform for operating LLM systems.",
    )
    session.add(case)
    session.flush()

    run1 = EvalRun(
        name="Baseline",
        dataset_id=dataset.id,
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        avg_score=0.8,
        avg_latency_ms=100,
        avg_tokens=10,
        status="completed",
    )
    run2 = EvalRun(
        name="Candidate",
        dataset_id=dataset.id,
        total_cases=1,
        passed_cases=1,
        failed_cases=0,
        avg_score=0.9,
        avg_latency_ms=90,
        avg_tokens=12,
        status="completed",
    )
    session.add_all([run1, run2])
    session.commit()
    session.close()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.get("/api/runs/compare", params={"run1": 1, "run2": 2})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["run1"]["id"] == 1
    assert payload["run2"]["id"] == 2
