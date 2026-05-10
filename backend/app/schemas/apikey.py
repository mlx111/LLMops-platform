from pydantic import BaseModel
from datetime import datetime


# Supported providers with their default models and optional base URLs
SUPPORTED_PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "default_model": "deepseek-chat",
     "base_url": "https://api.deepseek.com", "env_key": "DEEPSEEK_API_KEY",
     "requires_api_key": True, "requires_model": True, "requires_base_url": False,
     "supports_model_fetch": True, "model_catalog": []},
    {"id": "openai", "name": "OpenAI", "default_model": "gpt-4o-mini",
     "base_url": "https://api.openai.com/v1", "env_key": "OPENAI_API_KEY",
     "requires_api_key": True, "requires_model": True, "requires_base_url": False,
     "supports_model_fetch": True, "model_catalog": []},
    {"id": "dashscope", "name": "DashScope (Qwen)", "default_model": "qwen-plus",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "env_key": "DASHSCOPE_API_KEY",
     "requires_api_key": True, "requires_model": True, "requires_base_url": False,
     "supports_model_fetch": False,
     "model_catalog": [
         "qwen3-max", "qwen3-max-preview", "qwen-max", "qwen-max-latest",
         "qwen3.6-plus", "qwen3.5-plus", "qwen-plus", "qwen-plus-latest",
         "qwen3.6-flash", "qwen3.5-flash", "qwen-flash", "qwen-turbo", "qwen-turbo-latest",
         "qwen3-coder-plus", "qwen3-coder-flash", "qwen-coder-plus", "qwen-coder-plus-latest",
         "qwen-coder-turbo", "qwen-coder-turbo-latest",
         "qwen-long", "qwen-long-latest",
         "qwq-plus", "qwq-plus-latest",
         "qwen-math-plus", "qwen-math-plus-latest", "qwen-math-turbo", "qwen-math-turbo-latest",
         "qwen3.6-35b-a3b", "qwen3.5-397b-a17b", "qwen3.5-122b-a10b", "qwen3.5-35b-a3b", "qwen3.5-27b",
         "qwen3-next-80b-a3b-thinking", "qwen3-next-80b-a3b-instruct",
         "qwen3-235b-a22b-thinking-2507", "qwen3-235b-a22b-instruct-2507", "qwen3-235b-a22b",
         "qwen3-32b", "qwen3-30b-a3b-thinking-2507", "qwen3-30b-a3b-instruct-2507", "qwen3-30b-a3b",
         "qwen3-14b", "qwen3-8b", "qwen3-4b", "qwen3-1.7b", "qwen3-0.6b",
         "qwq-32b", "qwq-32b-preview",
         "qwen2.5-72b-instruct", "qwen2.5-32b-instruct", "qwen2.5-14b-instruct", "qwen2.5-7b-instruct",
         "qwen2.5-14b-instruct-1m", "qwen2.5-7b-instruct-1m",
         "qwen2.5-math-72b-instruct", "qwen2.5-math-7b-instruct",
         "qwen2.5-coder-32b-instruct", "qwen2.5-coder-14b-instruct", "qwen2.5-coder-7b-instruct",
         "codeqwen1.5-7b-chat",
     ]},
    {"id": "anthropic", "name": "Anthropic Claude", "default_model": "claude-haiku-4-5-20251001",
     "base_url": None, "env_key": "ANTHROPIC_API_KEY",
     "requires_api_key": True, "requires_model": True, "requires_base_url": False,
     "supports_model_fetch": True, "model_catalog": []},
    {"id": "ollama", "name": "Ollama (Local)", "default_model": "qwen2.5:7b",
     "base_url": "http://localhost:11434", "env_key": None,
     "requires_api_key": False, "requires_model": True, "requires_base_url": False,
     "supports_model_fetch": True, "model_catalog": []},
]


class APIKeyCreate(BaseModel):
    provider: str
    api_key: str
    base_url: str | None = None
    default_model: str


class APIKeyUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    default_model: str | None = None


class APIKeyOut(BaseModel):
    id: int
    provider: str
    api_key_masked: str
    base_url: str | None
    default_model: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProviderInfo(BaseModel):
    id: str
    name: str
    default_model: str
    base_url: str | None
    env_key: str | None
    configured: bool
    requires_api_key: bool = True
    requires_model: bool = True
    requires_base_url: bool = False
    supports_model_fetch: bool = True


class ProviderModelInfo(BaseModel):
    id: str
    label: str
    owned_by: str | None = None


class ProviderModelListOut(BaseModel):
    provider: str
    source: str
    warning: str | None = None
    models: list[ProviderModelInfo]
