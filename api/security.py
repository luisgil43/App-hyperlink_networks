# api/security.py

import os

from django.core.cache import cache

from .models import ApiFeature

API_FEATURE_CACHE_SECONDS = 30


DEFAULT_API_FEATURES = [
    {
        "code": "mobile_auth",
        "name": "Mobile Auth API",
        "description": "Allows JWT login, token refresh, and authenticated user lookup from the mobile app.",
        "is_enabled": True,
        "only_superusers": False,
    },
    {
        "code": "mobile_billing",
        "name": "Mobile Billing API",
        "description": "Allows the mobile app to list and view billing records.",
        "is_enabled": True,
        "only_superusers": False,
    },
    {
        "code": "mobile_fleet",
        "name": "Mobile Fleet API",
        "description": "Allows the mobile app to view vehicles, odometer logs, and services.",
        "is_enabled": False,
        "only_superusers": False,
    },
    {
        "code": "mobile_evidence",
        "name": "Mobile Evidence API",
        "description": "Allows the mobile app to upload evidence, photos, and files.",
        "is_enabled": False,
        "only_superusers": False,
    },
    {
        "code": "mobile_expenses",
        "name": "Mobile Expenses API",
        "description": "Allows the mobile app to create expense reports and submit costs.",
        "is_enabled": False,
        "only_superusers": False,
    },
]


def api_global_enabled():
    """
    Global API kill switch.

    If API_GLOBAL_ENABLED=0, all API features are blocked even when
    individual features are enabled in the database.

    In development, it defaults to enabled.
    In production, it can be controlled from Render environment variables.
    """
    raw = os.environ.get("API_GLOBAL_ENABLED", "1")
    return str(raw).strip().lower() in ["1", "true", "yes", "on"]


def ensure_default_api_features():
    """
    Creates the default API features if they do not exist.

    It also keeps name and description synchronized in English,
    without changing the current enabled/restriction state selected
    from the API Management screen.
    """
    for item in DEFAULT_API_FEATURES:
        feature, created = ApiFeature.objects.get_or_create(
            code=item["code"],
            defaults={
                "name": item["name"],
                "description": item["description"],
                "is_enabled": item["is_enabled"],
                "only_superusers": item["only_superusers"],
            },
        )

        if not created:
            changed = []

            if feature.name != item["name"]:
                feature.name = item["name"]
                changed.append("name")

            if feature.description != item["description"]:
                feature.description = item["description"]
                changed.append("description")

            if changed:
                feature.save(update_fields=changed + ["updated_at"])
                clear_api_feature_cache(feature.code)


def get_api_feature(code):
    """
    Returns an ApiFeature using a short cache.
    """
    if not code:
        return None

    cache_key = f"api_feature:{code}"
    cached_feature = cache.get(cache_key)

    if cached_feature is not None:
        return cached_feature

    try:
        feature = ApiFeature.objects.get(code=code)
    except ApiFeature.DoesNotExist:
        feature = None

    cache.set(cache_key, feature, API_FEATURE_CACHE_SECONDS)
    return feature


def is_api_feature_enabled(code, user=None):
    """
    Checks whether an API feature can be used.

    Rules:
    1. API_GLOBAL_ENABLED must be active.
    2. The feature must exist.
    3. The feature must be enabled.
    4. If only_superusers=True, the user must be a superuser.
    """
    if not api_global_enabled():
        return False

    feature = get_api_feature(code)

    if not feature:
        return False

    if not feature.is_enabled:
        return False

    if feature.only_superusers:
        if not user or not getattr(user, "is_superuser", False):
            return False

    return True


def clear_api_feature_cache(code=None):
    """
    Clears the cache for one feature or all known default features.
    """
    if code:
        cache.delete(f"api_feature:{code}")
        return

    for item in DEFAULT_API_FEATURES:
        cache.delete(f"api_feature:{item['code']}")
