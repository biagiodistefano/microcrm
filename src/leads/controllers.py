"""API controllers for leads."""

from django.db.models import QuerySet
from django.shortcuts import get_object_or_404
from ninja import Query
from ninja.errors import HttpError
from ninja_extra import ControllerBase, api_controller, route
from ninja_extra.pagination import PageNumberPaginationExtra, PaginatedResponseSchema, paginate
from ninja_extra.searching import Searching, searching

from leads import service
from leads.models import Action, City, Lead, LeadType, ResearchJob, Tag
from leads.schema import (
    ActionFilterSchema,
    ActionIn,
    ActionPatch,
    ActionSchema,
    CityFilterSchema,
    CityIn,
    CitySchema,
    JobActionResponse,
    LeadFilterSchema,
    LeadIn,
    LeadPatch,
    LeadSchema,
    LeadTypeSchema,
    ResearchJobDetailSchema,
    ResearchJobFilterSchema,
    ResearchJobIn,
    ResearchJobSchema,
    TagSchema,
)


@api_controller("/leads", tags=["Leads"])
class LeadController(ControllerBase):
    """Lead CRUD controller."""

    def get_queryset(self) -> QuerySet[Lead]:
        """Get base queryset."""
        return Lead.objects.select_related("city", "lead_type").prefetch_related("tags")

    @route.get("/", response=PaginatedResponseSchema[LeadSchema])
    @paginate(PageNumberPaginationExtra, page_size=20)
    @searching(Searching, search_fields=["name", "email", "company", "notes", "telegram", "instagram", "website"])
    def list_leads(
        self,
        filters: LeadFilterSchema = Query(...),  # type: ignore[type-arg]
    ) -> QuerySet[Lead]:
        """List leads with filtering, searching, and pagination.

        Supports full-text search across name, email, company, notes, telegram, instagram and website fields
        via the `search` query parameter.

        Filter options:
        - status: Filter by lead status (new, contacted, qualified, converted, lost)
        - temperature: Filter by temperature (cold, warm, hot)
        - lead_type: Filter by lead type name (exact, case-insensitive)
        - city_id: Filter by city ID (exact match, recommended for autocomplete)
        - city: Filter by city name (partial match)
        - country: Filter by country name (partial match)
        - tag: Filter by tag name (exact, case-insensitive)
        """
        return filters.filter(self.get_queryset()).distinct()

    @route.get("/{lead_id}", response=LeadSchema)
    def get_lead(self, lead_id: int) -> Lead:
        """Get a single lead by ID.

        Returns the full lead details including related city, lead type, and tags.
        """
        return get_object_or_404(self.get_queryset(), id=lead_id)

    @route.post("/", response={201: LeadSchema})
    def create_lead(self, data: LeadIn) -> tuple[int, Lead]:
        """Create a new lead.

        Related objects are automatically handled:
        - **city**: If provided, looks up by name+country (case-insensitive).
          Creates a new city if not found.
        - **lead_type**: If provided, looks up by name (case-insensitive).
          Creates a new lead type if not found.
        - **tags**: Each tag is looked up by name (case-insensitive).
          Creates new tags if not found.

        Example request:
        ```json
        {
          "name": "Berlin Techno Collective",
          "email": "info@btc.de",
          "lead_type": "Collective",
          "city": {"name": "Berlin", "country": "Germany", "iso2": "DE"},
          "tags": ["Techno", "LGBTQ+", "Nightlife"],
          "status": "new",
          "temperature": "warm"
        }
        ```
        """
        return 201, service.create_lead(data)

    @route.put("/{lead_id}", response=LeadSchema)
    def update_lead(self, lead_id: int, data: LeadIn) -> Lead:
        """Update a lead (full replacement).

        All fields are replaced with the provided values. Fields not provided
        will be reset to their defaults (empty strings, null, etc.).

        Related objects (city, lead_type, tags) follow the same auto-creation
        behavior as the create endpoint.
        """
        lead = get_object_or_404(Lead, id=lead_id)
        return service.update_lead(lead, data)

    @route.patch("/{lead_id}", response=LeadSchema)
    def patch_lead(self, lead_id: int, data: LeadPatch) -> Lead:
        """Partially update a lead.

        Only the provided fields are updated. Omitted fields retain their
        current values.

        Related objects (city, lead_type, tags) follow the same auto-creation
        behavior as the create endpoint.

        Example - update only status and add a note:
        ```json
        {
          "status": "contacted",
          "notes": "Called on Dec 17, interested in demo"
        }
        ```
        """
        lead = get_object_or_404(Lead, id=lead_id)
        return service.patch_lead(lead, data)

    @route.delete("/{lead_id}", response={204: None})
    def delete_lead(self, lead_id: int) -> tuple[int, None]:
        """Delete a lead.

        Permanently removes the lead. This action cannot be undone.
        Related cities, lead types, and tags are NOT deleted.
        """
        lead = get_object_or_404(Lead, id=lead_id)
        lead.delete()
        return 204, None


@api_controller("/cities", tags=["Cities"])
class CityController(ControllerBase):
    """City controller for managing cities."""

    @route.get("/", response=PaginatedResponseSchema[CitySchema])
    @paginate(PageNumberPaginationExtra, page_size=50)
    @searching(Searching, search_fields=["name", "country"])
    def list_cities(
        self,
        filters: CityFilterSchema = Query(...),  # type: ignore[type-arg]
    ) -> QuerySet[City]:
        """List cities with filtering and searching.

        Use the `search` parameter for autocomplete functionality.
        """
        return filters.filter(City.objects.all()).distinct()

    @route.get("/{city_id}", response=CitySchema)
    def get_city(self, city_id: int) -> City:
        """Get a single city by ID."""
        return get_object_or_404(City, id=city_id)

    @route.post("/", response={201: CitySchema})
    def create_city(self, data: CityIn) -> tuple[int, City]:
        """Create a new city.

        Returns 400 if a city with the same name and country already exists
        (case-insensitive matching).
        """
        return 201, service.create_city(data)

    @route.post("/{city_id}/research", response=JobActionResponse)
    def start_research(self, city_id: int) -> JobActionResponse:
        """Start deep research for a city.

        Creates a ResearchJob and queues it for Celery processing (rate-limited 1/min).
        Returns 400 if there's already an active research job for this city.
        """
        city = get_object_or_404(City, id=city_id)
        result = service.start_city_research(city)
        return JobActionResponse(**result)


@api_controller("/lead-types", tags=["Lead Types"])
class LeadTypeController(ControllerBase):
    """Lead type controller for browsing available lead types."""

    @route.get("/", response=list[LeadTypeSchema])
    def list_lead_types(self) -> QuerySet[LeadType]:
        """List all lead types.

        Lead types are automatically created when creating/updating leads,
        so this endpoint is primarily for browsing existing types.
        """
        return LeadType.objects.all()


@api_controller("/tags", tags=["Tags"])
class TagController(ControllerBase):
    """Tag controller for browsing available tags."""

    @route.get("/", response=list[TagSchema])
    @searching(Searching, search_fields=["name"])
    def list_tags(self) -> QuerySet[Tag]:
        """List all tags.

        Tags are automatically created when creating/updating leads
        (case-insensitive matching), so this endpoint is primarily
        for browsing existing tags.
        """
        return Tag.objects.all()


@api_controller("/actions", tags=["Actions"])
class ActionController(ControllerBase):
    """Action CRUD controller for lead follow-up tasks."""

    @route.get("/", response=PaginatedResponseSchema[ActionSchema])
    @paginate(PageNumberPaginationExtra, page_size=20)
    @searching(Searching, search_fields=["name", "notes"])
    def list_actions(
        self,
        filters: ActionFilterSchema = Query(...),  # type: ignore[type-arg]
    ) -> QuerySet[Action]:
        """List actions with filtering, searching, and pagination.

        Filter options:
        - lead_id: Filter by lead ID
        - status: Filter by status (pending, in_progress, completed, cancelled)
        - due_date: Filter by exact due date
        - due_before: Filter actions due on or before this date
        - due_after: Filter actions due on or after this date
        """
        return filters.filter(Action.objects.all())

    @route.get("/{action_id}", response=ActionSchema)
    def get_action(self, action_id: int) -> Action:
        """Get a single action by ID."""
        return get_object_or_404(Action, id=action_id)

    @route.post("/", response={201: ActionSchema})
    def create_action(self, data: ActionIn) -> tuple[int, Action]:
        """Create a new action for a lead.

        The lead_id must reference an existing lead.
        """
        return 201, service.create_action(data)

    @route.put("/{action_id}", response=ActionSchema)
    def update_action(self, action_id: int, data: ActionIn) -> Action:
        """Update an action (full replacement).

        All fields are replaced with the provided values.
        """
        action = get_object_or_404(Action, id=action_id)
        return service.update_action(action, data)

    @route.patch("/{action_id}", response=ActionSchema)
    def patch_action(self, action_id: int, data: ActionPatch) -> Action:
        """Partially update an action.

        Only the provided fields are updated.
        When status is set to 'completed', completed_at is automatically set.
        """
        action = get_object_or_404(Action, id=action_id)
        return service.patch_action(action, data)

    @route.delete("/{action_id}", response={204: None})
    def delete_action(self, action_id: int) -> tuple[int, None]:
        """Delete an action."""
        action = get_object_or_404(Action, id=action_id)
        action.delete()
        return 204, None


@api_controller("/research-jobs", tags=["Research"])
class ResearchJobController(ControllerBase):
    """ResearchJob controller for managing Gemini Deep Research jobs."""

    def get_queryset(self) -> QuerySet[ResearchJob]:
        """Get base queryset with city prefetched."""
        return ResearchJob.objects.select_related("city")

    @route.get("/", response=PaginatedResponseSchema[ResearchJobSchema])
    @paginate(PageNumberPaginationExtra, page_size=20)
    @searching(Searching, search_fields=["city__name"])
    def list_jobs(
        self,
        filters: ResearchJobFilterSchema = Query(...),  # type: ignore[type-arg]
    ) -> QuerySet[ResearchJob]:
        """List research jobs with filtering and pagination.

        Filter options:
        - city_id: Filter by city ID
        - status: Filter by status (not_started, pending, running, completed, failed)
        - country: Filter by city's country (partial match)
        """
        return filters.filter(self.get_queryset())

    @route.get("/{job_id}", response=ResearchJobDetailSchema)
    def get_job(self, job_id: int) -> ResearchJob:
        """Get a single research job by ID.

        Returns full details including raw_result and parsed result.
        """
        return get_object_or_404(self.get_queryset(), id=job_id)

    @route.post("/", response={201: ResearchJobSchema})
    def create_job(self, data: ResearchJobIn) -> tuple[int, ResearchJob]:
        """Create a new research job for a city.

        The job is created with NOT_STARTED status.
        Use the /run endpoint to start the research.

        Returns 400 if there's already an active research job for this city.
        """
        return 201, service.create_research_job(data)

    @route.post("/{job_id}/run", response=JobActionResponse)
    def run_job(self, job_id: int) -> JobActionResponse:
        """Start or retry a research job.

        Queues the job for processing via Celery (rate-limited to 1/min).
        Only works for jobs with status NOT_STARTED or FAILED.
        """
        job = get_object_or_404(ResearchJob, id=job_id)
        result = service.run_research_job(job)
        return JobActionResponse(**result)

    @route.post("/{job_id}/reprocess", response=JobActionResponse)
    def reprocess_job(self, job_id: int) -> JobActionResponse:
        """Reprocess a job that has raw_result.

        Retries parsing/lead creation without re-running Gemini research.
        Useful when parsing failed due to malformed JSON.
        """
        job = get_object_or_404(ResearchJob, id=job_id)
        result = service.reprocess_research_job(job)
        return JobActionResponse(**result)

    @route.delete("/{job_id}", response={204: None})
    def delete_job(self, job_id: int) -> tuple[int, None]:
        """Delete a research job.

        Only NOT_STARTED, COMPLETED, or FAILED jobs can be deleted.
        Returns 400 if the job is PENDING or RUNNING.
        """
        job = get_object_or_404(ResearchJob, id=job_id)
        if job.status in (ResearchJob.Status.PENDING, ResearchJob.Status.RUNNING):
            raise HttpError(400, f"Cannot delete job #{job_id} while it's {job.get_status_display()}")
        job.delete()
        return 204, None
