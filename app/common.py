from datetime import date
from decimal import Decimal
from typing import TypeVar

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.models import AnoLectivo, Base

M = TypeVar("M", bound=Base)


def as_dict(obj: Base) -> dict:
    """Colunas do registo como dicionário serializável em JSON."""
    return jsonable_encoder({c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs})


def obter(db: Session, model: type[M], id_: int, estabelecimento_id: int | None = None, nome: str | None = None) -> M:
    """Carrega um registo por id, garantindo que pertence ao estabelecimento do utilizador."""
    obj = db.get(model, id_)
    if obj is None or (estabelecimento_id is not None and getattr(obj, "estabelecimento_id", estabelecimento_id) != estabelecimento_id):
        raise HTTPException(404, f"{nome or model.__tablename__} {id_} não encontrado")
    return obj


def ano_activo(db: Session, estabelecimento_id: int, obrigatorio: bool = True) -> AnoLectivo | None:
    ano = db.scalar(select(AnoLectivo).where(AnoLectivo.estabelecimento_id == estabelecimento_id,
                                             AnoLectivo.estado == "activo"))
    if ano is None and obrigatorio:
        raise HTTPException(409, "Não há ano lectivo activo. Active um ano lectivo primeiro.")
    return ano


def primeiro_dia(d: date) -> date:
    return d.replace(day=1)


def soma_meses(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def dinheiro(v) -> float:
    return float(v or Decimal(0))


def aplicar(obj: Base, dados: dict) -> None:
    for k, v in dados.items():
        setattr(obj, k, v)
