"""Tests for Contact model + service + controller + research-task integration."""

import json

import pytest
from django.db import IntegrityError, transaction
from django.test import Client

from leads import service
from leads.models import City, Contact, EmailTemplate, Lead, LeadType
from leads.schema import ContactIn, ContactPatch, LeadIn, LeadPatch, SendEmailIn
from leads.tasks import ResearchLead, _create_lead_from_research, _find_existing_lead

# -----------------------------------------------------------------------------
# Model-level
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_contact_cascade_on_lead_delete(lead: Lead) -> None:
    """Deleting a lead deletes its contacts."""
    contact = Contact.objects.create(lead=lead, name="Alice", email="alice@example.com", is_primary=True)
    lead_id = lead.pk
    contact_id = contact.pk
    lead.delete()
    assert not Lead.objects.filter(pk=lead_id).exists()
    assert not Contact.objects.filter(pk=contact_id).exists()


@pytest.mark.django_db
def test_contact_unique_primary_constraint(lead: Lead) -> None:
    """Only one primary contact per lead is allowed; non-primaries are unrestricted."""
    Contact.objects.create(lead=lead, name="Primary", is_primary=True)
    Contact.objects.create(lead=lead, name="Secondary", is_primary=False)
    Contact.objects.create(lead=lead, name="Another", is_primary=False)

    with pytest.raises(IntegrityError), transaction.atomic():
        Contact.objects.create(lead=lead, name="Second Primary", is_primary=True)


@pytest.mark.django_db
def test_contact_primary_per_lead_is_scoped(city: City, lead_type: LeadType) -> None:
    """Two different leads can each have their own primary contact."""
    lead_a = Lead.objects.create(name="A", city=city, lead_type=lead_type)
    lead_b = Lead.objects.create(name="B", city=city, lead_type=lead_type)
    Contact.objects.create(lead=lead_a, name="Primary", is_primary=True)
    Contact.objects.create(lead=lead_b, name="Primary", is_primary=True)
    assert Contact.objects.filter(is_primary=True).count() == 2


@pytest.mark.django_db
def test_contact_history_tracked(lead: Lead) -> None:
    """Contact edits produce simple_history records."""
    contact = Contact.objects.create(lead=lead, name="Alice", email="a@example.com", is_primary=True)
    contact.email = "alice@example.com"
    contact.save()
    assert contact.history.count() == 2


# -----------------------------------------------------------------------------
# Service: create_contact / set_primary / dual-write
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_create_contact_demotes_existing_primary(lead: Lead) -> None:
    """Creating a new is_primary=True contact demotes any existing primary."""
    Contact.objects.create(lead=lead, name="Old Primary", is_primary=True)
    new = service.create_contact(ContactIn(lead_id=lead.pk, name="New Primary", email="new@x.com", is_primary=True))
    assert new.is_primary
    assert Contact.objects.filter(lead=lead, is_primary=True).count() == 1


@pytest.mark.django_db
def test_set_primary_contact_demotes_existing(lead: Lead) -> None:
    """set_primary_contact flips the flag atomically."""
    a = Contact.objects.create(lead=lead, name="A", is_primary=True)
    b = Contact.objects.create(lead=lead, name="B", is_primary=False)
    service.set_primary_contact(b)
    a.refresh_from_db()
    b.refresh_from_db()
    assert b.is_primary is True
    assert a.is_primary is False


@pytest.mark.django_db
def test_patch_contact_promote_to_primary(lead: Lead) -> None:
    """Patching is_primary=True demotes any existing primary."""
    Contact.objects.create(lead=lead, name="Old", is_primary=True)
    other = Contact.objects.create(lead=lead, name="Other", is_primary=False)
    service.patch_contact(other, ContactPatch(is_primary=True))
    other.refresh_from_db()
    assert other.is_primary
    assert Contact.objects.filter(lead=lead, is_primary=True).count() == 1


@pytest.mark.django_db
def test_apply_lead_data_dual_writes_to_primary_contact(city: City) -> None:
    """Creating/updating a lead via service mirrors contact fields to the primary contact."""
    lead = service.create_lead(LeadIn(name="ACME", email="info@acme.com", phone="+1234", city=None, instagram="acme"))
    primary = lead.contacts.get(is_primary=True)
    assert primary.email == "info@acme.com"
    assert primary.phone == "+1234"
    assert primary.instagram == "acme"

    # PATCH should overwrite touched fields and leave others alone
    service.patch_lead(lead, LeadPatch(email="hello@acme.com"))
    primary.refresh_from_db()
    assert primary.email == "hello@acme.com"
    assert primary.phone == "+1234"


# -----------------------------------------------------------------------------
# Research dedup via Contact + multi-contact merge (Q6-A)
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_dedup_finds_lead_via_contact_email(city: City) -> None:
    """Existing lead with a Contact carrying the email is matched on dedup."""
    lead = Lead.objects.create(name="Venue X", city=city)
    Contact.objects.create(lead=lead, name="Primary", email="hello@venue.com", is_primary=True)

    found = _find_existing_lead(ResearchLead(name="Different Name", email="hello@venue.com"), city)
    assert found is not None
    assert found.pk == lead.pk


@pytest.mark.django_db
def test_research_create_attaches_primary_contact(city: City) -> None:
    """A brand-new lead gets a primary Contact mirroring contact fields."""
    data = ResearchLead(name="Fresh", email="f@x.com", phone="+1", instagram="fresh")
    lead = _create_lead_from_research(data, city)
    assert lead is not None
    primary = lead.contacts.get(is_primary=True)
    assert primary.email == "f@x.com"
    assert primary.phone == "+1"
    assert primary.instagram == "fresh"


@pytest.mark.django_db
def test_research_namecity_match_with_new_email_creates_secondary_contact(city: City) -> None:
    """Q6-A: name+city match with a NEW email adds a non-primary Contact."""
    lead = Lead.objects.create(name="Same Venue", city=city)
    Contact.objects.create(lead=lead, name="Primary", email="alice@v.com", is_primary=True)

    data = ResearchLead(name="Same Venue", email="bob@v.com")
    result = _create_lead_from_research(data, city)
    assert result is not None
    assert result.pk == lead.pk
    contacts = list(lead.contacts.all())
    assert len(contacts) == 2
    secondary = [c for c in contacts if not c.is_primary][0]
    assert secondary.email == "bob@v.com"


@pytest.mark.django_db
def test_research_merge_fills_blanks_on_matched_contact(city: City) -> None:
    """Matching by Contact email fills blanks on that Contact, not just the Lead."""
    lead = Lead.objects.create(name="Merge", city=city, email="m@x.com")
    primary = Contact.objects.create(lead=lead, name="Primary", email="m@x.com", is_primary=True)

    data = ResearchLead(name="Merge", email="m@x.com", website="https://merge.com", phone="+999")
    _create_lead_from_research(data, city)
    primary.refresh_from_db()
    assert primary.website == "https://merge.com"
    assert primary.phone == "+999"


# -----------------------------------------------------------------------------
# Controller: /contacts CRUD
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_contact_controller_crud_flow(api_client: Client, lead: Lead) -> None:
    """Round-trip: create → list → get → patch → set-primary → delete."""
    # Create
    resp = api_client.post(
        "/api/contacts/",
        data=json.dumps({"lead_id": lead.pk, "name": "Alice", "email": "alice@x.com", "is_primary": True}),
        content_type="application/json",
    )
    assert resp.status_code == 201, resp.content
    contact_id = resp.json()["id"]

    # List filtered by lead_id
    resp = api_client.get(f"/api/contacts/?lead_id={lead.pk}")
    assert resp.status_code == 200
    items = resp.json()["results"]
    assert len(items) == 1
    assert items[0]["id"] == contact_id

    # Patch role
    resp = api_client.patch(
        f"/api/contacts/{contact_id}",
        data=json.dumps({"role": "booker"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert resp.json()["role"] == "booker"

    # Create a second contact + set-primary on it
    resp = api_client.post(
        "/api/contacts/",
        data=json.dumps({"lead_id": lead.pk, "name": "Bob"}),
        content_type="application/json",
    )
    bob_id = resp.json()["id"]
    resp = api_client.post(f"/api/contacts/{bob_id}/set-primary", content_type="application/json")
    assert resp.status_code == 200
    assert resp.json()["is_primary"] is True
    # Alice should now be non-primary
    alice = Contact.objects.get(pk=contact_id)
    assert alice.is_primary is False

    # Delete
    resp = api_client.delete(f"/api/contacts/{contact_id}")
    assert resp.status_code == 204
    assert not Contact.objects.filter(pk=contact_id).exists()


# -----------------------------------------------------------------------------
# Send-email contact_id override
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_send_email_uses_primary_contact_email_by_default(lead: Lead, email_template: EmailTemplate) -> None:
    """When 'to' is omitted, recipient defaults to the primary contact's email."""
    Contact.objects.create(lead=lead, name="Primary", email="primary@x.com", is_primary=True)
    Contact.objects.create(lead=lead, name="Other", email="other@x.com")

    result = service.send_email_to_lead_api(lead, SendEmailIn(template_id=email_template.pk))
    from leads.models import EmailSent

    sent = EmailSent.objects.get(pk=result["email_id"])
    assert sent.to == ["primary@x.com"]
    assert sent.contact is not None
    assert sent.contact.email == "primary@x.com"


@pytest.mark.django_db
def test_send_email_with_explicit_contact_id(lead: Lead, email_template: EmailTemplate) -> None:
    """Passing contact_id routes to that contact's email and records it on EmailSent."""
    Contact.objects.create(lead=lead, name="Primary", email="primary@x.com", is_primary=True)
    other = Contact.objects.create(lead=lead, name="Other", email="other@x.com")

    result = service.send_email_to_lead_api(lead, SendEmailIn(template_id=email_template.pk, contact_id=other.pk))
    from leads.models import EmailSent

    sent = EmailSent.objects.get(pk=result["email_id"])
    assert sent.to == ["other@x.com"]
    assert sent.contact_id == other.pk


# -----------------------------------------------------------------------------
# Email draft contact wiring
# -----------------------------------------------------------------------------


@pytest.mark.django_db
def test_lead_admin_hides_legacy_contact_fieldset() -> None:
    """Phase 3: the 'Contact' fieldset is gone so the 5 legacy fields can't be edited."""
    from django.contrib.admin.sites import AdminSite

    from leads import models as m
    from leads.admin import LeadAdmin

    admin_instance = LeadAdmin(m.Lead, AdminSite())
    fieldset_titles = [name for name, _opts in admin_instance.fieldsets]
    assert "Contact" not in fieldset_titles
    # And the inline now manages contacts
    assert any(inline.__name__ == "ContactInline" for inline in admin_instance.inlines)


@pytest.mark.django_db
def test_send_email_view_defaults_to_primary_contact_email(lead: Lead, api_client: Client) -> None:
    """Initial 'to' on the admin send-email form comes from the primary contact."""
    from django.contrib.auth.models import User

    Contact.objects.create(lead=lead, name="Primary", email="primary@x.com", is_primary=True)
    superuser = User.objects.create_superuser("admin", "admin@x.com", "pw")
    api_client.force_login(superuser)

    resp = api_client.get(f"/admin/leads/lead/{lead.pk}/send-email/")
    assert resp.status_code == 200
    assert b"primary@x.com" in resp.content


@pytest.mark.django_db
def test_create_email_draft_defaults_to_primary_contact(lead: Lead) -> None:
    """create_email_draft picks up primary contact's email when 'to' is omitted."""
    Contact.objects.create(lead=lead, name="Primary", email="p@x.com", is_primary=True)
    from leads.schema import EmailDraftIn

    draft = service.create_email_draft(EmailDraftIn(lead_id=lead.pk, subject="Hi", body="Body"))
    assert draft.to == ["p@x.com"]
    assert draft.contact is not None
    assert draft.contact.is_primary
