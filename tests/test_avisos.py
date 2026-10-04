"""enviar_aviso: la única salida del servidor. Solo decide el texto, nunca el destinatario."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client
from fastmcp.exceptions import ToolError

from inboxbridge.app import crear_servidor
from inboxbridge.cuentas import RepoCuentas
from tests.conftest import crear_settings

WEBHOOK = "https://n8n.example.com/webhook/aviso"


@pytest.fixture
def webhook() -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield router.post(WEBHOOK).mock(return_value=httpx.Response(200, json={"ok": True}))


async def cliente(**cambios: object) -> Client:
    ajustes = {"avisos_webhook_url": WEBHOOK, "avisos_webhook_token": "secreto"} | cambios
    return Client(crear_servidor(crear_settings(**ajustes)))


@pytest.fixture
async def mcp_avisos(repo: RepoCuentas, webhook: Any) -> AsyncIterator[Client]:
    async with await cliente() as c:
        yield c


async def test_envia_al_webhook_fijo_con_el_token(mcp_avisos: Client, webhook: Any) -> None:
    r = await mcp_avisos.call_tool("enviar_aviso", {"mensaje": "🔴 1 urgente\x07: prueba técnica"})

    assert r.data == "Aviso enviado."
    [llamada] = webhook.calls
    assert llamada.request.headers["X-Inboxbridge-Token"] == "secreto"
    # Los caracteres de control se quitan antes de enviar.
    assert json.loads(llamada.request.content) == {"mensaje": "🔴 1 urgente: prueba técnica"}


async def test_no_deja_elegir_destinatario(mcp_avisos: Client) -> None:
    [herramienta] = [h for h in await mcp_avisos.list_tools() if h.name == "enviar_aviso"]
    assert set(herramienta.input_schema["properties"]) == {"mensaje"}


async def test_limite_diario(repo: RepoCuentas, webhook: Any) -> None:
    async with await cliente(avisos_max_diarios=2) as c:
        await c.call_tool("enviar_aviso", {"mensaje": "uno"})
        await c.call_tool("enviar_aviso", {"mensaje": "dos"})
        with pytest.raises(ToolError, match="Ya se enviaron 2 avisos"):
            await c.call_tool("enviar_aviso", {"mensaje": "tres"})
    assert webhook.call_count == 2


@pytest.mark.parametrize(
    ("mensaje", "error"), [("  \n ", "vacío"), ("x" * 3001, "supera 3000 caracteres")]
)
async def test_rechaza_mensajes_invalidos(
    mcp_avisos: Client, webhook: Any, mensaje: str, error: str
) -> None:
    with pytest.raises(ToolError, match=error):
        await mcp_avisos.call_tool("enviar_aviso", {"mensaje": mensaje})
    assert webhook.call_count == 0


async def test_error_del_canal(mcp_avisos: Client, webhook: Any) -> None:
    webhook.mock(return_value=httpx.Response(500))
    with pytest.raises(ToolError, match="respondió 500"):
        await mcp_avisos.call_tool("enviar_aviso", {"mensaje": "hola"})


def test_sin_url_no_hay_herramienta() -> None:
    assert not crear_settings().avisos_activos


@pytest.mark.parametrize(
    ("cambios", "error"),
    [
        ({"avisos_webhook_url": "http://n8n.example.com/x", "avisos_webhook_token": "t"}, "https"),
        ({"avisos_webhook_url": WEBHOOK}, "AVISOS_WEBHOOK_TOKEN"),
    ],
)
def test_configuracion_invalida(cambios: dict[str, object], error: str) -> None:
    with pytest.raises(ValueError, match=error):
        crear_settings(**cambios)
