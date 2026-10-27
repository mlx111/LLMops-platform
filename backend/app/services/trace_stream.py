"""Trace 事件 → Kafka 生产端（大数据管道写入侧）。

评测并发执行时每个 case 结束都会产生一条 trace 事件（含全部 span）。
把这些事件发到 Kafka 有两个目的：
1. 削峰解耦：评测 worker 不直接依赖 ClickHouse 的可用性与写入吞吐；
2. 重放能力：ClickHouse 端故障恢复后可以从 offset 重放补数。

设计原则（与 core/redis.py 的降级风格一致）：
- 惰性初始化：首次 send 时才创建 KafkaProducer；
- 全链路 best-effort：kafka-python 未安装 / broker 不可达 / 发送失败
  只记 warning，绝不影响评测主流程；
- 未配置 ``KAFKA_BOOTSTRAP_SERVERS`` 时管道整体关闭（本地裸跑零依赖）。
"""

import json
import threading

from app.config import settings
from app.services.logger import logger

_lock = threading.Lock()
_producer = None  # KafkaProducer | False (初始化失败后短路) | None (未初始化)


def _get_producer():
    """Lazily create the KafkaProducer. Returns None when the pipe is off."""
    global _producer
    if not settings.kafka_bootstrap_servers:
        return None
    if _producer is not None:
        return _producer or None

    with _lock:
        if _producer is not None:
            return _producer or None
        try:
            from kafka import KafkaProducer

            _producer = KafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
                value_serializer=lambda v: json.dumps(v, default=str).encode("utf-8"),
                key_serializer=lambda k: k.encode("utf-8") if k else None,
                acks=1,
                retries=3,
                linger_ms=50,  # 批量微批，提升吞吐
                request_timeout_ms=5000,
            )
            logger.info(f"Kafka trace producer ready: {settings.kafka_bootstrap_servers}")
            return _producer
        except Exception as exc:
            # 短路为 False：后续调用不再反复尝试连接
            _producer = False
            logger.warning(f"Kafka trace producer unavailable, pipe disabled: {exc}")
            return None


def _trace_to_event(trace, steps) -> dict:
    """Flatten a Trace + its TraceSteps into one serializable event dict."""
    return {
        "event_type": "trace_completed",
        "trace_id": trace.trace_id,
        "run_id": trace.run_id,
        "case_id": trace.case_id,
        "user_input": trace.user_input,
        "session_id": trace.session_id,
        "model": trace.model,
        "prompt_version": trace.prompt_version,
        "retriever_version": trace.retriever_version,
        "status": trace.status,
        "total_latency_ms": trace.total_latency_ms or 0,
        "total_tokens": trace.total_tokens or 0,
        "error_message": trace.error_message,
        "created_at": trace.created_at.isoformat(timespec="seconds")
        if trace.created_at
        else None,
        "steps": [
            {
                "trace_id": s.trace_id_link,
                "step_name": s.step_name,
                "step_type": s.step_type,
                "step_key": s.step_key,
                "parent_step_id": s.parent_step_id,
                "latency_ms": s.latency_ms or 0,
                "tokens": s.tokens or 0,
                "order_index": s.order_index or 0,
                "error_message": s.error_message,
            }
            for s in steps
        ],
    }


def send_trace_event(trace, steps) -> bool:
    """Publish a completed trace event. Best-effort: never raises."""
    producer = _get_producer()
    if producer is None:
        return False
    try:
        event = _trace_to_event(trace, steps)
        # key=run_id：同一 run 的事件进同一分区，保证 run 内消费有序
        future = producer.send(
            settings.kafka_topic_traces,
            key=str(trace.run_id) if trace.run_id is not None else None,
            value=event,
        )
        future.get(timeout=3)
        return True
    except Exception as exc:
        logger.warning(f"Failed to publish trace event {getattr(trace, 'trace_id', '?')}: {exc}")
        return False


def close_producer(timeout: float = 2.0):
    """Flush and close on process exit (worker shutdown hooks)."""
    global _producer
    if _producer and _producer is not True:
        try:
            _producer.flush(timeout=timeout)
            _producer.close(timeout=timeout)
        except Exception:
            pass
        _producer = None