"""Configuración leída del entorno (.env en local, variables del contenedor en el VPS)."""

from typing import Self

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    base_url: str = "http://localhost:8000"
    google_client_id: str
    google_client_secret: SecretStr
    database_url: SecretStr
    # Correos de Google que pueden entrar (a Claude y a /cuentas), separados por coma.
    owner_emails: str
    # Secreto del que se derivan todas las llaves (ver crypto.derivar_llave).
    master_key: SecretStr = Field(min_length=32)
    zona_horaria: str = "America/Bogota"
    # Solo para desarrollo local: deja /mcp sin OAuth.
    auth_enabled: bool = True

    @field_validator("base_url")
    @classmethod
    def _sin_barra_final(cls, valor: str) -> str:
        return valor.rstrip("/")

    @model_validator(mode="after")
    def _validar(self) -> Self:
        if not self.duenos:
            raise ValueError("OWNER_EMAILS no puede estar vacío")
        if not self.auth_enabled and self.es_https:
            raise ValueError("AUTH_ENABLED=false solo se permite en local (BASE_URL http)")
        return self

    @property
    def duenos(self) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in self.owner_emails.split(",") if e.strip())

    @property
    def es_https(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def google_redirect_uri(self) -> str:
        return f"{self.base_url}/google/callback"
