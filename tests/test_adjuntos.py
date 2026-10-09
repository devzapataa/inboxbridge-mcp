"""leer_adjunto: texto de PDF y archivos de texto, con límites y errores claros."""

import io
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from pypdf import PdfWriter

from inboxbridge.app import crear_servidor
from inboxbridge.cuentas import RepoCuentas
from tests.conftest import crear_settings
from tests.google_falso import GoogleFalso, mensaje


def pdf_con_texto(texto: str) -> bytes:
    """Un PDF mínimo de una página con ese texto (ASCII)."""
    contenido = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode()
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        b" /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(contenido), contenido),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    salida = b"%PDF-1.4\n"
    posiciones = []
    for i, obj in enumerate(objetos, start=1):
        posiciones.append(len(salida))
        salida += b"%d 0 obj\n%s\nendobj\n" % (i, obj)
    xref = len(salida)
    salida += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objetos) + 1)
    salida += b"".join(b"%010d 00000 n \n" % p for p in posiciones)
    salida += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objetos) + 1,
        xref,
    )
    return salida


def pdf_con_clave() -> bytes:
    escritor = PdfWriter()
    escritor.add_blank_page(width=100, height=100)
    escritor.encrypt("clave", algorithm="AES-128")
    salida = io.BytesIO()
    escritor.write(salida)
    return salida.getvalue()


@pytest.fixture
async def mcp_cliente(repo: RepoCuentas, google: GoogleFalso) -> Any:
    await repo.guardar(
        email="yo@gmail.com", alias="personal", refresh_token="rt-personal", scopes="g"
    )
    google.buzones["rt-personal"] = {}
    async with Client(crear_servidor(crear_settings())) as cliente:
        yield cliente


def con_adjunto(
    google: GoogleFalso, datos: bytes, nombre: str = "extracto.pdf", **parte: Any
) -> None:
    msg = mensaje("abc123", de="Banco", asunto="Extracto", fecha_ms=0, texto="x", adjunto=nombre)
    adjunto = msg["payload"]["parts"][-1]
    adjunto.update(parte)
    google.mensajes["abc123"] = msg
    google.adjuntos[adjunto["body"]["attachmentId"]] = datos


async def leer(cliente: Client, nombre: str = "extracto.pdf") -> dict[str, Any]:
    r = await cliente.call_tool(
        "leer_adjunto", {"cuenta": "personal", "mensaje_id": "abc123", "nombre": nombre}
    )
    assert r.structured_content is not None
    return r.structured_content


async def test_lee_el_texto_de_un_pdf(mcp_cliente: Client, google: GoogleFalso) -> None:
    con_adjunto(google, pdf_con_texto("Fecha limite de pago: 15 de octubre"))

    datos = await leer(mcp_cliente)

    assert "Fecha limite de pago: 15 de octubre" in datos["texto"]
    assert (datos["paginas"], datos["truncado"], datos["tipo"]) == (1, False, "application/pdf")


async def test_lee_adjuntos_de_texto(mcp_cliente: Client, google: GoogleFalso) -> None:
    con_adjunto(google, b"fecha;valor\n15/10;100", "datos.csv", mimeType="text/csv")
    assert (await leer(mcp_cliente, "datos.csv"))["texto"] == "fecha;valor\n15/10;100"


@pytest.mark.parametrize(
    ("datos", "parte", "error"),
    [
        (pdf_con_clave(), {}, "protegido con contraseña"),
        (b"no soy un pdf", {}, "No pude leer ese PDF"),
        (
            b"PK\x03\x04",
            {"mimeType": "application/zip", "filename": "x.zip"},
            "solo se pueden leer",
        ),
        (b"x", {"body": {"attachmentId": "adj1", "size": 11 * 1024 * 1024}}, "más de 10 MB"),
    ],
)
async def test_errores(
    mcp_cliente: Client, google: GoogleFalso, datos: bytes, parte: dict[str, Any], error: str
) -> None:
    nombre = parte.get("filename", "extracto.pdf")
    con_adjunto(google, datos, **parte)
    with pytest.raises(ToolError, match=error):
        await leer(mcp_cliente, nombre)


async def test_adjunto_inexistente(mcp_cliente: Client, google: GoogleFalso) -> None:
    con_adjunto(google, b"x")
    with pytest.raises(ToolError, match="no tiene un adjunto llamado «otro.pdf»"):
        await leer(mcp_cliente, "otro.pdf")
