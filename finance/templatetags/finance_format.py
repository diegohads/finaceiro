from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django import template

register = template.Library()


def formatar_moeda(valor):
    try:
        quantizado = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return "0,00"
    formatado = f"{quantizado:,.2f}"
    return formatado.replace(",", "X").replace(".", ",").replace("X", ".")


@register.filter(name="moeda")
def moeda(valor):
    return formatar_moeda(valor)


@register.filter(name="localize")
def localize_moeda(valor):
    return formatar_moeda(valor)
