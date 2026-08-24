"""Configuración central. Todos los valores provienen del entorno o de un fichero .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ajustes de la aplicación.

    El prefijo de entorno es vacío para conservar nombres convencionales
    (por ejemplo ``DATABASE_URL``), tal como los esperan los proveedores cloud.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ general
    app_name: str = "Chatbox Orchestrator"
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"

    # ----------------------------------------------------------------- postgres
    database_url: str = Field(
        default="postgresql+asyncpg://chatbox:chatbox@localhost:5432/chatbox",
        description="DSN de SQLAlchemy. Debe usar el driver asyncpg.",
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False
    db_statement_timeout_ms: int = 15_000

    # ----------------------------------------------------------------- WhatsApp
    whatsapp_verify_token: SecretStr | None = None
    whatsapp_app_secret: SecretStr | None = None
    whatsapp_access_token: SecretStr | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_api_version: str = "v21.0"
    whatsapp_api_base: str = "https://graph.facebook.com"

    # ----------------------------------------- Microsoft Bot Framework (Teams…)
    microsoft_app_id: str | None = None
    microsoft_app_password: SecretStr | None = None
    microsoft_app_tenant_id: str | None = None
    microsoft_app_type: Literal["MultiTenant", "SingleTenant"] = "MultiTenant"
    microsoft_validate_jwt: bool = True

    # ----------------------------------------------------------------------- IA
    anthropic_api_key: SecretStr | None = None
    ai_model: str = "claude-opus-5"
    ai_max_tokens: int = 4_096
    ai_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    ai_history_turns: int = 12
    ai_system_prompt: str = (
        "Eres el asistente virtual de atención al cliente. "
        "Responde de forma breve, cordial y precisa, en el idioma del usuario. "
        "Si no dispones de la información solicitada, indícalo con claridad y "
        "ofrece derivar la conversación a un agente humano."
    )

    # -------------------------------------------------------------- orquestador
    outbox_workers: int = 2
    outbox_poll_interval_s: float = 1.0
    outbox_max_attempts: int = 6
    inbound_rate_limit_per_minute: int = 30
    default_tenant_slug: str = "default"

    # ------------------------------------------------------------------ consola
    admin_api_key: SecretStr | None = None
    cors_allow_origins: str = "*"
    #: Marca la cookie de sesión como ``Secure``. En una red local sin TLS debe
    #: quedar en falso, pues el navegador descartaría la cookie sobre HTTP.
    session_cookie_secure: bool = False
    #: Correo del supervisor creado por ``scripts/create_agent.py`` sin argumentos.
    bootstrap_supervisor_email: str | None = None

    # ------------------------------------------------------------------ adjuntos
    #: Directorio donde se guardan las imágenes subidas por el chatbox y la
    #: consola. Relativo al directorio de trabajo del proceso.
    uploads_dir: str = "uploads"
    upload_max_bytes: int = 8_388_608

    @model_validator(mode="before")
    @classmethod
    def _treat_blank_as_unset(cls, data: Any) -> Any:
        """Descarta los valores vacíos para que actúe el valor por omisión.

        Quien copia ``.env.example`` deja las claves sensibles en blanco. Sin
        esta normalización, ``ADMIN_API_KEY=`` produciría ``SecretStr("")`` en
        lugar de ``None``: el servicio pasaría a exigir una clave de API igual a
        la cadena vacía, es decir, quedaría bloqueado y con una credencial
        trivial a la vez. Lo mismo afecta a los secretos de los canales, que se
        usarían para firmar con una clave vacía.
        """
        if isinstance(data, dict):
            return {
                key: value
                for key, value in data.items()
                if not (isinstance(value, str) and not value.strip())
            }
        return data

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            # Comodidad: los proveedores cloud publican el DSN sincrónico.
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    """Devuelve la instancia única de configuración."""
    return Settings()
