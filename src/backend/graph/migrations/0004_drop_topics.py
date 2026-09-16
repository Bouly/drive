"""Topics are gone: every file is linked to every other one, by weight."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("graph", "0003_item_index"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="itemlink",
            name="surprising",
        ),
        migrations.DeleteModel(
            name="ItemTopic",
        ),
        migrations.DeleteModel(
            name="Topic",
        ),
    ]
