"""WhatsApp (Z-API ou Cloud API da Meta) e SMS (Twilio).

Notas para activar:
- WhatsApp por Z-API (WHATSAPP_PROVEDOR=zapi): ZAPI_INSTANCE_ID, ZAPI_TOKEN e ZAPI_CLIENT_TOKEN, do painel da Z-API.
  A Z-API não é a API oficial do WhatsApp (liga um telemóvel pelo WhatsApp Web): envios em massa podem levar a
  Meta a bloquear o número. Use um número próprio da escola e evite enviar centenas de mensagens seguidas.
- WhatsApp oficial (WHATSAPP_PROVEDOR=meta): a Meta só deixa a empresa iniciar conversa com um MODELO (template) aprovado. Fora da janela de 24 h
  desde a última mensagem do encarregado, o texto livre é recusado. Defina WHATSAPP_TEMPLATE com um modelo aprovado
  que tenha uma variável {{1}}; o corpo da mensagem vai nessa variável.
- SMS: o Twilio cobra por mensagem e precisa de um número ou remetente autorizado para Angola (TWILIO_FROM).
"""
import logging
import re
import uuid

import httpx

from app.config import get_settings
from app.integracoes import ErroIntegracao

log = logging.getLogger("clacs.mensagens")


def normalizar_telefone(numero: str) -> str:
    """'+244 923 000 111' / '923000111' → '244923000111' (formato internacional sem '+')."""
    digitos = re.sub(r"\D", "", numero or "")
    if len(digitos) == 9 and digitos.startswith("9"):  # número angolano sem indicativo
        digitos = "244" + digitos
    if len(digitos) < 9:
        raise ErroIntegracao(f"Número de telefone inválido: {numero!r}")
    return digitos


def enviar_whatsapp(numero: str, texto: str) -> str:
    if get_settings().whatsapp_provedor == "zapi":
        return _whatsapp_zapi(numero, texto)
    return _whatsapp_meta(numero, texto)


def _whatsapp_zapi(numero: str, texto: str) -> str:
    s = get_settings()
    destino = normalizar_telefone(numero)
    if not (s.zapi_instance_id and s.zapi_token):
        log.info("[whatsapp z-api simulado] para %s: %s", destino, texto)
        return f"sim-wa-{uuid.uuid4().hex[:12]}"
    url = f"{s.zapi_url.rstrip('/')}/instances/{s.zapi_instance_id}/token/{s.zapi_token}/send-text"
    cab = {"Client-Token": s.zapi_client_token} if s.zapi_client_token else {}
    try:
        r = httpx.post(url, json={"phone": destino, "message": texto[:4096]}, headers=cab, timeout=20)
    except httpx.HTTPError as e:
        raise ErroIntegracao(f"Z-API indisponível: {e}") from e
    if r.status_code >= 400:
        raise ErroIntegracao(f"Z-API recusou ({r.status_code}): {r.text[:300]}")
    dados = r.json() if r.content else {}
    return str(dados.get("messageId") or dados.get("zaapId") or dados.get("id") or "")


def _whatsapp_meta(numero: str, texto: str) -> str:
    s = get_settings()
    destino = normalizar_telefone(numero)
    if not (s.whatsapp_token and s.whatsapp_phone_id):
        log.info("[whatsapp simulado] para %s: %s", destino, texto)
        return f"sim-wa-{uuid.uuid4().hex[:12]}"
    if s.whatsapp_template:
        corpo = {"messaging_product": "whatsapp", "to": destino, "type": "template",
                 "template": {"name": s.whatsapp_template, "language": {"code": s.whatsapp_template_lingua},
                              "components": [{"type": "body", "parameters": [{"type": "text", "text": texto[:1000]}]}]}}
    else:
        corpo = {"messaging_product": "whatsapp", "to": destino, "type": "text", "text": {"body": texto[:4096]}}
    url = f"https://graph.facebook.com/{s.whatsapp_api_versao}/{s.whatsapp_phone_id}/messages"
    try:
        r = httpx.post(url, json=corpo, headers={"Authorization": f"Bearer {s.whatsapp_token}"}, timeout=20)
    except httpx.HTTPError as e:
        raise ErroIntegracao(f"WhatsApp indisponível: {e}") from e
    if r.status_code >= 400:
        raise ErroIntegracao(f"WhatsApp recusou ({r.status_code}): {r.text[:300]}")
    return r.json().get("messages", [{}])[0].get("id", "")


def enviar_sms(numero: str, texto: str) -> str:
    s = get_settings()
    destino = "+" + normalizar_telefone(numero)
    if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from):
        log.info("[sms simulado] para %s: %s", destino, texto)
        return f"sim-sms-{uuid.uuid4().hex[:12]}"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json"
    try:
        r = httpx.post(url, data={"To": destino, "From": s.twilio_from, "Body": texto[:1600]},
                       auth=(s.twilio_account_sid, s.twilio_auth_token), timeout=20)
    except httpx.HTTPError as e:
        raise ErroIntegracao(f"SMS indisponível: {e}") from e
    if r.status_code >= 400:
        raise ErroIntegracao(f"SMS recusado ({r.status_code}): {r.text[:300]}")
    return r.json().get("sid", "")
