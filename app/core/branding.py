"""Color de marca del inquilino.

Quien administra elige **un** color —el de su marca— y de ahí sale todo lo
demás. La alternativa era ofrecer un formulario con un color por cada uso
(relleno, texto, borde, burbuja); nadie acierta a rellenarlo y basta una
combinación desafortunada para dejar un botón ilegible.

El problema real es que el mismo ``--accent`` se usa de dos formas opuestas:

* como **relleno**, con el texto encima (botones, burbujas salientes);
* como **texto o borde** sobre el fondo de la página (enlaces, contornos de
  foco, la barra que marca la conversación abierta).

Un amarillo de marca funciona de relleno con texto negro y desaparece como
texto sobre blanco. Por eso de cada color se derivan tres valores por tema:

``--accent``          el relleno;
``--accent-contrast`` blanco o negro, el que mejor se lea sobre ese relleno;
``--accent-ink``      el mismo color llevado hasta que se lea sobre el fondo.

Los umbrales son los de la norma de accesibilidad WCAG 2.1: 4.5:1 para texto
y 3:1 para elementos de interfaz. No son un adorno: son lo que impide que el
color de la marca deje la consola inservible.

Cuando el inquilino no ha elegido color, esto no genera ni una línea de CSS y
la hoja de estilos queda tal cual, con el azul de siempre.
"""

from __future__ import annotations

import colorsys
import re
from typing import Any

#: Clave dentro de ``Tenant.settings`` donde vive la apariencia.
BRANDING_SETTINGS_KEY = "branding"

#: Fondo sobre el que se lee el color en cada tema. Es ``--surface`` de
#: ``chat.css``: el color de las tarjetas, los paneles y el hilo. El fondo de
#: la página (``--bg``) es más claro en el tema claro y más oscuro en el
#: oscuro, de modo que en ambos contrasta más; el que manda es este.
SURFACE_LIGHT = (0xFF, 0xFF, 0xFF)
SURFACE_DARK = (0x17, 0x1A, 0x20)

#: Umbrales de la WCAG 2.1: texto normal y componente de interfaz.
TEXT_RATIO = 4.5
UI_RATIO = 3.0

#: Mínimo para que un relleno no se confunda con la tarjeta que hay debajo.
#: Muy por debajo de los otros dos a propósito: aquí no se lee nada encima del
#: color, solo hace falta distinguir el borde del botón.
FILL_RATIO = 1.6

_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

Rgb = tuple[int, int, int]


class InvalidColor(ValueError):
    """El valor recibido no es un color hexadecimal."""


def normalize_hex(value: str) -> str:
    """Devuelve ``#rrggbb`` en minúsculas, o levanta ``InvalidColor``."""
    if not isinstance(value, str):
        raise InvalidColor("El color debe ser una cadena")
    text = value.strip()
    if not _HEX.match(text):
        raise InvalidColor("Use un color hexadecimal, por ejemplo #2f5bd7")
    text = text.lower()
    if len(text) == 4:  # #abc -> #aabbcc
        text = "#" + "".join(c * 2 for c in text[1:])
    return text


def _to_rgb(color: str) -> Rgb:
    value = normalize_hex(color)
    return (int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16))


def _to_hex(rgb: Rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _relative_luminance(rgb: Rgb) -> float:
    """Luminancia relativa según la WCAG: cuánta luz emite el color."""

    def canal(valor: int) -> float:
        c = valor / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (canal(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Rgb, b: Rgb) -> float:
    """Razón de contraste entre dos colores. Va de 1 (iguales) a 21."""
    la, lb = _relative_luminance(a), _relative_luminance(b)
    claro, oscuro = max(la, lb), min(la, lb)
    return (claro + 0.05) / (oscuro + 0.05)


def _shift_lightness(rgb: Rgb, amount: float) -> Rgb:
    """Aclara u oscurece conservando el tono y la saturación.

    Se trabaja en HLS y no sumando a cada canal: sumar desplaza el tono —un
    rojo aclarado a fuerza de canales se vuelve rosa anaranjado— y el color
    dejaría de ser el de la marca.
    """
    hue, lum, sat = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    lum = min(1.0, max(0.0, lum + amount))
    r, g, b = colorsys.hls_to_rgb(hue, lum, sat)
    return (round(r * 255), round(g * 255), round(b * 255))


def _readable_on(rgb: Rgb, surface: Rgb, ratio: float) -> Rgb:
    """Aclara u oscurece el color hasta que se lea sobre ``surface``.

    La dirección la marca el fondo: sobre blanco hay que oscurecer, sobre
    negro aclarar. Se avanza a pasos pequeños y se para en cuanto se alcanza
    el umbral, para alejarse del color de la marca lo menos posible.
    """
    if contrast(rgb, surface) >= ratio:
        return rgb
    #: Hacia el negro si el fondo es claro; hacia el blanco si es oscuro.
    direction = -1 if _relative_luminance(surface) > 0.5 else 1
    actual = rgb
    for _ in range(60):
        siguiente = _shift_lightness(actual, direction * 0.02)
        if siguiente == actual:
            break  # Ya está en blanco o negro puros: no hay adónde seguir.
        actual = siguiente
        if contrast(actual, surface) >= ratio:
            return actual
    # Un gris medio puede no alcanzar 4.5:1 ni en blanco ni en negro puros; se
    # devuelve el extremo, que es lo más legible que existe en esa dirección.
    return actual


def _contrast_text(rgb: Rgb) -> Rgb:
    """Blanco o negro sobre ese relleno: el que más contraste dé."""
    blanco: Rgb = (255, 255, 255)
    negro: Rgb = (0, 0, 0)
    return blanco if contrast(rgb, blanco) >= contrast(rgb, negro) else negro


def derive_palette(accent: str) -> dict[str, dict[str, str]]:
    """Deriva los colores de ambos temas a partir del color de la marca."""
    marca = _to_rgb(accent)

    # --- Tema claro ---
    # El relleno es el color de la marca tal cual: es lo que se pidió. Solo se
    # oscurece si es tan pálido que el botón se confundiría con la tarjeta.
    relleno_claro = _readable_on(marca, SURFACE_LIGHT, FILL_RATIO)
    ink_claro = _readable_on(marca, SURFACE_LIGHT, TEXT_RATIO)

    # --- Tema oscuro ---
    # Aquí el relleno sí se aclara siempre que haga falta: un azul marino de
    # marca sobre un panel casi negro se ve como una mancha sin forma.
    relleno_oscuro = _readable_on(marca, SURFACE_DARK, UI_RATIO)
    ink_oscuro = _readable_on(marca, SURFACE_DARK, TEXT_RATIO)

    def bloque(relleno: Rgb, ink: Rgb) -> dict[str, str]:
        texto = _contrast_text(relleno)
        return {
            "accent": _to_hex(relleno),
            "accent_contrast": _to_hex(texto),
            "accent_ink": _to_hex(ink),
            # La burbuja saliente comparte el relleno del acento: cumple el
            # mismo papel —una superficie de color con texto encima— y
            # separarlos solo abriría la puerta a que dejaran de combinar.
            "outbound": _to_hex(relleno),
            "outbound_text": _to_hex(texto),
        }

    return {
        "light": bloque(relleno_claro, ink_claro),
        "dark": bloque(relleno_oscuro, ink_oscuro),
    }


def _declarations(bloque: dict[str, str]) -> str:
    return (
        f"--accent:{bloque['accent']};"
        f"--accent-contrast:{bloque['accent_contrast']};"
        f"--accent-ink:{bloque['accent_ink']};"
        f"--outbound:{bloque['outbound']};"
        f"--outbound-text:{bloque['outbound_text']}"
    )


def brand_css(accent: str | None) -> str:
    """CSS que redefine la paleta, listo para incrustar en la página.

    Repite los tres selectores de ``chat.css`` —el claro, el oscuro del
    sistema y el oscuro elegido a mano— porque el navegador resuelve entre
    reglas de la misma especificidad por orden de aparición: con un solo
    ``:root`` no ganaría al bloque de tema oscuro de la hoja original, y quien
    tuviera el tema oscuro seguiría viendo el azul de siempre.

    Sin color elegido devuelve la cadena vacía y la hoja queda intacta.
    """
    if not accent:
        return ""
    palette = derive_palette(accent)
    claro = _declarations(palette["light"])
    oscuro = _declarations(palette["dark"])
    media = '@media (prefers-color-scheme:dark){:root:not([data-theme="light"])'
    return (
        f":root{{{claro}}}"
        f"{media}{{{oscuro}}}}}"
        f':root[data-theme="dark"]{{{oscuro}}}'
    )


def read_accent(settings: dict[str, Any]) -> str | None:
    """Extrae el color guardado en ``Tenant.settings``, si lo hay."""
    branding = settings.get(BRANDING_SETTINGS_KEY) or {}
    if not isinstance(branding, dict):
        return None
    return branding.get("accent") or None
