"""MicroCRM API Client CLI.

A full-featured CLI client for the MicroCRM API using Typer.
"""

import json
import typing as t
from pathlib import Path

import httpx
import typer
from decouple import config
from rich import print as rprint
from rich.console import Console
from rich.table import Table

# --- Configuration ---
API_KEY: str = config("API_KEY", default="dev-api-key")
BASE_URL: str = config("CRM_BASE_URL", default="http://localhost:8000/api")

console = Console()

# --- API Client ---


class APIClient:
    """HTTP client wrapper for the MicroCRM API."""

    def __init__(self, base_url: str = BASE_URL, api_key: str = API_KEY) -> None:
        """Initialize API client with base URL and API key."""
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _get_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": self.api_key},
            timeout=30.0,
        )

    def get(self, path: str, params: dict | None = None) -> dict | list:
        """GET request."""
        with self._get_client() as client:
            # Filter out None values from params
            if params:
                params = {k: v for k, v in params.items() if v is not None}
            response = client.get(path, params=params)
            response.raise_for_status()
            return response.json()

    def post(self, path: str, data: dict | None = None) -> dict:
        """POST request."""
        with self._get_client() as client:
            response = client.post(path, json=data)
            response.raise_for_status()
            return response.json()

    def put(self, path: str, data: dict) -> dict:
        """PUT request."""
        with self._get_client() as client:
            response = client.put(path, json=data)
            response.raise_for_status()
            return response.json()

    def patch(self, path: str, data: dict) -> dict:
        """PATCH request."""
        with self._get_client() as client:
            # Filter out None values for PATCH
            data = {k: v for k, v in data.items() if v is not None}
            response = client.patch(path, json=data)
            response.raise_for_status()
            return response.json()

    def delete(self, path: str) -> None:
        """DELETE request."""
        with self._get_client() as client:
            response = client.delete(path)
            response.raise_for_status()


# Global client instance
api = APIClient()

# --- Output Helpers ---


def output_json(data: t.Any, raw: bool = False) -> None:
    """Output data as formatted JSON."""
    if raw:
        console.print(json.dumps(data), highlight=False)
    else:
        rprint(data)


def output_table(data: list[dict], columns: list[str], title: str = "") -> None:
    """Output data as a rich table."""
    table = Table(title=title, show_header=True)
    for col in columns:
        table.add_column(col.replace("_", " ").title())

    for row in data:
        table.add_row(*[str(row.get(col, "")) for col in columns])

    console.print(table)


def handle_error(e: httpx.HTTPStatusError) -> None:
    """Handle HTTP errors with nice output."""
    try:
        detail = e.response.json()
    except Exception:
        detail = e.response.text
    rprint(f"[red]Error {e.response.status_code}:[/red] {detail}")
    raise typer.Exit(1)


# --- Main App ---
app = typer.Typer(
    name="microcrm",
    help="MicroCRM API Client - Manage leads, cities, actions, and more.",
    no_args_is_help=True,
)

# --- Leads Commands ---
leads_app = typer.Typer(help="Manage leads", no_args_is_help=True)
app.add_typer(leads_app, name="leads")


@leads_app.command("list")
def leads_list(
    # Pagination
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(20, "--page-size", "-s", help="Items per page"),
    # Search
    search: str | None = typer.Option(None, "--search", "-q", help="Search across name, email, company, notes"),
    # Filters
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (new, contacted, qualified, converted, lost)"
    ),
    temperature: str | None = typer.Option(None, "--temperature", "-t", help="Filter by temperature (cold, warm, hot)"),
    lead_type: str | None = typer.Option(None, "--type", help="Filter by lead type name"),
    city_id: int | None = typer.Option(None, "--city-id", help="Filter by city ID"),
    city: str | None = typer.Option(None, "--city", "-c", help="Filter by city name (partial match)"),
    country: str | None = typer.Option(None, "--country", help="Filter by country (partial match)"),
    tag: str | None = typer.Option(None, "--tag", help="Filter by tag name"),
    # Output
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List leads with filtering, searching, and pagination."""
    try:
        params = {
            "page": page,
            "page_size": page_size,
            "search": search,
            "status": status,
            "temperature": temperature,
            "lead_type": lead_type,
            "city_id": city_id,
            "city": city,
            "country": country,
            "tag": tag,
        }
        result = api.get("/leads/", params)
        if raw:
            output_json(result, raw=True)
        else:
            items = result.get("items") or result.get("results", [])
            if items:
                output_table(
                    items,
                    ["id", "name", "email", "status", "temperature"],
                    title=f"Leads (page {page}, {result.get('count', 0)} total)",
                )
            else:
                rprint("[yellow]No leads found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@leads_app.command("get")
def leads_get(
    lead_id: int = typer.Argument(..., help="Lead ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single lead by ID."""
    try:
        result = api.get(f"/leads/{lead_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@leads_app.command("create")
def leads_create(
    name: str = typer.Option(..., "--name", "-n", help="Lead name"),
    email: str = typer.Option("", "--email", "-e", help="Email address"),
    phone: str = typer.Option("", "--phone", help="Phone number"),
    company: str = typer.Option("", "--company", help="Company name"),
    lead_type: str | None = typer.Option(None, "--type", help="Lead type name"),
    city_name: str | None = typer.Option(None, "--city", "-c", help="City name"),
    city_country: str | None = typer.Option(None, "--country", help="City country (required with --city)"),
    city_iso2: str = typer.Option("", "--iso2", help="City ISO2 code"),
    telegram: str = typer.Option("", "--telegram", help="Telegram username"),
    instagram: str = typer.Option("", "--instagram", help="Instagram handle"),
    website: str = typer.Option("", "--website", "-w", help="Website URL"),
    source: str = typer.Option("", "--source", help="Lead source"),
    status: str = typer.Option("new", "--status", "-s", help="Status (new, contacted, qualified, converted, lost)"),
    temperature: str = typer.Option("cold", "--temperature", "-t", help="Temperature (cold, warm, hot)"),
    tags: list[str] = typer.Option([], "--tag", help="Tags (can be specified multiple times)"),
    notes: str = typer.Option("", "--notes", help="Additional notes"),
    value: float | None = typer.Option(None, "--value", "-v", help="Deal value"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Create a new lead."""
    try:
        data: dict[str, t.Any] = {
            "name": name,
            "email": email,
            "phone": phone,
            "company": company,
            "telegram": telegram,
            "instagram": instagram,
            "website": website,
            "source": source,
            "status": status,
            "temperature": temperature,
            "notes": notes,
        }
        if lead_type:
            data["lead_type"] = lead_type
        if city_name and city_country:
            data["city"] = {"name": city_name, "country": city_country, "iso2": city_iso2}
        if tags:
            data["tags"] = tags
        if value is not None:
            data["value"] = str(value)

        result = api.post("/leads/", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Created lead #{result['id']}:[/green] {result['name']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@leads_app.command("update")
def leads_update(
    lead_id: int = typer.Argument(..., help="Lead ID to update"),
    name: str | None = typer.Option(None, "--name", "-n", help="Lead name"),
    email: str | None = typer.Option(None, "--email", "-e", help="Email address"),
    phone: str | None = typer.Option(None, "--phone", help="Phone number"),
    company: str | None = typer.Option(None, "--company", help="Company name"),
    lead_type: str | None = typer.Option(None, "--type", help="Lead type name"),
    city_name: str | None = typer.Option(None, "--city", "-c", help="City name"),
    city_country: str | None = typer.Option(None, "--country", help="City country"),
    city_iso2: str | None = typer.Option(None, "--iso2", help="City ISO2 code"),
    telegram: str | None = typer.Option(None, "--telegram", help="Telegram username"),
    instagram: str | None = typer.Option(None, "--instagram", help="Instagram handle"),
    website: str | None = typer.Option(None, "--website", "-w", help="Website URL"),
    source: str | None = typer.Option(None, "--source", help="Lead source"),
    status: str | None = typer.Option(None, "--status", "-s", help="Status"),
    temperature: str | None = typer.Option(None, "--temperature", "-t", help="Temperature"),
    tags: list[str] | None = typer.Option(None, "--tag", help="Tags"),
    notes: str | None = typer.Option(None, "--notes", help="Additional notes"),
    value: float | None = typer.Option(None, "--value", "-v", help="Deal value"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Partially update a lead (PATCH). Only provided fields are updated."""
    try:
        data: dict[str, t.Any] = {
            "name": name,
            "email": email,
            "phone": phone,
            "company": company,
            "lead_type": lead_type,
            "telegram": telegram,
            "instagram": instagram,
            "website": website,
            "source": source,
            "status": status,
            "temperature": temperature,
            "notes": notes,
        }
        if city_name is not None and city_country is not None:
            data["city"] = {"name": city_name, "country": city_country, "iso2": city_iso2 or ""}
        if tags is not None:
            data["tags"] = tags
        if value is not None:
            data["value"] = str(value)

        result = api.patch(f"/leads/{lead_id}", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Updated lead #{result['id']}:[/green] {result['name']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@leads_app.command("delete")
def leads_delete(
    lead_id: int = typer.Argument(..., help="Lead ID to delete"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a lead."""
    if not force:
        confirm = typer.confirm(f"Delete lead #{lead_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        api.delete(f"/leads/{lead_id}")
        rprint(f"[green]Deleted lead #{lead_id}[/green]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@leads_app.command("import")
def leads_import(
    file: Path = typer.Argument(..., help="JSON file containing leads array"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Validate without creating"),
) -> None:
    """Import leads from a JSON file."""
    if not file.exists():
        rprint(f"[red]File not found: {file}[/red]")
        raise typer.Exit(1)

    try:
        leads = json.loads(file.read_text())
    except json.JSONDecodeError as e:
        rprint(f"[red]Invalid JSON: {e}[/red]")
        raise typer.Exit(1)

    if not isinstance(leads, list):
        rprint("[red]File must contain a JSON array of leads[/red]")
        raise typer.Exit(1)

    rprint(f"Found {len(leads)} leads in {file}")

    if dry_run:
        rprint("[yellow]Dry run - no leads created[/yellow]")
        return

    success = 0
    failed = 0
    for i, lead in enumerate(leads, 1):
        # Normalize null values
        for key in ("email", "phone", "telegram", "instagram", "website", "source", "notes", "company"):
            if lead.get(key) is None:
                lead[key] = ""
        try:
            result = api.post("/leads/", lead)
            rprint(f"  [{i}/{len(leads)}] [green]Created:[/green] {result['name']} (#{result['id']})")
            success += 1
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json()
            except Exception:
                detail = e.response.text
            rprint(f"  [{i}/{len(leads)}] [red]Failed:[/red] {lead.get('name', 'Unknown')} - {detail}")
            failed += 1

    rprint(f"\n[bold]Done![/bold] Created: {success}, Failed: {failed}")


def _send_email_dry_run(
    lead_id: int,
    template_id: int | None,
    subject: str | None,
    body: str | None,
    to: list[str],
    bcc: list[str],
) -> None:
    """Show what email would be sent without actually sending."""
    rprint("[yellow]DRY-RUN MODE[/yellow] - Email will NOT be sent. Use --send to actually send.\n")
    lead = api.get(f"/leads/{lead_id}")
    rprint(f"[bold]Lead:[/bold] {lead['name']} (#{lead['id']})")
    rprint(f"[bold]To:[/bold] {', '.join(to) if to else lead.get('email', 'N/A')}")
    if bcc:
        rprint(f"[bold]BCC:[/bold] {', '.join(bcc)}")
    if template_id:
        template = api.get(f"/email-templates/{template_id}")
        rprint(f"[bold]Template:[/bold] {template['name']} (#{template['id']})")
        rprint(f"[bold]Subject:[/bold] {template['subject']}")
        rprint(f"[bold]Body:[/bold]\n{template['body']}")
    else:
        rprint(f"[bold]Subject:[/bold] {subject}")
        rprint(f"[bold]Body:[/bold]\n{body}")


def _send_email_execute(
    lead_id: int,
    template_id: int | None,
    subject: str | None,
    body: str | None,
    to: list[str],
    bcc: list[str],
    background: bool,
    raw: bool,
) -> None:
    """Actually send the email via API."""
    data: dict[str, t.Any] = {"send_in_background": background}
    if template_id:
        data["template_id"] = template_id
    if subject:
        data["subject"] = subject
    if body:
        data["body"] = body
    if to:
        data["to"] = to
    if bcc:
        data["bcc"] = bcc

    result = api.post(f"/leads/{lead_id}/send-email", data)
    if raw:
        output_json(result, raw=True)
    else:
        rprint(f"[green]Email sent![/green] ID: {result['email_id']}, Status: {result['status']}")


@leads_app.command("send-email")
def leads_send_email(
    lead_id: int = typer.Argument(..., help="Lead ID"),
    template_id: int | None = typer.Option(None, "--template", "-t", help="Email template ID"),
    subject: str | None = typer.Option(None, "--subject", "-s", help="Email subject (if no template)"),
    body: str | None = typer.Option(None, "--body", "-b", help="Email body (if no template)"),
    to: list[str] = typer.Option([], "--to", help="Override recipient (can specify multiple)"),
    bcc: list[str] = typer.Option([], "--bcc", help="BCC recipients (can specify multiple)"),
    background: bool = typer.Option(False, "--background", help="Send via background task"),
    send: bool = typer.Option(False, "--send", help="Actually send the email (default is dry-run)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Send an email to a lead.

    By default runs in DRY-RUN mode and shows what would be sent.
    Use --send to actually send the email.
    """
    if template_id is None and (subject is None or body is None):
        rprint("[red]Either --template or both --subject and --body are required[/red]")
        raise typer.Exit(1)

    try:
        if send:
            _send_email_execute(lead_id, template_id, subject, body, to, bcc, background, raw)
        else:
            _send_email_dry_run(lead_id, template_id, subject, body, to, bcc)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@leads_app.command("emails")
def leads_emails(
    lead_id: int = typer.Argument(..., help="Lead ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List emails sent to a lead."""
    try:
        result = api.get(f"/leads/{lead_id}/emails")
        if raw:
            output_json(result, raw=True)
        else:
            if result:
                output_table(
                    result,
                    ["id", "to", "subject", "status", "created_at"],
                    title=f"Emails for lead #{lead_id}",
                )
            else:
                rprint("[yellow]No emails sent to this lead[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Cities Commands ---
cities_app = typer.Typer(help="Manage cities", no_args_is_help=True)
app.add_typer(cities_app, name="cities")


@cities_app.command("list")
def cities_list(
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(50, "--page-size", "-s", help="Items per page"),
    search: str | None = typer.Option(None, "--search", "-q", help="Search by name or country"),
    country: str | None = typer.Option(None, "--country", "-c", help="Filter by country"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List cities with filtering and searching."""
    try:
        params = {
            "page": page,
            "page_size": page_size,
            "search": search,
            "country": country,
        }
        result = api.get("/cities/", params)
        if raw:
            output_json(result, raw=True)
        else:
            items = result.get("items") or result.get("results", [])
            if items:
                output_table(items, ["id", "name", "country", "iso2"], title="Cities")
            else:
                rprint("[yellow]No cities found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@cities_app.command("get")
def cities_get(
    city_id: int = typer.Argument(..., help="City ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single city by ID."""
    try:
        result = api.get(f"/cities/{city_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@cities_app.command("create")
def cities_create(
    name: str = typer.Option(..., "--name", "-n", help="City name"),
    country: str = typer.Option(..., "--country", "-c", help="Country name"),
    iso2: str = typer.Option("", "--iso2", help="ISO2 country code"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Create a new city."""
    try:
        data = {"name": name, "country": country, "iso2": iso2}
        result = api.post("/cities/", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Created city #{result['id']}:[/green] {result['name']}, {result['country']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@cities_app.command("research")
def cities_research(
    city_id: int = typer.Argument(..., help="City ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Start deep research for a city."""
    try:
        result = api.post(f"/cities/{city_id}/research")
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Research started![/green] Job ID: {result['job_id']}, Status: {result['status']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Lead Types Commands ---
lead_types_app = typer.Typer(help="Browse lead types", no_args_is_help=True)
app.add_typer(lead_types_app, name="types")


@lead_types_app.command("list")
def lead_types_list(
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List all lead types."""
    try:
        result = api.get("/lead-types/")
        if raw:
            output_json(result, raw=True)
        else:
            if result:
                output_table(result, ["id", "name"], title="Lead Types")
            else:
                rprint("[yellow]No lead types found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Tags Commands ---
tags_app = typer.Typer(help="Browse tags", no_args_is_help=True)
app.add_typer(tags_app, name="tags")


@tags_app.command("list")
def tags_list(
    search: str | None = typer.Option(None, "--search", "-q", help="Search by name"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List all tags."""
    try:
        params = {"search": search}
        result = api.get("/tags/", params)
        if raw:
            output_json(result, raw=True)
        else:
            if result:
                output_table(result, ["id", "name"], title="Tags")
            else:
                rprint("[yellow]No tags found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Actions Commands ---
actions_app = typer.Typer(help="Manage actions/tasks", no_args_is_help=True)
app.add_typer(actions_app, name="actions")


@actions_app.command("list")
def actions_list(
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(20, "--page-size", "-s", help="Items per page"),
    search: str | None = typer.Option(None, "--search", "-q", help="Search by name or notes"),
    lead_id: int | None = typer.Option(None, "--lead", "-l", help="Filter by lead ID"),
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (pending, in_progress, completed, cancelled)"
    ),
    due_date: str | None = typer.Option(None, "--due-date", help="Filter by exact due date (YYYY-MM-DD)"),
    due_before: str | None = typer.Option(None, "--due-before", help="Filter due on or before (YYYY-MM-DD)"),
    due_after: str | None = typer.Option(None, "--due-after", help="Filter due on or after (YYYY-MM-DD)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List actions with filtering and pagination."""
    try:
        params = {
            "page": page,
            "page_size": page_size,
            "search": search,
            "lead_id": lead_id,
            "status": status,
            "due_date": due_date,
            "due_before": due_before,
            "due_after": due_after,
        }
        result = api.get("/actions/", params)
        if raw:
            output_json(result, raw=True)
        else:
            items = result.get("items") or result.get("results", [])
            if items:
                output_table(
                    items,
                    ["id", "name", "lead_id", "status", "due_date"],
                    title="Actions",
                )
            else:
                rprint("[yellow]No actions found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@actions_app.command("get")
def actions_get(
    action_id: int = typer.Argument(..., help="Action ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single action by ID."""
    try:
        result = api.get(f"/actions/{action_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@actions_app.command("create")
def actions_create(
    lead_id: int = typer.Option(..., "--lead", "-l", help="Lead ID"),
    name: str = typer.Option(..., "--name", "-n", help="Action name"),
    notes: str = typer.Option("", "--notes", help="Additional notes"),
    due_date: str | None = typer.Option(None, "--due-date", "-d", help="Due date (YYYY-MM-DD)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Create a new action for a lead."""
    try:
        data: dict[str, t.Any] = {
            "lead_id": lead_id,
            "name": name,
            "notes": notes,
        }
        if due_date:
            data["due_date"] = due_date

        result = api.post("/actions/", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Created action #{result['id']}:[/green] {result['name']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@actions_app.command("update")
def actions_update(
    action_id: int = typer.Argument(..., help="Action ID"),
    name: str | None = typer.Option(None, "--name", "-n", help="Action name"),
    notes: str | None = typer.Option(None, "--notes", help="Additional notes"),
    status: str | None = typer.Option(
        None, "--status", "-s", help="Status (pending, in_progress, completed, cancelled)"
    ),
    due_date: str | None = typer.Option(None, "--due-date", "-d", help="Due date (YYYY-MM-DD)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Partially update an action."""
    try:
        data: dict[str, t.Any] = {
            "name": name,
            "notes": notes,
            "status": status,
            "due_date": due_date,
        }
        result = api.patch(f"/actions/{action_id}", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Updated action #{result['id']}:[/green] {result['name']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@actions_app.command("delete")
def actions_delete(
    action_id: int = typer.Argument(..., help="Action ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete an action."""
    if not force:
        confirm = typer.confirm(f"Delete action #{action_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        api.delete(f"/actions/{action_id}")
        rprint(f"[green]Deleted action #{action_id}[/green]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Research Jobs Commands ---
jobs_app = typer.Typer(help="Manage research jobs", no_args_is_help=True)
app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def jobs_list(
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(20, "--page-size", "-s", help="Items per page"),
    search: str | None = typer.Option(None, "--search", "-q", help="Search by city name"),
    city_id: int | None = typer.Option(None, "--city-id", help="Filter by city ID"),
    status: str | None = typer.Option(
        None, "--status", help="Filter by status (not_started, pending, running, completed, failed)"
    ),
    country: str | None = typer.Option(None, "--country", "-c", help="Filter by country"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List research jobs with filtering and pagination."""
    try:
        params = {
            "page": page,
            "page_size": page_size,
            "search": search,
            "city_id": city_id,
            "status": status,
            "country": country,
        }
        result = api.get("/research-jobs/", params)
        if raw:
            output_json(result, raw=True)
        else:
            items = result.get("items") or result.get("results", [])
            if items:
                # Flatten city info for display
                for item in items:
                    if item.get("city"):
                        item["city_name"] = item["city"]["name"]
                output_table(
                    items,
                    ["id", "city_name", "status", "leads_created", "created_at"],
                    title="Research Jobs",
                )
            else:
                rprint("[yellow]No research jobs found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@jobs_app.command("get")
def jobs_get(
    job_id: int = typer.Argument(..., help="Job ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single research job by ID (includes full details)."""
    try:
        result = api.get(f"/research-jobs/{job_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@jobs_app.command("create")
def jobs_create(
    city_id: int = typer.Option(..., "--city", "-c", help="City ID to research"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Create a new research job (does not start it)."""
    try:
        data = {"city_id": city_id}
        result = api.post("/research-jobs/", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Created job #{result['id']}[/green] (status: {result['status']})")
            rprint("[dim]Use 'jobs run' to start the research[/dim]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@jobs_app.command("run")
def jobs_run(
    job_id: int = typer.Argument(..., help="Job ID to run"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Start or retry a research job."""
    try:
        result = api.post(f"/research-jobs/{job_id}/run")
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Job #{result['job_id']} started![/green] Status: {result['status']}")
            if result.get("message"):
                rprint(f"[dim]{result['message']}[/dim]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@jobs_app.command("reprocess")
def jobs_reprocess(
    job_id: int = typer.Argument(..., help="Job ID to reprocess"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Reprocess a job (re-parse results without calling Gemini)."""
    try:
        result = api.post(f"/research-jobs/{job_id}/reprocess")
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Job #{result['job_id']} reprocessed![/green]")
            if result.get("leads_created") is not None:
                rprint(f"Leads created: {result['leads_created']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@jobs_app.command("delete")
def jobs_delete(
    job_id: int = typer.Argument(..., help="Job ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete a research job (only if not running)."""
    if not force:
        confirm = typer.confirm(f"Delete job #{job_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        api.delete(f"/research-jobs/{job_id}")
        rprint(f"[green]Deleted job #{job_id}[/green]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Email Templates Commands ---
templates_app = typer.Typer(help="Manage email templates", no_args_is_help=True)
app.add_typer(templates_app, name="templates")


@templates_app.command("list")
def templates_list(
    search: str | None = typer.Option(None, "--search", "-q", help="Search by name or subject"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List all email templates."""
    try:
        params = {"search": search}
        result = api.get("/email-templates/", params)
        if raw:
            output_json(result, raw=True)
        else:
            if result:
                output_table(result, ["id", "name", "language", "subject"], title="Email Templates")
            else:
                rprint("[yellow]No templates found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@templates_app.command("get")
def templates_get(
    template_id: int = typer.Argument(..., help="Template ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single email template by ID."""
    try:
        result = api.get(f"/email-templates/{template_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@templates_app.command("create")
def templates_create(
    name: str = typer.Option(..., "--name", "-n", help="Template name (must be unique)"),
    subject: str = typer.Option(..., "--subject", "-s", help="Email subject"),
    body: str = typer.Option(..., "--body", "-b", help="Email body"),
    language: str = typer.Option("en", "--language", "-l", help="Language code (en, it, es, de, fr)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Create a new email template."""
    try:
        data = {
            "name": name,
            "subject": subject,
            "body": body,
            "language": language,
        }
        result = api.post("/email-templates/", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Created template #{result['id']}:[/green] {result['name']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@templates_app.command("update")
def templates_update(
    template_id: int = typer.Argument(..., help="Template ID"),
    name: str | None = typer.Option(None, "--name", "-n", help="Template name"),
    subject: str | None = typer.Option(None, "--subject", "-s", help="Email subject"),
    body: str | None = typer.Option(None, "--body", "-b", help="Email body"),
    language: str | None = typer.Option(None, "--language", "-l", help="Language code"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Partially update an email template."""
    try:
        data = {
            "name": name,
            "subject": subject,
            "body": body,
            "language": language,
        }
        result = api.patch(f"/email-templates/{template_id}", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Updated template #{result['id']}:[/green] {result['name']}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@templates_app.command("delete")
def templates_delete(
    template_id: int = typer.Argument(..., help="Template ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete an email template."""
    if not force:
        confirm = typer.confirm(f"Delete template #{template_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        api.delete(f"/email-templates/{template_id}")
        rprint(f"[green]Deleted template #{template_id}[/green]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Emails Sent Commands ---
emails_app = typer.Typer(help="View sent emails", no_args_is_help=True)
app.add_typer(emails_app, name="emails")


@emails_app.command("list")
def emails_list(
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(20, "--page-size", "-s", help="Items per page"),
    lead_id: int | None = typer.Option(None, "--lead", "-l", help="Filter by lead ID"),
    template_id: int | None = typer.Option(None, "--template", "-t", help="Filter by template ID"),
    status: str | None = typer.Option(None, "--status", help="Filter by status (pending, sent, failed)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List sent emails with filtering and pagination."""
    try:
        params = {
            "page": page,
            "page_size": page_size,
            "lead_id": lead_id,
            "template_id": template_id,
            "status": status,
        }
        result = api.get("/emails-sent/", params)
        if raw:
            output_json(result, raw=True)
        else:
            items = result.get("items") or result.get("results", [])
            if items:
                output_table(
                    items,
                    ["id", "lead_id", "to", "subject", "status", "created_at"],
                    title="Sent Emails",
                )
            else:
                rprint("[yellow]No emails found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@emails_app.command("get")
def emails_get(
    email_id: int = typer.Argument(..., help="Email ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single sent email by ID."""
    try:
        result = api.get(f"/emails-sent/{email_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Email Drafts Commands ---
drafts_app = typer.Typer(help="Manage email drafts", no_args_is_help=True)
app.add_typer(drafts_app, name="drafts")


@drafts_app.command("list")
def drafts_list(
    page: int = typer.Option(1, "--page", "-p", help="Page number"),
    page_size: int = typer.Option(20, "--page-size", "-s", help="Items per page"),
    lead_id: int | None = typer.Option(None, "--lead", "-l", help="Filter by lead ID"),
    template_id: int | None = typer.Option(None, "--template", "-t", help="Filter by template ID"),
    search: str | None = typer.Option(None, "--search", "-q", help="Search in subject, body, lead name"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """List email drafts with filtering and pagination."""
    try:
        params = {
            "page": page,
            "page_size": page_size,
            "lead_id": lead_id,
            "template_id": template_id,
            "search": search,
        }
        result = api.get("/email-drafts/", params)
        if raw:
            output_json(result, raw=True)
        else:
            items = result.get("items") or result.get("results", [])
            if items:
                output_table(
                    items,
                    ["id", "lead_id", "subject", "updated_at"],
                    title="Email Drafts",
                )
            else:
                rprint("[yellow]No drafts found[/yellow]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@drafts_app.command("get")
def drafts_get(
    draft_id: int = typer.Argument(..., help="Draft ID"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Get a single email draft by ID."""
    try:
        result = api.get(f"/email-drafts/{draft_id}")
        output_json(result, raw=raw)
    except httpx.HTTPStatusError as e:
        handle_error(e)


@drafts_app.command("create")
def drafts_create(
    lead_id: int = typer.Option(..., "--lead", "-l", help="Lead ID (required)"),
    subject: str = typer.Option(..., "--subject", "-s", help="Email subject"),
    body: str = typer.Option(..., "--body", "-b", help="Email body"),
    template_id: int | None = typer.Option(None, "--template", "-t", help="Template ID"),
    to: str | None = typer.Option(None, "--to", help="Recipient email (comma-separated, defaults to lead email)"),
    bcc: str | None = typer.Option(None, "--bcc", help="BCC emails (comma-separated)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Create a new email draft."""
    try:
        data: dict[str, t.Any] = {
            "lead_id": lead_id,
            "subject": subject,
            "body": body,
        }
        if template_id:
            data["template_id"] = template_id
        if to:
            data["to"] = [e.strip() for e in to.split(",")]
        if bcc:
            data["bcc"] = [e.strip() for e in bcc.split(",")]

        result = api.post("/email-drafts/", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Created draft #{result['id']}:[/green] {result['subject'][:50]}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@drafts_app.command("update")
def drafts_update(
    draft_id: int = typer.Argument(..., help="Draft ID"),
    subject: str | None = typer.Option(None, "--subject", "-s", help="Email subject"),
    body: str | None = typer.Option(None, "--body", "-b", help="Email body"),
    template_id: int | None = typer.Option(None, "--template", "-t", help="Template ID"),
    to: str | None = typer.Option(None, "--to", help="Recipient email (comma-separated)"),
    bcc: str | None = typer.Option(None, "--bcc", help="BCC emails (comma-separated)"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Partially update an email draft."""
    try:
        data: dict[str, t.Any] = {}
        if subject is not None:
            data["subject"] = subject
        if body is not None:
            data["body"] = body
        if template_id is not None:
            data["template_id"] = template_id
        if to is not None:
            data["to"] = [e.strip() for e in to.split(",")]
        if bcc is not None:
            data["bcc"] = [e.strip() for e in bcc.split(",")]

        result = api.patch(f"/email-drafts/{draft_id}", data)
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Updated draft #{result['id']}:[/green] {result['subject'][:50]}")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@drafts_app.command("delete")
def drafts_delete(
    draft_id: int = typer.Argument(..., help="Draft ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Delete an email draft."""
    if not force:
        confirm = typer.confirm(f"Delete draft #{draft_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        api.delete(f"/email-drafts/{draft_id}")
        rprint(f"[green]Deleted draft #{draft_id}[/green]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


@drafts_app.command("send")
def drafts_send(
    draft_id: int = typer.Argument(..., help="Draft ID"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Output raw JSON"),
) -> None:
    """Send an email draft.

    This will send the email, create an EmailSent record, update the lead's
    last_contact date, and delete the draft.
    """
    if not force:
        confirm = typer.confirm(f"Send draft #{draft_id}?")
        if not confirm:
            raise typer.Abort()
    try:
        result = api.post(f"/email-drafts/{draft_id}/send")
        if raw:
            output_json(result, raw=True)
        else:
            rprint(f"[green]Draft sent successfully! Email ID: #{result['id']}[/green]")
    except httpx.HTTPStatusError as e:
        handle_error(e)


# --- Config Command ---
@app.command("config")
def show_config() -> None:
    """Show current CLI configuration."""
    rprint(f"[bold]Base URL:[/bold] {BASE_URL}")
    rprint(f"[bold]API Key:[/bold] {API_KEY[:8]}..." if len(API_KEY) > 8 else f"[bold]API Key:[/bold] {API_KEY}")
    rprint("\n[dim]Configure via environment variables:[/dim]")
    rprint("  CRM_BASE_URL - API base URL")
    rprint("  API_KEY - API authentication key")


if __name__ == "__main__":
    app()
