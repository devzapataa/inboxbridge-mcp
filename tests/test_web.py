"""Flujo web: entrar como dueño, conectar y desconectar cuentas (Google simulado)."""

import asyncio
import re
from collections.abc import Iterator
from urllib.parse import parse_qs, urlparse

import asyncpg
import pytest
from starlette.testclient import TestClient

from inboxbridge.app import crear_app
from inboxbridge.google import GMAIL_BORRADORES, GMAIL_LECTURA
from tests.conftest import DUENO, crear_settings
from tests.google_falso import GoogleFalso

SCOPES_COMPLETOS = f"openid email {GMAIL_LECTURA} {GMAIL_BORRADORES}"


@pytest.fixture
def web(db_limpia: str, google: GoogleFalso) -> Iterator[TestClient]:
    with TestClient(crear_app(crear_settings()), follow_redirects=False) as cliente:
        yield cliente


def ir_a_google(respuesta) -> dict[str, str]:  # type: ignore[no-untyped-def]
    assert respuesta.status_code == 303
    url = urlparse(respuesta.headers["location"])
    assert url.netloc == "accounts.google.com"
    return {k: v[0] for k, v in parse_qs(url.query).items()}


def volver_de_google(
    web: TestClient,
    google: GoogleFalso,
    state: str,
    *,
    email: str,
    scope: str = "openid email",
    refresh: str | None = None,
):  # type: ignore[no-untyped-def]
    google.codigos["codigo"] = {"access_token": f"at-{email}", "scope": scope}
    if refresh:
        google.codigos["codigo"]["refresh_token"] = refresh
    google.usuarios[f"at-{email}"] = {"email": email, "email_verified": True}
    return web.get(f"/google/callback?state={state}&code=codigo")


def entrar(web: TestClient, google: GoogleFalso, email: str = DUENO):  # type: ignore[no-untyped-def]
    params = ir_a_google(web.get("/entrar"))
    assert params["scope"] == "openid email"
    return volver_de_google(web, google, params["state"], email=email)


def csrf(web: TestClient) -> str:
    pagina = web.get("/cuentas")
    assert pagina.status_code == 200
    m = re.search(r"name=csrf value='([^']+)'", pagina.text)
    assert m
    return m.group(1)


def test_el_duenio_entra(web: TestClient, google: GoogleFalso) -> None:
    assert web.get("/cuentas").headers["location"] == "/entrar"
    assert entrar(web, google).headers["location"] == "/cuentas"
    pagina = web.get("/cuentas")
    assert "Todavía no hay cuentas conectadas" in pagina.text
    assert "frame-ancestors 'none'" in pagina.headers["content-security-policy"]


def test_otra_cuenta_de_google_no_entra(web: TestClient, google: GoogleFalso) -> None:
    assert entrar(web, google, email="intruso@gmail.com").status_code == 403
    assert web.get("/cuentas").status_code == 303


def test_state_que_no_coincide(web: TestClient, google: GoogleFalso) -> None:
    ir_a_google(web.get("/entrar"))
    assert volver_de_google(web, google, "otro-state", email=DUENO).status_code == 400


def test_conectar_y_desconectar_una_cuenta(
    web: TestClient, google: GoogleFalso, db_limpia: str
) -> None:
    entrar(web, google)
    params = ir_a_google(
        web.post("/cuentas/conectar", data={"alias": "Trabajo", "csrf": csrf(web)})
    )
    assert params["access_type"] == "offline"
    assert set(params["scope"].split()) == set(SCOPES_COMPLETOS.split())
    assert params["code_challenge_method"] == "S256"

    r = volver_de_google(
        web,
        google,
        params["state"],
        email="yo@empresa.com",
        scope=SCOPES_COMPLETOS,
        refresh="1//refresh-secreto",
    )
    assert r.headers["location"] == "/cuentas"
    pagina = web.get("/cuentas").text
    assert "Conectada yo@empresa.com como «trabajo»" in pagina

    async def token_guardado() -> bytes:
        con = await asyncpg.connect(db_limpia)
        try:
            return await con.fetchval("SELECT refresh_token FROM cuentas")
        finally:
            await con.close()

    assert b"refresh-secreto" not in asyncio.run(token_guardado())

    r = web.post("/cuentas/1/desconectar", data={"csrf": csrf(web)})
    assert r.status_code == 303
    assert google.revocaciones == ["1//refresh-secreto"]
    assert "Todavía no hay cuentas conectadas" in web.get("/cuentas").text


def test_no_guarda_si_faltan_permisos_de_gmail(web: TestClient, google: GoogleFalso) -> None:
    entrar(web, google)
    params = ir_a_google(web.post("/cuentas/conectar", data={"alias": "b", "csrf": csrf(web)}))
    volver_de_google(
        web,
        google,
        params["state"],
        email="b@gmail.com",
        scope=f"openid email {GMAIL_LECTURA}",
        refresh="1//x",
    )
    pagina = web.get("/cuentas").text
    assert "marca las dos casillas de Gmail" in pagina
    assert "b@gmail.com" not in pagina


def test_formularios_sin_csrf(web: TestClient, google: GoogleFalso) -> None:
    entrar(web, google)
    assert web.post("/cuentas/conectar", data={"alias": "x"}).status_code == 403
    assert web.post("/cuentas/1/desconectar", data={"csrf": "falso"}).status_code == 403


def test_salud(web: TestClient) -> None:
    assert web.get("/salud").text == "ok"
