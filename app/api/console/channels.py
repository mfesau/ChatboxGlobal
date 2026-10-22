"""Cuentas de canal: números de WhatsApp, páginas, buzones y sus credenciales."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.api.deps import (
    AdminDep,
    SessionDep,
    SettingsDep,
)
from app.core.envelope import (
    ChannelKind,
)
from app.core.secrets import (
    DecryptionError,
    EncryptionNotConfiguredError,
    decrypt_json,
    encrypt_json,
)
from app.db import repositories as repo
from app.db.models import (
    ChannelAccount,
)
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["consola"])



# --------------------------------------------------------------------------- #
# Cuentas de canal
#
# Da de alta, con departamento propio, tantos números de WhatsApp, páginas de
# Facebook o equipos de Teams como se quiera — sin esto, todos comparten la
# cola común, exactamente como antes de que existiera esta pantalla.
# --------------------------------------------------------------------------- #
class ChannelAccountOut(BaseModel):
    id: uuid.UUID
    channel: str
    external_id: str
    display_name: str | None
    is_active: bool
    department_id: uuid.UUID | None
    department_name: str | None
    #: Nunca se expone el token en sí, ni cifrado ni en claro.
    has_own_credentials: bool


#: Credenciales propias que admite cada canal. Se guardan juntas y cifradas en
#: ``ChannelAccount.credentials_ciphertext``. En los canales de Meta, cualquiera
#: que falte se resuelve con el valor global de ``.env``, de modo que una
#: instalación con un solo número sigue funcionando sin tocar la consola; los
#: demás canales no tienen valor global y lo exigen aquí.
#:
#: Teams aparece con la tupla vacía a propósito: su autenticación va contra
#: Entra ID con los datos de ``.env``, y una cuenta de ese canal no guarda
#: secreto alguno.
CHANNEL_CREDENTIAL_FIELDS: dict[str, tuple[str, ...]] = {
    "whatsapp": ("access_token", "verify_token", "app_secret"),
    "facebook": ("access_token", "verify_token", "app_secret"),
    "msbot": (),
    # Telegram: el token del bot que da BotFather y el secreto que se declara
    # en `setWebhook`, con el que Telegram firma cada aviso.
    "telegram": ("access_token", "verify_token"),
    # Twilio: la cuenta, su testigo y el número desde el que se escribe.
    "sms": ("account_sid", "auth_token", "from_number"),
    # Correo: entrada por IMAP y salida por SMTP, cada una con lo suyo.
    "email": (
        "imap_host",
        "imap_port",
        "imap_username",
        "imap_password",
        "imap_folder",
        "smtp_host",
        "smtp_port",
        "smtp_username",
        "smtp_password",
        "from_email",
    ),
}

#: Canales que se dan de alta desde la consola. El chatbox web y los mensajes
#: internos no son cuentas que nadie configure.
MANAGED_CHANNELS = tuple(CHANNEL_CREDENTIAL_FIELDS)


def _credential_payload(channel: str, body: ChannelAccountIn | ChannelAccountUpdateIn) -> dict:
    """Reúne las credenciales enviadas y comprueba que el canal las admite.

    Los tres campos con nombre propio —los de Meta— conviven con el diccionario
    ``credentials``, que es por donde llegan las de los canales que necesitan
    más de tres. Se aceptan las dos formas para no romper a quien ya llamaba a
    esta API con los campos sueltos.
    """
    allowed = CHANNEL_CREDENTIAL_FIELDS.get(channel, ())
    sent: dict[str, str] = {}
    for field in ("access_token", "verify_token", "app_secret"):
        value = getattr(body, field, None)
        if value is not None:
            sent[field] = value
    sent.update(body.credentials or {})

    unknown = sorted(set(sent) - set(allowed))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El canal {channel} no admite: {', '.join(unknown)}",
        )
    return sent


class ChannelAccountIn(BaseModel):
    channel: Literal["whatsapp", "facebook", "msbot", "telegram", "email", "sms"]
    external_id: str = Field(min_length=1, max_length=128)
    display_name: str | None = Field(default=None, max_length=160)
    department_id: uuid.UUID | None = None
    #: Obligatorio en Facebook (cada página tiene el suyo) y en Telegram (es el
    #: token del bot); opcional en WhatsApp (si falta, se usa el global de
    #: .env); ignorado en Teams.
    access_token: str | None = Field(default=None, max_length=4096)
    #: El que se escribe en el panel de Meta, o el `secret_token` de Telegram,
    #: al dar de alta el webhook. Propio por cuenta cuando cada número vive en
    #: una aplicación distinta.
    verify_token: str | None = Field(default=None, max_length=512)
    #: Clave con la que Meta firma el cuerpo del webhook. Ídem: una por
    #: aplicación de Meta, no una por instalación.
    app_secret: str | None = Field(default=None, max_length=512)
    #: Las demás credenciales del canal, por nombre. Correo y Twilio necesitan
    #: más de tres valores y no tendría sentido darles a cada uno un campo
    #: propio en este modelo.
    credentials: dict[str, str] = Field(default_factory=dict)


class ChannelAccountUpdateIn(BaseModel):
    #: Solo se aplican los campos recibidos; ausente significa "no tocar".
    display_name: str | None = Field(default=None, max_length=160)
    department_id: uuid.UUID | None = None
    is_active: bool | None = None
    #: Cadena vacía = quitar ese valor propio y volver al global de ``.env``;
    #: ausente = no tocarlo. Se editan por separado: cambiar el token de acceso
    #: no debe obligar a volver a escribir el secreto de la app.
    access_token: str | None = Field(default=None, max_length=4096)
    verify_token: str | None = Field(default=None, max_length=512)
    app_secret: str | None = Field(default=None, max_length=512)
    credentials: dict[str, str] = Field(default_factory=dict)


def _channel_account_out(account: ChannelAccount) -> ChannelAccountOut:
    return ChannelAccountOut(
        id=account.id,
        channel=str(account.channel),
        external_id=account.external_id,
        display_name=account.display_name,
        is_active=account.is_active,
        department_id=account.department_id,
        department_name=account.department.name if account.department else None,
        has_own_credentials=bool(account.credentials_ciphertext),
    )


@router.get("/channel-accounts", response_model=list[ChannelAccountOut])
async def list_channel_accounts(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> list[ChannelAccountOut]:
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    accounts = await repo.list_channel_accounts(session, tenant_id=tenant_row.id)
    return [_channel_account_out(account) for account in accounts]


@router.post(
    "/channel-accounts", status_code=status.HTTP_201_CREATED, response_model=ChannelAccountOut
)
async def create_channel_account(
    body: ChannelAccountIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> ChannelAccountOut:
    """Alta manual de una cuenta de canal. Reservado a administración."""
    channel = ChannelKind(body.channel)
    credentials = _credential_payload(body.channel, body)

    # Canales sin valor global en ``.env``: lo que no llegue aquí no llega de
    # ninguna otra parte, así que se exige en el alta y no en el primer fallo
    # de entrega, cuando ya nadie está mirando.
    required = {
        ChannelKind.FACEBOOK: ("access_token",),
        ChannelKind.TELEGRAM: ("access_token",),
        ChannelKind.SMS: ("account_sid", "auth_token", "from_number"),
        ChannelKind.EMAIL: (
            "imap_host",
            "imap_username",
            "imap_password",
            "smtp_host",
            "smtp_username",
            "smtp_password",
        ),
    }.get(channel, ())
    missing = [field for field in required if not credentials.get(field)]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"El canal {body.channel} exige: {', '.join(missing)}",
        )

    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if body.department_id is not None:
        department = await repo.get_department(session, body.department_id)
        if department is None or department.tenant_id != tenant_row.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    credentials = {campo: valor for campo, valor in credentials.items() if valor}
    credentials_ciphertext = None
    if credentials and channel is not ChannelKind.MSBOT:
        try:
            credentials_ciphertext = encrypt_json(credentials, settings=settings)
        except EncryptionNotConfiguredError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    account = await repo.create_channel_account(
        session,
        tenant_id=tenant_row.id,
        channel=channel,
        external_id=body.external_id,
        display_name=body.display_name,
        department_id=body.department_id,
        credentials_ciphertext=credentials_ciphertext,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="channel_account_created",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={"channel": body.channel, "external_id": body.external_id},
    )
    account = await repo.get_channel_account(session, account.id)
    return _channel_account_out(account)


@router.patch("/channel-accounts/{account_id}", response_model=ChannelAccountOut)
async def update_channel_account(
    account_id: uuid.UUID,
    body: ChannelAccountUpdateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> ChannelAccountOut:
    """Edita nombre, departamento, estado o token propio. Reservado a administración."""
    account = await repo.get_channel_account(session, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")

    changes = body.model_dump(exclude_unset=True)
    if changes.get("department_id") is not None:
        department = await repo.get_department(session, changes["department_id"])
        if department is None or department.tenant_id != account.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
            )

    # Lo que llegó por su nombre propio y lo que llegó en el diccionario son la
    # misma cosa una vez guardadas; se comprueban juntas contra el canal.
    changes.pop("credentials", None)
    tocadas = _credential_payload(str(account.channel), body)
    for campo in ("access_token", "verify_token", "app_secret"):
        changes.pop(campo, None)
    if tocadas:
        # Se parte de lo que ya había y se aplica solo lo que llegó: así,
        # cambiar el token de acceso no borra el secreto de la app guardado
        # antes. Una cadena vacía sí quita ese valor concreto.
        try:
            actuales = (
                decrypt_json(account.credentials_ciphertext, settings=settings)
                if account.credentials_ciphertext
                else {}
            )
            for campo, valor in tocadas.items():
                if valor:
                    actuales[campo] = valor
                else:
                    actuales.pop(campo, None)
            changes["credentials_ciphertext"] = (
                encrypt_json(actuales, settings=settings) if actuales else None
            )
        except EncryptionNotConfiguredError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except DecryptionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await repo.update_channel_account(session, account, **changes)
    await repo.record_audit(
        session,
        tenant_id=account.tenant_id,
        actor=principal.audit_actor,
        action="channel_account_updated",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={
            key: (str(value) if isinstance(value, uuid.UUID) else value)
            for key, value in changes.items()
            if key != "credentials_ciphertext"
        },
    )
    account = await repo.get_channel_account(session, account.id)
    return _channel_account_out(account)


@router.post("/channel-accounts/{account_id}/telegram/webhook")
async def register_telegram_webhook(
    account_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
) -> dict[str, Any]:
    """Declara el webhook de este bot ante Telegram. Reservado a administración.

    Telegram no tiene panel de configuración: el webhook se da de alta llamando
    a su propia API con el token dentro de la URL. Sin este botón habría que
    salir de la consola y componer ese ``curl`` a mano.
    """
    account = await repo.get_channel_account(session, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")
    if account.channel is not ChannelKind.TELEGRAM:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Esta operación es solo para cuentas de Telegram",
        )
    if not account.credentials_ciphertext:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="La cuenta no tiene token del bot"
        )

    try:
        credentials = decrypt_json(account.credentials_ciphertext, settings=settings)
    except (EncryptionNotConfiguredError, DecryptionError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    url = f"{settings.public_base_url.rstrip('/')}/webhooks/telegram"
    adapter = request.app.state.registry.get(ChannelKind.TELEGRAM)
    error = await adapter.register_webhook(
        token=credentials.get("access_token", ""),
        url=url,
        secret=credentials.get("verify_token"),
    )
    if error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error)

    await repo.record_audit(
        session,
        tenant_id=account.tenant_id,
        actor=principal.audit_actor,
        action="telegram_webhook_registered",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={"url": url},
    )
    return {"status": "ok", "url": url}


@router.delete("/channel-accounts/{account_id}")
async def delete_channel_account(
    account_id: uuid.UUID,
    session: SessionDep,
    principal: AdminDep,
    confirm: bool = False,
) -> dict[str, Any]:
    """Borra una cuenta de canal. Reservado a administración.

    Con conversaciones detrás no se borra a la primera: se responde 409 con
    cuántas hay, y solo se procede con ``?confirm=true``. Es una operación que
    no se deshace, y quien la pide merece saber qué había antes de perderla.

    El historial no se va con la cuenta —la clave foránea es ``SET NULL``—,
    pero sí lo hacen sus credenciales y el enrutado automático al departamento.
    Para dejar de recibir sin perder nada, está desactivarla.
    """
    account = await repo.get_channel_account(session, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cuenta no encontrada")

    conversations = await repo.count_conversations_of_account(session, account.id)
    if conversations and not confirm:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"La cuenta tiene {conversations} conversaciones. Se conservarán, "
                "pero quedarán sin cuenta asociada. Confirme para continuar."
            ),
        )

    await repo.delete_channel_account(session, account)
    await repo.record_audit(
        session,
        tenant_id=account.tenant_id,
        actor=principal.audit_actor,
        action="channel_account_deleted",
        subject_type="channel_account",
        subject_id=str(account.id),
        detail={
            "channel": str(account.channel),
            "external_id": account.external_id,
            "conversaciones": conversations,
        },
    )
    log.info(
        "channel_account_deleted",
        channel=str(account.channel),
        external_id=account.external_id,
        conversations=conversations,
    )
    return {"status": "ok", "conversations_kept": conversations}
