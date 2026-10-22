"""El chatbox en un navegador de verdad: reconexión, cierre y conversación nueva.

Los fallos de esta parte no los ven las pruebas del servidor, porque viven en
el navegador: el historial que se pintaba dos veces al reconectar, la
conversación cerrada que el cliente seguía viendo abierta, el hilo nuevo que
arrastraba el anterior. Aquí se abre el chatbox con Playwright contra un
servidor propio (``tests/e2e/server.py``) y se comprueba lo que ve el cliente.

Se saltan solas si Playwright o su Chromium no están instalados::

    pip install playwright==1.58.0
    python -m playwright install chromium
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

playwright_api = pytest.importorskip("playwright.async_api")

from tests.e2e import server as e2e  # noqa: E402

pytestmark = pytest.mark.require_department

CLIENT_PASSWORD = "clave-cliente-e2e"  # noqa: S105  solo existe en la base temporal

#: Lo que tarda como mucho cada espera; una prueba atascada no debe colgar.
TIMEOUT_MS = 10_000


@pytest.fixture(scope="module")
def chat_server(tmp_path_factory):
    servidor = e2e.start(Path(tmp_path_factory.mktemp("e2e")))
    yield servidor
    e2e.stop(servidor)


@pytest_asyncio.fixture
async def chat(chat_server):
    """Una pestaña con el chatbox, cuyos WebSocket se pueden cortar a voluntad.

    API asíncrona de Playwright: las pruebas ya corren dentro del bucle de
    pytest-asyncio, y la síncrona se niega a arrancar dentro de uno.
    """
    async with playwright_api.async_playwright() as playwright:
        try:
            browser = await playwright.chromium.launch()
        except playwright_api.Error as exc:  # Chromium sin descargar
            pytest.skip(f"Chromium de Playwright no disponible: {exc}")
        context = await browser.new_context(base_url=chat_server.base_url)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT_MS)
        sockets: list = []

        async def interceptar(ws) -> None:
            # Sin manejadores propios, Playwright reenvía todo tal cual: solo
            # se guarda la ruta para poder cerrar el socket desde la prueba.
            ws.connect_to_server()
            sockets.append(ws)

        await page.route_web_socket(re.compile(r".*/ws/chat$"), interceptar)
        page.sockets = sockets
        yield page
        await browser.close()


async def _register_and_choose(page, correo: str) -> None:
    await page.goto("/")
    await page.click("#show-register")
    await page.fill("#register-name", "Cliente E2E")
    await page.fill("#register-email", correo)
    await page.fill("#register-password", CLIENT_PASSWORD)
    await page.click("#register-button")
    await _choose_department(page)


async def _choose_department(page) -> None:
    """Elegir área es obligatorio: sin esto no se puede escribir."""
    await page.locator(
        "#department-options .gate__department", has_text=e2e.DEPARTMENT
    ).click()
    await page.wait_for_selector("#shell:not([hidden])")
    await _wait_online(page)


async def _wait_online(page) -> None:
    await page.wait_for_function(
        "() => document.querySelector('#status-dot')?.dataset.state === 'online'"
    )


async def _send(page, texto: str) -> None:
    await page.fill("#composer-input", texto)
    await page.click("#send-button")


async def _bubbles(page) -> list[str]:
    return await page.locator("#thread .bubble").all_inner_texts()


async def _wait_for_bubbles(page, cuantas: int) -> None:
    await page.wait_for_function(
        "n => document.querySelectorAll('#thread .bubble').length >= n", arg=cuantas
    )


async def _admin_solves_the_conversation(base_url: str) -> None:
    async with httpx.AsyncClient(base_url=base_url) as admin:
        login = await admin.post(
            "/api/session/login", json={"email": e2e.ADMIN_EMAIL, "password": e2e.ADMIN_PASSWORD}
        )
        assert login.status_code == 200, login.text
        hilos = (await admin.get("/api/conversations?scope=all&status=")).json()
        abierta = next(h for h in hilos if h["channel"] == "web" and h["status"] == "open")
        hecho = await admin.post(
            f"/api/conversations/{abierta['id']}/state", json={"state": "solved"}
        )
        assert hecho.status_code == 200, hecho.text


# --------------------------------------------------------------------------- #
async def test_reconnecting_does_not_duplicate_the_thread(chat):
    await _register_and_choose(chat, "reconecta@clientes.local")
    await _send(chat, "Hola, primera consulta")
    await _wait_for_bubbles(chat, 2)  # lo enviado y la respuesta automática
    antes = await _bubbles(chat)

    # El proxy corta el WebSocket tras un rato en silencio; el chatbox se
    # reconecta solo y recibe otra vez el historial con `ready`.
    sockets_antes = len(chat.sockets)
    await chat.sockets[-1].close()
    for _ in range(100):
        if len(chat.sockets) > sockets_antes:
            break
        await chat.wait_for_timeout(100)
    assert len(chat.sockets) > sockets_antes, "el chatbox no se reconectó"
    await _wait_online(chat)
    await chat.wait_for_timeout(500)
    assert await _bubbles(chat) == antes

    # Recargar la página tampoco duplica nada.
    await chat.reload()
    await _wait_for_bubbles(chat, len(antes))
    await chat.wait_for_timeout(500)
    assert await _bubbles(chat) == antes


async def test_a_closed_conversation_shows_as_closed_and_a_new_one_starts_empty(
    chat, chat_server
):
    await _register_and_choose(chat, "cierre@clientes.local")
    await _send(chat, "Consulta que se va a cerrar")
    await _wait_for_bubbles(chat, 2)

    await _admin_solves_the_conversation(chat_server.base_url)

    # El cliente lo ve sin recargar: aviso de cerrada y sin cuadro de texto.
    await chat.wait_for_selector("#closed-notice:not([hidden])")
    assert await chat.locator("#composer-form").is_hidden()

    # Conversación nueva: vuelve a preguntar el área y empieza vacía.
    await chat.click("#new-chat-button")
    await chat.wait_for_selector("#department-card:not([hidden])")
    await _choose_department(chat)
    await chat.wait_for_timeout(500)
    assert await _bubbles(chat) == []
    assert await chat.locator("#closed-notice").is_hidden()

    await _send(chat, "Otra consulta distinta")
    await _wait_for_bubbles(chat, 1)
    await chat.wait_for_timeout(500)
    textos = await _bubbles(chat)
    assert textos.count("Otra consulta distinta") == 1
    assert not any("se va a cerrar" in texto for texto in textos)
