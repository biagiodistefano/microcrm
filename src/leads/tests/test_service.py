"""Tests for leads service."""

from unittest.mock import MagicMock, patch

import pytest

from leads.models import City, EmailSent, EmailTemplate, Lead, LeadType, Tag
from leads.schema import CityIn, LeadIn, LeadPatch
from leads.service import (
    create_lead,
    get_or_create_city,
    get_or_create_lead_type,
    get_or_create_tags,
    patch_lead,
    render_email_template,
    send_email_to_lead,
    update_lead,
    validate_no_placeholders,
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


class TestRenderEmailTemplate:
    """Tests for render_email_template function."""

    def test_renders_all_placeholders(self, lead: Lead, email_template: EmailTemplate) -> None:
        subject, body = render_email_template(email_template, lead)

        assert subject == "Hello Test Lead!"
        assert "Hi Test Lead," in body
        assert "Berlin, Germany" in body

    def test_handles_missing_lead_data(self, email_template: EmailTemplate) -> None:
        lead = Lead.objects.create(name="Minimal Lead")
        subject, body = render_email_template(email_template, lead)

        assert subject == "Hello Minimal Lead!"
        assert "Hi Minimal Lead," in body
        # City placeholder replaced with empty string
        assert "you're from ." in body

    def test_preserves_template_without_placeholders(
        self, lead: Lead, email_template_no_placeholders: EmailTemplate
    ) -> None:
        subject, body = render_email_template(email_template_no_placeholders, lead)

        assert subject == "General Announcement"
        assert body == "This is a general message with no personalization."


class TestValidateNoPlaceholders:
    """Tests for validate_no_placeholders function."""

    def test_returns_empty_list_for_clean_text(self) -> None:
        result = validate_no_placeholders("Hello World", "This is clean text")
        assert result == []

    def test_finds_placeholders_in_subject(self) -> None:
        result = validate_no_placeholders("Hello {name}!", "Clean body")
        assert "{name}" in result

    def test_finds_placeholders_in_body(self) -> None:
        result = validate_no_placeholders("Clean subject", "Hello {lead.name}, from {lead.city}")
        assert "{lead.name}" in result
        assert "{lead.city}" in result

    def test_finds_placeholders_in_both(self) -> None:
        result = validate_no_placeholders("Hi {name}", "You are from {city}")
        assert len(result) == 2
        assert "{name}" in result
        assert "{city}" in result


class TestSendEmailToLead:
    """Tests for send_email_to_lead function."""

    @patch("leads.service.EmailMessage")
    def test_sends_email_and_creates_record(self, mock_email_class: MagicMock, lead: Lead) -> None:
        mock_email = mock_email_class.return_value

        email_sent = send_email_to_lead(
            lead=lead,
            subject="Test Subject",
            body="Test Body",
            to=["recipient@example.com"],
        )

        assert email_sent.lead == lead
        assert email_sent.subject == "Test Subject"
        assert email_sent.body == "Test Body"
        assert email_sent.to == ["recipient@example.com"]
        assert email_sent.status == EmailSent.Status.SENT
        assert email_sent.sent_at is not None
        mock_email.send.assert_called_once_with(fail_silently=False)

    @patch("leads.service.EmailMessage")
    def test_updates_lead_last_contact(self, mock_email_class: MagicMock, lead: Lead) -> None:
        assert lead.last_contact is None

        send_email_to_lead(
            lead=lead,
            subject="Test",
            body="Test",
            to=["test@example.com"],
        )

        lead.refresh_from_db()
        assert lead.last_contact is not None

    @patch("leads.service.EmailMessage")
    def test_includes_bcc_recipients(self, mock_email_class: MagicMock, lead: Lead) -> None:
        send_email_to_lead(
            lead=lead,
            subject="Test",
            body="Test",
            to=["to@example.com"],
            bcc=["bcc@example.com"],
        )

        mock_email_class.assert_called_once()
        call_kwargs = mock_email_class.call_args.kwargs
        assert call_kwargs["bcc"] == ["bcc@example.com"]

    @patch("leads.service.EmailMessage")
    def test_associates_template_with_record(
        self, mock_email_class: MagicMock, lead: Lead, email_template: EmailTemplate
    ) -> None:
        email_sent = send_email_to_lead(
            lead=lead,
            subject="Test",
            body="Test",
            to=["test@example.com"],
            template=email_template,
        )

        assert email_sent.template == email_template

    def test_raises_error_for_unreplaced_placeholders(self, lead: Lead) -> None:
        with pytest.raises(ValueError, match="Unreplaced placeholders"):
            send_email_to_lead(
                lead=lead,
                subject="Hello {name}",
                body="Test",
                to=["test@example.com"],
            )

        # No EmailSent record should be created
        assert EmailSent.objects.count() == 0

    @patch("leads.service.EmailMessage")
    def test_marks_failed_on_send_error(self, mock_email_class: MagicMock, lead: Lead) -> None:
        mock_email = mock_email_class.return_value
        mock_email.send.side_effect = Exception("SMTP error")

        with pytest.raises(Exception, match="SMTP error"):
            send_email_to_lead(
                lead=lead,
                subject="Test",
                body="Test",
                to=["test@example.com"],
            )

        email_sent = EmailSent.objects.get(lead=lead)
        assert email_sent.status == EmailSent.Status.FAILED
        assert "SMTP error" in email_sent.error_message
        # last_contact should NOT be updated on failure
        lead.refresh_from_db()
        assert lead.last_contact is None
