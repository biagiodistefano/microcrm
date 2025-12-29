# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MicroCRM is a Django-based lead management system for event promotion. The system includes:
- REST API for lead management (Django Ninja)
- Admin interface with custom dashboard (Django Unfold)
- Celery-based background tasks for AI research using Google Gemini Deep Research
- Google SSO authentication
- Historical tracking of all lead changes (django-simple-history)

## Tech Stack

- **Framework**: Django 5.2+ (Python 3.14+)
- **Package Manager**: uv
- **API**: django-ninja + ninja-extra
- **Admin**: django-unfold
- **Task Queue**: Celery + Redis + django-celery-beat
- **Database**: SQLite (dev), PostgreSQL (prod)
- **AI**: Google Gemini Deep Research API

## Development Commands

### Initial Setup
```bash
make setup  # Creates .env, installs deps, runs migrations, bootstraps data
```

### Daily Development
```bash
make run              # Start Django dev server
make run-celery       # Start Celery worker
make run-celery-beat  # Start Celery beat scheduler
make shell            # Django shell
```

### Code Quality
```bash
make format  # Auto-format with ruff
make lint    # Lint and fix with ruff
make mypy    # Type check with mypy --strict
make check   # Run all: format, lint, mypy
```

### Testing
```bash
make test         # Run all tests with coverage
make test-failed  # Re-run only failed tests
```

### Database
```bash
make migrations  # Create migrations
make migrate     # Apply migrations
make bootstrap   # Load initial data (LeadTypes, etc.)
make nuke-db     # ⚠️ Nuclear option: delete db + migrations, start fresh
```

### Testing Individual Tests
```bash
# Run specific test file
uv run pytest src/leads/tests/test_service.py -v

# Run specific test function
uv run pytest src/leads/tests/test_service.py::test_create_lead -v

# Run with coverage for specific module
uv run pytest --cov=leads.service src/leads/tests/test_service.py -v
```

## CLI Client (cli.py)

A Typer-based CLI client for the MicroCRM API. Configure via environment variables:
- `CRM_BASE_URL` - API base URL (default: `http://localhost:8000/api`)
- `API_KEY` - API authentication key

### Main Commands
```
python cli.py [OPTIONS] COMMAND [ARGS]...

Commands:
  config      Show current CLI configuration
  leads       Manage leads
  cities      Manage cities
  types       Browse lead types
  tags        Browse tags
  actions     Manage actions/tasks
  jobs        Manage research jobs
  templates   Manage email templates
  emails      View sent emails
  drafts      Manage email drafts
```

### leads
```
leads list      [--page] [--page-size] [--search] [--status] [--temperature]
                [--type] [--city-id] [--city] [--country] [--tag] [--raw]
leads get       LEAD_ID [--raw]
leads create    --name [--email] [--phone] [--company] [--type] [--city]
                [--country] [--iso2] [--telegram] [--instagram] [--website]
                [--source] [--status] [--temperature] [--tag] [--notes]
                [--value] [--raw]
leads update    LEAD_ID [--name] [--email] [--phone] [--company] [--type]
                [--city] [--country] [--iso2] [--telegram] [--instagram]
                [--website] [--source] [--status] [--temperature] [--tag]
                [--notes] [--value] [--raw]
leads delete    LEAD_ID [--force]
leads import    FILE [--dry-run]
leads send-email LEAD_ID [--template] [--subject] [--body] [--to] [--bcc]
                [--background] [--send] [--raw]
                NOTE: Runs in DRY-RUN mode by default. Use --send to actually send.
leads emails    LEAD_ID [--raw]
```

### cities
```
cities list     [--page] [--page-size] [--search] [--country] [--raw]
cities get      CITY_ID [--raw]
cities create   --name --country [--iso2] [--raw]
cities research CITY_ID [--raw]
```

### types
```
types list      [--raw]
```

### tags
```
tags list       [--search] [--raw]
```

### actions
```
actions list    [--page] [--page-size] [--search] [--lead] [--status]
                [--due-date] [--due-before] [--due-after] [--raw]
actions get     ACTION_ID [--raw]
actions create  --lead --name [--notes] [--due-date] [--raw]
actions update  ACTION_ID [--name] [--notes] [--status] [--due-date] [--raw]
actions delete  ACTION_ID [--force]
```

### jobs (Research Jobs)
```
jobs list       [--page] [--page-size] [--search] [--city-id] [--status]
                [--country] [--raw]
jobs get        JOB_ID [--raw]
jobs create     --city [--raw]
jobs run        JOB_ID [--raw]
jobs reprocess  JOB_ID [--raw]
jobs delete     JOB_ID [--force]
```

### templates (Email Templates)
```
templates list   [--search] [--raw]
templates get    TEMPLATE_ID [--raw]
templates create --name --subject --body [--language] [--raw]
templates update TEMPLATE_ID [--name] [--subject] [--body] [--language] [--raw]
templates delete TEMPLATE_ID [--force]
```

### emails (Sent Emails)
```
emails list     [--page] [--page-size] [--lead] [--template] [--status] [--raw]
emails get      EMAIL_ID [--raw]
```

### drafts (Email Drafts)
```
drafts list     [--page] [--page-size] [--lead] [--template] [--raw]
drafts get      DRAFT_ID [--raw]
drafts create   --lead --subject --body [--template] [--to] [--bcc] [--raw]
drafts update   DRAFT_ID [--subject] [--body] [--template] [--to] [--bcc] [--raw]
drafts delete   DRAFT_ID [--force]
drafts send     DRAFT_ID [--raw]
```

### Common Options
- `-r, --raw` - Output raw JSON instead of formatted tables
- `-p, --page` - Page number for paginated results
- `-s, --page-size` - Items per page
- `-q, --search` - Search/filter text
- `-f, --force` - Skip confirmation prompts

### Examples
```bash
# List leads in Berlin with hot temperature
python cli.py leads list --city Berlin --temperature hot

# Create a new lead
python cli.py leads create --name "Test Venue" --city Berlin --country Germany --type Venue

# Import leads from JSON file
python cli.py leads import leads.json

# Preview email (dry-run, safe)
python cli.py leads send-email 123 --template 2

# Actually send email
python cli.py leads send-email 123 --template 2 --send

# Start research for a city
python cli.py cities research 11

# Check research job status
python cli.py jobs list --status completed
```

## Architecture

### Project Layout
```
src/
├── conftest.py             # Global test configuration (DB, Celery, auth)
├── crm/                    # Django project root
│   ├── settings.py         # Main settings (uses python-decouple)
│   ├── urls.py             # Root URL config
│   ├── api.py              # API initialization + health check
│   ├── dashboard.py        # Custom admin dashboard
│   └── celery.py           # Celery app config
└── leads/                  # Main app
    ├── models.py           # DB models
    ├── schema.py           # API schemas (Pydantic)
    ├── controllers.py      # API endpoints (Ninja controllers)
    ├── service.py          # Business logic layer
    ├── tasks.py            # Celery background tasks
    ├── admin.py            # Admin interface config
    └── tests/              # Test suite
```

### Data Model

**Core Models:**
- `Lead`: Main CRM entity with status, temperature, contact info, social links
- `City`: Lead location (name + country + ISO2 code)
- `LeadType`: Categorization (e.g., "Collective", "Venue", "Promoter")
- `Tag`: Many-to-many for flexible categorization
- `ResearchJob`: Tracks Gemini Deep Research jobs (status, results, leads created)
- `ResearchPromptConfig`: Singleton config for AI research prompt template

**Important Patterns:**
- Historical tracking enabled on `Lead` model via `simple_history`
- All related objects (City, LeadType, Tags) are auto-created via case-insensitive lookup in `service.py`
- Research jobs have a unique constraint preventing duplicate active research per city

### API Architecture (src/crm/api.py + src/leads/controllers.py)

The API uses **django-ninja-extra** with a controller-based pattern:

1. **API Registration** (src/crm/api.py):
   - `NinjaExtraAPI` instance with API key auth (disabled in DEBUG mode)
   - Controllers registered via `api.register_controllers()`
   - Auth uses `X-API-Key` header

2. **Controller Pattern** (src/leads/controllers.py):
   - Controllers decorated with `@api_controller("/path", tags=["Tag"])`
   - Routes defined with `@route.get/post/put/patch/delete`
   - Built-in pagination via `@paginate(PageNumberPaginationExtra)`
   - Built-in search via `@searching(Searching, search_fields=[...])`
   - Filtering via FilterSchema classes (e.g., `LeadFilterSchema`)

3. **Business Logic Separation**:
   - Controllers handle HTTP concerns (validation, pagination, responses)
   - All business logic lives in `service.py` functions
   - Service layer handles related object creation/lookup

### Background Tasks (src/leads/tasks.py)

**Gemini Deep Research Integration:**
1. `start_research(city_id)`: Initiates research job
   - Creates `ResearchJob` with PENDING status
   - Builds prompt from `ResearchPromptConfig` singleton
   - Calls Gemini Deep Research API with structured output (Pydantic schema)
   - Updates job to RUNNING with interaction ID

2. `poll_research_jobs()`: Periodic task (runs via celery-beat)
   - Polls all RUNNING jobs (filters by status)
   - Calls `process_research_job()` synchronously for each job
   - Returns summary of processed/completed/failed jobs

3. `process_research_job(job_id)`: Process a single research job
   - Fetches interaction from Gemini API
   - Handles completed/failed/cancelled/running states
   - Can be called synchronously from `poll_research_jobs()`
   - Can be triggered manually from admin panel via action
   - Creates/updates leads from structured results
   - Lead deduplication via email, phone, instagram, telegram, website, or name+city

**Lead Creation Strategy:**
- Existing leads: fills blank fields only (non-destructive merge)
- New leads: created with source="Gemini Deep Research"
- Tags are always additive

**Response Parsing (3-tier fallback):**
1. Direct JSON parsing (ideal case)
2. JSON extraction heuristic (handles malformed responses with schema mixed in)
3. AI-powered fallback using gemini-2.0-flash-exp to parse raw text

### Admin Interface (src/crm/dashboard.py + src/leads/admin.py)

- Uses django-unfold for modern UI
- Custom sidebar navigation defined in settings.py `UNFOLD` config
- Dashboard callback in `crm.dashboard.dashboard_callback`
- Historical changes viewable via simple_history integration

## Configuration (.env)

Key environment variables:
- `SECRET_KEY`: Django secret (required in prod)
- `DEBUG`: Boolean (default True)
- `ALLOWED_HOSTS`: CSV list (default localhost,127.0.0.1)
- `CSRF_TRUSTED_ORIGINS`: CSV list of trusted origins for CSRF validation (REQUIRED in prod, e.g., "https://crm.example.com")
- `ADMIN_URL`: Custom admin path (default "admin/")
- `API_KEY`: API authentication key (default "dev-api-key")
- `GEMINI_API_KEY`: Required for research tasks
- `REDIS_HOST`, `REDIS_PORT`: Celery broker
- `GOOGLE_SSO_CLIENT_ID`, `GOOGLE_SSO_CLIENT_SECRET`: OAuth
- `GOOGLE_SSO_SUPERUSER_LIST`: CSV of emails to auto-promote

PostgreSQL (prod only):
- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`

## Code Quality Standards

### Linting (Ruff)
- Line length: 120 chars
- Migrations excluded from docstring rules
- Google-style docstrings required
- Max complexity: 10
- Import sorting enabled

### Type Checking (Mypy)
- Strict mode with `--extra-checks --warn-unreachable --warn-unused-ignores`
- Django stubs configured (`django-stubs[compatible-mypy]`)
- Migrations excluded from checks
- See `pyproject.toml` for third-party ignore overrides

### Typing Style
- Always use `import typing as t` instead of `from typing import ...`
- Reference types as `t.Any`, `t.Annotated`, etc.

### Testing (Pytest)
- All tests in `src/leads/tests/`
- Uses `pytest-django` with `DJANGO_SETTINGS_MODULE=crm.settings`
- Test configuration (in-memory DB, Celery eager mode) handled via `src/conftest.py` fixtures
- Coverage configured to omit migrations, management commands, wsgi/asgi
- Fixtures in `conftest.py` files

## Docker

Multi-stage Dockerfile:
1. **Builder**: Uses uv to install deps, collects static files
2. **Runtime**: Minimal Python 3.14 slim image, runs as non-root `appuser`

Entrypoint script runs migrations only when starting gunicorn (web process).

## Common Patterns

### Adding a New API Endpoint
1. Define input/output schemas in `schema.py`
2. Add business logic function to `service.py`
3. Add route to appropriate controller in `controllers.py`
4. Write tests in `tests/`

### Adding a Celery Task
1. Define task in `tasks.py` with `@shared_task`
2. For periodic tasks, create via admin or migration (see `0006_add_poll_research_periodic_task.py`)
3. Configure schedule in django-celery-beat admin

### Working with Related Objects
Always use the service layer functions for related object creation:
- `get_or_create_city(city_in)` - case-insensitive by name+country
- `get_or_create_lead_type(name)` - case-insensitive by name
- `get_or_create_tags(tag_names)` - case-insensitive by name

These ensure consistent lookup behavior across API and tasks.