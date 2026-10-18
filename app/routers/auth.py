import hashlib
import logging
from datetime import datetime, timedelta, timezone

import jwt
import pyotp
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import limite_login
from app.common import as_dict, obter
from app.config import get_settings
from app.database import get_db
from app.integracoes import ErroIntegracao
from app.integracoes.email import enviar_email
from app.models import Estabelecimento, Perfil, Utilizador, UtilizadorPerfil
from app.schemas import CodigoIn, RecuperarIn, RedefinirIn, SenhaIn, UtilizadorIn, UtilizadorUpd
from app.security import (
    Contexto, auditar, carregar_contexto, contexto_actual, criar_token, exige, hash_senha, verificar_senha,
)

router = APIRouter(prefix="/api", tags=["Autenticação e utilizadores"])


def _perfil_ids(db: Session, codigos: list[str]) -> list[int]:
    perfis = dict(db.execute(select(Perfil.codigo, Perfil.id).where(Perfil.codigo.in_(codigos))).tuples().all())
    em_falta = set(codigos) - set(perfis)
    if em_falta:
        raise HTTPException(422, f"Perfis desconhecidos: {', '.join(sorted(em_falta))}")
    return list(perfis.values())


def _utilizador_out(db: Session, u: Utilizador, est: int) -> dict:
    d = as_dict(u)
    d.pop("senha_hash")
    d.pop("doisfa_segredo", None)
    d["perfis"] = list(db.scalars(select(Perfil.codigo).join(UtilizadorPerfil, UtilizadorPerfil.perfil_id == Perfil.id)
                                  .where(UtilizadorPerfil.utilizador_id == u.id,
                                         UtilizadorPerfil.estabelecimento_id == est)))
    return d


def _hash_curto(senha_hash: str) -> str:
    return hashlib.sha256(senha_hash.encode()).hexdigest()[:16]


@router.post("/auth/login", summary="Iniciar sessão (username = e-mail; codigo = verificação em dois passos)")
def login(request: Request, username: str = Form(...), password: str = Form(...), codigo: str | None = Form(None),
          db: Session = Depends(get_db)):
    email = username.strip().lower()
    chaves = (f"email:{email}", f"ip:{request.client.host if request.client else '-'}")
    espera = limite_login.bloqueado(*chaves)
    if espera:
        raise HTTPException(429, f"Demasiadas tentativas falhadas. Tente de novo dentro de {espera // 60 + 1} minuto(s).",
                            headers={"Retry-After": str(espera)})
    u = db.scalar(select(Utilizador).where(func.lower(Utilizador.email) == email))
    if not u or not u.activo or not verificar_senha(password, u.senha_hash):
        limite_login.registar_falha(*chaves)
        raise HTTPException(401, "E-mail ou senha incorrectos")
    if u.doisfa_activo:
        if not codigo:
            raise HTTPException(401, "Indique o código de verificação da aplicação autenticadora",
                                headers={"X-2FA": "required"})
        if not pyotp.TOTP(u.doisfa_segredo or "").verify(codigo.strip(), valid_window=1):
            limite_login.registar_falha(*chaves)
            raise HTTPException(401, "Código de verificação incorrecto", headers={"X-2FA": "required"})
    est = db.scalar(select(func.min(UtilizadorPerfil.estabelecimento_id)).where(UtilizadorPerfil.utilizador_id == u.id))
    if est is None:
        raise HTTPException(403, "Utilizador sem perfil atribuído")
    limite_login.limpar(f"email:{email}")
    u.ultimo_login = datetime.now(timezone.utc)
    ctx = carregar_contexto(db, u, est)
    auditar(db, ctx, "utilizador", u.id, "login")
    db.commit()
    return {"access_token": criar_token(u.id, est), "token_type": "bearer"}


@router.post("/auth/recuperar", status_code=204, summary="Envia por e-mail um link para definir nova senha")
def recuperar_senha(dados: RecuperarIn, db: Session = Depends(get_db)):
    # Responde sempre igual, para não revelar que e-mails existem.
    u = db.scalar(select(Utilizador).where(func.lower(Utilizador.email) == dados.email.strip().lower()))
    if not u or not u.activo:
        return
    s = get_settings()
    agora = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(u.id), "tipo": "reset", "fp": _hash_curto(u.senha_hash), "iat": agora,
                        "exp": agora + timedelta(minutes=30)}, s.jwt_secret, algorithm="HS256")
    link = f"{s.frontend_url.rstrip('/')}/redefinir-senha?token={token}"
    try:
        enviar_email(u.email, "CLACS Escola: definir nova senha",
                     f"Olá {u.nome},\n\nPara definir uma nova senha, abra este link (válido 30 minutos):\n{link}\n\n"
                     "Se não pediu esta alteração, ignore este e-mail.")
    except ErroIntegracao:
        logging.getLogger("clacs").exception("Falha a enviar o e-mail de recuperação")


@router.post("/auth/redefinir", status_code=204, summary="Define a nova senha com o token recebido por e-mail")
def redefinir_senha(dados: RedefinirIn, db: Session = Depends(get_db)):
    invalido = HTTPException(400, "O link é inválido ou já expirou. Peça outro.")
    try:
        p = jwt.decode(dados.token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise invalido
    u = db.get(Utilizador, int(p.get("sub", 0))) if p.get("tipo") == "reset" else None
    # O token deixa de valer assim que a senha muda (uso único).
    if not u or p.get("fp") != _hash_curto(u.senha_hash):
        raise invalido
    u.senha_hash = hash_senha(dados.senha_nova)
    auditar(db, None, "utilizador", u.id, "update", {"senha": "redefinida"},
            estabelecimento_id=db.scalar(select(func.min(UtilizadorPerfil.estabelecimento_id))
                                         .where(UtilizadorPerfil.utilizador_id == u.id)))
    db.commit()


@router.post("/auth/2fa/iniciar", summary="Gera o segredo para a aplicação autenticadora (Google Authenticator…)")
def doisfa_iniciar(ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)):
    u = db.get(Utilizador, ctx.id)
    if u.doisfa_activo:
        raise HTTPException(409, "A verificação em dois passos já está activa")
    u.doisfa_segredo = pyotp.random_base32()
    db.commit()
    return {"segredo": u.doisfa_segredo,
            "uri": pyotp.TOTP(u.doisfa_segredo).provisioning_uri(name=u.email, issuer_name="CLACS Escola")}


@router.post("/auth/2fa/activar", status_code=204)
def doisfa_activar(dados: CodigoIn, ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)):
    u = db.get(Utilizador, ctx.id)
    if not u.doisfa_segredo or not pyotp.TOTP(u.doisfa_segredo).verify(dados.codigo, valid_window=1):
        raise HTTPException(400, "Código incorrecto")
    u.doisfa_activo = True
    auditar(db, ctx, "utilizador", u.id, "update", {"doisfa": "activado"})
    db.commit()


@router.post("/auth/2fa/desactivar", status_code=204)
def doisfa_desactivar(dados: CodigoIn, ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)):
    u = db.get(Utilizador, ctx.id)
    if not u.doisfa_activo or not pyotp.TOTP(u.doisfa_segredo or "").verify(dados.codigo, valid_window=1):
        raise HTTPException(400, "Código incorrecto")
    u.doisfa_activo, u.doisfa_segredo = False, None
    auditar(db, ctx, "utilizador", u.id, "update", {"doisfa": "desactivado"})
    db.commit()


@router.get("/auth/me")
def eu(ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)):
    est = db.get(Estabelecimento, ctx.estabelecimento_id)
    return {**_utilizador_out(db, ctx.utilizador, ctx.estabelecimento_id),
            "estabelecimento": {"id": est.id, "nome": est.nome},
            "permissoes": sorted(f"{m}:{a}" for m, a in ctx.permissoes)}


@router.post("/auth/senha", status_code=204)
def alterar_senha(dados: SenhaIn, ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)):
    u = db.get(Utilizador, ctx.id)
    if not verificar_senha(dados.senha_actual, u.senha_hash):
        raise HTTPException(400, "A senha actual não está correcta")
    u.senha_hash = hash_senha(dados.senha_nova)
    db.commit()


@router.get("/perfis")
def perfis(ctx: Contexto = Depends(contexto_actual), db: Session = Depends(get_db)):
    return [as_dict(p) for p in db.scalars(select(Perfil).order_by(Perfil.id))]


@router.get("/utilizadores")
def listar_utilizadores(ctx: Contexto = Depends(exige("utilizadores")), db: Session = Depends(get_db)):
    us = db.scalars(select(Utilizador).join(UtilizadorPerfil, UtilizadorPerfil.utilizador_id == Utilizador.id)
                    .where(UtilizadorPerfil.estabelecimento_id == ctx.estabelecimento_id)
                    .distinct().order_by(Utilizador.nome))
    return [_utilizador_out(db, u, ctx.estabelecimento_id) for u in us]


@router.post("/utilizadores", status_code=201)
def criar_utilizador(dados: UtilizadorIn, ctx: Contexto = Depends(exige("utilizadores", "criar")),
                     db: Session = Depends(get_db)):
    email = dados.email.lower()
    if db.scalar(select(Utilizador.id).where(func.lower(Utilizador.email) == email)):
        raise HTTPException(409, "Já existe um utilizador com este e-mail")
    u = Utilizador(nome=dados.nome, email=email, telefone=dados.telefone, senha_hash=hash_senha(dados.senha),
                   funcionario_id=dados.funcionario_id, encarregado_id=dados.encarregado_id)
    db.add(u)
    db.flush()
    for pid in _perfil_ids(db, dados.perfis):
        db.add(UtilizadorPerfil(utilizador_id=u.id, perfil_id=pid, estabelecimento_id=ctx.estabelecimento_id))
    auditar(db, ctx, "utilizador", u.id, "insert", {"email": email, "perfis": dados.perfis})
    db.commit()
    return _utilizador_out(db, u, ctx.estabelecimento_id)


@router.patch("/utilizadores/{uid}")
def editar_utilizador(uid: int, dados: UtilizadorUpd, ctx: Contexto = Depends(exige("utilizadores", "editar")),
                      db: Session = Depends(get_db)):
    u = obter(db, Utilizador, uid, nome="Utilizador")
    if not db.scalar(select(UtilizadorPerfil.utilizador_id).where(
            UtilizadorPerfil.utilizador_id == uid, UtilizadorPerfil.estabelecimento_id == ctx.estabelecimento_id)):
        raise HTTPException(404, "Utilizador não encontrado")
    d = dados.model_dump(exclude_unset=True)
    if "senha" in d:
        u.senha_hash = hash_senha(d.pop("senha"))
    if "perfis" in d:
        ids = _perfil_ids(db, d.pop("perfis"))
        db.execute(delete(UtilizadorPerfil).where(UtilizadorPerfil.utilizador_id == uid,
                                                  UtilizadorPerfil.estabelecimento_id == ctx.estabelecimento_id))
        for pid in ids:
            db.add(UtilizadorPerfil(utilizador_id=uid, perfil_id=pid, estabelecimento_id=ctx.estabelecimento_id))
    for k, v in d.items():
        setattr(u, k, v)
    auditar(db, ctx, "utilizador", uid, "update", dados.model_dump(exclude_unset=True, exclude={"senha"}))
    db.commit()
    return _utilizador_out(db, u, ctx.estabelecimento_id)
