from calendar import monthrange
from datetime import date

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class CategoriaDespesa(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="categorias_despesa",
    )
    nome = models.CharField(max_length=80)
    ativa = models.BooleanField(default=True)
    criada_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nome"]
        unique_together = (("usuario", "nome"),)
        verbose_name = "Categoria de despesa"
        verbose_name_plural = "Categorias de despesas"

    def __str__(self):
        return self.nome


class FormaPagamentoPersonalizada(models.Model):
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="formas_pagamento")
    nome = models.CharField(max_length=40)
    ativa = models.BooleanField(default=True)

    class Meta:
        ordering = ["nome"]
        unique_together = ("usuario", "nome")
        verbose_name = "Forma de pagamento personalizada"
        verbose_name_plural = "Formas de pagamento personalizadas"

    def __str__(self):
        return self.nome


class Planejamento(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="planejamentos",
    )
    objeto = models.CharField(max_length=120)
    conta = models.CharField(max_length=120)
    valor_meta = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    valor_guardado = models.DecimalField(max_digits=12, decimal_places=2, default=0, validators=[MinValueValidator(0)])
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-criado_em"]
        verbose_name = "Planejamento"
        verbose_name_plural = "Planejamentos"

    def __str__(self):
        return self.objeto

    @property
    def valor_restante(self):
        return max(self.valor_meta - self.valor_guardado, 0)

    @property
    def percentual(self):
        return min((self.valor_guardado / self.valor_meta) * 100, 100)


class Transacao(models.Model):
    class Tipo(models.TextChoices):
        RECEITA = "receita", "Receita"
        DESPESA = "despesa", "Despesa"

    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        CONCLUIDO = "concluido", "Concluido"

    class FormaPagamento(models.TextChoices):
        PIX = "pix", "Pix"
        CARTAO = "cartao", "Cartao"
        BOLETO = "boleto", "Boleto"
        DINHEIRO = "dinheiro", "Dinheiro"
        OUTRO = "outro", "Outro"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="transacoes",
    )
    tipo = models.CharField(max_length=10, choices=Tipo.choices)
    descricao = models.CharField(max_length=160)
    categoria = models.ForeignKey(
        CategoriaDespesa,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="transacoes",
    )
    valor = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    data_vencimento = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    forma_pagamento = models.CharField(
        max_length=40,
        default=FormaPagamento.OUTRO,
    )
    parcelado = models.BooleanField(default=False)
    numero_parcelas = models.PositiveSmallIntegerField(default=1)
    parcelas_pagas = models.PositiveSmallIntegerField(default=0)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["data_vencimento", "-criado_em"]
        verbose_name = "Transacao"
        verbose_name_plural = "Transacoes"

    def save(self, *args, **kwargs):
        if not self.parcelado:
            self.numero_parcelas = 1
        if self.status == self.Status.CONCLUIDO:
            self.parcelas_pagas = self.numero_parcelas
        elif self.parcelas_pagas > self.numero_parcelas:
            self.parcelas_pagas = self.numero_parcelas
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_tipo_display()} - {self.descricao}"

    @property
    def valor_parcela(self):
        return self.valor / self.numero_parcelas

    @property
    def valor_pago(self):
        return self.valor_parcela * self.parcelas_pagas

    @property
    def parcelas_restantes(self):
        return max(self.numero_parcelas - self.parcelas_pagas, 0)

    def registrar_pagamento(self):
        if self.status == self.Status.CONCLUIDO:
            return
        self.parcelas_pagas = min(self.parcelas_pagas + 1, self.numero_parcelas)
        if self.parcelas_pagas >= self.numero_parcelas:
            self.status = self.Status.CONCLUIDO
        elif self.parcelado:
            hoje = date.today()
            proximo_mes = hoje.month % 12 + 1
            proximo_ano = hoje.year + (1 if hoje.month == 12 else 0)
            dia = min(hoje.day, monthrange(proximo_ano, proximo_mes)[1])
            self.data_vencimento = date(proximo_ano, proximo_mes, dia)
        self.save(update_fields=("parcelas_pagas", "status", "data_vencimento"))


class PermissaoRecibo(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="permissao_recibo",
    )
    pode_emitir = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Permissao de recibo"
        verbose_name_plural = "Permissoes de recibo"

    def __str__(self):
        return self.usuario.get_username()


class ConfiguracaoDocumento(models.Model):
    usuario = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="configuracao_documento",
    )
    nome_empresa = models.CharField(max_length=120, blank=True)
    titulo_relatorio = models.CharField(max_length=120, default="Relatorio financeiro")
    titulo_recibo = models.CharField(max_length=120, default="Recibo de pagamento")
    logo = models.FileField(upload_to="documentos/logos/", blank=True, null=True)
    cor_principal = models.CharField(max_length=7, default="#1f6b4f")
    rodape = models.CharField(max_length=240, blank=True)
    mostrar_forma_pagamento = models.BooleanField(default=True)
    mostrar_observacoes = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Configuracao de documentos"
        verbose_name_plural = "Configuracoes de documentos"

    def __str__(self):
        return self.nome_empresa or f"Documentos de {self.usuario.get_username()}"


class Cliente(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="clientes",
    )
    cnpj = models.CharField(max_length=18)
    razao_social = models.CharField(max_length=180)
    nome_fantasia = models.CharField(max_length=180, blank=True)
    logradouro = models.CharField(max_length=180, blank=True)
    numero = models.CharField(max_length=20, blank=True)
    complemento = models.CharField(max_length=80, blank=True)
    bairro = models.CharField(max_length=100, blank=True)
    municipio = models.CharField(max_length=100, blank=True)
    uf = models.CharField(max_length=2, blank=True)
    cep = models.CharField(max_length=10, blank=True)
    email = models.EmailField(blank=True)
    telefone = models.CharField(max_length=30, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["razao_social"]
        constraints = [
            models.UniqueConstraint(fields=("usuario", "cnpj"), name="cliente_usuario_cnpj_unico"),
        ]
        verbose_name = "Cliente"
        verbose_name_plural = "Clientes"

    def __str__(self):
        return self.nome_fantasia or self.razao_social

    @property
    def endereco_completo(self):
        partes = [self.logradouro, self.numero, self.complemento, self.bairro]
        cidade = " - ".join(part for part in (self.municipio, self.uf) if part)
        if cidade:
            partes.append(cidade)
        if self.cep:
            partes.append(f"CEP {self.cep}")
        return ", ".join(part for part in partes if part)


class Recibo(models.Model):
    class Status(models.TextChoices):
        PENDENTE = "pendente", "Pendente"
        RECEBIDO = "recebido", "Recebido"

    criado_por = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recibos_emitidos",
    )
    transacao = models.OneToOneField(
        Transacao,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recibo_origem",
    )
    cliente = models.ForeignKey(
        Cliente,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recibos",
    )
    recebedor = models.CharField(max_length=160)
    servico = models.CharField(max_length=180)
    valor = models.DecimalField(max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    data_pagamento = models.DateField()
    forma_pagamento = models.CharField(max_length=80, blank=True)
    observacoes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDENTE)
    recebido_em = models.DateTimeField(null=True, blank=True)
    criado_em = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-data_pagamento", "-criado_em"]
        verbose_name = "Recibo"
        verbose_name_plural = "Recibos"

    def __str__(self):
        return f"{self.recebedor} - R$ {self.valor:.2f}"
