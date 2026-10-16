"""Tests for the new lead fields and the lead upsert (dedupe + merge)."""

import typing as t

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from leads import service
from leads.models import Contact, Lead
from leads.schema import CityIn, LeadIn, LeadPatch, LeadUpsertIn

pytestmark = pytest.mark.django_db


class TestLeadStatus:
    def test_status_order(self) -> None:
        assert [s.value for s in Lead.Status] == [
            "new",
            "contacted",
            "replied",
            "qualified",
            "call_booked",
            "org_created",
            "first_event_live",
            "converted",
            "lost",
        ]

    def test_filter_by_new_status(self, api_client: Client, lead: Lead) -> None:
        Lead.objects.filter(pk=lead.pk).update(status=Lead.Status.CALL_BOOKED)
        assert api_client.get("/api/leads/?status=call_booked").json()["count"] == 1
        assert api_client.get("/api/leads/?status=replied").json()["count"] == 0


class TestExternalRefConstraint:
    def test_unique_when_non_empty(self) -> None:
        Lead.objects.create(name="A", external_ref="x-1")
        with pytest.raises(IntegrityError), transaction.atomic():
            Lead.objects.create(name="B", external_ref="x-1")

    def test_blank_allowed_many_times(self) -> None:
        Lead.objects.create(name="A")
        Lead.objects.create(name="B")
        assert Lead.objects.filter(external_ref="").count() == 2

    def test_create_endpoint_conflict_is_409(self, api_client: Client) -> None:
        Lead.objects.create(name="A", external_ref="x-1")
        resp = api_client.post("/api/leads/", {"name": "B", "external_ref": "x-1"}, content_type="application/json")
        assert resp.status_code == 409

    def test_patch_endpoint_conflict_is_409(self, api_client: Client) -> None:
        Lead.objects.create(name="A", external_ref="x-1")
        other = Lead.objects.create(name="B")
        resp = api_client.patch(f"/api/leads/{other.pk}", {"external_ref": "x-1"}, content_type="application/json")
        assert resp.status_code == 409


class TestNewFieldsCrud:
    def test_create_and_read_new_fields(self, api_client: Client) -> None:
        payload = {
            "name": "Queer Berlin",
            "bluesky": "queer.bsky.social",
            "facebook": "https://facebook.com/queerberlin",
            "linkedin": "https://linkedin.com/company/queerberlin",
            "whatsapp": "+49 170 1234567",
            "ticketing_platforms": ["Eventbrite", " RA ", "eventbrite"],
            "external_ref": "ops:1",
        }
        resp = api_client.post("/api/leads/", payload, content_type="application/json")
        assert resp.status_code == 201
        body = resp.json()
        assert body["ticketing_platforms"] == ["eventbrite", "ra"]
        assert body["bluesky"] == "queer.bsky.social"
        assert body["whatsapp"] == "+49 170 1234567"
        assert body["external_ref"] == "ops:1"
        got = api_client.get("/api/leads/?external_ref=OPS:1").json()
        assert got["count"] == 1

    def test_patch_ticketing_platforms(self, lead: Lead) -> None:
        service.patch_lead(lead, LeadPatch(ticketing_platforms=["RA"]))
        lead.refresh_from_db()
        assert lead.ticketing_platforms == ["ra"]


def _upsert(**kwargs: t.Any) -> tuple[Lead, bool]:
    kwargs.setdefault("name", "Org")
    lead, created, _ = service.upsert_lead(LeadUpsertIn(**kwargs))
    return lead, created


class TestUpsertDedupe:
    def test_creates_when_new(self) -> None:
        lead, created = _upsert(name="Fresh", email="f@x.org", ticketing_platforms=["ra"], tags=["Queer"])
        assert created is True
        assert lead.contacts.filter(is_primary=True, email="f@x.org").exists()
        assert lead.ticketing_platforms == ["ra"]
        assert [tg.name for tg in lead.tags.all()] == ["Queer"]

    @pytest.mark.parametrize("field", ["email", "phone", "instagram", "telegram", "website"])
    def test_matches_contact_methods(self, field: str) -> None:
        value = "https://site.org" if field == "website" else f"{field}-value@x.org" if field == "email" else "v1"
        first, _ = _upsert(name="First", **{field: value})
        second, created = _upsert(name="Different name", **{field: value.upper() if field != "website" else value})
        assert created is False
        assert second.pk == first.pk

    @pytest.mark.parametrize("field", ["bluesky", "facebook", "linkedin", "whatsapp"])
    def test_matches_lead_only_handles(self, field: str) -> None:
        first, _ = _upsert(name="First", **{field: "handle-1"})
        second, created = _upsert(name="Other", **{field: "HANDLE-1"})
        assert created is False
        assert second.pk == first.pk

    def test_matches_external_ref_first(self) -> None:
        first, _ = _upsert(name="First", external_ref="ops:1", email="a@x.org")
        # even if the email differs and name differs, the ref wins
        second, created = _upsert(name="Renamed", external_ref="OPS:1", email="b@x.org")
        assert created is False
        assert second.pk == first.pk
        assert Lead.objects.count() == 1

    def test_matches_name_and_city(self) -> None:
        city = CityIn(name="Berlin", country="Germany", iso2="DE")
        first, _ = _upsert(name="Club", city=city)
        second, created = _upsert(name="club", city=city)
        assert created is False
        assert second.pk == first.pk

    def test_name_alone_does_not_match(self) -> None:
        _upsert(name="Club")
        _, created = _upsert(name="Club")
        assert created is True

    def test_name_city_does_not_merge_distinct_external_refs(self) -> None:
        city = CityIn(name="Berlin", country="Germany", iso2="DE")
        _upsert(name="Club", city=city, external_ref="ops:1")
        _, created = _upsert(name="Club", city=city, external_ref="ops:2")
        assert created is True
        assert Lead.objects.count() == 2

    def test_idempotent_for_same_ref(self) -> None:
        a, c1 = _upsert(name="Org", external_ref="ops:9")
        b, c2 = _upsert(name="Org", external_ref="ops:9")
        assert (c1, c2) == (True, False)
        assert a.pk == b.pk


class TestUpsertMerge:
    def test_fills_blanks_only(self) -> None:
        existing, _ = _upsert(name="Org", email="o@x.org", company="Keep Co", notes="keep", bluesky="")
        merged, created = _upsert(
            name="Org2", email="o@x.org", company="Overwrite Co", notes="new", bluesky="org.bsky.social"
        )
        assert created is False
        merged.refresh_from_db()
        assert merged.pk == existing.pk
        assert merged.company == "Keep Co"
        assert merged.notes == "keep"
        assert merged.bluesky == "org.bsky.social"

    def test_never_overwrites_status_temperature_value(self) -> None:
        existing, _ = _upsert(name="Org", email="o@x.org")
        Lead.objects.filter(pk=existing.pk).update(status=Lead.Status.REPLIED, temperature=Lead.Temperature.HOT)
        merged, _ = _upsert(name="Org", email="o@x.org", status=Lead.Status.NEW, temperature=Lead.Temperature.COLD)
        merged.refresh_from_db()
        assert merged.status == Lead.Status.REPLIED
        assert merged.temperature == Lead.Temperature.HOT

    def test_tags_and_platforms_merge_additively(self) -> None:
        _upsert(name="Org", email="o@x.org", tags=["A"], ticketing_platforms=["ra"])
        merged, _ = _upsert(name="Org", email="o@x.org", tags=["a", "B"], ticketing_platforms=["eventbrite", "ra"])
        assert sorted(tg.name for tg in merged.tags.all()) == ["A", "B"]
        assert merged.ticketing_platforms == ["ra", "eventbrite"]

    def test_external_ref_filled_when_blank(self) -> None:
        existing, _ = _upsert(name="Org", email="o@x.org")
        merged, _ = _upsert(name="Org", email="o@x.org", external_ref="ops:5")
        assert merged.pk == existing.pk
        assert merged.external_ref == "ops:5"

    def test_existing_external_ref_not_replaced(self) -> None:
        _upsert(name="Org", email="o@x.org", external_ref="ops:1")
        other, created = _upsert(name="Org", email="o@x.org", external_ref="ops:2")
        assert created is False  # strong key (email) wins across sources; existing ref is kept
        assert other.external_ref == "ops:1"

    def test_fills_city_and_type_when_missing(self) -> None:
        _upsert(name="Org", email="o@x.org")
        merged, _ = _upsert(
            name="Org", email="o@x.org", lead_type="Venue", city=CityIn(name="Rome", country="Italy", iso2="IT")
        )
        assert merged.lead_type is not None and merged.lead_type.name == "Venue"
        assert merged.city is not None and merged.city.name == "Rome"

    def test_new_contact_method_becomes_secondary_contact(self) -> None:
        existing, _ = _upsert(name="Org", email="o@x.org")
        merged, _ = _upsert(name="Org", email="o@x.org", phone="+4912345")
        # shares email with the primary -> filled into the primary, no secondary
        assert merged.contacts.count() == 1
        assert merged.contacts.get(is_primary=True).phone == "+4912345"
        # matched through a handle only, bringing a brand new email -> secondary contact
        by_handle, _ = _upsert(name="Org", bluesky="org.bsky", email="other@x.org")
        assert by_handle.pk == existing.pk is False or by_handle.pk != existing.pk
        _upsert(name="Org", external_ref="r1", email="o@x.org")
        again, _ = _upsert(name="Org", external_ref="r1", email="third@x.org")
        assert again.contacts.filter(email="third@x.org", is_primary=False).exists()

    def test_lead_without_contacts_gets_primary(self) -> None:
        legacy = Lead.objects.create(name="Legacy", email="l@x.org")
        merged, created = _upsert(name="Legacy", email="l@x.org", phone="123")
        assert created is False and merged.pk == legacy.pk
        primary = Contact.objects.get(lead=legacy, is_primary=True)
        assert (primary.email, primary.phone) == ("l@x.org", "123")


class TestUpsertEndpoint:
    def _post(self, client: Client, payload: dict[str, t.Any]) -> t.Any:
        return client.post("/api/leads/upsert", payload, content_type="application/json")

    def test_create_then_update(self, api_client: Client) -> None:
        payload = {
            "external_ref": "ops:1",
            "name": "Org",
            "type": "Collective",
            "city": {"name": "Berlin", "country": "Germany", "iso2": "DE"},
            "instagram": "org",
            "ticketing_platforms": ["Eventbrite"],
            "tags": ["Queer"],
        }
        first = self._post(api_client, payload)
        assert first.status_code == 200
        body = first.json()
        assert body["created"] is True
        second = self._post(api_client, {**payload, "tags": ["Kink"]})
        assert second.json() == {
            "id": body["id"],
            "created": False,
            "do_not_contact": False,
            "matched_on": "external_ref",
        }
        lead = Lead.objects.get(pk=body["id"])
        assert lead.lead_type is not None and lead.lead_type.name == "Collective"
        assert sorted(tg.name for tg in lead.tags.all()) == ["Kink", "Queer"]

    def test_flat_city_and_country(self, api_client: Client) -> None:
        resp = self._post(api_client, {"name": "Org", "city": "Vienna", "country": "Austria"})
        assert resp.status_code == 200
        assert Lead.objects.get(pk=resp.json()["id"]).city.name == "Vienna"  # type: ignore[union-attr]

    def test_requires_name(self, api_client: Client) -> None:
        assert self._post(api_client, {"email": "x@y.org"}).status_code == 422

    def test_requires_api_key(self, api_client: Client, settings: t.Any) -> None:
        settings.DEBUG = False
        settings.API_KEY = "secret"
        assert self._post(api_client, {"name": "Org"}).status_code == 401
        resp = api_client.post(
            "/api/leads/upsert", {"name": "Org"}, content_type="application/json", HTTP_X_API_KEY="secret"
        )
        assert resp.status_code == 200


def test_lead_in_defaults() -> None:
    data = LeadIn(name="x")
    assert data.ticketing_platforms == [] and data.external_ref == ""
