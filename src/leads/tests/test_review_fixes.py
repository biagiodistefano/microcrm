"""Regression tests for review findings on the outreach branch."""

import typing as t
from unittest.mock import patch

import pytest
from django.contrib import admin as django_admin
from django.contrib.auth.models import User
from django.test import Client, RequestFactory
from django.urls import reverse
from ninja.errors import HttpError

from leads import models, service
from leads.models import EmailDraft, Lead, OutreachMessage
from leads.schema import CityIn, LeadUpsertIn

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def plain_static(settings: t.Any) -> None:
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def make_instances() -> dict[type, t.Any]:
    """One instance per leads model that has an admin. A new model without a factory fails the smoke test."""
    city = models.City.objects.create(name="Berlin", country="Germany", iso2="DE")
    lead = Lead.objects.create(name="L", instagram="l", external_ref="x")
    user = User.objects.create_user("u", email="u@x.io")
    tpl = models.EmailTemplate.objects.create(name="T", subject="s", body="b")
    return {
        models.City: city,
        models.LeadType: models.LeadType.objects.get_or_create(name="Venue")[0],
        models.Tag: models.Tag.objects.create(name="tg"),
        models.Contact: models.Contact.objects.create(lead=lead, name="C"),
        Lead: lead,
        models.Action: models.Action.objects.create(lead=lead, name="A"),
        models.EmailTemplate: tpl,
        models.EmailSent: models.EmailSent.objects.create(
            lead=lead, from_email="a@b.c", to=["x@y.z"], subject="s", body="b"
        ),
        EmailDraft: EmailDraft.objects.create(lead=lead, subject="s", body="b", assigned_to=user),
        models.GmailConnection: models.GmailConnection.objects.create(user=user, email="u@x.io", refresh_token="t"),
        models.EmailSignature: models.EmailSignature.objects.create(user=user, body="sig"),
        models.UserApiToken: models.UserApiToken.issue(user, "t")[0],
        models.ResearchPromptConfig: models.ResearchPromptConfig.get_solo(),
        models.ResearchJob: models.ResearchJob.objects.create(city=city),
        OutreachMessage: OutreachMessage.objects.create(lead=lead, channel="instagram", text="hi", assigned_to=user),
    }


def test_every_registered_admin_pages_render(admin_client: Client) -> None:
    instances = make_instances()
    req = RequestFactory().get("/")
    req.user = User.objects.get(is_superuser=True)
    leads_models = [m for m in django_admin.site._registry if m._meta.app_label == "leads"]
    assert set(leads_models) <= set(instances), f"add a factory for {set(leads_models) - set(instances)}"
    for model in leads_models:
        info = (model._meta.app_label, model._meta.model_name)
        madmin = django_admin.site._registry[model]
        r = admin_client.get(reverse("admin:%s_%s_changelist" % info))
        assert r.status_code == 200, f"{model.__name__} changelist -> {r.status_code}"
        r = admin_client.get(reverse("admin:%s_%s_change" % info, args=[instances[model].pk]))
        assert r.status_code == 200, f"{model.__name__} change -> {r.status_code}"
        if madmin.has_add_permission(req):
            r = admin_client.get(reverse("admin:%s_%s_add" % info))
            assert r.status_code == 200, f"{model.__name__} add -> {r.status_code}"


# --- item 2: do-not-contact applies to existing outreach and every send path ---


@pytest.fixture
def rep() -> User:
    return User.objects.create_user("weronika", email="weronika@letsrevel.io", password="x")


@pytest.fixture
def lead() -> Lead:
    return Lead.objects.create(name="Org", email="o@x.org", instagram="org")


def test_suppression_skips_drafts_and_deletes_email_drafts(lead: Lead) -> None:
    draft_msg = OutreachMessage.objects.create(lead=lead, channel="instagram", text="a")
    sent_msg = OutreachMessage.objects.create(lead=lead, channel="facebook", text="b", status="sent")
    EmailDraft.objects.create(lead=lead, subject="s", body="b")
    service.mark_do_not_contact(lead, "stop")
    draft_msg.refresh_from_db()
    sent_msg.refresh_from_db()
    assert draft_msg.status == "skipped" and sent_msg.status == "sent"
    assert not lead.email_drafts.exists()


@pytest.mark.parametrize("via", ["patch", "upsert_merge", "upsert_create_no_outreach", "admin_save"])
def test_every_flip_path_suppresses(lead: Lead, api_client: Client, via: str, admin_user: User) -> None:
    from django.contrib.admin.sites import AdminSite

    from leads.admin import LeadAdmin

    msg = OutreachMessage.objects.create(lead=lead, channel="instagram", text="a")
    EmailDraft.objects.create(lead=lead, subject="s", body="b")
    if via == "patch":
        api_client.patch(f"/api/leads/{lead.pk}", {"do_not_contact": True}, content_type="application/json")
    elif via == "upsert_merge":
        api_client.post(
            "/api/leads/upsert",
            {"name": "Org", "email": "o@x.org", "do_not_contact": True},
            content_type="application/json",
        )
    elif via == "admin_save":
        lead.do_not_contact = True
        request = RequestFactory().post("/")
        request.user = admin_user
        ladmin = LeadAdmin(Lead, AdminSite())
        ladmin.save_model(request, lead, None, True)
        form = type("F", (), {"instance": lead})()
        with patch("django.contrib.admin.options.ModelAdmin.save_related"):
            ladmin.save_related(request, form, [], True)
        lead.refresh_from_db()
        assert lead.do_not_contact_at is not None
    else:
        r = api_client.post(
            "/api/leads/upsert", {"name": "New", "do_not_contact": True}, content_type="application/json"
        ).json()
        assert r["created"] is True
        return
    msg.refresh_from_db()
    assert msg.status == "skipped" and not lead.email_drafts.exists()


def test_all_send_paths_refuse_dnc(lead: Lead, rep: User, api_client: Client) -> None:
    msg = OutreachMessage.objects.create(lead=lead, channel="instagram", text="a")
    draft = EmailDraft.objects.create(lead=lead, subject="s", body="b", to=["o@x.org"])
    Lead.objects.filter(pk=lead.pk).update(do_not_contact=True)  # bypass suppression: guards must hold alone
    lead.refresh_from_db()
    with pytest.raises(HttpError):
        service.mark_message_sent(msg, rep)
    msg.refresh_from_db()
    assert msg.status == "draft"
    with pytest.raises(HttpError), patch("leads.gmail.send_email_via_gmail") as gmail:
        service.send_email_draft(draft, rep)
    gmail.assert_not_called()
    assert api_client.post(f"/api/email-drafts/{draft.pk}/send").status_code == 403  # shared key cannot send
    assert api_client.post(f"/api/messages/{msg.pk}/mark-sent").status_code == 400
    assert not models.EmailSent.objects.exists()


def test_admin_send_selected_drafts_refuses_dnc(lead: Lead, rep: User) -> None:
    from django.contrib.admin.sites import AdminSite
    from django.contrib.messages.storage.fallback import FallbackStorage
    from django.test import RequestFactory

    from leads.admin import EmailDraftAdmin

    draft = EmailDraft.objects.create(lead=lead, subject="s", body="b", to=["o@x.org"])
    Lead.objects.filter(pk=lead.pk).update(do_not_contact=True)
    request = RequestFactory().get("/")
    request.user = rep
    request.session = {}  # type: ignore[assignment]
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
    EmailDraftAdmin(EmailDraft, AdminSite()).send_selected_drafts(request, EmailDraft.objects.filter(pk=draft.pk))
    assert EmailDraft.objects.filter(pk=draft.pk).exists() and not models.EmailSent.objects.exists()


def test_queue_excludes_dnc_leads(lead: Lead, rep: User) -> None:
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from leads.admin import OutreachMessageAdmin, OutreachQueueFilter

    OutreachMessage.objects.create(lead=lead, channel="instagram", text="a", assigned_to=rep)
    Lead.objects.filter(pk=lead.pk).update(do_not_contact=True)
    request = RequestFactory().get("/")
    request.user = rep
    for params in ({}, {"queue": ["drafts"]}):
        f = OutreachQueueFilter(
            request, t.cast(t.Any, dict(params)), OutreachMessage, OutreachMessageAdmin(OutreachMessage, AdminSite())
        )
        assert not f.queryset(request, OutreachMessage.objects.all()).exists()
    f = OutreachQueueFilter(
        request, t.cast(t.Any, {"queue": ["all"]}), OutreachMessage, OutreachMessageAdmin(OutreachMessage, AdminSite())
    )
    assert f.queryset(request, OutreachMessage.objects.all()).exists()


# --- item 3: strict row action origin check ---


@pytest.mark.parametrize(
    ("headers", "allowed"),
    [
        ({"HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_REFERER": "http://testserver/admin/"}, True),
        ({"HTTP_SEC_FETCH_SITE": "same-site", "HTTP_REFERER": "http://testserver/admin/"}, False),
        ({"HTTP_SEC_FETCH_SITE": "cross-site", "HTTP_REFERER": "http://testserver/admin/"}, False),
        ({"HTTP_SEC_FETCH_SITE": "same-origin"}, False),
        ({"HTTP_SEC_FETCH_SITE": "same-origin", "HTTP_REFERER": "http://evil.example/x"}, False),
        ({"HTTP_REFERER": "http://testserver/admin/"}, False),
        ({}, False),
    ],
)
def test_row_action_origin(admin_client: Client, lead: Lead, headers: dict[str, str], allowed: bool) -> None:
    m = OutreachMessage.objects.create(lead=lead, channel="instagram", text="hi")
    url = reverse("admin:leads_outreachmessage_mark_sent_row", args=[m.pk])
    r = admin_client.get(url, headers=None, **t.cast(dict[str, t.Any], headers))
    m.refresh_from_db()
    assert (m.status == "sent") is allowed
    assert r.status_code == (302 if allowed else 403)


# --- items 4 & 5: matching ---


def up(**kw: t.Any) -> tuple[Lead, bool, str | None]:
    kw.setdefault("name", "Org")
    return service.upsert_lead(LeadUpsertIn(**kw))


def test_strong_keys_win_regardless_of_ref() -> None:
    a, _, _ = up(external_ref="a", email="shared@x.org", phone="1", instagram="ig", bluesky="b", name="A")
    for key in ({"email": "shared@x.org"}, {"phone": "1"}, {"instagram": "IG"}, {"bluesky": "b"}):
        lead, created, matched_on = up(external_ref="other-" + next(iter(key)), name="Z", **key)
        assert created is False and lead.pk == a.pk and matched_on == next(iter(key))
        lead.refresh_from_db()
        assert lead.external_ref == "a"  # existing ref never overwritten


def test_weak_keys_vetoed_by_different_ref() -> None:
    a, _, _ = up(
        external_ref="a", website="https://clubx.de", name="Club", city=CityIn(name="B", country="G", iso2="DE")
    )
    w, created, _ = up(external_ref="b", website="clubx.de", name="Other")
    assert created is True and w.pk != a.pk
    n, created, _ = up(external_ref="c", name="club", city=CityIn(name="B", country="G", iso2="DE"))
    assert created is True and n.pk != a.pk


def test_cross_source_ref_does_not_bypass_do_not_contact() -> None:
    a, _, _ = up(name="Club", instagram="clubx", external_ref="salesops:ig:clubx")
    service.mark_do_not_contact(a, "not-a-fit")
    b, created, on = up(
        name="Club X", instagram="clubx", email="hi@clubx.de", external_ref="salesops:bsky:clubx.bsky.social"
    )
    assert (b.pk, created, on, b.do_not_contact) == (a.pk, False, "instagram", True)
    assert Lead.objects.count() == 1
    b.refresh_from_db()
    assert b.email == ""  # suppressed lead is left untouched


def test_do_not_contact_candidate_preferred_among_strong_matches() -> None:
    plain = Lead.objects.create(name="Plain", email="dup@x.org")
    sup = service.mark_do_not_contact(Lead.objects.create(name="Sup", email="dup@x.org"))
    lead, created, _ = up(name="Z", email="dup@x.org")
    assert created is False and lead.pk == sup.pk != plain.pk


def test_matched_on_values() -> None:
    assert up(external_ref="r", email="e@x.org", name="A")[2] is None
    assert up(external_ref="R ", name="Other")[2] == "external_ref"
    assert up(email="E@x.org", name="Other")[2] == "email"
    assert up(bluesky="x", name="B")[2] is None
    assert up(bluesky="X", name="C")[2] == "bluesky"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("https://www.Example.com/", "example.com"),
        ("http://example.com", "https://EXAMPLE.com/"),
        ("example.com/club/", "https://www.example.com/club?utm=1"),
    ],
)
def test_website_normalised_matching(a: str, b: str) -> None:
    first, _, _ = up(name="First", website=a)
    second, created, matched_on = up(name="Second", website=b)
    assert created is False and second.pk == first.pk and matched_on == "website"


@pytest.mark.parametrize(
    "url",
    [
        "https://linktr.ee/a", "https://www.instagram.com/a", "https://facebook.com/a", "https://fb.com/a",
        "https://www.eventbrite.com/o/a", "https://eventbrite.de/o/a", "https://ra.co/promoters/1",
        "https://bsky.app/profile/a", "https://linkedin.com/company/a", "https://tiktok.com/@a",
        "https://beacons.ai/a", "https://lnk.bio/a", "https://a.carrd.co", "https://google.com/maps/place/x",
        "https://t.me/a", "https://wa.me/49",
    ],
)  # fmt: skip
def test_aggregator_websites_never_match(url: str) -> None:
    first, _, _ = up(name="Org A", website=url)
    second, created, _ = up(name="Org B", website=url)
    assert created is True and second.pk != first.pk


def test_external_ref_normalised_everywhere(api_client: Client) -> None:
    r = api_client.post("/api/leads/", {"name": "A", "external_ref": "  ABC "}, content_type="application/json")
    assert r.json()["external_ref"] == "abc"
    lead = Lead.objects.create(name="B", external_ref=" XyZ ")  # model save normalises (admin path)
    assert lead.external_ref == "xyz"
    _, created, matched_on = up(name="C", external_ref="ABC")
    assert created is False and matched_on == "external_ref"
    r = api_client.patch(f"/api/leads/{lead.pk}", {"external_ref": " Q "}, content_type="application/json")
    assert r.json()["external_ref"] == "q"
    assert api_client.get("/api/leads/?external_ref=Q").json()["count"] == 1


def test_upsert_response_shape(api_client: Client) -> None:
    created = api_client.post("/api/leads/upsert", {"name": "A", "email": "a@x.org"}, content_type="application/json")
    assert created.json() == {"id": created.json()["id"], "created": True, "do_not_contact": False, "matched_on": None}
    again = api_client.post("/api/leads/upsert", {"name": "A", "email": "a@x.org"}, content_type="application/json")
    assert again.json()["matched_on"] == "email" and again.json()["created"] is False


# --- item 6: idempotent under stale objects ---


def test_stale_second_mark_sent_creates_one_action(lead: Lead) -> None:
    m = OutreachMessage.objects.create(lead=lead, channel="instagram", text="a")
    stale = OutreachMessage.objects.get(pk=m.pk)
    service.mark_message_sent(m, None)
    service.mark_message_sent(stale, None)
    assert models.Action.objects.filter(lead=lead).count() == 1


# --- item 7: unassigned drafts visible ---


def test_my_drafts_includes_unassigned(lead: Lead, rep: User) -> None:
    from django.contrib.admin.sites import AdminSite
    from django.test import RequestFactory

    from leads.admin import OutreachMessageAdmin, OutreachQueueFilter

    other = User.objects.create_user("o", email="o@x.io")
    OutreachMessage.objects.create(lead=lead, channel="instagram", text="mine", assigned_to=rep)
    OutreachMessage.objects.create(lead=lead, channel="instagram", text="nobody")
    OutreachMessage.objects.create(lead=lead, channel="instagram", text="theirs", assigned_to=other)
    request = RequestFactory().get("/")
    request.user = rep
    f = OutreachQueueFilter(request, {}, OutreachMessage, OutreachMessageAdmin(OutreachMessage, AdminSite()))
    assert sorted(f.queryset(request, OutreachMessage.objects.all()).values_list("text", flat=True)) == [
        "mine",
        "nobody",
    ]


# --- item 11: assigned drafts ---


class TestAssignedDraftSending:
    @pytest.fixture
    def draft(self, lead: Lead, rep: User) -> EmailDraft:
        models.GmailConnection.objects.create(user=rep, email="weronika@letsrevel.io", refresh_token="t")
        return EmailDraft.objects.create(lead=lead, subject="s", body="b", to=["o@x.org"], assigned_to=rep)

    def test_other_user_refused(self, draft: EmailDraft) -> None:
        other = User.objects.create_user("o", email="o@x.io")
        models.GmailConnection.objects.create(user=other, email="o@x.io", refresh_token="t")
        with pytest.raises(HttpError) as exc, patch("leads.gmail.send_email_via_gmail") as gmail:
            service.send_email_draft(draft, other)
        assert exc.value.status_code == 403 and "assigned to weronika@letsrevel.io" in exc.value.message
        gmail.assert_not_called()
        assert EmailDraft.objects.filter(pk=draft.pk).exists()

    def test_api_without_actor_refused(self, api_client: Client, draft: EmailDraft) -> None:
        assert api_client.post(f"/api/email-drafts/{draft.pk}/send").status_code == 403

    def test_assignee_sends_from_own_gmail(self, draft: EmailDraft, rep: User) -> None:
        with (
            patch("leads.gmail.get_gmail_credentials"),
            patch("leads.gmail.send_email_via_gmail") as gmail,
        ):
            sent = service.send_email_draft(draft, rep)
        assert gmail.call_args.args[1] == "weronika@letsrevel.io" and sent.sent_by == rep

    def test_superuser_uses_own_gmail_never_assignees(self, draft: EmailDraft) -> None:
        boss = User.objects.create_superuser("boss", email="boss@x.io", password="x")
        models.GmailConnection.objects.create(user=boss, email="boss@x.io", refresh_token="t")
        with patch("leads.gmail.get_gmail_credentials") as creds, patch("leads.gmail.send_email_via_gmail") as gmail:
            service.send_email_draft(draft, boss)
        assert gmail.call_args.args[1] == "boss@x.io"
        assert creds.call_args.args[0].user == boss

    def test_unassigned_draft_unchanged(self, lead: Lead, rep: User) -> None:
        models.GmailConnection.objects.create(user=rep, email="weronika@letsrevel.io", refresh_token="t")
        d = EmailDraft.objects.create(lead=lead, subject="s", body="b", to=["o@x.org"])
        with patch("leads.gmail.get_gmail_credentials"), patch("leads.gmail.send_email_via_gmail"):
            assert service.send_email_draft(d, rep).status == "sent"


# --- N2: admin form external_ref case ---


def test_admin_form_external_ref_case_is_form_error() -> None:
    from django.contrib.admin.sites import AdminSite

    from leads.admin import LeadAdmin

    Lead.objects.create(name="First", external_ref="abc")
    form_cls = LeadAdmin(Lead, AdminSite()).get_form(RequestFactory().get("/"), fields=["name", "external_ref"])
    form = form_cls({"name": "Second", "external_ref": "ABC"})
    assert not form.is_valid()  # a form error, not an IntegrityError 500
    ok = form_cls({"name": "Third", "external_ref": "XyZ"})
    assert ok.is_valid()
    assert ok.save().external_ref == "xyz"


# --- N3: cascade after inlines ---


def test_inline_save_cannot_resurrect_draft(lead: Lead) -> None:
    from django.contrib.admin.sites import AdminSite

    from leads.admin import LeadAdmin

    msg = OutreachMessage.objects.create(lead=lead, channel="instagram", text="a")
    stale = OutreachMessage.objects.get(pk=msg.pk)  # inline formset instance loaded before the cascade
    ladmin = LeadAdmin(Lead, AdminSite())
    request = RequestFactory().post("/")
    request.user = User.objects.create_user("adm", email="adm@x.io")
    lead.do_not_contact = True
    ladmin.save_model(request, lead, None, True)

    def inline_save(*args: t.Any, **kwargs: t.Any) -> None:
        stale.text = "edited inline"
        stale.save()

    form = type("F", (), {"instance": lead})()
    with patch("django.contrib.admin.options.ModelAdmin.save_related", side_effect=inline_save):
        ladmin.save_related(request, form, [], True)
    msg.refresh_from_db()
    assert msg.text == "edited inline" and msg.status == "skipped"
