import typing as t
from datetime import date
from urllib.parse import urlencode, urlparse

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.helpers import ACTION_CHECKBOX_NAME
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.db.models import Count, DateField, Q, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from ninja.errors import HttpError
from simple_history.admin import SimpleHistoryAdmin
from solo.admin import SingletonModelAdmin
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.contrib.filters.admin import DropdownFilter, RangeDateFilter
from unfold.decorators import action
from unfold.widgets import UnfoldAdminSingleDateWidget

from . import models
from . import service as lead_service
from . import tasks as lead_tasks


class CountryFilter(DropdownFilter):  # type: ignore[misc]
    """Filter leads by country (derived from city)."""

    title = "country"
    parameter_name = "country"

    def lookups(self, request: HttpRequest, model_admin: t.Any) -> list[tuple[str, str]]:
        """Return distinct countries that have at least one lead."""
        countries = (
            models.City.objects.filter(leads__isnull=False)
            .values_list("country", flat=True)
            .distinct()
            .order_by("country")
        )
        return [(c, c) for c in countries]

    def queryset(self, request: HttpRequest, queryset: QuerySet[t.Any]) -> QuerySet[t.Any]:
        """Filter leads by city__country."""
        if self.value():
            return queryset.filter(city__country=self.value())
        return queryset


class CityByCountryFilter(DropdownFilter):  # type: ignore[misc]
    """Filter leads by city, scoped to the currently selected country."""

    title = "city"
    parameter_name = "city__id__exact"

    def lookups(self, request: HttpRequest, model_admin: t.Any) -> list[tuple[str, str]]:
        """Return cities scoped to the selected country, or all cities with leads."""
        country = request.GET.get("country")
        qs = models.City.objects.filter(leads__isnull=False).distinct().order_by("name")
        if country:
            qs = qs.filter(country=country)
        return [(str(c.id), str(c)) for c in qs]

    def queryset(self, request: HttpRequest, queryset: QuerySet[t.Any]) -> QuerySet[t.Any]:
        """Filter leads by city id."""
        if self.value():
            return queryset.filter(city__id=self.value())
        return queryset


class DuplicateGroupFilter(DropdownFilter):  # type: ignore[misc]
    """Open one group of suspected duplicates (tag ``review:duplicate-<n>``) at a time."""

    title = "duplicate group"
    parameter_name = "duplicate_group"

    def lookups(self, request: HttpRequest, model_admin: t.Any) -> list[tuple[str, str]]:
        """Return one entry per review:duplicate-<n> tag, with the number of leads still in the group."""
        tags = (
            models.Tag.objects.filter(name__istartswith=lead_service.REVIEW_GROUP_PREFIX)
            .annotate(lead_count=Count("leads"))
            .order_by("name")
        )
        return sorted(
            (
                (tag.name, f"{tag.name.removeprefix(lead_service.REVIEW_GROUP_PREFIX)} ({tag.lead_count} leads)")
                for tag in tags
            ),
            key=lambda item: (len(item[0]), item[0]),
        )

    def queryset(self, request: HttpRequest, queryset: QuerySet[t.Any]) -> QuerySet[t.Any]:
        """Show only the leads carrying the chosen group tag."""
        if self.value():
            return queryset.filter(tags__name=self.value())
        return queryset


class HasContactFieldFilter(admin.SimpleListFilter):
    """Base filter for checking if any of the lead's contacts has a value for a field."""

    field_name = ""  # Override in subclasses

    def lookups(self, request: HttpRequest, model_admin: t.Any) -> list[tuple[str, str]]:
        """Return filter options."""
        return [
            ("yes", "Yes"),
            ("no", "No"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[t.Any]) -> QuerySet[t.Any]:
        """Filter leads by whether any contact (or the legacy Lead column) has the field populated.

        Uses an EXISTS subquery on Contact rather than a multi-valued reverse-FK join +
        exclude/filter, because Django's multi-valued exclude evaluates per-row and would
        misclassify leads that have some contacts with the field filled and others without.
        The legacy Lead.* column is OR'd in for the Phase 2/3 dual-write window; Phase 4
        drops it when the columns are removed.
        """
        from django.db.models import Exists, OuterRef, Q

        value = self.value()
        if value not in ("yes", "no"):
            return queryset

        has_contact_value = Exists(models.Contact.objects.filter(lead=OuterRef("pk"), **{f"{self.field_name}__gt": ""}))
        has_legacy_value = Q(**{f"{self.field_name}__gt": ""})
        combined = has_contact_value | has_legacy_value
        if value == "yes":
            return queryset.filter(combined)
        return queryset.filter(~combined)


class HasEmailFilter(HasContactFieldFilter):
    """Filter for leads with/without email."""

    title = "has email"
    parameter_name = "has_email"
    field_name = "email"


class HasPhoneFilter(HasContactFieldFilter):
    """Filter for leads with/without phone."""

    title = "has phone"
    parameter_name = "has_phone"
    field_name = "phone"


class HasInstagramFilter(HasContactFieldFilter):
    """Filter for leads with/without instagram."""

    title = "has instagram"
    parameter_name = "has_instagram"
    field_name = "instagram"


class HasTelegramFilter(HasContactFieldFilter):
    """Filter for leads with/without telegram."""

    title = "has telegram"
    parameter_name = "has_telegram"
    field_name = "telegram"


def _render_contact_block(contact: "models.Contact", send_url: str) -> str:
    """Render one contact as a vertical stack: header line + method lines.

    Used by LeadAdmin.display_contacts to show all methods of all contacts in one
    list-view cell. Each method is its own line so multiple contacts read clearly.
    """
    name = contact.name or "Contact"
    header = format_html("<strong>{}</strong>", name)
    if contact.is_primary:
        header = format_html('{} <span style="color:#10b981; font-size:0.75em;">★ primary</span>', header)
    if contact.role:
        header = format_html('{} <span style="color:#666; font-size:0.85em;">({})</span>', header, contact.role)

    parts: list[str] = [format_html('<div style="font-size: 0.85em;">{}</div>', header)]

    if contact.email:
        parts.append(
            format_html(
                '<div style="font-size: 0.8em;">📧 <a href="{}" onclick="event.stopPropagation()">{}</a></div>',
                send_url,
                contact.email,
            )
        )
    if contact.phone:
        parts.append(format_html('<div style="font-size: 0.8em;">📞 {}</div>', contact.phone))
    if contact.telegram:
        tg_url = (
            contact.telegram if contact.telegram.startswith("http") else f"https://t.me/{contact.telegram.lstrip('@')}"
        )
        parts.append(
            format_html(
                '<div style="font-size: 0.8em;">📱 '
                '<a href="{}" target="_blank" onclick="event.stopPropagation()">{}</a></div>',
                tg_url,
                contact.telegram,
            )
        )
    if contact.instagram:
        ig = contact.instagram.lstrip("@")
        parts.append(
            format_html(
                '<div style="font-size: 0.8em;">📷 '
                '<a href="https://instagram.com/{}" target="_blank" '
                'onclick="event.stopPropagation()">@{}</a></div>',
                ig,
                ig,
            )
        )
    if contact.website:
        parts.append(
            format_html(
                '<div style="font-size: 0.8em;">🌐 '
                '<a href="{}" target="_blank" onclick="event.stopPropagation()">{}</a></div>',
                contact.website,
                contact.website,
            )
        )

    return format_html(
        '<div style="border-left: 2px solid #e5e7eb; padding-left: 6px;">{}</div>',
        mark_safe("".join(parts)),
    )


class CityLinkMixin:
    """Mixin to add a link to a city."""

    def city_link(self, obj: t.Any) -> str:
        """Return a link to the city."""
        if not obj.city:
            return "-"
        url = reverse("admin:leads_city_change", args=[obj.city.id])
        return format_html('<a href="{}">{}</a>', url, obj.city)

    city_link.short_description = "City"  # type: ignore[attr-defined]


class LeadTypeLinkMixin:
    """Mixin to add a link to a lead type."""

    def lead_type_link(self, obj: t.Any) -> str:
        """Return a link to the lead type."""
        if not obj.lead_type:
            return "-"
        url = reverse("admin:leads_leadtype_change", args=[obj.lead_type.id])
        return format_html('<a href="{}">{}</a>', url, obj.lead_type)

    lead_type_link.short_description = "Type"  # type: ignore[attr-defined]


@admin.register(models.City)
class CityAdmin(ModelAdmin):  # type: ignore[misc]
    """Admin for City model."""

    list_display = ["name", "country", "iso2", "lead_count"]
    search_fields = ["name", "country"]
    list_filter = ["country"]
    ordering = ["name"]
    actions = ["start_research"]

    def get_queryset(self, request: HttpRequest) -> t.Any:
        """Annotate with lead count to avoid N+1."""
        from django.db.models import Count

        return super().get_queryset(request).annotate(_lead_count=Count("leads"))

    @admin.display(description="Leads", ordering="_lead_count")
    def lead_count(self, obj: models.City) -> int:
        """Return the number of leads for this city."""
        return obj._lead_count  # type: ignore[attr-defined, no-any-return]

    def get_actions(self, request: HttpRequest) -> dict[str, t.Any]:
        """Only superusers can trigger research (Gemini API calls are expensive)."""
        actions: dict[str, t.Any] = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("start_research", None)
        return actions

    @admin.action(description="🔬 Start Lead Research")
    def start_research(self, request: HttpRequest, queryset: QuerySet[models.City]) -> None:
        """Start research for selected cities."""
        from leads.tasks import queue_research

        for city in queryset:
            try:
                queue_research(city.id)
                self.message_user(request, f"Queued research for {city}", messages.SUCCESS)
            except RuntimeError as e:
                self.message_user(request, str(e), messages.WARNING)


@admin.register(models.LeadType)
class LeadTypeAdmin(ModelAdmin):  # type: ignore[misc]
    """Admin for LeadType model."""

    list_display = ["name", "lead_count"]
    search_fields = ["name"]
    ordering = ["name"]

    def get_queryset(self, request: HttpRequest) -> t.Any:
        """Annotate with lead count to avoid N+1."""
        from django.db.models import Count

        return super().get_queryset(request).annotate(_lead_count=Count("leads"))

    @admin.display(description="Leads", ordering="_lead_count")
    def lead_count(self, obj: models.LeadType) -> int:
        """Return the number of leads for this type."""
        return obj._lead_count  # type: ignore[attr-defined, no-any-return]


@admin.register(models.Tag)
class TagAdmin(ModelAdmin):  # type: ignore[misc]
    """Admin for Tag model."""

    list_display = ["name", "lead_count"]
    search_fields = ["name"]
    ordering = ["name"]

    def get_queryset(self, request: HttpRequest) -> t.Any:
        """Annotate with lead count to avoid N+1."""
        from django.db.models import Count

        return super().get_queryset(request).annotate(_lead_count=Count("leads"))

    @admin.display(description="Leads", ordering="_lead_count")
    def lead_count(self, obj: models.Tag) -> int:
        """Return the number of leads for this tag."""
        return obj._lead_count  # type: ignore[attr-defined, no-any-return]


@admin.register(models.Contact)
class ContactAdmin(ModelAdmin, SimpleHistoryAdmin):  # type: ignore[misc]
    """Admin for Contact model (Phase 1: verification only)."""

    list_display = ["name", "lead", "role", "is_primary", "email", "phone"]
    list_filter = ["is_primary"]
    search_fields = ["name", "role", "email", "phone", "telegram", "instagram", "website", "lead__name"]
    autocomplete_fields = ["lead"]
    ordering = ["lead__name", "-is_primary", "name"]


class ContactInline(StackedInline):  # type: ignore[misc]
    """Inline for contacts on Lead admin (per-person contact details)."""

    model = models.Contact
    extra = 0
    fields = [
        "name",
        "role",
        "is_primary",
        "email",
        "phone",
        "telegram",
        "instagram",
        "website",
        "notes",
    ]
    ordering = ["-is_primary", "name"]


def _humanized_badge(is_humanized: bool, humanized_hash: str) -> str:  # noqa: FBT001
    """Badge showing whether text still matches the hash recorded when it was humanized."""
    if is_humanized:
        return format_html('<span style="color:#059669;">&#10003; humanized</span>')
    label = "edited after humanizing" if humanized_hash else "not humanized"
    return format_html(
        '<span style="background:#f59e0b; color:#fff; padding:2px 8px; border-radius:4px; font-size:0.8em;">'
        "&#9888; {}</span>",
        label,
    )


def _dm_link(obj: "models.OutreachMessage") -> str:
    """Render the prominent "Open DM" button (new tab) for a message, or a dash if there is no deeplink."""
    url = obj.deeplink
    if not url:
        return "-"
    return format_html(
        '<a href="{}" target="_blank" rel="noopener noreferrer" '
        'style="display:inline-block; background:#2563eb; color:#fff; font-weight:600; padding:4px 12px; '
        'border-radius:6px; white-space:nowrap; text-decoration:none;">Open DM &#8599;</a>',
        url,
    )


class OutreachMessageInline(TabularInline):  # type: ignore[misc]
    """Inline for outreach messages on Lead admin. Status changes go through the Mark sent / Skip actions."""

    model = models.OutreachMessage
    extra = 0
    fields = ["channel", "text", "status", "assigned_to", "batch", "open_dm"]
    readonly_fields = ["status", "open_dm"]
    ordering = ["-created_at"]

    @admin.display(description="Open DM")
    def open_dm(self, obj: models.OutreachMessage) -> str:
        return _dm_link(obj) if obj.pk else "-"


class ActionInline(TabularInline):  # type: ignore[misc]
    """Inline for actions on Lead admin."""

    model = models.Action
    extra = 1
    fields = ["name", "notes", "status", "due_date", "completed_at"]
    readonly_fields = ["completed_at"]
    ordering = ["-created_at"]
    formfield_overrides = {
        DateField: {"widget": UnfoldAdminSingleDateWidget},
    }


class SendEmailForm(forms.Form):
    """Form for sending email to a lead."""

    draft_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput(),
    )
    back_url = forms.CharField(
        required=False,
        widget=forms.HiddenInput(),
    )
    language_filter = forms.ChoiceField(
        choices=[("all", "All Languages")] + list(models.EmailTemplate.Language.choices),
        required=False,
        initial="all",
        help_text="Filter templates by language",
    )
    template = forms.ModelChoiceField(
        queryset=models.EmailTemplate.objects.all(),
        required=False,
        empty_label="-- Select a template --",
        help_text="Select a template to auto-populate subject and body",
    )
    to = forms.CharField(
        widget=forms.TextInput(attrs={"class": "vTextField"}),
        help_text="Comma-separated list of email addresses",
    )
    bcc = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "vTextField"}),
        help_text="Comma-separated list of BCC email addresses (optional)",
    )
    subject = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "vTextField"}),
    )
    body = forms.CharField(
        widget=forms.Textarea(attrs={"class": "vLargeTextField", "rows": 15}),
    )
    send_in_background = forms.BooleanField(
        required=False,
        initial=True,
        help_text="Send email asynchronously via Celery",
    )

    def clean_to(self) -> list[str]:
        """Parse comma-separated email addresses."""
        value = self.cleaned_data["to"]
        emails = [e.strip() for e in value.split(",") if e.strip()]
        if not emails:
            raise forms.ValidationError("At least one recipient is required")
        return emails

    def clean_bcc(self) -> list[str]:
        """Parse comma-separated BCC email addresses."""
        value = self.cleaned_data.get("bcc", "")
        if not value:
            return []
        return [e.strip() for e in value.split(",") if e.strip()]

    def clean(self) -> dict[str, t.Any]:
        """Validate no placeholders remain in subject or body."""
        cleaned_data = super().clean()
        if cleaned_data is None:
            return {}
        subject = cleaned_data.get("subject", "")
        body = cleaned_data.get("body", "")

        unreplaced = lead_service.validate_no_placeholders(subject, body)
        if unreplaced:
            raise forms.ValidationError(f"Unreplaced placeholders found: {', '.join(unreplaced)}")

        return cleaned_data


@admin.register(models.Lead)
class LeadAdmin(ModelAdmin, SimpleHistoryAdmin, CityLinkMixin, LeadTypeLinkMixin):  # type: ignore[misc]
    """Admin for Lead model."""

    inlines = [ContactInline, OutreachMessageInline, ActionInline]
    list_display = [
        "display_name_with_notes",
        "display_company_type",
        "display_contacts",
        "city_link",
        "display_status",
        "display_temperature",
        "display_tags",
        "display_last_contact",
        "display_next_action",
        "display_value",
    ]
    list_filter = [
        CountryFilter,
        CityByCountryFilter,
        "status",
        "temperature",
        "do_not_contact",
        "lead_type",
        "tags",
        DuplicateGroupFilter,
        HasEmailFilter,
        HasPhoneFilter,
        HasInstagramFilter,
        HasTelegramFilter,
        ("created_at", RangeDateFilter),
    ]
    search_fields = [
        "name",
        "company",
        "notes",
        "contacts__email",
        "contacts__phone",
        "contacts__telegram",
        "contacts__instagram",
        "contacts__website",
        "external_ref",
        "bluesky",
        "facebook",
        "linkedin",
        "whatsapp",
    ]
    autocomplete_fields = ["city", "lead_type"]
    filter_horizontal = ["tags"]
    readonly_fields = ["created_at", "updated_at", "do_not_contact_at", "duplicate_groups"]
    date_hierarchy = "created_at"
    list_per_page = 25
    list_filter_submit = True
    save_on_top = True
    actions = [
        "merge_selected_leads",
        "mark_do_not_contact_bulk",
        "set_status_contacted",
        "set_status_replied",
        "set_status_qualified",
        "set_status_call_booked",
        "set_status_org_created",
        "set_status_first_event_live",
        "set_status_converted",
        "set_status_lost",
        "set_temp_cold",
        "set_temp_warm",
        "set_temp_hot",
    ]
    actions_submit_line = ["log_contact", "mark_contacted", "mark_converted", "mark_lost", "mark_do_not_contact"]

    # NOTE: the legacy "Contact" fieldset (email/phone/telegram/instagram/website)
    # has been removed in Phase 3. Contact info is now managed via the ContactInline.
    # The columns still exist on Lead and are dual-written by the service layer
    # until Phase 4 drops them.
    fieldsets = (
        (None, {"fields": ("name", "company", "lead_type", "city")}),
        ("Lead Info", {"fields": ("source", "status", "temperature", "tags", "duplicate_groups", "value")}),
        (
            "Social & Platforms",
            {"fields": ("bluesky", "facebook", "linkedin", "whatsapp", "ticketing_platforms", "external_ref")},
        ),
        ("Suppression", {"fields": ("do_not_contact", "do_not_contact_reason", "do_not_contact_at")}),
        ("Follow-up", {"fields": ("last_contact",)}),
        ("Notes", {"fields": ("notes",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ["collapse"]}),
    )

    class Media:
        js = ["leads/js/chained_filters.js"]

    def save_model(self, request: HttpRequest, obj: models.Lead, form: t.Any, change: bool) -> None:  # noqa: FBT001
        """Keep the do-not-contact stamp consistent."""
        lead_service.sync_do_not_contact_timestamp(obj)
        super().save_model(request, obj, form, change)

    def save_related(self, request: HttpRequest, form: t.Any, formsets: t.Any, change: bool) -> None:  # noqa: FBT001
        """Neutralise pending outreach AFTER inlines are saved, so an inline can't resurrect a draft."""
        super().save_related(request, form, formsets, change)
        lead_service.suppress_outreach(form.instance)

    def get_urls(self) -> list[t.Any]:
        """Add custom URLs for email and filter endpoints."""
        urls: list[t.Any] = super().get_urls()
        custom_urls = [
            path(
                "filter-cities/",
                self.admin_site.admin_view(self.filter_cities_view),
                name="leads_lead_filter_cities",
            ),
            path(
                "merge/",
                self.admin_site.admin_view(self.merge_leads_view),
                name="leads_lead_merge",
            ),
            path(
                "<int:lead_id>/send-email/",
                self.admin_site.admin_view(self.send_email_view),
                name="leads_lead_send_email",
            ),
            path(
                "<int:lead_id>/render-template/<int:template_id>/",
                self.admin_site.admin_view(self.render_template_view),
                name="leads_lead_render_template",
            ),
        ]
        return custom_urls + urls

    def filter_cities_view(self, request: HttpRequest) -> JsonResponse:
        """Return cities as JSON, optionally filtered by country."""
        country = request.GET.get("country")
        qs = models.City.objects.filter(leads__isnull=False).distinct().order_by("name")
        if country:
            qs = qs.filter(country=country)
        cities = [{"id": c.id, "label": str(c)} for c in qs]
        return JsonResponse({"cities": cities})

    def send_email_view(self, request: HttpRequest, lead_id: int) -> HttpResponse:
        """Custom view for sending email to a lead."""
        lead = get_object_or_404(models.Lead, id=lead_id)
        default_back_url = reverse("admin:leads_lead_change", args=[lead.id])

        if request.method == "POST":
            form = SendEmailForm(request.POST)
            if form.is_valid():
                # Check which button was clicked
                if "save_draft" in request.POST:
                    result = self._process_save_draft(request, lead, form)
                else:
                    result = self._process_send_email(request, lead, form)
                if result:
                    return result
        else:
            primary_contact = lead.contacts.filter(is_primary=True).first()
            default_to = (primary_contact.email if primary_contact and primary_contact.email else lead.email) or ""
            initial: dict[str, t.Any] = {"to": default_to} if default_to else {}

            # Check for back_url in session first (preserved across save-draft redirects)
            session_back_url = request.session.pop("send_email_back_url", None)
            if session_back_url:
                initial["back_url"] = session_back_url
            else:
                # Capture referrer for back navigation (only on initial GET)
                referrer = request.META.get("HTTP_REFERER", "")
                # Only use referrer if it's not the current page (avoid self-reference)
                current_path = request.build_absolute_uri()
                if referrer and referrer != current_path and "/send-email/" not in referrer:
                    initial["back_url"] = referrer
                else:
                    initial["back_url"] = default_back_url

            # Check if loading an existing draft
            draft_id = request.GET.get("draft_id")
            if draft_id:
                try:
                    draft = models.EmailDraft.objects.get(pk=draft_id, lead=lead)
                    initial.update(
                        {
                            "draft_id": draft.id,
                            "template": draft.template,
                            "to": ", ".join(draft.to) if draft.to else "",
                            "bcc": ", ".join(draft.bcc) if draft.bcc else "",
                            "subject": draft.subject,
                            "body": draft.body,
                        }
                    )
                except models.EmailDraft.DoesNotExist:
                    messages.warning(request, "Draft not found.")

            form = SendEmailForm(initial=initial)

        return self._render_send_email_form(request, lead, form)

    def _process_send_email(self, request: HttpRequest, lead: models.Lead, form: SendEmailForm) -> HttpResponse | None:
        """Process the send email form submission.

        Returns redirect on success, None on failure (to re-render form with errors).
        """
        data = form.cleaned_data
        template = data.get("template")
        draft_id = data.get("draft_id")
        back_url = data.get("back_url") or reverse("admin:leads_lead_change", args=[lead.id])

        try:
            if data.get("send_in_background"):
                lead_tasks.send_email_task.delay(
                    lead_id=lead.id,
                    subject=data["subject"],
                    body=data["body"],
                    to=data["to"],
                    bcc=data["bcc"],
                    template_id=template.id if template else None,
                    user_id=request.user.id,
                )
                messages.success(request, f"Email to {lead.name} queued for background sending.")
            else:
                lead_service.send_email_to_lead(
                    lead=lead,
                    subject=data["subject"],
                    body=data["body"],
                    to=data["to"],
                    bcc=data["bcc"],
                    template=template,
                    user=t.cast(User, request.user),
                )
                messages.success(request, f"Email sent successfully to {lead.name}.")

            # Delete the draft if one was being edited
            if draft_id:
                models.EmailDraft.objects.filter(id=draft_id).delete()

            return redirect(back_url)

        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Failed to send email: {e}")

        return None

    def _process_save_draft(self, request: HttpRequest, lead: models.Lead, form: SendEmailForm) -> HttpResponse | None:
        """Process save as draft form submission.

        Returns redirect on success, None on failure (to re-render form with errors).
        """
        data = form.cleaned_data
        template = data.get("template")
        draft_id = data.get("draft_id")
        back_url = data.get("back_url") or reverse("admin:leads_lead_change", args=[lead.id])

        try:
            draft = lead_service.save_email_as_draft(
                lead=lead,
                subject=data["subject"],
                body=data["body"],
                to=data["to"],
                bcc=data["bcc"],
                template=template,
                draft_id=draft_id,
                user=t.cast(User, request.user),
            )
            if draft_id:
                messages.success(request, f"Draft updated for {lead.name}.")
            else:
                messages.success(request, f"Draft saved for {lead.name}.")
            # Redirect back to the same send-email form with draft_id, so subsequent saves update
            # Also preserve back_url by storing it in session temporarily
            request.session["send_email_back_url"] = back_url
            url = reverse("admin:leads_lead_send_email", args=[lead.id])
            return redirect(f"{url}?draft_id={draft.id}")

        except Exception as e:
            messages.error(request, f"Failed to save draft: {e}")

        return None

    def _render_send_email_form(self, request: HttpRequest, lead: models.Lead, form: SendEmailForm) -> HttpResponse:
        """Render the send email form."""
        # Get template IDs already used for this lead
        used_template_ids = list(
            models.EmailSent.objects.filter(lead=lead, template__isnull=False)
            .values_list("template_id", flat=True)
            .distinct()
        )
        # Get all templates with their languages for JS filtering
        templates_by_language = {t.id: t.language for t in models.EmailTemplate.objects.all()}
        # Get next pending/in-progress action for this lead
        next_action = (
            models.Action.objects.filter(
                lead=lead,
                status__in=[models.Action.Status.PENDING, models.Action.Status.IN_PROGRESS],
            )
            .order_by("due_date", "created_at")
            .first()
        )
        # Build next_action context dict
        next_action_data: dict[str, t.Any] | None = None
        if next_action:
            is_overdue = next_action.due_date < date.today() if next_action.due_date else False
            next_action_data = {
                "name": next_action.name,
                "notes": next_action.notes,
                "due_date": next_action.due_date,
                "is_overdue": is_overdue,
            }
        # Get back_url from form (either from initial on GET, or from POST data)
        back_url = (
            form.data.get("back_url")
            or form.initial.get("back_url")
            or reverse("admin:leads_lead_change", args=[lead.id])
        )
        gmail_connected = False
        from_email = settings.DEFAULT_FROM_EMAIL
        try:
            gmail_conn = request.user.gmail_connection  # type: ignore[union-attr]
            if gmail_conn.is_active:
                from_email = gmail_conn.email
                gmail_connected = True
        except models.GmailConnection.DoesNotExist:
            pass

        context = {
            **self.admin_site.each_context(request),
            "title": f"Send Email to {lead.name}",
            "lead": lead,
            "form": form,
            "from_email": from_email,
            "gmail_connected": gmail_connected,
            "used_template_ids": used_template_ids,
            "templates_by_language": templates_by_language,
            "next_action": next_action_data,
            "back_url": back_url,
            "opts": self.model._meta,
            "has_view_permission": True,
        }
        return render(request, "admin/leads/lead/send_email.html", context)

    def render_template_view(self, request: HttpRequest, lead_id: int, template_id: int) -> JsonResponse:
        """AJAX endpoint to render a template for a lead."""
        lead = get_object_or_404(models.Lead, id=lead_id)
        template = get_object_or_404(models.EmailTemplate, id=template_id)

        subject, body = lead_service.render_email_template(template, lead)
        return JsonResponse({"subject": subject, "body": body})

    # Merging duplicates
    @admin.action(description="Merge selected leads…", permissions=["merge"])
    def merge_selected_leads(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> HttpResponse:
        """Send the selected leads to the "Merge leads" confirmation page."""
        ids = ",".join(str(pk) for pk in queryset.order_by("pk").values_list("pk", flat=True))
        return redirect(f"{reverse('admin:leads_lead_merge')}?ids={ids}")

    def has_merge_permission(self, request: HttpRequest) -> bool:
        """Merging deletes leads, so it needs both change and delete permission."""
        return bool(self.has_change_permission(request) and self.has_delete_permission(request))

    def _merge_page(
        self, request: HttpRequest, leads: list[models.Lead], primary_id: int, error: str = ""
    ) -> HttpResponse:
        """Render the merge confirmation page for ``leads`` with ``primary_id`` pre-selected."""
        counts = lead_service.summarize_merge(leads)
        rows = []
        for lead in leads:
            contact = lead.contacts.order_by("-is_primary", "pk").first()
            rows.append(
                {
                    "lead": lead,
                    "email": lead.email or (contact.email if contact else ""),
                    "instagram": lead.instagram or (contact.instagram if contact else ""),
                    "counts": counts[lead.pk],
                    "link": reverse("admin:leads_lead_change", args=[lead.pk]),
                }
            )
        context = {
            **self.admin_site.each_context(request),
            "title": "Merge leads",
            "opts": self.model._meta,
            "rows": rows,
            "primary_id": primary_id,
            "ids": ",".join(str(lead.pk) for lead in leads),
            "any_dnc": any(lead.do_not_contact for lead in leads),
            "error": error,
            "back_url": reverse("admin:leads_lead_changelist"),
        }
        return render(request, "admin/leads/lead/merge_confirm.html", context, status=400 if error else 200)

    def merge_leads_view(self, request: HttpRequest) -> HttpResponse:
        """Confirmation page (GET) and the merge itself (POST) for a set of suspected duplicates."""
        if not self.has_merge_permission(request):
            raise PermissionDenied
        raw = request.POST.get("ids") if request.method == "POST" else request.GET.get("ids", "")
        try:
            ids = list(dict.fromkeys(int(x) for x in (raw or "").split(",") if x.strip()))
        except ValueError:
            ids = []
        leads = sorted(
            models.Lead.objects.filter(pk__in=ids).select_related("city"), key=lambda lead: (lead.created_at, lead.pk)
        )
        back = reverse("admin:leads_lead_changelist")
        if len(leads) < 2:
            self.message_user(request, "Select at least two leads to merge.", messages.WARNING)
            return redirect(back)
        if len(leads) > lead_service.MERGE_MAX_LEADS:
            self.message_user(
                request, f"You can merge at most {lead_service.MERGE_MAX_LEADS} leads at once.", messages.ERROR
            )
            return redirect(back)
        default_primary = lead_service.pick_default_primary(leads)

        if request.method != "POST":
            return self._merge_page(request, leads, default_primary.pk)

        by_id = {lead.pk: lead for lead in leads}
        try:
            primary = by_id[int(request.POST.get("primary", ""))]
        except (KeyError, ValueError):
            return self._merge_page(request, leads, default_primary.pk, "Please choose which lead to keep.")
        try:
            merged = lead_service.merge_leads(primary, [lead for lead in leads if lead.pk != primary.pk], request.user)
        except lead_service.MergeError as exc:
            return self._merge_page(request, leads, primary.pk, str(exc))
        self.message_user(request, f"Merged {len(leads)} leads into '{merged.name}'.", messages.SUCCESS)
        return redirect(reverse("admin:leads_lead_change", args=[merged.pk]))

    @admin.display(description="Possible duplicates")
    def duplicate_groups(self, obj: models.Lead | None) -> str:
        """Links to open (or merge) each group of suspected duplicates this lead belongs to."""
        if obj is None or obj.pk is None:
            return "-"
        links = []
        for tag in obj.tags.filter(name__istartswith=lead_service.REVIEW_GROUP_PREFIX):
            members = list(tag.leads.order_by("pk").values_list("pk", flat=True))
            group_url = f"{reverse('admin:leads_lead_changelist')}?{urlencode({'duplicate_group': tag.name})}"
            part = format_html('<a href="{}">Open group ({} leads)</a>', group_url, len(members))
            if 2 <= len(members) <= lead_service.MERGE_MAX_LEADS:
                merge_url = f"{reverse('admin:leads_lead_merge')}?ids={','.join(map(str, members))}"
                part = format_html('{} · <a href="{}">Review and merge</a>', part, merge_url)
            links.append(part)
        return format_html_join(mark_safe("<br>"), "{}", ((link,) for link in links)) if links else "-"

    # Bulk actions for status
    @admin.action(description="→ Set status: Contacted")
    def set_status_contacted(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.CONTACTED)
        self.message_user(request, f"Updated {queryset.count()} leads to Contacted", messages.SUCCESS)

    @admin.action(description="⛔ Mark do not contact")
    def mark_do_not_contact_bulk(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        for lead in queryset:
            lead_service.mark_do_not_contact(lead)
        self.message_user(request, f"Marked {queryset.count()} leads as do not contact", messages.SUCCESS)

    @action(description="Mark Do Not Contact", url_path="mark-do-not-contact", icon="block", variant="danger")  # type: ignore[untyped-decorator]
    def mark_do_not_contact(self, request: HttpRequest, instance: models.Lead) -> HttpResponse:
        """Suppress this lead, using the reason typed in the form (if any)."""
        from django.http import HttpResponseRedirect

        reason = request.POST.get("do_not_contact_reason", "") if request.method == "POST" else ""
        lead_service.mark_do_not_contact(instance, reason)
        self.message_user(request, f"Marked '{instance.name}' as do not contact", messages.SUCCESS)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_lead_changelist")))

    @admin.action(description="→ Set status: Replied")
    def set_status_replied(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.REPLIED)
        self.message_user(request, f"Updated {queryset.count()} leads to Replied", messages.SUCCESS)

    @admin.action(description="→ Set status: Call booked")
    def set_status_call_booked(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.CALL_BOOKED)
        self.message_user(request, f"Updated {queryset.count()} leads to Call booked", messages.SUCCESS)

    @admin.action(description="→ Set status: Org created")
    def set_status_org_created(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.ORG_CREATED)
        self.message_user(request, f"Updated {queryset.count()} leads to Org created", messages.SUCCESS)

    @admin.action(description="→ Set status: First event live")
    def set_status_first_event_live(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.FIRST_EVENT_LIVE)
        self.message_user(request, f"Updated {queryset.count()} leads to First event live", messages.SUCCESS)

    @admin.action(description="→ Set status: Qualified")
    def set_status_qualified(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.QUALIFIED)
        self.message_user(request, f"Updated {queryset.count()} leads to Qualified", messages.SUCCESS)

    @admin.action(description="✓ Set status: Converted")
    def set_status_converted(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.CONVERTED)
        self.message_user(request, f"Updated {queryset.count()} leads to Converted", messages.SUCCESS)

    @admin.action(description="✗ Set status: Lost")
    def set_status_lost(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.LOST)
        self.message_user(request, f"Updated {queryset.count()} leads to Lost", messages.SUCCESS)

    # Bulk actions for temperature
    @admin.action(description="🔵 Set temperature: Cold")
    def set_temp_cold(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(temperature=models.Lead.Temperature.COLD)
        self.message_user(request, f"Updated {queryset.count()} leads to Cold", messages.SUCCESS)

    @admin.action(description="🟡 Set temperature: Warm")
    def set_temp_warm(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(temperature=models.Lead.Temperature.WARM)
        self.message_user(request, f"Updated {queryset.count()} leads to Warm", messages.SUCCESS)

    @admin.action(description="🔴 Set temperature: Hot")
    def set_temp_hot(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(temperature=models.Lead.Temperature.HOT)
        self.message_user(request, f"Updated {queryset.count()} leads to Hot", messages.SUCCESS)

    # Submit line actions for change view
    @action(description="Log Contact", url_path="log-contact", icon="event", variant="info")  # type: ignore[untyped-decorator]
    def log_contact(self, request: HttpRequest, instance: models.Lead) -> HttpResponse:
        """Set last_contact to today."""
        from django.http import HttpResponseRedirect

        instance.last_contact = date.today()
        instance.save(update_fields=["last_contact"])
        self.message_user(request, f"Logged contact for '{instance.name}'", messages.SUCCESS)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_lead_changelist")))

    @action(description="Mark Contacted", url_path="mark-contacted", icon="phone", variant="primary")  # type: ignore[untyped-decorator]
    def mark_contacted(self, request: HttpRequest, instance: models.Lead) -> HttpResponse:
        """Set status to Contacted and log contact date."""
        from django.http import HttpResponseRedirect

        instance.status = models.Lead.Status.CONTACTED
        instance.last_contact = date.today()
        instance.save(update_fields=["status", "last_contact"])
        self.message_user(request, f"Marked '{instance.name}' as Contacted", messages.SUCCESS)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_lead_changelist")))

    @action(description="Mark Converted", url_path="mark-converted", icon="check_circle", variant="success")  # type: ignore[untyped-decorator]
    def mark_converted(self, request: HttpRequest, instance: models.Lead) -> HttpResponse:
        """Set status to Converted."""
        from django.http import HttpResponseRedirect

        instance.status = models.Lead.Status.CONVERTED
        instance.save(update_fields=["status"])
        self.message_user(request, f"Marked '{instance.name}' as Converted", messages.SUCCESS)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_lead_changelist")))

    @action(description="Mark Lost", url_path="mark-lost", icon="cancel", variant="danger")  # type: ignore[untyped-decorator]
    def mark_lost(self, request: HttpRequest, instance: models.Lead) -> HttpResponse:
        """Set status to Lost."""
        from django.http import HttpResponseRedirect

        instance.status = models.Lead.Status.LOST
        instance.save(update_fields=["status"])
        self.message_user(request, f"Marked '{instance.name}' as Lost", messages.SUCCESS)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_lead_changelist")))

    def get_queryset(self, request: HttpRequest) -> QuerySet[models.Lead]:
        """Optimize queryset with select_related and prefetch_related."""
        from django.db.models import Prefetch

        qs: QuerySet[models.Lead] = super().get_queryset(request)
        pending_actions = models.Action.objects.filter(
            status__in=[models.Action.Status.PENDING, models.Action.Status.IN_PROGRESS]
        )
        return qs.select_related("city", "lead_type").prefetch_related(
            "tags",
            "contacts",
            Prefetch("actions", queryset=pending_actions, to_attr="pending_actions"),
        )

    @admin.display(description="Name", ordering="name")
    def display_name_with_notes(self, obj: models.Lead) -> str:
        """Display name with notes on hover, plus a do-not-contact badge."""
        badge = (
            format_html(
                ' <span title="{}" style="background:#b91c1c; color:#fff; padding:1px 6px; border-radius:4px; '
                'font-size:0.75em; font-weight:700;">DO NOT CONTACT</span>',
                obj.do_not_contact_reason,
            )
            if obj.do_not_contact
            else ""
        )
        if not obj.notes:
            return format_html("{}{}", obj.name, badge)
        # Truncate very long notes for tooltip (max 500 chars)
        tooltip_notes = obj.notes[:500] + "..." if len(obj.notes) > 500 else obj.notes
        return format_html(
            '<span title="{}" style="cursor: help; border-bottom: 1px dotted #999;">{}</span>{}',
            tooltip_notes,
            obj.name,
            badge,
        )

    @admin.display(description="Company / Type")
    def display_company_type(self, obj: models.Lead) -> str:
        """Display company and lead type combined."""
        parts = []
        if obj.company:
            parts.append(f"<strong>{obj.company}</strong>")
        if obj.lead_type:
            parts.append(f"<span style='color: #666; font-size: 0.85em;'>{obj.lead_type}</span>")
        return mark_safe("<br>".join(parts)) if parts else "-"

    @admin.display(description="Contacts")
    def display_contacts(self, obj: models.Lead) -> str:
        """Stack each contact's methods vertically with name/role headers."""
        contacts = list(obj.contacts.all())
        if not contacts:
            return "-"
        send_url = reverse("admin:leads_lead_send_email", args=[obj.id])
        blocks = [_render_contact_block(c, send_url) for c in contacts]
        return mark_safe('<div style="display: flex; flex-direction: column; gap: 6px;">' + "".join(blocks) + "</div>")

    @admin.display(description="Status")
    def display_status(self, obj: models.Lead) -> str:
        """Display status with colored badge."""
        colors: dict[str, str] = {
            models.Lead.Status.NEW: "#3b82f6",  # blue
            models.Lead.Status.CONTACTED: "#f59e0b",  # amber
            models.Lead.Status.REPLIED: "#8b5cf6",  # violet
            models.Lead.Status.QUALIFIED: "#10b981",  # green
            models.Lead.Status.CALL_BOOKED: "#06b6d4",  # cyan
            models.Lead.Status.ORG_CREATED: "#6366f1",  # indigo
            models.Lead.Status.FIRST_EVENT_LIVE: "#14b8a6",  # teal
            models.Lead.Status.CONVERTED: "#059669",  # darker green
            models.Lead.Status.LOST: "#ef4444",  # red
        }
        color = colors.get(obj.status, "#666")
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 0.8em;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Temp")
    def display_temperature(self, obj: models.Lead) -> str:
        """Display temperature as colored indicator."""
        indicators: dict[str, tuple[str, str]] = {
            models.Lead.Temperature.COLD: ("🔵", "Cold"),
            models.Lead.Temperature.WARM: ("🟡", "Warm"),
            models.Lead.Temperature.HOT: ("🔴", "Hot"),
        }
        icon, label = indicators.get(obj.temperature, ("⚪", "Unknown"))
        return format_html('<span title="{}">{}</span>', label, icon)

    @admin.display(description="Tags")
    def display_tags(self, obj: models.Lead) -> str:
        """Display tags as colored pills."""
        tags = obj.tags.all()
        if not tags:
            return "-"
        parts = []
        for tag in tags:
            parts.append(
                format_html(
                    '<span style="background: #e5e7eb; color: #374151; padding: 1px 6px; '
                    'border-radius: 9999px; font-size: 0.75em; margin-right: 2px;">{}</span>',
                    tag.name,
                )
            )
            if tag.name.lower().startswith(lead_service.REVIEW_GROUP_PREFIX):
                url = f"{reverse('admin:leads_lead_changelist')}?{urlencode({'duplicate_group': tag.name})}"
                parts.append(
                    format_html('<a href="{}" style="font-size: 0.75em; margin-right: 4px;">Open group</a>', url)
                )
        return format_html_join(" ", "{}", ((part,) for part in parts))

    @admin.display(description="Last Contact")
    def display_last_contact(self, obj: models.Lead) -> str:
        """Display days since last contact with color coding."""
        if not obj.last_contact:
            return format_html('<span style="color: #9ca3af;">Never</span>')

        days = (date.today() - obj.last_contact).days
        if days == 0:
            return format_html('<span style="color: #10b981;">Today</span>')
        elif days <= 7:
            return format_html('<span style="color: #10b981;">{} days ago</span>', days)
        elif days <= 30:
            return format_html('<span style="color: #f59e0b;">{} days ago</span>', days)
        else:
            return format_html('<span style="color: #ef4444;">{} days ago</span>', days)

    @admin.display(description="Next Action")
    def display_next_action(self, obj: models.Lead) -> str:
        """Display next action with date color coding, linked to the Action, with notes on hover."""
        # Use prefetched pending_actions (ordered by due_date nulls last, then created_at)
        pending_actions: list[models.Action] = getattr(obj, "pending_actions", [])
        if not pending_actions:
            return "-"

        action = pending_actions[0]
        action_text = action.name[:20]
        action_url = reverse("admin:leads_action_change", args=[action.id])

        # Prepare tooltip and styling for notes (similar to display_name_with_notes)
        tooltip_notes = ""
        notes_style = ""
        if action.notes:
            tooltip_notes = action.notes[:500] + "..." if len(action.notes) > 500 else action.notes
            notes_style = "cursor: help; border-bottom: 1px dotted currentColor;"

        if not action.due_date:
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #666; {}" title="{}">{}</a>',
                action_url,
                notes_style,
                tooltip_notes,
                action_text,
            )

        today = date.today()
        days_until = (action.due_date - today).days

        if days_until < 0:
            # Overdue - red
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #ef4444; font-weight: bold; {}" '
                'title="{}">{}<br><span style="font-size: 0.8em;">⚠️ {} days overdue</span></a>',
                action_url,
                notes_style,
                tooltip_notes,
                action_text,
                abs(days_until),
            )
        elif days_until == 0:
            # Today - orange
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #f59e0b; font-weight: bold; {}" '
                'title="{}">{}<br><span style="font-size: 0.8em;">📅 Today</span></a>',
                action_url,
                notes_style,
                tooltip_notes,
                action_text,
            )
        elif days_until <= 3:
            # Soon - amber
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #d97706; {}" title="{}">'
                '{}<br><span style="font-size: 0.8em;">In {} days</span></a>',
                action_url,
                notes_style,
                tooltip_notes,
                action_text,
                days_until,
            )
        else:
            # Future - normal
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="{}" title="{}">'
                '{}<br><span style="font-size: 0.8em; color: #666;">{}</span></a>',
                action_url,
                notes_style,
                tooltip_notes,
                action_text,
                action.due_date.strftime("%b %d"),
            )

    @admin.display(description="Value")
    def display_value(self, obj: models.Lead) -> str:
        """Display value with currency formatting."""
        if obj.value is None:
            return "-"
        formatted = f"€{obj.value:,.0f}"
        return format_html('<span style="font-family: monospace;">{}</span>', formatted)


@admin.register(models.Action)
class ActionAdmin(ModelAdmin, SimpleHistoryAdmin):  # type: ignore[misc]
    """Admin for Action model."""

    list_display = ["name", "lead_link", "display_status", "display_notes", "display_due_date", "created_at"]
    list_filter = ["status", ("due_date", RangeDateFilter), ("created_at", RangeDateFilter)]
    search_fields = ["name", "notes", "lead__name"]
    autocomplete_fields = ["lead"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "due_date"
    list_per_page = 25
    actions = ["mark_completed_bulk", "mark_cancelled_bulk"]
    actions_submit_line = ["mark_completed_single"]

    fieldsets = (
        (None, {"fields": ("lead", "name", "notes", "status", "due_date", "completed_at")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ["collapse"]}),
    )

    def get_queryset(self, request: HttpRequest) -> QuerySet[models.Action]:
        """Optimize queryset with select_related."""
        qs: QuerySet[models.Action] = super().get_queryset(request)
        return qs.select_related("lead")

    def lead_link(self, obj: models.Action) -> str:
        """Return a link to the lead."""
        url = reverse("admin:leads_lead_change", args=[obj.lead.id])
        return format_html('<a href="{}">{}</a>', url, obj.lead.name)

    lead_link.short_description = "Lead"  # type: ignore[attr-defined]

    @admin.display(description="Status")
    def display_status(self, obj: models.Action) -> str:
        """Display status with colored badge."""
        colors: dict[str, str] = {
            models.Action.Status.PENDING: "#f59e0b",  # amber
            models.Action.Status.IN_PROGRESS: "#3b82f6",  # blue
            models.Action.Status.COMPLETED: "#10b981",  # green
            models.Action.Status.CANCELLED: "#6b7280",  # gray
        }
        color = colors.get(obj.status, "#666")
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 0.8em;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.display(description="Notes")
    def display_notes(self, obj: models.Action) -> str:
        """Display truncated notes with full text on hover."""
        if not obj.notes:
            return "-"
        truncated = obj.notes[:50] + "..." if len(obj.notes) > 50 else obj.notes
        return format_html(
            '<span title="{}" style="cursor: help;">{}</span>',
            obj.notes,
            truncated,
        )

    @admin.display(description="Due Date")
    def display_due_date(self, obj: models.Action) -> str:
        """Display due date with color coding."""
        if not obj.due_date:
            return "-"

        today = date.today()
        days_until = (obj.due_date - today).days

        if obj.status == models.Action.Status.COMPLETED:
            return format_html('<span style="color: #9ca3af;">{}</span>', obj.due_date.strftime("%b %d"))
        elif days_until < 0:
            return format_html(
                '<span style="color: #ef4444; font-weight: bold;">⚠️ {} days overdue</span>',
                abs(days_until),
            )
        elif days_until == 0:
            return format_html('<span style="color: #f59e0b; font-weight: bold;">📅 Today</span>')
        elif days_until <= 3:
            return format_html('<span style="color: #d97706;">In {} days</span>', days_until)
        else:
            return format_html("<span>{}</span>", obj.due_date.strftime("%b %d"))

    @admin.action(description="✓ Mark as Completed")
    def mark_completed_bulk(self, request: HttpRequest, queryset: QuerySet[models.Action]) -> None:
        """Mark selected actions as completed."""
        from django.utils import timezone

        queryset.update(status=models.Action.Status.COMPLETED, completed_at=timezone.now())
        self.message_user(request, f"Marked {queryset.count()} actions as completed", messages.SUCCESS)

    @admin.action(description="✗ Mark as Cancelled")
    def mark_cancelled_bulk(self, request: HttpRequest, queryset: QuerySet[models.Action]) -> None:
        """Mark selected actions as cancelled."""
        queryset.update(status=models.Action.Status.CANCELLED)
        self.message_user(request, f"Marked {queryset.count()} actions as cancelled", messages.SUCCESS)

    @action(
        description="Mark Completed",
        url_path="mark-completed",
        icon="task_alt",
        variant="success",
        attrs={"target": "_self"},
    )  # type: ignore[untyped-decorator]
    def mark_completed_single(self, request: HttpRequest, instance: models.Action) -> HttpResponse:
        """Mark this action as completed."""
        from django.http import HttpResponseRedirect
        from django.utils import timezone

        if instance.status != models.Action.Status.COMPLETED:
            instance.status = models.Action.Status.COMPLETED
            instance.completed_at = timezone.now()
            instance.save(update_fields=["status", "completed_at"])
            self.message_user(request, f"Marked '{instance.name}' as completed", messages.SUCCESS)
        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_action_changelist")))


class OutreachQueueFilter(admin.SimpleListFilter):
    """Queue scope. Defaults to my (and unassigned) drafts; one click shows everyone's drafts or everything.

    Suppressed (do-not-contact) leads never appear in the drafts queues.
    """

    title = "queue"
    parameter_name = "queue"

    def lookups(self, request: HttpRequest, model_admin: t.Any) -> list[tuple[str, str]]:
        return [("drafts", "All drafts"), ("all", "Everything")]

    def choices(self, changelist: t.Any) -> t.Iterator[t.Any]:
        yield {
            "selected": self.value() is None,
            "query_string": changelist.get_query_string(remove=[self.parameter_name]),
            "display": "Mine + unassigned",
        }
        for lookup, title in self.lookup_choices:
            yield {
                "selected": self.value() == str(lookup),
                "query_string": changelist.get_query_string({self.parameter_name: lookup}),
                "display": title,
            }

    def queryset(self, request: HttpRequest, queryset: QuerySet[t.Any]) -> QuerySet[t.Any]:
        if self.value() == "all":
            return queryset
        queryset = queryset.filter(status=models.OutreachMessage.Status.DRAFT, lead__do_not_contact=False)
        if self.value() == "drafts":
            return queryset
        return queryset.filter(Q(assigned_to=request.user) | Q(assigned_to__isnull=True))


def _require_same_origin(request: HttpRequest) -> None:
    """Row actions are plain GET links (Unfold cannot POST them), so be strict about where the click came from.

    Requires Sec-Fetch-Site of same-origin/none AND a Referer on this exact host; a missing header is refused.
    """
    if request.headers.get("Sec-Fetch-Site") not in ("same-origin", "none"):
        raise PermissionDenied
    referer = request.headers.get("Referer", "")
    if not referer or urlparse(referer).netloc != request.get_host():
        raise PermissionDenied


@admin.register(models.OutreachMessage)
class OutreachMessageAdmin(ModelAdmin, SimpleHistoryAdmin):  # type: ignore[misc]
    """Admin for OutreachMessage: the rep's DM queue (open deeplink, paste text, send by hand, mark sent)."""

    list_display = ["lead_link", "channel", "open_dm", "text_with_copy", "display_humanized", "batch", "display_status"]
    list_display_links = ["channel"]
    list_filter = [OutreachQueueFilter, "status", "channel", "assigned_to", "batch"]
    search_fields = ["text", "lead__name", "batch"]
    autocomplete_fields = ["lead"]
    readonly_fields = ["status", "sent_at", "sent_by", "open_dm", "display_humanized", "created_at", "updated_at"]
    list_per_page = 25
    list_filter_submit = True
    actions = ["mark_sent_bulk", "skip_bulk"]
    actions_row = ["mark_sent_row", "skip_row"]
    actions_submit_line = ["mark_sent_detail", "skip_detail"]

    fieldsets = (
        (None, {"fields": ("lead", "channel", "open_dm", "text", "assigned_to", "batch")}),
        ("Status", {"fields": ("status", "sent_at", "sent_by", "humanized_hash", "display_humanized")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ["collapse"]}),
    )

    class Media:
        js = ["leads/js/outreach_copy.js"]

    def get_queryset(self, request: HttpRequest) -> QuerySet[models.OutreachMessage]:
        qs: QuerySet[models.OutreachMessage] = super().get_queryset(request)
        return qs.select_related("lead", "assigned_to", "sent_by").prefetch_related("lead__contacts")

    @admin.display(description="Lead", ordering="lead__name")
    def lead_link(self, obj: models.OutreachMessage) -> str:
        url = reverse("admin:leads_lead_change", args=[obj.lead_id])
        return format_html('<a href="{}">{}</a>', url, obj.lead.name)

    @admin.display(description="Open DM")
    def open_dm(self, obj: models.OutreachMessage) -> str:
        return _dm_link(obj) if obj.pk else "-"

    @admin.display(description="Message")
    def text_with_copy(self, obj: models.OutreachMessage) -> str:
        return format_html(
            '<div style="display:flex; gap:8px; align-items:flex-start; max-width:640px;">'
            '<span style="white-space:pre-wrap;">{}</span>'
            '<button type="button" class="js-copy-text" data-copy-text="{}" '
            'style="border:1px solid #9ca3af; border-radius:6px; padding:2px 8px; font-size:0.8em;">Copy</button>'
            "</div>",
            obj.text,
            obj.text,
        )

    @admin.display(description="Humanized")
    def display_humanized(self, obj: models.OutreachMessage) -> str:
        return _humanized_badge(obj.is_humanized, obj.humanized_hash)

    @admin.display(description="Status", ordering="status")
    def display_status(self, obj: models.OutreachMessage) -> str:
        colors: dict[str, str] = {
            models.OutreachMessage.Status.DRAFT: "#f59e0b",
            models.OutreachMessage.Status.SENT: "#10b981",
            models.OutreachMessage.Status.SKIPPED: "#6b7280",
        }
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 0.8em;">{}</span>',
            colors.get(obj.status, "#666"),
            obj.get_status_display(),
        )

    def _mark_sent(self, request: HttpRequest, queryset: QuerySet[models.OutreachMessage]) -> int:
        count = 0
        for message in queryset.exclude(status=models.OutreachMessage.Status.SENT):
            try:
                lead_service.mark_message_sent(message, t.cast(User, request.user))
            except HttpError as e:
                self.message_user(request, f"{message.lead.name}: {e.message}", messages.ERROR)
            else:
                count += 1
        return count

    def _skip(self, request: HttpRequest, queryset: QuerySet[models.OutreachMessage]) -> int:
        count = 0
        for message in queryset.exclude(status=models.OutreachMessage.Status.SENT):
            lead_service.mark_message_skipped(message)
            count += 1
        return count

    @admin.action(description="Mark sent")
    def mark_sent_bulk(self, request: HttpRequest, queryset: QuerySet[models.OutreachMessage]) -> None:
        count = self._mark_sent(request, queryset)
        self.message_user(request, f"Marked {count} message(s) as sent", messages.SUCCESS)

    @admin.action(description="Skip")
    def skip_bulk(self, request: HttpRequest, queryset: QuerySet[models.OutreachMessage]) -> None:
        count = self._skip(request, queryset)
        self.message_user(request, f"Skipped {count} message(s)", messages.SUCCESS)

    def _back(self, request: HttpRequest) -> HttpResponse:
        from django.http import HttpResponseRedirect

        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_outreachmessage_changelist")))

    @action(description="Mark sent", url_path="mark-sent", icon="send", variant="success", attrs={"target": "_self"})  # type: ignore[untyped-decorator]
    def mark_sent_row(self, request: HttpRequest, object_id: int) -> HttpResponse:
        _require_same_origin(request)
        count = self._mark_sent(request, self.get_queryset(request).filter(pk=object_id))
        self.message_user(request, f"Marked {count} message(s) as sent", messages.SUCCESS)
        return self._back(request)

    @action(description="Skip", url_path="skip", icon="skip_next", variant="default", attrs={"target": "_self"})  # type: ignore[untyped-decorator]
    def skip_row(self, request: HttpRequest, object_id: int) -> HttpResponse:
        _require_same_origin(request)
        count = self._skip(request, self.get_queryset(request).filter(pk=object_id))
        self.message_user(request, f"Skipped {count} message(s)", messages.SUCCESS)
        return self._back(request)

    @action(description="Mark sent", url_path="mark-sent-detail", icon="send", variant="success")  # type: ignore[untyped-decorator]
    def mark_sent_detail(self, request: HttpRequest, instance: models.OutreachMessage) -> HttpResponse:
        try:
            lead_service.mark_message_sent(instance, t.cast(User, request.user))
        except HttpError as e:
            self.message_user(request, e.message, messages.ERROR)
        else:
            self.message_user(request, f"Marked message to '{instance.lead.name}' as sent", messages.SUCCESS)
        return self._back(request)

    @action(description="Skip", url_path="skip-detail", icon="skip_next", variant="default")  # type: ignore[untyped-decorator]
    def skip_detail(self, request: HttpRequest, instance: models.OutreachMessage) -> HttpResponse:
        try:
            lead_service.mark_message_skipped(instance)
        except ValueError as e:
            self.message_user(request, str(e), messages.ERROR)
        else:
            self.message_user(request, f"Skipped message to '{instance.lead.name}'", messages.SUCCESS)
        return self._back(request)


@admin.register(models.EmailTemplate)
class EmailTemplateAdmin(ModelAdmin, SimpleHistoryAdmin):  # type: ignore[misc]
    """Admin for EmailTemplate model."""

    change_form_template = "admin/leads/emailtemplate/change_form.html"
    list_display = ["name", "language", "subject", "updated_at"]
    list_filter = ["language"]
    search_fields = ["name", "subject", "body"]
    readonly_fields = ["created_at", "updated_at"]
    ordering = ["name"]

    fieldsets = (
        (None, {"fields": ("name", "language", "subject", "body")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ["collapse"]}),
    )


@admin.register(models.EmailSent)
class EmailSentAdmin(ModelAdmin):  # type: ignore[misc]
    """Admin for EmailSent model."""

    list_display = ["id", "lead_link", "subject", "display_status", "display_recipients", "sent_at", "created_at"]
    list_filter = ["status", ("sent_at", RangeDateFilter), ("created_at", RangeDateFilter)]
    search_fields = ["subject", "body", "lead__name", "to", "from_email"]
    readonly_fields = [
        "lead",
        "template",
        "from_email",
        "to",
        "bcc",
        "subject",
        "display_body",
        "status",
        "error_message",
        "sent_by",
        "created_at",
        "sent_at",
    ]
    ordering = ["-created_at"]
    list_per_page = 25
    actions = ["mark_sent_confirmed", "mark_failed"]

    def get_actions(self, request: HttpRequest) -> t.Any:
        """Resolving a pending send is superuser-only."""
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("mark_sent_confirmed", None)
            actions.pop("mark_failed", None)
        return actions

    def _resolve_pending(
        self, request: HttpRequest, queryset: QuerySet[models.EmailSent], action: str, title: str
    ) -> HttpResponse | None:
        """Confirm page, then apply ``action`` ("sent" or "failed") to the PENDING rows in the queryset."""
        pending = list(queryset.filter(status=models.EmailSent.Status.PENDING).select_related("lead"))
        if not pending:
            self.message_user(request, "No pending emails selected.", messages.WARNING)
            return None
        if request.POST.get("confirm") != "yes":
            return render(
                request,
                "admin/leads/emailsent/resolve_confirm.html",
                {
                    **self.admin_site.each_context(request),
                    "title": title,
                    "emails": pending,
                    "action": action,
                    "action_checkbox_name": ACTION_CHECKBOX_NAME,
                    "selected": [e.pk for e in pending],
                    "opts": self.model._meta,
                },
            )
        for email in pending:
            if action == "sent":
                lead_service.finalize_sent(email)
            else:
                lead_service.mark_send_failed(email, f"Marked failed by {request.user}: not delivered per Gmail")
        self.message_user(request, f"Marked {len(pending)} email(s) as {action}.", messages.SUCCESS)
        return None

    @admin.action(description="Mark sent (confirmed in Gmail)", permissions=["resolve"])
    def mark_sent_confirmed(self, request: HttpRequest, queryset: QuerySet[models.EmailSent]) -> HttpResponse | None:
        """Pending rows that Gmail's Sent folder shows as delivered: apply the normal send side effects."""
        return self._resolve_pending(request, queryset, "sent", "Mark pending emails as sent")

    @admin.action(description="Mark failed (not delivered)", permissions=["resolve"])
    def mark_failed(self, request: HttpRequest, queryset: QuerySet[models.EmailSent]) -> HttpResponse | None:
        """Pending rows that Gmail's Sent folder does not show: allow the lead to be emailed again."""
        return self._resolve_pending(request, queryset, "failed", "Mark pending emails as failed")

    def has_resolve_permission(self, request: HttpRequest) -> bool:
        """Only superusers may resolve pending sends."""
        return bool(request.user.is_superuser)

    fieldsets = (
        (None, {"fields": ("lead", "template", "status", "sent_by")}),
        ("Recipients", {"fields": ("from_email", "to", "bcc")}),
        ("Content", {"fields": ("subject", "display_body")}),
        ("Status", {"fields": ("error_message", "created_at", "sent_at")}),
    )

    @admin.display(description="Body")
    def display_body(self, obj: models.EmailSent) -> str:
        """Render HTML email body."""
        return format_html(
            '<div style="background: white; padding: 1rem; border: 1px solid #e5e7eb; '
            'border-radius: 0.375rem; max-height: 400px; overflow-y: auto;">{}</div>',
            mark_safe(obj.body),
        )

    def lead_link(self, obj: models.EmailSent) -> str:
        """Return a link to the lead."""
        url = reverse("admin:leads_lead_change", args=[obj.lead.id])
        return format_html('<a href="{}">{}</a>', url, obj.lead.name)

    lead_link.short_description = "Lead"  # type: ignore[attr-defined]

    @admin.display(description="Status")
    def display_status(self, obj: models.EmailSent) -> str:
        """Display status with colored badge."""
        colors: dict[str, str] = {
            models.EmailSent.Status.PENDING: "#f59e0b",  # amber
            models.EmailSent.Status.SENT: "#10b981",  # green
            models.EmailSent.Status.FAILED: "#ef4444",  # red
        }
        color = colors.get(obj.status, "#666")
        label = obj.get_status_display()
        if lead_service.is_stale_pending(obj):
            label = "Pending: send outcome unknown, check Gmail Sent"
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 0.8em;">{}</span>',
            color,
            label,
        )

    @admin.display(description="To")
    def display_recipients(self, obj: models.EmailSent) -> str:
        """Display recipient list."""
        recipients: list[str] = obj.to if isinstance(obj.to, list) else []
        if not recipients:
            return "-"
        if len(recipients) == 1:
            return recipients[0]
        return f"{recipients[0]} (+{len(recipients) - 1})"


@admin.register(models.EmailDraft)
class EmailDraftAdmin(SimpleHistoryAdmin, ModelAdmin):  # type: ignore[misc]
    """Admin for EmailDraft model."""

    change_form_template = "admin/leads/emaildraft/change_form.html"

    list_display = [
        "id",
        "edit_link",
        "lead_link",
        "subject_preview",
        "assigned_to",
        "display_humanized",
        "template",
        "updated_at",
        "created_at",
    ]
    list_display_links = None  # Disable default linking since we have custom edit_link
    list_filter = ["assigned_to", "template", ("created_at", RangeDateFilter), ("updated_at", RangeDateFilter)]
    search_fields = ["subject", "body", "lead__name", "to"]
    readonly_fields = ["id", "display_humanized", "created_at", "updated_at"]
    autocomplete_fields = ["lead", "template"]
    actions = ["send_selected_drafts"]
    actions_submit_line = ["send_draft"]
    ordering = ["-updated_at"]
    list_per_page = 25

    fieldsets = (
        (None, {"fields": ("id", "lead", "template")}),
        ("Recipients", {"fields": ("assigned_to", "from_email", "to", "bcc")}),
        ("Content", {"fields": ("subject", "body", "humanized_hash", "display_humanized", "batch")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Humanized")
    def display_humanized(self, obj: models.EmailDraft) -> str:
        return _humanized_badge(obj.is_humanized, obj.humanized_hash)

    @admin.display(description="Edit")
    def edit_link(self, obj: models.EmailDraft) -> str:
        """Return a link to the email writing form."""
        url = reverse("admin:leads_lead_send_email", args=[obj.lead.id])
        return format_html('<a href="{}?draft_id={}">Edit</a>', url, obj.id)

    def lead_link(self, obj: models.EmailDraft) -> str:
        """Return a link to the lead."""
        url = reverse("admin:leads_lead_change", args=[obj.lead.id])
        return format_html('<a href="{}">{}</a>', url, obj.lead.name)

    lead_link.short_description = "Lead"  # type: ignore[attr-defined]

    @admin.display(description="Subject")
    def subject_preview(self, obj: models.EmailDraft) -> str:
        """Display subject preview."""
        if not obj.subject:
            return "(no subject)"
        return obj.subject[:60] + "..." if len(obj.subject) > 60 else obj.subject

    @admin.action(description="Send selected drafts")
    def send_selected_drafts(self, request: HttpRequest, queryset: QuerySet[models.EmailDraft]) -> None:
        """Send all selected drafts."""
        sent_count = 0
        failed_count = 0
        for draft in queryset:
            try:
                lead_service.send_email_draft(draft, user=t.cast(User, request.user))
                sent_count += 1
            except Exception as e:
                failed_count += 1
                self.message_user(
                    request,
                    f"Failed to send draft '{draft.subject[:30]}...' to {draft.lead.name}: {e}",
                    messages.ERROR,
                )

        if sent_count:
            self.message_user(
                request,
                f"Successfully sent {sent_count} email(s).",
                messages.SUCCESS,
            )

    @action(description="Send Draft", url_path="send", icon="send", variant="primary")  # type: ignore[untyped-decorator]
    def send_draft(self, request: HttpRequest, instance: models.EmailDraft) -> HttpResponse:
        """Send this draft email.

        Saves any pending form changes before sending to ensure the latest version is sent.
        """
        from django.http import HttpResponseRedirect

        # Save form data first if this is a POST with form fields
        if request.method == "POST" and "subject" in request.POST:
            # Update instance with form data before sending
            instance.subject = request.POST.get("subject", instance.subject)
            instance.body = request.POST.get("body", instance.body)
            instance.from_email = request.POST.get("from_email", instance.from_email)
            # Handle list fields - stored as JSON in the model
            to_value = request.POST.get("to", "")
            if to_value:
                instance.to = [e.strip() for e in to_value.split(",") if e.strip()]
            bcc_value = request.POST.get("bcc", "")
            if bcc_value:
                instance.bcc = [e.strip() for e in bcc_value.split(",") if e.strip()]
            else:
                instance.bcc = []
            # Handle foreign keys
            template_id = request.POST.get("template")
            if template_id:
                instance.template_id = int(template_id)
            else:
                instance.template = None
            lead_id = request.POST.get("lead")
            if lead_id:
                instance.lead_id = int(lead_id)
            instance.save()

        try:
            email_sent = lead_service.send_email_draft(instance, user=t.cast(User, request.user))
            messages.success(request, f"Email sent successfully to {email_sent.lead.name}.")
            # Redirect to lead change view since draft is now deleted
            return HttpResponseRedirect(reverse("admin:leads_lead_change", args=[email_sent.lead.pk]))
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Failed to send email: {e}")

        return HttpResponseRedirect(reverse("admin:leads_emaildraft_change", args=[instance.pk]))


@admin.register(models.GmailConnection)
class GmailConnectionAdmin(ModelAdmin):  # type: ignore[misc]
    list_display = ["user", "email", "is_active", "connected_at", "updated_at"]
    list_filter = ["is_active"]
    readonly_fields = ["user", "email", "is_active", "connected_at", "updated_at"]
    exclude = ["refresh_token"]

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: t.Any = None) -> bool:
        return False


@admin.register(models.EmailSignature)
class EmailSignatureAdmin(ModelAdmin):  # type: ignore[misc]
    """Admin for per-user email signatures."""

    change_form_template = "admin/leads/emailsignature/change_form.html"
    list_display = ["user", "updated_at"]
    readonly_fields = ["created_at", "updated_at"]
    autocomplete_fields = ["user"]

    fieldsets = (
        (None, {"fields": ("user", "body")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ["collapse"]}),
    )


@admin.register(models.UserApiToken)
class UserApiTokenAdmin(ModelAdmin):  # type: ignore[misc]
    """Personal API tokens: staff create/revoke their own (plaintext shown once); superusers see all."""

    list_display = ["name", "user", "prefix", "created_at", "last_used_at", "status"]
    list_filter = ["user"]
    readonly_fields = ["user", "prefix", "created_at", "last_used_at", "revoked_at"]
    actions = ["revoke_selected"]

    def get_fields(self, request: HttpRequest, obj: models.UserApiToken | None = None) -> list[str]:
        """Only the name when creating; read-only details afterwards."""
        if obj is None:
            return ["name"]
        return ["name", "user", "prefix", "created_at", "last_used_at", "revoked_at"]

    def get_readonly_fields(self, request: HttpRequest, obj: models.UserApiToken | None = None) -> list[str]:
        """The name is only editable on creation."""
        return [*self.readonly_fields, "name"] if obj is not None else []

    def get_queryset(self, request: HttpRequest) -> QuerySet[models.UserApiToken]:
        """Superusers see every token; everyone else only their own."""
        qs: QuerySet[models.UserApiToken] = super().get_queryset(request).select_related("user")
        return qs if request.user.is_superuser else qs.filter(user=t.cast(User, request.user))

    def has_module_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.is_active and request.user.is_staff)

    def has_view_permission(self, request: HttpRequest, obj: models.UserApiToken | None = None) -> bool:
        return bool(request.user.is_active and request.user.is_staff)

    def has_add_permission(self, request: HttpRequest) -> bool:
        return bool(request.user.is_active and request.user.is_staff)

    def has_change_permission(self, request: HttpRequest, obj: models.UserApiToken | None = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: models.UserApiToken | None = None) -> bool:
        return False

    @admin.display(description="Status")
    def status(self, obj: models.UserApiToken) -> str:
        return "active" if obj.is_active else "revoked"

    def save_model(self, request: HttpRequest, obj: models.UserApiToken, form: forms.ModelForm, change: bool) -> None:  # type: ignore[type-arg]
        """Issue a token for the requesting user and show the plaintext exactly once."""
        if change:
            return
        row, plaintext = models.UserApiToken.issue(t.cast(User, request.user), obj.name)
        obj.pk = row.pk
        obj.user = row.user
        obj.token_hash = row.token_hash
        obj.prefix = row.prefix
        obj.created_at = row.created_at
        messages.warning(
            request,
            format_html(
                "Your new API token (copy it now, it will not be shown again): <code>{}</code>",
                plaintext,
            ),
        )

    @admin.action(description="Revoke selected tokens")
    def revoke_selected(self, request: HttpRequest, queryset: QuerySet[models.UserApiToken]) -> None:
        """Revoke the selected tokens (own tokens only, unless superuser; the queryset is already scoped)."""
        count = 0
        for token in queryset.filter(revoked_at__isnull=True):
            token.revoke()
            count += 1
        self.message_user(request, f"Revoked {count} token(s).", messages.SUCCESS)


@admin.register(models.ResearchPromptConfig)
class ResearchPromptConfigAdmin(SingletonModelAdmin, ModelAdmin):  # type: ignore[misc]
    """Admin for ResearchPromptConfig singleton."""

    pass


@admin.register(models.ResearchJob)
class ResearchJobAdmin(ModelAdmin, CityLinkMixin):  # type: ignore[misc]
    """Admin for ResearchJob model."""

    list_display = ["id", "city_link", "display_status", "leads_created", "created_at", "completed_at"]
    list_filter = ["status", "city__country"]
    search_fields = ["city__name"]
    readonly_fields = [
        "gemini_interaction_id",
        "raw_result",
        "result",
        "error",
        "leads_created",
        "created_at",
        "completed_at",
    ]
    ordering = ["-created_at"]
    actions = ["run_job", "reprocess_job"]
    actions_submit_line = ["run_job_single", "reprocess_job_single"]

    def get_actions(self, request: HttpRequest) -> dict[str, t.Any]:
        """Only superusers can trigger research (Gemini API calls are expensive)."""
        actions: dict[str, t.Any] = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("run_job", None)
            actions.pop("reprocess_job", None)
        return actions

    def get_actions_submit_line(self, request: HttpRequest, object_id: int) -> list[t.Any]:
        """Only superusers can see run/reprocess buttons on detail page."""
        if not request.user.is_superuser:
            return []
        return super().get_actions_submit_line(request, object_id)  # type: ignore[no-any-return]

    @admin.display(description="Status")
    def display_status(self, obj: models.ResearchJob) -> str:
        """Display status with colored badge."""
        colors: dict[str, str] = {
            models.ResearchJob.Status.NOT_STARTED: "#6b7280",  # gray
            models.ResearchJob.Status.PENDING: "#f59e0b",
            models.ResearchJob.Status.RUNNING: "#3b82f6",
            models.ResearchJob.Status.COMPLETED: "#10b981",
            models.ResearchJob.Status.FAILED: "#ef4444",
        }
        color = colors.get(obj.status, "#666")
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 0.8em;">{}</span>',
            color,
            obj.get_status_display(),
        )

    @admin.action(description="🚀 Run Job")
    def run_job(self, request: HttpRequest, queryset: QuerySet[models.ResearchJob]) -> None:
        """Run research jobs.

        Queues jobs for starting via the rate-limited start_research_job task (1/min).
        Only NOT_STARTED and FAILED jobs can be started.
        """
        from leads.tasks import start_research_job

        allowed_statuses = {
            models.ResearchJob.Status.NOT_STARTED,
            models.ResearchJob.Status.FAILED,
        }

        queued = 0
        for job in queryset:
            if job.status not in allowed_statuses:
                self.message_user(request, f"Job #{job.id} is {job.get_status_display()}, skipping", messages.WARNING)
                continue

            # Set to PENDING and clear interaction_id before queuing to allow fresh start
            job.status = models.ResearchJob.Status.PENDING
            job.gemini_interaction_id = ""
            job.save()
            start_research_job.delay(job.id)
            queued += 1

        if queued:
            self.message_user(request, f"Queued {queued} job(s) for processing", messages.SUCCESS)

    @action(description="Run Job", url_path="run-job", icon="rocket_launch", variant="primary")  # type: ignore[untyped-decorator]
    def run_job_single(self, request: HttpRequest, instance: models.ResearchJob) -> HttpResponse:
        """Run this research job."""
        from django.http import HttpResponseRedirect

        from leads.tasks import start_research_job

        allowed_statuses = {
            models.ResearchJob.Status.NOT_STARTED,
            models.ResearchJob.Status.FAILED,
        }

        if instance.status not in allowed_statuses:
            self.message_user(
                request, f"Job #{instance.id} is {instance.get_status_display()}, cannot run", messages.WARNING
            )
        else:
            # Set to PENDING and clear interaction_id before queuing to allow fresh start
            instance.status = models.ResearchJob.Status.PENDING
            instance.gemini_interaction_id = ""
            instance.save()
            start_research_job.delay(instance.id)
            self.message_user(request, f"Queued job #{instance.id} for processing", messages.SUCCESS)

        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_researchjob_changelist")))

    @admin.action(description="🔄 Reprocess Job")
    def reprocess_job(self, request: HttpRequest, queryset: QuerySet[models.ResearchJob]) -> None:
        """Reprocess jobs that have raw_result but failed during parsing.

        This retries parsing/lead creation without re-running Gemini research.
        """
        from leads.tasks import reprocess_job

        processed = 0
        for job in queryset:
            if not job.raw_result:
                self.message_user(request, f"Job #{job.id} has no raw_result to reprocess", messages.WARNING)
                continue

            try:
                result = reprocess_job(job.id)
                self.message_user(
                    request, f"Reprocessed job #{job.id}: created {result['leads_created']} leads", messages.SUCCESS
                )
                processed += 1
            except Exception as e:
                self.message_user(request, f"Failed to reprocess job #{job.id}: {e}", messages.ERROR)

        if processed:
            self.message_user(request, f"Successfully reprocessed {processed} job(s)", messages.SUCCESS)

    @action(description="Reprocess Job", url_path="reprocess-job", icon="refresh", variant="warning")  # type: ignore[untyped-decorator]
    def reprocess_job_single(self, request: HttpRequest, instance: models.ResearchJob) -> HttpResponse:
        """Reprocess this research job."""
        from django.http import HttpResponseRedirect

        from leads.tasks import reprocess_job

        if not instance.raw_result:
            self.message_user(request, f"Job #{instance.id} has no raw_result to reprocess", messages.WARNING)
        else:
            try:
                result = reprocess_job(instance.id)
                self.message_user(
                    request,
                    f"Reprocessed job #{instance.id}: created {result['leads_created']} leads",
                    messages.SUCCESS,
                )
            except Exception as e:
                self.message_user(request, f"Failed to reprocess job #{instance.id}: {e}", messages.ERROR)

        return HttpResponseRedirect(request.META.get("HTTP_REFERER", reverse("admin:leads_researchjob_changelist")))
