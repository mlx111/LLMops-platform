from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base, get_db
from app.main import app
from app.models.apikey import APIKey, _encode
from app.services import provider_models


def _make_client(tmp_path: Path):
    db_path = tmp_path / "test_provider_models.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), testing_session_local


def test_provider_models_returns_live_models_from_provider(monkeypatch, tmp_path: Path):
    client, session_factory = _make_client(tmp_path)
    session = session_factory()
    session.add(
        APIKey(
            provider="openai",
            api_key=_encode("sk-test"),
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o-mini",
        )
    )
    session.commit()
    session.close()

    monkeypatch.setattr(
        "app.services.provider_models._fetch_openai_models",
        lambda config: [
            {"id": "gpt-4o-mini", "label": "gpt-4o-mini", "owned_by": "openai"},
            {"id": "gpt-4.1", "label": "gpt-4.1", "owned_by": "openai"},
        ],
    )

    try:
        response = client.get("/api/config/providers/openai/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai"
    assert payload["source"] == "live"
    assert payload["warning"] is None
    assert [item["id"] for item in payload["models"]] == ["gpt-4.1", "gpt-4o-mini"]


def test_provider_models_falls_back_to_default_model_without_api_key(tmp_path: Path):
    client, _ = _make_client(tmp_path)

    try:
        response = client.get("/api/config/providers/openai/models")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider"] == "openai"
    assert payload["source"] == "fallback"
    assert payload["models"] == [
        {"id": "gpt-4o-mini", "label": "gpt-4o-mini", "owned_by": "openai"}
    ]
    assert "API key" in payload["warning"]


def test_provider_models_caches_live_results_until_ttl_expires(monkeypatch, tmp_path: Path):
    client, session_factory = _make_client(tmp_path)
    session = session_factory()
    session.add(
        APIKey(
            provider="openai",
            api_key=_encode("sk-test"),
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o-mini",
        )
    )
    session.commit()
    session.close()

    provider_models.clear_provider_model_cache()
    calls = {"count": 0}
    now = {"value": 1000.0}

    def fake_fetch(config):
        calls["count"] += 1
        return [{"id": f'gpt-4o-mini-{calls["count"]}', "label": "gpt-4o-mini", "owned_by": "openai"}]

    monkeypatch.setattr("app.services.provider_models._fetch_openai_models", fake_fetch)
    monkeypatch.setattr("app.services.provider_models.time.time", lambda: now["value"])

    try:
        first = client.get("/api/config/providers/openai/models")
        second = client.get("/api/config/providers/openai/models")
        now["value"] += provider_models.PROVIDER_MODEL_CACHE_TTL_SECONDS + 1
        third = client.get("/api/config/providers/openai/models")
    finally:
        app.dependency_overrides.clear()
        provider_models.clear_provider_model_cache()

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert calls["count"] == 2
    assert first.json()["models"][0]["id"] == "gpt-4o-mini-1"
    assert second.json()["models"][0]["id"] == "gpt-4o-mini-1"
    assert third.json()["models"][0]["id"] == "gpt-4o-mini-2"
