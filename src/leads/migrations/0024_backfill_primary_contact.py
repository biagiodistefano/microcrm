"""Backfill one primary Contact per existing Lead from Lead.{email,phone,telegram,instagram,website}."""

from django.db import migrations


def backfill_primary_contacts(apps, schema_editor):
    """Create one primary Contact per Lead, copying the five contact fields."""
    Lead = apps.get_model("leads", "Lead")
    Contact = apps.get_model("leads", "Contact")

    to_create = []
    for lead in Lead.objects.all().iterator():
        if Contact.objects.filter(lead=lead).exists():
            continue
        to_create.append(
            Contact(
                lead=lead,
                name="Primary",
                is_primary=True,
                email=lead.email or "",
                phone=lead.phone or "",
                telegram=lead.telegram or "",
                instagram=lead.instagram or "",
                website=lead.website or "",
            )
        )
        if len(to_create) >= 500:
            Contact.objects.bulk_create(to_create)
            to_create = []
    if to_create:
        Contact.objects.bulk_create(to_create)


def delete_all_contacts(apps, schema_editor):
    """Reverse: remove all backfilled primary Contacts."""
    Contact = apps.get_model("leads", "Contact")
    Contact.objects.filter(is_primary=True, name="Primary").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("leads", "0023_contact"),
    ]

    operations = [
        migrations.RunPython(backfill_primary_contacts, delete_all_contacts),
    ]
