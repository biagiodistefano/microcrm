from django.db import models
from django.db.models import F
from simple_history.models import HistoricalRecords
from solo.models import SingletonModel


class City(models.Model):
    """City model for lead location."""

    name = models.CharField(max_length=255)
    country = models.CharField(max_length=255)
    iso2 = models.CharField(max_length=2)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "cities"
        constraints = [
            models.UniqueConstraint(fields=["name", "iso2"], name="leads_city_unique_name_country"),
        ]
        indexes = [
            models.Index(fields=["name"], name="leads_city_name"),
            models.Index(fields=["country"], name="leads_city_country"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.name}, {self.country}"


class LeadType(models.Model):
    """Lead type model."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        """Return string representation."""
        return self.name


class Tag(models.Model):
    """Tag model for lead categorization."""

    name = models.CharField(max_length=100, unique=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        """Return string representation."""
        return self.name


class Lead(models.Model):
    """Lead model for CRM."""

    class Status(models.TextChoices):
        NEW = "new", "New"
        CONTACTED = "contacted", "Contacted"
        QUALIFIED = "qualified", "Qualified"
        CONVERTED = "converted", "Converted"
        LOST = "lost", "Lost"

    class Temperature(models.TextChoices):
        COLD = "cold", "Cold"
        WARM = "warm", "Warm"
        HOT = "hot", "Hot"

    # Basic info
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)

    # Organization
    company = models.CharField(max_length=255, blank=True)
    lead_type = models.ForeignKey(LeadType, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads")

    # Location
    city = models.ForeignKey(City, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads")

    # Social & web
    telegram = models.CharField(max_length=255, blank=True, help_text="Telegram username or link")
    instagram = models.CharField(max_length=255, blank=True, help_text="Instagram handle")
    website = models.URLField(blank=True)

    # Lead tracking
    source = models.CharField(max_length=255, blank=True, help_text="How they found us / we found them")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NEW)
    temperature = models.CharField(max_length=10, choices=Temperature.choices, default=Temperature.COLD)
    tags = models.ManyToManyField(Tag, blank=True, related_name="leads")

    # Follow-up
    last_contact = models.DateField(null=True, blank=True)

    # Notes & value
    notes = models.TextField(blank=True)
    value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, help_text="Estimated deal value"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # Choice fields and dates - frequently filtered
            models.Index(fields=["status"], name="leads_lead_status"),
            models.Index(fields=["temperature"], name="leads_lead_temperature"),
            models.Index(fields=["created_at"], name="leads_lead_created_at"),
            # Contact fields - frequently searched/filtered (Note: FKs like city/lead_type get auto-indexed)
            models.Index(fields=["email"], name="leads_lead_email"),
            models.Index(fields=["phone"], name="leads_lead_phone"),
            models.Index(fields=["instagram"], name="leads_lead_instagram"),
            models.Index(fields=["telegram"], name="leads_lead_telegram"),
            models.Index(fields=["website"], name="leads_lead_website"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return self.name


class Action(models.Model):
    """Action model for lead follow-up tasks."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        IN_PROGRESS = "in_progress", "In Progress"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="actions")
    name = models.CharField(max_length=255)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    due_date = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = [F("due_date").asc(nulls_last=True), "created_at"]
        indexes = [
            models.Index(fields=["status"], name="leads_action_status"),
            models.Index(fields=["due_date"], name="leads_action_due_date"),
            models.Index(fields=["created_at"], name="leads_action_created_at"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.name} ({self.lead.name})"


class ResearchPromptConfig(SingletonModel):
    """Singleton config for research prompt template."""

    prompt_template = models.TextField(
        default="Research leads in {city} for event promotion.\n\nLead types: {lead_types}\n\nOutput schema: {schema}",
        help_text="Use {city}, {lead_types}, and {schema} as placeholders. See PROMPT_TEMPLATE.md for full template.",
    )

    class Meta:
        verbose_name = "Research Prompt Config"

    def __str__(self) -> str:
        return "Research Prompt Config"


class ResearchJob(models.Model):
    """Track Gemini deep research jobs."""

    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not Started"
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    city = models.ForeignKey(City, on_delete=models.CASCADE, related_name="research_jobs")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    gemini_interaction_id = models.CharField(max_length=255, blank=True)
    raw_result = models.TextField(blank=True, null=True)
    result = models.JSONField(null=True, blank=True)
    error = models.TextField(blank=True)
    leads_created = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["city"],
                condition=models.Q(status__in=["pending", "running"]),
                name="unique_active_research_per_city",
            ),
        ]

    def __str__(self) -> str:
        return f"Research: {self.city} ({self.status})"


class EmailTemplate(models.Model):
    """Reusable email template with placeholder support."""

    class Language(models.TextChoices):
        EN = "en", "English"
        IT = "it", "Italian"
        ES = "es", "Spanish"
        DE = "de", "German"
        FR = "fr", "French"

    name = models.CharField(max_length=255, unique=True, help_text="Template identifier (e.g., 'Initial Outreach')")
    language = models.CharField(
        max_length=5, choices=Language.choices, default=Language.EN, help_text="Language of the template"
    )
    subject = models.CharField(max_length=255, help_text="Subject line. Use {lead.name}, {lead.city}, etc.")
    body = models.TextField(help_text="Email body. Use {lead.name}, {lead.city}, etc.")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        """Return string representation."""
        return f"{self.name} ({self.get_language_display()})"


class EmailSent(models.Model):
    """Record of an email sent to a lead."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SENT = "sent", "Sent"
        FAILED = "failed", "Failed"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="emails_sent")
    template = models.ForeignKey(
        EmailTemplate, on_delete=models.SET_NULL, null=True, blank=True, related_name="emails_sent"
    )
    from_email = models.EmailField(help_text="Sender email address")
    to = models.JSONField(help_text="List of recipient email addresses")
    bcc = models.JSONField(default=list, blank=True, help_text="List of BCC email addresses")
    subject = models.CharField(max_length=255, help_text="Rendered subject at send time")
    body = models.TextField(help_text="Rendered body at send time")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    error_message = models.TextField(blank=True, help_text="Error details if sending failed")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Email Sent"
        verbose_name_plural = "Emails Sent"
        indexes = [
            models.Index(fields=["status"], name="leads_emailsent_status"),
            models.Index(fields=["sent_at"], name="leads_emailsent_sent_at"),
            models.Index(fields=["created_at"], name="leads_emailsent_created_at"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        return f"Email to {self.lead.name}: {self.subject[:50]}"


class EmailDraft(models.Model):
    """Draft email to be sent to a lead."""

    lead = models.ForeignKey(
        Lead,
        on_delete=models.CASCADE,
        related_name="email_drafts",
    )
    template = models.ForeignKey(
        EmailTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_drafts",
    )
    from_email = models.CharField(
        blank=True, help_text="Sender email address (defaults to DEFAULT_FROM_EMAIL)", max_length=255
    )
    to = models.JSONField(default=list, help_text="List of recipient email addresses")
    bcc = models.JSONField(default=list, blank=True, help_text="List of BCC email addresses")
    subject = models.CharField(max_length=255, help_text="Email subject")
    body = models.TextField(help_text="Email body")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "Email Draft"
        verbose_name_plural = "Email Drafts"
        indexes = [
            models.Index(fields=["created_at"], name="leads_emaildraft_created"),
            models.Index(fields=["updated_at"], name="leads_emaildraft_updated"),
        ]

    def __str__(self) -> str:
        """Return string representation."""
        subject_preview = self.subject[:50] if self.subject else "(no subject)"
        return f"Draft: {subject_preview} -> {self.lead.name}"
