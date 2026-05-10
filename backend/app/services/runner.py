"""Evaluation runner. Supports DeepSeek, OpenAI, DashScope, Anthropic, Ollama + Demo mode."""

import base64
import os
import re
import time

from app.services.logger import logger


# ---------- Provider config ----------

PROVIDER_META = {
    "deepseek": {"env_key": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com", "model_class": "DeepSeekModel"},
    "openai": {"env_key": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1", "model_class": "GPTModel"},
    "dashscope": {"env_key": "DASHSCOPE_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_class": "GPTModel"},
    "anthropic": {"env_key": "ANTHROPIC_API_KEY", "base_url": None, "model_class": "AnthropicModel"},
    "ollama": {"env_key": None, "base_url": "http://localhost:11434", "model_class": "OllamaModel"},
}

METRIC_MAP = {
    "qa": ["AnswerRelevancy", "Correctness"],
    "rag": ["Faithfulness", "AnswerRelevancy", "ContextRecall", "ContextPrecision"],
    "tool_calling": ["ToolCorrectness", "ArgumentAccuracy"],
    "multi_turn": ["TaskCompletion"],
}


def _get_key_from_db(provider: str) -> dict | None:
    """Read API key config from database."""
    from app.database import SessionLocal
    from app.models.apikey import APIKey
    db = SessionLocal()
    try:
        key = db.query(APIKey).filter(APIKey.provider == provider).first()
        if key:
            raw = base64.b64decode(key.api_key.encode()).decode()
            return {
                "api_key": raw,
                "base_url": key.base_url,
                "model": key.default_model,
            }
        return None
    finally:
        db.close()


def _resolve_provider_config(provider: str = "deepseek", model_override: str | None = None) -> dict:
    """Get API key + base_url for a provider. Checks DB first, then env vars."""
    db_config = _get_key_from_db(provider)
    meta = PROVIDER_META.get(provider, {})

    if db_config:
        if model_override:
            db_config["model"] = model_override
        return db_config

    # Fall back to env var
    env_key = meta.get("env_key")
    if env_key and os.getenv(env_key):
        return {
            "api_key": os.getenv(env_key),
            "base_url": meta.get("base_url"),
            "model": model_override or (
                "gpt-4o-mini" if provider == "openai" else
                "deepseek-chat" if provider == "deepseek" else
                "qwen-plus" if provider == "dashscope" else
                "claude-haiku-4-5-20251001" if provider == "anthropic" else
                "qwen2.5:7b"
            ),
        }

    return {"model": model_override} if model_override else {}


def _has_api_key(provider: str = "deepseek", model_override: str | None = None) -> bool:
    return bool(_resolve_provider_config(provider, model_override=model_override).get("api_key"))


# ── Token counting ──

# Encoding preference: o200k_base for GPT-4o family, cl100k_base for GPT-4 / DeepSeek / fallback
_MODEL_ENCODING: dict[str, str] = {
    "gpt-4o": "o200k_base", "gpt-4o-mini": "o200k_base",
    "gpt-4.1": "o200k_base",
}

_tiktoken_encoders: dict[str, object] = {}
_tiktoken_available = True


def _get_encoding(name: str):
    """Lazy-load a tiktoken encoding by name."""
    if name not in _tiktoken_encoders:
        import tiktoken
        _tiktoken_encoders[name] = tiktoken.get_encoding(name)
    return _tiktoken_encoders[name]


def _resolve_encoding_name(model: str | None) -> str:
    if model and model in _MODEL_ENCODING:
        return _MODEL_ENCODING[model]
    return "cl100k_base"


def count_tokens(text: str, model: str | None = None) -> int:
    """Count tokens in text using tiktoken. Falls back to char//3 on failure."""
    if not text:
        return 0
    global _tiktoken_available
    if _tiktoken_available:
        try:
            enc = _get_encoding(_resolve_encoding_name(model))
            return len(enc.encode(text))
        except Exception:
            _tiktoken_available = False
    return max(1, len(text) // 3)


# ---------- Demo mode ----------

def _demo_faithfulness(actual: str, reference: str, contexts: list[str]) -> dict:
    if not contexts:
        return {"score": 0.7, "reason": "No retrieval context", "success": True}
    all_context = " ".join(contexts).lower()
    words = set(actual.lower().split())
    context_words = set(all_context.split())
    if not words:
        return {"score": 0.0, "reason": "Empty output", "success": False}
    overlap = len(words & context_words) / len(words)
    score = min(overlap * 1.5, 1.0)
    return {"score": round(score, 4), "reason": f"Keyword overlap: {overlap:.1%}", "success": score >= 0.5}


def _demo_answer_relevancy(actual: str, question: str) -> dict:
    q_words = set(re.sub(r"[?？,，.。!！]", "", question).lower().split())
    a_words = set(actual.lower().split())
    if not q_words or not a_words:
        return {"score": 0.5, "reason": "Insufficient content", "success": True}
    overlap = len(q_words & a_words) / len(q_words)
    score = 0.3 + overlap * 0.7
    return {"score": round(score, 4), "reason": f"Q-A word overlap: {overlap:.1%}", "success": score >= 0.5}


def _demo_correctness(actual: str, reference: str) -> dict:
    if not reference:
        return {"score": 0.7, "reason": "No reference answer", "success": True}
    def ngrams(s, n):
        words = s.lower().split()
        return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    try:
        bg_a = ngrams(actual, 2)
        bg_r = ngrams(reference, 2)
        if not bg_r:
            return {"score": 0.5, "reason": "Reference too short", "success": True}
        overlap = len(bg_a & bg_r) / len(bg_r)
        score = round(overlap, 4) if overlap < 1 else 0.95
    except Exception:
        score = 0.5
    return {"score": score, "reason": f"Bigram overlap: {score:.1%}", "success": score >= 0.5}


def _demo_context_recall(reference: str, contexts: list[str]) -> dict:
    if not contexts or not reference:
        return {"score": 0.7, "reason": "No context/reference", "success": True}
    ref_words = set(reference.lower().split())
    ctx_words = set(" ".join(contexts).lower().split())
    if not ref_words:
        return {"score": 0.0, "reason": "Empty reference", "success": False}
    recall = len(ref_words & ctx_words) / len(ref_words)
    return {"score": round(recall, 4), "reason": f"Recall: {recall:.1%}", "success": recall >= 0.5}


def _demo_context_precision(contexts: list[str], actual: str) -> dict:
    if not contexts:
        return {"score": 0.7, "reason": "No contexts", "success": True}
    ctx_words = set(" ".join(contexts).lower().split())
    actual_words = set(actual.lower().split())
    if not ctx_words:
        return {"score": 0.5, "reason": "Empty contexts", "success": True}
    precision = len(ctx_words & actual_words) / len(ctx_words)
    return {"score": round(precision, 4), "reason": f"Precision: {precision:.1%}", "success": precision >= 0.3}


def _demo_tool_correctness(actual_tool: str, expected_tool: str) -> dict:
    if not expected_tool:
        return {"score": 0.7, "reason": "No expected tool", "success": True}
    match = actual_tool == expected_tool
    return {"score": 1.0 if match else 0.0, "reason": "Tool match" if match else f"Expected '{expected_tool}', got '{actual_tool}'", "success": match}


def _demo_argument_accuracy(actual_args: dict | None, expected_args: dict | None) -> dict:
    if not expected_args:
        return {"score": 0.7, "reason": "No expected args", "success": True}
    if not actual_args:
        return {"score": 0.0, "reason": "No args provided", "success": False}
    keys = set(expected_args.keys())
    correct = sum(1 for k in keys if str(actual_args.get(k, "")) == str(expected_args.get(k, "")))
    score = round(correct / len(keys), 4) if keys else 0.5
    return {"score": score, "reason": f"Correct: {correct}/{len(keys)}", "success": score >= 0.5}


def _run_demo(
    case_input, actual_output, case_type, reference_answer,
    retrieval_context, expected_tool, actual_tool, expected_args, actual_args,
) -> dict:
    metric_names = METRIC_MAP.get(case_type, ["AnswerRelevancy"])
    contexts = retrieval_context or []
    ref = reference_answer or ""
    scores = {}

    for name in metric_names:
        if name == "Faithfulness":
            scores[name] = _demo_faithfulness(actual_output, ref, contexts)
        elif name == "AnswerRelevancy":
            scores[name] = _demo_answer_relevancy(actual_output, case_input)
        elif name == "Correctness":
            scores[name] = _demo_correctness(actual_output, ref)
        elif name == "ContextRecall":
            scores[name] = _demo_context_recall(ref, contexts)
        elif name == "ContextPrecision":
            scores[name] = _demo_context_precision(contexts, actual_output)
        elif name == "ToolCorrectness":
            scores[name] = _demo_tool_correctness(actual_tool or "", expected_tool or "")
        elif name == "ArgumentAccuracy":
            scores[name] = _demo_argument_accuracy(actual_args, expected_args)
        else:
            scores[name] = {"score": 0.7, "reason": "Demo", "success": True}
    return {"scores": scores}


# ---------- Real mode: DeepEval ----------

def _run_deepeval(
    case_input, actual_output, case_type, reference_answer,
    retrieval_context, expected_tool, actual_tool, expected_args, actual_args,
    provider: str = "deepseek",
    model: str | None = None,
) -> dict:
    from deepeval.test_case import LLMTestCase, ToolCall
    from deepeval.metrics import (
        FaithfulnessMetric, AnswerRelevancyMetric,
        ContextualRecallMetric, ContextualPrecisionMetric,
        ToolCorrectnessMetric,
    )
    from deepeval.models import (
        DeepSeekModel, GPTModel, AnthropicModel, OllamaModel,
    )

    config = _resolve_provider_config(provider, model_override=model)
    api_key = config.get("api_key", "")
    base_url = config.get("base_url", "")
    model_name = config.get("model", model or "gpt-4o-mini")

    # Build DeepEval model instance
    model_class_map = {
        "DeepSeekModel": DeepSeekModel,
        "GPTModel": GPTModel,
        "AnthropicModel": AnthropicModel,
        "OllamaModel": OllamaModel,
    }
    meta = PROVIDER_META.get(provider, {})
    cls_name = meta.get("model_class", "GPTModel")
    model_cls = model_class_map.get(cls_name, GPTModel)

    kwargs = {"model": model_name}
    if cls_name == "GPTModel":
        kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
    elif cls_name == "DeepSeekModel":
        kwargs["api_key"] = api_key
    elif cls_name == "AnthropicModel":
        kwargs["api_key"] = api_key
    elif cls_name == "OllamaModel":
        if base_url:
            kwargs["base_url"] = base_url

    llm_model = model_cls(**kwargs)

    # Build test case
    tools_called = None
    expected_tools = None
    if actual_tool or expected_tool:
        tools_called = [ToolCall(name=actual_tool or "", input_parameters=actual_args or {})]
        expected_tools = [ToolCall(name=expected_tool or "", input_parameters=expected_args or {})]

    test_case = LLMTestCase(
        input=case_input,
        actual_output=actual_output,
        expected_output=reference_answer or "",
        retrieval_context=retrieval_context or [],
        tools_called=tools_called,
        expected_tools=expected_tools,
    )

    # Get metrics for case type
    metric_name_map = {
        "Faithfulness": FaithfulnessMetric,
        "AnswerRelevancy": AnswerRelevancyMetric,
        "ContextRecall": ContextualRecallMetric,
        "ContextPrecision": ContextualPrecisionMetric,
        "ToolCorrectness": ToolCorrectnessMetric,
    }

    metric_names = METRIC_MAP.get(case_type, ["AnswerRelevancy"])
    scores = {}

    for name in metric_names:
        m_cls = metric_name_map.get(name)
        if m_cls is None:
            continue
        m = m_cls(threshold=0.5, model=llm_model, include_reason=True)
        try:
            m.measure(test_case)
            scores[name] = {
                "score": round(m.score, 4) if m.score is not None else 0,
                "reason": m.reason,
                "success": m.success if m.success is not None else False,
            }
        except Exception as e:
            scores[name] = {"score": 0, "reason": str(e)[:1000], "success": False}

    return {"scores": scores}


# ---------- Public API ----------

def run_case_evaluation(
    case_input: str,
    actual_output: str,
    case_type: str = "qa",
    reference_answer: str | None = None,
    retrieval_context: list[str] | None = None,
    expected_tool: str | None = None,
    actual_tool: str | None = None,
    expected_args: dict | None = None,
    actual_args: dict | None = None,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
) -> dict:
    start = time.time()

    if _has_api_key(provider, model_override=model):
        logger.debug(f"Evaluating with DeepEval: provider={provider}, model={model}, case_type={case_type}")
        result = _run_deepeval(
            case_input, actual_output, case_type,
            reference_answer, retrieval_context,
            expected_tool, actual_tool, expected_args, actual_args,
            provider=provider,
            model=model,
        )
    else:
        logger.warning(f"No API key configured for {provider}, falling back to demo mode")
        result = _run_demo(
            case_input, actual_output, case_type,
            reference_answer, retrieval_context,
            expected_tool, actual_tool, expected_args, actual_args,
        )

    elapsed = int((time.time() - start) * 1000)

    # Count tokens: case input + retrieval context + reference
    input_parts = [case_input, reference_answer or ""]
    if retrieval_context:
        input_parts.extend(retrieval_context)
    if expected_tool:
        input_parts.append(expected_tool)
    if expected_args:
        input_parts.append(str(expected_args))
    input_text = "\n".join(input_parts)

    output_tokens = count_tokens(actual_output, model)
    # Input tokens sent to target system (case input + context + reference)
    input_tokens = count_tokens(input_text, model)

    return {
        "scores": result["scores"],
        "actual_output": actual_output,
        "latency_ms": elapsed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
