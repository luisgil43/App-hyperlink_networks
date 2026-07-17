# client_submissions/automation/smartsheet_form.py

"""
Fachada de compatibilidad para la automatización de Smartsheet.

El código fue dividido en varios módulos para reducir el tamaño
del archivo original, pero las importaciones existentes pueden
seguir realizándose desde:

    client_submissions.automation.smartsheet_form
"""

from .smartsheet_fields import (_clean, _fill_combobox, _fill_date_field,
                                _fill_text_field, _find_input_by_label,
                                _first_visible, _normalize_access_point_id,
                                _set_checkbox_or_radio, _set_radio_answer,
                                _upload_attachments, _wait_after_change,
                                _wait_for_question, _work_types_from_batch,
                                build_form_values, fill_smartsheet_form)
from .smartsheet_runner import (_submit_smartsheet_form,
                                run_smartsheet_dry_run, run_smartsheet_live)
from .smartsheet_state import (ACTIVE_BROWSERS, DEFAULT_TIMEOUT_MS,
                               SCREENSHOT_DIR, SmartsheetAttachmentError,
                               SmartsheetAutomationError,
                               SmartsheetDryRunResult,
                               SmartsheetFieldNotFoundError,
                               SmartsheetFormLoadError,
                               SmartsheetVerificationRequired,
                               _detect_verification_challenge,
                               _get_human_verification_state,
                               _load_batch_for_submission,
                               _mark_browser_confirmed, _mark_form_completed,
                               _mark_form_loaded, _mark_submit_clicked,
                               _mark_verification_completed,
                               _mark_verification_required,
                               _update_human_verification_state,
                               _wait_for_human_verification,
                               close_active_browser, get_active_browser,
                               register_active_browser, remove_active_browser)

__all__ = [
    # ========================================================
    # Ejecución pública
    # ========================================================
    "run_smartsheet_dry_run",
    "run_smartsheet_live",
    # ========================================================
    # Resultado
    # ========================================================
    "SmartsheetDryRunResult",
    # ========================================================
    # Excepciones
    # ========================================================
    "SmartsheetAutomationError",
    "SmartsheetFormLoadError",
    "SmartsheetFieldNotFoundError",
    "SmartsheetAttachmentError",
    "SmartsheetVerificationRequired",
    # ========================================================
    # Navegadores activos
    # ========================================================
    "ACTIVE_BROWSERS",
    "register_active_browser",
    "get_active_browser",
    "remove_active_browser",
    "close_active_browser",
    # ========================================================
    # Configuración
    # ========================================================
    "DEFAULT_TIMEOUT_MS",
    "SCREENSHOT_DIR",
    # ========================================================
    # Construcción y llenado
    # ========================================================
    "build_form_values",
    "fill_smartsheet_form",
]
