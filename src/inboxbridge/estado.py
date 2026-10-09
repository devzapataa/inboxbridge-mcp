"""Lo que vive mientras corre el servidor: pool de Postgres, cliente HTTP y servicios."""

import logging

import asyncpg
import httpx

from inboxbridge.avisos import Avisador
from inboxbridge.config import Settings
from inboxbridge.crypto import Cifrador, derivar_llave
from inboxbridge.cuentas import RepoCuentas
from inboxbridge.db import crear_pool, migrar
from inboxbridge.gmail import Gmail
from inboxbridge.google import ClienteGoogle
from inboxbridge.seguimientos import RepoSeguimientos

logger = logging.getLogger(__name__)


class Estado:
    pool: asyncpg.Pool
    http: httpx.AsyncClient
    cuentas: RepoCuentas
    google: ClienteGoogle
    gmail: Gmail
    seguimientos: RepoSeguimientos
    avisador: Avisador | None = None

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def iniciar(self) -> None:
        s = self.settings
        self.pool = await crear_pool(s.database_url.get_secret_value())
        aplicadas = await migrar(self.pool)
        if aplicadas:
            logger.info("Migraciones aplicadas: %s", ", ".join(aplicadas))
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        cifrador = Cifrador(derivar_llave(s.master_key.get_secret_value(), "tokens-google"))
        self.cuentas = RepoCuentas(self.pool, cifrador)
        self.google = ClienteGoogle(
            self.http,
            client_id=s.google_client_id,
            client_secret=s.google_client_secret.get_secret_value(),
            redirect_uri=s.google_redirect_uri,
        )
        self.gmail = Gmail(self.http, self.google, self.cuentas, s.zona_horaria)
        self.seguimientos = RepoSeguimientos(self.pool, s.zona_horaria)
        if s.avisos_webhook_url and s.avisos_webhook_token:
            cuentas = self.cuentas
            self.avisador = Avisador(
                self.http,
                url=s.avisos_webhook_url,
                token=s.avisos_webhook_token.get_secret_value(),
                max_diarios=s.avisos_max_diarios,
                contar_enviados=lambda: cuentas.contar_llamadas_ok("enviar_aviso", 24),
            )

    async def cerrar(self) -> None:
        await self.http.aclose()
        await self.pool.close()

    async def registrar_auditoria(self, **datos: object) -> None:
        await self.cuentas.registrar_auditoria(**datos)  # type: ignore[arg-type]
