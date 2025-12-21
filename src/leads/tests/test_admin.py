"""Tests for leads admin."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpRequest
from django.test import RequestFactory

from leads import models
from leads.admin import (
    ActionAdmin,
    CityAdmin,
    CityLinkMixin,
    HasEmailFilter,
    HasInstagramFilter,
    HasPhoneFilter,
    HasTelegramFilter,
    LeadAdmin,
    LeadTypeAdmin,
    LeadTypeLinkMixin,
    ResearchJobAdmin,
    TagAdmin,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def request_factory() -> RequestFactory:
    """Return a request factory."""
    return RequestFactory()


@pytest.fixture
def admin_request(request_factory: RequestFactory) -> HttpRequest:
    """Create an admin request with message support."""
    request = request_factory.get("/admin/")
    request.user = MagicMock()
    request.session = {}  # type: ignore[assignment]
    request._messages = FallbackStorage(request)  # type: ignore[attr-defined]
    return request


@pytest.fixture
def site() -> AdminSite:
    """Return the admin site."""
    return AdminSite()


# --- Admin Registration Tests ---


class TestAdminRegistration:
    def test_city_admin_registered(self) -> None:
        assert models.City in admin.site._registry
        assert isinstance(admin.site._registry[models.City], CityAdmin)

    def test_lead_type_admin_registered(self) -> None:
        assert models.LeadType in admin.site._registry
        assert isinstance(admin.site._registry[models.LeadType], LeadTypeAdmin)

    def test_tag_admin_registered(self) -> None:
        assert models.Tag in admin.site._registry
        assert isinstance(admin.site._registry[models.Tag], TagAdmin)

    def test_lead_admin_registered(self) -> None:
        assert models.Lead in admin.site._registry
        assert isinstance(admin.site._registry[models.Lead], LeadAdmin)

    def test_action_admin_registered(self) -> None:
        assert models.Action in admin.site._registry
        assert isinstance(admin.site._registry[models.Action], ActionAdmin)

    def test_research_job_admin_registered(self) -> None:
        assert models.ResearchJob in admin.site._registry
        assert isinstance(admin.site._registry[models.ResearchJob], ResearchJobAdmin)


# --- Custom Filter Tests ---


class TestHasEmailFilter:
    @pytest.fixture
    def lead_admin(self, site: AdminSite) -> LeadAdmin:
        return LeadAdmin(models.Lead, site)

    def test_lookups(self, request_factory: RequestFactory, lead_admin: LeadAdmin) -> None:
        request = request_factory.get("/admin/")
        filter_instance = HasEmailFilter(request, request.GET.copy(), models.Lead, lead_admin)
        lookups = filter_instance.lookups(request, lead_admin)
        assert lookups == [("yes", "Yes"), ("no", "No")]

    def test_filter_yes(self, request_factory: RequestFactory, lead: models.Lead, lead_admin: LeadAdmin) -> None:
        # Lead fixture has email - create one without email to verify filtering
        lead_no_email = models.Lead.objects.create(name="No Email", email="")
        # Django expects QueryDict (not plain dict)
        request = request_factory.get("/admin/", {"has_email": "yes"})
        filter_instance = HasEmailFilter(request, request.GET.copy(), models.Lead, lead_admin)
        qs = filter_instance.queryset(request, models.Lead.objects.all())
        assert lead in qs  # Has email - included
        assert lead_no_email not in qs  # No email - excluded

    def test_filter_no(self, request_factory: RequestFactory, lead_admin: LeadAdmin) -> None:
        # Create leads with and without email
        lead_with_email = models.Lead.objects.create(name="With Email", email="test@example.com")
        lead_no_email = models.Lead.objects.create(name="No Email Lead", email="")
        # Django expects QueryDict (uses value[-1] to get last list element)
        request = request_factory.get("/admin/", {"has_email": "no"})
        filter_instance = HasEmailFilter(request, request.GET.copy(), models.Lead, lead_admin)
        qs = filter_instance.queryset(request, models.Lead.objects.all())
        assert lead_no_email in qs
        assert lead_with_email not in qs

    def test_filter_none(self, request_factory: RequestFactory, lead: models.Lead, lead_admin: LeadAdmin) -> None:
        request = request_factory.get("/admin/")
        filter_instance = HasEmailFilter(request, request.GET.copy(), models.Lead, lead_admin)
        qs = filter_instance.queryset(request, models.Lead.objects.all())
        assert lead in qs  # All leads returned


class TestHasPhoneFilter:
    def test_field_name(self) -> None:
        assert HasPhoneFilter.field_name == "phone"
        assert HasPhoneFilter.parameter_name == "has_phone"


class TestHasInstagramFilter:
    def test_field_name(self) -> None:
        assert HasInstagramFilter.field_name == "instagram"
        assert HasInstagramFilter.parameter_name == "has_instagram"


class TestHasTelegramFilter:
    def test_field_name(self) -> None:
        assert HasTelegramFilter.field_name == "telegram"
        assert HasTelegramFilter.parameter_name == "has_telegram"


# --- Mixin Tests ---


class TestCityLinkMixin:
    def test_city_link_with_city(self, lead: models.Lead) -> None:
        mixin = CityLinkMixin()
        result = mixin.city_link(lead)
        assert lead.city is not None
        assert lead.city.name in result
        assert f"/admin/leads/city/{lead.city.id}/change/" in result

    def test_city_link_without_city(self) -> None:
        lead = models.Lead(name="No City Lead")
        mixin = CityLinkMixin()
        result = mixin.city_link(lead)
        assert result == "-"


class TestLeadTypeLinkMixin:
    def test_lead_type_link_with_type(self, lead: models.Lead) -> None:
        mixin = LeadTypeLinkMixin()
        result = mixin.lead_type_link(lead)
        assert lead.lead_type is not None
        assert lead.lead_type.name in result
        assert f"/admin/leads/leadtype/{lead.lead_type.id}/change/" in result

    def test_lead_type_link_without_type(self) -> None:
        lead = models.Lead(name="No Type Lead")
        mixin = LeadTypeLinkMixin()
        result = mixin.lead_type_link(lead)
        assert result == "-"


# --- Queryset Annotation Tests ---


class TestCityAdminQueryset:
    def test_lead_count_annotation(self, admin_request: HttpRequest, site: AdminSite, lead: models.Lead) -> None:
        assert lead.city is not None
        admin_instance = CityAdmin(models.City, site)
        qs = admin_instance.get_queryset(admin_request)
        city = qs.get(id=lead.city.id)
        assert hasattr(city, "_lead_count")
        assert city._lead_count == 1

    def test_lead_count_display(self, admin_request: HttpRequest, site: AdminSite, lead: models.Lead) -> None:
        assert lead.city is not None
        admin_instance = CityAdmin(models.City, site)
        qs = admin_instance.get_queryset(admin_request)
        city = qs.get(id=lead.city.id)
        assert admin_instance.lead_count(city) == 1


class TestLeadTypeAdminQueryset:
    def test_lead_count_annotation(self, admin_request: HttpRequest, site: AdminSite, lead: models.Lead) -> None:
        assert lead.lead_type is not None
        admin_instance = LeadTypeAdmin(models.LeadType, site)
        qs = admin_instance.get_queryset(admin_request)
        lead_type = qs.get(id=lead.lead_type.id)
        assert hasattr(lead_type, "_lead_count")
        assert lead_type._lead_count == 1


class TestTagAdminQueryset:
    def test_lead_count_annotation(self, admin_request: HttpRequest, site: AdminSite, lead: models.Lead) -> None:
        admin_instance = TagAdmin(models.Tag, site)
        qs = admin_instance.get_queryset(admin_request)
        first_tag = lead.tags.first()
        assert first_tag is not None
        tag = qs.get(id=first_tag.id)
        assert hasattr(tag, "_lead_count")
        assert tag._lead_count == 1


# --- LeadAdmin Display Methods ---


class TestLeadAdminDisplayMethods:
    @pytest.fixture
    def lead_admin(self, site: AdminSite) -> LeadAdmin:
        return LeadAdmin(models.Lead, site)

    def test_display_name_with_notes(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.notes = "Test notes"
        result = lead_admin.display_name_with_notes(lead)
        assert lead.name in result
        assert "Test notes" in result
        assert "title=" in result  # Has tooltip

    def test_display_name_without_notes(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.notes = ""
        result = lead_admin.display_name_with_notes(lead)
        assert result == lead.name

    def test_display_company_type_both(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.company = "Test Co"
        result = lead_admin.display_company_type(lead)
        assert lead.lead_type is not None
        assert "Test Co" in result
        assert lead.lead_type.name in result

    def test_display_company_type_none(self, lead_admin: LeadAdmin) -> None:
        lead = models.Lead(name="No company")
        result = lead_admin.display_company_type(lead)
        assert result == "-"

    def test_display_email_with_email(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        result = lead_admin.display_email(lead)
        assert "mailto:" in result
        assert lead.email in result

    def test_display_email_without_email(self, lead_admin: LeadAdmin) -> None:
        lead = models.Lead(name="No email", email="")
        result = lead_admin.display_email(lead)
        assert result == "-"

    def test_display_socials_all(self, lead_admin: LeadAdmin) -> None:
        lead = models.Lead(
            name="Social Lead",
            telegram="@test",
            instagram="test_ig",
            website="https://example.com",
        )
        result = lead_admin.display_socials(lead)
        assert "t.me/test" in result
        assert "instagram.com/test_ig" in result
        assert "https://example.com" in result

    def test_display_socials_none(self, lead_admin: LeadAdmin) -> None:
        lead = models.Lead(name="No socials")
        result = lead_admin.display_socials(lead)
        assert result == "-"

    def test_display_status(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.status = models.Lead.Status.CONTACTED
        result = lead_admin.display_status(lead)
        assert "Contacted" in result
        assert "background:" in result

    def test_display_temperature(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.temperature = models.Lead.Temperature.HOT
        result = lead_admin.display_temperature(lead)
        assert "Hot" in result

    def test_display_tags(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        result = lead_admin.display_tags(lead)
        first_tag = lead.tags.first()
        assert first_tag is not None
        assert first_tag.name in result

    def test_display_tags_empty(self, lead_admin: LeadAdmin) -> None:
        lead = models.Lead.objects.create(name="No tags")
        result = lead_admin.display_tags(lead)
        assert result == "-"

    def test_display_last_contact_never(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.last_contact = None
        result = lead_admin.display_last_contact(lead)
        assert "Never" in result

    def test_display_last_contact_today(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.last_contact = date.today()
        result = lead_admin.display_last_contact(lead)
        assert "Today" in result

    def test_display_last_contact_recent(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.last_contact = date.today() - timedelta(days=3)
        result = lead_admin.display_last_contact(lead)
        assert "3 days ago" in result

    def test_display_last_contact_old(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.last_contact = date.today() - timedelta(days=45)
        result = lead_admin.display_last_contact(lead)
        assert "45 days ago" in result

    def test_display_value_with_value(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.value = Decimal("1500")
        result = lead_admin.display_value(lead)
        assert "1,500" in result

    def test_display_value_without_value(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.value = None
        result = lead_admin.display_value(lead)
        assert result == "-"


# --- LeadAdmin Next Action Display ---


class TestLeadAdminNextActionDisplay:
    @pytest.fixture
    def lead_admin(self, site: AdminSite) -> LeadAdmin:
        return LeadAdmin(models.Lead, site)

    def test_display_next_action_no_actions(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        lead.pending_actions = []  # type: ignore[attr-defined]
        result = lead_admin.display_next_action(lead)
        assert result == "-"

    def test_display_next_action_overdue(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        action = models.Action.objects.create(
            lead=lead,
            name="Overdue Task",
            status=models.Action.Status.PENDING,
            due_date=date.today() - timedelta(days=5),
        )
        lead.pending_actions = [action]  # type: ignore[attr-defined]
        result = lead_admin.display_next_action(lead)
        assert "overdue" in result
        assert "5 days" in result

    def test_display_next_action_today(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        action = models.Action.objects.create(
            lead=lead,
            name="Today Task",
            status=models.Action.Status.PENDING,
            due_date=date.today(),
        )
        lead.pending_actions = [action]  # type: ignore[attr-defined]
        result = lead_admin.display_next_action(lead)
        assert "Today" in result

    def test_display_next_action_future(self, lead_admin: LeadAdmin, lead: models.Lead) -> None:
        action = models.Action.objects.create(
            lead=lead,
            name="Future Task",
            status=models.Action.Status.PENDING,
            due_date=date.today() + timedelta(days=10),
        )
        lead.pending_actions = [action]  # type: ignore[attr-defined]
        result = lead_admin.display_next_action(lead)
        assert "Future Task" in result


# --- ActionAdmin Display Methods ---


class TestActionAdminDisplayMethods:
    @pytest.fixture
    def action_admin(self, site: AdminSite) -> ActionAdmin:
        return ActionAdmin(models.Action, site)

    def test_lead_link(self, action_admin: ActionAdmin, action: models.Action) -> None:
        result = action_admin.lead_link(action)
        assert action.lead.name in result
        assert f"/admin/leads/lead/{action.lead.id}/change/" in result

    def test_display_status(self, action_admin: ActionAdmin, action: models.Action) -> None:
        result = action_admin.display_status(action)
        assert "Pending" in result
        assert "background:" in result

    def test_display_notes_with_notes(self, action_admin: ActionAdmin, action: models.Action) -> None:
        action.notes = "Test notes for the action"
        result = action_admin.display_notes(action)
        assert "Test notes" in result

    def test_display_notes_empty(self, action_admin: ActionAdmin, action: models.Action) -> None:
        action.notes = ""
        result = action_admin.display_notes(action)
        assert result == "-"

    def test_display_due_date_overdue(self, action_admin: ActionAdmin, action: models.Action) -> None:
        action.due_date = date.today() - timedelta(days=3)
        action.status = models.Action.Status.PENDING
        result = action_admin.display_due_date(action)
        assert "overdue" in result

    def test_display_due_date_today(self, action_admin: ActionAdmin, action: models.Action) -> None:
        action.due_date = date.today()
        action.status = models.Action.Status.PENDING
        result = action_admin.display_due_date(action)
        assert "Today" in result

    def test_display_due_date_completed(self, action_admin: ActionAdmin, action: models.Action) -> None:
        action.due_date = date.today() - timedelta(days=3)
        action.status = models.Action.Status.COMPLETED
        result = action_admin.display_due_date(action)
        # Completed tasks show grayed out date, not overdue
        assert "overdue" not in result


# --- ResearchJobAdmin Display Methods ---


class TestResearchJobAdminDisplayMethods:
    @pytest.fixture
    def job_admin(self, site: AdminSite) -> ResearchJobAdmin:
        return ResearchJobAdmin(models.ResearchJob, site)

    def test_display_status(self, job_admin: ResearchJobAdmin, research_job: models.ResearchJob) -> None:
        result = job_admin.display_status(research_job)
        assert "Not Started" in result
        assert "background:" in result


# --- Bulk Action Tests ---


class TestLeadBulkActions:
    @pytest.fixture
    def lead_admin(self, site: AdminSite) -> LeadAdmin:
        return LeadAdmin(models.Lead, site)

    def test_set_status_contacted(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_status_contacted(admin_request, qs)
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.CONTACTED

    def test_set_status_qualified(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_status_qualified(admin_request, qs)
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.QUALIFIED

    def test_set_status_converted(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_status_converted(admin_request, qs)
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.CONVERTED

    def test_set_status_lost(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_status_lost(admin_request, qs)
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.LOST

    def test_set_temp_cold(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_temp_cold(admin_request, qs)
        lead.refresh_from_db()
        assert lead.temperature == models.Lead.Temperature.COLD

    def test_set_temp_warm(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_temp_warm(admin_request, qs)
        lead.refresh_from_db()
        assert lead.temperature == models.Lead.Temperature.WARM

    def test_set_temp_hot(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        qs = models.Lead.objects.filter(id=lead.id)
        lead_admin.set_temp_hot(admin_request, qs)
        lead.refresh_from_db()
        assert lead.temperature == models.Lead.Temperature.HOT


class TestActionBulkActions:
    @pytest.fixture
    def action_admin(self, site: AdminSite) -> ActionAdmin:
        return ActionAdmin(models.Action, site)

    def test_mark_completed_bulk(
        self, action_admin: ActionAdmin, admin_request: HttpRequest, action: models.Action
    ) -> None:
        qs = models.Action.objects.filter(id=action.id)
        action_admin.mark_completed_bulk(admin_request, qs)
        action.refresh_from_db()
        assert action.status == models.Action.Status.COMPLETED
        assert action.completed_at is not None

    def test_mark_cancelled_bulk(
        self, action_admin: ActionAdmin, admin_request: HttpRequest, action: models.Action
    ) -> None:
        qs = models.Action.objects.filter(id=action.id)
        action_admin.mark_cancelled_bulk(admin_request, qs)
        action.refresh_from_db()
        assert action.status == models.Action.Status.CANCELLED


# --- CityAdmin Research Action ---


class TestCityAdminResearchAction:
    @pytest.fixture
    def city_admin(self, site: AdminSite) -> CityAdmin:
        return CityAdmin(models.City, site)

    @patch("leads.tasks.queue_research")
    def test_start_research(
        self, mock_queue: MagicMock, city_admin: CityAdmin, admin_request: HttpRequest, city: models.City
    ) -> None:
        qs = models.City.objects.filter(id=city.id)
        city_admin.start_research(admin_request, qs)
        mock_queue.assert_called_once_with(city.id)

    @patch("leads.tasks.queue_research")
    def test_start_research_error(
        self, mock_queue: MagicMock, city_admin: CityAdmin, admin_request: HttpRequest, city: models.City
    ) -> None:
        mock_queue.side_effect = RuntimeError("Already running")
        qs = models.City.objects.filter(id=city.id)
        # Should not raise, just show warning message
        city_admin.start_research(admin_request, qs)


# --- ResearchJobAdmin Actions ---


class TestResearchJobAdminActions:
    @pytest.fixture
    def job_admin(self, site: AdminSite) -> ResearchJobAdmin:
        return ResearchJobAdmin(models.ResearchJob, site)

    @patch("leads.tasks.start_research_job")
    def test_run_job(
        self,
        mock_task: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        qs = models.ResearchJob.objects.filter(id=research_job.id)
        job_admin.run_job(admin_request, qs)
        research_job.refresh_from_db()
        assert research_job.status == models.ResearchJob.Status.PENDING
        mock_task.delay.assert_called_once_with(research_job.id)

    @patch("leads.tasks.start_research_job")
    def test_run_job_skips_running(
        self,
        mock_task: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        research_job.status = models.ResearchJob.Status.RUNNING
        research_job.save()
        qs = models.ResearchJob.objects.filter(id=research_job.id)
        job_admin.run_job(admin_request, qs)
        mock_task.delay.assert_not_called()

    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job(
        self, mock_reprocess: MagicMock, job_admin: ResearchJobAdmin, admin_request: HttpRequest, city: models.City
    ) -> None:
        job = models.ResearchJob.objects.create(
            city=city,
            status=models.ResearchJob.Status.COMPLETED,
            raw_result='{"leads": []}',
        )
        mock_reprocess.return_value = {"leads_created": 2}
        qs = models.ResearchJob.objects.filter(id=job.id)
        job_admin.reprocess_job(admin_request, qs)
        mock_reprocess.assert_called_once_with(job.id)

    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job_no_raw_result(
        self,
        mock_reprocess: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        qs = models.ResearchJob.objects.filter(id=research_job.id)
        job_admin.reprocess_job(admin_request, qs)
        mock_reprocess.assert_not_called()

    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job_handles_exception(
        self,
        mock_reprocess: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        city: models.City,
    ) -> None:
        job = models.ResearchJob.objects.create(
            city=city,
            status=models.ResearchJob.Status.COMPLETED,
            raw_result='{"leads": []}',
        )
        mock_reprocess.side_effect = ValueError("Parsing failed")
        qs = models.ResearchJob.objects.filter(id=job.id)
        # Should not raise - exception is caught and shown as error message
        job_admin.reprocess_job(admin_request, qs)
        mock_reprocess.assert_called_once_with(job.id)


# --- Submit Line Action Tests ---


class TestLeadSubmitLineActions:
    """Tests for LeadAdmin submit line actions (single instance)."""

    @pytest.fixture
    def lead_admin(self, site: AdminSite) -> LeadAdmin:
        return LeadAdmin(models.Lead, site)

    def test_log_contact(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        lead.last_contact = None
        lead.save()

        response = lead_admin.log_contact(admin_request, lead)

        assert response.status_code == 302  # Redirect
        lead.refresh_from_db()
        assert lead.last_contact == date.today()

    def test_mark_contacted(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        lead.status = models.Lead.Status.NEW
        lead.last_contact = None
        lead.save()

        response = lead_admin.mark_contacted(admin_request, lead)

        assert response.status_code == 302
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.CONTACTED
        assert lead.last_contact == date.today()

    def test_mark_converted(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        lead.status = models.Lead.Status.CONTACTED
        lead.save()

        response = lead_admin.mark_converted(admin_request, lead)

        assert response.status_code == 302
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.CONVERTED

    def test_mark_lost(self, lead_admin: LeadAdmin, admin_request: HttpRequest, lead: models.Lead) -> None:
        lead.status = models.Lead.Status.CONTACTED
        lead.save()

        response = lead_admin.mark_lost(admin_request, lead)

        assert response.status_code == 302
        lead.refresh_from_db()
        assert lead.status == models.Lead.Status.LOST


class TestActionSubmitLineActions:
    """Tests for ActionAdmin submit line actions (single instance)."""

    @pytest.fixture
    def action_admin(self, site: AdminSite) -> ActionAdmin:
        return ActionAdmin(models.Action, site)

    def test_mark_completed_single(
        self, action_admin: ActionAdmin, admin_request: HttpRequest, action: models.Action
    ) -> None:
        action.status = models.Action.Status.PENDING
        action.completed_at = None
        action.save()

        response = action_admin.mark_completed_single(admin_request, action)

        assert response.status_code == 302
        action.refresh_from_db()
        assert action.status == models.Action.Status.COMPLETED
        assert action.completed_at is not None

    def test_mark_completed_single_already_completed(
        self, action_admin: ActionAdmin, admin_request: HttpRequest, action: models.Action
    ) -> None:
        """Test idempotency - calling on already completed action does nothing."""
        from django.utils import timezone

        original_completed_at = timezone.now()
        action.status = models.Action.Status.COMPLETED
        action.completed_at = original_completed_at
        action.save()

        response = action_admin.mark_completed_single(admin_request, action)

        assert response.status_code == 302
        action.refresh_from_db()
        assert action.status == models.Action.Status.COMPLETED
        # completed_at should not be updated
        assert action.completed_at == original_completed_at


class TestResearchJobSubmitLineActions:
    """Tests for ResearchJobAdmin submit line actions (single instance)."""

    @pytest.fixture
    def job_admin(self, site: AdminSite) -> ResearchJobAdmin:
        return ResearchJobAdmin(models.ResearchJob, site)

    @patch("leads.tasks.start_research_job")
    def test_run_job_single(
        self,
        mock_task: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        response = job_admin.run_job_single(admin_request, research_job)

        assert response.status_code == 302
        research_job.refresh_from_db()
        assert research_job.status == models.ResearchJob.Status.PENDING
        mock_task.delay.assert_called_once_with(research_job.id)

    @patch("leads.tasks.start_research_job")
    def test_run_job_single_skips_running(
        self,
        mock_task: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        research_job.status = models.ResearchJob.Status.RUNNING
        research_job.save()

        response = job_admin.run_job_single(admin_request, research_job)

        assert response.status_code == 302
        mock_task.delay.assert_not_called()

    @patch("leads.tasks.start_research_job")
    def test_run_job_single_allows_failed(
        self,
        mock_task: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        """Test that failed jobs can be retried."""
        research_job.status = models.ResearchJob.Status.FAILED
        research_job.save()

        response = job_admin.run_job_single(admin_request, research_job)

        assert response.status_code == 302
        research_job.refresh_from_db()
        assert research_job.status == models.ResearchJob.Status.PENDING
        mock_task.delay.assert_called_once_with(research_job.id)

    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job_single(
        self, mock_reprocess: MagicMock, job_admin: ResearchJobAdmin, admin_request: HttpRequest, city: models.City
    ) -> None:
        job = models.ResearchJob.objects.create(
            city=city,
            status=models.ResearchJob.Status.COMPLETED,
            raw_result='{"leads": []}',
        )
        mock_reprocess.return_value = {"leads_created": 3}

        response = job_admin.reprocess_job_single(admin_request, job)

        assert response.status_code == 302
        mock_reprocess.assert_called_once_with(job.id)

    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job_single_no_raw_result(
        self,
        mock_reprocess: MagicMock,
        job_admin: ResearchJobAdmin,
        admin_request: HttpRequest,
        research_job: models.ResearchJob,
    ) -> None:
        response = job_admin.reprocess_job_single(admin_request, research_job)

        assert response.status_code == 302
        mock_reprocess.assert_not_called()

    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job_single_handles_exception(
        self, mock_reprocess: MagicMock, job_admin: ResearchJobAdmin, admin_request: HttpRequest, city: models.City
    ) -> None:
        job = models.ResearchJob.objects.create(
            city=city,
            status=models.ResearchJob.Status.COMPLETED,
            raw_result='{"leads": []}',
        )
        mock_reprocess.side_effect = ValueError("Parsing failed")

        # Should not raise - exception is caught and shown as error message
        response = job_admin.reprocess_job_single(admin_request, job)

        assert response.status_code == 302
        mock_reprocess.assert_called_once_with(job.id)
