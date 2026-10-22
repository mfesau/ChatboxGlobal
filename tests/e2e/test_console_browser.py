"""La consola en un navegador de verdad: carga sin errores y se puede trabajar.

El JavaScript de la consola no tiene pruebas unitarias: un nombre mal importado
o una función que ya no existe solo se ve al abrirla. Esta prueba la abre como
administración, recorre cada pestaña y atiende una conversación de punta a
punta, y falla ante cualquier error de JavaScript en la página.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import pytest_asyncio

playwright_api = pytest.importorskip("playwright.async_api")

from tests.e2e import server as e2e  # noqa: E402
from tests.e2e.test_chat_browser import (  # noqa: E402
    TIMEOUT_MS,
    _bubbles,
    _register_and_choose,
    _send,
    _wait_for_bubbles,
)

pytestmark = pytest.mark.require_department


@pytest.fixture(scope="module")
def chat_server(tmp_path_factory):
    servidor = e2e.start(Path(tmp_path_factory.mktemp("e2e-consola")))
    yield servidor
    e2e.stop(servidor)


@pytest_asyncio.fixture
async def browser():
    async with playwright_api.async_playwright() as playwright:
        try:
            navegador = await playwright.chromium.launch()
        except playwright_api.Error as exc:
            pytest.skip(f"Chromium de Playwright no disponible: {exc}")
        yield navegador
        await navegador.close()


async def _page(browser, base_url: str, errores: list[str]):
    context = await browser.new_context(base_url=base_url, viewport={"width": 1400, "height": 900})
    page = await context.new_page()
    page.set_default_timeout(TIMEOUT_MS)
    page.on("pageerror", lambda exc: errores.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: errores.append(f"console.{msg.type}: {msg.text}")
        if msg.type == "error" and "Failed to load resource" not in msg.text
        else None,
    )
    return page


async def _login_admin(page) -> None:
    await page.goto("/")
    await page.fill("#login-email", e2e.ADMIN_EMAIL)
    await page.fill("#login-password", e2e.ADMIN_PASSWORD)
    await page.click("#login-button")
    await page.wait_for_url(re.compile(r".*/console$"))
    await page.wait_for_selector("#work-department")


async def test_every_admin_tab_opens_without_javascript_errors(browser, chat_server):
    errores: list[str] = []
    page = await _page(browser, chat_server.base_url, errores)
    await _login_admin(page)

    await page.click("#admin-button")
    await page.wait_for_selector("#admin-panel:not([hidden])")
    pestanas = await page.locator(".admin-tabs [data-admin-tab]").all()
    assert len(pestanas) >= 10
    for pestana in pestanas:
        nombre = await pestana.get_attribute("data-admin-tab")
        await pestana.click()
        await page.wait_for_selector(f"#admin-tab-{nombre}:not([hidden])")
        await page.wait_for_timeout(300)
    await page.click("#admin-close")

    await page.click("#supervisor-button")
    await page.wait_for_timeout(800)

    assert errores == []


async def test_an_agent_answers_a_customer_from_the_console(browser, chat_server):
    errores: list[str] = []
    cliente = await _page(browser, chat_server.base_url, [])
    await _register_and_choose(cliente, "desde-consola@clientes.local")
    await _send(cliente, "Necesito ayuda con mi reserva")
    await _wait_for_bubbles(cliente, 2)

    consola = await _page(browser, chat_server.base_url, errores)
    await _login_admin(consola)
    fila = consola.locator("#conversation-list li", has_text="Cliente E2E").first
    await fila.click()
    await consola.wait_for_selector("#console-thread .bubble")
    assert "Necesito ayuda con mi reserva" in await consola.locator("#console-thread").inner_text()

    await consola.fill("#console-input", "Con gusto le ayudo")
    await consola.click("#console-send")
    await _wait_for_bubbles(cliente, 3)
    assert (await _bubbles(cliente))[-1].startswith("Con gusto le ayudo")

    assert errores == []
