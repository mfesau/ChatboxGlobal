"""Contrato de los adaptadores de canal y su registro.

Para incorporar un canal nuevo basta con crear una subclase de
:class:`ChannelAdapter`, decorarla con :func:`register_channel` e importar el
módulo en ``app/channels/__init__.py``. El orquestador no cambia.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Mapping, Sequence
from typing import Any, ClassVar

from app.core.envelope import (
    ChannelKind,
    ConversationRef,
    DeliveryReceipt,
    InboundMessage,
    OutboundMessage,
)


class ChannelError(Exception):
    """Error atribuible al canal o al proveedor."""

    def __init__(self, message: str, *, code: str = "channel_error", retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class SignatureError(ChannelError):
    """La firma o el token de la petición entrante no es válido."""

    def __init__(self, message: str = "Firma no válida"):
        super().__init__(message, code="invalid_signature", retryable=False)


class ChannelAdapter(abc.ABC):
    """Traductor bidireccional entre un proveedor y el formato canónico."""

    #: Identificador del canal; debe coincidir con la clave del registro.
    kind: ClassVar[ChannelKind]
    #: Indica si el adaptador puede enviar mensajes salientes.
    supports_outbound: ClassVar[bool] = True

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    # ------------------------------------------------------------- entrada
    async def verify_request(
        self,
        *,
        headers: Mapping[str, str],
        body: bytes,
        credentials: Sequence[Mapping[str, str]] = (),
    ) -> None:
        """Valida la autenticidad de la petición entrante.

        Debe lanzar :class:`SignatureError` cuando la validación falle. La
        implementación por omisión no verifica nada, de modo que los canales
        internos (por ejemplo el chatbox web, protegido por sesión) no precisan
        sobrescribirla.

        ``credentials`` trae las credenciales propias de las cuentas dadas de
        alta desde la consola. Llegan hasta aquí porque la firma se comprueba
        **antes** de leer el cuerpo, así que en ese momento todavía no se sabe
        a qué cuenta pertenece el mensaje: se acepta el que valide con
        cualquiera de las configuradas.
        """
        return

    @abc.abstractmethod
    async def parse(
        self, *, payload: dict[str, Any], headers: Mapping[str, str]
    ) -> list[InboundMessage]:
        """Convierte la carga del proveedor en cero o más mensajes canónicos.

        Un único webhook puede contener varios mensajes y también acuses de
        recibo; estos últimos se devuelven con ``content_type`` ``SYSTEM``.
        """

    # ------------------------------------------------------------- salida
    @abc.abstractmethod
    async def send(
        self, *, ref: ConversationRef, message: OutboundMessage
    ) -> DeliveryReceipt:
        """Entrega un mensaje saliente por el canal."""

    async def set_typing(self, *, ref: ConversationRef) -> None:
        """Señala «escribiendo…» cuando el canal lo admite."""
        return

    async def mark_read(self, *, ref: ConversationRef, provider_message_id: str) -> None:
        """Marca como leído el mensaje entrante cuando el canal lo admite."""
        return

    async def aclose(self) -> None:
        """Libera recursos, por ejemplo clientes HTTP persistentes."""
        return


# --------------------------------------------------------------------------- #
# Registro
# --------------------------------------------------------------------------- #
_REGISTRY: dict[ChannelKind, type[ChannelAdapter]] = {}


def register_channel(
    kind: ChannelKind,
) -> Callable[[type[ChannelAdapter]], type[ChannelAdapter]]:
    """Decorador que inscribe una clase de adaptador en el registro."""

    def decorator(cls: type[ChannelAdapter]) -> type[ChannelAdapter]:
        if kind in _REGISTRY:
            raise RuntimeError(f"El canal '{kind}' ya está registrado")
        cls.kind = kind
        _REGISTRY[kind] = cls
        return cls

    return decorator


class ChannelRegistry:
    """Contenedor de instancias de adaptador, una por canal."""

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._instances: dict[ChannelKind, ChannelAdapter] = {}

    def get(self, kind: ChannelKind | str) -> ChannelAdapter:
        key = ChannelKind(kind)
        if key not in self._instances:
            cls = _REGISTRY.get(key)
            if cls is None:
                raise KeyError(f"Canal no soportado: {key}")
            self._instances[key] = cls(self._settings)
        return self._instances[key]

    @property
    def available(self) -> list[ChannelKind]:
        return sorted(_REGISTRY, key=str)

    async def aclose(self) -> None:
        for adapter in self._instances.values():
            await adapter.aclose()
        self._instances.clear()
