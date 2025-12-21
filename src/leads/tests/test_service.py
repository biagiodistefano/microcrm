"""Tests for leads service."""

import pytest

from leads.models import City, Lead, LeadType, Tag
from leads.schema import CityIn, LeadIn, LeadPatch
from leads.service import (
    create_lead,
    get_or_create_city,
    get_or_create_lead_type,
    get_or_create_tags,
    patch_lead,
    update_lead,
)

pytestmark = pytest.mark.django_db


class TestGetOrCreateCity:
    def test_creates_new_city(self) -> None:
        initial_count = City.objects.count()
        city = get_or_create_city(CityIn(name="Vienna", country="Austria", iso2="AT"))
        assert city.name == "Vienna"
        assert city.country == "Austria"
        assert city.iso2 == "AT"
        assert City.objects.count() == initial_count + 1

    def test_reuses_existing_city_case_insensitive(self) -> None:
        City.objects.create(name="Vienna", country="Austria", iso2="AT")
        initial_count = City.objects.count()
        city = get_or_create_city(CityIn(name="VIENNA", country="AUSTRIA", iso2="at"))
        assert city.name == "Vienna"  # Original case preserved
        assert City.objects.count() == initial_count  # No new city created


class TestGetOrCreateLeadType:
    def test_creates_new_lead_type(self) -> None:
        initial_count = LeadType.objects.count()
        lead_type = get_or_create_lead_type("Unique Test Type")
        assert lead_type.name == "Unique Test Type"
        assert LeadType.objects.count() == initial_count + 1

    def test_reuses_existing_lead_type_case_insensitive(self) -> None:
        LeadType.objects.create(name="Unique Venue Type")
        initial_count = LeadType.objects.count()
        lead_type = get_or_create_lead_type("UNIQUE VENUE TYPE")
        assert lead_type.name == "Unique Venue Type"
        assert LeadType.objects.count() == initial_count


class TestGetOrCreateTags:
    def test_creates_new_tags(self) -> None:
        initial_count = Tag.objects.count()
        tags = get_or_create_tags(["UniqueTag1", "UniqueTag2"])
        assert len(tags) == 2
        assert Tag.objects.count() == initial_count + 2

    def test_reuses_existing_tags_case_insensitive(self) -> None:
        Tag.objects.create(name="UniqueExistingTag")
        initial_count = Tag.objects.count()
        tags = get_or_create_tags(["UNIQUEEXISTINGTAG", "uniqueexistingtag", "NewUniqueTag"])
        assert len(tags) == 3  # Returns 3 items
        assert Tag.objects.count() == initial_count + 1  # Only 1 new tag
        assert tags[0].id == tags[1].id  # First two are the same


class TestCreateLead:
    def test_creates_lead_with_minimal_data(self) -> None:
        lead = create_lead(LeadIn(name="Test Lead"))
        assert lead.name == "Test Lead"
        assert lead.status == Lead.Status.NEW
        assert lead.temperature == Lead.Temperature.COLD

    def test_creates_lead_with_all_data(self) -> None:
        lead = create_lead(
            LeadIn(
                name="Full Lead",
                email="test@example.com",
                lead_type="Test Collective",
                city=CityIn(name="Test Berlin", country="Germany", iso2="DE"),
                tags=["TestTechno", "TestLGBTQ"],
                status=Lead.Status.CONTACTED,
                temperature=Lead.Temperature.HOT,
            )
        )
        assert lead.name == "Full Lead"
        assert lead.email == "test@example.com"
        assert lead.lead_type is not None
        assert lead.lead_type.name == "Test Collective"
        assert lead.city is not None
        assert lead.city.name == "Test Berlin"
        assert lead.tags.count() == 2
        assert lead.status == Lead.Status.CONTACTED
        assert lead.temperature == Lead.Temperature.HOT

    def test_creates_related_objects_automatically(self) -> None:
        initial_cities = City.objects.count()
        initial_types = LeadType.objects.count()
        initial_tags = Tag.objects.count()

        create_lead(
            LeadIn(
                name="Test",
                lead_type="Brand New Type",
                city=CityIn(name="Brand New City", country="Brand New Country"),
                tags=["BrandNewTag"],
            )
        )

        assert City.objects.count() == initial_cities + 1
        assert LeadType.objects.count() == initial_types + 1
        assert Tag.objects.count() == initial_tags + 1


class TestUpdateLead:
    def test_replaces_all_fields(self, lead: Lead) -> None:
        updated = update_lead(
            lead,
            LeadIn(
                name="Updated Name",
                email="new@example.com",
                status=Lead.Status.QUALIFIED,
            ),
        )
        assert updated.name == "Updated Name"
        assert updated.email == "new@example.com"
        assert updated.status == Lead.Status.QUALIFIED
        # Fields not provided are reset
        assert updated.company == ""
        assert updated.city is None
        assert updated.lead_type is None
        assert updated.tags.count() == 0


class TestPatchLead:
    def test_updates_only_provided_fields(self, lead: Lead) -> None:
        original_email = lead.email
        original_city = lead.city

        patched = patch_lead(lead, LeadPatch(status=Lead.Status.CONTACTED))

        assert patched.status == Lead.Status.CONTACTED
        assert patched.email == original_email  # Unchanged
        assert patched.city == original_city  # Unchanged

    def test_can_update_tags(self, lead: Lead) -> None:
        assert lead.tags.count() == 1

        patched = patch_lead(lead, LeadPatch(tags=["PatchTag1", "PatchTag2"]))

        assert patched.tags.count() == 2
        tag_names = list(patched.tags.values_list("name", flat=True))
        assert "PatchTag1" in tag_names
        assert "PatchTag2" in tag_names
