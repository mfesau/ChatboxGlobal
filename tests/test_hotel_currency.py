"""El hotel cobra en guaraníes: «Gs. 350.000», sin decimales."""

from __future__ import annotations

import pytest

from app.core.hotel_booking import format_price


@pytest.mark.parametrize(
    ("cents", "currency", "esperado"),
    [
        (35_000_000, "PYG", "Gs. 350.000"),
        (5_000, "PYG", "Gs. 50"),
        (123_456_700, "PYG", "Gs. 1.234.567"),
        (12_345, "USD", "123.45 USD"),
    ],
)
def test_prices_are_written_the_paraguayan_way(cents, currency, esperado):
    assert format_price(cents, currency) == esperado
