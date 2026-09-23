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
    # catalog_db: при старте применить миграции и заполнить ПУСТУЮ базу демо-каталогом.
    # Для базы с импортом из EKT ничего не меняется. false отключает автозаполнение.
    catalog_db_seed_demo: bool = True
    # Распознавание фото/сканов и смысловой поиск (участник 1). Провайдер openai берёт LLM_API_KEY и
    # LLM_DATA_POLICY_ACCEPTED, провайдер nvidia (build.nvidia.com) свои NVIDIA_*. Ключ сам ничего не включает.
    ai_services_provider: Literal["openai", "nvidia"] = "openai"
    nvidia_api_key: SecretStr = SecretStr("")
    nvidia_data_policy_accepted: bool = False
    ocr_enabled: bool = False
    ocr_model: str = ""  # пусто = по умолчанию для провайдера
    semantic_search_enabled: bool = False
    embed_model: str = ""  # пусто = по умолчанию для провайдера; должен совпадать с моделью индекса
    semantic_index_path: Path = ROOT.parent / "data" / "synthetic" / "embeddings.json"
    ai_site_daily_calls: int = Field(default=3000, ge=1)
    ai_session_daily_calls: int = Field(default=200, ge=1)
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
    worker_timeout_seconds: float = Field(default=75, ge=0.1, le=300)
    worker_lease_seconds: int = Field(default=120, ge=1)
    llm_enabled: bool = False
    llm_provider: Literal["openai"] = "openai"
    llm_model: str = "gpt-6-sol"
    llm_allowed_models: list[str] = ["gpt-6-sol"]
    llm_reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"] = "medium"
    llm_data_policy_accepted: bool = False
    llm_timeout_seconds: float = Field(default=45, ge=1, le=120)
    llm_max_output_tokens: int = Field(default=8192, ge=256, le=16384)
    llm_session_daily_tokens: int = Field(default=100000, ge=1000)
    llm_site_daily_tokens: int = Field(default=1000000, ge=1000)
    uploads_enabled: bool = False
    assets_root: Path = ROOT / "var" / "assets"
    scanner_command: list[str] = []
    max_upload_bytes: int = Field(default=10485760, ge=1024, le=10485760)
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
            raise ValueError(
                "INTEGRATION_MODE=catalog_db requires DATABASE_URL, e.g. "
                "postgresql://postgres:PASSWORD@127.0.0.1:5432/ekt. Without PostgreSQL set "
                "INTEGRATION_MODE=synthetic instead (no database needed). See data/db/README.md"
            )
        if self.ekt_live_refresh and not (self.ekt_api_user.get_secret_value()
                                          and self.ekt_api_password.get_secret_value()):
            raise ValueError("EKT_LIVE_REFRESH requires EKT_API_USER and EKT_API_PASSWORD")
        if self.app_env == "production" and self.documents_allow_unscanned:
            raise ValueError("Unscanned uploads are allowed only outside production")
        if (self.ocr_enabled or self.semantic_search_enabled) and not (self.ai_key() and self.ai_policy_accepted()):
            raise ValueError(
                "OCR/semantic search need an API key and data policy consent for AI_SERVICES_PROVIDER: "
                "openai -> LLM_API_KEY + LLM_DATA_POLICY_ACCEPTED=true, "
                "nvidia -> NVIDIA_API_KEY + NVIDIA_DATA_POLICY_ACCEPTED=true")
        if self.worker_lease_seconds <= self.worker_timeout_seconds:
            raise ValueError("Worker lease must exceed processing timeout")
        if self.app_env == "production" and not self.cookie_secure:
            raise ValueError("Production requires secure cookies")
        if self.app_env == "production" and any(
            not value.startswith("https://") for value in self.allowed_origins
        ):
            raise ValueError("Production origins must use HTTPS")
        if self.llm_enabled:
            if not self.llm_api_key.get_secret_value() or not self.llm_data_policy_accepted:
                raise ValueError("LLM requires a private API key and LLM_DATA_POLICY_ACCEPTED=true")
            if self.llm_model not in self.llm_allowed_models:
                raise ValueError("LLM_MODEL must be in LLM_ALLOWED_MODELS")
            if self.llm_timeout_seconds + 5 >= self.worker_timeout_seconds:
                raise ValueError("Worker timeout must cover the LLM deadline and local work")
        if self.uploads_enabled and not self.scanner_command:
            raise ValueError("Uploads require a configured malware scanner")
        return self

    # --- внешние ИИ-сервисы (распознавание, эмбеддинги) ---

    def ai_key(self) -> str:
        key = self.llm_api_key if self.ai_services_provider == "openai" else self.nvidia_api_key
        return key.get_secret_value()

    def ai_policy_accepted(self) -> bool:
        return self.llm_data_policy_accepted if self.ai_services_provider == "openai" else self.nvidia_data_policy_accepted

    def resolved_ocr_model(self) -> str:
        return self.ocr_model or {"openai": "gpt-4.1-mini", "nvidia": "google/gemma-3-12b-it"}[self.ai_services_provider]

    def resolved_embed_model(self) -> str:
        return self.embed_model or {"openai": "text-embedding-3-small",
                                    "nvidia": "nvidia/llama-3.2-nv-embedqa-1b-v1"}[self.ai_services_provider]
