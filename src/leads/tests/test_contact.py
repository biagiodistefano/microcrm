"""Tests for Contact model (Phase 1)."""

import pytest
from django.db import IntegrityError, transaction

from leads.models import City, Contact, Lead, LeadType


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
