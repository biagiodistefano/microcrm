"""Personal API token authentication (``Authorization: Bearer <token>``)."""

import typing as t

from django.contrib.auth.models import User
from django.http import HttpRequest
from django.utils import timezone
from ninja.errors import HttpError
from ninja.security import HttpBearer

from leads.models import UserApiToken


class BearerTokenAuth(HttpBearer):
    """Authenticate as the owner of a non-revoked personal API token.

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
