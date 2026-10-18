from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Assiduidade, Avaliacao, Matricula, Nota, ResultadoPeriodo, TipoAvaliacao

FALTAS = ("falta_justificada", "falta_injustificada")


def media_ponderada(db: Session, aluno_id: int, turma_id: int, disciplina_id: int | None = None,
                    periodo_id: int | None = None) -> float | None:
    stmt = (select(func.sum(Nota.valor * TipoAvaliacao.peso), func.sum(TipoAvaliacao.peso))
            .join(Avaliacao, Avaliacao.id == Nota.avaliacao_id)
            .join(TipoAvaliacao, TipoAvaliacao.id == Avaliacao.tipo_id)
            .where(Nota.aluno_id == aluno_id, Avaliacao.turma_id == turma_id, Nota.valor.is_not(None)))
    if disciplina_id:
        stmt = stmt.where(Avaliacao.disciplina_id == disciplina_id)
    if periodo_id:
        stmt = stmt.where(Avaliacao.periodo_id == periodo_id)
    soma, pesos = db.execute(stmt).one()
    if not pesos:
        return None
    return float((Decimal(soma) / Decimal(pesos)).quantize(Decimal("0.1"), ROUND_HALF_UP))


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
