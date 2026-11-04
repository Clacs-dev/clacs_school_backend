"""Acordos de pagamento (dívidas em prestações), modelos de acordo e isenção de multa autorizada."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import as_dict, obter
from app.database import get_db
from app.models import AcordoModelo, AcordoPagamento, Cobranca
from app.schemas import AcordoIn, AcordoModeloIn, IsencaoMultaIn, MotivoIn, PlanoAcordoIn
from app.security import Contexto, auditar, exige
from app.services import acordos as svc

router = APIRouter(prefix="/api", tags=["Propinas e cobranças"])
ver, criar, editar = exige("propinas"), exige("propinas", "criar"), exige("propinas", "editar")
# Aprovar acordos e isentar multas são excepções: Direcção (ou administrador).
APROVADORES = {"admin", "direccao"}


def _aprovador(ctx: Contexto) -> None:
    if not ctx.perfis & APROVADORES:
        raise HTTPException(403, "Só a Direcção aprova acordos e excepções")


@router.get("/acordos-modelos", response_model=list[R.AcordoModelo])
def modelos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(m) for m in db.scalars(select(AcordoModelo).where(AcordoModelo.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/acordos-modelos", response_model=R.AcordoModelo, status_code=201, summary="Modelo reutilizável de acordo")
def criar_modelo(dados: AcordoModeloIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    m = AcordoModelo(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(m)
    db.commit()
    return as_dict(m)


@router.get("/acordos", response_model=list[R.AcordoDetalhe],
            summary="Acordos de pagamento (incumprimento=true: com prestações vencidas por pagar)")
def listar(estado: str | None = None, encarregado_id: int | None = None, incumprimento: bool = False,
           ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(AcordoPagamento).where(AcordoPagamento.estabelecimento_id == ctx.estabelecimento_id)
    if estado:
        stmt = stmt.where(AcordoPagamento.estado == estado)
    if encarregado_id:
        stmt = stmt.where(AcordoPagamento.encarregado_id == encarregado_id)
    linhas = [svc.detalhe(db, a) for a in db.scalars(stmt.order_by(AcordoPagamento.criado_em.desc()))]
    db.commit()   # acordos que passaram a cumpridos
    return [x for x in linhas if x["incumprimento"]] if incumprimento else linhas


@router.post("/acordos", response_model=R.AcordoDetalhe, status_code=201,
             summary="Propõe um acordo: dívidas em prestações (por modelo ou personalizado); a Direcção aprova")
def criar_acordo(dados: AcordoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    a = svc.criar(db, ctx.estabelecimento_id, ctx.utilizador, **dados.model_dump())
    auditar(db, ctx, "acordo_pagamento", a.id, "insert", {"cobrancas": dados.cobranca_ids, "valor": float(a.valor_total)})
    db.commit()
    return svc.detalhe(db, a)


@router.get("/acordos/{aid}", response_model=R.AcordoDetalhe, summary="Acordo com prestações, pago, saldo e incumprimento")
def ver_acordo(aid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    a = obter(db, AcordoPagamento, aid, ctx.estabelecimento_id, "Acordo")
    d = svc.detalhe(db, a)
    db.commit()
    return d


@router.post("/acordos/{aid}/aprovar", response_model=R.AcordoDetalhe, summary="A Direcção aprova: o acordo fica activo")
def aprovar(aid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    _aprovador(ctx)
    a = obter(db, AcordoPagamento, aid, ctx.estabelecimento_id, "Acordo")
    if a.estado != "proposto":
        raise HTTPException(409, f"O acordo está {a.estado}")
    a.estado, a.aprovado_por, a.aprovado_em = "activo", ctx.id, datetime.now(timezone.utc)
    svc._historico(a, ctx.utilizador.nome, "Aprovado")
    auditar(db, ctx, "acordo_pagamento", aid, "update", {"estado": "activo"})
    db.commit()
    return svc.detalhe(db, a)


@router.put("/acordos/{aid}/prestacoes", response_model=R.AcordoDetalhe,
            summary="Altera o plano de prestações (a soma mantém-se); a alteração fica no histórico")
def alterar(aid: int, dados: PlanoAcordoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    _aprovador(ctx)
    a = obter(db, AcordoPagamento, aid, ctx.estabelecimento_id, "Acordo")
    svc.alterar_plano(db, a, ctx.utilizador, [(p.valor, p.vencimento) for p in dados.prestacoes], dados.motivo)
    auditar(db, ctx, "acordo_pagamento", aid, "update", {"plano": len(dados.prestacoes), "motivo": dados.motivo})
    db.commit()
    return svc.detalhe(db, a)


@router.post("/acordos/{aid}/cancelar", response_model=R.AcordoDetalhe,
             summary="Cancela o acordo; as cobranças voltam ao normal (e às multas)")
def cancelar(aid: int, dados: MotivoIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    a = obter(db, AcordoPagamento, aid, ctx.estabelecimento_id, "Acordo")
    if a.estado not in svc.VIGENTES:
        raise HTTPException(409, f"O acordo está {a.estado}")
    a.estado = "cancelado"
    svc._historico(a, ctx.utilizador.nome, f"Cancelado: {dados.motivo}")
    auditar(db, ctx, "acordo_pagamento", aid, "update", {"estado": "cancelado", "motivo": dados.motivo})
    db.commit()
    return svc.detalhe(db, a)


@router.post("/cobrancas/{cid}/isentar-multa", response_model=R.Cobranca,
             summary="Excepção autorizada pela Direcção: retira a multa e impede novas, com a justificação")
def isentar_multa(cid: int, dados: IsencaoMultaIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    _aprovador(ctx)
    c = obter(db, Cobranca, cid, ctx.estabelecimento_id, "Cobrança")
    antes = float(c.valor_multa)
    c.valor_multa, c.multa_isenta, c.multa_isencao_motivo, c.multa_isencao_por = 0, True, dados.motivo, ctx.id
    auditar(db, ctx, "cobranca", cid, "update", {"multa_isenta": True, "motivo": dados.motivo}, {"valor_multa": antes})
    db.commit()
    db.refresh(c)
    return as_dict(c)


crud.registar(router, "/acordos-modelos", AcordoModelo, nome="Modelo de acordo", modulo="propinas",
              resposta=R.AcordoModelo, entrada=AcordoModeloIn)
