"""Orçamento e despesas: previsto por categoria e mês, despesas registadas, previsão de receitas a partir das
matrículas e dos preços, e a execução (orçamento versus realizado, desvios e tendência mensal)."""
from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import as_dict, dinheiro, obter
from app.database import get_db
from app.models import (
    AdesaoServico, AnoLectivo, Cobranca, Despesa, Matricula, Orcamento, OrcamentoLinha, Pagamento, PagamentoAlocacao,
    Servico, Turma,
)
from app.schemas import DespesaIn, OrcamentoIn, OrcamentoLinhaIn
from app.security import Contexto, auditar, exige
from app.services import financeiro as fin

router = APIRouter(prefix="/api", tags=["Orçamento e despesas"])
ver, criar, editar = exige("propinas"), exige("propinas", "criar"), exige("propinas", "editar")


def _meses(ano: AnoLectivo) -> list[tuple[int, int]]:
    """(ano, mês) de cada mês do ano lectivo."""
    meses, d = [], ano.data_inicio.replace(day=1)
    while d <= ano.data_fim:
        meses.append((d.year, d.month))
        d = date(d.year + d.month // 12, d.month % 12 + 1, 1)
    return meses


@router.get("/orcamentos", response_model=list[R.Orcamento])
def orcamentos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(o) for o in db.scalars(select(Orcamento).where(Orcamento.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(Orcamento.criado_em.desc()))]


@router.post("/orcamentos", response_model=R.Orcamento, status_code=201)
def criar_orcamento(dados: OrcamentoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, AnoLectivo, dados.ano_id, ctx.estabelecimento_id, "Ano lectivo")
    o = Orcamento(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(o)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"Já existe o orçamento «{dados.designacao}» neste ano lectivo") from None
    return as_dict(o)


@router.get("/orcamentos/{oid}/linhas", response_model=list[R.OrcamentoLinha])
def linhas(oid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    obter(db, Orcamento, oid, ctx.estabelecimento_id, "Orçamento")
    return [as_dict(x) for x in db.scalars(select(OrcamentoLinha).where(OrcamentoLinha.orcamento_id == oid)
                                           .order_by(OrcamentoLinha.tipo, OrcamentoLinha.categoria, OrcamentoLinha.mes))]


@router.post("/orcamentos/{oid}/linhas", response_model=R.OrcamentoLinha, status_code=201,
             summary="Valor previsto de uma categoria (receita ou despesa), para um mês ou o ano inteiro")
def criar_linha(oid: int, dados: OrcamentoLinhaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    o = obter(db, Orcamento, oid, ctx.estabelecimento_id, "Orçamento")
    if o.estado == "fechado":
        raise HTTPException(409, "O orçamento está fechado")
    x = OrcamentoLinha(orcamento_id=oid, **dados.model_dump())
    db.add(x)
    auditar(db, ctx, "orcamento_linha", oid, "insert", dados.model_dump(mode="json"))
    db.commit()
    return as_dict(x)


@router.delete("/orcamentos/{oid}/linhas/{lid}", status_code=204)
def apagar_linha(oid: int, lid: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    o = obter(db, Orcamento, oid, ctx.estabelecimento_id, "Orçamento")
    x = db.get(OrcamentoLinha, lid)
    if x is None or x.orcamento_id != oid:
        raise HTTPException(404, "Linha não encontrada")
    if o.estado == "fechado":
        raise HTTPException(409, "O orçamento está fechado")
    db.delete(x)
    db.commit()


@router.get("/orcamentos/{oid}/previsao-receitas", response_model=list[R.PrevisaoReceita],
            summary="Receita prevista por categoria: preços × matrículas (propinas) ou adesões (outros serviços)")
def previsao(oid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    o = obter(db, Orcamento, oid, ctx.estabelecimento_id, "Orçamento")
    ano = db.get(AnoLectivo, o.ano_id)
    n_meses = len(_meses(ano))
    matriculas = db.execute(select(Matricula.id, Turma.classe_id).join(Turma, Turma.id == Matricula.turma_id).where(
        Matricula.ano_id == ano.id, Matricula.estado == "activa")).all()
    por_categoria: dict[str, dict] = defaultdict(lambda: {"alunos": 0, "valor": Decimal(0)})
    for s in db.scalars(select(Servico).where(Servico.estabelecimento_id == ctx.estabelecimento_id, Servico.activo)):
        vezes = {"mensal": n_meses, "trimestral": 3, "anual": 1, "unica": 1}[s.periodicidade]
        if s.categoria == "propina":
            alvo = matriculas
        else:
            aderentes = set(db.scalars(select(AdesaoServico.matricula_id).where(
                AdesaoServico.servico_id == s.id, AdesaoServico.estado == "activa")))
            alvo = [m for m in matriculas if m.id in aderentes]
        for m in alvo:
            p = fin.preco(db, s.id, ano.id, m.classe_id)
            if p:
                por_categoria[s.categoria]["alunos"] += 1
                por_categoria[s.categoria]["valor"] += p * vezes
    return [{"categoria": c, "alunos": v["alunos"], "valor": dinheiro(v["valor"])} for c, v in sorted(por_categoria.items())]


@router.get("/orcamentos/{oid}/execucao", response_model=R.ExecucaoOrcamento,
            summary="Previsto versus realizado por categoria (receitas recebidas, despesas registadas), desvios e meses")
def execucao(oid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    o = obter(db, Orcamento, oid, ctx.estabelecimento_id, "Orçamento")
    ano = db.get(AnoLectivo, o.ano_id)
    meses = _meses(ano)
    previsto: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    previsto_mes: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    for x in db.scalars(select(OrcamentoLinha).where(OrcamentoLinha.orcamento_id == oid)):
        previsto[(x.tipo, x.categoria)] += x.valor
        if x.mes:
            previsto_mes[(x.tipo, x.mes)] += x.valor
        else:
            for _, m in meses:
                previsto_mes[(x.tipo, m)] += x.valor / len(meses)
    realizado: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    realizado_mes: dict[tuple[str, int], Decimal] = defaultdict(Decimal)
    receitas = db.execute(select(Servico.categoria, Pagamento.data_pagamento, PagamentoAlocacao.valor)
                          .join(Pagamento, Pagamento.id == PagamentoAlocacao.pagamento_id)
                          .join(Cobranca, Cobranca.id == PagamentoAlocacao.cobranca_id)
                          .join(Servico, Servico.id == Cobranca.servico_id)
                          .where(Pagamento.estabelecimento_id == ctx.estabelecimento_id, Pagamento.estado == "confirmado",
                                 Pagamento.data_pagamento.between(ano.data_inicio, ano.data_fim)))
    for categoria, dia, valor in receitas:
        realizado[("receita", categoria)] += valor
        realizado_mes[("receita", dia.month)] += valor
    for d in db.scalars(select(Despesa).where(Despesa.estabelecimento_id == ctx.estabelecimento_id,
                                              Despesa.data.between(ano.data_inicio, ano.data_fim))):
        realizado[("despesa", d.categoria)] += d.valor
        realizado_mes[("despesa", d.data.month)] += d.valor
    categorias = []
    for tipo, categoria in sorted(set(previsto) | set(realizado)):
        p, r = previsto[(tipo, categoria)], realizado[(tipo, categoria)]
        categorias.append({"tipo": tipo, "categoria": categoria, "previsto": dinheiro(p), "realizado": dinheiro(r),
                           "desvio": dinheiro(r - p), "execucao": float(r / p * 100) if p else None})
    serie = [{"ano": a, "mes": m, **{f"{k}_{t}": dinheiro(v[(t, m)]) for t in ("receita", "despesa")
                                       for k, v in (("previsto", previsto_mes), ("realizado", realizado_mes))}}
             for a, m in meses]
    tot = lambda tipo, d: dinheiro(sum((v for (t, _), v in d.items() if t == tipo), Decimal(0)))  # noqa: E731
    return {"orcamento": as_dict(o), "categorias": categorias, "meses": serie,
            "receita_prevista": tot("receita", previsto), "receita_realizada": tot("receita", realizado),
            "despesa_prevista": tot("despesa", previsto), "despesa_realizada": tot("despesa", realizado)}


@router.get("/despesas", response_model=list[R.Despesa])
def despesas(desde: date | None = None, ate: date | None = None, categoria: str | None = None,
             ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(Despesa).where(Despesa.estabelecimento_id == ctx.estabelecimento_id)
    if desde:
        stmt = stmt.where(Despesa.data >= desde)
    if ate:
        stmt = stmt.where(Despesa.data <= ate)
    if categoria:
        stmt = stmt.where(Despesa.categoria == categoria)
    return [as_dict(d) for d in db.scalars(stmt.order_by(Despesa.data.desc()).limit(1000))]


@router.post("/despesas", response_model=R.Despesa, status_code=201)
def criar_despesa(dados: DespesaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    d = Despesa(estabelecimento_id=ctx.estabelecimento_id, registada_por=ctx.id, **dados.model_dump())
    db.add(d)
    auditar(db, ctx, "despesa", None, "insert", dados.model_dump(mode="json"))
    db.commit()
    return as_dict(d)


@router.get("/despesas/categorias", response_model=list[R.TotalCategoria], summary="Total de despesas por categoria")
def categorias(desde: date | None = None, ate: date | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = select(Despesa.categoria, func.sum(Despesa.valor), func.count()).where(
        Despesa.estabelecimento_id == ctx.estabelecimento_id)
    if desde:
        stmt = stmt.where(Despesa.data >= desde)
    if ate:
        stmt = stmt.where(Despesa.data <= ate)
    return [{"categoria": c, "total": dinheiro(t), "registos": n} for c, t, n in db.execute(stmt.group_by(Despesa.categoria))]


crud.registar(router, "/orcamentos", Orcamento, nome="Orçamento", modulo="propinas", resposta=R.Orcamento,
              entrada=OrcamentoIn, excluir=("ano_id",))
crud.registar(router, "/despesas", Despesa, nome="Despesa", modulo="propinas", resposta=R.Despesa, entrada=DespesaIn)
