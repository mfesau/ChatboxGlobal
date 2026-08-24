"""Adaptadores de canal.

La importación de los módulos concretos ejecuta el decorador
``@register_channel``, que los inscribe en el registro. Para incorporar un canal
nuevo, añada aquí su importación.
"""

from app.channels import msbot, web, whatsapp  # noqa: F401  (registro por efecto lateral)
from app.channels.base import (
    ChannelAdapter,
    ChannelError,
    ChannelRegistry,
    SignatureError,
    register_channel,
)

__all__ = [
    "ChannelAdapter",
    "ChannelError",
    "ChannelRegistry",
    "SignatureError",
    "register_channel",
]
