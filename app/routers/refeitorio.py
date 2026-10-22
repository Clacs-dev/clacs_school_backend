from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import ano_activo, aplicar, as_dict, obter, soma_meses
from app.database import get_db
from app.models import (
    AdesaoServico, Aluno, Cobranca, Ementa, Matricula, RefeicaoRegisto, RefeicaoTipo, Servico, Turma,
)
from app.schemas import AdesaoIn, AdesaoUpd, EmentaIn, RefeicaoRegistoIn, RefeicaoTipoIn
from app.security import Contexto, exige
from app.services.financeiro import actualizar_estado, cobrancas_detalhe, encarregado_financeiro

router = APIRouter(prefix="/api/refeitorio", tags=["Refeitório e serviços"])
ver, criar, editar = exige("refeitorio"), exige("refeitorio", "criar"), exige("refeitorio", "editar")
CATEGORIAS_REFEITORIO = ("alimentacao", "refeitorio")


@router.get("", response_model=R.ResumoRefeitorio, summary="Quem paga alimentação, quem paga só a utilização e o estado de cada um")
def resumo(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano = ano_activo(db, est)
    rows = db.execute(
        select(AdesaoServico, Servico, Aluno.id, Aluno.nome_completo, Turma.designacao)
        .join(Servico, Servico.id == AdesaoServico.servico_id)
        .join(Matricula, Matricula.id == AdesaoServico.matricula_id)
        .join(Aluno, Aluno.id == Matricula.aluno_id).join(Turma, Turma.id == Matricula.turma_id)
        .where(Matricula.ano_id == ano.id, Matricula.estado == "activa", AdesaoServico.estado == "activa",
               Servico.categoria.in_(CATEGORIAS_REFEITORIO))
        .order_by(Turma.designacao, Aluno.nome_completo)).all()
    ultimas = {}
    for x in cobrancas_detalhe(db, est):
        if x["categoria"] in CATEGORIAS_REFEITORIO:
            ultimas[(x["aluno_id"], x["servico_id"])] = x  # ordenado por competência: fica a mais recente
    alunos_activos = db.scalar(select(func.count()).select_from(Matricula).where(
        Matricula.estabelecimento_id == est, Matricula.ano_id == ano.id, Matricula.estado == "activa"))
    linhas = []
    for ad, s, aid, nome, turma in rows:
        c = ultimas.get((aid, s.id))
        linhas.append({"adesao_id": ad.id, "aluno_id": aid, "aluno": nome, "turma": turma, "servico_id": s.id,
                       "servico": s.nome, "categoria": s.categoria, "modalidade": ad.modalidade,
                       "valor": c["valor_total"] if c else None, "estado": c["estado"] if c else None,
                       "dias_atraso": c["dias_atraso"] if c else 0, "cobranca_id": c["cobranca_id"] if c else None})
    alimentacao = len({l["aluno_id"] for l in linhas if l["categoria"] == "alimentacao"})
    so_utilizacao = len({l["aluno_id"] for l in linhas if l["categoria"] == "refeitorio"})
    return {"totais": {"alimentacao": alimentacao, "so_utilizacao": so_utilizacao,
                       "nenhum": max(0, alunos_activos - len({l["aluno_id"] for l in linhas}))},
            "linhas": linhas}


@router.post("/adesoes", response_model=R.AdesaoServico, status_code=201)
def aderir(dados: AdesaoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano = ano_activo(db, est)
    m = db.scalar(select(Matricula).where(Matricula.aluno_id == dados.aluno_id, Matricula.ano_id == ano.id,
                                          Matricula.estado == "activa", Matricula.estabelecimento_id == est))
    if not m:
        raise HTTPException(422, "O aluno não tem matrícula activa")
    obter(db, Servico, dados.servico_id, est, "Serviço")
    ad = AdesaoServico(matricula_id=m.id, servico_id=dados.servico_id, modalidade=dados.modalidade,
                       data_inicio=dados.data_inicio or date.today())
    try:
        db.add(ad)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Adesão já registada nesta data")
    return as_dict(ad)


@router.get("/adesoes", response_model=list[R.AdesaoComNomes])
def adesoes(aluno_id: int | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(AdesaoServico, Servico.nome, Matricula.aluno_id)
            .join(Servico, Servico.id == AdesaoServico.servico_id)
            .join(Matricula, Matricula.id == AdesaoServico.matricula_id)
            .where(Matricula.estabelecimento_id == ctx.estabelecimento_id))
    if aluno_id:
        stmt = stmt.where(Matricula.aluno_id == aluno_id)
    return [{**as_dict(a), "servico": s, "aluno_id": aid} for a, s, aid in db.execute(stmt)]


@router.patch("/adesoes/{adid}", response_model=R.AdesaoServico)
def editar_adesao(adid: int, dados: AdesaoUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    ad = obter(db, AdesaoServico, adid, nome="Adesão")
    obter(db, Matricula, ad.matricula_id, ctx.estabelecimento_id, "Adesão")
    d = dados.model_dump(exclude_unset=True)
    if d.get("estado") == "cancelada" and "data_fim" not in d:
        d["data_fim"] = date.today()
    aplicar(ad, d)
    db.commit()
    return as_dict(ad)


@router.get("/tipos", response_model=list[R.RefeicaoTipo])
def tipos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(t) for t in db.scalars(select(RefeicaoTipo).where(RefeicaoTipo.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(RefeicaoTipo.hora_inicio))]


@router.post("/tipos", response_model=R.RefeicaoTipo, status_code=201)
def criar_tipo(dados: RefeicaoTipoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    t = RefeicaoTipo(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(t)
    db.commit()
    return as_dict(t)


@router.get("/ementas", response_model=list[R.EmentaComRefeicao])
def ementas(desde: date | None = None, ate: date | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(Ementa, RefeicaoTipo.designacao).join(RefeicaoTipo, RefeicaoTipo.id == Ementa.refeicao_tipo_id)
            .where(RefeicaoTipo.estabelecimento_id == ctx.estabelecimento_id))
    if desde:
        stmt = stmt.where(Ementa.data >= desde)
    if ate:
        stmt = stmt.where(Ementa.data <= ate)
    return [{**as_dict(e), "refeicao": r} for e, r in db.execute(stmt.order_by(Ementa.data))]


@router.put("/ementas", response_model=R.Ementa)
def definir_ementa(dados: EmentaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, RefeicaoTipo, dados.refeicao_tipo_id, ctx.estabelecimento_id, "Tipo de refeição")
    e = db.scalar(select(Ementa).where(Ementa.refeicao_tipo_id == dados.refeicao_tipo_id, Ementa.data == dados.data))
    e = e or Ementa(refeicao_tipo_id=dados.refeicao_tipo_id, data=dados.data)
    e.descricao = dados.descricao
    db.add(e)
    db.commit()
    return as_dict(e)


@router.post("/registos", response_model=R.RefeicoesRegistadas, summary="Controlo diário: regista quem comeu")
def registar_refeicoes(dados: RefeicaoRegistoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    obter(db, RefeicaoTipo, dados.refeicao_tipo_id, est, "Tipo de refeição")
    dia = dados.data or date.today()
    ano = ano_activo(db, est)
    adesoes = dict(db.execute(select(Matricula.aluno_id, AdesaoServico.id)
                              .join(AdesaoServico, AdesaoServico.matricula_id == Matricula.id)
                              .join(Servico, Servico.id == AdesaoServico.servico_id)
                              .where(Matricula.ano_id == ano.id, Matricula.aluno_id.in_(dados.aluno_ids),
                                     AdesaoServico.estado == "activa",
                                     Servico.categoria.in_(CATEGORIAS_REFEITORIO))).tuples().all())
    novos, cobradas, sem_adesao = 0, 0, []
    for aid in dados.aluno_ids:
        obter(db, Aluno, aid, est, "Aluno")
        if db.scalar(select(RefeicaoRegisto.id).where(RefeicaoRegisto.aluno_id == aid, RefeicaoRegisto.data == dia,
                                                      RefeicaoRegisto.refeicao_tipo_id == dados.refeicao_tipo_id)):
            continue
        avulso = dados.valor_avulso if aid not in adesoes and dados.valor_avulso else None
        if aid not in adesoes:
            sem_adesao.append(aid)
        r = RefeicaoRegisto(aluno_id=aid, refeicao_tipo_id=dados.refeicao_tipo_id, data=dia,
                            adesao_id=adesoes.get(aid), valor_avulso=avulso, registado_por=ctx.id)
        if avulso:
            r.cobranca_id = _cobrar_avulso(db, est, ano.id, aid, dia, Decimal(avulso))
            cobradas += 1
        db.add(r)
        novos += 1
    db.commit()
    return {"registados": novos, "sem_adesao": sem_adesao, "refeicoes_cobradas": cobradas}


def _servico_avulso(db: Session, est: int) -> Servico:
    s = db.scalar(select(Servico).where(Servico.estabelecimento_id == est, Servico.codigo == "REF-AVULSO"))
    if not s:
        s = Servico(estabelecimento_id=est, codigo="REF-AVULSO", nome="Refeições avulsas", categoria="refeitorio",
                    periodicidade="unica")
        db.add(s)
        db.flush()
    return s


def _cobrar_avulso(db: Session, est: int, ano_id: int, aluno_id: int, dia: date, valor: Decimal) -> int:
    """Junta as refeições avulsas do mês numa só cobrança por aluno ("conta do refeitório")."""
    m = db.scalar(select(Matricula).where(Matricula.aluno_id == aluno_id, Matricula.ano_id == ano_id,
                                          Matricula.estado == "activa"))
    if not m:
        raise HTTPException(422, f"O aluno {aluno_id} não tem matrícula activa para cobrar a refeição")
    s = _servico_avulso(db, est)
    comp = dia.replace(day=1)
    c = db.scalar(select(Cobranca).where(Cobranca.matricula_id == m.id, Cobranca.servico_id == s.id,
                                         Cobranca.competencia == comp))
    if c and c.estado != "anulada":
        c.valor_base += valor
        c.descricao = f"Refeições avulsas {comp:%m/%Y}"
        db.flush()
        actualizar_estado(db, c)  # uma conta já paga volta a ficar com saldo em aberto
    else:
        c = Cobranca(estabelecimento_id=est, matricula_id=m.id, aluno_id=aluno_id,
                     encarregado_id=encarregado_financeiro(db, aluno_id), servico_id=s.id, competencia=comp,
                     descricao=f"Refeições avulsas {comp:%m/%Y}", valor_base=valor,
                     vencimento=soma_meses(comp, 1).replace(day=10))
        db.add(c)
        db.flush()
    return c.id


@router.get("/registos", response_model=list[R.RegistoComNomes])
def registos(data: date | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    dia = data or date.today()
    rows = db.execute(select(RefeicaoRegisto, Aluno.nome_completo, RefeicaoTipo.designacao)
                      .join(Aluno, Aluno.id == RefeicaoRegisto.aluno_id)
                      .join(RefeicaoTipo, RefeicaoTipo.id == RefeicaoRegisto.refeicao_tipo_id)
                      .where(RefeicaoTipo.estabelecimento_id == ctx.estabelecimento_id, RefeicaoRegisto.data == dia))
    return [{**as_dict(r), "aluno": a, "refeicao": t} for r, a, t in rows]


# ---------------------------------------------------------------- CRUD completo (ver, editar, apagar)
crud.registar(router, "/tipos", RefeicaoTipo, nome="Tipo de refeição", modulo="refeitorio", resposta=R.RefeicaoTipo,
              entrada=RefeicaoTipoIn)
crud.registar(router, "/ementas", Ementa, nome="Ementa", modulo="refeitorio", resposta=R.Ementa, entrada=EmentaIn)
