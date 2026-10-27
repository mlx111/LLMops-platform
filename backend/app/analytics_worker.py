"""Kafka → ClickHouse trace 事件消费者（大数据管道落库侧）。

启动方式（compose 中由 ch-worker 服务运行）::

    python -m app.analytics_worker

职责：
1. 启动时在 ClickHouse 幂等建表（trace_events / trace_span_events）；
2. 从 Kafka topic ``llmops.trace.events`` 批量消费 trace 完成事件；
3. 微批写入 ClickHouse（MergeTree 按月分区），手动 commit offset
   实现 at-least-once 语义——CH 写入失败不提交，重启后重放补数。

降级约定：Kafka / ClickHouse 任一端暂不可用时按固定间隔重试，
进程不退出（容器由 restart: unless-stopped 兜底）。
"""

import os
import signal
import time
from datetime import datetime

from app.config import settings
from app.core.clickhouse import ensure_tables
from app.services.logger import logger

CONSUME_BATCH = 200
POLL_TIMEOUT_S = 1.0
RETRY_INTERVAL_S = 5.0


def _as_str(value) -> str:
    return "" if value is None else str(value)


def _as_int(value) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def _parse_created_at(value) -> datetime:
    if not value:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.utcnow()


def _trace_rows(event: dict) -> list:
    return [
        (
            _as_str(event.get("trace_id")),
            _as_int(event.get("run_id")),
            _as_int(event.get("case_id")),
            _as_str(event.get("user_input")),
            _as_str(event.get("session_id")),
            _as_str(event.get("model")),
            _as_str(event.get("prompt_version")),
            _as_str(event.get("retriever_version")),
            _as_str(event.get("status")) or "success",
            _as_int(event.get("total_latency_ms")),
            _as_int(event.get("total_tokens")),
            _as_str(event.get("error_message")),
            _parse_created_at(event.get("created_at")),
        )
    ]


def _span_rows(event: dict) -> list:
    created = _parse_created_at(event.get("created_at"))
    rows = []
    for s in event.get("steps") or []:
        rows.append(
            (
                _as_str(s.get("trace_id")) or _as_str(event.get("trace_id")),
                _as_str(s.get("step_name")),
                _as_str(s.get("step_type")) or "CHAIN",
                _as_str(s.get("step_key")),
                _as_int(s.get("parent_step_id")),
                _as_int(s.get("latency_ms")),
                _as_int(s.get("tokens")),
                _as_int(s.get("order_index")),
                _as_str(s.get("error_message")),
                created,
            )
        )
    return rows


def _insert_batch(client, batch: list[dict]) -> None:
    trace_rows: list[tuple] = []
    span_rows: list[tuple] = []
    for event in batch:
        trace_rows.extend(_trace_rows(event))
        span_rows.extend(_span_rows(event))

    if trace_rows:
        client.insert(
            "trace_events",
            trace_rows,
            column_names=[
                "trace_id", "run_id", "case_id", "user_input", "session_id",
                "model", "prompt_version", "retriever_version", "status",
                "total_latency_ms", "total_tokens", "error_message", "created_at",
            ],
        )
    if span_rows:
        client.insert(
            "trace_span_events",
            span_rows,
            column_names=[
                "trace_id", "step_name", "step_type", "step_key",
                "parent_step_id", "latency_ms", "tokens", "order_index",
                "error_message", "created_at",
            ],
        )


def _build_consumer():
    from kafka import KafkaConsumer

    return KafkaConsumer(
        settings.kafka_topic_traces,
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id="ch-analytics",
        auto_offset_reset="earliest",
        enable_auto_commit=False,  # at-least-once：写入 CH 成功后才提交 offset
        value_deserializer=lambda v: v,  # raw json bytes, decode later
        consumer_timeout_ms=1000,
    )


def run() -> None:
    if not settings.kafka_bootstrap_servers or not settings.clickhouse_url:
        logger.warning("analytics_worker disabled: KAFKA_BOOTSTRAP_SERVERS or CLICKHOUSE_URL not set")
        return

    import json

    from app.core.clickhouse import get_clickhouse, reset_client

    stopping = {"flag": False}

    def _handle_signal(signum, frame):
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    consumer = None
    client = None
    pending: list[dict] = []

    logger.info(
        f"analytics_worker starting: topic={settings.kafka_topic_traces}, "
        f"bootstrap={settings.kafka_bootstrap_servers}, ch={settings.clickhouse_url}"
    )

    while not stopping["flag"]:
        # --- ClickHouse 侧就绪检查 ---
        if client is None:
            client = get_clickhouse()
            if client is not None:
                try:
                    ensure_tables(client)
                    logger.info("ClickHouse tables ensured (trace_events / trace_span_events)")
                except Exception as exc:
                    logger.warning(f"ensure_tables failed, will retry: {exc}")
                    reset_client()
                    client = None
                    time.sleep(RETRY_INTERVAL_S)
                    continue
            else:
                time.sleep(RETRY_INTERVAL_S)
                continue

        # --- Kafka 侧就绪检查 ---
        if consumer is None:
            try:
                consumer = _build_consumer()
                logger.info("Kafka consumer connected")
            except Exception as exc:
                logger.warning(f"Kafka consumer unavailable, retry in {RETRY_INTERVAL_S}s: {exc}")
                time.sleep(RETRY_INTERVAL_S)
                continue

        # --- 拉取 + 微批写入 ---
        try:
            records = consumer.poll(timeout_ms=int(POLL_TIMEOUT_S * 1000), max_records=CONSUME_BATCH)
            raw_events = []
            for _tp, messages in records.items():
                for msg in messages:
                    try:
                        raw_events.append(json.loads(msg.value.decode("utf-8")))
                    except Exception as exc:
                        logger.warning(f"skip malformed kafka message: {exc}")
            if not raw_events:
                continue

            pending.extend(raw_events)
            _insert_batch(client, pending)
            consumer.commit()
            logger.info(f"ingested {len(pending)} trace events into ClickHouse")
            pending = []
        except Exception as exc:
            # 写入失败：不提交 offset，重启后重放（at-least-once）
            logger.warning(f"ingest batch failed, offsets not committed: {exc}")
            pending = []
            reset_client()
            client = None
            try:
                if consumer is not None:
                    consumer.close()
            except Exception:
                pass
            consumer = None
            time.sleep(RETRY_INTERVAL_S)

    # --- 优雅退出 ---
    logger.info("analytics_worker shutting down")
    if consumer is not None:
        try:
            consumer.close()
        except Exception:
            pass


if __name__ == "__main__":
    run()