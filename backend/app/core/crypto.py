"""Encryption key management using Fernet symmetric encryption.

The encryption key is read from the ENCRYPTION_KEY environment variable.
If missing, a key is auto-generated and persisted to the .env file so it
survives restarts.
"""

import os
import re
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_ENCRYPTION_KEY_ENV = "ENCRYPTION_KEY"
_DOTENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def _get_or_create_key() -> bytes:
    """Get the encryption key from the environment, or generate + persist it."""
    key_str = os.environ.get(_ENCRYPTION_KEY_ENV)
    if key_str:
        return key_str.encode()

    # Generate a new key and persist to .env
    key = Fernet.generate_key()
    _persist_key_to_dotenv(key.decode())
    os.environ[_ENCRYPTION_KEY_ENV] = key.decode()
    return key


def _persist_key_to_dotenv(key_str: str) -> None:
    """Write or update ENCRYPTION_KEY in the .env file."""
    try:
        if _DOTENV_PATH.exists():
            content = _DOTENV_PATH.read_text(encoding="utf-8")
            if re.search(rf"^{_ENCRYPTION_KEY_ENV}=", content, re.MULTILINE):
                content = re.sub(
                    rf"^{_ENCRYPTION_KEY_ENV}=.*",
                    f"{_ENCRYPTION_KEY_ENV}={key_str}",
                    content,
                    flags=re.MULTILINE,
                )
            else:
                content = content.rstrip() + f"\n{_ENCRYPTION_KEY_ENV}={key_str}\n"
        else:
            content = f"{_ENCRYPTION_KEY_ENV}={key_str}\n"
        _DOTENV_PATH.write_text(content, encoding="utf-8")
    except OSError:
        pass  # .env not writable — key exists in env for this session


_fernet = Fernet(_get_or_create_key())


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns a base64-encoded ciphertext string."""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a string that was encrypted with encrypt()."""
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        raise ValueError("Failed to decrypt API key — ENCRYPTION_KEY may have changed")
