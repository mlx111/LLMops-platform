"""L3+L4 Self-Healing Agent：发现失败 → 提议修复 → 高风险卡 HITL / 低风险直接执行 →
触发回归评测 → 对比回归 run vs 原 run 判断修复是否有效（L4 半闭环）。

L2 → L3 演进：从「跨 run 聚合 ReAct 找系统性失败模式」升级为「自主提议修复动作 +
高风险走 HITL 人工审批 + 低风险直接执行 + 触发回归评测」。
L3 → L4 半闭环：在 trigger_regression_run 后调 compare_regression 对比回归 run vs 原 run，
输出 regression_verdict (improved/declined/unchanged/pending) + score_delta，
形成「发现 → 修复 → 回归 → 对比」半闭环（仍需人工 approve HITL 才能真正落地修复，
故为半闭环；闭环留待 L4 全自治）。

自研 ReAct loop（Thought / Action / Action Input / Observation），复用 llm_judge._chat_completion，
零新依赖。失败兜底返回 `judge="agentic_self_heal_fallback"`，绝不抛异常。

工具集（闭包绑定 db Session + target_base_url，target_base_url 从 EvalRun.config_json
的 target_url 截取 scheme://host[:port] 推导）：
  - list_run_failures: 拿某 run 失败 case + 9 类分类（数据探查）
  - list_pending_fixes: HTTP GET mypaperweb /api/hitl/pending 看待审批修复
  - propose_fix: HTTP POST mypaperweb /api/hitl/propose 提议高风险修复（只提议不执行）
  - check_fix_status: HTTP GET mypaperweb /api/hitl/{id}/result 查修复状态
  - trigger_regression_run: 低风险，llmops 内 db.add(EvalRun) + run_evaluation 触发回归评测
  - compare_regression: L4 半闭环，对比回归 run vs 原 run（复用 runs.py compare 口径，直接查 DB，
    不走 HTTP），回归 run 未跑完时返回 still_running 让 agent 在 final_answer 里说明待对比

关键约束：高风险动作（改 prompt / 重建索引 / 清缓存 / 改配置）必须走 HITL 卡人工审批，
L3 agent 不直接执行；低风险动作（触发回归评测 + 对比回归）可直接执行。
"""

from __future__ import annotations

import datetime
import json
import time
from typing import Any
from urllib.parse import urlparse

import requests
from sqlalchemy.orm import Session

from app.models.dataset import EvalCase
from app.models.run import EvalResult, EvalRun
from app.services.classifier import classify
from app.services.llm_judge import (
    _chat_completion,
    _clamp01,
    _parse_json,
    _report_judge_to_langfuse,
)
from app.services.logger import logger


# ── ReAct 提示词（JSON-based ReAct 协议，同 L1/L2） ──
AGENT_SYS_PROMPT = """你是 Agentic Self-Healing 系统（L4 半闭环）。目标：分析失败 case，提议修复动作，触发回归评测，并对比回归 run 判断修复是否有效。

每轮严格输出一个 JSON 对象，两种格式之一：

格式1（调工具）：
{"thought": "思考", "action": "工具名", "action_input": {...}}

格式2（最终答案）：
{"thought": "最终思考", "final_answer": {"proposed_fixes": [{"tool_name": "...", "question": "中文描述修复内容", "risk_level": "high"}], "executed_fixes": [{"action": "trigger_regression_run", "run_id": N}, {"action": "compare_regression", "regression_run_id": N}], "regression_run_id": N或null, "regression_verdict": "improved/declined/unchanged/pending", "score_delta": 0.0, "healing_summary": "中文总结：发现 X 个失败，提议 Y 个高风险修复待审批，触发回归评测 run_id=N，对比 verdict=...，score_delta=..."}}

可用工具：
- list_run_failures（拿某 run 失败 case + 9 类分类，action_input={}）
- list_pending_fixes（看当前 HITL 待审批的修复提议，action_input={}）
- propose_fix（提议高风险修复写入 HITL pending，action_input={"tool_name": "update_prompt/rebuild_index/clear_cache/update_config", "question": "中文说明", "args": {...}}）
- check_fix_status（查某修复状态，action_input={"approval_id": "xxx"}）
- trigger_regression_run（低风险：触发回归评测，action_input={}）
- compare_regression（L4 半闭环：对比回归 run vs 原 run 判断修复是否有效，action_input={"regression_run_id": N}，不传则用最近一次 trigger_regression_run 产生的 run_id；若回归 run 还在跑返回 still_running）

风险等级：
- 高风险（必须 propose_fix 卡 HITL）：改 prompt 模板、重建 Milvus collection、清全量缓存、改 LLM/Embedding 配置
- 低风险（直接 trigger_regression_run + compare_regression）：重跑评测验证修复 + 对比结果

规则：
1. 第一轮必须先调 list_run_failures 查失败数据
2. 看完数据，对可修复的失败类别提议 propose_fix（高风险），然后调 trigger_regression_run
3. 触发回归评测后，必须调 compare_regression 对比回归 run vs 原 run：若返回 still_running，在 final_answer 里把 regression_verdict 设为 "pending" 并说明待对比；若返回 verdict，把 verdict 和 score_delta 填进 final_answer
4. 如果没有失败 case，final_answer 说明 "no failures to heal"，regression_run_id=null，regression_verdict="unchanged"
5. 最多 6 轮工具调用
6. 高风险修复只能 propose_fix，不能直接执行"""

MAX_ITER = 6


# ── 辅助：从 target_url 推导 mypaperweb base_url ──
def _derive_base_url(target_url: str | None) -> str:
    """从 target_url 截取 scheme://host[:port] 作为 mypaperweb base_url。

    L3 agent 在 llmops 侧，通过 HTTP 调 mypaperweb 的 hitl router。
    target_url 是评测时的 SUT 端点（如 http://mypaperweb:8000/api/agent/rag），
    截取 scheme://netloc 即得 mypaperweb base_url。
    """
    if not target_url:
        return ""
    p = urlparse(target_url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


# ── 辅助：HTTP 调用 mypaperweb hitl 端点（统一超时 + 异常兜底） ──
def _http_get(url: str, timeout: float = 5.0) -> dict:
    """HTTP GET，失败返回 {"error": "..."}，绝不抛异常。"""
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except Exception as exc:  # noqa: BLE001 - HTTP 调用失败不能中断 agent
        return {"error": f"request failed: {str(exc)[:200]}"}


def _http_post(url: str, body: dict, timeout: float = 5.0) -> dict:
    """HTTP POST，失败返回 {"error": "..."}，绝不抛异常。"""
    try:
        resp = requests.post(url, json=body, timeout=timeout)
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"request failed: {str(exc)[:200]}"}


# ── 工具集构造（闭包绑定 db Session + target_base_url + original_run + 共享状态） ──
def _build_tools(
    db: Session,
    run: EvalRun,
    base_url: str,
    state: dict,
) -> dict:
    """构造工具集，闭包绑定 db Session / 原 EvalRun / mypaperweb base_url /
    共享状态 state（state["regression_run_id"] 由主循环在 trigger_regression_run
    后写入，compare_regression 读取，实现跨工具的 run_id 传递）。
    """

    def list_run_failures(_inp: dict) -> str:
        """拿某 run 失败 case + 9 类分类。"""
        results = (
            db.query(EvalResult)
            .filter(
                EvalResult.run_id == run.id,
                EvalResult.status.in_(["failed", "error"]),
            )
            .limit(30)
            .all()
        )
        if not results:
            return json.dumps(
                {"failed_cases": [], "hint": f"no failed cases in run {run.id}"},
                ensure_ascii=False,
            )
        case_ids = [r.case_id for r in results]
        cases = (
            {c.id: c for c in db.query(EvalCase).filter(EvalCase.id.in_(case_ids)).all()}
            if case_ids
            else {}
        )
        items: list[dict] = []
        for r in results:
            case = cases.get(r.case_id)
            try:
                cats = classify(
                    result_scores=r.scores or {},
                    case=case,
                    latency_ms=r.latency_ms or 0,
                    input_tokens=r.input_tokens or 0,
                    output_tokens=r.output_tokens or 0,
                    actual_output=r.actual_output or "",
                    actual_tool=r.actual_tool or "",
                    actual_args=r.actual_args,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "self-heal classify failed for case %s: %s",
                    r.case_id, str(exc)[:120],
                )
                cats = ["quality_issue"]
            items.append(
                {
                    "case_id": r.case_id,
                    "status": r.status,
                    "user_input": (case.input[:150] if case else ""),
                    "failure_reason": (r.failure_reason or "")[:150],
                    "failure_categories": cats,
                    "scores": r.scores,
                }
            )
        return json.dumps({"failed_cases": items}, ensure_ascii=False)

    def list_pending_fixes(_inp: dict) -> str:
        """HTTP GET mypaperweb /api/hitl/pending。"""
        if not base_url:
            return json.dumps(
                {"error": "no base_url derived from target_url"},
                ensure_ascii=False,
            )
        data = _http_get(f"{base_url}/api/hitl/pending")
        return json.dumps(data, ensure_ascii=False)

    def propose_fix(inp: dict) -> str:
        """HTTP POST mypaperweb /api/hitl/propose 提议高风险修复。"""
        if not base_url:
            return json.dumps(
                {"error": "no base_url derived from target_url"},
                ensure_ascii=False,
            )
        tool_name = str(inp.get("tool_name", "external_fix_proposal"))
        question = str(inp.get("question", ""))[:500]
        args = inp.get("args") or {}
        if not isinstance(args, dict):
            args = {}
        body = {
            "tool_name": tool_name,
            "args": args,
            "session_id": f"self-heal-run-{run.id}",
            "question": question,
        }
        data = _http_post(f"{base_url}/api/hitl/propose", body)
        return json.dumps(data, ensure_ascii=False)

    def check_fix_status(inp: dict) -> str:
        """HTTP GET mypaperweb /api/hitl/{id}/result。"""
        if not base_url:
            return json.dumps(
                {"error": "no base_url derived from target_url"},
                ensure_ascii=False,
            )
        approval_id = str(inp.get("approval_id", "")).strip()
        if not approval_id:
            return json.dumps({"error": "approval_id required"}, ensure_ascii=False)
        data = _http_get(f"{base_url}/api/hitl/{approval_id}/result")
        return json.dumps(data, ensure_ascii=False)

    def trigger_regression_run(_inp: dict) -> str:
        """低风险：基于原 run 新建回归评测 run + 触发 Celery 执行。"""
        from app.tasks.celery_app import run_evaluation

        new_run = EvalRun(
            name=f"regression-{run.name}-{datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
            dataset_id=run.dataset_id,
            prompt_version_id=run.prompt_version_id,
            model_version_id=run.model_version_id,
            retriever_version_id=run.retriever_version_id,
            agent_version_id=run.agent_version_id,
            total_cases=run.total_cases,
            config_json=run.config_json or {},
        )
        db.add(new_run)
        db.commit()
        db.refresh(new_run)
        try:
            run_evaluation(new_run.id)
        except Exception as exc:  # noqa: BLE001 - Celery 派发失败不能中断 agent
            logger.error("self-heal trigger_regression_run failed: %s", str(exc)[:200])
            return json.dumps(
                {
                    "run_id": new_run.id,
                    "status": "created_but_not_triggered",
                    "error": str(exc)[:200],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"run_id": new_run.id, "status": "triggered"},
            ensure_ascii=False,
        )

    def compare_regression(inp: dict) -> str:
        """L4 半闭环：对比回归 run vs 原 run，判断修复是否有效。

        复用 runs.py compare_runs 的口径（聚合分数 + improved/regressed cases），
        但直接查 DB 不走 HTTP，避免网络开销和 schema 耦合。
        回归 run 未跑完时返回 still_running，让 agent 在 final_answer 里把
        regression_verdict 设为 "pending" 并说明待对比。
        """
        # action_input 可不传 run_id，fallback 到本次 self-heal 已触发的 regression_run_id
        reg_run_id = (
            inp.get("regression_run_id")
            or inp.get("run_id")
            or state.get("regression_run_id")
        )
        if not reg_run_id:
            return json.dumps(
                {"error": "regression_run_id required (call trigger_regression_run first)"},
                ensure_ascii=False,
            )
        try:
            reg_run_id_int = int(reg_run_id)
        except (TypeError, ValueError):
            return json.dumps(
                {"error": f"invalid regression_run_id: {reg_run_id}"},
                ensure_ascii=False,
            )
        reg_run = db.get(EvalRun, reg_run_id_int)
        if not reg_run:
            return json.dumps(
                {"error": f"regression run {reg_run_id_int} not found"},
                ensure_ascii=False,
            )

        # 回归 run 未完成：返回 still_running（不阻塞 agent，agent 会在 final_answer 里标 pending）
        if reg_run.status not in ("completed", "failed", "error"):
            completed = (
                db.query(EvalResult)
                .filter(EvalResult.run_id == reg_run.id)
                .count()
            )
            return json.dumps(
                {
                    "status": "still_running",
                    "regression_run_id": reg_run.id,
                    "completed_cases": completed,
                    "total_cases": reg_run.total_cases,
                    "hint": f"regression run {reg_run.id} still running "
                    f"({completed}/{reg_run.total_cases}), retry later",
                },
                ensure_ascii=False,
            )

        # 聚合分数（同 runs.py aggregate_metrics 口径）
        def aggregate_metrics(run_id_arg: int) -> dict[str, float]:
            totals: dict[str, list[float]] = {}
            for r in (
                db.query(EvalResult)
                .filter(EvalResult.run_id == run_id_arg)
                .all()
            ):
                if not r.scores:
                    continue
                for name, score_detail in r.scores.items():
                    if isinstance(score_detail, dict) and "score" in score_detail:
                        totals.setdefault(name, []).append(score_detail["score"])
            return {
                key: round(sum(values) / len(values), 4)
                for key, values in totals.items()
            }

        metrics1 = aggregate_metrics(run.id)
        metrics2 = aggregate_metrics(reg_run.id)

        results1 = {
            r.case_id: r
            for r in db.query(EvalResult).filter(EvalResult.run_id == run.id).all()
        }
        results2 = {
            r.case_id: r
            for r in db.query(EvalResult).filter(EvalResult.run_id == reg_run.id).all()
        }

        def case_avg_score(result: EvalResult) -> float:
            if not result.scores:
                return 0
            values = [
                s["score"]
                for s in result.scores.values()
                if isinstance(s, dict) and "score" in s
            ]
            return sum(values) / len(values) if values else 0

        common = set(results1.keys()) & set(results2.keys())
        improved = sum(
            1 for cid in common
            if case_avg_score(results2[cid]) > case_avg_score(results1[cid])
        )
        regressed = sum(
            1 for cid in common
            if case_avg_score(results2[cid]) < case_avg_score(results1[cid])
        )

        score_delta = round(
            (reg_run.avg_score or 0) - (run.avg_score or 0), 4
        )
        latency_delta = round(
            (reg_run.avg_latency_ms or 0) - (run.avg_latency_ms or 0), 1
        )

        if score_delta > 0:
            verdict = "improved"
        elif score_delta < 0:
            verdict = "declined"
        else:
            verdict = "unchanged"

        return json.dumps(
            {
                "regression_run_id": reg_run.id,
                "regression_status": reg_run.status,
                "original_avg_score": run.avg_score or 0,
                "regression_avg_score": reg_run.avg_score or 0,
                "score_delta": score_delta,
                "latency_delta_ms": latency_delta,
                "common_cases": len(common),
                "improved_cases": improved,
                "regressed_cases": regressed,
                "regression_verdict": verdict,
                "summary": (
                    f"regression run {reg_run.id} vs original run {run.id}: "
                    f"avg_score {verdict} by {abs(score_delta):.4f} "
                    f"({improved} improved, {regressed} regressed, "
                    f"{len(common)} common cases)"
                ),
            },
            ensure_ascii=False,
        )

    return {
        "list_run_failures": list_run_failures,
        "list_pending_fixes": list_pending_fixes,
        "propose_fix": propose_fix,
        "check_fix_status": check_fix_status,
        "trigger_regression_run": trigger_regression_run,
        "compare_regression": compare_regression,
    }


# ── ReAct 解析（JSON-based，同 L1/L2） ──
def _parse_response(response: str) -> dict | None:
    """解析 LLM 的 JSON ReAct 响应。

    返回三种情况：
      {"type": "action", "thought": ..., "tool": ..., "input": {...}}
      {"type": "final", "thought": ..., "final_answer": {...}}
      None（解析失败）
    """
    parsed = _parse_json(response)
    if not parsed or not isinstance(parsed, dict):
        return None
    thought = str(parsed.get("thought", ""))[:500]
    if "final_answer" in parsed:
        return {"type": "final", "thought": thought, "final_answer": parsed["final_answer"]}
    if "action" in parsed:
        tool_name = str(parsed["action"]).strip()
        tool_input = parsed.get("action_input") or parsed.get("input") or {}
        if not isinstance(tool_input, dict):
            tool_input = {}
        return {"type": "action", "thought": thought, "tool": tool_name, "input": tool_input}
    return None


def _empty_final_answer(reason: str, iterations: int) -> dict:
    """兜底 final_answer（LLM 超时或不可用时）。"""
    return {
        "proposed_fixes": [],
        "executed_fixes": [],
        "regression_run_id": None,
        "regression_verdict": "pending",
        "score_delta": None,
        "healing_summary": f"agent unavailable: {reason}",
        "judge": "agentic_self_heal_fallback",
        "iterations": iterations,
    }


# ── 主入口 ──
def agentic_self_heal(db: Session, run_id: int) -> dict:
    """L3+L4 Self-Healing Agent 主入口（L4 半闭环）。

    针对 run_id 的失败 case：提议修复（高风险走 HITL）+ 触发回归评测（低风险直接执行）
    + 对比回归 run vs 原 run 判断修复是否有效（L4 半闭环）。
    返回 {
        proposed_fixes, executed_fixes, regression_run_id,
        regression_verdict: "improved"/"declined"/"unchanged"/"pending",
        score_delta: float | None,
        healing_summary, trace,
        judge: "agentic_self_heal", iterations, elapsed_ms
    }。
    失败兜底返回 judge="agentic_self_heal_fallback" 或 "agentic_self_heal_timeout"。
    """
    started = time.time()
    run = db.get(EvalRun, run_id)
    if not run:
        return {
            **_empty_final_answer(f"run {run_id} not found", 0),
            "elapsed_ms": int((time.time() - started) * 1000),
        }

    # 从 config_json 推导 mypaperweb base_url
    config = run.config_json or {}
    target_url = config.get("target_url") if isinstance(config, dict) else None
    base_url = _derive_base_url(target_url)
    if base_url:
        logger.info("self-heal: run=%s base_url=%s", run_id, base_url)
    else:
        logger.warning("self-heal: run=%s cannot derive base_url from target_url=%s", run_id, target_url)

    # 共享状态：trigger_regression_run 写入 regression_run_id，compare_regression 读取
    state: dict = {"regression_run_id": None}
    tools = _build_tools(db, run, base_url, state)
    messages = [
        {"role": "system", "content": AGENT_SYS_PROMPT},
        {
            "role": "user",
            "content": (
                f"目标 run_id={run.id}，name={run.name}，"
                f"total={run.total_cases}，failed={run.failed_cases}，passed={run.passed_cases}。\n"
                f"target_base_url={base_url or '(未推导，HITL 工具不可用)'}。\n"
                "请先用 list_run_failures 查失败，再提议修复 + 触发回归评测 + "
                "调 compare_regression 对比回归结果。"
            ),
        },
    ]

    trace_log: list[dict] = []
    proposed_fixes: list[dict] = []
    executed_fixes: list[dict] = []
    regression_run_id: int | None = None
    regression_verdict: str = "pending"
    score_delta: float | None = None

    for iteration in range(MAX_ITER):
        response, usage = _chat_completion(messages, temperature=0.0)
        if not response:
            logger.warning("self-heal LLM call failed at iteration %d", iteration + 1)
            return {
                **_empty_final_answer("LLM unavailable", iteration + 1),
                "trace": trace_log,
                "elapsed_ms": int((time.time() - started) * 1000),
                "judge": "agentic_self_heal_fallback",
            }

        trace_log.append({"iteration": iteration + 1, "thought_raw": response[:500]})

        parsed_resp = _parse_response(response)
        if parsed_resp is None:
            messages.append({"role": "assistant", "content": response})
            messages.append(
                {
                    "role": "user",
                    "content": '请严格输出 JSON：{"thought":..., "action":..., "action_input":{...}} 或 {"thought":..., "final_answer":{...}}',
                }
            )
            continue

        # 1. final_answer 分支
        if parsed_resp["type"] == "final":
            final = parsed_resp["final_answer"] or {}
            if not isinstance(final, dict):
                final = {}
            trace_log[-1]["thought"] = parsed_resp.get("thought", "")
            trace_log[-1]["final_answer"] = final
            # 合并 agent 运行中累积的修复记录（优先 final_answer 里的）
            fa_fixes = final.get("proposed_fixes") or proposed_fixes
            fa_executed = final.get("executed_fixes") or executed_fixes
            fa_regression = final.get("regression_run_id")
            if fa_regression is None:
                fa_regression = regression_run_id
            # verdict/score_delta：优先 final_answer 里的（agent 可能基于 observation 自己填），
            # 其次用工具实际观测到的；都没有则保持 pending/None
            fa_verdict = final.get("regression_verdict") or regression_verdict
            fa_score_delta = final.get("score_delta")
            if fa_score_delta in (None, "", 0, "0", "0.0"):
                # fall back to observed value if agent didn't fill a meaningful one
                fa_score_delta = score_delta
            result = {
                "proposed_fixes": fa_fixes if isinstance(fa_fixes, list) else [],
                "executed_fixes": fa_executed if isinstance(fa_executed, list) else [],
                "regression_run_id": fa_regression,
                "regression_verdict": fa_verdict,
                "score_delta": fa_score_delta,
                "healing_summary": str(final.get("healing_summary", ""))[:800],
                "trace": trace_log,
                "judge": "agentic_self_heal",
                "iterations": iteration + 1,
                "elapsed_ms": int((time.time() - started) * 1000),
            }
            _report_judge_to_langfuse(
                "self_heal",
                messages[-1]["content"][:1000],
                json.dumps(final, ensure_ascii=False)[:1000],
                usage,
            )
            logger.info(
                "self-heal done: run=%s proposed=%d executed=%d regression=%s verdict=%s delta=%s "
                "iterations=%d elapsed=%dms",
                run_id, len(result["proposed_fixes"]), len(result["executed_fixes"]),
                result["regression_run_id"], result["regression_verdict"], result["score_delta"],
                result["iterations"], result["elapsed_ms"],
            )
            return result

        # 2. action 分支
        tool_name = parsed_resp["tool"]
        tool_fn = tools.get(tool_name)
        messages.append({"role": "assistant", "content": response})
        if tool_fn is None:
            observation = json.dumps(
                {"error": f"unknown tool: {tool_name}. available: {list(tools.keys())}"},
                ensure_ascii=False,
            )
        else:
            try:
                observation = tool_fn(parsed_resp["input"])
                # 记录副作用：propose_fix / trigger_regression_run / compare_regression
                if tool_name == "propose_fix":
                    obs_data = _parse_json(observation) or {}
                    if isinstance(obs_data, dict) and obs_data.get("approval_id"):
                        proposed_fixes.append(
                            {
                                "approval_id": obs_data["approval_id"],
                                "tool_name": obs_data.get("tool_name", ""),
                                "question": parsed_resp["input"].get("question", ""),
                                "risk_level": "high",
                                "status": "pending",
                            }
                        )
                elif tool_name == "trigger_regression_run":
                    obs_data = _parse_json(observation) or {}
                    if isinstance(obs_data, dict) and obs_data.get("run_id"):
                        regression_run_id = obs_data["run_id"]
                        # 同步更新共享状态，供 compare_regression fallback 使用
                        state["regression_run_id"] = regression_run_id
                        executed_fixes.append(
                            {
                                "action": "trigger_regression_run",
                                "run_id": regression_run_id,
                                "status": obs_data.get("status", ""),
                            }
                        )
                elif tool_name == "compare_regression":
                    obs_data = _parse_json(observation) or {}
                    if isinstance(obs_data, dict):
                        if obs_data.get("regression_verdict"):
                            regression_verdict = obs_data["regression_verdict"]
                            score_delta = obs_data.get("score_delta")
                            executed_fixes.append(
                                {
                                    "action": "compare_regression",
                                    "regression_run_id": obs_data.get("regression_run_id"),
                                    "verdict": regression_verdict,
                                    "score_delta": score_delta,
                                }
                            )
                        elif obs_data.get("status") == "still_running":
                            # 回归 run 未跑完，标记 pending，agent 可在 final_answer 里说明
                            regression_verdict = "pending"
                            executed_fixes.append(
                                {
                                    "action": "compare_regression",
                                    "regression_run_id": obs_data.get("regression_run_id"),
                                    "status": "still_running",
                                    "completed_cases": obs_data.get("completed_cases"),
                                    "total_cases": obs_data.get("total_cases"),
                                }
                            )
            except Exception as exc:  # noqa: BLE001 - 工具调用失败不能中断 agent
                observation = json.dumps(
                    {"error": f"tool execution failed: {str(exc)[:200]}"},
                    ensure_ascii=False,
                )
        trace_log[-1]["action"] = tool_name
        trace_log[-1]["input"] = parsed_resp["input"]
        trace_log[-1]["observation"] = observation[:500]
        messages.append({"role": "user", "content": f"Observation: {observation}"})

    # 超过 MAX_ITER，超时兜底
    logger.warning("self-heal exceeded MAX_ITER=%d for run=%s", MAX_ITER, run_id)
    return {
        "proposed_fixes": proposed_fixes,
        "executed_fixes": executed_fixes,
        "regression_run_id": regression_run_id,
        "regression_verdict": regression_verdict,
        "score_delta": score_delta,
        "healing_summary": f"agent timeout after {MAX_ITER} iterations; "
                           f"proposed {len(proposed_fixes)} fixes, "
                           f"executed {len(executed_fixes)} fixes, "
                           f"verdict={regression_verdict}",
        "trace": trace_log,
        "judge": "agentic_self_heal_timeout",
        "iterations": MAX_ITER,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
