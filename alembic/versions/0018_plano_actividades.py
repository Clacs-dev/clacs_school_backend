"""Plano de actividades pelas fases do ano lectivo: novos tipos (matrículas, abertura, aulas, exames, recurso,
entrega de boletins, pausa, férias) e o responsável de cada actividade.

Revision ID: 0018
Revises: 0017
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ANTIGOS = ("feriado", "interrupcao", "evento", "reuniao", "prova")
NOVOS = ("matricula", "abertura", "aulas", "prova", "exame", "recurso", "entrega", "reuniao", "evento",
         "pausa", "ferias", "interrupcao", "feriado")


def _lista(tipos) -> str:
    return ",".join(f"'{t}'" for t in tipos)


def upgrade() -> None:
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.add_column(sa.Column('responsavel', sa.Text(), nullable=True))
        b.drop_constraint(op.f("ck_evento_calendario_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_evento_calendario_tipo"), f"tipo IN ({_lista(NOVOS)})")


def downgrade() -> None:
    # Os tipos novos voltam a ser «evento» (pausa e férias, que não têm aulas, voltam a «interrupcao»).
    op.execute("UPDATE evento_calendario SET tipo = 'interrupcao' WHERE tipo IN ('pausa','ferias')")
    op.execute(f"UPDATE evento_calendario SET tipo = 'evento' WHERE tipo NOT IN ({_lista(ANTIGOS)})")
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.drop_constraint(op.f("ck_evento_calendario_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_evento_calendario_tipo"), f"tipo IN ({_lista(ANTIGOS)})")
        b.drop_column('responsavel')
