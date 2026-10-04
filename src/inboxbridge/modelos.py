"""Lo que devuelven las herramientas. FastMCP genera el output schema a partir de estos modelos."""

from datetime import datetime

from pydantic import BaseModel


class CuentaInfo(BaseModel):
    alias: str
    email: str
    estado: str
    conectada_en: datetime
    ultimo_uso: datetime | None


class ListaCuentas(BaseModel):
    cuentas: list[CuentaInfo]
    conectar_otra: str


class ResumenHilo(BaseModel):
    cuenta: str
    hilo_id: str
    asunto: str
    de: str
    fecha: datetime
    fragmento: str
    mensajes: int
    no_leido: bool


class ErrorCuenta(BaseModel):
    cuenta: str
    error: str


class ResultadoBusqueda(BaseModel):
    resultados: list[ResumenHilo]
    errores: list[ErrorCuenta]


class Adjunto(BaseModel):
    nombre: str
    tipo: str
    tamano_bytes: int


class Mensaje(BaseModel):
    id: str
    de: str
    para: str
    cc: str
    fecha: datetime
    asunto: str
    cuerpo: str
    cuerpo_truncado: bool
    adjuntos: list[Adjunto]


class Hilo(BaseModel):
    cuenta: str
    hilo_id: str
    asunto: str
    enlace: str
    mensajes: list[Mensaje]
    # Si el hilo es muy largo solo se devuelven los últimos mensajes.
    mensajes_omitidos: int


class BorradorCreado(BaseModel):
    cuenta: str
    borrador_id: str
    hilo_id: str | None
    para: str
    asunto: str
    enlace: str
