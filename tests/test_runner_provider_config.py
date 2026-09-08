from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.apikey import APIKey, _encode
from app.services.runner import _resolve_provider_config


def test_runner_reads_fernet_encrypted_api_key_from_database(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "provider_config.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = testing_session_local()
    session.add(
        APIKey(
            provider="openai",
            api_key=_encode("sk-live-test"),
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o-mini",
        )
    )
    session.commit()
    session.close()

    monkeypatch.setattr("app.database.SessionLocal", testing_session_local)

    config = _resolve_provider_config("openai")

    assert config["api_key"] == "sk-live-test"
    assert config["base_url"] == "https://api.openai.com/v1"
    assert config["model"] == "gpt-4o-mini"
