"""Tests for SSE progress stream endpoint."""

import asyncio
import json
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.dataset import Dataset, EvalCase
from app.models.run import EvalResult, EvalRun


def _setup_db(tmp_path: Path):
    db_path = tmp_path / "test_sse.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return engine, TestingSessionLocal


def test_sse_returns_correct_headers(monkeypatch, tmp_path: Path):
    """SSE endpoint returns 200 with text/event-stream content type."""
    _, TestingSessionLocal = _setup_db(tmp_path)
    session = TestingSessionLocal()
    dataset = Dataset(name="SSE Test", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    session.add(EvalCase(dataset_id=dataset.id, case_type="qa", input="Hi?"))
    run = EvalRun(name="SSE Run", dataset_id=dataset.id, total_cases=1, status="completed", passed_cases=0, failed_cases=0)
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
    monkeypatch.setattr("app.routers.runs.SessionLocal", TestingSessionLocal)
    async def _no_redis():
        return None
    monkeypatch.setattr("app.routers.runs.get_async_redis", _no_redis)
    client = TestClient(app)

    try:
        with client.stream("GET", f"/api/runs/{run_id}/stream") as response:
            assert response.status_code == 200
            assert response.headers.get("content-type") == "text/event-stream; charset=utf-8"
            assert response.headers.get("cache-control") == "no-cache"
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    assert "status" in data
                    break
    finally:
        app.dependency_overrides.clear()


def test_sse_streams_progress_updates(monkeypatch, tmp_path: Path):
    """SSE stream sends progress then completion when run is finished."""
    _, TestingSessionLocal = _setup_db(tmp_path)
    session = TestingSessionLocal()
    dataset = Dataset(name="SSE Prog", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    case = EvalCase(dataset_id=dataset.id, case_type="qa", input="Hi?")
    session.add(case)
    session.flush()

    run = EvalRun(name="Progress Run", dataset_id=dataset.id, total_cases=1, status="running")
    session.add(run)
    session.flush()
    run_id = run.id

    # Pre-create a result
    result = EvalResult(run_id=run_id, case_id=case.id, status="pending")
    session.add(result)
    session.commit()
    session.close()

    collected = []

    def complete_run_later():
        time.sleep(0.3)
        s = TestingSessionLocal()
        r = s.get(EvalRun, run_id)
        r.status = "completed"
        r.passed_cases = 1
        r.failed_cases = 0
        s.commit()
        s.close()

    t = threading.Thread(target=complete_run_later, daemon=True)
    t.start()

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.routers.runs.SessionLocal", TestingSessionLocal)
    async def _no_redis():
        return None
    monkeypatch.setattr("app.routers.runs.get_async_redis", _no_redis)
    client = TestClient(app)

    try:
        with client.stream("GET", f"/api/runs/{run_id}/stream", timeout=10) as response:
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    collected.append(data)
                    if data.get("status") == "completed":
                        break
    finally:
        app.dependency_overrides.clear()

    assert len(collected) >= 2  # initial + completed
    assert collected[-1]["status"] == "completed"


def test_sse_returns_error_for_nonexistent_run(monkeypatch, tmp_path: Path):
    """SSE stream for a non-existent run returns error."""
    _, TestingSessionLocal = _setup_db(tmp_path)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.get("/api/runs/99999/stream")
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
    finally:
        app.dependency_overrides.clear()


def test_sse_with_redis_pubsub(monkeypatch, tmp_path: Path):
    """SSE streams progress via Redis pubsub when Redis is available."""
    _, TestingSessionLocal = _setup_db(tmp_path)
    session = TestingSessionLocal()
    dataset = Dataset(name="Redis SSE", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    case = EvalCase(dataset_id=dataset.id, case_type="qa", input="Hi?")
    session.add(case)
    session.flush()

    run = EvalRun(name="Redis Run", dataset_id=dataset.id, total_cases=1, status="running")
    session.add(run)
    session.flush()
    run_id = run.id

    result = EvalResult(run_id=run_id, case_id=case.id, status="pending")
    session.add(result)
    session.commit()
    session.close()

    # Fake async Redis with pubsub
    class FakePubSub:
        def __init__(self):
            self._call_count = 0
            self.subscribed = False
            self.unsubscribed = False
            self.closed = False

        async def subscribe(self, *args, **kwargs):
            self.subscribed = True

        async def get_message(self, timeout=1.0, ignore_subscribe_messages=True):
            self._call_count += 1
            if self._call_count == 1:
                return None  # simulate timeout → DB poll fallback
            payload = json.dumps({
                "run_id": run_id, "status": "completed",
                "total": 1, "completed": 1, "passed": 1, "failed": 0,
            })
            return {"type": "message", "data": payload}

        async def unsubscribe(self):
            self.unsubscribed = True

        async def close(self):
            self.closed = True

    class FakeRedis:
        def pubsub(self):
            return FakePubSub()

    async def fake_get_async_redis():
        return FakeRedis()

    monkeypatch.setattr("app.routers.runs.get_async_redis", fake_get_async_redis)
    monkeypatch.setattr("app.routers.runs.SessionLocal", TestingSessionLocal)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    collected = []
    try:
        with client.stream("GET", f"/api/runs/{run_id}/stream", timeout=10) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    collected.append(data)
                    if data.get("status") == "completed":
                        break
    finally:
        app.dependency_overrides.clear()

    assert len(collected) >= 2
    assert collected[-1]["status"] == "completed"
