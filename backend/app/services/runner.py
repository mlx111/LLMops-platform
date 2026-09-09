"""Evaluation runner. Supports DeepSeek, OpenAI, DashScope, Anthropic, Ollama + Demo mode."""

import json
import os
import re
import time
from typing import Any

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
    "agent_trajectory": [
        "TaskSuccess",
        "ToolSelectionAccuracy",
        "ArgumentAccuracy",
        "StepEfficiency",
    ],
}


def _get_key_from_db(provider: str) -> dict | None:
    """Read API key config from database."""
    from app.database import SessionLocal
    from app.models.apikey import APIKey, _decode
    db = SessionLocal()
    try:
        key = db.query(APIKey).filter(APIKey.provider == provider).first()
        if key:
            raw = _decode(key.api_key)
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


def _stringify_for_tokens(value) -> str:
    """Convert structured values to stable text for token accounting."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)


# ---------- Demo mode ----------

# Chinese-friendly tokenization: ASCII word split does not work for CJK text
# (no spaces between words). We extract ASCII words/digits as-is and add
# single CJK chars plus adjacent bigrams so short Chinese terms match.
_ASCII_TOKEN = re.compile(r"[a-z0-9][a-z0-9._+-]*")
_CJK_CHAR = re.compile(r"[一-鿿]")


def _tokenize(text: str) -> set[str]:
    """Mixed-language token set: ASCII words + CJK unigrams & bigrams."""
    if not text:
        return set()
    lowered = text.lower()
    tokens: set[str] = set(_ASCII_TOKEN.findall(lowered))
    cjk = _CJK_CHAR.findall(lowered)
    tokens.update(cjk)
    tokens.update(cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1))
    return tokens


def _coverage(must_have: set[str], pool: set[str]) -> float:
    """Fraction of must_have tokens present in pool (deduplicated)."""
    if not must_have:
        return 0.0
    return len(must_have & pool) / len(must_have)


def _demo_faithfulness(actual: str, reference: str, contexts: list[str]) -> dict:
    """RAGAS-style faithfulness: answer claims supported by retrieved context."""
    if not contexts:
        return {"score": 0.7, "reason": "No retrieval context", "success": True}
    actual_tokens = _tokenize(actual)
    context_tokens = _tokenize(" ".join(contexts))
    if not actual_tokens:
        return {"score": 0.0, "reason": "Empty output", "success": False}
    supported = _coverage(actual_tokens, context_tokens)
    score = min(supported * 1.4, 1.0)
    return {"score": round(score, 4), "reason": f"Answer tokens supported by context: {supported:.1%}", "success": score >= 0.5}


_STOP_TERMS = {
    # Chinese question / function words (as unigrams and adjacent bigrams)
    "什么", "怎么", "怎样", "如何", "为什么", "为何", "哪些", "哪个", "哪里", "多少",
    "请问", "一下", "帮我", "我们", "他们", "可以", "能够", "需要", "应该", "什么是",
    "解释", "说明", "简述", "简要", "告诉", "对比", "比较", "区别", "之间", "关于",
    "根据", "通过", "进行", "以及", "等等", "作用", "原因", "优缺点", "特点",
    "这个", "那个", "一种", "什么", "怎么", "有什么", "是什么", "有哪些",
}


def _demo_answer_relevancy(actual: str, question: str, reference: str = "") -> dict:
    """RAGAS-style answer relevancy: does the answer address the question topic.

    Rule-based proxy for the embedding-based RAGAS metric. Two topical signals:
    question-term coverage (answer echoes the question's subject) and
    reference-term coverage (answer stays on the same topic as the reference).
    An answer sharing neither signal with the question domain is off-topic.
    """
    q_tokens = _tokenize(re.sub(r"[?？,，.。!！、：:]", "", question))
    a_tokens = _tokenize(actual)
    if not q_tokens or not a_tokens:
        return {"score": 0.5, "reason": "Insufficient content", "success": True}
    # Keep content-bearing terms: ASCII terms / CJK bigrams, minus stopwords.
    q_content = set()
    for t in q_tokens:
        is_ascii_term = bool(re.fullmatch(r"[a-z0-9][a-z0-9._+-]*", t))
        if is_ascii_term and len(t) >= 2:
            q_content.add(t)
        elif len(t) >= 2 and t not in _STOP_TERMS:
            q_content.add(t)
    if not q_content:
        q_content = {t for t in q_tokens if len(t) >= 2}
    q_cov = _coverage(q_content, a_tokens)
    r_content = {t for t in _tokenize(re.sub(r"参考要点[：:]", "", reference)) if len(t) >= 2}
    r_cov = _coverage(r_content, a_tokens) if r_content else 0.0
    score = 0.2 + 0.4 * q_cov + 0.4 * r_cov
    return {"score": round(score, 4),
            "reason": f"On-topic signals: question-term {q_cov:.1%}, reference-term {r_cov:.1%}",
            "success": score >= 0.5}


def _demo_correctness(actual: str, reference: str, must_keywords: list[str] | None = None) -> dict:
    """Reference coverage + required-keyword hits (works for zh/en mixed text)."""
    if not reference:
        return {"score": 0.7, "reason": "No reference answer", "success": True}
    a_tokens = _tokenize(actual)
    r_tokens = _tokenize(re.sub(r"参考要点[：:]", "", reference))
    ref_terms = {t for t in r_tokens if len(t) >= 2}
    if not ref_terms:
        return {"score": 0.5, "reason": "Reference too short", "success": True}
    coverage = _coverage(ref_terms, a_tokens)
    kw_hit = 1.0
    if must_keywords:
        kw_hit = sum(1 for kw in must_keywords if _tokenize(kw) & a_tokens) / len(must_keywords)
        score = round(0.6 * coverage + 0.4 * kw_hit, 4)
        reason = f"Reference term coverage: {coverage:.1%}; required keyword hit: {kw_hit:.1%}"
    else:
        score = round(min(coverage * 1.25, 0.95), 4)
        reason = f"Reference term coverage: {coverage:.1%}"
    return {"score": score, "reason": reason, "success": score >= 0.5}


def _demo_context_recall(reference: str, contexts: list[str]) -> dict:
    """RAGAS context recall: reference points retrievable from context."""
    if not contexts or not reference:
        return {"score": 0.7, "reason": "No context/reference", "success": True}
    ref_tokens = {t for t in _tokenize(reference) if len(t) >= 2}
    ctx_tokens = _tokenize(" ".join(contexts))
    if not ref_tokens:
        return {"score": 0.0, "reason": "Empty reference", "success": False}
    recall = _coverage(ref_tokens, ctx_tokens)
    return {"score": round(recall, 4), "reason": f"Reference points covered by context: {recall:.1%}", "success": recall >= 0.5}


def _demo_context_precision(contexts: list[str], actual: str) -> dict:
    """RAGAS context precision: per-chunk relevance to the answer, averaged."""
    if not contexts:
        return {"score": 0.7, "reason": "No contexts", "success": True}
    a_tokens = _tokenize(actual)
    if not a_tokens:
        return {"score": 0.5, "reason": "Empty answer", "success": True}
    chunk_scores = []
    for chunk in contexts:
        c_tokens = {t for t in _tokenize(chunk) if len(t) >= 2}
        if not c_tokens:
            continue
        chunk_scores.append(_coverage(c_tokens, a_tokens))
    if not chunk_scores:
        return {"score": 0.5, "reason": "Empty contexts", "success": True}
    # Weighted by rank (RAGAS contextual precision rewards relevant chunks ranking first).
    weights = [1.0 / (i + 1) for i in range(len(chunk_scores))]
    precision = sum(s * w for s, w in zip(chunk_scores, weights)) / sum(weights)
    return {"score": round(precision, 4), "reason": f"Rank-weighted chunk relevance: {precision:.1%}", "success": precision >= 0.3}


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
    must_keywords: list[str] | None = None,
) -> dict:
    if case_type == "agent_trajectory":
        from app.services.agent_metrics import evaluate_agent_trajectory

        return {"scores": evaluate_agent_trajectory(actual_output, reference_answer)}

    metric_names = METRIC_MAP.get(case_type, ["AnswerRelevancy"])
    contexts = retrieval_context or []
    ref = reference_answer or ""
    scores = {}

    for name in metric_names:
        if name == "Faithfulness":
            scores[name] = _demo_faithfulness(actual_output, ref, contexts)
        elif name == "AnswerRelevancy":
            scores[name] = _demo_answer_relevancy(actual_output, case_input, reference_answer or "")
        elif name == "Correctness":
            scores[name] = _demo_correctness(actual_output, ref, must_keywords=must_keywords)
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
    must_keywords: list[str] | None = None,
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
    actual_output: Any,
    case_type: str = "qa",
    reference_answer: Any = None,
    retrieval_context: list[str] | None = None,
    expected_tool: str | None = None,
    actual_tool: str | None = None,
    expected_args: dict | None = None,
    actual_args: dict | None = None,
    provider: str = "deepseek",
    model: str = "deepseek-chat",
    extra_metadata: dict | None = None,
) -> dict:
    start = time.time()
    must_keywords = (extra_metadata or {}).get("must_include_keywords") if extra_metadata else None

    if case_type == "agent_trajectory":
        # Trajectory metrics (TaskSuccess / ToolSelectionAccuracy /
        # ArgumentAccuracy / StepEfficiency) are deterministic and never use an
        # LLM judge, so they must not be routed to DeepEval (which has no
        # equivalent metrics and would return empty scores).
        from app.services.agent_metrics import evaluate_agent_trajectory

        logger.debug("Evaluating agent trajectory with deterministic metrics")
        result = {"scores": evaluate_agent_trajectory(actual_output, reference_answer)}
    elif _has_api_key(provider, model_override=model):
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
            must_keywords=must_keywords,
        )

    elapsed = int((time.time() - start) * 1000)

    # Count tokens: case input + retrieval context + reference
    input_parts = [_stringify_for_tokens(case_input), _stringify_for_tokens(reference_answer)]
    if retrieval_context:
        input_parts.extend(_stringify_for_tokens(item) for item in retrieval_context)
    if expected_tool:
        input_parts.append(_stringify_for_tokens(expected_tool))
    if expected_args:
        input_parts.append(_stringify_for_tokens(expected_args))
    input_text = "\n".join(input_parts)

    output_tokens = count_tokens(_stringify_for_tokens(actual_output), model)
    # Input tokens sent to target system (case input + context + reference)
    input_tokens = count_tokens(input_text, model)

    return {
        "scores": result["scores"],
        "actual_output": actual_output,
        "latency_ms": elapsed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
