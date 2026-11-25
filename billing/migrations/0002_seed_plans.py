"""Example credit packs. Change names and prices any time in Admin → Plans."""
from django.db import migrations

EXAMPLE_PLANS = [
    # name, minutes, price (BDT), highlight, order
    ("Starter", 300, 3000, False, 1),
    ("Project", 1200, 10800, True, 2),
    ("Study", 3000, 24000, False, 3),
]


def seed(apps, schema_editor):
    Plan = apps.get_model("billing", "Plan")
    if Plan.objects.exists():
        return
    for name, minutes, price, highlight, order in EXAMPLE_PLANS:
        Plan.objects.create(name=name, minutes=minutes, price_bdt=price, validity_days=30,
                            highlight=highlight, sort_order=order)


class Migration(migrations.Migration):
    dependencies = [("billing", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
