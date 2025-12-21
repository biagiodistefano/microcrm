"""Dashboard callback for Django Unfold admin interface."""

import json
import typing as t
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db.models import Count
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from leads.models import Action, City, Lead, LeadType, ResearchJob, Tag


def _get_leads_by_status() -> dict[str, int]:
    """Get lead counts by status."""
    stats = Lead.objects.values("status").annotate(count=Count("id"))
    return {stat["status"]: stat["count"] for stat in stats}


def _get_leads_by_temperature() -> dict[str, int]:
    """Get lead counts by temperature."""
    stats = Lead.objects.values("temperature").annotate(count=Count("id"))
    return {stat["temperature"]: stat["count"] for stat in stats}


def _get_lead_growth_data(days: int = 30) -> dict[str, t.Any]:
    """Calculate lead growth statistics over the specified period.

    Args:
        days: Number of days to look back (default: 30)

    Returns:
        Dictionary with labels (week labels) and data (lead counts per week)
    """
    end_date = timezone.now()
    start_date = end_date - timedelta(days=days)

    # Query leads created in the time period
    leads = Lead.objects.filter(created_at__gte=start_date, created_at__lte=end_date).values_list(
        "created_at", flat=True
    )

    # Group by week, storing both the date and count
    weekly_data: dict[date, int] = {}
    for created_at in leads:
        # Get the start of the week (Monday)
        week_start = created_at - timedelta(days=created_at.weekday())
        # Use date only (no time) as key
        week_key = week_start.date()
        weekly_data[week_key] = weekly_data.get(week_key, 0) + 1

    # Ensure we have all weeks in the range (even if 0)
    current_date = start_date.date()
    end_date_only = end_date.date()
    while current_date <= end_date_only:
        week_start_date = current_date - timedelta(days=current_date.weekday())
        if week_start_date not in weekly_data:
            weekly_data[week_start_date] = 0
        current_date += timedelta(days=7)

    # Sort by actual date and format labels
    sorted_weeks = sorted(weekly_data.items(), key=lambda x: x[0])

    return {
        "labels": [week[0].strftime("%b %d") for week in sorted_weeks],
        "data": [week[1] for week in sorted_weeks],
    }


def _get_upcoming_actions(days: int = 7) -> list[dict[str, t.Any]]:
    """Get upcoming actions with due dates."""
    today = timezone.now().date()
    cutoff = today + timedelta(days=days)

    # Include overdue items (past dates) and upcoming items
    actions = (
        Action.objects.filter(
            status__in=[Action.Status.PENDING, Action.Status.IN_PROGRESS],
            due_date__isnull=False,
            due_date__lte=cutoff,
        )
        .exclude(lead__status__in=[Lead.Status.CONVERTED, Lead.Status.LOST])
        .select_related("lead")
        .order_by("due_date")[:15]
    )

    result = []
    for action in actions:
        action_date = action.due_date
        if action_date is None:
            continue
        days_until = (action_date - today).days
        result.append(
            {
                "id": action.lead.id,
                "name": action.lead.name,
                "action_name": action.name,
                "due_date": action_date,
                "temperature": action.lead.temperature,
                "days_until": abs(days_until),
                "is_overdue": days_until < 0,
            }
        )

    return result


def _get_recent_leads_count(days: int = 7) -> int:
    """Get count of leads created in the last N days."""
    cutoff = timezone.now() - timedelta(days=days)
    return Lead.objects.filter(created_at__gte=cutoff).count()


def _get_top_cities(limit: int = 5) -> list[dict[str, t.Any]]:
    """Get top cities by lead count."""
    total = Lead.objects.exclude(city__isnull=True).count()
    if total == 0:
        return []

    cities = City.objects.annotate(lead_count=Count("leads")).filter(lead_count__gt=0).order_by("-lead_count")[:limit]
    base_url = reverse("admin:leads_lead_changelist")

    return [
        {
            "id": city.id,
            "name": f"{city.name}, {city.iso2}",
            "count": city.lead_count,
            "percentage": round((city.lead_count / total) * 100, 1),
            "url": f"{base_url}?city__id__exact={city.id}",
        }
        for city in cities
    ]


def _get_top_lead_types(limit: int = 5) -> list[dict[str, t.Any]]:
    """Get top lead types by count."""
    total = Lead.objects.exclude(lead_type__isnull=True).count()
    if total == 0:
        return []

    lead_types = (
        LeadType.objects.annotate(lead_count=Count("leads")).filter(lead_count__gt=0).order_by("-lead_count")[:limit]
    )
    base_url = reverse("admin:leads_lead_changelist")

    return [
        {
            "id": lt.id,
            "name": lt.name,
            "count": lt.lead_count,
            "percentage": round((lt.lead_count / total) * 100, 1),
            "url": f"{base_url}?lead_type__id__exact={lt.id}",
        }
        for lt in lead_types
    ]


def _get_top_tags(limit: int = 10) -> list[dict[str, t.Any]]:
    """Get top tags by usage count."""
    tags = Tag.objects.annotate(lead_count=Count("leads")).filter(lead_count__gt=0).order_by("-lead_count")[:limit]
    base_url = reverse("admin:leads_lead_changelist")

    return [
        {
            "id": tag.id,
            "name": tag.name,
            "count": tag.lead_count,
            "url": f"{base_url}?tags__id__exact={tag.id}",
        }
        for tag in tags
    ]


def _get_system_health(days: int = 7) -> dict[str, t.Any]:
    """Get system health statistics.

    Args:
        days: Number of days to look back (default: 7)

    Returns:
        Dictionary with system health stats
    """
    from django_celery_results.models import TaskResult

    cutoff_date = timezone.now() - timedelta(days=days)

    # Failed tasks in the last N days
    failed_tasks = TaskResult.objects.filter(status="FAILURE", date_done__gte=cutoff_date).order_by("-date_done")

    failed_count = failed_tasks.count()
    recent_failures = [
        {
            "task_name": task.task_name.split(".")[-1] if task.task_name else "Unknown",
            "date_done": task.date_done,
            "task_id": task.task_id,
        }
        for task in failed_tasks[:5]
    ]

    # Running research jobs
    running_jobs = ResearchJob.objects.filter(
        status__in=[ResearchJob.Status.PENDING, ResearchJob.Status.RUNNING]
    ).count()

    return {
        "failed_tasks": failed_count,
        "recent_failures": recent_failures,
        "running_jobs": running_jobs,
        "days": days,
    }


def _get_system_info() -> dict[str, t.Any]:
    """Get system information."""
    db_engine = str(settings.DATABASES["default"]["ENGINE"])
    db_name = "SQLite" if "sqlite" in db_engine else "PostgreSQL"

    return {
        "version": settings.VERSION,
        "debug": settings.DEBUG,
        "database": db_name,
        "celery_eager": settings.CELERY_TASK_ALWAYS_EAGER,
    }


def _get_recent_research_jobs(limit: int = 5) -> list[dict[str, t.Any]]:
    """Get recent research jobs."""
    jobs = ResearchJob.objects.select_related("city").order_by("-created_at")[:limit]

    return [
        {
            "id": job.id,
            "city": str(job.city),
            "status": job.status,
            "leads_created": job.leads_created,
            "created_at": job.created_at,
            "completed_at": job.completed_at,
        }
        for job in jobs
    ]


def _get_chart_data() -> dict[str, t.Any]:
    """Prepare chart data for JavaScript."""
    # Status data
    status_counts = _get_leads_by_status()
    status_order = ["new", "contacted", "qualified", "converted", "lost"]
    status_labels = ["New", "Contacted", "Qualified", "Converted", "Lost"]
    status_data = [status_counts.get(s, 0) for s in status_order]

    # Temperature data
    temp_counts = _get_leads_by_temperature()
    temp_order = ["cold", "warm", "hot"]
    temp_labels = ["Cold", "Warm", "Hot"]
    temp_data = [temp_counts.get(t, 0) for t in temp_order]

    # Growth data
    growth = _get_lead_growth_data(days=30)

    return {
        "status_labels": json.dumps(status_labels),
        "status_data": json.dumps(status_data),
        "temp_labels": json.dumps(temp_labels),
        "temp_data": json.dumps(temp_data),
        "growth_labels": json.dumps(growth["labels"]),
        "growth_data": json.dumps(growth["data"]),
    }


def dashboard_callback(request: HttpRequest, context: dict[str, t.Any]) -> dict[str, t.Any]:
    """Prepare custom variables for the admin dashboard.

    This callback is called by Django Unfold to inject custom data into the
    admin index template. Only staff and superusers can access the dashboard.

    Args:
        request: The HTTP request object
        context: The existing template context

    Returns:
        Updated context dictionary with dashboard data
    """
    user = request.user

    if isinstance(user, AnonymousUser) or not user.is_staff:
        return context

    # Get statistics
    temp_counts = _get_leads_by_temperature()
    today = timezone.now().date()
    overdue = Action.objects.filter(
        status__in=[Action.Status.PENDING, Action.Status.IN_PROGRESS],
        due_date__lt=today,
    ).count()

    upcoming_actions = _get_upcoming_actions(days=7)

    # Quick actions
    quick_actions = [
        {
            "title": "Add Lead",
            "url": reverse("admin:leads_lead_add"),
            "icon": "➕",
            "external": False,
        },
        {
            "title": "All Leads",
            "url": reverse("admin:leads_lead_changelist"),
            "icon": "👥",
            "external": False,
        },
        {
            "title": "Research Jobs",
            "url": reverse("admin:leads_researchjob_changelist"),
            "icon": "🔬",
            "external": False,
        },
        {
            "title": "Cities",
            "url": reverse("admin:leads_city_changelist"),
            "icon": "🌍",
            "external": False,
        },
    ]

    # Build filter URLs for quick stats
    leads_url = reverse("admin:leads_lead_changelist")
    actions_url = reverse("admin:leads_action_changelist")
    jobs_url = reverse("admin:leads_researchjob_changelist")

    context.update(
        {
            "dashboard": {
                "quick_actions": quick_actions,
                "quick_stats": {
                    "total_leads": Lead.objects.count(),
                    "total_leads_url": leads_url,
                    "recent_leads": _get_recent_leads_count(days=7),
                    "hot_leads": temp_counts.get("hot", 0),
                    "hot_leads_url": f"{leads_url}?temperature__exact=hot",
                    "warm_leads": temp_counts.get("warm", 0),
                    "warm_leads_url": f"{leads_url}?temperature__exact=warm",
                    "running_jobs": ResearchJob.objects.filter(
                        status__in=[ResearchJob.Status.PENDING, ResearchJob.Status.RUNNING]
                    ).count(),
                    "running_jobs_url": f"{jobs_url}?status__in=pending,running",
                    "completed_jobs": ResearchJob.objects.filter(status=ResearchJob.Status.COMPLETED).count(),
                    "completed_jobs_url": f"{jobs_url}?status__exact=completed",
                    "upcoming_actions_count": len(upcoming_actions),
                    "upcoming_actions_url": f"{actions_url}?status__in=pending,in_progress",
                    "overdue_actions": overdue,
                    "overdue_actions_url": f"{actions_url}?due_date__lt={today.isoformat()}",
                },
                "charts": _get_chart_data(),
                "top_cities": _get_top_cities(limit=5),
                "top_lead_types": _get_top_lead_types(limit=5),
                "top_tags": _get_top_tags(limit=10),
                "upcoming_actions": upcoming_actions,
                "system_health": _get_system_health(days=7),
                "system_info": _get_system_info(),
                "recent_research_jobs": _get_recent_research_jobs(limit=5),
            }
        }
    )

    return context
