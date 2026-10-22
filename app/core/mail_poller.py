"""Recogida de correo entrante por IMAP.

El correo es el único canal que no avisa: no hay webhook, hay que ir a mirar el
buzón. Este servicio hace eso cada pocos minutos, por cada cuenta de canal de
tipo ``email`` que esté activa, y entrega lo encontrado al orquestador como
cualquier otro entrante.

Sigue el mismo patrón que ``OutboxDispatcher``: una tarea de fondo que arranca
y para con la aplicación (ver ``app/main.py``). Y como ``imaplib`` es
bloqueante, cada visita al servidor corre en un hilo aparte; si no, un buzón
lento congelaría el bucle de eventos entero.

Marcar como leído es lo que evita traer dos veces el mismo mensaje. La clave de
idempotencia del orquestador —el ``Message-ID``— es la segunda red: si el
proceso muere entre leer y marcar, el correo vuelve a entrar y se descarta ahí.
"""

from __future__ import annotations

import asyncio
import contextlib
import imaplib
from dataclasses import dataclass

from app.config import Settings
from app.core.envelope import ChannelKind
from app.core.orchestrator import Orchestrator
from app.core.secrets import DecryptionError, EncryptionNotConfiguredError, decrypt_json
from app.db import repositories as repo
from app.db.engine import session_scope
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Cuántos mensajes sin leer se traen en cada visita. Un buzón con más queda
#: para la siguiente vuelta en lugar de bloquear el servicio durante minutos.
BATCH_SIZE = 25

#: Tiempo máximo de espera de una conexión IMAP, en segundos.
IMAP_TIMEOUT_S = 30


@dataclass(slots=True)
class Mailbox:
    """Buzón concreto del que se recoge, ya con sus credenciales descifradas."""

    external_id: str
    host: str
    port: int
    username: str
    password: str
    folder: str = "INBOX"

    @classmethod
    def from_credentials(cls, external_id: str, credentials: dict[str, str]) -> Mailbox | None:
        host = credentials.get("imap_host")
        username = credentials.get("imap_username")
        password = credentials.get("imap_password")
        if not (host and username and password):
            return None
        try:
            port = int(credentials.get("imap_port") or 993)
        except ValueError:
            port = 993
        return cls(
            external_id=external_id,
            host=host,
            port=port,
            username=username,
            password=password,
            folder=credentials.get("imap_folder") or "INBOX",
        )


def collect(mailbox: Mailbox) -> list[bytes]:
    """Trae los mensajes sin leer y los marca como leídos. Bloqueante.

    Se ejecuta mediante ``asyncio.to_thread``: ``imaplib`` no es asíncrono y
    una espera de red aquí detendría todas las peticiones de la aplicación.
    """
    mensajes: list[bytes] = []
    with imaplib.IMAP4_SSL(mailbox.host, mailbox.port, timeout=IMAP_TIMEOUT_S) as client:
        client.login(mailbox.username, mailbox.password)
        client.select(mailbox.folder)
        estado, datos = client.search(None, "UNSEEN")
        if estado != "OK":
            return []

        identificadores = (datos[0] or b"").split()[:BATCH_SIZE]
        for identificador in identificadores:
            estado, cuerpo = client.fetch(identificador, "(RFC822)")
            if estado != "OK" or not cuerpo or not isinstance(cuerpo[0], tuple):
                continue
            mensajes.append(cuerpo[0][1])
            # Marcar aquí y no al final: si el proceso muere a mitad, lo ya
            # entregado no se vuelve a traer y lo demás sí, que es lo correcto.
            client.store(identificador, "+FLAGS", "\\Seen")
    return mensajes


class MailPoller:
    """Tarea de fondo que recoge el correo de todas las cuentas activas."""

    def __init__(self, *, settings: Settings, orchestrator: Orchestrator) -> None:
        self.settings = settings
        self.orchestrator = orchestrator
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        if self._running or self.settings.imap_poll_interval_s <= 0:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop(), name="mail-poller")
        log.info("mail_poller_started", interval_s=self.settings.imap_poll_interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            # Se espera antes de la primera visita: durante el arranque hay
            # cosas más urgentes que hacer que abrir conexiones IMAP.
            await asyncio.sleep(self.settings.imap_poll_interval_s)
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mail_poller_error")

    async def poll_once(self) -> int:
        """Una vuelta por todos los buzones. Devuelve cuántos correos entraron."""
        buzones = await self._mailboxes()
        if not buzones:
            return 0

        total = 0
        for buzon in buzones:
            try:
                mensajes = await asyncio.to_thread(collect, buzon)
            except Exception as exc:  # noqa: BLE001 - red, credenciales, buzón caído
                # Un buzón que falla no debe impedir la recogida de los demás.
                log.warning(
                    "mailbox_unreachable", mailbox=buzon.external_id, error=str(exc)[:200]
                )
                continue

            for crudo in mensajes:
                try:
                    resultado = await self.orchestrator.handle_event(
                        ChannelKind.EMAIL,
                        payload={"raw": crudo, "mailbox": buzon.external_id},
                        headers={},
                        # No hay firma que comprobar: la autenticación fue el
                        # propio inicio de sesión IMAP contra el servidor.
                        verify=False,
                    )
                except Exception:
                    log.exception("mail_ingest_failed", mailbox=buzon.external_id)
                    continue
                total += resultado.get("processed", 0)

        if total:
            log.info("mail_poll_pass", ingested=total, mailboxes=len(buzones))
        return total

    async def _mailboxes(self) -> list[Mailbox]:
        """Cuentas de correo activas, con sus credenciales ya descifradas."""
        buzones: list[Mailbox] = []
        async with session_scope() as session:
            cuentas = await repo.list_channel_accounts_of_kind(
                session, channel=ChannelKind.EMAIL
            )
            for cuenta in cuentas:
                if not cuenta.credentials_ciphertext:
                    continue
                try:
                    credenciales = decrypt_json(
                        cuenta.credentials_ciphertext, settings=self.settings
                    )
                except (DecryptionError, EncryptionNotConfiguredError) as exc:
                    log.warning(
                        "mailbox_credentials_unreadable",
                        mailbox=cuenta.external_id,
                        error=str(exc),
                    )
                    continue
                buzon = Mailbox.from_credentials(cuenta.external_id, credenciales)
                if buzon is None:
                    log.warning("mailbox_incomplete", mailbox=cuenta.external_id)
                    continue
                buzones.append(buzon)
        return buzones
