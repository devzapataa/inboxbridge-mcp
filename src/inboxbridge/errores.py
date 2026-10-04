"""Errores con un mensaje que se le puede mostrar tal cual a Claude."""


class ErrorCorreo(Exception):
    """Base: el mensaje es seguro de mostrar (sin tokens ni detalles internos)."""


class CuentaNoEncontrada(ErrorCorreo):
    pass


class CuentaPorReconectar(ErrorCorreo):
    """Google revocó el acceso (invalid_grant): hay que volver a conectar la cuenta."""


class ErrorGoogle(ErrorCorreo):
    pass
