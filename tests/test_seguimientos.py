"""Memoria de seguimientos: lo que las rutinas registran y el dueño marca como hecho."""

from datetime import date, timedelta
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from inboxbridge.app import crear_servidor
from inboxbridge.cuentas import RepoCuentas
from tests.conftest import crear_settings
from tests.google_falso import GoogleFalso


@pytest.fixture
async def mcp_cliente(repo: RepoCuentas, google: GoogleFalso) -> Any:
    await repo.guardar(email="yo@gmail.com", alias="personal", refresh_token="rt", scopes="g")
    async with Client(crear_servidor(crear_settings())) as cliente:
        yield cliente


async def registrar(cliente: Client, **extra: Any) -> dict[str, Any]:
    args = {"cuenta": "personal", "hilo_id": "aaa111", "titulo": "Prueba técnica Acme"} | extra
    r = await cliente.call_tool("registrar_seguimiento", args)
    assert r.structured_content is not None
    return r.structured_content


async def test_registra_con_numero_enlace_y_hora_de_colombia(mcp_cliente: Client) -> None:
    datos = await registrar(mcp_cliente, tipo="empleo", vence_en="2026-10-10T18:00:00")

    s = datos["seguimiento"]
    assert datos["nuevo"] is True
    assert (s["numero"], s["estado"], s["tipo"]) == (1, "pendiente", "empleo")
    assert s["vence_en"] == "2026-10-10T18:00:00-05:00"
    assert s["enlace"] == "https://mail.google.com/mail/u/yo%40gmail.com/#all/aaa111"


async def test_registrar_de_nuevo_no_reabre_lo_hecho(mcp_cliente: Client) -> None:
    await registrar(mcp_cliente)
    await mcp_cliente.call_tool("actualizar_seguimiento", {"numero": 1, "nuevo_estado": "hecho"})

    datos = await registrar(mcp_cliente, titulo="Prueba Acme (recordatorio)")

    assert datos["nuevo"] is False
    assert datos["seguimiento"]["estado"] == "hecho"
    assert datos["seguimiento"]["titulo"] == "Prueba Acme (recordatorio)"


async def test_listar_solo_activos_y_ocultar_pospuestos(mcp_cliente: Client) -> None:
    await registrar(mcp_cliente, hilo_id="aaa111", titulo="Uno")
    await registrar(mcp_cliente, hilo_id="bbb222", titulo="Dos")
    await registrar(mcp_cliente, hilo_id="ccc333", titulo="Tres")
    pasado_manana = (date.today() + timedelta(days=2)).isoformat()
    await mcp_cliente.call_tool("actualizar_seguimiento", {"numero": 1, "nuevo_estado": "hecho"})
    await mcp_cliente.call_tool(
        "actualizar_seguimiento", {"numero": 2, "recordar_desde": pasado_manana}
    )

    async def titulos(**args: Any) -> list[str]:
        r = await mcp_cliente.call_tool("listar_seguimientos", args)
        assert r.structured_content is not None
        return [s["titulo"] for s in r.structured_content["seguimientos"]]

    assert await titulos() == ["Dos", "Tres"]
    assert await titulos(ocultar_pospuestos=True) == ["Tres"]
    assert await titulos(incluir_cerrados=True) == ["Uno", "Dos", "Tres"]


async def test_guarda_el_evento_y_el_borrador(mcp_cliente: Client) -> None:
    await registrar(mcp_cliente)
    r = await mcp_cliente.call_tool(
        "actualizar_seguimiento",
        {"numero": 1, "nuevo_estado": "avisado", "evento_id": "ev1", "borrador_id": "r-1"},
    )
    s = r.structured_content
    assert s is not None
    assert (s["estado"], s["evento_id"], s["borrador_id"]) == ("avisado", "ev1", "r-1")


async def test_errores_claros(mcp_cliente: Client) -> None:
    with pytest.raises(ToolError, match="No existe el seguimiento #99"):
        await mcp_cliente.call_tool(
            "actualizar_seguimiento", {"numero": 99, "nuevo_estado": "hecho"}
        )
    with pytest.raises(ToolError, match="no es un id de hilo"):
        await registrar(mcp_cliente, hilo_id="../x")
    with pytest.raises(ToolError, match="Cuentas conectadas: personal"):
        await registrar(mcp_cliente, cuenta="otra")
