"""Tests for the trace event stream (Kafka producer side of the analytics pipe).

The producer is best-effort: with no KAFKA_BOOTSTRAP_SERVERS configured
(or kafka-python missing / broker down) it must silently return False and
never break the evaluation flow.
"""

import pytest

from app.config import settings
from app.services import trace_stream
from app.services.trace_stream import _trace_to_event, send_trace_event
from app.models.trace import Trace, TraceStep


@pytest.fixture(autouse=True)
def _reset_producer_state(monkeypatch):
    monkeypatch.setattr(trace_stream, "_producer", None)
    yield
    monkeypatch.setattr(trace_stream, "_producer", None)


def _sample_trace() -> Trace:
    trace = Trace(
        trace_id="abc123",
        run_id=7,
        case_id=42,
        user_input="什么是机器学习？",
        session_id="sess-1",
        model="deepseek-chat",
        prompt_version="v1",
        retriever_version="v2",
        status="success",
        total_latency_ms=1234,
        total_tokens=456,
    )
    steps = [
        TraceStep(
            trace_id_link="abc123",
            step_name="target_call",
            step_type="LLM",
            step_key="s1",
            parent_step_id=None,
            latency_ms=1000,
            tokens=400,
            order_index=0,
        ),
        TraceStep(
            trace_id_link="abc123",
            step_name="evaluation",
            step_type="CHAIN",
            step_key="s2",
            parent_step_id=None,
            latency_ms=234,
            tokens=56,
            order_index=1,
        ),
    ]
    return trace, steps


def test_send_disabled_without_bootstrap(monkeypatch):
    monkeypatch.setattr(settings, "kafka_bootstrap_servers", "")
    trace, steps = _sample_trace()
    assert send_trace_event(trace, steps) is False


def test_event_shape():
    trace, steps = _sample_trace()
    event = _trace_to_event(trace, steps)
    assert event["event_type"] == "trace_completed"
    assert event["trace_id"] == "abc123"
    assert event["run_id"] == 7
    assert event["status"] == "success"
    assert event["total_latency_ms"] == 1234
    assert len(event["steps"]) == 2
    assert event["steps"][0]["step_type"] == "LLM"
    assert event["steps"][0]["step_name"] == "target_call"


def test_send_publishes_with_run_id_key(monkeypatch):
    monkeypatch.setattr(settings, "kafka_bootstrap_servers", "localhost:9092")
    monkeypatch.setattr(settings, "kafka_topic_traces", "llmops.trace.events")

    sent = {}

    class FakeFuture:
        def get(self, timeout=None):
            return None

    class FakeProducer:
        def send(self, topic, key=None, value=None):
            sent["topic"] = topic
            sent["key"] = key
            sent["value"] = value
            return FakeFuture()

    monkeypatch.setattr(trace_stream, "_get_producer", lambda: FakeProducer())

    trace, steps = _sample_trace()
    assert send_trace_event(trace, steps) is True
    assert sent["topic"] == "llmops.trace.events"
    assert sent["key"] == "7"  # same run -> same partition -> ordered
    assert sent["value"]["trace_id"] == "abc123"


def test_send_failure_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "kafka_bootstrap_servers", "localhost:9092")

    class BrokenProducer:
        def send(self, *args, **kwargs):
            raise ConnectionError("broker down")

    monkeypatch.setattr(trace_stream, "_get_producer", lambda: BrokenProducer())

    trace, steps = _sample_trace()
    assert send_trace_event(trace, steps) is False