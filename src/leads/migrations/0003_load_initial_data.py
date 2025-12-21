from django.db import migrations


def load_initial_data(apps, schema_editor):
    LeadType = apps.get_model("leads", "LeadType")
    Tag = apps.get_model("leads", "Tag")

    lead_types = ["Organization", "Collective", "Venue", "Theater", "Club", "Festival", "Individual", "Other"]
    for name in lead_types:
        LeadType.objects.get_or_create(name=name)

    tags = [
        "LGBTQ+",
        "Queer",
        "Fetish",
        "Kink",
        "Sex-positive",
        "Techno",
        "House",
        "Art",
        "Performance",
        "Theater",
        "Dance",
        "Outdoor",
        "Daytime",
        "Nightlife",
        "Community",
        "Private",
        "Members-only",
    ]
    for name in tags:
        Tag.objects.get_or_create(name=name)


def reverse(apps, schema_editor):
    LeadType = apps.get_model("leads", "LeadType")
    Tag = apps.get_model("leads", "Tag")
    LeadType.objects.all().delete()
    Tag.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("leads", "0002_leadtype_tag_historicallead_last_contact_and_more"),
    ]

    operations = [
        migrations.RunPython(load_initial_data, reverse),
    ]
