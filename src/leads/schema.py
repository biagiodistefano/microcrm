"""Schemas for leads API."""

import typing as t
from datetime import date
from decimal import Decimal

from ninja import FilterLookup, FilterSchema, ModelSchema, Schema
from pydantic import Field

from leads.models import Action, City, EmailSent, EmailTemplate, Lead, LeadType, ResearchJob, Tag


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


class LeadSchema(ModelSchema):
    """Lead output schema."""

    city: CitySchema | None = None
    lead_type: LeadTypeSchema | None = None
    tags: list[TagSchema] = []

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


class SendEmailIn(Schema):
    """Input schema for sending an email to a lead."""

    template_id: int | None = Field(default=None, description="Template ID to use (optional)")
    subject: str | None = Field(default=None, description="Email subject (required if no template)")
    body: str | None = Field(default=None, description="Email body (required if no template)")
    to: list[str] | None = Field(default=None, description="Override recipient(s), defaults to lead's email")
    bcc: list[str] = Field(default_factory=list, description="BCC recipients")
    send_in_background: bool = Field(default=False, description="Send via Celery background task")


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


class CityFilterSchema(FilterSchema):
    """City filter schema."""

    country: t.Annotated[str | None, FilterLookup(q="country__icontains")] = None


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


class SendEmailResponse(Schema):
    """Response for send email action."""

    email_id: int
    status: str
    message: str = ""
