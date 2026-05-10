from pydantic import BaseModel
from datetime import datetime


class VersionCreate(BaseModel):
    version_type: str
    name: str
    config_json: dict
    description: str | None = None


class VersionUpdate(BaseModel):
    name: str | None = None
    config_json: dict | None = None
    description: str | None = None


class VersionOut(BaseModel):
    id: int
    version_type: str
    name: str
    config_json: dict
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class VersionListOut(BaseModel):
    items: list[VersionOut]
    total: int
