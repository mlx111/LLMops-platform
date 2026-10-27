"""Dashboard 高级分析聚合：ClickHouse 优先，SQLite 降级。

管道侧（trace_stream + analytics_worker）把 trace 事件经 Kafka 批量
写入 ClickHouse；本模块是读侧——当 ClickHouse 可用时，按天/按模型/
按 span 类型的聚合查询全部下推到 CH（列存 + 向量化执行，数据量
增长后仍是毫秒级）；CH 不可用时自动降级为 SQLite 明细表聚合，
保证两种数据源输出同一套指标口径（``source`` 字段标明当前来源）。

为什么需要 CH：traces/trace_steps 是典型的 append-only 时序事件流，
随评测次数线性增长。SQLite 的明细行查询在百万级 span 后聚合会明显
变慢，而 OLAP 列存正是为此设计的——这也是 Langfuse v3 等产品的
同款架构选型（Kafka 缓冲 + ClickHouse 分析）。
"""

import datetime

from sqlalchemy.orm import Session

from app.core.clickhouse import get_clickhouse
from app.models.trace import Trace, TraceStep
from app.services.logger import logger


def _round(value, digits=1):
    return round(value, digits) if value is not None else None


def _pass_rate(passed: int, total: int) -> float | None:
    return round(passed / total * 100, 1) if total else None


def build_analytics(days: int, db: Session) -> dict:
    """Entry point: try ClickHouse, fall back to SQLite aggregates."""
    client = get_clickhouse()
    if client is not None:
        try:
            return _from_clickhouse(client, days)
        except Exception as exc:
            logger.warning(f"ClickHouse analytics query failed, degrade to SQLite: {exc}")
    return _from_sqlite(db, days)


# ---------------------------------------------------------------- ClickHouse

def _from_clickhouse(client, days: int) -> dict:
    trend = []
    for row in client.query(
        """
        SELECT toDate(created_at) AS d,
               count() AS traces,
               uniqExact(run_id) AS runs,
               countIf(status = 'success') AS passed,
               avg(total_latency_ms) AS avg_latency,
               quantile(0.95)(total_latency_ms) AS p95_latency,
               sum(total_tokens) AS tokens
        FROM trace_events
        WHERE created_at >= now() - INTERVAL {d:d} DAY
        GROUP BY d ORDER BY d
        """.format(d=days)
    ).result_rows:
        d, traces, runs, passed, avg_lat, p95, tokens = row
        trend.append(
            {
                "date": str(d),
                "traces": int(traces),
                "runs": int(runs),
                "pass_rate": _pass_rate(int(passed), int(traces)),
                "avg_latency_ms": _round(float(avg_lat)) if avg_lat is not None else None,
                "p95_latency_ms": _round(float(p95)) if p95 is not None else None,
                "tokens": int(tokens or 0),
            }
        )

    by_model = []
    for row in client.query(
        """
        SELECT if(model = '', 'unknown', model) AS m,
               count() AS traces,
               avg(total_latency_ms) AS avg_latency,
               sum(total_tokens) AS tokens
        FROM trace_events
        WHERE created_at >= now() - INTERVAL {d:d} DAY
        GROUP BY m ORDER BY traces DESC LIMIT 10
        """.format(d=days)
    ).result_rows:
        model, traces, avg_lat, tokens = row
        by_model.append(
            {
                "model": model,
                "traces": int(traces),
                "avg_latency_ms": _round(float(avg_lat)) if avg_lat is not None else None,
                "tokens": int(tokens or 0),
            }
        )

    span_breakdown = []
    for row in client.query(
        """
        SELECT step_type, count() AS cnt,
               avg(latency_ms) AS avg_ms, sum(latency_ms) AS total_ms
        FROM trace_span_events
        WHERE created_at >= now() - INTERVAL {d:d} DAY
        GROUP BY step_type ORDER BY total_ms DESC
        """.format(d=days)
    ).result_rows:
        step_type, cnt, avg_ms, total_ms = row
        span_breakdown.append(
            {
                "step_type": step_type,
                "count": int(cnt),
                "avg_latency_ms": _round(float(avg_ms)) if avg_ms is not None else None,
                "total_ms": int(total_ms or 0),
            }
        )

    return {"source": "clickhouse", "days": days, "trend": trend, "by_model": by_model, "span_breakdown": span_breakdown}


# -------------------------------------------------------------------- SQLite

def _percentile95(values: list[int]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    idx = max(0, min(len(values) - 1, int(round(0.95 * (len(values) - 1)))))
    return float(values[idx])


def _from_sqlite(db: Session, days: int) -> dict:
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    traces = (
        db.query(Trace)
        .filter(Trace.created_at >= since)
        .order_by(Trace.created_at.asc())
        .all()
    )

    daily: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for t in traces:
        day = t.created_at.strftime("%Y-%m-%d")
        bucket = daily.setdefault(
            day, {"traces": 0, "runs": set(), "passed": 0, "latencies": [], "tokens": 0}
        )
        bucket["traces"] += 1
        if t.run_id is not None:
            bucket["runs"].add(t.run_id)
        if t.status == "success":
            bucket["passed"] += 1
        bucket["latencies"].append(t.total_latency_ms or 0)
        bucket["tokens"] += t.total_tokens or 0

        m = t.model or "unknown"
        mb = by_model.setdefault(m, {"traces": 0, "latencies": [], "tokens": 0})
        mb["traces"] += 1
        mb["latencies"].append(t.total_latency_ms or 0)
        mb["tokens"] += t.total_tokens or 0

    trend = []
    for day in sorted(daily):
        b = daily[day]
        trend.append(
            {
                "date": day,
                "traces": b["traces"],
                "runs": len(b["runs"]),
                "pass_rate": _pass_rate(b["passed"], b["traces"]),
                "avg_latency_ms": _round(sum(b["latencies"]) / len(b["latencies"])),
                "p95_latency_ms": _round(_percentile95(b["latencies"])),
                "tokens": b["tokens"],
            }
        )

    model_rows = []
    for model, mb in sorted(by_model.items(), key=lambda kv: -kv[1]["traces"])[:10]:
        model_rows.append(
            {
                "model": model,
                "traces": mb["traces"],
                "avg_latency_ms": _round(sum(mb["latencies"]) / len(mb["latencies"])),
                "tokens": mb["tokens"],
            }
        )

    # span 聚合：join 不必要，trace_id_link 直接分组
    span_q = (
        db.query(TraceStep.step_type, TraceStep.latency_ms)
        .join(Trace, Trace.trace_id == TraceStep.trace_id_link)
        .filter(Trace.created_at >= since)
        .all()
    )
    spans: dict[str, list[int]] = {}
    for step_type, latency in span_q:
        spans.setdefault(step_type or "CHAIN", []).append(latency or 0)
    span_breakdown = [
        {
            "step_type": st,
            "count": len(lats),
            "avg_latency_ms": _round(sum(lats) / len(lats)),
            "total_ms": sum(lats),
        }
        for st, lats in sorted(spans.items(), key=lambda kv: -sum(kv[1]))
    ]

    return {
        "source": "sqlite",
        "days": days,
        "trend": trend,
        "by_model": model_rows,
        "span_breakdown": span_breakdown,
    }