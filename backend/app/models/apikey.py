import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import decrypt as _crypto_decrypt, encrypt as _crypto_encrypt
from app.database import Base


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    api_key: Mapped[str] = mapped_column(String(512), nullable=False)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )

    def mask_key(self) -> str:
        """Return masked key for display, e.g. 'sk-e6a4...3921e'"""
        raw = _decode(self.api_key)
        if len(raw) <= 12:
            return "*" * len(raw)
        return raw[:6] + "****" + raw[-4:]


def _encode(raw: str) -> str:
    """Encrypt a raw API key for storage."""
    return _crypto_encrypt(raw)


def _decode(encoded: str) -> str:
    """Decrypt a stored API key to its plaintext form."""
    return _crypto_decrypt(encoded)
