from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import (
    CategoriaDespesa,
    Cliente,
    ConfiguracaoDocumento,
    FormaPagamentoPersonalizada,
    Planejamento,
    Recibo,
    Transacao,
)


class TransacaoForm(forms.ModelForm):
    class Meta:
        model = Transacao
        fields = (
            "tipo",
            "descricao",
            "categoria",
            "valor",
            "data_vencimento",
            "status",
            "forma_pagamento",
            "parcelado",
            "numero_parcelas",
        )
        labels = {
            "parcelado": "Parcelar pagamento",
            "numero_parcelas": "Quantidade de parcelas",
        }
        widgets = {
            "data_vencimento": forms.DateInput(attrs={"type": "date"}),
            "valor": forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "data-currency": "true", "placeholder": "R$ 0,00"}),
            "numero_parcelas": forms.NumberInput(attrs={"min": "1", "max": "120"}),
        }

    def __init__(self, *args, **kwargs):
        self.usuario = kwargs.pop("usuario", None)
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.initial["data_vencimento"] = date.today()
        categorias = CategoriaDespesa.objects.filter(ativa=True)
        if self.usuario:
            categorias = categorias.filter(usuario=self.usuario)
        self.fields["categoria"].queryset = categorias
        self.fields["categoria"].required = False
        self.fields["categoria"].empty_label = "Sem categoria"
        formas = [(choice.value, choice.label) for choice in Transacao.FormaPagamento]
        if self.usuario:
            formas += list(FormaPagamentoPersonalizada.objects.filter(usuario=self.usuario, ativa=True).values_list("nome", "nome"))
        self.fields["forma_pagamento"].widget = forms.Select(choices=formas)

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("tipo") == Transacao.Tipo.RECEITA:
            cleaned_data["categoria"] = None
        forma_pagamento = cleaned_data.get("forma_pagamento")
        parcelado = cleaned_data.get("parcelado")
        numero_parcelas = cleaned_data.get("numero_parcelas")

        formas_padrao = {choice.value for choice in Transacao.FormaPagamento}
        if (
            forma_pagamento
            and forma_pagamento not in formas_padrao
            and (
                not self.usuario
                or not FormaPagamentoPersonalizada.objects.filter(
                    usuario=self.usuario,
                    nome=forma_pagamento,
                    ativa=True,
                ).exists()
            )
        ):
            self.add_error("forma_pagamento", "Selecione uma forma de pagamento valida.")

        if not parcelado:
            cleaned_data["numero_parcelas"] = 1
            cleaned_data["parcelas_pagas"] = 0
        elif numero_parcelas and numero_parcelas < 2:
            self.add_error("numero_parcelas", "Informe pelo menos 2 parcelas.")
        return cleaned_data


class CadastroForm(UserCreationForm):
    email = forms.EmailField(label="E-mail", required=True)

    class Meta:
        model = User
        fields = ("username", "email", "password1", "password2")


class CategoriaDespesaForm(forms.ModelForm):
    class Meta:
        model = CategoriaDespesa
        fields = ("nome",)


class FormaPagamentoForm(forms.ModelForm):
    class Meta:
        model = FormaPagamentoPersonalizada
        fields = ("nome",)


class PlanejamentoForm(forms.ModelForm):
    class Meta:
        model = Planejamento
        fields = ("objeto", "conta", "valor_meta", "valor_guardado")
        labels = {
            "objeto": "O que voce quer comprar?",
            "conta": "Onde o dinheiro esta guardado?",
            "valor_meta": "Valor da meta",
            "valor_guardado": "Quanto ja guardou",
        }
        widgets = {
            "valor_meta": forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "data-currency": "true"}),
            "valor_guardado": forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "data-currency": "true"}),
        }


class AdicionarGuardadoForm(forms.Form):
    valor = forms.DecimalField(
        label="Quanto voce guardou agora?",
        min_value=Decimal("0.01"),
        max_digits=12,
        decimal_places=2,
        widget=forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "data-currency": "true", "placeholder": "R$ 0,00"}),
    )


class ReciboForm(forms.ModelForm):
    cliente = forms.ModelChoiceField(queryset=Cliente.objects.none(), required=False, empty_label="Selecionar cliente cadastrado")

    class Meta:
        model = Recibo
        fields = ("cliente", "recebedor", "servico", "valor", "data_pagamento", "forma_pagamento", "observacoes")
        widgets = {
            "valor": forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "data-currency": "true", "placeholder": "R$ 0,00"}),
            "data_pagamento": forms.DateInput(attrs={"type": "date"}),
            "observacoes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.usuario = kwargs.pop("usuario", None)
        super().__init__(*args, **kwargs)
        self.fields["recebedor"].required = False
        if self.usuario:
            self.fields["cliente"].queryset = Cliente.objects.filter(usuario=self.usuario)
        if not self.instance.pk:
            self.initial["data_pagamento"] = date.today()

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get("cliente")
        if cliente:
            if self.usuario and cliente.usuario_id != self.usuario.pk:
                self.add_error("cliente", "Selecione um cliente valido.")
            cleaned_data["recebedor"] = cliente.nome_fantasia or cliente.razao_social
        elif not cleaned_data.get("recebedor"):
            self.add_error("recebedor", "Informe o recebedor ou selecione um cliente.")
        return cleaned_data


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = (
            "cnpj",
            "razao_social",
            "nome_fantasia",
            "logradouro",
            "numero",
            "complemento",
            "bairro",
            "municipio",
            "uf",
            "cep",
            "email",
            "telefone",
        )
        widgets = {
            "cnpj": forms.TextInput(attrs={"inputmode": "numeric", "autocomplete": "off", "data-cnpj": "true", "placeholder": "00.000.000/0000-00"}),
        }

    def clean_cnpj(self):
        cnpj = "".join(char for char in self.cleaned_data["cnpj"] if char.isdigit())
        if len(cnpj) != 14:
            raise forms.ValidationError("Informe um CNPJ valido com 14 digitos.")
        return cnpj


class ConfiguracaoDocumentoForm(forms.ModelForm):
    class Meta:
        model = ConfiguracaoDocumento
        fields = (
            "nome_empresa",
            "titulo_relatorio",
            "titulo_recibo",
            "logo",
            "cor_principal",
            "rodape",
            "mostrar_forma_pagamento",
            "mostrar_observacoes",
        )
        widgets = {
            "cor_principal": forms.TextInput(attrs={"type": "color"}),
            "rodape": forms.Textarea(attrs={"rows": 2}),
        }
