from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0013_logos_por_area"),
    ]

    operations = [
        migrations.AddField(
            model_name="recibo",
            name="cpf",
            field=models.CharField(blank=True, max_length=11),
        ),
    ]
