"""Cifrado de los refresh tokens de Google y derivación de llaves desde MASTER_KEY."""

import base64
import os

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_LARGO_NONCE = 12


def derivar_llave(maestra: str, proposito: str) -> bytes:
    """Una llave de 32 bytes por propósito: filtrar una no expone las demás."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=f"inboxbridge/{proposito}".encode(),
    )
    return hkdf.derive(maestra.encode())


def llave_fernet(maestra: str, proposito: str) -> bytes:
    return base64.urlsafe_b64encode(derivar_llave(maestra, proposito))


class Cifrador:
    """AES-256-GCM con el email de la cuenta como dato asociado.

    Así un token cifrado para una cuenta no se puede descifrar pasándolo por otra,
    aunque alguien mueva las filas en la base.
    """

    def __init__(self, llave: bytes) -> None:
        self._aes = AESGCM(llave)

    def cifrar(self, texto: str, contexto: str) -> bytes:
        nonce = os.urandom(_LARGO_NONCE)
        return nonce + self._aes.encrypt(nonce, texto.encode(), contexto.encode())

    def descifrar(self, blob: bytes, contexto: str) -> str:
        nonce, cifrado = blob[:_LARGO_NONCE], blob[_LARGO_NONCE:]
        return self._aes.decrypt(nonce, cifrado, contexto.encode()).decode()
