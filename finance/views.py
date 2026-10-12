from calendar import monthrange
from datetime import date
from decimal import Decimal
from io import BytesIO
import json
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
from django.utils import timezone

from .forms import (
    AdicionarGuardadoForm,
    CadastroForm,
    CategoriaDespesaForm,
    ClienteForm,
    ConfiguracaoDocumentoForm,
    FormaPagamentoForm,
    PlanejamentoForm,
    ReciboForm,
    TransacaoForm,
)
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
    hoje = date.today()
    inicio_mes = hoje.replace(day=1)
    fim_mes = hoje.replace(day=monthrange(hoje.year, hoje.month)[1])
    transacoes = Transacao.objects.filter(usuario=request.user)
    planejamentos = Planejamento.objects.filter(usuario=request.user)

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

    despesas_categoria = (
        movimentacoes_mes.filter(tipo=Transacao.Tipo.DESPESA, categoria__isnull=False)
        .values("categoria__nome")
        .annotate(total=Sum(valor_pago))
        .order_by("-total")
    )
    maior_categoria = despesas_categoria[0] if despesas_categoria else None
    dicas_organizacao = []
    if maior_categoria:
        percentual = (
            maior_categoria["total"] / total_despesas * 100
            if total_despesas
            else Decimal("0")
        )
        dicas_organizacao.append(
            f"{maior_categoria['categoria__nome']} representa {percentual:.0f}% das suas despesas pagas."
        )
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
        "despesas_categoria": despesas_categoria,
        "maior_categoria": maior_categoria,
        "dicas_organizacao": dicas_organizacao,
        "mes_atual": f"{MESES_PT_BR[hoje.month - 1]} de {hoje.year}",
        "planejamentos": planejamentos[:3],
    }
    return render(request, "finance/dashboard.html", contexto)


@login_required
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
def planejamento_excluir(request, pk):
    planejamento = get_object_or_404(Planejamento, pk=pk, usuario=request.user)
    if request.method == "POST":
        planejamento.delete()
        messages.success(request, "Planejamento excluido.")
    return redirect("planejamentos")


@login_required
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
    )
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
    documento.drawString(margem, y, texto(titulo))
    documento.setFont("Helvetica", 10)
    documento.setFillColorRGB(0.35, 0.38, 0.36)
    documento.drawString(margem, y - 18, texto(f"Periodo: {MESES_PT_BR[mes - 1]} de {ano}"))
    documento.setStrokeColor(cor_principal)
    documento.setLineWidth(1.2)
    documento.line(margem, y - 35, largura - margem, y - 35)
    y -= 70
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
            "id", "cnpj", "razao_social", "nome_fantasia"
        )),
        "recibos": Recibo.objects.filter(criado_por=request.user),
        "configuracao_documento": configuracao_do_usuario(request.user),
    })


@login_required
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
def cliente_excluir(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    cliente = get_object_or_404(Cliente, pk=pk, usuario=request.user)
    if request.method == "POST":
        cliente.delete()
        messages.success(request, "Empresa excluida.")
    return redirect("recibos")


@login_required
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
def recibo_excluir(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    recibo = get_object_or_404(Recibo, pk=pk, criado_por=request.user)
    if request.method == "POST":
        recibo.delete()
        messages.success(request, "Recibo excluido.")
    return redirect("recibos")


@login_required
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
def recibo_pdf(request, pk):
    if not pode_emitir_recibo(request.user):
        return HttpResponseForbidden("Seu usuario ainda nao tem permissao para emitir recibos.")
    recibo = get_object_or_404(Recibo, pk=pk, criado_por=request.user)
    try:
        from reportlab.lib.colors import HexColor
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas
    except ImportError:
        return HttpResponse("Instale reportlab para gerar PDF.", status=500)

    def texto(valor):
        return normalize("NFKD", str(valor)).encode("ascii", "ignore").decode("ascii")

    def linhas_quebradas(valor, fonte, tamanho, largura_maxima):
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

    def desenhar_texto_quebrado(valor, x, y_inicial, largura_maxima, fonte="Helvetica", tamanho=10, entrelinhas=14):
        documento.setFont(fonte, tamanho)
        y_atual = y_inicial
        for linha in linhas_quebradas(valor, fonte, tamanho, largura_maxima):
            documento.drawString(x, y_atual, linha)
            y_atual -= entrelinhas
        return y_atual

    configuracao = configuracao_do_usuario(request.user)
    buffer = BytesIO()
    documento = canvas.Canvas(buffer, pagesize=A4)
    _, altura = A4
    cor_principal = HexColor(configuracao.cor_principal if configuracao else "#1f6b4f")
    y = altura - 60
    if configuracao and configuracao.logo:
        try:
            documento.drawImage(configuracao.logo.path, 45, y - 25, width=75, height=38, preserveAspectRatio=True, anchor="sw")
        except (OSError, AttributeError):
            pass
    documento.setFillColor(cor_principal)
    documento.setFont("Helvetica-Bold", 19)
    titulo = configuracao.titulo_recibo if configuracao else "Recibo de pagamento"
    documento.drawString(135 if configuracao and configuracao.logo else 45, y, texto(titulo))
    y -= 28
    documento.setStrokeColor(cor_principal)
    documento.setLineWidth(1.5)
    documento.line(45, y, 550, y)
    y -= 34
    documento.setFillColorRGB(0.1, 0.13, 0.11)
    documento.setFont("Helvetica-Bold", 10)
    documento.drawString(55, y, "RECIBO")
    documento.setFont("Helvetica", 10)
    documento.drawRightString(540, y, texto(f"N. {recibo.pk:05d}"))
    y -= 34
    documento.setFillColorRGB(0.97, 0.98, 0.97)
    documento.roundRect(45, y - 165, 505, 165, 8, fill=1, stroke=0)
    documento.setFillColorRGB(0.1, 0.13, 0.11)
    documento.setFont("Helvetica-Bold", 10)
    documento.drawString(60, y - 28, "Recebi de")
    documento.drawString(60, y - 62, "Referente a")
    documento.drawString(60, y - 96, "Data do pagamento")
    documento.drawString(60, y - 130, "Status")
    documento.setFont("Helvetica", 11)
    desenhar_texto_quebrado(recibo.recebedor, 175, y - 28, 330, tamanho=10, entrelinhas=13)
    desenhar_texto_quebrado(recibo.servico, 175, y - 62, 330, tamanho=10, entrelinhas=13)
    documento.drawString(175, y - 96, texto(f"{recibo.data_pagamento:%d/%m/%Y}"))
    documento.drawString(175, y - 130, texto(recibo.get_status_display()))
    documento.setFillColor(cor_principal)
    documento.setFont("Helvetica-Bold", 16)
    documento.drawRightString(525, y - 28, texto(f"R$ {moeda(recibo.valor)}"))
    y -= 205
    if recibo.cliente:
        documento.setFillColorRGB(0.1, 0.13, 0.11)
        documento.setFont("Helvetica-Bold", 10)
        documento.drawString(55, y, "Dados do cliente")
        y -= 18
        documento.setFont("Helvetica", 10)
        y = desenhar_texto_quebrado(f"CNPJ: {cnpj_formatado(recibo.cliente.cnpj)}", 55, y, 485, tamanho=10, entrelinhas=14)
        if recibo.cliente.endereco_completo:
            y = desenhar_texto_quebrado(recibo.cliente.endereco_completo, 55, y, 485, tamanho=10, entrelinhas=14)
        y -= 10
    if configuracao and configuracao.mostrar_forma_pagamento and recibo.forma_pagamento:
        documento.setFillColorRGB(0.1, 0.13, 0.11)
        documento.setFont("Helvetica-Bold", 10)
        documento.drawString(55, y, "Forma de pagamento")
        documento.setFont("Helvetica", 11)
        desenhar_texto_quebrado(recibo.forma_pagamento, 175, y, 330, tamanho=10, entrelinhas=14)
        y -= 28
    if configuracao and configuracao.mostrar_observacoes and recibo.observacoes:
        y -= 12
        documento.setStrokeColorRGB(0.85, 0.87, 0.85)
        documento.line(55, y + 10, 540, y + 10)
        documento.setFont("Helvetica-Bold", 11)
        documento.drawString(55, y, "Observacoes")
        y -= 20
        documento.setFont("Helvetica", 10)
        desenhar_texto_quebrado(recibo.observacoes, 55, y, 485, tamanho=10, entrelinhas=14)
    if configuracao and configuracao.rodape:
        documento.setFont("Helvetica", 9)
        documento.setFillColorRGB(0.35, 0.35, 0.35)
        documento.drawString(55, 55, texto(configuracao.rodape)[:120])
    documento.save()
    resposta = HttpResponse(buffer.getvalue(), content_type="application/pdf")
    resposta["Content-Disposition"] = f'attachment; filename="recibo-{recibo.pk}.pdf"'
    return resposta


@login_required
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
def configuracao_documentos(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Apenas administradores podem configurar documentos.")
    configuracao, _ = ConfiguracaoDocumento.objects.get_or_create(usuario=request.user)
    if request.method == "POST":
        tipo = request.POST.get("tipo")
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
def cadastros_financeiros(request):
    categoria_editando = None
    forma_editando = None
    categoria_form = None
    forma_form = None
    if request.method == "POST":
        tipo = request.POST.get("tipo")
        cadastro_id = request.POST.get("cadastro_id")
        if tipo == "categoria":
            categoria_editando = get_object_or_404(
                CategoriaDespesa,
                pk=cadastro_id,
                usuario=request.user,
            ) if cadastro_id else None
            form = CategoriaDespesaForm(request.POST, instance=categoria_editando)
            if form.is_valid():
                categoria = form.save(commit=False)
                categoria.usuario = request.user
                categoria.save()
                mensagem = "Categoria atualizada." if categoria_editando else "Categoria cadastrada."
                messages.success(request, mensagem)
                return redirect("cadastros_financeiros")
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
    else:
        categoria_id = request.GET.get("editar_categoria")
        forma_id = request.GET.get("editar_forma_pagamento")
        if categoria_id:
            categoria_editando = get_object_or_404(
                CategoriaDespesa,
                pk=categoria_id,
                usuario=request.user,
            )
        if forma_id:
            forma_editando = get_object_or_404(
                FormaPagamentoPersonalizada,
                pk=forma_id,
                usuario=request.user,
            )
    if categoria_form is None:
        categoria_form = CategoriaDespesaForm(instance=categoria_editando)
    if forma_form is None:
        forma_form = FormaPagamentoForm(instance=forma_editando)
    categorias = CategoriaDespesa.objects.filter(usuario=request.user, ativa=True)
    formas = FormaPagamentoPersonalizada.objects.filter(usuario=request.user, ativa=True)
    return render(request, "finance/cadastros_financeiros.html", {
        "categoria_form": categoria_form,
        "forma_form": forma_form,
        "categorias": categorias,
        "formas": formas,
        "categoria_editando": categoria_editando,
        "forma_editando": forma_editando,
    })


@login_required
def categoria_excluir(request, pk):
    categoria = get_object_or_404(CategoriaDespesa, pk=pk, usuario=request.user)
    if request.method == "POST":
        categoria.delete()
        messages.success(request, "Categoria excluida.")
    return redirect("cadastros_financeiros")


@login_required
def forma_pagamento_excluir(request, pk):
    forma = get_object_or_404(FormaPagamentoPersonalizada, pk=pk, usuario=request.user)
    if request.method == "POST":
        forma.delete()
        messages.success(request, "Forma de pagamento excluida.")
    return redirect("cadastros_financeiros")


@login_required
def transacao_nova(request):
    if request.method == "POST":
        form = TransacaoForm(request.POST, usuario=request.user)
        if form.is_valid():
            transacao = form.save(commit=False)
            transacao.usuario = request.user
            transacao.save()
            messages.success(request, "Transacao adicionada com sucesso.")
            return redirect("dashboard")
    else:
        form = TransacaoForm(usuario=request.user)
    return render(request, "finance/transacao_form.html", {"form": form, "titulo": "Nova transacao"})


@login_required
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
def transacao_excluir(request, pk):
    transacao = get_object_or_404(Transacao, pk=pk, usuario=request.user)
    if request.method == "POST":
        transacao.delete()
        messages.success(request, "Transacao excluida.")
    return redirect("dashboard")


@login_required
def transacao_concluir(request, pk):
    transacao = get_object_or_404(Transacao, pk=pk, usuario=request.user)
    if request.method == "POST":
        transacao.registrar_pagamento()
        if transacao.tipo == Transacao.Tipo.RECEITA:
            mensagem = "Recebimento confirmado."
        elif transacao.parcelado and transacao.status != Transacao.Status.CONCLUIDO:
            mensagem = f"Parcela registrada. Restam {transacao.parcelas_restantes}."
        else:
            mensagem = "Despesa concluida."
        messages.success(request, mensagem)
    return redirect("dashboard")
