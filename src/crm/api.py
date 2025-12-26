"""API configuration."""

from django.conf import settings
from django.http import HttpRequest
from ninja import Schema
from ninja.security import APIKeyHeader
from ninja_extra import NinjaExtraAPI

from leads.controllers import (
    ActionController,
    CityController,
    EmailSentController,
    EmailTemplateController,
    LeadController,
    LeadTypeController,
    ResearchJobController,
    TagController,
)


class ApiKeyAuth(APIKeyHeader):
    """API Key authentication."""

    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str | None) -> bool | None:
        """Authenticate request with API key.

        Auth is disabled when DEBUG=True.
        """
        if settings.DEBUG:
            return True
        if key == settings.API_KEY:
            return True
        return None


# Build servers list for OpenAPI from CSRF_TRUSTED_ORIGINS
_servers = []
if settings.CSRF_TRUSTED_ORIGINS:
    _servers.append({"url": settings.CSRF_TRUSTED_ORIGINS[0], "description": "Production"})
if settings.DEBUG:
    _servers.append({"url": "http://localhost:8000", "description": "Local development"})

api = NinjaExtraAPI(
    title="MicroCRM API",
    version=settings.VERSION,
    docs_url="/docs",
    auth=ApiKeyAuth(),
    servers=_servers or None,
)


class HealthResponse(Schema):
    """Health check response."""

    status: str = "ok"


@api.get("/health", response=HealthResponse, tags=["Health"], auth=None)
def health(request: HttpRequest) -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse()


api.register_controllers(
    LeadController,
    ActionController,
    CityController,
    LeadTypeController,
    TagController,
    ResearchJobController,
    EmailTemplateController,
    EmailSentController,
)
