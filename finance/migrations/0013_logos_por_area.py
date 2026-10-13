from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0012_cliente_recibo_cliente_and_more"),
    ]

    operations = [
        migrations.RenameField(
            model_name="configuracaodocumento",
            old_name="logo",
            new_name="logo_recibo",
        ),
        migrations.AddField(
            model_name="configuracaodocumento",
            name="logo_menu",
            field=models.FileField(blank=True, null=True, upload_to="documentos/logos/"),
        ),
        migrations.AddField(
            model_name="configuracaodocumento",
            name="logo_relatorio",
            field=models.FileField(blank=True, null=True, upload_to="documentos/logos/"),
        ),
    ]
