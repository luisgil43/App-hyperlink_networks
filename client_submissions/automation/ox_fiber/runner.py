from __future__ import annotations

import os

from playwright.async_api import async_playwright

OX_LOGIN_URL = "https://itg-global.ociusx.com/"
OX_TDS_HOST = "tds-itg.ociusx.com"

DEFAULT_TIMEOUT_MS = 30_000


def _env_bool(
    name: str,
    default: bool = False,
) -> bool:
    value = os.getenv(
        name,
        "true" if default else "false",
    )

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


async def test_ox_login() -> None:
    """
    Prueba controlada OX-FIBER.

    Alcance actual:
    - Abrir Chromium.
    - Navegar al login global de OX.
    - Introducir credenciales desde ENV.
    - Ejecutar Sign in.
    - Confirmar acceso a /modules/multitenant.
    - Esperar la aparición del tenant TDS TELECOM.
    - Validar el enlace real del tenant.
    - Abrir TDS TELECOM.
    - Capturar la nueva pestaña creada por target="_blank".
    - Confirmar acceso al dominio TDS.
    - STOP.

    No selecciona DFN.
    No abre Map.
    No busca proyectos.
    No modifica ningún proyecto.
    """

    username = os.getenv(
        "OX_FIBER_USERNAME",
        "",
    ).strip()

    password = os.getenv(
        "OX_FIBER_PASSWORD",
        "",
    )

    headless = _env_bool(
        "OX_FIBER_HEADLESS",
        default=False,
    )

    if not username:
        raise RuntimeError("OX_FIBER_USERNAME is not configured.")

    if not password:
        raise RuntimeError("OX_FIBER_PASSWORD is not configured.")

    playwright = None
    browser = None

    try:
        # ====================================================
        # PLAYWRIGHT
        # ====================================================

        playwright = await async_playwright().start()

        print("=" * 60)
        print("OX-FIBER PHASE 1 TEST")
        print("=" * 60)

        print(
            "STARTING PLAYWRIGHT:",
            {
                "headless": headless,
                "url": OX_LOGIN_URL,
            },
        )

        browser = await playwright.chromium.launch(
            headless=headless,
            slow_mo=(150 if not headless else 0),
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            ignore_https_errors=True,
            locale="en-US",
        )

        page = await context.new_page()

        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        # ====================================================
        # STEP 1 — OX LOGIN PAGE
        # ====================================================

        print("STEP 1: Opening OX login...")

        await page.goto(
            OX_LOGIN_URL,
            wait_until="domcontentloaded",
        )

        print(f"PASS: OX page opened = {page.url}")

        # DOM real confirmado:
        #
        # Username:
        #   #txtAnvandarnamn
        #
        # Password:
        #   #txtLosenord
        #
        # Sign in:
        #   #btnLogin
        #   onclick="sendTokenRequest()"

        username_input = page.locator("#txtAnvandarnamn")

        password_input = page.locator("#txtLosenord")

        login_button = page.locator("#btnLogin")

        await username_input.wait_for(
            state="visible",
            timeout=DEFAULT_TIMEOUT_MS,
        )

        await password_input.wait_for(
            state="visible",
            timeout=DEFAULT_TIMEOUT_MS,
        )

        await login_button.wait_for(
            state="visible",
            timeout=DEFAULT_TIMEOUT_MS,
        )

        print("PASS: login controls detected")

        # ====================================================
        # STEP 2 — CREDENTIALS
        # ====================================================

        await username_input.fill(username)

        await password_input.fill(password)

        print("PASS: credentials entered")

        # ====================================================
        # STEP 3 — LOGIN
        # ====================================================

        print("STEP 2: Submitting login...")

        await login_button.click()

        # El destino real confirmado después del login es:
        #
        # https://itg-global.ociusx.com/modules/multitenant

        try:
            await page.wait_for_url(
                "**/modules/multitenant**",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception as exc:
            print("INFO: URL after login attempt = " f"{page.url}")

            password_still_visible = (
                await page.locator("#txtLosenord:visible").count() > 0
            )

            if password_still_visible:
                body_text = (await page.locator("body").inner_text()).strip()

                print("INFO: visible page text after " "failed login:")

                print(body_text[:1500])

            raise RuntimeError(
                "OX login did not reach " "/modules/multitenant."
            ) from exc

        print(f"INFO: URL after login = {page.url}")

        print("PASS: OX login")

        print("PASS: OX multitenant reached")

        # ====================================================
        # STEP 4 — TDS TELECOM
        # ====================================================

        print("=" * 60)
        print("OX MULTITENANT — OPEN TDS TELECOM")
        print("=" * 60)

        print("STEP 3: Waiting for TDS TELECOM tenant link...")

        # DOM REAL confirmado manualmente desde DevTools:
        #
        # <a
        #   class="tenant-card-link"
        #   href="https://tds-itg.ociusx.com"
        #   target="_blank"
        #   rel="noopener"
        # >
        #     <div class="tenant-card">
        #         <div class="ext-icon">...</div>
        #         <small>TDS-ITG.OCIUSX.COM</small>
        #         <h2>TDS Telecom</h2>
        #     </div>
        # </a>
        #
        # Importante:
        # no buscamos el H2.
        # Buscamos directamente el enlace real del tenant.

        tds_link = page.locator(
            'a.tenant-card-link[href^="https://tds-itg.ociusx.com"]'
        )

        try:
            await tds_link.wait_for(
                state="visible",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception as exc:
            body_text = (await page.locator("body").inner_text()).strip()

            print("INFO: Multitenant visible text:")

            print(body_text[:2500])

            raise RuntimeError(
                "TDS TELECOM tenant link did not become "
                "visible on the Multitenant page."
            ) from exc

        tds_count = await tds_link.count()

        print(f"INFO: TDS tenant link matches = {tds_count}")

        if tds_count != 1:
            raise RuntimeError(
                "Expected exactly one TDS tenant link, " f"found {tds_count}."
            )

        # ====================================================
        # STEP 5 — VALIDATE TENANT LINK
        # ====================================================

        tds_href = await tds_link.get_attribute("href")

        tds_target = await tds_link.get_attribute("target")

        tds_rel = await tds_link.get_attribute("rel")

        tds_text = (await tds_link.inner_text()).strip()

        print(
            "INFO: TDS tenant link:",
            {
                "href": tds_href,
                "target": tds_target,
                "rel": tds_rel,
                "text": tds_text,
            },
        )

        if not tds_href:
            raise RuntimeError("TDS tenant link has no href.")

        if OX_TDS_HOST not in tds_href.lower():
            raise RuntimeError("Unexpected TDS tenant href: " f"{tds_href}")

        if tds_target != "_blank":
            raise RuntimeError(
                "TDS tenant link no longer uses "
                'target="_blank". '
                f"Found: {tds_target!r}"
            )

        if "TDS TELECOM" not in tds_text.upper():
            raise RuntimeError(
                "TDS tenant link does not contain " "the expected TDS TELECOM text."
            )

        print("PASS: TDS TELECOM tenant found")

        print("PASS: TDS tenant link validated")

        # ====================================================
        # STEP 6 — OPEN TDS IN NEW TAB
        # ====================================================

        print("STEP 4: Opening TDS TELECOM...")

        # target="_blank" genera una nueva Page dentro del
        # mismo BrowserContext.
        #
        # Capturamos esa Page explícitamente.

        try:
            async with context.expect_page(timeout=DEFAULT_TIMEOUT_MS) as new_page_info:

                await tds_link.click()

            tds_page = await new_page_info.value

        except Exception as exc:
            print("INFO: Global page after tenant click = " f"{page.url}")

            print(
                "INFO: Browser pages after click:",
                [open_page.url for open_page in context.pages],
            )

            raise RuntimeError(
                "TDS tenant click did not create " "the expected new browser tab."
            ) from exc

        tds_page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        print("PASS: TDS new tab captured")

        # ====================================================
        # STEP 7 — WAIT FOR TDS
        # ====================================================

        # La nueva Page puede existir primero como about:blank
        # y navegar inmediatamente después.
        #
        # Esperamos explícitamente el host TDS.

        try:
            await tds_page.wait_for_url(
                f"**{OX_TDS_HOST}/**",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception:
            # No fallamos aquí todavía.
            # Validamos la URL final más abajo.
            pass

        try:
            await tds_page.wait_for_load_state(
                "domcontentloaded",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception:
            # No usamos networkidle porque una aplicación web
            # puede mantener conexiones activas.
            pass

        print(f"INFO: TDS page URL = {tds_page.url}")

        # ====================================================
        # STEP 8 — FINAL VALIDATION
        # ====================================================

        if OX_TDS_HOST not in tds_page.url.lower():
            raise RuntimeError("Unexpected TDS tenant URL: " f"{tds_page.url}")

        print("PASS: TDS tenant opened")

        print("PASS: TDS domain confirmed")

        print(f"INFO: Global page remains = {page.url}")

        print(f"INFO: TDS page = {tds_page.url}")

        # ====================================================
        # PHASE RESULT
        # ====================================================

        print("=" * 60)
        print("OX-FIBER PHASE 1 — TENANT ACCESS")
        print("=" * 60)

        print("PASS: OX login")

        print("PASS: TDS TELECOM tenant detected")

        print("PASS: TDS new tab captured")

        print("PASS: TDS domain confirmed")

        print(
            "PASS: TDS TELECOM opened successfully."
        )

        print("=" * 60)

        # ====================================================
        # STEP 9 — TEST DFN
        # ====================================================
        #
        # DFN temporal utilizado únicamente para descubrir y
        # validar el recorrido Dashboard -> DFN -> Map.
        #
        # Más adelante este valor será reemplazado por el DFN
        # derivado dinámicamente desde billing.proyecto_id.
        # ====================================================

        test_dfn = "0087AA_01"

        print("=" * 60)
        print("OX-FIBER — DFN TEST")
        print("=" * 60)

        print(
            f"STEP 5: Looking for DFN = {test_dfn}"
        )

        # DOM real confirmado:
        #
        # <li class="nav-dashboard-level1">
        #
        #   <a
        #     onclick="javascript:"
        #             "adminMaster.makeMenuSelectionActive(this);"
        #     title="0087AA_01"
        #   >
        #       ...
        #       <span class="trim-menu-project-name">
        #           0087AA_01
        #       </span>
        #   </a>
        #
        #   <ul class="nav nav-second-level collapse">
        #       ...
        #       <a href="/dashboard/105/map/?tp=0">
        #           Map
        #       </a>
        #   </ul>
        #
        # </li>

        dfn_link = tds_page.locator(
            f'a[title="{test_dfn}"]'
        )

        try:
            await dfn_link.wait_for(
                state="visible",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception as exc:
            raise RuntimeError(
                f"DFN {test_dfn} did not become visible "
                "in the TDS dashboard."
            ) from exc

        dfn_count = await dfn_link.count()

        print(
            f"INFO: DFN matches = {dfn_count}"
        )

        if dfn_count != 1:
            raise RuntimeError(
                f"Expected exactly one DFN {test_dfn}, "
                f"found {dfn_count}."
            )

        dfn_text = (
            await dfn_link.inner_text()
        ).strip()

        print(
            f"INFO: DFN link text = {dfn_text}"
        )

        print(
            f"PASS: DFN found = {test_dfn}"
        )

        # ====================================================
        # STEP 10 — PROJECT CONTAINER
        # ====================================================

        project_container = dfn_link.locator(
            "xpath=ancestor::li"
            "[contains("
            "concat(' ', normalize-space(@class), ' '), "
            "' nav-dashboard-level1 '"
            ")][1]"
        )

        if await project_container.count() != 1:
            raise RuntimeError(
                f"Could not resolve the project container "
                f"for DFN {test_dfn}."
            )

        print(
            "PASS: DFN project container found"
        )

        # ====================================================
        # STEP 11 — OPEN DFN MENU
        # ====================================================

        print(
            f"STEP 6: Opening DFN menu = {test_dfn}"
        )

        await dfn_link.click()

        print(
            "PASS: DFN selected"
        )

        # ====================================================
        # STEP 12 — FIND MAP FOR THIS DFN
        # ====================================================
        #
        # Importante:
        # buscamos Map solamente DENTRO del <li> correspondiente
        # al DFN seleccionado.
        #
        # No hardcodeamos el ID interno de OX (105).
        # ====================================================

        map_link = project_container.locator(
            'a[href*="/map/"]'
        )

        try:
            await map_link.wait_for(
                state="visible",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception as exc:
            raise RuntimeError(
                f"Map link did not become visible for "
                f"DFN {test_dfn}."
            ) from exc

        map_count = await map_link.count()

        if map_count != 1:
            raise RuntimeError(
                f"Expected exactly one Map link for "
                f"DFN {test_dfn}, found {map_count}."
            )

        map_href = await map_link.get_attribute(
            "href"
        )

        map_text = (
            await map_link.inner_text()
        ).strip()

        print(
            "INFO: DFN Map:",
            {
                "dfn": test_dfn,
                "text": map_text,
                "href": map_href,
            },
        )

        if not map_href:
            raise RuntimeError(
                f"Map link for DFN {test_dfn} "
                "has no href."
            )

        if "/map/" not in map_href:
            raise RuntimeError(
                f"Unexpected Map href for "
                f"DFN {test_dfn}: {map_href}"
            )

        print(
            f"PASS: Map found for DFN = {test_dfn}"
        )

        # ====================================================
        # STEP 13 — OPEN MAP
        # ====================================================

        print(
            f"STEP 7: Opening Map for DFN = {test_dfn}"
        )

        await map_link.click()

        try:
            await tds_page.wait_for_url(
                "**/map/**",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception as exc:
            raise RuntimeError(
                "Map navigation was not confirmed. "
                f"Current URL: {tds_page.url}"
            ) from exc

        try:
            await tds_page.wait_for_load_state(
                "domcontentloaded",
                timeout=DEFAULT_TIMEOUT_MS,
            )

        except Exception:
            pass

        print(
            f"INFO: Map URL = {tds_page.url}"
        )

        if "/map/" not in tds_page.url.lower():
            raise RuntimeError(
                "Unexpected URL after opening Map: "
                f"{tds_page.url}"
            )

        print(
            "PASS: Map opened"
        )

        # ====================================================
        # CURRENT TEST RESULT
        # ====================================================

        print("=" * 60)
        print("OX-FIBER — DASHBOARD TO MAP")
        print("=" * 60)

        print(
            "PASS: OX login"
        )

        print(
            "PASS: TDS tenant opened"
        )

        print(
            f"PASS: DFN found = {test_dfn}"
        )

        print(
            f"PASS: DFN selected = {test_dfn}"
        )

        print(
            f"PASS: Map found = {map_href}"
        )

        print(
            f"PASS: Map opened = {tds_page.url}"
        )

        print(
            "STOP: Map reached successfully. "
            "No marker was searched and "
            "no project data was modified."
        )

        print("=" * 60)

        # ====================================================
        # DEVELOPMENT VISUAL CHECK
        # ====================================================

        if not headless:
            await tds_page.bring_to_front()

            await tds_page.wait_for_timeout(
                10_000
            )

    finally:
        if browser is not None:
            await browser.close()

        if playwright is not None:
            await playwright.stop()
