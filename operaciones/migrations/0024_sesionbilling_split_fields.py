import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("operaciones", "0023_adjustmententry"),
    ]

    operations = [
        migrations.AddField(
            model_name="sesionbilling",
            name="is_split_child",
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name="sesionbilling",
            name="split_comment",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="sesionbilling",
            name="split_from",
            field=models.ForeignKey(
                related_name="split_children",
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to="operaciones.sesionbilling",
                help_text="If set, this billing session was created by splitting from the referenced session.",
            ),
        ),
        migrations.AddIndex(
            model_name="sesionbilling",
            index=models.Index(fields=["is_split_child"], name="operacione_is_spli_idx"),
        ),
    ]