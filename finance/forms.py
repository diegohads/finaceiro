from datetime import date
from decimal import Decimal

from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from PIL import Image, UnidentifiedImageError

from .models import (
    CategoriaDespesa,
    Cliente,
    ConfiguracaoDocumento,
    ContaFinanceira,
    FormaPagamentoPersonalizada,
    Planejamento,
    Recibo,
    TransferenciaFinanceira,
    Transacao,
)


def cpf_validado(valor):
    cpf = "".join(char for char in valor if char.isdigit())
    if not cpf:
        return ""
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        raise forms.ValidationError("Informe um CPF valido com 11 digitos.")
    digitos = [int(digito) for digito in cpf]
    for tamanho in (9, 10):
        soma = sum(digitos[indice] * (tamanho + 1 - indice) for indice in range(tamanho))
        resto = (soma * 10) % 11
        digito_verificador = 0 if resto == 10 else resto
        if digitos[tamanho] != digito_verificador:
            raise forms.ValidationError("Informe um CPF valido.")
    return cpf


class TransacaoForm(forms.ModelForm):
    conta_origem = forms.ModelChoiceField(
        queryset=ContaFinanceira.objects.none(),
        required=False,
        label="Conta financeira",
        empty_label="Selecionar ao dar baixa",
    )

    class Meta:
        model = Transacao
        fields = (
            "tipo",
            "descricao",
            "categoria",
            "conta_origem",
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
        tipo = self.data.get("tipo") if self.is_bound else self.instance.tipo
        if tipo not in Transacao.Tipo.values:
            tipo = Transacao.Tipo.DESPESA
        self.fields["tipo"].initial = tipo
        categorias = categorias.filter(tipo=CategoriaDespesa.Tipo.RECEITA)
        if self.usuario:
            categorias = categorias.filter(Q(usuario=self.usuario) | Q(usuario__isnull=True))
        self.fields["categoria"].queryset = categorias
        self.fields["categoria"].required = tipo == Transacao.Tipo.RECEITA
        self.fields["categoria"].label = "Tipo de receita"
        self.fields["categoria"].empty_label = "Selecione um tipo de receita"
        if self.usuario:
            self.fields["conta_origem"].queryset = ContaFinanceira.objects.filter(usuario=self.usuario)
        if self.instance.pk and self.instance.conta_origem_id:
            self.initial["conta_origem"] = self.instance.conta_origem_id
        formas = [(choice.value, choice.label) for choice in Transacao.FormaPagamento]
        if self.usuario:
            formas += list(FormaPagamentoPersonalizada.objects.filter(usuario=self.usuario, ativa=True).values_list("nome", "nome"))
        self.fields["forma_pagamento"].widget = forms.Select(choices=formas)

    def clean(self):
        cleaned_data = super().clean()
        tipo = cleaned_data.get("tipo")
        categoria = cleaned_data.get("categoria")
        if categoria and categoria.tipo != tipo:
            self.add_error("categoria", "Selecione uma opcao compativel com o tipo de lancamento.")
        conta_origem = cleaned_data.get("conta_origem")
        if (
            conta_origem
            and self.usuario
            and conta_origem.usuario_id != self.usuario.pk
        ):
            self.add_error("conta_origem", "Selecione uma conta valida.")
        if (
            tipo == Transacao.Tipo.DESPESA
            and categoria
        ):
            cleaned_data["categoria"] = None
        if (
            cleaned_data.get("status") == Transacao.Status.CONCLUIDO
            and not conta_origem
        ):
            self.add_error("conta_origem", "Selecione a conta financeira desta movimentacao.")
        valor = cleaned_data.get("valor")
        if (
            tipo == Transacao.Tipo.DESPESA
            and conta_origem
            and valor is not None
        ):
            saldo_disponivel = conta_origem.saldo
            if (
                self.instance.pk
                and self.instance.conta_origem_id == conta_origem.pk
            ):
                saldo_disponivel += self.instance.valor_pago
            valor_a_validar = valor
            if (
                cleaned_data.get("status") == Transacao.Status.CONCLUIDO
                and self.instance.pk
                and self.instance.parcelas_pagas
                and self.instance.conta_origem_id == conta_origem.pk
            ):
                valor_a_validar = max(valor - self.instance.valor_pago, Decimal("0.00"))
            if valor_a_validar > saldo_disponivel:
                self.add_error(
                    "conta_origem",
                    f"Saldo insuficiente. A conta tem R$ {saldo_disponivel:.2f}, "
                    f"mas esta despesa requer R$ {valor_a_validar:.2f}.",
                )
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


class SubUsuarioForm(UserCreationForm):
    first_name = forms.CharField(label="Nome", max_length=150)
    last_name = forms.CharField(label="Sobrenome", max_length=150, required=False)
    email = forms.EmailField(label="E-mail", required=False)

    class Meta:
        model = User
        fields = ("first_name", "last_name", "username", "email", "password1", "password2")


class CategoriaDespesaForm(forms.ModelForm):
    class Meta:
        model = CategoriaDespesa
        fields = ("nome",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def save(self, commit=True):
        categoria = super().save(commit=False)
        categoria.tipo = CategoriaDespesa.Tipo.RECEITA
        if commit:
            categoria.save()
            self.save_m2m()
        return categoria


class ContaFinanceiraForm(forms.ModelForm):
    class Meta:
        model = ContaFinanceira
        fields = ("nome",)
        labels = {"nome": "Nome da conta"}


class TransferenciaFinanceiraForm(forms.ModelForm):
    class Meta:
        model = TransferenciaFinanceira
        fields = ("conta_origem", "conta_destino", "valor")
        labels = {
            "conta_origem": "Conta de origem",
            "conta_destino": "Conta de destino",
            "valor": "Valor da transferencia",
        }
        widgets = {
            "valor": forms.TextInput(
                attrs={
                    "inputmode": "decimal",
                    "autocomplete": "off",
                    "data-currency": "true",
                    "placeholder": "R$ 0,00",
                }
            ),
        }

    def __init__(self, *args, usuario=None, **kwargs):
        self.usuario = usuario
        super().__init__(*args, **kwargs)
        if usuario:
            contas = ContaFinanceira.objects.filter(usuario=usuario)
            self.fields["conta_origem"].queryset = contas
            self.fields["conta_destino"].queryset = contas

    def clean(self):
        cleaned_data = super().clean()
        origem = cleaned_data.get("conta_origem")
        destino = cleaned_data.get("conta_destino")
        valor = cleaned_data.get("valor")
        if origem and destino and origem.pk == destino.pk:
            self.add_error("conta_destino", "A conta de origem e destino devem ser diferentes.")
        if origem and valor and origem.saldo < valor:
            self.add_error("valor", "O saldo da conta de origem e insuficiente.")
        return cleaned_data


class BaixaDespesaForm(forms.Form):
    conta_origem = forms.ModelChoiceField(
        queryset=ContaFinanceira.objects.none(),
        empty_label="Selecione uma conta",
    )

    def __init__(self, *args, usuario=None, transacao=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["conta_origem"].label = (
            "Em qual conta entrou o dinheiro?"
            if transacao and transacao.tipo == Transacao.Tipo.RECEITA
            else "De qual conta saiu o dinheiro?"
        )
        if usuario:
            contas = ContaFinanceira.objects.filter(usuario=usuario)
            if transacao and transacao.parcelas_pagas and transacao.conta_origem_id:
                contas = contas.filter(pk=transacao.conta_origem_id)
            self.fields["conta_origem"].queryset = contas
        if transacao and transacao.conta_origem_id:
            self.initial["conta_origem"] = transacao.conta_origem_id
        self.transacao = transacao

    def clean_conta_origem(self):
        conta = self.cleaned_data["conta_origem"]
        transacao = self.transacao
        if (
            transacao
            and transacao.tipo == Transacao.Tipo.DESPESA
            and conta.saldo < transacao.valor_parcela
        ):
            raise forms.ValidationError(
                f"Saldo insuficiente. A conta tem R$ {conta.saldo:.2f}, "
                f"mas esta baixa requer R$ {transacao.valor_parcela:.2f}."
            )
        return conta


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
    cpf = forms.CharField(
        label="CPF do pagador (opcional)",
        required=False,
        max_length=14,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "000.000.000-00",
            }
        ),
    )

    class Meta:
        model = Recibo
        fields = ("cliente", "recebedor", "cpf", "servico", "valor", "data_pagamento", "forma_pagamento", "observacoes")
        widgets = {
            "valor": forms.TextInput(attrs={"inputmode": "decimal", "autocomplete": "off", "data-currency": "true", "placeholder": "R$ 0,00"}),
            "data_pagamento": forms.DateInput(attrs={"type": "date"}),
            "observacoes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        self.usuario = kwargs.pop("usuario", None)
        super().__init__(*args, **kwargs)
        self.fields["recebedor"].required = False
        self.fields["cpf"].initial = self.instance.cpf or ""
        if self.usuario:
            self.fields["cliente"].queryset = Cliente.objects.filter(usuario=self.usuario)
        if not self.instance.pk:
            self.initial["data_pagamento"] = date.today()

    def clean_cpf(self):
        return cpf_validado(self.cleaned_data.get("cpf", ""))

    def clean(self):
        cleaned_data = super().clean()
        cliente = cleaned_data.get("cliente")
        if cliente:
            if self.usuario and cliente.usuario_id != self.usuario.pk:
                self.add_error("cliente", "Selecione um cliente valido.")
            cleaned_data["recebedor"] = cliente.nome_fantasia or cliente.razao_social
            cleaned_data["cpf"] = ""
        elif not cleaned_data.get("recebedor"):
            self.add_error("recebedor", "Informe o recebedor ou selecione um cliente.")
        return cleaned_data


class ClienteForm(forms.ModelForm):
    cpf = forms.CharField(
        label="CPF",
        required=False,
        max_length=14,
        widget=forms.TextInput(
            attrs={
                "inputmode": "numeric",
                "autocomplete": "off",
                "placeholder": "000.000.000-00",
                "data-cpf": "true",
            }
        ),
    )

    class Meta:
        model = Cliente
        fields = (
            "cnpj",
            "cpf",
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["razao_social"].label = "Nome / razao social"
        self.fields["razao_social"].help_text = "Para pessoa fisica, informe o nome completo."
        self.fields["cnpj"].required = False
        self.fields["cnpj"].widget.attrs["placeholder"] = "00.000.000/0000-00 (empresa)"
        self.fields["cpf"].initial = self.instance.cpf or ""

    def clean_cnpj(self):
        cnpj = "".join(char for char in self.cleaned_data.get("cnpj", "") if char.isdigit())
        if cnpj and len(cnpj) != 14:
            raise forms.ValidationError("Informe um CNPJ valido com 14 digitos.")
        return cnpj

    def clean_cpf(self):
        return cpf_validado(self.cleaned_data.get("cpf", ""))

    def clean(self):
        cleaned_data = super().clean()
        cnpj = cleaned_data.get("cnpj", "")
        cpf = cleaned_data.get("cpf", "")
        if not cnpj and not cpf:
            raise forms.ValidationError("Informe um CNPJ ou um CPF.")
        if cnpj and cpf:
            raise forms.ValidationError("Informe somente CNPJ para empresa ou CPF para pessoa fisica.")
        return cleaned_data


class ConfiguracaoDocumentoForm(forms.ModelForm):
    TAMANHO_MAXIMO_LOGO = 2 * 1024 * 1024
    DIMENSOES_MAXIMAS_LOGO = {
        "logo_menu": (500, 500),
        "logo_relatorio": (120, 100),
        "logo_recibo": (200, 200),
    }

    class Meta:
        model = ConfiguracaoDocumento
        fields = (
            "nome_empresa",
            "titulo_relatorio",
            "titulo_recibo",
            "logo_menu",
            "logo_relatorio",
            "logo_recibo",
            "cor_principal",
            "rodape",
            "mostrar_forma_pagamento",
            "mostrar_observacoes",
        )
        widgets = {
            "cor_principal": forms.TextInput(attrs={"type": "color"}),
            "rodape": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        limites = {
            "logo_menu": "500 x 500",
            "logo_relatorio": "120 x 100",
            "logo_recibo": "200 x 200",
        }
        for nome, dimensoes in limites.items():
            self.fields[nome].label = {
                "logo_menu": "Logo do menu",
                "logo_relatorio": "Logo do relatorio",
                "logo_recibo": "Logo do recibo",
            }[nome]
            self.fields[nome].widget.attrs["accept"] = ".png,.jpg,.jpeg,image/png,image/jpeg"
            self.fields[nome].help_text = (
                f"PNG ou JPG, ate 2 MB e com no maximo {dimensoes} pixels."
            )

    def _validar_logo(self, campo):
        logo = self.cleaned_data.get(campo)
        if not logo or getattr(logo, "_committed", False):
            return logo

        extensao = logo.name.rsplit(".", 1)[-1].lower() if "." in logo.name else ""
        if extensao not in {"png", "jpg", "jpeg"}:
            raise ValidationError("Envie a logo nos formatos PNG ou JPG.")
        if logo.size > self.TAMANHO_MAXIMO_LOGO:
            raise ValidationError("A logo deve ter no maximo 2 MB.")

        try:
            logo.seek(0)
            with Image.open(logo) as imagem:
                formato = imagem.format
                dimensoes = imagem.size
                imagem.verify()
        except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError) as erro:
            raise ValidationError("O arquivo precisa ser uma imagem PNG ou JPG valida.") from erro
        finally:
            logo.seek(0)

        extensoes_por_formato = {"PNG": {"png"}, "JPEG": {"jpg", "jpeg"}}
        if extensao not in extensoes_por_formato.get(formato, set()):
            raise ValidationError("A extensao do arquivo nao corresponde ao formato da imagem.")
        largura_maxima, altura_maxima = self.DIMENSOES_MAXIMAS_LOGO[campo]
        if dimensoes[0] > largura_maxima or dimensoes[1] > altura_maxima:
            raise ValidationError(
                f"A logo deve ter no maximo {largura_maxima} x {altura_maxima} pixels."
            )
        return logo

    def clean_logo_menu(self):
        return self._validar_logo("logo_menu")

    def clean_logo_relatorio(self):
        return self._validar_logo("logo_relatorio")

    def clean_logo_recibo(self):
        return self._validar_logo("logo_recibo")
