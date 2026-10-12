from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .forms import TransacaoForm
from .models import CategoriaDespesa, Cliente, FormaPagamentoPersonalizada, PermissaoRecibo, Planejamento, Recibo, Transacao


class IsolamentoFinanceiroTests(TestCase):
    def setUp(self):
        self.usuario_a = User.objects.create_user(username="ana", password="senha-segura")
        self.usuario_b = User.objects.create_user(username="bruno", password="senha-segura")
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

        self.client.post(reverse("transacao_concluir", args=[despesa.pk]))
        despesa.refresh_from_db()
        dashboard = self.client.get(reverse("dashboard"))
        self.assertEqual(despesa.status, Transacao.Status.CONCLUIDO)
        self.assertEqual(despesa.valor_pago, Decimal("750.00"))
        self.assertEqual(dashboard.context["total_despesas"], Decimal("750.00"))
        self.assertEqual(dashboard.context["saldo"], Decimal("-750.00"))

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
        self.client.post(reverse("transacao_concluir", args=[transacao.pk]))
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
        url = reverse("transacao_concluir", args=[transacao.pk])
        self.client.post(url)
        transacao.refresh_from_db()
        self.assertEqual(transacao.parcelas_pagas, 1)
        self.assertEqual(transacao.valor_pago, Decimal("100.00"))
        self.assertEqual(transacao.status, Transacao.Status.PENDENTE)
        self.client.post(url)
        self.client.post(url)
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
        self.client.post(reverse("transacao_concluir", args=[transacao.pk]))
        transacao.refresh_from_db()
        self.assertEqual(transacao.status, Transacao.Status.CONCLUIDO)
        self.assertEqual(transacao.valor_pago, Decimal("500.00"))

    def test_despesa_pode_usar_categoria_ativa(self):
        categoria = CategoriaDespesa.objects.create(nome="Alimentacao")
        form = TransacaoForm(
            data={
                "tipo": "despesa",
                "descricao": "Mercado",
                "categoria": categoria.pk,
                "valor": "90.00",
                "data_vencimento": date.today().isoformat(),
                "status": "pendente",
                "forma_pagamento": "pix",
                "parcelado": "",
                "numero_parcelas": "1",
            }
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["categoria"], categoria)

    def test_categoria_inativa_nao_aparece_para_novo_lancamento(self):
        categoria = CategoriaDespesa.objects.create(nome="Categoria antiga", ativa=False)
        form = TransacaoForm()
        self.assertNotIn(categoria, form.fields["categoria"].queryset)

    def test_usuario_cadastra_categoria_e_forma_de_pagamento(self):
        self.client.login(username="ana", password="senha-segura")
        self.client.post(reverse("cadastros_financeiros"), {"tipo": "categoria", "nome": "Academia"})
        self.client.post(reverse("cadastros_financeiros"), {"tipo": "forma_pagamento", "nome": "Cartao azul"})
        self.assertTrue(CategoriaDespesa.objects.filter(usuario=self.usuario_a, nome="Academia").exists())
        form = TransacaoForm(usuario=self.usuario_a)
        self.assertIn(("Cartao azul", "Cartao azul"), list(form.fields["forma_pagamento"].widget.choices))

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
        categoria = CategoriaDespesa.objects.create(usuario=self.usuario_a, nome="Academia")
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
        self.assertContains(self.client.get(reverse("planejamentos")), "2250,00")

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

    def test_dashboard_exibe_estatistica_por_categoria(self):
        self.client.login(username="ana", password="senha-segura")
        categoria = CategoriaDespesa.objects.create(nome="Alimentacao")
        Transacao.objects.create(
            usuario=self.usuario_a,
            tipo=Transacao.Tipo.DESPESA,
            descricao="Supermercado",
            categoria=categoria,
            valor=Decimal("250.00"),
            data_vencimento=date.today(),
            status=Transacao.Status.CONCLUIDO,
            parcelas_pagas=1,
        )
        response = self.client.get(reverse("dashboard"))
        self.assertContains(response, "Onde voce mais gastou")
        self.assertContains(response, "Alimentacao")
        self.assertContains(response, "Como se organizar")

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
