import hashlib
import json
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from app.models.apikey import APIKey, _decode
from app.schemas.apikey import ProviderModelInfo, SUPPORTED_PROVIDERS
from app.services.url_safety import resolve_and_validate


ANTHROPIC_VERSION = "2023-06-01"
PROVIDER_MODEL_CACHE_TTL_SECONDS = 60
_provider_model_cache: dict[str, tuple[float, dict]] = {}
_cache_lock = threading.Lock()


def _supported_provider_map() -> dict[str, dict]:
    return {item["id"]: item for item in SUPPORTED_PROVIDERS}


def _build_model_item(model_id: str, owned_by: str | None) -> dict:
    return ProviderModelInfo(id=model_id, label=model_id, owned_by=owned_by).model_dump()


def _sort_models(models: list[dict]) -> list[dict]:
    deduped = {item["id"]: item for item in models}
    return [deduped[key] for key in sorted(deduped.keys(), key=str.lower)]


def _cache_key(config: dict) -> str:
    """Cache key using hashed API key so plaintext never appears in the key string."""
    raw_key = config.get("api_key", "")
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()[:16] if raw_key else ""
    return "|".join([
        config["provider"],
        key_hash,
        config.get("base_url", "") or "",
        config.get("default_model", "") or "",
    ])


def _read_cached_provider_models(cache_key: str) -> dict | None:
    with _cache_lock:
        cached = _provider_model_cache.get(cache_key)
        if cached is None:
            return None
        expires_at, payload = cached
        if expires_at <= time.time():
            _provider_model_cache.pop(cache_key, None)
            return None
        return payload


def _write_cached_provider_models(cache_key: str, payload: dict) -> None:
    with _cache_lock:
        _provider_model_cache[cache_key] = (
            time.time() + PROVIDER_MODEL_CACHE_TTL_SECONDS,
            payload,
        )


def clear_provider_model_cache() -> None:
    with _cache_lock:
        _provider_model_cache.clear()


def _http_json_get(url: str, headers: dict[str, str] | None = None) -> dict:
    """HTTP GET with SSRF protection. Resolves DNS, validates IPs, and pins
    the connection to the validated IP to prevent DNS rebinding."""
    resolved_ip, original_host = resolve_and_validate(url)
    headers = dict(headers or {})
    headers["Host"] = original_host
    pinned_url = url.replace(f"://{original_host}", f"://{resolved_ip}", 1)
    request = Request(pinned_url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=20, context=ssl.create_default_context()) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:400]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Failed to reach {url}: {exc.reason}") from exc


def _get_provider_config(db: Session, provider: str) -> tuple[dict, dict]:
    provider_meta = _supported_provider_map().get(provider)
    if not provider_meta:
        raise ValueError(f"Unsupported provider: {provider}")

    key = db.query(APIKey).filter(APIKey.provider == provider).first()
    config = {
        "provider": provider,
        "api_key": _decode(key.api_key) if key else "",
        "base_url": key.base_url if key and key.base_url else provider_meta["base_url"],
        "default_model": key.default_model if key else provider_meta["default_model"],
    }
    return provider_meta, config


def _fetch_openai_models(config: dict) -> list[dict]:
    payload = _http_json_get(
        urljoin(f'{config["base_url"].rstrip("/")}/', "models"),
        headers={"Authorization": f'Bearer {config["api_key"]}'},
    )
    return [
        _build_model_item(item["id"], item.get("owned_by"))
        for item in payload.get("data", [])
        if item.get("id")
    ]


def _fetch_deepseek_models(config: dict) -> list[dict]:
    payload = _http_json_get(
        urljoin(f'{config["base_url"].rstrip("/")}/', "models"),
        headers={"Authorization": f'Bearer {config["api_key"]}'},
    )
    return [
        _build_model_item(item["id"], item.get("owned_by"))
        for item in payload.get("data", [])
        if item.get("id")
    ]


def _fetch_anthropic_models(config: dict) -> list[dict]:
    base_url = (config["base_url"] or "https://api.anthropic.com").rstrip("/") + "/"
    payload = _http_json_get(
        urljoin(base_url, "v1/models"),
        headers={
            "x-api-key": config["api_key"],
            "anthropic-version": ANTHROPIC_VERSION,
        },
    )
    return [
        ProviderModelInfo(
            id=item["id"],
            label=item.get("display_name") or item["id"],
            owned_by="anthropic",
        ).model_dump()
        for item in payload.get("data", [])
        if item.get("id")
    ]


def _fetch_ollama_models(config: dict) -> list[dict]:
    base_url = config["base_url"] or "http://localhost:11434"
    payload = _http_json_get(urljoin(f'{base_url.rstrip("/")}/', "api/tags"))
    return [
        ProviderModelInfo(
            id=item.get("model") or item.get("name"),
            label=item.get("name") or item.get("model"),
            owned_by="ollama",
        ).model_dump()
        for item in payload.get("models", [])
        if item.get("model") or item.get("name")
    ]


def _dashscope_catalog_models(provider_meta: dict) -> list[dict]:
    return [_build_model_item(model_id, "dashscope") for model_id in provider_meta.get("model_catalog", [])]


def list_provider_models(db: Session, provider: str) -> dict:
    provider_meta, config = _get_provider_config(db, provider)
    fallback_models = [_build_model_item(config["default_model"], provider)]
    supports_fetch = provider_meta.get("supports_model_fetch", True)
    model_catalog = provider_meta.get("model_catalog", [])
    key = _cache_key(config)
    cached_payload = _read_cached_provider_models(key)
    if cached_payload is not None:
        return cached_payload

    if not supports_fetch and model_catalog:
        payload = {
            "provider": provider,
            "source": "catalog",
            "warning": "Live model fetch not supported by this provider; showing the built-in model list.",
            "models": _sort_models(
                [_build_model_item(m, provider) for m in model_catalog]
            ),
        }
        _write_cached_provider_models(key, payload)
        return payload

    if provider != "ollama" and not config["api_key"]:
        payload = {
            "provider": provider,
            "source": "fallback",
            "warning": "API key is not configured for this provider; showing the default configured model only.",
            "models": fallback_models,
        }
        _write_cached_provider_models(key, payload)
        return payload

    fetchers = {
        "openai": _fetch_openai_models,
        "deepseek": _fetch_deepseek_models,
        "anthropic": _fetch_anthropic_models,
        "ollama": _fetch_ollama_models,
    }

    fetcher = fetchers.get(provider)
    if fetcher is None:
        raise ValueError(f"Unsupported provider: {provider}")

    models = _sort_models(fetcher(config))
    if not models:
        payload = {
            "provider": provider,
            "source": "fallback",
            "warning": "Provider returned no models; showing the default configured model only.",
            "models": fallback_models,
        }
        _write_cached_provider_models(key, payload)
        return payload
    payload = {
        "provider": provider,
        "source": "live",
        "warning": None,
        "models": models,
    }
    _write_cached_provider_models(key, payload)
    return payload
