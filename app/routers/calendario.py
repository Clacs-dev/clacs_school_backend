"""Calendário escolar, horários (com histórico de alterações), substituições, registo das aulas com a chamada por
tempo lectivo, assiduidade dos professores e relatório de assiduidade por disciplina."""
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app import respostas as R
from app.ciclo import livre_de_ciclo
from app.common import ano_activo, as_dict, obter
from app.database import get_db
from app.models import (
    Aluno, AnoLectivo, Assiduidade, Aula, Disciplina, EventoCalendario, Funcionario, Horario, Matricula, Sala, Substituicao, TempoLectivo,
    Turma,
)
from app import ordem as ordens
from app.plano import exigir_no_plano
from app.schemas import (
    AulaRegistoIn, CopiarPlanoIn, EventoIn,FaltaProfessorIn, HorarioAlterarIn, HorarioEditarIn, HorarioIn, ReordenarIn,
    ParTemposIn, SubstituicaoIn, TempoLectivoIn,
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


@router.get("/anos-lectivos/{ano_id}/semanas", response_model=R.SemanasDoCiclo,
            summary="Calendário semanal do ciclo: fase, n.º da semana lectiva, dias com aulas e actividades de cada semana")
@livre_de_ciclo
def semanas(ano_id: int, ctx: Contexto = Depends(ver_ped), db: Session = Depends(get_db)):
    return svc.semanas_do_ciclo(db, obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo"))


@router.post("/anos-lectivos/{ano_id}/plano/copiar", response_model=R.CopiaPlano,
             summary="Copia o plano de actividades de outro ciclo (datas ajustadas às do ciclo)")
@livre_de_ciclo
def copiar_plano(ano_id: int, dados: CopiarPlanoIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    destino = obter(db, AnoLectivo, ano_id, ctx.estabelecimento_id, "Ano lectivo")
    origem = obter(db, AnoLectivo, dados.origem_id, ctx.estabelecimento_id, "Ano lectivo")
    if destino.estado == "encerrado":
        raise HTTPException(409, f"O ciclo {destino.designacao} está encerrado: só consulta")
    if origem.id == destino.id:
        raise HTTPException(422, "Escolha outro ciclo para copiar o plano")
    res = svc.copiar_plano(db, origem, destino)
    auditar(db, ctx, "ano_lectivo", ano_id, "update", {"plano_copiado_de": origem.designacao, **res})
    db.commit()
    return res


# ---------------------------------------------------------------- tempos lectivos (Registo académico)
def _tempos_de(est: int):
    return select(TempoLectivo).where(TempoLectivo.estabelecimento_id == est)


@router.get("/tempos-lectivos", response_model=list[R.TempoLectivo], summary="Tempos lectivos da escola e a duração de cada um")
def tempos_lectivos(ctx: Contexto = Depends(ver_ped), db: Session = Depends(get_db)):
    return [as_dict(t) for t in db.scalars(_tempos_de(ctx.estabelecimento_id).order_by(TempoLectivo.ordem, TempoLectivo.id))]


def _duracao(inicio, fim, nome: str) -> int:
    """Duração em minutos entre as horas de um tempo (de 5 a 240)."""
    m = (fim.hour * 60 + fim.minute) - (inicio.hour * 60 + inicio.minute)
    if m <= 0:
        raise HTTPException(422, f"{nome}: a hora de fim tem de ser depois da de início")
    if not 5 <= m <= 240:
        raise HTTPException(422, f"{nome}: um tempo dura entre 5 e 240 minutos")
    return m


@router.post("/tempos-lectivos", response_model=R.TempoLectivo, status_code=201,
             summary="Novo tempo lectivo (ordem e nome automáticos se ficarem em branco)")
def criar_tempo_lectivo(dados: TempoLectivoIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    ordem = ordens.ao_criar(db, TempoLectivo, _tempos_de(est), dados.ordem, "um tempo lectivo", lambda t: t.designacao)
    if ordem > 12:
        raise HTTPException(422, "O dia tem no máximo 12 tempos lectivos")
    duracao = dados.duracao_minutos
    if dados.hora_inicio and dados.hora_fim:
        duracao = _duracao(dados.hora_inicio, dados.hora_fim, f"{ordem}.º tempo")
    t = TempoLectivo(estabelecimento_id=est, ordem=ordem, duracao_minutos=duracao, hora_inicio=dados.hora_inicio,
                     hora_fim=dados.hora_fim, designacao=(dados.designacao or "").strip() or f"{ordem}.º tempo")
    db.add(t)
    db.commit()
    return as_dict(t)


@router.post("/tempos-lectivos/par", response_model=list[R.TempoLectivo], status_code=201,
             summary="Regista um par de tempos (1.º e 2.º, 3.º e 4.º, 5.º e 6.º) com as horas de cada um")
def criar_par_tempos(dados: ParTemposIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    """Cria os dois tempos do par ou, se já existem, corrige as horas. Recusa horas que se sobreponham às de outro tempo."""
    est = ctx.estabelecimento_id
    a, b = 2 * dados.par - 1, 2 * dados.par
    pares = ((a, dados.primeiro_inicio, dados.primeiro_fim), (b, dados.segundo_inicio, dados.segundo_fim))
    duracoes = [_duracao(ini, fim, f"{ordem}.º tempo") for ordem, ini, fim in pares]
    if dados.segundo_inicio < dados.primeiro_fim:
        raise HTTPException(422, f"O {b}.º tempo tem de começar depois do fim do {a}.º tempo")
    outros = [t for t in db.scalars(_tempos_de(est).where(TempoLectivo.ordem.notin_((a, b))))
              if t.hora_inicio and t.hora_fim]
    for ordem, ini, fim in pares:
        for t in outros:
            if ini < t.hora_fim and t.hora_inicio < fim:
                raise HTTPException(422, f"O {ordem}.º tempo ({ini:%H:%M}–{fim:%H:%M}) sobrepõe-se ao {t.designacao} "
                                         f"({t.hora_inicio:%H:%M}–{t.hora_fim:%H:%M})")
    res = []
    for (ordem, ini, fim), duracao in zip(pares, duracoes):
        t = db.scalar(_tempos_de(est).where(TempoLectivo.ordem == ordem).order_by(TempoLectivo.id).limit(1))
        if t is None:
            t = TempoLectivo(estabelecimento_id=est, ordem=ordem, designacao=f"{ordem}.º tempo")
            db.add(t)
        t.hora_inicio, t.hora_fim, t.duracao_minutos = ini, fim, duracao
        res.append(t)
    db.commit()
    return [as_dict(t) for t in res]


@router.post("/tempos-lectivos/reordenar", response_model=list[R.TempoLectivo],
             summary="Põe os tempos lectivos pela ordem indicada (1.º, 2.º…)")
def reordenar_tempos(dados: ReordenarIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    """Atenção: a ordem é o número do tempo usado nos horários; reordenar não altera os horários já criados."""
    return [as_dict(t) for t in ordens.reordenar(db, TempoLectivo, _tempos_de(ctx.estabelecimento_id), dados.ids)]


def _editar_tempo(db: Session, ctx: Contexto, obj: TempoLectivo, mudancas: dict) -> None:
    if mudancas.get("ordem") and mudancas["ordem"] > 12:
        raise HTTPException(422, "O dia tem no máximo 12 tempos lectivos")
    if "designacao" in mudancas and not (mudancas["designacao"] or "").strip():
        mudancas.pop("designacao")
    inicio, fim = mudancas.get("hora_inicio", obj.hora_inicio), mudancas.get("hora_fim", obj.hora_fim)
    if inicio and fim and ({"hora_inicio", "hora_fim"} & mudancas.keys()):
        mudancas["duracao_minutos"] = _duracao(inicio, fim, obj.designacao)
    ordens.ao_editar(db, obj, _tempos_de(ctx.estabelecimento_id), mudancas)


def _horas_do_tempo(db: Session, est: int, tempo: int, inicio, fim) -> tuple:
    """Horas da aula a partir do tempo lectivo registado: o início e o fim do tempo (ex.: 1.º tempo 07:30–08:15).
    Se o início for outro, o fim é o início mais a duração do tempo."""
    t = db.scalar(_tempos_de(est).where(TempoLectivo.ordem == tempo).order_by(TempoLectivo.id).limit(1))
    if inicio is None:
        if t is None or t.hora_inicio is None:
            raise HTTPException(422, f"Indique a hora de início ou defina as horas do {tempo}.º tempo em "
                                     "Registo académico → Tempos lectivos")
        inicio = t.hora_inicio
    if fim is None:
        if t is None:
            raise HTTPException(422, f"Indique a hora de fim ou registe o {tempo}.º tempo em Registo académico → Tempos lectivos")
        if t.hora_fim and inicio == t.hora_inicio:
            fim = t.hora_fim
        else:
            fim = (datetime.combine(date.today(), inicio) + timedelta(minutes=t.duracao_minutos)).time()
    return inicio, fim


# ---------------------------------------------------------------- horários
def _horario(db: Session, h: Horario) -> dict:
    prof = h.funcionario_id and db.get(Funcionario, h.funcionario_id)
    sala = h.sala_id and db.get(Sala, h.sala_id)
    return {**as_dict(h), "turma": db.get(Turma, h.turma_id).designacao, "disciplina": db.get(Disciplina, h.disciplina_id).nome,
            "professor": prof.nome_completo if prof else None, "sala": sala.designacao if sala else None}


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
    exigir_no_plano(db, t.classe_id, dados.disciplina_id)
    if dados.funcionario_id:
        obter(db, Funcionario, dados.funcionario_id, ctx.estabelecimento_id, "Professor")
    if dados.hora_fim <= dados.hora_inicio:
        raise HTTPException(422, "A hora de fim tem de ser depois da de início")
    return t


@router.post("/horarios", response_model=R.HorarioComNomes, status_code=201,
             summary="Novo tempo lectivo (recusa conflitos de turma, professor ou sala)")
def criar_horario(dados: HorarioIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    if dados.sala_id is None:  # a aula é na sala da turma, salvo indicação em contrário
        turma = obter(db, Turma, dados.turma_id, ctx.estabelecimento_id, "Turma")
        dados.sala_id = turma.sala_id
    if dados.hora_inicio is None or dados.hora_fim is None:
        dados.hora_inicio, dados.hora_fim = _horas_do_tempo(db, ctx.estabelecimento_id, dados.tempo,
                                                            dados.hora_inicio, dados.hora_fim)
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


@router.post("/horarios/{hid}/editar", response_model=R.HorarioComNomes,
             summary="Corrige um tempo do horário: se ainda não começou, muda-o; se já está em uso, vale a partir de hoje")
def editar_horario(hid: int, dados: HorarioEditarIn, ctx: Contexto = Depends(editar_acad), db: Session = Depends(get_db)):
    est = ctx.estabelecimento_id
    h = obter(db, Horario, hid, est, "Horário")
    hoje = date.today()
    if h.valido_ate is not None and h.valido_ate < hoje:
        raise HTTPException(409, "Esta versão do horário já terminou")
    mudancas = dados.model_dump(exclude_unset=True)
    campos = {c: getattr(h, c) for c in ("turma_id", "disciplina_id", "funcionario_id", "sala_id", "dia_semana",
                                         "tempo", "hora_inicio", "hora_fim")}
    campos.update(mudancas)
    if "tempo" in mudancas and mudancas.get("hora_inicio") is None:   # outro tempo: as horas passam a ser as dele
        campos["hora_inicio"] = None
    if campos["hora_inicio"] is None or (mudancas.get("hora_fim") is None and ({"tempo", "hora_inicio"} & mudancas.keys())):
        campos["hora_inicio"], campos["hora_fim"] = _horas_do_tempo(db, est, campos["tempo"], campos["hora_inicio"], None)
    if campos["hora_fim"] <= campos["hora_inicio"]:
        raise HTTPException(422, "A hora de fim tem de ser depois da de início")
    if campos["disciplina_id"] != h.disciplina_id:
        obter(db, Disciplina, campos["disciplina_id"], est, "Disciplina")
        exigir_no_plano(db, db.get(Turma, h.turma_id).classe_id, campos["disciplina_id"])
    if campos["funcionario_id"] and campos["funcionario_id"] != h.funcionario_id:
        obter(db, Funcionario, campos["funcionario_id"], est, "Professor")
    if h.valido_desde >= hoje:            # ainda não começou (ou começa hoje): corrige-se o próprio registo
        for c, v in campos.items():
            setattr(h, c, v)
        svc.conflitos(db, h, h.valido_desde)
        alvo = h
    else:                                 # já em uso: fecha ontem e a nova versão vale a partir de hoje
        alvo = Horario(estabelecimento_id=est, valido_desde=hoje, **campos)
        h.valido_ate = hoje - timedelta(days=1)
        db.flush()
        svc.conflitos(db, alvo, hoje)
        db.add(alvo)
    auditar(db, ctx, "horario", hid, "update", {k: str(v) for k, v in campos.items()})
    db.commit()
    return _horario(db, alvo)


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
    svc.professor_livre(db, ctx.estabelecimento_id, dados.substituto_id, dados.data, h)
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
    return svc.assiduidade_professores(db, ctx.estabelecimento_id, desde, ate)


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


crud.registar(router, "/tempos-lectivos", TempoLectivo, nome="Tempo lectivo", modulo="academico",
              resposta=R.TempoLectivo, entrada=TempoLectivoIn, ao_editar=_editar_tempo)
crud.registar(router, "/eventos", EventoCalendario, nome="Evento", modulo="academico", resposta=R.EventoCalendario,
              entrada=EventoIn)
