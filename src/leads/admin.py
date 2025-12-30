import typing as t
from datetime import date

from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.db.models import DateField, QuerySet
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from simple_history.admin import SimpleHistoryAdmin
from solo.admin import SingletonModelAdmin
from unfold.admin import ModelAdmin, TabularInline
from unfold.contrib.filters.admin import RangeDateFilter
from unfold.decorators import action
from unfold.widgets import UnfoldAdminSingleDateWidget

from . import models
from . import service as lead_service
from . import tasks as lead_tasks


class HasContactFieldFilter(admin.SimpleListFilter):
    """Base filter for checking if a contact field has a value."""

    field_name = ""  # Override in subclasses

    def lookups(self, request: HttpRequest, model_admin: t.Any) -> list[tuple[str, str]]:
        """Return filter options."""
        return [
            ("yes", "Yes"),
            ("no", "No"),
        ]

    def queryset(self, request: HttpRequest, queryset: QuerySet[t.Any]) -> QuerySet[t.Any]:
        """Filter queryset based on whether field has a value."""
        if self.value() == "yes":
            return queryset.exclude(**{f"{self.field_name}__exact": ""})
        if self.value() == "no":
            return queryset.filter(**{f"{self.field_name}__exact": ""})
        return queryset


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
        initial=False,
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

    inlines = [ActionInline]
    list_display = [
        "display_name_with_notes",
        "display_company_type",
        "display_email",
        "display_socials",
        "city_link",
        "display_status",
        "display_temperature",
        "display_tags",
        "display_last_contact",
        "display_next_action",
        "display_value",
    ]
    list_filter = [
        "status",
        "temperature",
        "lead_type",
        "tags",
        ("city", admin.RelatedOnlyFieldListFilter),
        "city__country",
        HasEmailFilter,
        HasPhoneFilter,
        HasInstagramFilter,
        HasTelegramFilter,
        ("created_at", RangeDateFilter),
    ]
    search_fields = ["name", "email", "company", "notes", "telegram", "instagram", "website"]
    autocomplete_fields = ["city", "lead_type"]
    filter_horizontal = ["tags"]
    readonly_fields = ["created_at", "updated_at"]
    date_hierarchy = "created_at"
    list_per_page = 25
    save_on_top = True
    actions = [
        "set_status_contacted",
        "set_status_qualified",
        "set_status_converted",
        "set_status_lost",
        "set_temp_cold",
        "set_temp_warm",
        "set_temp_hot",
    ]
    actions_submit_line = ["log_contact", "mark_contacted", "mark_converted", "mark_lost"]

    fieldsets = (
        (None, {"fields": ("name", "company", "lead_type", "city")}),
        ("Contact", {"fields": ("email", "phone", "telegram", "instagram", "website")}),
        ("Lead Info", {"fields": ("source", "status", "temperature", "tags", "value")}),
        ("Follow-up", {"fields": ("last_contact",)}),
        ("Notes", {"fields": ("notes",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ["collapse"]}),
    )

    def get_urls(self) -> list[t.Any]:
        """Add custom URLs for email functionality."""
        urls: list[t.Any] = super().get_urls()
        custom_urls = [
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

    def send_email_view(self, request: HttpRequest, lead_id: int) -> HttpResponse:
        """Custom view for sending email to a lead."""
        lead = get_object_or_404(models.Lead, id=lead_id)

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
            initial: dict[str, t.Any] = {"to": lead.email} if lead.email else {}

            # Check if loading an existing draft
            draft_id = request.GET.get("draft_id")
            if draft_id:
                try:
                    draft = models.EmailDraft.objects.get(pk=draft_id, lead=lead)
                    initial = {
                        "draft_id": draft.id,
                        "template": draft.template,
                        "to": ", ".join(draft.to) if draft.to else "",
                        "bcc": ", ".join(draft.bcc) if draft.bcc else "",
                        "subject": draft.subject,
                        "body": draft.body,
                    }
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

        try:
            if data.get("send_in_background"):
                lead_tasks.send_email_task.delay(
                    lead_id=lead.id,
                    subject=data["subject"],
                    body=data["body"],
                    to=data["to"],
                    bcc=data["bcc"],
                    template_id=template.id if template else None,
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
                )
                messages.success(request, f"Email sent successfully to {lead.name}.")

            # Delete the draft if one was being edited
            if draft_id:
                models.EmailDraft.objects.filter(id=draft_id).delete()

            return redirect(reverse("admin:leads_lead_change", args=[lead.id]))

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

        try:
            draft = lead_service.save_email_as_draft(
                lead=lead,
                subject=data["subject"],
                body=data["body"],
                to=data["to"],
                bcc=data["bcc"],
                template=template,
                draft_id=draft_id,
            )
            if draft_id:
                messages.success(request, f"Draft updated for {lead.name}.")
            else:
                messages.success(request, f"Draft saved for {lead.name}.")
            # Redirect back to the same send-email form with draft_id, so subsequent saves update
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
        context = {
            **self.admin_site.each_context(request),
            "title": f"Send Email to {lead.name}",
            "lead": lead,
            "form": form,
            "from_email": settings.DEFAULT_FROM_EMAIL,
            "used_template_ids": used_template_ids,
            "templates_by_language": templates_by_language,
            "next_action": next_action_data,
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

    # Bulk actions for status
    @admin.action(description="→ Set status: Contacted")
    def set_status_contacted(self, request: HttpRequest, queryset: QuerySet[models.Lead]) -> None:
        queryset.update(status=models.Lead.Status.CONTACTED)
        self.message_user(request, f"Updated {queryset.count()} leads to Contacted", messages.SUCCESS)

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
            Prefetch("actions", queryset=pending_actions, to_attr="pending_actions"),
        )

    @admin.display(description="Name", ordering="name")
    def display_name_with_notes(self, obj: models.Lead) -> str:
        """Display name with notes on hover."""
        if not obj.notes:
            return obj.name
        # Truncate very long notes for tooltip (max 500 chars)
        tooltip_notes = obj.notes[:500] + "..." if len(obj.notes) > 500 else obj.notes
        return format_html(
            '<span title="{}" style="cursor: help; border-bottom: 1px dotted #999;">{}</span>',
            tooltip_notes,
            obj.name,
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

    @admin.display(description="Email")
    def display_email(self, obj: models.Lead) -> str:
        """Display email with link to send email view."""
        if not obj.email:
            return "-"
        send_url = reverse("admin:leads_lead_send_email", args=[obj.id])
        return format_html(
            '<a href="{}" onclick="event.stopPropagation()" title="Send email to {}">{}</a>',
            send_url,
            obj.email,
            obj.email,
        )

    @admin.display(description="Links")
    def display_socials(self, obj: models.Lead) -> str:
        """Display social links as icons."""
        links = []
        if obj.telegram:
            tg_url = obj.telegram if obj.telegram.startswith("http") else f"https://t.me/{obj.telegram.lstrip('@')}"
            links.append(
                f'<a href="{tg_url}" target="_blank" title="{obj.telegram}" onclick="event.stopPropagation()">📱</a>'
            )
        if obj.instagram:
            ig_handle = obj.instagram.lstrip("@")
            links.append(
                f'<a href="https://instagram.com/{ig_handle}" target="_blank" title="@{ig_handle}" '
                f'onclick="event.stopPropagation()">📷</a>'
            )
        if obj.website:
            links.append(
                f'<a href="{obj.website}" target="_blank" title="{obj.website}" '
                f'onclick="event.stopPropagation()">🌐</a>'
            )
        return mark_safe(" ".join(links)) if links else "-"

    @admin.display(description="Status")
    def display_status(self, obj: models.Lead) -> str:
        """Display status with colored badge."""
        colors: dict[str, str] = {
            models.Lead.Status.NEW: "#3b82f6",  # blue
            models.Lead.Status.CONTACTED: "#f59e0b",  # amber
            models.Lead.Status.QUALIFIED: "#10b981",  # green
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
        pills = []
        for tag in tags:
            pills.append(
                f'<span style="background: #e5e7eb; color: #374151; padding: 1px 6px; '
                f'border-radius: 9999px; font-size: 0.75em; margin-right: 2px;">{tag.name}</span>'
            )
        return mark_safe(" ".join(pills))

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
        """Display next action with date color coding, linked to the Action."""
        # Use prefetched pending_actions (ordered by due_date nulls last, then created_at)
        pending_actions: list[models.Action] = getattr(obj, "pending_actions", [])
        if not pending_actions:
            return "-"

        action = pending_actions[0]
        action_text = action.name[:20]
        action_url = reverse("admin:leads_action_change", args=[action.id])

        if not action.due_date:
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #666;">{}</a>',
                action_url,
                action_text,
            )

        today = date.today()
        days_until = (action.due_date - today).days

        if days_until < 0:
            # Overdue - red
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #ef4444; font-weight: bold;">'
                '{}<br><span style="font-size: 0.8em;">⚠️ {} days overdue</span></a>',
                action_url,
                action_text,
                abs(days_until),
            )
        elif days_until == 0:
            # Today - orange
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #f59e0b; font-weight: bold;">'
                '{}<br><span style="font-size: 0.8em;">📅 Today</span></a>',
                action_url,
                action_text,
            )
        elif days_until <= 3:
            # Soon - amber
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()" style="color: #d97706;">'
                '{}<br><span style="font-size: 0.8em;">In {} days</span></a>',
                action_url,
                action_text,
                days_until,
            )
        else:
            # Future - normal
            return format_html(
                '<a href="{}" onclick="event.stopPropagation()">'
                '{}<br><span style="font-size: 0.8em; color: #666;">{}</span></a>',
                action_url,
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


@admin.register(models.EmailTemplate)
class EmailTemplateAdmin(ModelAdmin, SimpleHistoryAdmin):  # type: ignore[misc]
    """Admin for EmailTemplate model."""

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
        "body",
        "status",
        "error_message",
        "created_at",
        "sent_at",
    ]
    ordering = ["-created_at"]
    list_per_page = 25

    fieldsets = (
        (None, {"fields": ("lead", "template", "status")}),
        ("Recipients", {"fields": ("from_email", "to", "bcc")}),
        ("Content", {"fields": ("subject", "body")}),
        ("Status", {"fields": ("error_message", "created_at", "sent_at")}),
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
        return format_html(
            '<span style="background: {}; color: white; padding: 2px 8px; '
            'border-radius: 4px; font-size: 0.8em;">{}</span>',
            color,
            obj.get_status_display(),
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

    list_display = ["id", "edit_link", "lead_link", "subject_preview", "template", "updated_at", "created_at"]
    list_display_links = None  # Disable default linking since we have custom edit_link
    list_filter = ["template", ("created_at", RangeDateFilter), ("updated_at", RangeDateFilter)]
    search_fields = ["subject", "body", "lead__name", "to"]
    readonly_fields = ["id", "created_at", "updated_at"]
    autocomplete_fields = ["lead", "template"]
    actions = ["send_selected_drafts"]
    actions_submit_line = ["send_draft"]
    ordering = ["-updated_at"]
    list_per_page = 25

    fieldsets = (
        (None, {"fields": ("id", "lead", "template")}),
        ("Recipients", {"fields": ("from_email", "to", "bcc")}),
        ("Content", {"fields": ("subject", "body")}),
        ("Timestamps", {"fields": ("created_at", "updated_at")}),
    )

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
                lead_service.send_email_draft(draft)
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
            email_sent = lead_service.send_email_draft(instance)
            messages.success(request, f"Email sent successfully to {email_sent.lead.name}.")
            # Redirect to lead change view since draft is now deleted
            return HttpResponseRedirect(reverse("admin:leads_lead_change", args=[email_sent.lead.pk]))
        except ValueError as e:
            messages.error(request, str(e))
        except Exception as e:
            messages.error(request, f"Failed to send email: {e}")

        return HttpResponseRedirect(reverse("admin:leads_emaildraft_change", args=[instance.pk]))


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
