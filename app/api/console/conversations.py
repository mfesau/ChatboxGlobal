"""Bandeja y conversaciones: listar, responder, derivar, notas y estado del hilo."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, status

from app.api.console._common import (
    WORK_STATES,
    ConversationDep,
    _agent_names,
    _conversation_out,
    _department_names,
    _effective_scope,
    _notify_assignment,
    _publish_control_change,
    _publish_state_change,
    _transfer_to_department,
)
from app.api.console.schemas import (
    AssignmentOut,
    ControlIn,
    ConversationOut,
    ConversationStateIn,
    MessageOut,
    NoteIn,
    NoteOut,
    ReleaseIn,
    ReplyIn,
    Scope,
    StartConversationIn,
    TransferIn,
)
from app.api.deps import (
    AdminDep,
    OrchestratorDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from app.channels.whatsapp import TemplatesUnavailable
from app.core import csat, translation
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    Direction,
    OutboundMessage,
    Party,
)
from app.core.hub import agent_topic, hub
from app.core.storage import save_upload
from app.db import repositories as repo
from app.db.models import (
    Message,
    OutboxItem,
    Tenant,
)
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["consola"])



# --------------------------------------------------------------------------- #
# Bandeja
# --------------------------------------------------------------------------- #
@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    scope: Scope | None = None,
    conversation_status: str | None = Query(default="open", alias="status"),
    channel: ChannelKind | None = None,
    department: uuid.UUID | None = None,
    include_unrouted: bool = False,
    label: uuid.UUID | None = None,
    tenant: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[ConversationOut]:
    """Devuelve la bandeja acotada al alcance permitido.

    Sin ``scope`` explícito, supervisión recibe todo y un agente recibe su
    cartera más la cola común. Si un agente pide ``all``, se degrada su petición
    en lugar de rechazarla: la consola es la misma para todos los roles.
    ``department`` acota la vista a uno de los departamentos ya accesibles;
    no amplía lo que ``department_ids`` permite.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    effective = _effective_scope(scope, principal)

    conversations = await repo.list_conversations(
        session,
        tenant_id=tenant_row.id,
        status=conversation_status,
        channel=channel,
        scope=effective,
        agent_id=principal.id,
        department_ids=principal.department_ids,
        department=department,
        include_unrouted=include_unrouted,
        label=label,
        limit=limit,
        offset=offset,
    )
    return [_conversation_out(row) for row in conversations]


@router.get("/inbox/summary")
async def inbox_summary(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    department: uuid.UUID | None = None,
    include_unrouted: bool = False,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Contadores de las pestañas: cola común, cartera propia y total.

    ``department`` acota los tres a la pestaña de departamento abierta, para
    que el número de la cola común sea el de ese departamento y no el del
    inquilino entero. Como en la bandeja, no amplía nada: es un filtro sobre
    lo que ``department_ids`` ya permite.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    summary: dict[str, Any] = {
        "unassigned": await repo.count_conversations(
            session,
            tenant_id=tenant_row.id,
            scope="unassigned",
            department_ids=principal.department_ids,
            department=department,
            include_unrouted=include_unrouted,
        ),
        "mine": await repo.count_conversations(
            session,
            tenant_id=tenant_row.id,
            scope="mine",
            agent_id=principal.id,
            department=department,
            include_unrouted=include_unrouted,
        )
        if principal.id
        else 0,
        "is_supervisor": principal.is_supervisor,
    }
    if principal.is_supervisor:
        summary["all"] = await repo.count_conversations(
            session,
            tenant_id=tenant_row.id,
            department_ids=principal.department_ids,
            department=department,
            include_unrouted=include_unrouted,
        )
    return summary


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(
    conversation: ConversationDep,
    session: SessionDep,
    limit: int = Query(default=200, ge=1, le=1_000),
) -> list[MessageOut]:
    """Historial completo del hilo, con independencia de cuántas veces se derivó."""
    messages = await repo.recent_messages(session, conversation.id, limit=limit)
    conversation.unread_count = 0
    return [
        MessageOut(
            id=row.id,
            direction="inbound" if row.direction is Direction.INBOUND else "outbound",
            author_type=row.author_type,
            content_type=str(row.content_type),
            status=str(row.status),
            text=row.text,
            attachments=list(row.attachments or []),
            created_at=row.created_at.isoformat() if row.created_at else None,
            translations=dict(row.translations or {}),
        )
        for row in messages
    ]


@router.post("/conversations/{conversation_id}/reply", status_code=status.HTTP_202_ACCEPTED)
async def reply_as_agent(
    conversation: ConversationDep,
    body: ReplyIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, str]:
    """Responde al cliente por el canal de origen del hilo.

    Quien responde una conversación de la cola común la asume de forma
    implícita: evita que dos personas contesten a la vez y ahorra un paso en la
    interfaz.
    """
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id not in (None, principal.agent.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La conversación la atiende otro compañero",
        )
    if not body.text and not body.attachments:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La respuesta necesita texto o al menos un adjunto",
        )

    original = body.text
    texto = body.text
    if body.translate_to and body.text:
        destino = body.translate_to.lower()
        if destino not in translation.LANGUAGES:
            raise HTTPException(status_code=422, detail=f"Idioma no admitido: {destino}")
        tenant_row = await session.get(Tenant, conversation.tenant_id)
        try:
            texto = await translation.translate_text(settings, tenant_row, body.text, destino)
        except translation.TranslationUnavailable as exc:
            # Mejor no enviar nada que enviar sin traducir algo que el cliente
            # quizá no entienda: el agente decide si lo manda tal cual.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"No se pudo traducir la respuesta: {exc}",
            ) from exc

    attachments = [Attachment.from_dict(a.model_dump()) for a in body.attachments]
    content_type = (
        attachments[0].content_type if attachments and not body.text else ContentType.TEXT
    )
    outbox_id = await orchestrator.send_from_agent(
        conversation_id=conversation.id,
        outbound=OutboundMessage(
            text=texto or None,
            quick_replies=body.quick_replies,
            attachments=attachments,
            content_type=content_type,
        ),
        agent_id=principal.id,
        # La misma transacción de la petición: ni una segunda conexión ni riesgo
        # de interbloqueo, y la respuesta se confirma junto al resto del turno.
        session=session,
    )
    if outbox_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversación no encontrada"
        )
    # Contestó una persona: se detiene aquí el reloj del objetivo. Se anota al
    # encolar y no al entregar, porque lo que se mide es cuánto tardó el
    # equipo en atender, no cuánto tardó el canal en despachar el mensaje.
    await repo.mark_first_human_response(session, conversation=conversation)
    if texto != original:
        # Lo que escribió el agente queda como «traducción» a su idioma: así
        # la consola enseña ambas versiones igual que en lo que escribe el cliente.
        item = await session.get(OutboxItem, outbox_id)
        message = await session.get(Message, item.message_id) if item else None
        if message is not None:
            idioma = (body.source_language or "").lower() or None
            message.translations = {
                translation.SOURCE_KEY: body.translate_to.lower(),
                **({idioma: original} if idioma else {}),
            }
    return {"status": "queued", "outbox_id": str(outbox_id)}


# --------------------------------------------------------------------------- #
# Conversaciones salientes
# --------------------------------------------------------------------------- #
def _render_template_body(body: str, variables: list[str]) -> str:
    """Sustituye los huecos ``{{n}}`` por los valores, para verlo en el hilo.

    Lo que viaja a WhatsApp es la plantilla con sus parametros aparte; esto es
    solo lo que se guarda y se muestra en la consola. Sin ello, el hilo abriria
    con una burbuja vacia y nadie sabria que se envio.
    """
    rendered = body or ""
    for index, value in enumerate(variables, start=1):
        rendered = rendered.replace(f"{{{{{index}}}}}", value)
    return rendered


@router.get("/whatsapp/templates")
async def list_whatsapp_templates(
    principal: AdminDep, orchestrator: OrchestratorDep
) -> list[dict[str, Any]]:
    """Plantillas aprobadas, para elegir al iniciar una conversacion.

    Reservado a administración, como el envío que lo usa: es el único sitio
    desde donde se eligen.
    """
    adapter = orchestrator.registry.get(ChannelKind.WHATSAPP)
    try:
        return await adapter.list_templates()
    except TemplatesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc


@router.post("/conversations/start", status_code=status.HTTP_201_CREATED)
async def start_conversation(
    body: StartConversationIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    orchestrator: OrchestratorDep,
    tenant: str | None = None,
) -> dict[str, str]:
    """Abre una conversacion de WhatsApp escribiendo primero. Solo administración.

    Abordar a alguien que no escribió antes no es atender una consulta: gasta
    una plantilla aprobada y llega a un teléfono que no pidió el contacto. Por
    eso lo decide administración y no cualquier agente ni supervisión.

    WhatsApp no deja mandar texto libre a quien no ha escrito en las ultimas 24
    horas; fuera de esa ventana solo admite plantillas aprobadas. Por eso aqui
    no hay campo de texto: se elige una plantilla y se rellenan sus huecos. En
    cuanto la persona conteste, el hilo queda abierto y la consola responde con
    texto normal como en cualquier otra conversacion.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    accounts = await repo.list_channel_accounts(session, tenant_id=tenant_row.id)
    account = next(
        (a for a in accounts if a.channel == ChannelKind.WHATSAPP and a.is_active), None
    )
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay ninguna cuenta de WhatsApp activa",
        )

    adapter = orchestrator.registry.get(ChannelKind.WHATSAPP)
    try:
        plantillas = await adapter.list_templates()
    except TemplatesUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    # Se comprueba contra la lista real de Meta en vez de confiar en lo que
    # llega: enviar una plantilla inexistente, en otro idioma o con un numero
    # de parametros distinto lo rechaza la Graph API con un error opaco, y el
    # mensaje se perderia en la cola sin que nadie entienda por que.
    elegida = next(
        (
            t
            for t in plantillas
            if t["name"] == body.template and t["language"] == body.language
        ),
        None,
    )
    if elegida is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay plantilla aprobada «{body.template}» en {body.language}",
        )
    if len(body.variables) != elegida["variables"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"La plantilla necesita {elegida['variables']} dato(s) "
                f"y llegaron {len(body.variables)}"
            ),
        )

    # Se replica lo que hace el adaptador con un mensaje entrante, para que la
    # conversacion sea indistinguible de una que empezo el cliente: el
    # identificador del hilo es el propio numero, y el contacto se unifica por
    # telefono con el que ya exista.
    ref = ConversationRef(
        channel=ChannelKind.WHATSAPP,
        channel_conversation_id=body.to,
        channel_account_id=account.external_id,
        extra={"wa_id": body.to},
    )
    contact = await repo.resolve_contact(
        session,
        tenant_id=tenant_row.id,
        channel=ChannelKind.WHATSAPP,
        party=Party(channel_user_id=body.to, phone=f"+{body.to}", raw={"wa_id": body.to}),
    )
    conversation = await repo.resolve_conversation(
        session,
        tenant_id=tenant_row.id,
        ref=ref,
        contact_id=contact.id,
        channel_account=account,
    )

    plantilla: dict[str, Any] = {
        "name": body.template,
        "language": {"code": body.language},
    }
    if body.variables:
        plantilla["components"] = [
            {
                "type": "body",
                "parameters": [{"type": "text", "text": v} for v in body.variables],
            }
        ]

    outbox_id = await orchestrator.send_from_agent(
        conversation_id=conversation.id,
        outbound=OutboundMessage(
            text=_render_template_body(elegida["body"], body.variables),
            content_type=ContentType.TEXT,
            channel_data={"template": plantilla},
        ),
        agent_id=principal.id,
        session=session,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="conversation_started",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"to": body.to, "template": body.template},
    )
    return {
        "status": "queued",
        "conversation_id": str(conversation.id),
        "outbox_id": str(outbox_id),
    }


# --------------------------------------------------------------------------- #
# Adjuntos
# --------------------------------------------------------------------------- #
@router.post("/uploads", status_code=status.HTTP_201_CREATED)
async def upload_image(
    file: UploadFile,
    principal: PrincipalDep,
    settings: SettingsDep,
    session: SessionDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Sube una imagen para adjuntarla a la próxima respuesta de un agente."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    attachment = await save_upload(file, namespace=str(tenant_row.id), settings=settings)
    return attachment.to_dict()


@router.post("/conversations/{conversation_id}/reopen")
async def reopen_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, str]:
    """Reabre un hilo cerrado sin perder nada de lo conversado."""
    conversation.status = "open"
    await repo.record_assignment(
        session,
        conversation=conversation,
        action="reopen",
        to_agent_id=conversation.assignee_id,
        by_agent_id=principal.id,
    )
    return {"status": "ok", "conversation_status": "open"}


# --------------------------------------------------------------------------- #
# Derivación entre compañeros
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation: ConversationDep, session: SessionDep, principal: PrincipalDep
) -> dict[str, Any]:
    """Toma una conversación de la cola común."""
    if principal.agent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La clave de servicio no puede atender conversaciones; inicie sesión",
        )
    if conversation.assignee_id not in (None, principal.agent.id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Otro compañero ya la está atendiendo",
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="claim",
        to_agent_id=principal.agent.id,
        by_agent_id=principal.agent.id,
    )
    await _notify_assignment(session, conversation, entry, principal, target=principal.agent)
    return {
        "status": "ok",
        "assignee_id": str(principal.agent.id),
        "assignee_name": principal.agent.label,
        "control": conversation.control,
    }


@router.post("/conversations/{conversation_id}/transfer")
async def transfer_conversation(
    conversation: ConversationDep,
    body: TransferIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Deriva la conversación a un compañero, o a la cola de un departamento.

    Un agente solo puede derivar lo que atiende; supervisión puede reasignar
    cualquier conversación. El motivo, si se indica, queda además como nota
    interna, de modo que quien la recibe entiende el contexto sin preguntar.
    """
    if (body.to_agent_id is None) == (body.to_department_id is None):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Elija un compañero o un departamento, no ambos ni ninguno",
        )
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id not in (None, principal.agent.id)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puede derivar las conversaciones que atiende",
        )

    if body.to_department_id is not None:
        return await _transfer_to_department(conversation, body, session, principal)

    target = await repo.get_agent(session, body.to_agent_id)
    if target is None or target.tenant_id != conversation.tenant_id or not target.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="El destinatario no existe o está inactivo",
        )
    if target.id == conversation.assignee_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="La conversación ya es suya"
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="transfer",
        to_agent_id=target.id,
        by_agent_id=principal.id,
        note=body.note,
    )
    if body.note:
        await repo.add_internal_note(
            session,
            conversation=conversation,
            agent_id=principal.id,
            body=f"Derivada a {target.label}: {body.note}",
            mentions=[str(target.id)],
        )
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_transferred",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"to": target.email, "note": body.note},
    )
    await _notify_assignment(session, conversation, entry, principal, target=target)
    log.info(
        "conversation_transferred",
        conversation_id=str(conversation.id),
        by=principal.label,
        to=target.email,
    )
    return {
        "status": "ok",
        "assignee_id": str(target.id),
        "assignee_name": target.label,
        "control": conversation.control,
    }


@router.post("/conversations/{conversation_id}/release")
async def release_conversation(
    conversation: ConversationDep,
    body: ReleaseIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, Any]:
    """Devuelve la conversación a la cola común y reactiva al asistente."""
    if (
        principal.agent is not None
        and not principal.is_supervisor
        and conversation.assignee_id != principal.agent.id
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo puede liberar las conversaciones que atiende",
        )

    entry = await repo.record_assignment(
        session,
        conversation=conversation,
        action="release",
        to_agent_id=None,
        by_agent_id=principal.id,
        note=body.note,
    )
    await _notify_assignment(session, conversation, entry, principal)
    return {
        "status": "ok",
        "assignee_id": None,
        "assignee_name": None,
        "control": conversation.control,
    }


@router.get("/conversations/{conversation_id}/assignments", response_model=list[AssignmentOut])
async def assignment_history(
    conversation: ConversationDep, session: SessionDep
) -> list[AssignmentOut]:
    """Traza de derivaciones: quién la atendió, quién la pasó y por qué."""
    entries = await repo.assignment_history(session, conversation.id)
    names = await _agent_names(session, conversation.tenant_id)
    department_names = await _department_names(session, conversation.tenant_id)
    return [
        AssignmentOut(
            id=entry.id,
            action=entry.action,
            from_agent=names.get(entry.from_agent_id),
            to_agent=names.get(entry.to_agent_id),
            to_department=department_names.get(entry.to_department_id),
            by_agent=names.get(entry.by_agent_id),
            note=entry.note,
            created_at=entry.created_at.isoformat(),
        )
        for entry in entries
    ]


# --------------------------------------------------------------------------- #
# Notas internas
# --------------------------------------------------------------------------- #
@router.get("/conversations/{conversation_id}/notes", response_model=list[NoteOut])
async def list_notes(conversation: ConversationDep, session: SessionDep) -> list[NoteOut]:
    notes = await repo.list_internal_notes(session, conversation.id)
    names = await _agent_names(session, conversation.tenant_id)
    return [
        NoteOut(
            id=note.id,
            agent=names.get(note.agent_id),
            body=note.body,
            created_at=note.created_at.isoformat(),
        )
        for note in notes
    ]


@router.post("/conversations/{conversation_id}/notes", status_code=status.HTTP_201_CREATED)
async def add_note(
    conversation: ConversationDep,
    body: NoteIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Añade una anotación visible solo para el equipo; el cliente no la recibe."""
    note = await repo.add_internal_note(
        session,
        conversation=conversation,
        agent_id=principal.id,
        body=body.body,
        mentions=[str(mention) for mention in body.mentions],
    )
    for mentioned in body.mentions:
        await hub.publish(
            agent_topic(str(mentioned)),
            {
                "type": "mentioned",
                "conversation_id": str(conversation.id),
                "by": principal.label,
                "body": body.body[:200],
            },
        )
    return {"status": "ok", "note_id": str(note.id)}


# --------------------------------------------------------------------------- #
# Estado del hilo
# --------------------------------------------------------------------------- #
@router.post("/conversations/{conversation_id}/control")
async def set_control(
    conversation: ConversationDep,
    body: ControlIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> dict[str, str]:
    """Alterna entre atención automática y atención humana."""
    await repo.set_conversation_control(session, conversation.id, body.control)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="control_changed",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"control": body.control},
    )
    assignee = (
        await repo.get_agent(session, conversation.assignee_id)
        if conversation.assignee_id
        else None
    )
    await _publish_control_change(
        conversation, control=body.control, assignee_name=assignee.label if assignee else None
    )
    return {"status": "ok", "control": body.control}


async def _ask_for_a_score(
    conversation: Any,
    session: Any,
    settings: Any,
    orchestrator: Any,
) -> None:
    """Envía la encuesta de satisfacción si la rama la tiene activa.

    Se llama al cerrar, por los dos caminos que cierran —el botón de cerrar y
    el estado «resuelta»—, para que la medida no dependa de por cuál se pasó.
    Un fallo aquí no impide cerrar: lo que importaba era cerrar.
    """
    if conversation.department_id is None:
        return
    department = await repo.get_department(session, conversation.department_id)
    if not csat.enabled(department):
        return

    survey, es_nueva = await csat.open_survey(session, conversation)
    if not es_nueva:
        # Ya se le preguntó por esta conversación; no se insiste al reabrir y
        # volver a cerrar.
        return

    message = csat.build_message(
        conversation=conversation,
        survey=survey,
        department=department,
        public_base_url=settings.public_base_url,
    )
    try:
        await orchestrator.send_from_agent(
            session=session,
            conversation_id=conversation.id,
            outbound=message,
            # Sin agente: la pregunta la manda el sistema, no quien cerró. Que
            # figure a su nombre haría pensar que la escribió esa persona.
            agent_id=None,
        )
    except Exception:  # noqa: BLE001 - un canal caído no debe impedir cerrar
        log.warning("csat_not_sent", conversation=str(conversation.id))
        return

    # Solo se espera la nota donde puede llegar por el propio hilo; en correo y
    # SMS la respuesta vuelve por la página del enlace.
    if conversation.channel in csat.CHANNELS_WITH_BUTTONS:
        conversation.state = {**conversation.state, csat.STATE_KEY: True}


@router.post("/conversations/{conversation_id}/close")
async def close_conversation(
    conversation: ConversationDep,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, str]:
    """Cierra el hilo. El historial permanece y se recupera al reabrirse."""
    conversation.status = "closed"
    await repo.record_assignment(
        session,
        conversation=conversation,
        action="close",
        to_agent_id=conversation.assignee_id,
        by_agent_id=principal.id,
    )
    await _ask_for_a_score(conversation, session, settings, orchestrator)
    await _publish_state_change(conversation, status="closed")
    return {"status": "ok", "conversation_status": "closed"}


@router.post("/conversations/{conversation_id}/state")
async def set_conversation_state(
    conversation: ConversationDep,
    body: ConversationStateIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, str]:
    """Marca en qué punto está la conversación: pendiente, en curso o resuelta.

    Sustituye al botón único de cerrar: entre «sin tocar» y «terminada» hay un
    estado intermedio que antes no se podía expresar, y sin él una conversación
    que alguien ya estaba resolviendo era indistinguible de otra que nadie
    había mirado.
    """
    target = WORK_STATES[body.state]
    if conversation.status == target:
        return {"status": "ok", "conversation_status": target}

    was_closed = conversation.status == "closed"
    conversation.status = target
    # La traza reutiliza las acciones que ya existían para los dos extremos;
    # el paso a «en proceso» no es una derivación y no se anota como tal.
    if target == "closed":
        action = "close"
    elif was_closed:
        action = "reopen"
    else:
        action = None

    if action:
        await repo.record_assignment(
            session,
            conversation=conversation,
            action=action,
            to_agent_id=conversation.assignee_id,
            by_agent_id=principal.id,
        )
    if target == "closed":
        await _ask_for_a_score(conversation, session, settings, orchestrator)
    # También al reabrir: el cliente tiene que poder volver a escribir.
    if target == "closed" or was_closed:
        await _publish_state_change(conversation, status=target)

    log.info(
        "conversation_state_set",
        conversation=str(conversation.id),
        state=body.state,
        by=principal.label,
    )
    return {"status": "ok", "conversation_status": target}
