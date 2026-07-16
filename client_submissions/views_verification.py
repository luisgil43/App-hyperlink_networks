# client_submissions/views_verification.py

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from client_submissions.models import (ClientSubmission, ClientSubmissionBatch,
                                       ClientSubmissionEvent)
from usuarios.decoradores import rol_requerido

from .views import _assert_manage_permission

# ============================================================
# Roles permitidos
# ============================================================


VERIFICATION_ROLES = (
    "admin",
    "pm",
    "supervisor",
    "facturacion",
    "emision_facturacion",
)


# ============================================================
# Helpers
# ============================================================


def _get_verification_state(
    submission: ClientSubmission,
) -> dict:
    browser_state = (
        submission.browser_state
        if isinstance(
            submission.browser_state,
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

    return verification_state


def _set_verification_state(
    submission: ClientSubmission,
    **changes,
) -> dict:
    browser_state = (
        dict(
            submission.browser_state,
        )
        if isinstance(
            submission.browser_state,
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

    submission.browser_state = browser_state

    return verification_state


# ============================================================
# Vista de verificación humana
# ============================================================


@login_required
@rol_requerido(
    *VERIFICATION_ROLES,
)
@require_GET
def verification_detail(
    request: HttpRequest,
    public_id,
) -> HttpResponse:
    _assert_manage_permission(
        request,
    )

    submission = get_object_or_404(
        ClientSubmission.objects.select_related(
            "batch",
            "billing_session",
        ),
        public_id=public_id,
    )

    if submission.status != ClientSubmission.Status.AWAITING_VERIFICATION:
        messages.warning(
            request,
            "This project is not currently waiting for human verification.",
        )

        return redirect(
            "client_submissions:batch_detail",
            public_id=submission.batch.public_id,
        )

    verification_state = _get_verification_state(
        submission,
    )

    context = {
        "submission": submission,
        "batch": submission.batch,
        "verification_state": verification_state,
        "continue_requested": bool(
            verification_state.get(
                "continue_requested_at",
            )
        ),
        "cancel_requested": bool(
            verification_state.get(
                "cancel_requested_at",
            )
        ),
        "verification_session_url": str(
            verification_state.get(
                "session_url",
                "",
            )
            or ""
        ).strip(),
    }

    return render(
        request,
        "client_submissions/verification.html",
        context,
    )


# ============================================================
# Estado de la verificación
# ============================================================


@login_required
@rol_requerido(
    *VERIFICATION_ROLES,
)
@require_GET
def verification_status_json(
    request: HttpRequest,
    public_id,
) -> JsonResponse:
    """
    Devuelve el estado actual de la verificación humana.

    Mantiene los datos detallados dentro de:

        submission
        verification

    También devuelve los campos principales en el nivel superior
    para que el template verification.html pueda consumirlos
    directamente mediante JavaScript.
    """

    _assert_manage_permission(
        request,
    )

    submission = get_object_or_404(
        ClientSubmission.objects.select_related(
            "batch",
        ),
        public_id=public_id,
    )

    verification_state = _get_verification_state(
        submission,
    )

    session_url = str(
        verification_state.get(
            "session_url",
            "",
        )
        or ""
    ).strip()

    session_available = bool(
        verification_state.get(
            "session_available",
            False,
        )
    )

    continue_requested_at = verification_state.get(
        "continue_requested_at",
    )

    cancel_requested_at = verification_state.get(
        "cancel_requested_at",
    )

    worker_checked_at = verification_state.get(
        "worker_checked_at",
    )

    captcha_cleared = bool(
        verification_state.get(
            "captcha_cleared",
            False,
        )
    )

    verification_message = str(
        verification_state.get(
            "message",
            "",
        )
        or ""
    ).strip()

    submission_data = {
        "public_id": str(
            submission.public_id,
        ),
        "project_id": submission.project_id,
        "status": submission.status,
        "status_label": submission.get_status_display(),
        "browser_confirmation_received": (submission.browser_confirmation_received),
        "email_confirmation_received": (submission.email_confirmation_received),
        "verification_required_at": (
            submission.verification_required_at.isoformat()
            if submission.verification_required_at
            else None
        ),
        "last_error_code": submission.last_error_code,
        "last_error_message": submission.last_error_message,
    }

    verification_data = {
        "continue_requested_at": continue_requested_at,
        "continue_requested_by": verification_state.get(
            "continue_requested_by",
        ),
        "continue_requested_username": verification_state.get(
            "continue_requested_username",
            "",
        ),
        "cancel_requested_at": cancel_requested_at,
        "cancel_requested_by": verification_state.get(
            "cancel_requested_by",
        ),
        "cancel_requested_username": verification_state.get(
            "cancel_requested_username",
            "",
        ),
        "worker_checked_at": worker_checked_at,
        "captcha_cleared": captcha_cleared,
        "session_available": session_available,
        "session_url": session_url,
        "message": verification_message,
    }

    return JsonResponse(
        {
            "ok": True,
            # =================================================
            # Campos directos utilizados por verification.html
            # =================================================
            "status": submission.status,
            "status_label": submission.get_status_display(),
            "browser_confirmation_received": (submission.browser_confirmation_received),
            "email_confirmation_received": (submission.email_confirmation_received),
            "last_error_code": submission.last_error_code,
            "last_error_message": submission.last_error_message,
            "session_available": session_available,
            "session_url": session_url,
            "continue_requested_at": continue_requested_at,
            "cancel_requested_at": cancel_requested_at,
            "worker_checked_at": worker_checked_at,
            "captcha_cleared": captcha_cleared,
            "message": verification_message,
            # =================================================
            # Estructura detallada
            # =================================================
            "submission": submission_data,
            "verification": verification_data,
        }
    )


# ============================================================
# Solicitar continuación
# ============================================================


@login_required
@rol_requerido(
    *VERIFICATION_ROLES,
)
@require_POST
@transaction.atomic
def verification_continue(
    request: HttpRequest,
    public_id,
) -> JsonResponse:
    """
    Solicita al worker que vuelva a comprobar el navegador.

    IMPORTANTE:

    Esta vista NO marca browser_confirmation_received=True.

    La confirmación solamente puede registrarse después de que
    el worker compruebe realmente que:

    1. El CAPTCHA ya no está visible.
    2. Smartsheet aceptó el formulario.
    3. Apareció una confirmación confiable.
    """

    _assert_manage_permission(
        request,
    )

    submission = get_object_or_404(
        ClientSubmission.objects.select_for_update().select_related(
            "batch",
        ),
        public_id=public_id,
    )

    if submission.status != ClientSubmission.Status.AWAITING_VERIFICATION:
        return JsonResponse(
            {
                "ok": False,
                "error": "This submission is not awaiting verification.",
            },
            status=409,
        )

    now = timezone.now()

    verification_state = _set_verification_state(
        submission,
        continue_requested_at=now.isoformat(),
        continue_requested_by=request.user.pk,
        continue_requested_username=request.user.get_username(),
        cancel_requested_at=None,
        cancel_requested_by=None,
        worker_checked_at=None,
        captcha_cleared=False,
        message=(
            "Continuation was requested. "
            "Waiting for the automation worker to verify the browser."
        ),
    )

    submission.save(
        update_fields=[
            "browser_state",
            "updated_at",
        ]
    )

    ClientSubmissionEvent.objects.create(
        batch=submission.batch,
        submission=submission,
        level=ClientSubmissionEvent.Level.INFO,
        event_type="verification_continue_requested",
        message=(
            "Human verification continuation was requested for "
            f"{submission.project_id} by "
            f"{request.user.get_username()}."
        ),
        metadata={
            "user_id": request.user.pk,
            "username": request.user.get_username(),
            "requested_at": now.isoformat(),
        },
    )

    return JsonResponse(
        {
            "ok": True,
            "message": (
                "Continuation requested. "
                "The worker must now validate the browser session."
            ),
            "verification": verification_state,
        }
    )


# ============================================================
# Cancelar procesamiento
# ============================================================


@login_required
@rol_requerido(
    *VERIFICATION_ROLES,
)
@require_POST
@transaction.atomic
def verification_cancel(
    request: HttpRequest,
    public_id,
) -> JsonResponse:
    """
    Solicita cancelar la verificación y cierra el Submission.

    La vista:

    1. Registra cancel_requested_at en browser_state.
    2. Marca el Submission como CANCELLED.
    3. Pausa el Batch si estaba esperando verificación.
    4. El worker deberá detectar cancel_requested_at y cerrar
       el navegador activo.
    """

    _assert_manage_permission(
        request,
    )

    submission = get_object_or_404(
        ClientSubmission.objects.select_for_update().select_related(
            "batch",
        ),
        public_id=public_id,
    )

    if submission.status != ClientSubmission.Status.AWAITING_VERIFICATION:
        return JsonResponse(
            {
                "ok": False,
                "error": "This submission is not awaiting verification.",
            },
            status=409,
        )

    now = timezone.now()

    verification_state = _set_verification_state(
        submission,
        cancel_requested_at=now.isoformat(),
        cancel_requested_by=request.user.pk,
        cancel_requested_username=request.user.get_username(),
        continue_requested_at=None,
        continue_requested_by=None,
        continue_requested_username=None,
        worker_checked_at=None,
        captcha_cleared=False,
        message=(
            "Human verification was cancelled. "
            "Waiting for the worker to close the browser session."
        ),
    )

    submission.status = ClientSubmission.Status.CANCELLED
    submission.finished_at = now

    submission.save(
        update_fields=[
            "status",
            "finished_at",
            "browser_state",
            "updated_at",
        ]
    )

    batch = submission.batch

    batch.last_activity_at = now

    if batch.status == ClientSubmissionBatch.Status.AWAITING_VERIFICATION:
        batch.status = ClientSubmissionBatch.Status.PAUSED
        batch.paused_at = now
        batch.current_submission = None

        batch.save(
            update_fields=[
                "status",
                "paused_at",
                "current_submission",
                "last_activity_at",
                "updated_at",
            ]
        )

    else:
        batch.save(
            update_fields=[
                "last_activity_at",
                "updated_at",
            ]
        )

    ClientSubmissionEvent.objects.create(
        batch=batch,
        submission=submission,
        level=ClientSubmissionEvent.Level.WARNING,
        event_type="verification_cancelled",
        message=(
            "Human verification was cancelled for "
            f"{submission.project_id} by "
            f"{request.user.get_username()}."
        ),
        metadata={
            "user_id": request.user.pk,
            "username": request.user.get_username(),
            "cancelled_at": now.isoformat(),
        },
    )

    redirect_url = reverse(
        "client_submissions:batch_detail",
        kwargs={
            "public_id": batch.public_id,
        },
    )

    return JsonResponse(
        {
            "ok": True,
            "message": "Human verification was cancelled.",
            "verification": verification_state,
            "redirect_url": redirect_url,
        }
    )
