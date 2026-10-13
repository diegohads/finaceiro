from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("relatorio/", views.relatorio_mensal, name="relatorio_mensal"),
    path("relatorio/pdf/", views.relatorio_mensal, name="relatorio_pdf"),
    path("recibos/", views.recibos, name="recibos"),
    path("recibos/clientes/consultar/", views.cliente_consultar_cnpj, name="cliente_consultar_cnpj"),
    path("recibos/clientes/<int:pk>/editar/", views.cliente_editar, name="cliente_editar"),
    path("recibos/clientes/<int:pk>/excluir/", views.cliente_excluir, name="cliente_excluir"),
    path("recibos/<int:pk>/pdf/", views.recibo_pdf, name="recibo_pdf"),
    path("recibos/<int:pk>/editar/", views.recibo_editar, name="recibo_editar"),
    path("recibos/<int:pk>/excluir/", views.recibo_excluir, name="recibo_excluir"),
    path("recibos/<int:pk>/baixar/", views.recibo_baixar, name="recibo_baixar"),
    path("configuracao/documentos/", views.configuracao_documentos, name="configuracao_documentos"),
    path("cadastros/", views.cadastros_financeiros, name="cadastros_financeiros"),
    path("cadastros/contas/<int:pk>/excluir/", views.conta_financeira_excluir, name="conta_financeira_excluir"),
    path("cadastros/transferencias/<int:pk>/excluir/", views.transferencia_financeira_excluir, name="transferencia_financeira_excluir"),
    path("cadastros/categorias/<int:pk>/excluir/", views.categoria_excluir, name="categoria_excluir"),
    path("cadastros/formas-pagamento/<int:pk>/excluir/", views.forma_pagamento_excluir, name="forma_pagamento_excluir"),
    path("planejamentos/", views.planejamentos, name="planejamentos"),
    path("planejamentos/<int:pk>/excluir/", views.planejamento_excluir, name="planejamento_excluir"),
    path("planejamentos/<int:pk>/adicionar-guardado/", views.planejamento_adicionar_guardado, name="planejamento_adicionar_guardado"),
    path("subusuarios/", views.subusuarios, name="subusuarios"),
    path("subusuarios/<int:pk>/bloquear/", views.subusuario_alternar_bloqueio, name="subusuario_alternar_bloqueio"),
    path("subusuarios/<int:pk>/excluir/", views.subusuario_excluir, name="subusuario_excluir"),
    path("login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path(
        "recuperar-senha/",
        auth_views.PasswordResetView.as_view(
            template_name="registration/password_reset_form.html",
            email_template_name="registration/password_reset_email.html",
            success_url="/recuperar-senha/enviado/",
            subject_template_name="registration/password_reset_subject.txt",
        ),
        name="password_reset",
    ),
    path(
        "recuperar-senha/enviado/",
        auth_views.PasswordResetDoneView.as_view(template_name="registration/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "recuperar-senha/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="registration/password_reset_confirm.html",
            success_url="/recuperar-senha/concluido/",
        ),
        name="password_reset_confirm",
    ),
    path(
        "recuperar-senha/concluido/",
        auth_views.PasswordResetCompleteView.as_view(template_name="registration/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    path("cadastro/", views.cadastro, name="cadastro"),
    path("logout/", auth_views.LogoutView.as_view(next_page="/login/"), name="logout"),
    path("transacoes/nova/", views.transacao_nova, name="transacao_nova"),
    path("transacoes/<int:pk>/editar/", views.transacao_editar, name="transacao_editar"),
    path("transacoes/<int:pk>/excluir/", views.transacao_excluir, name="transacao_excluir"),
    path("transacoes/<int:pk>/concluir/", views.transacao_concluir, name="transacao_concluir"),
]
