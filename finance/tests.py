from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import tempfile

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.test.utils import override_settings
from django.urls import reverse
from PIL import Image

from .forms import ConfiguracaoDocumentoForm, TransacaoForm
from .models import (
    CategoriaDespesa,
    Cliente,
    ConfiguracaoDocumento,
    ContaFinanceira,
    FormaPagamentoPersonalizada,
    PermissaoRecibo,
    Planejamento,
    Recibo,
    SubUsuario,
    TransferenciaFinanceira,
    Transacao,
)
from .views import (
    recibo_numero_paragrafos,
    recibo_status_legivel,
    recibo_status_paragrafo,
)


class IsolamentoFinanceiroTests(TestCase):
    def setUp(self):
        self.usuario_a = User.objects.create_user(username="ana", password="senha-segura")
        self.usuario_b = User.objects.create_user(username="bruno", password="senha-segura")
        self.conta = ContaFinanceira.objects.create(usuario=self.usuario_a, nome="Conta principal")
        self.transacao_b = Transacao.objects.create(
            usuario=self.usuario_b,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Conta privada",
            valor=Decimal("120.00"),
            data_vencimento=date.today(),
        )

    def test_usuario_precisa_estar_autenticado(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, "/login/?next=/")

    def test_usuario_nao_acessa_transacao_de_outro_usuario(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(reverse("transacao_editar", args=[self.transacao_b.pk]))
        self.assertEqual(response.status_code, 404)

        response = self.client.post(reverse("transacao_excluir", args=[self.transacao_b.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Transacao.objects.filter(pk=self.transacao_b.pk).exists())

    def test_dashboard_exibe_alerta_de_pendencia(self):
        self.client.login(username="ana", password="senha-segura")
        propria = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Recebimento atrasado",
            valor=Decimal("80.00"),
            data_vencimento=date.today() - timedelta(days=1),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, propria.descricao)
        self.assertContains(response, "atrasada")
        self.assertContains(response, "Contas pendentes: atencao")

    def test_lancamento_de_hoje_entra_direto_em_contas_para_baixa(self):
        self.client.login(username="ana", password="senha-segura")
        transacao = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Despesa para baixa hoje",
            valor=Decimal("45.00"),
            data_vencimento=date.today(),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Contas para baixa")
        self.assertContains(response, transacao.descricao)

    def test_despesa_criada_pela_tela_aparece_e_so_desconta_apos_baixa(self):
        self.client.login(username="ana", password="senha-segura")
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Saldo para pagar aluguel",
            valor=Decimal("750.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        response = self.client.post(
            reverse("transacao_nova"),
            {
                "tipo": "despesa",
                "descricao": "Aluguel do escritorio",
                "valor": "750.00",
                "data_vencimento": date.today().isoformat(),
                "status": "pendente",
                "forma_pagamento": "pix",
                "parcelado": "",
                "numero_parcelas": "1",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        despesa = Transacao.objects.get(usuario=self.usuario_a, descricao="Aluguel do escritorio")
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, despesa.descricao)
        self.assertEqual(dashboard.context["total_despesas"], Decimal("0"))

        self.client.post(reverse("transacao_concluir", args=[despesa.pk]), {"conta_origem": self.conta.pk})
        despesa.refresh_from_db()
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(despesa.status, Transacao.Status.CONCLUIDO)
        self.assertEqual(despesa.valor_pago, Decimal("750.00"))
        self.assertEqual(dashboard.context["total_despesas"], Decimal("750.00"))
        self.assertEqual(dashboard.context["saldo"], Decimal("0.00"))

    def test_lancamento_criado_como_concluido_fica_pago(self):
        receita = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Receita recebida",
            valor=Decimal("500.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
        )
        despesa = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Despesa paga",
            valor=Decimal("300.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            forma_pagamento=Transacao.FormaPagamento.CARTAO,
            parcelado=True,
            numero_parcelas=3,
        )
        receita.refresh_from_db()
        despesa.refresh_from_db()
        self.assertEqual(receita.status, Transacao.Status.CONCLUIDO)
        self.assertEqual(receita.parcelas_pagas, 1)
        self.assertEqual(despesa.parcelas_pagas, 3)
        self.assertEqual(despesa.valor_pago, Decimal("300.00"))

    def test_baixa_de_parcela_agenda_proxima_e_remove_atraso(self):
        self.client.login(username="ana", password="senha-segura")
        transacao = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Cartao parcelado em dia",
            valor=Decimal("300.00"),
            data_vencimento=date.today() - timedelta(days=2),
            forma_pagamento=Transacao.FormaPagamento.CARTAO,
            parcelado=True,
            numero_parcelas=3,
        )
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Saldo suficiente para parcela",
            valor=Decimal("300.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        self.client.post(reverse("transacao_concluir", args=[transacao.pk]), {"conta_origem": self.conta.pk})
        transacao.refresh_from_db()
        self.assertEqual(transacao.parcelas_pagas, 1)
        self.assertGreater(transacao.data_vencimento, date.today())
        response = self.client.get(reverse("dashboard"))
        self.assertNotIn(transacao, response.context["alertas"])

    def test_cadastro_cria_usuario_comum_e_autentica(self):
        response = self.client.post(
            reverse("cadastro"),
            {
                "username": "carla",
                "email": "carla@example.com",
                "password1": "UmaSenhaForte123!",
                "password2": "UmaSenhaForte123!",
            },
        )
        self.assertRedirects(response, reverse("dashboard"))
        usuario = User.objects.get(username="carla")
        self.assertFalse(usuario.is_staff)
        self.assertFalse(usuario.is_superuser)
        self.assertTrue(usuario.is_authenticated)

    def test_usuario_comum_nao_acessa_admin(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get("/admin/")
        self.assertRedirects(response, "/admin/login/?next=/admin/")

    def test_sair_da_conta_volta_para_login_com_cadastro(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))
        login_response = self.client.get(reverse("login"))
        self.assertContains(login_response, "Cadastre-se")
        cadastro_response = self.client.get(reverse("cadastro"))
        self.assertEqual(cadastro_response.status_code, 200)

    def test_parcelamento_aceita_pix_e_exige_duas_parcelas(self):
        dados = {
            "tipo": "despesa",
            "descricao": "Compra parcelada",
            "valor": "300.00",
            "data_vencimento": date.today().isoformat(),
            "status": "pendente",
            "forma_pagamento": "pix",
            "parcelado": "on",
            "numero_parcelas": "1",
        }
        form = TransacaoForm(data=dados)
        self.assertFalse(form.is_valid())
        self.assertNotIn("forma_pagamento", form.errors)
        self.assertIn("numero_parcelas", form.errors)

        dados["numero_parcelas"] = "3"
        form = TransacaoForm(data=dados)
        self.assertTrue(form.is_valid(), form.errors)

    def test_baixa_de_parcelas_debita_apenas_uma_parcela(self):
        self.client.login(username="ana", password="senha-segura")
        transacao = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Compra no cartao",
            valor=Decimal("300.00"),
            data_vencimento=date.today(),
            forma_pagamento=Transacao.FormaPagamento.CARTAO,
            parcelado=True,
            numero_parcelas=3,
        )
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Saldo para debito das parcelas",
            valor=Decimal("300.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        url = reverse("transacao_concluir", args=[transacao.pk])
        self.client.post(url, {"conta_origem": self.conta.pk})
        transacao.refresh_from_db()
        self.assertEqual(transacao.parcelas_pagas, 1)
        self.assertEqual(transacao.valor_pago, Decimal("100.00"))
        self.assertEqual(transacao.status, Transacao.Status.PENDENTE)
        self.client.post(url, {"conta_origem": self.conta.pk})
        self.client.post(url, {"conta_origem": self.conta.pk})
        transacao.refresh_from_db()
        self.assertEqual(transacao.parcelas_pagas, 3)
        self.assertEqual(transacao.status, Transacao.Status.CONCLUIDO)

    def test_parcela_e_exibida_com_duas_casas_decimais(self):
        self.client.login(username="ana", password="senha-segura")
        transacao = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Divida em doze vezes",
            valor=Decimal("1000.00"),
            data_vencimento=date.today(),
            forma_pagamento=Transacao.FormaPagamento.PIX,
            parcelado=True,
            numero_parcelas=12,
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "R$ 83,33 cada")
        self.assertNotContains(response, "83.333333")
        self.assertEqual(transacao.valor_parcela, Decimal("83.33333333333333333333333333"))

    def test_receita_agendada_so_entra_apos_confirmacao(self):
        self.client.login(username="ana", password="senha-segura")
        transacao = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Receita agendada",
            valor=Decimal("500.00"),
            data_vencimento=date.today(),
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Confirmar recebimento")
        self.assertContains(response, "Receita agendada")
        self.client.post(
            reverse("transacao_concluir", args=[transacao.pk]),
            {"conta_origem": self.conta.pk},
        )
        transacao.refresh_from_db()
        self.assertEqual(transacao.status, Transacao.Status.CONCLUIDO)
        self.assertEqual(transacao.valor_pago, Decimal("500.00"))
        self.assertEqual(self.conta.saldo, Decimal("500.00"))

    def test_despesa_nao_pode_selecionar_categoria(self):
        categoria = CategoriaDespesa.objects.create(
            nome="Salario",
            tipo=CategoriaDespesa.Tipo.RECEITA,
        )
        form = TransacaoForm(
            data={
                "tipo": "despesa",
                "descricao": "Mercado",
                "categoria": "",
                "valor": "90.00",
                "data_vencimento": date.today().isoformat(),
                "status": "pendente",
                "forma_pagamento": "pix",
                "parcelado": "",
                "numero_parcelas": "1",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIsNone(form.cleaned_data["categoria"])
        self.assertIn(categoria, form.fields["categoria"].queryset)

    def test_categoria_inativa_nao_aparece_para_novo_lancamento(self):
        categoria = CategoriaDespesa.objects.create(nome="Categoria antiga", ativa=False)
        form = TransacaoForm()
        self.assertNotIn(categoria, form.fields["categoria"].queryset)

    def test_usuario_cadastra_categoria_e_forma_de_pagamento(self):
        self.client.login(username="ana", password="senha-segura")
        self.client.post(reverse("cadastros_financeiros"), {"tipo": "categoria", "nome": "Academia"})
        self.client.post(reverse("cadastros_financeiros"), {"tipo": "forma_pagamento", "nome": "Cartao azul"})
        self.assertTrue(CategoriaDespesa.objects.filter(usuario=self.usuario_a, nome="Academia").exists())
        self.assertEqual(
            CategoriaDespesa.objects.get(usuario=self.usuario_a, nome="Academia").tipo,
            CategoriaDespesa.Tipo.RECEITA,
        )
        form = TransacaoForm(usuario=self.usuario_a)
        self.assertIn(("Cartao azul", "Cartao azul"), list(form.fields["forma_pagamento"].widget.choices))

    def test_baixa_de_despesa_exige_conta_do_proprio_usuario(self):
        self.client.force_login(self.usuario_a)
        despesa = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Conta com origem",
            valor=Decimal("60.00"),
            data_vencimento=date.today(),
        )
        url = reverse("transacao_concluir", args=[despesa.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        resposta = self.client.post(url)
        self.assertEqual(resposta.status_code, 400)
        despesa.refresh_from_db()
        self.assertEqual(despesa.parcelas_pagas, 0)

        conta_de_b = ContaFinanceira.objects.create(usuario=self.usuario_b, nome="Conta privada")
        resposta = self.client.post(url, {"conta_origem": conta_de_b.pk})
        self.assertEqual(resposta.status_code, 400)
        despesa.refresh_from_db()
        self.assertEqual(despesa.parcelas_pagas, 0)

        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Saldo para despesa",
            valor=Decimal("60.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        resposta = self.client.post(url, {"conta_origem": self.conta.pk})
        self.assertRedirects(resposta, reverse("dashboard"))
        despesa.refresh_from_db()
        self.assertEqual(despesa.conta_origem, self.conta)
        self.assertEqual(despesa.status, Transacao.Status.CONCLUIDO)

    def test_baixa_nao_permite_despesa_acima_do_saldo_da_conta(self):
        self.client.force_login(self.usuario_a)
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Saldo disponivel",
            valor=Decimal("40.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        despesa = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Despesa maior que saldo",
            valor=Decimal("50.00"),
            data_vencimento=date.today(),
        )

        resposta = self.client.post(
            reverse("transacao_concluir", args=[despesa.pk]),
            {"conta_origem": self.conta.pk},
        )

        self.assertEqual(resposta.status_code, 400)
        self.assertContains(resposta, "Saldo insuficiente", status_code=400)
        despesa.refresh_from_db()
        self.assertEqual(despesa.parcelas_pagas, 0)
        self.assertEqual(despesa.status, Transacao.Status.PENDENTE)
        self.assertEqual(self.conta.saldo, Decimal("40.00"))

    def test_formulario_de_despesa_avisa_saldo_insuficiente_se_conta_escolhida(self):
        form = TransacaoForm(
            data={
                "tipo": Transacao.Tipo.DESPESA,
                "descricao": "Despesa maior que saldo",
                "categoria": "",
                "conta_origem": self.conta.pk,
                "valor": "50.00",
                "data_vencimento": date.today().isoformat(),
                "status": Transacao.Status.PENDENTE,
                "forma_pagamento": Transacao.FormaPagamento.OUTRO,
                "numero_parcelas": "1",
            },
            usuario=self.usuario_a,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Saldo insuficiente", str(form.errors["conta_origem"]))

    def test_tipos_de_receita_aparecem_separados_no_dashboard(self):
        salario = CategoriaDespesa.objects.create(
            usuario=self.usuario_a,
            nome="Salario",
            tipo=CategoriaDespesa.Tipo.RECEITA,
        )
        CategoriaDespesa.objects.create(
            usuario=self.usuario_a,
            nome="Moradia",
            tipo=CategoriaDespesa.Tipo.DESPESA,
        )
        receita = Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Salario mensal",
            categoria=salario,
            valor=Decimal("2000.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
        )
        self.client.force_login(self.usuario_a)
        resposta = self.client.get(reverse("dashboard"))
        self.assertEqual(
            list(resposta.context["receitas_categoria"]),
            [{"categoria__nome": "Salario", "total": receita.valor_pago}],
        )
        self.assertContains(resposta, "Receitas por tipo")
        form = TransacaoForm(data={
            "tipo": Transacao.Tipo.RECEITA,
            "descricao": "Freelance",
            "categoria": "",
            "valor": "500.00",
            "data_vencimento": date.today().isoformat(),
            "status": Transacao.Status.PENDENTE,
            "forma_pagamento": Transacao.FormaPagamento.OUTRO,
            "numero_parcelas": "1",
        }, usuario=self.usuario_a)
        self.assertFalse(form.is_valid())
        self.assertIn("categoria", form.errors)

    def test_usuario_pode_cadastrar_contas_financeiras_isoladas(self):
        self.client.force_login(self.usuario_a)
        resposta = self.client.post(
            reverse("cadastros_financeiros"),
            {"acao": "conta", "nome": "Carteira"},
        )
        self.assertRedirects(resposta, reverse("cadastros_financeiros"))
        self.assertTrue(
            ContaFinanceira.objects.filter(usuario=self.usuario_a, nome="Carteira").exists()
        )
        self.client.force_login(self.usuario_b)
        resposta = self.client.get(reverse("cadastros_financeiros"))
        self.assertNotContains(resposta, "Conta principal")
        self.assertNotContains(resposta, "Carteira")

    def test_formulario_da_tela_salva_tipo_de_receita_e_conta(self):
        self.client.force_login(self.usuario_a)
        url = reverse("cadastros_financeiros")

        resposta = self.client.post(
            url,
            {
                "acao": "categoria",
                "nome": "Servicos",
                "tipo": CategoriaDespesa.Tipo.RECEITA,
            },
        )
        self.assertRedirects(resposta, url)
        self.assertTrue(
            CategoriaDespesa.objects.filter(
                usuario=self.usuario_a,
                nome="Servicos",
                tipo=CategoriaDespesa.Tipo.RECEITA,
            ).exists()
        )

        resposta = self.client.post(
            url,
            {"acao": "conta", "nome": "Banco principal"},
        )
        self.assertRedirects(resposta, url)
        self.assertTrue(
            ContaFinanceira.objects.filter(
                usuario=self.usuario_a,
                nome="Banco principal",
            ).exists()
        )

    def test_transferencia_move_saldo_e_bloqueia_saldo_insuficiente(self):
        destino = ContaFinanceira.objects.create(usuario=self.usuario_a, nome="Poupanca")
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Deposito inicial",
            valor=Decimal("200.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        self.client.force_login(self.usuario_a)
        url = reverse("cadastros_financeiros")
        resposta = self.client.post(
            url,
            {
                "acao": "transferencia",
                "conta_origem": self.conta.pk,
                "conta_destino": destino.pk,
                "valor": "75.00",
            },
        )
        self.assertRedirects(resposta, url)
        transferencia = TransferenciaFinanceira.objects.get(usuario=self.usuario_a)
        self.assertEqual(transferencia.valor, Decimal("75.00"))
        self.assertEqual(self.conta.saldo, Decimal("125.00"))
        self.assertEqual(destino.saldo, Decimal("75.00"))

        resposta = self.client.post(
            url,
            {
                "acao": "transferencia",
                "conta_origem": self.conta.pk,
                "conta_destino": destino.pk,
                "valor": "126.00",
            },
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertContains(resposta, "saldo da conta de origem e insuficiente")
        self.assertEqual(TransferenciaFinanceira.objects.count(), 1)

    def test_transferencia_pode_ser_excluida_e_saldos_sao_recalculados(self):
        destino = ContaFinanceira.objects.create(usuario=self.usuario_a, nome="Poupanca")
        transferencia = TransferenciaFinanceira.objects.create(
            usuario=self.usuario_a,
            conta_origem=self.conta,
            conta_destino=destino,
            valor=Decimal("45.00"),
        )
        transferencia_outro_usuario = TransferenciaFinanceira.objects.create(
            usuario=self.usuario_b,
            conta_origem=None,
            conta_destino=None,
            valor=Decimal("20.00"),
        )
        self.client.force_login(self.usuario_a)
        resposta = self.client.get(reverse("cadastros_financeiros"))
        self.assertContains(resposta, "Transferencias registradas")
        self.assertContains(resposta, "R$ 45,00")
        self.assertNotContains(resposta, "R$ 20,00")

        resposta = self.client.post(
            reverse("transferencia_financeira_excluir", args=[transferencia_outro_usuario.pk])
        )
        self.assertEqual(resposta.status_code, 404)

        resposta = self.client.post(
            reverse("transferencia_financeira_excluir", args=[transferencia.pk])
        )
        self.assertRedirects(resposta, reverse("cadastros_financeiros"))
        self.assertFalse(TransferenciaFinanceira.objects.filter(pk=transferencia.pk).exists())
        self.assertTrue(
            TransferenciaFinanceira.objects.filter(pk=transferencia_outro_usuario.pk).exists()
        )
        self.assertEqual(self.conta.saldo, Decimal("0.00"))
        self.assertEqual(destino.saldo, Decimal("0.00"))

    def test_editar_conta_e_excluir_apenas_com_saldo_zerado(self):
        self.client.force_login(self.usuario_a)
        url = reverse("cadastros_financeiros")
        resposta = self.client.post(
            url,
            {
                "acao": "conta",
                "cadastro_id": self.conta.pk,
                "nome": "Conta atualizada",
            },
        )
        self.assertRedirects(resposta, url)
        self.conta.refresh_from_db()
        self.assertEqual(self.conta.nome, "Conta atualizada")

        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Saldo de abertura",
            valor=Decimal("10.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        url_excluir = reverse("conta_financeira_excluir", args=[self.conta.pk])
        resposta = self.client.post(url_excluir)
        self.assertRedirects(resposta, url)
        self.assertTrue(ContaFinanceira.objects.filter(pk=self.conta.pk).exists())

        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Saida do saldo",
            valor=Decimal("10.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            conta_origem=self.conta,
        )
        self.assertEqual(self.conta.saldo, Decimal("0.00"))
        resposta = self.client.post(url_excluir)
        self.assertRedirects(resposta, url)
        self.assertFalse(ContaFinanceira.objects.filter(pk=self.conta.pk).exists())

    def test_categoria_e_forma_de_pagamento_sao_isoladas_por_usuario(self):
        categoria_b = CategoriaDespesa.objects.create(usuario=self.usuario_b, nome="Categoria do Bruno")
        FormaPagamentoPersonalizada.objects.create(usuario=self.usuario_b, nome="Cartao do Bruno")

        form_a = TransacaoForm(usuario=self.usuario_a)

        self.assertNotIn(categoria_b, form_a.fields["categoria"].queryset)
        self.assertNotIn(
            ("Cartao do Bruno", "Cartao do Bruno"),
            list(form_a.fields["forma_pagamento"].widget.choices),
        )

        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(reverse("cadastros_financeiros"))
        self.assertNotContains(response, categoria_b.nome)
        self.assertNotContains(response, "Cartao do Bruno")

    def test_lancamento_rejeita_forma_de_pagamento_de_outro_usuario(self):
        FormaPagamentoPersonalizada.objects.create(usuario=self.usuario_b, nome="Cartao do Bruno")
        self.client.login(username="ana", password="senha-segura")

        response = self.client.post(
            reverse("transacao_nova"),
            {
                "tipo": "despesa",
                "descricao": "Compra privada",
                "valor": "90.00",
                "data_vencimento": date.today().isoformat(),
                "status": "pendente",
                "forma_pagamento": "Cartao do Bruno",
                "parcelado": "",
                "numero_parcelas": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Transacao.objects.filter(usuario=self.usuario_a, descricao="Compra privada").exists())
        self.assertContains(response, "Selecione uma forma de pagamento valida.")

    def test_usuario_pode_editar_e_excluir_seus_cadastros(self):
        categoria = CategoriaDespesa.objects.create(
            usuario=self.usuario_a,
            nome="Academia",
            tipo=CategoriaDespesa.Tipo.RECEITA,
        )
        forma = FormaPagamentoPersonalizada.objects.create(usuario=self.usuario_a, nome="Cartao azul")
        categoria_de_b = CategoriaDespesa.objects.create(usuario=self.usuario_b, nome="Categoria privada")
        forma_de_b = FormaPagamentoPersonalizada.objects.create(usuario=self.usuario_b, nome="Pagamento privado")
        self.client.login(username="ana", password="senha-segura")

        response = self.client.get(
            reverse("cadastros_financeiros"),
            {"editar_categoria": categoria.pk},
        )
        self.assertContains(response, 'value="Academia"')
        response = self.client.post(
            reverse("cadastros_financeiros"),
            {"tipo": "categoria", "cadastro_id": categoria.pk, "nome": "Academia atualizada"},
        )
        self.assertRedirects(response, reverse("cadastros_financeiros"))
        self.assertTrue(CategoriaDespesa.objects.filter(pk=categoria.pk, nome="Academia atualizada").exists())

        response = self.client.get(
            reverse("cadastros_financeiros"),
            {"editar_forma_pagamento": forma.pk},
        )
        self.assertContains(response, 'value="Cartao azul"')
        response = self.client.post(
            reverse("cadastros_financeiros"),
            {"tipo": "forma_pagamento", "cadastro_id": forma.pk, "nome": "Pix pessoal"},
        )
        self.assertRedirects(response, reverse("cadastros_financeiros"))
        self.assertTrue(FormaPagamentoPersonalizada.objects.filter(pk=forma.pk, nome="Pix pessoal").exists())

        response = self.client.post(reverse("categoria_excluir", args=[categoria.pk]))
        self.assertRedirects(response, reverse("cadastros_financeiros"))
        self.assertFalse(CategoriaDespesa.objects.filter(pk=categoria.pk).exists())
        response = self.client.post(reverse("forma_pagamento_excluir", args=[forma.pk]))
        self.assertRedirects(response, reverse("cadastros_financeiros"))
        self.assertFalse(FormaPagamentoPersonalizada.objects.filter(pk=forma.pk).exists())

        self.assertEqual(
            self.client.post(reverse("categoria_excluir", args=[categoria_de_b.pk])).status_code,
            404,
        )
        self.assertEqual(
            self.client.post(reverse("forma_pagamento_excluir", args=[forma_de_b.pk])).status_code,
            404,
        )

    def test_relatorio_mensal_pode_ser_baixado_em_pdf(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(reverse("relatorio_pdf"), {"ano": date.today().year, "mes": date.today().month})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_logo_png_e_jpg_sao_aceitos_e_outros_formatos_rejeitados(self):
        dados = {
            "nome_empresa": "",
            "titulo_relatorio": "Relatorio",
            "titulo_recibo": "Recibo",
            "cor_principal": "#1f6b4f",
            "rodape": "",
        }
        for campo, dimensoes in ConfiguracaoDocumentoForm.DIMENSOES_MAXIMAS_LOGO.items():
            with self.subTest(campo=campo):
                conteudo = BytesIO()
                Image.new("RGB", dimensoes, color="white").save(conteudo, format="PNG")
                form = ConfiguracaoDocumentoForm(
                    data=dados,
                    files={
                        campo: SimpleUploadedFile(
                            f"{campo}.png",
                            conteudo.getvalue(),
                            content_type="image/png",
                        )
                    },
                )
                self.assertTrue(form.is_valid(), form.errors)

                conteudo = BytesIO()
                Image.new("RGB", (dimensoes[0] + 1, dimensoes[1]), color="white").save(conteudo, format="PNG")
                form = ConfiguracaoDocumentoForm(
                    data=dados,
                    files={
                        campo: SimpleUploadedFile(
                            f"{campo}.png",
                            conteudo.getvalue(),
                            content_type="image/png",
                        )
                    },
                )
                self.assertFalse(form.is_valid())
                self.assertIn(campo, form.errors)

        jpg = BytesIO()
        Image.new("RGB", (120, 100), color="white").save(jpg, format="JPEG")
        form = ConfiguracaoDocumentoForm(
            data=dados,
            files={
                "logo_relatorio": SimpleUploadedFile(
                    "logo.jpg",
                    jpg.getvalue(),
                    content_type="image/jpeg",
                )
            },
        )
        self.assertTrue(form.is_valid(), form.errors)

        arquivo_grande = SimpleUploadedFile(
            "logo.png",
            b"x" * (ConfiguracaoDocumentoForm.TAMANHO_MAXIMO_LOGO + 1),
            content_type="image/png",
        )
        form = ConfiguracaoDocumentoForm(data=dados, files={"logo_menu": arquivo_grande})
        self.assertFalse(form.is_valid())
        self.assertIn("logo_menu", form.errors)

        for nome, tipo in (("logo.gif", "image/gif"), ("logo.jpg", "image/jpeg")):
            arquivo_invalido = SimpleUploadedFile(nome, b"not an image", content_type=tipo)
            form = ConfiguracaoDocumentoForm(data=dados, files={"logo_menu": arquivo_invalido})
            self.assertFalse(form.is_valid())
            self.assertIn("logo_menu", form.errors)

    def test_relatorio_e_recibo_pdf_sao_gerados_com_logo(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        recibo = Recibo.objects.create(
            criado_por=self.usuario_a,
            recebedor="Cliente",
            servico="Consultoria",
            valor=Decimal("250.00"),
            data_pagamento=date.today(),
        )
        with tempfile.TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            configuracao = ConfiguracaoDocumento.objects.create(usuario=self.usuario_a)
            for campo, dimensoes in (
                ("logo_menu", (500, 500)),
                ("logo_relatorio", (120, 100)),
                ("logo_recibo", (200, 200)),
            ):
                conteudo = BytesIO()
                Image.new("RGB", dimensoes, color="white").save(conteudo, format="PNG")
                getattr(configuracao, campo).save(
                    f"{campo}.png",
                    SimpleUploadedFile(
                        f"{campo}.png",
                        conteudo.getvalue(),
                        content_type="image/png",
                    ),
                    save=False,
                )
            configuracao.save()
            self.client.force_login(self.usuario_a)
            dashboard = self.client.get(reverse("dashboard"))
            self.assertContains(dashboard, configuracao.logo_menu.url)
            for url in (
                reverse("relatorio_pdf"),
                reverse("recibo_pdf", args=[recibo.pk]),
            ):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response["Content-Type"], "application/pdf")
                    self.assertTrue(response.content.startswith(b"%PDF"))

    def test_recibo_requer_permissao(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(reverse("recibos"))
        self.assertEqual(response.status_code, 403)

        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        response = self.client.post(
            reverse("recibos"),
            {
                "recebedor": "Cliente da Ana",
                "servico": "Consultoria",
                "valor": "250.00",
                "data_pagamento": date.today().isoformat(),
                "forma_pagamento": "Pix",
                "observacoes": "Pagamento integral",
            },
        )
        self.assertRedirects(response, reverse("recibos"))
        recibo = Recibo.objects.get(criado_por=self.usuario_a)
        self.assertEqual(self.client.get(reverse("recibo_pdf", args=[recibo.pk])).status_code, 200)

    def test_recibo_pessoa_fisica_valida_cpf_e_gera_pdf(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        self.client.force_login(self.usuario_a)
        response = self.client.post(
            reverse("recibos"),
            {
                "recebedor": "Maria da Silva",
                "cpf": "529.982.247-25",
                "servico": "Consultoria financeira",
                "valor": "350.00",
                "data_pagamento": date.today().isoformat(),
                "forma_pagamento": "Pix",
                "observacoes": "Atendimento individual",
            },
        )
        self.assertRedirects(response, reverse("recibos"))
        recibo = Recibo.objects.get(criado_por=self.usuario_a)
        self.assertEqual(recibo.cpf, "52998224725")

        pdf = self.client.get(reverse("recibo_pdf", args=[recibo.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

        resposta_invalida = self.client.post(
            reverse("recibos"),
            {
                "recebedor": "Maria da Silva",
                "cpf": "111.111.111-11",
                "servico": "Consultoria financeira",
                "valor": "350.00",
                "data_pagamento": date.today().isoformat(),
            },
        )
        self.assertEqual(resposta_invalida.status_code, 200)
        self.assertContains(resposta_invalida, "Informe um CPF valido")
        self.assertEqual(Recibo.objects.filter(criado_por=self.usuario_a).count(), 1)

    def test_cliente_consumidor_final_com_cpf_pode_ser_cadastrado_e_usado_no_recibo(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        self.client.force_login(self.usuario_a)
        resposta = self.client.post(
            reverse("recibos"),
            {
                "tipo": "cliente",
                "cpf": "529.982.247-25",
                "razao_social": "Maria da Silva",
            },
        )
        self.assertRedirects(resposta, reverse("recibos"))
        cliente = Cliente.objects.get(usuario=self.usuario_a)
        self.assertEqual(cliente.cnpj, "")
        self.assertEqual(cliente.cpf, "52998224725")

        resposta = self.client.post(
            reverse("recibos"),
            {
                "cliente": cliente.pk,
                "recebedor": "",
                "cpf": "",
                "servico": "Servico para consumidor final",
                "valor": "120.00",
                "data_pagamento": date.today().isoformat(),
            },
        )
        self.assertRedirects(resposta, reverse("recibos"))
        recibo = Recibo.objects.get(criado_por=self.usuario_a)
        self.assertEqual(recibo.cliente, cliente)
        pdf = self.client.get(reverse("recibo_pdf", args=[recibo.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_status_do_recibo_no_pdf_usa_rotulo_legivel(self):
        self.assertEqual(recibo_status_legivel(Recibo.Status.PENDENTE), "Pendente")
        self.assertEqual(recibo_status_legivel(Recibo.Status.RECEBIDO), "Recebido")
        self.assertEqual(recibo_status_legivel("valor-desconhecido"), "Indefinido")

    def test_cabecalho_pdf_mostra_status_e_numero_sem_tags_html(self):
        from reportlab.lib.styles import getSampleStyleSheet

        estilo = getSampleStyleSheet()["Normal"]
        numero = "\n".join(
            linha.getPlainText()
            for linha in recibo_numero_paragrafos(42, estilo)
        )
        status_pendente = recibo_status_paragrafo(
            Recibo.Status.PENDENTE,
            estilo,
            "#b45309",
        ).getPlainText()
        status_recebido = recibo_status_paragrafo(
            Recibo.Status.RECEBIDO,
            estilo,
            "#047857",
        ).getPlainText()

        self.assertEqual(numero, "RECIBO\nN. 00042")
        self.assertEqual(status_pendente, "PENDENTE")
        self.assertEqual(status_recebido, "RECEBIDO")
        for texto in (numero, status_pendente, status_recebido):
            self.assertNotIn("<", texto)
            self.assertNotIn(">", texto)

    def test_administrador_configura_acesso_de_outro_usuario(self):
        self.usuario_a.is_staff = True
        self.usuario_a.save(update_fields=("is_staff",))
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(
            reverse("configuracao_documentos"),
            {
                "tipo": "acessos",
                "usuarios_liberados": [self.usuario_b.pk],
                "usuarios_ativos": [self.usuario_b.pk],
            },
        )
        self.assertRedirects(response, reverse("configuracao_documentos"))
        self.assertTrue(PermissaoRecibo.objects.get(usuario=self.usuario_b).pode_emitir)
        self.usuario_b.refresh_from_db()
        self.assertTrue(self.usuario_b.is_active)
        self.client.post(reverse("configuracao_documentos"), {"tipo": "acessos"})
        self.usuario_b.refresh_from_db()
        self.assertFalse(self.usuario_b.is_active)

    def test_recibo_pode_ser_editado_excluido_e_baixado(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        recibo = Recibo.objects.create(
            criado_por=self.usuario_a,
            recebedor="Cliente",
            servico="Aula",
            valor=Decimal("100.00"),
            data_pagamento=date.today(),
        )
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(
            reverse("recibo_editar", args=[recibo.pk]),
            {
                "recebedor": "Cliente atualizado",
                "servico": "Aula avancada",
                "valor": "150.00",
                "data_pagamento": date.today().isoformat(),
                "forma_pagamento": "Pix",
                "observacoes": "Atualizado",
            },
        )
        self.assertRedirects(response, reverse("recibos"))
        recibo.refresh_from_db()
        self.assertEqual(recibo.recebedor, "Cliente atualizado")
        self.assertEqual(recibo.status, Recibo.Status.PENDENTE)
        self.client.post(reverse("recibo_baixar", args=[recibo.pk]))
        recibo.refresh_from_db()
        self.assertEqual(recibo.status, Recibo.Status.RECEBIDO)
        receita = Transacao.objects.get(recibo_origem=recibo)
        self.assertEqual(receita.descricao, "Recibo - Aula avancada")
        self.assertEqual(receita.valor, Decimal("150.00"))
        self.assertEqual(receita.status, Transacao.Status.CONCLUIDO)
        self.client.post(reverse("recibo_baixar", args=[recibo.pk]))
        self.assertEqual(Transacao.objects.filter(recibo_origem=recibo).count(), 1)
        self.client.post(reverse("recibo_excluir", args=[recibo.pk]))
        self.assertFalse(Recibo.objects.filter(pk=recibo.pk).exists())

    def test_cliente_e_recibo_ficam_vinculados_pelo_cnpj(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(
            reverse("recibos"),
            {
                "tipo": "cliente",
                "cnpj": "12.345.678/0001-90",
                "razao_social": "Empresa de Teste Ltda",
                "nome_fantasia": "Empresa Teste",
                "logradouro": "Rua Principal",
                "numero": "100",
                "municipio": "Sao Paulo",
                "uf": "SP",
                "cep": "01000-000",
            },
        )
        self.assertRedirects(response, reverse("recibos"))
        cliente = Cliente.objects.get(usuario=self.usuario_a)
        self.assertEqual(cliente.cnpj, "12345678000190")
        response = self.client.post(
            reverse("recibos"),
            {
                "cliente": cliente.pk,
                "recebedor": "",
                "servico": "Manutencao",
                "valor": "200.00",
                "data_pagamento": date.today().isoformat(),
                "forma_pagamento": "Pix",
                "observacoes": "Servico mensal",
            },
        )
        self.assertRedirects(response, reverse("recibos"))
        recibo = Recibo.objects.get(criado_por=self.usuario_a)
        self.assertEqual(recibo.cliente, cliente)
        self.assertEqual(recibo.recebedor, "Empresa Teste")
        pdf = self.client.get(reverse("recibo_pdf", args=[recibo.pk]))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_consulta_cnpj_retorna_cliente_ja_cadastrado(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        cliente = Cliente.objects.create(
            usuario=self.usuario_a,
            cnpj="12345678000190",
            razao_social="Empresa de Teste Ltda",
            nome_fantasia="Empresa Teste",
        )
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(reverse("cliente_consultar_cnpj"), {"cnpj": "12.345.678/0001-90"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["origem"], "cadastro")
        self.assertEqual(response.json()["razao_social"], cliente.razao_social)

    def test_empresa_pode_ser_editada_e_apagada_com_isolamento(self):
        PermissaoRecibo.objects.create(usuario=self.usuario_a, pode_emitir=True)
        empresa_a = Cliente.objects.create(
            usuario=self.usuario_a,
            cnpj="12345678000190",
            razao_social="Empresa A",
        )
        empresa_b = Cliente.objects.create(
            usuario=self.usuario_b,
            cnpj="98765432000110",
            razao_social="Empresa B",
        )
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(
            reverse("cliente_editar", args=[empresa_a.pk]),
            {
                "cnpj": "12.345.678/0001-90",
                "razao_social": "Empresa A Atualizada",
            },
        )
        self.assertRedirects(response, reverse("recibos"))
        empresa_a.refresh_from_db()
        self.assertEqual(empresa_a.razao_social, "Empresa A Atualizada")
        self.assertEqual(self.client.post(reverse("cliente_excluir", args=[empresa_b.pk])).status_code, 404)
        self.client.post(reverse("cliente_excluir", args=[empresa_a.pk]))
        self.assertFalse(Cliente.objects.filter(pk=empresa_a.pk).exists())

    def test_relatorio_pdf_permite_selecionar_secoes(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(
            reverse("relatorio_pdf"),
            {
                "ano": date.today().year,
                "mes": date.today().month,
                "pdf_secao": ["resumo"],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_usuario_cria_planejamento_com_valor_restante(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(
            reverse("planejamentos"),
            {
                "objeto": "Notebook",
                "conta": "Poupanca",
                "valor_meta": "3000.00",
                "valor_guardado": "750.00",
            },
        )
        self.assertRedirects(response, reverse("planejamentos"))
        planejamento = Planejamento.objects.get(usuario=self.usuario_a)
        self.assertEqual(planejamento.valor_restante, Decimal("2250.00"))
        self.assertContains(self.client.get(reverse("planejamentos")), "2.250,00")

    def test_usuario_nao_acessa_planejamento_de_outro_usuario(self):
        planejamento = Planejamento.objects.create(
            usuario=self.usuario_b,
            objeto="Viagem",
            conta="Conta do Bruno",
            valor_meta=Decimal("2000.00"),
        )
        self.client.login(username="ana", password="senha-segura")
        self.assertEqual(self.client.get(reverse("planejamentos"), {"editar": planejamento.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("planejamento_excluir", args=[planejamento.pk])).status_code, 404)

    def test_usuario_adiciona_dinheiro_e_reduz_valor_restante(self):
        planejamento = Planejamento.objects.create(
            usuario=self.usuario_a,
            objeto="Celular",
            conta="Poupanca",
            valor_meta=Decimal("1500.00"),
            valor_guardado=Decimal("400.00"),
        )
        self.client.login(username="ana", password="senha-segura")
        response = self.client.post(
            reverse("planejamento_adicionar_guardado", args=[planejamento.pk]),
            {"valor": "250.00"},
        )
        self.assertRedirects(response, reverse("planejamentos"))
        planejamento.refresh_from_db()
        self.assertEqual(planejamento.valor_guardado, Decimal("650.00"))
        self.assertEqual(planejamento.valor_restante, Decimal("850.00"))

    def test_adicionar_guardado_nao_ultrapassa_meta(self):
        planejamento = Planejamento.objects.create(
            usuario=self.usuario_a,
            objeto="Bicicleta",
            conta="Poupanca",
            valor_meta=Decimal("1000.00"),
            valor_guardado=Decimal("900.00"),
        )
        self.client.login(username="ana", password="senha-segura")
        self.client.post(
            reverse("planejamento_adicionar_guardado", args=[planejamento.pk]),
            {"valor": "200.00"},
        )
        planejamento.refresh_from_db()
        self.assertEqual(planejamento.valor_guardado, Decimal("1000.00"))
        self.assertEqual(planejamento.valor_restante, Decimal("0.00"))

    def test_dashboard_exibe_receitas_por_tipo_e_saldo_das_contas(self):
        self.client.login(username="ana", password="senha-segura")
        categoria = CategoriaDespesa.objects.create(
            nome="Servicos",
            tipo=CategoriaDespesa.Tipo.RECEITA,
        )
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Servico prestado",
            categoria=categoria,
            valor=Decimal("250.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            parcelas_pagas=1,
            conta_origem=self.conta,
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Receitas por tipo")
        self.assertContains(response, "Servicos")
        self.assertContains(response, "Saldo das contas")
        self.assertContains(response, "250,00")

    def test_dashboard_exibe_mes_em_portugues(self):
        self.client.login(username="ana", password="senha-segura")
        response = self.client.get(reverse("dashboard"))
        meses = (
            "janeiro", "fevereiro", "marco", "abril", "maio", "junho",
            "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
        )
        self.assertContains(response, f"{meses[date.today().month - 1]} de {date.today().year}")

    def test_relatorio_mensal_filtra_mes_e_usuario(self):
        self.client.login(username="ana", password="senha-segura")
        categoria = CategoriaDespesa.objects.create(nome="Alimentacao")
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Despesa de setembro",
            categoria=categoria,
            valor=Decimal("100.00"),
            data_vencimento=date(2026, 9, 5),
            status=Transacao.Status.CONCLUIDO,
        )
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.RECEITA,
            descricao="Receita de agosto",
            valor=Decimal("800.00"),
            data_vencimento=date(2026, 8, 10),
            status=Transacao.Status.CONCLUIDO,
        )
        response = self.client.get(reverse("relatorio_mensal"), {"ano": 2026, "mes": 8})
        self.assertContains(response, "Receita de agosto")
        self.assertNotContains(response, "Despesa de setembro")
        self.assertContains(response, "Agosto de 2026")


class SubUsuarioTests(TestCase):
    def setUp(self):
        self.usuario_principal = User.objects.create_user(
            username="principal",
            password="UmaSenhaForte123!",
            first_name="Maria",
            last_name="Silva",
        )
        self.client.force_login(self.usuario_principal)

    def criar_subusuario(self, username="operador"):
        usuario = User.objects.create_user(
            username=username,
            password="UmaSenhaForte123!",
            first_name="Joao",
            last_name="Operador",
        )
        return SubUsuario.objects.create(
            usuario=usuario,
            conta_principal=self.usuario_principal,
        )

    def test_usuario_principal_cria_subusuario(self):
        resposta = self.client.post(
            reverse("subusuarios"),
            {
                "first_name": "Joao",
                "last_name": "Operador",
                "username": "operador",
                "email": "joao@example.com",
                "password1": "UmaSenhaForte123!",
                "password2": "UmaSenhaForte123!",
            },
        )
        self.assertRedirects(resposta, reverse("subusuarios"))
        perfil = SubUsuario.objects.get(usuario__username="operador")
        self.assertEqual(perfil.conta_principal, self.usuario_principal)
        self.assertFalse(perfil.usuario.is_staff)
        self.assertFalse(perfil.usuario.is_superuser)
        self.assertEqual(self.client.get(reverse("subusuarios")).status_code, 200)

    def test_subusuario_cria_lancamento_na_conta_principal_com_autoria(self):
        perfil = self.criar_subusuario()
        self.client.force_login(perfil.usuario)
        resposta = self.client.post(
            reverse("transacao_nova"),
            {
                "tipo": "despesa",
                "descricao": "Compra registrada por operador",
                "valor": "25.00",
                "data_vencimento": date.today().isoformat(),
                "status": "pendente",
                "forma_pagamento": "pix",
                "parcelado": "",
                "numero_parcelas": "1",
            },
        )
        self.assertRedirects(resposta, reverse("dashboard"))
        lancamento = Transacao.objects.get(descricao="Compra registrada por operador")
        self.assertEqual(lancamento.usuario, self.usuario_principal)
        self.assertEqual(lancamento.criado_por, perfil.usuario)
        painel = self.client.get(reverse("dashboard"))
        self.assertContains(painel, "Joao Operador")
        self.assertContains(painel, lancamento.descricao)
        self.assertTrue(painel.context["eh_subusuario"])

        self.client.force_login(self.usuario_principal)
        painel_principal = self.client.get(reverse("dashboard"))
        self.assertContains(painel_principal, "Joao Operador")
        self.assertContains(painel_principal, lancamento.descricao)
        relatorio = self.client.get(reverse("relatorio_mensal"))
        self.assertContains(relatorio, "Joao Operador")

    def test_subusuario_nao_pode_alterar_lancamentos_ou_acessar_outras_areas(self):
        perfil = self.criar_subusuario()
        lancamento = Transacao.objects.create(
            usuario=self.usuario_principal,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Lancamento protegido",
            valor=Decimal("30.00"),
            data_vencimento=date.today(),
        )
        self.client.force_login(perfil.usuario)

        self.assertEqual(self.client.get(reverse("transacao_nova")).status_code, 200)
        for nome in ("relatorio_mensal", "cadastros_financeiros", "planejamentos", "subusuarios"):
            with self.subTest(url=nome):
                self.assertEqual(self.client.get(reverse(nome)).status_code, 403)
        for nome in ("transacao_editar", "transacao_excluir", "transacao_concluir"):
            with self.subTest(url=nome):
                resposta = self.client.post(reverse(nome, args=[lancamento.pk]))
                self.assertEqual(resposta.status_code, 403)
        lancamento.refresh_from_db()
        self.assertEqual(lancamento.descricao, "Lancamento protegido")

    def test_usuario_principal_pode_bloquear_desbloquear_e_excluir_subusuario(self):
        perfil = self.criar_subusuario()
        url_bloquear = reverse("subusuario_alternar_bloqueio", args=[perfil.pk])
        self.assertEqual(self.client.get(url_bloquear).status_code, 405)

        resposta = self.client.post(url_bloquear)
        self.assertRedirects(resposta, reverse("subusuarios"))
        perfil.usuario.refresh_from_db()
        self.assertFalse(perfil.usuario.is_active)

        self.client.force_login(perfil.usuario)
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 302)

        self.client.force_login(self.usuario_principal)
        self.client.post(url_bloquear)
        perfil.usuario.refresh_from_db()
        self.assertTrue(perfil.usuario.is_active)
        self.client.post(reverse("subusuario_excluir", args=[perfil.pk]))
        self.assertFalse(User.objects.filter(username="operador").exists())

    def test_usuario_principal_nao_pode_administrar_subusuario_de_outra_conta(self):
        outro_principal = User.objects.create_user(username="outra-conta")
        outro_usuario = User.objects.create_user(username="de-outra-conta")
        perfil = SubUsuario.objects.create(
            usuario=outro_usuario,
            conta_principal=outro_principal,
        )
        resposta = self.client.post(
            reverse("subusuario_alternar_bloqueio", args=[perfil.pk])
        )
        self.assertEqual(resposta.status_code, 404)
        outro_usuario.refresh_from_db()
        self.assertTrue(outro_usuario.is_active)
