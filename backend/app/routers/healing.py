"""L3+L4 Self-Healing Agent 端点（L4 半闭环）：
发现失败 → 提议修复 → 高风险卡 HITL / 低风险直接执行 → 触发回归评测 → 对比回归 run 判断修复是否有效。

POST /api/healing/run?run_id=N
  针对某评测 run 触发 Self-Healing agent：
    1. 用 list_run_failures 查失败 case + 9 类分类
    2. 对可修复的高风险失败调 propose_fix 写入 mypaperweb HITL pending（等人工审批）
    3. 调 trigger_regression_run 触发回归评测（低风险直接执行）
    4. 调 compare_regression 对比回归 run vs 原 run，输出 regression_verdict + score_delta（L4 半闭环）
  返回 proposed_fixes / executed_fixes / regression_run_id /
        regression_verdict (improved/declined/unchanged/pending) /
        score_delta / healing_summary / trace / judge / iterations / elapsed_ms。

agent 失败兜底返回 judge="agentic_self_heal_fallback" 或 "agentic_self_heal_timeout"，
绝不抛 HTTP 5xx，便于前端展示兜底文案。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.logger import logger
from app.services.self_heal_agent import agentic_self_heal

router = APIRouter(prefix="/api", tags=["healing"])


@router.post("/healing/run")
def run_self_healing(
    run_id: int = Query(..., ge=1, description="要 self-heal 的评测 run id"),
    db: Session = Depends(get_db),
) -> dict:
    """触发 Self-Healing Agent 处理某 run 的失败 case（L4 半闭环）。

    同步执行（agent 内部最多 6 轮 ReAct，每轮一次 LLM 调用 + HTTP 调 mypaperweb HITL）。
    兜底：LLM 不可用 → judge="agentic_self_heal_fallback"；
    超过最大轮次 → judge="agentic_self_heal_timeout"。
    高风险修复动作绝不直接执行，只写入 mypaperweb HITL pending_store 等人工审批。
    L4 半闭环：触发回归评测后自动调 compare_regression 对比结果，
    regression_verdict=pending 表示回归 run 还在跑（异步，未跑完无法对比）。
    """
    logger.info("Self-Healing agent triggered: run_id=%d", run_id)
    result = agentic_self_heal(db, run_id)
    logger.info(
        "Self-Healing agent done: judge=%s, proposed=%d, executed=%d, "
        "regression_run_id=%s, verdict=%s, score_delta=%s, elapsed_ms=%s",
        result.get("judge"),
        len(result.get("proposed_fixes") or []),
        len(result.get("executed_fixes") or []),
        result.get("regression_run_id"),
        result.get("regression_verdict"),
        result.get("score_delta"),
        result.get("elapsed_ms"),
    )
    return result
