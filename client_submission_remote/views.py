# client_submission_remote/views.py

from __future__ import annotations

import json
import logging
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from client_submission_remote.models import (RemoteBrowserAction,
                                             RemoteBrowserSession)

logger = logging.getLogger(__name__)


# ============================================================
# Configuración
# ============================================================


REMOTE_BROWSER_ALLOWED_ROLES = {
    "admin",
    "admin_general",
    "pm",
    "supervisor",
    "facturacion",
    "emision_facturacion",
}

REMOTE_BROWSER_MAX_PENDING_ACTIONS = 100

REMOTE_BROWSER_CONTROLLER_IDLE_SECONDS = 300

REMOTE_BROWSER_ALLOWED_ACTIONS = {
    RemoteBrowserAction.ActionType.CLICK,
    RemoteBrowserAction.ActionType.DOUBLE_CLICK,
    RemoteBrowserAction.ActionType.MULTI_CLICK,
    RemoteBrowserAction.ActionType.SCROLL,
    RemoteBrowserAction.ActionType.REFRESH_SCREENSHOT,
    RemoteBrowserAction.ActionType.VERIFY,
    RemoteBrowserAction.ActionType.CONTINUE,
    RemoteBrowserAction.ActionType.RESTART,
    RemoteBrowserAction.ActionType.CANCEL,
}


# ============================================================
# Respuestas JSON
# ============================================================


def _json_error(
    message: str,
    *,
    status: int = 400,
    code: str = "INVALID_REQUEST",
    details: Any = None,
) -> JsonResponse:
    payload = {
        "ok": False,
        "code": str(
            code or "INVALID_REQUEST",
        ),
        "message": str(
            message or "Invalid request.",
        ),
    }

    if details is not None:
        payload["details"] = details

    return JsonResponse(
        payload,
        status=status,
    )


def _json_success(
    *,
    status: int = 200,
    **payload,
) -> JsonResponse:
    return JsonResponse(
        {
            "ok": True,
            **payload,
        },
        status=status,
    )


# ============================================================
# Lectura JSON
# ============================================================


def _read_json_body(
    request: HttpRequest,
) -> dict:
    if not request.body:
        return {}

    try:
        decoded = request.body.decode(
            "utf-8",
        )

    except UnicodeDecodeError as exc:
        raise ValidationError("The request body is not valid UTF-8.") from exc

    try:
        payload = json.loads(
            decoded,
        )

    except json.JSONDecodeError as exc:
        raise ValidationError("The request body is not valid JSON.") from exc

    if not isinstance(
        payload,
        dict,
    ):
        raise ValidationError("The JSON request body must be an object.")

    return payload


# ============================================================
# Acceso
# ============================================================


def _normalize_role_name(
    value,
) -> str:
    return (
        str(
            value or "",
        )
        .strip()
        .lower()
    )


def _extract_user_roles(
    user,
) -> set[str]:
    """
    Obtiene roles sin asumir una única implementación del modelo
    de usuario.

    Soporta:

    - user.rol
    - user.role
    - user.roles como ManyToMany
    - roles con campos nombre, name, codigo, code o slug
    """

    roles: set[str] = set()

    for attribute_name in (
        "rol",
        "role",
    ):
        try:
            value = getattr(
                user,
                attribute_name,
                None,
            )

        except Exception:
            value = None

        if value:
            normalized = _normalize_role_name(
                value,
            )

            if normalized:
                roles.add(
                    normalized,
                )

    try:
        role_manager = getattr(
            user,
            "roles",
            None,
        )

    except Exception:
        role_manager = None

    if role_manager is not None:
        try:
            role_objects = role_manager.all()

        except Exception:
            role_objects = []

        for role_object in role_objects:
            role_values = []

            for field_name in (
                "nombre",
                "name",
                "codigo",
                "code",
                "slug",
            ):
                try:
                    field_value = getattr(
                        role_object,
                        field_name,
                        None,
                    )

                except Exception:
                    field_value = None

                if field_value:
                    role_values.append(
                        field_value,
                    )

            if not role_values:
                role_values.append(
                    str(
                        role_object,
                    )
                )

            for role_value in role_values:
                normalized = _normalize_role_name(
                    role_value,
                )

                if normalized:
                    roles.add(
                        normalized,
                    )

    return roles


def _user_can_manage_remote_browser(
    user,
) -> bool:
    if not getattr(
        user,
        "is_authenticated",
        False,
    ):
        return False

    if getattr(
        user,
        "is_superuser",
        False,
    ):
        return True

    user_roles = _extract_user_roles(
        user,
    )

    return bool(
        user_roles.intersection(
            REMOTE_BROWSER_ALLOWED_ROLES,
        )
    )


def _enforce_remote_browser_access(
    request: HttpRequest,
):
    if not _user_can_manage_remote_browser(
        request.user,
    ):
        raise PermissionDenied(
            "You do not have permission to control "
            "Client Submission browser sessions."
        )


# ============================================================
# Sesión
# ============================================================


def _get_session_or_404(
    public_id,
) -> RemoteBrowserSession:
    return get_object_or_404(
        RemoteBrowserSession.objects.select_related(
            "submission",
            "submission__batch",
            "attempt",
            "controller_user",
        ),
        public_id=public_id,
    )


def _is_controller_stale(
    session: RemoteBrowserSession,
) -> bool:
    if not session.controller_user_id:
        return True

    last_activity = (
        session.controller_last_activity_at or session.controller_acquired_at
    )

    if not last_activity:
        return True

    elapsed_seconds = (timezone.now() - last_activity).total_seconds()

    return elapsed_seconds >= REMOTE_BROWSER_CONTROLLER_IDLE_SECONDS


@transaction.atomic
def _acquire_controller(
    *,
    session_id: int,
    user,
) -> RemoteBrowserSession:
    """
    Asigna o conserva el control de la sesión remota.

    La fila de RemoteBrowserSession se bloquea sin incluir relaciones
    opcionales en la consulta FOR UPDATE. Esto evita el error de
    PostgreSQL:

        FOR UPDATE cannot be applied to the nullable side
        of an outer join

    Para evitar escrituras constantes durante el polling:

    - guarda inmediatamente cuando cambia el controlador;
    - actualiza la actividad del mismo controlador como máximo
      una vez cada 30 segundos;
    - permite tomar el control cuando el anterior está inactivo.
    """

    session = RemoteBrowserSession.objects.select_for_update().get(
        pk=session_id,
    )

    now = timezone.now()

    controller_is_current_user = session.controller_user_id == user.pk

    controller_is_available = (
        not session.controller_user_id
        or controller_is_current_user
        or _is_controller_stale(
            session,
        )
    )

    if controller_is_available:
        if not controller_is_current_user:
            session.controller_user = user
            session.controller_acquired_at = now
            session.controller_last_activity_at = now

            session.save(
                update_fields=[
                    "controller_user",
                    "controller_acquired_at",
                    "controller_last_activity_at",
                    "updated_at",
                ]
            )

        else:
            last_activity = (
                session.controller_last_activity_at or session.controller_acquired_at
            )

            should_refresh_activity = (
                last_activity is None or (now - last_activity).total_seconds() >= 30
            )

            if should_refresh_activity:
                session.controller_last_activity_at = now

                session.save(
                    update_fields=[
                        "controller_last_activity_at",
                        "updated_at",
                    ]
                )

    return RemoteBrowserSession.objects.select_related(
        "submission",
        "submission__batch",
        "attempt",
        "controller_user",
    ).get(
        pk=session.pk,
    )


def _user_controls_session(
    *,
    session: RemoteBrowserSession,
    user,
) -> bool:
    return bool(session.controller_user_id and session.controller_user_id == user.pk)


# ============================================================
# Serialización
# ============================================================


def _safe_screenshot_url(
    session: RemoteBrowserSession,
) -> str:
    if not session.screenshot:
        return ""

    try:
        return str(
            session.screenshot.url or "",
        )

    except Exception:
        logger.exception(
            "Could not resolve screenshot URL for remote session %s.",
            session.pk,
        )

        return ""


def _serialize_controller(
    session: RemoteBrowserSession,
) -> dict:
    controller = session.controller_user

    if not controller:
        return {
            "assigned": False,
            "id": None,
            "username": "",
            "display_name": "",
            "acquired_at": None,
            "last_activity_at": None,
        }

    display_name = ""

    try:
        display_name = controller.get_full_name()

    except Exception:
        display_name = ""

    if not display_name:
        display_name = str(
            getattr(
                controller,
                "username",
                "",
            )
            or getattr(
                controller,
                "email",
                "",
            )
            or controller
        ).strip()

    return {
        "assigned": True,
        "id": controller.pk,
        "username": str(
            getattr(
                controller,
                "username",
                "",
            )
            or ""
        ),
        "display_name": display_name,
        "acquired_at": (
            session.controller_acquired_at.isoformat()
            if session.controller_acquired_at
            else None
        ),
        "last_activity_at": (
            session.controller_last_activity_at.isoformat()
            if session.controller_last_activity_at
            else None
        ),
    }


def _serialize_session(
    session: RemoteBrowserSession,
    *,
    request_user=None,
) -> dict:
    submission = session.submission

    batch = submission.batch

    pending_actions = session.actions.filter(
        status=RemoteBrowserAction.Status.PENDING,
    ).count()

    processing_actions = session.actions.filter(
        status=RemoteBrowserAction.Status.PROCESSING,
    ).count()

    latest_action = session.actions.order_by(
        "-requested_at",
        "-id",
    ).first()

    latest_action_payload = None

    if latest_action:
        latest_action_payload = {
            "public_id": str(
                latest_action.public_id,
            ),
            "action_type": latest_action.action_type,
            "action_type_label": (latest_action.get_action_type_display()),
            "status": latest_action.status,
            "status_label": latest_action.get_status_display(),
            "requested_at": (
                latest_action.requested_at.isoformat()
                if latest_action.requested_at
                else None
            ),
            "processed_at": (
                latest_action.processed_at.isoformat()
                if latest_action.processed_at
                else None
            ),
            "error_code": latest_action.error_code,
            "error_message": latest_action.error_message,
        }

    user_has_control = False

    if request_user is not None:
        user_has_control = _user_controls_session(
            session=session,
            user=request_user,
        )

    return {
        "public_id": str(
            session.public_id,
        ),
        "status": session.status,
        "status_label": session.get_status_display(),
        "captcha_status": session.captcha_status,
        "captcha_status_label": (session.get_captcha_status_display()),
        "stage": session.stage,
        "is_open": session.is_open,
        "is_expired": session.is_expired,
        "can_accept_actions": (session.can_accept_actions and user_has_control),
        "user_has_control": user_has_control,
        "project": {
            "submission_id": submission.pk,
            "submission_public_id": str(
                submission.public_id,
            ),
            "project_id": str(
                submission.project_id or "",
            ),
            "access_point_id": str(
                submission.access_point_id or "",
            ),
            "batch_id": batch.pk,
            "batch_public_id": str(
                batch.public_id,
            ),
            "batch_name": str(
                batch.name or "",
            ),
        },
        "controller": _serialize_controller(
            session,
        ),
        "viewport": {
            "width": session.viewport_width,
            "height": session.viewport_height,
            "device_scale_factor": float(
                session.device_scale_factor,
            ),
        },
        "screenshot": {
            "available": bool(
                session.screenshot,
            ),
            "url": _safe_screenshot_url(
                session,
            ),
            "version": session.screenshot_version,
            "width": session.screenshot_width,
            "height": session.screenshot_height,
            "captured_at": (
                session.screenshot_captured_at.isoformat()
                if session.screenshot_captured_at
                else None
            ),
            "error": session.screenshot_error,
        },
        "browser": {
            "url": session.browser_url,
            "title": session.browser_title,
            "scroll_x": session.page_scroll_x,
            "scroll_y": session.page_scroll_y,
            "document_width": session.page_document_width,
            "document_height": session.page_document_height,
        },
        "captcha": {
            "round_number": session.round_number,
            "max_rounds": session.max_rounds,
        },
        "actions": {
            "count": session.action_count,
            "pending": pending_actions,
            "processing": processing_actions,
            "last_action_type": session.last_action_type,
            "last_action_at": (
                session.last_action_at.isoformat() if session.last_action_at else None
            ),
            "last_result": session.last_result,
            "latest": latest_action_payload,
        },
        "worker": {
            "identifier": session.worker_identifier,
            "last_activity_at": (
                session.last_worker_activity_at.isoformat()
                if session.last_worker_activity_at
                else None
            ),
        },
        "message": session.message,
        "error": {
            "code": session.error_code,
            "message": session.error_message,
        },
        "dates": {
            "started_at": (
                session.started_at.isoformat() if session.started_at else None
            ),
            "expires_at": (
                session.expires_at.isoformat() if session.expires_at else None
            ),
            "closed_at": (session.closed_at.isoformat() if session.closed_at else None),
            "created_at": session.created_at.isoformat(),
            "updated_at": session.updated_at.isoformat(),
        },
    }


# ============================================================
# Payload de acciones
# ============================================================


def _numeric_value(
    value,
    *,
    field_name: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(
        value,
        bool,
    ):
        raise ValidationError(f"{field_name} must be numeric.")

    try:
        parsed = float(
            value,
        )

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise ValidationError(f"{field_name} must be numeric.") from exc

    if minimum is not None and parsed < minimum:
        raise ValidationError(f"{field_name} cannot be less than {minimum}.")

    if maximum is not None and parsed > maximum:
        raise ValidationError(f"{field_name} cannot be greater than {maximum}.")

    return parsed


def _normalize_click_payload(
    payload: dict,
    *,
    session: RemoteBrowserSession,
) -> dict:
    x = _numeric_value(
        payload.get(
            "x",
        ),
        field_name="x",
        minimum=0,
        maximum=max(
            session.viewport_width - 1,
            0,
        ),
    )

    y = _numeric_value(
        payload.get(
            "y",
        ),
        field_name="y",
        minimum=0,
        maximum=max(
            session.viewport_height - 1,
            0,
        ),
    )

    return {
        "x": round(
            x,
            2,
        ),
        "y": round(
            y,
            2,
        ),
    }


def _normalize_multi_click_payload(
    payload: dict,
    *,
    session: RemoteBrowserSession,
) -> dict:
    points = payload.get(
        "points",
    )

    if not isinstance(
        points,
        list,
    ):
        raise ValidationError("points must be a list.")

    if not points:
        raise ValidationError("At least one click point is required.")

    if (
        len(
            points,
        )
        > 25
    ):
        raise ValidationError("A maximum of 25 click points is allowed.")

    normalized_points = []

    for index, point in enumerate(
        points,
        start=1,
    ):
        if not isinstance(
            point,
            dict,
        ):
            raise ValidationError(f"Point #{index} must be an object.")

        normalized_points.append(
            _normalize_click_payload(
                point,
                session=session,
            )
        )

    return {
        "points": normalized_points,
    }


def _normalize_scroll_payload(
    payload: dict,
) -> dict:
    delta_x = _numeric_value(
        payload.get(
            "delta_x",
            0,
        ),
        field_name="delta_x",
        minimum=-5000,
        maximum=5000,
    )

    delta_y = _numeric_value(
        payload.get(
            "delta_y",
            0,
        ),
        field_name="delta_y",
        minimum=-5000,
        maximum=5000,
    )

    if delta_x == 0 and delta_y == 0:
        raise ValidationError("The scroll distance cannot be zero.")

    return {
        "delta_x": round(
            delta_x,
            2,
        ),
        "delta_y": round(
            delta_y,
            2,
        ),
    }


def _normalize_action_payload(
    *,
    action_type: str,
    payload,
    session: RemoteBrowserSession,
) -> dict:
    safe_payload = (
        dict(
            payload,
        )
        if isinstance(
            payload,
            dict,
        )
        else {}
    )

    if action_type in {
        RemoteBrowserAction.ActionType.CLICK,
        RemoteBrowserAction.ActionType.DOUBLE_CLICK,
        RemoteBrowserAction.ActionType.VERIFY,
    }:
        return _normalize_click_payload(
            safe_payload,
            session=session,
        )

    if action_type == RemoteBrowserAction.ActionType.MULTI_CLICK:
        return _normalize_multi_click_payload(
            safe_payload,
            session=session,
        )

    if action_type == RemoteBrowserAction.ActionType.SCROLL:
        return _normalize_scroll_payload(
            safe_payload,
        )

    return {}


# ============================================================
# Console
# ============================================================


@login_required
@require_GET
def remote_browser_console(
    request: HttpRequest,
    public_id,
) -> HttpResponse:
    _enforce_remote_browser_access(
        request,
    )

    initial_session = _get_session_or_404(
        public_id,
    )

    session = _acquire_controller(
        session_id=initial_session.pk,
        user=request.user,
    )

    state_url = reverse(
        "client_submission_remote:state",
        kwargs={
            "public_id": session.public_id,
        },
    )

    action_url = reverse(
        "client_submission_remote:action",
        kwargs={
            "public_id": session.public_id,
        },
    )

    return render(
        request,
        "client_submission_remote/console.html",
        {
            "remote_session": session,
            "remote_state": _serialize_session(
                session,
                request_user=request.user,
            ),
            "remote_state_url": state_url,
            "remote_action_url": action_url,
        },
    )


# ============================================================
# Estado JSON
# ============================================================


@login_required
@require_GET
def remote_browser_state(
    request: HttpRequest,
    public_id,
) -> JsonResponse:
    _enforce_remote_browser_access(
        request,
    )

    initial_session = _get_session_or_404(
        public_id,
    )

    session = _acquire_controller(
        session_id=initial_session.pk,
        user=request.user,
    )

    return _json_success(
        session=_serialize_session(
            session,
            request_user=request.user,
        ),
        server_time=timezone.now().isoformat(),
    )


# ============================================================
# Registrar acción
# ============================================================


@login_required
@require_POST
def remote_browser_action(
    request: HttpRequest,
    public_id,
) -> JsonResponse:
    _enforce_remote_browser_access(
        request,
    )

    try:
        request_payload = _read_json_body(
            request,
        )

    except ValidationError as exc:
        return _json_error(
            str(
                exc,
            ),
            status=400,
            code="INVALID_JSON",
        )

    action_type = str(
        request_payload.get(
            "action_type",
            "",
        )
        or ""
    ).strip()

    if action_type not in REMOTE_BROWSER_ALLOWED_ACTIONS:
        return _json_error(
            "Unsupported remote browser action.",
            status=400,
            code="UNSUPPORTED_ACTION",
        )

    try:
        screenshot_version = int(
            request_payload.get(
                "screenshot_version",
                0,
            )
            or 0
        )

    except (
        TypeError,
        ValueError,
    ):
        return _json_error(
            "screenshot_version must be an integer.",
            status=400,
            code="INVALID_SCREENSHOT_VERSION",
        )

    if screenshot_version < 0:
        return _json_error(
            "screenshot_version cannot be negative.",
            status=400,
            code="INVALID_SCREENSHOT_VERSION",
        )

    try:
        with transaction.atomic():
            session = RemoteBrowserSession.objects.select_for_update().get(
                public_id=public_id,
            )

            if session.is_expired:
                return _json_error(
                    "The remote browser session has expired.",
                    status=409,
                    code="SESSION_EXPIRED",
                )

            if not session.is_open:
                return _json_error(
                    "The remote browser session is closed.",
                    status=409,
                    code="SESSION_CLOSED",
                )

            if not _user_controls_session(
                session=session,
                user=request.user,
            ):
                controller_name = ""

                if session.controller_user_id:
                    controller_user = session.controller_user

                    try:
                        controller_name = controller_user.get_full_name()

                    except Exception:
                        controller_name = ""

                    if not controller_name:
                        controller_name = str(
                            controller_user,
                        )

                return _json_error(
                    (
                        "This remote browser session is currently "
                        f"controlled by "
                        f"{controller_name or 'another user'}."
                    ),
                    status=409,
                    code="CONTROLLER_CONFLICT",
                )

            pending_action_count = RemoteBrowserAction.objects.filter(
                session_id=session.pk,
                status__in=[
                    RemoteBrowserAction.Status.PENDING,
                    RemoteBrowserAction.Status.PROCESSING,
                ],
            ).count()

            if pending_action_count >= REMOTE_BROWSER_MAX_PENDING_ACTIONS:
                return _json_error(
                    "The remote browser action queue is full.",
                    status=429,
                    code="ACTION_QUEUE_FULL",
                )

            coordinate_actions = {
                RemoteBrowserAction.ActionType.CLICK,
                RemoteBrowserAction.ActionType.DOUBLE_CLICK,
                RemoteBrowserAction.ActionType.MULTI_CLICK,
                RemoteBrowserAction.ActionType.VERIFY,
            }

            if (
                action_type in coordinate_actions
                and screenshot_version != session.screenshot_version
            ):
                return _json_error(
                    (
                        "The screenshot changed before the action "
                        "was submitted. Refresh the console and "
                        "try again."
                    ),
                    status=409,
                    code="STALE_SCREENSHOT",
                    details={
                        "current_version": (session.screenshot_version),
                        "submitted_version": screenshot_version,
                    },
                )

            normalized_payload = _normalize_action_payload(
                action_type=action_type,
                payload=request_payload.get(
                    "payload",
                    {},
                ),
                session=session,
            )

            action = RemoteBrowserAction(
                session=session,
                action_type=action_type,
                status=RemoteBrowserAction.Status.PENDING,
                payload=normalized_payload,
                screenshot_version=screenshot_version,
                requested_by=request.user,
                requested_at=timezone.now(),
            )

            action.full_clean()

            action.save()

            session.controller_last_activity_at = timezone.now()

            session.save(
                update_fields=[
                    "controller_last_activity_at",
                    "updated_at",
                ]
            )

    except RemoteBrowserSession.DoesNotExist as exc:
        raise Http404("Remote browser session not found.") from exc

    except ValidationError as exc:
        if hasattr(
            exc,
            "message_dict",
        ):
            details = exc.message_dict

        else:
            details = exc.messages

        return _json_error(
            "The remote browser action is invalid.",
            status=400,
            code="INVALID_ACTION",
            details=details,
        )

    except Exception as exc:
        logger.exception(
            "Could not create remote browser action " "for session %s.",
            public_id,
        )

        return _json_error(
            "The remote browser action could not be created.",
            status=500,
            code=exc.__class__.__name__,
        )

    return _json_success(
        status=201,
        action={
            "public_id": str(
                action.public_id,
            ),
            "action_type": action.action_type,
            "action_type_label": (action.get_action_type_display()),
            "status": action.status,
            "status_label": (action.get_status_display()),
            "screenshot_version": (action.screenshot_version),
            "requested_at": (action.requested_at.isoformat()),
        },
    )
