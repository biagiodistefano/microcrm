"""Tests for merging duplicate leads: service, admin flow, API and CLI."""

import importlib.util
import json
import typing as t
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from typer.testing import CliRunner

from leads import service
from leads.models import (
    Action,
    City,
    Contact,
    EmailDraft,
    EmailSent,
    Lead,
    LeadType,
    OutreachMessage,
    Tag,
)

pytestmark = pytest.mark.django_db

_spec = importlib.util.spec_from_file_location("microcrm_cli_merge", Path(__file__).resolve().parents[3] / "cli.py")
assert _spec and _spec.loader
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)
runner = CliRunner()


def make(name: str, age_days: int = 0, **kwargs: t.Any) -> Lead:
    """Create a lead whose created_at is ``age_days`` ago (bigger = older)."""
    lead = Lead.objects.create(name=name, **kwargs)
    Lead.objects.filter(pk=lead.pk).update(
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) - timedelta(days=age_days)
    )
    lead.refresh_from_db()
    return lead


@pytest.fixture
def user() -> User:
    return User.objects.create_user("rep", "rep@example.com", "pw")


class TestFieldRules:
    def test_fills_blank_from_oldest_other_first_and_never_overwrites(self) -> None:
        primary = make("P", email="p@x.org", phone="")
        young = make("Y", age_days=1, phone="111", website="https://y.example", email="y@x.org")
        old = make("O", age_days=9, phone="999", website="https://o.example", company="OldCo")
        merged = service.merge_leads(primary, [young, old])
        assert merged.email == "p@x.org"
        assert merged.phone == "999"  # oldest other wins
        assert merged.website == "https://o.example"
        assert merged.company == "OldCo"

    def test_fills_city_and_type(self, city: City, lead_type: LeadType) -> None:
        primary = make("P")
        other = make("O", city=city, lead_type=lead_type)
        merged = service.merge_leads(primary, [other])
        assert merged.city == city and merged.lead_type == lead_type

    def test_notes_appended_once(self) -> None:
        primary = make("P", notes="mine")
        other = make("O", notes="theirs")
        empty = make("E")
        merged = service.merge_leads(primary, [other, empty])
        assert merged.notes == f"mine\n\nMerged from #{other.pk} (O): theirs"
        assert "#" + str(empty.pk) not in merged.notes
        assert service._merge_notes(merged, [other]) == merged.notes  # idempotent

    def test_notes_when_primary_blank(self) -> None:
        primary = make("P")
        other = make("O", notes="x")
        assert service.merge_leads(primary, [other]).notes == f"Merged from #{other.pk} (O): x"

    def test_tags_platforms_value_last_contact_temperature(self) -> None:
        primary = make("P", value=Decimal("100"), last_contact=date(2026, 1, 5), ticketing_platforms=["ra"])
        other = make(
            "O",
            value=Decimal("500"),
            last_contact=date(2026, 3, 1),
            ticketing_platforms=["ra", "eventbrite"],
            temperature=Lead.Temperature.HOT,
        )
        primary.tags.add(Tag.objects.create(name="a"))
        other.tags.add(Tag.objects.get(name="a"), Tag.objects.create(name="b"))
        merged = service.merge_leads(primary, [other])
        assert merged.value == Decimal("500.00")
        assert merged.last_contact == date(2026, 3, 1)
        assert merged.ticketing_platforms == ["ra", "eventbrite"]
        assert merged.temperature == "hot"
        assert set(merged.tags.values_list("name", flat=True)) == {"a", "b"}

    def test_value_none_when_nobody_has_one(self) -> None:
        merged = service.merge_leads(make("P"), [make("O")])
        assert merged.value is None and merged.last_contact is None

    def test_external_ref_primary_kept(self) -> None:
        primary = make("P", external_ref="aaa")
        other = make("O", external_ref="bbb")
        assert service.merge_leads(primary, [other]).external_ref == "aaa"

    def test_external_ref_adopted_from_first_other(self) -> None:
        primary = make("P")
        first = make("F", age_days=5, external_ref="old-ref")
        second = make("S", age_days=1, external_ref="new-ref")
        merged = service.merge_leads(primary, [second, first])
        assert merged.external_ref == "old-ref"
        assert Lead.objects.filter(external_ref="old-ref").count() == 1


class TestStatus:
    @pytest.mark.parametrize(
        ("primary", "other", "expected"),
        [
            ("new", "contacted", "contacted"),
            ("qualified", "replied", "qualified"),
            ("contacted", "converted", "converted"),
            ("call_booked", "org_created", "org_created"),
            ("org_created", "first_event_live", "first_event_live"),
            ("lost", "new", "lost"),  # primary lost, nobody beyond contacted: keep primary's
            ("new", "lost", "lost"),  # primary new -> lost
            ("contacted", "lost", "contacted"),  # primary not new -> keep
            ("lost", "contacted", "lost"),
            ("lost", "replied", "replied"),  # someone beyond contacted: lost ignored
            ("qualified", "lost", "qualified"),
        ],
    )
    def test_precedence(self, primary: str, other: str, expected: str) -> None:
        merged = service.merge_leads(make("P", status=primary), [make("O", status=other)])
        assert merged.status == expected

    def test_default_primary_most_advanced_then_oldest(self) -> None:
        a = make("A", age_days=1, status="contacted")
        b = make("B", age_days=9, status="contacted")
        c = make("C", age_days=20, status="new")
        d = make("D", age_days=30, status="lost")
        assert service.pick_default_primary([a, b, c, d]) == b
        assert service.pick_default_primary([c, d]) == c  # lost ranks below new
        assert service.pick_default_primary([make("E", status="qualified"), a]).name == "E"


class TestDoNotContact:
    def test_any_dnc_propagates_with_earliest_stamp_and_combined_reasons(self) -> None:
        primary = make("P", do_not_contact=True, do_not_contact_reason="asked to stop")
        Lead.objects.filter(pk=primary.pk).update(do_not_contact_at=datetime(2026, 5, 1, tzinfo=timezone.utc))
        other = make("O", do_not_contact=True, do_not_contact_reason="bounced")
        Lead.objects.filter(pk=other.pk).update(do_not_contact_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        primary.refresh_from_db()
        other.refresh_from_db()
        merged = service.merge_leads(primary, [other])
        assert merged.do_not_contact
        assert merged.do_not_contact_reason == "asked to stop; bounced"
        assert merged.do_not_contact_at == datetime(2026, 2, 1, tzinfo=timezone.utc)

    def test_dnc_on_other_only_cascades_to_moved_outreach(self) -> None:
        primary = make("P")
        other = make("O", do_not_contact=True, do_not_contact_reason="no")
        Lead.objects.filter(pk=other.pk).update(do_not_contact_at=datetime(2026, 2, 1, tzinfo=timezone.utc))
        other.refresh_from_db()
        msg = OutreachMessage.objects.create(lead=primary, channel="instagram", text="hi")
        draft = EmailDraft.objects.create(lead=primary, subject="s", body="b", to=["a@b.co"])
        moved_msg = OutreachMessage.objects.create(lead=other, channel="instagram", text="yo", status="sent")
        merged = service.merge_leads(primary, [other])
        assert merged.do_not_contact and merged.do_not_contact_at is not None
        msg.refresh_from_db()
        assert msg.status == OutreachMessage.Status.SKIPPED
        assert not EmailDraft.objects.filter(pk=draft.pk).exists()
        moved_msg.refresh_from_db()
        assert moved_msg.lead == merged and moved_msg.status == "sent"

    def test_dnc_stamp_set_when_missing(self) -> None:
        other = make("O", do_not_contact=True)
        Lead.objects.filter(pk=other.pk).update(do_not_contact_at=None)
        other.refresh_from_db()
        merged = service.merge_leads(make("P"), [other])
        assert merged.do_not_contact_at is not None

    def test_no_dnc_stays_clean(self) -> None:
        assert not service.merge_leads(make("P"), [make("O")]).do_not_contact


class TestMovesAndFks:
    def test_every_lead_fk_is_covered(self) -> None:
        """If a new model gets a FK to Lead, the merge must learn about it (add it to _move_related / contacts)."""
        covered = {Contact, Action, OutreachMessage, EmailDraft, EmailSent}
        related = {
            rel.related_model
            for rel in Lead._meta.related_objects
            if not rel.many_to_many and not rel.name.startswith("history")
        }
        assert related == covered

    def test_all_related_rows_move_and_others_deleted(self, user: User) -> None:
        primary = make("P")
        other = make("O")
        contact = Contact.objects.create(lead=other, name="Booker", email="b@x.org")
        action = Action.objects.create(lead=other, name="call")
        message = OutreachMessage.objects.create(lead=other, channel="instagram", text="hi")
        draft = EmailDraft.objects.create(lead=other, subject="s", body="b", to=["b@x.org"], contact=contact)
        sent = EmailSent.objects.create(
            lead=other, from_email="a@b.co", to=["b@x.org"], subject="s", body="b", contact=contact
        )
        merged = service.merge_leads(primary, [other], user)
        for obj in (contact, action, message, draft, sent):
            obj.refresh_from_db()
            assert obj.lead == merged
        assert not Lead.objects.filter(pk=other.pk).exists()

    def test_moved_rows_keep_history(self) -> None:
        primary = make("P")
        other = make("O")
        action = Action.objects.create(lead=other, name="call")
        before = action.updated_at
        service.merge_leads(primary, [other])
        action.refresh_from_db()
        assert action.updated_at == before
        assert action.history.first().lead_id == primary.pk

    def test_history_records_merge_reason(self, user: User) -> None:
        primary = make("P")
        a, b = make("A"), make("B")
        merged = service.merge_leads(primary, [a, b], user)
        record = merged.history.first()
        assert record is not None
        assert record.history_change_reason == f"merged #{a.pk}, #{b.pk} by rep"

    def test_change_reason_truncated(self) -> None:
        others = [make(f"L{i}") for i in range(19)]
        reason = service._change_reason(others, User(username="x" * 40))
        assert len(reason) <= 100 and reason.endswith("...")

    def test_contact_dedup_by_email_and_instagram(self) -> None:
        primary = make("P")
        keep = Contact.objects.create(lead=primary, name="Primary", email="Same@X.org", is_primary=True)
        other = make("O")
        dup_email = Contact.objects.create(lead=other, name="Dup", email="same@x.org", phone="123", is_primary=True)
        dup_ig = Contact.objects.create(lead=other, name="IG", instagram="@Club")
        Contact.objects.create(lead=primary, name="IG existing", instagram="club")
        fresh = Contact.objects.create(lead=other, name="Fresh", email="new@x.org")
        draft = EmailDraft.objects.create(lead=other, subject="s", body="b", to=["x@y.co"], contact=dup_email)
        sent = EmailSent.objects.create(
            lead=other, contact=dup_email, from_email="a@b.co", to=["x@y.co"], subject="s", body="b"
        )
        merged = service.merge_leads(primary, [other])
        names = sorted(merged.contacts.values_list("name", flat=True))
        assert names == ["Fresh", "IG existing", "Primary"]
        keep.refresh_from_db()
        assert keep.phone == "123"  # blank filled from the duplicate
        draft.refresh_from_db()
        sent.refresh_from_db()
        assert draft.contact == keep and sent.contact == keep
        assert not Contact.objects.filter(pk__in=[dup_email.pk, dup_ig.pk]).exists()
        fresh.refresh_from_db()
        assert fresh.lead == merged and not fresh.is_primary
        assert merged.contacts.filter(is_primary=True).count() == 1

    def test_primary_contact_adopted_when_primary_has_none(self) -> None:
        primary = make("P")
        other = make("O")
        Contact.objects.create(lead=other, name="Main", email="m@x.org", is_primary=True)
        Contact.objects.create(lead=other, name="Second", email="s@x.org")
        merged = service.merge_leads(primary, [other])
        assert merged.contacts.get(is_primary=True).name == "Main"

    def test_two_others_with_same_contact_collapse(self) -> None:
        primary = make("P")
        a, b = make("A", age_days=2), make("B", age_days=1)
        Contact.objects.create(lead=a, name="x", email="dup@x.org")
        Contact.objects.create(lead=b, name="y", email="DUP@x.org")
        assert service.merge_leads(primary, [a, b]).contacts.count() == 1


class TestTagCleanup:
    def _tag(self, name: str, *leads: Lead) -> Tag:
        tag = Tag.objects.create(name=name)
        for lead in leads:
            lead.tags.add(tag)
        return tag

    def test_group_tags_removed_when_group_fully_merged(self) -> None:
        primary, other = make("P"), make("O")
        self._tag("review:duplicate-1", primary, other)
        self._tag("review:duplicates", primary, other)
        self._tag("Keep me", other)
        stray = make("Stray")  # another cluster still carries review:duplicates
        stray.tags.add(Tag.objects.get(name="review:duplicates"))
        merged = service.merge_leads(primary, [other])
        assert set(merged.tags.values_list("name", flat=True)) == {"Keep me"}

    def test_group_tag_kept_while_another_lead_carries_it(self) -> None:
        primary, other, third = make("P"), make("O"), make("T")
        self._tag("review:duplicate-1", primary, other, third)
        self._tag("review:duplicates", primary, other, third)
        merged = service.merge_leads(primary, [other])
        assert set(merged.tags.values_list("name", flat=True)) == {"review:duplicate-1", "review:duplicates"}

    def test_generic_tag_kept_while_in_another_group(self) -> None:
        primary, other, third = make("P"), make("O"), make("T")
        self._tag("review:duplicate-1", primary, other)
        self._tag("review:duplicate-2", primary, third)
        self._tag("review:duplicates", primary, other, third)
        merged = service.merge_leads(primary, [other])
        assert set(merged.tags.values_list("name", flat=True)) == {"review:duplicate-2", "review:duplicates"}


class TestRefusals:
    def test_into_itself(self) -> None:
        lead = make("P")
        with pytest.raises(service.MergeError, match="itself"):
            service.merge_leads(lead, [lead])

    def test_empty_others(self) -> None:
        with pytest.raises(service.MergeError, match="at least one"):
            service.merge_leads(make("P"), [])

    def test_too_many(self) -> None:
        primary = make("P")
        others = [make(f"L{i}") for i in range(20)]
        with pytest.raises(service.MergeError, match="at most 20"):
            service.merge_leads(primary, others)
        assert Lead.objects.count() == 21

    def test_exactly_twenty_ok(self) -> None:
        primary = make("P")
        others = [make(f"L{i}") for i in range(19)]
        service.merge_leads(primary, others)
        assert Lead.objects.count() == 1

    def test_vanished_lead_changes_nothing(self) -> None:
        primary, other = make("P", notes="n"), make("O")
        ghost = make("G")
        Lead.objects.filter(pk=ghost.pk).delete()
        with pytest.raises(service.MergeError, match="no longer exist"):
            service.merge_leads(primary, [other, ghost])
        assert Lead.objects.filter(pk=other.pk).exists()

    def test_duplicate_other_ids_are_deduped(self) -> None:
        primary, other = make("P"), make("O", notes="n")
        merged = service.merge_leads(primary, [other, other])
        assert merged.notes.count("Merged from") == 1


class TestAdminFlow:
    @pytest.fixture
    def trio(self) -> list[Lead]:
        return [
            make("Old Club", age_days=10, status="contacted", email="old@x.org"),
            make("New Club", age_days=1, status="qualified", instagram="newclub"),
            make("Third", age_days=2),
        ]

    def test_action_redirects_to_confirm_page(self, admin_client: Client, trio: list[Lead]) -> None:
        r = admin_client.post(
            reverse("admin:leads_lead_changelist"),
            {"action": "merge_selected_leads", "_selected_action": [lead.pk for lead in trio]},
        )
        assert r.status_code == 302
        assert r["Location"].startswith(reverse("admin:leads_lead_merge"))
        page = admin_client.get(r["Location"])
        assert page.status_code == 200
        html = page.content.decode()
        for lead in trio:
            assert lead.name in html
        assert 'name="primary"' in html and "csrfmiddlewaretoken" in html and "Confirm merge" in html
        assert page.context["primary_id"] == trio[1].pk  # most advanced status

    def test_confirm_page_warns_about_dnc(self, admin_client: Client, trio: list[Lead]) -> None:
        Lead.objects.filter(pk=trio[2].pk).update(do_not_contact=True)
        ids = ",".join(str(lead.pk) for lead in trio)
        html = admin_client.get(reverse("admin:leads_lead_merge"), {"ids": ids}).content.decode()
        assert "do not contact" in html

    def test_post_merges_into_chosen_primary(self, admin_client: Client, trio: list[Lead]) -> None:
        ids = ",".join(str(lead.pk) for lead in trio)
        r = admin_client.post(reverse("admin:leads_lead_merge"), {"ids": ids, "primary": trio[0].pk})
        assert r.status_code == 302
        assert r["Location"] == reverse("admin:leads_lead_change", args=[trio[0].pk])
        assert list(Lead.objects.values_list("pk", flat=True)) == [trio[0].pk]
        merged = Lead.objects.get()
        assert merged.status == "qualified" and merged.instagram == "newclub"
        assert merged.history.first().history_change_reason.endswith("by admin")

    def test_post_without_choice_rerenders(self, admin_client: Client, trio: list[Lead]) -> None:
        ids = ",".join(str(lead.pk) for lead in trio)
        r = admin_client.post(reverse("admin:leads_lead_merge"), {"ids": ids})
        assert r.status_code == 400
        assert "choose which lead to keep" in r.content.decode()
        assert Lead.objects.count() == 3

    def test_needs_two_leads(self, admin_client: Client, trio: list[Lead]) -> None:
        r = admin_client.get(reverse("admin:leads_lead_merge"), {"ids": str(trio[0].pk)})
        assert r.status_code == 302 and r["Location"] == reverse("admin:leads_lead_changelist")
        r = admin_client.get(reverse("admin:leads_lead_merge"), {"ids": "abc"})
        assert r.status_code == 302

    def test_too_many_redirects(self, admin_client: Client) -> None:
        leads = [make(f"L{i}") for i in range(21)]
        r = admin_client.get(reverse("admin:leads_lead_merge"), {"ids": ",".join(str(x.pk) for x in leads)})
        assert r.status_code == 302 and Lead.objects.count() == 21

    def test_requires_staff_permission(self, client: Client, trio: list[Lead]) -> None:
        r = client.post(reverse("admin:leads_lead_merge"), {"ids": f"{trio[0].pk},{trio[1].pk}", "primary": trio[0].pk})
        assert r.status_code == 302 and "login" in r["Location"]
        assert Lead.objects.count() == 3

    def test_staff_without_delete_permission_refused(self, client: Client, trio: list[Lead]) -> None:
        from django.contrib.auth.models import Permission

        staff = User.objects.create_user("clerk", password="pw", is_staff=True)
        staff.user_permissions.add(
            Permission.objects.get(codename="change_lead"), Permission.objects.get(codename="view_lead")
        )
        client.force_login(staff)
        r = client.post(reverse("admin:leads_lead_merge"), {"ids": f"{trio[0].pk},{trio[1].pk}", "primary": trio[0].pk})
        assert r.status_code == 403
        assert Lead.objects.count() == 3

    def test_duplicate_group_filter_and_links(self, admin_client: Client) -> None:
        a, b, c = make("A"), make("B"), make("C")
        g1, g2 = Tag.objects.create(name="review:duplicate-1"), Tag.objects.create(name="review:duplicate-12")
        a.tags.add(g1)
        b.tags.add(g1)
        c.tags.add(g2)
        url = reverse("admin:leads_lead_changelist")
        r = admin_client.get(url, {"duplicate_group": "review:duplicate-1"})
        assert r.status_code == 200
        assert {lead.pk for lead in r.context["cl"].result_list} == {a.pk, b.pk}
        html = admin_client.get(url).content.decode()
        assert "Open group" in html and "duplicate_group=review%3Aduplicate-1" in html
        change = admin_client.get(reverse("admin:leads_lead_change", args=[a.pk])).content.decode()
        assert "Open group (2 leads)" in change and "Review and merge" in change
        assert f"ids={a.pk},{b.pk}" in change

    def test_filter_lookups_empty_without_groups(self, admin_client: Client) -> None:
        assert admin_client.get(reverse("admin:leads_lead_changelist")).status_code == 200


class TestApi:
    def test_merge_endpoint(self, api_client: Client) -> None:
        primary = make("P")
        other = make("O", email="o@x.org", status="replied")
        Contact.objects.create(lead=other, name="c", email="o@x.org")
        r = api_client.post(
            "/api/leads/merge", {"primary_id": primary.pk, "other_ids": [other.pk]}, content_type="application/json"
        )
        assert r.status_code == 200, r.content
        data = r.json()
        assert data["id"] == primary.pk and data["email"] == "o@x.org" and data["status"] == "replied"
        assert len(data["contacts"]) == 1
        assert not Lead.objects.filter(pk=other.pk).exists()
        assert primary.history.first().history_change_reason == f"merged #{other.pk} by API"

    @pytest.mark.parametrize(
        ("body", "status"),
        [
            ({"primary_id": 1, "other_ids": []}, 400),
            ({"primary_id": 1, "other_ids": [1]}, 400),
            ({"primary_id": 1, "other_ids": [999999]}, 404),
            ({"primary_id": 999999, "other_ids": [1]}, 404),
        ],
    )
    def test_refusals(self, api_client: Client, body: dict[str, t.Any], status: int) -> None:
        lead = make("P")
        body = {k: ([lead.pk if i == 1 else i for i in v] if k == "other_ids" else v) for k, v in body.items()}
        if body["primary_id"] == 1:
            body["primary_id"] = lead.pk
        r = api_client.post("/api/leads/merge", body, content_type="application/json")
        assert r.status_code == status
        assert Lead.objects.count() == 1

    def test_too_many(self, api_client: Client) -> None:
        leads = [make(f"L{i}") for i in range(21)]
        r = api_client.post(
            "/api/leads/merge",
            {"primary_id": leads[0].pk, "other_ids": [x.pk for x in leads[1:]]},
            content_type="application/json",
        )
        assert r.status_code == 400 and Lead.objects.count() == 21


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, t.Any]] = []

    def get(self, path: str, params: t.Any = None) -> dict[str, t.Any]:
        self.calls.append(("get", path, params))
        return {"id": int(path.rsplit("/", 1)[1]), "name": "N", "status": "new", "created_at": "2026-01-01"}

    def post(self, path: str, data: t.Any = None) -> dict[str, t.Any]:
        self.calls.append(("post", path, data))
        return {"id": data["primary_id"], "name": "Merged"}


class TestCli:
    @pytest.fixture
    def fake(self, monkeypatch: pytest.MonkeyPatch) -> FakeApi:
        f = FakeApi()
        monkeypatch.setattr(cli, "api", f)
        return f

    def test_force_posts_without_preview(self, fake: FakeApi) -> None:
        r = runner.invoke(cli.app, ["leads", "merge", "5", "6", "7", "--force"])
        assert r.exit_code == 0, r.output
        assert fake.calls == [("post", "/leads/merge", {"primary_id": 5, "other_ids": [6, 7]})]
        assert "Merged 2 lead(s) into #5" in r.output

    def test_preview_then_confirm(self, fake: FakeApi) -> None:
        r = runner.invoke(cli.app, ["leads", "merge", "5", "6"], input="y\n")
        assert r.exit_code == 0, r.output
        assert [c[:2] for c in fake.calls] == [("get", "/leads/5"), ("get", "/leads/6"), ("post", "/leads/merge")]
        assert "KEEP" in r.output

    def test_preview_decline_aborts(self, fake: FakeApi) -> None:
        r = runner.invoke(cli.app, ["leads", "merge", "5", "6"], input="n\n")
        assert r.exit_code != 0
        assert not any(c[0] == "post" for c in fake.calls)

    def test_raw(self, fake: FakeApi) -> None:
        r = runner.invoke(cli.app, ["leads", "merge", "5", "6", "-f", "--raw"])
        assert json.loads(r.output) == {"id": 5, "name": "Merged"}
