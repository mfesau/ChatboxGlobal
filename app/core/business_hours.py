"""Horario de atención de un departamento.

El horario se guarda en el propio departamento como un objeto JSON, y no en una
tabla aparte: nunca hace falta consultarlo desde SQL —siempre se evalúa "¿está
abierto ahora?" sobre una fila ya cargada— y así no se paga un join por turno.

Formato de ``Department.business_hours``::

    {"1": [["09:00", "18:00"]], "6": [["09:00", "13:00"]]}

Las claves son el día de la semana ISO (1 = lunes … 7 = domingo) y cada día
lleva una lista de tramos, de modo que un corte al mediodía se expresa con dos.
Un día ausente, o con la lista vacía, está cerrado. El objeto vacío significa
"sin horario configurado", que se trata como atención permanente: así, hasta
que alguien lo configure, nada cambia respecto de cómo venía funcionando.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:  # pragma: no cover - solo para anotaciones
    from app.db.models import Department

from app.core.envelope import utcnow
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Zona con la que se interpreta un horario sin zona propia.
DEFAULT_TIMEZONE = "UTC"

#: Clave de ``Tenant.settings`` donde vive lo que rige por omisión, es decir
#: lo que se aplica a la cola común y a los departamentos que no fijan lo suyo.
SERVICE_SETTINGS_KEY = "service"

#: Hasta cuántos días se avanza buscando minutos hábiles antes de rendirse.
#: Cubre de sobra un cierre por vacaciones; sin este tope, un horario que en
#: la práctica no abre nunca dejaría el cálculo dando vueltas.
MAX_LOOKAHEAD_DAYS = 60


def parse_clock(value: str) -> time | None:
    """``"09:30"`` → ``time(9, 30)``. Devuelve ``None`` si no es una hora."""
    try:
        hours, _, minutes = value.partition(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return None


def resolve_timezone(name: str | None) -> ZoneInfo:
    """Zona horaria por nombre, con UTC como red de seguridad.

    Un nombre inválido —o una instalación sin la base de zonas— no puede
    tumbar el turno de un cliente: se registra y se sigue en UTC.
    """
    if not name:
        return ZoneInfo(DEFAULT_TIMEZONE)
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("timezone_desconocida", timezone=name)
        return ZoneInfo(DEFAULT_TIMEZONE)


def is_within_business_hours(
    schedule: dict[str, Any] | None,
    timezone: str | None = None,
    moment: datetime | None = None,
) -> bool:
    """¿Cae ``moment`` dentro del horario de atención?

    Sin horario configurado se atiende siempre, que es como venía
    comportándose el servicio antes de que este campo existiera.
    """
    if not schedule:
        return True

    zone = resolve_timezone(timezone)
    local = (moment or utcnow()).astimezone(zone)
    spans = schedule.get(str(local.isoweekday())) or []
    now = local.time()

    for span in spans:
        # Un tramo mal escrito se ignora en lugar de cerrar el día entero:
        # es preferible atender de más que dejar a un cliente sin respuesta.
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            continue
        opens, closes = parse_clock(str(span[0])), parse_clock(str(span[1]))
        if opens is None or closes is None:
            continue
        if opens <= closes:
            if opens <= now < closes:
                return True
        # Tramo que cruza la medianoche (22:00–02:00): vale desde la apertura
        # hasta el final del día, y desde el comienzo del día hasta el cierre.
        elif now >= opens or now < closes:
            return True

    return False


def _spans_of_day(schedule: dict[str, Any], weekday: int) -> list[tuple[time, time]]:
    """Tramos legibles de un día, ordenados y sin los que cruzan medianoche.

    Un tramo nocturno se recorta al final del día: para contar minutos hábiles
    alcanza con la parte que cae dentro de la jornada, y así el recorrido
    avanza siempre hacia adelante sin casos especiales.
    """
    spans: list[tuple[time, time]] = []
    for span in schedule.get(str(weekday)) or []:
        if not isinstance(span, (list, tuple)) or len(span) != 2:
            continue
        opens, closes = parse_clock(str(span[0])), parse_clock(str(span[1]))
        if opens is None or closes is None:
            continue
        spans.append((opens, closes if closes > opens else time(23, 59)))
    return sorted(spans)


def add_business_minutes(
    schedule: dict[str, Any] | None,
    timezone: str | None,
    start: datetime,
    minutes: int,
) -> datetime | None:
    """Instante que resulta de sumar ``minutes`` **hábiles** a ``start``.

    Es lo que hace falta para un objetivo de respuesta con el reloj detenido
    fuera de hora: un mensaje que entra a las 23:00 con una hora de objetivo
    no vence a medianoche, sino a las 10:00 del día siguiente. Así el número
    mide al equipo y no al reloj.

    Sin horario configurado la cuenta es la del reloj corriente. Devuelve
    ``None`` si el horario no da esos minutos dentro de los próximos
    ``MAX_LOOKAHEAD_DAYS`` días, y entonces no hay vencimiento que fijar.
    """
    if not schedule:
        return start + timedelta(minutes=minutes)
    if minutes <= 0:
        return start

    zone = resolve_timezone(timezone)
    cursor = start.astimezone(zone)
    remaining = timedelta(minutes=minutes)

    for day_offset in range(MAX_LOOKAHEAD_DAYS + 1):
        day = (cursor if day_offset == 0 else cursor.replace(hour=0, minute=0, second=0)).date()
        for opens, closes in _spans_of_day(schedule, day.isoweekday()):
            span_opens = datetime.combine(day, opens, tzinfo=zone)
            span_closes = datetime.combine(day, closes, tzinfo=zone)
            # El tramo ya pasó, o empezó antes de este instante: se cuenta
            # solo desde donde está el cursor.
            begins = max(span_opens, cursor)
            if begins >= span_closes:
                continue
            available = span_closes - begins
            if available >= remaining:
                return (begins + remaining).astimezone(start.tzinfo)
            remaining -= available

        # Día agotado: se sigue por el arranque del siguiente.
        cursor = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=zone)

    log.warning("sla_sin_horario_suficiente", minutos=minutes, dias=MAX_LOOKAHEAD_DAYS)
    return None


@dataclass(frozen=True, slots=True)
class ServicePolicy:
    """Horario y objetivo que rigen para una conversación concreta.

    Se resuelve campo por campo y no todo o nada: un departamento puede fijar
    su horario y heredar el objetivo del inquilino, o al revés, sin tener que
    repetir lo que ya está escrito una vez.
    """

    business_hours: dict[str, Any]
    timezone: str | None
    out_of_hours_message: str | None
    first_response_target_minutes: int | None

    @property
    def has_schedule(self) -> bool:
        return bool(self.business_hours)


def resolve_service_policy(
    department: Department | None, tenant_settings: dict[str, Any] | None
) -> ServicePolicy:
    """Combina lo del departamento con lo que el inquilino fija por omisión.

    Sin departamento —la cola común— rige todo lo del inquilino, que es lo que
    permite medir también lo que todavía no se derivó a nadie.
    """
    defaults = (tenant_settings or {}).get(SERVICE_SETTINGS_KEY) or {}

    def inherited(field: str, own: Any = None) -> Any:
        return own if own else defaults.get(field)

    return ServicePolicy(
        business_hours=inherited(
            "business_hours", getattr(department, "business_hours", None)
        )
        or {},
        timezone=inherited("timezone", getattr(department, "timezone", None)),
        out_of_hours_message=inherited(
            "out_of_hours_message", getattr(department, "out_of_hours_message", None)
        ),
        first_response_target_minutes=inherited(
            "first_response_target_minutes",
            getattr(department, "first_response_target_minutes", None),
        ),
    )
