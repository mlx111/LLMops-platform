"""ClickHouse 客户端（读侧/建表用），惰性初始化 + 降级。

与 core/redis.py 同一套约定：返回 None 表示不可用，调用方必须
自行降级（Dashboard 高级分析降级 SQLite 明细聚合）。
clickhouse-connect 未安装时同样返回 None——本地裸跑不装该依赖
也能正常启动。
"""

import threading
from urllib.parse import urlparse

from app.config import settings
from app.services.logger import logger

_client = None  # clickhouse_connect client | False (失败短路) | None (未初始化)
_lock = threading.Lock()

TRACE_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS {db}.trace_events
(
    trace_id String,
    run_id Int64,
    case_id Int64,
    user_input String,
    session_id String,
    model String,
    prompt_version String,
    retriever_version String,
    status LowCardinality(String),
    total_latency_ms UInt64,
    total_tokens UInt64,
    error_message String,
    created_at DateTime,
    event_date Date MATERIALIZED toDate(created_at)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (run_id, created_at)
"""

SPAN_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS {db}.trace_span_events
(
    trace_id String,
    step_name String,
    step_type LowCardinality(String),
    step_key String,
    parent_step_id Int64,
    latency_ms UInt64,
    tokens UInt64,
    order_index UInt32,
    error_message String,
    created_at DateTime,
    event_date Date MATERIALIZED toDate(created_at)
)
ENGINE = MergeTree
PARTITION BY toYYYYMM(event_date)
ORDER BY (trace_id, order_index)
"""


def _parse_url() -> tuple[str, int]:
    parsed = urlparse(settings.clickhouse_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8123
    return host, port


def get_clickhouse():
    """Return a ClickHouse client or None when unavailable/disabled."""
    global _client
    if not settings.clickhouse_url:
        return None
    if _client is not None:
        return _client if _client is not True else None

    with _lock:
        if _client is not None:
            return _client if _client is not True else None
        try:
            import clickhouse_connect

            host, port = _parse_url()
            _client = clickhouse_connect.get_client(
                host=host,
                port=port,
                username=settings.clickhouse_user,
                password=settings.clickhouse_password,
                database=settings.clickhouse_db,
                connect_timeout=3,
            )
            _client.command("SELECT 1")
            logger.info(f"ClickHouse analytics ready: {settings.clickhouse_url}")
            return _client
        except Exception as exc:
            _client = False  # 短路：避免每次请求都重试连接
            logger.warning(f"ClickHouse unavailable, analytics degrades to SQLite: {exc}")
            return None


def ensure_tables(client) -> None:
    """Create the trace event tables (idempotent)."""
    client.command(TRACE_EVENTS_DDL.format(db=settings.clickhouse_db))
    client.command(SPAN_EVENTS_DDL.format(db=settings.clickhouse_db))


def reset_client():
    """Force re-connection on next call (used after failures)."""
    global _client
    _client = None
