"""Pagamentos por referência (Multicaixa / gateway).

ESTADO: o fluxo está pronto com um gateway "simulado": a escola gera uma referência, o encarregado paga, e o
gateway avisa a API pelo webhook /api/pagamentos/webhook, que confirma o pagamento e salda as cobranças.
Para ligar um gateway real (ProxyPay, EMIS, um banco…), acrescente uma classe com `criar_referencia` e ajuste
`validar_webhook` ao formato de assinatura desse fornecedor; as credenciais vão para o .env.
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.config import get_settings
from app.integracoes import ErroIntegracao


@dataclass
class Referencia:
    entidade: str
    referencia: str
    valor: Decimal
    expira: date


class GatewaySimulado:
    nome = "simulado"

    def criar_referencia(self, valor: Decimal, descricao: str, dias: int = 15) -> Referencia:
        return Referencia(entidade=get_settings().pagamentos_entidade,
                          referencia="".join(secrets.choice("0123456789") for _ in range(9)),
                          valor=valor, expira=date.today() + timedelta(days=dias))


def gateway():
    nome = get_settings().pagamentos_gateway
    if nome == "simulado":
        return GatewaySimulado()
    raise ErroIntegracao(f"Gateway de pagamentos '{nome}' ainda não implementado.")


def assinatura(corpo: bytes) -> str:
    segredo = get_settings().pagamentos_webhook_segredo
    return hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()


def validar_webhook(corpo: bytes, recebida: str | None) -> None:
    """HMAC-SHA256 do corpo com PAGAMENTOS_WEBHOOK_SEGREDO, no cabeçalho X-Assinatura."""
    if not get_settings().pagamentos_webhook_segredo:
        raise ErroIntegracao("PAGAMENTOS_WEBHOOK_SEGREDO não está definido; o webhook está desligado.")
    if not recebida or not hmac.compare_digest(assinatura(corpo), recebida):
        raise ErroIntegracao("Assinatura do webhook inválida.")
