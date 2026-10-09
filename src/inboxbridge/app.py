"""Arma la aplicación: FastMCP (herramientas + OAuth) y las páginas web, en una sola app ASGI."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware

from inboxbridge.api import registrar_api
from inboxbridge.auditoria import Auditoria
from inboxbridge.auth import crear_auth
from inboxbridge.config import Settings
from inboxbridge.crypto import derivar_llave
from inboxbridge.estado import Estado
from inboxbridge.herramientas import INSTRUCCIONES, registrar_herramientas
from inboxbridge.web import registrar_rutas

logger = logging.getLogger(__name__)


def crear_servidor(settings: Settings) -> FastMCP:
    """El servidor MCP con sus herramientas y páginas, sin la capa HTTP de sesiones."""
    estado = Estado(settings)

    @asynccontextmanager
    async def ciclo_de_vida(_servidor: FastMCP) -> AsyncIterator[None]:
        await estado.iniciar()
        try:
            yield
        finally:
            await estado.cerrar()

    if not settings.auth_enabled:
        logger.warning("AUTH_ENABLED=false: /mcp queda sin autenticación (solo para local)")

    mcp = FastMCP(
        "inboxbridge",
        instructions=INSTRUCCIONES,
        website_url=settings.base_url,
        auth=crear_auth(settings) if settings.auth_enabled else None,
        lifespan=ciclo_de_vida,
        middleware=[Auditoria(estado.registrar_auditoria)],
        # Los errores inesperados no llegan a Claude con detalles internos;
        # los ToolError (mensajes pensados para el usuario) sí.
        mask_error_details=True,
    )
    registrar_herramientas(mcp, estado)
    registrar_rutas(mcp, estado)
    registrar_api(mcp, estado)
    return mcp


def crear_app(settings: Settings | None = None) -> Starlette:
    settings = settings or Settings()  # type: ignore[call-arg]
    sesion = Middleware(
        SessionMiddleware,
        secret_key=derivar_llave(settings.master_key.get_secret_value(), "sesion-web").hex(),
        session_cookie="inboxbridge_sesion",
        max_age=12 * 3600,
        same_site="lax",
        https_only=settings.es_https,
    )
    return crear_servidor(settings).http_app(path="/mcp", middleware=[sesion])
