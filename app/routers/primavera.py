"""Integração Primavera. O Primavera mantém a facturação legal e a ligação à AGT.

A API real do Primavera (versão e credenciais) ainda tem de ser validada com o cliente; até lá,
a sincronização é simulada e só regista o log. As tabelas de mapeamento e divergências já estão prontas.
"""
from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import respostas as R
from app.common import aplicar, as_dict, obter
from app.database import get_db
from app.models import (
    Cobranca, DocumentoFiscal, IntegracaoConfig, IntegracaoDivergencia, IntegracaoSyncLog, MetodoPagamento,
)
from app.schemas import IntegracaoIn
from app.integracoes import ErroIntegracao, primavera
from app.security import Contexto, auditar, exige
from app.services.financeiro import registar_pagamento, valor_em_aberto

router = APIRouter(prefix="/api/primavera", tags=["Integração Primavera"])
ver, editar = exige("primavera"), exige("primavera", "editar")


def _config(db: Session, est: int) -> IntegracaoConfig:
    c = db.scalar(select(IntegracaoConfig).where(IntegracaoConfig.estabelecimento_id == est,
                                                 IntegracaoConfig.sistema == "primavera"))
    if not c:
        c = IntegracaoConfig(estabelecimento_id=est, sistema="primavera", activo=False)
        db.add(c)
        db.flush()
    return c


@router.get("", response_model=R.EstadoPrimavera, summary="Estado da sincronização")
def estado(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    cfg = _config(db, est)
    db.commit()
    ultima = db.scalar(select(IntegracaoSyncLog).where(IntegracaoSyncLog.estabelecimento_id == est)
                       .order_by(IntegracaoSyncLog.iniciado_em.desc()).limit(1))
    inicio_dia = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    hoje = db.scalar(select(func.coalesce(func.sum(IntegracaoSyncLog.total_registos), 0)).where(
        IntegracaoSyncLog.estabelecimento_id == est, IntegracaoSyncLog.iniciado_em >= inicio_dia))
    abertas = db.scalar(select(func.count()).select_from(IntegracaoDivergencia).where(
        IntegracaoDivergencia.estabelecimento_id == est, IntegracaoDivergencia.estado == "aberta"))
    return {"config": as_dict(cfg), "ultima_sincronizacao": as_dict(ultima) if ultima else None,
            "documentos_hoje": hoje, "divergencias_abertas": abertas, "simulado": not cfg.activo}


@router.put("/config", response_model=R.IntegracaoConfig)
def configurar(dados: IntegracaoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    cfg = _config(db, ctx.estabelecimento_id)
    aplicar(cfg, dados.model_dump(exclude_unset=True))
    auditar(db, ctx, "integracao_config", cfg.id, "update", dados.model_dump(exclude_unset=True))
    db.commit()
    return as_dict(cfg)


@router.post("/sincronizar", response_model=R.IntegracaoSyncLog, summary="Sincroniza clientes e documentos (simulado enquanto a integração estiver inactiva)")
def sincronizar(ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    _config(db, ctx.estabelecimento_id)
    try:
        reg = primavera.sincronizar(db, ctx.estabelecimento_id)
    except ErroIntegracao as e:
        raise HTTPException(502, str(e))
    auditar(db, ctx, "integracao_sync_log", reg.id, "insert", {"estado": reg.estado, "registos": reg.total_registos})
    db.commit()
    return as_dict(reg)


@router.get("/divergencias", response_model=list[R.IntegracaoDivergencia])
def divergencias(estado: str | None = "aberta", ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(IntegracaoDivergencia).where(IntegracaoDivergencia.estabelecimento_id == ctx.estabelecimento_id)
    if estado:
        stmt = stmt.where(IntegracaoDivergencia.estado == estado)
    return [as_dict(d) for d in db.scalars(stmt.order_by(IntegracaoDivergencia.detectada_em.desc()))]


@router.post("/divergencias/{did}/{accao}", response_model=R.IntegracaoDivergencia, summary="Resolver ou ignorar uma divergência")
def tratar(did: int, accao: str, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    if accao not in ("resolver", "ignorar"):
        raise HTTPException(422, "Acção inválida: use resolver ou ignorar")
    d = obter(db, IntegracaoDivergencia, did, ctx.estabelecimento_id, "Divergência")
    if d.estado != "aberta":
        raise HTTPException(409, "A divergência já foi tratada")
    d.estado = "resolvida" if accao == "resolver" else "ignorada"
    d.resolvida_por, d.resolvida_em = ctx.id, datetime.now(timezone.utc)
    # Cobrança paga no Primavera e pendente na escola: regista o pagamento com a referência do documento.
    if accao == "resolver" and d.entidade == "cobranca" and d.id_local and d.valor_externo == "paga":
        c = db.get(Cobranca, d.id_local)
        if c and c.estabelecimento_id == ctx.estabelecimento_id and c.estado in ("pendente", "parcial"):
            metodo = db.scalar(select(MetodoPagamento).where(MetodoPagamento.estabelecimento_id == ctx.estabelecimento_id,
                                                             MetodoPagamento.activo).order_by(MetodoPagamento.id))
            if metodo is None:
                raise HTTPException(409, "Defina um método de pagamento antes de resolver")
            registar_pagamento(db, ctx.estabelecimento_id, ctx.id, metodo_id=metodo.id,
                               valor=valor_em_aberto(db, c), cobranca_ids=[c.id], referencia=d.id_externo)
    auditar(db, ctx, "integracao_divergencia", did, "update", {"estado": d.estado})
    db.commit()
    return as_dict(d)


@router.get("/documentos", response_model=list[R.DocumentoFiscal])
def documentos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(d) for d in db.scalars(select(DocumentoFiscal).where(
        DocumentoFiscal.estabelecimento_id == ctx.estabelecimento_id).order_by(DocumentoFiscal.data_emissao.desc()).limit(500))]
