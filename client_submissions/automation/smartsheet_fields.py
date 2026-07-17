# client_submissions/automation/smartsheet_fields.py

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from playwright.async_api import Locator, Page

from .smartsheet_state import (SmartsheetAttachmentError,
                               SmartsheetAutomationError,
                               SmartsheetFieldNotFoundError)

logger = logging.getLogger(__name__)


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
    Normaliza el Access Point ID dejando solamente números.
    """

    text = _clean(
        value,
    )

    return "".join(character for character in text if character.isdigit())


def _work_types_from_batch(
    batch,
) -> list[str]:
    """
    Compatibilidad con configuraciones anteriores donde los
    Work Types se obtenían directamente desde el Batch.
    """

    work_types: list[str] = []

    if batch.fiber_placed:
        work_types.append(
            "Fiber Placed",
        )

    if batch.splicing:
        work_types.append(
            "Splicing",
        )

    if batch.testing:
        work_types.append(
            "Testing",
        )

    if batch.aerial_case:
        work_types.append(
            "Aerial Case",
        )

    if batch.re_entry:
        work_types.append(
            "Re-Entry",
        )

    return work_types


async def _first_visible(
    locator: Locator,
):
    """
    Devuelve el primer elemento visible dentro de un Locator.
    """

    try:
        count = await locator.count()

    except Exception:
        return None

    for index in range(
        count,
    ):
        candidate = locator.nth(
            index,
        )

        try:
            if await candidate.is_visible():
                return candidate

        except Exception:
            continue

    return None


# ============================================================
# Esperar preguntas progresivas
# ============================================================


async def _wait_for_question(
    page: Page,
    labels: list[str],
    *,
    timeout: int = 15_000,
    required: bool = True,
) -> bool:
    """
    Espera hasta que una pregunta aparezca de forma visible.

    El formulario Smartsheet es progresivo, por lo que varias
    preguntas aparecen solamente después de responder la
    pregunta anterior.
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
    Da tiempo a Smartsheet para renderizar las preguntas
    condicionales después de cambiar un campo.
    """

    await page.wait_for_timeout(
        milliseconds,
    )


# ============================================================
# Localización de controles
# ============================================================


async def _find_input_by_label(
    page: Page,
    labels: list[str],
):
    """
    Busca un input, textarea o combobox relacionado con alguno
    de los textos recibidos.

    Intenta varios métodos porque el HTML de Smartsheet puede
    variar según el tipo de pregunta.
    """

    for raw_label in labels:
        label_text = _clean(
            raw_label,
        )

        if not label_text:
            continue

        # ====================================================
        # 1. Asociación accesible por label
        # ====================================================

        try:
            locator = page.get_by_label(
                label_text,
                exact=False,
            )

            candidate = await _first_visible(
                locator,
            )

            if candidate is not None:
                return candidate

        except Exception:
            pass

        normalized = label_text.lower().strip()

        # ====================================================
        # 2. Fallback especial para Submitted by
        # ====================================================

        if "submitted by" in normalized:
            try:
                candidate = await _first_visible(page.locator('input[type="email"]'))

                if candidate is not None:
                    return candidate

            except Exception:
                pass

        # ====================================================
        # 3. Fallback especial para fecha
        # ====================================================

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
        # 4. Buscar el texto de la pregunta y sus controles
        # ====================================================

        question_candidates = []

        try:
            exact_locator = page.get_by_text(
                label_text,
                exact=True,
            )

            exact_count = await exact_locator.count()

            for index in range(
                exact_count,
            ):
                question_candidates.append(
                    exact_locator.nth(
                        index,
                    )
                )

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
                question_candidates.append(
                    approximate_locator.nth(
                        index,
                    )
                )

        except Exception:
            pass

        for question in question_candidates:
            try:
                if not await question.is_visible():
                    continue

            except Exception:
                continue

            for ancestor_level in range(
                1,
                4,
            ):
                try:
                    xpath = "xpath=" + "/.." * ancestor_level

                    container = question.locator(
                        xpath,
                    )

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

                    if control_count == 1:
                        candidate = controls.first

                        try:
                            if await candidate.is_visible():
                                return candidate

                        except Exception:
                            continue

                    question_box = await question.bounding_box()

                    if not question_box:
                        continue

                    best_candidate = None
                    best_distance = None

                    for control_index in range(
                        control_count,
                    ):
                        candidate = controls.nth(
                            control_index,
                        )

                        try:
                            if not await candidate.is_visible():
                                continue

                            control_box = await candidate.bounding_box()

                            if not control_box:
                                continue

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
        # 5. Buscar por placeholder
        # ====================================================

        try:
            locator = page.get_by_placeholder(
                label_text,
                exact=False,
            )

            candidate = await _first_visible(
                locator,
            )

            if candidate is not None:
                return candidate

        except Exception:
            pass

    return None


# ============================================================
# Campo de texto
# ============================================================


async def _fill_text_field(
    page: Page,
    *,
    labels: list[str],
    value: Any,
    required: bool = True,
) -> bool:
    """
    Llena un campo de texto normal.
    """

    text = _clean(
        value,
    )

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

        await locator.fill(
            text,
        )

    except Exception as exc:
        if required:
            raise SmartsheetAutomationError(
                f"Could not fill field " f"'{labels[0]}': {exc}"
            ) from exc

        logger.warning(
            "Optional field could not be filled: " "%s. Error: %s",
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
    Selecciona un checkbox o un control con role=checkbox.

    Esta función se utiliza principalmente para:

    - Sub Contractor
    - Work Types
    - Send me a copy of my responses
    """

    if not checked:
        return False

    last_error = None

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

            for index in range(
                count,
            ):
                control = candidate.nth(
                    index,
                )

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
        # 1. Role exacto
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
        # 2. Role aproximado
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
        # 3. Asociación accesible
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
        # 4. Elemento label con atributo for
        # ====================================================

        try:
            label_locators = page.locator(
                "label",
            ).filter(
                has_text=label_text,
            )

            label_count = await label_locators.count()

            for index in range(
                label_count,
            ):
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
        # 5. Localizar contenedor real de la pregunta
        # ====================================================

        question_candidates = []

        try:
            exact_questions = page.get_by_text(
                label_text,
                exact=True,
            )

            exact_count = await exact_questions.count()

            for index in range(
                exact_count,
            ):
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
                    "xpath=ancestor::div" "[@data-field-name][1]"
                )

                if await field_container.count() > 0:
                    checkbox = field_container.locator('input[type="checkbox"]')

                    if await try_check(
                        checkbox,
                        method=("data_field_name_container"),
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
        # 6. Buscar checkbox cercano visualmente
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
                                method=(
                                    "near_question_" f"ancestor_" f"{ancestor_level}"
                                ),
                                label_text=label_text,
                            ):
                                return True

                    except Exception as exc:
                        last_error = exc
                        continue

            except Exception as exc:
                last_error = exc
                continue

    if required:
        raise SmartsheetFieldNotFoundError(
            (
                "Checkbox/radio not found or could not "
                f"be checked: {labels[0]}. "
                f"Last error: {last_error}"
            )
        )

    logger.warning(
        "Optional checkbox/radio could not be checked: " "%s. Last error: %s",
        labels,
        last_error,
    )

    return False


# ============================================================
# Radio YES / NO
# ============================================================


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

    - Aerial Case
    - Re-Entry
    """

    answer_value = "YES" if answer else "NO"

    question = None

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

    field_container = question.locator("xpath=ancestor::div" "[@data-field-name][1]")

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

    radio = field_container.locator(f'input[type="radio"]' f'[value="{answer_value}"]')

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
                f"{question_labels[0]} "
                f"did not remain checked as "
                f"{answer_value}."
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
            "Optional radio question could not be " "completed: %s. Error: %s",
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
    """

    text = _clean(
        value,
    )

    if not text:
        if required:
            raise SmartsheetAutomationError(
                f"Empty value for required combobox: " f"{labels[0]}"
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

        try:
            await locator.fill(
                text,
            )

        except Exception:
            await locator.press(
                "Control+A",
            )

            await locator.type(
                text,
                delay=50,
            )

        await page.wait_for_timeout(
            700,
        )

        option = None

        try:
            exact_option = page.get_by_text(
                text,
                exact=True,
            )

            option = await _first_visible(
                exact_option,
            )

        except Exception:
            option = None

        if option is None:
            try:
                approximate_option = page.get_by_text(
                    text,
                    exact=False,
                )

                option = await _first_visible(
                    approximate_option,
                )

            except Exception:
                option = None

        if option is not None:
            try:
                tag_name = await option.evaluate("el => el.tagName")

                if str(
                    tag_name,
                ).upper() not in {
                    "INPUT",
                    "TEXTAREA",
                }:
                    await option.click()

                    await page.wait_for_timeout(
                        700,
                    )

                    return True

            except Exception:
                pass

        await locator.press(
            "Enter",
        )

        await page.wait_for_timeout(
            700,
        )

        return True

    except Exception as exc:
        if required:
            raise SmartsheetAutomationError(
                f"Could not select '{text}' in " f"'{labels[0]}': {exc}"
            ) from exc

        logger.warning(
            "Optional combobox could not be completed: " "%s. Error: %s",
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
    real entre label.for e input.id.
    """

    if not value:
        if required:
            raise SmartsheetAutomationError("Production Completed Date has no value.")

        return False

    if hasattr(
        value,
        "strftime",
    ):
        date_value = value.strftime("%m/%d/%Y")

    else:
        raw_date_value = _clean(
            value,
        )

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
            "original": repr(
                value,
            ),
            "smartsheet_value": repr(
                date_value,
            ),
        },
    )

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

    label_locator = None

    for label_text in labels:
        try:
            labels_found = page.locator(
                "label",
            ).filter(
                has_text=label_text,
            )

            count = await labels_found.count()

            for index in range(
                count,
            ):
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

    if label_locator is None:
        if required:
            raise SmartsheetFieldNotFoundError(
                "Production Completed Date label " "could not be located."
            )

        return False

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
                f"#{input_id} did not become visible: "
                f"{exc}"
            ) from exc

        return False

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
            repr(
                current_value,
            ),
        )

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
                repr(
                    current_value,
                ),
            )

        if not current_value:
            raise SmartsheetAutomationError(
                "Production Completed Date input " "remained empty after fill."
            )

        await locator.press(
            "Tab",
        )

        await page.wait_for_timeout(
            1500,
        )

        print(
            "PRODUCTION COMPLETED DATE FILLED:",
            repr(
                current_value,
            ),
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
            "Production Completed Date could not " "be filled: %s",
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
    Adjunta uno o varios ZIP al campo de archivos de
    Smartsheet.
    """

    normalized_paths: list[Path] = []

    for raw_path in file_paths:
        path = Path(
            raw_path,
        )

        if not path.exists():
            raise SmartsheetAttachmentError(
                f"Attachment file does not exist: " f"{path}"
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

    if (
        len(
            normalized_paths,
        )
        > 10
    ):
        raise SmartsheetAttachmentError(
            (
                "Smartsheet attachment limit exceeded. "
                f"Received {len(normalized_paths)} ZIP "
                "files; maximum allowed by this "
                "automation is 10."
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
            await locator.set_input_files(
                [
                    str(
                        path,
                    )
                    for path in normalized_paths
                ]
            )

            await page.wait_for_timeout(
                3000,
            )

            uploaded_filenames = [path.name for path in normalized_paths]

            try:
                body_text = await page.locator(
                    "body",
                ).inner_text()

            except Exception:
                body_text = ""

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
                        f"Visible error: "
                        f"{detected_size_error}. "
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
                        path.name: (path.stat().st_size) for path in normalized_paths
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
        "Could not upload Smartsheet " f"attachments: {last_error}"
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
            "splice_case_code": (splice_case_code),
            "splice_case_label": (splice_case_label),
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

    return {
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
        "production_completed_date": (
            payload.get(
                "production_completed_date",
                batch.production_completed_date,
            )
        ),
        "market": _clean(
            payload.get(
                "market",
                batch.market,
            )
        ),
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
        "access_point_id": (
            _normalize_access_point_id(
                payload.get(
                    "access_point_id",
                    submission.access_point_id,
                )
            )
        ),
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
        "aerial_case_value_1": (aerial_sequential_in),
        "aerial_case_value_2": (aerial_sequential_out),
        "aerial_sequential_in": (aerial_sequential_in),
        "aerial_sequential_out": (aerial_sequential_out),
        "splice_case_code": (splice_case_code),
        "splice_case_label": (splice_case_label),
        "splice_case_quantity": (splice_case_quantity),
        "quantities": quantities,
        "payload": payload,
    }


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
    """

    values = build_form_values(
        submission,
        batch,
    )

    filled: dict[str, Any] = {}

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
            "aerial_sequential_in": values["aerial_case_value_1"],
            "aerial_sequential_out": values["aerial_case_value_2"],
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
                        "label": (smartsheet_label),
                        "value": quantity,
                    },
                )

                await _wait_after_change(
                    page,
                    500,
                )

    # ========================================================
    # 13. Send me a copy
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

            copy_email = _clean(
                values["copy_email"],
            ) or _clean(
                values["submitted_by_email"],
            )

            if not copy_email:
                raise SmartsheetAutomationError(
                    "Send me a copy of my responses "
                    "is enabled but no copy email "
                    "is configured."
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
