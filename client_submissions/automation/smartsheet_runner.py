# client_submissions/automation/smartsheet_runner.py

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone
from playwright.async_api import (Browser, BrowserContext, Page, Playwright,
                                  async_playwright)

from .smartsheet_fields import (_clean, _upload_attachments,
                                fill_smartsheet_form)
from .smartsheet_state import (DEFAULT_TIMEOUT_MS, SCREENSHOT_DIR,
                               SmartsheetAttachmentError,
                               SmartsheetAutomationError,
                               SmartsheetDryRunResult,
                               SmartsheetFieldNotFoundError,
                               SmartsheetFormLoadError,
                               SmartsheetVerificationRequired,
                               _detect_verification_challenge,
                               _inspect_smartsheet_submission_result,
                               _load_batch_for_submission,
                               _mark_form_completed, _mark_form_loaded,
                               _mark_submit_clicked,
                               _wait_for_human_verification,
                               remove_active_browser)

logger = logging.getLogger(__name__)


# ============================================================
# Envío del formulario
# ============================================================


async def _submit_smartsheet_form(
    page: Page,
    *,
    submission,
    playwright: Playwright,
    browser: Browser,
    context: BrowserContext,
) -> dict[str, Any]:
    """
    Presiona Submit una sola vez y espera una confirmación real.

    Si aparece CAPTCHA después de Submit, conserva la misma
    sesión. Una vez resuelto, la confirmación de Smartsheet se
    detecta automáticamente sin exigir el botón Continue.

    Continue permanece disponible como respaldo.
    """

    submit_button = page.get_by_role(
        "button",
        name="Submit",
        exact=True,
    )

    try:
        await submit_button.wait_for(
            state="visible",
            timeout=15_000,
        )

    except Exception as exc:
        raise SmartsheetFieldNotFoundError(
            f"Smartsheet Submit button was not found: {exc}"
        ) from exc

    try:
        disabled = await submit_button.is_disabled()

    except Exception:
        disabled = False

    if disabled:
        body_text = ""

        try:
            body_text = await page.locator(
                "body",
            ).inner_text()

        except Exception:
            body_text = ""

        raise SmartsheetAutomationError(
            "Smartsheet Submit button is disabled. "
            f"Visible body: {body_text[:3000]!r}"
        )

    original_url = str(
        page.url or "",
    ).strip()

    try:
        original_form_count = await page.locator(
            'form[aria-label*="questions in this form" i]'
        ).count()

    except Exception:
        original_form_count = 0

    print(
        "SMARTSHEET SUBMIT STARTING:",
        {
            "original_url": original_url,
            "original_form_count": original_form_count,
        },
    )

    try:
        await submit_button.scroll_into_view_if_needed()

        await submit_button.click(
            timeout=15_000,
        )

    except Exception as exc:
        raise SmartsheetAutomationError(
            f"Could not click the Smartsheet Submit button: {exc}"
        ) from exc

    submit_clicked_at = timezone.now()

    print(
        "SMARTSHEET SUBMIT CLICKED:",
        {
            "clicked_at": submit_clicked_at.isoformat(),
            "url": page.url,
        },
    )

    timeout_ms = 90_000
    poll_interval_ms = 1000
    elapsed_ms = 0

    final_inspection = {
        "confirmed": False,
        "verification_required": False,
        "error": "",
        "confirmation_reference": "",
        "confirmation_text": "",
        "final_url": page.url,
        "form_count": original_form_count,
        "submit_button_visible": True,
    }

    while elapsed_ms < timeout_ms:
        await page.wait_for_timeout(
            poll_interval_ms,
        )

        elapsed_ms += poll_interval_ms

        final_inspection = await _inspect_smartsheet_submission_result(
            page,
            original_url=original_url,
            original_form_count=(original_form_count),
            submit_button=submit_button,
        )

        inspection_error = str(
            final_inspection.get(
                "error",
                "",
            )
            or ""
        ).strip()

        if inspection_error:
            raise SmartsheetAutomationError(
                inspection_error,
            )

        if final_inspection.get(
            "confirmed",
            False,
        ):
            break

        if final_inspection.get(
            "verification_required",
            False,
        ):
            print(
                "SMARTSHEET VERIFICATION REQUIRED:",
                {
                    "submission_id": submission.pk,
                    "project_id": submission.project_id,
                    "url": page.url,
                    "elapsed_seconds": (elapsed_ms / 1000),
                },
            )

            await _wait_for_human_verification(
                page=page,
                submission=submission,
                playwright=playwright,
                browser=browser,
                context=context,
                stage="after_submit",
            )

            # La espera de confirmación vuelve a comenzar
            # después de resolver el CAPTCHA.
            elapsed_ms = 0

            continue

    if not final_inspection.get(
        "confirmed",
        False,
    ):
        raise SmartsheetAutomationError(
            "Submit was clicked, but Smartsheet did not show "
            "a reliable browser confirmation within 90 seconds. "
            f"Current URL: {page.url!r}. "
            "Form count: "
            f"{final_inspection.get('form_count')!r}. "
            "Submit visible: "
            f"{final_inspection.get('submit_button_visible')!r}. "
            "Visible body: "
            f"{str(final_inspection.get('confirmation_text', ''))[:5000]!r}"
        )

    confirmation_reference = str(
        final_inspection.get(
            "confirmation_reference",
            "",
        )
        or ""
    ).strip()

    confirmation_text = str(
        final_inspection.get(
            "confirmation_text",
            "",
        )
        or ""
    ).strip()

    final_url = str(
        final_inspection.get(
            "final_url",
            page.url,
        )
        or page.url
    ).strip()

    print(
        "SMARTSHEET BROWSER CONFIRMATION RECEIVED:",
        {
            "submission_id": submission.pk,
            "project_id": submission.project_id,
            "final_url": final_url,
            "confirmation_reference": (confirmation_reference),
            "confirmation_text": (confirmation_text[:1000]),
            "elapsed_seconds": (elapsed_ms / 1000),
        },
    )

    return {
        "submit_clicked": True,
        "verification_required": False,
        "browser_confirmation_received": True,
        "confirmation_reference": (confirmation_reference),
        "confirmation_text": confirmation_text,
        "final_url": final_url,
        "submitted_at": (submit_clicked_at.isoformat()),
    }


# ============================================================
# Ejecución Live
# ============================================================


async def run_smartsheet_live(
    *,
    submission,
    attachment_paths: list[str] | None = None,
    headless: bool = True,
) -> SmartsheetDryRunResult:
    """
    Ejecuta la automatización en modo LIVE.

    Llena el formulario, adjunta los archivos y presiona Submit.
    """

    return await run_smartsheet_dry_run(
        submission=submission,
        attachment_paths=attachment_paths,
        headless=headless,
        submit_form=True,
    )


# ============================================================
# Ejecución principal Dry Run
# ============================================================


async def run_smartsheet_dry_run(
    *,
    submission,
    attachment_paths: list[str] | None = None,
    headless: bool = False,
    submit_form: bool | None = None,
) -> SmartsheetDryRunResult:
    """
    Abre el formulario Smartsheet, completa los campos,
    adjunta los ZIP y ejecuta según el modo seleccionado.

    Cuando aparece una verificación humana, mantiene el mismo
    navegador y el mismo event loop activos hasta que:

    - El usuario resuelva el challenge.
    - Smartsheet confirme automáticamente el envío.
    - El usuario presione Continue únicamente como respaldo.
    """

    batch = await _load_batch_for_submission(
        submission,
    )

    if submit_form is None:
        submit_form = bool(
            getattr(
                batch,
                "is_live",
                False,
            )
        )

    execution_mode = "live" if submit_form else "dry_run"

    attachment_paths = [
        str(
            path,
        )
        for path in (attachment_paths or [])
        if str(
            path,
        ).strip()
    ]

    SCREENSHOT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    screenshot_path = SCREENSHOT_DIR / (
        f"submission_{submission.pk}_" f"{submission.public_id}.png"
    )

    playwright: Playwright | None = None

    browser: Browser | None = None

    context: BrowserContext | None = None

    try:
        playwright = await async_playwright().start()

        print(
            "STARTING PLAYWRIGHT:",
            {
                "submission_id": submission.pk,
                "project_id": (submission.project_id),
                "headless": headless,
                "execution_mode": execution_mode,
                "submit_form": submit_form,
                "attachment_count": len(
                    attachment_paths,
                ),
            },
        )

        browser = await playwright.chromium.launch(
            headless=headless,
            slow_mo=(120 if not headless else 0),
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )

        context = await browser.new_context(
            viewport={
                "width": 1440,
                "height": 1100,
            },
            accept_downloads=True,
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 "
                "(X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 "
                "Safari/537.36"
            ),
            locale="en-US",
        )

        page = await context.new_page()

        page.set_default_timeout(
            DEFAULT_TIMEOUT_MS,
        )

        page.on(
            "console",
            lambda message: print(
                "BROWSER CONSOLE:",
                {
                    "type": message.type,
                    "text": message.text,
                },
            ),
        )

        page.on(
            "pageerror",
            lambda error: print(
                "BROWSER PAGE ERROR:",
                str(
                    error,
                ),
            ),
        )

        page.on(
            "requestfailed",
            lambda request: print(
                "BROWSER REQUEST FAILED:",
                {
                    "url": request.url,
                    "failure": request.failure,
                },
            ),
        )

        navigation_attempts = 5

        navigation_attempt = 0

        navigation_error = None

        form_loaded = False

        last_http_status = None

        last_body_text = ""

        retryable_http_statuses = {
            408,
            425,
            429,
            500,
            502,
            503,
            504,
        }

        for navigation_attempt in range(
            1,
            navigation_attempts + 1,
        ):
            print(
                "OPENING SMARTSHEET FORM:",
                {
                    "url": batch.form_url,
                    "attempt": navigation_attempt,
                    "max_attempts": (navigation_attempts),
                },
            )

            try:
                try:
                    await page.goto(
                        "about:blank",
                        wait_until="commit",
                        timeout=15_000,
                    )

                except Exception:
                    pass

                await page.wait_for_timeout(
                    1000,
                )

                navigation_response = await page.goto(
                    batch.form_url,
                    wait_until="domcontentloaded",
                    timeout=90_000,
                )

                last_http_status = (
                    navigation_response.status if navigation_response else None
                )

                print(
                    "SMARTSHEET NAVIGATION RESPONSE:",
                    {
                        "attempt": navigation_attempt,
                        "status": last_http_status,
                        "url": page.url,
                    },
                )

                try:
                    last_body_text = await page.locator(
                        "body",
                    ).inner_text(
                        timeout=10_000,
                    )

                except Exception:
                    last_body_text = ""

                if last_http_status in retryable_http_statuses:
                    navigation_error = SmartsheetFormLoadError(
                        "Smartsheet returned temporary "
                        f"HTTP {last_http_status}. "
                        "Visible body: "
                        f"{last_body_text[:1000]!r}"
                    )

                    if navigation_attempt < navigation_attempts:
                        await page.wait_for_timeout(
                            min(
                                navigation_attempt * 5000,
                                20_000,
                            )
                        )

                        continue

                    break

                if last_http_status is not None and last_http_status >= 400:
                    navigation_error = SmartsheetFormLoadError(
                        "Smartsheet returned HTTP "
                        f"{last_http_status}. "
                        "Visible body: "
                        f"{last_body_text[:1000]!r}"
                    )

                    break

                try:
                    await page.wait_for_load_state(
                        "domcontentloaded",
                        timeout=30_000,
                    )

                except Exception:
                    pass

                await page.wait_for_timeout(
                    3000,
                )

                if await _detect_verification_challenge(
                    page,
                ):
                    await _wait_for_human_verification(
                        page=page,
                        submission=submission,
                        playwright=playwright,
                        browser=browser,
                        context=context,
                        stage="form_load",
                    )

                form_locator = page.locator(
                    "form[aria-label*=" '"questions in this form" i]'
                )

                submitted_by_input = page.locator(
                    'input[type="email"]' '[data-client-id="form-field"]'
                )

                generic_email_input = page.locator(
                    'input[type="email"]',
                )

                try:
                    await form_locator.wait_for(
                        state="attached",
                        timeout=30_000,
                    )

                except Exception:
                    pass

                submitted_ready = False

                try:
                    await submitted_by_input.first.wait_for(
                        state="visible",
                        timeout=30_000,
                    )

                    submitted_ready = True

                except Exception:
                    try:
                        await generic_email_input.first.wait_for(
                            state="visible",
                            timeout=15_000,
                        )

                        submitted_ready = True

                    except Exception as submitted_exc:
                        navigation_error = submitted_exc

                if submitted_ready:
                    form_loaded = True

                    navigation_error = None

                    print(
                        "SMARTSHEET FORM FULLY READY:",
                        {
                            "attempt": (navigation_attempt),
                            "status": (last_http_status),
                            "url": page.url,
                        },
                    )

                    break

                if navigation_attempt < navigation_attempts:
                    await page.wait_for_timeout(
                        min(
                            navigation_attempt * 5000,
                            20_000,
                        )
                    )

                    continue

                break

            except SmartsheetVerificationRequired:
                raise

            except Exception as exc:
                navigation_error = exc

                if navigation_attempt >= navigation_attempts:
                    break

                await page.wait_for_timeout(
                    min(
                        navigation_attempt * 5000,
                        20_000,
                    )
                )

        if not form_loaded:
            raise SmartsheetFormLoadError(
                "Could not load the Smartsheet form after "
                f"{navigation_attempts} attempts. "
                "Last HTTP status: "
                f"{last_http_status!r}. "
                f"Current URL: {page.url!r}. "
                "Visible body: "
                f"{last_body_text[:1000]!r}. "
                f"Last error: {navigation_error}"
            ) from navigation_error

        await page.wait_for_timeout(
            1500,
        )

        await _mark_form_loaded(
            submission,
        )

        try:
            initial_screenshot_path = SCREENSHOT_DIR / (
                f"submission_{submission.pk}_" "form_loaded.png"
            )

            await page.screenshot(
                path=str(
                    initial_screenshot_path.resolve(),
                ),
                full_page=True,
            )

        except Exception as exc:
            print(
                "COULD NOT SAVE INITIAL " "FORM SCREENSHOT:",
                str(
                    exc,
                ),
            )

        if await _detect_verification_challenge(
            page,
        ):
            await _wait_for_human_verification(
                page=page,
                submission=submission,
                playwright=playwright,
                browser=browser,
                context=context,
                stage="before_form_fill",
            )

        fields_filled = await fill_smartsheet_form(
            page,
            submission,
            batch,
        )

        attachments_uploaded = False

        attachment_filenames: list[str] = []

        if attachment_paths:
            file_inputs = page.locator(
                'input[type="file"]',
            )

            file_input_count = await file_inputs.count()

            print(
                "SMARTSHEET FILE INPUT COUNT:",
                file_input_count,
            )

            if file_input_count <= 0:
                raise SmartsheetAttachmentError(
                    "The attachment field did not " "appear in the Smartsheet form."
                )

            attachment_filenames = await _upload_attachments(
                page,
                attachment_paths,
            )

            attachments_uploaded = bool(
                attachment_filenames,
            )

            await page.wait_for_timeout(
                3000,
            )

        if attachment_paths and not attachments_uploaded:
            raise SmartsheetAttachmentError("The ZIP attachments were not uploaded.")

        if attachment_paths and len(
            attachment_filenames,
        ) != len(
            attachment_paths,
        ):
            raise SmartsheetAttachmentError(
                "Not all ZIP parts were attached. "
                f"Expected: {len(attachment_paths)}. "
                "Attached: "
                f"{len(attachment_filenames)}."
            )

        await page.screenshot(
            path=str(
                screenshot_path.resolve(),
            ),
            full_page=True,
        )

        print(
            "SMARTSHEET FINAL SCREENSHOT:",
            str(
                screenshot_path.resolve(),
            ),
        )

        submit_clicked = False

        browser_confirmation_received = False

        confirmation_reference = ""

        confirmation_text = ""

        if submit_form:
            submit_result = await _submit_smartsheet_form(
                page,
                submission=submission,
                playwright=playwright,
                browser=browser,
                context=context,
            )

            submit_clicked = bool(
                submit_result.get(
                    "submit_clicked",
                    False,
                )
            )

            if submit_clicked:
                await _mark_submit_clicked(
                    submission,
                )

            browser_confirmation_received = bool(
                submit_result.get(
                    "browser_confirmation_received",
                    False,
                )
            )

            confirmation_reference = _clean(
                submit_result.get(
                    "confirmation_reference",
                    "",
                )
            )

            confirmation_text = _clean(
                submit_result.get(
                    "confirmation_text",
                    "",
                )
            )

            if not browser_confirmation_received:
                raise SmartsheetAutomationError(
                    "Smartsheet Submit was clicked, "
                    "but no browser confirmation "
                    "was received."
                )

            confirmation_screenshot_path = SCREENSHOT_DIR / (
                f"submission_{submission.pk}_"
                f"{submission.public_id}_"
                "submitted.png"
            )

            try:
                await page.screenshot(
                    path=str(
                        confirmation_screenshot_path.resolve(),
                    ),
                    full_page=True,
                )

                screenshot_path = confirmation_screenshot_path

            except Exception as exc:
                print(
                    "COULD NOT SAVE SUBMISSION " "CONFIRMATION SCREENSHOT:",
                    str(
                        exc,
                    ),
                )

        await page.wait_for_timeout(
            3000,
        )

        html_snapshot = await page.content()

        html_snapshot = html_snapshot[:500_000]

        await _mark_form_completed(
            submission,
            execution_mode=execution_mode,
            final_url=page.url,
            fields_filled=fields_filled,
            attachments_uploaded=(attachments_uploaded),
            attachment_filenames=(attachment_filenames),
            submit_clicked=submit_clicked,
            browser_confirmation_received=(browser_confirmation_received),
            confirmation_reference=(confirmation_reference),
            confirmation_text=(confirmation_text),
        )

        return SmartsheetDryRunResult(
            ok=True,
            final_url=page.url,
            page_title=await page.title(),
            screenshot_path=str(
                screenshot_path.resolve(),
            ),
            html_snapshot=html_snapshot,
            fields_filled=fields_filled,
            attachments_uploaded=(attachments_uploaded),
            attachment_filenames=(attachment_filenames),
            verification_required=False,
            submit_clicked=submit_clicked,
            browser_confirmation_received=(browser_confirmation_received),
            confirmation_reference=(confirmation_reference),
            confirmation_text=confirmation_text,
            metadata={
                "execution_mode": execution_mode,
                "submit_clicked": submit_clicked,
                "browser_confirmation_received": (browser_confirmation_received),
                "confirmation_reference": (confirmation_reference),
                "confirmation_text": (confirmation_text),
                "progressive_form": True,
                "navigation_attempts": (navigation_attempt),
                "http_status": last_http_status,
                "headless": headless,
                "attachment_count": len(
                    attachment_filenames,
                ),
            },
        )

    finally:
        remove_active_browser(
            submission,
        )

        if context:
            try:
                await context.close()

            except Exception:
                logger.exception("Could not close browser context.")

        if browser:
            try:
                await browser.close()

            except Exception:
                logger.exception("Could not close browser.")

        if playwright:
            try:
                await playwright.stop()

            except Exception:
                logger.exception("Could not stop Playwright.")
