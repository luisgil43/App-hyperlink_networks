# client_submission_remote/services.py

from __future__ import annotations

import logging
import os
import socket
import time
import uuid
from datetime import timedelta
from io import BytesIO
from typing import Any

from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.db import (IntegrityError, OperationalError, close_old_connections,
                       transaction)
from django.db.models import F
from django.utils import timezone
from playwright.async_api import Page

from client_submission_remote.models import (RemoteBrowserAction,
                                             RemoteBrowserSession)

logger = logging.getLogger(__name__)


# ============================================================
# Configuración
# ============================================================


REMOTE_BROWSER_SESSION_MINUTES = 35

REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH = 1440

REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT = 1100

REMOTE_BROWSER_MAX_CAPTCHA_ROUNDS = 10

REMOTE_BROWSER_SCREENSHOT_TYPE = "png"

REMOTE_BROWSER_ACTION_DELAY_MS = 700

REMOTE_BROWSER_MULTI_CLICK_DELAY_MS = 450

REMOTE_BROWSER_MAX_PENDING_ACTIONS = 100


# ============================================================
# Worker
# ============================================================


def get_remote_worker_identifier() -> str:
    """
    Devuelve un identificador estable del worker actual.

    En Render utiliza RENDER_INSTANCE_ID o HOSTNAME.
    En local utiliza el hostname del equipo.
    """

    return (
        os.getenv("RENDER_INSTANCE_ID")
        or os.getenv("HOSTNAME")
        or socket.gethostname()
        or "client-submission-remote-worker"
    )


# ============================================================
# Helpers de valores
# ============================================================


def _safe_int(
    value,
    *,
    default: int = 0,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(
            value,
        )

    except (
        TypeError,
        ValueError,
    ):
        parsed = default

    if minimum is not None:
        parsed = max(
            minimum,
            parsed,
        )

    if maximum is not None:
        parsed = min(
            maximum,
            parsed,
        )

    return parsed


def _safe_float(
    value,
    *,
    default: float = 0.0,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        parsed = float(
            value,
        )

    except (
        TypeError,
        ValueError,
    ):
        parsed = default

    if minimum is not None:
        parsed = max(
            minimum,
            parsed,
        )

    if maximum is not None:
        parsed = min(
            maximum,
            parsed,
        )

    return parsed


def _normalize_payload(
    payload,
) -> dict:
    if isinstance(
        payload,
        dict,
    ):
        return dict(
            payload,
        )

    return {}


# ============================================================
# Crear o recuperar sesión
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def get_or_create_remote_session(
    *,
    submission,
    attempt=None,
    stage: str,
    browser_session_key: str = "",
    viewport_width: int = REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
    viewport_height: int = REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
    metadata: dict | None = None,
) -> RemoteBrowserSession:
    """
    Recupera la sesión remota abierta del Submission o crea
    una nueva.

    No modifica ClientSubmission ni ClientSubmissionAttempt.

    La restricción parcial de PostgreSQL garantiza que exista
    una sola sesión abierta por Submission.
    """

    now = timezone.now()

    expires_at = now + timedelta(
        minutes=REMOTE_BROWSER_SESSION_MINUTES,
    )

    open_statuses = [
        RemoteBrowserSession.Status.STARTING,
        RemoteBrowserSession.Status.ACTIVE,
        RemoteBrowserSession.Status.PROCESSING_ACTION,
        RemoteBrowserSession.Status.WAITING_FOR_USER,
        RemoteBrowserSession.Status.RESTART_REQUESTED,
    ]

    worker_identifier = get_remote_worker_identifier()

    viewport_width = _safe_int(
        viewport_width,
        default=REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
        minimum=320,
        maximum=5000,
    )

    viewport_height = _safe_int(
        viewport_height,
        default=REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
        minimum=320,
        maximum=5000,
    )

    safe_metadata = (
        dict(
            metadata,
        )
        if isinstance(
            metadata,
            dict,
        )
        else {}
    )

    try:
        with transaction.atomic():
            session = (
                RemoteBrowserSession.objects.select_for_update()
                .filter(
                    submission_id=submission.pk,
                    status__in=open_statuses,
                )
                .order_by(
                    "-created_at",
                    "-id",
                )
                .first()
            )

            if session:
                session.attempt = attempt or session.attempt
                session.stage = str(
                    stage or session.stage or "",
                ).strip()
                session.worker_identifier = worker_identifier

                if browser_session_key:
                    session.browser_session_key = str(
                        browser_session_key,
                    ).strip()

                session.viewport_width = viewport_width
                session.viewport_height = viewport_height
                session.expires_at = expires_at
                session.last_worker_activity_at = now
                session.error_code = ""
                session.error_message = ""
                session.message = (
                    "The remote browser session is connected "
                    "to the active Playwright page."
                )

                current_metadata = (
                    dict(
                        session.metadata,
                    )
                    if isinstance(
                        session.metadata,
                        dict,
                    )
                    else {}
                )

                session.metadata = {
                    **current_metadata,
                    **safe_metadata,
                }

                session.save(
                    update_fields=[
                        "attempt",
                        "stage",
                        "worker_identifier",
                        "browser_session_key",
                        "viewport_width",
                        "viewport_height",
                        "expires_at",
                        "last_worker_activity_at",
                        "error_code",
                        "error_message",
                        "message",
                        "metadata",
                        "updated_at",
                    ]
                )

                return session

            session = RemoteBrowserSession.objects.create(
                submission=submission,
                attempt=attempt,
                status=RemoteBrowserSession.Status.STARTING,
                captcha_status=(RemoteBrowserSession.CaptchaStatus.CHALLENGE_ACTIVE),
                stage=str(
                    stage or "",
                ).strip(),
                worker_identifier=worker_identifier,
                browser_session_key=str(
                    browser_session_key or "",
                ).strip(),
                viewport_width=viewport_width,
                viewport_height=viewport_height,
                round_number=1,
                max_rounds=REMOTE_BROWSER_MAX_CAPTCHA_ROUNDS,
                message=("The remote browser session is starting."),
                metadata=safe_metadata,
                started_at=now,
                expires_at=expires_at,
                last_worker_activity_at=now,
            )

            return session

    except IntegrityError:
        session = (
            RemoteBrowserSession.objects.filter(
                submission_id=submission.pk,
                status__in=open_statuses,
            )
            .order_by(
                "-created_at",
                "-id",
            )
            .first()
        )

        if not session:
            raise

        return session


# ============================================================
# Obtener sesión
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def get_remote_session_by_id(
    session_id: int,
) -> RemoteBrowserSession | None:
    return (
        RemoteBrowserSession.objects.select_related(
            "submission",
            "attempt",
            "controller_user",
        )
        .filter(
            pk=session_id,
        )
        .first()
    )


# ============================================================
# Actualizar página
# ============================================================


async def inspect_remote_page(
    page: Page,
) -> dict[str, Any]:
    """
    Obtiene información visible y segura de la página actual.
    """

    if page.is_closed():
        return {
            "closed": True,
            "url": "",
            "title": "",
            "scroll_x": 0,
            "scroll_y": 0,
            "document_width": 0,
            "document_height": 0,
            "viewport_width": 0,
            "viewport_height": 0,
        }

    try:
        title = await page.title()

    except Exception:
        title = ""

    try:
        page_metrics = await page.evaluate("""
            () => {
                const documentElement = document.documentElement;
                const body = document.body;

                const documentWidth = Math.max(
                    documentElement ? documentElement.scrollWidth : 0,
                    documentElement ? documentElement.clientWidth : 0,
                    body ? body.scrollWidth : 0,
                    body ? body.clientWidth : 0
                );

                const documentHeight = Math.max(
                    documentElement ? documentElement.scrollHeight : 0,
                    documentElement ? documentElement.clientHeight : 0,
                    body ? body.scrollHeight : 0,
                    body ? body.clientHeight : 0
                );

                return {
                    scrollX: Math.round(window.scrollX || 0),
                    scrollY: Math.round(window.scrollY || 0),
                    documentWidth: Math.round(documentWidth || 0),
                    documentHeight: Math.round(documentHeight || 0),
                    viewportWidth: Math.round(window.innerWidth || 0),
                    viewportHeight: Math.round(window.innerHeight || 0)
                };
            }
            """)

    except Exception:
        page_metrics = {}

    viewport_size = page.viewport_size or {}

    return {
        "closed": False,
        "url": str(
            page.url or "",
        ).strip(),
        "title": str(
            title or "",
        ).strip(),
        "scroll_x": _safe_int(
            page_metrics.get(
                "scrollX",
                0,
            ),
            default=0,
        ),
        "scroll_y": _safe_int(
            page_metrics.get(
                "scrollY",
                0,
            ),
            default=0,
        ),
        "document_width": _safe_int(
            page_metrics.get(
                "documentWidth",
                0,
            ),
            default=0,
            minimum=0,
        ),
        "document_height": _safe_int(
            page_metrics.get(
                "documentHeight",
                0,
            ),
            default=0,
            minimum=0,
        ),
        "viewport_width": _safe_int(
            page_metrics.get(
                "viewportWidth",
                viewport_size.get(
                    "width",
                    REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
                ),
            ),
            default=REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
            minimum=320,
        ),
        "viewport_height": _safe_int(
            page_metrics.get(
                "viewportHeight",
                viewport_size.get(
                    "height",
                    REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
                ),
            ),
            default=REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
            minimum=320,
        ),
    }


# ============================================================
# Guardar captura
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def _persist_remote_screenshot(
    *,
    session_id: int,
    screenshot_bytes: bytes,
    page_state: dict,
    captcha_status: str,
    message: str,
) -> RemoteBrowserSession:
    """
    Guarda una captura remota evitando transacciones de lectura
    convertidas posteriormente en escritura sobre SQLite.

    Flujo:

    1. Lee únicamente los datos necesarios de la sesión.
    2. Sube la captura a Wasabi fuera de cualquier transacción.
    3. Actualiza la sesión mediante un único UPDATE directo.
    4. Incrementa screenshot_version de forma atómica con F().
    5. Reintenta cuando SQLite se encuentra temporalmente bloqueado.
    6. Elimina la captura anterior después de guardar la nueva.
    7. Elimina la nueva captura si la actualización de BD falla.
    """

    maximum_attempts = 10
    retry_delay_seconds = 0.35

    close_old_connections()

    initial_data = (
        RemoteBrowserSession.objects.filter(
            pk=session_id,
        )
        .values(
            "id",
            "submission_id",
            "public_id",
            "screenshot",
            "viewport_width",
            "viewport_height",
        )
        .first()
    )

    if not initial_data:
        raise RemoteBrowserSession.DoesNotExist(
            f"Remote browser session {session_id} does not exist."
        )

    field = RemoteBrowserSession._meta.get_field(
        "screenshot",
    )

    storage = field.storage

    old_screenshot_name = str(
        initial_data.get(
            "screenshot",
            "",
        )
        or ""
    ).strip()

    unique_filename = (
        f"submission_{initial_data['submission_id']}_"
        f"remote_{initial_data['public_id']}_"
        f"{uuid.uuid4().hex}.png"
    )

    temporary_session = RemoteBrowserSession(
        pk=session_id,
        submission_id=initial_data["submission_id"],
    )

    generated_name = field.generate_filename(
        temporary_session,
        unique_filename,
    )

    uploaded_name = ""

    try:
        uploaded_name = storage.save(
            generated_name,
            ContentFile(
                screenshot_bytes,
            ),
        )

    except Exception:
        logger.exception(
            "Could not upload remote browser screenshot. " "session=%s filename=%s",
            session_id,
            generated_name,
        )
        raise

    current_viewport_width = _safe_int(
        initial_data.get(
            "viewport_width",
            REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
        ),
        default=REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
        minimum=320,
    )

    current_viewport_height = _safe_int(
        initial_data.get(
            "viewport_height",
            REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
        ),
        default=REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
        minimum=320,
    )

    viewport_width = _safe_int(
        page_state.get(
            "viewport_width",
            current_viewport_width,
        ),
        default=current_viewport_width,
        minimum=320,
    )

    viewport_height = _safe_int(
        page_state.get(
            "viewport_height",
            current_viewport_height,
        ),
        default=current_viewport_height,
        minimum=320,
    )

    screenshot_width = _safe_int(
        page_state.get(
            "viewport_width",
            viewport_width,
        ),
        default=viewport_width,
        minimum=1,
    )

    screenshot_height = _safe_int(
        page_state.get(
            "viewport_height",
            viewport_height,
        ),
        default=viewport_height,
        minimum=1,
    )

    browser_url = str(
        page_state.get(
            "url",
            "",
        )
        or ""
    ).strip()

    browser_title = str(
        page_state.get(
            "title",
            "",
        )
        or ""
    ).strip()

    page_scroll_x = _safe_int(
        page_state.get(
            "scroll_x",
            0,
        ),
        default=0,
    )

    page_scroll_y = _safe_int(
        page_state.get(
            "scroll_y",
            0,
        ),
        default=0,
    )

    page_document_width = _safe_int(
        page_state.get(
            "document_width",
            0,
        ),
        default=0,
        minimum=0,
    )

    page_document_height = _safe_int(
        page_state.get(
            "document_height",
            0,
        ),
        default=0,
        minimum=0,
    )

    resolved_message = str(
        message or "",
    ).strip()

    last_error = None
    update_succeeded = False

    try:
        for attempt_number in range(
            1,
            maximum_attempts + 1,
        ):
            try:
                close_old_connections()

                now = timezone.now()

                updated_rows = RemoteBrowserSession.objects.filter(
                    pk=session_id,
                ).update(
                    screenshot=uploaded_name,
                    screenshot_version=F(
                        "screenshot_version",
                    )
                    + 1,
                    screenshot_width=screenshot_width,
                    screenshot_height=screenshot_height,
                    screenshot_captured_at=now,
                    screenshot_error="",
                    browser_url=browser_url,
                    browser_title=browser_title,
                    page_scroll_x=page_scroll_x,
                    page_scroll_y=page_scroll_y,
                    page_document_width=page_document_width,
                    page_document_height=page_document_height,
                    viewport_width=viewport_width,
                    viewport_height=viewport_height,
                    status=(RemoteBrowserSession.Status.WAITING_FOR_USER),
                    captcha_status=captcha_status,
                    message=resolved_message,
                    error_code="",
                    error_message="",
                    last_worker_activity_at=now,
                    updated_at=now,
                )

                if updated_rows != 1:
                    raise RemoteBrowserSession.DoesNotExist(
                        f"Remote browser session {session_id} " "no longer exists."
                    )

                update_succeeded = True
                break

            except OperationalError as exc:
                last_error = exc

                normalized_error = str(
                    exc,
                ).lower()

                is_database_locked = (
                    "database is locked" in normalized_error
                    or "database table is locked" in normalized_error
                    or "database schema is locked" in normalized_error
                )

                if not is_database_locked or attempt_number >= maximum_attempts:
                    raise

                logger.warning(
                    "SQLite database locked while updating remote "
                    "browser screenshot with direct UPDATE. "
                    "Retrying. session=%s attempt=%s/%s",
                    session_id,
                    attempt_number,
                    maximum_attempts,
                )

                close_old_connections()

                time.sleep(
                    retry_delay_seconds * attempt_number,
                )

        if not update_succeeded:
            if last_error:
                raise last_error

            raise RuntimeError(
                "The remote browser screenshot metadata " "could not be saved."
            )

    except Exception:
        if uploaded_name:
            try:
                storage.delete(
                    uploaded_name,
                )

            except Exception:
                logger.exception(
                    "Could not delete uploaded screenshot after "
                    "database update failure. session=%s file=%s",
                    session_id,
                    uploaded_name,
                )

        close_old_connections()
        raise

    try:
        saved_session = RemoteBrowserSession.objects.select_related(
            "submission",
        ).get(
            pk=session_id,
        )

    except Exception:
        logger.exception(
            "Screenshot was saved, but the refreshed remote "
            "browser session could not be loaded. session=%s",
            session_id,
        )
        raise

    if old_screenshot_name and old_screenshot_name != uploaded_name:
        try:
            storage.delete(
                old_screenshot_name,
            )

        except Exception:
            logger.exception(
                "Could not delete previous remote browser "
                "screenshot. session=%s file=%s",
                session_id,
                old_screenshot_name,
            )

    close_old_connections()

    return saved_session


@sync_to_async(
    thread_sensitive=True,
)
def _mark_screenshot_failed(
    *,
    session_id: int,
    error: Exception,
):
    session = RemoteBrowserSession.objects.filter(
        pk=session_id,
    ).first()

    if not session:
        return

    session.screenshot_error = str(
        error,
    )
    session.error_code = error.__class__.__name__
    session.error_message = str(
        error,
    )
    session.last_worker_activity_at = timezone.now()

    session.save(
        update_fields=[
            "screenshot_error",
            "error_code",
            "error_message",
            "last_worker_activity_at",
            "updated_at",
        ]
    )


async def capture_remote_screenshot(
    *,
    page: Page,
    session: RemoteBrowserSession,
    captcha_visible: bool,
    message: str = "",
) -> RemoteBrowserSession:
    """
    Captura únicamente el viewport visible.

    No utiliza full_page=True porque las coordenadas enviadas por
    el usuario deben coincidir exactamente con el viewport actual
    de Playwright.
    """

    try:
        if page.is_closed():
            raise RuntimeError("The Playwright page is closed.")

        page_state = await inspect_remote_page(
            page,
        )

        screenshot_bytes = await page.screenshot(
            type=REMOTE_BROWSER_SCREENSHOT_TYPE,
            full_page=False,
            animations="disabled",
            caret="hide",
        )

        captcha_status = (
            RemoteBrowserSession.CaptchaStatus.CHALLENGE_ACTIVE
            if captcha_visible
            else RemoteBrowserSession.CaptchaStatus.NOT_VISIBLE
        )

        resolved_message = str(
            message or "",
        ).strip()

        if not resolved_message:
            resolved_message = (
                "The CAPTCHA is visible and waiting for interaction."
                if captcha_visible
                else (
                    "The CAPTCHA is no longer visible. "
                    "The worker is validating the submission."
                )
            )

        return await _persist_remote_screenshot(
            session_id=session.pk,
            screenshot_bytes=screenshot_bytes,
            page_state=page_state,
            captcha_status=captcha_status,
            message=resolved_message,
        )

    except Exception as exc:
        await _mark_screenshot_failed(
            session_id=session.pk,
            error=exc,
        )

        raise


# ============================================================
# Reclamar acción
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def claim_next_remote_action(
    *,
    session_id: int,
) -> RemoteBrowserAction | None:
    """
    Reclama de forma segura una acción pendiente.

    select_for_update(skip_locked=True) evita que dos procesos
    ejecuten la misma acción.
    """

    with transaction.atomic():
        session = (
            RemoteBrowserSession.objects.select_for_update()
            .filter(
                pk=session_id,
            )
            .first()
        )

        if not session:
            return None

        if session.is_expired:
            session.status = RemoteBrowserSession.Status.EXPIRED
            session.closed_at = timezone.now()
            session.message = "The remote browser session expired."

            session.save(
                update_fields=[
                    "status",
                    "closed_at",
                    "message",
                    "updated_at",
                ]
            )

            RemoteBrowserAction.objects.filter(
                session=session,
                status=RemoteBrowserAction.Status.PENDING,
            ).update(
                status=RemoteBrowserAction.Status.CANCELLED,
                processed_at=timezone.now(),
                error_code="REMOTE_SESSION_EXPIRED",
                error_message=(
                    "The remote browser session expired "
                    "before the action was processed."
                ),
            )

            return None

        action = (
            RemoteBrowserAction.objects.select_for_update(
                skip_locked=True,
            )
            .filter(
                session=session,
                status=RemoteBrowserAction.Status.PENDING,
            )
            .order_by(
                "requested_at",
                "id",
            )
            .first()
        )

        if not action:
            return None

        action.status = RemoteBrowserAction.Status.PROCESSING
        action.processing_started_at = timezone.now()

        action.save(
            update_fields=[
                "status",
                "processing_started_at",
                "updated_at",
            ]
        )

        session.status = RemoteBrowserSession.Status.PROCESSING_ACTION
        session.last_action_type = action.action_type
        session.last_action_at = timezone.now()
        session.last_worker_activity_at = timezone.now()

        session.save(
            update_fields=[
                "status",
                "last_action_type",
                "last_action_at",
                "last_worker_activity_at",
                "updated_at",
            ]
        )

        return action


# ============================================================
# Estado de acción
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def mark_remote_action_completed(
    *,
    action_id: int,
    result: dict | None = None,
):
    with transaction.atomic():
        action = (
            RemoteBrowserAction.objects.select_for_update()
            .select_related(
                "session",
            )
            .get(
                pk=action_id,
            )
        )

        action.status = RemoteBrowserAction.Status.COMPLETED
        action.processed_at = timezone.now()
        action.result = (
            dict(
                result,
            )
            if isinstance(
                result,
                dict,
            )
            else {}
        )
        action.error_code = ""
        action.error_message = ""

        action.save(
            update_fields=[
                "status",
                "processed_at",
                "result",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

        session = action.session

        session.action_count = (
            int(
                session.action_count or 0,
            )
            + 1
        )
        session.last_action_type = action.action_type
        session.last_action_at = timezone.now()
        session.last_result = "completed"
        session.last_worker_activity_at = timezone.now()

        session.save(
            update_fields=[
                "action_count",
                "last_action_type",
                "last_action_at",
                "last_result",
                "last_worker_activity_at",
                "updated_at",
            ]
        )


@sync_to_async(
    thread_sensitive=True,
)
def mark_remote_action_failed(
    *,
    action_id: int,
    error: Exception,
):
    with transaction.atomic():
        action = (
            RemoteBrowserAction.objects.select_for_update()
            .select_related(
                "session",
            )
            .get(
                pk=action_id,
            )
        )

        action.status = RemoteBrowserAction.Status.FAILED
        action.processed_at = timezone.now()
        action.error_code = error.__class__.__name__
        action.error_message = str(
            error,
        )

        action.save(
            update_fields=[
                "status",
                "processed_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

        session = action.session

        session.status = RemoteBrowserSession.Status.WAITING_FOR_USER
        session.last_result = "failed"
        session.error_code = error.__class__.__name__
        session.error_message = str(
            error,
        )
        session.last_worker_activity_at = timezone.now()

        session.save(
            update_fields=[
                "status",
                "last_result",
                "error_code",
                "error_message",
                "last_worker_activity_at",
                "updated_at",
            ]
        )


# ============================================================
# Validación de captura
# ============================================================


def validate_action_screenshot_version(
    *,
    session: RemoteBrowserSession,
    action: RemoteBrowserAction,
):
    """
    Impide ejecutar clics sobre una captura antigua.

    Las acciones sin coordenadas, como refresh o cancel, pueden
    ejecutarse aunque la versión cambie.
    """

    coordinate_actions = {
        RemoteBrowserAction.ActionType.CLICK,
        RemoteBrowserAction.ActionType.DOUBLE_CLICK,
        RemoteBrowserAction.ActionType.MULTI_CLICK,
        RemoteBrowserAction.ActionType.VERIFY,
    }

    if action.action_type not in coordinate_actions:
        return

    expected_version = int(
        session.screenshot_version or 0,
    )

    requested_version = int(
        action.screenshot_version or 0,
    )

    if requested_version != expected_version:
        raise RuntimeError(
            "The browser screenshot changed before this action "
            "was processed. Refresh the console and click again. "
            f"Expected version: {expected_version}. "
            f"Received version: {requested_version}."
        )


# ============================================================
# Ejecutar acciones Playwright
# ============================================================


async def _execute_click(
    *,
    page: Page,
    payload: dict,
    click_count: int = 1,
):
    viewport = page.viewport_size or {}

    viewport_width = _safe_int(
        viewport.get(
            "width",
            REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
        ),
        default=REMOTE_BROWSER_DEFAULT_VIEWPORT_WIDTH,
        minimum=1,
    )

    viewport_height = _safe_int(
        viewport.get(
            "height",
            REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
        ),
        default=REMOTE_BROWSER_DEFAULT_VIEWPORT_HEIGHT,
        minimum=1,
    )

    x = _safe_float(
        payload.get(
            "x",
        ),
        minimum=0,
        maximum=max(
            viewport_width - 1,
            0,
        ),
    )

    y = _safe_float(
        payload.get(
            "y",
        ),
        minimum=0,
        maximum=max(
            viewport_height - 1,
            0,
        ),
    )

    await page.mouse.click(
        x,
        y,
        click_count=click_count,
        delay=80,
    )

    return {
        "x": x,
        "y": y,
        "click_count": click_count,
    }


async def _execute_multi_click(
    *,
    page: Page,
    payload: dict,
):
    points = payload.get(
        "points",
        [],
    )

    if (
        not isinstance(
            points,
            list,
        )
        or not points
    ):
        raise RuntimeError("The multiple click action has no points.")

    completed_points = []

    for point in points:
        if not isinstance(
            point,
            dict,
        ):
            raise RuntimeError("A multiple click point is invalid.")

        result = await _execute_click(
            page=page,
            payload=point,
            click_count=1,
        )

        completed_points.append(
            result,
        )

        await page.wait_for_timeout(
            REMOTE_BROWSER_MULTI_CLICK_DELAY_MS,
        )

    return {
        "points": completed_points,
        "count": len(
            completed_points,
        ),
    }


async def _execute_scroll(
    *,
    page: Page,
    payload: dict,
):
    delta_x = _safe_float(
        payload.get(
            "delta_x",
            0,
        ),
        default=0,
        minimum=-5000,
        maximum=5000,
    )

    delta_y = _safe_float(
        payload.get(
            "delta_y",
            0,
        ),
        default=0,
        minimum=-5000,
        maximum=5000,
    )

    if delta_x == 0 and delta_y == 0:
        raise RuntimeError("The scroll action has no movement.")

    await page.mouse.wheel(
        delta_x,
        delta_y,
    )

    return {
        "delta_x": delta_x,
        "delta_y": delta_y,
    }


async def execute_remote_action(
    *,
    page: Page,
    session: RemoteBrowserSession,
    action: RemoteBrowserAction,
) -> dict[str, Any]:
    """
    Ejecuta una acción permitida sobre la página Playwright.

    No acepta JavaScript, selectores arbitrarios ni navegación
    enviada desde la interfaz.
    """

    if page.is_closed():
        raise RuntimeError("The Playwright page is closed.")

    validate_action_screenshot_version(
        session=session,
        action=action,
    )

    payload = _normalize_payload(
        action.payload,
    )

    action_type = action.action_type

    if action_type == RemoteBrowserAction.ActionType.CLICK:
        result = await _execute_click(
            page=page,
            payload=payload,
            click_count=1,
        )

    elif action_type == RemoteBrowserAction.ActionType.DOUBLE_CLICK:
        result = await _execute_click(
            page=page,
            payload=payload,
            click_count=2,
        )

    elif action_type == RemoteBrowserAction.ActionType.MULTI_CLICK:
        result = await _execute_multi_click(
            page=page,
            payload=payload,
        )

    elif action_type == RemoteBrowserAction.ActionType.SCROLL:
        result = await _execute_scroll(
            page=page,
            payload=payload,
        )

    elif action_type == RemoteBrowserAction.ActionType.REFRESH_SCREENSHOT:
        result = {
            "refresh_requested": True,
        }

    elif action_type == RemoteBrowserAction.ActionType.VERIFY:
        result = await _execute_click(
            page=page,
            payload=payload,
            click_count=1,
        )

        result["verification_click"] = True

    elif action_type == RemoteBrowserAction.ActionType.CONTINUE:
        result = {
            "continue_requested": True,
        }

    elif action_type == RemoteBrowserAction.ActionType.RESTART:
        result = {
            "restart_requested": True,
        }

    elif action_type == RemoteBrowserAction.ActionType.CANCEL:
        result = {
            "cancel_requested": True,
        }

    else:
        raise RuntimeError(f"Unsupported remote browser action: {action_type!r}.")

    await page.wait_for_timeout(
        REMOTE_BROWSER_ACTION_DELAY_MS,
    )

    return {
        "action_type": action_type,
        "payload": payload,
        "playwright_result": result,
        "processed_at": timezone.now().isoformat(),
        "url": page.url,
    }


# ============================================================
# Procesar próxima acción
# ============================================================


async def process_next_remote_action(
    *,
    page: Page,
    session: RemoteBrowserSession,
) -> dict[str, Any]:
    """
    Reclama y ejecuta una sola acción pendiente.

    Devuelve indicadores que posteriormente utilizará
    _wait_for_human_verification().
    """

    action = await claim_next_remote_action(
        session_id=session.pk,
    )

    if not action:
        return {
            "processed": False,
            "action": None,
            "continue_requested": False,
            "restart_requested": False,
            "cancel_requested": False,
        }

    try:
        current_session = await get_remote_session_by_id(
            session.pk,
        )

        if not current_session:
            raise RuntimeError("The remote browser session no longer exists.")

        action_result = await execute_remote_action(
            page=page,
            session=current_session,
            action=action,
        )

        await mark_remote_action_completed(
            action_id=action.pk,
            result=action_result,
        )

        return {
            "processed": True,
            "action": action,
            "result": action_result,
            "continue_requested": (
                action.action_type == RemoteBrowserAction.ActionType.CONTINUE
            ),
            "restart_requested": (
                action.action_type == RemoteBrowserAction.ActionType.RESTART
            ),
            "cancel_requested": (
                action.action_type == RemoteBrowserAction.ActionType.CANCEL
            ),
        }

    except Exception as exc:
        logger.exception(
            "Remote browser action failed. session=%s action=%s",
            session.pk,
            action.pk,
        )

        await mark_remote_action_failed(
            action_id=action.pk,
            error=exc,
        )

        return {
            "processed": True,
            "action": action,
            "error": str(
                exc,
            ),
            "continue_requested": False,
            "restart_requested": False,
            "cancel_requested": False,
        }


# ============================================================
# Rondas CAPTCHA
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def register_new_captcha_round(
    *,
    session_id: int,
    message: str = "",
) -> RemoteBrowserSession:
    """
    Incrementa la ronda solo cuando Smartsheet presenta un
    challenge realmente nuevo.

    No debe utilizarse para reemplazos dinámicos de mosaicos
    dentro de la misma ronda.
    """

    with transaction.atomic():
        session = RemoteBrowserSession.objects.select_for_update().get(
            pk=session_id,
        )

        next_round = (
            int(
                session.round_number or 1,
            )
            + 1
        )

        if next_round > int(
            session.max_rounds or REMOTE_BROWSER_MAX_CAPTCHA_ROUNDS,
        ):
            session.status = RemoteBrowserSession.Status.RESTART_REQUESTED
            session.captcha_status = (
                RemoteBrowserSession.CaptchaStatus.VERIFICATION_FAILED
            )
            session.message = (
                "The maximum number of CAPTCHA rounds was exceeded. "
                "This submission must restart."
            )
            session.last_result = "maximum_rounds_exceeded"
            session.last_worker_activity_at = timezone.now()

            session.save(
                update_fields=[
                    "status",
                    "captcha_status",
                    "message",
                    "last_result",
                    "last_worker_activity_at",
                    "updated_at",
                ]
            )

            return session

        session.round_number = next_round
        session.status = RemoteBrowserSession.Status.WAITING_FOR_USER
        session.captcha_status = (
            RemoteBrowserSession.CaptchaStatus.NEW_CHALLENGE_DETECTED
        )
        session.last_result = "new_challenge_detected"
        session.message = str(
            message or "",
        ).strip() or ("Smartsheet requested an additional CAPTCHA round.")
        session.last_worker_activity_at = timezone.now()

        session.save(
            update_fields=[
                "round_number",
                "status",
                "captcha_status",
                "last_result",
                "message",
                "last_worker_activity_at",
                "updated_at",
            ]
        )

        return session


# ============================================================
# Cerrar sesión
# ============================================================


@sync_to_async(
    thread_sensitive=True,
)
def close_remote_session(
    *,
    session_id: int,
    status: str = RemoteBrowserSession.Status.CLOSED,
    message: str = "",
):
    """
    Cierra la sesión remota y cancela acciones pendientes.
    """

    with transaction.atomic():
        session = (
            RemoteBrowserSession.objects.select_for_update()
            .filter(
                pk=session_id,
            )
            .first()
        )

        if not session:
            return None

        now = timezone.now()

        session.status = status
        session.closed_at = now
        session.last_worker_activity_at = now

        if message:
            session.message = str(
                message,
            )

        session.save(
            update_fields=[
                "status",
                "closed_at",
                "last_worker_activity_at",
                "message",
                "updated_at",
            ]
        )

        RemoteBrowserAction.objects.filter(
            session=session,
            status=RemoteBrowserAction.Status.PENDING,
        ).update(
            status=RemoteBrowserAction.Status.CANCELLED,
            processed_at=now,
            error_code="REMOTE_SESSION_CLOSED",
            error_message=(
                "The remote browser session was closed "
                "before the action was processed."
            ),
        )

        return session
