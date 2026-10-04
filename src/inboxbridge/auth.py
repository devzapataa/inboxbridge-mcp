"""Capa A: OAuth 2.1 entre Claude y este servidor.

FastMCP hace de servidor de autorización (CIMD, DCR, PKCE, rotación de tokens) y usa
Google para saber quién eres. Encima de eso solo dejamos pasar los correos de OWNER_EMAILS.
"""

import logging

from cryptography.fernet import Fernet
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.provider import AccessToken

from inboxbridge.config import Settings
from inboxbridge.crypto import derivar_llave, llave_fernet

logger = logging.getLogger(__name__)

# claude.ai (web, escritorio, móvil) usa la primera; Claude Code y el MCP Inspector,
# un puerto local que cambia en cada sesión.
REDIRECCIONES_PERMITIDAS = [
    "https://claude.ai/api/mcp/auth_callback",
    "http://localhost:*",
    "http://127.0.0.1:*",
]


class GoogleSoloDuenos(GoogleProvider):
    """GoogleProvider que rechaza cualquier cuenta de Google fuera de la lista de dueños."""

    def __init__(self, *, duenos: frozenset[str], **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._duenos = duenos

    async def load_access_token(self, token: str) -> AccessToken | None:  # type: ignore[override]
        acceso = await super().load_access_token(token)
        if acceso is None:
            return None
        # El proxy anota el tipo del SDK, pero devuelve el de FastMCP, con los claims de Google.
        claims = getattr(acceso, "claims", None) or {}
        email = str(claims.get("email") or "").lower()
        verificado = str(claims.get("email_verified")).lower() == "true"
        if verificado and email in self._duenos:
            return acceso
        logger.warning("Acceso a /mcp rechazado: la cuenta no está en OWNER_EMAILS")
        return None


def crear_auth(settings: Settings) -> GoogleSoloDuenos:
    maestra = settings.master_key.get_secret_value()
    # Clientes registrados y tokens de FastMCP, cifrados, en la misma base de Postgres.
    almacen = FernetEncryptionWrapper(
        PostgreSQLStore(url=settings.database_url.get_secret_value(), table_name="oauth_kv"),
        fernet=Fernet(llave_fernet(maestra, "oauth-proxy")),
    )
    return GoogleSoloDuenos(
        duenos=settings.duenos,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        base_url=settings.base_url,
        required_scopes=["openid", "email"],
        allowed_client_redirect_uris=REDIRECCIONES_PERMITIDAS,
        client_storage=almacen,
        jwt_signing_key=derivar_llave(maestra, "jwt-fastmcp"),
    )
