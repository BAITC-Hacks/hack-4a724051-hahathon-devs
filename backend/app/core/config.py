from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT.parent / ".env", extra="ignore", frozen=True,
    )

    app_env: Literal["development", "test", "production"] = "development"
    # catalog_db: каталог из PostgreSQL (импорт через app.catalog_cli), корзина пока демо в SQLite
    integration_mode: Literal["unavailable", "synthetic", "catalog_db"] = "unavailable"
    database_url: SecretStr = SecretStr("")
    # Перечитывать цену и остаток из EKT API перед корзиной. Нужны EKT_API_USER/PASSWORD.
    ekt_live_refresh: bool = False
    # Файлы клиентов (catalog_db). Без clamd разбор запрещён, кроме явного демо-флага.
    assets_dir: Path = ROOT / "var" / "assets"
    clamd_socket: str = ""
    documents_allow_unscanned: bool = False
    local_db_path: Path = ROOT / "var" / "participant2.sqlite3"
    allowed_origins: list[str] = [
        "http://localhost:3000", "http://127.0.0.1:3000",
        "http://localhost:8000", "http://127.0.0.1:8000",
    ]
    allowed_hosts: list[str] = ["localhost", "127.0.0.1", "testserver"]
    session_cookie: str = "ekt_session"
    cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=86400, ge=60, le=604800)
    max_body_bytes: int = Field(default=32768, ge=1024, le=1048576)
    requests_per_minute: int = Field(default=120, ge=1)
    max_pending_jobs: int = Field(default=100, ge=1)
    max_conversations: int = Field(default=20, ge=1)
    max_turns_per_session: int = Field(default=200, ge=1)
    worker_poll_seconds: float = Field(default=0.5, ge=0.1)
    worker_timeout_seconds: float = Field(default=25, ge=0.1, le=300)
    worker_lease_seconds: int = Field(default=60, ge=1)
    llm_enabled: bool = False
    llm_api_key: SecretStr = SecretStr("")
    ekt_api_user: SecretStr = SecretStr("")
    ekt_api_password: SecretStr = SecretStr("")

    @model_validator(mode="after")
    def validate_boundaries(self):
        if not self.allowed_origins or not self.allowed_hosts:
            raise ValueError("Explicit allowed origins and hosts are required")
        if any(not host or "*" in host or "://" in host or "/" in host
               for host in self.allowed_hosts):
            raise ValueError("Allowed hosts must be explicit hostnames")
        for origin in self.allowed_origins:
            parts = urlsplit(origin)
            if (parts.scheme not in {"http", "https"} or not parts.hostname
                    or parts.username or parts.password or parts.path
                    or parts.query or parts.fragment or "*" in origin):
                raise ValueError("Origins must be explicit scheme://host[:port]")
        if self.integration_mode == "catalog_db" and not self.database_url.get_secret_value():
            raise ValueError("INTEGRATION_MODE=catalog_db requires DATABASE_URL")
        if self.ekt_live_refresh and not (self.ekt_api_user.get_secret_value()
                                          and self.ekt_api_password.get_secret_value()):
            raise ValueError("EKT_LIVE_REFRESH requires EKT_API_USER and EKT_API_PASSWORD")
        if self.app_env == "production" and self.documents_allow_unscanned:
            raise ValueError("Unscanned uploads are allowed only outside production")
        if self.worker_lease_seconds <= self.worker_timeout_seconds:
            raise ValueError("Worker lease must exceed processing timeout")
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("Production requires secure cookies")
        if self.app_env == "production" and any(
            not value.startswith("https://") for value in self.allowed_origins
        ):
            raise ValueError("Production origins must use HTTPS")
        if self.llm_enabled:
            raise ValueError(
                "Paid LLM execution is not integrated yet; keep LLM_ENABLED=false"
            )
        return self
