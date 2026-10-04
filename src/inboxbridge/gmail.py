"""Cliente de la API de Gmail para varias cuentas.

Cada cuenta tiene su refresh token cifrado en la base; el access token se guarda en
memoria hasta poco antes de vencer y se renueva con un candado por cuenta.
"""

import asyncio
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from inboxbridge import mensajes as m
from inboxbridge.cuentas import Cuenta, RepoCuentas
from inboxbridge.errores import CuentaPorReconectar, ErrorCorreo, ErrorGoogle
from inboxbridge.google import ClienteGoogle
from inboxbridge.modelos import BorradorCreado, Hilo, Mensaje, ResumenHilo

API = "https://gmail.googleapis.com/gmail/v1/users/me"
_MARGEN_VENCIMIENTO_S = 120
_MAX_CUERPO = 15_000
_MAX_MENSAJES_HILO = 25
_CONCURRENCIA = 8
# Los ids de Gmail son hexadecimales; validarlos evita que un id armado
# apunte a otra ruta de la API (p. ej. "../settings").
_ID_GMAIL = re.compile(r"^[0-9a-f]{6,32}$")

# Tupla y no lista: httpx tipa la versión con lista como invariante.
Params = dict[str, str] | tuple[tuple[str, str], ...]


def enlace_hilo(email: str, hilo_id: str) -> str:
    return f"https://mail.google.com/mail/u/{quote(email)}/#all/{hilo_id}"


def validar_id(hilo_id: str) -> str:
    if not _ID_GMAIL.fullmatch(hilo_id):
        raise ErrorCorreo(f"«{hilo_id}» no es un id de hilo de Gmail válido.")
    return hilo_id


class Gmail:
    def __init__(
        self, http: httpx.AsyncClient, google: ClienteGoogle, cuentas: RepoCuentas, zona: str
    ) -> None:
        self._http = http
        self._google = google
        self._cuentas = cuentas
        self._zona = ZoneInfo(zona)
        self._tokens: dict[int, tuple[str, float]] = {}
        self._candados: dict[int, asyncio.Lock] = {}
        self._limite = asyncio.Semaphore(_CONCURRENCIA)

    def olvidar_token(self, cuenta_id: int) -> None:
        self._tokens.pop(cuenta_id, None)

    async def buscar(self, cuenta: Cuenta, consulta: str, maximo: int) -> list[ResumenHilo]:
        datos = await self._pedir(
            cuenta, "GET", "/threads", params={"q": consulta, "maxResults": str(maximo)}
        )
        ids = [h["id"] for h in datos.get("threads", [])]
        params: Params = (
            ("format", "metadata"),
            ("metadataHeaders", "From"),
            ("metadataHeaders", "Subject"),
        )
        hilos = await asyncio.gather(
            *(self._pedir(cuenta, "GET", f"/threads/{i}", params=params) for i in ids)
        )
        await self._cuentas.marcar_uso(cuenta.id)
        return [self._resumen(cuenta, h) for h in hilos if h.get("messages")]

    async def leer_hilo(self, cuenta: Cuenta, hilo_id: str) -> Hilo:
        validar_id(hilo_id)
        hilo = await self._pedir(cuenta, "GET", f"/threads/{hilo_id}", params={"format": "full"})
        todos = hilo.get("messages", [])
        recientes = todos[-_MAX_MENSAJES_HILO:]
        mensajes = [self._mensaje(msg) for msg in recientes]
        await self._cuentas.marcar_uso(cuenta.id)
        asunto = m.cabeceras(todos[0].get("payload", {})).get("subject", "") if todos else ""
        return Hilo(
            cuenta=cuenta.alias,
            hilo_id=hilo_id,
            asunto=asunto,
            enlace=enlace_hilo(cuenta.email, hilo_id),
            mensajes=mensajes,
            mensajes_omitidos=len(todos) - len(recientes),
        )

    async def crear_borrador(
        self,
        cuenta: Cuenta,
        *,
        para: str | None,
        asunto: str | None,
        cuerpo: str,
        cc: str | None = None,
        hilo_id: str | None = None,
    ) -> BorradorCreado:
        en_respuesta_a = referencias = None
        if hilo_id:
            validar_id(hilo_id)
            cabeceras_pedidas = ("Subject", "From", "Reply-To", "Message-ID", "References")
            hilo = await self._pedir(
                cuenta,
                "GET",
                f"/threads/{hilo_id}",
                params=(("format", "metadata"),)
                + tuple(("metadataHeaders", c) for c in cabeceras_pedidas),
            )
            ultimo = m.cabeceras(hilo["messages"][-1].get("payload", {}))
            en_respuesta_a = ultimo.get("message-id")
            referencias = ultimo.get("references")
            asunto = asunto or m.asunto_respuesta(ultimo.get("subject", ""))
            para = para or ultimo.get("reply-to") or ultimo.get("from")
        if not para:
            raise ErrorCorreo("Falta el destinatario (para).")
        if not asunto:
            raise ErrorCorreo("Falta el asunto.")

        mensaje: dict[str, str] = {
            "raw": m.construir_borrador(
                de=cuenta.email,
                para=para,
                asunto=asunto,
                cuerpo=cuerpo,
                cc=cc,
                en_respuesta_a=en_respuesta_a,
                referencias=referencias,
            )
        }
        if hilo_id:
            mensaje["threadId"] = hilo_id
        creado = await self._pedir(cuenta, "POST", "/drafts", json={"message": mensaje})
        await self._cuentas.marcar_uso(cuenta.id)
        return BorradorCreado(
            cuenta=cuenta.alias,
            borrador_id=creado["id"],
            hilo_id=hilo_id,
            para=para,
            asunto=asunto,
            enlace=f"https://mail.google.com/mail/u/{quote(cuenta.email)}/#drafts",
        )

    # --- internos -------------------------------------------------------------

    def _fecha(self, mensaje: dict[str, Any]) -> datetime:
        return datetime.fromtimestamp(int(mensaje.get("internalDate", 0)) / 1000, tz=self._zona)

    def _resumen(self, cuenta: Cuenta, hilo: dict[str, Any]) -> ResumenHilo:
        mensajes = hilo["messages"]
        primero = m.cabeceras(mensajes[0].get("payload", {}))
        ultimo = mensajes[-1]
        return ResumenHilo(
            cuenta=cuenta.alias,
            hilo_id=hilo["id"],
            asunto=primero.get("subject", "(sin asunto)"),
            de=m.cabeceras(ultimo.get("payload", {})).get("from", ""),
            fecha=self._fecha(ultimo),
            fragmento=m.fragmento(ultimo.get("snippet", "")),
            mensajes=len(mensajes),
            no_leido=any("UNREAD" in (x.get("labelIds") or []) for x in mensajes),
        )

    def _mensaje(self, msg: dict[str, Any]) -> Mensaje:
        payload = msg.get("payload", {})
        cab = m.cabeceras(payload)
        cuerpo = m.extraer_cuerpo(payload)
        return Mensaje(
            id=msg["id"],
            de=cab.get("from", ""),
            para=cab.get("to", ""),
            cc=cab.get("cc", ""),
            fecha=self._fecha(msg),
            asunto=cab.get("subject", ""),
            cuerpo=cuerpo[:_MAX_CUERPO],
            cuerpo_truncado=len(cuerpo) > _MAX_CUERPO,
            adjuntos=m.extraer_adjuntos(payload),
        )

    async def _access_token(self, cuenta: Cuenta) -> str:
        guardado = self._tokens.get(cuenta.id)
        if guardado and guardado[1] > time.monotonic():
            return guardado[0]
        async with self._candados.setdefault(cuenta.id, asyncio.Lock()):
            guardado = self._tokens.get(cuenta.id)
            if guardado and guardado[1] > time.monotonic():
                return guardado[0]
            if not cuenta.activa:
                raise CuentaPorReconectar(
                    f"La cuenta «{cuenta.alias}» necesita reconectarse en /cuentas."
                )
            refresh = await self._cuentas.refresh_token(cuenta)
            try:
                tokens = await self._google.refrescar(refresh)
            except CuentaPorReconectar as e:
                await self._cuentas.marcar_reconectar(cuenta.id)
                raise CuentaPorReconectar(
                    f"Google revocó el acceso a «{cuenta.alias}». Vuelve a conectarla en /cuentas."
                ) from e
            vence = time.monotonic() + tokens.expires_in - _MARGEN_VENCIMIENTO_S
            self._tokens[cuenta.id] = (tokens.access_token, vence)
            return tokens.access_token

    async def _pedir(
        self,
        cuenta: Cuenta,
        metodo: str,
        ruta: str,
        *,
        params: Params | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        for intento in range(2):
            token = await self._access_token(cuenta)
            async with self._limite:
                try:
                    r = await self._http.request(
                        metodo,
                        f"{API}{ruta}",
                        params=params,
                        json=json,
                        headers={"Authorization": f"Bearer {token}"},
                    )
                except httpx.HTTPError as e:
                    raise ErrorGoogle("No pude comunicarme con Gmail.") from e
            if r.status_code == 401 and intento == 0:
                # El token pudo invalidarse antes de tiempo: se pide uno nuevo y se reintenta.
                self.olvidar_token(cuenta.id)
                continue
            if r.status_code == 404:
                raise ErrorGoogle(f"Gmail no encontró ese elemento en «{cuenta.alias}».")
            if r.status_code == 429:
                raise ErrorGoogle("Gmail está limitando las solicitudes; intenta en un momento.")
            if r.status_code == 403:
                raise ErrorGoogle(
                    f"Gmail negó el acceso en «{cuenta.alias}». Puede faltar un permiso: "
                    "reconecta la cuenta en /cuentas."
                )
            if r.status_code >= 400:
                raise ErrorGoogle(f"Gmail respondió {r.status_code} en «{cuenta.alias}».")
            return r.json()
        raise ErrorGoogle(f"Gmail rechazó las credenciales de «{cuenta.alias}».")
