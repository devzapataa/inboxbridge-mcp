"""Pool de asyncpg y migraciones en SQL plano (src/inboxbridge/migraciones)."""

from importlib import resources

import asyncpg

_CANDADO = "inboxbridge/migraciones"


async def crear_pool(url: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(url, min_size=1, max_size=5)


async def migrar(pool: asyncpg.Pool) -> list[str]:
    """Aplica en orden las migraciones que falten y devuelve sus nombres."""
    carpeta = resources.files("inboxbridge").joinpath("migraciones")
    archivos = sorted(
        (a for a in carpeta.iterdir() if a.name.endswith(".sql")), key=lambda a: a.name
    )
    aplicadas: list[str] = []
    async with pool.acquire() as con, con.transaction():
        # Si arrancan dos contenedores a la vez, el segundo espera al primero.
        await con.execute("SELECT pg_advisory_xact_lock(hashtext($1))", _CANDADO)
        await con.execute(
            "CREATE TABLE IF NOT EXISTS migraciones ("
            " nombre text PRIMARY KEY,"
            " aplicada_en timestamptz NOT NULL DEFAULT now())"
        )
        hechas = {r["nombre"] for r in await con.fetch("SELECT nombre FROM migraciones")}
        for archivo in archivos:
            if archivo.name in hechas:
                continue
            await con.execute(archivo.read_text())
            await con.execute("INSERT INTO migraciones (nombre) VALUES ($1)", archivo.name)
            aplicadas.append(archivo.name)
    return aplicadas
