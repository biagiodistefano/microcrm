"""Celery tasks for lead research."""

import logging
import traceback
import typing as t
from enum import Enum

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from google import genai
from ninja.errors import HttpError
from pydantic import BaseModel

from leads.models import City, Contact, Lead, LeadType, ResearchJob, ResearchPromptConfig, Tag

logger = logging.getLogger(__name__)

DEEP_RESEARCH_AGENT = "deep-research-pro-preview-12-2025"

FALLBACK_PARSING_PROMPT = """
The following text contains research results about potential leads for an event promotion platform.
Extract all the leads mentioned in the text and structure them according to the provided schema.

For each lead, extract:
- name (required)
- company (if different from name)
- lead_type (e.g., Collective, Venue, Promoter, Organization, Individual, etc.)
- email (if publicly available)
- phone (with international prefix if available)
- instagram (handle without @)
- telegram (username or handle, not the link)
- website (full URL)
- notes (include current registration method, event frequency, size, pain points, why they're a good fit)
- temperature (hot/warm/cold based on fit and complexity of their needs)
- tags (relevant keywords like "LGBTQ+", "Queer", "Sex-positive", "Kink", "Workshop", "Party", etc.)

Research text:
{text_output}

Please extract all leads and return them in structured format.
""".strip()

GEMINI_FALLBACK_MODEL = "gemini-3-flash-preview"


class Temperature(str, Enum):
    """Temperature enum for research leads."""

    cold = "cold"
    warm = "warm"
    hot = "hot"


class ResearchLead(BaseModel):
    """Schema for a lead from research."""

    name: str
    company: str = ""
    lead_type: str = ""
    email: str = ""
    phone: str = ""
    instagram: str = ""
    telegram: str = ""
    website: str = ""
    notes: str = ""
    temperature: Temperature = Temperature.cold
    tags: list[str] = []


class ResearchResult(BaseModel):
    """Schema for research output."""

    leads: list[ResearchLead]


def get_gemini_client() -> genai.Client:
    """Get Gemini client with API key."""
    return genai.Client(api_key=settings.GEMINI_API_KEY)


def queue_research(city_id: int) -> dict[str, t.Any]:
    """Queue a deep research job for a city.

    Creates a new ResearchJob and queues it for processing. The actual API call
    is rate-limited to 1/min via the start_research_job task.

    Args:
        city_id: The ID of the city to research

    Returns:
        Dict with job_id and status

    Raises:
        HttpError: 400 if there's already an active research job for this city.
    """
    city = City.objects.get(id=city_id)

    # Check for existing active jobs first
    active_statuses = [ResearchJob.Status.PENDING, ResearchJob.Status.RUNNING]
    if ResearchJob.objects.filter(city=city, status__in=active_statuses).exists():
        raise HttpError(400, f"Research already running for {city}")

    # Create job and set to PENDING before queuing to avoid race conditions
    job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.PENDING)

    # Queue the rate-limited task
    start_research_job.delay(job.id)

    logger.info("Queued research job %s for %s", job.id, city)
    return {"job_id": job.id, "status": "pending"}


@shared_task(rate_limit="1/m")
def start_research_job(job_id: int) -> dict[str, t.Any]:
    """Start research for a job by creating a Gemini interaction.

    This task is rate-limited to 1/min to comply with Gemini Deep Research API limits.

    Args:
        job_id: The ID of the ResearchJob to start

    Returns:
        Dict with job_id, status, and interaction_id
    """
    job = ResearchJob.objects.get(id=job_id)

    # Skip if job already has an interaction (already started)
    if job.gemini_interaction_id:
        logger.warning("Job %s already has interaction_id, skipping start", job.id)
        return {"job_id": job.id, "status": "already_started"}

    # Skip if job is not in a startable state
    if job.status not in (ResearchJob.Status.PENDING, ResearchJob.Status.NOT_STARTED):
        logger.warning("Job %s in unexpected status %s, skipping start", job.id, job.status)
        return {"job_id": job.id, "status": "skipped", "reason": f"status is {job.status}"}

    config = ResearchPromptConfig.get_solo()
    lead_types = list(LeadType.objects.values_list("name", flat=True))

    # Build prompt with schema
    schema_json = ResearchResult.model_json_schema()
    prompt = config.prompt_template.format(
        city=str(job.city),
        lead_types=", ".join(lead_types),
        schema=schema_json,
    )

    try:
        client = get_gemini_client()
        interaction = client.interactions.create(
            input=prompt,
            agent=DEEP_RESEARCH_AGENT,
            background=True,
            response_format=schema_json,
        )
        job.gemini_interaction_id = interaction.id
        job.status = ResearchJob.Status.RUNNING
        job.error = ""
        job.completed_at = None
        job.save()
        logger.info("Started research for job %s (interaction: %s)", job.id, interaction.id)
        return {"job_id": job.id, "status": "running", "interaction_id": interaction.id}
    except Exception:
        job.status = ResearchJob.Status.FAILED
        job.error = traceback.format_exc()
        job.completed_at = timezone.now()
        job.save()
        raise


def _poll_and_process(job: ResearchJob) -> dict[str, t.Any]:
    """Poll Gemini for job status and process if completed."""
    try:
        client = get_gemini_client()
        interaction = client.interactions.get(job.gemini_interaction_id)

        if interaction.status == "completed":
            _process_completed_job(job, interaction)
            logger.info("Completed job %s (created %d leads)", job.id, job.leads_created)
            return {"job_id": job.id, "status": "completed", "leads_created": job.leads_created}
        elif interaction.status in ("failed", "cancelled"):
            job.status = ResearchJob.Status.FAILED
            job.error = f"Gemini status: {interaction.status}"
            job.completed_at = timezone.now()
            job.save()
            return {"job_id": job.id, "status": "failed", "error": job.error}
        else:
            # Still running/pending - ensure our status reflects this
            if job.status != ResearchJob.Status.RUNNING:
                job.status = ResearchJob.Status.RUNNING
                job.save()
            return {"job_id": job.id, "status": interaction.status, "message": "Job not yet completed"}

    except Exception as e:
        logger.exception("Error processing job %s", job.id)
        job.status = ResearchJob.Status.FAILED
        job.error = str(e)
        job.completed_at = timezone.now()
        job.save()
        return {"job_id": job.id, "status": "error", "error": str(e)}


def reprocess_job(job_id: int) -> dict[str, t.Any]:
    """Re-process a job that has raw_result but failed during parsing.

    Use this to retry parsing/lead creation without re-running Gemini research.
    Useful when parsing failed due to malformed JSON or other transient issues.

    Args:
        job_id: The ID of the ResearchJob to reprocess

    Returns:
        Dict with job_id, status, and leads_created
    """
    job = ResearchJob.objects.get(id=job_id)

    if not job.raw_result:
        raise ValueError(f"Job {job_id} has no raw_result to reprocess")

    try:
        result = _parse_research_result(job.raw_result)
        job.result = result.model_dump()

        # Create leads
        leads_created = 0
        for lead_data in result.leads:
            lead = _create_lead_from_research(lead_data, job.city)
            if lead:
                leads_created += 1

        job.leads_created = leads_created
        job.status = ResearchJob.Status.COMPLETED
        job.error = ""
        job.completed_at = timezone.now()
        job.save()

        logger.info("Reprocessed job %s (created %d leads)", job.id, leads_created)
        return {"job_id": job.id, "status": "completed", "leads_created": leads_created}

    except Exception as e:
        logger.exception("Error reprocessing job %s", job.id)
        job.status = ResearchJob.Status.FAILED
        job.error = str(e)
        job.save()
        raise


@shared_task
def poll_research_jobs() -> dict[str, t.Any]:
    """Poll running research jobs and process completed ones.

    This task is not rate-limited since interactions.get() doesn't count
    against Gemini Deep Research rate limits.
    """
    running_jobs = ResearchJob.objects.filter(status=ResearchJob.Status.RUNNING)
    results: dict[str, t.Any] = {"processed": 0, "completed": 0, "failed": 0}

    if not running_jobs.exists():
        return results

    for job in running_jobs:
        results["processed"] += 1
        result = _poll_and_process(job)

        if result["status"] == "completed":
            results["completed"] += 1
        elif result["status"] in ("failed", "error"):
            results["failed"] += 1

    return results


def _process_completed_job(job: ResearchJob, interaction: t.Any) -> None:
    """Process a completed research job and create leads."""
    # Parse structured output
    text_output = interaction.outputs[-1].text
    if not text_output:
        raise ValueError("No text output in response")

    job.raw_result = text_output
    job.save()

    # Try to parse the response with fallback heuristics
    result = _parse_research_result(text_output)
    job.result = result.model_dump()

    # Create leads
    leads_created = 0
    for lead_data in result.leads:
        lead = _create_lead_from_research(lead_data, job.city)
        if lead:
            leads_created += 1

    job.leads_created = leads_created
    job.status = ResearchJob.Status.COMPLETED
    job.completed_at = timezone.now()
    job.save()


def _parse_with_gemini_fallback(text_output: str) -> ResearchResult:
    """Use Gemini to parse raw text into structured output.

    This is a fallback strategy when the response is not valid JSON.
    Uses gemini-2.0-flash-exp with structured output to extract leads from raw text.

    Args:
        text_output: The raw text response from Deep Research

    Returns:
        Parsed ResearchResult with extracted leads

    Raises:
        ValueError: If Gemini fails to parse or returns empty response
    """
    logger.info("Attempting to use Gemini to parse raw text response (length: %d chars)", len(text_output))

    client = get_gemini_client()
    prompt = FALLBACK_PARSING_PROMPT.format(text_output=text_output)

    response = client.models.generate_content(
        model=GEMINI_FALLBACK_MODEL,
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": ResearchResult,
        },
    )

    if not response.text:
        raise ValueError("Gemini returned empty response when parsing raw text")

    result = ResearchResult.model_validate_json(response.text)
    logger.info("Successfully parsed raw text using Gemini (found %d leads)", len(result.leads))
    return result


def _parse_research_result(text_output: str) -> ResearchResult:
    """Parse research result with fallback heuristics for malformed JSON.

    Gemini Deep Research sometimes returns the JSON schema alongside the actual data,
    or even raw text instead of JSON. We try multiple parsing strategies:
    1. Parse entire response as-is (JSON)
    2. Extract from "leads": [ onwards and wrap in {} (malformed JSON)
    3. Use regular Gemini model to parse raw text into structured output (fallback)
    """
    # Strategy 1: Try parsing the entire response as-is
    try:
        return ResearchResult.model_validate_json(text_output)
    except Exception as e:
        logger.warning("Failed to parse response as direct JSON: %s", e)

    # Strategy 2: Extract from "leads": [ onwards
    json_str = None
    try:
        leads_marker = '"leads": ['
        idx = text_output.find(leads_marker)
        if idx == -1:
            raise ValueError("Could not find '\"leads\": [' in response")

        # Extract from marker onwards
        extracted = text_output[idx:]

        # Strip trailing code fence markers (```)
        extracted = extracted.rstrip()
        if extracted.endswith("```"):
            extracted = extracted[:-3].rstrip()

        # Wrap in {} to make valid JSON
        json_str = "{" + extracted + "}"

        logger.info("Attempting to parse extracted JSON (from char %d, length: %d)", idx, len(json_str))
        return ResearchResult.model_validate_json(json_str)
    except Exception as e:
        logger.warning("Failed to parse extracted JSON: %s", e)
        if json_str:
            logger.warning("Extracted content preview: %s", json_str[:500])

    # Strategy 3: Use regular Gemini model to parse raw text
    try:
        return _parse_with_gemini_fallback(text_output)
    except Exception as e:
        logger.error("Failed to parse raw text using Gemini: %s", e)
        logger.error("Raw text preview (first 1000 chars): %s", text_output[:1000])
        raise ValueError(f"Could not parse research result after trying all strategies: {e}") from e


CONTACT_METHOD_FIELDS: tuple[str, ...] = ("email", "phone", "instagram", "telegram", "website")


_TEMP_MAP = {
    Temperature.cold: Lead.Temperature.COLD,
    Temperature.warm: Lead.Temperature.WARM,
    Temperature.hot: Lead.Temperature.HOT,
}


def _resolve_lead_type(name: str) -> LeadType | None:
    if not name:
        return None
    lt, _ = LeadType.objects.get_or_create(name__iexact=name, defaults={"name": name})
    return lt


def _merge_lead_fields(lead: Lead, data: ResearchLead, lead_type: LeadType | None) -> None:
    """Fill blanks on the existing lead from research data (dual-write to Lead.*)."""
    for field in ("company", "notes", *CONTACT_METHOD_FIELDS):
        if not getattr(lead, field) and getattr(data, field):
            setattr(lead, field, getattr(data, field))
    if not lead.lead_type and lead_type:
        lead.lead_type = lead_type
    lead.save()


def _merge_contact_fields(contact: Contact, data: ResearchLead) -> None:
    """Fill blanks on the matched contact from research data."""
    for field in CONTACT_METHOD_FIELDS:
        if not getattr(contact, field) and getattr(data, field):
            setattr(contact, field, getattr(data, field))
    contact.save()


def _create_lead_with_primary(data: ResearchLead, city: City, lead_type: LeadType | None) -> Lead:
    """Create a fresh lead + its primary contact mirroring the contact fields."""
    lead = Lead.objects.create(
        name=data.name,
        company=data.company,
        email=data.email,
        phone=data.phone,
        instagram=data.instagram,
        telegram=data.telegram,
        website=data.website,
        notes=data.notes,
        city=city,
        lead_type=lead_type,
        temperature=_TEMP_MAP.get(data.temperature, Lead.Temperature.COLD),
        source="Gemini Deep Research",
    )
    Contact.objects.create(
        lead=lead,
        name="Primary",
        is_primary=True,
        email=data.email,
        phone=data.phone,
        telegram=data.telegram,
        instagram=data.instagram,
        website=data.website,
    )
    return lead


def _create_lead_from_research(data: ResearchLead, city: City) -> Lead | None:
    """Create or update a lead from research data.

    Dedup + merge behavior:
    - If a lead is found by matching contact method (any of email/phone/ig/tg/web),
      we fill blanks on the lead AND on the matched Contact.
    - If a lead is found by name+city but the incoming contact method is new,
      we add a NEW non-primary Contact to capture it (multi-contact merge).
    """
    matched_contact, lead = _find_existing_lead_with_contact(data, city)
    lead_type = _resolve_lead_type(data.lead_type)

    if lead:
        _merge_lead_fields(lead, data, lead_type)
        if matched_contact:
            _merge_contact_fields(matched_contact, data)
        else:
            _attach_secondary_contact(lead, data)
    else:
        lead = _create_lead_with_primary(data, city, lead_type)

    for tag_name in data.tags:
        tag, _ = Tag.objects.get_or_create(name__iexact=tag_name, defaults={"name": tag_name})
        lead.tags.add(tag)

    return lead


def _attach_secondary_contact(lead: Lead, data: ResearchLead) -> None:
    """Attach a new non-primary Contact to the lead if any contact method is present."""
    if not any(getattr(data, f) for f in CONTACT_METHOD_FIELDS):
        return
    Contact.objects.create(
        lead=lead,
        name=data.name or "Contact",
        is_primary=False,
        email=data.email,
        phone=data.phone,
        telegram=data.telegram,
        instagram=data.instagram,
        website=data.website,
    )


def _find_existing_lead_with_contact(data: ResearchLead, city: City) -> tuple[Contact | None, Lead | None]:
    """Find an existing lead by Contact match, falling back to name+city.

    Returns:
        Tuple of (matched_contact, lead). matched_contact is the Contact whose
        method matched, or None if only name+city matched (caller should decide
        whether to add a secondary Contact).
    """
    for field in CONTACT_METHOD_FIELDS:
        value = getattr(data, field)
        if not value:
            continue
        contact = Contact.objects.select_related("lead").filter(**{f"{field}__iexact": value}).first()
        if contact:
            return contact, contact.lead
        # Dual-write fallback: also check legacy Lead.* columns until Phase 4.
        lead_match = Lead.objects.filter(**{f"{field}__iexact": value}).first()
        if lead_match:
            return None, lead_match

    lead = Lead.objects.filter(name__iexact=data.name, city=city).first()
    return None, lead


def _find_existing_lead(data: ResearchLead, city: City) -> Lead | None:
    """Find existing lead by contact method or name+city (thin wrapper for callers)."""
    _, lead = _find_existing_lead_with_contact(data, city)
    return lead


# --- Email Tasks ---


@shared_task
def send_email_task(
    lead_id: int,
    subject: str,
    body: str,
    to: list[str],
    bcc: list[str] | None = None,
    template_id: int | None = None,
    user_id: int | None = None,
    contact_id: int | None = None,
) -> dict[str, t.Any]:
    """Send an email to a lead in the background.

    Args:
        lead_id: The ID of the Lead
        subject: Rendered subject line
        body: Rendered email body
        to: List of recipient email addresses
        bcc: Optional list of BCC email addresses
        template_id: Optional EmailTemplate ID (for reference)
        user_id: Optional User ID (for Gmail OAuth credential lookup)
        contact_id: Optional Contact ID (for EmailSent.contact reference)

    Returns:
        Dict with email_sent_id and status
    """
    from django.contrib.auth.models import User

    from leads.models import EmailTemplate
    from leads.service import send_email_to_lead

    lead = Lead.objects.get(id=lead_id)
    template = EmailTemplate.objects.get(id=template_id) if template_id else None
    user = User.objects.filter(id=user_id).first() if user_id else None
    contact = Contact.objects.filter(id=contact_id, lead=lead).first() if contact_id else None

    try:
        email_sent = send_email_to_lead(
            lead=lead,
            subject=subject,
            body=body,
            to=to,
            bcc=bcc,
            template=template,
            user=user,
            contact=contact,
        )
        return {"email_sent_id": email_sent.id, "status": "sent"}
    except ValueError as e:
        # Placeholder validation error
        logger.error("Email validation error for lead %s: %s", lead_id, e)
        return {"email_sent_id": None, "status": "failed", "error": str(e)}
    except Exception as e:
        logger.exception("Error sending email to lead %s", lead_id)
        return {"email_sent_id": None, "status": "failed", "error": str(e)}
