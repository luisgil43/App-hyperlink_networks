import html
import io
import mimetypes
import os
import re
import tempfile
import threading
import urllib.request
import zipfile
from urllib.parse import quote, urlparse

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.files import File
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.test import Client as DjangoTestClient
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST

from .forms import DeliveryPackageForm
from .models import (ClientProjectAssignment, DeliveryAccessLog,
                     DeliveryPackage, DeliveryPackageFile, DeliveryZipJob)
from .permissions import (can_manage_deliverables, can_publish_deliverables,
                          can_revoke_deliverables, can_view_client_portal,
                          filter_delivery_packages_by_user,
                          user_can_access_delivery_package)
from .services import discover_project_deliverables
from .utils import log_delivery_access, package_or_404_if_unavailable

# ============================================================
# Helpers
# ============================================================


class _ZipBuildRequestContext:
    def __init__(self, host, port):
        self._host = host
        self._port = port

    def get_host(self):
        return self._host

    def get_port(self):
        return self._port


def _safe_project_folder_name(project_id):
    raw = str(project_id or "").strip()
    slug = slugify(raw).replace("-", "_") or "no_project_id"
    return f"Project_{slug}"


def _clean_filename_piece(value, fallback="deliverable"):
    value = str(value or "").strip()
    value = html.unescape(value)
    value = value.replace("\\", "_").replace("/", "_")

    base, ext = os.path.splitext(value)

    base = slugify(base).replace("-", "_") or fallback
    ext = (ext or "").lower()

    return base, ext


def _extension_from_content_type(content_type):
    ct = (content_type or "").lower()

    if "spreadsheetml.sheet" in ct:
        return ".xlsx"
    if "application/vnd.ms-excel" in ct:
        return ".xls"
    if "text/csv" in ct:
        return ".csv"
    if "application/pdf" in ct:
        return ".pdf"
    if "wordprocessingml.document" in ct:
        return ".docx"
    if "application/msword" in ct:
        return ".doc"
    if "application/zip" in ct or "x-zip-compressed" in ct:
        return ".zip"
    if "image/jpeg" in ct:
        return ".jpg"
    if "image/png" in ct:
        return ".png"
    if "image/webp" in ct:
        return ".webp"

    return ""


def _extension_from_file_type(file_obj):
    file_type = str(getattr(file_obj, "file_type", "") or "").strip()

    if file_type == DeliveryPackageFile.FILE_CLIENT_REPORT:
        return ".xlsx"
    if file_type == DeliveryPackageFile.FILE_OPERATIONAL_REPORT:
        return ".xlsx"
    if file_type == DeliveryPackageFile.FILE_LIGHT_LEVELS:
        return ".xlsx"
    if file_type == DeliveryPackageFile.FILE_PHOTO_REPORT:
        return ".pdf"
    if file_type == DeliveryPackageFile.FILE_PHOTOS_ZIP:
        return ".zip"

    return ""


def _filename_from_content_disposition(content_disposition):
    header = str(content_disposition or "")

    if not header:
        return ""

    match_star = re.search(
        r"filename\*\s*=\s*UTF-8''([^;]+)",
        header,
        flags=re.IGNORECASE,
    )

    if match_star:
        try:
            from urllib.parse import unquote

            return unquote(match_star.group(1).strip().strip('"'))
        except Exception:
            return match_star.group(1).strip().strip('"')

    match = re.search(
        r'filename\s*=\s*"([^"]+)"',
        header,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    match_plain = re.search(
        r"filename\s*=\s*([^;]+)",
        header,
        flags=re.IGNORECASE,
    )

    if match_plain:
        return match_plain.group(1).strip().strip('"')

    return ""


def _final_download_filename(file_obj, response_filename="", content_type=""):
    candidates = []

    if response_filename:
        candidates.append(response_filename)

    if file_obj.file and getattr(file_obj.file, "name", ""):
        candidates.append(os.path.basename(file_obj.file.name))

    candidates.append(file_obj.safe_filename())
    candidates.append(file_obj.display_name)

    forced_ext = _extension_from_content_type(
        content_type
    ) or _extension_from_file_type(file_obj)

    for candidate in candidates:
        base, ext = _clean_filename_piece(candidate)

        if ext:
            return f"{base}{ext}"

        if forced_ext:
            return f"{base}{forced_ext}"

    base, _ = _clean_filename_piece(file_obj.display_name or "deliverable")

    if forced_ext:
        return f"{base}{forced_ext}"

    return f"{base}.bin"


def _safe_zip_filename(name, fallback="deliverable"):
    base, ext = _clean_filename_piece(name, fallback=fallback)

    if not ext:
        ext = ".bin"

    return f"{base}{ext}"


def _unique_name(existing_names, filename):
    filename = _safe_zip_filename(filename)
    base, ext = os.path.splitext(filename)

    candidate = filename
    counter = 2

    while candidate in existing_names:
        candidate = f"{base}_{counter}{ext}"
        counter += 1

    existing_names.add(candidate)
    return candidate


def _response_to_bytes(response):
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content)

    return response.content


def _internal_source_path(request_context, source_url):
    source_url = html.unescape(str(source_url or "").strip())

    if not source_url:
        return ""

    parsed = urlparse(source_url)

    if parsed.scheme and parsed.netloc:
        current_host = request_context.get_host()

        if parsed.netloc != current_host:
            return ""

        path = parsed.path or "/"

        if parsed.query:
            path = f"{path}?{parsed.query}"

        return path

    if source_url.startswith("/"):
        return source_url

    return "/" + source_url


def _fetch_delivery_file_bytes(request_context, package, file_obj):
    fallback_filename = _final_download_filename(file_obj)

    if file_obj.file:
        try:
            with file_obj.file.open("rb") as fh:
                content = fh.read()

            content_type = (
                mimetypes.guess_type(file_obj.file.name)[0]
                or mimetypes.guess_type(fallback_filename)[0]
                or "application/octet-stream"
            )

            filename = _final_download_filename(
                file_obj,
                response_filename=os.path.basename(file_obj.file.name),
                content_type=content_type,
            )

            return {
                "ok": True,
                "content": content,
                "content_type": content_type,
                "filename": filename,
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Could not read stored file: {e}",
            }

    source_url = html.unescape(str(file_obj.source_url or "").strip())

    if not source_url:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Missing source URL.",
        }

    parsed = urlparse(source_url)

    if (
        parsed.scheme in ("http", "https")
        and parsed.netloc != request_context.get_host()
    ):
        try:
            with urllib.request.urlopen(source_url, timeout=120) as response:
                content = response.read()
                content_type = response.headers.get(
                    "Content-Type",
                    "application/octet-stream",
                )
                cd = response.headers.get("Content-Disposition", "")

            response_filename = _filename_from_content_disposition(cd)

            filename = _final_download_filename(
                file_obj,
                response_filename=response_filename,
                content_type=content_type,
            )

            return {
                "ok": True,
                "content": content,
                "content_type": content_type or "application/octet-stream",
                "filename": filename,
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Could not fetch external source: {e}",
            }

    internal_path = _internal_source_path(request_context, source_url)

    if not internal_path:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Invalid internal source URL.",
        }

    if internal_path.startswith("/client-deliverables/"):
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Invalid recursive delivery source URL.",
        }

    try:
        client = DjangoTestClient(
            HTTP_HOST=request_context.get_host(),
            SERVER_NAME=request_context.get_host().split(":")[0],
            SERVER_PORT=request_context.get_port() or "80",
        )

        if package.created_by_id and package.created_by:
            client.force_login(package.created_by)

        response = client.get(
            internal_path,
            follow=True,
            HTTP_HOST=request_context.get_host(),
        )

        if response.status_code != 200:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Internal source returned status {response.status_code}.",
            }

        content = _response_to_bytes(response)

        content_type = response.get("Content-Type", "") or "application/octet-stream"
        cd = response.get("Content-Disposition", "")

        if "text/html" in content_type.lower():
            return {
                "ok": False,
                "content": b"",
                "content_type": content_type,
                "filename": fallback_filename,
                "error": "Internal source returned HTML instead of a downloadable file.",
            }

        response_filename = _filename_from_content_disposition(cd)

        filename = _final_download_filename(
            file_obj,
            response_filename=response_filename,
            content_type=content_type,
        )

        return {
            "ok": True,
            "content": content,
            "content_type": content_type,
            "filename": filename,
            "error": "",
        }

    except Exception as e:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": f"Could not generate internal source: {e}",
        }


def _build_delivery_zip_job(job_id, host, port):
    job = None
    temp_path = None

    try:
        job = DeliveryZipJob.objects.select_related(
            "package",
            "package__created_by",
        ).get(pk=job_id)

        package = job.package

        job.status = DeliveryZipJob.STATUS_PROCESSING
        job.started_at = timezone.now()
        job.error_message = ""
        job.errors = []
        job.save(
            update_fields=[
                "status",
                "started_at",
                "error_message",
                "errors",
            ]
        )

        files = list(
            package.files.filter(is_active=True).order_by("project_id", "order", "id")
        )

        job.total_files = len(files)
        job.save(update_fields=["total_files"])

        if not files:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.error_message = "This package does not have active deliverables."
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])
            return

        request_context = _ZipBuildRequestContext(host=host, port=port)

        used_names_by_folder = {}
        total_files_added = 0
        errors = []

        safe_package_name = (
            slugify(package.name).replace("-", "_") or "delivery_package"
        )
        final_filename = f"{safe_package_name}.zip"

        fd, temp_path = tempfile.mkstemp(suffix=".zip")
        os.close(fd)

        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_obj in files:
                project_id = str(file_obj.project_id or "NO_PROJECT_ID").strip()
                folder_name = _safe_project_folder_name(project_id)

                if folder_name not in used_names_by_folder:
                    used_names_by_folder[folder_name] = set()

                result = _fetch_delivery_file_bytes(request_context, package, file_obj)

                if not result["ok"]:
                    errors.append(
                        {
                            "project_id": project_id,
                            "display_name": file_obj.display_name,
                            "source_url": file_obj.source_url,
                            "error": result["error"],
                        }
                    )
                    continue

                filename = _unique_name(
                    used_names_by_folder[folder_name],
                    result["filename"],
                )

                zip_file.writestr(
                    f"{folder_name}/{filename}",
                    result["content"],
                )

                total_files_added += 1

            manifest_lines = []
            manifest_lines.append("Hyperlink Networks - Delivery Package")
            manifest_lines.append("")
            manifest_lines.append(f"Package: {package.name}")
            manifest_lines.append(
                f"Generated at: {timezone.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
            manifest_lines.append("")
            manifest_lines.append(f"Files included: {total_files_added}")
            manifest_lines.append(f"Files with errors: {len(errors)}")
            manifest_lines.append("")

            if errors:
                manifest_lines.append("Errors:")
                for error in errors:
                    manifest_lines.append("")
                    manifest_lines.append(f"Project ID: {error['project_id']}")
                    manifest_lines.append(f"Deliverable: {error['display_name']}")
                    manifest_lines.append(f"Source: {error['source_url']}")
                    manifest_lines.append(f"Error: {error['error']}")

            zip_file.writestr(
                "README.txt",
                "\n".join(manifest_lines),
            )

        if total_files_added == 0:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.files_added = 0
            job.files_failed = len(errors)
            job.errors = errors
            job.error_message = (
                "The ZIP could not be generated because none of the deliverables "
                "could be downloaded."
            )
            job.finished_at = timezone.now()
            job.save(
                update_fields=[
                    "status",
                    "files_added",
                    "files_failed",
                    "errors",
                    "error_message",
                    "finished_at",
                ]
            )
            return

        with open(temp_path, "rb") as fh:
            job.zip_file.save(final_filename, File(fh), save=False)

        job.status = DeliveryZipJob.STATUS_READY
        job.filename = final_filename
        job.files_added = total_files_added
        job.files_failed = len(errors)
        job.errors = errors
        job.finished_at = timezone.now()
        job.error_message = ""
        job.save(
            update_fields=[
                "status",
                "zip_file",
                "filename",
                "files_added",
                "files_failed",
                "errors",
                "finished_at",
                "error_message",
            ]
        )

    except Exception as e:
        if job:
            job.status = DeliveryZipJob.STATUS_FAILED
            job.error_message = str(e)
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "finished_at"])

    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _start_zip_job_background(job, request):
    host = request.get_host()
    port = request.get_port() or "80"

    thread = threading.Thread(
        target=_build_delivery_zip_job,
        args=(str(job.id), host, port),
        daemon=True,
    )
    thread.start()


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


def _detail_url_with_project(package, project_id, anchor="load-deliverables-section"):
    base = reverse("client_deliverables:admin_package_detail", args=[package.id])
    project_id = str(project_id or "").strip()

    if project_id:
        url = f"{base}?project_id={quote(project_id)}"
    else:
        url = base

    if anchor:
        url = f"{url}#{anchor}"

    return url


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

    for package in packages:
        package.public_url_for_copy = _package_public_url(request, package)
        package.visible_access_key = getattr(package, "access_key_plain", "") or ""

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
    recommended_session_key = "delivery_package_recommended_key_new"

    if not request.session.get(recommended_session_key):
        request.session[recommended_session_key] = DeliveryPackage.generate_access_key()
        request.session.modified = True

    recommended_access_key = request.session.get(recommended_session_key, "")

    if request.method == "POST":
        posted_recommended_key = (
            request.POST.get("recommended_access_key")
            or recommended_access_key
            or DeliveryPackage.generate_access_key()
        )

        form = DeliveryPackageForm(
            request.POST,
            recommended_access_key=posted_recommended_key,
        )

        if form.is_valid():
            package = form.save(commit=False)
            package.created_by = request.user
            package.save()

            generated_key = form.generated_key

            if generated_key:
                request.session[f"delivery_package_key_{package.id}"] = generated_key

            request.session.pop(recommended_session_key, None)
            request.session.modified = True

            messages.success(request, "Delivery package created successfully.")
            return redirect("client_deliverables:admin_package_detail", pk=package.id)
    else:
        form = DeliveryPackageForm(
            recommended_access_key=recommended_access_key,
            initial={
                "recommended_access_key": recommended_access_key,
            },
        )

    return render(
        request,
        "client_deliverables/admin_package_form.html",
        {
            "form": form,
            "mode": "create",
            "generated_key": generated_key,
            "recommended_access_key": recommended_access_key,
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
    visible_access_key = getattr(package, "access_key_plain", "") or generated_key or ""

    outlook_subject = _build_outlook_subject(package)
    outlook_body = _build_outlook_body(request, package)

    outlook_href = "mailto:?subject={}&body={}".format(
        quote(outlook_subject),
        quote(outlook_body),
    )

    project_lookup_id = (request.GET.get("project_id") or "").strip()
    project_lookup = None

    selected_source_keys = set(
        package.files.filter(is_active=True)
        .exclude(source_key="")
        .values_list("source_key", flat=True)
    )

    if project_lookup_id:
        project_lookup = discover_project_deliverables(
            request.user,
            project_lookup_id,
        )

    return render(
        request,
        "client_deliverables/admin_package_detail.html",
        {
            "package": package,
            "public_url": public_url,
            "grouped": grouped,
            "generated_key": generated_key,
            "visible_access_key": visible_access_key,
            "outlook_subject": outlook_subject,
            "outlook_body": outlook_body,
            "outlook_href": outlook_href,
            "project_lookup_id": project_lookup_id,
            "project_lookup": project_lookup,
            "selected_source_keys": selected_source_keys,
        },
    )


@login_required
@require_POST
def admin_package_add_selected(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    if package.status == DeliveryPackage.STATUS_REVOKED:
        messages.error(request, "You cannot add deliverables to a revoked package.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="package-actions-section",
            )
        )

    project_id = (request.POST.get("project_id") or "").strip()
    selected_keys = request.POST.getlist("selected_deliverables")

    if not project_id:
        messages.error(request, "Project ID is required.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="load-deliverables-section",
            )
        )

    if not selected_keys:
        messages.error(request, "Select at least one deliverable.")
        return redirect(
            _detail_url_with_project(
                package,
                project_id,
                anchor="load-deliverables-section",
            )
        )

    project_lookup = discover_project_deliverables(request.user, project_id)

    if not project_lookup or not project_lookup.get("ok"):
        messages.error(
            request,
            project_lookup.get("message", "Project deliverables are not available."),
        )
        return redirect(
            _detail_url_with_project(
                package,
                project_id,
                anchor="load-deliverables-section",
            )
        )

    available_by_key = {
        item["source_key"]: item for item in project_lookup.get("deliverables", [])
    }

    created_count = 0
    skipped_count = 0

    for source_key in selected_keys:
        item = available_by_key.get(source_key)

        if not item:
            skipped_count += 1
            continue

        exists = DeliveryPackageFile.objects.filter(
            package=package,
            source_key=item["source_key"],
            is_active=True,
        ).exists()

        if exists:
            skipped_count += 1
            continue

        DeliveryPackageFile.objects.create(
            package=package,
            project_id=item["project_id"],
            file_type=item["file_type"],
            display_name=item["title"],
            source_url=item["source_url"],
            source_key=item["source_key"],
            order=package.files.count() + 1,
            is_active=True,
            created_by=request.user,
        )

        created_count += 1

    if created_count:
        messages.success(
            request,
            f"{created_count} deliverable(s) added to the package.",
        )

    if skipped_count:
        messages.warning(
            request,
            f"{skipped_count} deliverable(s) were skipped because they were already added or unavailable.",
        )

    return redirect(
        _detail_url_with_project(
            package,
            project_id,
            anchor="included-deliverables-section",
        )
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
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="included-deliverables-section",
            )
        )

    package.publish(user=request.user)
    package.save()

    messages.success(request, "Delivery package published successfully.")
    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="package-actions-section",
        )
    )


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
    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="package-actions-section",
        )
    )


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


@login_required
@require_POST
def admin_package_delete_file(request, pk, file_id):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    file_obj = get_object_or_404(
        DeliveryPackageFile,
        pk=file_id,
        package=package,
    )

    project_id = file_obj.project_id
    display_name = file_obj.display_name

    file_obj.delete()

    messages.warning(
        request,
        f"Deliverable removed: {display_name} / Project ID {project_id}.",
    )

    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="included-deliverables-section",
        )
    )


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
            "client_deliverables:public_package_unlock",
            token=package.token,
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
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    if _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
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


def _safe_project_folder_name(project_id):
    raw = str(project_id or "").strip()
    slug = slugify(raw).replace("-", "_") or "no_project_id"
    return f"Project_{slug}"


def _clean_filename_piece(value, fallback="deliverable"):
    value = str(value or "").strip()
    value = html.unescape(value)
    value = value.replace("\\", "_").replace("/", "_")

    base, ext = os.path.splitext(value)

    base = slugify(base).replace("-", "_") or fallback
    ext = (ext or "").lower()

    return base, ext


def _extension_from_content_type(content_type):
    ct = (content_type or "").lower()

    if "spreadsheetml.sheet" in ct:
        return ".xlsx"

    if "application/vnd.ms-excel" in ct:
        return ".xls"

    if "text/csv" in ct:
        return ".csv"

    if "application/pdf" in ct:
        return ".pdf"

    if "wordprocessingml.document" in ct:
        return ".docx"

    if "application/msword" in ct:
        return ".doc"

    if "application/zip" in ct or "x-zip-compressed" in ct:
        return ".zip"

    if "image/jpeg" in ct:
        return ".jpg"

    if "image/png" in ct:
        return ".png"

    if "image/webp" in ct:
        return ".webp"

    return ""


def _extension_from_file_type(file_obj):
    file_type = str(getattr(file_obj, "file_type", "") or "").strip()

    if file_type == DeliveryPackageFile.FILE_CLIENT_REPORT:
        return ".xlsx"

    if file_type == DeliveryPackageFile.FILE_OPERATIONAL_REPORT:
        return ".xlsx"

    if file_type == DeliveryPackageFile.FILE_LIGHT_LEVELS:
        return ".xlsx"

    if file_type == DeliveryPackageFile.FILE_PHOTO_REPORT:
        return ".pdf"

    if file_type == DeliveryPackageFile.FILE_PHOTOS_ZIP:
        return ".zip"

    return ""


def _filename_from_content_disposition(content_disposition):
    header = str(content_disposition or "")

    if not header:
        return ""

    match_star = re.search(
        r"filename\*\s*=\s*UTF-8''([^;]+)",
        header,
        flags=re.IGNORECASE,
    )

    if match_star:
        try:
            from urllib.parse import unquote

            return unquote(match_star.group(1).strip().strip('"'))
        except Exception:
            return match_star.group(1).strip().strip('"')

    match = re.search(
        r'filename\s*=\s*"([^"]+)"',
        header,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).strip()

    match_plain = re.search(
        r"filename\s*=\s*([^;]+)",
        header,
        flags=re.IGNORECASE,
    )

    if match_plain:
        return match_plain.group(1).strip().strip('"')

    return ""


def _final_download_filename(file_obj, response_filename="", content_type=""):
    """
    Define nombre final con extensión correcta.

    Prioridad:
    1. filename del Content-Disposition de la vista interna.
    2. nombre del archivo físico.
    3. safe_filename del modelo.
    4. display_name + extensión por tipo.
    """
    candidates = []

    if response_filename:
        candidates.append(response_filename)

    if file_obj.file and getattr(file_obj.file, "name", ""):
        candidates.append(os.path.basename(file_obj.file.name))

    candidates.append(file_obj.safe_filename())
    candidates.append(file_obj.display_name)

    forced_ext = _extension_from_content_type(
        content_type
    ) or _extension_from_file_type(file_obj)

    for candidate in candidates:
        base, ext = _clean_filename_piece(candidate)

        if ext:
            return f"{base}{ext}"

        if forced_ext:
            return f"{base}{forced_ext}"

    base, _ = _clean_filename_piece(file_obj.display_name or "deliverable")

    if forced_ext:
        return f"{base}{forced_ext}"

    return f"{base}.bin"


def _safe_zip_filename(name, fallback="deliverable"):
    base, ext = _clean_filename_piece(name, fallback=fallback)

    if not ext:
        ext = ".bin"

    return f"{base}{ext}"


def _unique_name(existing_names, filename):
    filename = _safe_zip_filename(filename)
    base, ext = os.path.splitext(filename)

    candidate = filename
    counter = 2

    while candidate in existing_names:
        candidate = f"{base}_{counter}{ext}"
        counter += 1

    existing_names.add(candidate)
    return candidate


def _response_to_bytes(response):
    if hasattr(response, "streaming_content"):
        return b"".join(response.streaming_content)

    return response.content


def _internal_source_path(request, source_url):
    """
    Convierte source_url a path interno para DjangoTestClient.

    Soporta:
      /operaciones/...
      http://127.0.0.1:8000/operaciones/...
      https://dominio.com/operaciones/...
    """
    source_url = html.unescape(str(source_url or "").strip())

    if not source_url:
        return ""

    parsed = urlparse(source_url)

    if parsed.scheme and parsed.netloc:
        current_host = request.get_host()

        if parsed.netloc != current_host:
            return ""

        path = parsed.path or "/"

        if parsed.query:
            path = f"{path}?{parsed.query}"

        return path

    if source_url.startswith("/"):
        return source_url

    return "/" + source_url


def _fetch_delivery_file_bytes(request, package, file_obj):
    """
    Obtiene el archivo real para un entregable.

    Soporta:
    - file_obj.file
    - source_url externa pública
    - source_url interna protegida usando force_login(package.created_by)

    Retorna dict con:
      ok, content, content_type, filename, error
    """
    fallback_filename = _final_download_filename(file_obj)

    # ============================================================
    # 1) Archivo físico guardado en storage
    # ============================================================
    if file_obj.file:
        try:
            with file_obj.file.open("rb") as fh:
                content = fh.read()

            content_type = (
                mimetypes.guess_type(file_obj.file.name)[0]
                or mimetypes.guess_type(fallback_filename)[0]
                or "application/octet-stream"
            )

            filename = _final_download_filename(
                file_obj,
                response_filename=os.path.basename(file_obj.file.name),
                content_type=content_type,
            )

            return {
                "ok": True,
                "content": content,
                "content_type": content_type,
                "filename": filename,
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Could not read stored file: {e}",
            }

    source_url = html.unescape(str(file_obj.source_url or "").strip())

    if not source_url:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Missing source URL.",
        }

    parsed = urlparse(source_url)

    # ============================================================
    # 2) URL externa pública
    # ============================================================
    if parsed.scheme in ("http", "https") and parsed.netloc != request.get_host():
        try:
            with urllib.request.urlopen(source_url, timeout=120) as response:
                content = response.read()
                content_type = response.headers.get(
                    "Content-Type",
                    "application/octet-stream",
                )
                cd = response.headers.get("Content-Disposition", "")

            response_filename = _filename_from_content_disposition(cd)

            filename = _final_download_filename(
                file_obj,
                response_filename=response_filename,
                content_type=content_type,
            )

            return {
                "ok": True,
                "content": content,
                "content_type": content_type or "application/octet-stream",
                "filename": filename,
                "error": "",
            }

        except Exception as e:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Could not fetch external source: {e}",
            }

    # ============================================================
    # 3) URL interna protegida de Hyperlink
    # ============================================================
    internal_path = _internal_source_path(request, source_url)

    if not internal_path:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Invalid internal source URL.",
        }

    if internal_path.startswith("/client-deliverables/"):
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": "Invalid recursive delivery source URL.",
        }

    try:
        client = DjangoTestClient(
            HTTP_HOST=request.get_host(),
            SERVER_NAME=request.get_host().split(":")[0],
            SERVER_PORT=request.get_port() or "80",
        )

        if package.created_by_id and package.created_by:
            client.force_login(package.created_by)

        response = client.get(
            internal_path,
            follow=True,
            HTTP_HOST=request.get_host(),
        )

        if response.status_code != 200:
            return {
                "ok": False,
                "content": b"",
                "content_type": "application/octet-stream",
                "filename": fallback_filename,
                "error": f"Internal source returned status {response.status_code}.",
            }

        content = _response_to_bytes(response)

        content_type = response.get("Content-Type", "") or "application/octet-stream"
        cd = response.get("Content-Disposition", "")

        # Si terminó en HTML, normalmente cayó en login o en una página de error.
        if "text/html" in content_type.lower():
            return {
                "ok": False,
                "content": b"",
                "content_type": content_type,
                "filename": fallback_filename,
                "error": "Internal source returned HTML instead of a downloadable file. It may require a permission or a different export endpoint.",
            }

        response_filename = _filename_from_content_disposition(cd)

        filename = _final_download_filename(
            file_obj,
            response_filename=response_filename,
            content_type=content_type,
        )

        return {
            "ok": True,
            "content": content,
            "content_type": content_type,
            "filename": filename,
            "error": "",
        }

    except Exception as e:
        return {
            "ok": False,
            "content": b"",
            "content_type": "application/octet-stream",
            "filename": fallback_filename,
            "error": f"Could not generate internal source: {e}",
        }


def public_download_file(request, token, file_id):
    package = get_object_or_404(
        DeliveryPackage.objects.select_related("created_by"),
        token=token,
    )

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
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

    result = _fetch_delivery_file_bytes(request, package, file_obj)

    if not result["ok"]:
        messages.error(
            request,
            f"This deliverable could not be downloaded. {result['error']}",
        )
        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    filename = _safe_zip_filename(result["filename"], fallback="deliverable")

    response = HttpResponse(
        result["content"],
        content_type=result["content_type"] or "application/octet-stream",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["X-Content-Type-Options"] = "nosniff"

    return response


def public_download_all(request, token):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    files_count = package.files.filter(is_active=True).count()

    if not files_count:
        messages.error(request, "This package does not have active deliverables.")
        return redirect(
            "client_deliverables:public_package_detail",
            token=package.token,
        )

    job = DeliveryZipJob.objects.create(
        package=package,
        status=DeliveryZipJob.STATUS_PENDING,
        total_files=files_count,
        requested_by=request.user if request.user.is_authenticated else None,
    )

    log_delivery_access(
        request,
        package,
        DeliveryAccessLog.ACTION_DOWNLOAD_ALL,
        extra={"zip_job_id": str(job.id)},
    )

    _start_zip_job_background(job, request)

    return redirect(
        "client_deliverables:public_download_all_status",
        token=package.token,
        job_id=job.id,
    )


def public_download_all_status(request, token, job_id):
    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        token=token,
    )

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    job = get_object_or_404(
        DeliveryZipJob,
        pk=job_id,
        package=package,
    )

    return render(
        request,
        "client_deliverables/public_download_all_status.html",
        {
            "package": package,
            "job": job,
        },
    )


def public_download_all_status_json(request, token, job_id):
    package = get_object_or_404(DeliveryPackage, token=token)

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return JsonResponse(
            {
                "ok": False,
                "status": "locked",
                "message": "Access key required.",
            },
            status=403,
        )

    job = get_object_or_404(
        DeliveryZipJob,
        pk=job_id,
        package=package,
    )

    download_url = ""

    if job.is_ready():
        download_url = reverse(
            "client_deliverables:public_download_all_file",
            args=[package.token, job.id],
        )

    return JsonResponse(
        {
            "ok": True,
            "status": job.status,
            "files_added": job.files_added,
            "files_failed": job.files_failed,
            "total_files": job.total_files,
            "error_message": job.error_message,
            "download_url": download_url,
        }
    )


def public_download_all_file(request, token, job_id):
    package = get_object_or_404(DeliveryPackage, token=token)

    package_or_404_if_unavailable(request, package)

    if package.requires_access_key and not _is_package_unlocked(request, package):
        return redirect(
            "client_deliverables:public_package_unlock",
            token=package.token,
        )

    job = get_object_or_404(
        DeliveryZipJob,
        pk=job_id,
        package=package,
    )

    if not job.is_ready():
        messages.error(request, "The ZIP file is not ready yet.")
        return redirect(
            "client_deliverables:public_download_all_status",
            token=package.token,
            job_id=job.id,
        )

    filename = job.filename or f"{slugify(package.name).replace('-', '_')}.zip"

    response = FileResponse(
        job.zip_file.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="application/zip",
    )
    response["X-Content-Type-Options"] = "nosniff"

    return response


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


@login_required
@require_POST
def admin_package_reopen(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to reopen this package.")

    if package.status != DeliveryPackage.STATUS_REVOKED:
        messages.warning(request, "Only revoked packages can be reopened.")
        return redirect(
            _detail_url_with_project(
                package,
                "",
                anchor="package-actions-section",
            )
        )

    package.status = DeliveryPackage.STATUS_DRAFT
    package.revoked_at = None
    package.revoked_by = None
    package.published_at = None
    package.published_by = None
    package.save(
        update_fields=[
            "status",
            "revoked_at",
            "revoked_by",
            "published_at",
            "published_by",
            "updated_at",
        ]
    )

    messages.success(
        request,
        "Package reopened as draft. You can add deliverables now.",
    )

    return redirect(
        _detail_url_with_project(
            package,
            "",
            anchor="package-actions-section",
        )
    )


@login_required
def admin_package_edit(request, pk):
    _require_manager(request.user)

    package = get_object_or_404(
        DeliveryPackage.objects.prefetch_related("files"),
        pk=pk,
    )

    if not user_can_access_delivery_package(request.user, package):
        raise PermissionDenied("You do not have access to this delivery package.")

    generated_key = None
    recommended_session_key = f"delivery_package_recommended_key_{package.id}"

    if not request.session.get(recommended_session_key):
        request.session[recommended_session_key] = DeliveryPackage.generate_access_key()
        request.session.modified = True

    recommended_access_key = request.session.get(recommended_session_key, "")

    if request.method == "POST":
        posted_recommended_key = (
            request.POST.get("recommended_access_key")
            or recommended_access_key
            or DeliveryPackage.generate_access_key()
        )

        form = DeliveryPackageForm(
            request.POST,
            instance=package,
            recommended_access_key=posted_recommended_key,
        )

        if form.is_valid():
            package = form.save(commit=False)
            package.save()

            generated_key = form.generated_key

            if generated_key:
                request.session[f"delivery_package_key_{package.id}"] = generated_key
                request.session.pop(recommended_session_key, None)
                request.session.modified = True

            messages.success(request, "Delivery package updated successfully.")
            return redirect("client_deliverables:admin_package_detail", pk=package.id)
    else:
        form = DeliveryPackageForm(
            instance=package,
            recommended_access_key=recommended_access_key,
            initial={
                "recommended_access_key": recommended_access_key,
            },
        )

    return render(
        request,
        "client_deliverables/admin_package_form.html",
        {
            "form": form,
            "mode": "edit",
            "package": package,
            "generated_key": generated_key,
            "recommended_access_key": recommended_access_key,
        },
    )
