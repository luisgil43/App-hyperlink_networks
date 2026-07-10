from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect


def _can_access_plan_reader(user):
    return user.is_authenticated and getattr(user, "es_admin_general", False)


@login_required
def job_list(request):
    if not _can_access_plan_reader(request.user):
        messages.warning(
            request,
            "Plan Reader is coming soon. Access is currently limited to administrators.",
        )
        return redirect("dashboard_admin:inicio_admin")

    return HttpResponse("Plan Reader - Job list")


@login_required
def job_create(request):
    if not _can_access_plan_reader(request.user):
        messages.warning(
            request,
            "Plan Reader is coming soon. Access is currently limited to administrators.",
        )
        return redirect("dashboard_admin:inicio_admin")

    return HttpResponse("Plan Reader - Create job")


@login_required
def job_detail(request, job_id):
    if not _can_access_plan_reader(request.user):
        messages.warning(
            request,
            "Plan Reader is coming soon. Access is currently limited to administrators.",
        )
        return redirect("dashboard_admin:inicio_admin")

    return HttpResponse(f"Plan Reader - Job detail {job_id}")
