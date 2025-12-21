import random
import typing as t

from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker

from leads.models import Action, City, Lead, LeadType, Tag

NUM_LEADS = 50


class Command(BaseCommand):
    """Seed the database with fake leads for local development.

    WARNING: This command creates fake data and should NOT be run in production.
    Use 'bootstrap' command for production-safe initialization.
    """

    help = "Seed the database with fake leads for local development (NOT for production)."

    def handle(self, *args: t.Any, **options: t.Any) -> None:
        """Handle the command."""
        self.faker = Faker()
        Faker.seed(42)
        random.seed(42)

        with transaction.atomic():
            self._create_leads()

        self.stdout.write(self.style.SUCCESS("Seed complete!"))

    def _create_leads(self) -> None:
        cities = list(City.objects.all())
        lead_types = list(LeadType.objects.all())
        tags = list(Tag.objects.all())

        if not lead_types:
            self.stdout.write(self.style.WARNING("No lead types found. Run migrations first."))
            return

        if not cities:
            self.stdout.write(self.style.WARNING("No cities found. Run 'bootstrap' command first."))
            return

        created = 0
        for _ in range(NUM_LEADS):
            lead = Lead.objects.create(
                name=self.faker.company(),
                email=self.faker.email(),
                phone=self.faker.phone_number()[:50],
                company=self.faker.company() if random.random() > 0.3 else "",
                lead_type=random.choice(lead_types),
                city=random.choice(cities) if cities else None,
                telegram=f"@{self.faker.user_name()}" if random.random() > 0.5 else "",
                instagram=f"@{self.faker.user_name()}" if random.random() > 0.5 else "",
                website=self.faker.url() if random.random() > 0.5 else "",
                source=random.choice(["Instagram", "Referral", "Cold outreach", "Event", "Website", ""]),
                status=random.choice([s[0] for s in Lead.Status.choices]),
                temperature=random.choice([t[0] for t in Lead.Temperature.choices]),
                last_contact=self.faker.date_between(start_date="-90d", end_date="today")
                if random.random() > 0.3
                else None,
                notes=self.faker.paragraph() if random.random() > 0.5 else "",
                value=random.randint(100, 10000) if random.random() > 0.6 else None,
            )
            lead.tags.set(random.sample(tags, k=random.randint(0, min(3, len(tags)))))

            # Create actions for some leads
            if random.random() > 0.4:
                action_name = random.choice(["Follow up", "Send proposal", "Schedule call", "Demo"])
                Action.objects.create(
                    lead=lead,
                    name=action_name,
                    status=random.choice([Action.Status.PENDING, Action.Status.IN_PROGRESS]),
                    due_date=self.faker.date_between(start_date="today", end_date="+30d")
                    if random.random() > 0.3
                    else None,
                )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Created {created} fake leads."))
