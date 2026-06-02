# api/urls.py

from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views, views_admin

app_name = "api"

urlpatterns = [
    # API Management web/admin
    path("management/", views_admin.api_feature_list, name="api_feature_list"),
    path(
        "management/<int:pk>/toggle/",
        views_admin.api_feature_toggle,
        name="api_feature_toggle",
    ),
    path(
        "management/<int:pk>/toggle-superuser/",
        views_admin.api_feature_superuser_toggle,
        name="api_feature_superuser_toggle",
    ),
    # API móvil
    path(
        "auth/login/",
        views.MobileTokenObtainPairView.as_view(),
        name="token_obtain_pair",
    ),
    path("auth/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/me/", views.api_me, name="api_me"),
    path("billing/my/", views.api_my_billing_list, name="api_my_billing_list"),
    path(
        "billing/my/<int:pk>/",
        views.api_my_billing_detail,
        name="api_my_billing_detail",
    ),
]
