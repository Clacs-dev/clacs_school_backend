"""Ordem (posição numa lista) de níveis de ensino, classes e unidades do programa.

A ordem não é um identificador: é a posição na sequência (1.ª Classe = 1, 2.ª = 2…). Regras:
- ao criar sem ordem, o sistema dá a seguinte (a maior + 1);
- ao criar com uma ordem já usada, recusa (409) e diz quem a tem;
- ao editar para uma ordem já usada, os dois registos trocam de posição (nunca ficam duas iguais).

Os dados antigos com ordens repetidas não são alterados automaticamente: corrigem-se editando um deles.
"""
from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session


def proxima(db: Session, irmaos: Select) -> int:
    """Ordem seguinte entre os irmãos (consulta das entidades do mesmo grupo)."""
    sub = irmaos.subquery()
    return (db.scalar(select(func.max(sub.c.ordem))) or 0) + 1


def _ocupante(db: Session, modelo, irmaos: Select, ordem: int, excepto: int | None):
    stmt = irmaos.where(modelo.ordem == ordem)
    if excepto:
        stmt = stmt.where(modelo.id != excepto)
    return db.scalars(stmt.limit(1)).first()


def ao_criar(db: Session, modelo, irmaos: Select, ordem: int | None, nome: str, rotulo) -> int:
    """Ordem a gravar num registo novo: a seguinte, ou a pedida se estiver livre."""
    if ordem is None:
        return proxima(db, irmaos)
    if outro := _ocupante(db, modelo, irmaos, ordem, None):
        raise HTTPException(409, f"Já existe {nome} com a ordem {ordem}: {rotulo(outro)}. "
                                 "Deixe a ordem em branco para usar a seguinte.")
    return ordem


def ao_editar(db: Session, obj, irmaos: Select, mudancas: dict) -> None:
    """Muda a ordem do registo; se outra já a tiver, essa fica com a ordem antiga deste (troca)."""
    nova = mudancas.pop("ordem", None)
    if nova is None or nova == obj.ordem:
        return
    if outro := _ocupante(db, type(obj), irmaos, nova, obj.id):
        outro.ordem = obj.ordem
    obj.ordem = nova


def reordenar(db: Session, modelo, irmaos: Select, ids: list[int]) -> list:
    """Põe os registos pela ordem dos ids (1, 2, 3…); os do grupo que não vierem na lista ficam a seguir, pela ordem
    em que estavam. Corrige também ordens repetidas antigas. Usado por «Subir» e «Descer»."""
    todos = list(db.scalars(irmaos.order_by(modelo.ordem, modelo.id)))
    por_id = {o.id: o for o in todos}
    if len(set(ids)) != len(ids) or any(i not in por_id for i in ids):
        raise HTTPException(422, "A lista tem registos repetidos ou que não pertencem a este grupo")
    nova = [por_id[i] for i in ids] + [o for o in todos if o.id not in set(ids)]
    for n, o in enumerate(nova, start=1):
        o.ordem = n
    db.commit()
    return nova
