"""Páginas para conectar y desconectar cuentas de Gmail (/cuentas) y el callback de Google.

Solo entra el dueño: inicia sesión con Google y su correo tiene que estar en OWNER_EMAILS.
La sesión es una cookie firmada (SessionMiddleware) y los formularios llevan token CSRF.
"""

import re
import secrets
from html import escape

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

from inboxbridge.cuentas import Cuenta
from inboxbridge.errores import ErrorCorreo
from inboxbridge.estado import Estado
from inboxbridge.google import (
    GMAIL_BORRADORES,
    GMAIL_LECTURA,
    SCOPES_ENTRAR,
    SCOPES_GMAIL,
    nuevo_pkce,
)

ALIAS = re.compile(r"^[a-z0-9][a-z0-9-]{0,29}$")

_CABECERAS_SEGURIDAD = {
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; "
        "form-action 'self' https://accounts.google.com; frame-ancestors 'none'; base-uri 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}

_ESTILO = """
body{font-family:system-ui,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;
     color:#1f2328;background:#fff;line-height:1.5}
h1{font-size:1.4rem}table{width:100%;border-collapse:collapse;margin:16px 0}
td,th{text-align:left;padding:8px;border-bottom:1px solid #d0d7de}
.aviso{background:#ddf4ff;border:1px solid #54aeff;padding:8px 12px;border-radius:6px}
.error{background:#ffebe9;border-color:#ff8182}
.reconectar{color:#cf222e;font-weight:600}
input{padding:6px;font:inherit}button{padding:6px 12px;font:inherit;cursor:pointer}
form.linea{display:inline}small{color:#59636e}
@media (prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}
td,th{border-color:#30363d}.aviso{background:#0c2d6b;border-color:#1f6feb}
.error{background:#5d0f12;border-color:#da3633}small{color:#9198a1}}
"""


def _html(cuerpo: str, status: int = 200) -> HTMLResponse:
    pagina = (
        "<!doctype html><html lang=es><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>Inboxbridge</title><style>{_ESTILO}</style></head><body>{cuerpo}</body></html>"
    )
    return HTMLResponse(pagina, status_code=status, headers=_CABECERAS_SEGURIDAD)


def _error(mensaje: str, status: int) -> HTMLResponse:
    return _html(
        f"<h1>Inboxbridge</h1><p class='aviso error'>{escape(mensaje)}</p>"
        "<p><a href='/cuentas'>Volver</a></p>",
        status,
    )


def _csrf(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


def _csrf_valido(request: Request, valor: object) -> bool:
    esperado = request.session.get("csrf")
    return bool(esperado) and isinstance(valor, str) and secrets.compare_digest(esperado, valor)


def _pagina_cuentas(dueno: str, cuentas: list[Cuenta], csrf: str, aviso: str | None) -> str:
    token = f"<input type=hidden name=csrf value='{escape(csrf)}'>"
    filas = "".join(
        f"<tr><td><b>{escape(c.alias)}</b></td><td>{escape(c.email)}</td>"
        + ("<td class=reconectar>reconectar</td>" if not c.activa else "<td>activa</td>")
        + f"<td><form class=linea method=post action='/cuentas/{c.id}/desconectar'>{token}"
        "<button>Desconectar</button></form></td></tr>"
        for c in cuentas
    )
    tabla = (
        f"<table><tr><th>Alias</th><th>Correo</th><th>Estado</th><th></th></tr>{filas}</table>"
        if cuentas
        else "<p>Todavía no hay cuentas conectadas.</p>"
    )
    return (
        "<h1>Cuentas de Gmail conectadas</h1>"
        + (f"<p class=aviso>{escape(aviso)}</p>" if aviso else "")
        + tabla
        + "<h2>Conectar una cuenta</h2>"
        "<form method=post action='/cuentas/conectar'>"
        f"{token}<input name=alias required pattern='[a-z0-9][a-z0-9\\-]{{0,29}}' "
        "placeholder='alias, p. ej. personal'> <button>Conectar</button></form>"
        "<p><small>Google dirá que la app no está verificada: es tu propio servidor, sigue por "
        "<i>Avanzado → Ir a…</i> y marca los dos permisos de Gmail. Para reconectar una cuenta "
        "usa el mismo alias.</small></p>"
        f"<p><small>Sesión: {escape(dueno)}</small> "
        f"<form class=linea method=post action='/salir'>{token}<button>Salir</button></form></p>"
    )


def registrar_rutas(mcp: FastMCP, estado: Estado) -> None:
    duenos = estado.settings.duenos

    def a_google(
        request: Request,
        *,
        proposito: str,
        scopes: tuple[str, ...],
        offline: bool,
        alias: str | None = None,
    ) -> Response:
        verifier, challenge = nuevo_pkce()
        state = secrets.token_urlsafe(32)
        request.session["flujo"] = {
            "state": state,
            "verifier": verifier,
            "proposito": proposito,
            "alias": alias,
        }
        url = estado.google.url_autorizacion(
            scopes=scopes, state=state, code_challenge=challenge, offline=offline
        )
        return RedirectResponse(url, status_code=303)

    def volver(aviso: str, request: Request) -> Response:
        request.session["aviso"] = aviso
        return RedirectResponse("/cuentas", status_code=303)

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def inicio(_request: Request) -> Response:
        return RedirectResponse("/cuentas", status_code=303)

    @mcp.custom_route("/salud", methods=["GET"], include_in_schema=False)
    async def salud(_request: Request) -> Response:
        try:
            await estado.pool.fetchval("SELECT 1")
        except Exception:
            return PlainTextResponse("base de datos caída", status_code=503)
        return PlainTextResponse("ok")

    @mcp.custom_route("/entrar", methods=["GET"], include_in_schema=False)
    async def entrar(request: Request) -> Response:
        return a_google(request, proposito="entrar", scopes=SCOPES_ENTRAR, offline=False)

    @mcp.custom_route("/salir", methods=["POST"], include_in_schema=False)
    async def salir(request: Request) -> Response:
        form = await request.form()
        if not _csrf_valido(request, form.get("csrf")):
            return _error("Formulario vencido. Recarga la página.", 403)
        request.session.clear()
        return _html(
            "<h1>Inboxbridge</h1><p>Sesión cerrada.</p><p><a href='/cuentas'>Entrar</a></p>"
        )

    @mcp.custom_route("/cuentas", methods=["GET"], include_in_schema=False)
    async def cuentas(request: Request) -> Response:
        dueno = request.session.get("dueno")
        if not dueno:
            return RedirectResponse("/entrar", status_code=303)
        aviso = request.session.pop("aviso", None)
        lista = await estado.cuentas.listar()
        return _html(_pagina_cuentas(dueno, lista, _csrf(request), aviso))

    @mcp.custom_route("/cuentas/conectar", methods=["POST"], include_in_schema=False)
    async def conectar(request: Request) -> Response:
        form = await request.form()
        if not request.session.get("dueno") or not _csrf_valido(request, form.get("csrf")):
            return _error("Tu sesión expiró. Entra de nuevo.", 403)
        alias = str(form.get("alias", "")).strip().lower()
        if not ALIAS.fullmatch(alias):
            return volver("El alias solo puede tener minúsculas, números y guiones.", request)
        return a_google(
            request, proposito="conectar", scopes=SCOPES_GMAIL, offline=True, alias=alias
        )

    @mcp.custom_route(
        "/cuentas/{cuenta_id:int}/desconectar", methods=["POST"], include_in_schema=False
    )
    async def desconectar(request: Request) -> Response:
        form = await request.form()
        if not request.session.get("dueno") or not _csrf_valido(request, form.get("csrf")):
            return _error("Tu sesión expiró. Entra de nuevo.", 403)
        borrada = await estado.cuentas.borrar(request.path_params["cuenta_id"])
        if borrada is None:
            return volver("Esa cuenta ya no estaba conectada.", request)
        cuenta, refresh_token = borrada
        estado.gmail.olvidar_token(cuenta.id)
        await estado.google.revocar(refresh_token)
        return volver(f"Desconectada {cuenta.email} y revocado su acceso en Google.", request)

    @mcp.custom_route("/google/callback", methods=["GET"], include_in_schema=False)
    async def callback(request: Request) -> Response:
        flujo = request.session.pop("flujo", None)
        state = request.query_params.get("state", "")
        if not flujo or not secrets.compare_digest(flujo["state"], state):
            return _error("El inicio de sesión venció o no coincide. Vuelve a intentarlo.", 400)
        if request.query_params.get("error"):
            return volver("Cancelaste el permiso en Google.", request)
        code = request.query_params.get("code")
        if not code:
            return _error("Google no devolvió el código de autorización.", 400)
        try:
            tokens = await estado.google.canjear_codigo(code, flujo["verifier"])
            info = await estado.google.userinfo(tokens.access_token)
        except ErrorCorreo as e:
            return _error(str(e), 502)
        email = str(info.get("email", "")).lower()
        verificado = info.get("email_verified") is True

        if flujo["proposito"] == "entrar":
            if not verificado or email not in duenos:
                return _error("Esta cuenta de Google no tiene acceso a este servidor.", 403)
            request.session.clear()
            request.session["dueno"] = email
            return RedirectResponse("/cuentas", status_code=303)

        if not request.session.get("dueno"):
            return _error("Tu sesión expiró. Entra de nuevo.", 403)
        if not verificado:
            return volver("Google no confirma que ese correo esté verificado.", request)
        if not {GMAIL_LECTURA, GMAIL_BORRADORES} <= set(tokens.scope.split()):
            return volver(
                "Faltaron permisos: al conectar marca las dos casillas de Gmail.", request
            )
        if not tokens.refresh_token:
            return volver("Google no entregó un refresh token. Intenta de nuevo.", request)
        try:
            cuenta = await estado.cuentas.guardar(
                email=email,
                alias=flujo["alias"],
                refresh_token=tokens.refresh_token,
                scopes=tokens.scope,
            )
        except ErrorCorreo as e:
            return volver(str(e), request)
        estado.gmail.olvidar_token(cuenta.id)
        return volver(f"Conectada {cuenta.email} como «{cuenta.alias}».", request)
