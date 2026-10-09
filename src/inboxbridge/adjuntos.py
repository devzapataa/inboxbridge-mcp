"""Extraer el texto de un adjunto (PDF o texto) para que Claude pueda leerlo."""

import io
import logging

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from inboxbridge.errores import ErrorCorreo
from inboxbridge.mensajes import html_a_texto

logger = logging.getLogger(__name__)

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGINAS = 40
MAX_CARACTERES = 30_000


def extraer_texto(datos: bytes, tipo: str, nombre: str) -> tuple[str, int | None]:
    """Devuelve (texto, páginas). CPU puro: llámalo con asyncio.to_thread."""
    if tipo == "application/pdf" or nombre.lower().endswith(".pdf"):
        return _pdf(datos)
    if tipo.startswith("text/"):
        texto = datos.decode("utf-8", errors="replace")
        return (html_a_texto(texto) if tipo == "text/html" else texto), None
    raise ErrorCorreo(f"«{nombre}» es {tipo}: solo se pueden leer adjuntos PDF o de texto.")


def _pdf(datos: bytes) -> tuple[str, int]:
    try:
        lector = PdfReader(io.BytesIO(datos))
        # Muchos PDF vienen "cifrados" con contraseña vacía; esos sí se pueden abrir.
        if lector.is_encrypted and not lector.decrypt(""):
            raise ErrorCorreo("El PDF está protegido con contraseña; no lo puedo leer.")
        paginas = lector.pages[:MAX_PAGINAS]
        texto = "\n\n".join((p.extract_text() or "").strip() for p in paginas)
        return texto.strip(), len(lector.pages)
    except ErrorCorreo:
        raise
    except (PdfReadError, ValueError, KeyError, NotImplementedError) as e:
        logger.info("PDF ilegible: %s", type(e).__name__)
        raise ErrorCorreo("No pude leer ese PDF (está dañado o tiene un formato raro).") from e
