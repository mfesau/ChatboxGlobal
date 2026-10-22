"""Configuración: respuestas guardadas, etiquetas, macros y ajustes del inquilino."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.api.console._common import (
    ConversationDep,
    _label_out,
    _require_known_timezone,
    _transfer_to_department,
)
from app.api.console.schemas import (
    AdminSettingsIn,
    BrandingIn,
    BusinessHoursIn,
    CannedResponseIn,
    CannedResponseOut,
    CannedResponsePatchIn,
    ConversationLabelsIn,
    LabelIn,
    LabelOut,
    MacroIn,
    MacroOut,
    MacroStep,
    TransferIn,
)
from app.api.deps import (
    AdminDep,
    OrchestratorDep,
    Principal,
    PrincipalDep,
    SessionDep,
    SettingsDep,
)
from app.core.branding import (
    BRANDING_SETTINGS_KEY,
    InvalidColor,
    brand_css,
    derive_palette,
    normalize_hex,
    read_accent,
)
from app.core.business_hours import SERVICE_SETTINGS_KEY
from app.core.envelope import (
    ContentType,
    OutboundMessage,
)
from app.db import repositories as repo
from app.db.models import (
    CannedResponse,
)
from app.handlers.builtin import FallbackHandler
from app.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["consola"])



# --------------------------------------------------------------------------- #
# Respuestas guardadas
# --------------------------------------------------------------------------- #
def _canned_response_out(row: CannedResponse) -> CannedResponseOut:
    return CannedResponseOut(
        id=row.id,
        shortcode=row.shortcode,
        title=row.title,
        body=row.body,
        department_id=row.department_id,
    )


def _usable_by(principal: Principal, row: Any) -> bool:
    """¿Puede quien pregunta ver y usar este recurso de un departamento?"""
    allowed = principal.department_ids
    return allowed is None or row.department_id is None or row.department_id in allowed


async def _require_department(
    session: Any, *, tenant_id: uuid.UUID, department_id: uuid.UUID | None
) -> None:
    """El departamento al que se ata un recurso tiene que existir en el inquilino."""
    if department_id is None:
        return
    department = await repo.get_department(session, department_id)
    if department is None or department.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="El departamento no existe"
        )


@router.get("/canned-responses", response_model=list[CannedResponseOut])
async def list_canned_responses(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[CannedResponseOut]:
    """Respuestas guardadas que quien pregunta puede usar: las de toda la
    empresa y las de sus departamentos. Administración las ve todas."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    canned_responses = await repo.list_canned_responses(
        session, tenant_id=tenant_row.id, department_ids=principal.department_ids
    )
    return [_canned_response_out(row) for row in canned_responses]


@router.post(
    "/canned-responses", status_code=status.HTTP_201_CREATED, response_model=CannedResponseOut
)
async def create_canned_response(
    body: CannedResponseIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> CannedResponseOut:
    """Crea una respuesta guardada. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_canned_response_by_shortcode(
        session, tenant_id=tenant_row.id, shortcode=body.shortcode
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una respuesta con ese atajo"
        )
    await _require_department(session, tenant_id=tenant_row.id, department_id=body.department_id)
    canned_response = await repo.create_canned_response(
        session,
        tenant_id=tenant_row.id,
        shortcode=body.shortcode,
        title=body.title,
        body=body.body,
        department_id=body.department_id,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="canned_response_created",
        subject_type="canned_response",
        subject_id=str(canned_response.id),
        detail={"shortcode": canned_response.shortcode},
    )
    return _canned_response_out(canned_response)


@router.patch("/canned-responses/{canned_response_id}", response_model=CannedResponseOut)
async def update_canned_response(
    canned_response_id: uuid.UUID,
    body: CannedResponsePatchIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> CannedResponseOut:
    """Edita una respuesta guardada. Solo se aplican los campos recibidos."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    canned_response = await repo.get_canned_response(session, canned_response_id)
    if canned_response is None or canned_response.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta guardada no encontrada"
        )
    if body.shortcode is not None and body.shortcode != canned_response.shortcode:
        existing = await repo.find_canned_response_by_shortcode(
            session, tenant_id=canned_response.tenant_id, shortcode=body.shortcode
        )
        if existing is not None and existing.id != canned_response.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ya existe una respuesta con ese atajo",
            )
        canned_response.shortcode = body.shortcode
    if body.title is not None:
        canned_response.title = body.title
    if body.body is not None:
        canned_response.body = body.body
    if "department_id" in body.model_fields_set:
        await _require_department(
            session, tenant_id=tenant_row.id, department_id=body.department_id
        )
        canned_response.department_id = body.department_id
    await session.flush()
    return _canned_response_out(canned_response)


@router.delete("/canned-responses/{canned_response_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_canned_response(
    canned_response_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una respuesta guardada. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    canned_response = await repo.get_canned_response(session, canned_response_id)
    if canned_response is None or canned_response.tenant_id != tenant_row.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Respuesta guardada no encontrada"
        )
    await repo.delete_canned_response(session, canned_response)
    await repo.record_audit(
        session,
        tenant_id=canned_response.tenant_id,
        actor=principal.audit_actor,
        action="canned_response_deleted",
        subject_type="canned_response",
        subject_id=str(canned_response.id),
        detail={"shortcode": canned_response.shortcode},
    )


# --------------------------------------------------------------------------- #
# Etiquetas
# --------------------------------------------------------------------------- #
@router.get("/labels", response_model=list[LabelOut])
async def list_labels(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[LabelOut]:
    """Etiquetas que quien pregunta puede usar: las de toda la empresa y las
    de sus departamentos. Administración las ve todas."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    labels = await repo.list_labels(
        session, tenant_id=tenant_row.id, department_ids=principal.department_ids
    )
    return [_label_out(label) for label in labels]


@router.post("/labels", status_code=status.HTTP_201_CREATED, response_model=LabelOut)
async def create_label(
    body: LabelIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> LabelOut:
    """Crea una etiqueta. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_label_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una etiqueta con ese nombre"
        )
    await _require_department(session, tenant_id=tenant_row.id, department_id=body.department_id)
    label = await repo.create_label(
        session,
        tenant_id=tenant_row.id,
        name=body.name,
        color=body.color,
        department_id=body.department_id,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="label_created",
        subject_type="label",
        subject_id=str(label.id),
        detail={"name": label.name},
    )
    return _label_out(label)


@router.delete("/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(
    label_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una etiqueta. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    label = await repo.get_label(session, label_id)
    if label is None or label.tenant_id != tenant_row.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etiqueta no encontrada")
    await repo.delete_label(session, label)
    await repo.record_audit(
        session,
        tenant_id=label.tenant_id,
        actor=principal.audit_actor,
        action="label_deleted",
        subject_type="label",
        subject_id=str(label.id),
        detail={"name": label.name},
    )


@router.put("/conversations/{conversation_id}/labels", response_model=list[LabelOut])
async def set_conversation_labels(
    conversation: ConversationDep,
    body: ConversationLabelsIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> list[LabelOut]:
    """Fija las etiquetas de una conversación. Cualquiera con acceso a ella puede hacerlo.

    Una etiqueta nueva tiene que ser de toda la empresa o del departamento de
    la conversación. Las que ya tenía se respetan aunque no lo sean —una
    conversación derivada conserva lo que le pusieron antes—: quitarlas al
    tocar otra sería borrar algo que nadie pidió borrar.
    """
    await session.refresh(conversation, attribute_names=["labels"])
    ya_puestas = {label.id for label in conversation.labels}
    for label_id in body.label_ids:
        label = await repo.get_label(session, label_id)
        if label is None or label.tenant_id != conversation.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Una de las etiquetas indicadas no existe",
            )
        if label_id not in ya_puestas and not (
            repo.fits_department(label, conversation.department_id)
            and _usable_by(principal, label)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"La etiqueta «{label.name}» es de otro departamento",
            )
    await repo.set_conversation_labels(session, conversation=conversation, label_ids=body.label_ids)
    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="conversation_labels_updated",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"label_ids": [str(i) for i in body.label_ids]},
    )
    return [
        _label_out(label)
        for label in conversation.labels
    ]


# --------------------------------------------------------------------------- #
# Macros
# --------------------------------------------------------------------------- #
def _macro_out(row: Any) -> MacroOut:
    return MacroOut(
        id=row.id,
        name=row.name,
        steps=[MacroStep(**s) for s in row.steps or []],
        department_id=row.department_id,
    )


@router.get("/macros", response_model=list[MacroOut])
async def list_macros(
    session: SessionDep, settings: SettingsDep, principal: PrincipalDep, tenant: str | None = None
) -> list[MacroOut]:
    """Macros que quien pregunta puede ejecutar: las de toda la empresa y las
    de sus departamentos. Administración las ve todas."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    rows = await repo.list_macros(
        session, tenant_id=tenant_row.id, department_ids=principal.department_ids
    )
    return [_macro_out(row) for row in rows]


@router.post("/macros", status_code=status.HTTP_201_CREATED, response_model=MacroOut)
async def create_macro(
    body: MacroIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> MacroOut:
    """Crea una macro. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    if await repo.find_macro_by_name(session, tenant_id=tenant_row.id, name=body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Ya existe una macro con ese nombre"
        )
    await _require_department(session, tenant_id=tenant_row.id, department_id=body.department_id)
    # Lo que la macro apunta debe existir ahora: guardar una macro rota sería
    # descubrirlo recién el día que alguien la use. Y debe valer allí donde la
    # macro se usa: una macro de toda la empresa no puede poner una etiqueta
    # del Hotel en una conversación de Informática.
    for step in body.steps:
        target = await _resolve_macro_step(session, step, tenant_id=tenant_row.id)
        if (
            step.action in {"label", "reply"}
            and target.department_id is not None
            and target.department_id != body.department_id
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    "Un paso usa una etiqueta o respuesta de otro departamento que el "
                    "de la macro"
                ),
            )

    macro = await repo.create_macro(
        session,
        tenant_id=tenant_row.id,
        name=body.name,
        steps=[s.model_dump(mode="json", exclude_none=True) for s in body.steps],
        department_id=body.department_id,
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="macro_created",
        subject_type="macro",
        subject_id=str(macro.id),
        detail={"name": macro.name, "pasos": len(body.steps)},
    )
    return _macro_out(macro)


@router.delete("/macros/{macro_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_macro(
    macro_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> None:
    """Borra una macro. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    macro = await repo.get_macro(session, macro_id)
    if macro is None or macro.tenant_id != tenant_row.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Macro no encontrada")
    await repo.delete_macro(session, macro)
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="macro_deleted",
        subject_type="macro",
        subject_id=str(macro.id),
        detail={"name": macro.name},
    )


async def _resolve_macro_step(
    session: SessionDep, step: MacroStep, *, tenant_id: uuid.UUID
) -> Any:
    """Comprueba que lo que el paso apunta existe y es de este inquilino."""
    if step.action == "label":
        label = await repo.get_label(session, step.label_id)
        if label is None or label.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="La etiqueta del paso no existe"
            )
        return label
    if step.action == "transfer_department":
        department = await repo.get_department(session, step.department_id)
        if department is None or department.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="El departamento del paso no existe"
            )
        return department
    if step.action == "reply":
        canned = await repo.get_canned_response(session, step.canned_response_id)
        if canned is None or canned.tenant_id != tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La respuesta guardada del paso no existe",
            )
        return canned
    return None


@router.post("/conversations/{conversation_id}/macros/{macro_id}", status_code=status.HTTP_200_OK)
async def run_macro(
    macro_id: uuid.UUID,
    conversation: ConversationDep,
    session: SessionDep,
    principal: PrincipalDep,
    orchestrator: OrchestratorDep,
) -> dict[str, Any]:
    """Ejecuta la macro sobre la conversación, paso a paso y en orden.

    Todo ocurre en la misma transacción de la petición: si un paso falla, no
    queda la conversación a medio procesar —etiquetada y derivada, pero sin la
    respuesta que la macro prometía—.
    """
    macro = await repo.get_macro(session, macro_id)
    if (
        macro is None
        or macro.tenant_id != conversation.tenant_id
        or not _usable_by(principal, macro)
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Macro no encontrada")
    if not repo.fits_department(macro, conversation.department_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Esta macro es de otro departamento que la conversación",
        )

    aplicados: list[str] = []
    for raw in macro.steps or []:
        step = MacroStep(**raw)
        target = await _resolve_macro_step(session, step, tenant_id=conversation.tenant_id)

        if step.action == "label":
            # ``authorized_conversation`` trae la fila sin las etiquetas, y
            # leerlas sin más dispararía una carga diferida en pleno async.
            await session.refresh(conversation, attribute_names=["labels"])
            actuales = [row.id for row in conversation.labels]
            # Repetir la macro no debe duplicar lo que ya está puesto.
            if target.id not in actuales:
                await repo.set_conversation_labels(
                    session, conversation=conversation, label_ids=[*actuales, target.id]
                )
        elif step.action == "note":
            await repo.add_internal_note(
                session, conversation=conversation, agent_id=principal.id, body=step.body
            )
        elif step.action == "reply":
            await orchestrator.send_from_agent(
                conversation_id=conversation.id,
                outbound=OutboundMessage(text=target.body, content_type=ContentType.TEXT),
                agent_id=principal.id,
                session=session,
            )
            await repo.mark_first_human_response(session, conversation=conversation)
        elif step.action == "transfer_department":
            await _transfer_to_department(
                conversation,
                TransferIn(to_department_id=target.id, note=step.body),
                session,
                principal,
            )
        elif step.action == "close":
            conversation.status = "closed"
            await repo.record_assignment(
                session,
                conversation=conversation,
                action="close",
                to_agent_id=conversation.assignee_id,
                by_agent_id=principal.id,
            )
        aplicados.append(step.action)

    await repo.record_audit(
        session,
        tenant_id=conversation.tenant_id,
        actor=principal.audit_actor,
        action="macro_run",
        subject_type="conversation",
        subject_id=str(conversation.id),
        detail={"macro": macro.name, "pasos": aplicados},
    )
    log.info("macro_ejecutada", macro=macro.name, conversation=str(conversation.id))
    return {"status": "ok", "applied": aplicados}


# --------------------------------------------------------------------------- #
# Configuración del inquilino
# --------------------------------------------------------------------------- #
@router.get("/admin/settings")
async def get_admin_settings(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> dict[str, Any]:
    """Ajustes editables desde la consola. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    fallback_message = tenant_row.settings.get("fallback_message") or FallbackHandler.MESSAGE
    return {"fallback_message": fallback_message}


@router.put("/admin/settings")
async def update_admin_settings(
    body: AdminSettingsIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Actualiza la respuesta automática del asistente. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    await repo.update_tenant_settings(
        session, tenant_row, fallback_message=body.fallback_message
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="fallback_message_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
    )
    return {"status": "ok", "fallback_message": tenant_row.settings["fallback_message"]}


def _branding_payload(accent: str | None) -> dict[str, Any]:
    """Lo que necesita la consola: el color guardado y el CSS ya derivado.

    Se devuelve el CSS hecho, y no solo los colores, para que la consola pueda
    repintarse al guardar sin repetir en JavaScript el calculo de contraste que
    ya vive en ``app/core/branding.py``. Una segunda copia de esa aritmetica
    acabaria desviandose de la primera.
    """
    return {
        "accent": accent,
        "css": brand_css(accent),
        "palette": derive_palette(accent) if accent else None,
    }


@router.get("/admin/branding")
async def get_branding(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> dict[str, Any]:
    """Color de marca vigente. Reservado a administración."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    return _branding_payload(read_accent(tenant_row.settings))


@router.get("/admin/branding/preview")
async def preview_branding(
    principal: AdminDep, accent: str = Query(min_length=4, max_length=7)
) -> dict[str, Any]:
    """Deriva la paleta de un color sin guardarlo.

    Permite que la consola enseñe como quedaria el color antes de fijarlo, sin
    que haya que guardar para verlo y sin repetir en el navegador el calculo de
    contraste. No toca la base: solo hace cuentas.
    """
    try:
        return _branding_payload(normalize_hex(accent))
    except InvalidColor as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/admin/branding")
async def set_branding(
    body: BrandingIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> dict[str, Any]:
    """Fija el color de marca de toda la instalación. Reservado a administración.

    Se guarda un solo color: el resto de la paleta —el texto que va encima, la
    variante que se lee sobre el fondo, la de tema oscuro— se deriva de él con
    los umbrales de contraste de la WCAG. Es lo que impide que una elección
    desafortunada deje botones o enlaces ilegibles.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    await repo.update_tenant_settings(
        session,
        tenant_row,
        **{BRANDING_SETTINGS_KEY: {"accent": body.accent} if body.accent else {}},
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="branding_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
        detail={"accent": body.accent},
    )
    return _branding_payload(body.accent)


@router.get("/admin/service-defaults", response_model=BusinessHoursIn)
async def get_service_defaults(
    session: SessionDep, settings: SettingsDep, principal: AdminDep, tenant: str | None = None
) -> BusinessHoursIn:
    """Horario y objetivo que rigen la cola común y lo que no fija su propio valor."""
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    stored = tenant_row.settings.get(SERVICE_SETTINGS_KEY) or {}
    return BusinessHoursIn(**stored)


@router.put("/admin/service-defaults", response_model=BusinessHoursIn)
async def set_service_defaults(
    body: BusinessHoursIn,
    session: SessionDep,
    settings: SettingsDep,
    principal: AdminDep,
    tenant: str | None = None,
) -> BusinessHoursIn:
    """Fija lo que rige por omisión. Reservado a administración.

    Es lo que permite medir también la cola común: una conversación que aún no
    se derivó a nadie no tiene departamento del que heredar un objetivo.
    """
    tenant_row = await repo.get_or_create_tenant(session, tenant or settings.default_tenant_slug)
    _require_known_timezone(body.timezone)
    await repo.update_tenant_settings(
        session,
        tenant_row,
        **{SERVICE_SETTINGS_KEY: body.model_dump(mode="json", exclude_none=True)},
    )
    await repo.record_audit(
        session,
        tenant_id=tenant_row.id,
        actor=principal.audit_actor,
        action="service_defaults_updated",
        subject_type="tenant",
        subject_id=str(tenant_row.id),
        detail={
            "timezone": body.timezone,
            "objetivo_minutos": body.first_response_target_minutes,
        },
    )
    return body
