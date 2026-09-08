"""Tests for agent trajectory evaluation metrics."""

from app.services.runner import _run_demo, run_case_evaluation


def _trajectory(steps):
    return {"steps": steps, "success": True}


def test_agent_trajectory_scores_successful_tool_path():
    expected = _trajectory(
        [
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "agent evaluation"}},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p1"}},
            {"type": "final", "content": "summary"},
        ]
    )
    actual = _trajectory(
        [
            {"type": "plan", "content": "search then summarize"},
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "agent evaluation"}},
            {"type": "observation", "content": "found p1"},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p1"}},
            {"type": "final", "content": "summary"},
        ]
    )

    result = _run_demo(
        case_input="Find and summarize a paper about agent evaluation",
        actual_output=actual,
        case_type="agent_trajectory",
        reference_answer=expected,
        retrieval_context=None,
        expected_tool=None,
        actual_tool=None,
        expected_args=None,
        actual_args=None,
    )

    scores = result["scores"]
    assert set(scores) == {
        "TaskSuccess",
        "ToolSelectionAccuracy",
        "ArgumentAccuracy",
        "StepEfficiency",
    }
    assert scores["TaskSuccess"]["score"] == 1.0
    assert scores["ToolSelectionAccuracy"]["score"] == 1.0
    assert scores["ArgumentAccuracy"]["score"] == 1.0
    assert scores["StepEfficiency"]["score"] == 1.0


def test_agent_trajectory_penalizes_wrong_tool_args_and_extra_steps():
    expected = _trajectory(
        [
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "RAG"}},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p1"}},
            {"type": "final", "content": "answer"},
        ]
    )
    actual = _trajectory(
        [
            {"type": "tool_call", "tool_name": "web_search", "tool_args": {"query": "RAG"}},
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "wrong"}},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p2"}},
            {"type": "tool_call", "tool_name": "get_paper_bibtex", "tool_args": {"paper_id": "p2"}},
            {"type": "final", "content": "answer"},
        ]
    )

    result = _run_demo(
        case_input="Find a RAG paper",
        actual_output=actual,
        case_type="agent_trajectory",
        reference_answer=expected,
        retrieval_context=None,
        expected_tool=None,
        actual_tool=None,
        expected_args=None,
        actual_args=None,
    )

    scores = result["scores"]
    assert scores["TaskSuccess"]["score"] == 1.0
    assert scores["ToolSelectionAccuracy"]["score"] == 1.0
    assert scores["ArgumentAccuracy"]["score"] == 0.0
    assert scores["StepEfficiency"]["score"] == 0.5
    assert scores["ToolSelectionAccuracy"]["success"] is True
    assert scores["ArgumentAccuracy"]["success"] is False


def test_agent_trajectory_reports_missing_required_tool():
    expected = _trajectory(
        [
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "RAG"}},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p1"}},
            {"type": "final", "content": "answer"},
        ]
    )
    actual = _trajectory(
        [
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "RAG"}},
            {"type": "tool_call", "tool_name": "web_search", "tool_args": {"query": "RAG"}},
            {"type": "final", "content": "answer"},
        ]
    )

    result = _run_demo(
        case_input="Find a RAG paper",
        actual_output=actual,
        case_type="agent_trajectory",
        reference_answer=expected,
        retrieval_context=None,
        expected_tool=None,
        actual_tool=None,
        expected_args=None,
        actual_args=None,
    )

    scores = result["scores"]
    assert scores["ToolSelectionAccuracy"]["score"] == 0.5
    assert scores["ArgumentAccuracy"]["score"] == 0.5


def test_run_case_evaluation_accepts_agent_trajectory_dict_payload():
    expected = _trajectory(
        [
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "agent evaluation"}},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p1"}},
            {"type": "final", "content": "summary"},
        ]
    )
    actual = _trajectory(
        [
            {"type": "tool_call", "tool_name": "search_papers", "tool_args": {"query": "agent evaluation"}},
            {"type": "tool_call", "tool_name": "get_paper_abstract", "tool_args": {"paper_id": "p1"}},
            {"type": "final", "content": "summary"},
        ]
    )

    result = run_case_evaluation(
        case_input="Find and summarize a paper about agent evaluation",
        actual_output=actual,
        case_type="agent_trajectory",
        reference_answer=expected,
        provider="deepseek",
        model="deepseek-chat",
    )

    assert result["scores"]["TaskSuccess"]["score"] == 1.0
    assert result["scores"]["ToolSelectionAccuracy"]["score"] == 1.0
    assert result["scores"]["ArgumentAccuracy"]["score"] == 1.0
    assert result["scores"]["StepEfficiency"]["score"] == 1.0
    assert result["actual_output"] == actual
    assert result["output_tokens"] > 0
