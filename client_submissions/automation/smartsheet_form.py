# client_submissions/automation/smartsheet_form.py

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from asgiref.sync import sync_to_async
from django.utils import timezone
from playwright.async_api import (Browser, BrowserContext, Locator, Page,
                                  Playwright, async_playwright)

logger = logging.getLogger(__name__)


# ============================================================
# Excepciones
# ============================================================


class SmartsheetAutomationError(Exception):
    """Error base de automatización del formulario."""


class SmartsheetFormLoadError(SmartsheetAutomationError):
    """El formulario no pudo cargarse correctamente."""


class SmartsheetFieldNotFoundError(SmartsheetAutomationError):
    """No se pudo encontrar un campo obligatorio."""


class SmartsheetAttachmentError(SmartsheetAutomationError):
    """No se pudo adjuntar el archivo esperado."""


class SmartsheetVerificationRequired(SmartsheetAutomationError):
    """
    El formulario requiere intervención humana.

    Ejemplo:
    - CAPTCHA
    - Turnstile
    - reCAPTCHA
    - challenge inesperado
    """


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


async def run_smartsheet_live(
    *,
    submission,
    attachment_paths: list[str] | None = None,
    headless: bool = True,
) -> SmartsheetDryRunResult:
    """
    Ejecuta el formulario Smartsheet en modo LIVE.

    Completa los campos, adjunta los ZIP, presiona Submit
    y espera una confirmación real del navegador.
    """

    return await run_smartsheet_dry_run(
        submission=submission,
        attachment_paths=attachment_paths,
        headless=headless,
        submit_form=True,
    )

# ============================================================
# Helpers Django ORM para contexto async
# ============================================================


@sync_to_async(thread_sensitive=True)
def _load_batch_for_submission(
    submission,
):
    """
    Carga explícitamente el Batch asociado al Submission
    fuera del contexto ORM async.
    """

    return submission.batch


@sync_to_async(thread_sensitive=True)
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


@sync_to_async(thread_sensitive=True)
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
            "attachments_uploaded": attachments_uploaded,
            "attachment_filenames": attachment_filenames,
            "attachment_count": len(
                attachment_filenames,
            ),
            "submit_clicked": submit_clicked,
            "browser_confirmation_received": (browser_confirmation_received),
            "confirmation_reference": confirmation_reference,
            "confirmation_text": confirmation_text,
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


@sync_to_async(thread_sensitive=True)
def _mark_submit_clicked(
    submission,
):
    """
    Registra el momento exacto en que Playwright presiona
    el botón Submit del formulario Smartsheet.
    """

    now = timezone.now()

    submission.submit_clicked_at = now

    submission.save(
        update_fields=[
            "submit_clicked_at",
            "updated_at",
        ]
    )

    return now


# ============================================================
# Helpers generales
# ============================================================


def _clean(
    value: Any,
) -> str:

    if value is None:

        return ""

    return str(value).strip()


def _normalize_access_point_id(
    value: Any,
) -> str:
    """

    Normaliza Access Point ID para Smartsheet.

    Smartsheet acepta únicamente dígitos.

    Ejemplos:

        1000-019   -> 1000019

        5005-009-7 -> 50050097

        1000_019   -> 1000019

    """

    text = _clean(value)

    return "".join(character for character in text if character.isdigit())


def _work_types_from_batch(
    batch,
) -> list[str]:
    work_types = []

    if batch.fiber_placed:
        work_types.append("Fiber Placed")

    if batch.splicing:
        work_types.append("Splicing")

    if batch.testing:
        work_types.append("Testing")

    if batch.aerial_case:
        work_types.append("Aerial Case")

    if batch.re_entry:
        work_types.append("Re-Entry")

    return work_types


async def _first_visible(
    locator: Locator,
):
    """
    Devuelve el primer elemento visible de un Locator.
    """

    try:
        count = await locator.count()

    except Exception:
        return None

    for index in range(count):
        candidate = locator.nth(index)

        try:
            if await candidate.is_visible():
                return candidate

        except Exception:
            continue

    return None


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

            for index in range(count):
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

            for index in range(iframe_count):
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
                        or (
                            viewport_width > 0
                            and iframe_x > viewport_width
                        )
                        or (
                            viewport_height > 0
                            and iframe_y > viewport_height
                        )
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

            for index in range(count):
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

# ============================================================
# Esperar campos progresivos
# ============================================================


async def _wait_for_question(
    page: Page,
    labels: list[str],
    *,
    timeout: int = 15_000,
    required: bool = True,
) -> bool:
    """
    Espera hasta que una pregunta del formulario sea visible.

    Se utiliza porque Smartsheet va mostrando nuevas preguntas
    después de completar cada respuesta.
    """

    for label_text in labels:
        try:
            locator = page.get_by_text(
                label_text,
                exact=False,
            )

            await locator.first.wait_for(
                state="visible",
                timeout=timeout,
            )

            return True

        except Exception:
            continue

    if required:
        raise SmartsheetFieldNotFoundError(f"Question did not appear: {labels[0]}")

    return False


async def _wait_after_change(
    page: Page,
    milliseconds: int = 700,
):
    """
    Pequeña espera después de modificar un campo dinámico.
    """

    await page.wait_for_timeout(milliseconds)


# ============================================================
# Localización de controles
# ============================================================


async def _find_input_by_label(
    page: Page,
    labels: list[str],
):
    """
    Busca el control correspondiente a una pregunta concreta.

    IMPORTANTE:
    No sube indiscriminadamente por todo el formulario porque
    eso puede provocar que, por ejemplo, Production Completed Date
    termine usando el input de Submitted by.
    """

    for raw_label in labels:
        label_text = _clean(raw_label)

        if not label_text:
            continue

        # ====================================================
        # 1. Asociación semántica real mediante <label>
        # ====================================================

        try:
            locator = page.get_by_label(
                label_text,
                exact=False,
            )

            candidate = await _first_visible(locator)

            if candidate is not None:
                return candidate

        except Exception:
            pass

        # ====================================================
        # 2. Casos conocidos por tipo de pregunta
        # ====================================================

        normalized = label_text.lower().strip()

        # ----------------------------------------------------
        # Submitted by
        # ----------------------------------------------------

        if "submitted by" in normalized:
            try:
                candidate = await _first_visible(page.locator('input[type="email"]'))

                if candidate is not None:
                    return candidate

            except Exception:
                pass

        # ----------------------------------------------------
        # Production Completed Date
        # ----------------------------------------------------

        if (
            "production completed date" in normalized
            or "production complete date" in normalized
            or "completed date" in normalized
        ):
            try:
                candidate = await _first_visible(
                    page.locator('input[placeholder*="mm/dd/yyyy" i]')
                )

                if candidate is not None:
                    return candidate

            except Exception:
                pass

        # ====================================================
        # 3. Buscar texto exacto/aproximado de la pregunta
        # ====================================================

        question_candidates = []

        try:
            exact_locator = page.get_by_text(
                label_text,
                exact=True,
            )

            exact_count = await exact_locator.count()

            for index in range(exact_count):
                question_candidates.append(exact_locator.nth(index))

        except Exception:
            pass

        try:
            approximate_locator = page.get_by_text(
                label_text,
                exact=False,
            )

            approximate_count = await approximate_locator.count()

            for index in range(
                min(
                    approximate_count,
                    10,
                )
            ):
                question_candidates.append(approximate_locator.nth(index))

        except Exception:
            pass

        # ====================================================
        # 4. Desde la pregunta, buscar SOLAMENTE en un
        #    contenedor cercano.
        # ====================================================

        for question in question_candidates:
            try:
                if not await question.is_visible():
                    continue

            except Exception:
                continue

            # Solo pocos niveles.
            # No subir hasta el contenedor general del formulario.
            for ancestor_level in range(
                1,
                4,
            ):
                try:
                    xpath = "xpath=" + "/.." * ancestor_level

                    container = question.locator(xpath)

                    controls = container.locator(
                        "input:not([type='hidden'])"
                        ":not([type='checkbox'])"
                        ":not([type='radio']), "
                        "textarea, "
                        "[role='combobox']"
                    )

                    control_count = await controls.count()

                    if control_count == 0:
                        continue

                    # Preferimos un único control.
                    if control_count == 1:
                        candidate = controls.first

                        try:
                            if await candidate.is_visible():
                                return candidate

                        except Exception:
                            continue

                    # Si hay varios, buscar el más próximo
                    # verticalmente a la pregunta.
                    question_box = await question.bounding_box()

                    if not question_box:
                        continue

                    best_candidate = None
                    best_distance = None

                    for control_index in range(control_count):
                        candidate = controls.nth(control_index)

                        try:
                            if not await candidate.is_visible():
                                continue

                            control_box = await candidate.bounding_box()

                            if not control_box:
                                continue

                            # El control esperado normalmente está
                            # debajo de la pregunta.
                            vertical_distance = control_box["y"] - question_box["y"]

                            if vertical_distance < -10:
                                continue

                            if (
                                best_distance is None
                                or vertical_distance < best_distance
                            ):
                                best_distance = vertical_distance

                                best_candidate = candidate

                        except Exception:
                            continue

                    if best_candidate is not None:
                        return best_candidate

                except Exception:
                    continue

        # ====================================================
        # 5. Placeholder exacto/aproximado
        # ====================================================

        try:
            locator = page.get_by_placeholder(
                label_text,
                exact=False,
            )

            candidate = await _first_visible(locator)

            if candidate is not None:
                return candidate

        except Exception:
            pass

    return None


# ============================================================
# Texto normal
# ============================================================


async def _fill_text_field(
    page: Page,
    *,
    labels: list[str],
    value: Any,
    required: bool = True,
) -> bool:
    text = _clean(value)

    if not text and not required:
        return False

    locator = await _find_input_by_label(
        page,
        labels,
    )

    if locator is None:
        if required:
            raise SmartsheetFieldNotFoundError(f"Field not found: {labels[0]}")

        logger.warning(
            "Optional field not found and will be skipped: %s",
            labels,
        )

        return False

    try:
        await locator.scroll_into_view_if_needed()

        await locator.fill(text)

    except Exception as exc:
        if required:
            raise SmartsheetAutomationError(
                f"Could not fill field '{labels[0]}': {exc}"
            ) from exc

        logger.warning(
            "Optional field could not be filled: %s. Error: %s",
            labels,
            exc,
        )

        return False

    return True


# ============================================================
# Checkbox
# ============================================================


async def _set_checkbox_or_radio(
    page: Page,
    *,
    labels: list[str],
    checked: bool,
    required: bool = False,
) -> bool:
    """
    Marca un checkbox o radio de Smartsheet.

    Estrategia:

    1. Buscar por role + accessible name.
    2. Buscar por get_by_label.
    3. Buscar un <label for="..."> real.
    4. Buscar la pregunta visible y localizar el checkbox
       dentro de su contenedor data-field-name.
    5. Fallback por proximidad visual vertical.

    Esto soporta preguntas Smartsheet donde el texto de la
    pregunta está separado visualmente del checkbox.

    Ejemplos:
    - Sub Contractor
    - Fiber Placed
    - Splicing
    - Testing
    - Send me a copy of my responses
    """

    if not checked:
        return False

    last_error = None

    # ========================================================
    # Helper para marcar y verificar un control
    # ========================================================

    async def try_check(
        candidate: Locator,
        *,
        method: str,
        label_text: str,
    ) -> bool:
        nonlocal last_error

        try:
            count = await candidate.count()

            if count <= 0:
                return False

            for index in range(count):
                control = candidate.nth(index)

                try:
                    if not await control.is_visible():
                        continue

                    control_type = _clean(
                        await control.get_attribute(
                            "type",
                        )
                    ).lower()

                    control_role = _clean(
                        await control.get_attribute(
                            "role",
                        )
                    ).lower()

                    if (
                        control_type
                        not in {
                            "checkbox",
                            "radio",
                        }
                        and control_role != "checkbox"
                    ):
                        continue

                    await control.scroll_into_view_if_needed()

                    if await control.is_checked():
                        print(
                            "CHECKBOX ALREADY CHECKED:",
                            {
                                "label": label_text,
                                "method": method,
                                "index": index,
                            },
                        )

                        return True

                    await control.set_checked(
                        True,
                    )

                    await page.wait_for_timeout(
                        500,
                    )

                    current_state = await control.is_checked()

                    print(
                        "CHECKBOX SET:",
                        {
                            "label": label_text,
                            "method": method,
                            "index": index,
                            "type": control_type,
                            "role": control_role,
                            "checked": current_state,
                        },
                    )

                    if current_state:
                        return True

                except Exception as exc:
                    last_error = exc

                    print(
                        "CHECKBOX CANDIDATE FAILED:",
                        {
                            "label": label_text,
                            "method": method,
                            "index": index,
                            "error": str(
                                exc,
                            ),
                        },
                    )

                    continue

        except Exception as exc:
            last_error = exc

        return False

    # ========================================================
    # Procesar cada label posible
    # ========================================================

    for raw_label in labels:
        label_text = _clean(
            raw_label,
        )

        if not label_text:
            continue

        print(
            "SEARCHING CHECKBOX:",
            {
                "label": label_text,
            },
        )

        # ====================================================
        # 1. Role + accessible name exacto
        # ====================================================

        try:
            locator = page.get_by_role(
                "checkbox",
                name=label_text,
                exact=True,
            )

            if await try_check(
                locator,
                method="role_exact",
                label_text=label_text,
            ):
                return True

        except Exception as exc:
            last_error = exc

        # ====================================================
        # 2. Role + accessible name aproximado
        # ====================================================

        try:
            locator = page.get_by_role(
                "checkbox",
                name=label_text,
                exact=False,
            )

            if await try_check(
                locator,
                method="role_approximate",
                label_text=label_text,
            ):
                return True

        except Exception as exc:
            last_error = exc

        # ====================================================
        # 3. get_by_label
        # ====================================================

        try:
            locator = page.get_by_label(
                label_text,
                exact=False,
            )

            if await try_check(
                locator,
                method="get_by_label",
                label_text=label_text,
            ):
                return True

        except Exception as exc:
            last_error = exc

        # ====================================================
        # 4. LABEL real con atributo FOR
        # ====================================================

        try:
            label_locators = page.locator(
                "label",
            ).filter(
                has_text=label_text,
            )

            label_count = await label_locators.count()

            for index in range(label_count):
                label_candidate = label_locators.nth(
                    index,
                )

                try:
                    if not await label_candidate.is_visible():
                        continue

                    visible_text = _clean(
                        await label_candidate.inner_text(),
                    )

                    if label_text.lower() not in visible_text.lower():
                        continue

                    input_id = await label_candidate.get_attribute(
                        "for",
                    )

                    if not input_id:
                        continue

                    candidate = page.locator(
                        f"#{input_id}",
                    )

                    if await try_check(
                        candidate,
                        method="label_for",
                        label_text=label_text,
                    ):
                        return True

                except Exception as exc:
                    last_error = exc

                    continue

        except Exception as exc:
            last_error = exc

        # ====================================================
        # 5. Buscar texto visible de la pregunta
        # ====================================================

        question_candidates = []

        try:
            exact_questions = page.get_by_text(
                label_text,
                exact=True,
            )

            exact_count = await exact_questions.count()

            for index in range(exact_count):
                question_candidates.append(
                    exact_questions.nth(
                        index,
                    )
                )

        except Exception as exc:
            last_error = exc

        try:
            approximate_questions = page.get_by_text(
                label_text,
                exact=False,
            )

            approximate_count = await approximate_questions.count()

            for index in range(
                min(
                    approximate_count,
                    15,
                )
            ):
                question_candidates.append(
                    approximate_questions.nth(
                        index,
                    )
                )

        except Exception as exc:
            last_error = exc

        # ====================================================
        # 6. Encontrar checkbox dentro de data-field-name
        #
        # Este es el caso importante de Smartsheet.
        # ====================================================

        for question in question_candidates:
            try:
                if not await question.is_visible():
                    continue

                question_text = _clean(
                    await question.inner_text(),
                )

                if label_text.lower() not in question_text.lower():
                    continue

                print(
                    "CHECKBOX QUESTION FOUND:",
                    {
                        "label": label_text,
                        "question_text": question_text,
                    },
                )

                field_container = question.locator(
                    "xpath=ancestor::div[@data-field-name][1]"
                )

                if await field_container.count() > 0:
                    checkbox = field_container.locator('input[type="checkbox"]')

                    if await try_check(
                        checkbox,
                        method="data_field_name_container",
                        label_text=label_text,
                    ):
                        return True

                    role_checkbox = field_container.locator('[role="checkbox"]')

                    if await try_check(
                        role_checkbox,
                        method="data_field_name_role",
                        label_text=label_text,
                    ):
                        return True

            except Exception as exc:
                last_error = exc

                continue

        # ====================================================
        # 7. Buscar contenedores padres cercanos
        #
        # Smartsheet a veces no utiliza data-field-name en
        # todos los controles.
        # ====================================================

        for question in question_candidates:
            try:
                if not await question.is_visible():
                    continue

                question_box = await question.bounding_box()

                if not question_box:
                    continue

                for ancestor_level in range(
                    1,
                    7,
                ):
                    try:
                        xpath = "xpath=" + "/.." * ancestor_level

                        container = question.locator(
                            xpath,
                        )

                        checkboxes = container.locator(
                            'input[type="checkbox"], ' '[role="checkbox"]'
                        )

                        checkbox_count = await checkboxes.count()

                        if checkbox_count == 0:
                            continue

                        # ====================================
                        # Buscar el checkbox visualmente más
                        # cercano al texto de la pregunta.
                        # ====================================

                        best_candidate = None

                        best_distance = None

                        for checkbox_index in range(
                            checkbox_count,
                        ):
                            candidate = checkboxes.nth(
                                checkbox_index,
                            )

                            try:
                                if not await candidate.is_visible():
                                    continue

                                checkbox_box = await candidate.bounding_box()

                                if not checkbox_box:
                                    continue

                                vertical_distance = (
                                    checkbox_box["y"] - question_box["y"]
                                )

                                horizontal_distance = abs(
                                    checkbox_box["x"] - question_box["x"]
                                )

                                # El checkbox normalmente está
                                # justo debajo del título.
                                if vertical_distance < -20:
                                    continue

                                if vertical_distance > 180:
                                    continue

                                distance = (
                                    abs(
                                        vertical_distance,
                                    )
                                    + horizontal_distance
                                )

                                if best_distance is None or distance < best_distance:
                                    best_distance = distance

                                    best_candidate = candidate

                            except Exception as exc:
                                last_error = exc

                                continue

                        if best_candidate is not None:
                            if await try_check(
                                best_candidate,
                                method=("near_question_" f"ancestor_{ancestor_level}"),
                                label_text=label_text,
                            ):
                                return True

                    except Exception as exc:
                        last_error = exc

                        continue

            except Exception as exc:
                last_error = exc

                continue

    # ========================================================
    # No se pudo marcar
    # ========================================================

    if required:
        raise SmartsheetFieldNotFoundError(
            (
                "Checkbox/radio not found or could not be "
                f"checked: {labels[0]}. "
                f"Last error: {last_error}"
            )
        )

    logger.warning(
        ("Optional checkbox/radio could not be checked: " "%s. Last error: %s"),
        labels,
        last_error,
    )

    return False


async def _set_radio_answer(
    page: Page,
    *,
    question_labels: list[str],
    answer: bool,
    required: bool = True,
) -> bool:
    """
    Selecciona YES o NO en una pregunta radio de Smartsheet.

    Ejemplos:
    - Aerial Case -> YES / NO
    - Re-Entry -> YES / NO

    Busca primero el contenedor real de la pregunta y luego
    selecciona el radio cuyo value sea YES o NO.
    """

    answer_value = "YES" if answer else "NO"

    question = None

    # ========================================================
    # Encontrar pregunta visible
    # ========================================================

    for raw_label in question_labels:
        label_text = _clean(
            raw_label,
        )

        if not label_text:
            continue

        try:
            candidates = page.locator(
                "label",
            ).filter(
                has_text=label_text,
            )

            count = await candidates.count()

            for index in range(
                count,
            ):
                candidate = candidates.nth(
                    index,
                )

                try:
                    if not await candidate.is_visible():
                        continue

                    candidate_text = _clean(
                        await candidate.inner_text(),
                    )

                    if label_text.lower() not in candidate_text.lower():
                        continue

                    question = candidate

                    break

                except Exception:
                    continue

            if question is not None:
                break

        except Exception:
            continue

    if question is None:
        if required:
            raise SmartsheetFieldNotFoundError(
                f"Radio question not found: " f"{question_labels[0]}"
            )

        return False

    # ========================================================
    # Subir hasta el data-field-name de la pregunta
    # ========================================================

    field_container = question.locator("xpath=ancestor::div[@data-field-name][1]")

    try:
        await field_container.wait_for(
            state="visible",
            timeout=10_000,
        )

    except Exception as exc:
        if required:
            raise SmartsheetFieldNotFoundError(
                f"Radio field container not found for " f"{question_labels[0]}: {exc}"
            ) from exc

        return False

    # ========================================================
    # Buscar radio YES / NO dentro de esa pregunta
    # ========================================================

    radio = field_container.locator(f'input[type="radio"][value="{answer_value}"]')

    try:
        await radio.wait_for(
            state="visible",
            timeout=10_000,
        )

        await radio.scroll_into_view_if_needed()

        await radio.set_checked(
            True,
        )

        checked = await radio.is_checked()

        print(
            "RADIO ANSWER SET:",
            {
                "question": question_labels[0],
                "answer": answer_value,
                "checked": checked,
            },
        )

        if not checked:
            raise SmartsheetAutomationError(
                f"{question_labels[0]} " f"did not remain checked as {answer_value}."
            )

        await page.wait_for_timeout(
            700,
        )

        return True

    except SmartsheetAutomationError:
        raise

    except Exception as exc:
        if required:
            raise SmartsheetAutomationError(
                f"Could not select {answer_value} " f"for {question_labels[0]}: {exc}"
            ) from exc

        logger.warning(
            "Optional radio question could not be completed: " "%s. Error: %s",
            question_labels,
            exc,
        )

        return False


# ============================================================
# Combobox progresivo
# ============================================================


async def _fill_combobox(
    page: Page,
    *,
    labels: list[str],
    value: Any,
    required: bool = True,
) -> bool:
    """
    Llena un combobox/autocomplete de Smartsheet.

    Flujo:
    1. Encuentra el control relacionado con la pregunta.
    2. Hace click.
    3. Escribe el texto.
    4. Espera la opción.
    5. Selecciona la opción exacta o aproximada.
    6. Espera el render de la siguiente pregunta.
    """

    text = _clean(value)

    if not text:
        if required:
            raise SmartsheetAutomationError(
                f"Empty value for required combobox: {labels[0]}"
            )

        return False

    locator = await _find_input_by_label(
        page,
        labels,
    )

    if locator is None:
        if required:
            raise SmartsheetFieldNotFoundError(f"Combobox not found: {labels[0]}")

        return False

    try:
        await locator.scroll_into_view_if_needed()

        await locator.click()

        # Algunos combobox permiten fill directamente.
        try:
            await locator.fill(text)

        except Exception:
            await locator.press("Control+A")

            await locator.type(
                text,
                delay=50,
            )

        await page.wait_for_timeout(700)

        # ====================================================
        # Buscar opción exacta
        # ====================================================

        option = None

        try:
            exact_option = page.get_by_text(
                text,
                exact=True,
            )

            option = await _first_visible(exact_option)

        except Exception:
            option = None

        # ====================================================
        # Buscar opción aproximada
        # ====================================================

        if option is None:
            try:
                approximate_option = page.get_by_text(
                    text,
                    exact=False,
                )

                option = await _first_visible(approximate_option)

            except Exception:
                option = None

        # ====================================================
        # Si aparece una opción, seleccionarla.
        # ====================================================

        if option is not None:
            try:
                # Evitar hacer click en el propio input.
                tag_name = await option.evaluate("el => el.tagName")

                if str(tag_name).upper() not in {
                    "INPUT",
                    "TEXTAREA",
                }:
                    await option.click()

                    await page.wait_for_timeout(700)

                    return True

            except Exception:
                pass

        # ====================================================
        # Fallback:
        # Enter selecciona normalmente la opción activa.
        # ====================================================

        await locator.press("Enter")

        await page.wait_for_timeout(700)

        return True

    except Exception as exc:
        if required:
            raise SmartsheetAutomationError(
                f"Could not select '{text}' in '{labels[0]}': {exc}"
            ) from exc

        logger.warning(
            "Optional combobox could not be completed: %s. Error: %s",
            labels,
            exc,
        )

        return False


# ============================================================
# Fecha
# ============================================================


async def _fill_date_field(
    page: Page,
    *,
    labels: list[str],
    value,
    required: bool = True,
) -> bool:
    """
    Llena Production Completed Date utilizando la asociación
    HTML real del formulario Smartsheet:

        <label for="INPUT_ID">
            Production Completed Date
        </label>

        <input id="INPUT_ID" ...>

    No depende de:
    - placeholder
    - posición visual
    - coordenadas
    - foco actual

    Esto evita escribir accidentalmente en Submitted by.
    """

    if not value:
        if required:
            raise SmartsheetAutomationError("Production Completed Date has no value.")

        return False

    # ========================================================
    # Valor de fecha
    # ========================================================

    if hasattr(
        value,
        "strftime",
    ):
        date_value = value.strftime("%m/%d/%Y")

    else:
        raw_date_value = _clean(value)

        date_value = raw_date_value

        try:
            year, month, day = raw_date_value.split("-")

            if len(year) == 4 and len(month) == 2 and len(day) == 2:
                date_value = f"{month}/{day}/{year}"

        except ValueError:
            pass

    print(
        "PRODUCTION COMPLETED DATE NORMALIZED:",
        {
            "original": repr(value),
            "smartsheet_value": repr(date_value),
        },
    )

    # ========================================================
    # Esperar la pregunta
    # ========================================================

    appeared = await _wait_for_question(
        page,
        labels,
        timeout=15_000,
        required=required,
    )

    if not appeared:
        return False

    await page.wait_for_timeout(
        500,
    )

    # ========================================================
    # Buscar el LABEL REAL de Production Completed Date
    # ========================================================

    label_locator = None

    for label_text in labels:
        try:
            labels_found = page.locator("label").filter(
                has_text=label_text,
            )

            count = await labels_found.count()

            for index in range(count):
                candidate = labels_found.nth(
                    index,
                )

                try:
                    if not await candidate.is_visible():
                        continue

                    label_for = await candidate.get_attribute(
                        "for",
                    )

                    if not label_for:
                        continue

                    label_locator = candidate

                    print(
                        "DATE LABEL FOUND:",
                        {
                            "text": (await candidate.inner_text()),
                            "for": label_for,
                        },
                    )

                    break

                except Exception:
                    continue

            if label_locator is not None:
                break

        except Exception:
            continue

    # ========================================================
    # Label no encontrado
    # ========================================================

    if label_locator is None:
        if required:
            raise SmartsheetFieldNotFoundError(
                "Production Completed Date label " "could not be located."
            )

        return False

    # ========================================================
    # Obtener el ID exacto del input usando label.for
    # ========================================================

    input_id = await label_locator.get_attribute(
        "for",
    )

    if not input_id:
        if required:
            raise SmartsheetFieldNotFoundError(
                "Production Completed Date label " "does not contain a 'for' attribute."
            )

        return False

    print(
        "DATE INPUT ID FROM LABEL:",
        input_id,
    )

    # ========================================================
    # Buscar INPUT EXACTO
    # ========================================================

    locator = page.locator(f"#{input_id}")

    try:
        await locator.wait_for(
            state="visible",
            timeout=15_000,
        )

    except Exception as exc:
        if required:
            raise SmartsheetFieldNotFoundError(
                "Production Completed Date input "
                f"#{input_id} did not become visible: {exc}"
            ) from exc

        return False

    # ========================================================
    # Verificar que realmente sea el input correcto
    # ========================================================

    input_info = await locator.evaluate("""
        el => ({
            tag: el.tagName || "",
            type: el.type || "",
            id: el.id || "",
            name: el.name || "",
            value: el.value || "",
            ariaRequired:
                el.getAttribute("aria-required") || "",
            ariaLabelledBy:
                el.getAttribute("aria-labelledby") || ""
        })
        """)

    print(
        "DATE REAL INPUT:",
        input_info,
    )

    input_type = _clean(
        input_info.get(
            "type",
            "",
        )
    ).lower()

    if input_type == "email":
        raise SmartsheetAutomationError(
            "Production Completed Date resolved " "to an email input."
        )

    # ========================================================
    # Llenar usando Playwright sobre el input exacto
    # ========================================================

    try:
        await locator.scroll_into_view_if_needed()

        await locator.click()

        await locator.fill(
            date_value,
        )

        await page.wait_for_timeout(
            300,
        )

        current_value = await locator.input_value()

        print(
            "DATE VALUE AFTER FILL:",
            repr(current_value),
        )

        # ====================================================
        # Si fill no fue suficiente, escribir como usuario
        # ====================================================

        if not current_value:
            await locator.click()

            await locator.press(
                "Meta+A",
            )

            await locator.press(
                "Backspace",
            )

            await locator.type(
                date_value,
                delay=100,
            )

            await page.wait_for_timeout(
                300,
            )

            current_value = await locator.input_value()

            print(
                "DATE VALUE AFTER TYPE:",
                repr(current_value),
            )

        # ====================================================
        # Validar que sí se escribió
        # ====================================================

        if not current_value:
            raise SmartsheetAutomationError(
                "Production Completed Date input " "remained empty after fill."
            )

        # ====================================================
        # Sacar foco para activar lógica condicional
        # ====================================================

        await locator.press(
            "Tab",
        )

        await page.wait_for_timeout(
            1500,
        )

        print(
            "PRODUCTION COMPLETED DATE FILLED:",
            repr(current_value),
        )

        return True

    except SmartsheetAutomationError:
        raise

    except Exception as exc:
        if required:
            raise SmartsheetAutomationError(
                "Could not fill Production Completed Date "
                f"with {date_value!r}: {exc}"
            ) from exc

        logger.warning(
            "Production Completed Date could not be filled: %s",
            exc,
        )

        return False


# ============================================================
# Upload
# ============================================================


async def _upload_attachments(
    page: Page,
    file_paths: list[str],
) -> list[str]:
    """
    Adjunta uno o varios ZIP al campo de archivos de Smartsheet.

    Playwright permite asignar varios archivos al mismo
    input usando set_input_files([...]), siempre que el
    input soporte múltiples archivos.

    Devuelve los nombres de los archivos que quedaron
    seleccionados en el navegador.
    """

    normalized_paths: list[Path] = []

    for raw_path in file_paths:
        path = Path(
            raw_path,
        )

        if not path.exists():
            raise SmartsheetAttachmentError(
                ("Attachment file does not exist: " f"{path}")
            )

        file_size = path.stat().st_size

        if file_size > 29_000_000:
            raise SmartsheetAttachmentError(
                (
                    f"Attachment {path.name} exceeds "
                    "the Smartsheet limit. "
                    f"Size: {file_size} bytes. "
                    "Maximum: 29000000 bytes."
                )
            )

        normalized_paths.append(
            path.resolve(),
        )

    if not normalized_paths:
        return []

    if len(normalized_paths) > 10:
        raise SmartsheetAttachmentError(
            (
                "Smartsheet attachment limit exceeded. "
                f"Received {len(normalized_paths)} ZIP files; "
                "maximum allowed by this automation is 10."
            )
        )

    file_inputs = page.locator(
        'input[type="file"]',
    )

    count = await file_inputs.count()

    if count == 0:
        raise SmartsheetAttachmentError(
            "No file upload field was found " "in the Smartsheet form."
        )

    last_error = None

    for index in range(
        count,
    ):
        locator = file_inputs.nth(
            index,
        )

        try:
            await locator.set_input_files([str(path) for path in normalized_paths])

            await page.wait_for_timeout(
                3000,
            )

            uploaded_filenames = [path.name for path in normalized_paths]

            body_text = ""

            try:
                body_text = await page.locator(
                    "body",
                ).inner_text()

            except Exception:
                body_text = ""

            # =================================================
            # Smartsheet puede aceptar el input técnicamente,
            # pero mostrar un error visual de tamaño.
            # =================================================

            size_error_messages = [
                "Exceeds the max file size",
                "exceeds the max file size",
                "File is too large",
                "file is too large",
            ]

            detected_size_error = next(
                (message for message in size_error_messages if message in body_text),
                None,
            )

            if detected_size_error:
                raise SmartsheetAttachmentError(
                    (
                        "Smartsheet rejected one or more "
                        "attachments because of file size. "
                        f"Visible error: {detected_size_error}. "
                        f"Files: {uploaded_filenames}"
                    )
                )

            print(
                "SMARTSHEET ATTACHMENTS UPLOADED:",
                {
                    "input_index": index,
                    "count": len(
                        uploaded_filenames,
                    ),
                    "filenames": (uploaded_filenames),
                    "sizes": {
                        path.name: path.stat().st_size for path in normalized_paths
                    },
                },
            )

            return uploaded_filenames

        except Exception as exc:
            last_error = exc

            print(
                "SMARTSHEET ATTACHMENT INPUT FAILED:",
                {
                    "input_index": index,
                    "error": str(
                        exc,
                    ),
                },
            )

    if isinstance(
        last_error,
        SmartsheetAttachmentError,
    ):
        raise last_error

    raise SmartsheetAttachmentError(
        ("Could not upload Smartsheet attachments: " f"{last_error}")
    )


# ============================================================
# Construcción de datos
# ============================================================


def build_form_values(
    submission,
    batch,
) -> dict[str, Any]:
    """
    Convierte ClientSubmission.form_payload en los valores
    que deberá recibir el formulario Smartsheet.

    La configuración de Work Completed proviene del
    form_payload individual del ClientSubmission.

    Aerial Case y Re-Entry son respuestas independientes
    YES / NO.

    Cuando Aerial Case = YES:
    - aerial_case_value_1 representa Aerial Sequential IN.
    - aerial_case_value_2 representa Aerial Sequential OUT.

    Reglas C-108:

        C-108-UG
            -> Aerial Case = NO
            -> C-108-UG - Splice Case Quantity

        C-108-AER
            -> Aerial Case = YES
            -> C-108-AER - Splice Case Quantity

    Se mantiene compatibilidad con payloads anteriores que
    contienen:

        splice_case_ug_quantity
        splice_case_aer_quantity

    aunque todavía no tengan:

        splice_case_quantity
    """

    payload = (
        submission.form_payload
        if isinstance(
            submission.form_payload,
            dict,
        )
        else {}
    )

    raw_quantities = payload.get(
        "quantities",
        {},
    )

    quantities = (
        dict(
            raw_quantities,
        )
        if isinstance(
            raw_quantities,
            dict,
        )
        else {}
    )

    # ========================================================
    # Configuración individual de Work Completed
    # ========================================================

    fiber_placed = bool(
        payload.get(
            "fiber_placed",
            False,
        )
    )

    splicing = bool(
        payload.get(
            "splicing",
            False,
        )
    )

    testing = bool(
        payload.get(
            "testing",
            False,
        )
    )

    aerial_case = bool(
        payload.get(
            "aerial_case",
            False,
        )
    )

    re_entry = bool(
        payload.get(
            "re_entry",
            False,
        )
    )

    # ========================================================
    # Aerial Sequential
    # ========================================================

    aerial_sequential_in = _clean(
        payload.get(
            "aerial_case_value_1",
            "",
        )
    )

    aerial_sequential_out = _clean(
        payload.get(
            "aerial_case_value_2",
            "",
        )
    )

    if not aerial_case:
        aerial_sequential_in = ""
        aerial_sequential_out = ""

    # ========================================================
    # Construir Work Types
    # ========================================================

    work_types = []

    if fiber_placed:
        work_types.append(
            "Fiber Placed",
        )

    if splicing:
        work_types.append(
            "Splicing",
        )

    if testing:
        work_types.append(
            "Testing",
        )

    # ========================================================
    # Configuración dinámica de C-108
    # ========================================================

    if aerial_case:
        splice_case_code = "C-108-AER"

        splice_case_label = "C-108-AER - Splice Case Quantity"

        fallback_splice_case_quantity = quantities.get(
            "splice_case_aer_quantity",
            0,
        )

    else:
        splice_case_code = "C-108-UG"

        splice_case_label = "C-108-UG - Splice Case Quantity"

        fallback_splice_case_quantity = quantities.get(
            "splice_case_ug_quantity",
            0,
        )

    # ========================================================
    # Compatibilidad de payloads
    #
    # Nuevo payload:
    #     splice_case_quantity
    #
    # Payload anterior:
    #     splice_case_ug_quantity
    #     splice_case_aer_quantity
    # ========================================================

    splice_case_quantity = quantities.get(
        "splice_case_quantity",
    )

    if splice_case_quantity in {
        None,
        "",
    }:
        splice_case_quantity = fallback_splice_case_quantity

    quantities["splice_case_quantity"] = splice_case_quantity

    print(
        "SMARTSHEET SPLICE CASE VALUES:",
        {
            "submission_id": submission.pk,
            "project_id": _clean(
                payload.get(
                    "project_id",
                    submission.project_id,
                )
            ),
            "aerial_case": aerial_case,
            "splice_case_code": splice_case_code,
            "splice_case_label": splice_case_label,
            "splice_case_quantity": (splice_case_quantity),
            "raw_splice_case_quantity": (
                raw_quantities.get(
                    "splice_case_quantity",
                )
                if isinstance(
                    raw_quantities,
                    dict,
                )
                else None
            ),
            "splice_case_ug_quantity": (
                quantities.get(
                    "splice_case_ug_quantity",
                    0,
                )
            ),
            "splice_case_aer_quantity": (
                quantities.get(
                    "splice_case_aer_quantity",
                    0,
                )
            ),
        },
    )

    # ========================================================
    # Valores finales
    # ========================================================

    return {
        # ----------------------------------------------------
        # Información general
        # ----------------------------------------------------
        "submitted_by_email": _clean(
            payload.get(
                "submitted_by_email",
                batch.submitted_by_email,
            )
        ),
        "send_copy_of_responses": bool(
            payload.get(
                "send_copy_of_responses",
                batch.send_copy_of_responses,
            )
        ),
        "copy_email": _clean(
            payload.get(
                "copy_email",
                batch.copy_email,
            )
        ),
        "is_subcontractor": bool(
            payload.get(
                "is_subcontractor",
                batch.is_subcontractor,
            )
        ),
        "subcontractor_name": _clean(
            payload.get(
                "subcontractor_name",
                batch.subcontractor_name,
            )
        ),
        "production_completed_date": payload.get(
            "production_completed_date",
            batch.production_completed_date,
        ),
        "market": _clean(
            payload.get(
                "market",
                batch.market,
            )
        ),
        # ----------------------------------------------------
        # Proyecto
        # ----------------------------------------------------
        "project_id": _clean(
            payload.get(
                "project_id",
                submission.project_id,
            )
        ),
        "dfn_name": _clean(
            payload.get(
                "dfn_name",
                submission.dfn_name,
            )
        ),
        "access_point_id": _normalize_access_point_id(
            payload.get(
                "access_point_id",
                submission.access_point_id,
            )
        ),
        # ----------------------------------------------------
        # Work Completed individual
        # ----------------------------------------------------
        "configuration_mode": _clean(
            payload.get(
                "configuration_mode",
                "common",
            )
        ),
        "fiber_placed": fiber_placed,
        "splicing": splicing,
        "testing": testing,
        "aerial_case": aerial_case,
        "re_entry": re_entry,
        "work_types": work_types,
        # ----------------------------------------------------
        # Aerial Sequential
        # ----------------------------------------------------
        "aerial_case_value_1": (aerial_sequential_in),
        "aerial_case_value_2": (aerial_sequential_out),
        "aerial_sequential_in": (aerial_sequential_in),
        "aerial_sequential_out": (aerial_sequential_out),
        # ----------------------------------------------------
        # Splice Case dinámico
        # ----------------------------------------------------
        "splice_case_code": (splice_case_code),
        "splice_case_label": (splice_case_label),
        "splice_case_quantity": (splice_case_quantity),
        # ----------------------------------------------------
        # Quantities
        # ----------------------------------------------------
        "quantities": quantities,
        # ----------------------------------------------------
        # Payload completo
        # ----------------------------------------------------
        "payload": payload,
    }


async def _inspect_production_date_dom(
    page: Page,
):
    """
    Inspecciona el DOM real alrededor de
    Production Completed Date.

    No intenta llenar la fecha.
    Solo imprime la estructura HTML real para
    dejar de adivinar selectores.
    """

    print("\n")
    print("=" * 100)
    print("INSPECTING PRODUCTION COMPLETED DATE DOM")
    print("=" * 100)

    # ========================================================
    # Esperar que aparezca el texto
    # ========================================================

    question = page.get_by_text(
        "Production Completed Date",
        exact=False,
    ).first

    await question.wait_for(
        state="visible",
        timeout=15_000,
    )

    await page.wait_for_timeout(1000)

    # ========================================================
    # Información del elemento que contiene el texto
    # ========================================================

    question_info = await question.evaluate("""
        el => ({
            tag: el.tagName || "",
            id: el.id || "",
            className:
                typeof el.className === "string"
                    ? el.className
                    : "",
            text: el.innerText || el.textContent || "",
            html: el.outerHTML || ""
        })
        """)

    print("\n===== QUESTION ELEMENT =====")
    print(question_info)

    # ========================================================
    # Ancestros del label/pregunta
    # ========================================================

    ancestors = await question.evaluate("""
        el => {
            const result = [];

            let current = el;

            for (let level = 0; level < 10; level++) {
                if (!current) {
                    break;
                }

                result.push({
                    level: level,
                    tag: current.tagName || "",
                    id: current.id || "",
                    className:
                        typeof current.className === "string"
                            ? current.className
                            : "",
                    role:
                        current.getAttribute
                            ? (
                                current.getAttribute("role")
                                || ""
                            )
                            : "",
                    ariaLabel:
                        current.getAttribute
                            ? (
                                current.getAttribute("aria-label")
                                || ""
                            )
                            : "",
                    ariaLabelledBy:
                        current.getAttribute
                            ? (
                                current.getAttribute(
                                    "aria-labelledby"
                                )
                                || ""
                            )
                            : "",
                    html:
                        (current.outerHTML || "")
                        .slice(0, 10000)
                });

                current = current.parentElement;
            }

            return result;
        }
        """)

    print("\n===== QUESTION ANCESTORS =====")

    for ancestor in ancestors:
        print("\n")
        print("-" * 100)
        print("LEVEL:", ancestor["level"])
        print("TAG:", ancestor["tag"])
        print("ID:", ancestor["id"])
        print("CLASS:", ancestor["className"])
        print("ROLE:", ancestor["role"])
        print("ARIA-LABEL:", ancestor["ariaLabel"])
        print(
            "ARIA-LABELLEDBY:",
            ancestor["ariaLabelledBy"],
        )
        print("HTML:")
        print(ancestor["html"])

    # ========================================================
    # Todos los inputs visibles
    # ========================================================

    controls = await page.locator("""
        input,
        textarea,
        button,
        select,
        [role="textbox"],
        [role="combobox"],
        [contenteditable="true"]
        """).evaluate_all(
        """
        elements => elements.map(
            (el, index) => {
                const rect =
                    el.getBoundingClientRect();

                const style =
                    window.getComputedStyle(el);

                return {
                    index: index,
                    tag: el.tagName || "",
                    type: el.type || "",
                    id: el.id || "",
                    name: el.name || "",
                    className:
                        typeof el.className === "string"
                            ? el.className
                            : "",
                    placeholder:
                        el.placeholder || "",
                    value:
                        el.value || "",
                    role:
                        el.getAttribute("role") || "",
                    ariaLabel:
                        el.getAttribute("aria-label") || "",
                    ariaLabelledBy:
                        el.getAttribute(
                            "aria-labelledby"
                        )
                        || "",
                    contenteditable:
                        el.getAttribute(
                            "contenteditable"
                        )
                        || "",
                    visible:
                        !!(
                            rect.width
                            && rect.height
                            && style.visibility !== "hidden"
                            && style.display !== "none"
                        ),
                    x: rect.x,
                    y: rect.y,
                    width: rect.width,
                    height: rect.height,
                    html:
                        (el.outerHTML || "")
                        .slice(0, 5000)
                };
            }
        )
        """
    )

    print("\n===== ALL CONTROLS =====")

    for control in controls:
        print("\n")
        print("-" * 100)

        for key, value in control.items():
            print(
                f"{key}:",
                value,
            )

    # ========================================================
    # Elementos debajo del punto visual de la fecha
    # ========================================================

    question_box = await question.bounding_box()

    if question_box:
        points = [
            {
                "name": "below_20",
                "x": (question_box["x"] + 100),
                "y": (question_box["y"] + question_box["height"] + 20),
            },
            {
                "name": "below_40",
                "x": (question_box["x"] + 100),
                "y": (question_box["y"] + question_box["height"] + 40),
            },
            {
                "name": "below_60",
                "x": (question_box["x"] + 100),
                "y": (question_box["y"] + question_box["height"] + 60),
            },
            {
                "name": "center_field_guess",
                "x": (question_box["x"] + 400),
                "y": (question_box["y"] + question_box["height"] + 45),
            },
        ]

        print("\n===== ELEMENTS FROM POINT =====")

        for point in points:
            info = await page.evaluate(
                """
                point => {
                    const el = document.elementFromPoint(
                        point.x,
                        point.y
                    );

                    if (!el) {
                        return null;
                    }

                    return {
                        point: point,
                        tag: el.tagName || "",
                        type: el.type || "",
                        id: el.id || "",
                        name: el.name || "",
                        className:
                            typeof el.className === "string"
                                ? el.className
                                : "",
                        role:
                            el.getAttribute("role") || "",
                        placeholder:
                            el.placeholder || "",
                        value:
                            el.value || "",
                        ariaLabel:
                            el.getAttribute("aria-label")
                            || "",
                        ariaLabelledBy:
                            el.getAttribute(
                                "aria-labelledby"
                            )
                            || "",
                        html:
                            (el.outerHTML || "")
                            .slice(0, 10000),
                        parentHtml:
                            (
                                el.parentElement
                                && el.parentElement.outerHTML
                                || ""
                            )
                            .slice(0, 15000)
                    };
                }
                """,
                point,
            )

            print("\nPOINT:", point["name"])
            print(info)

    # ========================================================
    # Guardar HTML completo localmente
    # ========================================================

    html = await page.content()

    debug_dir = Path("tmp/client_submissions/debug")

    debug_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    html_path = debug_dir / "smartsheet_after_hyperlink.html"

    html_path.write_text(
        html,
        encoding="utf-8",
    )

    print("\n===== HTML SAVED =====")
    print(str(html_path.resolve()))

    print("\n")
    print("=" * 100)
    print("END PRODUCTION COMPLETED DATE DOM INSPECTION")
    print("=" * 100)
    print("\n")


# ============================================================
# Rellenar formulario progresivamente
# ============================================================


async def fill_smartsheet_form(
    page: Page,
    submission,
    batch,
) -> dict[str, Any]:
    """
    Rellena progresivamente el formulario Smartsheet.

    La configuración de Work Completed se obtiene desde
    ClientSubmission.form_payload.

    Flujo observado:

    1. Submitted by
    2. Sub Contractor
    3. Sub Contractor Name
    4. Production Completed Date
    5. Market
    6. DFN Name
    7. Work Types
    8. Aerial Case
    9. Aerial Sequential IN / OUT cuando Aerial Case = YES
    10. Re-Entry
    11. Access Point ID
    12. Quantities
    13. Send me a copy
    14. Email address

    Importante:

    Aerial Case = NO:
        C-108-UG - Splice Case Quantity

    Aerial Case = YES:
        C-108-AER - Splice Case Quantity
    """

    values = build_form_values(
        submission,
        batch,
    )

    filled: dict[str, Any] = {}

    # ========================================================
    # Debug configuración individual
    # ========================================================

    print(
        "SUBMISSION WORK CONFIGURATION:",
        {
            "submission_id": submission.pk,
            "project_id": values["project_id"],
            "configuration_mode": values["configuration_mode"],
            "fiber_placed": values["fiber_placed"],
            "splicing": values["splicing"],
            "testing": values["testing"],
            "aerial_case": values["aerial_case"],
            "re_entry": values["re_entry"],
            "aerial_sequential_in": (values["aerial_case_value_1"]),
            "aerial_sequential_out": (values["aerial_case_value_2"]),
            "work_types": values["work_types"],
        },
    )

    # ========================================================
    # 1. Submitted by
    # ========================================================

    await _wait_for_question(
        page,
        [
            "Submitted by",
        ],
        required=True,
    )

    submitted_filled = await _fill_text_field(
        page,
        labels=[
            "Submitted by:",
            "Submitted by",
            "Submitted By",
        ],
        value=values["submitted_by_email"],
        required=True,
    )

    if submitted_filled:
        filled["submitted_by_email"] = values["submitted_by_email"]

    await _wait_after_change(
        page,
    )

    # ========================================================
    # 2. Sub Contractor
    # ========================================================

    if values["is_subcontractor"]:
        await _wait_for_question(
            page,
            [
                "Sub Contractor",
            ],
            required=True,
        )

        checked = await _set_checkbox_or_radio(
            page,
            labels=[
                "Sub Contractor",
            ],
            checked=True,
            required=True,
        )

        filled["is_subcontractor"] = checked

        await _wait_after_change(
            page,
            1000,
        )

    # ========================================================
    # 3. Sub Contractor Name
    # ========================================================

    await _wait_for_question(
        page,
        [
            "Sub Contractor Name",
        ],
        timeout=15_000,
        required=True,
    )

    contractor_filled = await _fill_combobox(
        page,
        labels=[
            "Sub Contractor Name",
            "Subcontractor Name",
        ],
        value=values["subcontractor_name"],
        required=True,
    )

    if contractor_filled:
        filled["subcontractor_name"] = values["subcontractor_name"]

    await _wait_after_change(
        page,
        1000,
    )

    # ========================================================
    # 4. Production Completed Date
    # ========================================================

    await _wait_for_question(
        page,
        [
            "Production Completed Date",
        ],
        timeout=15_000,
        required=True,
    )

    date_filled = await _fill_date_field(
        page,
        labels=[
            "Production Completed Date",
            "Production completed date",
        ],
        value=values["production_completed_date"],
        required=True,
    )

    if date_filled:
        date_value = values["production_completed_date"]

        if hasattr(
            date_value,
            "isoformat",
        ):
            date_value = date_value.isoformat()

        filled["production_completed_date"] = _clean(
            date_value,
        )

    # ========================================================
    # 5. Market
    # ========================================================

    await _wait_for_question(
        page,
        [
            "Market",
        ],
        timeout=15_000,
        required=True,
    )

    market_filled = await _fill_combobox(
        page,
        labels=[
            "Market",
        ],
        value=values["market"],
        required=True,
    )

    if market_filled:
        filled["market"] = values["market"]

    await _wait_after_change(
        page,
        1000,
    )

    # ========================================================
    # 6. DFN Name
    # ========================================================

    await _wait_for_question(
        page,
        [
            "DFN Name",
        ],
        timeout=15_000,
        required=True,
    )

    dfn_filled = await _fill_text_field(
        page,
        labels=[
            "DFN Name",
            "DFN",
        ],
        value=values["dfn_name"],
        required=True,
    )

    if dfn_filled:
        filled["dfn_name"] = values["dfn_name"]

    dfn_locator = await _find_input_by_label(
        page,
        [
            "DFN Name",
            "DFN",
        ],
    )

    if dfn_locator is not None:
        try:
            await dfn_locator.press(
                "Tab",
            )

        except Exception:
            pass

    await _wait_after_change(
        page,
        1000,
    )

    # ========================================================
    # 7. Work Types
    # ========================================================

    for work_type in values["work_types"]:
        appeared = await _wait_for_question(
            page,
            [
                work_type,
            ],
            timeout=10_000,
            required=False,
        )

        if not appeared:
            logger.warning(
                "Work type did not appear: %s",
                work_type,
            )

            filled[f"work_type:{work_type}"] = False

            continue

        selected = await _set_checkbox_or_radio(
            page,
            labels=[
                work_type,
            ],
            checked=True,
            required=False,
        )

        filled[f"work_type:{work_type}"] = selected

        if selected:
            await _wait_after_change(
                page,
                1000,
            )

    # ========================================================
    # 8. Aerial Case
    # ========================================================

    aerial_case_appeared = await _wait_for_question(
        page,
        [
            "Aerial Case",
        ],
        timeout=10_000,
        required=False,
    )

    if aerial_case_appeared:
        aerial_case_filled = await _set_radio_answer(
            page,
            question_labels=[
                "Aerial Case",
            ],
            answer=values["aerial_case"],
            required=True,
        )

        if aerial_case_filled:
            filled["aerial_case"] = values["aerial_case"]

        await _wait_after_change(
            page,
            1200,
        )

    # ========================================================
    # 9. Aerial Sequential IN / OUT
    #
    # Solo aparecen cuando Aerial Case = YES.
    # Ambos campos son obligatorios en Smartsheet.
    # ========================================================

    if values["aerial_case"]:
        aerial_sequential_in = _clean(
            values["aerial_case_value_1"],
        )

        aerial_sequential_out = _clean(
            values["aerial_case_value_2"],
        )

        if not aerial_sequential_in:
            raise SmartsheetAutomationError(
                "Aerial Case is YES but " "Aerial Sequential IN is empty."
            )

        if not aerial_sequential_out:
            raise SmartsheetAutomationError(
                "Aerial Case is YES but " "Aerial Sequential OUT is empty."
            )

        # ----------------------------------------------------
        # Aerial Sequential IN
        # ----------------------------------------------------

        await _wait_for_question(
            page,
            [
                "Aerial Sequential IN",
            ],
            timeout=15_000,
            required=True,
        )

        aerial_in_filled = await _fill_text_field(
            page,
            labels=[
                "Aerial Sequential IN",
            ],
            value=aerial_sequential_in,
            required=True,
        )

        if aerial_in_filled:
            filled["aerial_sequential_in"] = aerial_sequential_in

            print(
                "AERIAL SEQUENTIAL IN FILLED:",
                repr(
                    aerial_sequential_in,
                ),
            )

        await _wait_after_change(
            page,
            700,
        )

        # ----------------------------------------------------
        # Aerial Sequential OUT
        # ----------------------------------------------------

        await _wait_for_question(
            page,
            [
                "Aerial Sequential OUT",
            ],
            timeout=15_000,
            required=True,
        )

        aerial_out_filled = await _fill_text_field(
            page,
            labels=[
                "Aerial Sequential OUT",
            ],
            value=aerial_sequential_out,
            required=True,
        )

        if aerial_out_filled:
            filled["aerial_sequential_out"] = aerial_sequential_out

            print(
                "AERIAL SEQUENTIAL OUT FILLED:",
                repr(
                    aerial_sequential_out,
                ),
            )

        await _wait_after_change(
            page,
            700,
        )

    # ========================================================
    # 10. Re-Entry
    # ========================================================

    re_entry_appeared = await _wait_for_question(
        page,
        [
            "Re-Entry",
            "Re Entry",
        ],
        timeout=10_000,
        required=False,
    )

    if re_entry_appeared:
        re_entry_filled = await _set_radio_answer(
            page,
            question_labels=[
                "Re-Entry",
                "Re Entry",
            ],
            answer=values["re_entry"],
            required=True,
        )

        if re_entry_filled:
            filled["re_entry"] = values["re_entry"]

        await _wait_after_change(
            page,
            700,
        )

    # ========================================================
    # 11. Access Point ID
    # ========================================================

    await _wait_for_question(
        page,
        [
            "Access Point ID",
            "Access Point Id",
            "AP ID",
        ],
        timeout=10_000,
        required=True,
    )

    access_point_filled = await _fill_text_field(
        page,
        labels=[
            "Access Point ID",
            "Access Point Id",
            "AP ID",
        ],
        value=values["access_point_id"],
        required=True,
    )

    if access_point_filled:
        filled["access_point_id"] = values["access_point_id"]

        print(
            "ACCESS POINT ID FILLED:",
            repr(
                values["access_point_id"],
            ),
        )

        await _wait_after_change(
            page,
        )

    # ========================================================
    # 12. Quantities
    #
    # C-108 cambia según Aerial Case:
    #
    # NO  -> C-108-UG
    # YES -> C-108-AER
    # ========================================================

    quantities = values["quantities"]

    if values["aerial_case"]:
        splice_case_label = "C-108-AER - Splice Case Quantity"

        splice_case_code = "C-108-AER"

    else:
        splice_case_label = "C-108-UG - Splice Case Quantity"

        splice_case_code = "C-108-UG"

    print(
        "SPLICE CASE SMARTSHEET FIELD:",
        {
            "aerial_case": values["aerial_case"],
            "code": splice_case_code,
            "label": splice_case_label,
        },
    )

    quantity_field_map = {
        "splice_case_quantity": (splice_case_label),
        "fusion_splice_quantity": ("C-109 - HO-1 Fusion Splice Quantity"),
        "ds_splitter_1x2_quantity": ("C-110 - DS Splitter Add - 1x2"),
        "ds_splitter_1x4_quantity": ("C-110 - DS Splitter Add - 1x4"),
        "ds_splitter_1x8_quantity": ("C-110 - DS Splitter Add - 1x8"),
        "ds_splitter_1x16_quantity": ("C-110 - DS Splitter Add - 1x16"),
    }

    if isinstance(
        quantities,
        dict,
    ):
        for (
            quantity_key,
            smartsheet_label,
        ) in quantity_field_map.items():

            # -------------------------------------------------
            # C-108
            #
            # Mientras splice_case_quantity no venga informado
            # en Billing, utiliza cero.
            # -------------------------------------------------

            if quantity_key == "splice_case_quantity":
                quantity = quantities.get(
                    quantity_key,
                    0,
                )

            else:
                quantity = quantities.get(
                    quantity_key,
                )

            if quantity in (
                None,
                "",
            ):
                continue

            appeared = await _wait_for_question(
                page,
                [
                    smartsheet_label,
                ],
                timeout=10_000,
                required=False,
            )

            if not appeared:
                logger.warning(
                    "Quantity field did not appear: %s",
                    smartsheet_label,
                )

                continue

            field_filled = await _fill_text_field(
                page,
                labels=[
                    smartsheet_label,
                ],
                value=quantity,
                required=False,
            )

            if field_filled:
                filled[f"quantity:{quantity_key}"] = quantity

                print(
                    "QUANTITY FILLED:",
                    {
                        "key": quantity_key,
                        "label": smartsheet_label,
                        "value": quantity,
                    },
                )

                await _wait_after_change(
                    page,
                    500,
                )

    # ========================================================
    # 13. Send me a copy of my responses
    # ========================================================

    if values["send_copy_of_responses"]:
        checked = await _set_checkbox_or_radio(
            page,
            labels=[
                "Send me a copy of my responses",
            ],
            checked=True,
            required=True,
        )

        filled["send_copy_of_responses"] = checked

        if checked:
            await _wait_after_change(
                page,
                1000,
            )

            # =================================================
            # 14. Email address
            # =================================================

            copy_email = _clean(
                values["copy_email"],
            ) or _clean(
                values["submitted_by_email"],
            )

            if not copy_email:
                raise SmartsheetAutomationError(
                    "Send me a copy of my responses is enabled "
                    "but no copy email is configured."
                )

            await _wait_for_question(
                page,
                [
                    "Email address",
                    "Email Address",
                ],
                timeout=10_000,
                required=True,
            )

            email_filled = await _fill_text_field(
                page,
                labels=[
                    "Email address",
                    "Email Address",
                ],
                value=copy_email,
                required=True,
            )

            if email_filled:
                filled["copy_email"] = copy_email

                print(
                    "COPY EMAIL FILLED:",
                    repr(
                        copy_email,
                    ),
                )

                await _wait_after_change(
                    page,
                    700,
                )

    return filled


# ============================================================
# Debug del estado actual del formulario
# ============================================================


async def _debug_form_state(
    page: Page,
    title: str,
):
    """
    Muestra el estado actual del formulario.

    Útil porque Smartsheet agrega campos progresivamente.
    """

    try:
        labels_debug = await page.locator("label").all_inner_texts()

        inputs_debug = await page.locator(
            "input, textarea, select, [role='combobox']"
        ).evaluate_all(
            """
            elements => elements.map((el, index) => ({
                index: index,
                tag: el.tagName || '',
                type: el.type || '',
                name: el.name || '',
                id: el.id || '',
                placeholder: el.placeholder || '',
                ariaLabel:
                    el.getAttribute('aria-label') || '',
                ariaLabelledBy:
                    el.getAttribute('aria-labelledby') || '',
                role:
                    el.getAttribute('role') || '',
                value:
                    el.value || ''
            }))
            """
        )

        body_text = await page.locator("body").inner_text()

        print("\n")
        print("=" * 80)
        print(title)
        print("=" * 80)

        print("\n===== LABELS =====")

        for index, label_text in enumerate(
            labels_debug,
            start=1,
        ):
            print(f"{index}: {label_text!r}")

        print("\n===== CONTROLS =====")

        for item in inputs_debug:
            print(item)

        print("\n===== VISIBLE TEXT SAMPLE =====")

        print(body_text[:20_000])

        print("=" * 80)

        print(f"END {title}")

        print("=" * 80)

        print("\n")

    except Exception:
        logger.exception("Could not inspect current Smartsheet form state.")


async def _submit_smartsheet_form(
    page: Page,
) -> dict[str, Any]:
    """
    Presiona Submit una sola vez y espera una confirmación
    real de Smartsheet.

    No considera exitoso únicamente el clic.

    El envío se confirma cuando ocurre al menos una de estas
    condiciones:

    - Aparece un mensaje de agradecimiento o recepción.
    - Aparece un mensaje indicando que la respuesta fue enviada.
    - El formulario desaparece después del clic.
    - El botón Submit desaparece y aparece una pantalla distinta.

    Devuelve información de confirmación del navegador.
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

    original_url = page.url

    original_form_count = await page.locator(
        'form[aria-label*="questions in this form" i]'
    ).count()

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

    success_markers = [
        "thank you",
        "your response has been recorded",
        "your response was submitted",
        "your response has been submitted",
        "response submitted",
        "submission received",
        "successfully submitted",
        "form submitted",
    ]

    error_markers = [
        "please complete all required fields",
        "please fill out this field",
        "required field",
        "there was an error submitting",
        "could not submit",
        "submission failed",
        "please try again",
        "exceeds the max file size",
        "file is too large",
    ]

    confirmation_received = False

    confirmation_text = ""

    confirmation_reference = ""

    final_body_text = ""

    final_form_count = original_form_count

    submit_button_visible = True

    timeout_ms = 90_000

    poll_interval_ms = 1000

    elapsed_ms = 0

    while elapsed_ms < timeout_ms:
        await page.wait_for_timeout(
            poll_interval_ms,
        )

        elapsed_ms += poll_interval_ms

        try:
            final_body_text = await page.locator(
                "body",
            ).inner_text(
                timeout=5000,
            )

        except Exception:
            final_body_text = ""

        normalized_body = final_body_text.lower()

        detected_error = next(
            (marker for marker in error_markers if marker in normalized_body),
            None,
        )

        if detected_error:
            raise SmartsheetAutomationError(
                "Smartsheet showed an error after Submit. "
                f"Detected message: {detected_error!r}. "
                f"Visible body: {final_body_text[:5000]!r}"
            )

        detected_success = next(
            (marker for marker in success_markers if marker in normalized_body),
            None,
        )

        if detected_success:
            confirmation_received = True

            confirmation_text = final_body_text[:5000]

            confirmation_reference = detected_success

            break

        try:
            final_form_count = await page.locator(
                'form[aria-label*="questions in this form" i]'
            ).count()

        except Exception:
            final_form_count = 0

        try:
            submit_button_visible = await submit_button.is_visible()

        except Exception:
            submit_button_visible = False

        if (
            original_form_count > 0
            and final_form_count == 0
            and not submit_button_visible
        ):
            confirmation_received = True

            confirmation_text = final_body_text[:5000]

            confirmation_reference = "form_disappeared_after_submit"

            break

        if page.url != original_url and not submit_button_visible:
            confirmation_received = True

            confirmation_text = final_body_text[:5000]

            confirmation_reference = "url_changed_after_submit"

            break

        if await _detect_verification_challenge(
            page,
        ):
            return {
                "submit_clicked": True,
                "verification_required": True,
                "browser_confirmation_received": False,
                "confirmation_reference": "",
                "confirmation_text": final_body_text[:5000],
                "final_url": page.url,
                "submitted_at": submit_clicked_at.isoformat(),
            }

    if not confirmation_received:
        raise SmartsheetAutomationError(
            "Submit was clicked, but Smartsheet did not show "
            "a reliable browser confirmation within 90 seconds. "
            f"Current URL: {page.url!r}. "
            f"Form count: {final_form_count}. "
            f"Submit visible: {submit_button_visible}. "
            f"Visible body: {final_body_text[:5000]!r}"
        )

    print(
        "SMARTSHEET BROWSER CONFIRMATION RECEIVED:",
        {
            "final_url": page.url,
            "confirmation_reference": confirmation_reference,
            "confirmation_text": confirmation_text[:1000],
        },
    )

    return {
        "submit_clicked": True,
        "verification_required": False,
        "browser_confirmation_received": True,
        "confirmation_reference": confirmation_reference,
        "confirmation_text": confirmation_text,
        "final_url": page.url,
        "submitted_at": submit_clicked_at.isoformat(),
    }


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

    Dry Run:
        Completa todo pero no presiona Submit.

    Live:
        Completa todo, presiona Submit y espera una
        confirmación real del navegador.

    Cuando submit_form es None, utiliza el modo del Batch.
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
        str(path)
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
        f"submission_" f"{submission.pk}_" f"{submission.public_id}.png"
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
                "project_id": submission.project_id,
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
                    "max_attempts": navigation_attempts,
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
                        "Smartsheet returned temporary HTTP "
                        f"{last_http_status}. "
                        f"Visible body: {last_body_text[:1000]!r}"
                    )

                    try:
                        http_error_path = SCREENSHOT_DIR / (
                            f"submission_"
                            f"{submission.pk}_"
                            f"http_error_attempt_"
                            f"{navigation_attempt}.png"
                        )

                        await page.screenshot(
                            path=str(
                                http_error_path.resolve(),
                            ),
                            full_page=True,
                        )

                    except Exception:
                        pass

                    if navigation_attempt < navigation_attempts:
                        retry_delay_ms = min(
                            navigation_attempt * 5000,
                            20_000,
                        )

                        await page.wait_for_timeout(
                            retry_delay_ms,
                        )

                        continue

                    break

                if last_http_status is not None and last_http_status >= 400:
                    navigation_error = SmartsheetFormLoadError(
                        "Smartsheet returned HTTP "
                        f"{last_http_status}. "
                        f"Visible body: {last_body_text[:1000]!r}"
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
                    return SmartsheetDryRunResult(
                        ok=False,
                        final_url=page.url,
                        page_title=(await page.title()),
                        verification_required=True,
                        submit_clicked=False,
                        browser_confirmation_received=False,
                        metadata={
                            "stage": "form_load",
                            "execution_mode": execution_mode,
                            "navigation_attempt": (navigation_attempt),
                            "http_status": last_http_status,
                        },
                    )

                form_locator = page.locator(
                    'form[aria-label*="questions in this form" i]'
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
                            "attempt": navigation_attempt,
                            "status": last_http_status,
                            "url": page.url,
                        },
                    )

                    break

                try:
                    last_body_text = await page.locator(
                        "body",
                    ).inner_text(
                        timeout=10_000,
                    )

                except Exception:
                    last_body_text = ""

                try:
                    failed_load_path = SCREENSHOT_DIR / (
                        f"submission_"
                        f"{submission.pk}_"
                        f"load_attempt_"
                        f"{navigation_attempt}.png"
                    )

                    await page.screenshot(
                        path=str(
                            failed_load_path.resolve(),
                        ),
                        full_page=True,
                    )

                except Exception:
                    pass

                if navigation_attempt < navigation_attempts:
                    retry_delay_ms = min(
                        navigation_attempt * 5000,
                        20_000,
                    )

                    await page.wait_for_timeout(
                        retry_delay_ms,
                    )

                    continue

                break

            except Exception as exc:
                navigation_error = exc

                try:
                    last_body_text = await page.locator(
                        "body",
                    ).inner_text(
                        timeout=5000,
                    )

                except Exception:
                    last_body_text = ""

                try:
                    exception_path = SCREENSHOT_DIR / (
                        f"submission_"
                        f"{submission.pk}_"
                        f"navigation_exception_"
                        f"{navigation_attempt}.png"
                    )

                    await page.screenshot(
                        path=str(
                            exception_path.resolve(),
                        ),
                        full_page=True,
                    )

                except Exception:
                    pass

                if navigation_attempt >= navigation_attempts:
                    break

                retry_delay_ms = min(
                    navigation_attempt * 5000,
                    20_000,
                )

                await page.wait_for_timeout(
                    retry_delay_ms,
                )

        if not form_loaded:
            raise SmartsheetFormLoadError(
                "Could not load the Smartsheet form after "
                f"{navigation_attempts} attempts. "
                f"Last HTTP status: {last_http_status!r}. "
                f"Current URL: {page.url!r}. "
                f"Visible body: {last_body_text[:1000]!r}. "
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
                f"submission_" f"{submission.pk}_" "form_loaded.png"
            )

            await page.screenshot(
                path=str(
                    initial_screenshot_path.resolve(),
                ),
                full_page=True,
            )

        except Exception as exc:
            print(
                "COULD NOT SAVE INITIAL FORM SCREENSHOT:",
                str(
                    exc,
                ),
            )

        if await _detect_verification_challenge(
            page,
        ):
            return SmartsheetDryRunResult(
                ok=False,
                final_url=page.url,
                page_title=(await page.title()),
                verification_required=True,
                submit_clicked=False,
                browser_confirmation_received=False,
                metadata={
                    "stage": "before_form_fill",
                    "execution_mode": execution_mode,
                    "navigation_attempt": (navigation_attempt),
                    "http_status": last_http_status,
                },
            )

        await _debug_form_state(
            page,
            "SMARTSHEET FORM INITIAL STATE",
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

            print(
                "SMARTSHEET ZIP PARTS TO UPLOAD:",
                {
                    "count": len(
                        attachment_paths,
                    ),
                    "filenames": [
                        Path(
                            path,
                        ).name
                        for path in attachment_paths
                    ],
                    "sizes": {
                        Path(path).name: (Path(path).stat().st_size)
                        for path in attachment_paths
                    },
                },
            )

            if file_input_count <= 0:
                raise SmartsheetAttachmentError(
                    "The attachment field did not appear " "in the Smartsheet form."
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
                f"Attached: {len(attachment_filenames)}."
            )

        try:
            email_label = page.get_by_text(
                "Email address",
                exact=False,
            ).last

            await email_label.wait_for(
                state="visible",
                timeout=10_000,
            )

            await email_label.scroll_into_view_if_needed()

            await page.wait_for_timeout(
                1000,
            )

        except Exception as exc:
            print(
                "COULD NOT SCROLL TO COPY EMAIL FIELD:",
                str(
                    exc,
                ),
            )

        await _debug_form_state(
            page,
            "SMARTSHEET FORM AFTER PROGRESSIVE FILL",
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

        verification_required = False

        if submit_form:
            submit_result = await _submit_smartsheet_form(
                page,
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

            verification_required = bool(
                submit_result.get(
                    "verification_required",
                    False,
                )
            )

            confirmation_screenshot_path = SCREENSHOT_DIR / (
                f"submission_"
                f"{submission.pk}_"
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

            if verification_required:
                return SmartsheetDryRunResult(
                    ok=False,
                    final_url=page.url,
                    page_title=(await page.title()),
                    screenshot_path=str(
                        screenshot_path.resolve(),
                    ),
                    fields_filled=fields_filled,
                    attachments_uploaded=(attachments_uploaded),
                    attachment_filenames=(attachment_filenames),
                    verification_required=True,
                    submit_clicked=submit_clicked,
                    browser_confirmation_received=False,
                    confirmation_reference="",
                    confirmation_text=confirmation_text,
                    metadata={
                        "execution_mode": execution_mode,
                        "submit_clicked": submit_clicked,
                        "browser_confirmation_received": False,
                        "confirmation_reference": "",
                        "confirmation_text": confirmation_text,
                        "stage": "after_submit",
                        "navigation_attempts": (navigation_attempt),
                        "http_status": last_http_status,
                        "headless": headless,
                        "attachment_count": len(
                            attachment_filenames,
                        ),
                    },
                )

            if not browser_confirmation_received:
                raise SmartsheetAutomationError(
                    "Smartsheet Submit was clicked, but no "
                    "browser confirmation was received."
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
            attachments_uploaded=attachments_uploaded,
            attachment_filenames=attachment_filenames,
            submit_clicked=submit_clicked,
            browser_confirmation_received=(browser_confirmation_received),
            confirmation_reference=confirmation_reference,
            confirmation_text=confirmation_text,
        )

        return SmartsheetDryRunResult(
            ok=True,
            final_url=page.url,
            page_title=(await page.title()),
            screenshot_path=str(
                screenshot_path.resolve(),
            ),
            html_snapshot=html_snapshot,
            fields_filled=fields_filled,
            attachments_uploaded=attachments_uploaded,
            attachment_filenames=attachment_filenames,
            verification_required=False,
            submit_clicked=submit_clicked,
            browser_confirmation_received=(browser_confirmation_received),
            confirmation_reference=confirmation_reference,
            confirmation_text=confirmation_text,
            metadata={
                "execution_mode": execution_mode,
                "submit_clicked": submit_clicked,
                "browser_confirmation_received": (browser_confirmation_received),
                "confirmation_reference": (confirmation_reference),
                "confirmation_text": confirmation_text,
                "progressive_form": True,
                "navigation_attempts": navigation_attempt,
                "http_status": last_http_status,
                "headless": headless,
                "attachment_count": len(
                    attachment_filenames,
                ),
            },
        )

    finally:
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
