"""Test fixtures for leads app."""

from datetime import date, timedelta

import pytest
from django.test import Client

from leads.models import Action, City, Lead, LeadType, ResearchJob, Tag


@pytest.fixture
def city() -> City:
    """Create a test city."""
    city, _ = City.objects.get_or_create(name="Berlin", defaults={"country": "Germany", "iso2": "DE"})
    return city


@pytest.fixture
def lead_type() -> LeadType:
    """Create a test lead type."""
    lead_type, _ = LeadType.objects.get_or_create(name="Collective")
    return lead_type


@pytest.fixture
def tag() -> Tag:
    """Create a test tag."""
    tag, _ = Tag.objects.get_or_create(name="Techno")
    return tag


@pytest.fixture
def lead(city: City, lead_type: LeadType, tag: Tag) -> Lead:
    """Create a test lead with related objects."""
    lead = Lead.objects.create(
        name="Test Lead",
        email="test@example.com",
        company="Test Company",
        city=city,
        lead_type=lead_type,
        status=Lead.Status.NEW,
        temperature=Lead.Temperature.WARM,
    )
    lead.tags.add(tag)
    return lead


@pytest.fixture
def api_client() -> Client:
    """Return a Django test client."""
    return Client()


@pytest.fixture
def action(lead: Lead) -> Action:
    """Create a test action for a lead."""
    return Action.objects.create(
        lead=lead,
        name="Follow up call",
        notes="Discuss partnership",
        status=Action.Status.PENDING,
        due_date=date.today() + timedelta(days=3),
    )


@pytest.fixture
def research_job(city: City) -> ResearchJob:
    """Create a test research job."""
    return ResearchJob.objects.create(
        city=city,
        status=ResearchJob.Status.NOT_STARTED,
    )


@pytest.fixture
def completed_research_job(city: City) -> ResearchJob:
    """Create a completed research job with raw_result."""
    return ResearchJob.objects.create(
        city=city,
        status=ResearchJob.Status.COMPLETED,
        raw_result='{"leads": []}',
        result={"leads": []},
        leads_created=0,
    )
