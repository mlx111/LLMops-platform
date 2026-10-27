"""L2 Insight Agent 端点：自动扫历史 run + 分类失败模式 + 聚类用户意图。

GET /api/insights/generate?days=7
  返回 ReAct agent 的洞察结果：failure_distribution / top_failure_categories /
  intent_clusters / insight_summary / trace / judge / iterations / elapsed_ms。

agent 失败兜底返回 judge="agentic_insight_fallback" 或 "agentic_insight_timeout"，
绝不抛 HTTP 5xx，便于前端展示兜底文案。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.insight_agent import agentic_insight
from app.services.logger import logger

router = APIRouter(prefix="/api", tags=["insights"])


@router.get("/insights/generate")
def generate_insights(
    days: int = Query(7, ge=1, le=90, description="回溯天数"),
    db: Session = Depends(get_db),
) -> dict:
    """触发 L2 Insight Agent 分析最近 N 天的评测 run。

    同步执行（agent 内部最多 6 轮 ReAct，每轮一次 LLM 调用）。
    兜底：LLM 不可用 → judge="agentic_insight_fallback"；
    超过最大轮次 → judge="agentic_insight_timeout"。
    """
    logger.info("Insight agent triggered: days=%d", days)
    result = agentic_insight(db, days=days)
    logger.info(
        "Insight agent done: judge=%s, iterations=%s, elapsed_ms=%s",
        result.get("judge"),
        result.get("iterations"),
        result.get("elapsed_ms"),
    )
    return result
