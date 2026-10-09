"""Memoria de las rutinas: plazos y procesos que hay que vigilar entre una ejecución y otra.

Sin esto cada rutina empieza de cero y vuelve a deducir todo desde Gmail, y termina
repitiendo lo que el dueño ya hizo. Aquí queda registrado qué se avisó, qué está hecho y
hasta cuándo se pospuso.
"""

from datetime import date, datetime
from typing import Any, Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

import asyncpg

from inboxbridge.errores import ErrorCorreo
from inboxbridge.modelos import SeguimientoInfo

Estado = Literal["pendiente", "avisado", "hecho", "descartado", "vencido"]
Tipo = Literal["empleo", "legal", "pago", "tramite", "otro"]

_SELECT = """
    SELECT s.*, c.email
      FROM seguimientos s
      LEFT JOIN cuentas c ON c.alias = s.cuenta
"""


class SeguimientoNoEncontrado(ErrorCorreo):
    pass


class RepoSeguimientos:
    def __init__(self, pool: asyncpg.Pool, zona: str) -> None:
        self._pool = pool
        self._zona = ZoneInfo(zona)

    def hoy(self) -> date:
        return datetime.now(self._zona).date()

    async def registrar(
        self,
        *,
        cuenta: str,
        hilo_id: str,
        titulo: str,
        tipo: Tipo = "otro",
        vence_en: datetime | None = None,
        proximo_paso: str | None = None,
    ) -> tuple[SeguimientoInfo, bool]:
        """Crea el seguimiento o actualiza sus datos. Nunca cambia el estado de uno existente:
        registrar de nuevo algo que el dueño ya marcó como hecho no lo reabre."""
        fila = await self._pool.fetchrow(
            """
            WITH s AS (
                INSERT INTO seguimientos (cuenta, hilo_id, titulo, tipo, vence_en, proximo_paso)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (cuenta, hilo_id) DO UPDATE
                   SET titulo = EXCLUDED.titulo,
                       tipo = EXCLUDED.tipo,
                       vence_en = coalesce(EXCLUDED.vence_en, seguimientos.vence_en),
                       proximo_paso = coalesce(EXCLUDED.proximo_paso, seguimientos.proximo_paso),
                       actualizado_en = now()
                RETURNING *, (xmax = 0) AS creado
            )
            SELECT s.*, c.email FROM s LEFT JOIN cuentas c ON c.alias = s.cuenta
            """,
            cuenta,
            hilo_id,
            titulo.strip()[:200],
            tipo,
            vence_en,
            proximo_paso.strip()[:300] if proximo_paso else None,
        )
        if fila is None:  # no pasa: el upsert siempre devuelve la fila
            raise ErrorCorreo("No se pudo guardar el seguimiento.")
        datos = dict(fila)
        creado = bool(datos.pop("creado"))
        return self._modelo(datos), creado

    async def actualizar(
        self,
        numero: int,
        *,
        estado: Estado | None = None,
        nota: str | None = None,
        recordar_desde: date | None = None,
        vence_en: datetime | None = None,
        evento_id: str | None = None,
        borrador_id: str | None = None,
    ) -> SeguimientoInfo:
        cambios: dict[str, Any] = {
            "estado": estado,
            "nota": nota.strip()[:500] if nota else None,
            "recordar_desde": recordar_desde,
            "vence_en": vence_en,
            "evento_id": evento_id,
            "borrador_id": borrador_id,
        }
        cambios = {k: v for k, v in cambios.items() if v is not None}
        if not cambios:
            return await self.obtener(numero)
        # Las columnas salen de la lista fija de arriba, nunca de la entrada.
        asignaciones = ", ".join(f"{col} = ${i}" for i, col in enumerate(cambios, start=2))
        fila = await self._pool.fetchrow(
            f"WITH s AS (UPDATE seguimientos SET {asignaciones}, actualizado_en = now()"  # noqa: S608
            " WHERE id = $1 RETURNING *)"
            " SELECT s.*, c.email FROM s LEFT JOIN cuentas c ON c.alias = s.cuenta",
            numero,
            *cambios.values(),
        )
        if fila is None:
            raise SeguimientoNoEncontrado(f"No existe el seguimiento #{numero}.")
        return self._modelo(dict(fila))

    async def obtener(self, numero: int) -> SeguimientoInfo:
        fila = await self._pool.fetchrow(f"{_SELECT} WHERE s.id = $1", numero)
        if fila is None:
            raise SeguimientoNoEncontrado(f"No existe el seguimiento #{numero}.")
        return self._modelo(dict(fila))

    async def listar(
        self,
        *,
        solo_activos: bool = True,
        tipo: str | None = None,
        ocultar_pospuestos: bool = False,
    ) -> list[SeguimientoInfo]:
        condiciones: list[str] = []
        parametros: list[Any] = []
        if tipo:
            parametros.append(tipo)
            condiciones.append(f"s.tipo = ${len(parametros)}")
        if solo_activos:
            condiciones.append("s.estado IN ('pendiente', 'avisado')")
        if ocultar_pospuestos:
            parametros.append(self.hoy())
            condiciones.append(
                f"(s.recordar_desde IS NULL OR s.recordar_desde <= ${len(parametros)})"
            )
        donde = f" WHERE {' AND '.join(condiciones)}" if condiciones else ""
        # Las condiciones son fijas; los valores van siempre como parámetros.
        filas = await self._pool.fetch(
            f"{_SELECT}{donde} ORDER BY s.vence_en NULLS LAST, s.id",  # noqa: S608
            *parametros,
        )
        return [self._modelo(dict(f)) for f in filas]

    def _modelo(self, fila: dict[str, Any]) -> SeguimientoInfo:
        email = fila.pop("email", None)
        vence = fila["vence_en"]
        enlace = (
            f"https://mail.google.com/mail/u/{quote(email)}/#all/{fila['hilo_id']}" if email else ""
        )
        return SeguimientoInfo(
            numero=fila["id"],
            cuenta=fila["cuenta"],
            hilo_id=fila["hilo_id"],
            titulo=fila["titulo"],
            tipo=fila["tipo"],
            estado=fila["estado"],
            vence_en=vence.astimezone(self._zona) if vence else None,
            proximo_paso=fila["proximo_paso"],
            recordar_desde=fila["recordar_desde"],
            evento_id=fila["evento_id"],
            borrador_id=fila["borrador_id"],
            nota=fila["nota"],
            enlace=enlace,
        )
