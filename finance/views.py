from calendar import monthrange
from datetime import date
from decimal import Decimal
from functools import wraps
from io import BytesIO
import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from unicodedata import normalize

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.db import transaction as db_transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from .forms import (
    AdicionarGuardadoForm,
    BaixaDespesaForm,
    CadastroForm,
    CategoriaDespesaForm,
    ClienteForm,
    ConfiguracaoDocumentoForm,
    ContaFinanceiraForm,
    FormaPagamentoForm,
    PlanejamentoForm,
    ReciboForm,
    SubUsuarioForm,
    TransferenciaFinanceiraForm,
    TransacaoForm,
)
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


MESES_PT_BR = (
    "janeiro",
    "fevereiro",
    "marco",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

logger = logging.getLogger(__name__)


def usuario_da_conta(usuario):
    perfil = SubUsuario.objects.filter(usuario=usuario).select_related("conta_principal").first()
    return perfil.conta_principal if perfil else usuario


def principal_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if SubUsuario.objects.filter(usuario=request.user).exists():
            return HttpResponseForbidden("Esta area esta disponivel apenas para o usuario principal.")
        return view_func(request, *args, **kwargs)

    return wrapper


def desenhar_logo_pdf(documento, logo, x, topo, largura_maxima, altura_maxima):
    if not logo:
        return 0

    from reportlab.lib.utils import ImageReader

    try:
        with logo.open("rb") as arquivo:
            imagem = ImageReader(arquivo)
            largura_original, altura_original = imagem.getSize()
            escala = min(
                largura_maxima / largura_original,
                altura_maxima / altura_original,
            )
            largura = largura_original * escala
            altura = altura_original * escala
            documento.drawImage(
                imagem,
                x,
                topo - altura,
                width=largura,
                height=altura,
                mask="auto",
            )
    except (OSError, ValueError):
        logger.warning(
            "Nao foi possivel inserir a logo %s no PDF; o documento sera gerado sem ela.",
            logo.name,
            exc_info=True,
        )
        return 0
    return altura


def moeda(valor):
    return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def cnpj_formatado(cnpj):
    digitos = "".join(char for char in str(cnpj) if char.isdigit())
    if len(digitos) == 14:
        return f"{digitos[:2]}.{digitos[2:5]}.{digitos[5:8]}/{digitos[8:12]}-{digitos[12:]}"
    return str(cnpj)


def pode_emitir_recibo(usuario):
    if usuario.is_staff:
        return True
    return PermissaoRecibo.objects.filter(usuario=usuario, pode_emitir=True).exists()


def configuracao_do_usuario(usuario):
    return ConfiguracaoDocumento.objects.filter(usuario=usuario).first()


def recibo_status_legivel(status):
    return {
        Recibo.Status.PENDENTE: "Pendente",
        Recibo.Status.RECEBIDO: "Recebido",
    }.get(status, "Indefinido")


def recibo_numero_paragrafos(numero, estilo):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph

    estilo_numero = ParagraphStyle(
        "ReciboNumeroCabecalho",
        parent=estilo,
        fontName="Helvetica-Bold",
    )
    return [
        Paragraph("RECIBO", estilo),
        Paragraph(f"N. {numero:05d}", estilo_numero),
    ]


def recibo_status_paragrafo(status, estilo, cor):
    from reportlab.lib.colors import HexColor
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    from xml.sax.saxutils import escape

    status_texto = escape(recibo_status_legivel(status).upper())
    estilo_status = ParagraphStyle(
        "ReciboStatusCabecalho",
        parent=estilo,
        fontName="Helvetica-Bold",
        textColor=HexColor(cor),
    )
    return Paragraph(status_texto, estilo_status)


def cadastro(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    if request.method == "POST":
        form = CadastroForm(request.POST)
        if form.is_valid():
            usuario = form.save()
            login(request, usuario)
            return redirect("dashboard")
    else:
        form = CadastroForm()
    return render(request, "registration/cadastro.html", {"form": form})


@login_required
def dashboard(request):
    conta_principal = usuario_da_conta(request.user)
    eh_subusuario = conta_principal.pk != request.user.pk
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    fim_mes = hoje.replace(day=monthrange(hoje.year, hoje.month)[1])
    transacoes = Transacao.objects.filter(usuario=conta_principal).select_related("criado_por")
    planejamentos = Planejamento.objects.filter(usuario=conta_principal)

    movimentacoes_mes = transacoes.filter(
        data_vencimento__gte=inicio_mes,
        data_vencimento__lte=fim_mes,
    )
    valor_pago = ExpressionWrapper(
        F("valor") * F("parcelas_pagas") / F("numero_parcelas"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    total_receitas = movimentacoes_mes.filter(tipo=Transacao.Tipo.RECEITA).aggregate(
        total=Sum(valor_pago)
    )["total"] or Decimal("0")
    total_despesas = movimentacoes_mes.filter(tipo=Transacao.Tipo.DESPESA).aggregate(
        total=Sum(valor_pago)
    )["total"] or Decimal("0")

    receitas_categoria = (
        movimentacoes_mes.filter(
            tipo=Transacao.Tipo.RECEITA,
            status=Transacao.Status.CONCLUIDO,
            categoria__tipo=CategoriaDespesa.Tipo.RECEITA,
        )
        .values("categoria__nome")
        .annotate(total=Sum(valor_pago))
        .order_by("-total")
    )
    dicas_organizacao = []
    if total_despesas > total_receitas:
        dicas_organizacao.append("Suas despesas pagas superam as receitas recebidas. Revise gastos variaveis e parcelas.")
    elif total_receitas and total_despesas >= total_receitas * Decimal("0.8"):
        dicas_organizacao.append("Voce ja comprometeu 80% ou mais do que recebeu. Reserve uma margem para imprevistos.")
    else:
        dicas_organizacao.append("Separe parte do saldo para uma reserva antes de assumir novos gastos.")

    alertas = transacoes.filter(
        status=Transacao.Status.PENDENTE,
        data_vencimento__lte=hoje,
    ).order_by("data_vencimento")
    proximas = transacoes.filter(
        status=Transacao.Status.PENDENTE,
        data_vencimento__gt=hoje,
    ).order_by("data_vencimento")[:5]

    contexto = {
        "today": hoje,
        "transacoes": transacoes.order_by("-criado_em")[:8],
        "alertas": alertas,
        "baixas_pendentes": alertas,
        "proximas": proximas,
        "total_receitas": total_receitas,
        "total_despesas": total_despesas,
        "saldo": total_receitas - total_despesas,
        "receitas_categoria": receitas_categoria,
        "contas_financeiras": ContaFinanceira.objects.filter(usuario=conta_principal),
        "dicas_organizacao": dicas_organizacao,
        "mes_atual": f"{MESES_PT_BR[hoje.month - 1]} de {hoje.year}",
        "planejamentos": planejamentos[:3],
        "configuracao_documento": configuracao_do_usuario(conta_principal),
        "conta_principal": conta_principal,
        "eh_subusuario": eh_subusuario,
    }
    return render(request, "finance/dashboard.html", contexto)


@login_required
@principal_required
def subusuarios(request):
    form = SubUsuarioForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with db_transaction.atomic():
            usuario = form.save()
            SubUsuario.objects.create(usuario=usuario, conta_principal=request.user)
        messages.success(request, "Subusuario criado com sucesso.")
        return redirect("subusuarios")

    return render(
        request,
        "finance/subusuarios.html",
        {
            "form": form,
            "subusuarios": SubUsuario.objects.filter(
                conta_principal=request.user
            ).select_related("usuario"),
        },
    )


@login_required
@principal_required
@require_POST
def subusuario_alternar_bloqueio(request, pk):
    perfil = get_object_or_404(SubUsuario, pk=pk, conta_principal=request.user)
    perfil.usuario.is_active = not perfil.usuario.is_active
    perfil.usuario.save(update_fields=["is_active"])
    estado = "desbloqueado" if perfil.usuario.is_active else "bloqueado"
    messages.success(request, f"Subusuario {estado}.")
    return redirect("subusuarios")


@login_required
@principal_required
@require_POST
def subusuario_excluir(request, pk):
    perfil = get_object_or_404(SubUsuario, pk=pk, conta_principal=request.user)
    perfil.usuario.delete()
    messages.success(request, "Subusuario excluido.")
    return redirect("subusuarios")


@login_required
@principal_required
def planejamentos(request):
    planejamento_editando = None
    if request.method == "POST":
        planejamento_id = request.POST.get("planejamento_id")
        planejamento_editando = get_object_or_404(
            Planejamento,
            pk=planejamento_id,
            usuario=request.user,
        ) if planejamento_id else None
        form = PlanejamentoForm(request.POST, instance=planejamento_editando)
        if form.is_valid():
            planejamento = form.save(commit=False)
            planejamento.usuario = request.user
            planejamento.save()
            messages.success(
                request,
                "Planejamento atualizado." if planejamento_editando else "Planejamento criado.",
            )
            return redirect("planejamentos")
    else:
        editar_id = request.GET.get("editar")
        if editar_id:
            planejamento_editando = get_object_or_404(
                Planejamento,
                pk=editar_id,
                usuario=request.user,
            )
        form = PlanejamentoForm(instance=planejamento_editando)
    return render(
        request,
        "finance/planejamentos.html",
        {
            "form": form,
            "planejamentos": Planejamento.objects.filter(usuario=request.user),
            "planejamento_editando": planejamento_editando,
        },
    )


@login_required
@principal_required
def planejamento_excluir(request, pk):
    planejamento = get_object_or_404(Planejamento, pk=pk, usuario=request.user)
    if request.method == "POST":
        planejamento.delete()
        messages.success(request, "Planejamento excluido.")
    return redirect("planejamentos")


@login_required
@principal_required
def planejamento_adicionar_guardado(request, pk):
    planejamento = get_object_or_404(Planejamento, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = AdicionarGuardadoForm(request.POST)
        if form.is_valid():
            valor_adicionado = form.cleaned_data["valor"]
            planejamento.valor_guardado = min(
                planejamento.valor_guardado + valor_adicionado,
                planejamento.valor_meta,
            )
            planejamento.save(update_fields=("valor_guardado",))
            messages.success(request, "Valor guardado atualizado. Acompanhe quanto falta para sua meta.")
    return redirect("planejamentos")


@login_required
@principal_required
def relatorio_mensal(request):
    hoje = date.today()
    try:
        ano = int(request.GET.get("ano", hoje.year))
        mes = int(request.GET.get("mes", hoje.month))
        inicio_mes = date(ano, mes, 1)
    except (TypeError, ValueError):
        ano, mes = hoje.year, hoje.month
        inicio_mes = date(ano, mes, 1)

    fim_mes = date(ano, mes, monthrange(ano, mes)[1])
    transacoes = Transacao.objects.filter(
        usuario=request.user,
        data_vencimento__gte=inicio_mes,
        data_vencimento__lte=fim_mes,
    ).select_related("criado_por")
    valor_pago = ExpressionWrapper(
        F("valor") * F("parcelas_pagas") / F("numero_parcelas"),
        output_field=DecimalField(max_digits=12, decimal_places=2),
    )
    receitas = transacoes.filter(tipo=Transacao.Tipo.RECEITA).aggregate(total=Sum(valor_pago))["total"] or Decimal("0")
    despesas = transacoes.filter(tipo=Transacao.Tipo.DESPESA).aggregate(total=Sum(valor_pago))["total"] or Decimal("0")
    categorias = (
        transacoes.filter(tipo=Transacao.Tipo.DESPESA, categoria__isnull=False)
        .values("categoria__nome")
        .annotate(total=Sum(valor_pago))
        .order_by("-total")
    )
    meses = [(numero, MESES_PT_BR[numero - 1]) for numero in range(1, 13)]
    contexto = {
        "transacoes": transacoes,
        "receitas": receitas,
        "despesas": despesas,
        "saldo": receitas - despesas,
        "categorias": categorias,
        "meses": meses,
        "mes_selecionado": mes,
        "ano_selecionado": ano,
        "mes_atual": f"{MESES_PT_BR[mes - 1]} de {ano}",
    }
    contexto["configuracao_documento"] = configuracao_do_usuario(request.user)
    secoes_pdf = set(request.GET.getlist("pdf_secao"))
    if not secoes_pdf:
        secoes_pdf = {"resumo", "lancamentos", "categorias"}
    contexto["secoes_pdf"] = secoes_pdf
    if request.GET.get("formato") == "pdf" or request.resolver_match.url_name == "relatorio_pdf":
        return relatorio_pdf(request, contexto, ano, mes)
    return render(request, "finance/relatorio_mensal.html", contexto)


def relatorio_pdf(request, contexto, ano, mes):
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas
    except ImportError:
        return HttpResponse("Instale reportlab para gerar PDF.", status=500)

    def texto(valor):
        return normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")

    def quebrar(valor, fonte, tamanho, largura_maxima):
        palavras = texto(valor).split()
        linhas = []
        linha = ""
        for palavra in palavras:
            tentativa = f"{linha} {palavra}".strip()
            if linha and stringWidth(tentativa, fonte, tamanho) > largura_maxima:
                linhas.append(linha)
                linha = palavra
            else:
                linha = tentativa
        if linha:
            linhas.append(linha)
        return linhas or [""]

    buffer = BytesIO()
    documento = canvas.Canvas(buffer, pagesize=A4)
    largura, altura = A4
    configuracao = contexto.get("configuracao_documento") or configuracao_do_usuario(request.user)
    cor_principal = HexColor(configuracao.cor_principal if configuracao else "#1f6b4f")
    margem = 42
    y = altura - 48
    documento.setFillColor(cor_principal)
    documento.setFont("Helvetica-Bold", 18)
    titulo = configuracao.titulo_relatorio if configuracao else "Relatorio financeiro"
    largura_logo = 120
    altura_logo = 100
    tem_logo = bool(configuracao and configuracao.logo_relatorio)
    if tem_logo:
        tem_logo = bool(desenhar_logo_pdf(
            documento,
            configuracao.logo_relatorio,
            largura - margem - largura_logo,
            y + 14,
            largura_logo,
            altura_logo,
        ))
    titulo_largura = largura - 2 * margem - (largura_logo + 10 if tem_logo else 0)
    linhas_titulo = quebrar(titulo, "Helvetica-Bold", 18, titulo_largura)
    for indice, linha_titulo in enumerate(linhas_titulo):
        documento.drawString(margem, y - indice * 22, linha_titulo)
    documento.setFont("Helvetica", 10)
    documento.setFillColorRGB(0.35, 0.38, 0.36)
    y_periodo = y - 18 - (len(linhas_titulo) - 1) * 22
    documento.drawString(margem, y_periodo, texto(f"Periodo: {MESES_PT_BR[mes - 1]} de {ano}"))
    documento.setStrokeColor(cor_principal)
    documento.setLineWidth(1.2)
    documento.line(margem, y_periodo - 17, largura - margem, y_periodo - 17)
    y = y_periodo - 52
    secoes_pdf = contexto.get("secoes_pdf", {"resumo", "lancamentos", "categorias"})
    if "resumo" in secoes_pdf:
        documento.setFillColorRGB(0.95, 0.97, 0.95)
        documento.roundRect(margem, y - 62, largura - 2 * margem, 62, 7, fill=1, stroke=0)
        colunas = (("Receitas recebidas", contexto["receitas"]), ("Despesas pagas", contexto["despesas"]), ("Saldo", contexto["saldo"]))
        largura_coluna = (largura - 2 * margem) / 3
        for indice, (rotulo, valor) in enumerate(colunas):
            x = margem + indice * largura_coluna + 14
            documento.setFillColorRGB(0.35, 0.38, 0.36)
            documento.setFont("Helvetica-Bold", 8)
            documento.drawString(x, y - 20, texto(rotulo.upper()))
            documento.setFillColor(cor_principal if indice != 1 else HexColor("#df6f58"))
            documento.setFont("Helvetica-Bold", 14)
            documento.drawString(x, y - 43, texto(f"R$ {moeda(valor)}"))
        y -= 88

    if "lancamentos" in secoes_pdf:
        if y < 120:
            documento.showPage()
            y = altura - 48
        documento.setFillColorRGB(0.1, 0.13, 0.11)
        documento.setFont("Helvetica-Bold", 12)
        documento.drawString(margem, y, "Lancamentos do periodo")
        y -= 20
        documento.setFillColorRGB(0.95, 0.96, 0.95)
        documento.rect(margem, y - 18, largura - 2 * margem, 22, fill=1, stroke=0)
        documento.setFillColorRGB(0.35, 0.38, 0.36)
        documento.setFont("Helvetica-Bold", 8)
        documento.drawString(margem + 8, y - 11, "DESCRICAO")
        documento.drawString(370, y - 11, "DATA")
        documento.drawRightString(largura - margem - 8, y - 11, "VALOR")
        y -= 28
        documento.setFont("Helvetica", 9)
        for transacao in contexto["transacoes"]:
            descricao = f"{transacao.descricao} ({transacao.get_tipo_display()})"
            documento.setFillColorRGB(0.1, 0.13, 0.11)
            documento.drawString(margem + 8, y, quebrar(descricao, "Helvetica", 9, 270)[0])
            documento.setFillColorRGB(0.35, 0.38, 0.36)
            documento.drawString(370, y, texto(f"{transacao.data_vencimento:%d/%m/%Y}"))
            documento.setFillColor(HexColor("#df6f58") if transacao.tipo == Transacao.Tipo.DESPESA else cor_principal)
            documento.drawRightString(largura - margem - 8, y, texto(f"R$ {moeda(transacao.valor_pago)}"))
            if transacao.criado_por:
                nome_autor = transacao.criado_por.get_full_name() or transacao.criado_por.get_username()
                documento.setFillColorRGB(0.45, 0.48, 0.46)
                documento.setFont("Helvetica", 7)
                documento.drawString(margem + 8, y - 10, texto(f"Lancado por {nome_autor}")[:100])
                y -= 20
            else:
                y -= 21
            documento.setStrokeColorRGB(0.88, 0.89, 0.88)
            documento.line(margem, y + 8, largura - margem, y + 8)
            if y < 50:
                documento.showPage()
                y = altura - 48
                documento.setFont("Helvetica", 9)

    if "categorias" in secoes_pdf:
        if y < 85:
            documento.showPage()
            y = altura - 50
        documento.setFillColorRGB(0.1, 0.13, 0.11)
        documento.setFont("Helvetica-Bold", 12)
        documento.drawString(margem, y, "Despesas por categoria")
        y -= 22
        documento.setFont("Helvetica", 9)
        for item in contexto["categorias"]:
            documento.setFillColorRGB(0.1, 0.13, 0.11)
            documento.drawString(margem + 8, y, texto(item["categoria__nome"])[:75])
            documento.setFillColor(cor_principal)
            documento.drawRightString(largura - margem - 8, y, texto(f"R$ {moeda(item['total'])}"))
            y -= 16
            if y < 50:
                documento.showPage()
                y = altura - 48
                documento.setFont("Helvetica", 9)
    documento.save()
    resposta = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    resposta["Content-Disposition"] = f'attachment; filename="relatorio-{ano}-{mes:02d}.pdf"'
    return resposta


@login_required
@principal_required
def recibos(request):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    form = ReciboForm(usuario=request.user)
    cliente_form = ClienteForm()
    if request.method == "POST":
        if request.POST.get("tipo") == "cliente":
            cliente_form = ClienteForm(request.POST)
            if cliente_form.is_valid():
                cliente = cliente_form.save(commit=False)
                cliente.usuario = request.user
                cliente.save()
                messages.success(request, "Cliente cadastrado para futuros recibos.")
                return redirect("recibos")
        else:
            form = ReciboForm(request.POST, usuario=request.user)
            if form.is_valid():
                recibo = form.save(commit=False)
                recibo.criado_por = request.user
                recibo.save()
                messages.success(request, "Recibo criado como pendente. De a baixa quando receber.")
                return redirect("recibos")
    else:
        form = ReciboForm(usuario=request.user)
    return render(request, "finance/recibos.html", {
        "form": form,
        "cliente_form": cliente_form,
        "clientes": Cliente.objects.filter(usuario=request.user),
        "clientes_json": list(Cliente.objects.filter(usuario=request.user).values(
            "id", "cnpj", "cpf", "razao_social", "nome_fantasia"
        )),
        "recibos": Recibo.objects.filter(criado_por=request.user),
        "configuracao_documento": configuracao_do_usuario(request.user),
    })


@login_required
@principal_required
def cliente_editar(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    cliente = get_object_or_404(Cliente, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            messages.success(request, "Empresa atualizada.")
            return redirect("recibos")
    else:
        form = ClienteForm(instance=cliente)
    return render(request, "finance/cliente_editar.html", {"form": form, "cliente": cliente})


@login_required
@principal_required
def cliente_excluir(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    cliente = get_object_or_404(Cliente, pk=pk, usuario=request.user)
    if request.method == "POST":
        cliente.delete()
        messages.success(request, "Empresa excluida.")
    return redirect("recibos")


@login_required
@principal_required
def recibo_editar(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    recibo = get_object_or_404(Recibo, pk=pk, criado_por=request.user)
    if request.method == "POST":
        form = ReciboForm(request.POST, instance=recibo, usuario=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Recibo atualizado.")
            return redirect("recibos")
    else:
        form = ReciboForm(instance=recibo, usuario=request.user)
    return render(request, "finance/recibo_editar.html", {"form": form, "recibo": recibo})


@login_required
@principal_required
def recibo_excluir(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    recibo = get_object_or_404(Recibo, pk=pk, criado_por=request.user)
    if request.method == "POST":
        recibo.delete()
        messages.success(request, "Recibo excluido.")
    return redirect("recibos")


@login_required
@principal_required
def recibo_baixar(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    recibo = get_object_or_404(Recibo, pk=pk, criado_por=request.user)
    if request.method == "POST" and recibo.status == Recibo.Status.PENDENTE:
        with db_transaction.atomic():
            recibo = Recibo.objects.select_for_update().get(pk=recibo.pk)
            if recibo.status == Recibo.Status.PENDENTE:
                transacao = Transacao.objects.create(
                    usuario=request.user,
                    criado_por=request.user,
                    tipo=Transacao.Tipo.RECEITA,
                    descricao=f"Recibo - {recibo.servico}",
                    valor=recibo.valor,
                    data_vencimento=date.today(),
                    status=Transacao.Status.CONCLUIDO,
                    forma_pagamento=recibo.forma_pagamento or Transacao.FormaPagamento.OUTRO,
                )
                recibo.status = Recibo.Status.RECEBIDO
                recibo.recebido_em = timezone.now()
                recibo.transacao = transacao
                recibo.save(update_fields=("status", "recebido_em", "transacao"))
        messages.success(request, "Recibo baixado e receita lancada no dashboard.")
    return redirect("recibos")


@login_required
@principal_required
def recibo_pdf(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    recibo = get_object_or_404(Recibo, pk=pk, criado_por=request.user)
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.utils import ImageReader
        from reportlab.platypus import (
            Image,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
        from xml.sax.saxutils import escape
    except ImportError:
        return HttpResponse("Instale reportlab para gerar PDF.", status=500)

    configuracao = configuracao_do_usuario(request.user)
    buffer = BytesIO()
    cor_principal = colors.HexColor(configuracao.cor_principal if configuracao else "#1f6b4f")
    documento = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=42,
        leftMargin=42,
        topMargin=34,
        bottomMargin=50,
        title=f"Recibo {recibo.pk:05d}",
        author=configuracao.nome_empresa if configuracao else "",
    )
    estilos = getSampleStyleSheet()
    estilo_titulo = ParagraphStyle(
        "ReciboTitulo",
        parent=estilos["Title"],
        fontName="Helvetica-Bold",
        fontSize=17,
        leading=21,
        textColor=colors.white,
        alignment=0,
        spaceAfter=4,
    )
    estilo_empresa = ParagraphStyle(
        "ReciboEmpresa",
        parent=estilos["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#d1fae5"),
    )
    estilo_rotulo = ParagraphStyle(
        "ReciboRotulo",
        parent=estilos["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=11,
        textColor=colors.HexColor("#64748b"),
        spaceAfter=3,
    )
    estilo_valor = ParagraphStyle(
        "ReciboValor",
        parent=estilos["Normal"],
        fontName="Helvetica",
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#102a43"),
    )
    estilo_secao = ParagraphStyle(
        "ReciboSecao",
        parent=estilos["Heading2"],
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=cor_principal,
        spaceBefore=8,
        spaceAfter=8,
    )
    estilo_valor_destaque = ParagraphStyle(
        "ReciboValorDestaque",
        parent=estilos["Normal"],
        fontName="Helvetica-Bold",
        fontSize=22,
        leading=26,
        textColor=cor_principal,
        alignment=2,
    )

    def paragrafo(valor, estilo=estilo_valor):
        return Paragraph(escape(str(valor or "")).replace("\n", "<br/>"), estilo)

    largura_util = A4[0] - 84
    logo = None
    if configuracao and configuracao.logo_recibo:
        try:
            with configuracao.logo_recibo.open("rb") as arquivo:
                conteudo_logo = BytesIO(arquivo.read())
            largura_original, altura_original = ImageReader(conteudo_logo).getSize()
            escala = min(52 / largura_original, 52 / altura_original, 1)
            conteudo_logo.seek(0)
            logo = Image(
                conteudo_logo,
                width=largura_original * escala,
                height=altura_original * escala,
            )
        except (OSError, ValueError):
            logger.warning(
                "Nao foi possivel carregar a logo do recibo %s.",
                configuracao.logo_recibo.name,
                exc_info=True,
            )

    titulo = configuracao.titulo_recibo if configuracao else "Recibo de pagamento"
    status_cor = "#047857" if recibo.status == Recibo.Status.RECEBIDO else "#b45309"
    cabecalho_texto = [
        paragrafo(titulo, estilo_titulo),
        paragrafo(configuracao.nome_empresa, estilo_empresa)
        if configuracao and configuracao.nome_empresa
        else paragrafo("COMPROVANTE DE PAGAMENTO", estilo_empresa),
    ]
    conteudo_cabecalho = [
        logo or "",
        cabecalho_texto,
        recibo_numero_paragrafos(recibo.pk, estilo_empresa)
        + [recibo_status_paragrafo(recibo.status, estilo_empresa, status_cor)],
    ]
    larguras_cabecalho = [76, largura_util - 166, 90]
    cabecalho = Table(
        [conteudo_cabecalho],
        colWidths=larguras_cabecalho,
        rowHeights=76,
    )
    cabecalho.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), cor_principal),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("LEFTPADDING", (0, 0), (0, 0), 6),
                ("RIGHTPADDING", (0, 0), (0, 0), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )

    story = [cabecalho, Spacer(1, 16)]
    destaque = Table(
        [[
            [
                paragrafo("VALOR DO RECIBO", estilo_rotulo),
                paragrafo(f"R$ {moeda(recibo.valor)}", estilo_valor_destaque),
            ],
            [
                paragrafo("STATUS", estilo_rotulo),
                recibo_status_paragrafo(recibo.status, estilo_valor, status_cor),
            ],
        ]],
        colWidths=[largura_util * 0.7, largura_util * 0.3],
    )
    destaque.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f0fdfa")),
                ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#ccfbf1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("TOPPADDING", (0, 0), (-1, -1), 13),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
            ]
        )
    )
    story.extend([destaque, Spacer(1, 14), paragrafo("PAGADOR", estilo_secao)])

    documento_pagador = "Nao informado"
    endereco_pagador = ""
    if recibo.cliente:
        if recibo.cliente.cnpj:
            documento_pagador = f"CNPJ: {cnpj_formatado(recibo.cliente.cnpj)}"
        elif recibo.cliente.cpf:
            cpf = recibo.cliente.cpf
            documento_pagador = f"CPF: {cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"
        endereco_pagador = recibo.cliente.endereco_completo
    elif recibo.cpf:
        cpf = recibo.cpf
        documento_pagador = f"CPF: {cpf[:3]}.{cpf[3:6]}.{cpf[6:9]}-{cpf[9:]}"

    pagador = [
        [paragrafo("NOME / RAZAO SOCIAL", estilo_rotulo), paragrafo("DOCUMENTO", estilo_rotulo)],
        [paragrafo(recibo.recebedor), paragrafo(documento_pagador)],
    ]
    if endereco_pagador:
        pagador.extend(
            [
                [paragrafo("ENDERECO", estilo_rotulo), ""],
                [paragrafo(endereco_pagador), ""],
            ]
        )
        estilo_tabela_pagador = [
            ("SPAN", (0, 3), (1, 3)),
        ]
    else:
        estilo_tabela_pagador = []
    tabela_pagador = Table(pagador, colWidths=[largura_util * 0.58, largura_util * 0.42])
    tabela_pagador.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LINEBELOW", (0, 1), (-1, 1), 0.5, colors.HexColor("#e2e8f0")),
                *estilo_tabela_pagador,
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend([tabela_pagador, Spacer(1, 12), paragrafo("DETALHES DO PAGAMENTO", estilo_secao)])

    detalhes = [
        [paragrafo("SERVICO / REFERENTE A", estilo_rotulo), paragrafo("DATA", estilo_rotulo)],
        [paragrafo(recibo.servico), paragrafo(f"{recibo.data_pagamento:%d/%m/%Y}")],
    ]
    if configuracao and configuracao.mostrar_forma_pagamento and recibo.forma_pagamento:
        detalhes[0].append(paragrafo("FORMA DE PAGAMENTO", estilo_rotulo))
        detalhes[1].append(paragrafo(recibo.forma_pagamento))
    else:
        detalhes[0].append("")
        detalhes[1].append("")
    tabela_detalhes = Table(
        detalhes,
        colWidths=[largura_util * 0.52, largura_util * 0.22, largura_util * 0.26],
    )
    tabela_detalhes.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story.append(tabela_detalhes)
    if configuracao and configuracao.mostrar_observacoes and recibo.observacoes:
        story.extend(
            [
                Spacer(1, 12),
                paragrafo("OBSERVACOES", estilo_secao),
                paragrafo(recibo.observacoes),
            ]
        )
    texto_recibo = (
        f"Este documento registra o valor de R$ {moeda(recibo.valor)}, "
        f"referente a {recibo.servico}, associado a {recibo.recebedor}."
    )
    story.extend(
        [
            Spacer(1, 18),
            paragrafo(texto_recibo),
            Spacer(1, 28),
            Table(
                [["", ""]],
                colWidths=[largura_util * 0.48, largura_util * 0.48],
                style=TableStyle(
                    [
                        ("LINEABOVE", (0, 0), (-1, -1), 0.6, colors.HexColor("#94a3b8")),
                        ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ]
                ),
            ),
            Table(
                [[
                    paragrafo(recibo.recebedor),
                    paragrafo(configuracao.nome_empresa if configuracao and configuracao.nome_empresa else "Assinatura do emitente"),
                ]],
                colWidths=[largura_util * 0.48, largura_util * 0.48],
            ),
        ]
    )

    def rodape(canvas, documento_pdf):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
        canvas.line(42, 36, A4[0] - 42, 36)
        if configuracao and configuracao.rodape:
            canvas.setFont("Helvetica", 8)
            canvas.setFillColor(colors.HexColor("#64748b"))
            canvas.drawCentredString(A4[0] / 2, 23, configuracao.rodape[:120])
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#64748b"))
        canvas.drawCentredString(A4[0] / 2, 11, f"Pagina {documento_pdf.page}")
        canvas.restoreState()

    documento.build(story, onFirstPage=rodape, onLaterPages=rodape)
    resposta = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    resposta["Content-Disposition"] = f'attachment; filename="recibo-{recibo.pk}.pdf"'
    return resposta


@login_required
@principal_required
def cliente_consultar_cnpj(request):
    if not pode_emitir_recibo(request.user):
        return JsonResponse({"erro": "Sem permissao."}, status=403)
    cnpj = "".join(char for char in request.GET.get("cnpj", "") if char.isdigit())
    if len(cnpj) != 14:
        return JsonResponse({"erro": "Informe um CNPJ com 14 digitos."}, status=400)
    cliente = Cliente.objects.filter(usuario=request.user, cnpj=cnpj).first()
    if cliente:
        return JsonResponse({
            "origem": "cadastro",
            "cnpj": cliente.cnpj,
            "razao_social": cliente.razao_social,
            "nome_fantasia": cliente.nome_fantasia,
            "logradouro": cliente.logradouro,
            "numero": cliente.numero,
            "complemento": cliente.complemento,
            "bairro": cliente.bairro,
            "municipio": cliente.municipio,
            "uf": cliente.uf,
            "cep": cliente.cep,
            "email": cliente.email,
            "telefone": cliente.telefone,
        })
    try:
        requisicao = Request(f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}", headers={"User-Agent": "Nexsaldo/1.0"})
        with urlopen(requisicao, timeout=8) as resposta:
            dados = json.loads(resposta.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, ValueError):
        return JsonResponse({"erro": "Nao foi possivel consultar este CNPJ agora."}, status=502)
    return JsonResponse({
        "origem": "consulta",
        "cnpj": cnpj,
        "razao_social": dados.get("razao_social", ""),
        "nome_fantasia": dados.get("nome_fantasia", ""),
        "logradouro": dados.get("logradouro", ""),
        "numero": dados.get("numero", ""),
        "complemento": dados.get("complemento", ""),
        "bairro": dados.get("bairro", ""),
        "municipio": dados.get("municipio", ""),
        "uf": dados.get("uf", ""),
        "cep": dados.get("cep", ""),
        "email": dados.get("email", ""),
        "telefone": dados.get("ddd_telefone_1", "") or dados.get("telefone", ""),
    })


@login_required
@principal_required
def configuracao_documentos(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Apenas administradores podem configurar documentos.")
    configuracao, _ = ConfiguracaoDocumento.objects.get_or_create(usuario=request.user)
    if request.method == "POST":
        tipo = request.POST.get("acao", request.POST.get("tipo"))
        if tipo == "acessos":
            usuarios_liberados = set(request.POST.getlist("usuarios_liberados"))
            usuarios_ativos = set(request.POST.getlist("usuarios_ativos"))
            for usuario in User.objects.filter(is_staff=False).exclude(pk=request.user.pk):
                permissao, _ = PermissaoRecibo.objects.get_or_create(usuario=usuario)
                permissao.pode_emitir = str(usuario.pk) in usuarios_liberados
                permissao.save(update_fields=("pode_emitir",))
                usuario.is_active = str(usuario.pk) in usuarios_ativos
                usuario.save(update_fields=("is_active",))
            messages.success(request, "Permissoes de recibo atualizadas.")
            return redirect("configuracao_documentos")
        form = ConfiguracaoDocumentoForm(request.POST, request.FILES, instance=configuracao)
        if form.is_valid():
            form.save()
            messages.success(request, "Configuracao dos documentos salva.")
            return redirect("configuracao_documentos")
    else:
        form = ConfiguracaoDocumentoForm(instance=configuracao)
    return render(request, "finance/configuracao_documentos.html", {
        "form": form,
        "usuarios": User.objects.filter(is_staff=False).order_by("username"),
        "usuarios_ativos": set(User.objects.filter(is_active=True).values_list("pk", flat=True)),
        "permissoes": set(PermissaoRecibo.objects.filter(pode_emitir=True).values_list("usuario_id", flat=True)),
    })


@login_required
@principal_required
def cadastros_financeiros(request):
    categoria_editando = None
    forma_editando = None
    conta_editando = None
    transferencia_form = TransferenciaFinanceiraForm(usuario=request.user)
    conta_form = ContaFinanceiraForm()
    categoria_form = None
    forma_form = None
    if request.method == "POST":
        tipo = request.POST.get("acao", request.POST.get("tipo"))
        cadastro_id = request.POST.get("cadastro_id")
        if tipo == "categoria":
            categoria_editando = get_object_or_404(
                CategoriaDespesa,
                pk=cadastro_id,
                usuario=request.user,
                tipo=CategoriaDespesa.Tipo.RECEITA,
            ) if cadastro_id else None
            dados_categoria = request.POST.copy()
            dados_categoria.pop("tipo", None)
            form = CategoriaDespesaForm(dados_categoria, instance=categoria_editando)
            if form.is_valid():
                categoria = form.save(commit=False)
                categoria.usuario = request.user
                duplicada = CategoriaDespesa.objects.filter(
                    usuario=request.user,
                    nome=categoria.nome,
                ).exclude(pk=categoria.pk)
                if duplicada.exists():
                    form.add_error("nome", "Voce ja possui uma categoria com esse nome.")
                else:
                    categoria.save()
                    mensagem = "Categoria atualizada." if categoria_editando else "Categoria cadastrada."
                    messages.success(request, mensagem)
                    return redirect("cadastros_financeiros")
            categoria_form = form
        elif tipo == "forma_pagamento":
            forma_editando = get_object_or_404(
                FormaPagamentoPersonalizada,
                pk=cadastro_id,
                usuario=request.user,
            ) if cadastro_id else None
            form = FormaPagamentoForm(request.POST, instance=forma_editando)
            if form.is_valid():
                forma = form.save(commit=False)
                forma.usuario = request.user
                forma.save()
                mensagem = "Forma de pagamento atualizada." if forma_editando else "Forma de pagamento cadastrada."
                messages.success(request, mensagem)
                return redirect("cadastros_financeiros")
            forma_form = form
        elif tipo == "conta":
            conta_editando = get_object_or_404(
                ContaFinanceira,
                pk=cadastro_id,
                usuario=request.user,
            ) if cadastro_id else None
            conta_form = ContaFinanceiraForm(request.POST, instance=conta_editando)
            if conta_form.is_valid():
                conta = conta_form.save(commit=False)
                conta.usuario = request.user
                if ContaFinanceira.objects.filter(
                    usuario=request.user,
                    nome=conta.nome,
                ).exclude(pk=conta.pk).exists():
                    conta_form.add_error("nome", "Voce ja possui uma conta com esse nome.")
                else:
                    conta.save()
                    mensagem = "Conta financeira atualizada." if conta_editando else "Conta financeira cadastrada."
                    messages.success(request, mensagem)
                    return redirect("cadastros_financeiros")
        elif tipo == "transferencia":
            transferencia_form = TransferenciaFinanceiraForm(
                request.POST,
                usuario=request.user,
            )
            if transferencia_form.is_valid():
                with db_transaction.atomic():
                    origem = ContaFinanceira.objects.select_for_update().get(
                        pk=transferencia_form.cleaned_data["conta_origem"].pk,
                        usuario=request.user,
                    )
                    valor = transferencia_form.cleaned_data["valor"]
                    if origem.saldo < valor:
                        transferencia_form.add_error(
                            "valor",
                            "O saldo da conta de origem foi alterado e nao e suficiente para esta transferencia.",
                        )
                    else:
                        TransferenciaFinanceira.objects.create(
                            usuario=request.user,
                            conta_origem=origem,
                            conta_destino=transferencia_form.cleaned_data["conta_destino"],
                            valor=valor,
                        )
                        messages.success(request, "Transferencia realizada com sucesso.")
                        return redirect("cadastros_financeiros")
    else:
        categoria_id = request.GET.get("editar_categoria")
        forma_id = request.GET.get("editar_forma_pagamento")
        conta_id = request.GET.get("editar_conta")
        if categoria_id:
            categoria_editando = get_object_or_404(
                CategoriaDespesa,
                pk=categoria_id,
                usuario=request.user,
                tipo=CategoriaDespesa.Tipo.RECEITA,
            )
        if forma_id:
            forma_editando = get_object_or_404(
                FormaPagamentoPersonalizada,
                pk=forma_id,
                usuario=request.user,
            )
        if conta_id:
            conta_editando = get_object_or_404(
                ContaFinanceira,
                pk=conta_id,
                usuario=request.user,
            )
    if categoria_form is None:
        categoria_form = CategoriaDespesaForm(instance=categoria_editando)
    if forma_form is None:
        forma_form = FormaPagamentoForm(instance=forma_editando)
    if request.method != "POST" or request.POST.get("acao") != "conta":
        conta_form = ContaFinanceiraForm(instance=conta_editando)
    categorias = CategoriaDespesa.objects.filter(
        usuario=request.user,
        ativa=True,
        tipo=CategoriaDespesa.Tipo.RECEITA,
    )
    formas = FormaPagamentoPersonalizada.objects.filter(usuario=request.user, ativa=True)
    return render(request, "finance/cadastros_financeiros.html", {
        "categoria_form": categoria_form,
        "forma_form": forma_form,
        "categorias": categorias,
        "formas": formas,
        "conta_form": conta_form,
        "conta_editando": conta_editando,
        "transferencia_form": transferencia_form,
        "transferencias": TransferenciaFinanceira.objects.filter(
            usuario=request.user,
        ).select_related("conta_origem", "conta_destino"),
        "contas": ContaFinanceira.objects.filter(usuario=request.user),
        "categoria_editando": categoria_editando,
        "forma_editando": forma_editando,
    })


@login_required
@principal_required
def conta_financeira_excluir(request, pk):
    conta = get_object_or_404(ContaFinanceira, pk=pk, usuario=request.user)
    if request.method == "POST":
        if conta.saldo != Decimal("0.00"):
            messages.error(request, "So e permitido excluir uma conta com saldo zerado.")
        else:
            conta.delete()
            messages.success(request, "Conta financeira excluida.")
    return redirect("cadastros_financeiros")


@login_required
@principal_required
def transferencia_financeira_excluir(request, pk):
    transferencia = get_object_or_404(
        TransferenciaFinanceira,
        pk=pk,
        usuario=request.user,
    )
    if request.method == "POST":
        transferencia.delete()
        messages.success(request, "Transferencia excluida e saldos recalculados.")
    return redirect("cadastros_financeiros")


@login_required
@principal_required
def categoria_excluir(request, pk):
    categoria = get_object_or_404(
        CategoriaDespesa,
        pk=pk,
        usuario=request.user,
        tipo=CategoriaDespesa.Tipo.RECEITA,
    )
    if request.method == "POST":
        categoria.delete()
        messages.success(request, "Categoria excluida.")
    return redirect("cadastros_financeiros")


@login_required
@principal_required
def forma_pagamento_excluir(request, pk):
    forma = get_object_or_404(FormaPagamentoPersonalizada, pk=pk, usuario=request.user)
    if request.method == "POST":
        forma.delete()
        messages.success(request, "Forma de pagamento excluida.")
    return redirect("cadastros_financeiros")


@login_required
def transacao_nova(request):
    conta_principal = usuario_da_conta(request.user)
    if request.method == "POST":
        form = TransacaoForm(request.POST, usuario=conta_principal)
        if form.is_valid():
            transacao = form.save(commit=False)
            transacao.usuario = conta_principal
            transacao.criado_por = request.user
            transacao.save()
            messages.success(request, "Transacao adicionada com sucesso.")
            return redirect("dashboard")
    else:
        form = TransacaoForm(usuario=conta_principal)
    return render(request, "finance/transacao_form.html", {"form": form, "titulo": "Nova transacao"})


@login_required
@principal_required
def transacao_editar(request, pk):
    transacao = get_object_or_404(Transacao, pk=pk, usuario=request.user)
    if request.method == "POST":
        form = TransacaoForm(request.POST, instance=transacao, usuario=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, "Transacao atualizada com sucesso.")
            return redirect("dashboard")
    else:
        form = TransacaoForm(instance=transacao, usuario=request.user)
    return render(request, "finance/transacao_form.html", {"form": form, "titulo": "Editar transacao"})


@login_required
@principal_required
def transacao_excluir(request, pk):
    transacao = get_object_or_404(Transacao, pk=pk, usuario=request.user)
    if request.method == "POST":
        transacao.delete()
        messages.success(request, "Transacao excluida.")
    return redirect("dashboard")


@login_required
@principal_required
def transacao_concluir(request, pk):
    transacao = get_object_or_404(Transacao, pk=pk, usuario=request.user)
    if request.method == "GET" and transacao.status != Transacao.Status.CONCLUIDO:
        return render(
            request,
            "finance/baixa_despesa.html",
            {
                "transacao": transacao,
                "form": BaixaDespesaForm(usuario=request.user, transacao=transacao),
                "contas_disponiveis": ContaFinanceira.objects.filter(usuario=request.user).exists(),
            },
        )
    if request.method == "POST" and transacao.status != Transacao.Status.CONCLUIDO:
        form = BaixaDespesaForm(request.POST, usuario=request.user, transacao=transacao)
        if not form.is_valid():
            return render(
                request,
                "finance/baixa_despesa.html",
                {
                    "transacao": transacao,
                    "form": form,
                    "contas_disponiveis": ContaFinanceira.objects.filter(usuario=request.user).exists(),
                },
                status=400,
            )
        conta_origem = form.cleaned_data["conta_origem"]
        transacao.registrar_pagamento(conta_origem=conta_origem)
        if transacao.tipo == Transacao.Tipo.RECEITA:
            mensagem = f"Recebimento confirmado na conta {conta_origem.nome}."
        elif transacao.parcelado and transacao.status != Transacao.Status.CONCLUIDO:
            mensagem = f"Parcela registrada pela conta {conta_origem.nome}. Restam {transacao.parcelas_restantes}."
        else:
            mensagem = f"Despesa concluida pela conta {conta_origem.nome}."
        messages.success(request, mensagem)
    return redirect("dashboard")
