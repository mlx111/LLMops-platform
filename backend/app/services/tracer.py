"""Trace and TraceStep recorder for evaluation observability.

DB writes are buffered in memory and flushed once at ``end_trace``. This keeps
the long-running target HTTP call / LLM evaluation from holding an open write
transaction the whole time — under SQLite (WAL) that open transaction otherwise
takes the database write lock for tens of seconds and makes concurrent cases
fail with "database is locked". With buffering, the write lock is only held for
the few milliseconds of the final flush + commit.

Nested spans (e.g. multi-turn ``turn_N`` parents wrapping per-turn
``target_call`` / ``evaluation`` children) are linked via logical ``step_key``
/ ``parent_key`` strings: real PKs do not exist until the final flush, so
``parent_step_id`` is resolved from the key map at flush time.
"""

import time
import uuid
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.models.trace import Trace, TraceStep
from app.services.logger import logger
from app.services.trace_stream import send_trace_event

SPAN_KINDS = [
    "LLM", "CHAIN", "TOOL", "RETRIEVER", "RERANKER", "EMBEDDING", "AGENT",
]


class Tracer:
    """Records pipeline traces during evaluation (buffered, flushed at end)."""

    def __init__(self, db: Session):
        self.db = db
        self._trace: Trace | None = None
        self._pending_steps: list[TraceStep] = []
        # temporary (negative) step id -> in-memory step, used before flush
        self._step_index: dict[int, TraceStep] = {}
        self._tmp_seq = 0

    def start_trace(
        self,
        run_id: int,
        case_id: int,
        user_input: str,
        prompt_version: str | None = None,
        model: str | None = None,
        retriever_version: str | None = None,
        session_id: str | None = None,
    ) -> str:
        trace_id = uuid.uuid4().hex[:16]
        logger.debug(f"Trace {trace_id}: started for case {case_id}, model={model}")
        # Buffered in memory only — no DB write / lock yet.
        self._trace = Trace(
            trace_id=trace_id,
            run_id=run_id,
            case_id=case_id,
            user_input=user_input,
            prompt_version=prompt_version,
            model=model,
            retriever_version=retriever_version,
            session_id=session_id,
        )
        return trace_id

    @contextmanager
    def step(
        self,
        trace_id: str,
        step_name: str,
        step_type: str,
        parent_step_id: int | None = None,
        input_data: dict | None = None,
        step_key: str | None = None,
        parent_key: str | None = None,
    ):
        if step_type not in SPAN_KINDS:
            step_type = "CHAIN"

        self._tmp_seq += 1
        tmp_id = -self._tmp_seq  # negative temp id, never collides with a DB PK

        step = TraceStep(
            trace_id_link=trace_id,
            step_name=step_name,
            step_type=step_type,
            parent_step_id=parent_step_id,
            input_json=input_data,
            order_index=len(self._pending_steps),
            step_key=step_key,
            parent_key=parent_key,
        )
        step.id = tmp_id
        self._pending_steps.append(step)
        self._step_index[tmp_id] = step

        start = time.time()
        try:
            yield step
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            step.latency_ms = elapsed
            step.error_message = str(exc)[:2000]
            logger.warning(f"Step '{step_name}' failed after {elapsed}ms: {str(exc)[:500]}")
            raise
        else:
            elapsed = int((time.time() - start) * 1000)
            step.latency_ms = elapsed
            if elapsed > 30000:
                logger.warning(f"Step '{step_name}' took {elapsed}ms (threshold exceeded)")

    def set_step_output(self, step_id: int, output_data: dict, tokens: int | None = None):
        # Resolve against the in-memory (not-yet-flushed) steps first.
        step = self._step_index.get(step_id)
        if step is None:
            step = self.db.get(TraceStep, step_id)
        if step:
            step.output_json = output_data
            if tokens is not None:
                step.tokens = tokens

    def end_trace(self, trace_id: str, status: str = "success", error_message: str | None = None):
        if not self._trace or self._trace.trace_id != trace_id:
            return

        # Parent spans wrap their children's wall time, so summing every step
        # would double-count. Aggregate latency/tokens over leaf steps only.
        parent_keys = {s.parent_key for s in self._pending_steps if s.parent_key}
        parent_db_ids = {
            s.parent_step_id
            for s in self._pending_steps
            if s.parent_step_id is not None and s.parent_step_id >= 0
        }

        def _is_leaf(s: TraceStep) -> bool:
            if s.step_key and s.step_key in parent_keys:
                return False
            if s.id in parent_db_ids:
                return False
            return True

        leaves = [s for s in self._pending_steps if _is_leaf(s)]
        self._trace.total_latency_ms = sum(s.latency_ms or 0 for s in leaves)
        self._trace.total_tokens = sum(s.tokens or 0 for s in leaves)
        self._trace.status = status
        if error_message:
            self._trace.error_message = error_message

        # Single short write transaction: persist trace + all steps at once.
        self.db.add(self._trace)
        for s in self._pending_steps:
            s.id = None  # clear temp id so the DB assigns the real PK
            self.db.add(s)
        # First flush assigns real PKs while logical keys are still on the rows.
        self.db.flush()

        # Resolve buffered parent_key -> parent_step_id now that PKs exist.
        key_to_id = {s.step_key: s.id for s in self._pending_steps if s.step_key}
        for s in self._pending_steps:
            if s.parent_key and s.parent_step_id is None:
                parent_id = key_to_id.get(s.parent_key)
                if parent_id is not None:
                    s.parent_step_id = parent_id
        self.db.flush()

        # 大数据管道：SQLite 明细落库后，best-effort 把同一事件发到
        # Kafka（削峰解耦，由 ch-worker 批量写入 ClickHouse）。失败只记
        # warning，绝不影响评测主流程（与 Redis/Celery 的降级策略一致）。
        try:
            send_trace_event(self._trace, self._pending_steps)
        except Exception as exc:  # pragma: no cover - send itself is best-effort
            logger.warning(f"trace event publish failed: {exc}")

        logger.info(
            f"Trace {trace_id} ended: status={status}, "
            f"latency={self._trace.total_latency_ms}ms, tokens={self._trace.total_tokens}"
        )
