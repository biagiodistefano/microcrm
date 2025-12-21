# Release Notes

## v1.0.0 - First Public Release

**Release Date:** December 2025

This is the first public release of MicroCRM, a lightweight, AI-powered CRM for lead management and research. Built initially to power lead generation for [Revel](https://github.com/letsrevel), it's fully generic and can be adapted for any business needing smart lead tracking.

---

### Highlights

- **Full-featured Lead Management** - Complete CRUD API with rich filtering, searching, and pagination
- **AI-Powered Research** - Automated lead discovery using Google Gemini Deep Research
- **Modern Admin Interface** - Beautiful, responsive admin UI with Django Unfold
- **Production-Ready Deployment** - Docker Compose setup with Caddy, PostgreSQL, Redis, and Celery
- **Easy Setup Wizards** - Interactive scripts for both local development and production deployment

---

### Features

#### Lead Management
- Create, read, update, and delete leads via REST API
- Rich lead data model: contact info, company details, location, status, temperature, tags
- Full-text search across name, email, company, notes, and social handles
- Advanced filtering by status, temperature, lead type, city, country, and tags
- Pagination with configurable page sizes
- Case-insensitive auto-creation of related objects (cities, lead types, tags)

#### Action Tracking
- Schedule and track follow-up actions for each lead
- Status workflow: pending, in_progress, completed, cancelled
- Due date tracking with filtering support
- Historical tracking of all action changes

#### AI Research (Gemini Deep Research)
- Automated lead discovery for specific cities
- Customizable research prompts via admin interface
- Background processing with Celery
- Smart lead deduplication (email, phone, instagram, telegram, website, name+city)
- Non-destructive merging: fills blank fields only on existing leads
- Additive tag handling
- 3-tier response parsing with fallback heuristics

#### Admin Interface
- Modern, responsive UI with Django Unfold
- Custom dashboard with lead statistics
- Google SSO authentication
- Task monitoring (Celery results and periodic schedules)
- Historical change tracking via django-simple-history

#### API
- RESTful API built with Django Ninja
- Auto-generated OpenAPI documentation at `/api/docs`
- API key authentication (disabled in DEBUG mode)
- Comprehensive endpoints for leads, actions, cities, lead types, tags, and research jobs

---

### Deployment Options

#### Local Development
- `./local_wizard.sh` - Interactive setup wizard for Docker Desktop
- Full stack: Redis, Django, Celery worker, Celery beat
- SQLite database, sample data seeding

#### Production
- `./prod_wizard.sh` - Interactive production deployment wizard
- Full stack: Caddy (automatic HTTPS), PostgreSQL, Redis, Django/Gunicorn, Celery
- Automatic Let's Encrypt certificates
- Security headers and secure cookie settings

#### DigitalOcean
- Cloud-init script for one-click Droplet setup
- Non-interactive deployment via environment variables
- See `.do/README.md` for details

---

### Technical Stack

| Component | Technology |
|-----------|------------|
| Framework | Django 5.2+ |
| Python | 3.14+ |
| API | Django Ninja + Django Ninja Extra |
| Admin | Django Unfold |
| Task Queue | Celery + Redis |
| Scheduler | django-celery-beat |
| AI | Google Gemini Deep Research |
| Database | SQLite (dev), PostgreSQL (prod) |
| Package Manager | uv |
| Linting | Ruff |
| Type Checking | mypy (strict mode) |
| Testing | pytest + pytest-django |

---

### Docker Images

Docker images are published to GitHub Container Registry:

```
ghcr.io/biagiodistefano/microcrm:1.0.0
ghcr.io/biagiodistefano/microcrm:latest
```

---

### Configuration

Key environment variables:

| Variable | Description |
|----------|-------------|
| `DOMAIN` | Production domain (single source of truth) |
| `SECRET_KEY` | Django secret key |
| `DEBUG` | Enable debug mode (default: True) |
| `GEMINI_API_KEY` | Google Gemini API key for AI research |
| `API_KEY` | API authentication key |
| `SUPERUSER_USERNAME` | Admin username |
| `SUPERUSER_PASSWORD` | Admin password |
| `DB_*` | PostgreSQL connection settings (production) |
| `REDIS_HOST` | Redis hostname |
| `GOOGLE_SSO_*` | Google SSO credentials (optional) |

See `.env.example` for the complete list.

---

### Getting Started

#### Quick Start (Docker)
```bash
git clone https://github.com/biagiodistefano/microcrm.git
cd microcrm
./local_wizard.sh
```

#### Development Setup
```bash
make setup
make run
```

Access the application:
- Admin Panel: http://localhost:8000/admin/
- API Docs: http://localhost:8000/api/docs

---

### License

MIT License - see [LICENSE](LICENSE) for details.

---

### Links

- **Repository:** https://github.com/biagiodistefano/microcrm
- **Issues:** https://github.com/biagiodistefano/microcrm/issues
- **Documentation:** See README.md
