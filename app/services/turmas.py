"""Ocupação das turmas: capacidade por omissão, turma cheia e a próxima turma com vaga (A → B → C…)."""
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Classe, Matricula, Parametro, Turma

CAPACIDADE_PADRAO = 7  # alunos por turma quando a escola não definiu o parâmetro capacidade_turma


def capacidade_padrao(db: Session, est: int) -> int:
    p = db.get(Parametro, (est, "capacidade_turma"))
    return int(p.valor) if p is not None else CAPACIDADE_PADRAO


def ocupacao(db: Session, turma_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Matricula)
                     .where(Matricula.turma_id == turma_id, Matricula.estado == "activa"))


def vagas(db: Session, t: Turma) -> dict:
    n = ocupacao(db, t.id)
    return {"id": t.id, "designacao": t.designacao, "periodo_dia": t.periodo_dia, "alunos": n,
            "capacidade": t.capacidade, "vagas": max(0, t.capacidade - n), "cheia": n >= t.capacidade}


def com_vaga(db: Session, est: int, ano_id: int, classe_id: int, depois_de: Turma | None = None) -> Turma | None:
    """A primeira turma da classe com vaga, por ordem de designação; depois_de = começa na turma seguinte a essa."""
    turmas = db.scalars(select(Turma).where(Turma.estabelecimento_id == est, Turma.ano_id == ano_id,
                                            Turma.classe_id == classe_id).order_by(Turma.designacao)).all()
    if depois_de is not None:
        seguintes = [t for t in turmas if t.designacao > depois_de.designacao]
        anteriores = [t for t in turmas if t.designacao < depois_de.designacao]
        turmas = seguintes + anteriores
    return next((t for t in turmas if ocupacao(db, t.id) < t.capacidade), None)


def garantir_vaga(db: Session, t: Turma) -> None:
    """409 se a turma estiver cheia, dizendo qual é a próxima turma da mesma classe com vaga."""
    n = ocupacao(db, t.id)
    if n < t.capacidade:
        return
    seguinte = com_vaga(db, t.estabelecimento_id, t.ano_id, t.classe_id, depois_de=t)
    if seguinte is not None:
        dica = f"Próxima turma com vaga: {seguinte.designacao} (turma_id {seguinte.id})."
    else:
        classe = db.get(Classe, t.classe_id)
        dica = f"Não há outra turma da {classe.designacao if classe else 'mesma classe'} com vaga: crie uma nova turma."
    raise HTTPException(409, f"A turma {t.designacao} está cheia ({n}/{t.capacidade}). {dica}")


def escolher(db: Session, est: int, ano_id: int, classe_id: int) -> Turma:
    """Matrícula por classe: a primeira turma com vaga (enche a A, depois a B, …)."""
    t = com_vaga(db, est, ano_id, classe_id)
    if t is None:
        classe = db.get(Classe, classe_id)
        raise HTTPException(409, f"Não há turma da {classe.designacao if classe else f'classe {classe_id}'} com vaga "
                                 "neste ano lectivo: crie uma nova turma.")
    return t
