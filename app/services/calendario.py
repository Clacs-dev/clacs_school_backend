"""Calendário escolar (feriados, interrupções, eventos), horários com versões e as aulas previstas de cada dia."""
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Aula, Disciplina, EventoCalendario, Funcionario, Horario, Parametro, Substituicao, Turma

SEM_AULAS = ("feriado", "interrupcao")


def dias_sem_aulas(db: Session, est: int, desde: date, ate: date) -> set[date]:
    """Dias de feriado ou interrupção lectiva entre as duas datas."""
    dias = set()
    for e in db.scalars(select(EventoCalendario).where(
            EventoCalendario.estabelecimento_id == est, EventoCalendario.tipo.in_(SEM_AULAS),
            EventoCalendario.data_inicio <= ate,
            func.coalesce(EventoCalendario.data_fim, EventoCalendario.data_inicio) >= desde)):
        d = max(e.data_inicio, desde)
        while d <= min(e.data_fim or e.data_inicio, ate):
            dias.add(d)
            d += timedelta(days=1)
    return dias


def somar_dias_uteis(db: Session, est: int, inicio: date, n: int) -> date:
    """Data ao fim de n dias úteis (sem fins de semana, feriados e interrupções)."""
    fechados = dias_sem_aulas(db, est, inicio, inicio + timedelta(days=n * 3 + 30))
    d, contados = inicio, 0
    while contados < n:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in fechados:
            contados += 1
    return d


def prazo_pedidos(db: Session, est: int) -> int:
    p = db.get(Parametro, (est, "prazo_pedidos_dias"))
    return int(p.valor) if p is not None else 5


def horarios_validos(db: Session, est: int, dia: date, turma_id: int | None = None,
                     funcionario_id: int | None = None) -> list[Horario]:
    """Tempos lectivos em vigor nesse dia da semana (a versão válida nessa data)."""
    stmt = select(Horario).where(Horario.estabelecimento_id == est, Horario.dia_semana == dia.isoweekday(),
                                 Horario.valido_desde <= dia, or_(Horario.valido_ate.is_(None), Horario.valido_ate >= dia))
    if turma_id:
        stmt = stmt.where(Horario.turma_id == turma_id)
    lista = list(db.scalars(stmt.order_by(Horario.turma_id, Horario.tempo)))
    if funcionario_id:
        subs = {s.horario_id: s for s in db.scalars(select(Substituicao).where(Substituicao.data == dia))}
        lista = [h for h in lista if (subs[h.id].substituto_id if h.id in subs else h.funcionario_id) == funcionario_id]
    return lista


def conflitos(db: Session, h: Horario, desde: date) -> None:
    """Turma, professor ou sala já ocupados nesse dia e hora (versões em vigor a partir de desde)."""
    base = select(Horario).where(Horario.estabelecimento_id == h.estabelecimento_id, Horario.dia_semana == h.dia_semana,
                                 Horario.id != (h.id or 0), or_(Horario.valido_ate.is_(None), Horario.valido_ate >= desde),
                                 Horario.hora_inicio < h.hora_fim, Horario.hora_fim > h.hora_inicio)
    for coluna, valor, nome in ((Horario.turma_id, h.turma_id, "a turma"), (Horario.funcionario_id, h.funcionario_id, "o professor"),
                                (Horario.sala_id, h.sala_id, "a sala")):
        if valor and db.scalar(base.where(coluna == valor).limit(1)):
            raise HTTPException(409, f"Conflito: {nome} já tem aula nesse dia e hora")


def aulas_do_dia(db: Session, est: int, dia: date, turma_id: int | None = None, funcionario_id: int | None = None) -> list[dict]:
    if dia in dias_sem_aulas(db, est, dia, dia):
        return []
    subs = {s.horario_id: s for s in db.scalars(select(Substituicao).where(Substituicao.data == dia))}
    registos = {a.horario_id: a for a in db.scalars(select(Aula).where(Aula.data == dia))}
    linhas = []
    for h in horarios_validos(db, est, dia, turma_id, funcionario_id):
        s, a = subs.get(h.id), registos.get(h.id)
        prof = db.get(Funcionario, s.substituto_id if s else h.funcionario_id) if (s or h.funcionario_id) else None
        linhas.append({"horario_id": h.id, "data": dia, "turma_id": h.turma_id, "turma": db.get(Turma, h.turma_id).designacao,
                       "disciplina_id": h.disciplina_id, "disciplina": db.get(Disciplina, h.disciplina_id).nome,
                       "tempo": h.tempo, "hora_inicio": h.hora_inicio, "hora_fim": h.hora_fim,
                       "professor_id": prof.id if prof else None, "professor": prof.nome_completo if prof else None,
                       "substituicao": s is not None, "registada": a is not None, "estado": a.estado if a else None,
                       "sumario": a.sumario if a else None})
    return linhas
