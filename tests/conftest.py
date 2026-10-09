import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import asyncpg
import pytest
import respx

from inboxbridge.config import Settings
from inboxbridge.crypto import Cifrador, derivar_llave
from inboxbridge.cuentas import RepoCuentas
from inboxbridge.db import migrar
from tests.google_falso import GoogleFalso

URL_ADMIN = os.getenv(
    "TEST_DATABASE_ADMIN_URL", "postgresql://inboxbridge:inboxbridge@localhost:5433/inboxbridge"
)
BASE_PRUEBAS = "inboxbridge_test"
URL_PRUEBAS = URL_ADMIN.rsplit("/", 1)[0] + f"/{BASE_PRUEBAS}"
MASTER_KEY = "llave-maestra-de-pruebas-0123456789-abcdefghij"
DUENO = "dueno@example.com"


def crear_settings(**cambios: object) -> Settings:
    datos: dict[str, object] = {
        "base_url": "http://testserver",
        "google_client_id": "cliente.apps.googleusercontent.com",
        "google_client_secret": "GOCSPX-secreto",
        "database_url": URL_PRUEBAS,
        "owner_emails": DUENO,
        "master_key": MASTER_KEY,
        "auth_enabled": False,
    }
    return Settings(_env_file=None, **(datos | cambios))  # type: ignore[call-arg]


@pytest.fixture(scope="session")
def base_de_datos() -> str:
    """Crea desde cero la base de pruebas y aplica las migraciones."""

    async def preparar() -> None:
        admin = await asyncpg.connect(URL_ADMIN)
        try:
            await admin.execute(f"DROP DATABASE IF EXISTS {BASE_PRUEBAS} WITH (FORCE)")
            await admin.execute(f"CREATE DATABASE {BASE_PRUEBAS}")
        finally:
            await admin.close()
        pool = await asyncpg.create_pool(URL_PRUEBAS)
        try:
            await migrar(pool)
        finally:
            await pool.close()

    try:
        asyncio.run(preparar())
    except (OSError, asyncpg.PostgresError) as e:
        pytest.skip(
            f"No hay Postgres de pruebas ({e}). Corre: docker compose -f compose.dev.yml up -d"
        )
    return URL_PRUEBAS


@pytest.fixture
def db_limpia(base_de_datos: str) -> str:
    async def vaciar() -> None:
        con = await asyncpg.connect(base_de_datos)
        try:
            await con.execute("TRUNCATE cuentas, auditoria, seguimientos RESTART IDENTITY")
        finally:
            await con.close()

    asyncio.run(vaciar())
    return base_de_datos


@pytest.fixture
async def repo(db_limpia: str) -> AsyncIterator[RepoCuentas]:
    pool = await asyncpg.create_pool(db_limpia, min_size=1, max_size=2)
    try:
        yield RepoCuentas(pool, Cifrador(derivar_llave(MASTER_KEY, "tokens-google")))
    finally:
        await pool.close()


@pytest.fixture
def google() -> Iterator[GoogleFalso]:
    falso = GoogleFalso()
    with respx.mock(assert_all_called=False) as router:
        falso.montar(router)
        yield falso
