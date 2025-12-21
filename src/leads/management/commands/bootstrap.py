import typing as t

from decouple import config
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sites.models import Site
from django.core.management.base import BaseCommand
from django.db import transaction

from leads.models import City

User = get_user_model()

CITIES = [
    ("Berlin", "Germany", "DE"),
    ("Vienna", "Austria", "AT"),
    ("Amsterdam", "Netherlands", "NL"),
    ("London", "United Kingdom", "GB"),
    ("Paris", "France", "FR"),
    ("Barcelona", "Spain", "ES"),
    ("Milan", "Italy", "IT"),
    ("Prague", "Czech Republic", "CZ"),
    ("Lisbon", "Portugal", "PT"),
    ("Copenhagen", "Denmark", "DK"),
]


class Command(BaseCommand):
    """Bootstrap the database with essential data (superuser and reference cities).

    This command is safe to run in production. For fake lead data, use the 'seed' command.
    """

    help = "Bootstrap the database with superuser and reference cities (production-safe)."

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        """Handle the command."""
        with transaction.atomic():
            self._configure_site()
            self._create_superuser()
            self._create_cities()

        self.stdout.write(self.style.SUCCESS("Bootstrap complete!"))

    def _configure_site(self) -> None:
        """Configure the Django Site based on DOMAIN or SITE_NAME settings."""
        domain = config("DOMAIN", default="localhost:8000")
        site_name = getattr(settings, "SITE_NAME", "Micro CRM")

        site, created = Site.objects.update_or_create(
            id=settings.SITE_ID,
            defaults={"domain": domain, "name": site_name},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created site: {domain}"))
        else:
            self.stdout.write(f"Updated site: {domain}")

    def _create_superuser(self) -> None:
        username = config("SUPERUSER_USERNAME", default="admin")
        email = config("SUPERUSER_EMAIL", default="admin@example.com")
        password = config("SUPERUSER_PASSWORD", default="admin")

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' already exists.")
            return
        User.objects.create_superuser(username=username, email=email, password=password)
        self.stdout.write(self.style.SUCCESS(f"Created superuser: {username}"))

    def _create_cities(self) -> None:
        created = 0
        for name, country, iso2 in CITIES:
            _, was_created = City.objects.get_or_create(name=name, country=country, iso2=iso2)
            if was_created:
                created += 1
        if created:
            self.stdout.write(self.style.SUCCESS(f"Created {created} cities."))
        else:
            self.stdout.write("All cities already exist.")
