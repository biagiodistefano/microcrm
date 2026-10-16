"""Project middleware."""

import re
import typing as t

from django.http import HttpRequest, HttpResponse

# Token-authenticated draft sends talk to Gmail, so they must not run inside ATOMIC_REQUESTS: the service commits
# in short transactions around the Gmail call (see leads.service.send_draft_as_user).
_NON_ATOMIC = re.compile(r"^/api/email-drafts/(send|\d+/send)/?$")


class NonAtomicSendMiddleware:
    """Mark the draft-send views ``non_atomic_requests`` (runs before Django wraps views in a transaction)."""

    def __init__(self, get_response: t.Callable[[HttpRequest], HttpResponse]) -> None:
        """Store the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Pass the request through."""
        return self.get_response(request)

    def process_view(
        self, request: HttpRequest, view_func: t.Callable[..., HttpResponse], view_args: t.Any, view_kwargs: t.Any
    ) -> None:
        """Opt matching views out of the per-request transaction."""
        if request.method == "POST" and _NON_ATOMIC.match(request.path):
            view_func._non_atomic_requests = frozenset({"default"})  # type: ignore[attr-defined]
