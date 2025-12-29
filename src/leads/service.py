"""Business logic for leads."""

import re
import typing as t

from django.conf import settings
from django.core.mail import EmailMessage
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja.errors import HttpError

from leads.models import Action, City, EmailDraft, EmailSent, EmailTemplate, Lead, LeadType, ResearchJob, Tag
from leads.schema import (
    ActionIn,
    ActionPatch,
    CityIn,
    EmailDraftIn,
    EmailDraftPatch,
    EmailTemplateIn,
    EmailTemplatePatch,
    LeadIn,
    LeadPatch,
    ResearchJobIn,
    SendEmailIn,
)


def get_or_create_city(city_in: CityIn) -> City:
    """Get or create a city."""
    city, _ = City.objects.get_or_create(
        name__iexact=city_in.name,
        country__iexact=city_in.country,
        defaults={"name": city_in.name, "country": city_in.country, "iso2": city_in.iso2.upper()},
    )
    return city


def get_or_create_lead_type(name: str) -> LeadType:
    """Get or create a lead type."""
    lead_type, _ = LeadType.objects.get_or_create(name__iexact=name, defaults={"name": name})
    return lead_type


def get_or_create_tags(tag_names: list[str]) -> list[Tag]:
    """Get or create tags (case insensitive)."""
    tags = []
    for name in tag_names:
        tag, _ = Tag.objects.get_or_create(name__iexact=name, defaults={"name": name})
        tags.append(tag)
    return tags


def apply_lead_data(lead: Lead, data: LeadIn | LeadPatch, is_patch: bool = False) -> Lead:
    """Apply data to a lead, handling related objects."""
    exclude = {"city", "lead_type", "tags"}
    data_dict = data.model_dump(exclude=exclude, exclude_unset=is_patch)

    for field, value in data_dict.items():
        setattr(lead, field, value)

    # Handle city
    if data.city is not None:
        lead.city = get_or_create_city(data.city)
    elif not is_patch:
        lead.city = None

    # Handle lead_type
    if data.lead_type is not None:
        lead.lead_type = get_or_create_lead_type(data.lead_type)
    elif not is_patch:
        lead.lead_type = None

    lead.save()

    # Handle tags
    if data.tags is not None:
        tags = get_or_create_tags(data.tags)
        lead.tags.set(tags)
    elif not is_patch:
        lead.tags.clear()

    return lead


def create_lead(data: LeadIn) -> Lead:
    """Create a new lead."""
    lead = Lead()
    return apply_lead_data(lead, data)


def update_lead(lead: Lead, data: LeadIn) -> Lead:
    """Update a lead (full replacement)."""
    return apply_lead_data(lead, data)


def patch_lead(lead: Lead, data: LeadPatch) -> Lead:
    """Partially update a lead."""
    return apply_lead_data(lead, data, is_patch=True)


# --- Action Functions ---


def create_action(data: ActionIn) -> Action:
    """Create a new action.

    Actions always start with PENDING status.

    Raises:
        HttpError: 404 if the lead doesn't exist.
    """
    try:
        lead = Lead.objects.get(id=data.lead_id)
    except Lead.DoesNotExist:
        raise HttpError(404, f"Lead with id {data.lead_id} not found")
    return Action.objects.create(
        lead=lead,
        name=data.name,
        notes=data.notes,
        due_date=data.due_date,
    )


def update_action(action: Action, data: ActionIn) -> Action:
    """Update an action (full replacement).

    Note: status is not changed via PUT. Use PATCH to update status.

    Raises:
        HttpError: 404 if the lead doesn't exist.
    """
    # Validate lead exists
    if not Lead.objects.filter(id=data.lead_id).exists():
        raise HttpError(404, f"Lead with id {data.lead_id} not found")
    action.lead_id = data.lead_id
    action.name = data.name
    action.notes = data.notes
    action.due_date = data.due_date
    action.save()
    return action


def patch_action(action: Action, data: ActionPatch) -> Action:
    """Partially update an action."""
    data_dict = data.model_dump(exclude_unset=True)
    for field, value in data_dict.items():
        setattr(action, field, value)
    _handle_action_completion(action)
    action.save()
    return action


def _handle_action_completion(action: Action) -> None:
    """Set completed_at when status changes to COMPLETED."""
    if action.status == Action.Status.COMPLETED and action.completed_at is None:
        action.completed_at = timezone.now()
    elif action.status != Action.Status.COMPLETED:
        action.completed_at = None


# --- City Functions ---


def create_city(data: CityIn) -> City:
    """Create a new city.

    Raises:
        HttpError: 400 if a city with the same name and country already exists.
    """
    if City.objects.filter(name__iexact=data.name, country__iexact=data.country).exists():
        raise HttpError(400, f"City '{data.name}' in '{data.country}' already exists")
    return City.objects.create(
        name=data.name,
        country=data.country,
        iso2=data.iso2.upper() if data.iso2 else "",
    )


def start_city_research(city: City) -> dict[str, t.Any]:
    """Start deep research for a city.

    Creates a ResearchJob and queues it for processing via Celery.

    Returns:
        Dict with job_id and status.

    Raises:
        ValueError: If there's already an active research job for this city.
    """
    from leads.tasks import queue_research

    return queue_research(city.id)


# --- ResearchJob Functions ---


def create_research_job(data: ResearchJobIn) -> ResearchJob:
    """Create a new research job for a city.

    The job is created with NOT_STARTED status. Use run_research_job() to start it.

    Raises:
        HttpError: 404 if the city doesn't exist.
        HttpError: 400 if there's already an active research job for this city.
    """
    try:
        city = City.objects.get(id=data.city_id)
    except City.DoesNotExist:
        raise HttpError(404, f"City with id {data.city_id} not found")

    # Check for existing active jobs
    active_statuses = [ResearchJob.Status.PENDING, ResearchJob.Status.RUNNING]
    if ResearchJob.objects.filter(city=city, status__in=active_statuses).exists():
        raise HttpError(400, f"Research already active for {city}")

    return ResearchJob.objects.create(city=city, status=ResearchJob.Status.NOT_STARTED)


def run_research_job(job: ResearchJob) -> dict[str, t.Any]:
    """Queue a research job for processing.

    Sets the job to PENDING and queues the start_research_job task.

    Returns:
        Dict with job_id, status, and message.

    Raises:
        HttpError: 400 if the job is not in a runnable state.
    """
    from leads.tasks import start_research_job

    allowed_statuses = {ResearchJob.Status.NOT_STARTED, ResearchJob.Status.FAILED}
    if job.status not in allowed_statuses:
        raise HttpError(400, f"Job #{job.id} is {job.get_status_display()}, cannot run")

    # Set to PENDING and clear interaction_id before queuing
    job.status = ResearchJob.Status.PENDING
    job.gemini_interaction_id = ""
    job.error = ""
    job.save()

    start_research_job.delay(job.id)

    return {"job_id": job.id, "status": "queued", "message": f"Job #{job.id} queued for processing"}


def reprocess_research_job(job: ResearchJob) -> dict[str, t.Any]:
    """Reprocess a research job that has raw_result.

    This retries parsing/lead creation without re-running Gemini research.

    Returns:
        Dict with job_id, status, leads_created, and message.

    Raises:
        HttpError: 400 if the job has no raw_result to reprocess.
    """
    from leads.tasks import reprocess_job

    if not job.raw_result:
        raise HttpError(400, f"Job #{job.id} has no raw_result to reprocess")

    result = reprocess_job(job.id)
    return {
        "job_id": job.id,
        "status": "completed",
        "leads_created": result["leads_created"],
        "message": f"Reprocessed job #{job.id}, created {result['leads_created']} leads",
    }


# --- Email Functions ---

# Regex to find unreplaced placeholders like {something}
PLACEHOLDER_PATTERN = re.compile(r"\{[^}]+\}")


def render_email_template(template: EmailTemplate, lead: Lead) -> tuple[str, str]:
    """Render an email template with lead data.

    Available placeholders:
    - {lead.name}, {lead.email}, {lead.phone}, {lead.company}
    - {lead.city}, {lead.lead_type}
    - {lead.instagram}, {lead.telegram}, {lead.website}

    Args:
        template: The EmailTemplate to render
        lead: The Lead to use for placeholder values

    Returns:
        Tuple of (rendered_subject, rendered_body)
    """
    context = {
        "lead.name": lead.name or "",
        "lead.email": lead.email or "",
        "lead.phone": lead.phone or "",
        "lead.company": lead.company or "",
        "lead.city": str(lead.city) if lead.city else "",
        "lead.lead_type": str(lead.lead_type) if lead.lead_type else "",
        "lead.instagram": lead.instagram or "",
        "lead.telegram": lead.telegram or "",
        "lead.website": lead.website or "",
    }

    subject = template.subject
    body = template.body

    for placeholder, value in context.items():
        subject = subject.replace("{" + placeholder + "}", value)
        body = body.replace("{" + placeholder + "}", value)

    return subject, body


def validate_no_placeholders(subject: str, body: str) -> list[str]:
    """Check if there are any unreplaced placeholders in subject or body.

    Returns:
        List of unreplaced placeholders found, empty if none.
    """
    placeholders = []
    placeholders.extend(PLACEHOLDER_PATTERN.findall(subject))
    placeholders.extend(PLACEHOLDER_PATTERN.findall(body))
    return placeholders


def send_email_to_lead(
    lead: Lead,
    subject: str,
    body: str,
    to: list[str],
    bcc: list[str] | None = None,
    template: EmailTemplate | None = None,
) -> EmailSent:
    """Send an email to a lead and log it.

    Args:
        lead: The Lead receiving the email
        subject: Rendered subject line
        body: Rendered email body
        to: List of recipient email addresses
        bcc: Optional list of BCC email addresses
        template: Optional EmailTemplate used (for reference)

    Returns:
        EmailSent record

    Raises:
        ValueError: If there are unreplaced placeholders in subject or body
    """
    # Validate no placeholders remain
    unreplaced = validate_no_placeholders(subject, body)
    if unreplaced:
        raise ValueError(f"Unreplaced placeholders found: {', '.join(unreplaced)}")

    from_email = settings.DEFAULT_FROM_EMAIL
    bcc = bcc or []

    # Create the EmailSent record first with PENDING status
    email_sent = EmailSent.objects.create(
        lead=lead,
        template=template,
        from_email=from_email,
        to=to,
        bcc=bcc,
        subject=subject,
        body=body,
        status=EmailSent.Status.PENDING,
    )

    try:
        email = EmailMessage(
            subject=subject,
            body=body,
            from_email=from_email,
            to=to,
            bcc=bcc if bcc else None,
        )
        email.send(fail_silently=False)

        email_sent.status = EmailSent.Status.SENT
        email_sent.sent_at = timezone.now()
        email_sent.save()

        # Update lead's last_contact
        lead.last_contact = timezone.now().date()
        lead.save(update_fields=["last_contact"])

    except Exception as e:
        email_sent.status = EmailSent.Status.FAILED
        email_sent.error_message = str(e)
        email_sent.save()
        raise

    return email_sent


def create_email_template(data: EmailTemplateIn) -> EmailTemplate:
    """Create a new email template."""
    return EmailTemplate.objects.create(**data.model_dump())


def update_email_template(template: EmailTemplate, data: EmailTemplateIn) -> EmailTemplate:
    """Update an email template (full replacement)."""
    for field, value in data.model_dump().items():
        setattr(template, field, value)
    template.save()
    return template


def patch_email_template(template: EmailTemplate, data: EmailTemplatePatch) -> EmailTemplate:
    """Partially update an email template."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(template, field, value)
    template.save()
    return template


def send_email_to_lead_api(lead: Lead, data: SendEmailIn) -> dict[str, t.Any]:
    """Send an email to a lead via API.

    This is the API wrapper that handles template rendering and background sending.

    Args:
        lead: The Lead to send email to
        data: SendEmailIn with template_id or subject/body

    Returns:
        Dict with email_id, status, and message

    Raises:
        HttpError: 400 if validation fails
    """
    from leads.tasks import send_email_task

    template: EmailTemplate | None = None

    # Determine subject and body
    if data.template_id:
        try:
            template = EmailTemplate.objects.get(id=data.template_id)
        except EmailTemplate.DoesNotExist:
            raise HttpError(404, f"Template with id {data.template_id} not found")
        subject, body = render_email_template(template, lead)
    elif data.subject and data.body:
        subject = data.subject
        body = data.body
    else:
        raise HttpError(400, "Either template_id or both subject and body are required")

    # Determine recipients
    to = data.to if data.to else [lead.email] if lead.email else []
    if not to:
        raise HttpError(400, "No recipient email address (lead has no email and 'to' not provided)")

    bcc = data.bcc or []

    # Send in background or synchronously
    if data.send_in_background:
        send_email_task.delay(
            lead_id=lead.id,
            subject=subject,
            body=body,
            to=to,
            bcc=bcc,
            template_id=template.id if template else None,
        )
        return {
            "email_id": 0,  # Not yet created
            "status": EmailSent.Status.PENDING,
            "message": "Email queued for background sending",
        }
    else:
        email_sent = send_email_to_lead(
            lead=lead,
            subject=subject,
            body=body,
            to=to,
            bcc=bcc,
            template=template,
        )
        return {
            "email_id": email_sent.id,
            "status": email_sent.status,
            "message": "Email sent successfully",
        }


# --- Email Draft Functions ---


def create_email_draft(data: EmailDraftIn) -> EmailDraft:
    """Create a new email draft.

    Args:
        data: EmailDraftIn with lead_id, subject, body, etc.

    Returns:
        EmailDraft record

    Raises:
        Http404: If lead or template not found
    """
    lead = get_object_or_404(Lead, pk=data.lead_id)
    template = get_object_or_404(EmailTemplate, pk=data.template_id) if data.template_id else None

    return EmailDraft.objects.create(
        lead=lead,
        template=template,
        from_email=data.from_email or settings.DEFAULT_FROM_EMAIL,
        to=data.to or ([lead.email] if lead.email else []),
        bcc=data.bcc,
        subject=data.subject,
        body=data.body,
    )


def update_email_draft(draft: EmailDraft, data: EmailDraftIn) -> EmailDraft:
    """Update an email draft (full replacement).

    Args:
        draft: The EmailDraft to update
        data: EmailDraftIn with new values

    Returns:
        Updated EmailDraft record

    Raises:
        Http404: If lead or template not found
    """
    draft.lead = get_object_or_404(Lead, pk=data.lead_id)
    draft.template = get_object_or_404(EmailTemplate, pk=data.template_id) if data.template_id else None
    draft.from_email = data.from_email or settings.DEFAULT_FROM_EMAIL
    draft.to = data.to or ([draft.lead.email] if draft.lead.email else [])
    draft.bcc = data.bcc
    draft.subject = data.subject
    draft.body = data.body
    draft.save()
    return draft


def patch_email_draft(draft: EmailDraft, data: EmailDraftPatch) -> EmailDraft:
    """Partially update an email draft.

    Args:
        draft: The EmailDraft to update
        data: EmailDraftPatch with fields to update

    Returns:
        Updated EmailDraft record

    Raises:
        Http404: If template not found
    """
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "template_id":
            draft.template = get_object_or_404(EmailTemplate, pk=value) if value else None
        else:
            setattr(draft, field, value)
    draft.save()
    return draft


def send_email_draft(draft: EmailDraft) -> EmailSent:
    """Send an email draft.

    Creates EmailSent record, updates lead's last_contact, and deletes the draft.

    Args:
        draft: The EmailDraft to send

    Returns:
        EmailSent record

    Raises:
        ValueError: If there are unreplaced placeholders in subject or body
    """
    # Validate no placeholders remain
    errors = validate_no_placeholders(draft.subject, draft.body)
    if errors:
        raise ValueError(f"Unreplaced placeholders found: {', '.join(errors)}")

    # Send the email using existing function
    email_sent = send_email_to_lead(
        lead=draft.lead,
        subject=draft.subject,
        body=draft.body,
        to=draft.to,
        bcc=draft.bcc or None,
        template=draft.template,
    )

    # Delete the draft after successful send
    draft.delete()

    return email_sent


def save_email_as_draft(
    lead: Lead,
    subject: str,
    body: str,
    to: list[str],
    bcc: list[str] | None = None,
    template: EmailTemplate | None = None,
    draft_id: int | None = None,
) -> EmailDraft:
    """Save email form data as a draft.

    Called from admin send_email view when "Save as Draft" is clicked.
    If draft_id is provided, updates the existing draft instead of creating a new one.

    Args:
        lead: The Lead this draft is for
        subject: Email subject
        body: Email body
        to: List of recipient email addresses
        bcc: Optional list of BCC email addresses
        template: Optional EmailTemplate used
        draft_id: Optional ID of existing draft to update

    Returns:
        EmailDraft record (created or updated)

    Raises:
        Http404: If draft_id is invalid or belongs to a different lead
    """
    if draft_id:
        # Validate draft exists AND belongs to this lead
        draft = get_object_or_404(EmailDraft, pk=draft_id, lead=lead)
        draft.template = template
        draft.from_email = settings.DEFAULT_FROM_EMAIL
        draft.to = to
        draft.bcc = bcc or []
        draft.subject = subject
        draft.body = body
        draft.save()
        return draft

    return EmailDraft.objects.create(
        lead=lead,
        template=template,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to,
        bcc=bcc or [],
        subject=subject,
        body=body,
    )
