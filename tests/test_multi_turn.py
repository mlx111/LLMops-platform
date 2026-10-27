"""Tests for genuine multi-turn evaluation (layered metrics)."""

from app.services.multi_turn import (
    evaluate_multi_turn,
    _rule_contextual_consistency,
)


# A coherent 3-turn conversation: later answers explicitly carry forward the
# virtual-environment entities established earlier.
COHERENT_TURNS = [
    {
        "user": "How do I set up a Python virtual environment?",
        "reference": "Create it with python -m venv myenv and activate it with source myenv/bin/activate.",
        "answer": "Create a virtual environment with python -m venv myenv, then activate it using source myenv/bin/activate on macOS/Linux.",
    },
    {
        "user": "How do I install packages into it?",
        "reference": "Run pip install <package> while the virtual environment is active.",
        "answer": "With the virtual environment still active, run pip install <package>; packages go into that venv instead of the global interpreter.",
    },
    {
        "user": "How do I export the packages for a teammate?",
        "reference": "Run pip freeze > requirements.txt; teammates use pip install -r requirements.txt.",
        "answer": "Run pip freeze > requirements.txt from the activated virtual environment, and your teammate recreates it with pip install -r requirements.txt.",
    },
]


# Isolated answers: each later turn is a grammatically correct reply but never
# references anything established previously — simulating "answers every turn
# from scratch" (lost conversation context).
ISOLATED_TURNS = [
    {
        "user": "How do I set up a Python virtual environment?",
        "reference": "Create it with python -m venv myenv and activate it.",
        "answer": "Use python -m venv myenv then source myenv/bin/activate to activate the environment.",
    },
    {
        "user": "How do I install packages into it?",
        "reference": "Run pip install with the environment active.",
        # Zero overlap with the venv/activate/environment terms from turn 1.
        "answer": "Bananas are elongated yellow fruits rich in potassium.",
    },
    {
        "user": "How do I export the packages?",
        "reference": "Use pip freeze into requirements.txt.",
        "answer": "The weather in mountain regions changes quickly with altitude.",
    },
]


def test_coherent_conversation_passes_all_metrics():
    result = evaluate_multi_turn(
        COHERENT_TURNS,
        task_overview="End-to-end venv tutorial across 3 turns",
        overall_reference=(
            "Create/activate the venv, pip install packages while active, "
            "pip freeze requirements.txt, pip install -r to reproduce."
        ),
        use_llm_judge=False,
    )
    scores = result["scores"]
    assert set(scores) == {
        "PerTurnCorrectness", "ContextualConsistency", "TaskCompletion"
    }
    assert scores["PerTurnCorrectness"]["success"] is True
    assert scores["ContextualConsistency"]["success"] is True
    assert scores["TaskCompletion"]["success"] is True
    assert len(result["turn_details"]) == 3
    assert all(d["judge"] == "rule" for d in result["turn_details"])


def test_isolated_answers_flag_context_loss():
    """Per-turn quality vs cross-turn consistency must be distinguishable:
    isolated/off-topic later turns fail ContextualConsistency."""
    result = evaluate_multi_turn(
        ISOLATED_TURNS,
        task_overview="venv tutorial",
        overall_reference="venv, pip install, pip freeze",
        use_llm_judge=False,
    )
    scores = result["scores"]
    assert scores["ContextualConsistency"]["success"] is False
    assert scores["ContextualConsistency"]["score"] < 0.4
    assert scores["TaskCompletion"]["success"] is False


def test_rule_consistency_single_turn_is_neutral():
    entry = _rule_contextual_consistency([{"user": "hi", "answer": "hello"}])
    # No prior turn to carry context from — must not punish single-turn cases.
    assert entry["score"] == 0.7
    assert entry["success"] is True


def test_rule_consistency_coherent_scores_higher_than_isolated():
    coherent = _rule_contextual_consistency(COHERENT_TURNS)["score"]
    isolated = _rule_contextual_consistency(ISOLATED_TURNS)["score"]
    assert coherent > isolated


def test_evaluation_never_raises_without_judge_config(monkeypatch):
    """Requesting LLM judge mode with no configured provider must fall back
    to rule-based scoring instead of failing the run."""
    from app.services import llm_judge

    monkeypatch.setattr(llm_judge, "resolve_judge_config", lambda: None)
    monkeypatch.setattr(llm_judge, "_cached_provider", None)

    result = evaluate_multi_turn(
        COHERENT_TURNS,
        task_overview="venv tutorial",
        overall_reference="venv pip freeze",
        use_llm_judge=True,
    )
    assert all(
        isinstance(s["score"], float) for s in result["scores"].values()
    )
    assert all(
        d["judge"] == "rule" for d in result["turn_details"]
    )
