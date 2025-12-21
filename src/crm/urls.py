"""URL configuration."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from crm.api import api

admin.site.site_header = f"{settings.SITE_NAME} v{settings.VERSION}"
admin.site.index_title = f"Welcome to {settings.SITE_NAME} v{settings.VERSION} Admin"
admin.site.site_title = f"{settings.SITE_NAME} v{settings.VERSION} Admin"

urlpatterns = [
    path("", RedirectView.as_view(url=f"/{settings.ADMIN_URL}", permanent=False)),
    path(settings.ADMIN_URL, admin.site.urls),
    path("api/", api.urls),
    path("google_sso/", include("django_google_sso.urls", namespace="django_google_sso")),
]
