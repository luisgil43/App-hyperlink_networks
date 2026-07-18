# client_submissions/automation/smartsheet_state.py

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from django.urls import reverse
from django.utils import timezone
from playwright.async_api import (Browser, BrowserContext, Locator, Page,
                                  Playwright)

from client_submission_remote.models import RemoteBrowserSession
from client_submission_remote.services import (capture_remote_screenshot,
                                               close_remote_session,
                                               get_or_create_remote_session,
                                               get_remote_session_by_id,
                                               process_next_remote_action)

logger = logging.getLogger(__name__)


# ============================================================
# Navegadores vivos
# ============================================================

ACTIVE_BROWSERS: dict[str, dict] = {}


def register_active_browser(
    *,
    submission,
    playwright: Playwright,
    browser: Browser,
    context: BrowserContext,
    page: Page,
):
    """
    Registra un navegador vivo.

    Mientras exista en ACTIVE_BROWSERS el navegador NO debe
    cerrarse automáticamente.

    La clave utilizada será el public_id del Submission.
    """

    ACTIVE_BROWSERS[str(submission.public_id)] = {
        "submission_id": submission.pk,
        "playwright": playwright,
        "browser": browser,
        "context": context,
        "page": page,
        "created_at": timezone.now(),
    }


def get_active_browser(
    submission,
):
    return ACTIVE_BROWSERS.get(
        str(submission.public_id),
    )


def remove_active_browser(
    submission,
):
    return ACTIVE_BROWSERS.pop(
        str(submission.public_id),
        None,
    )


async def close_active_browser(
    submission,
):
    """
    Cierra completamente un navegador registrado.
    """

    browser_data = remove_active_browser(
        submission,
    )

    if not browser_data:
        return

    try:
        await browser_data["context"].close()

    except Exception:
        logger.exception("Could not close browser context.")

    try:
        await browser_data["browser"].close()

    except Exception:
        logger.exception("Could not close browser.")

    try:
        await browser_data["playwright"].stop()

    except Exception:
        logger.exception("Could not stop Playwright.")


@sync_to_async(
    thread_sensitive=True,
)
def _mark_browser_confirmed(
    submission,
    reference="",
):
    submission.mark_browser_confirmed(
        reference=reference,
        save=True,
    )


# ============================================================
# Excepciones
# ============================================================


class SmartsheetAutomationError(Exception):
    """Error base de automatización del formulario."""


class SmartsheetFormLoadError(
    SmartsheetAutomationError,
):
    """El formulario no pudo cargarse correctamente."""


class SmartsheetFieldNotFoundError(
    SmartsheetAutomationError,
):
    """No se pudo encontrar un campo obligatorio."""


class SmartsheetAttachmentError(
    SmartsheetAutomationError,
):
    """No se pudo adjuntar el archivo esperado."""


class SmartsheetVerificationRequired(
    SmartsheetAutomationError,
):
    """
    El formulario requiere intervención humana.

    Ejemplo:
    - CAPTCHA
    - Turnstile
    - reCAPTCHA
    - challenge inesperado
    """


class SmartsheetRestartSubmissionRequested(
    SmartsheetVerificationRequired,
):
    """
    Indica que el CAPTCHA venció o se cerró y que debe
    reiniciarse únicamente el Submission actual.

    No representa un fallo definitivo.
    """


# ============================================================
# Registro del clic en Submit
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def _mark_submit_clicked(
    submission,
):
    """
    Registra el momento exacto en que el botón Submit fue
    presionado en el navegador.

    Esto NO significa que Smartsheet haya confirmado el envío;
    únicamente indica que el clic fue ejecutado.
    """

    now = timezone.now()

    submission.submit_clicked_at = now

    browser_state = submission.browser_state or {}

    browser_state["submit_clicked_at"] = now.isoformat()

    submission.browser_state = browser_state

    submission.save(
        update_fields=[
            "submit_clicked_at",
            "browser_state",
            "updated_at",
        ]
    )

    return now


# ============================================================
# Resultado
# ============================================================


@dataclass
class SmartsheetDryRunResult:
    ok: bool

    final_url: str = ""

    page_title: str = ""

    screenshot_path: str = ""

    html_snapshot: str = ""

    fields_filled: dict[str, Any] = field(
        default_factory=dict,
    )

    attachments_uploaded: bool = False

    attachment_filenames: list[str] = field(
        default_factory=list,
    )

    verification_required: bool = False

    submit_clicked: bool = False

    browser_confirmation_received: bool = False

    confirmation_reference: str = ""

    confirmation_text: str = ""

    metadata: dict[str, Any] = field(
        default_factory=dict,
    )

    # ========================================================
    # Compatibilidad con código anterior
    # ========================================================

    @property
    def attachment_uploaded(
        self,
    ) -> bool:
        return self.attachments_uploaded

    @property
    def attachment_filename(
        self,
    ) -> str:
        if not self.attachment_filenames:
            return ""

        return self.attachment_filenames[0]


# ============================================================
# Configuración
# ============================================================

DEFAULT_TIMEOUT_MS = 30_000

SCREENSHOT_DIR = Path("tmp/client_submissions/screenshots")


# ============================================================
# Helpers Django ORM para contexto async
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def _load_batch_for_submission(
    submission,
):
    """
    Carga explícitamente el Batch asociado al Submission
    fuera del contexto ORM async.
    """

    return submission.batch


@sync_to_async(
    thread_sensitive=True,
)
def _mark_form_loaded(
    submission,
):
    """
    Guarda la fecha en que el formulario terminó de cargar.
    """

    submission.form_loaded_at = timezone.now()

    submission.save(
        update_fields=[
            "form_loaded_at",
            "updated_at",
        ]
    )

    return submission.form_loaded_at


@sync_to_async(
    thread_sensitive=True,
)
def _mark_form_completed(
    submission,
    *,
    execution_mode: str,
    final_url: str,
    fields_filled: dict,
    attachments_uploaded: bool,
    attachment_filenames: list[str],
    submit_clicked: bool,
    browser_confirmation_received: bool,
    confirmation_reference: str,
    confirmation_text: str,
):
    """
    Guarda el resultado del llenado del formulario.

    Funciona tanto para:

        dry_run
        live

    También registra si Submit fue presionado y si el
    navegador confirmó el envío.
    """

    now = timezone.now()

    submission.form_completed_at = now

    execution_key = "live" if execution_mode == "live" else "dry_run"

    submission.browser_state = {
        **(submission.browser_state or {}),
        execution_key: {
            "completed_at": now.isoformat(),
            "final_url": final_url,
            "fields_filled": fields_filled,
            "attachments_uploaded": (attachments_uploaded),
            "attachment_filenames": (attachment_filenames),
            "attachment_count": len(
                attachment_filenames,
            ),
            "submit_clicked": submit_clicked,
            "browser_confirmation_received": (browser_confirmation_received),
            "confirmation_reference": (confirmation_reference),
            "confirmation_text": (confirmation_text),
        },
    }

    submission.save(
        update_fields=[
            "form_completed_at",
            "browser_state",
            "updated_at",
        ]
    )

    return now


@sync_to_async(
    thread_sensitive=True,
)
def _mark_verification_required(
    submission,
):
    """
    Marca el Submission como esperando intervención humana.

    El navegador permanecerá abierto para continuar
    posteriormente.
    """

    now = timezone.now()

    submission.status = submission.Status.AWAITING_VERIFICATION

    submission.verification_required_at = now

    submission.save(
        update_fields=[
            "status",
            "verification_required_at",
            "updated_at",
        ]
    )

    return now


@sync_to_async(
    thread_sensitive=True,
)
def _get_human_verification_state(
    submission,
) -> dict:
    """
    Recarga el Submission desde la base de datos y devuelve
    el estado persistido de verificación humana.
    """

    from client_submissions.models import ClientSubmission

    current_submission = ClientSubmission.objects.select_related(
        "batch",
    ).get(
        pk=submission.pk,
    )

    browser_state = (
        current_submission.browser_state
        if isinstance(
            current_submission.browser_state,
            dict,
        )
        else {}
    )

    verification_state = browser_state.get(
        "human_verification",
        {},
    )

    if not isinstance(
        verification_state,
        dict,
    ):
        verification_state = {}

    return {
        "submission_status": (current_submission.status),
        "batch_status": (current_submission.batch.status),
        "verification_state": (verification_state),
    }


@sync_to_async(
    thread_sensitive=True,
)
def _update_human_verification_state(
    submission,
    **changes,
) -> dict:
    """
    Actualiza browser_state["human_verification"] sin eliminar
    el resto del estado del navegador.
    """

    from client_submissions.models import ClientSubmission

    current_submission = ClientSubmission.objects.select_related(
        "batch",
    ).get(
        pk=submission.pk,
    )

    browser_state = (
        dict(
            current_submission.browser_state,
        )
        if isinstance(
            current_submission.browser_state,
            dict,
        )
        else {}
    )

    verification_state = browser_state.get(
        "human_verification",
        {},
    )

    if not isinstance(
        verification_state,
        dict,
    ):
        verification_state = {}

    verification_state = {
        **verification_state,
        **changes,
    }

    browser_state["human_verification"] = verification_state

    current_submission.browser_state = browser_state

    current_submission.save(
        update_fields=[
            "browser_state",
            "updated_at",
        ]
    )

    return verification_state


@sync_to_async(
    thread_sensitive=True,
)
def _mark_verification_completed(
    submission,
):
    """
    Marca la verificación como completada y devuelve tanto el
    Submission como el Batch al estado de procesamiento.
    """

    from client_submissions.models import (ClientSubmission,
                                           ClientSubmissionBatch)

    current_submission = ClientSubmission.objects.select_related(
        "batch",
    ).get(
        pk=submission.pk,
    )

    now = timezone.now()

    browser_state = (
        dict(
            current_submission.browser_state,
        )
        if isinstance(
            current_submission.browser_state,
            dict,
        )
        else {}
    )

    verification_state = browser_state.get(
        "human_verification",
        {},
    )

    if not isinstance(
        verification_state,
        dict,
    ):
        verification_state = {}

    verification_state.update(
        {
            "continue_requested_at": None,
            "continue_requested_by": None,
            "continue_requested_username": "",
            "worker_checked_at": (now.isoformat()),
            "captcha_cleared": True,
            "session_available": False,
            "message": (
                "Human verification was completed. " "Automation resumed successfully."
            ),
        }
    )

    browser_state["human_verification"] = verification_state

    current_submission.browser_state = browser_state

    current_submission.verification_completed_at = now

    current_submission.status = ClientSubmission.Status.SUBMITTING

    current_submission.save(
        update_fields=[
            "browser_state",
            "verification_completed_at",
            "status",
            "updated_at",
        ]
    )

    batch = current_submission.batch

    if batch.status == ClientSubmissionBatch.Status.AWAITING_VERIFICATION:
        batch.status = ClientSubmissionBatch.Status.RUNNING

        batch.paused_at = None

        batch.last_activity_at = now

        batch.current_submission = current_submission

        batch.save(
            update_fields=[
                "status",
                "paused_at",
                "last_activity_at",
                "current_submission",
                "updated_at",
            ]
        )

    return verification_state


async def _retry_verification_submit(
    *,
    page: Page,
    submission,
    stage: str,
    verification_state: dict,
) -> bool:
    """
    Vuelve a pulsar Submit dentro de la misma página y sesión
    de Playwright para provocar nuevamente el CAPTCHA.

    Utiliza varios métodos de búsqueda porque Smartsheet puede
    modificar el rol accesible del botón después del primer
    intento de envío.
    """

    processed_at = timezone.now()

    retry_requested_at = verification_state.get(
        "retry_challenge_requested_at",
    )

    if not retry_requested_at:
        return False

    try:
        retry_count = int(
            verification_state.get(
                "retry_challenge_count",
                0,
            )
            or 0
        )
    except (
        TypeError,
        ValueError,
    ):
        retry_count = 0

    if stage != "after_submit":
        error_message = (
            "The CAPTCHA cannot be shown again from this stage. "
            "The browser is not currently waiting after Submit."
        )

        await _update_human_verification_state(
            submission,
            retry_challenge_processed_at=(processed_at.isoformat()),
            retry_challenge_clicked_at=None,
            retry_challenge_error=error_message,
            worker_checked_at=processed_at.isoformat(),
            message=error_message,
        )

        print(
            "CAPTCHA RETRY REJECTED:",
            {
                "submission_id": submission.pk,
                "stage": stage,
                "url": page.url,
                "error": error_message,
            },
        )

        return False

    try:
        if page.is_closed():
            raise SmartsheetAutomationError(
                "The active Smartsheet page is already closed."
            )

        # ====================================================
        # Recuperar el desplazamiento después del CAPTCHA
        # ====================================================

        try:
            await page.evaluate("""
                () => {
                    document.documentElement.style.overflow = "";
                    document.body.style.overflow = "";

                    const elements = Array.from(
                        document.querySelectorAll("*")
                    );

                    for (const element of elements) {
                        const style = window.getComputedStyle(element);

                        if (
                            element.scrollHeight > element.clientHeight &&
                            (
                                style.overflowY === "auto" ||
                                style.overflowY === "scroll" ||
                                style.overflowY === "hidden"
                            )
                        ) {
                            element.style.overflowY = "auto";
                        }
                    }
                }
                """)
        except Exception:
            pass

        # ====================================================
        # Buscar el botón mediante varios selectores
        # ====================================================

        submit_candidates = [
            page.get_by_role(
                "button",
                name="Submit",
                exact=True,
            ),
            page.locator(
                'button:has-text("Submit")',
            ),
            page.locator(
                'button[type="submit"]',
            ),
            page.locator(
                'input[type="submit"]',
            ),
            page.locator(
                '[data-client-id*="submit" i]',
            ),
            page.locator(
                '[aria-label="Submit" i]',
            ),
        ]

        submit_button = None
        discovered_candidates = []

        for locator in submit_candidates:
            try:
                count = await locator.count()

                for index in range(count):
                    candidate = locator.nth(index)

                    try:
                        text = ""

                        try:
                            text = (
                                await candidate.inner_text(
                                    timeout=1000,
                                )
                            ).strip()
                        except Exception:
                            try:
                                text = str(
                                    await candidate.get_attribute(
                                        "value",
                                    )
                                    or ""
                                ).strip()
                            except Exception:
                                text = ""

                        visible = await candidate.is_visible()

                        discovered_candidates.append(
                            {
                                "text": text,
                                "visible": visible,
                            }
                        )

                        if not visible:
                            continue

                        submit_button = candidate
                        break

                    except Exception:
                        continue

                if submit_button is not None:
                    break

            except Exception:
                continue

        # ====================================================
        # Respaldo: búsqueda directa mediante JavaScript
        # ====================================================

        if submit_button is None:
            clicked_by_javascript = await page.evaluate("""
                () => {
                    const candidates = Array.from(
                        document.querySelectorAll(
                            'button, input[type="submit"], [role="button"]'
                        )
                    );

                    const submit = candidates.find((element) => {
                        const text = (
                            element.innerText ||
                            element.textContent ||
                            element.value ||
                            element.getAttribute("aria-label") ||
                            ""
                        ).trim().toLowerCase();

                        const style = window.getComputedStyle(element);

                        const rect = element.getBoundingClientRect();

                        const visible =
                            style.display !== "none" &&
                            style.visibility !== "hidden" &&
                            Number(style.opacity || 1) > 0 &&
                            rect.width > 0 &&
                            rect.height > 0;

                        return visible && text === "submit";
                    });

                    if (!submit) {
                        return false;
                    }

                    if (
                        submit.disabled ||
                        submit.getAttribute("aria-disabled") === "true"
                    ) {
                        return false;
                    }

                    submit.scrollIntoView({
                        behavior: "instant",
                        block: "center",
                        inline: "center"
                    });

                    submit.click();

                    return true;
                }
                """)

            if not clicked_by_javascript:
                try:
                    button_debug = await page.evaluate("""
                        () => Array.from(
                            document.querySelectorAll(
                                'button, input[type="submit"], [role="button"]'
                            )
                        ).map((element) => ({
                            text: (
                                element.innerText ||
                                element.textContent ||
                                element.value ||
                                element.getAttribute("aria-label") ||
                                ""
                            ).trim(),
                            disabled:
                                Boolean(element.disabled) ||
                                element.getAttribute("aria-disabled") === "true",
                            display:
                                window.getComputedStyle(element).display,
                            visibility:
                                window.getComputedStyle(element).visibility
                        })).slice(0, 100)
                        """)
                except Exception:
                    button_debug = []

                raise SmartsheetFieldNotFoundError(
                    "The Smartsheet Submit button could not be found "
                    "in the active browser session. "
                    f"Playwright candidates: {discovered_candidates!r}. "
                    f"DOM buttons: {button_debug!r}"
                )

        else:
            try:
                disabled = await submit_button.is_disabled()
            except Exception:
                disabled = False

            if disabled:
                raise SmartsheetAutomationError(
                    "The Smartsheet Submit button is disabled."
                )

            await submit_button.scroll_into_view_if_needed()

            await page.wait_for_timeout(
                500,
            )

            try:
                await submit_button.click(
                    timeout=15_000,
                )

            except Exception:
                await submit_button.evaluate("""
                    element => {
                        element.scrollIntoView({
                            behavior: "instant",
                            block: "center",
                            inline: "center"
                        });

                        element.click();
                    }
                    """)

        clicked_at = timezone.now()

        retry_count += 1

        await page.wait_for_timeout(
            2500,
        )

        challenge_visible = await _detect_verification_challenge(
            page,
        )

        if challenge_visible:
            message = (
                "Submit was clicked again and a new CAPTCHA "
                "is visible in the active browser session."
            )
        else:
            message = (
                "Submit was clicked again, but a visible CAPTCHA "
                "has not been detected yet. Check the Chromium window."
            )

        await _update_human_verification_state(
            submission,
            retry_challenge_processed_at=(clicked_at.isoformat()),
            retry_challenge_clicked_at=(clicked_at.isoformat()),
            retry_challenge_error="",
            retry_challenge_count=retry_count,
            worker_checked_at=clicked_at.isoformat(),
            captcha_cleared=False,
            session_available=True,
            session_url=page.url,
            message=message,
        )

        print(
            "CAPTCHA RETRY SUBMIT CLICKED:",
            {
                "submission_id": submission.pk,
                "stage": stage,
                "retry_count": retry_count,
                "clicked_at": clicked_at.isoformat(),
                "challenge_visible": challenge_visible,
                "url": page.url,
            },
        )

        return True

    except Exception as exc:
        error_message = (
            "The worker could not click Submit again in the "
            f"active browser session: {exc}"
        )

        await _update_human_verification_state(
            submission,
            retry_challenge_processed_at=(processed_at.isoformat()),
            retry_challenge_clicked_at=None,
            retry_challenge_error=error_message,
            worker_checked_at=processed_at.isoformat(),
            session_available=True,
            session_url=page.url if not page.is_closed() else "",
            message=error_message,
        )

        print(
            "CAPTCHA RETRY SUBMIT FAILED:",
            {
                "submission_id": submission.pk,
                "stage": stage,
                "url": (page.url if not page.is_closed() else ""),
                "error": str(exc),
            },
        )

        return False


# ============================================================
# Espera de verificación humana
# ============================================================


async def _wait_for_human_verification(
    *,
    page: Page,
    submission,
    playwright: Playwright,
    browser: Browser,
    context: BrowserContext,
    stage: str,
    timeout_ms: int = 30 * 60 * 1000,
) -> bool:
    """
    Mantiene la misma página y sesión Playwright abiertas mientras
    el usuario resuelve una verificación humana.

    Flujo posterior a Submit:

        1. Se detecta el CAPTCHA.
        2. El usuario resuelve el CAPTCHA.
        3. Se inspecciona si Smartsheet ya confirmó el envío.
        4. Si el formulario y el botón Submit siguen visibles,
           se vuelve a pulsar Submit dentro de la misma sesión.
        5. Se espera una confirmación real de Smartsheet.
        6. Solo entonces se marca la verificación como completada.

    El botón Continue permanece disponible como respaldo, pero
    después de Submit no puede marcar el proceso como exitoso si
    Smartsheet todavía no ha confirmado el formulario.
    """

    await _mark_verification_required(
        submission,
    )

    register_active_browser(
        submission=submission,
        playwright=playwright,
        browser=browser,
        context=context,
        page=page,
    )

    started_at = timezone.now()

    original_url = str(
        page.url or "",
    ).strip()

    try:
        original_form_count = await page.locator(
            'form[aria-label*="questions in this form" i]'
        ).count()

    except Exception:
        original_form_count = 0

    submit_button = page.get_by_role(
        "button",
        name="Submit",
        exact=True,
    )

    remote_session = None

    remote_session_final_status = RemoteBrowserSession.Status.CLOSED

    remote_session_final_message = "Human verification completed successfully."

    # El método fue llamado porque ya se detectó una verificación.
    captcha_was_visible = True

    # Permite un nuevo clic después de cada CAPTCHA resuelto,
    # evitando clics repetidos cada segundo.
    submit_reclick_count = 0
    max_submit_reclicks = 5

    async def click_submit_after_captcha() -> bool:
        """
        Busca y pulsa nuevamente Submit dentro de la misma sesión.

        No reinicia Playwright, no recarga el formulario y no
        descarta el CAPTCHA ya resuelto.
        """

        nonlocal submit_reclick_count
        nonlocal captcha_was_visible

        if stage != "after_submit":
            return False

        if submit_reclick_count >= max_submit_reclicks:
            raise SmartsheetAutomationError(
                "Smartsheet continued showing the completed form "
                f"after {max_submit_reclicks} Submit attempts following "
                "human verification."
            )

        if page.is_closed():
            raise SmartsheetAutomationError(
                "The active Smartsheet page was closed before "
                "Submit could be clicked again."
            )

        # Recuperar el scroll que puede quedar bloqueado después
        # de cerrar el diálogo del CAPTCHA.
        try:
            await page.evaluate("""
                () => {
                    document.documentElement.style.overflow = "";
                    document.body.style.overflow = "";

                    const elements = Array.from(
                        document.querySelectorAll("*")
                    );

                    for (const element of elements) {
                        const style = window.getComputedStyle(element);

                        if (
                            element.scrollHeight > element.clientHeight &&
                            (
                                style.overflowY === "auto" ||
                                style.overflowY === "scroll" ||
                                style.overflowY === "hidden"
                            )
                        ) {
                            element.style.overflowY = "auto";
                        }
                    }
                }
                """)

        except Exception:
            pass

        submit_candidates = [
            page.get_by_role(
                "button",
                name="Submit",
                exact=True,
            ),
            page.locator(
                'button:has-text("Submit")',
            ),
            page.locator(
                'button[type="submit"]',
            ),
            page.locator(
                'input[type="submit"]',
            ),
            page.locator(
                '[data-client-id*="submit" i]',
            ),
            page.locator(
                '[aria-label="Submit" i]',
            ),
        ]

        visible_submit_button = None
        discovered_candidates = []

        for locator in submit_candidates:
            try:
                count = await locator.count()

            except Exception:
                continue

            for index in range(count):
                candidate = locator.nth(index)

                try:
                    visible = await candidate.is_visible()

                    text = ""

                    try:
                        text = (
                            await candidate.inner_text(
                                timeout=1000,
                            )
                        ).strip()

                    except Exception:
                        try:
                            text = str(
                                await candidate.get_attribute(
                                    "value",
                                )
                                or ""
                            ).strip()

                        except Exception:
                            text = ""

                    discovered_candidates.append(
                        {
                            "text": text,
                            "visible": visible,
                        }
                    )

                    if not visible:
                        continue

                    try:
                        disabled = await candidate.is_disabled()

                    except Exception:
                        disabled = False

                    if disabled:
                        continue

                    visible_submit_button = candidate
                    break

                except Exception:
                    continue

            if visible_submit_button is not None:
                break

        clicked_by_javascript = False

        if visible_submit_button is None:
            clicked_by_javascript = await page.evaluate("""
                () => {
                    const candidates = Array.from(
                        document.querySelectorAll(
                            'button, input[type="submit"], [role="button"]'
                        )
                    );

                    const submit = candidates.find((element) => {
                        const text = (
                            element.innerText ||
                            element.textContent ||
                            element.value ||
                            element.getAttribute("aria-label") ||
                            ""
                        ).trim().toLowerCase();

                        const style = window.getComputedStyle(element);
                        const rect = element.getBoundingClientRect();

                        const visible =
                            style.display !== "none" &&
                            style.visibility !== "hidden" &&
                            Number(style.opacity || 1) > 0 &&
                            rect.width > 0 &&
                            rect.height > 0;

                        const disabled =
                            Boolean(element.disabled) ||
                            element.getAttribute("aria-disabled") === "true";

                        return (
                            visible &&
                            !disabled &&
                            text === "submit"
                        );
                    });

                    if (!submit) {
                        return false;
                    }

                    submit.scrollIntoView({
                        behavior: "instant",
                        block: "center",
                        inline: "center"
                    });

                    submit.click();

                    return true;
                }
                """)

            if not clicked_by_javascript:
                try:
                    button_debug = await page.evaluate("""
                        () => Array.from(
                            document.querySelectorAll(
                                'button, input[type="submit"], [role="button"]'
                            )
                        ).map((element) => ({
                            text: (
                                element.innerText ||
                                element.textContent ||
                                element.value ||
                                element.getAttribute("aria-label") ||
                                ""
                            ).trim(),
                            disabled:
                                Boolean(element.disabled) ||
                                element.getAttribute("aria-disabled") === "true",
                            display:
                                window.getComputedStyle(element).display,
                            visibility:
                                window.getComputedStyle(element).visibility
                        })).slice(0, 100)
                        """)

                except Exception:
                    button_debug = []

                raise SmartsheetFieldNotFoundError(
                    "The CAPTCHA was completed, but the Smartsheet "
                    "Submit button could not be found. "
                    f"Playwright candidates: {discovered_candidates!r}. "
                    f"DOM buttons: {button_debug!r}"
                )

        else:
            await visible_submit_button.scroll_into_view_if_needed()

            await page.wait_for_timeout(
                500,
            )

            try:
                await visible_submit_button.click(
                    timeout=15_000,
                )

            except Exception:
                await visible_submit_button.evaluate("""
                    element => {
                        element.scrollIntoView({
                            behavior: "instant",
                            block: "center",
                            inline: "center"
                        });

                        element.click();
                    }
                    """)

        clicked_at = await _mark_submit_clicked(
            submission,
        )

        submit_reclick_count += 1

        # Se consume el CAPTCHA que ya fue resuelto. Si después
        # del nuevo clic aparece otro, el loop volverá a colocar
        # esta bandera en True.
        captcha_was_visible = False

        await _update_human_verification_state(
            submission,
            worker_checked_at=clicked_at.isoformat(),
            captcha_cleared=True,
            submit_reclicked_after_captcha=True,
            submit_reclicked_at=clicked_at.isoformat(),
            submit_reclick_count=submit_reclick_count,
            session_available=True,
            session_url=page.url,
            message=(
                "The CAPTCHA was completed. Submit was clicked "
                "again in the same browser session. The worker "
                "is waiting for Smartsheet confirmation."
            ),
        )

        print(
            "SMARTSHEET SUBMIT RECLICKED AFTER CAPTCHA:",
            {
                "submission_id": submission.pk,
                "project_id": submission.project_id,
                "stage": stage,
                "clicked_at": clicked_at.isoformat(),
                "submit_reclick_count": submit_reclick_count,
                "clicked_by_javascript": clicked_by_javascript,
                "url": page.url,
            },
        )

        await page.wait_for_timeout(
            2500,
        )

        return True

    async def finalize_confirmed_submission(
        inspection: dict,
        *,
        source: str,
    ) -> bool:
        """
        Finaliza la verificación únicamente después de detectar
        confirmación real del envío en Smartsheet.
        """

        nonlocal remote_session_final_status
        nonlocal remote_session_final_message

        completed_at = timezone.now()

        confirmation_reference = str(
            inspection.get(
                "confirmation_reference",
                "",
            )
            or ""
        ).strip()

        await _update_human_verification_state(
            submission,
            worker_checked_at=completed_at.isoformat(),
            captcha_cleared=True,
            continue_requested_at=None,
            continue_requested_by=None,
            continue_requested_username="",
            session_available=False,
            session_url=page.url,
            automatic_confirmation=True,
            automatic_confirmation_source=source,
            automatic_confirmation_reference=confirmation_reference,
            automatic_confirmation_at=completed_at.isoformat(),
            message=(
                "Smartsheet confirmation was detected after " "human verification."
            ),
        )

        await _mark_verification_completed(
            submission,
        )

        remote_session_final_status = RemoteBrowserSession.Status.CLOSED

        remote_session_final_message = (
            "Smartsheet confirmation was detected after " "human verification."
        )

        print(
            "AUTOMATIC SMARTSHEET CONFIRMATION:",
            {
                "submission_id": submission.pk,
                "project_id": submission.project_id,
                "stage": stage,
                "remote_session_id": (
                    remote_session.pk if remote_session is not None else None
                ),
                "source": source,
                "url": page.url,
                "confirmation_reference": confirmation_reference,
            },
        )

        return True

    async def inspect_and_continue_after_submit(
        *,
        source: str,
    ) -> str:
        """
        Inspecciona el resultado actual después del CAPTCHA.

        Retorna:

            confirmed
            challenge_visible
            submit_reclicked
            waiting
        """

        nonlocal captcha_was_visible

        inspection = await _inspect_smartsheet_submission_result(
            page,
            original_url=original_url,
            original_form_count=original_form_count,
            submit_button=submit_button,
        )

        inspection_error = str(
            inspection.get(
                "error",
                "",
            )
            or ""
        ).strip()

        if inspection_error:
            raise SmartsheetAutomationError(
                inspection_error,
            )

        if inspection.get(
            "confirmed",
            False,
        ):
            await finalize_confirmed_submission(
                inspection,
                source=source,
            )

            return "confirmed"

        verification_required = bool(
            inspection.get(
                "verification_required",
                False,
            )
        )

        if verification_required:
            captcha_was_visible = True

            return "challenge_visible"

        try:
            form_count = int(
                inspection.get(
                    "form_count",
                    0,
                )
                or 0
            )

        except (
            TypeError,
            ValueError,
        ):
            form_count = 0

        submit_button_visible = bool(
            inspection.get(
                "submit_button_visible",
                False,
            )
        )

        # El CAPTCHA desapareció, pero el formulario continúa
        # abierto. El primer clic fue interceptado, por lo que
        # debemos pulsar Submit nuevamente.
        if captcha_was_visible and form_count > 0 and submit_button_visible:
            await click_submit_after_captcha()

            return "submit_reclicked"

        return "waiting"

    try:
        # ====================================================
        # Crear o recuperar sesión remota
        # ====================================================

        viewport_size = page.viewport_size or {
            "width": 1440,
            "height": 1100,
        }

        remote_session = await get_or_create_remote_session(
            submission=submission,
            attempt=None,
            stage=stage,
            browser_session_key=str(
                submission.public_id,
            ),
            viewport_width=int(
                viewport_size.get(
                    "width",
                    1440,
                )
                or 1440
            ),
            viewport_height=int(
                viewport_size.get(
                    "height",
                    1100,
                )
                or 1100
            ),
            metadata={
                "source": "smartsheet_state",
                "submission_id": submission.pk,
                "submission_public_id": str(
                    submission.public_id,
                ),
                "project_id": str(
                    submission.project_id or "",
                ),
                "stage": stage,
                "started_at": started_at.isoformat(),
            },
        )

        remote_console_url = reverse(
            "client_submission_remote:console",
            kwargs={
                "public_id": remote_session.public_id,
            },
        )

        await _update_human_verification_state(
            submission,
            stage=stage,
            requested_at=started_at.isoformat(),
            continue_requested_at=None,
            continue_requested_by=None,
            continue_requested_username="",
            cancel_requested_at=None,
            cancel_requested_by=None,
            cancel_requested_username="",
            worker_checked_at=None,
            captcha_cleared=False,
            session_available=True,
            session_url=page.url,
            retry_challenge_requested_at=None,
            retry_challenge_requested_by=None,
            retry_challenge_requested_username="",
            retry_challenge_processed_at=None,
            retry_challenge_clicked_at=None,
            retry_challenge_error="",
            submit_reclicked_after_captcha=False,
            submit_reclicked_at=None,
            submit_reclick_count=0,
            remote_session_id=remote_session.pk,
            remote_session_public_id=str(
                remote_session.public_id,
            ),
            remote_console_url=remote_console_url,
            message=(
                "The browser is waiting for human verification. "
                "Complete the CAPTCHA. If the form remains visible, "
                "Submit will be clicked again automatically in the "
                "same browser session."
            ),
        )

        print(
            "WAITING FOR HUMAN VERIFICATION:",
            {
                "submission_id": submission.pk,
                "project_id": submission.project_id,
                "stage": stage,
                "url": page.url,
                "remote_session_id": remote_session.pk,
                "remote_session_public_id": str(
                    remote_session.public_id,
                ),
                "remote_console_url": remote_console_url,
            },
        )

        # ====================================================
        # Captura inicial
        # ====================================================

        try:
            challenge_visible = await _detect_verification_challenge(
                page,
            )

            if challenge_visible:
                captcha_was_visible = True

            remote_session = await capture_remote_screenshot(
                page=page,
                session=remote_session,
                captcha_visible=challenge_visible,
                message=(
                    "The CAPTCHA is visible and waiting for "
                    "interaction from the Remote Browser Console."
                    if challenge_visible
                    else (
                        "The worker is waiting for human "
                        "verification. Review the active browser."
                    )
                ),
            )

        except Exception as exc:
            logger.exception(
                "Could not capture initial remote browser "
                "screenshot. submission=%s session=%s",
                submission.pk,
                remote_session.pk,
            )

            await _update_human_verification_state(
                submission,
                worker_checked_at=timezone.now().isoformat(),
                message=(
                    "The remote session was created, but its "
                    f"initial screenshot failed: {exc}"
                ),
            )

        # ====================================================
        # Espera y procesamiento
        # ====================================================

        elapsed_ms = 0
        poll_interval_ms = 1000

        while elapsed_ms < timeout_ms:
            await page.wait_for_timeout(
                poll_interval_ms,
            )

            elapsed_ms += poll_interval_ms

            if page.is_closed():
                remote_session_final_status = RemoteBrowserSession.Status.FAILED

                remote_session_final_message = (
                    "The Playwright page was closed while "
                    "waiting for human verification."
                )

                raise SmartsheetVerificationRequired(remote_session_final_message)

            # =================================================
            # Detección automática posterior a Submit
            # =================================================

            if stage == "after_submit":
                automatic_result = await inspect_and_continue_after_submit(
                    source="automatic_poll",
                )

                if automatic_result == "confirmed":
                    return True

                if automatic_result == "submit_reclicked":
                    continue

            # =================================================
            # Procesar acción de la consola remota
            # =================================================

            remote_action_result = await process_next_remote_action(
                page=page,
                session=remote_session,
            )

            if remote_action_result.get(
                "processed",
                False,
            ):
                current_remote_session = await get_remote_session_by_id(
                    remote_session.pk,
                )

                if current_remote_session is not None:
                    remote_session = current_remote_session

                action_error = str(
                    remote_action_result.get(
                        "error",
                        "",
                    )
                    or ""
                ).strip()

                if action_error:
                    await _update_human_verification_state(
                        submission,
                        worker_checked_at=timezone.now().isoformat(),
                        session_available=True,
                        session_url=page.url,
                        message=(
                            "The remote browser action failed: " f"{action_error}"
                        ),
                    )

                # ---------------------------------------------
                # Cancelar
                # ---------------------------------------------

                if remote_action_result.get(
                    "cancel_requested",
                    False,
                ):
                    cancelled_at = timezone.now()

                    remote_session_final_status = RemoteBrowserSession.Status.CANCELLED

                    remote_session_final_message = (
                        "Human verification was cancelled from "
                        "the Remote Browser Console."
                    )

                    await _update_human_verification_state(
                        submission,
                        cancel_requested_at=cancelled_at.isoformat(),
                        worker_checked_at=cancelled_at.isoformat(),
                        session_available=False,
                        session_url="",
                        message=remote_session_final_message,
                    )

                    raise SmartsheetVerificationRequired(remote_session_final_message)

                # ---------------------------------------------
                # Reiniciar
                # ---------------------------------------------

                if remote_action_result.get(
                    "restart_requested",
                    False,
                ):
                    restarted_at = timezone.now()

                    remote_session_final_status = (
                        RemoteBrowserSession.Status.RESTART_REQUESTED
                    )

                    remote_session_final_message = (
                        "A restart was requested from the Remote "
                        "Browser Console. This Submission will "
                        "restart in a new browser session."
                    )

                    await _update_human_verification_state(
                        submission,
                        retry_challenge_requested_at=(restarted_at.isoformat()),
                        retry_challenge_processed_at=(restarted_at.isoformat()),
                        retry_challenge_clicked_at=None,
                        retry_challenge_error="",
                        worker_checked_at=restarted_at.isoformat(),
                        session_available=False,
                        session_url="",
                        captcha_cleared=False,
                        message=remote_session_final_message,
                    )

                    print(
                        "REMOTE CAPTCHA RESTART REQUESTED:",
                        {
                            "submission_id": submission.pk,
                            "project_id": submission.project_id,
                            "stage": stage,
                            "remote_session_id": remote_session.pk,
                            "url": page.url,
                        },
                    )

                    raise SmartsheetRestartSubmissionRequested(
                        remote_session_final_message
                    )

                # ---------------------------------------------
                # Continue manual
                # ---------------------------------------------

                if remote_action_result.get(
                    "continue_requested",
                    False,
                ):
                    worker_checked_at = timezone.now()

                    if stage == "after_submit":
                        continue_result = await inspect_and_continue_after_submit(
                            source="remote_continue",
                        )

                        if continue_result == "confirmed":
                            return True

                        if continue_result == "challenge_visible":
                            message = (
                                "The CAPTCHA is still visible. "
                                "Complete it before pressing "
                                "Continue again."
                            )

                        elif continue_result == "submit_reclicked":
                            message = (
                                "The CAPTCHA was completed and "
                                "Submit was clicked again. The "
                                "worker is waiting for Smartsheet "
                                "confirmation."
                            )

                        else:
                            message = (
                                "The CAPTCHA is no longer visible, "
                                "but Smartsheet has not confirmed "
                                "the submission yet. The worker "
                                "will continue waiting."
                            )

                        await _update_human_verification_state(
                            submission,
                            worker_checked_at=(worker_checked_at.isoformat()),
                            continue_requested_at=None,
                            continue_requested_by=None,
                            continue_requested_username="",
                            session_available=True,
                            session_url=page.url,
                            message=message,
                        )

                        continue

                    challenge_visible = await _detect_verification_challenge(
                        page,
                    )

                    if challenge_visible:
                        captcha_was_visible = True

                        await _update_human_verification_state(
                            submission,
                            worker_checked_at=(worker_checked_at.isoformat()),
                            captcha_cleared=False,
                            continue_requested_at=None,
                            continue_requested_by=None,
                            continue_requested_username="",
                            session_available=True,
                            session_url=page.url,
                            message=(
                                "The CAPTCHA is still visible. "
                                "Complete it before pressing "
                                "Continue again."
                            ),
                        )

                        continue

                    # Solo para verificaciones anteriores a Submit.
                    await _mark_verification_completed(
                        submission,
                    )

                    remote_session_final_status = RemoteBrowserSession.Status.CLOSED

                    remote_session_final_message = (
                        "Human verification was completed. "
                        "The Smartsheet automation resumed."
                    )

                    print(
                        "REMOTE HUMAN VERIFICATION COMPLETED:",
                        {
                            "submission_id": submission.pk,
                            "stage": stage,
                            "remote_session_id": (remote_session.pk),
                            "url": page.url,
                        },
                    )

                    return True

                # ---------------------------------------------
                # Actualizar captura después de una acción
                # ---------------------------------------------

                try:
                    challenge_visible = await _detect_verification_challenge(
                        page,
                    )

                    if challenge_visible:
                        captcha_was_visible = True

                    current_remote_session = await get_remote_session_by_id(
                        remote_session.pk,
                    )

                    if current_remote_session is not None:
                        remote_session = current_remote_session

                    remote_session = await capture_remote_screenshot(
                        page=page,
                        session=remote_session,
                        captcha_visible=challenge_visible,
                        message=(
                            "Browser action completed. "
                            "Review the updated screenshot."
                        ),
                    )

                    await _update_human_verification_state(
                        submission,
                        worker_checked_at=timezone.now().isoformat(),
                        captcha_cleared=not challenge_visible,
                        session_available=True,
                        session_url=page.url,
                        message=(
                            "Browser action completed. "
                            "The remote screenshot was updated."
                        ),
                    )

                except Exception as exc:
                    logger.exception(
                        "Could not refresh remote screenshot "
                        "after browser action. "
                        "submission=%s session=%s",
                        submission.pk,
                        remote_session.pk,
                    )

                    await _update_human_verification_state(
                        submission,
                        worker_checked_at=timezone.now().isoformat(),
                        session_available=True,
                        session_url=page.url,
                        message=(
                            "The browser action was processed, "
                            "but the updated screenshot failed: "
                            f"{exc}"
                        ),
                    )

            # =================================================
            # Leer controles anteriores de browser_state
            # =================================================

            state_data = await _get_human_verification_state(
                submission,
            )

            verification_state = state_data.get(
                "verification_state",
                {},
            )

            submission_status = state_data.get(
                "submission_status",
                "",
            )

            # =================================================
            # Cancelación mediante interfaz anterior
            # =================================================

            cancel_requested_at = verification_state.get(
                "cancel_requested_at",
            )

            if cancel_requested_at or submission_status == "cancelled":
                remote_session_final_status = RemoteBrowserSession.Status.CANCELLED

                remote_session_final_message = "Human verification was cancelled."

                raise SmartsheetVerificationRequired(remote_session_final_message)

            # =================================================
            # Reinicio mediante interfaz anterior
            # =================================================

            retry_requested_at = verification_state.get(
                "retry_challenge_requested_at",
            )

            retry_processed_at = verification_state.get(
                "retry_challenge_processed_at",
            )

            if retry_requested_at and not retry_processed_at:
                restart_requested_at = timezone.now()

                try:
                    retry_count = int(
                        verification_state.get(
                            "retry_challenge_count",
                            0,
                        )
                        or 0
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    retry_count = 0

                retry_count += 1

                remote_session_final_status = (
                    RemoteBrowserSession.Status.RESTART_REQUESTED
                )

                remote_session_final_message = (
                    "The current CAPTCHA session expired or "
                    "was closed. This project will restart "
                    "from the beginning in a new browser session."
                )

                await _update_human_verification_state(
                    submission,
                    retry_challenge_processed_at=(restart_requested_at.isoformat()),
                    retry_challenge_clicked_at=None,
                    retry_challenge_error="",
                    retry_challenge_count=retry_count,
                    worker_checked_at=(restart_requested_at.isoformat()),
                    session_available=False,
                    session_url="",
                    captcha_cleared=False,
                    message=remote_session_final_message,
                )

                print(
                    "CAPTCHA RESTART REQUESTED:",
                    {
                        "submission_id": submission.pk,
                        "project_id": submission.project_id,
                        "stage": stage,
                        "retry_count": retry_count,
                        "url": page.url,
                    },
                )

                raise SmartsheetRestartSubmissionRequested(remote_session_final_message)

            # =================================================
            # Continue mediante interfaz anterior
            # =================================================

            continue_requested_at = verification_state.get(
                "continue_requested_at",
            )

            if not continue_requested_at:
                continue

            worker_checked_at = timezone.now()

            if stage == "after_submit":
                continue_result = await inspect_and_continue_after_submit(
                    source="legacy_continue",
                )

                if continue_result == "confirmed":
                    return True

                if continue_result == "challenge_visible":
                    message = (
                        "The CAPTCHA is still visible. Complete "
                        "it before pressing verify and continue."
                    )

                elif continue_result == "submit_reclicked":
                    message = (
                        "The CAPTCHA was completed and Submit "
                        "was clicked again. The worker is waiting "
                        "for Smartsheet confirmation."
                    )

                else:
                    message = (
                        "Smartsheet has not confirmed the "
                        "submission yet. The worker will continue "
                        "waiting in the active browser session."
                    )

                await _update_human_verification_state(
                    submission,
                    worker_checked_at=worker_checked_at.isoformat(),
                    continue_requested_at=None,
                    continue_requested_by=None,
                    continue_requested_username="",
                    session_available=True,
                    session_url=page.url,
                    message=message,
                )

                continue

            challenge_visible = await _detect_verification_challenge(
                page,
            )

            if challenge_visible:
                captcha_was_visible = True

                await _update_human_verification_state(
                    submission,
                    worker_checked_at=worker_checked_at.isoformat(),
                    captcha_cleared=False,
                    continue_requested_at=None,
                    continue_requested_by=None,
                    continue_requested_username="",
                    session_available=True,
                    session_url=page.url,
                    message=(
                        "The CAPTCHA is still visible. Complete "
                        "it before pressing verify and continue."
                    ),
                )

                continue

            # Solo para verificaciones anteriores a Submit.
            await _mark_verification_completed(
                submission,
            )

            remote_session_final_status = RemoteBrowserSession.Status.CLOSED

            remote_session_final_message = (
                "Human verification was completed. " "Automation resumed successfully."
            )

            print(
                "HUMAN VERIFICATION COMPLETED:",
                {
                    "submission_id": submission.pk,
                    "stage": stage,
                    "url": page.url,
                },
            )

            return True

        # ====================================================
        # Tiempo agotado
        # ====================================================

        expired_at = timezone.now()

        remote_session_final_status = RemoteBrowserSession.Status.EXPIRED

        remote_session_final_message = (
            "The human verification session expired before " "it was completed."
        )

        await _update_human_verification_state(
            submission,
            worker_checked_at=expired_at.isoformat(),
            session_available=False,
            session_url="",
            message=remote_session_final_message,
        )

        raise SmartsheetVerificationRequired(
            "Human verification timed out after 30 minutes."
        )

    except SmartsheetRestartSubmissionRequested:
        raise

    except SmartsheetVerificationRequired:
        raise

    except Exception as exc:
        remote_session_final_status = RemoteBrowserSession.Status.FAILED

        remote_session_final_message = (
            "The remote human verification process failed: " f"{exc}"
        )

        logger.exception(
            "Remote human verification failed. " "submission=%s stage=%s",
            submission.pk,
            stage,
        )

        raise

    finally:
        remove_active_browser(
            submission,
        )

        if remote_session is not None:
            try:
                await close_remote_session(
                    session_id=remote_session.pk,
                    status=remote_session_final_status,
                    message=remote_session_final_message,
                )

            except Exception:
                logger.exception(
                    "Could not close remote browser session. "
                    "submission=%s session=%s",
                    submission.pk,
                    remote_session.pk,
                )


# ============================================================
# Detección de challenge
# ============================================================


async def _detect_verification_challenge(
    page: Page,
) -> bool:
    """
    Detecta solamente challenges visibles y bloqueantes.

    Smartsheet puede cargar iframes de reCAPTCHA en segundo
    plano aunque no exista una verificación activa.

    Por eso NO basta con detectar la existencia de un iframe.
    El challenge se considera real únicamente cuando:

    - Existe texto visible de verificación.
    - Existe un diálogo visible de CAPTCHA.
    - Existe un iframe visible con dimensiones suficientes.
    - Existe un checkbox visible de reCAPTCHA/Turnstile.
    """

    # ========================================================
    # 1. Textos visibles que indican verificación real
    # ========================================================

    visible_text_markers = [
        "verify you are human",
        "verification required",
        "i am not a robot",
        "i'm not a robot",
        "complete the captcha",
        "complete the verification",
        "security verification",
        "checking your browser",
        "confirm you are human",
        "prove you are human",
    ]

    try:
        body_text = await page.locator(
            "body",
        ).inner_text(
            timeout=5000,
        )

    except Exception:
        body_text = ""

    normalized_body_text = body_text.lower()

    for marker in visible_text_markers:
        if marker in normalized_body_text:
            print(
                "VISIBLE VERIFICATION TEXT DETECTED:",
                {
                    "marker": marker,
                    "url": page.url,
                },
            )

            return True

    # ========================================================
    # 2. Contenedores visibles asociados con CAPTCHA
    # ========================================================

    visible_container_selectors = [
        '[role="dialog"][class*="captcha" i]',
        '[role="dialog"][id*="captcha" i]',
        '[class*="captcha-container" i]',
        '[class*="captcha-challenge" i]',
        '[id*="captcha-container" i]',
        '[id*="captcha-challenge" i]',
        '[class*="turnstile" i]',
        '[id*="turnstile" i]',
        ".g-recaptcha",
    ]

    for selector in visible_container_selectors:
        try:
            locator = page.locator(
                selector,
            )

            count = await locator.count()

            for index in range(
                count,
            ):
                candidate = locator.nth(
                    index,
                )

                try:
                    if not await candidate.is_visible():
                        continue

                    box = await candidate.bounding_box()

                    if not box:
                        continue

                    if box["width"] < 100 or box["height"] < 40:
                        continue

                    print(
                        "VISIBLE VERIFICATION CONTAINER DETECTED:",
                        {
                            "selector": selector,
                            "index": index,
                            "box": box,
                            "url": page.url,
                        },
                    )

                    return True

                except Exception:
                    continue

        except Exception:
            continue

    # ========================================================
    # 3. Iframes de CAPTCHA
    #
    # Solo se consideran challenge si están realmente visibles
    # y tienen dimensiones propias de una interfaz interactiva.
    #
    # Un iframe invisible, diminuto o fuera de pantalla no debe
    # detener la automatización.
    # ========================================================

    iframe_selectors = [
        'iframe[src*="recaptcha" i]',
        'iframe[src*="turnstile" i]',
        'iframe[src*="captcha" i]',
        'iframe[title*="recaptcha" i]',
        'iframe[title*="challenge" i]',
        'iframe[title*="captcha" i]',
    ]

    for selector in iframe_selectors:
        try:
            iframes = page.locator(
                selector,
            )

            iframe_count = await iframes.count()

            for index in range(
                iframe_count,
            ):
                iframe = iframes.nth(
                    index,
                )

                try:
                    if not await iframe.is_visible():
                        continue

                    box = await iframe.bounding_box()

                    if not box:
                        continue

                    width = float(
                        box.get(
                            "width",
                            0,
                        )
                        or 0
                    )

                    height = float(
                        box.get(
                            "height",
                            0,
                        )
                        or 0
                    )

                    # ----------------------------------------
                    # Ignorar iframe técnico/invisible.
                    # ----------------------------------------

                    if width < 150 or height < 60:
                        print(
                            "BACKGROUND CAPTCHA IFRAME IGNORED:",
                            {
                                "selector": selector,
                                "index": index,
                                "width": width,
                                "height": height,
                                "url": page.url,
                            },
                        )

                        continue

                    viewport = page.viewport_size or {
                        "width": 0,
                        "height": 0,
                    }

                    iframe_x = float(
                        box.get(
                            "x",
                            0,
                        )
                        or 0
                    )

                    iframe_y = float(
                        box.get(
                            "y",
                            0,
                        )
                        or 0
                    )

                    viewport_width = float(
                        viewport.get(
                            "width",
                            0,
                        )
                        or 0
                    )

                    viewport_height = float(
                        viewport.get(
                            "height",
                            0,
                        )
                        or 0
                    )

                    completely_outside_viewport = (
                        iframe_x + width < 0
                        or iframe_y + height < 0
                        or (viewport_width > 0 and iframe_x > viewport_width)
                        or (viewport_height > 0 and iframe_y > viewport_height)
                    )

                    if completely_outside_viewport:
                        print(
                            "OFFSCREEN CAPTCHA IFRAME IGNORED:",
                            {
                                "selector": selector,
                                "index": index,
                                "box": box,
                                "viewport": viewport,
                                "url": page.url,
                            },
                        )

                        continue

                    print(
                        "VISIBLE CAPTCHA IFRAME DETECTED:",
                        {
                            "selector": selector,
                            "index": index,
                            "box": box,
                            "viewport": viewport,
                            "url": page.url,
                        },
                    )

                    return True

                except Exception:
                    continue

        except Exception:
            continue

    # ========================================================
    # 4. Botones o checkboxes visibles de verificación
    # ========================================================

    verification_controls = [
        page.get_by_role(
            "checkbox",
            name="I'm not a robot",
            exact=False,
        ),
        page.get_by_role(
            "checkbox",
            name="I am not a robot",
            exact=False,
        ),
        page.get_by_role(
            "button",
            name="Verify",
            exact=False,
        ),
        page.get_by_role(
            "button",
            name="Continue",
            exact=False,
        ),
    ]

    for locator in verification_controls:
        try:
            count = await locator.count()

            for index in range(
                count,
            ):
                candidate = locator.nth(
                    index,
                )

                try:
                    if not await candidate.is_visible():
                        continue

                    box = await candidate.bounding_box()

                    if not box:
                        continue

                    print(
                        "VISIBLE VERIFICATION CONTROL DETECTED:",
                        {
                            "index": index,
                            "box": box,
                            "url": page.url,
                        },
                    )

                    return True

                except Exception:
                    continue

        except Exception:
            continue

    print(
        "NO BLOCKING VERIFICATION CHALLENGE DETECTED:",
        {
            "url": page.url,
        },
    )

    return False


async def _inspect_smartsheet_submission_result(
    page: Page,
    *,
    original_url: str = "",
    original_form_count: int | None = None,
    submit_button: Locator | None = None,
) -> dict[str, Any]:
    """
    Inspecciona el estado actual de Smartsheet después de Submit.

    Detecta:

    - Confirmación explícita mediante texto.
    - Confirmación mediante URL con confirm=true.
    - Desaparición del formulario y del botón Submit.
    - Errores visibles.
    - CAPTCHA todavía activo.

    Esta función no modifica la base de datos.
    """

    if page.is_closed():
        return {
            "confirmed": False,
            "verification_required": False,
            "page_closed": True,
            "error": (
                "The Smartsheet page was closed before "
                "the submission could be confirmed."
            ),
            "confirmation_reference": "",
            "confirmation_text": "",
            "final_url": "",
            "form_count": 0,
            "submit_button_visible": False,
        }

    current_url = str(
        page.url or "",
    ).strip()

    final_body_text = ""

    try:
        final_body_text = await page.locator(
            "body",
        ).inner_text(
            timeout=5000,
        )

    except Exception:
        final_body_text = ""

    normalized_body = " ".join(
        str(
            final_body_text or "",
        )
        .lower()
        .split()
    )

    normalized_url = current_url.lower()

    # ========================================================
    # 1. Errores reales mostrados por Smartsheet
    # ========================================================

    error_markers = [
        "please complete all required fields",
        "please fill out this field",
        "there was an error submitting",
        "could not submit",
        "submission failed",
        "please try again",
        "exceeds the max file size",
        "file is too large",
    ]

    detected_error = next(
        (marker for marker in error_markers if marker in normalized_body),
        "",
    )

    if detected_error:
        return {
            "confirmed": False,
            "verification_required": False,
            "page_closed": False,
            "error": (
                "Smartsheet showed an error after Submit. "
                f"Detected message: {detected_error!r}. "
                f"Visible body: {final_body_text[:5000]!r}"
            ),
            "confirmation_reference": "",
            "confirmation_text": final_body_text[:5000],
            "final_url": current_url,
            "form_count": None,
            "submit_button_visible": None,
        }

    # ========================================================
    # 2. Confirmación explícita por texto
    # ========================================================

    success_markers = [
        "we've captured your response",
        "we have captured your response",
        "your response has been captured",
        "your response has been recorded",
        "your response was submitted",
        "your response has been submitted",
        "response submitted",
        "submission received",
        "successfully submitted",
        "form submitted",
    ]

    detected_success = next(
        (marker for marker in success_markers if marker in normalized_body),
        "",
    )

    if detected_success:
        return {
            "confirmed": True,
            "verification_required": False,
            "page_closed": False,
            "error": "",
            "confirmation_reference": detected_success,
            "confirmation_text": final_body_text[:5000],
            "final_url": current_url,
            "form_count": None,
            "submit_button_visible": False,
        }

    # ========================================================
    # 3. Confirmación explícita mediante URL
    # ========================================================

    url_confirmation_markers = [
        "confirm=true",
        "confirmation=true",
        "submitted=true",
        "success=true",
    ]

    detected_url_confirmation = next(
        (marker for marker in url_confirmation_markers if marker in normalized_url),
        "",
    )

    if detected_url_confirmation:
        return {
            "confirmed": True,
            "verification_required": False,
            "page_closed": False,
            "error": "",
            "confirmation_reference": (f"url:{detected_url_confirmation}"),
            "confirmation_text": final_body_text[:5000],
            "final_url": current_url,
            "form_count": None,
            "submit_button_visible": False,
        }

    # ========================================================
    # 4. Comprobar si el CAPTCHA sigue visible
    # ========================================================

    challenge_visible = await _detect_verification_challenge(
        page,
    )

    if challenge_visible:
        return {
            "confirmed": False,
            "verification_required": True,
            "page_closed": False,
            "error": "",
            "confirmation_reference": "",
            "confirmation_text": final_body_text[:5000],
            "final_url": current_url,
            "form_count": None,
            "submit_button_visible": None,
        }

    # ========================================================
    # 5. Revisar formulario y botón Submit
    # ========================================================

    try:
        final_form_count = await page.locator(
            'form[aria-label*="questions in this form" i]'
        ).count()

    except Exception:
        final_form_count = 0

    submit_button_visible = False

    if submit_button is not None:
        try:
            submit_button_visible = await submit_button.is_visible()

        except Exception:
            submit_button_visible = False

    else:
        try:
            submit_button_visible = await page.get_by_role(
                "button",
                name="Submit",
                exact=True,
            ).is_visible()

        except Exception:
            submit_button_visible = False

    # ========================================================
    # 6. El formulario desapareció después de Submit
    # ========================================================

    if (
        original_form_count is not None
        and original_form_count > 0
        and final_form_count == 0
        and not submit_button_visible
    ):
        return {
            "confirmed": True,
            "verification_required": False,
            "page_closed": False,
            "error": "",
            "confirmation_reference": ("form_disappeared_after_submit"),
            "confirmation_text": final_body_text[:5000],
            "final_url": current_url,
            "form_count": final_form_count,
            "submit_button_visible": (submit_button_visible),
        }

    # ========================================================
    # 7. La URL cambió y el formulario ya no está disponible
    # ========================================================

    if (
        original_url
        and current_url != original_url
        and not submit_button_visible
        and final_form_count == 0
    ):
        return {
            "confirmed": True,
            "verification_required": False,
            "page_closed": False,
            "error": "",
            "confirmation_reference": ("url_changed_after_submit"),
            "confirmation_text": final_body_text[:5000],
            "final_url": current_url,
            "form_count": final_form_count,
            "submit_button_visible": (submit_button_visible),
        }

    return {
        "confirmed": False,
        "verification_required": False,
        "page_closed": False,
        "error": "",
        "confirmation_reference": "",
        "confirmation_text": final_body_text[:5000],
        "final_url": current_url,
        "form_count": final_form_count,
        "submit_button_visible": submit_button_visible,
    }
