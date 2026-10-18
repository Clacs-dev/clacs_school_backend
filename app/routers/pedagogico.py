from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import aplicar, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, Assiduidade, Avaliacao, Disciplina, Matricula, Nota, Ocorrencia, PeriodoLectivo, RegistoDiario,
    ResultadoPeriodo, TipoAvaliacao, TipoOcorrencia, Turma,
)
from app.schemas import (
    AssiduidadeIn, AvaliacaoIn, NotasIn, OcorrenciaIn, OcorrenciaUpd, RegistoDiarioIn, TipoAvaliacaoIn,
    TipoOcorrenciaIn,
)
from app.security import Contexto, auditar, exige, turmas_permitidas, verificar_turma
from app.services.pedagogico import media_ponderada

router = APIRouter(prefix="/api", tags=["Pedagógico"])
ver, criar, editar = exige("pedagogico"), exige("pedagogico", "criar"), exige("pedagogico", "editar")


def _turma(db: Session, ctx: Contexto, turma_id: int) -> Turma:
    """A turma tem de ser do estabelecimento e, para um professor, uma das suas."""
    t = obter(db, Turma, turma_id, ctx.estabelecimento_id, "Turma")
    verificar_turma(db, ctx, turma_id)
    return t


def _aluno_permitido(db: Session, ctx: Contexto, aluno_id: int) -> None:
    permitidas = turmas_permitidas(db, ctx)
    if permitidas is None:
        return
    turmas = set(db.scalars(select(Matricula.turma_id).where(Matricula.aluno_id == aluno_id, Matricula.estado == "activa")))
    if not turmas & permitidas:
        raise HTTPException(403, "Sem acesso a este aluno")


def _alunos_turma(db: Session, turma_id: int) -> list[tuple[int, int, str]]:
    return db.execute(select(Matricula.id, Aluno.id, Aluno.nome_completo).join(Aluno, Aluno.id == Matricula.aluno_id)
                      .where(Matricula.turma_id == turma_id, Matricula.estado == "activa")
                      .order_by(Aluno.nome_completo)).all()


@router.get("/tipos-avaliacao")
def tipos_avaliacao(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(t) for t in db.scalars(select(TipoAvaliacao).where(TipoAvaliacao.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/tipos-avaliacao", status_code=201)
def criar_tipo_avaliacao(dados: TipoAvaliacaoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    t = TipoAvaliacao(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(t)
    db.commit()
    return as_dict(t)


@router.get("/avaliacoes")
def avaliacoes(turma_id: int, periodo_id: int | None = None, disciplina_id: int | None = None,
               ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    _turma(db, ctx, turma_id)
    stmt = (select(Avaliacao, Disciplina.nome, TipoAvaliacao.designacao)
            .join(Disciplina, Disciplina.id == Avaliacao.disciplina_id)
            .join(TipoAvaliacao, TipoAvaliacao.id == Avaliacao.tipo_id).where(Avaliacao.turma_id == turma_id))
    if periodo_id:
        stmt = stmt.where(Avaliacao.periodo_id == periodo_id)
    if disciplina_id:
        stmt = stmt.where(Avaliacao.disciplina_id == disciplina_id)
    return [{**as_dict(a), "disciplina": d, "tipo": t} for a, d, t in db.execute(stmt.order_by(Avaliacao.data))]


@router.post("/avaliacoes", status_code=201)
def criar_avaliacao(dados: AvaliacaoIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    _turma(db, ctx, dados.turma_id)
    obter(db, Disciplina, dados.disciplina_id, est, "Disciplina")
    obter(db, TipoAvaliacao, dados.tipo_id, est, "Tipo de avaliação")
    obter(db, PeriodoLectivo, dados.periodo_id, nome="Período")
    a = Avaliacao(**dados.model_dump())
    db.add(a)
    db.commit()
    return as_dict(a)


def _avaliacao(db: Session, avid: int, ctx: Contexto) -> Avaliacao:
    a = obter(db, Avaliacao, avid, nome="Avaliação")
    obter(db, Turma, a.turma_id, ctx.estabelecimento_id, "Avaliação")
    verificar_turma(db, ctx, a.turma_id)
    return a


@router.get("/avaliacoes/{avid}/notas")
def notas(avid: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    a = _avaliacao(db, avid, ctx)
    lancadas = {n.aluno_id: n for n in db.scalars(select(Nota).where(Nota.avaliacao_id == avid))}
    return [{"aluno_id": aid, "aluno": nome,
             "valor": float(lancadas[aid].valor) if aid in lancadas and lancadas[aid].valor is not None else None,
             "observacao": lancadas[aid].observacao if aid in lancadas else None}
            for _, aid, nome in _alunos_turma(db, a.turma_id)]


@router.put("/avaliacoes/{avid}/notas", summary="Lança ou corrige as notas da avaliação")
def lancar_notas(avid: int, dados: NotasIn, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    a = _avaliacao(db, avid, ctx)
    da_turma = {aid for _, aid, _ in _alunos_turma(db, a.turma_id)}
    for item in dados.notas:
        if item.aluno_id not in da_turma:
            raise HTTPException(422, f"O aluno {item.aluno_id} não pertence à turma")
        n = db.scalar(select(Nota).where(Nota.avaliacao_id == avid, Nota.aluno_id == item.aluno_id))
        n = n or Nota(avaliacao_id=avid, aluno_id=item.aluno_id)
        n.valor, n.observacao, n.lancada_por = item.valor, item.observacao, ctx.id
        db.add(n)
    auditar(db, ctx, "nota", avid, "update", {"notas": len(dados.notas)})
    db.commit()
    return {"lancadas": len(dados.notas)}


@router.get("/pautas", summary="Pauta da turma no período: média por aluno e disciplina")
def pauta(turma_id: int, periodo_id: int, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    _turma(db, ctx, turma_id)
    discs = db.execute(select(Disciplina.id, Disciplina.nome).join(Avaliacao, Avaliacao.disciplina_id == Disciplina.id)
                       .where(Avaliacao.turma_id == turma_id, Avaliacao.periodo_id == periodo_id)
                       .distinct().order_by(Disciplina.nome)).all()
    linhas = []
    for mid, aid, nome in _alunos_turma(db, turma_id):
        medias = {d: media_ponderada(db, aid, turma_id, d, periodo_id) for d, _ in discs}
        validas = [v for v in medias.values() if v is not None]
        linhas.append({"matricula_id": mid, "aluno_id": aid, "aluno": nome,
                       "medias": {n: medias[d] for d, n in discs},
                       "media_geral": round(sum(validas) / len(validas), 1) if validas else None})
    return {"disciplinas": [n for _, n in discs], "linhas": linhas}


@router.post("/pautas/fechar", summary="Grava as médias do período em resultado_periodo")
def fechar_pauta(turma_id: int, periodo_id: int, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    _turma(db, ctx, turma_id)
    n = 0
    discs = db.scalars(select(Avaliacao.disciplina_id).where(Avaliacao.turma_id == turma_id,
                                                             Avaliacao.periodo_id == periodo_id).distinct()).all()
    for mid, aid, _ in _alunos_turma(db, turma_id):
        for d in discs:
            media = media_ponderada(db, aid, turma_id, d, periodo_id)
            r = db.scalar(select(ResultadoPeriodo).where(ResultadoPeriodo.matricula_id == mid,
                                                         ResultadoPeriodo.disciplina_id == d,
                                                         ResultadoPeriodo.periodo_id == periodo_id))
            r = r or ResultadoPeriodo(matricula_id=mid, disciplina_id=d, periodo_id=periodo_id)
            r.media = Decimal(str(media)) if media is not None else None
            db.add(r)
            n += 1
    auditar(db, ctx, "resultado_periodo", periodo_id, "update", {"turma_id": turma_id, "registos": n})
    db.commit()
    return {"resultados": n}


# ---------------------------------------------------------------- assiduidade
@router.post("/assiduidade", summary="Chamada: presenças e faltas da turma num dia")
def chamada(dados: AssiduidadeIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    _turma(db, ctx, dados.turma_id)
    da_turma = {aid for _, aid, _ in _alunos_turma(db, dados.turma_id)}
    for r in dados.registos:
        if r.aluno_id not in da_turma:
            raise HTTPException(422, f"O aluno {r.aluno_id} não pertence à turma")
        stmt = select(Assiduidade).where(Assiduidade.aluno_id == r.aluno_id, Assiduidade.turma_id == dados.turma_id,
                                         Assiduidade.data == dados.data)
        stmt = stmt.where(Assiduidade.disciplina_id == dados.disciplina_id) if dados.disciplina_id \
            else stmt.where(Assiduidade.disciplina_id.is_(None))
        a = db.scalar(stmt) or Assiduidade(aluno_id=r.aluno_id, turma_id=dados.turma_id,
                                           disciplina_id=dados.disciplina_id, data=dados.data)
        a.estado, a.justificacao, a.registada_por = r.estado, r.justificacao, ctx.id
        db.add(a)
    db.commit()
    return {"registos": len(dados.registos)}


@router.get("/assiduidade")
def assiduidade(turma_id: int | None = None, aluno_id: int | None = None, desde: date | None = None,
                ate: date | None = None, ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    stmt = (select(Assiduidade, Aluno.nome_completo).join(Aluno, Aluno.id == Assiduidade.aluno_id)
            .where(Aluno.estabelecimento_id == ctx.estabelecimento_id))
    permitidas = turmas_permitidas(db, ctx)
    if permitidas is not None:
        stmt = stmt.where(Assiduidade.turma_id.in_(permitidas))
    if turma_id:
        stmt = stmt.where(Assiduidade.turma_id == turma_id)
    if aluno_id:
        stmt = stmt.where(Assiduidade.aluno_id == aluno_id)
    if desde:
        stmt = stmt.where(Assiduidade.data >= desde)
    if ate:
        stmt = stmt.where(Assiduidade.data <= ate)
    return [{**as_dict(a), "aluno": n} for a, n in db.execute(stmt.order_by(Assiduidade.data.desc()).limit(2000))]


# ---------------------------------------------------------------- ocorrências e registo diário
@router.get("/tipos-ocorrencia")
def tipos_ocorrencia(ctx: Contexto = Depends(ver), db: Session = Depends(get_db)):
    return [as_dict(t) for t in db.scalars(select(TipoOcorrencia).where(TipoOcorrencia.estabelecimento_id == ctx.estabelecimento_id))]


@router.post("/tipos-ocorrencia", status_code=201)
def criar_tipo_ocorrencia(dados: TipoOcorrenciaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    t = TipoOcorrencia(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(t)
    db.commit()
    return as_dict(t)


@router.get("/ocorrencias")
def ocorrencias(aluno_id: int | None = None, estado: str | None = None, ctx: Contexto = Depends(ver),
                db: Session = Depends(get_db)):
    stmt = (select(Ocorrencia, Aluno.nome_completo, TipoOcorrencia.designacao, TipoOcorrencia.gravidade)
            .join(Aluno, Aluno.id == Ocorrencia.aluno_id).join(TipoOcorrencia, TipoOcorrencia.id == Ocorrencia.tipo_id)
            .where(Aluno.estabelecimento_id == ctx.estabelecimento_id))
    permitidas = turmas_permitidas(db, ctx)
    if permitidas is not None:
        stmt = stmt.where(Ocorrencia.aluno_id.in_(select(Matricula.aluno_id).where(
            Matricula.turma_id.in_(permitidas), Matricula.estado == "activa")))
    if aluno_id:
        stmt = stmt.where(Ocorrencia.aluno_id == aluno_id)
    if estado:
        stmt = stmt.where(Ocorrencia.estado == estado)
    return [{**as_dict(o), "aluno": a, "tipo": t, "gravidade": g}
            for o, a, t, g in db.execute(stmt.order_by(Ocorrencia.data.desc()))]


@router.post("/ocorrencias", status_code=201)
def criar_ocorrencia(dados: OcorrenciaIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    obter(db, Aluno, dados.aluno_id, ctx.estabelecimento_id, "Aluno")
    _aluno_permitido(db, ctx, dados.aluno_id)
    obter(db, TipoOcorrencia, dados.tipo_id, ctx.estabelecimento_id, "Tipo de ocorrência")
    o = Ocorrencia(registada_por=ctx.id, **dados.model_dump(exclude_none=True))
    db.add(o)
    db.commit()
    return as_dict(o)


@router.patch("/ocorrencias/{oid}")
def editar_ocorrencia(oid: int, dados: OcorrenciaUpd, ctx: Contexto = Depends(editar), db: Session = Depends(get_db)):
    o = obter(db, Ocorrencia, oid, nome="Ocorrência")
    obter(db, Aluno, o.aluno_id, ctx.estabelecimento_id, "Ocorrência")
    _aluno_permitido(db, ctx, o.aluno_id)
    aplicar(o, dados.model_dump(exclude_unset=True))
    db.commit()
    return as_dict(o)


@router.post("/registo-diario", status_code=201)
def registo_diario(dados: RegistoDiarioIn, ctx: Contexto = Depends(criar), db: Session = Depends(get_db)):
    _turma(db, ctx, dados.turma_id)
    if dados.aluno_id:
        obter(db, Aluno, dados.aluno_id, ctx.estabelecimento_id, "Aluno")
        _aluno_permitido(db, ctx, dados.aluno_id)
    r = RegistoDiario(autor_id=ctx.id, **dados.model_dump(exclude_none=True))
    db.add(r)
    db.commit()
    return as_dict(r)


@router.get("/registo-diario")
def ver_registo_diario(turma_id: int, data: date | None = None, ctx: Contexto = Depends(ver),
                       db: Session = Depends(get_db)):
    _turma(db, ctx, turma_id)
    stmt = select(RegistoDiario).where(RegistoDiario.turma_id == turma_id)
    if data:
        stmt = stmt.where(RegistoDiario.data == data)
    return [as_dict(r) for r in db.scalars(stmt.order_by(RegistoDiario.data.desc()).limit(500))]
