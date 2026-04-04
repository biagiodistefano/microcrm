"""Gmail OAuth2 integration for per-user email sending via Gmail API."""

import base64
import logging
import typing as t
from email.mime.text import MIMEText

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.urls import reverse

from leads.models import GmailConnection

logger = logging.getLogger(__name__)


class GmailAuthError(Exception):
    """Raised when Gmail OAuth credentials are invalid or expired beyond recovery."""


# ---------------------------------------------------------------------------
# Credential management
# ---------------------------------------------------------------------------


def get_gmail_credentials(connection: GmailConnection) -> t.Any:
    """Build and refresh Google OAuth2 credentials from a GmailConnection.

    Returns a google.oauth2.credentials.Credentials object ready to use.
    Raises GmailAuthError if the token cannot be refreshed.
    """
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    credentials = Credentials(  # type: ignore[no-untyped-call]
        token=None,
        refresh_token=connection.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_SSO_CLIENT_ID,
        client_secret=settings.GOOGLE_SSO_CLIENT_SECRET,
        scopes=settings.GMAIL_SCOPES,
    )

    try:
        credentials.refresh(Request())  # type: ignore[no-untyped-call]
    except RefreshError as e:
        logger.error("Gmail token refresh failed for %s: %s", connection.email, e)
        connection.is_active = False
        connection.save(update_fields=["is_active", "updated_at"])
        raise GmailAuthError(f"Gmail authentication failed for {connection.email}. Please reconnect.") from e

    # If Google rotated the refresh token, update the stored one
    if credentials.refresh_token and credentials.refresh_token != connection.refresh_token:
        connection.refresh_token = credentials.refresh_token
        connection.save(update_fields=["refresh_token", "updated_at"])

    return credentials


# ---------------------------------------------------------------------------
# Gmail API send
# ---------------------------------------------------------------------------


def send_email_via_gmail(
    credentials: t.Any,
    from_email: str,
    to: list[str],
    subject: str,
    body: str,
    bcc: list[str] | None = None,
) -> None:
    """Send an email using the Gmail API.

    Respects the EMAIL_DRY_RUN setting — logs instead of sending when active.
    """
    message = MIMEText(body, "html")
    message["to"] = ", ".join(to)
    message["from"] = from_email
    message["subject"] = subject
    if bcc:
        message["bcc"] = ", ".join(bcc)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    if settings.EMAIL_DRY_RUN:
        logger.info("[DRY RUN] Gmail API send from %s to %s: %s", from_email, to, subject)
        return

    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


# ---------------------------------------------------------------------------
# OAuth views
# ---------------------------------------------------------------------------


def _build_flow(request: HttpRequest) -> t.Any:
    """Build a google_auth_oauthlib Flow for the Gmail OAuth2 flow."""
    from google_auth_oauthlib.flow import Flow

    redirect_uri = request.build_absolute_uri(reverse("gmail_callback"))

    return Flow.from_client_config(
        client_config={
            "web": {
                "client_id": settings.GOOGLE_SSO_CLIENT_ID,
                "client_secret": settings.GOOGLE_SSO_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=settings.GMAIL_SCOPES,
        redirect_uri=redirect_uri,
    )


@login_required
@staff_member_required
def gmail_connect_view(request: HttpRequest) -> HttpResponse:
    """Initiate the Gmail OAuth2 connect flow."""
    import os
    import secrets

    # Allow HTTP redirect URIs in local development
    if settings.DEBUG:
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

    flow = _build_flow(request)

    state = secrets.token_urlsafe(32)
    request.session["gmail_oauth_state"] = state

    authorization_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        login_hint=request.user.email,  # type: ignore[union-attr]
    )

    return HttpResponseRedirect(authorization_url)


@login_required
@staff_member_required
def gmail_callback_view(request: HttpRequest) -> HttpResponse:
    """Handle the OAuth2 callback from Google."""
    logger.info("Gmail OAuth callback received: %s", request.GET.dict())

    stored_state = request.session.pop("gmail_oauth_state", None)
    received_state = request.GET.get("state")

    if not stored_state or stored_state != received_state:
        logger.warning("OAuth state mismatch: stored=%s received=%s", stored_state, received_state)
        messages.error(request, "Invalid OAuth state. Please try again.")
        return HttpResponseRedirect(reverse("admin:index"))

    error = request.GET.get("error")
    if error:
        logger.warning("Google OAuth error: %s", error)
        messages.error(request, f"Google OAuth error: {error}")
        return HttpResponseRedirect(reverse("admin:index"))

    try:
        flow = _build_flow(request)
        logger.info("Fetching token with redirect_uri=%s", flow.redirect_uri)

        flow.fetch_token(authorization_response=request.build_absolute_uri())

        credentials = flow.credentials
        if not credentials or not credentials.refresh_token:
            logger.error("No refresh token in credentials. Scopes granted: %s", getattr(credentials, "scopes", None))
            messages.error(request, "Failed to obtain refresh token. Please try again.")
            return HttpResponseRedirect(reverse("admin:index"))

        # Get the authenticated user's email from Google
        from googleapiclient.discovery import build

        oauth2_service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
        user_info = oauth2_service.userinfo().get().execute()
        gmail_email: str = user_info["email"]
        logger.info("Gmail OAuth successful for %s", gmail_email)

        # Validate domain if restricted
        allowed_domain = settings.GMAIL_ALLOWED_DOMAIN
        if allowed_domain and not gmail_email.endswith(f"@{allowed_domain}"):
            logger.warning("Domain rejected: %s (allowed: %s)", gmail_email, allowed_domain)
            messages.error(request, f"Only @{allowed_domain} accounts are allowed.")
            return HttpResponseRedirect(reverse("admin:index"))

        # Store the connection — refresh_token is encrypted transparently by EncryptedTextField
        user = t.cast(User, request.user)
        GmailConnection.objects.update_or_create(
            user=user,
            defaults={
                "email": gmail_email,
                "refresh_token": credentials.refresh_token,
                "is_active": True,
            },
        )

        messages.success(request, f"Gmail connected: {gmail_email}")
        return HttpResponseRedirect(reverse("admin:index"))

    except Exception:
        logger.exception("Gmail OAuth callback failed")
        messages.error(request, "Gmail connection failed. Check server logs for details.")
        return HttpResponseRedirect(reverse("admin:index"))


@login_required
@staff_member_required
def gmail_disconnect_view(request: HttpRequest) -> HttpResponse:
    """Disconnect the user's Gmail account."""
    if request.method != "POST":
        return HttpResponseRedirect(reverse("admin:index"))

    try:
        connection = GmailConnection.objects.get(user=t.cast(User, request.user))
    except GmailConnection.DoesNotExist:
        messages.warning(request, "No Gmail account connected.")
        return HttpResponseRedirect(reverse("admin:index"))

    # Revoke the token at Google
    try:
        import requests as http_requests  # type: ignore[import-untyped]

        http_requests.post(
            "https://oauth2.googleapis.com/revoke",
            params={"token": connection.refresh_token},
            timeout=10,
        )
    except Exception:
        logger.warning("Failed to revoke Gmail token for %s", connection.email, exc_info=True)

    email = connection.email
    connection.delete()

    messages.success(request, f"Gmail disconnected: {email}")
    return HttpResponseRedirect(reverse("admin:index"))
