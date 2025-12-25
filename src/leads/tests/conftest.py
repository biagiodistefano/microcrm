"""Test fixtures for leads app."""

from datetime import date, timedelta

import pytest
from django.test import Client

from leads.models import Action, City, EmailSent, EmailTemplate, Lead, LeadType, ResearchJob, Tag


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


@pytest.fixture
def email_template() -> EmailTemplate:
    """Create a test email template."""
    return EmailTemplate.objects.create(
        name="Test Template",
        subject="Hello {lead.name}!",
        body="Hi {lead.name},\n\nWe noticed you're from {lead.city}.\n\nBest regards",
    )


@pytest.fixture
def email_template_no_placeholders() -> EmailTemplate:
    """Create a test email template without placeholders."""
    return EmailTemplate.objects.create(
        name="Simple Template",
        subject="General Announcement",
        body="This is a general message with no personalization.",
    )


@pytest.fixture
def email_sent(lead: Lead, email_template: EmailTemplate) -> EmailSent:
    """Create a test sent email record."""
    return EmailSent.objects.create(
        lead=lead,
        template=email_template,
        from_email="test@example.com",
        to=["recipient@example.com"],
        bcc=[],
        subject="Hello Test Lead!",
        body="Hi Test Lead,\n\nWe noticed you're from Berlin, Germany.\n\nBest regards",
        status=EmailSent.Status.SENT,
    )
