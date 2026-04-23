from dataclasses import dataclass
from functools import lru_cache
import os


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    app_host: str
    app_port: int
    app_name: str
    app_env: str
    db_host: str
    db_port: int
    db_name: str
    db_user: str
    db_password: str
    db_echo: bool
    db_init_attempts: int
    db_init_delay: float
    database_url_override: str | None
    rabbitmq_host: str
    rabbitmq_port: int
    rabbitmq_user: str
    rabbitmq_password: str
    rabbitmq_queue: str
    rabbitmq_connection_attempts: int
    rabbitmq_connection_delay: float
    worker_id: str

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+psycopg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_host=os.getenv("APP_HOST", "0.0.0.0"),
        app_port=int(os.getenv("APP_PORT", "8000")),
        app_name=os.getenv("APP_NAME", "ml-service-app"),
        app_env=os.getenv("APP_ENV", "development"),
        db_host=os.getenv("DB_HOST", "database"),
        db_port=int(os.getenv("DB_PORT", "5432")),
        db_name=os.getenv("DB_NAME", "ml_service"),
        db_user=os.getenv("DB_USER", "ml_user"),
        db_password=os.getenv("DB_PASSWORD", "ml_password"),
        db_echo=_to_bool(os.getenv("DB_ECHO"), default=False),
        db_init_attempts=int(os.getenv("DB_INIT_ATTEMPTS", "30")),
        db_init_delay=float(os.getenv("DB_INIT_DELAY", "1")),
        database_url_override=os.getenv("DATABASE_URL"),
        rabbitmq_host=os.getenv("RABBITMQ_HOST", "rabbitmq"),
        rabbitmq_port=int(os.getenv("RABBITMQ_PORT", "5672")),
        rabbitmq_user=os.getenv("RABBITMQ_USER", "ml_queue_user"),
        rabbitmq_password=os.getenv("RABBITMQ_PASSWORD", "ml_queue_password"),
        rabbitmq_queue=os.getenv("RABBITMQ_QUEUE", "ml_tasks"),
        rabbitmq_connection_attempts=int(os.getenv("RABBITMQ_CONNECTION_ATTEMPTS", "30")),
        rabbitmq_connection_delay=float(os.getenv("RABBITMQ_CONNECTION_DELAY", "1")),
        worker_id=os.getenv("WORKER_ID", "worker-default"),
    )
