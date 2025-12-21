from django.db import migrations


def create_poll_research_task(apps, schema_editor):
    IntervalSchedule = apps.get_model("django_celery_beat", "IntervalSchedule")
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")

    schedule, _ = IntervalSchedule.objects.get_or_create(every=1, period="minutes")

    PeriodicTask.objects.update_or_create(
        name="Poll research jobs",
        defaults={
            "task": "leads.tasks.poll_research_jobs",
            "interval": schedule,
            "enabled": True,
        },
    )


def delete_poll_research_task(apps, schema_editor):
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.filter(name="Poll research jobs").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("leads", "0005_researchpromptconfig_researchjob"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [
        migrations.RunPython(create_poll_research_task, reverse_code=delete_poll_research_task),
    ]
