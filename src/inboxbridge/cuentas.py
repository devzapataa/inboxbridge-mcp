"""Cuentas de Gmail conectadas: guardar, resolver por alias/email y leer su refresh token."""

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from inboxbridge.crypto import Cifrador
from inboxbridge.errores import CuentaNoEncontrada, ErrorCorreo

_COLUMNAS = "id, email, alias, estado, scopes, conectada_en, ultimo_uso"


@dataclass(frozen=True)
class Cuenta:
    id: int
    email: str
    alias: str
    estado: str
    scopes: str
    conectada_en: datetime
    ultimo_uso: datetime | None

    @property
    def activa(self) -> bool:
        return self.estado == "activa"


class RepoCuentas:
    def __init__(self, pool: asyncpg.Pool, cifrador: Cifrador) -> None:
        self._pool = pool
        self._cifrador = cifrador

    async def listar(self) -> list[Cuenta]:
        filas = await self._pool.fetch(f"SELECT {_COLUMNAS} FROM cuentas ORDER BY id")
        return [Cuenta(**dict(f)) for f in filas]

    async def resolver(self, nombre: str) -> Cuenta:
        """Busca por alias o por email, sin distinguir mayúsculas."""
        clave = nombre.strip().lower()
        fila = await self._pool.fetchrow(
            f"SELECT {_COLUMNAS} FROM cuentas WHERE alias = $1 OR lower(email) = $1", clave
        )
        if fila is None:
            disponibles = ", ".join(c.alias for c in await self.listar()) or "ninguna"
            raise CuentaNoEncontrada(
                f"No hay una cuenta llamada «{nombre}». Cuentas conectadas: {disponibles}."
            )
        return Cuenta(**dict(fila))

    async def guardar(self, *, email: str, alias: str, refresh_token: str, scopes: str) -> Cuenta:
        """Conecta una cuenta nueva o renueva el token de una existente (conserva su alias)."""
        cifrado = self._cifrador.cifrar(refresh_token, email.lower())
        try:
            fila = await self._pool.fetchrow(
                f"""
                INSERT INTO cuentas (email, alias, refresh_token, scopes)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (email) DO UPDATE
                   SET refresh_token = EXCLUDED.refresh_token,
                       scopes = EXCLUDED.scopes,
                       estado = 'activa',
                       conectada_en = now()
                RETURNING {_COLUMNAS}
                """,
                email.lower(),
                alias,
                cifrado,
                scopes,
            )
        except asyncpg.UniqueViolationError as e:
            raise ErrorCorreo(f"El alias «{alias}» ya lo usa otra cuenta.") from e
        except asyncpg.CheckViolationError as e:
            raise ErrorCorreo(
                "El alias solo puede tener minúsculas, números y guiones (máx. 30)."
            ) from e
        if fila is None:  # no pasa: el upsert siempre devuelve la fila
            raise ErrorCorreo("No se pudo guardar la cuenta.")
        return Cuenta(**dict(fila))

    async def refresh_token(self, cuenta: Cuenta) -> str:
        blob = await self._pool.fetchval(
            "SELECT refresh_token FROM cuentas WHERE id = $1", cuenta.id
        )
        if blob is None:
            raise CuentaNoEncontrada(f"La cuenta «{cuenta.alias}» ya no está conectada.")
        return self._cifrador.descifrar(blob, cuenta.email)

    async def marcar_reconectar(self, cuenta_id: int) -> None:
        await self._pool.execute(
            "UPDATE cuentas SET estado = 'reconectar' WHERE id = $1", cuenta_id
        )

    async def marcar_uso(self, cuenta_id: int) -> None:
        await self._pool.execute("UPDATE cuentas SET ultimo_uso = now() WHERE id = $1", cuenta_id)

    async def borrar(self, cuenta_id: int) -> tuple[Cuenta, str] | None:
        """Borra la cuenta y devuelve su refresh token para poder revocarlo en Google."""
        fila = await self._pool.fetchrow(
            f"DELETE FROM cuentas WHERE id = $1 RETURNING {_COLUMNAS}, refresh_token", cuenta_id
        )
        if fila is None:
            return None
        datos = dict(fila)
        blob = datos.pop("refresh_token")
        cuenta = Cuenta(**datos)
        return cuenta, self._cifrador.descifrar(blob, cuenta.email)

    async def ultima_llamada_ok(self, herramienta: str) -> datetime | None:
        return await self._pool.fetchval(
            "SELECT max(momento) FROM auditoria WHERE herramienta = $1 AND resultado = 'ok'",
            herramienta,
        )

    async def contar_llamadas_ok(self, herramienta: str, horas: int) -> int:
        return await self._pool.fetchval(
            "SELECT count(*) FROM auditoria WHERE herramienta = $1 AND resultado = 'ok'"
            " AND momento > now() - make_interval(hours => $2)",
            herramienta,
            horas,
        )

    async def registrar_auditoria(
        self, *, herramienta: str, cuenta: str | None, resultado: str, duracion_ms: int
    ) -> None:
        await self._pool.execute(
            "INSERT INTO auditoria (herramienta, cuenta, resultado, duracion_ms)"
            " VALUES ($1, $2, $3, $4)",
            herramienta,
            cuenta,
            resultado,
            duracion_ms,
        )
