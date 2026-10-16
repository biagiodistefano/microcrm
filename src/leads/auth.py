"""Personal API token authentication (``Authorization: Bearer <token>``)."""

import re
import typing as t

from django.contrib.auth.models import User
from django.http import HttpRequest
from django.utils import timezone
from ninja.errors import HttpError
from ninja.security import HttpBearer

from leads.models import UserApiToken

# What a personal token may call (method, path regex). Everything else needs the shared X-API-Key.
TOKEN_ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/email-drafts/(send|\d+/send)/?$")),
    ("GET", re.compile(r"^/api/email-drafts(/\d+)?/?$")),
    ("GET", re.compile(r"^/api/messages(/\d+(/contact-check)?)?/?$")),
    ("POST", re.compile(r"^/api/messages/\d+/mark-sent/?$")),
    ("GET", re.compile(r"^/api/leads(/.*)?$")),
)


def token_may_call(method: str, path: str) -> bool:
    """Whether a personal token is allowed to call this endpoint."""
    return any(method == m and pattern.match(path) for m, pattern in TOKEN_ALLOWED)


class BearerTokenAuth(HttpBearer):
    """Authenticate as the (active, staff) owner of a non-revoked personal API token, for allow-listed endpoints only.

    ``request.auth`` becomes the ``User``. Requests without a valid bearer token fall through to the next
    authenticator (the shared ``X-API-Key``).
    """

    def authenticate(self, request: HttpRequest, token: str) -> User | None:
        """Resolve a bearer token to its (active) user and stamp ``last_used_at``."""
        row = (
            UserApiToken.objects.select_related("user")
            .filter(token_hash=UserApiToken.hash_token(token), revoked_at__isnull=True, user__is_active=True)
            .first()
        )
        if row is None:
            return None
        if not row.user.is_staff:
            return None
        if not token_may_call(request.method or "", request.path):
            raise HttpError(403, "This endpoint is not available to personal tokens; use the API key for this endpoint")
        UserApiToken.objects.filter(pk=row.pk).update(last_used_at=timezone.now())
        return row.user


def get_request_user(request: HttpRequest | None) -> User | None:
    """The user a request authenticated as via a personal token, or None (shared API key / no auth)."""
    auth: t.Any = getattr(request, "auth", None)
    return auth if isinstance(auth, User) else None


def require_request_user(request: HttpRequest | None) -> User:
    """Like :func:`get_request_user` but raise 403 when the request is not authenticated as a user.

    Raises:
        HttpError: 403 when only the shared X-API-Key (or nothing) was used.
    """
    user = get_request_user(request)
    if user is None:
        raise HttpError(403, "This endpoint requires a personal API token (Authorization: Bearer <token>)")
    return user
