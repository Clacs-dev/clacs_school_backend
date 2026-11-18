"""Regra de negócio: o plano curricular da classe define as disciplinas.

Uma turma só tem professores, horário, programa, avaliações e pauta das disciplinas do plano curricular da sua
classe (Registo académico → Classes → Plano curricular).
"""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Classe, ClasseDisciplina, Disciplina


def disciplinas_do_plano(db: Session, classe_id: int) -> list[int]:
    return list(db.scalars(select(ClasseDisciplina.disciplina_id).where(ClasseDisciplina.classe_id == classe_id)))


def exigir_no_plano(db: Session, classe_id: int, disciplina_id: int) -> None:
    """Recusa (422) uma disciplina que não está no plano curricular da classe."""
    if db.get(ClasseDisciplina, (classe_id, disciplina_id)) is None:
        disciplina = db.get(Disciplina, disciplina_id)
        classe = db.get(Classe, classe_id)
        raise HTTPException(422, f"A disciplina «{disciplina.nome if disciplina else disciplina_id}» não faz parte do plano "
                                 f"curricular da {classe.designacao if classe else 'classe'}. Associe-a primeiro em "
                                 "Registo académico → Classes → Plano curricular.")
