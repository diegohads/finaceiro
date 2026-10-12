from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Transacao",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("tipo", models.CharField(choices=[("receita", "Receita"), ("despesa", "Despesa")], max_length=10)),
                ("descricao", models.CharField(max_length=160)),
                ("valor", models.DecimalField(decimal_places=2, max_digits=12, validators=[django.core.validators.MinValueValidator(0.01)])),
                ("data_vencimento", models.DateField()),
                ("status", models.CharField(choices=[("pendente", "Pendente"), ("concluido", "Concluido")], default="pendente", max_length=10)),
                ("criado_em", models.DateTimeField(auto_now_add=True)),
                ("usuario", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="transacoes", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Transacao",
                "verbose_name_plural": "Transacoes",
                "ordering": ["data_vencimento", "-criado_em"],
            },
        ),
    ]
