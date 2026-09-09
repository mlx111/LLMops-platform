"""Lightweight LLM-as-a-judge client (OpenAI-compatible Chat Completions).

Used for judge-vs-rule calibration. Works with any OpenAI-compatible endpoint
(DashScope qwen, DeepSeek, OpenAI) via stdlib urllib — no new dependencies.

Env vars (first configured wins):
    DASHSCOPE_API_KEY  -> https://dashscope.aliyuncs.com/compatible-mode/v1 (qwen-plus)
    DEEPSEEK_API_KEY   -> https://api.deepseek.com (deepseek-chat)
    OPENAI_API_KEY     -> https://api.openai.com/v1 (gpt-4o-mini)
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request

from app.services.logger import logger

JUDGE_PROVIDERS = [
    {"env_key": "DASHSCOPE_API_KEY", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus"},
    {"env_key": "DEEPSEEK_API_KEY", "base_url": "https://api.deepseek.com", "model": "deepseek-chat"},
    {"env_key": "OPENAI_API_KEY", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
]

_cached_provider: dict | None = None


def resolve_judge_config() -> dict | None:
    """Return the first configured judge provider, or None."""
    global _cached_provider
    if _cached_provider is not None:
        return _cached_provider or None
    for p in JUDGE_PROVIDERS:
        api_key = os.getenv(p["env_key"])
        if api_key:
            _cached_provider = {**p, "api_key": api_key}
            return _cached_provider
    _cached_provider = {}
    return None


def judge_available() -> bool:
    return resolve_judge_config() is not None


def _report_judge_to_langfuse(
    kind: str,
    user_prompt: str,
    output: str | None,
    usage: dict | None,
) -> None:
    """把一次 judge 调用上报为 Langfuse trace + generation（含 token 成本）。

    Phase 6 可观测：评测/judge 自身的 LLM 开销进 Langfuse 看板。
    未配置 keys 或 SDK 缺失时静默跳过，绝不影响评测主链路。
    """
    pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    if not pk or not sk:
        return
    global _langfuse_client
    try:
        from langfuse import Langfuse

        if _langfuse_client is None:
            _langfuse_client = Langfuse()
        cfg = resolve_judge_config() or {}
        trace = _langfuse_client.trace(
            name="llmops-judge",
            session_id=f"judge-{kind}",
            metadata={"kind": kind, "judge_model": cfg.get("model", "")},
        )
        usage_payload = None
        if usage:
            usage_payload = {
                "input": int(usage.get("prompt_tokens") or 0),
                "output": int(usage.get("completion_tokens") or 0),
                "total": int(usage.get("total_tokens") or 0),
                "unit": "TOKENS",
            }
        trace.generation(
            name=f"judge-{kind}",
            model=cfg.get("model", "unknown"),
            input=user_prompt[:4000],
            output=(output or "")[:2000],
            usage=usage_payload,
            metadata={"kind": kind},
        )
        _langfuse_client.flush()
    except Exception as exc:  # noqa: BLE001 - observability must never break eval
        logger.warning(f"Langfuse judge report failed: {str(exc)[:200]}")


def _chat_completion(
    messages: list[dict], temperature: float = 0.0, timeout: int = 60, retries: int = 2
) -> tuple[str | None, dict | None]:
    """调用 judge 模型，返回 (content, usage)；失败返回 (None, None)。"""
    cfg = resolve_judge_config()
    if not cfg:
        return None, None
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    data = json.dumps(payload).encode("utf-8")
    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    last_err = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            req.add_header("Authorization", f"Bearer {cfg['api_key']}")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            raw_usage = body.get("usage") or {}
            usage = {
                "prompt_tokens": raw_usage.get("prompt_tokens") or 0,
                "completion_tokens": raw_usage.get("completion_tokens") or 0,
                "total_tokens": raw_usage.get("total_tokens") or 0,
            }
            return body["choices"][0]["message"]["content"], usage
        except (urllib.error.URLError, urllib.error.HTTPError, KeyError, json.JSONDecodeError) as exc:
            last_err = str(exc)
            logger.warning(f"LLM judge call failed (attempt {attempt + 1}): {last_err[:200]}")
            time.sleep(1.5 * (attempt + 1))
    logger.error(f"LLM judge gave up: {last_err[:200]}")
    return None, None


def _parse_json(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return None
    return None


def _clamp01(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


JUDGE_SYS_PROMPT = (
    "你是严格的大模型评测裁判。根据给定信息打分，只输出 JSON，不要输出多余文字。"
    "评分必须客观、依据证据，避免位置与措辞偏差。"
)


def judge_correctness(question: str, answer: str, reference: str) -> dict:
    """Score answer correctness against reference. Returns {score, reason}."""
    user = (
        "问题：\n" + question +
        "\n\n参考答案要点：\n" + (reference or "(无)") +
        "\n\n待评答案：\n" + (answer or "(空)") +
        "\n\n请判断待评答案在多大程度上正确覆盖了参考答案要点（允许表述不同）。"
        "输出 JSON：{\"score\": 0到1的浮点数, \"reason\": \"简短中文理由\"}。"
        "score=1 表示完全正确覆盖，0 表示完全错误或答非所问。"
    )
    raw = _chat_completion([
        {"role": "system", "content": JUDGE_SYS_PROMPT},
        {"role": "user", "content": user},
    ])
    parsed = _parse_json(raw)
    if not parsed:
        return {"score": None, "reason": "judge unavailable"}
    return {"score": _clamp01(parsed.get("score")), "reason": str(parsed.get("reason", ""))[:300]}


def judge_faithfulness(question: str, answer: str, contexts: list[str]) -> dict:
    """RAGAS-style faithfulness: answer claims supported by contexts."""
    ctx_text = "\n---\n".join(contexts) if contexts else "(无检索上下文)"
    user = (
        "问题：\n" + question +
        "\n\n检索上下文：\n" + ctx_text +
        "\n\n待评答案：\n" + (answer or "(空)") +
        "\n\n请判断答案中的陈述有多大比例能由检索上下文直接支持（无编造）。"
        "输出 JSON：{\"score\": 0到1, \"reason\": \"简短中文理由\"}。"
    )
    raw = _chat_completion([
        {"role": "system", "content": JUDGE_SYS_PROMPT},
        {"role": "user", "content": user},
    ])
    parsed = _parse_json(raw)
    if not parsed:
        return {"score": None, "reason": "judge unavailable"}
    return {"score": _clamp01(parsed.get("score")), "reason": str(parsed.get("reason", ""))[:300]}


def judge_preference(question: str, answer_a: str, answer_b: str) -> dict:
    """Pairwise preference. Returns {preferred: 'A'|'B'|'tie', reason}."""
    user = (
        "问题：\n" + question +
        "\n\n答案 A：\n" + (answer_a or "(空)") +
        "\n\n答案 B：\n" + (answer_b or "(空)") +
        "\n\n请比较两个答案的质量（正确性、完整性、相关性），选出更好的一个。"
        "输出 JSON：{\"preferred\": \"A\" 或 \"B\" 或 \"tie\", \"reason\": \"简短中文理由\"}。"
    )
    raw, usage = _chat_completion([
        {"role": "system", "content": JUDGE_SYS_PROMPT},
        {"role": "user", "content": user},
    ])
    _report_judge_to_langfuse("preference", user, raw, usage)
    parsed = _parse_json(raw)
    if not parsed:
        return {"preferred": None, "reason": "judge unavailable"}
    preferred = str(parsed.get("preferred", "")).upper().strip()
    if preferred not in ("A", "B", "TIE"):
        preferred = "TIE"
    return {"preferred": preferred, "reason": str(parsed.get("reason", ""))[:300]}
