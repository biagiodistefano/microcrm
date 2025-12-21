"""Tests for leads API controllers."""

import typing as t
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client

from leads.models import Action, City, Lead, LeadType, ResearchJob, Tag

pytestmark = pytest.mark.django_db


class TestLeadListEndpoint:
    def test_list_leads_empty(self, api_client: Client) -> None:
        response = api_client.get("/api/leads/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_list_leads_with_data(self, api_client: Client, lead: Lead) -> None:
        response = api_client.get("/api/leads/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == lead.name

    def test_list_leads_filter_by_status(self, api_client: Client, lead: Lead) -> None:
        response = api_client.get("/api/leads/?status=new")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        response = api_client.get("/api/leads/?status=converted")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_list_leads_filter_by_temperature(self, api_client: Client, lead: Lead) -> None:
        response = api_client.get("/api/leads/?temperature=warm")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_list_leads_search(self, api_client: Client, lead: Lead) -> None:
        response = api_client.get("/api/leads/?search=Test")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        response = api_client.get("/api/leads/?search=nonexistent")
        assert response.status_code == 200
        assert response.json()["count"] == 0


class TestLeadGetEndpoint:
    def test_get_lead(self, api_client: Client, lead: Lead) -> None:
        response = api_client.get(f"/api/leads/{lead.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == lead.name
        assert data["city"]["name"] == "Berlin"
        assert data["lead_type"]["name"] == "Collective"
        assert len(data["tags"]) == 1

    def test_get_lead_not_found(self, api_client: Client) -> None:
        response = api_client.get("/api/leads/99999")
        assert response.status_code == 404


class TestLeadCreateEndpoint:
    def test_create_lead_minimal(self, api_client: Client) -> None:
        response = api_client.post(
            "/api/leads/",
            data={"name": "New Lead"},
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "New Lead"
        assert data["status"] == "new"
        assert Lead.objects.count() == 1

    def test_create_lead_with_auto_created_relations(self, api_client: Client) -> None:
        response = api_client.post(
            "/api/leads/",
            data={
                "name": "Full Lead",
                "lead_type": "New Type",
                "city": {"name": "Munich", "country": "Germany", "iso2": "DE"},
                "tags": ["Tag1", "Tag2"],
            },
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["lead_type"]["name"] == "New Type"
        assert data["city"]["name"] == "Munich"
        assert len(data["tags"]) == 2

        # Verify objects were created
        assert LeadType.objects.filter(name="New Type").exists()
        assert City.objects.filter(name="Munich").exists()
        assert Tag.objects.filter(name="Tag1").exists()
        assert Tag.objects.filter(name="Tag2").exists()


class TestLeadUpdateEndpoint:
    def test_update_lead(self, api_client: Client, lead: Lead) -> None:
        response = api_client.put(
            f"/api/leads/{lead.id}",
            data={"name": "Updated Lead", "status": "contacted"},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated Lead"
        assert data["status"] == "contacted"
        # Relations cleared since not provided
        assert data["city"] is None
        assert data["tags"] == []


class TestLeadPatchEndpoint:
    def test_patch_lead(self, api_client: Client, lead: Lead) -> None:
        response = api_client.patch(
            f"/api/leads/{lead.id}",
            data={"status": "qualified"},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "qualified"
        # Other fields unchanged
        assert data["name"] == lead.name
        assert data["city"]["name"] == "Berlin"


class TestLeadDeleteEndpoint:
    def test_delete_lead(self, api_client: Client, lead: Lead) -> None:
        lead_id = lead.id
        response = api_client.delete(f"/api/leads/{lead_id}")
        assert response.status_code == 204
        assert not Lead.objects.filter(id=lead_id).exists()

    def test_delete_lead_not_found(self, api_client: Client) -> None:
        response = api_client.delete("/api/leads/99999")
        assert response.status_code == 404


class TestCityListEndpoint:
    def test_list_cities(self, api_client: Client, city: City) -> None:
        response = api_client.get("/api/cities/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == "Berlin"

    def test_list_cities_search(self, api_client: Client, city: City) -> None:
        response = api_client.get("/api/cities/?search=Berlin")
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestLeadTypeListEndpoint:
    def test_list_lead_types(self, api_client: Client, lead_type: LeadType) -> None:
        response = api_client.get("/api/lead-types/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(item["name"] == "Collective" for item in data)


class TestTagListEndpoint:
    def test_list_tags(self, api_client: Client, tag: Tag) -> None:
        response = api_client.get("/api/tags/")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(item["name"] == "Techno" for item in data)


class TestActionListEndpoint:
    def test_list_actions_empty(self, api_client: Client) -> None:
        response = api_client.get("/api/actions/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []

    def test_list_actions_with_data(self, api_client: Client, action: Action) -> None:
        response = api_client.get("/api/actions/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["name"] == action.name
        assert data["results"][0]["lead_id"] == action.lead_id

    def test_list_actions_filter_by_lead_id(self, api_client: Client, action: Action) -> None:
        response = api_client.get(f"/api/actions/?lead_id={action.lead_id}")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        response = api_client.get("/api/actions/?lead_id=99999")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_list_actions_filter_by_status(self, api_client: Client, action: Action) -> None:
        response = api_client.get("/api/actions/?status=pending")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        response = api_client.get("/api/actions/?status=completed")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_list_actions_filter_by_due_date_range(self, api_client: Client, action: Action) -> None:
        today = date.today().isoformat()
        future = (date.today() + timedelta(days=7)).isoformat()

        response = api_client.get(f"/api/actions/?due_after={today}&due_before={future}")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_list_actions_search(self, api_client: Client, action: Action) -> None:
        response = api_client.get("/api/actions/?search=Follow")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        response = api_client.get("/api/actions/?search=nonexistent")
        assert response.status_code == 200
        assert response.json()["count"] == 0


class TestActionGetEndpoint:
    def test_get_action(self, api_client: Client, action: Action) -> None:
        response = api_client.get(f"/api/actions/{action.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == action.name
        assert data["lead_id"] == action.lead_id
        assert data["status"] == "pending"

    def test_get_action_not_found(self, api_client: Client) -> None:
        response = api_client.get("/api/actions/99999")
        assert response.status_code == 404


class TestActionCreateEndpoint:
    def test_create_action(self, api_client: Client, lead: Lead) -> None:
        response = api_client.post(
            "/api/actions/",
            data={
                "lead_id": lead.id,
                "name": "Send proposal",
                "notes": "Include pricing",
                "due_date": (date.today() + timedelta(days=5)).isoformat(),
            },
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Send proposal"
        assert data["lead_id"] == lead.id
        assert data["status"] == "pending"  # Always starts as pending
        assert Action.objects.count() == 1

    def test_create_action_minimal(self, api_client: Client, lead: Lead) -> None:
        response = api_client.post(
            "/api/actions/",
            data={"lead_id": lead.id, "name": "Quick task"},
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Quick task"
        assert data["status"] == "pending"
        assert data["due_date"] is None

    def test_create_action_invalid_lead(self, api_client: Client) -> None:
        response = api_client.post(
            "/api/actions/",
            data={"lead_id": 99999, "name": "Invalid"},
            content_type="application/json",
        )
        assert response.status_code == 404  # Lead not found


class TestActionUpdateEndpoint:
    def test_update_action(self, api_client: Client, action: Action, lead: Lead) -> None:
        response = api_client.put(
            f"/api/actions/{action.id}",
            data={
                "lead_id": lead.id,
                "name": "Updated action",
                "notes": "New notes",
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Updated action"
        assert data["notes"] == "New notes"
        # Status is not changed via PUT
        assert data["status"] == "pending"

    def test_update_action_preserves_status(self, api_client: Client, action: Action, lead: Lead) -> None:
        # Set action to in_progress first
        action.status = Action.Status.IN_PROGRESS
        action.save()

        response = api_client.put(
            f"/api/actions/{action.id}",
            data={
                "lead_id": lead.id,
                "name": "Updated name",
            },
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        # Status should be preserved
        assert data["status"] == "in_progress"


class TestActionPatchEndpoint:
    def test_patch_action(self, api_client: Client, action: Action) -> None:
        response = api_client.patch(
            f"/api/actions/{action.id}",
            data={"status": "in_progress"},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "in_progress"
        # Other fields unchanged
        assert data["name"] == action.name

    def test_patch_action_complete_sets_completed_at(self, api_client: Client, action: Action) -> None:
        response = api_client.patch(
            f"/api/actions/{action.id}",
            data={"status": "completed"},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["completed_at"] is not None

    def test_patch_action_uncomplete_clears_completed_at(self, api_client: Client, action: Action) -> None:
        # First complete it
        action.status = Action.Status.COMPLETED
        action.save()

        # Then uncomplete
        response = api_client.patch(
            f"/api/actions/{action.id}",
            data={"status": "pending"},
            content_type="application/json",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
        assert data["completed_at"] is None


class TestActionDeleteEndpoint:
    def test_delete_action(self, api_client: Client, action: Action) -> None:
        action_id = action.id
        response = api_client.delete(f"/api/actions/{action_id}")
        assert response.status_code == 204
        assert not Action.objects.filter(id=action_id).exists()

    def test_delete_action_not_found(self, api_client: Client) -> None:
        response = api_client.delete("/api/actions/99999")
        assert response.status_code == 404


# --- City Create Tests ---


class TestCityGetEndpoint:
    def test_get_city(self, api_client: Client, city: City) -> None:
        response = api_client.get(f"/api/cities/{city.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == city.name
        assert data["country"] == city.country

    def test_get_city_not_found(self, api_client: Client) -> None:
        response = api_client.get("/api/cities/99999")
        assert response.status_code == 404


class TestCityCreateEndpoint:
    def test_create_city(self, api_client: Client) -> None:
        response = api_client.post(
            "/api/cities/",
            data={"name": "Munich", "country": "Germany", "iso2": "de"},
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "Munich"
        assert data["country"] == "Germany"
        assert data["iso2"] == "DE"  # Should be uppercased
        assert City.objects.filter(name="Munich").exists()

    def test_create_city_duplicate(self, api_client: Client, city: City) -> None:
        response = api_client.post(
            "/api/cities/",
            data={"name": city.name, "country": city.country},
            content_type="application/json",
        )
        assert response.status_code == 400  # Duplicate city


class TestCityResearchEndpoint:
    @patch("leads.tasks.start_research_job")
    def test_start_research(self, mock_task: MagicMock, api_client: Client, city: City) -> None:
        mock_task.delay.return_value = None

        response = api_client.post(f"/api/cities/{city.id}/research")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"  # Job queued, status is pending
        assert "job_id" in data

        # Verify a research job was created
        assert ResearchJob.objects.filter(city=city).exists()

    @patch("leads.tasks.start_research_job")
    def test_start_research_duplicate(self, mock_task: MagicMock, api_client: Client, city: City) -> None:
        # Create an active research job first
        ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING)

        response = api_client.post(f"/api/cities/{city.id}/research")
        assert response.status_code == 400  # Research already active

    def test_start_research_city_not_found(self, api_client: Client) -> None:
        response = api_client.post("/api/cities/99999/research")
        assert response.status_code == 404


# --- ResearchJob Tests ---


class TestResearchJobListEndpoint:
    def test_list_jobs_empty(self, api_client: Client) -> None:
        response = api_client.get("/api/research-jobs/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0

    def test_list_jobs_with_data(self, api_client: Client, research_job: ResearchJob) -> None:
        response = api_client.get("/api/research-jobs/")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert data["results"][0]["city"]["name"] == research_job.city.name

    def test_list_jobs_filter_by_status(self, api_client: Client, research_job: ResearchJob) -> None:
        response = api_client.get("/api/research-jobs/?status=not_started")
        assert response.status_code == 200
        assert response.json()["count"] == 1

        response = api_client.get("/api/research-jobs/?status=completed")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_list_jobs_filter_by_city_id(self, api_client: Client, research_job: ResearchJob) -> None:
        response = api_client.get(f"/api/research-jobs/?city_id={research_job.city_id}")
        assert response.status_code == 200
        assert response.json()["count"] == 1


class TestResearchJobGetEndpoint:
    def test_get_job(self, api_client: Client, research_job: ResearchJob) -> None:
        response = api_client.get(f"/api/research-jobs/{research_job.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["city"]["name"] == research_job.city.name
        assert data["status"] == "not_started"

    def test_get_job_not_found(self, api_client: Client) -> None:
        response = api_client.get("/api/research-jobs/99999")
        assert response.status_code == 404


class TestResearchJobCreateEndpoint:
    def test_create_job(self, api_client: Client, city: City) -> None:
        response = api_client.post(
            "/api/research-jobs/",
            data={"city_id": city.id},
            content_type="application/json",
        )
        assert response.status_code == 201
        data = response.json()
        assert data["city"]["id"] == city.id
        assert data["status"] == "not_started"
        assert ResearchJob.objects.count() == 1

    def test_create_job_invalid_city(self, api_client: Client) -> None:
        response = api_client.post(
            "/api/research-jobs/",
            data={"city_id": 99999},
            content_type="application/json",
        )
        assert response.status_code == 404  # City not found

    def test_create_job_duplicate_active(self, api_client: Client, research_job: ResearchJob) -> None:
        # Set to PENDING to make it active
        research_job.status = ResearchJob.Status.PENDING
        research_job.save()

        response = api_client.post(
            "/api/research-jobs/",
            data={"city_id": research_job.city_id},
            content_type="application/json",
        )
        assert response.status_code == 400  # Research already active


class TestResearchJobRunEndpoint:
    @patch("leads.tasks.start_research_job")
    def test_run_job(self, mock_task: MagicMock, api_client: Client, research_job: ResearchJob) -> None:
        mock_task.delay.return_value = None

        response = api_client.post(f"/api/research-jobs/{research_job.id}/run")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"
        assert data["job_id"] == research_job.id

        # Verify job status updated
        research_job.refresh_from_db()
        assert research_job.status == ResearchJob.Status.PENDING

        # Verify task was queued
        mock_task.delay.assert_called_once_with(research_job.id)

    def test_run_job_wrong_status(self, api_client: Client, research_job: ResearchJob) -> None:
        research_job.status = ResearchJob.Status.RUNNING
        research_job.save()

        response = api_client.post(f"/api/research-jobs/{research_job.id}/run")
        assert response.status_code == 400  # Job not in runnable state

    @patch("leads.tasks.start_research_job")
    def test_run_failed_job(self, mock_task: MagicMock, api_client: Client, research_job: ResearchJob) -> None:
        mock_task.delay.return_value = None
        research_job.status = ResearchJob.Status.FAILED
        research_job.save()

        response = api_client.post(f"/api/research-jobs/{research_job.id}/run")
        assert response.status_code == 200


class TestResearchJobReprocessEndpoint:
    @patch("leads.tasks.reprocess_job")
    def test_reprocess_job(
        self, mock_reprocess: t.Any, api_client: Client, completed_research_job: ResearchJob
    ) -> None:
        mock_reprocess.return_value = {"leads_created": 5}

        response = api_client.post(f"/api/research-jobs/{completed_research_job.id}/reprocess")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "completed"
        assert data["leads_created"] == 5

    def test_reprocess_job_no_raw_result(self, api_client: Client, research_job: ResearchJob) -> None:
        response = api_client.post(f"/api/research-jobs/{research_job.id}/reprocess")
        assert response.status_code == 400  # No raw_result to reprocess


class TestResearchJobDeleteEndpoint:
    def test_delete_completed_job(self, api_client: Client, completed_research_job: ResearchJob) -> None:
        job_id = completed_research_job.id
        response = api_client.delete(f"/api/research-jobs/{job_id}")
        assert response.status_code == 204
        assert not ResearchJob.objects.filter(id=job_id).exists()

    def test_delete_not_started_job(self, api_client: Client, research_job: ResearchJob) -> None:
        job_id = research_job.id
        response = api_client.delete(f"/api/research-jobs/{job_id}")
        assert response.status_code == 204
        assert not ResearchJob.objects.filter(id=job_id).exists()

    def test_delete_running_job_fails(self, api_client: Client, research_job: ResearchJob) -> None:
        research_job.status = ResearchJob.Status.RUNNING
        research_job.save()

        response = api_client.delete(f"/api/research-jobs/{research_job.id}")
        assert response.status_code == 400  # Cannot delete while running

    def test_delete_job_not_found(self, api_client: Client) -> None:
        response = api_client.delete("/api/research-jobs/99999")
        assert response.status_code == 404
