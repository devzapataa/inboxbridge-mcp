"""Pruebas sin red ni base de datos: cifrado, conversión de mensajes y borradores MIME."""

import base64
import email
from email import policy

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet

from inboxbridge.crypto import Cifrador, derivar_llave, llave_fernet
from inboxbridge.errores import ErrorCorreo
from inboxbridge.mensajes import (
    asunto_respuesta,
    construir_borrador,
    extraer_adjuntos,
    extraer_cuerpo,
    html_a_texto,
)
from tests.google_falso import b64, mensaje


class TestCifrado:
    def test_ida_y_vuelta(self) -> None:
        c = Cifrador(derivar_llave("maestra" * 6, "tokens"))
        blob = c.cifrar("1//refresh-token", "a@gmail.com")
        assert b"refresh" not in blob
        assert c.descifrar(blob, "a@gmail.com") == "1//refresh-token"

    def test_no_se_descifra_con_el_email_de_otra_cuenta(self) -> None:
        c = Cifrador(derivar_llave("maestra" * 6, "tokens"))
        blob = c.cifrar("1//refresh-token", "a@gmail.com")
        with pytest.raises(InvalidTag):
            c.descifrar(blob, "b@gmail.com")

    def test_cada_proposito_tiene_su_llave(self) -> None:
        assert derivar_llave("m" * 40, "tokens") != derivar_llave("m" * 40, "sesion")
        Fernet(llave_fernet("m" * 40, "oauth"))  # es una llave Fernet válida


class TestMensajes:
    def test_html_a_texto_quita_estilos_y_conserva_enlaces(self) -> None:
        texto = html_a_texto(
            "<html><head><style>p{color:red}</style></head><body>"
            "<p>Hola&nbsp;mundo</p><script>alert(1)</script>"
            "<p>Mira <a href='https://ejemplo.com/x'>aquí</a></p></body></html>"
        )
        assert texto == "Hola mundo\n\nMira aquí (https://ejemplo.com/x)"

    def test_prefiere_texto_plano(self) -> None:
        payload = mensaje("1", de="a", asunto="s", fecha_ms=0, texto="plano", html="<b>html</b>")
        assert extraer_cuerpo(payload["payload"]) == "plano"

    def test_usa_html_si_no_hay_texto_plano(self) -> None:
        payload = mensaje("1", de="a", asunto="s", fecha_ms=0, html="<p>Solo <b>html</b></p>")
        assert extraer_cuerpo(payload["payload"]) == "Solo html"

    def test_respeta_el_charset(self) -> None:
        parte = {
            "mimeType": "text/plain",
            "headers": [{"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}],
            "body": {"data": b64("Señor, ¿cómo está?", "iso-8859-1")},
        }
        assert extraer_cuerpo(parte) == "Señor, ¿cómo está?"

    def test_lista_adjuntos(self) -> None:
        payload = mensaje("1", de="a", asunto="s", fecha_ms=0, texto="x", adjunto="factura.pdf")
        [adjunto] = extraer_adjuntos(payload["payload"])
        assert (adjunto.nombre, adjunto.tipo, adjunto.tamano_bytes) == (
            "factura.pdf",
            "application/pdf",
            2048,
        )


class TestBorradores:
    def test_respuesta_lleva_cabeceras_de_hilo(self) -> None:
        raw = construir_borrador(
            de="yo@gmail.com",
            para="Ana <ana@example.com>",
            asunto="Re: Entrevista",
            cuerpo="¡Gracias! Confirmo el martes.",
            en_respuesta_a="<m2@example.com>",
            referencias="<m1@example.com>",
        )
        msg = email.message_from_bytes(base64.urlsafe_b64decode(raw), policy=policy.default)
        assert msg["In-Reply-To"] == "<m2@example.com>"
        assert msg["References"] == "<m1@example.com> <m2@example.com>"
        assert msg["To"] == "Ana <ana@example.com>"
        assert msg.get_content().strip() == "¡Gracias! Confirmo el martes."

    def test_rechaza_inyeccion_de_cabeceras(self) -> None:
        with pytest.raises(ErrorCorreo):
            construir_borrador(
                de="yo@gmail.com",
                para="ana@example.com",
                asunto="Hola\r\nBcc: espia@evil.com",
                cuerpo="x",
            )

    def test_rechaza_destinatarios_invalidos(self) -> None:
        with pytest.raises(ErrorCorreo):
            construir_borrador(de="yo@gmail.com", para="no es un correo", asunto="x", cuerpo="x")

    @pytest.mark.parametrize(
        ("original", "esperado"),
        [("Entrevista", "Re: Entrevista"), ("RE: Entrevista", "RE: Entrevista")],
    )
    def test_asunto_respuesta(self, original: str, esperado: str) -> None:
        assert asunto_respuesta(original) == esperado
