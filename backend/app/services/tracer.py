"""Trace and TraceStep recorder for evaluation observability."""

import datetime
import time
import uuid
from contextlib import contextmanager

from sqlalchemy.orm import Session

from app.models.trace import Trace, TraceStep
from app.services.logger import logger

SPAN_KINDS = [
    "LLM", "CHAIN", "TOOL", "RETRIEVER", "RERANKER", "EMBEDDING", "AGENT",
]


class Tracer:
    """Records pipeline traces during evaluation."""

    def __init__(self, db: Session):
        self.db = db

    def start_trace(
        self,
        run_id: int,
        case_id: int,
        user_input: str,
        prompt_version: str | None = None,
        model: str | None = None,
        retriever_version: str | None = None,
    ) -> str:
        trace_id = uuid.uuid4().hex[:16]
        logger.debug(f"Trace {trace_id}: started for case {case_id}, model={model}")
        trace = Trace(
            trace_id=trace_id,
            run_id=run_id,
            case_id=case_id,
            user_input=user_input,
            prompt_version=prompt_version,
            model=model,
            retriever_version=retriever_version,
        )
        self.db.add(trace)
        self.db.flush()
        return trace_id

    @contextmanager
    def step(
        self,
        trace_id: str,
        step_name: str,
        step_type: str,
        parent_step_id: int | None = None,
        input_data: dict | None = None,
    ):
        if step_type not in SPAN_KINDS:
            step_type = "CHAIN"

        order = (
            self.db.query(TraceStep)
            .filter(TraceStep.trace_id_link == trace_id)
            .count()
        )

        step = TraceStep(
            trace_id_link=trace_id,
            step_name=step_name,
            step_type=step_type,
            parent_step_id=parent_step_id,
            input_json=input_data,
            order_index=order,
        )
        self.db.add(step)
        self.db.flush()

        start = time.time()
        try:
            yield step
        except Exception as exc:
            elapsed = int((time.time() - start) * 1000)
            step.latency_ms = elapsed
            step.error_message = str(exc)[:2000]
            self.db.flush()
            logger.warning(f"Step '{step_name}' failed after {elapsed}ms: {str(exc)[:500]}")
            raise
        else:
            elapsed = int((time.time() - start) * 1000)
            step.latency_ms = elapsed
            if elapsed > 30000:
                logger.warning(f"Step '{step_name}' took {elapsed}ms (threshold exceeded)")

    def set_step_output(self, step_id: int, output_data: dict, tokens: int | None = None):
        step = self.db.get(TraceStep, step_id)
        if step:
            step.output_json = output_data
            if tokens is not None:
                step.tokens = tokens
            self.db.flush()

    def end_trace(self, trace_id: str, status: str = "success", error_message: str | None = None):
        trace = self.db.query(Trace).filter(Trace.trace_id == trace_id).first()
        if not trace:
            return

        steps = (
            self.db.query(TraceStep)
            .filter(TraceStep.trace_id_link == trace_id)
            .all()
        )
        trace.total_latency_ms = sum(s.latency_ms for s in steps)
        trace.total_tokens = sum(s.tokens or 0 for s in steps)
        trace.status = status
        if error_message:
            trace.error_message = error_message
        self.db.flush()
        logger.info(f"Trace {trace_id} ended: status={status}, latency={trace.total_latency_ms}ms, tokens={trace.total_tokens}")
