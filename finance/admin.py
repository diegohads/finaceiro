from django.contrib import admin

from .models import (
    CategoriaDespesa,
    Cliente,
    ConfiguracaoDocumento,
    FormaPagamentoPersonalizada,
    PermissaoRecibo,
    Planejamento,
    Recibo,
    Transacao,
)


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("razao_social", "nome_fantasia", "cnpj", "usuario", "municipio", "uf")
    search_fields = ("razao_social", "nome_fantasia", "cnpj", "usuario__username")
    autocomplete_fields = ("usuario",)


@admin.register(PermissaoRecibo)
class PermissaoReciboAdmin(admin.ModelAdmin):
    list_display = ("usuario", "pode_emitir")
    list_filter = ("pode_emitir",)
    search_fields = ("usuario__username", "usuario__email")
    autocomplete_fields = ("usuario",)


@admin.register(ConfiguracaoDocumento)
class ConfiguracaoDocumentoAdmin(admin.ModelAdmin):
    list_display = ("usuario", "nome_empresa", "titulo_relatorio", "titulo_recibo")
    search_fields = ("usuario__username", "nome_empresa")
    autocomplete_fields = ("usuario",)


@admin.register(Recibo)
class ReciboAdmin(admin.ModelAdmin):
    list_display = ("recebedor", "servico", "valor", "data_pagamento", "status", "criado_por")
    list_filter = ("data_pagamento", "status")
    search_fields = ("recebedor", "servico", "criado_por__username")
    autocomplete_fields = ("criado_por", "cliente")


@admin.register(CategoriaDespesa)
class CategoriaDespesaAdmin(admin.ModelAdmin):
    list_display = ("nome", "usuario", "ativa", "criada_em")
    list_filter = ("ativa",)
    search_fields = ("nome",)
    ordering = ("nome",)


@admin.register(FormaPagamentoPersonalizada)
class FormaPagamentoPersonalizadaAdmin(admin.ModelAdmin):
    list_display = ("nome", "usuario", "ativa")
    list_filter = ("ativa",)
    search_fields = ("nome", "usuario__username")
    autocomplete_fields = ("usuario",)


@admin.register(Planejamento)
class PlanejamentoAdmin(admin.ModelAdmin):
    list_display = ("objeto", "usuario", "conta", "valor_guardado", "valor_meta")
    search_fields = ("objeto", "conta", "usuario__username")
    autocomplete_fields = ("usuario",)


@admin.register(Transacao)
class TransacaoAdmin(admin.ModelAdmin):
    list_display = (
        "descricao",
        "usuario",
        "tipo",
        "categoria",
        "valor",
        "data_vencimento",
        "status",
        "forma_pagamento",
        "parcelamento",
        "parcelas_pagas",
    )
    list_filter = ("tipo", "categoria", "status", "forma_pagamento", "parcelado", "data_vencimento")
    search_fields = ("descricao", "usuario__username", "usuario__email", "categoria__nome")
    date_hierarchy = "data_vencimento"
    autocomplete_fields = ("usuario",)

    @admin.display(description="Parcelamento")
    def parcelamento(self, obj):
        return f"{obj.numero_parcelas}x" if obj.parcelado else "A vista"
