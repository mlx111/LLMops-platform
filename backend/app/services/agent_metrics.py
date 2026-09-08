"""Deterministic metrics for evaluating agent execution trajectories."""

from __future__ import annotations

import json
from typing import Any


def evaluate_agent_trajectory(actual: Any, expected: Any) -> dict[str, dict[str, Any]]:
    """Evaluate an agent trajectory against an expected trajectory.

    The expected trajectory defines the required tool-call sequence and arguments.
    Non-tool steps such as plan, observation, reflection, and final are allowed
    in actual trajectories and do not count against tool selection.
    """
    actual_trajectory = _coerce_trajectory(actual)
    expected_trajectory = _coerce_trajectory(expected)

    actual_tool_calls = _tool_calls(actual_trajectory)
    expected_tool_calls = _tool_calls(expected_trajectory)

    task_success = _score_task_success(actual_trajectory, actual_tool_calls)
    tool_accuracy = _score_tool_selection(actual_tool_calls, expected_tool_calls)
    arg_accuracy = _score_arguments(actual_tool_calls, expected_tool_calls)
    step_efficiency = _score_step_efficiency(actual_tool_calls, expected_tool_calls)

    return {
        "TaskSuccess": _metric(
            task_success,
            "Trajectory completed successfully" if task_success == 1.0 else "Trajectory did not complete successfully",
            task_success >= 1.0,
        ),
        "ToolSelectionAccuracy": _metric(
            tool_accuracy,
            f"Matched {round(tool_accuracy * len(expected_tool_calls))}/{len(expected_tool_calls)} expected tool calls"
            if expected_tool_calls
            else "No expected tool calls",
            tool_accuracy >= 0.5,
        ),
        "ArgumentAccuracy": _metric(
            arg_accuracy,
            "Expected tool arguments matched" if arg_accuracy == 1.0 else "One or more expected tool arguments did not match",
            arg_accuracy >= 0.5,
        ),
        "StepEfficiency": _metric(
            step_efficiency,
            f"Actual tool calls: {len(actual_tool_calls)}, expected tool calls: {len(expected_tool_calls)}",
            step_efficiency >= 0.5,
        ),
    }


def _metric(score: float, reason: str, success: bool) -> dict[str, Any]:
    return {"score": round(score, 4), "reason": reason, "success": success}


def _coerce_trajectory(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"final_answer": text, "success": bool(text), "steps": []}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _tool_calls(trajectory: dict[str, Any]) -> list[dict[str, Any]]:
    steps = trajectory.get("steps") or []
    if not isinstance(steps, list):
        return []
    calls: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if step.get("type") != "tool_call":
            continue
        tool_name = step.get("tool_name") or step.get("name") or step.get("tool")
        if not tool_name:
            continue
        tool_args = step.get("tool_args") or step.get("args") or step.get("arguments") or {}
        calls.append({"tool_name": str(tool_name), "tool_args": tool_args if isinstance(tool_args, dict) else {}})
    return calls


def _score_task_success(trajectory: dict[str, Any], actual_tool_calls: list[dict[str, Any]]) -> float:
    if trajectory.get("success") is True:
        return 1.0
    status = str(trajectory.get("status") or "").lower()
    if status in {"success", "passed", "completed"}:
        return 1.0
    if trajectory.get("final_answer") or any(step.get("type") == "final" for step in trajectory.get("steps", []) if isinstance(step, dict)):
        return 1.0 if actual_tool_calls or trajectory.get("final_answer") else 0.0
    return 0.0


def _score_tool_selection(actual_calls: list[dict[str, Any]], expected_calls: list[dict[str, Any]]) -> float:
    if not expected_calls:
        return 1.0
    matched_actual_indexes: set[int] = set()
    matched = 0
    for expected in expected_calls:
        index = _find_unmatched_tool_call(actual_calls, expected["tool_name"], matched_actual_indexes)
        if index is None:
            continue
        matched_actual_indexes.add(index)
        matched += 1
    return matched / len(expected_calls)


def _score_arguments(actual_calls: list[dict[str, Any]], expected_calls: list[dict[str, Any]]) -> float:
    expected_args = [
        (index, call["tool_args"])
        for index, call in enumerate(expected_calls)
        if call.get("tool_args")
    ]
    if not expected_args:
        return 1.0

    total = 0
    matched = 0
    matched_actual_indexes: set[int] = set()
    for _index, expected in expected_args:
        expected_tool = expected_calls[_index]["tool_name"]
        actual_index = _find_unmatched_tool_call(actual_calls, expected_tool, matched_actual_indexes)
        actual = actual_calls[actual_index]["tool_args"] if actual_index is not None else {}
        if actual_index is not None:
            matched_actual_indexes.add(actual_index)
        for key, expected_value in expected.items():
            total += 1
            if str(actual.get(key, "")).strip().lower() == str(expected_value).strip().lower():
                matched += 1
    return matched / total if total else 1.0


def _score_step_efficiency(actual_calls: list[dict[str, Any]], expected_calls: list[dict[str, Any]]) -> float:
    if not expected_calls:
        return 1.0
    if not actual_calls:
        return 0.0
    extra_calls = max(0, len(actual_calls) - len(expected_calls))
    return max(0.0, 1.0 - (extra_calls / max(len(actual_calls), 1)))


def _find_unmatched_tool_call(
    actual_calls: list[dict[str, Any]],
    tool_name: str,
    used_indexes: set[int],
) -> int | None:
    for index, call in enumerate(actual_calls):
        if index in used_indexes:
            continue
        if call["tool_name"] == tool_name:
            return index
    return None
