from django.http import Http404
from django.utils import timezone

from .models import DeliveryAccessLog


def get_client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()

    return request.META.get("REMOTE_ADDR")


def log_delivery_access(request, package, action, file=None, extra=None):
    try:
        DeliveryAccessLog.objects.create(
            package=package,
            file=file,
            user=request.user if request.user.is_authenticated else None,
            action=action,
            ip_address=get_client_ip(request),
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
            extra=extra or {},
        )
    except Exception:
        pass


def package_or_404_if_unavailable(request, package):
    if package.status == package.STATUS_REVOKED:
        log_delivery_access(
            request,
            package,
            DeliveryAccessLog.ACTION_REVOKED_ACCESS,
        )
        raise Http404("Package not available.")

    if package.is_expired():
        log_delivery_access(
            request,
            package,
            DeliveryAccessLog.ACTION_EXPIRED_ACCESS,
        )
        raise Http404("Package not available.")

    if package.status != package.STATUS_PUBLISHED:
        raise Http404("Package not available.")

    if package.is_locked():
        raise Http404("Package temporarily locked.")

    return package


def now():
    return timezone.now()
