import time

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect

from plan_reader.models import PlanReaderJob
from plan_reader.services.excel_export_service import \
    build_plan_reader_excel_response
from plan_reader.views.job_views import (can_access_plan_reader,
                                         deny_plan_reader_access)


@login_required
def download_excel(request, job_id):
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    job = get_object_or_404(PlanReaderJob, id=job_id)

    if not job.items.exists():
        messages.warning(
            request,
            "This job does not have detected items yet. Process the PDF before downloading Excel.",
        )
        return redirect("plan_reader:job_detail", job_id=job.id)

    if not job.items.filter(is_duplicate=False).exists():
        messages.warning(
            request,
            "This job does not have included items to export. Review duplicated items before downloading Excel.",
        )
        return redirect("plan_reader:job_detail", job_id=job.id)

    start = time.perf_counter()

    filename, content = build_plan_reader_excel_response(job.id)

    total = time.perf_counter() - start
    size_kb = len(content) / 1024

    print(
        f"[PLAN_READER_EXCEL_DOWNLOAD] job={job.id} "
        f"time={total:.2f}s size={size_kb:.1f}KB filename={filename}"
    )

    response = HttpResponse(
        content,
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"

    return response
