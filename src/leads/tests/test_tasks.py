"""Tests for research tasks."""

from unittest.mock import MagicMock, patch

import pytest
from ninja.errors import HttpError

from leads.models import City, EmailSent, EmailTemplate, Lead, LeadType, ResearchJob, Tag
from leads.tasks import (
    ResearchLead,
    ResearchResult,
    Temperature,
    _create_lead_from_research,
    _parse_research_result,
    _parse_with_gemini_fallback,
    _poll_and_process,
    _process_completed_job,
    get_gemini_client,
    poll_research_jobs,
    queue_research,
    reprocess_job,
    send_email_task,
    start_research_job,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def city() -> City:
    return City.objects.create(name="Berlin", country="Germany", iso2="DE")


class TestQueueResearch:
    """Tests for queue_research function."""

    @patch("leads.tasks.start_research_job.delay")
    def test_creates_job_and_queues_task(self, mock_delay: MagicMock, city: City) -> None:
        result = queue_research(city.id)

        assert result["status"] == "pending"
        job = ResearchJob.objects.get(id=result["job_id"])
        assert job.status == ResearchJob.Status.PENDING
        assert job.city == city
        mock_delay.assert_called_once_with(job.id)

    def test_prevents_duplicate_jobs(self, city: City) -> None:
        ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING)

        with pytest.raises(HttpError, match="already running"):
            queue_research(city.id)

    def test_prevents_duplicate_pending_jobs(self, city: City) -> None:
        ResearchJob.objects.create(city=city, status=ResearchJob.Status.PENDING)

        with pytest.raises(HttpError, match="already running"):
            queue_research(city.id)


class TestStartResearchJob:
    """Tests for the rate-limited start_research_job task."""

    @patch("leads.tasks.get_gemini_client")
    def test_starts_research_and_updates_job(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.PENDING)

        mock_client = MagicMock()
        mock_client.interactions.create.return_value = MagicMock(id="interaction-123")
        mock_get_client.return_value = mock_client

        result = start_research_job(job.id)

        assert result["status"] == "running"
        assert result["interaction_id"] == "interaction-123"
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.RUNNING
        assert job.gemini_interaction_id == "interaction-123"
        mock_client.interactions.create.assert_called_once()

    @patch("leads.tasks.get_gemini_client")
    def test_skips_job_with_interaction_id(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(
            city=city, status=ResearchJob.Status.PENDING, gemini_interaction_id="existing-123"
        )

        result = start_research_job(job.id)

        assert result["status"] == "already_started"
        mock_get_client.assert_not_called()

    @patch("leads.tasks.get_gemini_client")
    def test_skips_completed_job(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.COMPLETED)

        result = start_research_job(job.id)

        assert result["status"] == "skipped"
        mock_get_client.assert_not_called()

    @patch("leads.tasks.get_gemini_client")
    def test_marks_failed_on_gemini_error(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.PENDING)

        mock_client = MagicMock()
        mock_client.interactions.create.side_effect = Exception("API error")
        mock_get_client.return_value = mock_client

        with pytest.raises(Exception, match="API error"):
            start_research_job(job.id)

        job.refresh_from_db()
        assert job.status == ResearchJob.Status.FAILED
        assert "API error" in job.error

    @patch("leads.tasks.get_gemini_client")
    def test_can_retry_failed_job(self, mock_get_client: MagicMock, city: City) -> None:
        """Failed job without interaction_id can be retried."""
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.FAILED, error="Previous error")

        mock_client = MagicMock()
        mock_client.interactions.create.return_value = MagicMock(id="retry-interaction-123")
        mock_get_client.return_value = mock_client

        # Change status to PENDING to allow retry
        job.status = ResearchJob.Status.PENDING
        job.save()

        result = start_research_job(job.id)

        assert result["status"] == "running"
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.RUNNING
        assert job.gemini_interaction_id == "retry-interaction-123"
        assert job.error == ""  # Error cleared


class TestPollAndProcess:
    """Tests for _poll_and_process function."""

    @patch("leads.tasks.get_gemini_client")
    def test_polls_running_job(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(
            city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="existing-123"
        )

        mock_interaction = MagicMock()
        mock_interaction.status = "running"
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = _poll_and_process(job)

        assert result["status"] == "running"
        mock_client.interactions.get.assert_called_once_with("existing-123")

    @patch("leads.tasks.get_gemini_client")
    def test_processes_completed_interaction(self, mock_get_client: MagicMock, city: City) -> None:
        result_data = ResearchResult(
            leads=[
                ResearchLead(name="Lead 1", email="lead1@test.com"),
                ResearchLead(name="Lead 2", email="lead2@test.com"),
            ]
        )
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        mock_interaction = MagicMock()
        mock_interaction.status = "completed"
        mock_interaction.outputs = [MagicMock(text=result_data.model_dump_json())]
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = _poll_and_process(job)

        assert result["status"] == "completed"
        assert result["leads_created"] == 2
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.COMPLETED
        assert job.leads_created == 2
        assert Lead.objects.count() == 2

    @patch("leads.tasks.get_gemini_client")
    def test_handles_failed_gemini_status(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        mock_interaction = MagicMock()
        mock_interaction.status = "failed"
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = _poll_and_process(job)

        assert result["status"] == "failed"
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.FAILED


class TestReprocessJob:
    """Tests for reprocess_job function."""

    def test_reprocesses_job_with_raw_result(self, city: City) -> None:
        result_data = ResearchResult(
            leads=[
                ResearchLead(name="Lead 1", email="lead1@test.com"),
                ResearchLead(name="Lead 2", email="lead2@test.com"),
            ]
        )
        job = ResearchJob.objects.create(
            city=city,
            status=ResearchJob.Status.FAILED,
            raw_result=result_data.model_dump_json(),
            error="Previous parsing error",
        )

        result = reprocess_job(job.id)

        assert result["status"] == "completed"
        assert result["leads_created"] == 2
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.COMPLETED
        assert job.leads_created == 2
        assert job.error == ""
        assert job.completed_at is not None
        assert Lead.objects.count() == 2

    def test_raises_error_without_raw_result(self, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.FAILED)

        with pytest.raises(ValueError, match="no raw_result"):
            reprocess_job(job.id)

    @patch("leads.tasks._parse_research_result")
    def test_marks_failed_on_parsing_error(self, mock_parse: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.FAILED, raw_result='{"leads": []}')
        mock_parse.side_effect = ValueError("Parsing failed")

        with pytest.raises(ValueError, match="Parsing failed"):
            reprocess_job(job.id)

        job.refresh_from_db()
        assert job.status == ResearchJob.Status.FAILED
        assert "Parsing failed" in job.error


class TestPollResearchJobs:
    @patch("leads.tasks.get_gemini_client")
    def test_processes_completed_job(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")
        result_data = ResearchResult(
            leads=[
                ResearchLead(
                    name="Test Lead", company="Test Co", email="test@example.com", temperature=Temperature.warm
                )
            ]
        )

        mock_interaction = MagicMock()
        mock_interaction.status = "completed"
        mock_interaction.outputs = [MagicMock(type="text", text=result_data.model_dump_json())]

        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = poll_research_jobs()

        assert result == {"processed": 1, "completed": 1, "failed": 0}
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.COMPLETED
        assert job.leads_created == 1
        assert Lead.objects.filter(name="Test Lead").exists()

    @patch("leads.tasks.get_gemini_client")
    def test_handles_failed_gemini_status(self, mock_get_client: MagicMock, city: City) -> None:
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        mock_interaction = MagicMock()
        mock_interaction.status = "failed"
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = poll_research_jobs()

        assert result == {"processed": 1, "completed": 0, "failed": 1}
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.FAILED

    @patch("leads.tasks.get_gemini_client")
    def test_skips_when_no_running_jobs(self, mock_get_client: MagicMock) -> None:
        result = poll_research_jobs()
        assert result == {"processed": 0, "completed": 0, "failed": 0}
        mock_get_client.assert_not_called()


class TestLeadDeduplication:
    @patch("leads.tasks.get_gemini_client")
    def test_updates_existing_lead_by_email(self, mock_get_client: MagicMock, city: City) -> None:
        existing = Lead.objects.create(name="Old Name", email="test@example.com", city=city)
        ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        result_data = ResearchResult(leads=[ResearchLead(name="New Name", email="test@example.com", company="New Co")])
        mock_interaction = MagicMock()
        mock_interaction.status = "completed"
        mock_interaction.outputs = [MagicMock(type="text", text=result_data.model_dump_json())]
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        poll_research_jobs()

        assert Lead.objects.count() == 1
        existing.refresh_from_db()
        assert existing.company == "New Co"
        assert existing.name == "Old Name"  # Not overwritten

    @patch("leads.tasks.get_gemini_client")
    def test_fills_blanks_only(self, mock_get_client: MagicMock, city: City) -> None:
        existing = Lead.objects.create(name="Lead", email="test@example.com", company="Existing Co", city=city)
        ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        result_data = ResearchResult(
            leads=[ResearchLead(name="Lead", email="test@example.com", company="New Co", website="https://new.com")]
        )
        mock_interaction = MagicMock()
        mock_interaction.status = "completed"
        mock_interaction.outputs = [MagicMock(type="text", text=result_data.model_dump_json())]
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        poll_research_jobs()

        existing.refresh_from_db()
        assert existing.company == "Existing Co"  # Preserved
        assert existing.website == "https://new.com"  # Filled

    @patch("leads.tasks.get_gemini_client")
    def test_creates_new_lead_types_and_tags(self, mock_get_client: MagicMock, city: City) -> None:
        ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        result_data = ResearchResult(leads=[ResearchLead(name="Lead", lead_type="New Type", tags=["Tag1", "Tag2"])])
        mock_interaction = MagicMock()
        mock_interaction.status = "completed"
        mock_interaction.outputs = [MagicMock(type="text", text=result_data.model_dump_json())]
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        poll_research_jobs()

        assert LeadType.objects.filter(name="New Type").exists()
        assert Tag.objects.filter(name="Tag1").exists()
        assert Tag.objects.filter(name="Tag2").exists()
        lead = Lead.objects.get(name="Lead")
        assert lead.tags.count() == 2

    def test_assigns_lead_type_to_existing_lead_without_type(self, city: City) -> None:
        """Test that lead_type is assigned to existing lead that has no lead_type."""
        existing = Lead.objects.create(name="Lead", email="test@example.com", city=city, lead_type=None)

        lead_data = ResearchLead(name="Lead", email="test@example.com", lead_type="Collective")
        result = _create_lead_from_research(lead_data, city)

        assert result is not None
        assert result.id == existing.id
        existing.refresh_from_db()
        assert existing.lead_type is not None
        assert existing.lead_type.name == "Collective"


class TestGetGeminiClient:
    @patch("leads.tasks.settings")
    @patch("leads.tasks.genai.Client")
    def test_creates_client_with_api_key(self, mock_client_class: MagicMock, mock_settings: MagicMock) -> None:
        mock_settings.GEMINI_API_KEY = "test-api-key"

        get_gemini_client()

        mock_client_class.assert_called_once_with(api_key="test-api-key")


class TestPollAndProcessExceptionHandling:
    @patch("leads.tasks.get_gemini_client")
    def test_handles_api_exception(self, mock_get_client: MagicMock, city: City) -> None:
        """Test that _poll_and_process handles API exceptions gracefully."""
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        mock_client = MagicMock()
        mock_client.interactions.get.side_effect = Exception("API connection error")
        mock_get_client.return_value = mock_client

        result = _poll_and_process(job)

        assert result["status"] == "error"
        assert "API connection error" in result["error"]
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.FAILED
        assert "API connection error" in job.error
        assert job.completed_at is not None

    @patch("leads.tasks.get_gemini_client")
    def test_updates_status_from_pending_to_running(self, mock_get_client: MagicMock, city: City) -> None:
        """Test that status is updated when Gemini reports running but our status is not RUNNING."""
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.PENDING, gemini_interaction_id="int-123")

        mock_interaction = MagicMock()
        mock_interaction.status = "running"
        mock_client = MagicMock()
        mock_client.interactions.get.return_value = mock_interaction
        mock_get_client.return_value = mock_client

        result = _poll_and_process(job)

        assert result["status"] == "running"
        job.refresh_from_db()
        assert job.status == ResearchJob.Status.RUNNING


class TestProcessCompletedJob:
    @patch("leads.tasks.get_gemini_client")
    def test_raises_on_no_text_output(self, mock_get_client: MagicMock, city: City) -> None:
        """Test that ValueError is raised when interaction has no text output."""
        job = ResearchJob.objects.create(city=city, status=ResearchJob.Status.RUNNING, gemini_interaction_id="int-123")

        mock_interaction = MagicMock()
        mock_interaction.outputs = [MagicMock(text=None)]

        with pytest.raises(ValueError, match="No text output"):
            _process_completed_job(job, mock_interaction)


class TestParseResearchResult:
    def test_parses_valid_json(self) -> None:
        """Test strategy 1: direct JSON parsing."""
        result_data = ResearchResult(leads=[ResearchLead(name="Test Lead")])
        json_str = result_data.model_dump_json()

        result = _parse_research_result(json_str)

        assert len(result.leads) == 1
        assert result.leads[0].name == "Test Lead"

    def test_extracts_from_malformed_json(self) -> None:
        """Test strategy 2: extract from 'leads': [ marker."""
        # Simulate response with schema and data mixed together
        malformed = """
Some preamble text that's not JSON
{
  "type": "object",
  "properties": {"leads": {"type": "array"}}
}

Here's the actual data:
"leads": [
    {"name": "Extracted Lead", "company": "Test Co"}
]
"""
        result = _parse_research_result(malformed)

        assert len(result.leads) == 1
        assert result.leads[0].name == "Extracted Lead"
        assert result.leads[0].company == "Test Co"

    def test_handles_code_fence_markers(self) -> None:
        """Test that trailing code fence markers are stripped."""
        json_with_fence = """
"leads": [
    {"name": "Lead With Fence"}
]
```
"""
        result = _parse_research_result(json_with_fence)

        assert len(result.leads) == 1
        assert result.leads[0].name == "Lead With Fence"

    @patch("leads.tasks._parse_with_gemini_fallback")
    def test_strategy2_fails_with_invalid_json_after_marker(self, mock_fallback: MagicMock) -> None:
        """Test that strategy 2 failure logs preview and falls back to Gemini."""
        mock_fallback.return_value = ResearchResult(leads=[ResearchLead(name="Gemini Parsed")])

        # Text with 'leads' marker but invalid JSON after it
        malformed_text = """
"leads": [
    {"name": "Incomplete object"
"""
        result = _parse_research_result(malformed_text)

        assert len(result.leads) == 1
        assert result.leads[0].name == "Gemini Parsed"
        mock_fallback.assert_called_once()

    @patch("leads.tasks._parse_with_gemini_fallback")
    def test_falls_back_to_gemini_parsing(self, mock_fallback: MagicMock) -> None:
        """Test strategy 3: fallback to Gemini when other strategies fail."""
        mock_fallback.return_value = ResearchResult(leads=[ResearchLead(name="Gemini Parsed")])

        # Text that doesn't contain valid JSON or 'leads' marker
        raw_text = "Some raw text about leads without proper JSON structure"

        result = _parse_research_result(raw_text)

        assert len(result.leads) == 1
        assert result.leads[0].name == "Gemini Parsed"
        mock_fallback.assert_called_once_with(raw_text)

    @patch("leads.tasks._parse_with_gemini_fallback")
    def test_raises_when_all_strategies_fail(self, mock_fallback: MagicMock) -> None:
        """Test that ValueError is raised when all parsing strategies fail."""
        mock_fallback.side_effect = ValueError("Gemini parsing failed")

        raw_text = "Completely unparseable garbage text"

        with pytest.raises(ValueError, match="Could not parse research result"):
            _parse_research_result(raw_text)


class TestParseWithGeminiFallback:
    @patch("leads.tasks.get_gemini_client")
    def test_parses_raw_text_successfully(self, mock_get_client: MagicMock) -> None:
        """Test successful parsing with Gemini fallback."""
        mock_response = MagicMock()
        mock_response.text = '{"leads": [{"name": "Parsed Lead"}]}'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = _parse_with_gemini_fallback("Some raw text about leads")

        assert len(result.leads) == 1
        assert result.leads[0].name == "Parsed Lead"
        mock_client.models.generate_content.assert_called_once()

    @patch("leads.tasks.get_gemini_client")
    def test_raises_on_empty_response(self, mock_get_client: MagicMock) -> None:
        """Test that ValueError is raised when Gemini returns empty response."""
        mock_response = MagicMock()
        mock_response.text = None
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        with pytest.raises(ValueError, match="empty response"):
            _parse_with_gemini_fallback("Some raw text")


class TestSendEmailTask:
    """Tests for send_email_task Celery task."""

    @pytest.fixture
    def lead(self, city: City) -> Lead:
        """Create a lead for email tests."""
        return Lead.objects.create(
            name="Email Test Lead",
            email="lead@example.com",
            city=city,
        )

    @pytest.fixture
    def email_template(self) -> EmailTemplate:
        """Create an email template for tests."""
        return EmailTemplate.objects.create(
            name="Task Test Template",
            subject="Hello from task",
            body="This is a test email from the task.",
        )

    @patch("leads.service.EmailMessage")
    def test_sends_email_successfully(self, mock_email_class: MagicMock, lead: Lead) -> None:
        result = send_email_task(
            lead_id=lead.id,
            subject="Test Subject",
            body="Test Body",
            to=["recipient@example.com"],
        )

        assert result["status"] == "sent"
        assert result["email_sent_id"] is not None
        email_sent = EmailSent.objects.get(id=result["email_sent_id"])
        assert email_sent.lead == lead
        assert email_sent.subject == "Test Subject"
        mock_email_class.return_value.send.assert_called_once()

    @patch("leads.service.EmailMessage")
    def test_includes_template_when_provided(
        self, mock_email_class: MagicMock, lead: Lead, email_template: EmailTemplate
    ) -> None:
        result = send_email_task(
            lead_id=lead.id,
            subject="Test",
            body="Test",
            to=["test@example.com"],
            template_id=email_template.id,
        )

        assert result["status"] == "sent"
        email_sent = EmailSent.objects.get(id=result["email_sent_id"])
        assert email_sent.template == email_template

    @patch("leads.service.EmailMessage")
    def test_includes_bcc_when_provided(self, mock_email_class: MagicMock, lead: Lead) -> None:
        send_email_task(
            lead_id=lead.id,
            subject="Test",
            body="Test",
            to=["to@example.com"],
            bcc=["bcc@example.com"],
        )

        mock_email_class.assert_called_once()
        call_kwargs = mock_email_class.call_args.kwargs
        assert call_kwargs["bcc"] == ["bcc@example.com"]

    def test_returns_failed_on_validation_error(self, lead: Lead) -> None:
        result = send_email_task(
            lead_id=lead.id,
            subject="Hello {foo}",
            body="Test",
            to=["test@example.com"],
        )

        assert result["status"] == "failed"
        assert result["email_sent_id"] is None
        assert "Unreplaced placeholders" in result["error"]

    @patch("leads.service.EmailMessage")
    def test_returns_failed_on_send_error(self, mock_email_class: MagicMock, lead: Lead) -> None:
        mock_email_class.return_value.send.side_effect = Exception("SMTP connection failed")

        result = send_email_task(
            lead_id=lead.id,
            subject="Test",
            body="Test",
            to=["test@example.com"],
        )

        assert result["status"] == "failed"
        assert result["email_sent_id"] is None
        assert "SMTP connection failed" in result["error"]
