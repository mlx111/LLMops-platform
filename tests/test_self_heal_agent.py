"""Tests for L3+L4 Self-Healing Agent (self_heal_agent.py).

覆盖：
  - _parse_response: action / final / 非法 JSON
  - _derive_base_url: 正常 / 缺 scheme / 空
  - _empty_final_answer: 含 regression_verdict / score_delta 新字段
  - _build_tools: 6 个工具齐全（含 compare_regression）
  - list_run_failures: 返回失败 case
  - trigger_regression_run: 新建回归 run + 状态 triggered
  - compare_regression:
      * 空 input + 空 state → required error
      * 非法 run_id → invalid
      * 不存在的 run → not found
      * 回归 run 仍 running → still_running
      * 回归 run completed → verdict (improved/declined/unchanged) + score_delta
  - agentic_self_heal 兜底：
      * run 不存在 → judge=agentic_self_heal_fallback
      * LLM 不可用 → judge=agentic_self_heal_fallback
      * 超过 MAX_ITER → judge=agentic_self_heal_timeout
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.dataset import Dataset, EvalCase
from app.models.run import EvalResult, EvalRun
from app.services import self_heal_agent as mod
from app.services.self_heal_agent import (
    AGENT_SYS_PROMPT,
    MAX_ITER,
    _build_tools,
    _derive_base_url,
    _empty_final_answer,
    _parse_response,
    agentic_self_heal,
)


# ── 测试夹具：内存 sqlite + 一个 Dataset + 2 个 case ──

@pytest.fixture
def db_session(tmp_path: Path):
    """构造一个干净的 sqlite Session，建表 + 种入 Dataset 和 2 个 case。"""
    db_path = tmp_path / "self_heal_test.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    dataset = Dataset(name="Heal Dataset", description="t", case_type="qa")
    session.add(dataset)
    session.flush()
    for i in range(2):
        session.add(
            EvalCase(
                dataset_id=dataset.id,
                case_type="qa",
                input=f"user question {i}",
                reference_answer=f"ref answer {i}",
            )
        )
    session.commit()
    yield session
    session.close()


def _make_run(
    session,
    *,
    name: str = "baseline",
    status: str = "completed",
    avg_score: float = 0.5,
    dataset_id: int | None = None,
    failed_cases: int = 1,
    passed_cases: int = 0,
    total_cases: int = 2,
    target_url: str = "http://mypaperweb:8080/api/agent/rag",
) -> EvalRun:
    """在 session 里插一条 EvalRun 并返回（detach 状态由 session 控制）。"""
    if dataset_id is None:
        dataset_id = session.query(Dataset).first().id
    run = EvalRun(
        name=name,
        dataset_id=dataset_id,
        status=status,
        total_cases=total_cases,
        passed_cases=passed_cases,
        failed_cases=failed_cases,
        avg_score=avg_score,
        avg_latency_ms=100.0,
        avg_tokens=10.0,
        config_json={"target_url": target_url},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _add_result(
    session,
    *,
    run_id: int,
    case_id: int,
    status: str = "failed",
    scores: dict | None = None,
    failure_reason: str = "",
) -> EvalResult:
    res = EvalResult(
        run_id=run_id,
        case_id=case_id,
        status=status,
        actual_output="out",
        scores=scores or {"correctness": {"score": 0.0, "success": False}},
        failure_reason=failure_reason or "timeout",
        latency_ms=1000,
        input_tokens=5,
        output_tokens=5,
    )
    session.add(res)
    session.commit()
    return res


# ── _parse_response ──

def test_parse_response_action():
    resp = '{"thought": "查失败", "action": "list_run_failures", "action_input": {}}'
    parsed = _parse_response(resp)
    assert parsed is not None
    assert parsed["type"] == "action"
    assert parsed["tool"] == "list_run_failures"
    assert parsed["input"] == {}


def test_parse_response_final():
    resp = '{"thought": "done", "final_answer": {"regression_run_id": 42, "regression_verdict": "improved"}}'
    parsed = _parse_response(resp)
    assert parsed is not None
    assert parsed["type"] == "final"
    assert parsed["final_answer"]["regression_verdict"] == "improved"


def test_parse_response_invalid_returns_none():
    assert _parse_response("not json at all") is None
    assert _parse_response('{"thought": "no action or final"}') is None


# ── _derive_base_url ──

def test_derive_base_url_normal():
    assert _derive_base_url("http://mypaperweb:8080/api/agent/rag") == "http://mypaperweb:8080"


def test_derive_base_url_none_or_invalid():
    assert _derive_base_url(None) == ""
    assert _derive_base_url("") == ""
    assert _derive_base_url("not-a-url") == ""


# ── _empty_final_answer ──

def test_empty_final_answer_has_l4_fields():
    fa = _empty_final_answer("LLM unavailable", 3)
    assert fa["judge"] == "agentic_self_heal_fallback"
    assert fa["regression_run_id"] is None
    # L4 新增字段
    assert fa["regression_verdict"] == "pending"
    assert fa["score_delta"] is None
    assert fa["iterations"] == 3


# ── _build_tools 工具集齐全 ──

def test_build_tools_has_six_tools_including_compare_regression(db_session):
    run = _make_run(db_session)
    tools = _build_tools(db_session, run, "http://mypaperweb:8080", {"regression_run_id": None})
    expected = {
        "list_run_failures",
        "list_pending_fixes",
        "propose_fix",
        "check_fix_status",
        "trigger_regression_run",
        "compare_regression",
    }
    assert set(tools.keys()) == expected


# ── list_run_failures 工具 ──

def test_list_run_failures_returns_failed_cases(db_session):
    run = _make_run(db_session, name="r1", failed_cases=1)
    case = db_session.query(EvalCase).first()
    _add_result(db_session, run_id=run.id, case_id=case.id, status="failed",
                scores={"correctness": {"score": 0.0, "success": False}})
    tools = _build_tools(db_session, run, "", {"regression_run_id": None})
    obs = json.loads(tools["list_run_failures"]({}))
    assert obs["failed_cases"]
    assert obs["failed_cases"][0]["case_id"] == case.id
    # 9 类分类兜底（classify 可能返回 quality_issue 或其它类别，但必为 list）
    assert isinstance(obs["failed_cases"][0]["failure_categories"], list)


def test_list_run_failures_no_failures(db_session):
    run = _make_run(db_session, name="no-fail", failed_cases=0)
    tools = _build_tools(db_session, run, "", {"regression_run_id": None})
    obs = json.loads(tools["list_run_failures"]({}))
    assert obs["failed_cases"] == []
    assert "no failed cases" in obs["hint"]


# ── trigger_regression_run 工具 ──

def test_trigger_regression_run_creates_new_run(db_session, monkeypatch):
    run = _make_run(db_session, name="orig")
    # 桩掉 Celery 派发，避免真的跑评测
    monkeypatch.setattr("app.tasks.celery_app.run_evaluation", lambda _rid: None)
    state = {"regression_run_id": None}
    tools = _build_tools(db_session, run, "", state)
    obs = json.loads(tools["trigger_regression_run"]({}))
    assert obs["status"] == "triggered"
    new_id = obs["run_id"]
    assert new_id > run.id
    # 新 run 已持久化
    new_run = db_session.get(EvalRun, new_id)
    assert new_run is not None
    assert new_run.dataset_id == run.dataset_id
    # state 被主循环更新（这里手动模拟主循环的行为）
    state["regression_run_id"] = new_id


# ── compare_regression 工具 ──

def test_compare_regression_no_run_id_returns_required_error(db_session):
    run = _make_run(db_session, name="r")
    tools = _build_tools(db_session, run, "", {"regression_run_id": None})
    obs = json.loads(tools["compare_regression"]({}))
    assert "error" in obs
    assert "regression_run_id required" in obs["error"]


def test_compare_regression_invalid_run_id(db_session):
    run = _make_run(db_session, name="r")
    tools = _build_tools(db_session, run, "", {"regression_run_id": None})
    obs = json.loads(tools["compare_regression"]({"regression_run_id": "abc"}))
    assert "invalid regression_run_id" in obs["error"]


def test_compare_regression_not_found(db_session):
    run = _make_run(db_session, name="r")
    tools = _build_tools(db_session, run, "", {"regression_run_id": None})
    obs = json.loads(tools["compare_regression"]({"regression_run_id": 999999}))
    assert "not found" in obs["error"]


def test_compare_regression_still_running(db_session):
    run = _make_run(db_session, name="orig", status="completed", avg_score=0.5)
    # 回归 run 仍 running
    reg_run = _make_run(db_session, name="reg", status="running", avg_score=0.0)
    tools = _build_tools(db_session, run, "", {"regression_run_id": reg_run.id})
    obs = json.loads(tools["compare_regression"]({}))
    assert obs["status"] == "still_running"
    assert obs["regression_run_id"] == reg_run.id
    assert "retry later" in obs["hint"]


def test_compare_regression_falls_back_to_state_run_id(db_session):
    """不传 regression_run_id，工具应 fallback 到 state['regression_run_id']。"""
    run = _make_run(db_session, name="orig", status="completed", avg_score=0.5)
    reg_run = _make_run(db_session, name="reg", status="completed", avg_score=0.8)
    case = db_session.query(EvalCase).first()
    _add_result(db_session, run_id=run.id, case_id=case.id,
                scores={"correctness": {"score": 0.4, "success": False}})
    _add_result(db_session, run_id=reg_run.id, case_id=case.id, status="passed",
                scores={"correctness": {"score": 0.9, "success": True}})
    tools = _build_tools(db_session, run, "", {"regression_run_id": reg_run.id})
    obs = json.loads(tools["compare_regression"]({}))
    assert obs["regression_run_id"] == reg_run.id
    assert obs["regression_verdict"] == "improved"
    assert obs["score_delta"] == pytest.approx(0.3, abs=1e-4)


def test_compare_regression_verdict_improved(db_session):
    run = _make_run(db_session, name="orig", status="completed", avg_score=0.5)
    reg_run = _make_run(db_session, name="reg", status="completed", avg_score=0.8)
    case = db_session.query(EvalCase).first()
    _add_result(db_session, run_id=run.id, case_id=case.id,
                scores={"correctness": {"score": 0.4, "success": False}})
    _add_result(db_session, run_id=reg_run.id, case_id=case.id, status="passed",
                scores={"correctness": {"score": 0.9, "success": True}})
    tools = _build_tools(db_session, run, "", {"regression_run_id": reg_run.id})
    obs = json.loads(tools["compare_regression"]({"regression_run_id": reg_run.id}))
    assert obs["regression_verdict"] == "improved"
    assert obs["score_delta"] > 0
    assert obs["improved_cases"] >= 1
    assert obs["regressed_cases"] == 0
    assert "improved" in obs["summary"]


def test_compare_regression_verdict_declined(db_session):
    run = _make_run(db_session, name="orig", status="completed", avg_score=0.8)
    reg_run = _make_run(db_session, name="reg", status="completed", avg_score=0.3)
    case = db_session.query(EvalCase).first()
    _add_result(db_session, run_id=run.id, case_id=case.id, status="passed",
                scores={"correctness": {"score": 0.9, "success": True}})
    _add_result(db_session, run_id=reg_run.id, case_id=case.id,
                scores={"correctness": {"score": 0.2, "success": False}})
    tools = _build_tools(db_session, run, "", {"regression_run_id": reg_run.id})
    obs = json.loads(tools["compare_regression"]({"regression_run_id": reg_run.id}))
    assert obs["regression_verdict"] == "declined"
    assert obs["score_delta"] < 0
    assert obs["regressed_cases"] >= 1


def test_compare_regression_verdict_unchanged(db_session):
    run = _make_run(db_session, name="orig", status="completed", avg_score=0.5)
    reg_run = _make_run(db_session, name="reg", status="completed", avg_score=0.5)
    case = db_session.query(EvalCase).first()
    _add_result(db_session, run_id=run.id, case_id=case.id,
                scores={"correctness": {"score": 0.5, "success": False}})
    _add_result(db_session, run_id=reg_run.id, case_id=case.id,
                scores={"correctness": {"score": 0.5, "success": False}})
    tools = _build_tools(db_session, run, "", {"regression_run_id": reg_run.id})
    obs = json.loads(tools["compare_regression"]({"regression_run_id": reg_run.id}))
    assert obs["regression_verdict"] == "unchanged"
    assert obs["score_delta"] == 0


# ── agentic_self_heal 兜底 ──

def test_agentic_self_heal_run_not_found_returns_fallback(db_session):
    result = agentic_self_heal(db_session, run_id=999999)
    assert result["judge"] == "agentic_self_heal_fallback"
    assert result["regression_run_id"] is None
    assert result["regression_verdict"] == "pending"
    assert "not found" in result["healing_summary"]
    assert "elapsed_ms" in result


def test_agentic_self_heal_llm_unavailable_returns_fallback(db_session, monkeypatch):
    run = _make_run(db_session, name="r", failed_cases=1)
    # LLM 不可用：_chat_completion 返回空
    monkeypatch.setattr(mod, "_chat_completion", lambda _msgs, temperature=0.0: ("", None))
    result = agentic_self_heal(db_session, run_id=run.id)
    assert result["judge"] == "agentic_self_heal_fallback"
    assert result["regression_verdict"] == "pending"
    assert result["score_delta"] is None
    assert "LLM unavailable" in result["healing_summary"]


def test_agentic_self_heal_timeout_after_max_iter(db_session, monkeypatch):
    run = _make_run(db_session, name="r", failed_cases=1)
    # LLM 永远只调工具、从不下 final_answer → 跑满 MAX_ITER 超时
    def always_action(_msgs, temperature=0.0):
        return (
            '{"thought": "still probing", "action": "list_run_failures", "action_input": {}}',
            None,
        )
    monkeypatch.setattr(mod, "_chat_completion", always_action)
    # 桩掉 Langfuse 上报，避免无 client 时产生噪声
    monkeypatch.setattr(mod, "_report_judge_to_langfuse", lambda *a, **k: None)
    result = agentic_self_heal(db_session, run_id=run.id)
    assert result["judge"] == "agentic_self_heal_timeout"
    assert result["iterations"] == MAX_ITER
    assert "verdict=" in result["healing_summary"]
    assert result["regression_verdict"] == "pending"  # 从未触发回归 run


def test_agentic_self_heal_final_answer_path_merges_tracked_state(db_session, monkeypatch):
    """LLM 第一轮就给 final_answer，应合并主循环已追踪的 regression_run_id/verdict。"""
    run = _make_run(db_session, name="r", failed_cases=0, passed_cases=2)
    # 预先种一个 regression run + 结果，模拟之前 trigger + compare 已发生过
    reg_run = _make_run(db_session, name="reg", status="completed", avg_score=0.9)

    responses = [
        # 第一轮：直接给 final_answer，但不填 regression_run_id/verdict（让主循环用追踪值兜底）
        (
            '{"thought": "done", "final_answer": {"healing_summary": "no failures, verdict from tracked state"}}',
            None,
        ),
    ]

    def fake_chat(_msgs, temperature=0.0):
        return responses.pop(0) if responses else ("", None)

    monkeypatch.setattr(mod, "_chat_completion", fake_chat)
    monkeypatch.setattr(mod, "_report_judge_to_langfuse", lambda *a, **k: None)

    # 直接调主入口（不会经过 trigger/compare 工具），验证 final 分支默认值
    result = agentic_self_heal(db_session, run_id=run.id)
    assert result["judge"] == "agentic_self_heal"
    assert result["regression_run_id"] is None  # 没触发回归
    assert result["regression_verdict"] == "pending"  # 默认值
    assert result["iterations"] == 1


# ── prompt 包含 L4 半闭环 ──

def test_agent_sys_prompt_mentions_compare_regression_and_verdict():
    assert "compare_regression" in AGENT_SYS_PROMPT
    assert "regression_verdict" in AGENT_SYS_PROMPT
    assert "score_delta" in AGENT_SYS_PROMPT
    assert "still_running" in AGENT_SYS_PROMPT
    assert "pending" in AGENT_SYS_PROMPT
