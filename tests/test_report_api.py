"""Tests for Report generation and retrieval API."""

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.dataset import Dataset, EvalCase
from app.models.run import EvalResult, EvalRun


def _setup(tmp_path: Path):
    db_path = tmp_path / "test_report.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()

    dataset = Dataset(name="Report DS", description="test", case_type="qa")
    session.add(dataset)
    session.flush()

    case1 = EvalCase(dataset_id=dataset.id, case_type="qa", input="Q1", reference_answer="A1")
    case2 = EvalCase(dataset_id=dataset.id, case_type="qa", input="Q2", reference_answer="A2")
    session.add(case1)
    session.add(case2)
    session.flush()

    return engine, TestingSessionLocal, session, dataset, case1, case2


def test_generate_report_requires_finished_run(tmp_path: Path):
    """Generating a report for a running run should fail."""
    engine, TestingSessionLocal, session, dataset, _, _ = _setup(tmp_path)
    run = EvalRun(name="Running Run", dataset_id=dataset.id, total_cases=2, status="running")
    session.add(run)
    session.commit()
    run_id = run.id
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
        response = client.post(f"/api/runs/{run_id}/report")
        assert response.status_code == 400
        assert "not finished" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_generate_report_returns_markdown_and_summary(tmp_path: Path):
    """A completed run returns a report with markdown and summary_json."""
    engine, TestingSessionLocal, session, dataset, case1, case2 = _setup(tmp_path)

    run = EvalRun(
        name="Complete Run", dataset_id=dataset.id, total_cases=2,
        status="completed", passed_cases=1, failed_cases=1,
        avg_score=0.65, avg_latency_ms=150.0, avg_tokens=200.0,
    )
    session.add(run)
    session.flush()
    run_id = run.id

    session.add(EvalResult(
        run_id=run_id, case_id=case1.id, status="passed", latency_ms=100,
        input_tokens=50, output_tokens=30,
        scores={"Correctness": {"score": 0.9, "reason": "good", "success": True}},
    ))
    session.add(EvalResult(
        run_id=run_id, case_id=case2.id, status="failed", latency_ms=200,
        input_tokens=100, output_tokens=70,
        scores={"Correctness": {"score": 0.3, "reason": "bad", "success": False}},
        failure_reason="quality_issue",
    ))
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
        response = client.post(f"/api/runs/{run_id}/report")
        assert response.status_code == 200
        data = response.json()

        assert data["run_id"] == run_id
        assert "report_markdown" in data
        assert "summary_json" in data

        md = data["report_markdown"]
        assert "Evaluation Report: Complete Run" in md
        assert "50.0%" in md  # 1/2 pass rate
        assert "quality_issue" in md

        summary = data["summary_json"]
        assert summary["total_cases"] == 2
        assert summary["passed"] == 1
        assert summary["failed"] == 1
        assert summary["pass_rate"] == 50.0
    finally:
        app.dependency_overrides.clear()


def test_get_report_by_id(tmp_path: Path):
    """Generated report can be retrieved by ID."""
    engine, TestingSessionLocal, session, dataset, case1, _ = _setup(tmp_path)

    run = EvalRun(
        name="Get Report Run", dataset_id=dataset.id, total_cases=1,
        status="completed", passed_cases=1, failed_cases=0, avg_score=0.9,
    )
    session.add(run)
    session.flush()
    run_id = run.id

    session.add(EvalResult(
        run_id=run_id, case_id=case1.id, status="passed", latency_ms=50,
        input_tokens=10, output_tokens=20,
        scores={"Correctness": {"score": 0.9, "reason": "good", "success": True}},
    ))
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
        # Generate
        create_resp = client.post(f"/api/runs/{run_id}/report")
        report_id = create_resp.json()["id"]

        # Retrieve
        get_resp = client.get(f"/api/reports/{report_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == report_id
        assert get_resp.json()["run_id"] == run_id
    finally:
        app.dependency_overrides.clear()


def test_list_reports(tmp_path: Path):
    """Reports can be listed with pagination."""
    engine, TestingSessionLocal, session, dataset, case1, _ = _setup(tmp_path)

    run = EvalRun(
        name="List Report Run", dataset_id=dataset.id, total_cases=1,
        status="completed", passed_cases=1, failed_cases=0, avg_score=0.9,
    )
    session.add(run)
    session.flush()
    run_id = run.id

    session.add(EvalResult(
        run_id=run_id, case_id=case1.id, status="passed", latency_ms=50,
        input_tokens=10, output_tokens=20,
        scores={"Correctness": {"score": 0.9, "reason": "good", "success": True}},
    ))
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
        client.post(f"/api/runs/{run_id}/report")
        list_resp = client.get("/api/reports")
        assert list_resp.status_code == 200
        assert len(list_resp.json()["items"]) == 1
        assert list_resp.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()
