from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .forms import RouteForm
from .models import (Route, RouteSegment, SegmentStageProgress, Stage,
                     seed_default_stages)


@login_required
def route_list(request):
    seed_default_stages()
    routes = Route.objects.all().order_by("-id")
    return render(request, "underground/route_list.html", {"routes": routes})


@login_required
def route_create(request):
    seed_default_stages()

    if request.method == "POST":
        form = RouteForm(request.POST)
        if form.is_valid():
            route = form.save()
            messages.success(request, "Ruta creada. Segmentos generados automáticamente.")
            return redirect("underground:route_detail", route_id=route.id)
    else:
        form = RouteForm()

    return render(request, "underground/route_form.html", {"form": form})


@login_required
def route_detail(request, route_id: int):
    seed_default_stages()

    route = get_object_or_404(Route, id=route_id)
    stages = Stage.objects.filter(is_active=True).order_by("order", "id")

    stage_code = (request.GET.get("stage") or "marking").strip().lower()
    stage = stages.filter(code=stage_code).first() or stages.first()

    # segmentos + progreso para la etapa seleccionada
    segments = list(route.segments.all().order_by("index"))

    progress_map = {}
    prog_qs = SegmentStageProgress.objects.filter(
        segment__route=route,
        stage=stage,
    ).select_related("segment")

    for p in prog_qs:
        progress_map[p.segment_id] = p

    # resumen ft done / total para cada etapa (para mostrar arriba)
    stage_summaries = []
    for st in stages:
        done_ids = SegmentStageProgress.objects.filter(
            segment__route=route,
            stage=st,
            status=SegmentStageProgress.STATUS_DONE,
        ).values_list("segment_id", flat=True)

        done_len = sum([s.length_ft for s in segments if s.id in set(done_ids)])
        stage_summaries.append({
            "stage": st,
            "done_ft": done_len,
            "total_ft": route.total_length_ft,
        })

    ctx = {
        "route": route,
        "stages": stages,
        "stage": stage,
        "segments": segments,
        "progress_map": progress_map,
        "stage_summaries": stage_summaries,
    }
    return render(request, "underground/route_detail.html", ctx)


@login_required
@require_POST
def route_regenerate_segments(request, route_id: int):
    route = get_object_or_404(Route, id=route_id)
    route.regenerate_segments()
    messages.success(request, "Segmentos regenerados según start/end/segment_length.")
    return redirect("underground:route_detail", route_id=route.id)


@login_required
@require_POST
def update_segment_progress(request, route_id: int):
    """
    AJAX/POST: actualiza el status de un segmento en una etapa.
    Requiere:
      - segment_id
      - stage_code
      - status
      - notes (opcional)
    Aplica gating simple: si stage.requires_prev_stage, esa etapa previa debe estar DONE.
    """
    route = get_object_or_404(Route, id=route_id)

    segment_id = request.POST.get("segment_id")
    stage_code = (request.POST.get("stage_code") or "").strip().lower()
    status = (request.POST.get("status") or "").strip()

    if not segment_id or not stage_code or not status:
        return HttpResponseBadRequest("Faltan parámetros.")

    segment = get_object_or_404(RouteSegment, id=segment_id, route=route)
    stage = get_object_or_404(Stage, code=stage_code, is_active=True)

    notes = (request.POST.get("notes") or "").strip()

    # gating
    if stage.requires_prev_stage_id:
        prev = stage.requires_prev_stage
        prev_prog = SegmentStageProgress.objects.filter(segment=segment, stage=prev).first()
        if not prev_prog or prev_prog.status != SegmentStageProgress.STATUS_DONE:
            return JsonResponse({
                "ok": False,
                "error": f"No puedes avanzar {stage.name} si {prev.name} no está DONE en este segmento."
            }, status=400)

    if status not in dict(SegmentStageProgress.STATUS_CHOICES):
        return JsonResponse({"ok": False, "error": "Status inválido."}, status=400)

    with transaction.atomic():
        obj, _ = SegmentStageProgress.objects.get_or_create(segment=segment, stage=stage)
        obj.status = status
        obj.notes = notes
        obj.updated_by = request.user
        obj.updated_at = timezone.now()
        obj.save()

    return JsonResponse({
        "ok": True,
        "segment_id": segment.id,
        "stage_code": stage.code,
        "status": obj.status,
    })