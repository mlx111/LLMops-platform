"""Tests for creating runs with target_url: validation and propagation."""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.dataset import Dataset, EvalCase


def _setup(tmp_path: Path):
    db_path = tmp_path / "test_target_url.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    dataset = Dataset(name="URL Test DS", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    session.add(EvalCase(dataset_id=dataset.id, case_type="qa", input="Q1"))
    session.commit()
    session.close()
    return TestingSessionLocal


def test_create_run_with_valid_target_url(tmp_path: Path):
    """A valid https target_url is stored in config_json."""
    TestingSessionLocal = _setup(tmp_path)

    scheduled = []

    def fake_run_evaluation(run_id: int):
        scheduled.append(run_id)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides["app.tasks.celery_app.run_evaluation"] = fake_run_evaluation
    client = TestClient(app)

    try:
        response = client.post("/api/runs", json={
            "name": "URL Test",
            "dataset_id": 1,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "target_url": "https://rag.example.com/query",
            "target_type": "rag",
            "target_timeout": 60,
        })
        assert response.status_code == 200
        config = response.json()["config_json"]
        assert config["target_url"] == "https://rag.example.com/query"
        assert config["target_type"] == "rag"
        assert config["target_timeout"] == 60
    finally:
        app.dependency_overrides.clear()


def test_create_run_rejects_ssrf_localhost(tmp_path: Path):
    """Localhost URLs are rejected by SSRF validation."""
    TestingSessionLocal = _setup(tmp_path)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.post("/api/runs", json={
            "name": "SSRF Test",
            "dataset_id": 1,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "target_url": "http://localhost:8080/query",
        })
        assert response.status_code == 400
        assert "localhost" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_create_run_rejects_ssrf_private_ip(tmp_path: Path):
    """Private IP URLs are rejected by SSRF validation."""
    TestingSessionLocal = _setup(tmp_path)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.post("/api/runs", json={
            "name": "SSRF Test",
            "dataset_id": 1,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "target_url": "http://192.168.1.1/query",
        })
        assert response.status_code == 400
        assert "private" in response.json()["detail"].lower() or "internal" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_create_run_rejects_file_scheme(tmp_path: Path):
    """file:// URLs are rejected."""
    TestingSessionLocal = _setup(tmp_path)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.post("/api/runs", json={
            "name": "SSRF Test",
            "dataset_id": 1,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "target_url": "file:///etc/passwd",
        })
        assert response.status_code == 400
        assert "scheme" in response.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


def test_create_run_rejects_loopback_ip(tmp_path: Path):
    """127.0.0.1 is rejected by SSRF validation."""
    TestingSessionLocal = _setup(tmp_path)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        response = client.post("/api/runs", json={
            "name": "SSRF Test",
            "dataset_id": 1,
            "provider": "openai",
            "model": "gpt-4o-mini",
            "target_url": "http://127.0.0.1:8000/query",
        })
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_create_run_rejects_domain_resolving_to_private_ip(tmp_path: Path):
    """Domains that resolve to private IPs are rejected."""
    TestingSessionLocal = _setup(tmp_path)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    try:
        with patch("app.services.url_safety.socket.getaddrinfo", return_value=[
            (0, 0, 0, "", ("10.0.0.8", 443)),
        ]):
            response = client.post("/api/runs", json={
                "name": "DNS SSRF Test",
                "dataset_id": 1,
                "provider": "openai",
                "model": "gpt-4o-mini",
                "target_url": "https://internal.example/query",
            })
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "resolved" in response.json()["detail"].lower() or "private" in response.json()["detail"].lower()
