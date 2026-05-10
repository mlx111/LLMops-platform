import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parents[2] / "logs"
LOG_DIR.mkdir(exist_ok=True)

_SENSITIVE_FIELDS = {"api_key", "api_key_masked", "password", "token", "secret", "authorization"}


def _sanitize(msg: str) -> str:
    """Strip sensitive key material from log messages (best-effort)."""
    import re
    for field in _SENSITIVE_FIELDS:
        msg = re.sub(
            rf'("{field}"\s*:\s*")[^"]+(")',
            rf'\1***\2',
            msg,
            flags=re.IGNORECASE,
        )
        msg = re.sub(
            rf'(\b{field}=)[^\s,]+',
            rf'\1***',
            msg,
            flags=re.IGNORECASE,
        )
    return msg


class _SanitizingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return _sanitize(original)


def setup_logger(name: str = "llmops") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))

    file_handler = RotatingFileHandler(
        LOG_DIR / "llmops.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(_SanitizingFormatter(
        "%(asctime)s [%(levelname)s] %(name)s %(filename)s:%(lineno)d: %(message)s",
    ))

    logger.addHandler(console)
    logger.addHandler(file_handler)
    return logger


logger = setup_logger()
