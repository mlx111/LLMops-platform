from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.apikey import APIKey, _encode
from app.services.provider_models import list_provider_models
from app.services.url_safety import validate_target_url
from app.schemas.apikey import (
    APIKeyCreate, APIKeyOut, ProviderInfo, ProviderModelListOut, SUPPORTED_PROVIDERS,
)

router = APIRouter(prefix="/api", tags=["config"])


@router.get("/config/providers", response_model=list[ProviderInfo])
def list_providers(db: Session = Depends(get_db)):
    """List all supported model providers and which ones are configured."""
    configured = {k.provider: k for k in db.query(APIKey).all()}
    result = []
    for p in SUPPORTED_PROVIDERS:
        result.append(ProviderInfo(
            id=p["id"],
            name=p["name"],
            default_model=p["default_model"],
            base_url=p["base_url"],
            env_key=p["env_key"],
            configured=p["id"] in configured or p["env_key"] is None,
            requires_api_key=p.get("requires_api_key", True),
            requires_model=p.get("requires_model", True),
            requires_base_url=p.get("requires_base_url", False),
            supports_model_fetch=p.get("supports_model_fetch", True),
        ))
    return result


@router.get("/config/apikeys", response_model=list[APIKeyOut])
def list_api_keys(db: Session = Depends(get_db)):
    """List configured API keys (masked)."""
    keys = db.query(APIKey).all()
    return [
        APIKeyOut(
            id=k.id,
            provider=k.provider,
            api_key_masked=k.mask_key(),
            base_url=k.base_url,
            default_model=k.default_model,
            created_at=k.created_at,
            updated_at=k.updated_at,
        )
        for k in keys
    ]


@router.get("/config/providers/{provider}/models", response_model=ProviderModelListOut)
def get_provider_models(provider: str, db: Session = Depends(get_db)):
    try:
        return list_provider_models(db, provider)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/config/apikeys", response_model=APIKeyOut)
def set_api_key(body: APIKeyCreate, db: Session = Depends(get_db)):
    """Create or update an API key for a provider."""
    if body.base_url:
        validate_target_url(body.base_url)
    existing = db.query(APIKey).filter(APIKey.provider == body.provider).first()
    if existing:
        existing.api_key = _encode(body.api_key)
        existing.base_url = body.base_url
        existing.default_model = body.default_model
        db.commit()
        db.refresh(existing)
        return APIKeyOut(
            id=existing.id,
            provider=existing.provider,
            api_key_masked=existing.mask_key(),
            base_url=existing.base_url,
            default_model=existing.default_model,
            created_at=existing.created_at,
            updated_at=existing.updated_at,
        )

    key = APIKey(
        provider=body.provider,
        api_key=_encode(body.api_key),
        base_url=body.base_url,
        default_model=body.default_model,
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    return APIKeyOut(
        id=key.id,
        provider=key.provider,
        api_key_masked=key.mask_key(),
        base_url=key.base_url,
        default_model=key.default_model,
        created_at=key.created_at,
        updated_at=key.updated_at,
    )


@router.delete("/config/apikeys/{provider}")
def delete_api_key(provider: str, db: Session = Depends(get_db)):
    """Remove an API key."""
    key = db.query(APIKey).filter(APIKey.provider == provider).first()
    if not key:
        raise HTTPException(404, "API key not found")
    db.delete(key)
    db.commit()
    return {"ok": True}
