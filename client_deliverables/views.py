from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import DeliveryPackageForm
from .models import (ClientProjectAssignment, DeliveryAccessLog,
                     DeliveryPackage, DeliveryPackageFile)
from .permissions import (can_manage_deliverables, can_publish_deliverables,
                          can_revoke_deliverables, can_view_client_portal,
                          filter_delivery_packages_by_user,
                          user_can_access_delivery_package)
from .utils import log_delivery_access, package_or_404_if_unavailable

# ============================================================
# Helpers
# ============================================================


def _absolute_url(request, path):
    return request.build_absolute_uri(path)


def _require_manager(user):
    if not can_manage_deliverables(user):
        raise PermissionDenied("You do not have permission to manage deliverables.")


def _require_publisher(user):
    if not can_publish_deliverables(user):
        raise PermissionDenied("You do not have permission to publish deliverables.")


def _require_revoker(user):
    if not can_revoke_deliverables(user):
        raise PermissionDenied("You do not have permission to revoke deliverables.")


def _package_public_url(request, package):
    return _absolute_url(
        request,
        reverse("client_deliverables:public_package_detail", args=[package.token]),
    )


def _package_project_summary(package):
    grouped = {}

    files = package.files.filter(is_active=True).order_by("project_id", "order", "id")

    for f in files:
        project_id = (f.project_id or "NO_PROJECT_ID").strip()
        grouped.setdefault(project_id, []).append(f)

    return grouped


def _build_outlook_subject(package):
    project_ids = package.project_ids()

    if len(project_ids) == 1:
        return f"Project Deliverables Available - Project {project_ids[0]}"

    if len(project_ids) > 1:
        joined = " / ".join(project_ids[:4])

        if len(project_ids) > 4:
            joined += f" + {len(project_ids) - 4} more"

        return f"Project Deliverables Available - Projects {joined}"

    return f"Project Deliverables Available - {package.name}"


def _build_outlook_body(request, package):
    public_url = _package_public_url(request, package)
    grouped = _package_project_summary(package)

    lines = []
    lines.append("Hello,")
    lines.append("")
    lines.append("We are sharing the deliverables for the following project(s):")
    lines.append("")

    if grouped:
        for project_id, files in grouped.items():
            lines.append(f"Project ID: {project_id}")
            lines.append("Included deliverables:")

            for f in files:
                lines.append(f"- {f.display_name or f.get_file_type_display()}")

            lines.append("")
    else:
        lines.append(f"Package: {package.name}")
        lines.append("Included deliverables will be available through the link below.")
        lines.append("")

    lines.append("Access link:")
    lines.append(public_url)
    lines.append("")

    if package.expires_at:
        expires_txt = timezone.localtime(package.expires_at).strftime(
            "%m/%d/%Y %I:%M %p"
        )
        lines.append(f"This link will expire on {expires_txt}.")
    else:
        lines.append("This link does not have an expiration date.")

    lines.append("")

    if package.requires_access_key:
        lines.append(
            "For security reasons, the access key will be provided separately."
        )
        lines.append("")

    lines.append("Regards,")
    lines.append("Hyperlink Networks")

    return "\n".join(lines)


def _is_package_unlocked(request, package):
    if not package.requires_access_key:
        return True

    return bool(request.session.get(f"delivery_package_unlocked_{package.id}") is True)


def _mark_package_unlocked(request, package):
    request.session[f"delivery_package_unlocked_{package.id}"] = True
    request.session.modified = True


def _client_has_project_assignment(user, project_id):
    project_id = str(project_id or "").strip()

    if not project_id:
        return False

    return ClientProjectAssignment.objects.filter(
        user=user,
        project_id=project_id,
        is_active=True,
    ).exists()


# ============================================================
# Admin / Internal portal
# ============================================================


@login_required
def admin_package_list(request):
    _require_manager(request.user)

    packages = (
        DeliveryPackage.objects.select_related("created_by", "published_by")
        .prefetch_related("files")
        .order_by("-created_at")
    )

    packages = filter_delivery_packages_by_user(packages, request.user)

    return render(
        request,
        "client_deliverables/admin_package_list.html",
        {
            "packages": packages,
        },
    )


@login_required
def admin_package_create(request):
    _require_manager(request.user)

    generated_key = None

    if request.method == "POST":
        form = DeliveryPackageForm(request.POST)

        if form.is_valid():
            package = form.save(commit=False)
            package.created_by = request.user
            package.save()

            generated_key = form.generated_key

            if generated_key:
                request.session[f"delivery_package_key_{package.id}"] = generated_key

            messages.success(request, "Delivery package created successfully.")
            return redirect("client_deliverables:admin_package_detail", pk=package.id)
    else:
        form = DeliveryPackageForm()

    return render(
        request,
        "client_deliverables/admin_package_form.html",
        {
            "form": form,
            "mode": "create",
            "generated_key": generated_key,
        },
    )


@login_required
def admin_package_detail(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    public_url = _package_public_url(request, package)
    grouped = _package_project_summary(package)

    generated_key = request.session.pop(f"delivery_package_key_{package.id}", None)

    outlook_subject = _build_outlook_subject(package)
    outlook_body = _build_outlook_body(request, package)

    outlook_href = "mailto:?subject={}&body={}".format(
        quote(outlook_subject),
        quote(outlook_body),
    )

    return render(
        request,
        "client_deliverables/admin_package_detail.html",
        {
            "package": package,
            "public_url": public_url,
            "grouped": grouped,
            "generated_key": generated_key,
            "outlook_subject": outlook_subject,
            "outlook_body": outlook_body,
            "outlook_href": outlook_href,
        },
    )


@login_required
@require_POST
def admin_package_publish(request, pk):
    _require_publisher(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to publish this package.")

    if not package.files.filter(is_active=True).exists():
        messages.error(request, "You cannot publish a package without files.")
        return redirect("client_deliverables:admin_package_detail", pk=package.id)

    package.publish(user=request.user)
    package.save()

    messages.success(request, "Delivery package published successfully.")
    return redirect("client_deliverables:admin_package_detail", pk=package.id)


@login_required
@require_POST
def admin_package_revoke(request, pk):
    _require_revoker(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to revoke this package.")

    package.revoke(user=request.user)
    package.save()

    messages.warning(request, "Delivery package revoked.")
    return redirect("client_deliverables:admin_package_detail", pk=package.id)


@login_required
def admin_package_outlook(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    subject = _build_outlook_subject(package)
    body = _build_outlook_body(request, package)

    href = "mailto:?subject={}&body={}".format(
        quote(subject),
        quote(body),
    )

    return redirect(href)


# ============================================================
# Public package link
# ============================================================


def public_package_detail(request, token):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    package_or_404_if_unavailable(request, package)

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_VIEW,
    )

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock", token=package.token
        )

    grouped = _package_project_summary(package)

    return render(
        request,
        "client_deliverables/public_package_detail.html",
        {
            "package": package,
            "grouped": grouped,
        },
    )


def public_package_unlock(request, token):
    package = get_object_or_404(DeliveryPackage, token=token)

    package_or_404_if_unavailable(request, package)

    if not package.requires_access_key:
        return redirect(
            "client_deliverables:public_package_detail", token=package.token
        )

    if _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_detail", token=package.token
        )

    if request.method == "POST":
        access_key = (request.POST.get("access_key") or "").strip()

        if package.check_access_key(access_key):
            package.reset_failed_attempts()
            package.save(update_fields=["failed_attempts", "locked_until"])

            _mark_package_unlocked(request, package)

            log_delivery_access(
                request,
                package,
                DeliveryAccessLog.ACTION_UNLOCK_SUCCESS,
            )

            return redirect(
                "client_deliverables:public_package_detail",
                token=package.token,
            )

        package.register_failed_attempt()
        package.save(update_fields=["failed_attempts", "locked_until"])

        log_delivery_access(
            request,
            package,
            DeliveryAccessLog.ACTION_UNLOCK_FAILED,
        )

        messages.error(request, "Invalid access key.")

        if package.is_locked():
            messages.error(
                request,
                "This link has been temporarily locked due to multiple failed attempts.",
            )

    return render(
        request,
        "client_deliverables/public_package_unlock.html",
        {
            "package": package,
        },
    )


def public_download_file(request, token, file_id):
    package = get_object_or_404(DeliveryPackage, token=token)

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock", token=package.token
        )

    file_obj = get_object_or_404(
        DeliveryPackageFile,
        pk=file_id,
        package=package,
        is_active=True,
    )

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_DOWNLOAD_FILE,
        file=file_obj,
    )

    return render(
        request,
        "client_deliverables/public_placeholder.html",
        {
            "title": "Download file",
            "message": (
                f"Download file pending implementation: "
                f"{file_obj.display_name} / Project {file_obj.project_id}"
            ),
        },
    )


def public_download_all(request, token):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock", token=package.token
        )

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_DOWNLOAD_ALL,
    )

    return render(
        request,
        "client_deliverables/public_placeholder.html",
        {
            "title": "Download all",
            "message": (
                f"Download all pending implementation for package: {package.name}"
            ),
        },
    )


# ============================================================
# Client portal
# ============================================================


@login_required
def client_my_deliverables(request):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    assignments = ClientProjectAssignment.objects.filter(
        user=request.user,
        is_active=True,
    ).order_by("project_id")

    return render(
        request,
        "client_deliverables/client_my_deliverables.html",
        {
            "assignments": assignments,
        },
    )


@login_required
def client_project_search(request):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    project_id = (request.GET.get("project_id") or "").strip()

    if project_id:
        return redirect(
            "client_deliverables:client_project_detail",
            project_id=project_id,
        )

    return redirect("client_deliverables:client_my_deliverables")


@login_required
def client_project_detail(request, project_id):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    project_id = str(project_id or "").strip()

    if not _client_has_project_assignment(request.user, project_id):
        return render(
            request,
            "client_deliverables/client_project_detail.html",
            {
                "project_id": project_id,
                "files": [],
                "packages": [],
                "not_found": True,
            },
        )

    files = (
        DeliveryPackageFile.objects.select_related("package")
        .filter(
            project_id=project_id,
            is_active=True,
            package__status=DeliveryPackage.STATUS_PUBLISHED,
        )
        .exclude(package__status=DeliveryPackage.STATUS_REVOKED)
        .order_by("-package__published_at", "order", "id")
    )

    valid_files = []

    for f in files:
        package = f.package

        if package.is_expired():
            continue

        valid_files.append(f)

    package_ids = list({f.package_id for f in valid_files})

    packages = DeliveryPackage.objects.filter(id__in=package_ids).order_by(
        "-published_at"
    )

    return render(
        request,
        "client_deliverables/client_project_detail.html",
        {
            "project_id": project_id,
            "files": valid_files,
            "packages": packages,
            "not_found": False,
        },
    )


@login_required
def client_project_download_all(request, project_id):
    if not can_view_client_portal(request.user):
        raise PermissionDenied("Only client users can access this portal.")

    project_id = str(project_id or "").strip()

    if not _client_has_project_assignment(request.user, project_id):
        raise PermissionDenied("You do not have access to this project.")

    return render(
        request,
        "client_deliverables/public_placeholder.html",
        {
            "title": "Client download all",
            "message": f"Client download all pending implementation for project {project_id}",
        },
    )
