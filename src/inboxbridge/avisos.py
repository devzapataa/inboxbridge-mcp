"""Avisos al dueño (por ejemplo, el resumen diario por WhatsApp) a través de un webhook fijo.

Es la única salida del servidor hacia afuera, así que va acotada: la URL del webhook está
en el .env y el destinatario lo fija el workflow que la recibe (n8n). Claude solo decide
el texto. Aunque un correo malicioso lo manipule, el aviso solo le puede llegar al dueño.
"""

import re
from collections.abc import Awaitable, Callable

import httpx

from inboxbridge.errores import ErrorCorreo

MAX_CARACTERES = 3000
# Caracteres de control salvo el salto de línea y el tabulador.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


class Avisador:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        url: str,
        token: str,
        max_diarios: int,
        contar_enviados: Callable[[], Awaitable[int]],
    ) -> None:
        self._http = http
        self._url = url
        self._token = token
        self._max_diarios = max_diarios
        self._contar_enviados = contar_enviados

    async def enviar(self, mensaje: str) -> None:
        texto = _CONTROL.sub("", mensaje).strip()
        if not texto:
            raise ErrorCorreo("El aviso está vacío.")
        if len(texto) > MAX_CARACTERES:
            raise ErrorCorreo(f"El aviso supera {MAX_CARACTERES} caracteres; resúmelo.")
        # El límite evita que un bucle (o un correo malicioso) llene el WhatsApp de mensajes.
        if await self._contar_enviados() >= self._max_diarios:
            raise ErrorCorreo(f"Ya se enviaron {self._max_diarios} avisos en las últimas 24 horas.")
        try:
            r = await self._http.post(
                self._url,
                json={"mensaje": texto},
                headers={"X-Inboxbridge-Token": self._token},
            )
        except httpx.HTTPError as e:
            raise ErrorCorreo("No pude contactar el canal de avisos.") from e
        if r.status_code >= 400:
            raise ErrorCorreo(f"El canal de avisos respondió {r.status_code}.")
