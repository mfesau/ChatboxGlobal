"""Pruebas del formato canónico y de la cadena de handlers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.core.branding import (
    FILL_RATIO,
    SURFACE_DARK,
    SURFACE_LIGHT,
    TEXT_RATIO,
    UI_RATIO,
    InvalidColor,
    _to_rgb,
    brand_css,
    contrast,
    derive_palette,
    normalize_hex,
)
from app.core.business_hours import (
    add_business_minutes,
    is_within_business_hours,
    parse_clock,
    resolve_service_policy,
)
from app.core.envelope import (
    Attachment,
    ChannelKind,
    ContentType,
    ConversationRef,
    DeliveryReceipt,
    DeliveryStatus,
    InboundMessage,
    OutboundMessage,
    Party,
)
from app.core.hub import Hub
from app.core.localized import normalize_locale
from app.core.localized import pick as pick_localized
from app.core.pipeline import Handler, NextFn, Pipeline
from app.core.storage import save_incoming_media


# --------------------------------------------------------------------------- #
# Formato canónico
# --------------------------------------------------------------------------- #
def test_dedupe_key_defaults_to_channel_and_provider_id():
    message = InboundMessage(
        channel=ChannelKind.WHATSAPP,
        conversation=ConversationRef(
            channel=ChannelKind.WHATSAPP, channel_conversation_id="595981000111"
        ),
        sender=Party(channel_user_id="595981000111"),
        provider_message_id="wamid.XYZ",
    )
    assert message.dedupe_key == "whatsapp:wamid.XYZ"


def test_explicit_dedupe_key_is_respected():
    message = InboundMessage(
        channel=ChannelKind.WEB,
        conversation=ConversationRef(channel=ChannelKind.WEB, channel_conversation_id="s"),
        sender=Party(channel_user_id="s"),
        provider_message_id="m-1",
        dedupe_key="clave-propia",
    )
    assert message.dedupe_key == "clave-propia"


def test_outbound_message_survives_a_serialisation_round_trip():
    """La cola de salida persiste el mensaje como JSON; nada debe perderse."""
    original = OutboundMessage(
        text="Aquí tiene la factura.",
        quick_replies=[{"id": "ok", "title": "Recibido"}],
        attachments=[
            Attachment(
                content_type=ContentType.DOCUMENT,
                url="https://ejemplo.test/f.pdf",
                mime_type="application/pdf",
                filename="f.pdf",
                size_bytes=1024,
            )
        ],
        channel_data={"template": {"name": "aviso"}},
    )

    restored = OutboundMessage.from_dict(original.to_dict())

    assert restored.text == original.text
    assert restored.quick_replies == original.quick_replies
    assert restored.channel_data == original.channel_data
    assert restored.client_message_id == original.client_message_id
    assert restored.attachments[0].filename == "f.pdf"
    assert restored.attachments[0].content_type is ContentType.DOCUMENT
    assert restored.attachments[0].size_bytes == 1024


def test_conversation_ref_round_trip_preserves_routing_data():
    original = ConversationRef(
        channel=ChannelKind.MSBOT,
        channel_conversation_id="19:hilo",
        channel_account_id="28:bot",
        service_url="https://smba.trafficmanager.net/emea/",
        reply_to_message_id="act-1",
        extra={"channel_id": "msteams"},
    )

    restored = ConversationRef.from_dict(original.to_dict())

    assert restored == original


def test_system_messages_are_not_actionable():
    message = InboundMessage(
        channel=ChannelKind.WHATSAPP,
        conversation=ConversationRef(
            channel=ChannelKind.WHATSAPP, channel_conversation_id="1"
        ),
        sender=Party(channel_user_id="1"),
        provider_message_id="s-1",
        content_type=ContentType.SYSTEM,
    )
    assert message.is_actionable is False


def test_receipt_helpers_set_the_expected_state():
    sent = DeliveryReceipt.sent("prov-1")
    assert sent.ok is True
    assert sent.status is DeliveryStatus.SENT

    failed = DeliveryReceipt.failed("http_429", "cuota agotada", retryable=True)
    assert failed.ok is False
    assert failed.retryable is True


def test_outbound_messages_get_distinct_identifiers():
    first = OutboundMessage(text="a")
    second = OutboundMessage(text="b")
    assert first.client_message_id != second.client_message_id
    assert isinstance(first.client_message_id, uuid.UUID)


# --------------------------------------------------------------------------- #
# Cadena de handlers
# --------------------------------------------------------------------------- #
def stub_context() -> SimpleNamespace:
    """Contexto mínimo: la cadena solo necesita identificar la conversación."""
    return SimpleNamespace(conversation=SimpleNamespace(id="conversacion-de-prueba"))


class Recorder(Handler):
    """Handler que registra el orden de entrada y de salida."""

    def __init__(self, label: str, trace: list[str], *, delegate: bool = True) -> None:
        self.name = label
        self._trace = trace
        self._delegate = delegate

    async def handle(self, ctx, next_: NextFn) -> None:
        self._trace.append(f"entra:{self.name}")
        if self._delegate:
            await next_()
        self._trace.append(f"sale:{self.name}")


async def test_pipeline_runs_handlers_as_nested_middlewares():
    trace: list[str] = []
    pipeline = Pipeline([Recorder("a", trace), Recorder("b", trace)])

    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert trace == ["entra:a", "entra:b", "sale:b", "sale:a"]


async def test_pipeline_short_circuits_when_a_handler_does_not_delegate():
    trace: list[str] = []
    pipeline = Pipeline(
        [Recorder("a", trace), Recorder("b", trace, delegate=False), Recorder("c", trace)]
    )

    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert "entra:c" not in trace


async def test_pipeline_continues_after_a_handler_raises():
    trace: list[str] = []

    class Broken(Handler):
        name: ClassVar[str] = "roto"

        async def handle(self, ctx, next_: NextFn) -> None:
            raise RuntimeError("fallo")

    pipeline = Pipeline([Broken(), Recorder("siguiente", trace)])
    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert trace == ["entra:siguiente", "sale:siguiente"]


async def test_pipeline_does_not_replay_the_tail_when_a_handler_fails_after_delegating():
    """Si el handler delega y luego falla, la cola no debe ejecutarse dos veces."""
    trace: list[str] = []

    class DelegatesThenFails(Handler):
        name: ClassVar[str] = "delega_y_falla"

        async def handle(self, ctx, next_: NextFn) -> None:
            await next_()
            raise RuntimeError("fallo posterior")

    pipeline = Pipeline([DelegatesThenFails(), Recorder("cola", trace)])
    await pipeline.run(stub_context())  # type: ignore[arg-type]

    assert trace.count("entra:cola") == 1


def test_appending_a_handler_extends_the_pipeline():
    trace: list[str] = []
    pipeline = Pipeline([Recorder("a", trace)])
    pipeline.append(Recorder("b", trace))
    assert [handler.name for handler in pipeline.handlers] == ["a", "b"]


# --------------------------------------------------------------------------- #
# Bus de difusión
# --------------------------------------------------------------------------- #
async def test_hub_delivers_to_every_subscriber_of_a_topic():
    hub = Hub()
    received: list[dict] = []

    async def first(event):
        received.append(event)

    async def second(event):
        received.append(event)

    await hub.subscribe("tema", first)
    await hub.subscribe("tema", second)

    delivered = await hub.publish("tema", {"n": 1})

    assert delivered == 2
    assert len(received) == 2


async def test_hub_drops_a_subscriber_that_fails():
    hub = Hub()

    async def broken(event):
        raise ConnectionError("socket cerrado")

    await hub.subscribe("tema", broken)
    assert await hub.publish("tema", {}) == 0
    assert hub.subscriber_count("tema") == 0


async def test_hub_ignores_topics_without_subscribers():
    hub = Hub()
    assert await hub.publish("nadie-escucha", {}) == 0


@pytest.mark.parametrize("kind", list(ChannelKind))
def test_every_channel_kind_serialises_as_its_value(kind: ChannelKind):
    assert str(kind) == kind.value


# --------------------------------------------------------------------------- #
# Horario de atención
# --------------------------------------------------------------------------- #
LUNES_A_VIERNES = {str(day): [["09:00", "18:00"]] for day in range(1, 6)}


def _momento(año, mes, dia, hora, minuto=0):
    """Instante en UTC; los casos declaran la zona aparte."""
    return datetime(año, mes, dia, hora, minuto, tzinfo=UTC)


def test_without_a_schedule_the_service_is_always_open():
    # Sin configurar, nada cambia respecto de como venía funcionando.
    assert is_within_business_hours({}) is True
    assert is_within_business_hours(None) is True


def test_a_weekday_inside_the_span_is_open():
    # Miércoles 12:00 UTC.
    assert is_within_business_hours(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 2, 12)) is True


def test_before_opening_and_after_closing_are_closed():
    assert is_within_business_hours(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 2, 8, 59)) is False
    assert is_within_business_hours(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 2, 18)) is False


def test_a_day_missing_from_the_schedule_is_closed():
    # Domingo: no figura en el horario.
    assert is_within_business_hours(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 6, 12)) is False


def test_the_timezone_decides_whether_it_is_open():
    """Las 12:00 UTC son las 9:00 en Buenos Aires: recién abre."""
    momento = _momento(2026, 9, 2, 12)
    assert is_within_business_hours(LUNES_A_VIERNES, "America/Argentina/Buenos_Aires", momento)
    # Una hora antes, allí son las 8:00 y todavía está cerrado.
    assert not is_within_business_hours(
        LUNES_A_VIERNES, "America/Argentina/Buenos_Aires", _momento(2026, 9, 2, 11)
    )


def test_a_split_shift_leaves_the_lunch_break_closed():
    partido = {"3": [["09:00", "13:00"], ["15:00", "19:00"]]}
    assert is_within_business_hours(partido, "UTC", _momento(2026, 9, 2, 10)) is True
    assert is_within_business_hours(partido, "UTC", _momento(2026, 9, 2, 14)) is False
    assert is_within_business_hours(partido, "UTC", _momento(2026, 9, 2, 16)) is True


def test_a_span_across_midnight_covers_both_sides():
    nocturno = {"3": [["22:00", "02:00"]]}
    assert is_within_business_hours(nocturno, "UTC", _momento(2026, 9, 2, 23)) is True
    assert is_within_business_hours(nocturno, "UTC", _momento(2026, 9, 2, 1)) is True
    assert is_within_business_hours(nocturno, "UTC", _momento(2026, 9, 2, 12)) is False


def test_an_unreadable_span_is_ignored_instead_of_closing_the_day():
    """Atender de más es preferible a dejar sin respuesta por un dato malo."""
    roto = {"3": [["nueve", "18:00"], ["09:00", "18:00"]]}
    assert is_within_business_hours(roto, "UTC", _momento(2026, 9, 2, 12)) is True
    ilegible = {"3": [["nueve", "seis"]]}
    assert is_within_business_hours(ilegible, "UTC", _momento(2026, 9, 2, 12)) is False


def test_an_unknown_timezone_falls_back_to_utc_without_failing():
    assert is_within_business_hours(LUNES_A_VIERNES, "Marte/Olympus", _momento(2026, 9, 2, 12))


def test_parse_clock_reads_valid_times_and_rejects_the_rest():
    assert parse_clock("09:30") == time(9, 30)
    assert parse_clock("00:00") == time(0, 0)
    assert parse_clock("nueve") is None
    assert parse_clock("") is None


# --------------------------------------------------------------------------- #
# Minutos hábiles (objetivo de respuesta con el reloj detenido fuera de hora)
# --------------------------------------------------------------------------- #
def test_without_a_schedule_the_clock_simply_runs():
    inicio = _momento(2026, 9, 2, 23)
    assert add_business_minutes({}, "UTC", inicio, 60) == inicio + timedelta(minutes=60)


def test_inside_the_span_the_deadline_is_the_plain_sum():
    # Miércoles 10:00, una hora de objetivo: vence a las 11:00 del mismo día.
    vence = add_business_minutes(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 2, 10), 60)
    assert vence == _momento(2026, 9, 2, 11)


def test_the_clock_stops_at_closing_and_resumes_next_morning():
    """Lo que motivó todo: de noche el reloj no corre."""
    # Miércoles 17:30 con 60 minutos: quedan 30 hasta las 18:00 y los otros
    # 30 se cuentan desde las 09:00 del jueves.
    vence = add_business_minutes(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 2, 17, 30), 60)
    assert vence == _momento(2026, 9, 3, 9, 30)


def test_a_message_arriving_at_night_starts_counting_at_opening():
    # Miércoles 23:00: el reloj arranca el jueves a las 09:00.
    vence = add_business_minutes(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 2, 23), 60)
    assert vence == _momento(2026, 9, 3, 10)


def test_the_weekend_is_skipped_entirely():
    # Viernes 17:30 con 60 minutos: sábado y domingo no cuentan, así que
    # el resto se cuenta el lunes por la mañana.
    vence = add_business_minutes(LUNES_A_VIERNES, "UTC", _momento(2026, 9, 4, 17, 30), 60)
    assert vence == _momento(2026, 9, 7, 9, 30)


def test_a_split_shift_does_not_count_the_lunch_break():
    partido = {"3": [["09:00", "13:00"], ["15:00", "19:00"]]}
    # Miércoles 12:30 con 60 minutos: 30 antes del corte y 30 después.
    vence = add_business_minutes(partido, "UTC", _momento(2026, 9, 2, 12, 30), 60)
    assert vence == _momento(2026, 9, 2, 15, 30)


def test_the_deadline_respects_the_department_timezone():
    """09:00 en Buenos Aires son las 12:00 UTC."""
    vence = add_business_minutes(
        LUNES_A_VIERNES, "America/Argentina/Buenos_Aires", _momento(2026, 9, 2, 3), 30
    )
    assert vence == _momento(2026, 9, 2, 12, 30)


def test_a_schedule_that_never_opens_yields_no_deadline():
    # Sin ningún día abierto no hay minutos hábiles que contar.
    assert add_business_minutes({"1": []}, "UTC", _momento(2026, 9, 2, 10), 60) is None


def test_a_target_of_zero_minutes_expires_immediately():
    inicio = _momento(2026, 9, 2, 10)
    assert add_business_minutes(LUNES_A_VIERNES, "UTC", inicio, 0) == inicio


# --------------------------------------------------------------------------- #
# Qué horario y objetivo rigen cada conversación
# --------------------------------------------------------------------------- #
AJUSTES_INQUILINO = {
    "service": {
        "business_hours": {"1": [["08:00", "16:00"]]},
        "timezone": "UTC",
        "out_of_hours_message": "Aviso del inquilino",
        "first_response_target_minutes": 600,
    }
}


def _departamento(**campos):
    """Departamento mínimo: la resolución solo mira estos cuatro campos."""
    return SimpleNamespace(
        business_hours=campos.get("business_hours"),
        timezone=campos.get("timezone"),
        out_of_hours_message=campos.get("out_of_hours_message"),
        first_response_target_minutes=campos.get("first_response_target_minutes"),
    )


def test_the_common_queue_takes_everything_from_the_tenant():
    """Sin departamento —lo que aún no se derivó— rige lo del inquilino."""
    policy = resolve_service_policy(None, AJUSTES_INQUILINO)
    assert policy.first_response_target_minutes == 600
    assert policy.business_hours == {"1": [["08:00", "16:00"]]}
    assert policy.out_of_hours_message == "Aviso del inquilino"


def test_what_the_department_defines_wins():
    policy = resolve_service_policy(
        _departamento(first_response_target_minutes=5, timezone="America/Asuncion"),
        AJUSTES_INQUILINO,
    )
    assert policy.first_response_target_minutes == 5
    assert policy.timezone == "America/Asuncion"


def test_the_department_inherits_field_by_field():
    """Fijar el horario propio no obliga a repetir también el objetivo."""
    policy = resolve_service_policy(
        _departamento(business_hours={"6": [["10:00", "14:00"]]}), AJUSTES_INQUILINO
    )
    assert policy.business_hours == {"6": [["10:00", "14:00"]]}
    assert policy.first_response_target_minutes == 600


def test_with_nothing_configured_there_is_no_schedule_and_no_target():
    policy = resolve_service_policy(_departamento(), {})
    assert policy.has_schedule is False
    assert policy.first_response_target_minutes is None


# --------------------------------------------------------------------------- #
# Textos hacia el cliente, en su idioma
# --------------------------------------------------------------------------- #
POR_IDIOMA = {"es": "Estamos cerrados.", "en": "We are closed.", "de": "Wir haben geschlossen."}


def test_a_plain_string_reaches_everyone_alike():
    """Lo configurado antes de que existieran los idiomas sigue funcionando."""
    assert pick_localized("Un solo texto", "de") == "Un solo texto"
    assert pick_localized("Un solo texto", None) == "Un solo texto"


def test_each_language_gets_its_own_text():
    assert pick_localized(POR_IDIOMA, "de") == "Wir haben geschlossen."
    assert pick_localized(POR_IDIOMA, "en") == "We are closed."


def test_the_region_is_ignored_when_choosing():
    """El navegador manda «de-AT»; no hay una versión para Austria."""
    assert pick_localized(POR_IDIOMA, "de-AT") == "Wir haben geschlossen."
    assert pick_localized(POR_IDIOMA, "es_AR") == "Estamos cerrados."


def test_an_unwritten_language_falls_back_to_spanish():
    assert pick_localized({"es": "Solo español"}, "de") == "Solo español"
    assert pick_localized(POR_IDIOMA, "fr") == "Estamos cerrados."
    assert pick_localized(POR_IDIOMA, None) == "Estamos cerrados."


def test_with_spanish_missing_any_written_language_is_better_than_silence():
    assert pick_localized({"en": "Only English"}, "de") == "Only English"


def test_nothing_written_is_nothing_sent():
    assert pick_localized(None, "es") is None
    assert pick_localized({}, "es") is None
    assert pick_localized({"es": "   "}, "es") is None


def test_normalize_locale_only_admits_what_we_speak():
    assert normalize_locale("de-DE") == "de"
    assert normalize_locale("EN") == "en"
    assert normalize_locale("fr-FR") is None
    assert normalize_locale(None) is None


# --------------------------------------------------------------------------- #
# Color de marca
# --------------------------------------------------------------------------- #
#: Colores extremos a propósito: el segundo y el tercero son los que rompen una
#: implementación ingenua que se limite a copiar el color en todas partes.
COLORES = ["#2f5bd7", "#ffe600", "#000000", "#ffffff", "#e8f0ff", "#808080", "#c0392b"]


@pytest.mark.parametrize("color", COLORES)
def test_the_brand_colour_always_stays_readable_as_text(color):
    """Sea cual sea el color elegido, la tinta se lee sobre el fondo.

    Es la garantía que justifica dejar elegir el color: un amarillo de marca
    sirve de relleno y desaparece como texto sobre blanco, así que el color de
    la tinta se calcula aparte y no puede quedar por debajo del umbral.
    """
    paleta = derive_palette(color)
    assert contrast(_to_rgb(paleta["light"]["accent_ink"]), SURFACE_LIGHT) >= TEXT_RATIO
    assert contrast(_to_rgb(paleta["dark"]["accent_ink"]), SURFACE_DARK) >= TEXT_RATIO


@pytest.mark.parametrize("color", COLORES)
def test_what_is_written_on_the_fill_is_readable_too(color):
    paleta = derive_palette(color)
    for tema in ("light", "dark"):
        relleno = _to_rgb(paleta[tema]["accent"])
        assert contrast(_to_rgb(paleta[tema]["accent_contrast"]), relleno) >= TEXT_RATIO
        # La burbuja saliente es la misma superficie con texto encima.
        burbuja = _to_rgb(paleta[tema]["outbound"])
        assert contrast(_to_rgb(paleta[tema]["outbound_text"]), burbuja) >= TEXT_RATIO


@pytest.mark.parametrize("color", COLORES)
def test_a_filled_button_never_blends_into_the_panel_behind_it(color):
    paleta = derive_palette(color)
    assert contrast(_to_rgb(paleta["light"]["accent"]), SURFACE_LIGHT) >= FILL_RATIO
    assert contrast(_to_rgb(paleta["dark"]["accent"]), SURFACE_DARK) >= UI_RATIO


def test_a_colour_that_already_reads_well_is_left_untouched():
    """No se retoca lo que no hace falta: es el color de la marca."""
    paleta = derive_palette("#2f5bd7")
    assert paleta["light"]["accent"] == "#2f5bd7"
    assert paleta["light"]["accent_ink"] == "#2f5bd7"


def test_lightening_keeps_the_hue_so_the_brand_survives():
    """Un rojo aclarado sigue siendo rojo, no rosa anaranjado."""
    import colorsys

    original = _to_rgb("#c0392b")
    aclarado = _to_rgb(derive_palette("#c0392b")["dark"]["accent_ink"])
    tono = lambda rgb: colorsys.rgb_to_hls(*(v / 255 for v in rgb))[0]  # noqa: E731
    assert abs(tono(original) - tono(aclarado)) < 0.01


def test_normalize_hex_accepts_the_short_form_and_rejects_the_rest():
    assert normalize_hex("#ABC") == "#aabbcc"
    assert normalize_hex("  #2F5BD7  ") == "#2f5bd7"
    for malo in ["azul", "#12345", "", "#2f5bd7;}", "rgb(0,0,0)"]:
        with pytest.raises(InvalidColor):
            normalize_hex(malo)


def test_without_a_chosen_colour_not_a_single_rule_is_emitted():
    """La hoja de estilos queda intacta para quien no eligió nada."""
    assert brand_css(None) == ""
    assert brand_css("") == ""


def test_the_generated_css_covers_the_three_theme_selectors():
    """Sin los tres, quien tenga el tema oscuro seguiría viendo el azul."""
    css = brand_css("#c0392b")
    assert css.startswith(":root{")
    assert "@media (prefers-color-scheme:dark)" in css
    assert ':root[data-theme="dark"]' in css
    assert css.count("--accent-ink:") == 3


# --------------------------------------------------------------------------- #
# Adjuntos entrantes
# --------------------------------------------------------------------------- #
def test_incoming_media_accepts_what_a_customer_can_actually_send(tmp_path):
    """Más amplio que lo que se admite al subir: nadie elige lo que le mandan."""
    settings = SimpleNamespace(uploads_dir=str(tmp_path), inbound_media_max_bytes=1_000_000)
    for mime, esperado in [
        ("video/mp4", ContentType.VIDEO),
        ("audio/ogg; codecs=opus", ContentType.AUDIO),
        ("image/jpeg", ContentType.IMAGE),
        ("application/pdf", ContentType.DOCUMENT),
    ]:
        guardado = save_incoming_media(
            b"datos", mime_type=mime, namespace="inquilino", settings=settings
        )
        assert guardado is not None, mime
        assert guardado.content_type is esperado
        assert guardado.url.startswith("/uploads/inquilino/")


def test_what_cannot_be_stored_is_dropped_instead_of_raising(tmp_path):
    """Un adjunto imposible no puede tumbar el webhook: Meta lo reintentaría."""
    settings = SimpleNamespace(uploads_dir=str(tmp_path), inbound_media_max_bytes=10)
    assert save_incoming_media(b"x", mime_type="application/x-msdownload",
                               namespace="t", settings=settings) is None
    assert save_incoming_media(b"", mime_type="video/mp4",
                               namespace="t", settings=settings) is None
    assert save_incoming_media(b"demasiado-grande", mime_type="video/mp4",
                               namespace="t", settings=settings) is None
    assert save_incoming_media(b"x", mime_type=None,
                               namespace="t", settings=settings) is None
