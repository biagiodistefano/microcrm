"""Business logic for leads."""

import logging
import re
import typing as t
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from ninja.errors import HttpError

from leads.models import (
    Action,
    City,
    Contact,
    EmailDraft,
    EmailSent,
    EmailTemplate,
    GmailConnection,
    Lead,
    LeadType,
    OutreachMessage,
    ResearchJob,
    Tag,
)
from leads.schema import (
    ActionIn,
    ActionPatch,
    CityIn,
    ContactIn,
    ContactPatch,
    EmailDraftIn,
    EmailDraftPatch,
    EmailTemplateIn,
    EmailTemplatePatch,
    LeadIn,
    LeadPatch,
    LeadUpsertIn,
    OutreachMessageIn,
    OutreachMessagePatch,
    ResearchJobIn,
    SendEmailIn,
)

CONTACT_FIELDS: tuple[str, ...] = ("email", "phone", "telegram", "instagram", "website")
# Lead-only identity fields (not mirrored on Contact) that also count as dedupe keys
LEAD_ONLY_MATCH_FIELDS: tuple[str, ...] = ("bluesky", "facebook", "linkedin", "whatsapp")
LEAD_MATCH_FIELDS: tuple[str, ...] = (*CONTACT_FIELDS, *LEAD_ONLY_MATCH_FIELDS)
# Fields filled on an existing lead only when currently blank
LEAD_FILL_FIELDS: tuple[str, ...] = ("company", "notes", "source", "external_ref", *LEAD_MATCH_FIELDS)
FOLLOW_UP_DAYS = 4

logger = logging.getLogger(__name__)


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


def get_or_create_primary_contact(lead: Lead) -> Contact:
    """Return the primary Contact for a lead, creating a default one if missing."""
    primary = lead.contacts.filter(is_primary=True).first()
    if primary:
        return primary
    return Contact.objects.create(lead=lead, name="Primary", is_primary=True)


def _apply_lead_relations(lead: Lead, data: LeadIn | LeadPatch, is_patch: bool) -> None:
    """Set city / lead_type FKs from input data."""
    if data.city is not None:
        lead.city = get_or_create_city(data.city)
    elif not is_patch:
        lead.city = None

    if data.lead_type is not None:
        lead.lead_type = get_or_create_lead_type(data.lead_type)
    elif not is_patch:
        lead.lead_type = None


def _dual_write_primary_contact(lead: Lead, contact_updates: dict[str, str], is_patch: bool) -> None:
    """Mirror contact-field writes onto the lead's primary contact.

    Phase 4 will remove the Lead-level columns; until then writes land in both places.
    PUT semantics reset any non-mentioned contact fields on the primary.
    """
    if not contact_updates and is_patch:
        return
    primary = get_or_create_primary_contact(lead)
    for field, value in contact_updates.items():
        setattr(primary, field, value)
    if not is_patch:
        for field in CONTACT_FIELDS:
            if field not in contact_updates:
                setattr(primary, field, "")
    primary.save()


def sync_do_not_contact_timestamp(lead: Lead) -> None:
    """Stamp do_not_contact_at when suppression is switched on; reset the reason/stamp when switched off."""
    if lead.do_not_contact and lead.do_not_contact_at is None:
        lead.do_not_contact_at = timezone.now()
    elif not lead.do_not_contact:
        lead.do_not_contact_at = None
        lead.do_not_contact_reason = ""


def refuse_if_do_not_contact(lead: Lead) -> None:
    """Raise a 400 if the lead is suppressed (no outreach may be created for it).

    Raises:
        HttpError: 400 when the lead is marked do not contact.
    """
    if lead.do_not_contact:
        raise HttpError(400, f"Lead '{lead.name}' is marked do not contact; no outreach can be created for it")


def suppress_outreach(lead: Lead) -> None:
    """Neutralise pending outreach for a do-not-contact lead.

    Draft DMs are marked skipped; unsent email drafts are deleted (their history rows are kept by
    simple_history, so the audit trail survives, and nothing unsendable lingers in anyone's queue).
    """
    if not lead.do_not_contact:
        return
    for message in lead.messages.filter(status=OutreachMessage.Status.DRAFT):
        message.status = OutreachMessage.Status.SKIPPED
        message.save(update_fields=["status", "updated_at"])
    for draft in lead.email_drafts.all():
        draft.delete()


def mark_do_not_contact(lead: Lead, reason: str = "") -> Lead:
    """Suppress a lead (idempotent; keeps an existing reason when none is given)."""
    lead.do_not_contact = True
    if reason:
        lead.do_not_contact_reason = reason
    sync_do_not_contact_timestamp(lead)
    lead.save(update_fields=["do_not_contact", "do_not_contact_reason", "do_not_contact_at", "updated_at"])
    suppress_outreach(lead)
    return lead


def _ensure_external_ref_free(lead: Lead, external_ref: str) -> None:
    """Raise 409 if another lead already owns this (non-empty) external_ref."""
    if external_ref and Lead.objects.filter(external_ref=external_ref).exclude(pk=lead.pk).exists():
        raise HttpError(409, f"A lead with external_ref '{external_ref}' already exists")


def apply_lead_data(lead: Lead, data: LeadIn | LeadPatch, is_patch: bool = False) -> Lead:
    """Apply data to a lead, handling related objects and dual-writing contact fields."""
    exclude = {"city", "lead_type", "tags"}
    data_dict = data.model_dump(exclude=exclude, exclude_unset=is_patch)

    _ensure_external_ref_free(lead, data_dict.get("external_ref") or "")

    contact_updates: dict[str, str] = {}
    for field, value in data_dict.items():
        if value is None and field in ("ticketing_platforms", "do_not_contact", "do_not_contact_reason"):
            continue
        setattr(lead, field, value)
        if field in CONTACT_FIELDS:
            contact_updates[field] = value or ""

    _apply_lead_relations(lead, data, is_patch)
    sync_do_not_contact_timestamp(lead)
    lead.save()
    suppress_outreach(lead)

    _dual_write_primary_contact(lead, contact_updates, is_patch)

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


def bulk_update_lead_tags(lead_ids: list[int], add: list[str], remove: list[str]) -> int:
    """Add and/or remove tags (case-insensitive names) on many leads. Returns how many leads exist and were touched."""
    leads = list(Lead.objects.filter(id__in=lead_ids))
    to_add = get_or_create_tags(add)
    to_remove = [tag for name in remove for tag in Tag.objects.filter(name__iexact=name)]
    with transaction.atomic():
        for lead in leads:
            if to_add:
                lead.tags.add(*to_add)
            if to_remove:
                lead.tags.remove(*to_remove)
    return len(leads)


# --- Lead Upsert (dedupe + merge) ---


# Hosts shared by many organizations (link hubs, socials, ticketing, maps): never a dedupe key
AGGREGATOR_HOSTS: tuple[str, ...] = (
    "linktr.ee",
    "instagram.com",
    "facebook.com",
    "fb.com",
    "ra.co",
    "bsky.app",
    "linkedin.com",
    "tiktok.com",
    "beacons.ai",
    "lnk.bio",
    "carrd.co",
    "t.me",
    "wa.me",
    "maps.app.goo.gl",
)


def _is_aggregator(host: str, path: str) -> bool:
    """True for hosts/paths that identify a platform rather than one organization."""
    if any(host == h or host.endswith(f".{h}") for h in AGGREGATOR_HOSTS):
        return True
    if host.startswith("eventbrite.") or ".eventbrite." in host:
        return True
    is_google = host == "google.com" or host.startswith("google.") or host.endswith(".google.com")
    return is_google and (path.startswith("/maps") or host.startswith("maps."))


def normalize_website(url: str) -> str:
    """Normalise a website to ``host/path`` (no scheme, www, query or trailing slash; host lowercased).

    Returns "" for empty input and for aggregator/social hosts, which must never be used for matching.
    """
    url = url.strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url if "//" in url else f"//{url}")
        host = (parsed.hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""
    path = parsed.path.rstrip("/")
    if not host or _is_aggregator(host, path):
        return ""
    return f"{host}{path}"


def _match_exact(field: str, value: str) -> tuple[Contact | None, Lead] | None:
    """Case-insensitive exact match on a strong identity key (Contact rows first, then Lead columns).

    Strong keys are never vetoed by external_ref. If several leads match, a do-not-contact one wins so a
    suppressed lead can never be bypassed by a duplicate.
    """
    candidates: list[tuple[Contact | None, Lead]] = []
    if field in CONTACT_FIELDS:
        candidates += [
            (c, c.lead) for c in Contact.objects.select_related("lead").filter(**{f"{field}__iexact": value})
        ]
    candidates += [(None, lead) for lead in Lead.objects.filter(**{f"{field}__iexact": value})]
    return next((c for c in candidates if c[1].do_not_contact), candidates[0] if candidates else None)


def _match_website(value: str, ok: t.Callable[[Lead], bool]) -> tuple[Contact | None, Lead] | None:
    """Match on a normalised website; stored values are normalised on the fly."""
    norm = normalize_website(value)
    if not norm:
        return None
    host = norm.split("/")[0]
    for contact in Contact.objects.select_related("lead").filter(website__icontains=host):
        if normalize_website(contact.website) == norm and ok(contact.lead):
            return contact, contact.lead
    for lead in Lead.objects.filter(website__icontains=host):
        if normalize_website(lead.website) == norm and ok(lead):
            return None, lead
    return None


def find_existing_lead(data: LeadUpsertIn, city: City | None) -> tuple[Contact | None, Lead | None, str | None]:
    """Find the lead an incoming record refers to.

    Order: external_ref, then each contact method / handle, then name+city. Exact matches on strong keys
    (email, phone, instagram, telegram, bluesky, facebook, linkedin, whatsapp) win regardless of external_ref,
    since the same organisation found on another source arrives with a different ref. Only the weak keys
    (website, name+city) are vetoed by a different non-empty external_ref on the candidate.

    Returns:
        Tuple of (matched_contact, lead, matched_on). matched_contact is set only on a Contact-row match;
        matched_on is the key name ("external_ref", "email", ..., "name_city"), or None if nothing matched.
    """
    ref = data.external_ref

    def ok(lead: Lead) -> bool:
        return not (ref and lead.external_ref and lead.external_ref != ref)

    if ref:
        lead = Lead.objects.filter(external_ref=ref).first()
        if lead:
            return None, lead, "external_ref"

    for field in LEAD_MATCH_FIELDS:
        value = (getattr(data, field) or "").strip()
        if not value:
            continue
        found = _match_website(value, ok) if field == "website" else _match_exact(field, value)
        if found:
            return found[0], found[1], field

    if city is not None:
        for candidate in Lead.objects.filter(name__iexact=data.name, city=city):
            if ok(candidate):
                return None, candidate, "name_city"
    return None, None, None


def _merge_contact_methods(lead: Lead, matched: Contact | None, data: LeadUpsertIn) -> None:
    """Mirror incoming contact methods onto Contacts: fill blanks, or add a secondary for a new method."""
    incoming = {f: getattr(data, f) for f in CONTACT_FIELDS if getattr(data, f)}
    if not incoming:
        return
    target = matched or lead.contacts.filter(is_primary=True).first()
    shares = target is not None and any(
        (getattr(target, f) or "").strip().lower() == v.strip().lower() for f, v in incoming.items()
    )
    if target is None:
        Contact.objects.create(lead=lead, name="Primary", is_primary=True, **incoming)
    elif matched is not None or shares:
        for field, value in incoming.items():
            if not getattr(target, field):
                setattr(target, field, value)
        target.save()
    else:
        Contact.objects.create(lead=lead, name=data.name or "Contact", is_primary=False, **incoming)


def merge_lead_data(lead: Lead, matched: Contact | None, data: LeadUpsertIn) -> Lead:
    """Merge incoming data into an existing lead.

    Only blank fields are filled; status, temperature, value and last_contact are never touched.
    Tags and ticketing platforms are merged additively.
    """
    for field in LEAD_FILL_FIELDS:
        incoming = getattr(data, field)
        if incoming and not getattr(lead, field):
            if field == "external_ref":
                _ensure_external_ref_free(lead, incoming)
            setattr(lead, field, incoming)
    if data.do_not_contact:
        lead.do_not_contact = True
        lead.do_not_contact_reason = lead.do_not_contact_reason or data.do_not_contact_reason
        sync_do_not_contact_timestamp(lead)
    if data.lead_type and not lead.lead_type:
        lead.lead_type = get_or_create_lead_type(data.lead_type)
    if data.city and not lead.city:
        lead.city = get_or_create_city(data.city)
    lead.ticketing_platforms = list(dict.fromkeys([*(lead.ticketing_platforms or []), *data.ticketing_platforms]))
    lead.save()
    suppress_outreach(lead)

    _merge_contact_methods(lead, matched, data)
    if data.tags:
        lead.tags.add(*get_or_create_tags(data.tags))
    return lead


def upsert_lead(data: LeadUpsertIn) -> tuple[Lead, bool, str | None]:
    """Create a lead or merge into the existing one it matches (idempotent for a given external_ref).

    Returns:
        Tuple of (lead, created, matched_on); matched_on is None when a lead was created.
    """
    city = get_or_create_city(data.city) if data.city else None
    matched, lead, matched_on = find_existing_lead(data, city)
    if lead is None:
        try:
            with transaction.atomic():
                return create_lead(data), True, None
        except IntegrityError:
            # Lost a race on external_ref: the other writer's lead is now the match
            matched, lead, matched_on = find_existing_lead(data, city)
            if lead is None:
                raise
    if lead.do_not_contact:
        return lead, False, matched_on  # suppressed: change nothing
    return merge_lead_data(lead, matched, data), False, matched_on


# --- Contact Functions ---


def _demote_existing_primary(lead: Lead, exclude_pk: int | None = None) -> None:
    """Clear is_primary on any existing primary contact for a lead (except exclude_pk)."""
    qs = lead.contacts.filter(is_primary=True)
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    qs.update(is_primary=False)


def create_contact(data: ContactIn) -> Contact:
    """Create a new contact for a lead.

    If is_primary=True, any existing primary contact is demoted atomically.

    Raises:
        HttpError: 404 if the lead doesn't exist.
    """
    try:
        lead = Lead.objects.get(id=data.lead_id)
    except Lead.DoesNotExist:
        raise HttpError(404, f"Lead with id {data.lead_id} not found")

    fields = data.model_dump(exclude={"lead_id"})
    with transaction.atomic():
        if data.is_primary:
            _demote_existing_primary(lead)
        return Contact.objects.create(lead=lead, **fields)


def update_contact(contact: Contact, data: ContactIn) -> Contact:
    """Update a contact (full replacement).

    Changing lead_id moves the contact to a different lead.
    """
    if contact.lead_id != data.lead_id:
        if not Lead.objects.filter(id=data.lead_id).exists():
            raise HttpError(404, f"Lead with id {data.lead_id} not found")
        contact.lead_id = data.lead_id

    fields = data.model_dump(exclude={"lead_id"})
    with transaction.atomic():
        if data.is_primary:
            _demote_existing_primary(contact.lead, exclude_pk=contact.pk)
        for field, value in fields.items():
            setattr(contact, field, value)
        contact.save()
    return contact


def patch_contact(contact: Contact, data: ContactPatch) -> Contact:
    """Partially update a contact."""
    updates = data.model_dump(exclude_unset=True)
    with transaction.atomic():
        if updates.get("is_primary"):
            _demote_existing_primary(contact.lead, exclude_pk=contact.pk)
        for field, value in updates.items():
            setattr(contact, field, value)
        contact.save()
    return contact


def set_primary_contact(contact: Contact) -> Contact:
    """Mark a contact as primary for its lead, demoting any existing primary."""
    with transaction.atomic():
        _demote_existing_primary(contact.lead, exclude_pk=contact.pk)
        if not contact.is_primary:
            contact.is_primary = True
            contact.save(update_fields=["is_primary", "updated_at"])
    return contact


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


def render_email_template(template: EmailTemplate, lead: Lead, contact: Contact | None = None) -> tuple[str, str]:
    """Render an email template with lead + contact data.

    Available placeholders:
    - {lead.name}, {lead.email}, {lead.phone}, {lead.company}
    - {lead.city}, {lead.lead_type}
    - {lead.instagram}, {lead.telegram}, {lead.website}

    Contact-scoped placeholders resolve from the given contact (or the lead's
    primary contact if not provided), falling back to the Lead-level field.

    Args:
        template: The EmailTemplate to render
        lead: The Lead to use for placeholder values
        contact: Optional Contact to prefer over the primary contact

    Returns:
        Tuple of (rendered_subject, rendered_body)
    """
    resolved_contact = contact or lead.contacts.filter(is_primary=True).first()

    def field(attr: str) -> str:
        if resolved_contact is not None:
            value = getattr(resolved_contact, attr, "")
            if value:
                return str(value)
        return str(getattr(lead, attr, "") or "")

    context = {
        "lead.name": lead.name or "",
        "lead.email": field("email"),
        "lead.phone": field("phone"),
        "lead.company": lead.company or "",
        "lead.city": str(lead.city) if lead.city else "",
        "lead.lead_type": str(lead.lead_type) if lead.lead_type else "",
        "lead.instagram": field("instagram"),
        "lead.telegram": field("telegram"),
        "lead.website": field("website"),
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


def _get_gmail_connection(user: User | None) -> GmailConnection | None:
    """Get active Gmail connection for a user, if any."""
    if not isinstance(user, User):
        return None
    try:
        return GmailConnection.objects.get(user=user, is_active=True)
    except GmailConnection.DoesNotExist:
        return None


def _resolve_from_email(gmail_connection: GmailConnection | None) -> str:
    """Determine the from_email based on Gmail connection status."""
    if gmail_connection:
        user = gmail_connection.user
        full_name = user.get_full_name()
        if full_name:
            return f"{full_name} <{gmail_connection.email}>"
        return gmail_connection.email
    return str(settings.DEFAULT_FROM_EMAIL)


def _send_via_smtp(
    subject: str,
    body: str,
    from_email: str,
    to: list[str],
    bcc: list[str] | None,
) -> None:
    """Send an email via Django's SMTP backend."""
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=from_email,
        to=to,
        bcc=bcc if bcc else None,
    )
    email.content_subtype = "html"
    email.send(fail_silently=False)


def send_email_to_lead(  # noqa: PLR0913
    lead: Lead,
    subject: str,
    body: str,
    to: list[str],
    bcc: list[str] | None = None,
    template: EmailTemplate | None = None,
    user: User | None = None,
    contact: Contact | None = None,
) -> EmailSent:
    """Send an email to a lead and log it.

    If user is provided, requires an active Gmail connection — no SMTP fallback.
    If user is None (API/CLI), sends via SMTP.

    Args:
        lead: The Lead receiving the email
        subject: Rendered subject line
        body: Rendered email body
        to: List of recipient email addresses
        bcc: Optional list of BCC email addresses
        template: Optional EmailTemplate used (for reference)
        user: Optional User who initiated the send (for Gmail OAuth lookup)
        contact: Optional Contact this email is addressed to (recorded on EmailSent)

    Returns:
        EmailSent record

    Raises:
        ValueError: If there are unreplaced placeholders or user has no Gmail connection
    """
    from leads.gmail import get_gmail_credentials, send_email_via_gmail

    refuse_if_do_not_contact(lead)

    # Validate no placeholders remain
    unreplaced = validate_no_placeholders(subject, body)
    if unreplaced:
        raise ValueError(f"Unreplaced placeholders found: {', '.join(unreplaced)}")

    gmail_connection = _get_gmail_connection(user)

    # Require Gmail connection when sending as a user
    if user and isinstance(user, User) and not gmail_connection:
        raise ValueError("Gmail account not connected. Please connect your Gmail before sending emails.")

    from_email = _resolve_from_email(gmail_connection)
    bcc = bcc or []

    # Create the EmailSent record first with PENDING status
    email_sent = EmailSent.objects.create(
        lead=lead,
        contact=contact,
        template=template,
        from_email=from_email,
        to=to,
        bcc=bcc,
        subject=subject,
        body=body,
        status=EmailSent.Status.PENDING,
        sent_by=user if isinstance(user, User) else None,
    )

    try:
        if gmail_connection:
            credentials = get_gmail_credentials(gmail_connection)
            send_email_via_gmail(credentials, from_email, to, subject, body, bcc or None)
        else:
            _send_via_smtp(subject, body, from_email, to, bcc)

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


def _resolve_send_contact(lead: Lead, contact_id: int | None) -> Contact | None:
    """Resolve the contact to use when sending: explicit id > primary > None."""
    if contact_id:
        try:
            return Contact.objects.get(id=contact_id, lead=lead)
        except Contact.DoesNotExist:
            raise HttpError(404, f"Contact with id {contact_id} not found for this lead")
    return lead.contacts.filter(is_primary=True).first()


def _resolve_send_subject_body(
    lead: Lead, data: SendEmailIn, contact: Contact | None
) -> tuple[EmailTemplate | None, str, str]:
    """Resolve the (template, subject, body) tuple for an outgoing email."""
    if data.template_id:
        try:
            template = EmailTemplate.objects.get(id=data.template_id)
        except EmailTemplate.DoesNotExist:
            raise HttpError(404, f"Template with id {data.template_id} not found")
        subject, body = render_email_template(template, lead, contact=contact)
        return template, subject, body
    if data.subject and data.body:
        return None, data.subject, data.body
    raise HttpError(400, "Either template_id or both subject and body are required")


def _resolve_send_recipients(explicit_to: list[str] | None, contact: Contact | None, lead: Lead) -> list[str]:
    """Determine recipient list: explicit > contact email > lead email."""
    if explicit_to:
        return explicit_to
    if contact and contact.email:
        return [contact.email]
    if lead.email:
        return [lead.email]
    raise HttpError(400, "No recipient email address (lead has no contact email and 'to' not provided)")


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

    refuse_if_do_not_contact(lead)
    contact = _resolve_send_contact(lead, data.contact_id)
    template, subject, body = _resolve_send_subject_body(lead, data, contact)
    to = _resolve_send_recipients(data.to, contact, lead)
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
            contact_id=contact.id if contact else None,
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
            contact=contact,
        )
        return {
            "email_id": email_sent.id,
            "status": email_sent.status,
            "message": "Email sent successfully",
        }


# --- Email Draft Functions ---


def _resolve_draft_contact(lead: Lead, contact_id: int | None) -> Contact | None:
    """Resolve the contact for a draft: explicit id > primary > None."""
    if contact_id:
        return get_object_or_404(Contact, pk=contact_id, lead=lead)
    return lead.contacts.filter(is_primary=True).first()


def _default_draft_recipients(contact: Contact | None, lead: Lead) -> list[str]:
    """Default recipient list based on resolved contact, then lead."""
    if contact and contact.email:
        return [contact.email]
    if lead.email:
        return [lead.email]
    return []


def _resolve_user_by_email(email: str) -> User:
    """Look up a user by email (case-insensitive).

    Raises:
        HttpError: 404 if no user has that email.
    """
    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        raise HttpError(404, f"User with email {email} not found")
    return user


def create_email_draft(data: EmailDraftIn) -> EmailDraft:
    """Create a new email draft.

    Args:
        data: EmailDraftIn with lead_id, subject, body, etc.

    Returns:
        EmailDraft record

    Raises:
        Http404: If lead, template, or contact not found
    """
    lead = get_object_or_404(Lead, pk=data.lead_id)
    template = get_object_or_404(EmailTemplate, pk=data.template_id) if data.template_id else None
    contact = _resolve_draft_contact(lead, data.contact_id)
    refuse_if_do_not_contact(lead)
    assigned_to = _resolve_user_by_email(data.assigned_to_email) if data.assigned_to_email else None

    return EmailDraft.objects.create(
        lead=lead,
        contact=contact,
        template=template,
        assigned_to=assigned_to,
        from_email=data.from_email or _resolve_from_email(_get_gmail_connection(assigned_to)),
        to=data.to or _default_draft_recipients(contact, lead),
        bcc=data.bcc,
        subject=data.subject,
        body=data.body,
        humanized_hash=data.humanized_hash,
        batch=data.batch,
    )


def update_email_draft(draft: EmailDraft, data: EmailDraftIn) -> EmailDraft:
    """Update an email draft (full replacement).

    Args:
        draft: The EmailDraft to update
        data: EmailDraftIn with new values

    Returns:
        Updated EmailDraft record

    Raises:
        Http404: If lead, template, or contact not found
    """
    new_lead = get_object_or_404(Lead, pk=data.lead_id)
    if new_lead.pk != draft.lead_id:
        refuse_if_do_not_contact(new_lead)
    draft.lead = new_lead
    draft.template = get_object_or_404(EmailTemplate, pk=data.template_id) if data.template_id else None
    draft.contact = _resolve_draft_contact(draft.lead, data.contact_id)
    draft.assigned_to = _resolve_user_by_email(data.assigned_to_email) if data.assigned_to_email else None
    draft.from_email = data.from_email or _resolve_from_email(_get_gmail_connection(draft.assigned_to))
    draft.to = data.to or _default_draft_recipients(draft.contact, draft.lead)
    draft.bcc = data.bcc
    draft.subject = data.subject
    draft.body = data.body
    draft.humanized_hash = data.humanized_hash
    draft.batch = data.batch
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
        Http404: If template or contact not found
    """
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "template_id":
            draft.template = get_object_or_404(EmailTemplate, pk=value) if value else None
        elif field == "contact_id":
            if value is None:
                draft.contact = None
            else:
                draft.contact = get_object_or_404(Contact, pk=value, lead=draft.lead)
        elif field == "assigned_to_email":
            draft.assigned_to = _resolve_user_by_email(value) if value else None
        else:
            setattr(draft, field, value)
    draft.save()
    return draft


def _ensure_may_send_draft(draft: EmailDraft, user: User | None) -> None:
    """Only the assignee (or a superuser acting explicitly) may send an assigned draft.

    Sending always uses the acting user's own Gmail connection, never anyone else's.

    Raises:
        HttpError: 403 if the draft is assigned to someone else, or to someone and there is no acting user.
    """
    assignee = draft.assigned_to
    if assignee is None:
        return
    if not isinstance(user, User):
        raise HttpError(403, f"Draft is assigned to {assignee.email}; it must be sent by them from the admin")
    if user.pk != assignee.pk and not user.is_superuser:
        raise HttpError(403, f"Draft is assigned to {assignee.email}; only they (or a superuser) can send it")


def send_email_draft(draft: EmailDraft, user: User | None = None) -> EmailSent:
    """Send an email draft.

    Creates EmailSent record, updates lead's last_contact, and deletes the draft.

    Args:
        draft: The EmailDraft to send
        user: Optional User who initiated the send (for Gmail OAuth lookup)

    Returns:
        EmailSent record

    Raises:
        ValueError: If there are unreplaced placeholders in subject or body
    """
    refuse_if_do_not_contact(draft.lead)
    _ensure_may_send_draft(draft, user)

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
        user=user,
        contact=draft.contact,
    )

    # Delete the draft after successful send
    draft.delete()

    return email_sent


def save_email_as_draft(  # noqa: PLR0913
    lead: Lead,
    subject: str,
    body: str,
    to: list[str],
    bcc: list[str] | None = None,
    template: EmailTemplate | None = None,
    draft_id: int | None = None,
    user: User | None = None,
    contact: Contact | None = None,
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
        user: Optional User (for resolving from_email via Gmail connection)
        contact: Optional Contact this draft targets (defaults to lead's primary contact)

    Returns:
        EmailDraft record (created or updated)

    Raises:
        Http404: If draft_id is invalid or belongs to a different lead
    """
    refuse_if_do_not_contact(lead)
    from_email = _resolve_from_email(_get_gmail_connection(user))

    resolved_contact = contact if contact is not None else lead.contacts.filter(is_primary=True).first()

    if draft_id:
        # Validate draft exists AND belongs to this lead
        draft = get_object_or_404(EmailDraft, pk=draft_id, lead=lead)
        draft.template = template
        draft.contact = resolved_contact
        draft.from_email = from_email
        draft.assigned_to = draft.assigned_to or (user if isinstance(user, User) else None)
        draft.to = to
        draft.bcc = bcc or []
        draft.subject = subject
        draft.body = body
        draft.save()
        return draft

    return EmailDraft.objects.create(
        lead=lead,
        contact=resolved_contact,
        template=template,
        from_email=from_email,
        assigned_to=user if isinstance(user, User) else None,
        to=to,
        bcc=bcc or [],
        subject=subject,
        body=body,
    )


# --- Outreach Message Functions ---


def create_outreach_message(data: OutreachMessageIn) -> OutreachMessage:
    """Create an outreach message in `draft` status. Never sends anything.

    Raises:
        Http404: If the lead doesn't exist.
        HttpError: 404 if assigned_to_email matches no user.
    """
    lead = get_object_or_404(Lead, pk=data.lead_id)
    refuse_if_do_not_contact(lead)
    assigned_to = _resolve_user_by_email(data.assigned_to_email) if data.assigned_to_email else None
    return OutreachMessage.objects.create(
        lead=lead,
        channel=data.channel,
        text=data.text,
        assigned_to=assigned_to,
        humanized_hash=data.humanized_hash,
        batch=data.batch,
    )


def update_outreach_message(message: OutreachMessage, data: OutreachMessageIn) -> OutreachMessage:
    """Update an outreach message (full replacement of its input fields)."""
    new_lead = get_object_or_404(Lead, pk=data.lead_id)
    if new_lead.pk != message.lead_id:
        refuse_if_do_not_contact(new_lead)
    message.lead = new_lead
    message.channel = data.channel
    message.text = data.text
    message.assigned_to = _resolve_user_by_email(data.assigned_to_email) if data.assigned_to_email else None
    message.humanized_hash = data.humanized_hash
    message.batch = data.batch
    message.save()
    return message


def patch_outreach_message(message: OutreachMessage, data: OutreachMessagePatch) -> OutreachMessage:
    """Partially update an outreach message."""
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "assigned_to_email":
            message.assigned_to = _resolve_user_by_email(value) if value else None
        elif value is not None:
            setattr(message, field, value)
    message.save()
    return message


def mark_message_sent(message: OutreachMessage, user: User | None = None) -> OutreachMessage:
    """Record that a human sent this message by hand.

    Sets status/sent_at/sent_by, bumps the lead's last_contact to today, moves a `new` lead to `contacted`
    (later statuses are never downgraded) and schedules a follow-up Action. Idempotent for already-sent messages.
    """
    today = timezone.localdate()
    with transaction.atomic():
        # Lock the row and re-check, so a concurrent/stale second call creates no second follow-up
        list(OutreachMessage.objects.select_for_update().filter(pk=message.pk))
        message.refresh_from_db()
        if message.status == OutreachMessage.Status.SENT:
            return message
        refuse_if_do_not_contact(message.lead)
        message.status = OutreachMessage.Status.SENT
        message.sent_at = timezone.now()
        message.sent_by = user if isinstance(user, User) else None
        message.save()

        # Re-read the lead so a stale in-memory status can never cause a downgrade
        lead = Lead.objects.select_for_update().get(pk=message.lead_id)
        lead.last_contact = today
        update_fields = ["last_contact", "updated_at"]
        if lead.status == Lead.Status.NEW:
            lead.status = Lead.Status.CONTACTED
            update_fields.append("status")
        lead.save(update_fields=update_fields)

        Action.objects.create(
            lead=lead,
            name=f"Follow up {message.get_channel_display()} DM",
            notes=f"Follow up on outreach message #{message.pk}",
            due_date=today + timedelta(days=FOLLOW_UP_DAYS),
        )
    return message


def mark_message_skipped(message: OutreachMessage) -> OutreachMessage:
    """Mark a message as skipped (no lead side effects).

    Raises:
        ValueError: If the message was already sent.
    """
    if message.status == OutreachMessage.Status.SENT:
        raise ValueError("Cannot skip a message that was already sent")
    message.status = OutreachMessage.Status.SKIPPED
    message.save(update_fields=["status", "updated_at"])
    return message
