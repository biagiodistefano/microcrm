"""Tests for personal API tokens, token-authenticated draft sending, signature and contact-check."""

import hashlib
import typing as t
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.test import Client, RequestFactory
from django.utils import timezone

from leads import service
from leads.admin import UserApiTokenAdmin
from leads.models import (
    Action,
    EmailDraft,
    EmailSent,
    EmailSignature,
    GmailConnection,
    Lead,
    OutreachMessage,
    UserApiToken,
)

pytestmark = pytest.mark.django_db

JSON = "application/json"


def humanize(subject: str, body: str) -> str:
    return hashlib.sha256(f"{subject}\x1f{body}".encode()).hexdigest()


@pytest.fixture
def prod_auth(settings: t.Any) -> None:
    """Turn real API auth on (tests normally run with DEBUG=True, which disables it)."""
    settings.DEBUG = False
    settings.API_KEY = "shared-key"


@pytest.fixture
def me() -> User:
    user = User.objects.create_user(
        "biagio", email="biagio@letsrevel.io", password="x", first_name="Biagio", is_staff=True
    )
    GmailConnection.objects.create(user=user, email="biagio@letsrevel.io", refresh_token="r")
    return user


@pytest.fixture
def other() -> User:
    return User.objects.create_user("weronika", email="weronika@letsrevel.io", password="x", is_staff=True)


@pytest.fixture
def token(me: User) -> str:
    return UserApiToken.issue(me, "cli")[1]


@pytest.fixture
def lead() -> Lead:
    return Lead.objects.create(name="Org", email="org@example.com", status=Lead.Status.NEW)


def make_draft(lead: Lead, user: User | None, **kw: t.Any) -> EmailDraft:
    subject, body = kw.pop("subject", "Hello"), kw.pop("body", "<p>Hi there</p>")
    kw.setdefault("humanized_hash", humanize(subject, body))
    return EmailDraft.objects.create(
        lead=lead, assigned_to=user, to=["org@example.com"], subject=subject, body=body, **kw
    )


def bearer(tok: str) -> dict[str, t.Any]:
    return {"HTTP_AUTHORIZATION": f"Bearer {tok}"}


@pytest.fixture
def gmail() -> t.Iterator[MagicMock]:
    with patch("leads.gmail.get_gmail_credentials"), patch("leads.gmail.send_email_via_gmail") as send:
        yield send


# --- token model & auth ---


def test_only_hash_is_stored(me: User) -> None:
    row, plaintext = UserApiToken.issue(me, "cli")
    assert plaintext.startswith("mcrm_")
    assert row.token_hash == hashlib.sha256(plaintext.encode()).hexdigest()
    assert plaintext not in {row.token_hash, row.prefix, row.name}
    assert not UserApiToken.objects.filter(token_hash=plaintext).exists()
    assert plaintext.startswith(row.prefix)


def test_bearer_authenticates_and_stamps_last_used(prod_auth: None, api_client: Client, token: str) -> None:
    assert api_client.get("/api/leads/").status_code == 401
    r = api_client.get("/api/leads/", **bearer(token))
    assert r.status_code == 200
    assert UserApiToken.objects.get().last_used_at is not None


def test_bad_or_revoked_token_rejected(prod_auth: None, api_client: Client, me: User, token: str) -> None:
    assert api_client.get("/api/leads/", **bearer("mcrm_nope")).status_code == 401
    UserApiToken.objects.get().revoke()
    assert api_client.get("/api/leads/", **bearer(token)).status_code == 401


def test_inactive_user_token_rejected(prod_auth: None, api_client: Client, me: User, token: str) -> None:
    User.objects.filter(pk=me.pk).update(is_active=False)
    assert api_client.get("/api/leads/", **bearer(token)).status_code == 401


def test_shared_api_key_still_works(prod_auth: None, api_client: Client) -> None:
    assert api_client.get("/api/leads/", HTTP_X_API_KEY="shared-key").status_code == 200
    assert api_client.get("/api/leads/", HTTP_X_API_KEY="wrong").status_code == 401


def test_api_key_alone_gets_403_on_send(prod_auth: None, api_client: Client, me: User, lead: Lead) -> None:
    draft = make_draft(lead, me)
    r = api_client.post(f"/api/email-drafts/{draft.pk}/send", HTTP_X_API_KEY="shared-key")
    assert r.status_code == 403
    assert "personal API token" in r.json()["detail"]
    r = api_client.post(
        "/api/email-drafts/send", {"draft_ids": [draft.pk]}, content_type=JSON, HTTP_X_API_KEY="shared-key"
    )
    assert r.status_code == 403
    assert EmailDraft.objects.filter(pk=draft.pk).exists()


def test_revoke_idempotent(me: User) -> None:
    row, _ = UserApiToken.issue(me, "x")
    row.revoke()
    first = row.revoked_at
    row.revoke()
    assert row.revoked_at == first and not row.is_active


# --- admin ---


def _admin_request(user: User) -> t.Any:
    req: t.Any = RequestFactory().post("/")
    req.user = user
    req.session = {}
    req._messages = FallbackStorage(req)
    return req


def test_admin_create_shows_plaintext_once_and_scopes(me: User, other: User) -> None:
    madmin = UserApiTokenAdmin(UserApiToken, django_admin.site)
    me.is_staff = False
    req = _admin_request(me)
    assert madmin.has_add_permission(req) is False  # not staff
    me.is_staff = True
    req = _admin_request(me)
    assert madmin.has_add_permission(req)
    obj = UserApiToken(name="laptop")
    madmin.save_model(req, obj, MagicMock(), change=False)
    row = UserApiToken.objects.get(name="laptop")
    assert row.user == me and obj.pk == row.pk
    shown = [str(m) for m in req._messages]
    assert len(shown) == 1 and "mcrm_" in shown[0]
    plaintext = shown[0].split("<code>")[1].split("</code>")[0]
    assert UserApiToken.hash_token(plaintext) == row.token_hash

    UserApiToken.issue(other, "theirs")
    assert list(madmin.get_queryset(req)) == [row]
    me.is_superuser = True
    assert madmin.get_queryset(req).count() == 2


def test_admin_revoke_action_only_own(me: User, other: User) -> None:
    madmin = UserApiTokenAdmin(UserApiToken, django_admin.site)
    mine, _ = UserApiToken.issue(me, "mine")
    theirs, _ = UserApiToken.issue(other, "theirs")
    req = _admin_request(me)
    madmin.revoke_selected(req, madmin.get_queryset(req))
    mine.refresh_from_db()
    theirs.refresh_from_db()
    assert mine.revoked_at is not None and theirs.revoked_at is None


# --- sending: success path ---


def test_send_single_success(
    prod_auth: None, api_client: Client, me: User, token: str, lead: Lead, gmail: MagicMock
) -> None:
    EmailSignature.objects.create(user=me, body="<b>Biagio</b>")
    draft = make_draft(lead, me)
    r = api_client.post(f"/api/email-drafts/{draft.pk}/send", **bearer(token))
    assert r.status_code == 200
    data = r.json()
    assert data["sent"] is True and data["reasons"] == [] and data["draft_id"] == draft.pk

    gmail.assert_called_once()
    _, from_email, to, subject, body, _bcc = gmail.call_args.args
    assert from_email == "Biagio <biagio@letsrevel.io>" and to == ["org@example.com"] and subject == "Hello"
    assert body == "<p>Hi there</p><p><br></p><p>--</p><div data-revel-signature><b>Biagio</b></div>"

    sent = EmailSent.objects.get()
    assert sent.pk == data["email_sent_id"] and sent.status == EmailSent.Status.SENT and sent.sent_by == me
    assert sent.body == body
    assert not EmailDraft.objects.filter(pk=draft.pk).exists()
    lead.refresh_from_db()
    assert lead.status == Lead.Status.CONTACTED and lead.last_contact == timezone.localdate()
    action = Action.objects.get(lead=lead)
    assert action.name == "Follow up email" and action.due_date == timezone.localdate() + timedelta(days=4)


def test_qualified_lead_moves_to_contacted(me: User, lead: Lead, gmail: MagicMock) -> None:
    Lead.objects.filter(pk=lead.pk).update(status=Lead.Status.QUALIFIED)
    assert service.send_draft_as_user(make_draft(lead, me).pk, me).sent
    lead.refresh_from_db()
    assert lead.status == Lead.Status.CONTACTED


def test_batch_mixed_results(
    prod_auth: None, api_client: Client, me: User, other: User, token: str, gmail: MagicMock
) -> None:
    ok = make_draft(Lead.objects.create(name="A"), me)
    theirs = make_draft(Lead.objects.create(name="B"), other)
    r = api_client.post(
        "/api/email-drafts/send", {"draft_ids": [ok.pk, theirs.pk, 99999, ok.pk]}, content_type=JSON, **bearer(token)
    )
    assert r.status_code == 200
    data = r.json()
    assert data["sent"] == 1 and data["refused"] == 2
    by_id = {x["draft_id"]: x for x in data["results"]}
    assert len(data["results"]) == 3  # duplicate collapsed
    assert by_id[ok.pk]["sent"] and by_id[theirs.pk]["reasons"] == ["draft is not assigned to you"]
    assert by_id[99999]["reasons"] == ["draft not found"]
    assert gmail.call_count == 1


# --- signature ---


def test_signature_not_duplicated_when_body_already_has_it(me: User) -> None:
    EmailSignature.objects.create(user=me, body="<p>Biagio&nbsp;Di Stefano</p>")
    body = "<p>Hi</p><p>--</p><p>Biagio Di  Stefano</p>"
    assert service.append_signature(body, me) == body


def test_signature_noop_without_signature_or_user(me: User) -> None:
    assert service.append_signature("<p>x</p>", me) == "<p>x</p>"
    EmailSignature.objects.create(user=me, body="  ")
    assert service.append_signature("<p>x</p>", me) == "<p>x</p>"
    assert service.append_signature("<p>x</p>", None) == "<p>x</p>"


def test_admin_send_path_appends_signature_too(me: User, lead: Lead, gmail: MagicMock) -> None:
    EmailSignature.objects.create(user=me, body="<i>sig</i>")
    service.send_email_to_lead(lead, "S", "<p>b</p>", ["org@example.com"], user=me)
    assert gmail.call_args.args[4] == "<p>b</p><p><br></p><p>--</p><div data-revel-signature><i>sig</i></div>"


# --- sending: refusals ---


def _refused(me: User, draft: EmailDraft, gmail: MagicMock, fragment: str) -> None:
    result = service.send_draft_as_user(draft.pk, me)
    assert not result.sent and any(fragment in r for r in result.reasons), result.reasons
    gmail.assert_not_called()
    assert EmailDraft.objects.filter(pk=draft.pk).exists()
    assert not EmailSent.objects.exists()
    assert not Action.objects.exists()


def test_refuse_not_assigned(me: User, other: User, lead: Lead, gmail: MagicMock) -> None:
    _refused(me, make_draft(lead, other), gmail, "not assigned to you")


def test_refuse_unassigned(me: User, lead: Lead, gmail: MagicMock) -> None:
    _refused(me, make_draft(lead, None), gmail, "not assigned to you")


def test_single_endpoint_other_users_draft_is_404(
    prod_auth: None, api_client: Client, other: User, token: str, lead: Lead
) -> None:
    draft = make_draft(lead, other)
    assert api_client.post(f"/api/email-drafts/{draft.pk}/send", **bearer(token)).status_code == 404
    assert api_client.post("/api/email-drafts/99999/send", **bearer(token)).status_code == 404


def test_single_endpoint_refusal_is_409(
    prod_auth: None, api_client: Client, me: User, token: str, lead: Lead, gmail: MagicMock
) -> None:
    draft = make_draft(lead, me, humanized_hash="")
    r = api_client.post(f"/api/email-drafts/{draft.pk}/send", **bearer(token))
    assert r.status_code == 409 and r.json()["sent"] is False and r.json()["reasons"]
    gmail.assert_not_called()


def test_refuse_do_not_contact(me: User, lead: Lead, gmail: MagicMock) -> None:
    draft = make_draft(lead, me)
    Lead.objects.filter(pk=lead.pk).update(do_not_contact=True)
    _refused(me, draft, gmail, "do not contact")


def test_refuse_previous_email(me: User, lead: Lead, gmail: MagicMock) -> None:
    EmailSent.objects.create(lead=lead, from_email="a@b.c", to=["x@y.z"], subject="s", body="b", status="sent")
    result = service.send_draft_as_user(make_draft(lead, me).pk, me)
    assert not result.sent and any("email was already sent" in r for r in result.reasons)
    gmail.assert_not_called()


def test_failed_previous_email_does_not_block(me: User, lead: Lead, gmail: MagicMock) -> None:
    EmailSent.objects.create(lead=lead, from_email="a@b.c", to=["x@y.z"], subject="s", body="b", status="failed")
    assert service.send_draft_as_user(make_draft(lead, me).pk, me).sent


def test_refuse_previous_dm(me: User, lead: Lead, gmail: MagicMock) -> None:
    OutreachMessage.objects.create(lead=lead, channel="instagram", text="hi", status=OutreachMessage.Status.SENT)
    result = service.send_draft_as_user(make_draft(lead, me).pk, me)
    assert any("DM was already sent" in r for r in result.reasons)
    gmail.assert_not_called()


def test_unsent_dm_does_not_block(me: User, lead: Lead, gmail: MagicMock) -> None:
    OutreachMessage.objects.create(lead=lead, channel="instagram", text="hi")
    assert service.send_draft_as_user(make_draft(lead, me).pk, me).sent


@pytest.mark.parametrize(
    "status", [s for s in Lead.Status.values if s not in (Lead.Status.NEW.value, Lead.Status.QUALIFIED.value)]
)
def test_refuse_status_beyond_qualified(me: User, lead: Lead, gmail: MagicMock, status: str) -> None:
    Lead.objects.filter(pk=lead.pk).update(status=status)
    _refused(me, make_draft(lead, me), gmail, "lead status")


def test_refuse_last_contact_set(me: User, lead: Lead, gmail: MagicMock) -> None:
    Lead.objects.filter(pk=lead.pk).update(last_contact=date(2026, 1, 1))
    _refused(me, make_draft(lead, me), gmail, "last contacted")


def test_refuse_not_humanized(me: User, lead: Lead, gmail: MagicMock) -> None:
    _refused(me, make_draft(lead, me, humanized_hash=""), gmail, "not humanized")


def test_refuse_edited_after_humanizing(me: User, lead: Lead, gmail: MagicMock) -> None:
    draft = make_draft(lead, me)
    EmailDraft.objects.filter(pk=draft.pk).update(body="<p>edited</p>")
    _refused(me, EmailDraft.objects.get(pk=draft.pk), gmail, "not humanized")


def test_refuse_no_gmail_connection(me: User, lead: Lead, gmail: MagicMock) -> None:
    GmailConnection.objects.filter(user=me).update(is_active=False)
    _refused(me, make_draft(lead, me), gmail, "Gmail connection")


def test_refuse_placeholders(me: User, lead: Lead, gmail: MagicMock) -> None:
    _refused(me, make_draft(lead, me, subject="Hi {name}"), gmail, "placeholders")


def test_reasons_accumulate(me: User, lead: Lead, gmail: MagicMock) -> None:
    Lead.objects.filter(pk=lead.pk).update(do_not_contact=True, status="contacted")
    result = service.send_draft_as_user(make_draft(lead, me, humanized_hash="").pk, me)
    assert len(result.reasons) >= 3


def test_gmail_failure_keeps_draft_and_reports(me: User, lead: Lead, gmail: MagicMock) -> None:
    gmail.side_effect = RuntimeError("boom")
    draft = make_draft(lead, me)
    result = service.send_draft_as_user(draft.pk, me)
    assert not result.sent and "boom" in result.reasons[0]
    assert EmailDraft.objects.filter(pk=draft.pk).exists()
    assert EmailSent.objects.get().status == EmailSent.Status.FAILED
    lead.refresh_from_db()
    assert lead.status == Lead.Status.NEW and lead.last_contact is None
    assert not Action.objects.exists()
    gmail.side_effect = None  # a failed attempt does not block the retry
    assert service.send_draft_as_user(draft.pk, me).sent


# --- rate limit ---


def test_daily_limit(settings: t.Any, me: User, gmail: MagicMock) -> None:
    settings.EMAIL_SEND_DAILY_LIMIT_PER_USER = 2
    drafts = [make_draft(Lead.objects.create(name=f"L{i}"), me) for i in range(3)]
    results = service.send_drafts_as_user([d.pk for d in drafts], me)
    assert [r.sent for r in results] == [True, True, False]
    assert "daily send limit" in results[2].reasons[0]
    assert gmail.call_count == 2 and EmailDraft.objects.filter(pk=drafts[2].pk).exists()


def test_daily_limit_is_per_user_and_per_day(settings: t.Any, me: User, other: User, gmail: MagicMock) -> None:
    settings.EMAIL_SEND_DAILY_LIMIT_PER_USER = 1
    GmailConnection.objects.create(user=other, email="w@letsrevel.io", refresh_token="r")
    old = EmailSent.objects.create(
        lead=Lead.objects.create(name="Old"), from_email="a@b.c", to=["x@y.z"], subject="s", body="b",
        status="sent", sent_by=me, sent_at=timezone.now() - timedelta(days=1, hours=1),
    )  # fmt: skip
    EmailSent.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=1, hours=1))
    assert service.send_draft_as_user(make_draft(Lead.objects.create(name="A"), me).pk, me).sent  # yesterday ignored
    assert not service.send_draft_as_user(make_draft(Lead.objects.create(name="B"), me).pk, me).sent
    assert service.send_draft_as_user(make_draft(Lead.objects.create(name="C"), other).pk, other).sent


def test_default_limit_is_50(settings: t.Any) -> None:
    assert settings.EMAIL_SEND_DAILY_LIMIT_PER_USER == 50


# --- mark-sent & contact-check ---


def make_msg(lead: Lead, **kw: t.Any) -> OutreachMessage:
    return OutreachMessage.objects.create(lead=lead, channel="instagram", text="hi", **kw)


def test_mark_sent_with_token_records_sender(
    prod_auth: None, api_client: Client, me: User, token: str, lead: Lead
) -> None:
    msg = make_msg(lead)
    r = api_client.post(f"/api/messages/{msg.pk}/mark-sent", **bearer(token))
    assert r.status_code == 200 and r.json()["sent_by_email"] == "biagio@letsrevel.io"
    msg.refresh_from_db()
    assert msg.sent_by == me


def test_mark_sent_with_api_key_has_no_sender(prod_auth: None, api_client: Client, lead: Lead) -> None:
    msg = make_msg(lead)
    r = api_client.post(f"/api/messages/{msg.pk}/mark-sent", HTTP_X_API_KEY="shared-key")
    assert r.status_code == 200 and r.json()["sent_by_email"] is None


def test_contact_check_clean(api_client: Client, lead: Lead) -> None:
    r = api_client.get(f"/api/messages/{make_msg(lead).pk}/contact-check")
    assert r.status_code == 200 and r.json() == {"already_contacted": False, "reasons": []}


def test_contact_check_reasons(api_client: Client, lead: Lead) -> None:
    msg = make_msg(lead)
    EmailSent.objects.create(lead=lead, from_email="a@b.c", to=["x@y.z"], subject="s", body="b", status="sent")
    make_msg(lead, status=OutreachMessage.Status.SENT)
    Lead.objects.filter(pk=lead.pk).update(status="replied", last_contact=date(2026, 1, 1))
    data = api_client.get(f"/api/messages/{msg.pk}/contact-check").json()
    assert data["already_contacted"] is True and len(data["reasons"]) == 4


def test_contact_check_404(api_client: Client) -> None:
    assert api_client.get("/api/messages/99999/contact-check").status_code == 404


# --- review round: scope, staff, PENDING flow, non-atomic, marker, batch cap ---


def test_send_email_endpoint_removed(prod_auth: None, api_client: Client, token: str, lead: Lead) -> None:
    body = {"subject": "s", "body": "b", "to": ["x@y.z"]}
    for headers in ({"HTTP_X_API_KEY": "shared-key"}, bearer(token)):
        r = api_client.post(f"/api/leads/{lead.pk}/send-email", body, content_type=JSON, **headers)
        assert r.status_code in (403, 404, 405)


def test_no_other_api_send_path() -> None:
    from crm.api import api

    senders = sorted(p for p in api.get_openapi_schema()["paths"] if "send" in p)
    assert senders == ["/api/email-drafts/send", "/api/email-drafts/{draft_id}/send"]


def test_non_staff_token_fails(prod_auth: None, api_client: Client, me: User, token: str) -> None:
    assert api_client.get("/api/leads/", **bearer(token)).status_code == 200
    User.objects.filter(pk=me.pk).update(is_staff=False)
    assert api_client.get("/api/leads/", **bearer(token)).status_code == 401
    lead = Lead.objects.create(name="Z")
    assert api_client.delete(f"/api/leads/{lead.pk}", **bearer(token)).status_code == 401
    assert Lead.objects.filter(pk=lead.pk).exists()


def test_token_scope(prod_auth: None, api_client: Client, token: str, lead: Lead) -> None:
    h = bearer(token)
    for path in ("/api/leads/", f"/api/leads/{lead.pk}", "/api/email-drafts/", "/api/messages/"):
        assert api_client.get(path, **h).status_code == 200, path
    for method, path in (
        ("get", "/api/tags/"),
        ("get", "/api/email-templates/"),
        ("get", "/api/emails-sent/"),
        ("delete", f"/api/leads/{lead.pk}"),
        ("patch", f"/api/leads/{lead.pk}"),
        ("post", "/api/leads/"),
        ("post", "/api/leads/merge"),
        ("post", "/api/email-drafts/"),
        ("delete", "/api/email-drafts/1"),
        ("post", "/api/messages/"),
        ("post", "/api/messages/1/skip"),
        ("post", "/api/cities/"),
    ):
        r = getattr(api_client, method)(path, **h)
        assert r.status_code == 403, (method, path, r.status_code)
        assert "use the API key" in r.json()["detail"]
    assert Lead.objects.filter(pk=lead.pk).exists()


def test_scope_allows_contact_check_and_mark_sent(prod_auth: None, api_client: Client, token: str, lead: Lead) -> None:
    msg = make_msg(lead)
    assert api_client.get(f"/api/messages/{msg.pk}/contact-check", **bearer(token)).status_code == 200
    assert api_client.post(f"/api/messages/{msg.pk}/mark-sent", **bearer(token)).status_code == 200


def test_api_key_still_unrestricted(prod_auth: None, api_client: Client, lead: Lead) -> None:
    assert api_client.delete(f"/api/leads/{lead.pk}", HTTP_X_API_KEY="shared-key").status_code == 204


def test_pending_row_created_before_gmail_and_blocks_retry(me: User, lead: Lead) -> None:
    draft = make_draft(lead, me)
    seen: dict[str, t.Any] = {}

    def fake_send(*args: t.Any, **kw: t.Any) -> None:
        from django.db import connection

        seen["in_atomic"] = connection.in_atomic_block
        row = EmailSent.objects.get()
        seen["status"] = row.status
        # a concurrent/retried send for the same lead is refused while this one is in flight
        seen["retry"] = service.send_draft_as_user(draft.pk, me)

    with patch("leads.gmail.get_gmail_credentials"), patch("leads.gmail.send_email_via_gmail", side_effect=fake_send):
        result = service.send_draft_as_user(draft.pk, me)
    assert result.sent
    assert seen["status"] == EmailSent.Status.PENDING
    assert not seen["retry"].sent and any("email was already sent" in r for r in seen["retry"].reasons)
    assert EmailSent.objects.get().status == EmailSent.Status.SENT


def test_stale_pending_still_blocks_and_is_flagged(me: User, lead: Lead, gmail: MagicMock) -> None:
    row = EmailSent.objects.create(
        lead=lead, from_email="a@b.c", to=["x@y.z"], subject="s", body="b", status="pending", sent_by=me
    )
    assert not service.is_stale_pending(row)
    EmailSent.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(minutes=16))
    row.refresh_from_db()
    assert service.is_stale_pending(row)
    madmin: t.Any = django_admin.site._registry[EmailSent]
    assert "outcome unknown" in str(madmin.display_status(row))
    assert not service.send_draft_as_user(make_draft(lead, me).pk, me).sent
    gmail.assert_not_called()


def test_phase_c_failure_leaves_pending_and_blocks_resend(me: User, lead: Lead) -> None:
    draft = make_draft(lead, me)
    with (
        patch("leads.gmail.get_gmail_credentials"),
        patch("leads.gmail.send_email_via_gmail") as send,
        patch.object(service, "_apply_sent_effects", side_effect=RuntimeError("db hiccup")),
    ):
        with pytest.raises(RuntimeError):
            service.send_draft_as_user(draft.pk, me)
        assert send.call_count == 1
        assert EmailSent.objects.get().status == EmailSent.Status.PENDING
        assert EmailDraft.objects.filter(pk=draft.pk).exists()
        assert not service.send_draft_as_user(draft.pk, me).sent
        assert send.call_count == 1  # no double send


@pytest.mark.django_db(transaction=True)
def test_batch_failure_midway_does_not_double_send_on_retry(prod_auth: None, me: User, token: str) -> None:
    from django.db import connection

    ids = [make_draft(Lead.objects.create(name=f"L{i}"), me).pk for i in range(2)]
    calls = {"n": 0}
    real = service._apply_sent_effects

    def flaky(lead: Lead, es: EmailSent) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("worker died")
        real(lead, es)

    c = Client(raise_request_exception=False)
    old = connection.settings_dict["ATOMIC_REQUESTS"]
    connection.settings_dict["ATOMIC_REQUESTS"] = True  # as in production
    try:
        with (
            patch("leads.gmail.get_gmail_credentials"),
            patch("leads.gmail.send_email_via_gmail") as send,
            patch.object(service, "_apply_sent_effects", flaky),
        ):
            r = c.post("/api/email-drafts/send", {"draft_ids": ids}, content_type=JSON, **bearer(token))
            assert r.status_code == 500 and send.call_count == 2
            assert EmailSent.objects.filter(status="sent").count() == 1  # first one's commit survived
            r = c.post("/api/email-drafts/send", {"draft_ids": ids}, content_type=JSON, **bearer(token))
            assert r.status_code == 200
            assert send.call_count == 2  # nothing re-sent
    finally:
        connection.settings_dict["ATOMIC_REQUESTS"] = old


def test_non_atomic_middleware_marks_only_send_views() -> None:
    from crm.middleware import NonAtomicSendMiddleware

    mw = NonAtomicSendMiddleware(MagicMock())
    for path, flagged in (
        ("/api/email-drafts/send", True),
        ("/api/email-drafts/12/send", True),
        ("/api/email-drafts/", False),
        ("/api/leads/", False),
    ):
        req = RequestFactory().post(path)

        def view(request: t.Any) -> t.Any: ...

        mw.process_view(req, view, (), {})
        assert hasattr(view, "_non_atomic_requests") is flagged, path


def test_daily_limit_counts_pending(settings: t.Any, me: User, gmail: MagicMock) -> None:
    settings.EMAIL_SEND_DAILY_LIMIT_PER_USER = 1
    EmailSent.objects.create(
        lead=Lead.objects.create(name="P"), from_email="a@b.c", to=["x@y.z"], subject="s", body="b",
        status="pending", sent_by=me,
    )  # fmt: skip
    result = service.send_draft_as_user(make_draft(Lead.objects.create(name="A"), me).pk, me)
    assert not result.sent and "daily send limit" in result.reasons[0]


def test_send_slot_lock_is_taken_on_gmail_connection(me: User, lead: Lead, gmail: MagicMock) -> None:
    from django.db.models.query import QuerySet

    locked: list[str] = []
    real = QuerySet.select_for_update

    def spy(self: t.Any, *a: t.Any, **k: t.Any) -> t.Any:
        locked.append(self.model.__name__)
        return real(self, *a, **k)

    with patch.object(QuerySet, "select_for_update", spy):
        assert service.send_draft_as_user(make_draft(lead, me).pk, me).sent
    assert locked.index("GmailConnection") < locked.index("Lead")


def test_batch_capped_at_10(prod_auth: None, api_client: Client, token: str) -> None:
    r = api_client.post("/api/email-drafts/send", {"draft_ids": list(range(1, 12))}, content_type=JSON, **bearer(token))
    assert r.status_code == 422
    r = api_client.post("/api/email-drafts/send", {"draft_ids": list(range(1, 11))}, content_type=JSON, **bearer(token))
    assert r.status_code == 200


def test_signature_marker_prevents_duplicate_even_if_edited(me: User) -> None:
    EmailSignature.objects.create(user=me, body="<p>Weronika &amp; team<br>+49 123</p>")
    prefilled = "<p>Hi</p><p><br></p><p>--</p><div data-revel-signature><p>Weronika (edited) +49</p></div>"
    assert service.append_signature(prefilled, me) == prefilled
    first = service.append_signature("<p>Hi</p>", me)
    assert "<div data-revel-signature>" in first
    assert service.append_signature(first, me) == first


def test_signature_image_only_marker(me: User) -> None:
    EmailSignature.objects.create(user=me, body='<p><img src="https://x/logo.png"></p>')
    once = service.append_signature("<p>Hi</p>", me)
    assert service.append_signature(once, me) == once


def test_admin_editor_prefill_uses_marker() -> None:
    from pathlib import Path

    tpl = Path(__file__).parents[2] / "templates/admin/leads/lead/send_email.html"
    assert "data-revel-signature" in tpl.read_text()
