from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "sqlite:///./llmops.db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    openai_api_key: str = ""
    default_model: str = "gpt-4o-mini"
    default_concurrency: int = 5
    cors_origins: str = "*"

    # Trace 事件大数据管道：Kafka 削峰 + ClickHouse OLAP。
    # 留空 = 禁用（本地裸跑无 Kafka/CH 时自动走 SQLite，零影响）。
    kafka_bootstrap_servers: str = ""
    kafka_topic_traces: str = "llmops.trace.events"
    clickhouse_url: str = ""
    clickhouse_db: str = "llmops"
    clickhouse_user: str = "llmops"
    clickhouse_password: str = "llmops"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
