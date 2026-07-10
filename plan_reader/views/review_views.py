from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from plan_reader.forms import PlanReaderItemReviewForm
from plan_reader.models import PlanReaderItem
from plan_reader.views.job_views import (can_access_plan_reader,
                                         deny_plan_reader_access)


@login_required
def item_review(request, item_id):
    if not can_access_plan_reader(request.user):
        return deny_plan_reader_access(request)

    item = get_object_or_404(
        PlanReaderItem.objects.select_related("job", "page"),
        id=item_id,
    )

    if request.method == "POST":
        form = PlanReaderItemReviewForm(request.POST, instance=item)

        if form.is_valid():
            form.save()
            messages.success(request, "Plan Reader item updated.")
            return redirect("plan_reader:job_detail", job_id=item.job_id)
    else:
        form = PlanReaderItemReviewForm(instance=item)

    return render(
        request,
        "plan_reader/item_review.html",
        {
            "item": item,
            "job": item.job,
            "form": form,
        },
    )


@login_required
def toggle_item_duplicate(request, item_id):
    """
    Marca/desmarca un item como duplicado.

    Si is_duplicate=True:
    - Se excluye del Excel final.
    - Sale de la tabla principal.
    - Queda visible en Duplicate detail.

    Si is_duplicate=False:
    - Vuelve a la tabla principal.
    - Vuelve al Excel final.
    """

    if not can_access_plan_reader(request.user):
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Access denied.",
                },
                status=403,
            )

        return deny_plan_reader_access(request)

    if request.method != "POST":
        return JsonResponse(
            {
                "ok": False,
                "error": "Invalid method.",
            },
            status=405,
        )

    item = get_object_or_404(
        PlanReaderItem.objects.select_related("job"),
        id=item_id,
    )

    new_value = not bool(item.is_duplicate)
    item.is_duplicate = new_value

    current = (item.observation or "").strip()

    if new_value:
        item.needs_review = True

        note = "Manually marked as duplicate. Excluded from billing draft."

        if note not in current:
            item.observation = f"{current} {note}".strip()

    else:
        note = "Manually restored as included in billing draft."

        if note not in current:
            item.observation = f"{current} {note}".strip()

    item.save(
        update_fields=[
            "is_duplicate",
            "needs_review",
            "observation",
        ]
    )

    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return JsonResponse(
            {
                "ok": True,
                "item_id": item.id,
                "is_duplicate": item.is_duplicate,
                "needs_review": item.needs_review,
                "billing_action": "Duplicate" if item.is_duplicate else "Included",
            }
        )

    if new_value:
        messages.warning(
            request,
            "Item marked as duplicate and excluded from billing draft.",
        )
    else:
        messages.success(
            request,
            "Item restored and included in billing draft.",
        )

    return redirect("plan_reader:job_detail", job_id=item.job_id)
