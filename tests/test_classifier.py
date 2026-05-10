"""Tests for failure classifier."""

from app.services.classifier import classify


def _make_case(**kwargs):
    """Minimal EvalCase-like object for classifier tests."""
    class FakeCase:
        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
    return FakeCase(**kwargs)


# ── Classification rules ──


def test_classify_hallucination():
    case = _make_case(
        id=1,
        reference_answer="Diabetes is a chronic metabolic disorder.",
        reference_context_ids=["ctx1"],
        expected_tool=None,
        expected_args=None,
    )
    output = "Aliens cause diabetes through cosmic radiation waves from space."
    scores = {"Faithfulness": {"score": 0.2, "success": False}}

    reasons = classify(scores, case, actual_output=output)
    assert "hallucination" in reasons


def test_classify_retrieval_miss():
    case = _make_case(
        id=2,
        reference_answer="test",
        reference_context_ids=["ctx1", "ctx2"],
        expected_tool=None,
        expected_args=None,
    )
    scores = {"ContextRecall": {"score": 0.2, "success": False}}

    reasons = classify(scores, case)
    assert "retrieval_miss" in reasons


def test_classify_low_context_precision():
    case = _make_case(
        id=3,
        reference_answer="test",
        reference_context_ids=["c1", "c2", "c3", "c4", "c5"],
        expected_tool=None,
        expected_args=None,
    )
    scores = {"ContextPrecision": {"score": 0.1, "success": False}}

    reasons = classify(scores, case)
    assert "low_context_precision" in reasons


def test_classify_evidence_ignored():
    case = _make_case(
        id=4,
        reference_answer="test",
        reference_context_ids=["ctx1"],
        expected_tool=None,
        expected_args=None,
    )
    scores = {
        "ContextRecall": {"score": 0.8, "success": True},
        "Faithfulness": {"score": 0.3, "success": False},
    }

    reasons = classify(scores, case)
    assert "evidence_ignored" in reasons


def test_classify_tool_selection_error():
    case = _make_case(
        id=5,
        reference_answer="",
        reference_context_ids=None,
        expected_tool="search_papers",
        expected_args={"query": "AI"},
    )
    scores = {}

    reasons = classify(
        scores, case, actual_tool="list_files",
        actual_args={"query": "AI"},
    )
    assert "tool_selection_error" in reasons


def test_classify_tool_selection_error_when_expected_tool_missing():
    case = _make_case(
        id=51,
        reference_answer="",
        reference_context_ids=None,
        expected_tool="search_papers",
        expected_args={"query": "AI"},
    )
    scores = {}

    reasons = classify(
        scores, case, actual_tool="",
        actual_args=None,
    )
    assert "tool_selection_error" in reasons


def test_classify_tool_argument_error():
    case = _make_case(
        id=6,
        reference_answer="",
        reference_context_ids=None,
        expected_tool="search_papers",
        expected_args={"query": "AI", "limit": "10"},
    )
    scores = {}

    reasons = classify(
        scores, case,
        actual_tool="search_papers",
        actual_args={"query": "AI", "limit": "5"},  # limit is wrong
    )
    assert "tool_argument_error" in reasons


def test_classify_tool_argument_error_no_match_when_args_are_same():
    case = _make_case(
        id=7,
        reference_answer="",
        reference_context_ids=None,
        expected_tool="search_papers",
        expected_args={"query": "AI"},
    )
    scores = {}

    reasons = classify(
        scores, case,
        actual_tool="search_papers",
        actual_args={"query": "AI"},  # Exact match → no argument error
    )
    assert "tool_argument_error" not in reasons


def test_classify_timeout():
    case = _make_case(
        id=8,
        reference_answer="",
        reference_context_ids=None,
        expected_tool=None,
        expected_args=None,
    )
    scores = {}

    reasons = classify(scores, case, latency_ms=60000)
    assert "timeout" in reasons


def test_classify_high_cost():
    case = _make_case(
        id=9,
        reference_answer="",
        reference_context_ids=None,
        expected_tool=None,
        expected_args=None,
    )
    scores = {}

    reasons = classify(scores, case, input_tokens=6000, output_tokens=5000)
    assert "high_cost" in reasons


def test_classify_prompt_constraint_violation():
    case = _make_case(
        id=10,
        reference_answer="Expected output.",
        reference_context_ids=None,
        expected_tool=None,
        expected_args=None,
    )
    scores = {}

    # Output containing refusal markers
    reasons = classify(
        scores, case,
        actual_output="I cannot answer that question as an AI assistant.",
    )
    assert "prompt_constraint_violation" in reasons


def test_classify_prompt_constraint_too_short():
    case = _make_case(
        id=11,
        reference_answer="Expected output.",
        reference_context_ids=None,
        expected_tool=None,
        expected_args=None,
    )
    scores = {}

    reasons = classify(scores, case, actual_output="Hi")
    assert "prompt_constraint_violation" in reasons


def test_classify_quality_issue_fallback():
    """When no specific rule matches, fall back to quality_issue."""
    case = _make_case(
        id=12,
        reference_answer="test",
        reference_context_ids=None,
        expected_tool=None,
        expected_args=None,
    )
    scores = {"AnswerRelevancy": {"score": 0.8, "success": True}}

    reasons = classify(scores, case, actual_output="normal response")
    assert reasons == ["quality_issue"]


def test_classify_multiple_reasons():
    """One case can trigger multiple failure categories."""
    case = _make_case(
        id=13,
        reference_answer="Diabetes is a chronic metabolic disorder.",
        reference_context_ids=["c1", "c2", "c3", "c4", "c5"],
        expected_tool=None,
        expected_args=None,
    )
    output = "Aliens cause diabetes through cosmic radiation waves."
    scores = {
        "Faithfulness": {"score": 0.2, "success": False},
        "ContextRecall": {"score": 0.2, "success": False},
        "ContextPrecision": {"score": 0.1, "success": False},
    }

    reasons = classify(scores, case, latency_ms=60000, actual_output=output)
    assert "hallucination" in reasons
    assert "retrieval_miss" in reasons
    assert "low_context_precision" in reasons
    assert "timeout" in reasons
