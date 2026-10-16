"""Tests for outreach messages, do-not-contact, humanized hashes, draft assignment and new lead filters."""

import hashlib
import typing as t
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.contrib import admin as django_admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.contrib.messages.storage.fallback import FallbackStorage
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpRequest
from django.test import Client, RequestFactory
from django.utils import timezone

from leads import models, service
from leads.admin import EmailDraftAdmin, LeadAdmin, OutreachMessageAdmin, OutreachQueueFilter
from leads.models import Action, EmailDraft, GmailConnection, Lead, OutreachMessage, Tag

pytestmark = pytest.mark.django_db


ASSIGNEE = "weronika@letsrevel.io"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def rep() -> User:
    return User.objects.create_user("weronika", email="weronika@letsrevel.io", password="x")


@pytest.fixture
def social_lead() -> Lead:
    return Lead.objects.create(
        name="Social Org",
        instagram="@some.org",
        facebook="https://www.facebook.com/someorg/",
        whatsapp="+49 (170) 123-4567",
        bluesky="@some.bsky.social",
        linkedin="https://www.linkedin.com/company/some-org",
        telegram="https://t.me/someorg",
    )


def msg(lead: Lead, channel: str, text: str = "hi", **kw: t.Any) -> OutreachMessage:
    return OutreachMessage.objects.create(lead=lead, channel=channel, text=text, **kw)


class TestDeeplinks:
    @pytest.mark.parametrize(
        ("channel", "expected"),
        [
            ("instagram", "https://ig.me/m/some.org"),
            ("facebook", "https://m.me/someorg"),
            ("whatsapp", "https://wa.me/491701234567?text=hello%20w%C3%B6rld%20%26%20co%3F%0Aline2"),
            ("bluesky", "https://bsky.app/profile/some.bsky.social"),
            ("linkedin", "https://www.linkedin.com/company/some-org"),
            ("telegram", "https://t.me/someorg"),
        ],
    )
    def test_each_channel(self, social_lead: Lead, channel: str, expected: str) -> None:
        m = msg(social_lead, channel, "hello wörld & co?\nline2")
        assert m.deeplink == expected

    @pytest.mark.parametrize("channel", [c.value for c in OutreachMessage.Channel])
    def test_empty_when_lead_lacks_field(self, channel: str) -> None:
        lead = Lead.objects.create(name="Bare")
        assert msg(lead, channel).deeplink == ""

    def test_url_forms(self) -> None:
        lead = Lead.objects.create(
            name="U",
            instagram="https://instagram.com/foo/?hl=en",
            facebook="https://facebook.com/profile.php?id=12345",
            bluesky="https://bsky.app/profile/bar.bsky.social",
            linkedin="linkedin.com/in/x",
        )
        assert msg(lead, "instagram").deeplink == "https://ig.me/m/foo"
        assert msg(lead, "facebook").deeplink == "https://m.me/12345"
        assert msg(lead, "bluesky").deeplink == "https://bsky.app/profile/bar.bsky.social"
        assert msg(lead, "linkedin").deeplink == "https://linkedin.com/in/x"

    def test_facebook_pages_url_uses_id(self) -> None:
        lead = Lead.objects.create(name="F", facebook="https://facebook.com/pages/Some-Org/998877")
        assert msg(lead, "facebook").deeplink == "https://m.me/998877"

    def test_instagram_falls_back_to_contact(self) -> None:
        lead = Lead.objects.create(name="C")
        models.Contact.objects.create(lead=lead, name="Primary", is_primary=True, instagram="viacontact")
        assert msg(lead, "instagram").deeplink == "https://ig.me/m/viacontact"

    def test_handles_are_url_quoted(self) -> None:
        lead = Lead.objects.create(name="Q", telegram="a b/../c")
        assert " " not in msg(lead, "telegram").deeplink

    @pytest.mark.parametrize(
        "value",
        [
            "javascript:alert(1)",
            "javascript:alert(1)//linkedin.com",
            "https://evil.com/linkedin.com",
            "badlinkedin.com/x",
            "https://linkedin.com.evil.io/in/x",
            "some-slug",
            "ftp://linkedin.com/in/x",
        ],
    )
    def test_linkedin_only_on_linkedin_host(self, value: str) -> None:
        lead = Lead.objects.create(name="X", linkedin=value)
        assert msg(lead, "linkedin").deeplink == ""


class TestHumanized:
    def test_message(self, lead: Lead) -> None:
        assert msg(lead, "instagram", "hello").is_humanized is False
        assert msg(lead, "instagram", "hello", humanized_hash=sha("hello")).is_humanized is True
        assert msg(lead, "instagram", "edited", humanized_hash=sha("hello")).is_humanized is False

    def test_draft(self, lead: Lead) -> None:
        h = sha("Subj\x1fBody")
        assert EmailDraft(lead=lead, subject="Subj", body="Body", humanized_hash=h).is_humanized is True
        assert EmailDraft(lead=lead, subject="Subj", body="Body2", humanized_hash=h).is_humanized is False
        assert EmailDraft(lead=lead, subject="Subj", body="Body").is_humanized is False

    def test_api_exposes_flag(self, api_client: Client, lead: Lead) -> None:
        m = msg(lead, "instagram", "hello", humanized_hash=sha("hello"))
        assert api_client.get(f"/api/messages/{m.pk}").json()["is_humanized"] is True
        d = EmailDraft.objects.create(lead=lead, subject="S", body="B", humanized_hash=sha("S\x1fB"))
        assert api_client.get(f"/api/email-drafts/{d.pk}").json()["is_humanized"] is True


class TestMarkSent:
    def test_side_effects(self, lead: Lead, rep: User) -> None:
        m = msg(lead, "instagram")
        service.mark_message_sent(m, rep)
        m.refresh_from_db()
        lead.refresh_from_db()
        assert m.status == "sent" and m.sent_by == rep and m.sent_at is not None
        assert lead.status == Lead.Status.CONTACTED
        assert lead.last_contact == timezone.localdate()
        action = Action.objects.get(lead=lead)
        assert action.name == "Follow up Instagram DM"
        assert action.due_date == timezone.localdate() + timedelta(days=4)
        assert action.status == Action.Status.PENDING

    @pytest.mark.parametrize("status", ["contacted", "replied", "qualified", "call_booked", "converted", "lost"])
    def test_no_status_change_for_non_new(self, lead: Lead, status: str) -> None:
        Lead.objects.filter(pk=lead.pk).update(status=status)
        service.mark_message_sent(msg(lead, "facebook"), None)
        lead.refresh_from_db()
        assert lead.status == status
        assert lead.last_contact == timezone.localdate()

    def test_idempotent(self, lead: Lead) -> None:
        m = msg(lead, "instagram")
        service.mark_message_sent(m, None)
        service.mark_message_sent(m, None)
        assert Action.objects.filter(lead=lead).count() == 1

    def test_skip(self, lead: Lead) -> None:
        m = service.mark_message_skipped(msg(lead, "instagram"))
        lead.refresh_from_db()
        assert m.status == "skipped"
        assert lead.status == Lead.Status.NEW and lead.last_contact is None
        assert not Action.objects.exists()

    def test_cannot_skip_sent(self, lead: Lead) -> None:
        m = service.mark_message_sent(msg(lead, "instagram"), None)
        with pytest.raises(ValueError):
            service.mark_message_skipped(m)


class TestMessageApi:
    def _create(self, client: Client, lead: Lead, **extra: t.Any) -> t.Any:
        payload = {"lead_id": lead.pk, "channel": "instagram", "text": "Hey!", "assigned_to_email": ASSIGNEE, **extra}
        return client.post("/api/messages/", payload, content_type="application/json")

    def test_create_shape(self, api_client: Client, lead: Lead, rep: User) -> None:
        lead.instagram = "org"
        lead.save()
        resp = self._create(
            api_client, lead, assigned_to_email="WERONIKA@letsrevel.io", humanized_hash="abc", batch="b1"
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["status"] == "draft"
        assert body["assigned_to_email"] == "weronika@letsrevel.io"
        assert body["deeplink"] == "https://ig.me/m/org"
        assert body["batch"] == "b1" and body["humanized_hash"] == "abc"
        assert body["lead_id"] == lead.pk and body["is_humanized"] is False
        assert body["sent_at"] is None and body["sent_by_email"] is None

    def test_create_ignores_status(self, api_client: Client, lead: Lead) -> None:
        User.objects.create_user("w2", email=ASSIGNEE)
        resp = self._create(api_client, lead, status="sent")
        assert resp.json()["status"] == "draft"

    def test_unknown_assignee_404_and_lead_404(self, api_client: Client, lead: Lead, rep: User) -> None:
        assert self._create(api_client, lead, assigned_to_email="no@one.io").status_code == 404
        resp = api_client.post(
            "/api/messages/",
            {"lead_id": 999, "channel": "instagram", "text": "x", "assigned_to_email": rep.email},
            content_type="application/json",
        )
        assert resp.status_code == 404

    def test_assignee_required(self, api_client: Client, lead: Lead) -> None:
        r = api_client.post(
            "/api/messages/", {"lead_id": lead.pk, "channel": "instagram", "text": "x"}, content_type="application/json"
        )
        assert r.status_code == 422

    def test_bad_channel_and_empty_text(self, api_client: Client, lead: Lead) -> None:
        assert self._create(api_client, lead, channel="sms").status_code == 422
        assert self._create(api_client, lead, text="").status_code == 422

    def test_list_filters(self, api_client: Client, lead: Lead, rep: User) -> None:
        a = msg(lead, "instagram", batch="b1", assigned_to=rep)
        msg(lead, "facebook", batch="b2")
        service.mark_message_skipped(msg(lead, "whatsapp", batch="b1"))

        def count(q: str) -> int:
            return t.cast(int, api_client.get(f"/api/messages/?{q}").json()["count"])

        assert count("") == 3
        assert count("batch=b1") == 2
        assert count("status=draft") == 2
        assert count("status=skipped") == 1
        assert count("channel=facebook") == 1
        assert count("assigned_to_email=Weronika@letsrevel.io") == 1
        assert count(f"lead_id={lead.pk}") == 3
        assert count("lead_id=9999") == 0
        assert count("created_since=2999-01-01T00:00:00Z") == 0
        assert count("created_since=2000-01-01T00:00:00Z") == 3
        assert a.pk

    def test_patch_put_delete(self, api_client: Client, lead: Lead, rep: User) -> None:
        m = msg(lead, "instagram")
        r = api_client.patch(
            f"/api/messages/{m.pk}", {"text": "new", "assigned_to_email": rep.email}, content_type="application/json"
        )
        assert r.json()["text"] == "new" and r.json()["assigned_to_email"] == rep.email
        r = api_client.put(
            f"/api/messages/{m.pk}",
            {"lead_id": lead.pk, "channel": "telegram", "text": "put", "assigned_to_email": rep.email},
            content_type="application/json",
        )
        assert r.json()["channel"] == "telegram" and r.json()["assigned_to_email"] == rep.email
        assert api_client.delete(f"/api/messages/{m.pk}").status_code == 204
        assert api_client.get(f"/api/messages/{m.pk}").status_code == 404

    def test_mark_sent_endpoint(self, api_client: Client, lead: Lead) -> None:
        m = msg(lead, "instagram")
        r = api_client.post(f"/api/messages/{m.pk}/mark-sent")
        assert r.status_code == 200 and r.json()["status"] == "sent" and r.json()["sent_at"]
        lead.refresh_from_db()
        assert lead.status == Lead.Status.CONTACTED

    def test_skip_endpoint(self, api_client: Client, lead: Lead) -> None:
        m = msg(lead, "instagram")
        assert api_client.post(f"/api/messages/{m.pk}/skip").json()["status"] == "skipped"
        s = msg(lead, "facebook")
        service.mark_message_sent(s, None)
        assert api_client.post(f"/api/messages/{s.pk}/skip").status_code == 400
        assert api_client.post("/api/messages/9999/skip").status_code == 404

    def test_auth_required(self, api_client: Client, lead: Lead, settings: t.Any) -> None:
        settings.DEBUG = False
        settings.API_KEY = "secret"
        m = msg(lead, "instagram")
        for method, path in [
            ("get", "/api/messages/"),
            ("get", f"/api/messages/{m.pk}"),
            ("post", "/api/messages/"),
            ("post", f"/api/messages/{m.pk}/mark-sent"),
            ("post", f"/api/messages/{m.pk}/skip"),
            ("delete", f"/api/messages/{m.pk}"),
        ]:
            assert getattr(api_client, method)(path).status_code == 401, path
        ok = api_client.get("/api/messages/", HTTP_X_API_KEY="secret")
        assert ok.status_code == 200


class TestEmailDraftAssignment:
    def _post(self, client: Client, lead: Lead, **extra: t.Any) -> t.Any:
        payload = {"lead_id": lead.pk, "subject": "S", "body": "B", "to": ["a@b.org"], **extra}
        return client.post("/api/email-drafts/", payload, content_type="application/json")

    def test_explicit_from_and_assignee(self, api_client: Client, lead: Lead, rep: User) -> None:
        r = self._post(
            api_client,
            lead,
            from_email="Weronika <weronika@letsrevel.io>",
            assigned_to_email=rep.email,
            humanized_hash="h",
            batch="b9",
        )
        assert r.status_code == 201
        body = r.json()
        assert body["from_email"] == "Weronika <weronika@letsrevel.io>"
        assert body["assigned_to_email"] == rep.email
        assert body["batch"] == "b9" and body["humanized_hash"] == "h"
        assert EmailDraft.objects.get(pk=body["id"]).assigned_to == rep

    def test_from_defaults_to_assignee_gmail(self, api_client: Client, lead: Lead, rep: User) -> None:
        GmailConnection.objects.create(user=rep, email="weronika@letsrevel.io", refresh_token="t")
        r = self._post(api_client, lead, assigned_to_email=rep.email)
        assert r.json()["from_email"] == "weronika@letsrevel.io"

    def test_unknown_assignee(self, api_client: Client, lead: Lead) -> None:
        assert self._post(api_client, lead, assigned_to_email="x@y.z").status_code == 404

    def test_create_does_not_send(self, api_client: Client, lead: Lead, rep: User, mailoutbox: t.Any) -> None:
        self._post(api_client, lead, assigned_to_email=rep.email)
        assert mailoutbox == []
        assert not models.EmailSent.objects.exists()

    def test_filters_and_patch(self, api_client: Client, lead: Lead, rep: User) -> None:
        r = self._post(api_client, lead, assigned_to_email=rep.email, batch="b1").json()
        self._post(api_client, lead, batch="b2")
        assert api_client.get(f"/api/email-drafts/?assigned_to_email={rep.email}").json()["count"] == 1
        assert api_client.get("/api/email-drafts/?batch=b2").json()["count"] == 1
        assert api_client.get("/api/email-drafts/?created_since=2999-01-01T00:00:00Z").json()["count"] == 0
        assert api_client.get("/api/email-drafts/?created_since=2000-01-01T00:00:00Z").json()["count"] == 2
        p = api_client.patch(
            f"/api/email-drafts/{r['id']}", {"assigned_to_email": None}, content_type="application/json"
        )
        assert p.json()["assigned_to_email"] is None


class TestDoNotContact:
    @pytest.fixture
    def dnc(self) -> Lead:
        return service.mark_do_not_contact(Lead.objects.create(name="Nope", email="n@x.org"), "asked to stop")

    def test_marking_stamps(self, dnc: Lead) -> None:
        dnc.refresh_from_db()
        assert dnc.do_not_contact and dnc.do_not_contact_reason == "asked to stop" and dnc.do_not_contact_at

    def test_message_refused(self, api_client: Client, dnc: Lead) -> None:
        r = api_client.post(
            "/api/messages/",
            {"lead_id": dnc.pk, "channel": "instagram", "text": "x", "assigned_to_email": "a@b.c"},
            content_type="application/json",
        )
        assert r.status_code == 400 and "do not contact" in r.json()["detail"]
        assert not OutreachMessage.objects.exists()

    def test_draft_refused(self, api_client: Client, dnc: Lead) -> None:
        r = api_client.post(
            "/api/email-drafts/", {"lead_id": dnc.pk, "subject": "S", "body": "B"}, content_type="application/json"
        )
        assert r.status_code == 400 and "do not contact" in r.json()["detail"]
        assert not EmailDraft.objects.exists()

    def test_admin_save_draft_refused(self, dnc: Lead) -> None:
        from ninja.errors import HttpError

        with pytest.raises(HttpError):
            service.save_email_as_draft(dnc, "S", "B", ["n@x.org"])

    def test_model_clean_refuses_new_only(self, dnc: Lead, lead: Lead) -> None:
        with pytest.raises(ValidationError):
            OutreachMessage(lead=dnc, channel="instagram", text="x").clean()
        with pytest.raises(ValidationError):
            EmailDraft(lead=dnc, subject="S", body="B").clean()
        OutreachMessage(lead=lead, channel="instagram", text="x").clean()

    def test_moving_message_to_dnc_lead_refused(self, api_client: Client, dnc: Lead, lead: Lead) -> None:
        m = msg(lead, "instagram")
        r = api_client.put(
            f"/api/messages/{m.pk}",
            {"lead_id": dnc.pk, "channel": "instagram", "text": "x", "assigned_to_email": "a@b.c"},
            content_type="application/json",
        )
        assert r.status_code == 400

    def test_upsert_match_changes_nothing(self, api_client: Client, dnc: Lead) -> None:
        r = api_client.post(
            "/api/leads/upsert",
            {"name": "Nope", "email": "N@x.org", "company": "NewCo", "tags": ["T"], "do_not_contact": False},
            content_type="application/json",
        )
        assert r.json() == {"id": dnc.pk, "created": False, "do_not_contact": True, "matched_on": "email"}
        dnc.refresh_from_db()
        assert dnc.company == "" and dnc.do_not_contact is True and dnc.tags.count() == 0

    def test_upsert_never_clears(self, api_client: Client, lead: Lead) -> None:
        lead.email = "e@x.org"
        lead.save()
        api_client.post(
            "/api/leads/upsert",
            {"name": "x", "email": "e@x.org", "do_not_contact": True, "do_not_contact_reason": "unsub"},
            content_type="application/json",
        )
        lead.refresh_from_db()
        assert lead.do_not_contact and lead.do_not_contact_reason == "unsub" and lead.do_not_contact_at
        api_client.post(
            "/api/leads/upsert",
            {"name": "x", "email": "e@x.org", "do_not_contact": False},
            content_type="application/json",
        )
        lead.refresh_from_db()
        assert lead.do_not_contact

    def test_upsert_creates_suppressed(self, api_client: Client) -> None:
        r = api_client.post(
            "/api/leads/upsert", {"name": "New", "do_not_contact": True}, content_type="application/json"
        ).json()
        assert r["created"] is True and r["do_not_contact"] is True

    def test_put_and_default_patch_do_not_clear(self, api_client: Client, dnc: Lead) -> None:
        api_client.put(f"/api/leads/{dnc.pk}", {"name": "Nope"}, content_type="application/json")
        api_client.patch(f"/api/leads/{dnc.pk}", {"notes": "n"}, content_type="application/json")
        dnc.refresh_from_db()
        assert dnc.do_not_contact

    def test_explicit_patch_can_clear(self, api_client: Client, dnc: Lead) -> None:
        r = api_client.patch(f"/api/leads/{dnc.pk}", {"do_not_contact": False}, content_type="application/json")
        assert r.json()["do_not_contact"] is False and r.json()["do_not_contact_reason"] == ""

    def test_research_merge_keeps_flag(self, dnc: Lead) -> None:
        from leads.models import City
        from leads.tasks import ResearchLead, _create_lead_from_research

        city = City.objects.create(name="Berlin", country="Germany", iso2="DE")
        _create_lead_from_research(ResearchLead(name="Nope", email="n@x.org", company="Co"), city)
        dnc.refresh_from_db()
        assert dnc.do_not_contact

    def test_list_filter(self, api_client: Client, dnc: Lead, lead: Lead) -> None:
        assert api_client.get("/api/leads/?do_not_contact=true").json()["count"] == 1
        assert api_client.get("/api/leads/?do_not_contact=false").json()["count"] == 1


class TestLeadListFilters:
    def test_tags_all_of(self, api_client: Client) -> None:
        a = Lead.objects.create(name="A")
        b = Lead.objects.create(name="B")
        service.bulk_update_lead_tags([a.pk, b.pk], ["x"], [])
        service.bulk_update_lead_tags([a.pk], ["y"], [])
        assert api_client.get("/api/leads/?tags=x").json()["count"] == 2
        r = api_client.get("/api/leads/?tags=X&tags=y").json()
        assert r["count"] == 1 and r["results"][0]["id"] == a.pk
        assert api_client.get("/api/leads/?tags=x&tags=nope").json()["count"] == 0

    def test_source_prefix_and_since(self, api_client: Client) -> None:
        old = Lead.objects.create(name="Old", source="Brevo import")
        Lead.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=10), updated_at=timezone.now() - timedelta(days=10)
        )
        Lead.objects.create(name="New", source="Instagram")
        assert api_client.get("/api/leads/?source=brevo").json()["count"] == 1
        since = (timezone.now() - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        assert api_client.get("/api/leads/", {"updated_since": since}).json()["count"] == 1
        assert api_client.get("/api/leads/", {"created_since": since}).json()["count"] == 1

    def test_bulk_tags_endpoint(self, api_client: Client) -> None:
        a = Lead.objects.create(name="A")
        b = Lead.objects.create(name="B")
        service.bulk_update_lead_tags([a.pk, b.pk], ["old"], [])
        r = api_client.post(
            "/api/leads/tags",
            {"lead_ids": [a.pk, b.pk, 999], "add": ["Wave 1"], "remove": ["OLD"]},
            content_type="application/json",
        )
        assert r.status_code == 200 and r.json() == {"updated": 2}
        assert [tg.name for tg in a.tags.all()] == ["Wave 1"]
        assert Tag.objects.filter(name="old").exists()

    def test_bulk_tags_requires_auth(self, api_client: Client, settings: t.Any) -> None:
        settings.DEBUG = False
        settings.API_KEY = "secret"
        r = api_client.post("/api/leads/tags", {"lead_ids": []}, content_type="application/json")
        assert r.status_code == 401


# --- Admin ---


@pytest.fixture
def req(rep: User) -> HttpRequest:
    request = RequestFactory().get("/admin/")
    request.user = rep
    request.session = {}  # type: ignore[assignment]
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
    return request


def same_origin(request: HttpRequest) -> None:
    request.META["HTTP_SEC_FETCH_SITE"] = "same-origin"
    request.META["HTTP_REFERER"] = "http://testserver/admin/leads/outreachmessage/"


@pytest.fixture
def msg_admin() -> OutreachMessageAdmin:
    return OutreachMessageAdmin(OutreachMessage, AdminSite())


class TestOutreachAdmin:
    def test_registered_and_sidebar(self, settings: t.Any) -> None:
        assert OutreachMessage in django_admin.site._registry
        links = [i["title"] for g in settings.UNFOLD["SIDEBAR"]["navigation"] for i in g.get("items", [])]
        assert "Outreach Messages" in links

    def test_default_queue_is_my_drafts(self, req: HttpRequest, rep: User, lead: Lead) -> None:
        mine = msg(lead, "instagram", assigned_to=rep)
        msg(lead, "instagram")  # unassigned
        msg(lead, "instagram", assigned_to=User.objects.create_user("o", email="o@x.io"))
        sent = msg(lead, "instagram", assigned_to=rep)
        service.mark_message_sent(sent, rep)

        def ids(queue: str | None) -> set[int]:
            params: dict[str, t.Any] = {"queue": [queue]} if queue else {}
            f = OutreachQueueFilter(req, params, OutreachMessage, OutreachMessageAdmin(OutreachMessage, AdminSite()))
            return set(f.queryset(req, OutreachMessage.objects.all()).values_list("pk", flat=True))

        assert len(ids(None)) == 2  # mine + unassigned; not the other user's, not the sent one
        assert mine.pk in ids(None)
        assert len(ids("drafts")) == 3
        assert len(ids("all")) == 4

    def test_queue_filter_choices(self, req: HttpRequest, msg_admin: OutreachMessageAdmin) -> None:
        f = OutreachQueueFilter(req, {}, OutreachMessage, msg_admin)
        cl = MagicMock()
        cl.get_query_string.return_value = "?"
        displays = [c["display"] for c in f.choices(cl)]
        assert displays == ["Mine + unassigned", "All drafts", "Everything"]
        assert [c["selected"] for c in f.choices(cl)] == [True, False, False]

    def test_open_dm_and_copy_html(self, msg_admin: OutreachMessageAdmin, social_lead: Lead) -> None:
        m = msg(social_lead, "instagram", 'Hi <b>"x"</b> & more')
        link = msg_admin.open_dm(m)
        assert 'href="https://ig.me/m/some.org"' in link
        assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link and "Open DM" in link
        cell = msg_admin.text_with_copy(m)
        assert "js-copy-text" in cell and "<b>" not in cell and "&lt;b&gt;" in cell
        assert msg_admin.open_dm(msg(Lead.objects.create(name="Bare"), "instagram")) == "-"

    def test_humanized_badges(self, msg_admin: OutreachMessageAdmin, lead: Lead) -> None:
        assert "not humanized" in msg_admin.display_humanized(msg(lead, "instagram"))
        assert "edited after humanizing" in msg_admin.display_humanized(msg(lead, "instagram", humanized_hash="x"))
        assert "&#10003;" in msg_admin.display_humanized(msg(lead, "instagram", "t", humanized_hash=sha("t")))
        da = EmailDraftAdmin(EmailDraft, AdminSite())
        assert "edited after humanizing" in da.display_humanized(
            EmailDraft(lead=lead, subject="a", body="b", humanized_hash="x")
        )

    def test_bulk_mark_sent_and_skip(
        self, msg_admin: OutreachMessageAdmin, req: HttpRequest, rep: User, lead: Lead
    ) -> None:
        a, b = msg(lead, "instagram"), msg(lead, "facebook")
        msg_admin.mark_sent_bulk(req, OutreachMessage.objects.filter(pk=a.pk))
        msg_admin.skip_bulk(req, OutreachMessage.objects.filter(pk=b.pk))
        a.refresh_from_db()
        b.refresh_from_db()
        assert a.status == "sent" and a.sent_by == rep and b.status == "skipped"
        # re-running over sent messages is a no-op
        msg_admin.skip_bulk(req, OutreachMessage.objects.filter(pk=a.pk))
        a.refresh_from_db()
        assert a.status == "sent"

    def test_row_and_detail_actions(
        self, msg_admin: OutreachMessageAdmin, req: HttpRequest, rep: User, lead: Lead
    ) -> None:
        a, b, c = msg(lead, "instagram"), msg(lead, "facebook"), msg(lead, "whatsapp")
        same_origin(req)
        msg_admin.mark_sent_row(req, a.pk)
        msg_admin.skip_row(req, b.pk)
        msg_admin.mark_sent_detail(req, c)
        for m in (a, b, c):
            m.refresh_from_db()
        assert (a.status, b.status, c.status) == ("sent", "skipped", "sent")
        msg_admin.skip_detail(req, c)  # sent -> error message, unchanged
        c.refresh_from_db()
        assert c.status == "sent"

    def test_row_action_rejects_cross_site(self, msg_admin: OutreachMessageAdmin, req: HttpRequest, lead: Lead) -> None:
        m = msg(lead, "instagram")
        req.META["HTTP_SEC_FETCH_SITE"] = "cross-site"
        with pytest.raises(PermissionDenied):
            msg_admin.mark_sent_row(req, m.pk)
        m.refresh_from_db()
        assert m.status == "draft"

    def test_lead_inline_and_media(self, msg_admin: OutreachMessageAdmin) -> None:
        from leads.admin import OutreachMessageInline

        assert OutreachMessageInline in LeadAdmin.inlines
        assert "leads/js/outreach_copy.js" in msg_admin.media._js

    def test_lead_admin_dnc_badge_and_actions(self, req: HttpRequest, lead: Lead) -> None:
        la = LeadAdmin(Lead, AdminSite())
        assert "DO NOT CONTACT" not in la.display_name_with_notes(lead)
        la.mark_do_not_contact_bulk(req, Lead.objects.filter(pk=lead.pk))
        lead.refresh_from_db()
        assert lead.do_not_contact
        assert "DO NOT CONTACT" in la.display_name_with_notes(lead)
        assert "do_not_contact" in la.list_filter

    def test_lead_admin_dnc_single_action_with_reason(self, req: HttpRequest, lead: Lead) -> None:
        la = LeadAdmin(Lead, AdminSite())
        post = RequestFactory().post("/x/", {"do_not_contact_reason": "bounced"})
        post.user = req.user
        post.session = {}  # type: ignore[assignment]
        post._messages = FallbackStorage(post)  # type: ignore[attr-defined]
        la.mark_do_not_contact(post, lead)
        lead.refresh_from_db()
        assert lead.do_not_contact and lead.do_not_contact_reason == "bounced"

    def test_lead_admin_status_actions(self, req: HttpRequest, lead: Lead) -> None:
        la = LeadAdmin(Lead, AdminSite())
        for name, status in [
            ("set_status_replied", "replied"),
            ("set_status_call_booked", "call_booked"),
            ("set_status_org_created", "org_created"),
            ("set_status_first_event_live", "first_event_live"),
        ]:
            getattr(la, name)(req, Lead.objects.filter(pk=lead.pk))
            lead.refresh_from_db()
            assert lead.status == status
            assert "background" in la.display_status(lead)


def test_dashboard_chart_lists_all_statuses() -> None:
    import json

    from crm.dashboard import _get_chart_data

    data = _get_chart_data()
    assert len(json.loads(data["status_labels"])) == len(Lead.Status) == 9
    assert "Call booked" in json.loads(data["status_labels"])
