"""Tests for Gmail OAuth2 integration."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User

from leads.gmail import send_email_via_gmail
from leads.models import City, EmailDraft, EmailSent, EmailSignature, GmailConnection, Lead, LeadType
from leads.service import send_email_draft, send_email_to_lead

pytestmark = pytest.mark.django_db


@pytest.fixture
def gmail_user() -> User:
    return User.objects.create_user(
        username="salesperson",
        email="sales@letsrevel.io",
        password="testpass",
    )


@pytest.fixture
def gmail_connection(gmail_user: User) -> GmailConnection:
    return GmailConnection.objects.create(
        user=gmail_user,
        email="sales@letsrevel.io",
        refresh_token="fake-refresh-token",
        is_active=True,
    )


@pytest.fixture
def gmail_lead() -> Lead:
    city, _ = City.objects.get_or_create(name="Vienna", defaults={"country": "Austria", "iso2": "AT"})
    lead_type, _ = LeadType.objects.get_or_create(name="Venue")
    return Lead.objects.create(
        name="Test Venue",
        email="venue@example.com",
        city=city,
        lead_type=lead_type,
    )


# ---------------------------------------------------------------------------
# Gmail API send tests
# ---------------------------------------------------------------------------


class TestSendEmailViaGmail:
    @patch("googleapiclient.discovery.build")
    def test_sends_via_gmail_api(self, mock_build: MagicMock, settings: MagicMock) -> None:
        settings.EMAIL_DRY_RUN = False
        credentials = MagicMock()

        send_email_via_gmail(
            credentials=credentials,
            from_email="sales@letsrevel.io",
            to=["venue@example.com"],
            subject="Hello",
            body="Test body",
        )

        mock_build.assert_called_once_with("gmail", "v1", credentials=credentials, cache_discovery=False)
        mock_service = mock_build.return_value
        mock_service.users().messages().send.assert_called_once()

    def test_dry_run_does_not_send(self, settings: MagicMock) -> None:
        settings.EMAIL_DRY_RUN = True
        credentials = MagicMock()

        send_email_via_gmail(
            credentials=credentials,
            from_email="sales@letsrevel.io",
            to=["venue@example.com"],
            subject="Hello",
            body="Test body",
        )
        # No exception = success, gmail API was never called

    @patch("googleapiclient.discovery.build")
    def test_sends_with_bcc(self, mock_build: MagicMock, settings: MagicMock) -> None:
        settings.EMAIL_DRY_RUN = False
        credentials = MagicMock()

        send_email_via_gmail(
            credentials=credentials,
            from_email="sales@letsrevel.io",
            to=["venue@example.com"],
            subject="Hello",
            body="Test",
            bcc=["bcc@example.com"],
        )

        mock_service = mock_build.return_value
        mock_service.users().messages().send.assert_called_once()

    @patch("googleapiclient.discovery.build")
    def test_sends_html_mime_type(self, mock_build: MagicMock, settings: MagicMock) -> None:
        settings.EMAIL_DRY_RUN = False
        credentials = MagicMock()

        send_email_via_gmail(
            credentials=credentials,
            from_email="sales@letsrevel.io",
            to=["venue@example.com"],
            subject="Hello",
            body="<p>Test</p>",
        )

        # Verify the raw message sent to Gmail contains HTML content type
        mock_service = mock_build.return_value
        call_args = mock_service.users().messages().send.call_args
        raw_msg = call_args.kwargs.get("body", call_args[1].get("body", {})) if call_args else {}
        import base64

        decoded = base64.urlsafe_b64decode(raw_msg["raw"]).decode()
        assert "Content-Type: text/html" in decoded
        assert "<p>Test</p>" in decoded


# ---------------------------------------------------------------------------
# EmailSignature model tests
# ---------------------------------------------------------------------------


class TestEmailSignature:
    def test_create_signature(self, gmail_user: User) -> None:
        sig = EmailSignature.objects.create(
            user=gmail_user,
            body="<p>Best regards,</p><p>Sales Team</p>",
        )
        assert sig.body == "<p>Best regards,</p><p>Sales Team</p>"
        assert sig.user == gmail_user

    def test_str_with_full_name(self, gmail_user: User) -> None:
        gmail_user.first_name = "John"
        gmail_user.last_name = "Doe"
        gmail_user.save()
        sig = EmailSignature.objects.create(user=gmail_user, body="<p>sig</p>")
        assert str(sig) == "Signature for John Doe"

    def test_str_without_full_name(self, gmail_user: User) -> None:
        sig = EmailSignature.objects.create(user=gmail_user, body="<p>sig</p>")
        assert str(sig) == "Signature for salesperson"

    def test_one_to_one_constraint(self, gmail_user: User) -> None:
        EmailSignature.objects.create(user=gmail_user, body="<p>first</p>")
        from django.db import IntegrityError

        with pytest.raises(IntegrityError):
            EmailSignature.objects.create(user=gmail_user, body="<p>second</p>")

    def test_blank_body_allowed(self, gmail_user: User) -> None:
        sig = EmailSignature.objects.create(user=gmail_user, body="")
        assert sig.body == ""


# ---------------------------------------------------------------------------
# Credential management tests
# ---------------------------------------------------------------------------


class TestGetGmailCredentials:
    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials")
    def test_builds_and_refreshes_credentials(
        self,
        mock_credentials_class: MagicMock,
        mock_request: MagicMock,
        gmail_connection: GmailConnection,
    ) -> None:
        from leads.gmail import get_gmail_credentials

        mock_creds = mock_credentials_class.return_value
        mock_creds.refresh_token = "fake-refresh-token"  # same as original

        result = get_gmail_credentials(gmail_connection)

        mock_credentials_class.assert_called_once()
        mock_creds.refresh.assert_called_once()
        assert result == mock_creds

    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials")
    def test_deactivates_on_refresh_error(
        self,
        mock_credentials_class: MagicMock,
        mock_request: MagicMock,
        gmail_connection: GmailConnection,
    ) -> None:
        from google.auth.exceptions import RefreshError

        from leads.gmail import GmailAuthError, get_gmail_credentials

        mock_creds = mock_credentials_class.return_value
        mock_creds.refresh.side_effect = RefreshError("Token revoked")  # type: ignore[no-untyped-call]

        with pytest.raises(GmailAuthError):
            get_gmail_credentials(gmail_connection)

        gmail_connection.refresh_from_db()
        assert not gmail_connection.is_active

    @patch("google.auth.transport.requests.Request")
    @patch("google.oauth2.credentials.Credentials")
    def test_updates_rotated_refresh_token(
        self,
        mock_credentials_class: MagicMock,
        mock_request: MagicMock,
        gmail_connection: GmailConnection,
    ) -> None:
        from leads.gmail import get_gmail_credentials

        mock_creds = mock_credentials_class.return_value
        mock_creds.refresh_token = "new-rotated-token"

        get_gmail_credentials(gmail_connection)

        gmail_connection.refresh_from_db()
        assert gmail_connection.refresh_token == "new-rotated-token"


# ---------------------------------------------------------------------------
# send_email_to_lead with Gmail integration
# ---------------------------------------------------------------------------


class TestSendEmailToLeadWithGmail:
    @patch("leads.gmail.send_email_via_gmail")
    @patch("leads.gmail.get_gmail_credentials")
    def test_sends_via_gmail_when_user_has_connection(
        self,
        mock_get_creds: MagicMock,
        mock_gmail_send: MagicMock,
        gmail_lead: Lead,
        gmail_user: User,
        gmail_connection: GmailConnection,
    ) -> None:
        email_sent = send_email_to_lead(
            lead=gmail_lead,
            subject="Hello",
            body="Test",
            to=["venue@example.com"],
            user=gmail_user,
        )

        assert email_sent.from_email == "sales@letsrevel.io"
        assert email_sent.sent_by == gmail_user
        assert email_sent.status == EmailSent.Status.SENT
        mock_get_creds.assert_called_once_with(gmail_connection)
        mock_gmail_send.assert_called_once()

    def test_raises_without_gmail_connection(
        self,
        gmail_lead: Lead,
        gmail_user: User,
    ) -> None:
        with pytest.raises(ValueError, match="Gmail account not connected"):
            send_email_to_lead(
                lead=gmail_lead,
                subject="Hello",
                body="Test",
                to=["venue@example.com"],
                user=gmail_user,
            )

    @patch("leads.service._send_via_smtp")
    def test_sends_via_smtp_without_user(
        self,
        mock_smtp: MagicMock,
        gmail_lead: Lead,
        settings: MagicMock,
    ) -> None:
        settings.DEFAULT_FROM_EMAIL = "crm@example.com"

        email_sent = send_email_to_lead(
            lead=gmail_lead,
            subject="Hello",
            body="Test",
            to=["venue@example.com"],
        )

        assert email_sent.from_email == "crm@example.com"
        assert email_sent.sent_by is None
        mock_smtp.assert_called_once()


# ---------------------------------------------------------------------------
# send_email_draft with Gmail integration
# ---------------------------------------------------------------------------


class TestSendEmailDraftWithGmail:
    @patch("leads.gmail.send_email_via_gmail")
    @patch("leads.gmail.get_gmail_credentials")
    def test_sends_draft_via_gmail(
        self,
        mock_get_creds: MagicMock,
        mock_gmail_send: MagicMock,
        gmail_lead: Lead,
        gmail_user: User,
        gmail_connection: GmailConnection,
    ) -> None:
        draft = EmailDraft.objects.create(
            lead=gmail_lead,
            from_email="sales@letsrevel.io",
            to=["venue@example.com"],
            bcc=[],
            subject="Draft Subject",
            body="Draft Body",
        )
        draft_id = draft.id

        email_sent = send_email_draft(draft, user=gmail_user)

        assert email_sent.from_email == "sales@letsrevel.io"
        assert email_sent.status == EmailSent.Status.SENT
        assert not EmailDraft.objects.filter(id=draft_id).exists()
        mock_gmail_send.assert_called_once()


# ---------------------------------------------------------------------------
# Celery task with user_id
# ---------------------------------------------------------------------------


class TestSendEmailTaskWithUser:
    @patch("leads.gmail.send_email_via_gmail")
    @patch("leads.gmail.get_gmail_credentials")
    def test_task_resolves_user_and_sends_via_gmail(
        self,
        mock_get_creds: MagicMock,
        mock_gmail_send: MagicMock,
        gmail_lead: Lead,
        gmail_user: User,
        gmail_connection: GmailConnection,
    ) -> None:
        from leads.tasks import send_email_task

        result = send_email_task(
            lead_id=gmail_lead.id,
            subject="Hello",
            body="Test",
            to=["venue@example.com"],
            user_id=gmail_user.id,
        )

        assert result["status"] == "sent"
        mock_gmail_send.assert_called_once()

    @patch("leads.service._send_via_smtp")
    def test_task_falls_back_without_user_id(
        self,
        mock_smtp: MagicMock,
        gmail_lead: Lead,
    ) -> None:
        from leads.tasks import send_email_task

        result = send_email_task(
            lead_id=gmail_lead.id,
            subject="Hello",
            body="Test",
            to=["venue@example.com"],
        )

        assert result["status"] == "sent"
        mock_smtp.assert_called_once()


# ---------------------------------------------------------------------------
# OAuth views tests
# ---------------------------------------------------------------------------


class TestGmailOAuthViews:
    @pytest.fixture
    def staff_user(self) -> User:
        return User.objects.create_user(
            username="staff",
            email="staff@letsrevel.io",
            password="testpass",
            is_staff=True,
        )

    def test_connect_requires_login(self) -> None:
        from django.test import Client

        client = Client()
        response = client.get("/gmail/oauth/connect/")
        assert response.status_code == 302
        assert "/login/" in response.url  # type: ignore[attr-defined]

    def test_disconnect_requires_post(self, staff_user: User) -> None:
        from django.test import Client

        client = Client()
        client.force_login(staff_user)
        response = client.get("/gmail/oauth/disconnect/")
        assert response.status_code == 302  # redirect, doesn't process GET

    def test_disconnect_deletes_connection(self, staff_user: User) -> None:
        from django.test import Client

        GmailConnection.objects.create(
            user=staff_user,
            email="staff@letsrevel.io",
            refresh_token="fake-token",
            is_active=True,
        )

        client = Client()
        client.force_login(staff_user)

        with patch("requests.post"):
            response = client.post("/gmail/oauth/disconnect/")

        assert response.status_code == 302
        assert not GmailConnection.objects.filter(user=staff_user).exists()

    def test_callback_rejects_invalid_state(self, staff_user: User) -> None:
        from django.test import Client

        client = Client()
        client.force_login(staff_user)

        response = client.get("/gmail/oauth/callback/", {"state": "bad-state", "code": "test"})
        assert response.status_code == 302  # redirect to admin
