"""Tests for evaluation runner: token counting and demo scoring."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.dataset import Dataset, EvalCase
from app.models.run import EvalResult, EvalRun
from app.services.runner import count_tokens, _run_demo
from app.tasks import celery_app


# ── count_tokens ──


def test_count_tokens_english():
    n = count_tokens("The quick brown fox jumps over the lazy dog")
    assert 8 <= n <= 10  # "the quick..." is exactly 9 tokens


def test_count_tokens_empty():
    assert count_tokens("") == 0


def test_count_tokens_chinese():
    n = count_tokens("你好世界")
    assert n >= 3  # Chinese chars are at least 1 token each


def test_count_tokens_model_specific():
    text = "Hello, world!"
    gpt4 = count_tokens(text, "gpt-4")
    gpt4o = count_tokens(text, "gpt-4o")
    # Short English text should be similar across encodings
    assert gpt4 == gpt4o == 4


def test_count_tokens_unknown_model_defaults_to_cl100k():
    n = count_tokens("hello", "some-unknown-model")
    assert n > 0


# ── _run_demo QA ──


def test_demo_qa_matches_reference():
    result = _run_demo(
        case_input="What is Python?",
        actual_output="Python is a programming language.",
        case_type="qa",
        reference_answer="Python is a programming language.",
        retrieval_context=None,
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    scores = result["scores"]
    assert "AnswerRelevancy" in scores
    assert "Correctness" in scores
    assert scores["AnswerRelevancy"]["score"] > 0.5
    assert scores["Correctness"]["score"] > 0.5


def test_demo_qa_wrong_answer():
    result = _run_demo(
        case_input="What is Python?",
        actual_output="Python is a type of snake.",
        case_type="qa",
        reference_answer="Python is a programming language.",
        retrieval_context=None,
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    scores = result["scores"]
    # Bigram overlap between "python is a type of snake" and "python is a programming language"
    # should be low
    assert scores["Correctness"]["score"] < 0.8


def test_demo_qa_empty_output():
    result = _run_demo(
        case_input="What is Python?",
        actual_output="",
        case_type="qa",
        reference_answer="Python is a programming language.",
        retrieval_context=None,
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    # Empty output should not crash, AnswerRelevancy should be low
    scores = result["scores"]
    assert scores["AnswerRelevancy"]["score"] <= 0.5


# ── _run_demo RAG ──


def test_demo_rag_faithfulness():
    result = _run_demo(
        case_input="What is diabetes?",
        actual_output="Diabetes is a chronic metabolic disorder characterized by high blood sugar.",
        case_type="rag",
        reference_answer="Diabetes is a chronic metabolic disorder.",
        retrieval_context=["Diabetes is a chronic metabolic disorder characterized by high blood sugar levels."],
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    scores = result["scores"]
    assert "Faithfulness" in scores
    assert "AnswerRelevancy" in scores
    assert "ContextRecall" in scores
    assert "ContextPrecision" in scores
    # Output mostly from context → high faithfulness
    assert scores["Faithfulness"]["score"] >= 0.5


def test_demo_rag_no_context():
    result = _run_demo(
        case_input="What is diabetes?",
        actual_output="Diabetes is a condition.",
        case_type="rag",
        reference_answer="Diabetes is a condition.",
        retrieval_context=[],
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    scores = result["scores"]
    assert scores["Faithfulness"]["score"] == 0.7  # "No retrieval context"
    assert scores["ContextRecall"]["score"] == 0.7  # "No context/reference"
    assert scores["ContextPrecision"]["score"] == 0.7  # "No contexts"


# ── _run_demo Tool Calling ──


def test_demo_tool_calling_match():
    result = _run_demo(
        case_input="Search for papers about AI",
        actual_output="Found 5 papers.",
        case_type="tool_calling",
        reference_answer="Found 5 papers.",
        retrieval_context=None,
        expected_tool="search_papers",
        actual_tool="search_papers",
        expected_args={"query": "AI"},
        actual_args={"query": "AI"},
    )
    scores = result["scores"]
    assert "ToolCorrectness" in scores
    assert scores["ToolCorrectness"]["score"] == 1.0  # Exact match


def test_demo_tool_calling_mismatch():
    result = _run_demo(
        case_input="Search for papers about AI",
        actual_output="",
        case_type="tool_calling",
        reference_answer="",
        retrieval_context=None,
        expected_tool="search_papers",
        actual_tool="list_files",  # Wrong tool
        expected_args={"query": "AI"},
        actual_args={"query": "AI"},
    )
    scores = result["scores"]
    assert scores["ToolCorrectness"]["score"] == 0.0  # Tool mismatch


def test_demo_tool_calling_wrong_args():
    result = _run_demo(
        case_input="Search for papers",
        actual_output="",
        case_type="tool_calling",
        reference_answer="",
        retrieval_context=None,
        expected_tool="search_papers",
        actual_tool="search_papers",
        expected_args={"query": "AI"},
        actual_args={"query": "wrong"},  # Wrong argument
    )
    scores = result["scores"]
    assert "ArgumentAccuracy" in scores
    assert scores["ArgumentAccuracy"]["score"] == 0.0  # 0/1 correct


def test_demo_tool_calling_no_expected_tool():
    result = _run_demo(
        case_input="Search for papers",
        actual_output="",
        case_type="tool_calling",
        reference_answer="",
        retrieval_context=None,
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    scores = result["scores"]
    assert scores["ToolCorrectness"]["score"] == 0.7  # "No expected tool"


# ── _run_demo unknown type ──


def test_demo_unknown_case_type():
    result = _run_demo(
        case_input="test",
        actual_output="test",
        case_type="unknown_type",
        reference_answer="test",
        retrieval_context=None,
        expected_tool=None, actual_tool=None,
        expected_args=None, actual_args=None,
    )
    scores = result["scores"]
    assert "AnswerRelevancy" in scores  # Default metric
    assert scores["AnswerRelevancy"]["score"] >= 0.7  # Q-A overlap-based score


def test_single_case_eval_fails_when_target_omits_expected_tool(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "tool_calling_eval.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    dataset = Dataset(name="Tool Dataset", description="test", case_type="tool_calling")
    session.add(dataset)
    session.flush()

    case = EvalCase(
        dataset_id=dataset.id,
        case_type="tool_calling",
        input="Search for papers about AI",
        reference_answer="Found 5 papers.",
        expected_tool="search_papers",
        expected_args={"query": "AI"},
    )
    session.add(case)
    session.flush()

    run = EvalRun(
        name="Tool Run",
        dataset_id=dataset.id,
        total_cases=1,
        config_json={"provider": "deepseek", "model": "deepseek-chat"},
    )
    session.add(run)
    session.flush()

    result = EvalResult(run_id=run.id, case_id=case.id, status="pending")
    session.add(result)
    session.commit()
    session.close()

    monkeypatch.setattr(celery_app, "_call_target_system", lambda case, config: (
        "Found 5 papers.", None, None, 12, 3
    ))
    monkeypatch.setattr(celery_app, "SessionLocal", TestingSessionLocal, raising=False)
    monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)

    outcome = celery_app._evaluate_single_case(
        result_id=1,
        case_id=1,
        run_id=1,
        provider="deepseek",
        model="deepseek-chat",
        case_input="Search for papers about AI",
        case_type="tool_calling",
        reference_answer="Found 5 papers.",
        reference_context_ids=None,
        expected_tool="search_papers",
        expected_args={"query": "AI"},
        config_json={"provider": "deepseek", "model": "deepseek-chat"},
    )

    check = TestingSessionLocal()
    stored_result = check.get(EvalResult, 1)
    try:
        assert outcome["status"] == "failed"
        assert stored_result is not None
        assert stored_result.status == "failed"
        assert stored_result.scores["ToolCorrectness"]["score"] == 0.0
        assert stored_result.scores["ArgumentAccuracy"]["score"] == 0.0
    finally:
        check.close()


def test_run_evaluation_marks_demo_mode_when_no_target_url(monkeypatch, tmp_path: Path):
    db_path = tmp_path / "demo_mode_run.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    session = TestingSessionLocal()
    dataset = Dataset(name="Demo Dataset", description="test", case_type="qa")
    session.add(dataset)
    session.flush()
    session.add(
        EvalCase(
            dataset_id=dataset.id,
            case_type="qa",
            input="What is LLMOps?",
            reference_answer="A platform for operating LLM systems.",
        )
    )
    session.flush()
    run = EvalRun(
        name="Demo Run",
        dataset_id=dataset.id,
        total_cases=1,
        config_json={"provider": "deepseek", "model": "deepseek-chat", "concurrency": 1},
    )
    session.add(run)
    session.commit()
    session.close()

    monkeypatch.setattr(celery_app, "SessionLocal", TestingSessionLocal, raising=False)
    monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)

    outcome = celery_app.run_evaluation_task(1)

    check = TestingSessionLocal()
    stored_run = check.get(EvalRun, 1)
    try:
        assert outcome["status"] == "completed"
        assert stored_run is not None
        assert stored_run.config_json["target_mode"] == "demo"
    finally:
        check.close()
