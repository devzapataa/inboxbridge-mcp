"""Convertir mensajes de la API de Gmail en texto legible y armar borradores MIME."""

import base64
import html
import re
from collections.abc import Iterator
from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any

from inboxbridge.errores import ErrorCorreo
from inboxbridge.modelos import Adjunto

_BLOQUES = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "hr"}
_IGNORAR = {"script", "style", "head", "title"}
_CHARSET = re.compile(r"charset=\"?([\w.:-]+)", re.IGNORECASE)


def cabeceras(parte: dict[str, Any]) -> dict[str, str]:
    """Cabeceras de una parte, con nombre en minúsculas (se queda con la primera)."""
    resultado: dict[str, str] = {}
    for c in parte.get("headers", []) or []:
        resultado.setdefault(c["name"].lower(), c["value"])
    return resultado


def _recorrer(parte: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield parte
    for hija in parte.get("parts", []) or []:
        yield from _recorrer(hija)


def _decodificar(data: str, content_type: str) -> str:
    crudo = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    m = _CHARSET.search(content_type)
    charset = m.group(1) if m else "utf-8"
    try:
        return crudo.decode(charset, errors="replace")
    except LookupError:
        return crudo.decode("utf-8", errors="replace")


class _ExtractorTexto(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._partes: list[str] = []
        self._ignorando = 0
        self._href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _IGNORAR:
            self._ignorando += 1
        elif tag in _BLOQUES:
            self._partes.append("\n")
        elif tag == "a":
            href = dict(attrs).get("href") or ""
            self._href = href if href.startswith(("http://", "https://")) else None

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORAR:
            self._ignorando = max(0, self._ignorando - 1)
        elif tag in _BLOQUES:
            self._partes.append("\n")
        elif tag == "a" and self._href:
            self._partes.append(f" ({self._href[:150]})")
            self._href = None

    def handle_data(self, data: str) -> None:
        if not self._ignorando:
            self._partes.append(data)

    def texto(self) -> str:
        crudo = "".join(self._partes)
        lineas = [re.sub(r"[ \t\xa0]+", " ", linea).strip() for linea in crudo.splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lineas)).strip()


def html_a_texto(contenido: str) -> str:
    extractor = _ExtractorTexto()
    extractor.feed(contenido)
    extractor.close()
    return extractor.texto()


def extraer_cuerpo(payload: dict[str, Any]) -> str:
    """Prefiere text/plain; si no hay (o está vacío), convierte el text/html."""
    texto: str | None = None
    contenido_html: str | None = None
    for parte in _recorrer(payload):
        data = (parte.get("body") or {}).get("data")
        if parte.get("filename") or not data:
            continue
        tipo = parte.get("mimeType", "")
        content_type = cabeceras(parte).get("content-type", "")
        if tipo == "text/plain" and texto is None:
            texto = _decodificar(data, content_type)
        elif tipo == "text/html" and contenido_html is None:
            contenido_html = _decodificar(data, content_type)
    if texto and texto.strip():
        return texto.strip()
    if contenido_html:
        return html_a_texto(contenido_html)
    return ""


def extraer_adjuntos(payload: dict[str, Any]) -> list[Adjunto]:
    return [
        Adjunto(
            nombre=parte["filename"],
            tipo=parte.get("mimeType", "application/octet-stream"),
            tamano_bytes=int((parte.get("body") or {}).get("size", 0)),
        )
        for parte in _recorrer(payload)
        if parte.get("filename")
    ]


def fragmento(snippet: str) -> str:
    # Gmail devuelve el snippet con entidades HTML (&#39;, &amp;...).
    return html.unescape(snippet or "")


def validar_destinatarios(valor: str) -> str:
    direcciones = [d for _, d in getaddresses([valor]) if d]
    if not direcciones or any("@" not in d for d in direcciones):
        raise ErrorCorreo(f"«{valor}» no es una lista válida de correos.")
    return valor


def construir_borrador(
    *,
    de: str,
    para: str,
    asunto: str,
    cuerpo: str,
    cc: str | None = None,
    en_respuesta_a: str | None = None,
    referencias: str | None = None,
) -> str:
    """Arma el mensaje MIME y lo devuelve en base64url, como lo pide drafts.create."""
    mensaje = EmailMessage()
    try:
        mensaje["From"] = de
        mensaje["To"] = validar_destinatarios(para)
        if cc:
            mensaje["Cc"] = validar_destinatarios(cc)
        mensaje["Subject"] = asunto
        if en_respuesta_a:
            mensaje["In-Reply-To"] = en_respuesta_a
            mensaje["References"] = f"{referencias} {en_respuesta_a}".strip()
        mensaje.set_content(cuerpo)
        crudo = mensaje.as_bytes()
    except ValueError as e:
        # La política de email rechaza saltos de línea en las cabeceras (inyección de cabeceras).
        raise ErrorCorreo("El asunto o los destinatarios tienen caracteres no permitidos.") from e
    return base64.urlsafe_b64encode(crudo).decode()


def asunto_respuesta(asunto: str) -> str:
    return asunto if re.match(r"^\s*re\s*:", asunto, re.IGNORECASE) else f"Re: {asunto}"
