"""OAuth de Google para conectar cuentas de Gmail y para que el dueño entre a /cuentas.

Es la "capa B": el servidor frente a Google. La "capa A" (Claude frente al servidor)
la maneja FastMCP en auth.py.
"""

import base64
import contextlib
import hashlib
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from inboxbridge.errores import CuentaPorReconectar, ErrorGoogle

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (es una URL, no un secreto)
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

GMAIL_LECTURA = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_BORRADORES = "https://www.googleapis.com/auth/gmail.compose"

SCOPES_ENTRAR = ("openid", "email")
SCOPES_GMAIL = ("openid", "email", GMAIL_LECTURA, GMAIL_BORRADORES)


@dataclass(frozen=True)
class Tokens:
    access_token: str
    expires_in: int
    scope: str
    refresh_token: str | None = None


def nuevo_pkce() -> tuple[str, str]:
    """Devuelve (verifier, challenge S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class ClienteGoogle:
    def __init__(
        self, http: httpx.AsyncClient, *, client_id: str, client_secret: str, redirect_uri: str
    ) -> None:
        self._http = http
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri

    def url_autorizacion(
        self,
        *,
        scopes: tuple[str, ...],
        state: str,
        code_challenge: str,
        offline: bool,
        login_hint: str | None = None,
    ) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "consent select_account" if offline else "select_account",
        }
        if offline:
            # Sin esto Google no entrega refresh token.
            params["access_type"] = "offline"
        if login_hint:
            params["login_hint"] = login_hint
        return f"{AUTH_URL}?{urlencode(params)}"

    async def canjear_codigo(self, code: str, code_verifier: str) -> Tokens:
        return await self._pedir_token(
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": self._redirect_uri,
            }
        )

    async def refrescar(self, refresh_token: str) -> Tokens:
        return await self._pedir_token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token}
        )

    async def userinfo(self, access_token: str) -> dict[str, object]:
        r = await self._http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
        if r.status_code != 200:
            raise ErrorGoogle("Google no devolvió los datos de la cuenta.")
        return r.json()

    async def revocar(self, token: str) -> None:
        """Intenta revocar; si falla no importa, el token ya se borró de la base."""
        with contextlib.suppress(httpx.HTTPError):
            await self._http.post(REVOKE_URL, data={"token": token})

    async def _pedir_token(self, datos: dict[str, str]) -> Tokens:
        datos |= {"client_id": self._client_id, "client_secret": self._client_secret}
        try:
            r = await self._http.post(TOKEN_URL, data=datos)
        except httpx.HTTPError as e:
            raise ErrorGoogle("No pude comunicarme con Google.") from e
        cuerpo = (
            r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        )
        if r.status_code == 400 and cuerpo.get("error") == "invalid_grant":
            raise CuentaPorReconectar(
                "Google revocó el acceso a esta cuenta. Vuelve a conectarla en /cuentas."
            )
        if r.status_code != 200:
            raise ErrorGoogle(f"Google rechazó la solicitud de token ({r.status_code}).")
        return Tokens(
            access_token=cuerpo["access_token"],
            expires_in=int(cuerpo.get("expires_in", 3600)),
            scope=cuerpo.get("scope", ""),
            refresh_token=cuerpo.get("refresh_token"),
        )
