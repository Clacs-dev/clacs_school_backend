"""Notificações push para a app do encarregado (Firebase Cloud Messaging, API HTTP v1).

Para activar: criar um projecto no Firebase, gerar uma chave de conta de serviço (JSON) e pôr o caminho em
FCM_CREDENCIAIS. A app móvel (ou o browser) regista o seu token em POST /api/portal/dispositivos.
Sem credenciais, as notificações ficam só no registo; a mensagem continua visível no portal.
"""
import json
import logging
import time
from threading import Lock

import httpx
import jwt
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.integracoes import ErroIntegracao
from app.models import DispositivoPush, Utilizador

log = logging.getLogger("clacs.push")
_token: dict = {"valor": None, "expira": 0.0}
_lock = Lock()


def _credenciais() -> dict | None:
    caminho = get_settings().fcm_credenciais
    if not caminho:
        return None
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        raise ErroIntegracao(f"Não foi possível ler FCM_CREDENCIAIS: {e}") from e


def _acesso(cred: dict) -> str:
    """Token OAuth da conta de serviço (válido 1 h, guardado em memória)."""
    with _lock:
        if _token["valor"] and _token["expira"] - 60 > time.time():
            return _token["valor"]
        agora = int(time.time())
        assercao = jwt.encode({"iss": cred["client_email"], "scope": "https://www.googleapis.com/auth/firebase.messaging",
                               "aud": cred.get("token_uri", "https://oauth2.googleapis.com/token"),
                               "iat": agora, "exp": agora + 3600}, cred["private_key"], algorithm="RS256")
        try:
            r = httpx.post(cred.get("token_uri", "https://oauth2.googleapis.com/token"), timeout=20,
                           data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assercao})
        except httpx.HTTPError as e:
            raise ErroIntegracao(f"Google OAuth indisponível: {e}") from e
        if r.status_code >= 400:
            raise ErroIntegracao(f"Google OAuth recusou ({r.status_code}): {r.text[:200]}")
        _token["valor"], _token["expira"] = r.json()["access_token"], time.time() + int(r.json().get("expires_in", 3600))
        return _token["valor"]


def enviar_ao_encarregado(db: Session, encarregado_id: int, titulo: str, corpo: str) -> int:
    """Envia a notificação a todos os dispositivos das contas do portal deste encarregado. Devolve quantos receberam."""
    tokens = db.scalars(select(DispositivoPush.token).join(Utilizador, Utilizador.id == DispositivoPush.utilizador_id)
                        .where(Utilizador.encarregado_id == encarregado_id, Utilizador.activo)).all()
    if not tokens:
        return 0
    cred = _credenciais()
    if not cred:
        log.info("[push simulado] encarregado %s (%s dispositivo(s)): %s", encarregado_id, len(tokens), titulo)
        return len(tokens)
    acesso = _acesso(cred)
    url = f"https://fcm.googleapis.com/v1/projects/{cred['project_id']}/messages:send"
    enviados, invalidos = 0, []
    for t in tokens:
        try:
            r = httpx.post(url, headers={"Authorization": f"Bearer {acesso}"}, timeout=20, json={"message": {
                "token": t, "notification": {"title": titulo[:100], "body": corpo[:500]}}})
        except httpx.HTTPError as e:
            raise ErroIntegracao(f"FCM indisponível: {e}") from e
        if r.status_code in (400, 404) and ("UNREGISTERED" in r.text or "INVALID_ARGUMENT" in r.text):
            invalidos.append(t)  # app desinstalada ou token expirado
        elif r.status_code >= 400:
            raise ErroIntegracao(f"FCM recusou ({r.status_code}): {r.text[:200]}")
        else:
            enviados += 1
    if invalidos:
        db.execute(delete(DispositivoPush).where(DispositivoPush.token.in_(invalidos)))
    return enviados
