from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from plan_reader.forms import PlanReaderJobForm
from plan_reader.models import PlanReaderJob
from plan_reader.services.processor import mark_duplicates


def can_access_plan_reader(user):
    return user.is_authenticated and getattr(user, "es_admin_general", False)


def deny_plan_reader_access(request):
    messages.warning(
        request,
        "DFN Plan Reader is coming soon. Access is currently limited to administrators.",
    )
    return redirect("dashboard_admin:inicio_admin")


def _status_badge_class(status):
    if status == "completed":
        return "bg-green-100 text-green-700"
    if status == "failed":
        return "bg-red-100 text-red-700"
    if status == "processing":
        return "bg-blue-100 text-blue-700"
    if status == "needs_review":
        return "bg-yellow-100 text-yellow-700"
    return "bg-gray-100 text-gray-700"


def _safe_percent(value):
    if value is None:
        return ""
    return f"{value}%"


def _clean_key_text(value):
    text = str(value or "").strip().upper()

    if text in {"", "-", "—", "N/A", "NA", "NONE", "NULL"}:
        return ""

    return text


def _duplicate_project_names_for_items(items):
    """
    Detecta números de caja repetidos SOLO dentro de los items visibles.
    Sirve para pintar rojo los que siguen incluidos pero necesitan revisión.
    """
    counts = {}

    for item in items:
        project_name = _clean_key_text(item.project_name)

        if not project_name:
            continue

        counts[project_name] = counts.get(project_name, 0) + 1

    return {project_name for project_name, count in counts.items() if count > 1}


def _is_visible_duplicate_review(item, duplicate_project_names):
    if item.is_duplicate:
        return False

    project_name = _clean_key_text(item.project_name)

    if not project_name:
        return False

    return project_name in duplicate_project_names


def _item_payload(item, index, duplicate_project_names=None):
    duplicate_project_names = duplicate_project_names or set()
    duplicate_review = _is_visible_duplicate_review(item, duplicate_project_names)

    return {
        "row_number": index,
        "id": item.id,
        "sheet": item.sheet or "-",
        "project_name": item.project_name or "-",
        "primary_feed": item.primary_feed or "-",
        "visible_type": item.visible_type or "-",
        "detected_box_type": item.detected_box_type or "-",
        "calculated_box_type": item.calculated_box_type or "-",
        "has_p": "Yes" if item.has_p else "No",
        "s_splitter": item.s_splitter or "-",
        "t_splitter": item.t_splitter or "-",
        "splice_count": item.splice_count or 0,
        "c108_ug": item.c108_ug or 0,
        "c109_splices": item.c109_splices or 0,
        "c110_splitters": item.c110_splitters or 0,
        "needs_review": bool(item.needs_review),
        "is_duplicate": bool(item.is_duplicate),
        "duplicate_review": bool(duplicate_review),
        "billing_action": "Duplicate" if item.is_duplicate else "Included",
        "observation": item.observation or "",
        "edit_url": reverse("plan_reader:item_review", args=[item.id]),
        "toggle_duplicate_url": reverse(
            "plan_reader:toggle_item_duplicate",
            args=[item.id],
        ),
    }


@login_required
def job_list(request):
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    jobs = PlanReaderJob.objects.select_related("uploaded_by").all()

    return render(
        request,
        "plan_reader/job_list.html",
        {
            "jobs": jobs,
        },
    )


@login_required
def job_create(request):
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    if request.method == "POST":
        form = PlanReaderJobForm(request.POST, request.FILES)

        if form.is_valid():
            job = form.save(commit=False)
            job.uploaded_by = request.user

            if job.pdf_file:
                job.original_filename = job.pdf_file.name

            job.save()

            messages.success(
                request,
                "Plan Reader job created and queued. The worker will process it automatically.",
            )
            return redirect("plan_reader:job_detail", job_id=job.id)
    else:
        form = PlanReaderJobForm()

    return render(
        request,
        "plan_reader/job_create.html",
        {
            "form": form,
        },
    )


@login_required
def job_detail(request, job_id):
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    job = get_object_or_404(
        PlanReaderJob.objects.select_related("uploaded_by"),
        id=job_id,
    )

    pages = job.pages.all().order_by("page_number")

    items = list(
        job.items.filter(is_duplicate=False).order_by(
            "sheet",
            "project_name",
            "primary_feed",
            "id",
        )
    )

    duplicate_items = list(
        job.items.filter(is_duplicate=True).order_by(
            "sheet",
            "project_name",
            "primary_feed",
            "id",
        )
    )

    duplicate_project_names = _duplicate_project_names_for_items(items)

    included_items_count = len(items)
    duplicate_items_count = len(duplicate_items)
    needs_review_count = sum(1 for item in items if item.needs_review)

    return render(
        request,
        "plan_reader/job_detail.html",
        {
            "job": job,
            "pages": pages,
            "items": items,
            "duplicate_items": duplicate_items,
            "included_items_count": included_items_count,
            "needs_review_count": needs_review_count,
            "duplicate_items_count": duplicate_items_count,
            "duplicate_project_names": duplicate_project_names,
        },
    )


@login_required
@require_POST
def queue_job_processing(request, job_id):
    """
    No procesa OpenAI en la vista.

    Solo deja el Job en pending para que el worker de Render lo tome.
    """
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    job = get_object_or_404(PlanReaderJob, id=job_id)

    if job.status == PlanReaderJob.STATUS_PROCESSING:
        messages.info(
            request,
            "This job is already being processed by the worker.",
        )
        return redirect("plan_reader:job_detail", job_id=job.id)

    job.status = PlanReaderJob.STATUS_PENDING
    job.error_message = ""
    job.save(
        update_fields=[
            "status",
            "error_message",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "Job queued. The Render worker will process it automatically.",
    )
    return redirect("plan_reader:job_detail", job_id=job.id)


@login_required
@require_POST
def recalculate_job_duplicates(request, job_id):
    """
    Recalcula duplicados usando los items ya leídos.

    No llama OpenAI.
    No vuelve a leer el PDF.
    """
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    job = get_object_or_404(PlanReaderJob, id=job_id)

    if job.status == PlanReaderJob.STATUS_PROCESSING:
        messages.warning(
            request,
            "Cannot recalculate duplicates while the job is processing.",
        )
        return redirect("plan_reader:job_detail", job_id=job.id)

    if not job.items.exists():
        messages.warning(
            request,
            "There are no detected items to recalculate.",
        )
        return redirect("plan_reader:job_detail", job_id=job.id)

    mark_duplicates(job)

    messages.success(
        request,
        "Duplicates recalculated successfully.",
    )
    return redirect("plan_reader:job_detail", job_id=job.id)


@login_required
def job_status_json(request, job_id):
    """
    Endpoint liviano para polling automático desde job_detail.html.
    No procesa nada. Solo devuelve el estado actual en DB.
    """
    if not can_access_plan_reader(request.user):
        return JsonResponse(
            {
                "ok": False,
                "error": "Access denied.",
            },
            status=403,
        )

    job = get_object_or_404(
        PlanReaderJob.objects.select_related("uploaded_by"),
        id=job_id,
    )

    pages = list(job.pages.all().order_by("page_number"))

    items = list(
        job.items.filter(is_duplicate=False).order_by(
            "sheet",
            "project_name",
            "primary_feed",
            "id",
        )
    )

    duplicate_items = list(
        job.items.filter(is_duplicate=True).order_by(
            "sheet",
            "project_name",
            "primary_feed",
            "id",
        )
    )

    duplicate_project_names = _duplicate_project_names_for_items(items)

    pages_payload = []
    for page in pages:
        pages_payload.append(
            {
                "page_number": page.page_number,
                "sheet_name": page.sheet_name or "-",
                "status": page.status,
                "status_display": page.get_status_display(),
                "status_badge_class": _status_badge_class(page.status),
                "confidence": _safe_percent(page.confidence),
                "processed_at": (
                    page.processed_at.strftime("%Y-%m-%d %H:%M")
                    if page.processed_at
                    else "-"
                ),
                "error_message": page.error_message or "-",
            }
        )

    items_payload = []
    for index, item in enumerate(items, start=1):
        items_payload.append(_item_payload(item, index, duplicate_project_names))

    duplicate_items_payload = []
    for index, item in enumerate(duplicate_items, start=1):
        duplicate_items_payload.append(_item_payload(item, index, set()))

    included_items_count = len(items)
    duplicate_items_count = len(duplicate_items)
    needs_review_count = sum(1 for item in items if item.needs_review)

    can_download_excel = included_items_count > 0

    return JsonResponse(
        {
            "ok": True,
            "job": {
                "id": job.id,
                "status": job.status,
                "status_display": job.get_status_display(),
                "processed_pages": job.processed_pages,
                "total_pages": job.total_pages,
                "failed_pages": job.failed_pages,
                "progress_percent": job.progress_percent,
                "items_count": included_items_count,
                "included_items_count": included_items_count,
                "needs_review_count": needs_review_count,
                "duplicate_items_count": duplicate_items_count,
                "error_message": job.error_message or "",
                "started_at": (
                    job.started_at.strftime("%Y-%m-%d %H:%M") if job.started_at else "-"
                ),
                "completed_at": (
                    job.completed_at.strftime("%Y-%m-%d %H:%M")
                    if job.completed_at
                    else "-"
                ),
                "can_download_excel": can_download_excel,
                "download_excel_url": (
                    reverse("plan_reader:download_excel", args=[job.id])
                    if can_download_excel
                    else ""
                ),
            },
            "pages": pages_payload,
            "items": items_payload,
            "duplicate_items": duplicate_items_payload,
        }
    )
