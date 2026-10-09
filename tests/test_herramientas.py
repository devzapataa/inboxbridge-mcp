"""Herramientas MCP de punta a punta: cliente MCP en memoria, Postgres real y Google simulado."""

import base64
import email
from email import policy
from typing import Any

import asyncpg
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from inboxbridge.app import crear_servidor
from inboxbridge.cuentas import RepoCuentas
from tests.conftest import crear_settings
from tests.google_falso import GoogleFalso, mensaje

MARTES = 1_791_000_000_000  # ms
MIERCOLES = MARTES + 86_400_000


@pytest.fixture
async def mcp_cliente(repo: RepoCuentas, google: GoogleFalso) -> Any:
    async with Client(crear_servidor(crear_settings())) as cliente:
        yield cliente


async def conectar(repo: RepoCuentas, alias: str, email_: str) -> None:
    await repo.guardar(email=email_, alias=alias, refresh_token=f"rt-{alias}", scopes="gmail")


async def preparar_dos_cuentas(repo: RepoCuentas, google: GoogleFalso) -> None:
    await conectar(repo, "personal", "yo@gmail.com")
    await conectar(repo, "trabajo", "yo@empresa.com")
    google.buzones["rt-personal"] = {
        "aaa111": [
            mensaje(
                "m1",
                de="Banco <alertas@banco.com>",
                asunto="Pago recibido",
                fecha_ms=MARTES,
                texto="Recibiste $100.000",
                no_leido=True,
            )
        ],
    }
    google.buzones["rt-trabajo"] = {
        "bbb222": [
            mensaje(
                "m2",
                de="RRHH <rrhh@empresa.com>",
                asunto="Entrevista",
                fecha_ms=MIERCOLES,
                texto="¿Puedes el jueves?",
            )
        ],
    }


async def test_busca_en_todas_las_cuentas_y_ordena_por_fecha(
    mcp_cliente: Client, repo: RepoCuentas, google: GoogleFalso
) -> None:
    await preparar_dos_cuentas(repo, google)

    r = await mcp_cliente.call_tool("buscar_correos", {"consulta": "newer_than:7d"})

    datos = r.structured_content
    assert datos is not None
    assert [(h["cuenta"], h["asunto"]) for h in datos["resultados"]] == [
        ("trabajo", "Entrevista"),
        ("personal", "Pago recibido"),
    ]
    assert datos["resultados"][1]["no_leido"] is True
    assert datos["resultados"][1]["fragmento"] == "Fragmento de Pago recibido & más"
    assert datos["errores"] == []


async def test_reutiliza_el_access_token(
    mcp_cliente: Client, repo: RepoCuentas, google: GoogleFalso
) -> None:
    await preparar_dos_cuentas(repo, google)
    for _ in range(3):
        await mcp_cliente.call_tool("buscar_correos", {"consulta": "x", "cuenta": "personal"})
    assert google.refrescos == 1


async def test_una_cuenta_revocada_no_tumba_la_busqueda(
    mcp_cliente: Client, repo: RepoCuentas, google: GoogleFalso
) -> None:
    await preparar_dos_cuentas(repo, google)
    google.revocados.add("rt-trabajo")

    r = await mcp_cliente.call_tool("buscar_correos", {"consulta": "x"})

    datos = r.structured_content
    assert datos is not None
    assert [h["cuenta"] for h in datos["resultados"]] == ["personal"]
    [error] = datos["errores"]
    assert error["cuenta"] == "trabajo"
    assert "Vuelve a conectarla" in error["error"]
    assert (await repo.resolver("trabajo")).estado == "reconectar"


async def test_lee_un_hilo(mcp_cliente: Client, repo: RepoCuentas, google: GoogleFalso) -> None:
    await conectar(repo, "personal", "yo@gmail.com")
    google.buzones["rt-personal"] = {
        "ccc333": [
            mensaje(
                "m3",
                de="Ana <ana@example.com>",
                asunto="Contrato",
                fecha_ms=MARTES,
                html="<p>Adjunto el <b>contrato</b></p>",
                adjunto="contrato.pdf",
            )
        ],
    }

    r = await mcp_cliente.call_tool("leer_hilo", {"cuenta": "YO@gmail.com", "hilo_id": "ccc333"})

    hilo = r.structured_content
    assert hilo is not None
    assert hilo["asunto"] == "Contrato"
    assert hilo["enlace"] == "https://mail.google.com/mail/u/yo%40gmail.com/#all/ccc333"
    [msg] = hilo["mensajes"]
    assert msg["cuerpo"] == "Adjunto el contrato"
    assert msg["adjuntos"][0]["nombre"] == "contrato.pdf"
    assert msg["fecha"].endswith("-05:00")  # America/Bogota


async def test_rechaza_ids_que_no_son_de_gmail(mcp_cliente: Client, repo: RepoCuentas) -> None:
    await conectar(repo, "personal", "yo@gmail.com")
    with pytest.raises(ToolError, match="no es un id de hilo"):
        await mcp_cliente.call_tool("leer_hilo", {"cuenta": "personal", "hilo_id": "../settings"})


async def test_cuenta_desconocida_dice_cuales_hay(mcp_cliente: Client, repo: RepoCuentas) -> None:
    await conectar(repo, "personal", "yo@gmail.com")
    with pytest.raises(ToolError, match="Cuentas conectadas: personal"):
        await mcp_cliente.call_tool("leer_hilo", {"cuenta": "otra", "hilo_id": "aaa111"})


async def test_borrador_en_respuesta_a_un_hilo(
    mcp_cliente: Client, repo: RepoCuentas, google: GoogleFalso
) -> None:
    await conectar(repo, "trabajo", "yo@empresa.com")
    google.buzones["rt-trabajo"] = {
        "bbb222": [
            mensaje(
                "m1",
                de="yo@empresa.com",
                asunto="Entrevista",
                fecha_ms=MARTES,
                texto="x",
                cabeceras={"Message-ID": "<m1@empresa.com>"},
            ),
            mensaje(
                "m2",
                de="RRHH <rrhh@empresa.com>",
                asunto="Re: Entrevista",
                fecha_ms=MIERCOLES,
                texto="¿El jueves?",
                cabeceras={
                    "Message-ID": "<m2@empresa.com>",
                    "References": "<m1@empresa.com>",
                    "Reply-To": "agenda@empresa.com",
                },
            ),
        ],
    }

    r = await mcp_cliente.call_tool(
        "crear_borrador", {"cuenta": "trabajo", "hilo_id": "bbb222", "cuerpo": "Sí, el jueves."}
    )

    creado = r.structured_content
    assert creado is not None
    assert (creado["para"], creado["asunto"]) == ("agenda@empresa.com", "Re: Entrevista")
    [enviado] = google.borradores
    assert enviado["message"]["threadId"] == "bbb222"
    msg = email.message_from_bytes(
        base64.urlsafe_b64decode(enviado["message"]["raw"]), policy=policy.default
    )
    assert msg["From"] == "yo@empresa.com"
    assert msg["In-Reply-To"] == "<m2@empresa.com>"
    assert msg["References"] == "<m1@empresa.com> <m2@empresa.com>"


async def test_no_hay_herramientas_para_enviar_ni_borrar(mcp_cliente: Client) -> None:
    nombres = {h.name for h in await mcp_cliente.list_tools()}
    assert nombres == {
        "listar_cuentas",
        "buscar_correos",
        "leer_hilo",
        "leer_adjunto",
        "crear_borrador",
        "conectar_cuenta",
        "listar_seguimientos",
        "registrar_seguimiento",
        "actualizar_seguimiento",
    }


async def test_auditoria_sin_contenido(
    mcp_cliente: Client, repo: RepoCuentas, google: GoogleFalso, db_limpia: str
) -> None:
    await preparar_dos_cuentas(repo, google)
    await mcp_cliente.call_tool("buscar_correos", {"consulta": "secreto", "cuenta": "personal"})

    con = await asyncpg.connect(db_limpia)
    try:
        filas = await con.fetch("SELECT herramienta, cuenta, resultado FROM auditoria")
    finally:
        await con.close()
    assert [tuple(f) for f in filas] == [("buscar_correos", "personal", "ok")]
