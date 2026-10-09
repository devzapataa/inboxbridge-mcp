"""Simulación de Google (OAuth + Gmail) para las pruebas, montada con respx."""

import base64
import json
from typing import Any
from urllib.parse import parse_qs

import httpx
import respx

from inboxbridge.gmail import API
from inboxbridge.google import REVOKE_URL, TOKEN_URL, USERINFO_URL


def b64(texto: str, charset: str = "utf-8") -> str:
    return base64.urlsafe_b64encode(texto.encode(charset)).decode().rstrip("=")


def mensaje(
    id_: str,
    *,
    de: str,
    asunto: str,
    fecha_ms: int,
    texto: str | None = None,
    html: str | None = None,
    no_leido: bool = False,
    cabeceras: dict[str, str] | None = None,
    adjunto: str | None = None,
) -> dict[str, Any]:
    partes: list[dict[str, Any]] = []
    if texto is not None:
        partes.append({"mimeType": "text/plain", "body": {"data": b64(texto)}})
    if html is not None:
        partes.append({"mimeType": "text/html", "body": {"data": b64(html)}})
    if adjunto:
        partes.append(
            {
                "mimeType": "application/pdf",
                "filename": adjunto,
                "body": {"attachmentId": "adj1", "size": 2048},
            }
        )
    todas = {"From": de, "To": "yo@example.com", "Subject": asunto, **(cabeceras or {})}
    return {
        "id": id_,
        "internalDate": str(fecha_ms),
        "labelIds": ["INBOX", "UNREAD"] if no_leido else ["INBOX"],
        "snippet": f"Fragmento de {asunto} &amp; más",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [{"name": k, "value": v} for k, v in todas.items()],
            "parts": partes,
        },
    }


class GoogleFalso:
    def __init__(self) -> None:
        # refresh token -> {hilo_id: [mensajes]}
        self.buzones: dict[str, dict[str, list[dict[str, Any]]]] = {}
        self.revocados: set[str] = set()
        self.codigos: dict[str, dict[str, Any]] = {}
        self.usuarios: dict[str, dict[str, Any]] = {}
        self.borradores: list[dict[str, Any]] = []
        # mensaje_id -> mensaje (formato full) y attachmentId -> bytes del adjunto
        self.mensajes: dict[str, dict[str, Any]] = {}
        self.adjuntos: dict[str, bytes] = {}
        self.revocaciones: list[str] = []
        self.refrescos = 0

    def montar(self, router: respx.MockRouter) -> None:
        router.post(TOKEN_URL).mock(side_effect=self._token)
        router.get(USERINFO_URL).mock(side_effect=self._userinfo)
        router.post(REVOKE_URL).mock(side_effect=self._revocar)
        router.get(f"{API}/threads").mock(side_effect=self._listar_hilos)
        router.get(url__regex=rf"{API}/threads/(?P<hilo>[^/?]+)").mock(side_effect=self._hilo)
        router.post(f"{API}/drafts").mock(side_effect=self._borrador)
        # El de adjuntos va primero: respx usa la primera ruta que coincide.
        router.get(url__regex=rf"{API}/messages/(?P<msg>[^/?]+)/attachments/(?P<adj>[^/?]+)").mock(
            side_effect=self._adjunto
        )
        router.get(url__regex=rf"{API}/messages/(?P<msg>[^/?]+)").mock(side_effect=self._mensaje)

    def _token(self, request: httpx.Request) -> httpx.Response:
        datos = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if datos["grant_type"] == "refresh_token":
            self.refrescos += 1
            if datos["refresh_token"] in self.revocados:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(
                200,
                json={
                    "access_token": f"at-{datos['refresh_token']}",
                    "expires_in": 3599,
                    "scope": "openid email",
                },
            )
        respuesta = self.codigos.get(datos["code"])
        if respuesta is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={"expires_in": 3599, **respuesta})

    def _userinfo(self, request: httpx.Request) -> httpx.Response:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(200, json=self.usuarios[token])

    def _revocar(self, request: httpx.Request) -> httpx.Response:
        self.revocaciones.append(parse_qs(request.content.decode())["token"][0])
        return httpx.Response(200)

    def _buzon(self, request: httpx.Request) -> dict[str, list[dict[str, Any]]] | None:
        token = request.headers["Authorization"].removeprefix("Bearer at-")
        return self.buzones.get(token)

    def _listar_hilos(self, request: httpx.Request) -> httpx.Response:
        buzon = self._buzon(request)
        if buzon is None:
            return httpx.Response(401)
        maximo = int(request.url.params.get("maxResults", "100"))
        return httpx.Response(200, json={"threads": [{"id": i} for i in list(buzon)[:maximo]]})

    def _hilo(self, request: httpx.Request, hilo: str) -> httpx.Response:
        buzon = self._buzon(request)
        if buzon is None:
            return httpx.Response(401)
        if hilo not in buzon:
            return httpx.Response(404)
        return httpx.Response(200, json={"id": hilo, "messages": buzon[hilo]})

    def _mensaje(self, request: httpx.Request, msg: str) -> httpx.Response:
        if self._buzon(request) is None:
            return httpx.Response(401)
        if msg not in self.mensajes:
            return httpx.Response(404)
        return httpx.Response(200, json=self.mensajes[msg])

    def _adjunto(self, request: httpx.Request, msg: str, adj: str) -> httpx.Response:
        if self._buzon(request) is None:
            return httpx.Response(401)
        if adj not in self.adjuntos:
            return httpx.Response(404)
        datos = base64.urlsafe_b64encode(self.adjuntos[adj]).decode().rstrip("=")
        return httpx.Response(200, json={"data": datos, "size": len(self.adjuntos[adj])})

    def _borrador(self, request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        self.borradores.append(cuerpo)
        return httpx.Response(200, json={"id": f"r-{len(self.borradores)}", **cuerpo})
