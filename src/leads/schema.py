"""Schemas for leads API."""

import typing as t
from datetime import date
from decimal import Decimal

from django.db.models import Q
from ninja import FilterLookup, FilterSchema, ModelSchema, Schema
from pydantic import AliasChoices, AwareDatetime, Field, field_validator, model_validator

from leads.models import (
    Action,
    City,
    Contact,
    EmailDraft,
    EmailSent,
    EmailTemplate,
    Lead,
    LeadType,
    OutreachMessage,
    ResearchJob,
    Tag,
)


def normalize_platforms(value: list[str] | None) -> list[str] | None:
    """Lowercase, strip and de-duplicate ticketing platform keys (order preserved)."""
    if value is None:
        return None
    return list(dict.fromkeys(p.strip().lower() for p in value if p and p.strip()))


# --- Output Schemas ---
class CitySchema(ModelSchema):
    """City output schema."""

    class Meta:
        model = City
        fields = ["id", "name", "country", "iso2"]


class LeadTypeSchema(ModelSchema):
    """LeadType output schema."""

    class Meta:
        model = LeadType
        fields = ["id", "name"]


class TagSchema(ModelSchema):
    """Tag output schema."""

    class Meta:
        model = Tag
        fields = ["id", "name"]


class ActionSchema(ModelSchema):
    """Action output schema."""

    lead_id: int

    class Meta:
        model = Action
        fields = [
            "id",
            "name",
            "notes",
            "status",
            "due_date",
            "completed_at",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_lead_id(obj: Action) -> int:
        """Resolve lead_id from the foreign key."""
        return obj.lead_id


class ContactSchema(ModelSchema):
    """Contact output schema."""

    lead_id: int

    class Meta:
        model = Contact
        fields = [
            "id",
            "name",
            "role",
            "email",
            "phone",
            "telegram",
            "instagram",
            "website",
            "notes",
            "is_primary",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_lead_id(obj: Contact) -> int:
        """Resolve lead_id from the foreign key."""
        return obj.lead_id


class LeadSchema(ModelSchema):
    """Lead output schema."""

    city: CitySchema | None = None
    lead_type: LeadTypeSchema | None = None
    tags: list[TagSchema] = []
    contacts: list[ContactSchema] = []
    primary_contact: ContactSchema | None = None

    class Meta:
        model = Lead
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "company",
            "telegram",
            "instagram",
            "website",
            "bluesky",
            "facebook",
            "linkedin",
            "whatsapp",
            "ticketing_platforms",
            "external_ref",
            "do_not_contact",
            "do_not_contact_reason",
            "do_not_contact_at",
            "source",
            "status",
            "temperature",
            "last_contact",
            "notes",
            "value",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_tags(obj: Lead) -> list[Tag]:
        """Resolve tags."""
        return list(obj.tags.all())

    @staticmethod
    def resolve_contacts(obj: Lead) -> list[Contact]:
        """Resolve contacts (primary first)."""
        return list(obj.contacts.all())

    @staticmethod
    def resolve_primary_contact(obj: Lead) -> Contact | None:
        """Resolve the primary contact, if any."""
        for c in obj.contacts.all():
            if c.is_primary:
                return c
        return None


class ResearchJobSchema(ModelSchema):
    """ResearchJob output schema."""

    city: CitySchema

    class Meta:
        model = ResearchJob
        fields = [
            "id",
            "status",
            "gemini_interaction_id",
            "error",
            "leads_created",
            "created_at",
            "completed_at",
        ]


class ResearchJobDetailSchema(ModelSchema):
    """ResearchJob detail schema with result data."""

    city: CitySchema

    class Meta:
        model = ResearchJob
        fields = [
            "id",
            "status",
            "gemini_interaction_id",
            "raw_result",
            "result",
            "error",
            "leads_created",
            "created_at",
            "completed_at",
        ]


class EmailTemplateSchema(ModelSchema):
    """EmailTemplate output schema."""

    class Meta:
        model = EmailTemplate
        fields = ["id", "name", "language", "subject", "body", "created_at", "updated_at"]


class EmailSentSchema(ModelSchema):
    """EmailSent output schema."""

    lead_id: int
    template_id: int | None = None

    class Meta:
        model = EmailSent
        fields = [
            "id",
            "from_email",
            "to",
            "bcc",
            "subject",
            "body",
            "status",
            "error_message",
            "created_at",
            "sent_at",
        ]

    @staticmethod
    def resolve_lead_id(obj: EmailSent) -> int:
        """Resolve lead_id from the foreign key."""
        return obj.lead_id

    @staticmethod
    def resolve_template_id(obj: EmailSent) -> int | None:
        """Resolve template_id from the foreign key."""
        return obj.template_id


class EmailDraftSchema(ModelSchema):
    """EmailDraft output schema."""

    lead_id: int
    template_id: int | None = None
    assigned_to_email: str | None = None
    is_humanized: bool = False

    class Meta:
        model = EmailDraft
        fields = [
            "id",
            "humanized_hash",
            "batch",
            "from_email",
            "to",
            "bcc",
            "subject",
            "body",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_lead_id(obj: EmailDraft) -> int:
        """Resolve lead_id from the foreign key."""
        return obj.lead_id

    @staticmethod
    def resolve_template_id(obj: EmailDraft) -> int | None:
        """Resolve template_id from the foreign key."""
        return obj.template_id

    @staticmethod
    def resolve_assigned_to_email(obj: EmailDraft) -> str | None:
        """Resolve the assignee's email."""
        return obj.assigned_to.email if obj.assigned_to else None

    @staticmethod
    def resolve_is_humanized(obj: EmailDraft) -> bool:
        """Resolve whether subject + body still match the humanized hash."""
        return obj.is_humanized


class OutreachMessageSchema(ModelSchema):
    """OutreachMessage output schema."""

    lead_id: int
    assigned_to_email: str | None = None
    sent_by_email: str | None = None
    deeplink: str = ""
    is_humanized: bool = False

    class Meta:
        model = OutreachMessage
        fields = [
            "id",
            "channel",
            "text",
            "status",
            "sent_at",
            "humanized_hash",
            "batch",
            "created_at",
            "updated_at",
        ]

    @staticmethod
    def resolve_lead_id(obj: OutreachMessage) -> int:
        """Resolve lead_id from the foreign key."""
        return obj.lead_id

    @staticmethod
    def resolve_assigned_to_email(obj: OutreachMessage) -> str | None:
        """Resolve the assignee's email."""
        return obj.assigned_to.email if obj.assigned_to else None

    @staticmethod
    def resolve_sent_by_email(obj: OutreachMessage) -> str | None:
        """Resolve the sender's email."""
        return obj.sent_by.email if obj.sent_by else None

    @staticmethod
    def resolve_is_humanized(obj: OutreachMessage) -> bool:
        """Resolve whether the text still matches the humanized hash."""
        return obj.is_humanized

    @staticmethod
    def resolve_deeplink(obj: OutreachMessage) -> str:
        """Resolve the channel deeplink."""
        return obj.deeplink


# --- Input Schemas ---
class CityIn(Schema):
    """City input for creating/finding a city.

    If a city with the same name and country exists (case-insensitive),
    it will be reused. Otherwise, a new city is created.
    """

    name: str = Field(..., description="City name")
    country: str = Field(..., description="Country name")
    iso2: str = Field(default="", description="ISO 3166-1 alpha-2 country code (optional)")


class LeadIn(Schema):
    """Lead create/update input schema.

    Related objects (city, lead_type, tags) are automatically created if they
    don't exist, or reused if they do (matched case-insensitively).
    """

    name: str = Field(..., description="Lead/organization name")
    email: str = Field(default="", description="Contact email")
    phone: str = Field(default="", description="Contact phone")
    company: str = Field(default="", description="Company/organization name")
    lead_type: str | None = Field(default=None, description="Lead type name (auto-created if not exists)")
    city: CityIn | None = Field(default=None, description="City (auto-created if not exists)")
    telegram: str = Field(default="", description="Telegram username or link")
    instagram: str = Field(default="", description="Instagram handle")
    website: str = Field(default="", description="Website URL")
    bluesky: str = Field(default="", description="Bluesky handle or profile URL")
    facebook: str = Field(default="", description="Facebook page URL or slug")
    linkedin: str = Field(default="", description="LinkedIn URL")
    whatsapp: str = Field(default="", description="WhatsApp number (international format)")
    ticketing_platforms: list[str] = Field(
        default_factory=list, description="Ticketing platform keys (e.g. eventbrite, ra); lowercased"
    )
    external_ref: str = Field(
        default="", description="Stable ID from an external tool (unique when non-empty; used for upserts)"
    )
    source: str = Field(default="", description="How they found us / we found them")
    status: Lead.Status = Field(default=Lead.Status.NEW, description="Lead status")
    temperature: Lead.Temperature = Field(default=Lead.Temperature.COLD, description="Lead temperature")
    tags: list[str] = Field(
        default_factory=list,
        description="Tag names (auto-created if not exists, case-insensitive matching)",
    )
    last_contact: date | None = Field(default=None, description="Date of last contact")
    notes: str = Field(default="", description="Additional notes")
    value: Decimal | None = Field(default=None, description="Estimated deal value")

    @field_validator("ticketing_platforms")
    @classmethod
    def _normalize_platforms(cls, value: list[str]) -> list[str]:
        return normalize_platforms(value) or []

    @field_validator("external_ref")
    @classmethod
    def _normalize_ref(cls, value: str) -> str:
        return value.strip().lower()


class LeadUpsertIn(LeadIn):
    """Lead upsert input: same fields as LeadIn, deduplicated/merged instead of always creating.

    Matches an existing lead by `external_ref`, then email/phone/instagram/telegram/website/bluesky/
    facebook/linkedin/whatsapp, then name+city. On a match only blank fields are filled (status,
    temperature and value are never overwritten); tags and ticketing platforms are merged additively.
    `type` is accepted as an alias of `lead_type`; `city` may be an object or a name with a sibling `country`.
    """

    lead_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("lead_type", "type"),
        description="Lead type name (auto-created if not exists); alias: type",
    )
    do_not_contact: bool = Field(
        default=False, description="Set true to suppress the lead. Upsert can set it but never clears it"
    )
    do_not_contact_reason: str = Field(default="", description="Why the lead is suppressed")

    @model_validator(mode="before")
    @classmethod
    def _flat_city(cls, data: t.Any) -> t.Any:
        raw = getattr(data, "_obj", data)  # ninja wraps request bodies in a DjangoGetter
        if isinstance(raw, dict) and isinstance(raw.get("city"), str):
            return {**raw, "city": {"name": raw["city"], "country": raw.get("country", "")}}
        return data


class LeadUpsertOut(Schema):
    """Result of a lead upsert."""

    id: int
    created: bool
    do_not_contact: bool = False
    matched_on: str | None = Field(
        default=None,
        description="Key the existing lead was matched on (external_ref, email, phone, instagram, telegram, "
        "website, bluesky, facebook, linkedin, whatsapp, name_city); null when a lead was created",
    )


class LeadPatch(Schema):
    """Lead partial update schema.

    Only provided fields are updated. Related objects (city, lead_type, tags)
    are automatically created if they don't exist.
    """

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    company: str | None = None
    lead_type: str | None = Field(default=None, description="Lead type name (auto-created if not exists)")
    city: CityIn | None = Field(default=None, description="City (auto-created if not exists)")
    telegram: str | None = None
    instagram: str | None = None
    website: str | None = None
    bluesky: str | None = None
    facebook: str | None = None
    linkedin: str | None = None
    whatsapp: str | None = None
    ticketing_platforms: list[str] | None = None
    external_ref: str | None = None
    do_not_contact: bool | None = Field(default=None, description="Explicitly set/clear suppression")
    do_not_contact_reason: str | None = None
    source: str | None = None
    status: Lead.Status | None = None
    temperature: Lead.Temperature | None = None
    tags: list[str] | None = Field(
        default=None,
        description="Tag names (auto-created if not exists, case-insensitive matching)",
    )
    last_contact: date | None = None
    notes: str | None = None
    value: Decimal | None = None

    @field_validator("ticketing_platforms")
    @classmethod
    def _normalize_platforms(cls, value: list[str] | None) -> list[str] | None:
        return normalize_platforms(value)

    @field_validator("external_ref")
    @classmethod
    def _normalize_ref(cls, value: str | None) -> str | None:
        return value.strip().lower() if value is not None else None


class ContactIn(Schema):
    """Contact create input schema."""

    lead_id: int = Field(..., description="ID of the lead this contact belongs to")
    name: str = Field(..., description="Person name or label (e.g., 'Alice Booker', 'Primary')")
    role: str = Field(default="", description="Role at the organization (e.g., 'booker')")
    email: str = Field(default="", description="Contact email")
    phone: str = Field(default="", description="Contact phone")
    telegram: str = Field(default="", description="Telegram username or link")
    instagram: str = Field(default="", description="Instagram handle")
    website: str = Field(default="", description="Website URL")
    notes: str = Field(default="", description="Additional notes")
    is_primary: bool = Field(default=False, description="Mark this contact as the primary one for the lead")


class ContactPatch(Schema):
    """Contact partial update schema."""

    name: str | None = None
    role: str | None = None
    email: str | None = None
    phone: str | None = None
    telegram: str | None = None
    instagram: str | None = None
    website: str | None = None
    notes: str | None = None
    is_primary: bool | None = None


class ActionIn(Schema):
    """Action create input schema.

    Note: status is always PENDING on creation. Use PATCH to update status.
    """

    lead_id: int = Field(..., description="ID of the lead this action belongs to")
    name: str = Field(..., description="Action name/title")
    notes: str = Field(default="", description="Additional notes")
    due_date: date | None = Field(default=None, description="Due date for the action")


class ActionPatch(Schema):
    """Action partial update schema."""

    name: str | None = None
    notes: str | None = None
    status: Action.Status | None = None
    due_date: date | None = None


class ResearchJobIn(Schema):
    """ResearchJob create input schema."""

    city_id: int = Field(..., description="ID of the city to research")


class EmailTemplateIn(Schema):
    """EmailTemplate create/update input schema."""

    name: str = Field(..., description="Template name (must be unique)")
    language: str = Field(default="en", description="Language code (e.g., 'en', 'it', 'es', 'de', 'fr')")
    subject: str = Field(..., description="Email subject (supports placeholders like {lead.name})")
    body: str = Field(..., description="Email body (supports placeholders like {lead.name}, {lead.city})")


class EmailTemplatePatch(Schema):
    """EmailTemplate partial update schema."""

    name: str | None = None
    language: str | None = None
    subject: str | None = None
    body: str | None = None


class EmailDraftIn(Schema):
    """EmailDraft create/update input schema."""

    lead_id: int = Field(..., description="ID of the lead this draft is for")
    contact_id: int | None = Field(
        default=None, description="Contact this draft targets (defaults to the lead's primary contact)"
    )
    template_id: int | None = Field(default=None, description="Template ID used (optional)")
    from_email: str | None = Field(
        default=None,
        description="Sender email. Defaults to the assignee's connected Gmail, else DEFAULT_FROM_EMAIL",
    )
    assigned_to_email: str | None = Field(
        default=None, description="Email of the user whose queue this draft lands in (404 if no such user)"
    )
    to: list[str] | None = Field(default=None, description="Recipients (defaults to primary contact's email)")
    bcc: list[str] = Field(default_factory=list, description="BCC recipients")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body")
    humanized_hash: str = Field(default="", description="sha256 hex of subject + '\\x1f' + body as humanized (audit)")
    batch: str = Field(default="", description="Batch label")


class EmailDraftPatch(Schema):
    """EmailDraft partial update schema."""

    contact_id: int | None = None
    template_id: int | None = None
    from_email: str | None = None
    assigned_to_email: str | None = None
    to: list[str] | None = None
    bcc: list[str] | None = None
    subject: str | None = None
    body: str | None = None
    humanized_hash: str | None = None
    batch: str | None = None


class OutreachMessageIn(Schema):
    """OutreachMessage create/update input schema. Status is always `draft`; use mark-sent / skip to change it."""

    lead_id: int = Field(..., description="ID of the lead this message is for")
    channel: OutreachMessage.Channel = Field(..., description="Channel the DM is sent on")
    text: str = Field(..., min_length=1, description="Message text, ready to paste")
    assigned_to_email: str = Field(..., min_length=1, description="Email of the user who should send it (required)")
    humanized_hash: str = Field(default="", description="sha256 hex of the humanized text, stored for audit")
    batch: str = Field(default="", description="Batch label")


class OutreachMessagePatch(Schema):
    """OutreachMessage partial update schema."""

    channel: OutreachMessage.Channel | None = None
    text: str | None = Field(default=None, min_length=1)
    assigned_to_email: str | None = None
    humanized_hash: str | None = None
    batch: str | None = None


class SendEmailIn(Schema):
    """Input schema for sending an email to a lead."""

    contact_id: int | None = Field(
        default=None, description="Contact to email (defaults to the lead's primary contact)"
    )
    template_id: int | None = Field(default=None, description="Template ID to use (optional)")
    subject: str | None = Field(default=None, description="Email subject (required if no template)")
    body: str | None = Field(default=None, description="Email body (required if no template)")
    to: list[str] | None = Field(
        default=None, description="Override recipient(s), defaults to the primary contact's email"
    )
    bcc: list[str] = Field(default_factory=list, description="BCC recipients")
    send_in_background: bool = Field(default=False, description="Send via Celery background task")


class LeadTagsIn(Schema):
    """Bulk tag add/remove input."""

    lead_ids: list[int] = Field(..., description="Leads to modify")
    add: list[str] = Field(default_factory=list, description="Tag names to add (auto-created)")
    remove: list[str] = Field(default_factory=list, description="Tag names to remove (case-insensitive)")


class LeadMergeIn(Schema):
    """Merge duplicate leads into one."""

    primary_id: int = Field(..., description="Lead to keep; the others are merged into it and deleted")
    other_ids: list[int] = Field(..., description="Leads to merge into the primary (max 19)")


class LeadTagsOut(Schema):
    """Bulk tag result."""

    updated: int


class JobActionResponse(Schema):
    """Response for job actions (run, reprocess)."""

    job_id: int
    status: str
    message: str = ""
    leads_created: int | None = None


# --- Filter Schemas ---
class LeadFilterSchema(FilterSchema):
    """Lead filter schema."""

    status: Lead.Status | None = None
    temperature: Lead.Temperature | None = None
    lead_type: t.Annotated[str | None, FilterLookup(q="lead_type__name__iexact")] = None
    city_id: t.Annotated[int | None, FilterLookup(q="city__id")] = None
    city: t.Annotated[str | None, FilterLookup(q="city__name__icontains")] = None
    country: t.Annotated[str | None, FilterLookup(q="city__country__icontains")] = None
    tag: t.Annotated[str | None, FilterLookup(q="tags__name__iexact")] = None
    external_ref: t.Annotated[str | None, FilterLookup(q="external_ref__iexact")] = None
    do_not_contact: bool | None = None
    source: t.Annotated[str | None, FilterLookup(q="source__istartswith")] = None
    updated_since: t.Annotated[AwareDatetime | None, FilterLookup(q="updated_at__gte")] = None
    created_since: t.Annotated[AwareDatetime | None, FilterLookup(q="created_at__gte")] = None
    tags: list[str] | None = Field(None, description="Repeat the param; leads must have ALL these tags")
    has_draft: bool | None = Field(None, description="Filter by draft existence (true/false)")

    def filter_tags(self, value: list[str] | None) -> Q:
        """All-of tag matching is applied by the controller (one join per tag); nothing to add here."""
        return Q()

    def filter_has_draft(self, value: bool) -> Q:  # noqa: FBT001
        """Filter leads by draft existence."""
        if value:
            return Q(email_drafts__isnull=False)
        return Q(email_drafts__isnull=True)


class CityFilterSchema(FilterSchema):
    """City filter schema."""

    country: t.Annotated[str | None, FilterLookup(q="country__icontains")] = None


class ContactFilterSchema(FilterSchema):
    """Contact filter schema."""

    lead_id: t.Annotated[int | None, FilterLookup(q="lead_id")] = None
    is_primary: bool | None = None


class ActionFilterSchema(FilterSchema):
    """Action filter schema."""

    lead_id: t.Annotated[int | None, FilterLookup(q="lead_id")] = None
    status: Action.Status | None = None
    due_date: date | None = None
    due_before: t.Annotated[date | None, FilterLookup(q="due_date__lte")] = None
    due_after: t.Annotated[date | None, FilterLookup(q="due_date__gte")] = None


class ResearchJobFilterSchema(FilterSchema):
    """ResearchJob filter schema."""

    city_id: t.Annotated[int | None, FilterLookup(q="city_id")] = None
    status: ResearchJob.Status | None = None
    country: t.Annotated[str | None, FilterLookup(q="city__country__icontains")] = None


class EmailSentFilterSchema(FilterSchema):
    """EmailSent filter schema."""

    lead_id: t.Annotated[int | None, FilterLookup(q="lead_id")] = None
    template_id: t.Annotated[int | None, FilterLookup(q="template_id")] = None
    status: EmailSent.Status | None = None


class EmailDraftFilterSchema(FilterSchema):
    """EmailDraft filter schema."""

    lead_id: t.Annotated[int | None, FilterLookup(q="lead_id")] = None
    template_id: t.Annotated[int | None, FilterLookup(q="template_id")] = None
    assigned_to_email: t.Annotated[str | None, FilterLookup(q="assigned_to__email__iexact")] = None
    batch: str | None = None
    created_since: t.Annotated[AwareDatetime | None, FilterLookup(q="created_at__gte")] = None


class OutreachMessageFilterSchema(FilterSchema):
    """OutreachMessage filter schema."""

    status: OutreachMessage.Status | None = None
    channel: OutreachMessage.Channel | None = None
    batch: str | None = None
    created_since: t.Annotated[AwareDatetime | None, FilterLookup(q="created_at__gte")] = None
    assigned_to_email: t.Annotated[str | None, FilterLookup(q="assigned_to__email__iexact")] = None
    lead_id: t.Annotated[int | None, FilterLookup(q="lead_id")] = None


class SendEmailResponse(Schema):
    """Response for send email action."""

    email_id: int
    status: str
    message: str = ""
