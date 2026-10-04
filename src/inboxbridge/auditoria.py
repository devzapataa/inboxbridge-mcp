"""Registro de cada llamada a una herramienta: cuál, con qué cuenta, cómo terminó y cuánto tardó."""

import logging
import time
from collections.abc import Awaitable, Callable

import mcp.types as mt
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult

logger = logging.getLogger(__name__)

Registrar = Callable[..., Awaitable[None]]


class Auditoria(Middleware):
    def __init__(self, registrar: Registrar) -> None:
        self._registrar = registrar

    async def on_call_tool(
        self,
        context: MiddlewareContext[mt.CallToolRequestParams],
        call_next: CallNext[mt.CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        inicio = time.monotonic()
        resultado = "ok"
        try:
            return await call_next(context)
        except Exception as e:
            resultado = f"error: {type(e).__name__}"
            raise
        finally:
            cuenta = (context.message.arguments or {}).get("cuenta")
            try:
                await self._registrar(
                    herramienta=context.message.name,
                    cuenta=str(cuenta) if cuenta else None,
                    resultado=resultado,
                    duracion_ms=int((time.monotonic() - inicio) * 1000),
                )
            except Exception:
                logger.exception("No se pudo guardar la auditoría")
