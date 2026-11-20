"""Calendário escolar (feriados, interrupções, eventos), horários com versões e as aulas previstas de cada dia."""
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AnoLectivo, Aula, Disciplina, EventoCalendario, Funcionario, Horario, Parametro, PeriodoLectivo, Substituicao, Turma,
)

SEM_AULAS = ("feriado", "interrupcao", "pausa", "ferias")


def _eventos_entre(db: Session, est: int, desde: date, ate: date) -> list[EventoCalendario]:
    return list(db.scalars(select(EventoCalendario).where(
        EventoCalendario.estabelecimento_id == est, EventoCalendario.data_inicio <= ate,
        func.coalesce(EventoCalendario.data_fim, EventoCalendario.data_inicio) >= desde)
        .order_by(EventoCalendario.data_inicio, EventoCalendario.id)))


def semanas_do_ciclo(db: Session, ano: AnoLectivo) -> dict:
    """Semanas do ciclo (segunda a domingo), como no calendário do ano académico: a fase de cada semana (preparação,
    o trimestre, pausa, fim do ano), o número da semana lectiva dentro do trimestre, os dias com aulas (segunda a
    sexta, dentro de um trimestre e fora de feriados, interrupções, pausas e férias) e as actividades do plano."""
    est, ini, fim = ano.estabelecimento_id, ano.data_inicio, ano.data_fim
    periodos = list(db.scalars(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == ano.id).order_by(PeriodoLectivo.ordem)))
    eventos = _eventos_entre(db, est, ini, fim)
    sem_aulas = dias_sem_aulas(db, est, ini, fim)
    resumo = {p.id: {"id": p.id, "designacao": p.designacao, "data_inicio": p.data_inicio, "data_fim": p.data_fim,
                     "semanas_lectivas": 0, "dias_lectivos": 0} for p in periodos}

    def periodo_de(d: date) -> PeriodoLectivo | None:
        return next((p for p in periodos if p.data_inicio <= d <= p.data_fim), None)

    semanas, numero = [], {}
    segunda = ini - timedelta(days=ini.weekday())
    while segunda <= fim:
        domingo = segunda + timedelta(days=6)
        uteis = [segunda + timedelta(days=i) for i in range(5) if ini <= segunda + timedelta(days=i) <= fim]
        lectivos = [d for d in uteis if periodo_de(d) and d not in sem_aulas]
        periodo = periodo_de(lectivos[0]) if lectivos else next((periodo_de(d) for d in uteis if periodo_de(d)), None)
        if periodo is not None:
            fase = periodo.designacao
        elif not periodos or segunda + timedelta(days=4) < periodos[0].data_inicio:
            fase = "Preparação do ano"
        elif segunda > periodos[-1].data_fim:
            fase = "Fim do ano"
        else:
            fase = "Pausa"
        n = None
        if lectivos:
            numero[periodo.id] = n = numero.get(periodo.id, 0) + 1
            resumo[periodo.id]["semanas_lectivas"] += 1
            resumo[periodo.id]["dias_lectivos"] += len(lectivos)
        semanas.append({
            "inicio": segunda, "fim": domingo, "numero": n, "fase": fase, "periodo_id": periodo.id if periodo else None,
            "lectiva": bool(lectivos), "dias_lectivos": len(lectivos),
            "actividades": [{"id": e.id, "titulo": e.titulo, "tipo": e.tipo, "abrangencia": e.abrangencia, "data_inicio": e.data_inicio, "data_fim": e.data_fim}
                            for e in eventos if e.data_inicio <= domingo and (e.data_fim or e.data_inicio) >= segunda]})
        segunda += timedelta(days=7)
    lista = list(resumo.values())
    return {"ano_id": ano.id, "designacao": ano.designacao, "data_inicio": ini, "data_fim": fim,
            "semanas_lectivas": sum(p["semanas_lectivas"] for p in lista), "dias_lectivos": sum(p["dias_lectivos"] for p in lista),
            "periodos": lista, "semanas": semanas}


def assiduidade_professores(db: Session, est: int, desde: date, ate: date) -> list[dict]:
    """Mapa de assiduidade dos professores no período (até hoje): aulas e horas previstas, faltas, leccionadas e por
    registar, substituições feitas e minutos de atraso. A aula dada por um substituto conta para o substituto."""
    def zero():
        return {"previstas": 0, "dadas": 0, "faltas": 0, "por_registar": 0, "substituicoes_feitas": 0, "minutos_atraso": 0,
                "minutos_previstos": 0, "minutos_dados": 0, "minutos_faltas": 0, "minutos_por_registar": 0}
    stats: dict[int, dict] = {}
    d = desde
    while d <= min(ate, date.today()):
        for x in aulas_do_dia(db, est, d):
            if x["professor_id"] is None:
                continue
            s = stats.setdefault(x["professor_id"], zero())
            dur = (x["hora_fim"].hour * 60 + x["hora_fim"].minute) - (x["hora_inicio"].hour * 60 + x["hora_inicio"].minute)
            s["previstas"] += 1
            s["minutos_previstos"] += dur
            s["substituicoes_feitas"] += x["substituicao"]
            chave = {"dada": "dados", "falta_professor": "faltas"}.get(x["estado"], "por_registar")
            s[{"dados": "dadas"}.get(chave, chave)] += 1
            s[f"minutos_{chave}"] += dur
        d += timedelta(days=1)
    for a in db.scalars(select(Aula).where(Aula.data.between(desde, ate), Aula.atraso_minutos > 0)):
        if a.funcionario_id in stats:
            stats[a.funcionario_id]["minutos_atraso"] += a.atraso_minutos
    linhas = [{"funcionario_id": fid, "professor": db.get(Funcionario, fid).nome_completo, **s} for fid, s in stats.items()]
    return sorted(linhas, key=lambda x: x["professor"].lower())


def _mesmo_dia_noutro_ano(d: date, anos: int) -> date:
    try:
        return d.replace(year=d.year + anos)
    except ValueError:          # 29 de Fevereiro num ano que não é bissexto
        return d.replace(year=d.year + anos, day=28)


def copiar_plano(db: Session, origem: AnoLectivo, destino: AnoLectivo) -> dict:
    """Copia as actividades do plano de um ciclo para outro. As datas avançam um número inteiro de semanas (a reunião
    de segunda-feira continua a ser à segunda-feira); os feriados ficam no mesmo dia e mês. Não copia o que já existe
    no destino (mesmo tipo, título e data) nem o que ficaria fora das datas do ciclo."""
    semanas = round((destino.data_inicio - origem.data_inicio).days / 7)
    anos = destino.data_inicio.year - origem.data_inicio.year
    existentes = {(e.tipo, e.titulo.strip().lower(), e.data_inicio)
                  for e in _eventos_entre(db, destino.estabelecimento_id, destino.data_inicio, destino.data_fim)}
    res = {"copiadas": 0, "repetidas": 0, "fora_do_ciclo": 0}
    for e in _eventos_entre(db, origem.estabelecimento_id, origem.data_inicio, origem.data_fim):
        if e.tipo == "feriado":
            mudar = lambda d: _mesmo_dia_noutro_ano(d, anos)  # noqa: E731
        else:
            mudar = lambda d: d + timedelta(weeks=semanas)  # noqa: E731
        inicio, fim = mudar(e.data_inicio), e.data_fim and mudar(e.data_fim)
        if inicio < destino.data_inicio or (fim or inicio) > destino.data_fim:
            res["fora_do_ciclo"] += 1
        elif (e.tipo, e.titulo.strip().lower(), inicio) in existentes:
            res["repetidas"] += 1
        else:
            db.add(EventoCalendario(estabelecimento_id=destino.estabelecimento_id, tipo=e.tipo, titulo=e.titulo,
                                    data_inicio=inicio, data_fim=fim, descricao=e.descricao, responsavel=e.responsavel,
                                    objectivo=e.objectivo, abrangencia=e.abrangencia,
                                    publico=e.publico))
            existentes.add((e.tipo, e.titulo.strip().lower(), inicio))
            res["copiadas"] += 1
    return res


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


DIAS = ("", "segunda", "terça", "quarta", "quinta", "sexta", "sábado")


def _hm(t) -> str:
    return t.strftime("%H:%M")


def _descrever(db: Session, h: Horario) -> str:
    """Ex.: "Língua Portuguesa na 5.ª A, segunda das 08:10 às 09:00"."""
    return (f"{db.get(Disciplina, h.disciplina_id).nome} na {db.get(Turma, h.turma_id).designacao}, "
            f"{DIAS[h.dia_semana]} das {_hm(h.hora_inicio)} às {_hm(h.hora_fim)}")


def conflitos(db: Session, h: Horario, desde: date) -> None:
    """Recusa (409) o tempo lectivo se, no mesmo dia da semana e com horas sobrepostas, a turma, o professor ou a sala
    já estiverem ocupados noutra versão em vigor a partir de `desde`. Só conta o mesmo ciclo (ano lectivo) da turma:
    horários de anos anteriores nunca bloqueiam. Outro professor (mesmo da mesma disciplina) pode dar aula à mesma
    hora noutra turma."""
    ano_id = db.get(Turma, h.turma_id).ano_id
    base = (select(Horario).join(Turma, Turma.id == Horario.turma_id)
            .where(Horario.estabelecimento_id == h.estabelecimento_id, Turma.ano_id == ano_id,
                   Horario.dia_semana == h.dia_semana, Horario.id != (h.id or 0),
                   or_(Horario.valido_ate.is_(None), Horario.valido_ate >= desde),
                   Horario.hora_inicio < h.hora_fim, Horario.hora_fim > h.hora_inicio))
    if h.funcionario_id and (outro := db.scalar(base.where(Horario.funcionario_id == h.funcionario_id).limit(1))):
        prof = db.get(Funcionario, h.funcionario_id).nome_completo
        raise HTTPException(409, f"Conflito: o professor {prof} já dá {_descrever(db, outro)}. "
                                 "Escolha outro tempo ou outro professor.")
    if outro := db.scalar(base.where(Horario.turma_id == h.turma_id).limit(1)):
        raise HTTPException(409, f"Conflito: a turma já tem {_descrever(db, outro)}. Escolha outro tempo.")
    if h.sala_id and (outro := db.scalar(base.where(Horario.sala_id == h.sala_id).limit(1))):
        raise HTTPException(409, f"Conflito: a sala já está ocupada com {_descrever(db, outro)}. Escolha outra sala ou outro tempo.")


def professor_livre(db: Session, est: int, funcionario_id: int, dia: date, h: Horario) -> None:
    """Recusa (409) um substituto que nesse dia, a essa hora, já dá uma aula (dele ou como substituto)."""
    for outro in horarios_validos(db, est, dia, funcionario_id=funcionario_id):
        if outro.id != h.id and outro.hora_inicio < h.hora_fim and outro.hora_fim > h.hora_inicio:
            prof = db.get(Funcionario, funcionario_id).nome_completo
            raise HTTPException(409, f"Conflito: o professor {prof} já dá {_descrever(db, outro)} nesse dia. "
                                     "Escolha outro substituto.")


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
