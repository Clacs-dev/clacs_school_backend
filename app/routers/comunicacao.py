from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import ano_activo, aplicar, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, AlunoEncarregado, Comunicado, ComunicadoAlvo, Encarregado, Matricula, Mensagem, ModeloMensagem,
    RegraAutomacao, Turma,
)
from app.schemas import ComunicadoIn, MensagemAlunoIn, ModeloIn, RegraIn, RegraUpd
from app.security import Contexto, exige
from app.services import comunicacao as com

router = APIRouter(prefix="/api", tags=["Comunicação e regras"])
ver, criar, editar = exige("comunicacao"), exige("comunicacao", "criar"), exige("comunicacao", "editar")


@router.get("/modelos-mensagem", response_model=list[R.ModeloMensagem])
def modelos(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(m) for m in db.scalars(select(ModeloMensagem).where(ModeloMensagem.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/modelos-mensagem", response_model=R.ModeloMensagem, status_code=201)
def criar_modelo(dados: ModeloIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    m = ModeloMensagem(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    try:
        db.add(m)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Já existe um modelo com este código para este canal")
    return as_dict(m)


@router.get("/regras", response_model=list[R.RegraAutomacao])
def regras(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(r) for r in db.scalars(select(RegraAutomacao).where(RegraAutomacao.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(RegraAutomacao.id))]


@router.post("/regras", response_model=R.RegraAutomacao, status_code=201)
def criar_regra(dados: RegraIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    if dados.modelo_id:
        obter(db, ModeloMensagem, dados.modelo_id, ctx.estabelecimento_id, "Modelo")
    r = RegraAutomacao(estabelecimento_id=ctx.estabelecimento_id, criada_por=ctx.id, **dados.model_dump())
    db.add(r)
    db.commit()
    return as_dict(r)


@router.patch("/regras/{rid}", response_model=R.RegraAutomacao)
def editar_regra(rid: int, dados: RegraUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    r = obter(db, RegraAutomacao, rid, ctx.estabelecimento_id, "Regra")
    aplicar(r, dados.model_dump(exclude_unset=True))
    db.commit()
    return as_dict(r)


@router.post("/regras/executar", response_model=dict[str, int], summary="Avalia as regras activas e põe as mensagens na fila")
def executar(ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return com.executar_regras(db, ctx.estabelecimento_id)


@router.post("/mensagens/despachar", response_model=R.Despacho, summary="Envia as mensagens agendadas pelo fornecedor configurado")
def despachar(ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    return com.despachar(db, ctx.estabelecimento_id)


@router.get("/mensagens", response_model=list[R.MensagemComEncarregado])
def mensagens(estado: str | None = None, aluno_id: int | None = None, encarregado_id: int | None = None,
              limit: int = 200, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(Mensagem, Encarregado.nome_completo).join(Encarregado, Encarregado.id == Mensagem.encarregado_id)
            .where(Mensagem.estabelecimento_id == ctx.estabelecimento_id))
    if estado:
        stmt = stmt.where(Mensagem.estado == estado)
    if aluno_id:
        stmt = stmt.where(Mensagem.aluno_id == aluno_id)
    if encarregado_id:
        stmt = stmt.where(Mensagem.encarregado_id == encarregado_id)
    rows = db.execute(stmt.order_by(Mensagem.id.desc()).limit(min(limit, 1000)))
    return [{**as_dict(m), "encarregado": e} for m, e in rows]


@router.post("/alunos/{aid}/mensagem", response_model=R.Mensagens, status_code=201, summary="Mensagem ao(s) encarregado(s) do aluno")
def mensagem_aluno(aid: int, dados: MensagemAlunoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    a = obter(db, Aluno, aid, ctx.estabelecimento_id, "Aluno")
    ees = db.scalars(select(AlunoEncarregado.encarregado_id).where(
        AlunoEncarregado.aluno_id == aid, or_(AlunoEncarregado.principal, AlunoEncarregado.responsavel_financeiro))).all()
    if not ees:
        raise HTTPException(422, "O aluno não tem encarregado registado")
    n = 0
    for eid in set(ees):
        for canal in dados.canais:
            db.add(Mensagem(estabelecimento_id=ctx.estabelecimento_id, encarregado_id=eid, aluno_id=aid, canal=canal,
                            assunto=dados.assunto, corpo=com.render(dados.corpo, {"aluno": a.nome_completo})))
            n += 1
    db.commit()
    return {"mensagens": n}


@router.get("/comunicados", response_model=list[R.Comunicado])
def comunicados(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(c) for c in db.scalars(select(Comunicado).where(Comunicado.estabelecimento_id == ctx.estabelecimento_id)
                                           .order_by(Comunicado.id.desc()))]


@router.post("/comunicados", response_model=R.ComunicadoEnviado, status_code=201, summary="Comunicado a toda a escola, a classes ou a turmas")
def criar_comunicado(dados: ComunicadoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ano = ano_activo(db, est)
    c = Comunicado(estabelecimento_id=est, titulo=dados.titulo, corpo=dados.corpo, autor_id=ctx.id, alvo=dados.alvo)
    db.add(c)
    db.flush()
    for t in dados.turma_ids:
        db.add(ComunicadoAlvo(comunicado_id=c.id, turma_id=t))
    for k in dados.classe_ids:
        db.add(ComunicadoAlvo(comunicado_id=c.id, classe_id=k))
    stmt = (select(AlunoEncarregado.encarregado_id, Matricula.aluno_id)
            .join(Matricula, Matricula.aluno_id == AlunoEncarregado.aluno_id)
            .join(Turma, Turma.id == Matricula.turma_id)
            .where(Matricula.ano_id == ano.id, Matricula.estado == "activa", AlunoEncarregado.principal))
    if dados.alvo == "turma":
        stmt = stmt.where(Turma.id.in_(dados.turma_ids))
    elif dados.alvo == "classe":
        stmt = stmt.where(Turma.classe_id.in_(dados.classe_ids))
    destinos = {}
    for eid, aid in db.execute(stmt):
        destinos.setdefault(eid, aid)
    for eid, aid in destinos.items():
        for canal in dados.canais:
            db.add(Mensagem(estabelecimento_id=est, comunicado_id=c.id, encarregado_id=eid, aluno_id=aid,
                            canal=canal, assunto=dados.titulo, corpo=dados.corpo))
    db.commit()
    return {**as_dict(c), "destinatarios": len(destinos)}


# ---------------------------------------------------------------- CRUD completo (ver, editar, apagar)
crud.registar(router, "/modelos-mensagem", ModeloMensagem, nome="Modelo de mensagem", modulo="comunicacao",
              resposta=R.ModeloMensagem, entrada=ModeloIn)
crud.registar(router, "/regras", RegraAutomacao, nome="Regra", modulo="comunicacao", resposta=R.RegraAutomacao,
              ops=("ver", "apagar"))
