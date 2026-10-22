"""Lo de un departamento no se ve desde otro.

La bandeja y la API de cada conversación ya respetaban los departamentos, pero
otras vías no: los avisos en directo de la consola, el directorio de contactos
de supervisión, la exportación RGPD, la traza de auditoría, los mensajes
programados y el consumo de la IA. Estas pruebas fijan cada una de ellas.

Escenario: Marta supervisa solo «Hotel»; Ana es agente de «Hotel»; hay una
conversación en «Hotel» y otra en «Informática».
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest_asyncio

from app.api.ws import _make_inbox_sender
from app.core.hub import access_scope
from app.db import repositories as repo
from app.db.engine import session_scope
from app.db.models import AIRun
from tests.test_teamwork import arrive


@pytest_asyncio.fixture
async def ramas(anonymous, as_agent, team) -> dict[str, Any]:
    admin = await as_agent(team["admin"]["email"])
    hotel = (await admin.post("/api/departments", json={"name": "Hotel"})).json()
    informatica = (await admin.post("/api/departments", json={"name": "Informática"})).json()
    for persona in ("marta", "ana"):
        respuesta = await admin.put(
            f"/api/agents/{team[persona]['id']}/departments",
            json={"department_id": hotel["id"], "extra_department_ids": []},
        )
        assert respuesta.status_code == 200, respuesta.text

    del_hotel = await arrive(anonymous, "cliente-del-hotel", "Quiero reservar")
    de_informatica = await arrive(anonymous, "cliente-de-informatica", "No anda el router")
    for conversacion, rama in ((del_hotel, hotel), (de_informatica, informatica)):
        await admin.post(
            f"/api/conversations/{conversacion}/transfer", json={"to_department_id": rama["id"]}
        )

    async with session_scope() as session:
        ajena = await repo.get_conversation(session, uuid.UUID(de_informatica))
        contacto_ajeno = str(ajena.contact_id)
        propia = await repo.get_conversation(session, uuid.UUID(del_hotel))
        contacto_propio = str(propia.contact_id)

    return {
        "admin": admin,
        "hotel": hotel,
        "informatica": informatica,
        "propia": del_hotel,
        "ajena": de_informatica,
        "contacto_propio": contacto_propio,
        "contacto_ajeno": contacto_ajeno,
    }


# --------------------------------------------------------------------------- #
# Avisos en directo
# --------------------------------------------------------------------------- #
class _Socket:
    def __init__(self) -> None:
        self.recibido: list[dict[str, Any]] = []

    async def send_json(self, event: dict[str, Any]) -> None:
        self.recibido.append(event)


async def test_live_notices_of_another_department_carry_no_content(ramas, team):
    socket = _Socket()
    enviar = _make_inbox_sender(socket, team["ana"]["id"])

    await enviar({"type": "message", "conversation_id": ramas["ajena"], "text": "router"})
    await enviar({"type": "message", "conversation_id": ramas["propia"], "text": "reserva"})

    assert socket.recibido[0] == {"type": "refresh", "conversation_id": ramas["ajena"]}
    assert socket.recibido[1]["text"] == "reserva"


async def test_the_scope_in_the_notice_decides_before_the_row_is_committed(ramas, team):
    """El primer mensaje de un cliente sale antes de que su conversación exista."""
    socket = _Socket()
    enviar = _make_inbox_sender(socket, team["ana"]["id"])
    nueva = str(uuid.uuid4())
    hotel = uuid.UUID(ramas["hotel"]["id"])
    informatica = uuid.UUID(ramas["informatica"]["id"])

    def evento(departamento: uuid.UUID) -> dict[str, Any]:
        fila = type("C", (), {"department_id": departamento, "assignee_id": None})()
        return {"type": "message", "conversation_id": nueva, "text": "hola", **access_scope(fila)}

    await enviar(evento(hotel))
    await enviar(evento(informatica))

    assert socket.recibido[0]["text"] == "hola"
    assert "_access" not in socket.recibido[0]
    assert socket.recibido[1] == {"type": "refresh", "conversation_id": nueva}


async def test_administration_receives_every_notice(ramas, team):
    socket = _Socket()
    enviar = _make_inbox_sender(socket, team["admin"]["id"])

    await enviar({"type": "message", "conversation_id": ramas["ajena"], "text": "router"})

    assert socket.recibido[0]["text"] == "router"


# --------------------------------------------------------------------------- #
# Directorio de contactos y exportación
# --------------------------------------------------------------------------- #
async def test_a_scoped_supervisor_only_lists_contacts_of_her_departments(
    ramas, as_agent, team
):
    marta = await as_agent(team["marta"]["email"])

    suyos = {fila["id"] for fila in (await marta.get("/api/contacts")).json()}
    todos = {fila["id"] for fila in (await ramas["admin"].get("/api/contacts")).json()}

    assert ramas["contacto_propio"] in suyos
    assert ramas["contacto_ajeno"] not in suyos
    assert ramas["contacto_ajeno"] in todos


async def test_a_contact_of_another_department_cannot_be_opened_by_id(ramas, as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    ajeno = ramas["contacto_ajeno"]

    assert (await marta.get(f"/api/contacts/{ajeno}")).status_code == 404
    assert (
        await marta.patch(f"/api/contacts/{ajeno}", json={"display_name": "X"})
    ).status_code == 404
    assert (
        await marta.post(f"/api/contacts/{ajeno}/comments", json={"body": "nota"})
    ).status_code == 404

    propio = (await marta.get(f"/api/contacts/{ramas['contacto_propio']}")).json()
    assert {c["id"] for c in propio["conversations"]} == {ramas["propia"]}


async def test_only_administration_exports_a_contacts_data(ramas, as_agent, team):
    marta = await as_agent(team["marta"]["email"])
    contacto = ramas["contacto_propio"]

    assert (await marta.get(f"/api/contacts/{contacto}/export")).status_code == 403
    assert (await ramas["admin"].get(f"/api/contacts/{contacto}/export")).status_code == 200


# --------------------------------------------------------------------------- #
# Traza de auditoría
# --------------------------------------------------------------------------- #
async def test_a_scoped_supervisor_sees_only_the_trail_of_her_departments(
    ramas, as_agent, team
):
    marta = await as_agent(team["marta"]["email"])

    traza = (await marta.get("/api/audit?limit=500")).json()
    sujetos = {fila["subject_id"] for fila in traza}

    assert ramas["propia"] in sujetos
    assert ramas["ajena"] not in sujetos
    # Ni la configuración de administración (alta de departamentos, permisos).
    assert all(
        fila["subject_type"] == "conversation" or "marta" in fila["actor"] for fila in traza
    )

    traza_admin = (await ramas["admin"].get("/api/audit?limit=500")).json()
    completa = {fila["subject_id"] for fila in traza_admin}
    assert ramas["ajena"] in completa


# --------------------------------------------------------------------------- #
# Mensajes programados
# --------------------------------------------------------------------------- #
async def test_scheduled_messages_of_another_department_stay_hidden(ramas, as_agent, team):
    ana = await as_agent(team["ana"]["email"])

    assert (await ana.get(f"/api/conversations/{ramas['ajena']}/scheduled")).status_code == 404
    assert (await ana.get(f"/api/conversations/{ramas['propia']}/scheduled")).status_code == 200


# --------------------------------------------------------------------------- #
# Consumo de la IA en los informes
# --------------------------------------------------------------------------- #
async def test_ai_usage_in_reports_follows_the_department_scope(ramas, as_agent, team):
    async with session_scope() as session:
        for conversacion, tokens in ((ramas["propia"], 100), (ramas["ajena"], 1_000)):
            fila = await repo.get_conversation(session, uuid.UUID(conversacion))
            session.add(
                AIRun(
                    tenant_id=fila.tenant_id,
                    conversation_id=fila.id,
                    model="prueba/modelo",
                    handler="ai",
                    input_tokens=tokens,
                    output_tokens=0,
                )
            )

    marta = await as_agent(team["marta"]["email"])
    suyo = (await marta.get("/api/reports/summary")).json()["ai"]
    total = (await ramas["admin"].get("/api/reports/summary")).json()["ai"]

    assert suyo["input_tokens"] == 100
    assert total["input_tokens"] == 1_100


# --------------------------------------------------------------------------- #
# Respuestas guardadas, etiquetas y macros por departamento
# --------------------------------------------------------------------------- #
async def _recursos(ramas) -> dict[str, Any]:
    """Una de cada: de toda la empresa, del Hotel y de Informática."""
    admin = ramas["admin"]
    creados: dict[str, Any] = {}
    for clave, departamento in (
        ("empresa", None),
        ("hotel", ramas["hotel"]["id"]),
        ("informatica", ramas["informatica"]["id"]),
    ):
        etiqueta = await admin.post(
            "/api/labels", json={"name": f"etiqueta-{clave}", "department_id": departamento}
        )
        respuesta = await admin.post(
            "/api/canned-responses",
            json={
                "shortcode": f"r-{clave}",
                "title": clave,
                "body": f"texto {clave}",
                "department_id": departamento,
            },
        )
        assert etiqueta.status_code == respuesta.status_code == 201, etiqueta.text
        macro = await admin.post(
            "/api/macros",
            json={
                "name": f"macro-{clave}",
                "department_id": departamento,
                "steps": [{"action": "label", "label_id": etiqueta.json()["id"]}],
            },
        )
        assert macro.status_code == 201, macro.text
        creados[clave] = {
            "label": etiqueta.json()["id"],
            "canned": respuesta.json()["id"],
            "macro": macro.json()["id"],
        }
    return creados


async def test_each_agent_only_lists_what_belongs_to_her_departments(ramas, as_agent, team):
    recursos = await _recursos(ramas)
    ana = await as_agent(team["ana"]["email"])

    for ruta, clave in (("/api/labels", "label"), ("/api/canned-responses", "canned"),
                        ("/api/macros", "macro")):
        suyos = {fila["id"] for fila in (await ana.get(ruta)).json()}
        todos = {fila["id"] for fila in (await ramas["admin"].get(ruta)).json()}
        assert recursos["empresa"][clave] in suyos, ruta
        assert recursos["hotel"][clave] in suyos, ruta
        assert recursos["informatica"][clave] not in suyos, ruta
        assert recursos["informatica"][clave] in todos, ruta


async def test_a_label_of_another_department_cannot_be_applied(ramas, as_agent, team):
    recursos = await _recursos(ramas)
    admin = ramas["admin"]
    propia = ramas["propia"]  # conversación del Hotel

    ajena = await admin.put(
        f"/api/conversations/{propia}/labels",
        json={"label_ids": [recursos["informatica"]["label"]]},
    )
    assert ajena.status_code == 422

    valida = await admin.put(
        f"/api/conversations/{propia}/labels",
        json={"label_ids": [recursos["empresa"]["label"], recursos["hotel"]["label"]]},
    )
    assert valida.status_code == 200, valida.text


async def test_labels_already_applied_survive_a_transfer(ramas, as_agent, team):
    """Derivar no obliga a quitar lo que se puso en el departamento anterior."""
    recursos = await _recursos(ramas)
    admin = ramas["admin"]
    propia = ramas["propia"]
    await admin.put(
        f"/api/conversations/{propia}/labels", json={"label_ids": [recursos["hotel"]["label"]]}
    )
    await admin.post(
        f"/api/conversations/{propia}/transfer",
        json={"to_department_id": ramas["informatica"]["id"]},
    )

    otra_mas = await admin.put(
        f"/api/conversations/{propia}/labels",
        json={"label_ids": [recursos["hotel"]["label"], recursos["informatica"]["label"]]},
    )
    assert otra_mas.status_code == 200, otra_mas.text


async def test_a_macro_only_runs_in_its_department(ramas, as_agent, team):
    recursos = await _recursos(ramas)
    ana = await as_agent(team["ana"]["email"])
    await ana.post(f"/api/conversations/{ramas['propia']}/claim")

    ajena = await ramas["admin"].post(
        f"/api/conversations/{ramas['propia']}/macros/{recursos['informatica']['macro']}"
    )
    assert ajena.status_code == 422

    # Ana ni siquiera la ve: para ella no existe.
    oculta = await ana.post(
        f"/api/conversations/{ramas['propia']}/macros/{recursos['informatica']['macro']}"
    )
    assert oculta.status_code == 404

    propia = await ana.post(
        f"/api/conversations/{ramas['propia']}/macros/{recursos['hotel']['macro']}"
    )
    assert propia.status_code == 200, propia.text


async def test_a_company_macro_cannot_use_a_department_label(ramas):
    recursos = await _recursos(ramas)

    respuesta = await ramas["admin"].post(
        "/api/macros",
        json={
            "name": "mezcla",
            "steps": [{"action": "label", "label_id": recursos["hotel"]["label"]}],
        },
    )
    assert respuesta.status_code == 422


async def test_a_canned_response_can_move_to_another_department(ramas):
    recursos = await _recursos(ramas)
    admin = ramas["admin"]

    movida = await admin.patch(
        f"/api/canned-responses/{recursos['empresa']['canned']}",
        json={"department_id": ramas["informatica"]["id"]},
    )
    assert movida.json()["department_id"] == ramas["informatica"]["id"]

    sin_tocar = await admin.patch(
        f"/api/canned-responses/{recursos['empresa']['canned']}", json={"title": "otro"}
    )
    assert sin_tocar.json()["department_id"] == ramas["informatica"]["id"]

    a_toda = await admin.patch(
        f"/api/canned-responses/{recursos['empresa']['canned']}", json={"department_id": None}
    )
    assert a_toda.json()["department_id"] is None
