"""API para n8n: el estado de las rutinas (para la alarma) y los comandos por WhatsApp.

Solo existe si hay API_TOKEN, y cada petición debe traer `Authorization: Bearer <token>`.
"""

import secrets
import time

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from inboxbridge import comandos
from inboxbridge.estado import Estado

_MAX_TEXTO = 500


def registrar_api(mcp: FastMCP, estado: Estado) -> None:
    if estado.settings.api_token is None:
        return
    esperado = f"Bearer {estado.settings.api_token.get_secret_value()}"

    def autorizado(request: Request) -> bool:
        return secrets.compare_digest(request.headers.get("authorization", ""), esperado)

    @mcp.custom_route("/api/estado", methods=["GET"], include_in_schema=False)
    async def api_estado(request: Request) -> Response:
        if not autorizado(request):
            return JSONResponse({"error": "no autorizado"}, status_code=401)
        ultimo = await estado.cuentas.ultima_llamada_ok("enviar_aviso")
        cuentas = await estado.cuentas.listar()
        return JSONResponse(
            {
                "ultimo_aviso": ultimo.isoformat() if ultimo else None,
                "cuentas": len(cuentas),
                "por_reconectar": [c.alias for c in cuentas if not c.activa],
            }
        )

    @mcp.custom_route("/api/comandos", methods=["POST"], include_in_schema=False)
    async def api_comandos(request: Request) -> Response:
        if not autorizado(request):
            return JSONResponse({"error": "no autorizado"}, status_code=401)
        try:
            cuerpo = await request.json()
            texto = str(cuerpo["texto"])[:_MAX_TEXTO]
        except ValueError, KeyError, TypeError:
            return JSONResponse({"error": 'se espera {"texto": "..."}'}, status_code=400)
        inicio = time.monotonic()
        respuesta = await comandos.ejecutar(texto, estado.seguimientos)
        await estado.registrar_auditoria(
            herramienta="comando_whatsapp",
            cuenta=None,
            resultado="ok",
            duracion_ms=int((time.monotonic() - inicio) * 1000),
        )
        return JSONResponse({"respuesta": respuesta})
