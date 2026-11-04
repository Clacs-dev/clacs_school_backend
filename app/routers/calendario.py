"""Calendário escolar, horários (com histórico de alterações), substituições, registo das aulas com a chamada por
tempo lectivo, assiduidade dos professores e relatório de assiduidade por disciplina."""
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.common import ano_activo, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, Assiduidade, Aula, Disciplina, EventoCalendario, Funcionario, Horario, Matricula, Substituicao, Turma,
)
from app.schemas import (
    AulaRegistoIn, EventoIn, FaltaProfessorIn, HorarioAlterarIn, HorarioIn, SubstituicaoIn,
)
from app.security import Contexto, auditar, exige, turmas_permitidas, verificar_turma
from app.services import calendario as svc

router = APIRouter(prefix="/api", tags=["Calendário e horários"])
ver_acad, editar_acad = exige("academico"), exige("academico", "editar")
ver_ped, criar_ped = exige("pedagogico"), exige("pedagogico", "criar")


# ---------------------------------------------------------------- calendário
@router.get("/eventos", response_model=list[R.EventoCalendario], summary="Feriados, interrupções, provas, reuniões e eventos")
def eventos(desde: date | None = None, ate: date | None = None, tipo: str | None = None,
            ctx: Contexto = Depends(exige("pedagogico")), db: Session = Depends(get_db)):
    stmt = select(EventoCalendario).where(EventoCalendario.estabelecimento_id == ctx.estabelecimento_id)
    if desde:
        stmt = stmt.where((EventoCalendario.data_fim >= desde) | (EventoCalendario.data_inicio >= desde))
    if ate:
        stmt = stmt.where(EventoCalendario.data_inicio <= ate)
    if tipo:
        stmt = stmt.where(EventoCalendario.tipo == tipo)
    return [as_dict(e) for e in db.scalars(stmt.order_by(EventoCalendario.data_inicio))]


@router.post("/eventos", response_model=R.EventoCalendario, status_code=201)
def criar_evento(dados: EventoIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    if dados.data_fim and dados.data_fim < dados.data_inicio:
        raise HTTPException(422, "A data de fim é anterior à de início")
    e = EventoCalendario(estabelecimento_id=ctx.estabelecimento_id, **dados.model_dump())
    db.add(e)
    db.commit()
    return as_dict(e)


# ---------------------------------------------------------------- horários
def _horario(db: Session, h: Horario) -> dict:
    prof = h.funcionario_id and db.get(Funcionario, h.funcionario_id)
    return {**as_dict(h), "turma": db.get(Turma, h.turma_id).designacao, "disciplina": db.get(Disciplina, h.disciplina_id).nome,
            "professor": prof.nome_completo if prof else None}


@router.get("/horarios", response_model=list[R.HorarioComNomes],
            summary="Horário em vigor numa data (por omissão hoje), da turma ou do professor; historico=true: todas as versões")
def horarios(turma_id: int | None = None, funcionario_id: int | None = None, data: date | None = None,
             historico: bool = False, ctx: Contexto = Depends(ver_ped), db: Session = Depends(get_db)):
    stmt = select(Horario).where(Horario.estabelecimento_id == ctx.estabelecimento_id)
    if not historico:
        d = data or date.today()
        stmt = stmt.where(Horario.valido_desde <= d, (Horario.valido_ate.is_(None)) | (Horario.valido_ate >= d))
    if turma_id:
        stmt = stmt.where(Horario.turma_id == turma_id)
    if funcionario_id:
        stmt = stmt.where(Horario.funcionario_id == funcionario_id)
    permitidas = turmas_permitidas(db, ctx)
    if permitidas is not None:
        stmt = stmt.where(Horario.turma_id.in_(permitidas))
    return [_horario(db, h) for h in db.scalars(stmt.order_by(Horario.turma_id, Horario.dia_semana, Horario.tempo,
                                                              Horario.valido_desde))]


def _validar(db: Session, ctx: Contexto, dados) -> None:
    t = obter(db, Turma, dados.turma_id, ctx.estabelecimento_id, "Turma")
    obter(db, Disciplina, dados.disciplina_id, ctx.estabelecimento_id, "Disciplina")
    if dados.funcionario_id:
        obter(db, Funcionario, dados.funcionario_id, ctx.estabelecimento_id, "Professor")
    if dados.hora_fim <= dados.hora_inicio:
        raise HTTPException(422, "A hora de fim tem de ser depois da de início")
    return t


@router.post("/horarios", response_model=R.HorarioComNomes, status_code=201,
             summary="Novo tempo lectivo (recusa conflitos de turma, professor ou sala)")
def criar_horario(dados: HorarioIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    _validar(db, ctx, dados)
    h = Horario(estabelecimento_id=ctx.estabelecimento_id, valido_desde=dados.valido_desde or date.today(),
                **dados.model_dump(exclude={"valido_desde"}))
    svc.conflitos(db, h, h.valido_desde)
    db.add(h)
    db.commit()
    return _horario(db, h)


@router.post("/horarios/{hid}/alterar", response_model=R.HorarioComNomes, status_code=201,
             summary="Altera a partir de uma data: a versão anterior fica no histórico (valido_ate) e nasce uma nova")
def alterar_horario(hid: int, dados: HorarioAlterarIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    antigo = obter(db, Horario, hid, ctx.estabelecimento_id, "Horário")
    if antigo.valido_ate is not None and antigo.valido_ate < dados.desde:
        raise HTTPException(409, "Esta versão do horário já terminou")
    if dados.desde <= antigo.valido_desde:
        raise HTTPException(422, "A alteração tem de começar depois do início desta versão")
    campos = {c: getattr(antigo, c) for c in ("turma_id", "disciplina_id", "funcionario_id", "sala_id", "dia_semana",
                                              "tempo", "hora_inicio", "hora_fim")}
    campos.update(dados.model_dump(exclude={"desde", "motivo"}, exclude_unset=True))
    novo = Horario(estabelecimento_id=ctx.estabelecimento_id, valido_desde=dados.desde, **campos)
    antigo.valido_ate = dados.desde - timedelta(days=1)
    db.flush()
    svc.conflitos(db, novo, dados.desde)
    db.add(novo)
    auditar(db, ctx, "horario", hid, "update", {"desde": dados.desde.isoformat(), "motivo": dados.motivo,
                                               **{k: str(v) for k, v in campos.items()}})
    db.commit()
    return _horario(db, novo)


@router.post("/horarios/{hid}/terminar", response_model=R.HorarioComNomes, summary="Termina o tempo lectivo (fica no histórico)")
def terminar_horario(hid: int, ate: date | None = None, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    h = obter(db, Horario, hid, ctx.estabelecimento_id, "Horário")
    h.valido_ate = ate or date.today()
    db.commit()
    return _horario(db, h)


# ---------------------------------------------------------------- substituições e aulas
@router.post("/substituicoes", response_model=R.Substituicao, status_code=201,
             summary="Professor substituto para uma aula num dia")
def substituir(dados: SubstituicaoIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    h = obter(db, Horario, dados.horario_id, ctx.estabelecimento_id, "Horário")
    obter(db, Funcionario, dados.substituto_id, ctx.estabelecimento_id, "Professor")
    if h not in svc.horarios_validos(db, ctx.estabelecimento_id, dados.data):
        raise HTTPException(422, "Esse tempo lectivo não tem aula nessa data")
    s = db.scalar(select(Substituicao).where(Substituicao.horario_id == h.id, Substituicao.data == dados.data)) or \
        Substituicao(horario_id=h.id, data=dados.data)
    s.substituto_id, s.motivo, s.registada_por = dados.substituto_id, dados.motivo, ctx.id
    db.add(s)
    db.commit()
    return as_dict(s)


@router.get("/aulas", response_model=list[R.AulaPrevista],
            summary="Aulas previstas num dia (sem feriados), com substituições e o que já foi registado; "
                    "um professor vê as suas (meus=true)")
def aulas(data: date | None = None, turma_id: int | None = None, meus: bool = False, ctx: Contexto = Depends(ver_ped),
          db: Session = Depends(get_db)):
    funcionario = ctx.utilizador.funcionario_id if meus else None
    if meus and not funcionario:
        raise HTTPException(409, "A sua conta não está ligada a um professor")
    linhas = svc.aulas_do_dia(db, ctx.estabelecimento_id, data or date.today(), turma_id, funcionario)
    permitidas = turmas_permitidas(db, ctx)
    return [x for x in linhas if permitidas is None or x["turma_id"] in permitidas or x["professor_id"] == ctx.utilizador.funcionario_id]


def _aula_prevista(db: Session, ctx: Contexto, horario_id: int, dia: date) -> tuple[Horario, dict]:
    if dia > date.today():
        raise HTTPException(422, "Uma aula só se regista no próprio dia ou depois")
    h = obter(db, Horario, horario_id, ctx.estabelecimento_id, "Horário")
    prevista = next((x for x in svc.aulas_do_dia(db, ctx.estabelecimento_id, dia) if x["horario_id"] == h.id), None)
    if prevista is None:
        raise HTTPException(422, "Não há essa aula nessa data (dia da semana, versão do horário ou feriado)")
    if prevista["professor_id"] != ctx.utilizador.funcionario_id:
        verificar_turma(db, ctx, h.turma_id)
    return h, prevista


@router.post("/aulas/registar", response_model=R.AulaRegistada,
             summary="O professor regista a aula prevista: sumário, atraso e a chamada desse tempo lectivo")
def registar_aula(dados: AulaRegistoIn, ctx: Contexto = Depends(criar_ped), db: Session = Depends(get_db)):
    h, prevista = _aula_prevista(db, ctx, dados.horario_id, dados.data)
    ano = ano_activo(db, ctx.estabelecimento_id)
    da_turma = set(db.scalars(select(Matricula.aluno_id).where(Matricula.turma_id == h.turma_id, Matricula.ano_id == ano.id,
                                                              Matricula.estado == "activa")))
    registos = [(p.aluno_id, p.estado) for p in dados.presencas]
    if dados.todos:   # "todos presentes": a turma inteira, menos os que vêm em presencas
        individuais = {aid for aid, _ in registos}
        registos += [(aid, dados.todos) for aid in sorted(da_turma - individuais)]
    a = db.scalar(select(Aula).where(Aula.horario_id == h.id, Aula.data == dados.data)) or Aula(horario_id=h.id, data=dados.data)
    a.estado, a.funcionario_id = "dada", prevista["professor_id"]
    a.sumario, a.atraso_minutos, a.registada_por = dados.sumario, dados.atraso_minutos, ctx.id
    db.add(a)
    n = 0
    for aluno_id, estado in registos:
        if aluno_id not in da_turma:
            raise HTTPException(422, f"O aluno {aluno_id} não pertence à turma")
        r = db.scalar(select(Assiduidade).where(Assiduidade.aluno_id == aluno_id, Assiduidade.horario_id == h.id,
                                                Assiduidade.data == dados.data)) or \
            Assiduidade(aluno_id=aluno_id, turma_id=h.turma_id, disciplina_id=h.disciplina_id, data=dados.data, horario_id=h.id)
        if r.estado_original is None:   # justificada: a decisão fica, o registo do professor actualiza o original
            r.estado = estado
        else:
            r.estado_original = estado
        r.registada_por = ctx.id
        db.add(r)
        n += 1
    db.commit()
    return {**as_dict(a), "presencas": n}


@router.post("/aulas/falta-professor", response_model=R.Aula, summary="Regista que o professor faltou a uma aula prevista")
def falta_professor(dados: FaltaProfessorIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    h, prevista = _aula_prevista(db, ctx, dados.horario_id, dados.data)
    a = db.scalar(select(Aula).where(Aula.horario_id == h.id, Aula.data == dados.data)) or Aula(horario_id=h.id, data=dados.data)
    a.estado, a.funcionario_id, a.sumario, a.registada_por = "falta_professor", prevista["professor_id"], dados.motivo, ctx.id
    db.add(a)
    db.commit()
    return as_dict(a)


# ---------------------------------------------------------------- relatórios
@router.get("/professores/assiduidade", response_model=list[R.AssiduidadeProfessor],
            summary="Aulas previstas, dadas, faltas, substituições feitas e atrasos de cada professor")
def assiduidade_professores(desde: date, ate: date, ctx: Contexto = Depends(ver_acad), db: Session = Depends(get_db)):
    if ate < desde or (ate - desde).days > 366:
        raise HTTPException(422, "Período inválido (máximo um ano)")
    est = ctx.estabelecimento_id
    stats: dict[int, dict] = defaultdict(lambda: {"previstas": 0, "dadas": 0, "faltas": 0, "por_registar": 0,
                                                  "substituicoes_feitas": 0, "minutos_atraso": 0})
    d = desde
    while d <= min(ate, date.today()):
        for x in svc.aulas_do_dia(db, est, d):
            if x["professor_id"] is None:
                continue
            s = stats[x["professor_id"]]
            s["previstas"] += 1
            s["substituicoes_feitas"] += x["substituicao"]
            if x["estado"] == "dada":
                s["dadas"] += 1
            elif x["estado"] == "falta_professor":
                s["faltas"] += 1
            else:
                s["por_registar"] += 1
        d += timedelta(days=1)
    for a in db.scalars(select(Aula).where(Aula.data.between(desde, ate), Aula.atraso_minutos > 0)):
        if a.funcionario_id in stats:
            stats[a.funcionario_id]["minutos_atraso"] += a.atraso_minutos
    return [{"funcionario_id": fid, "professor": db.get(Funcionario, fid).nome_completo, **s} for fid, s in sorted(stats.items())]


@router.get("/assiduidade/relatorio", response_model=list[R.AssiduidadeResumo],
            summary="Presenças, faltas e atrasos por aluno e disciplina num período")
def relatorio_assiduidade(turma_id: int, desde: date, ate: date, ctx: Contexto = Depends(ver_ped), db: Session = Depends(get_db)):
    obter(db, Turma, turma_id, ctx.estabelecimento_id, "Turma")
    verificar_turma(db, ctx, turma_id)
    linhas: dict[tuple, dict] = {}
    for a, nome, disc in db.execute(select(Assiduidade, Aluno.nome_completo, Disciplina.nome)
                                    .join(Aluno, Aluno.id == Assiduidade.aluno_id)
                                    .outerjoin(Disciplina, Disciplina.id == Assiduidade.disciplina_id)
                                    .where(Assiduidade.turma_id == turma_id, Assiduidade.data.between(desde, ate))):
        x = linhas.setdefault((a.aluno_id, a.disciplina_id), {
            "aluno_id": a.aluno_id, "aluno": nome, "disciplina_id": a.disciplina_id, "disciplina": disc,
            "presente": 0, "atraso": 0, "falta_justificada": 0, "falta_injustificada": 0})
        x[a.estado] += 1
    return sorted(linhas.values(), key=lambda x: (x["aluno"], x["disciplina"] or ""))


crud.registar(router, "/eventos", EventoCalendario, nome="Evento", modulo="academico", resposta=R.EventoCalendario,
              entrada=EventoIn)
