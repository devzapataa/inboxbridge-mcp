"""Comandos por WhatsApp (hecho 12, posponer 12 lunes) y la API que usa n8n."""

import asyncio
from collections.abc import Iterator
from datetime import date

import asyncpg
import pytest
from starlette.testclient import TestClient

from inboxbridge.app import crear_app
from inboxbridge.comandos import Comando, interpretar
from tests.conftest import crear_settings
from tests.google_falso import GoogleFalso

JUEVES = date(2026, 10, 8)
TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("lista", Comando("lista")),
        ("Hecho #12", Comando("hecho", (12,))),
        ("listo 12, 13 12", Comando("hecho", (12, 13))),
        ("descartar 7", Comando("descartar", (7,))),
        ("posponer 12", Comando("posponer", (12,), date(2026, 10, 9))),
        ("posponer 12 mañana", Comando("posponer", (12,), date(2026, 10, 9))),
        ("posponer #12 lunes", Comando("posponer", (12,), date(2026, 10, 12))),
        ("posponer 12 jueves", Comando("posponer", (12,), date(2026, 10, 15))),
        ("recordar 12 3d", Comando("posponer", (12,), date(2026, 10, 11))),
        ("posponer 12 15/10", Comando("posponer", (12,), date(2026, 10, 15))),
        ("posponer 12 1/10", Comando("posponer", (12,), date(2027, 10, 1))),
        ("posponer 12 31/02", None),
        ("hola", None),
    ],
)
def test_interpretar(texto: str, esperado: Comando | None) -> None:
    assert interpretar(texto, JUEVES) == esperado


@pytest.fixture
def api(db_limpia: str, google: GoogleFalso) -> Iterator[TestClient]:
    async def sembrar() -> None:
        con = await asyncpg.connect(db_limpia)
        try:
            await con.execute(
                "INSERT INTO seguimientos (cuenta, hilo_id, titulo) VALUES"
                " ('personal', 'aaa111', 'Prueba Acme'), ('trabajo', 'bbb222', 'Entrevista Globex')"
            )
        finally:
            await con.close()

    asyncio.run(sembrar())
    with TestClient(crear_app(crear_settings(api_token=TOKEN))) as cliente:
        yield cliente


def comando(api: TestClient, texto: str) -> str:
    r = api.post("/api/comandos", json={"texto": texto}, headers=AUTH)
    assert r.status_code == 200
    return r.json()["respuesta"]


def test_hecho_y_lista(api: TestClient) -> None:
    assert comando(api, "hecho 1") == "✅ #1 hecho: Prueba Acme"
    assert comando(api, "lista") == "#2 Entrevista Globex"
    assert comando(api, "hecho 1 9") == "✅ #1 hecho: Prueba Acme\nNo existe el seguimiento #9."


def test_posponer_aparece_en_la_lista(api: TestClient) -> None:
    assert comando(api, "posponer 2 3d").startswith("⏰ #2 vuelve el ")
    assert "pospuesto hasta" in comando(api, "lista")


def test_no_entendi_muestra_la_ayuda(api: TestClient) -> None:
    assert comando(api, "qué onda").startswith("No entendí. Comandos:")


def test_estado_para_la_alarma(api: TestClient) -> None:
    r = api.get("/api/estado", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == {"ultimo_aviso": None, "cuentas": 0, "por_reconectar": []}


def test_token_obligatorio(api: TestClient) -> None:
    assert api.get("/api/estado").status_code == 401
    assert api.post("/api/comandos", json={"texto": "lista"}).status_code == 401
    assert api.post("/api/comandos", json={"x": 1}, headers=AUTH).status_code == 400


def test_sin_token_configurado_no_hay_api(db_limpia: str, google: GoogleFalso) -> None:
    with TestClient(crear_app(crear_settings())) as cliente:
        assert cliente.get("/api/estado", headers=AUTH).status_code == 404
