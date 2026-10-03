"""Single source of truth for configuration, split per environment.

`APP_ENV` (sandbox | production | test) selects which `.env.<APP_ENV>` file is
layered underneath the real process environment. Real environment variables
always win, so containers can inject values without any file on disk.

Nothing sensitive has a default: a database password, JWT key or object-storage
credential that is missing from both the file and the environment stops the
process at import time instead of silently falling back to a guessable value.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL

Environment = Literal["sandbox", "production", "test"]
ENVIRONMENTS: tuple[str, ...] = ("sandbox", "production", "test")


class ConfigurationError(RuntimeError):
    """The environment is missing, unknown, or unsafe for the selected mode."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    app_env: Environment
    # Never True in production (enforced below). Controls FastAPI debug pages,
    # interactive API docs and the 500 handler.
    debug: bool = False

    # PostgreSQL. One database per environment; see the isolation check below.
    postgres_user: str
    postgres_password: SecretStr
    postgres_host: str
    postgres_port: int = 5432
    postgres_db: str
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_recycle: int = 1800

    # Redis / Celery
    celery_broker_url: str
    celery_result_backend: str
    celery_task_queue: str = "memory_batches"
    rollup_check_interval_seconds: int = 3600
    stream_flush_interval_seconds: int = 60
    memory_stream_maxlen: int = 10_000
    rollup_interval_override_seconds: int | None = None

    # Object storage (backups)
    minio_endpoint: str
    minio_access_key: SecretStr
    minio_secret_key: SecretStr
    minio_bucket: str

    # Auth
    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60
    allow_insecure_jwt_secret: bool = False

    # Application server
    api_host: str = "0.0.0.0"  # bound inside a container
    api_port: int = 8000
    api_workers: int = Field(default=2, ge=1)
    api_reload: bool = False
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def database_url(self) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @model_validator(mode="after")
    def _enforce_environment_isolation(self) -> Settings:
        db = self.postgres_db.lower()
        bucket = self.minio_bucket.lower()

        if self.is_production:
            if self.debug:
                raise ValueError("DEBUG must be false when APP_ENV=production")
            if self.api_reload:
                raise ValueError("API_RELOAD must be false when APP_ENV=production")
            if self.allow_insecure_jwt_secret:
                raise ValueError(
                    "ALLOW_INSECURE_JWT_SECRET is not permitted in production"
                )
            secrets = {
                "POSTGRES_PASSWORD": self.postgres_password,
                "MINIO_ACCESS_KEY": self.minio_access_key,
                "MINIO_SECRET_KEY": self.minio_secret_key,
                "JWT_SECRET_KEY": self.jwt_secret_key,
            }
            for label, secret in secrets.items():
                if "change_me" in secret.get_secret_value().lower():
                    raise ValueError(
                        f"{label} still holds the CHANGE_ME placeholder from the "
                        "example file"
                    )
            for label, name in (("POSTGRES_DB", db), ("MINIO_BUCKET", bucket)):
                if "sandbox" in name or "test" in name:
                    raise ValueError(
                        f"{label}={name!r} looks like a non-production resource; "
                        "production must use its own database and bucket"
                    )
        elif self.app_env == "sandbox":
            for label, name in (("POSTGRES_DB", db), ("MINIO_BUCKET", bucket)):
                if "prod" in name:
                    raise ValueError(
                        f"{label}={name!r} looks like a production resource; "
                        "sandbox must never point at production data"
                    )
        return self


def _env_file_candidates(app_env: str) -> list[Path]:
    name = f".env.{app_env}"
    return [Path.cwd() / name, Path.cwd() / "infra" / name]


@lru_cache
def get_settings() -> Settings:
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env not in ENVIRONMENTS:
        raise ConfigurationError(
            f"APP_ENV must be one of {', '.join(ENVIRONMENTS)}; got {app_env!r}. "
            "Copy infra/.env.sandbox.example or infra/.env.production.example "
            "and select it with APP_ENV."
        )

    explicit = os.environ.get("ENV_FILE")
    files = [Path(explicit)] if explicit else _env_file_candidates(app_env)
    existing = [str(path) for path in files if path.is_file()]

    return Settings(_env_file=existing or None)  # type: ignore[call-arg]


settings = get_settings()
