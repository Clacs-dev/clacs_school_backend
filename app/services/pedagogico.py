from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Aluno, Assiduidade, Avaliacao, ClasseDisciplina, Disciplina, Matricula, Nota, Parametro, PeriodoLectivo,
    ResultadoPeriodo, TipoAvaliacao, Turma,
)

FALTAS = ("falta_justificada", "falta_injustificada")
NOTA_MAXIMA = 10  # escala por omissão (Talatona Kids: 0 a 10); o parâmetro nota_maxima muda-a, ex. 20


def escala(db: Session, est: int) -> dict:
    """Escala das notas: nota_maxima (10) e nota_minima para positiva/aprovação (metade da máxima)."""
    maxima = db.get(Parametro, (est, "nota_maxima"))
    maxima = float(maxima.valor) if maxima is not None else float(NOTA_MAXIMA)
    minima = db.get(Parametro, (est, "nota_minima"))
    return {"nota_maxima": maxima, "nota_minima": float(minima.valor) if minima is not None else maxima / 2}


def media_ponderada(db: Session, aluno_id: int, turma_id: int, disciplina_id: int | None = None,
                    periodo_id: int | None = None, mac: bool | None = None) -> float | None:
    """mac=True: só as avaliações contínuas (MAC); mac=False: só as provas; None: todas, pelo peso de cada tipo."""
    stmt = (select(func.sum(Nota.valor * TipoAvaliacao.peso), func.sum(TipoAvaliacao.peso))
            .join(Avaliacao, Avaliacao.id == Nota.avaliacao_id)
            .join(TipoAvaliacao, TipoAvaliacao.id == Avaliacao.tipo_id)
            .where(Nota.aluno_id == aluno_id, Avaliacao.turma_id == turma_id, Nota.valor.is_not(None)))
    if disciplina_id:
        stmt = stmt.where(Avaliacao.disciplina_id == disciplina_id)
    if periodo_id:
        stmt = stmt.where(Avaliacao.periodo_id == periodo_id)
    if mac is not None:
        stmt = stmt.where(e_mac(TipoAvaliacao.designacao) if mac else ~e_mac(TipoAvaliacao.designacao))
    soma, pesos = db.execute(stmt).one()
    if not pesos:
        return None
    return float((Decimal(soma) / Decimal(pesos)).quantize(Decimal("0.1"), ROUND_HALF_UP))


def periodo_actual(db: Session, ano_id: int, hoje: date | None = None) -> PeriodoLectivo | None:
    """O trimestre em curso; entre trimestres, o último que já começou; antes do ano, o primeiro."""
    hoje = hoje or date.today()
    periodos = db.scalars(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == ano_id).order_by(PeriodoLectivo.ordem)).all()
    comecados = [p for p in periodos if p.data_inicio <= hoje]
    return comecados[-1] if comecados else (periodos[0] if periodos else None)


def e_mac(designacao):
    """Tipos de avaliação contínua (MAC): designação com "MAC" ou "contínua"."""
    return designacao.ilike("%mac%") | designacao.ilike("%contínua%") | designacao.ilike("%continua%")


def _media(valores: list[float | None]) -> float | None:
    validos = [v for v in valores if v is not None]
    return round(sum(validos) / len(validos), 1) if validos else None


def pauta_final(db: Session, turma: Turma) -> dict:
    """Pauta final da turma: média de cada disciplina em cada trimestre, a classificação final e a situação."""
    periodos = db.scalars(select(PeriodoLectivo).where(PeriodoLectivo.ano_id == turma.ano_id)
                          .order_by(PeriodoLectivo.ordem)).all()
    discs = db.execute(select(Disciplina.id, Disciplina.nome, Disciplina.sigla)
                       .join(ClasseDisciplina, ClasseDisciplina.disciplina_id == Disciplina.id)
                       .where(ClasseDisciplina.classe_id == turma.classe_id).order_by(Disciplina.nome)).all()
    alunos = db.execute(select(Aluno.id, Aluno.numero_processo, Aluno.nome_completo)
                        .join(Matricula, Matricula.aluno_id == Aluno.id)
                        .where(Matricula.turma_id == turma.id, Matricula.estado == "activa")
                        .order_by(Aluno.nome_completo)).all()
    esc = escala(db, turma.estabelecimento_id)
    linhas = []
    for aid, processo, nome in alunos:
        disciplinas = []
        for did, dnome, _ in discs:
            trimestres = [media_ponderada(db, aid, turma.id, did, p.id) for p in periodos]
            disciplinas.append({"disciplina_id": did, "disciplina": dnome, "trimestres": trimestres,
                                "final": _media(trimestres)})
        finais = [d["final"] for d in disciplinas]
        final = _media(finais)
        linhas.append({"aluno_id": aid, "numero_processo": processo, "aluno": nome, "disciplinas": disciplinas,
                       "medias_trimestre": [_media([d["trimestres"][i] for d in disciplinas]) for i in range(len(periodos))],
                       "media_final": final,
                       "negativas": sum(1 for f in finais if f is not None and f < esc["nota_minima"]),
                       "situacao": None if final is None else ("aprovado" if final >= esc["nota_minima"] else "reprovado")})
    return {"turma_id": turma.id, "turma": turma.designacao, "periodos": [p.designacao for p in periodos],
            "disciplinas": [{"id": did, "nome": n, "sigla": s} for did, n, s in discs], "linhas": linhas, **esc}


def media_geral(db: Session, m: Matricula) -> float | None:
    """Média das pautas trimestrais; sem pautas fechadas, a média ponderada das notas lançadas."""
    media = db.scalar(select(func.avg(ResultadoPeriodo.media)).where(ResultadoPeriodo.matricula_id == m.id))
    if media is not None:
        return round(float(media), 1)
    return media_ponderada(db, m.aluno_id, m.turma_id)


def total_faltas(db: Session, aluno_id: int, turma_id: int | None = None) -> int:
    stmt = select(func.count()).select_from(Assiduidade).where(Assiduidade.aluno_id == aluno_id,
                                                               Assiduidade.estado.in_(FALTAS))
    if turma_id:
        stmt = stmt.where(Assiduidade.turma_id == turma_id)
    return db.scalar(stmt) or 0
