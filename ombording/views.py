import base64
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import (OmbordingForm, PositionForm, PublicAcceptanceForm,
                    PublicAccessCodeForm, PublicIdentityForm,
                    PublicPersonalForm, PublicSignatureForm,
                    PublicTaxBankingForm)
from .models import (DocumentKey, Ombording, OmbordingDocument,
                     OmbordingFieldReview, OmbordingSignature, OmbordingStatus,
                     OmbordingStep, Position)
from .services import (consume_temp_uploads_into_ombording,
                       ensure_field_reviews, generate_filled_documents,
                       get_temp_uploads_map, ordered_field_reviews,
                       prepare_new_ombording, refresh_document_review_states,
                       register_audit_log, save_temp_uploads_from_request,
                       save_uploaded_documents_from_form, send_ombording_email,
                       sync_status_after_internal_save, update_document_review,
                       update_field_review)

COUNTRIES = [
    "United States",
    "Canada",
    "Mexico",
    "Chile",
    "Peru",
    "Colombia",
    "Brazil",
    "Argentina",
    "Venezuela",
    "Ecuador",
    "Uruguay",
    "Paraguay",
    "Bolivia",
    "Spain",
    "France",
    "Italy",
    "Germany",
    "Portugal",
    "United Kingdom",
    "India",
    "China",
    "Japan",
    "Philippines",
    "Australia",
]


PUBLIC_STEP_VERIFY = "verify"
PUBLIC_STEP_ACCEPT = "accept"
PUBLIC_STEP_PERSONAL = "personal"
PUBLIC_STEP_IDENTITY = "identity"
PUBLIC_STEP_TAX_BANKING = "tax_banking"
PUBLIC_STEP_SIGNATURE = "signature"

PUBLIC_STEP_LABELS = {
    PUBLIC_STEP_VERIFY: "Access",
    PUBLIC_STEP_ACCEPT: "Review Documents",
    PUBLIC_STEP_PERSONAL: "Personal",
    PUBLIC_STEP_IDENTITY: "Identity",
    PUBLIC_STEP_TAX_BANKING: "Tax & Banking",
    PUBLIC_STEP_SIGNATURE: "Signature",
}


def _public_previous_step(step):
    order = [
        PUBLIC_STEP_VERIFY,
        PUBLIC_STEP_ACCEPT,
        PUBLIC_STEP_PERSONAL,
        PUBLIC_STEP_IDENTITY,
        PUBLIC_STEP_TAX_BANKING,
        PUBLIC_STEP_SIGNATURE,
    ]
    try:
        idx = order.index(step)
    except ValueError:
        return None
    if idx <= 0:
        return None
    return order[idx - 1]


def _public_existing_documents(obj):
    keys = [
        DocumentKey.ADDRESS_PROOF,
        DocumentKey.SSN_FRONT,
        DocumentKey.SSN_BACK,
        DocumentKey.PASSPORT_FRONT,
        DocumentKey.PASSPORT_BACK,
        DocumentKey.WORK_PERMIT_FRONT,
        DocumentKey.WORK_PERMIT_BACK,
        DocumentKey.DRIVER_LICENSE_FRONT,
        DocumentKey.DRIVER_LICENSE_BACK,
    ]

    data = {}
    for key in keys:
        doc = _public_document_by_key(obj, key)
        data[key] = doc if doc and doc.file else None
    return data


def _build_excel_global(queryset):
    return {
        "0": sorted(
            {x.created_at.strftime("%Y-%m-%d") for x in queryset if x.created_at}
        ),
        "1": sorted({(x.first_name or "").strip() for x in queryset if x.first_name}),
        "2": sorted({(x.last_name or "").strip() for x in queryset if x.last_name}),
        "3": sorted({(x.email or "").strip() for x in queryset if x.email}),
        "4": sorted({x.position.name for x in queryset if x.position_id}),
        "5": sorted({x.get_status_display() for x in queryset if x.status}),
        "6": sorted({x.get_current_step_display() for x in queryset if x.current_step}),
    }


def _keep_querystring(request, ignore=None):
    ignore = set(ignore or [])
    params = request.GET.copy()
    for key in ignore:
        params.pop(key, None)
    return urlencode(params, doseq=True)


def _ensure_upload_session(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key


def _public_verified_key(obj):
    return f"ombording_public_verified_{obj.pk}_{obj.link_token}"


def _public_accepted_key(obj):
    return f"ombording_public_accepted_{obj.pk}_{obj.link_token}"


def _public_download_key(obj):
    return f"ombording_public_download_{obj.pk}_{obj.link_token}"


def _public_is_verified(request, obj):
    return bool(request.session.get(_public_verified_key(obj)))


def _public_mark_verified(request, obj):
    request.session[_public_verified_key(obj)] = True
    request.session.modified = True


def _public_clear_verified(request, obj):
    request.session.pop(_public_verified_key(obj), None)
    request.session.pop(_public_accepted_key(obj), None)
    request.session.modified = True


def _public_is_accepted(request, obj):
    return bool(request.session.get(_public_accepted_key(obj)))


def _public_mark_accepted(request, obj):
    request.session[_public_accepted_key(obj)] = True
    request.session.modified = True


def _public_allow_downloads(request, obj):
    request.session[_public_download_key(obj)] = True
    request.session.modified = True


def _public_can_download_after_complete(request, obj):
    return bool(request.session.get(_public_download_key(obj)))


def _public_step_url(token, step):
    return f"{reverse('ombording:public_start', kwargs={'token': token})}?step={step}"


def _public_document_by_key(obj, document_key):
    return (
        OmbordingDocument.objects.filter(
            ombording=obj,
            document_key=document_key,
        )
        .order_by("-id")
        .first()
    )


def _public_base_documents(obj, token):
    items = []
    mapping = [
        (
            DocumentKey.CONTRACTOR_AGREEMENT_BASE,
            "Independent Contractor Agreement",
        ),
        (
            DocumentKey.EXHIBIT_BASE,
            "Exhibit",
        ),
        (
            DocumentKey.W9_BASE,
            "W-9 Base",
        ),
    ]
    for document_key, label in mapping:
        doc = _public_document_by_key(obj, document_key)
        if doc and doc.file:
            items.append(
                {
                    "label": label,
                    "document_key": document_key,
                    "url": reverse(
                        "ombording:public_document_download",
                        kwargs={"token": token, "document_key": document_key},
                    ),
                }
            )
    return items


def _public_filled_documents(obj, token):
    items = []
    mapping = [
        (
            DocumentKey.CONTRACTOR_AGREEMENT_FILLED,
            "Independent Contractor Agreement Filled",
        ),
        (
            DocumentKey.EXHIBIT_FILLED,
            "Exhibit Filled",
        ),
        (
            DocumentKey.W9_FILLED,
            "W-9 Filled",
        ),
    ]
    for document_key, label in mapping:
        doc = _public_document_by_key(obj, document_key)
        if doc and doc.file:
            items.append(
                {
                    "label": label,
                    "document_key": document_key,
                    "url": reverse(
                        "ombording:public_document_download",
                        kwargs={"token": token, "document_key": document_key},
                    ),
                }
            )
    return items


def _public_render(
    request,
    obj,
    step,
    access_form=None,
    acceptance_form=None,
    personal_form=None,
    identity_form=None,
    tax_banking_form=None,
    signature_form=None,
    public_completed=False,
):
    base_documents = _public_base_documents(obj, obj.link_token)
    filled_documents = _public_filled_documents(obj, obj.link_token)
    existing_documents = _public_existing_documents(obj)

    steps = []
    for key in [
        PUBLIC_STEP_VERIFY,
        PUBLIC_STEP_ACCEPT,
        PUBLIC_STEP_PERSONAL,
        PUBLIC_STEP_IDENTITY,
        PUBLIC_STEP_TAX_BANKING,
        PUBLIC_STEP_SIGNATURE,
    ]:
        steps.append(
            {
                "key": key,
                "label": PUBLIC_STEP_LABELS[key],
                "active": key == step,
            }
        )

    previous_step = _public_previous_step(step)
    back_url = (
        _public_step_url(obj.link_token, previous_step) if previous_step else None
    )

    return render(
        request,
        "ombording/public_placeholder.html",
        {
            "obj": obj,
            "step": step,
            "steps": steps,
            "countries": COUNTRIES,
            "access_form": access_form or PublicAccessCodeForm(),
            "acceptance_form": acceptance_form or PublicAcceptanceForm(),
            "personal_form": personal_form or PublicPersonalForm(instance=obj),
            "identity_form": identity_form or PublicIdentityForm(instance=obj),
            "tax_banking_form": tax_banking_form or PublicTaxBankingForm(instance=obj),
            "signature_form": signature_form
            or PublicSignatureForm(initial={"signature_name": obj.full_name}),
            "base_documents": base_documents,
            "filled_documents": filled_documents,
            "existing_documents": existing_documents,
            "public_completed": public_completed,
            "back_url": back_url,
        },
    )


@login_required
def ombording_list(request):
    import json

    qs = Ombording.objects.select_related("position", "created_by", "reviewed_by").all()

    search = (request.GET.get("search") or "").strip()
    status = (request.GET.get("status") or "").strip()
    cantidad = (request.GET.get("cantidad") or "10").strip()

    if search:
        qs = qs.filter(
            Q(first_name__icontains=search)
            | Q(last_name__icontains=search)
            | Q(email__icontains=search)
            | Q(position__name__icontains=search)
        )

    if status:
        qs = qs.filter(status=status)

    qs = qs.order_by("-created_at")

    excel_filters_raw = (request.GET.get("excel_filters") or "").strip()
    try:
        excel_filters = json.loads(excel_filters_raw) if excel_filters_raw else {}
    except json.JSONDecodeError:
        excel_filters = {}

    status_label_map = {
        "draft": "Draft",
        "pending_user": "Pending User",
        "in_correction": "In Correction",
        "in_review": "Pending Review",
        "rejected": "Rejected",
        "approved": "Approved",
        "expired": "Expired",
    }

    def ombording_status_label(obj):
        return status_label_map.get(obj.status, obj.get_status_display())

    def excel_value_for_ombording(obj, col):
        if col == "0":
            return obj.created_at.strftime("%Y-%m-%d") if obj.created_at else ""
        elif col == "1":
            return (obj.first_name or "").strip()
        elif col == "2":
            return (obj.last_name or "").strip()
        elif col == "3":
            return (obj.email or "").strip()
        elif col == "4":
            return obj.position.name if obj.position_id else ""
        elif col == "5":
            return ombording_status_label(obj)
        elif col == "6":
            doc = (
                obj.documents.filter(document_key=DocumentKey.CONTRACTOR_AGREEMENT_BASE)
                .order_by("-id")
                .first()
            )
            return "Open" if doc and doc.file else "—"
        elif col == "7":
            doc = (
                obj.documents.filter(document_key=DocumentKey.EXHIBIT_BASE)
                .order_by("-id")
                .first()
            )
            return "Open" if doc and doc.file else "—"
        elif col == "8":
            doc = (
                obj.documents.filter(document_key=DocumentKey.W9_BASE)
                .order_by("-id")
                .first()
            )
            return "Open" if doc and doc.file else "—"
        elif col == "9":
            return "Yes" if obj.link_token else "—"
        return ""

    all_rows = list(qs)

    if excel_filters:
        filtered_rows = []
        for obj in all_rows:
            ok = True
            for col, values in excel_filters.items():
                values_set = set(values or [])
                if not values_set:
                    continue

                current_value = excel_value_for_ombording(obj, col)
                if current_value not in values_set:
                    ok = False
                    break

            if ok:
                filtered_rows.append(obj)

        all_rows = filtered_rows

    excel_global = {}
    for col in range(10):
        vals = set()
        for obj in all_rows:
            value = excel_value_for_ombording(obj, str(col))
            vals.add(value or "(Empty)")
        excel_global[str(col)] = sorted(vals)

    excel_global_json = json.dumps(excel_global)

    try:
        per_page = int(cantidad)
    except Exception:
        per_page = 10

    per_page = max(5, min(per_page, 100))
    cantidad = str(per_page)

    paginator = Paginator(all_rows, per_page)
    pagina = paginator.get_page(request.GET.get("page"))

    keep_params = {}
    if cantidad:
        keep_params["cantidad"] = cantidad
    if search:
        keep_params["search"] = search
    if status:
        keep_params["status"] = status
    if excel_filters_raw:
        keep_params["excel_filters"] = excel_filters_raw

    qs_keep = urlencode(keep_params)

    context = {
        "pagina": pagina,
        "cantidad": cantidad,
        "search": search,
        "status": status,
        "qs_keep": qs_keep,
        "excel_global_json": excel_global_json,
        "status_choices": OmbordingStatus.choices,
    }

    template_name = "ombording/ombording_list.html"

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return render(request, template_name, context)

    return render(request, template_name, context)


@login_required
def position_list(request):
    items = Position.objects.all().order_by("name")
    return render(request, "ombording/position_list.html", {"items": items})


@login_required
def position_create(request):
    if request.method == "POST":
        form = PositionForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Position created successfully.")
            return redirect("ombording:position_list")
    else:
        form = PositionForm()

    return render(
        request,
        "ombording/position_form.html",
        {"form": form, "title": "New Position", "submit_label": "Create"},
    )


@login_required
def position_edit(request, pk):
    obj = get_object_or_404(Position, pk=pk)
    if request.method == "POST":
        form = PositionForm(request.POST, instance=obj)
        if form.is_valid():
            form.save()
            messages.success(request, "Position updated successfully.")
            return redirect("ombording:position_list")
    else:
        form = PositionForm(instance=obj)

    return render(
        request,
        "ombording/position_form.html",
        {"form": form, "title": "Edit Position", "submit_label": "Save", "obj": obj},
    )


@login_required
def ombording_create(request):
    upload_session_key = _ensure_upload_session(request)

    if request.method == "POST":
        save_temp_uploads_from_request(upload_session_key, request.user, request.FILES)

        form = OmbordingForm(request.POST, request.FILES)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.send_email_on_create = bool(form.cleaned_data.get("send_email_now"))
            obj.entry_mode = "public_link"

            prepare_new_ombording(obj, created_by=request.user)
            obj.save()

            save_uploaded_documents_from_form(obj, request.user, request.FILES)
            consume_temp_uploads_into_ombording(obj, request.user, upload_session_key)

            ensure_field_reviews(obj)
            refresh_document_review_states(obj)

            sync_status_after_internal_save(obj)
            obj.save()

            register_audit_log(
                obj,
                action="ombording_created",
                performed_by=request.user,
                detail="Ombording created from internal panel.",
            )

            if obj.send_email_on_create:
                ok, err = send_ombording_email(
                    obj, request=request, email_type="initial"
                )
                if ok:
                    messages.success(
                        request, "Ombording created and email sent successfully."
                    )
                else:
                    messages.warning(
                        request, f"Ombording created but email failed: {err}"
                    )
            else:
                messages.success(request, "Ombording created successfully.")

            return redirect("ombording:ombording_review", pk=obj.pk)
    else:
        form = OmbordingForm()

    temp_uploads = get_temp_uploads_map(upload_session_key)

    return render(
        request,
        "ombording/ombording_form.html",
        {
            "form": form,
            "title": "New Ombording",
            "submit_label": "Create",
            "countries": COUNTRIES,
            "temp_uploads": temp_uploads,
        },
    )


@login_required
def ombording_edit(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)
    upload_session_key = f"ombording-edit-{obj.pk}-user-{request.user.pk}"

    if request.method == "POST":
        save_temp_uploads_from_request(upload_session_key, request.user, request.FILES)

        form = OmbordingForm(request.POST, request.FILES, instance=obj)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.send_email_on_create = bool(form.cleaned_data.get("send_email_now"))
            obj.save()

            save_uploaded_documents_from_form(obj, request.user, request.FILES)
            consume_temp_uploads_into_ombording(obj, request.user, upload_session_key)

            ensure_field_reviews(obj)
            refresh_document_review_states(obj)

            sync_status_after_internal_save(obj)
            obj.save()

            register_audit_log(
                obj,
                action="ombording_updated",
                performed_by=request.user,
                detail="Ombording updated from internal panel.",
            )
            messages.success(request, "Ombording updated successfully.")
            return redirect("ombording:ombording_review", pk=obj.pk)
    else:
        form = OmbordingForm(instance=obj)

    temp_uploads = get_temp_uploads_map(upload_session_key)

    return render(
        request,
        "ombording/ombording_form.html",
        {
            "form": form,
            "obj": obj,
            "title": "Edit Ombording",
            "submit_label": "Save",
            "countries": COUNTRIES,
            "temp_uploads": temp_uploads,
        },
    )


@login_required
def ombording_review(request, pk):

    obj = get_object_or_404(
        Ombording.objects.select_related("position", "created_by", "reviewed_by"),
        pk=pk,
    )

    documents = obj.documents.all().order_by("document_key", "-created_at")

    audit_logs = obj.audit_logs.select_related("performed_by").all()[:20]

    context = {
        "obj": obj,
        "documents": documents,
        "audit_logs": audit_logs,
    }

    return render(request, "ombording/ombording_review.html", context)


@login_required
@require_POST
def ombording_approve(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)

    obj.status = OmbordingStatus.APPROVED
    obj.reviewed_by = request.user
    obj.approved_at = timezone.now()
    obj.rejected_at = None
    obj.rejection_note = ""
    obj.link_expires_at = timezone.now()
    obj.current_step = OmbordingStep.REVIEW
    obj.save(
        update_fields=[
            "status",
            "reviewed_by",
            "approved_at",
            "rejected_at",
            "rejection_note",
            "link_expires_at",
            "current_step",
            "updated_at",
        ]
    )

    register_audit_log(
        obj,
        action="ombording_approved",
        performed_by=request.user,
        detail="Ombording approved globally.",
    )
    messages.success(request, "Ombording approved successfully.")
    return redirect("ombording:ombording_review", pk=obj.pk)


@login_required
@require_POST
def ombording_reject(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)
    rejection_note = (request.POST.get("rejection_note") or "").strip()

    if not rejection_note:
        messages.error(request, "Rejection note is required.")
        return redirect("ombording:ombording_review", pk=obj.pk)

    obj.status = OmbordingStatus.REJECTED
    obj.reviewed_by = request.user
    obj.rejected_at = timezone.now()
    obj.approved_at = None
    obj.rejection_note = rejection_note
    obj.link_expires_at = timezone.now() + timezone.timedelta(days=7)
    obj.current_step = OmbordingStep.REVIEW
    obj.public_verified_at = None
    obj.save(
        update_fields=[
            "status",
            "reviewed_by",
            "rejected_at",
            "approved_at",
            "rejection_note",
            "link_expires_at",
            "current_step",
            "public_verified_at",
            "updated_at",
        ]
    )

    register_audit_log(
        obj,
        action="ombording_rejected",
        performed_by=request.user,
        detail=f"Ombording rejected globally. Note: {rejection_note}",
    )
    messages.success(request, "Ombording rejected successfully.")
    return redirect("ombording:ombording_review", pk=obj.pk)


@login_required
@require_POST
def ombording_field_review_update(request, pk, review_id):
    obj = get_object_or_404(Ombording, pk=pk)
    review_obj = get_object_or_404(
        OmbordingFieldReview,
        pk=review_id,
        ombording=obj,
    )

    review_status = (request.POST.get("review_status") or "").strip()
    review_comment = (request.POST.get("review_comment") or "").strip()

    try:
        update_field_review(review_obj, review_status, review_comment, request.user)
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    obj.refresh_from_db()
    approved_count, total_count = obj.review_progress

    register_audit_log(
        obj,
        action="field_review_updated",
        performed_by=request.user,
        detail=f"{review_obj.field_label}: {review_obj.get_review_status_display()}",
    )

    return JsonResponse(
        {
            "ok": True,
            "review_id": review_obj.id,
            "review_status": review_obj.review_status,
            "review_status_label": review_obj.get_review_status_display(),
            "review_comment": review_obj.review_comment or "",
            "overall_status": obj.status,
            "overall_status_label": obj.get_status_display(),
            "approved_count": approved_count,
            "total_count": total_count,
        }
    )


@login_required
@require_POST
def ombording_document_review_update(request, pk, document_id):
    obj = get_object_or_404(Ombording, pk=pk)
    document_obj = get_object_or_404(
        OmbordingDocument,
        pk=document_id,
        ombording=obj,
    )

    review_status = (request.POST.get("review_status") or "").strip()
    review_comment = (request.POST.get("review_comment") or "").strip()

    try:
        update_document_review(
            document_obj, review_status, review_comment, request.user
        )
    except ValueError as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)

    obj.refresh_from_db()
    approved_count, total_count = obj.review_progress

    register_audit_log(
        obj,
        action="document_review_updated",
        performed_by=request.user,
        detail=f"{document_obj.label}: {document_obj.get_review_status_display()}",
    )

    return JsonResponse(
        {
            "ok": True,
            "document_id": document_obj.id,
            "review_status": document_obj.review_status,
            "review_status_label": document_obj.get_review_status_display(),
            "review_comment": document_obj.review_comment or "",
            "overall_status": obj.status,
            "overall_status_label": obj.get_status_display(),
            "approved_count": approved_count,
            "total_count": total_count,
        }
    )


@login_required
def ombording_send_email(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)
    ok, err = send_ombording_email(obj, request=request, email_type="initial")
    if ok:
        register_audit_log(
            obj, "email_sent", request.user, "Onboarding email sent manually."
        )
        messages.success(request, "Email sent successfully.")
    else:
        messages.error(request, f"Email could not be sent: {err}")
    return redirect("ombording:ombording_list")


@login_required
def ombording_reactivate(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)
    obj.link_expires_at = timezone.now() + timezone.timedelta(days=7)
    obj.public_verified_at = None
    obj.worker_signed_at = None
    obj.status = OmbordingStatus.PENDING_USER
    obj.rejection_note = ""
    obj.approved_at = None
    obj.rejected_at = None
    obj.save(
        update_fields=[
            "link_expires_at",
            "public_verified_at",
            "worker_signed_at",
            "status",
            "rejection_note",
            "approved_at",
            "rejected_at",
            "updated_at",
        ]
    )
    register_audit_log(obj, "reactivated", request.user, "Ombording reactivated.")
    messages.success(request, "Ombording reactivated successfully.")
    return redirect("ombording:ombording_list")


def public_document_download(request, token, document_key):
    obj = get_object_or_404(Ombording, link_token=token)

    if obj.link_expires_at and obj.link_expires_at < timezone.now():
        if not _public_can_download_after_complete(request, obj):
            raise Http404("This onboarding link has expired.")

    is_allowed_before_complete = _public_is_verified(request, obj)
    is_allowed_after_complete = _public_can_download_after_complete(request, obj)

    if not is_allowed_before_complete and not is_allowed_after_complete:
        raise Http404("Document not available.")

    allowed_keys = {
        DocumentKey.CONTRACTOR_AGREEMENT_BASE,
        DocumentKey.EXHIBIT_BASE,
        DocumentKey.W9_BASE,
        DocumentKey.CONTRACTOR_AGREEMENT_FILLED,
        DocumentKey.EXHIBIT_FILLED,
        DocumentKey.W9_FILLED,
    }
    if document_key not in allowed_keys:
        raise Http404("Document not available.")

    doc = _public_document_by_key(obj, document_key)
    if not doc or not doc.file:
        raise Http404("Document not found.")

    doc.file.open("rb")
    filename = doc.original_name or f"{document_key}.pdf"
    return FileResponse(doc.file, as_attachment=True, filename=filename)


def public_start(request, token):
    obj = get_object_or_404(Ombording, link_token=token)

    if obj.link_expires_at and obj.link_expires_at < timezone.now():
        if _public_can_download_after_complete(request, obj):
            return render(
                request,
                "ombording/public_link_closed.html",
                {
                    "obj": obj,
                    "closed_title": "Onboarding completed successfully",
                    "closed_message": (
                        "Thank you. Your onboarding has been submitted successfully and is now under review. "
                        "Our team will contact you if any additional information is needed."
                    ),
                },
                status=200,
            )

        return render(
            request,
            "ombording/public_link_closed.html",
            {
                "obj": obj,
                "closed_title": "Onboarding closed",
                "closed_message": (
                    "This onboarding link is no longer available. "
                    "If you still need to complete or correct information, "
                    "please contact the person who sent you this onboarding so they can reopen the link."
                ),
            },
            status=410,
        )

    if (
        obj.status == OmbordingStatus.IN_REVIEW
        and obj.worker_signed_at
        and not _public_is_verified(request, obj)
        and _public_can_download_after_complete(request, obj)
    ):
        return render(
            request,
            "ombording/public_link_closed.html",
            {
                "obj": obj,
                "closed_title": "Onboarding completed successfully",
                "closed_message": (
                    "Thank you. Your onboarding has been submitted successfully and is now under review. "
                    "Our team will contact you if any additional information is needed."
                ),
            },
            status=200,
        )

    step = (request.GET.get("step") or "").strip() or PUBLIC_STEP_VERIFY
    is_verified = _public_is_verified(request, obj)
    is_accepted = _public_is_accepted(request, obj)

    if not is_verified:
        step = PUBLIC_STEP_VERIFY
    elif not is_accepted and step != PUBLIC_STEP_ACCEPT:
        step = PUBLIC_STEP_ACCEPT

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "verify_access":
            form = PublicAccessCodeForm(request.POST)
            if form.is_valid():
                entered = form.cleaned_data["access_code"]
                expected = (obj.public_access_code or "").strip().upper()
                if entered == expected:
                    _public_mark_verified(request, obj)
                    obj.public_verified_at = timezone.now()

                    if obj.status == OmbordingStatus.REJECTED:
                        obj.status = OmbordingStatus.IN_CORRECTION

                    obj.save(
                        update_fields=["public_verified_at", "status", "updated_at"]
                    )

                    register_audit_log(
                        obj,
                        action="public_access_verified",
                        performed_by=None,
                        detail="Public access code verified successfully.",
                    )
                    return redirect(_public_step_url(token, PUBLIC_STEP_ACCEPT))

                form.add_error("access_code", "Invalid access code.")

            return _public_render(
                request,
                obj,
                PUBLIC_STEP_VERIFY,
                access_form=form,
            )

        if not _public_is_verified(request, obj):
            return render(
                request,
                "ombording/public_link_closed.html",
                {
                    "obj": obj,
                    "closed_title": "Onboarding not available",
                    "closed_message": (
                        "This onboarding link is not currently available. "
                        "Please request a new access from your company contact."
                    ),
                },
                status=410,
            )

        if action == "accept_documents":
            form = PublicAcceptanceForm(request.POST)
            if form.is_valid():
                _public_mark_accepted(request, obj)
                register_audit_log(
                    obj,
                    action="public_documents_accepted",
                    performed_by=None,
                    detail="Worker reviewed and accepted base documents.",
                )
                return redirect(_public_step_url(token, PUBLIC_STEP_PERSONAL))
            return _public_render(
                request,
                obj,
                PUBLIC_STEP_ACCEPT,
                acceptance_form=form,
            )

        if not _public_is_accepted(request, obj):
            return redirect(_public_step_url(token, PUBLIC_STEP_ACCEPT))

        if action == "save_personal":
            form = PublicPersonalForm(request.POST, request.FILES, instance=obj)
            if form.is_valid():
                personal_obj = form.save(commit=False)
                personal_obj.entry_mode = "public_link"
                personal_obj.current_step = OmbordingStep.IDENTITY

                if personal_obj.status not in (
                    OmbordingStatus.IN_CORRECTION,
                    OmbordingStatus.IN_REVIEW,
                    OmbordingStatus.APPROVED,
                ):
                    personal_obj.status = OmbordingStatus.PENDING_USER

                personal_obj.save()

                save_uploaded_documents_from_form(personal_obj, None, request.FILES)

                register_audit_log(
                    personal_obj,
                    action="public_personal_saved",
                    performed_by=None,
                    detail="Personal step completed from public link.",
                )
                messages.success(request, "Personal information saved successfully.")
                return redirect(_public_step_url(token, PUBLIC_STEP_IDENTITY))

            return _public_render(
                request,
                obj,
                PUBLIC_STEP_PERSONAL,
                personal_form=form,
            )

        if action == "save_identity":
            form = PublicIdentityForm(request.POST, request.FILES, instance=obj)
            if form.is_valid():
                identity_obj = form.save(commit=False)
                identity_obj.entry_mode = "public_link"
                identity_obj.current_step = OmbordingStep.BANKING

                if identity_obj.status not in (
                    OmbordingStatus.IN_CORRECTION,
                    OmbordingStatus.IN_REVIEW,
                    OmbordingStatus.APPROVED,
                ):
                    identity_obj.status = OmbordingStatus.PENDING_USER

                identity_obj.save()

                save_uploaded_documents_from_form(identity_obj, None, request.FILES)

                register_audit_log(
                    identity_obj,
                    action="public_identity_saved",
                    performed_by=None,
                    detail="Identity step completed from public link.",
                )
                messages.success(request, "Identity information saved successfully.")
                return redirect(_public_step_url(token, PUBLIC_STEP_TAX_BANKING))

            return _public_render(
                request,
                obj,
                PUBLIC_STEP_IDENTITY,
                identity_form=form,
            )

        if action == "save_tax_banking":
            form = PublicTaxBankingForm(request.POST, request.FILES, instance=obj)
            if form.is_valid():
                tax_obj = form.save(commit=False)
                tax_obj.entry_mode = "public_link"
                tax_obj.current_step = OmbordingStep.SIGNATURE

                if tax_obj.status not in (
                    OmbordingStatus.IN_CORRECTION,
                    OmbordingStatus.IN_REVIEW,
                    OmbordingStatus.APPROVED,
                ):
                    tax_obj.status = OmbordingStatus.PENDING_USER

                tax_obj.save()

                register_audit_log(
                    tax_obj,
                    action="public_tax_banking_saved",
                    performed_by=None,
                    detail="Tax and banking step completed from public link.",
                )
                messages.success(
                    request, "Tax and banking information saved successfully."
                )
                return redirect(_public_step_url(token, PUBLIC_STEP_SIGNATURE))

            return _public_render(
                request,
                obj,
                PUBLIC_STEP_TAX_BANKING,
                tax_banking_form=form,
            )

        if action == "save_signature":
            if not (
                obj.is_personal_complete()
                and obj.is_identity_complete()
                and obj.is_banking_complete()
            ):
                messages.error(
                    request,
                    "Please complete all previous steps before signing.",
                )
                return redirect(_public_step_url(token, PUBLIC_STEP_PERSONAL))

            form = PublicSignatureForm(request.POST)
            if form.is_valid():
                signature_dataurl = (
                    form.cleaned_data.get("signature_dataurl") or ""
                ).strip()
                signature_name = (
                    form.cleaned_data.get("signature_name") or obj.full_name or ""
                ).strip()

                if not signature_dataurl.startswith("data:image"):
                    form.add_error(None, "Please draw your signature before saving.")
                    return _public_render(
                        request,
                        obj,
                        PUBLIC_STEP_SIGNATURE,
                        signature_form=form,
                    )

                try:
                    _, b64_data = signature_dataurl.split(",", 1)
                    image_bytes = base64.b64decode(b64_data)
                except Exception:
                    form.add_error(None, "Invalid signature data.")
                    return _public_render(
                        request,
                        obj,
                        PUBLIC_STEP_SIGNATURE,
                        signature_form=form,
                    )

                if not image_bytes:
                    form.add_error(None, "The signature image is empty.")
                    return _public_render(
                        request,
                        obj,
                        PUBLIC_STEP_SIGNATURE,
                        signature_form=form,
                    )

                def _build_initials(full_name, fallback_first="", fallback_last=""):
                    text = (full_name or "").strip()
                    if text:
                        parts = [p for p in text.split() if p.strip()]
                        if len(parts) >= 2:
                            return f"{parts[0][0]}{parts[-1][0]}".upper()
                        if len(parts) == 1:
                            return parts[0][:2].upper()
                    a = (fallback_first or "").strip()[:1].upper()
                    b = (fallback_last or "").strip()[:1].upper()
                    return f"{a}{b}".strip()

                signature_obj, _ = OmbordingSignature.objects.get_or_create(
                    ombording=obj
                )

                if signature_obj.signature_file:
                    try:
                        signature_obj.signature_file.delete(save=False)
                    except Exception:
                        pass

                signature_obj.signature_name = signature_name or obj.full_name
                signature_obj.initials = _build_initials(
                    signature_obj.signature_name,
                    fallback_first=obj.first_name,
                    fallback_last=obj.last_name,
                )

                filename = "signature.png"
                signature_obj.signature_file.save(
                    filename,
                    ContentFile(image_bytes, name=filename),
                    save=False,
                )
                signature_obj.save()

                obj.worker_signed_at = timezone.now()
                obj.current_step = OmbordingStep.REVIEW
                obj.status = OmbordingStatus.IN_REVIEW
                obj.rejection_note = ""
                if not obj.submitted_at:
                    obj.submitted_at = timezone.now()

                obj.link_expires_at = timezone.now() + timezone.timedelta(days=7)

                obj.save(
                    update_fields=[
                        "worker_signed_at",
                        "current_step",
                        "status",
                        "rejection_note",
                        "submitted_at",
                        "link_expires_at",
                        "updated_at",
                    ]
                )

                generate_filled_documents(obj, user=None)

                _public_allow_downloads(request, obj)
                _public_clear_verified(request, obj)

                register_audit_log(
                    obj,
                    action="public_signature_saved",
                    performed_by=None,
                    detail="Worker completed onboarding and sent it to review from the public link.",
                )

                messages.success(
                    request, "Your onboarding was sent for review successfully."
                )

                return _public_render(
                    request,
                    obj,
                    PUBLIC_STEP_SIGNATURE,
                    public_completed=True,
                )

            return _public_render(
                request,
                obj,
                PUBLIC_STEP_SIGNATURE,
                signature_form=form,
            )

    if step == PUBLIC_STEP_VERIFY:
        return _public_render(request, obj, PUBLIC_STEP_VERIFY)

    if step == PUBLIC_STEP_ACCEPT:
        return _public_render(request, obj, PUBLIC_STEP_ACCEPT)

    if step == PUBLIC_STEP_PERSONAL:
        return _public_render(
            request,
            obj,
            PUBLIC_STEP_PERSONAL,
            personal_form=PublicPersonalForm(instance=obj),
        )

    if step == PUBLIC_STEP_IDENTITY:
        return _public_render(
            request,
            obj,
            PUBLIC_STEP_IDENTITY,
            identity_form=PublicIdentityForm(instance=obj),
        )

    if step == PUBLIC_STEP_TAX_BANKING:
        return _public_render(
            request,
            obj,
            PUBLIC_STEP_TAX_BANKING,
            tax_banking_form=PublicTaxBankingForm(instance=obj),
        )

    return _public_render(
        request,
        obj,
        PUBLIC_STEP_SIGNATURE,
        signature_form=PublicSignatureForm(initial={"signature_name": obj.full_name}),
    )


@login_required
def ombording_pause(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)

    obj.link_expires_at = timezone.now()
    obj.status = OmbordingStatus.EXPIRED
    obj.public_verified_at = None
    obj.save(
        update_fields=[
            "link_expires_at",
            "status",
            "public_verified_at",
            "updated_at",
        ]
    )

    register_audit_log(
        obj,
        "paused",
        request.user,
        "Ombording paused manually from admin panel.",
    )
    messages.success(request, "Ombording paused successfully.")
    return redirect("ombording:ombording_list")


@login_required
@require_POST
def ombording_delete(request, pk):
    obj = get_object_or_404(Ombording, pk=pk)

    register_audit_log(
        obj,
        action="ombording_deleted",
        performed_by=request.user,
        detail="Ombording deleted from admin panel.",
    )

    obj.delete()
    messages.success(request, "Ombording deleted successfully.")
    return redirect("ombording:ombording_list")
