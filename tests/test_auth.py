"""La capa A solo deja pasar a los dueños, aunque Google valide a cualquiera."""

from typing import Any

import pytest
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.google import GoogleProvider
from key_value.aio.stores.memory import MemoryStore

from inboxbridge.auth import GoogleSoloDuenos


def proveedor() -> GoogleSoloDuenos:
    return GoogleSoloDuenos(
        duenos=frozenset({"dueno@example.com"}),
        client_id="cliente.apps.googleusercontent.com",
        client_secret="GOCSPX-secreto",
        base_url="http://localhost:8000",
        client_storage=MemoryStore(),
        jwt_signing_key="x" * 32,
    )


@pytest.mark.parametrize(
    ("claims", "pasa"),
    [
        ({"email": "dueno@example.com", "email_verified": "true"}, True),
        ({"email": "DUENO@example.com", "email_verified": True}, True),
        ({"email": "dueno@example.com", "email_verified": "false"}, False),
        ({"email": "intruso@gmail.com", "email_verified": "true"}, False),
        ({}, False),
    ],
)
async def test_filtra_por_dueno(
    monkeypatch: pytest.MonkeyPatch, claims: dict[str, Any], pasa: bool
) -> None:
    async def validado(_self: GoogleProvider, token: str) -> AccessToken:
        return AccessToken(token=token, client_id="c", scopes=["openid"], claims=claims)

    monkeypatch.setattr(GoogleProvider, "load_access_token", validado)
    resultado = await proveedor().load_access_token("jwt")
    assert (resultado is not None) is pasa


async def test_token_invalido_sigue_siendo_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    async def invalido(_self: GoogleProvider, _token: str) -> None:
        return None

    monkeypatch.setattr(GoogleProvider, "load_access_token", invalido)
    assert await proveedor().load_access_token("jwt") is None
