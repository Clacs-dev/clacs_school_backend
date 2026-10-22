"""Saúde escolar na ficha do aluno: grupo sanguíneo, condições de saúde, medicação e contacto de emergência.

Revision ID: 0005
Revises: 0004
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUNAS = ("grupo_sanguineo", "condicoes_saude", "medicacao", "contacto_emergencia", "telefone_emergencia")


def upgrade() -> None:
    with op.batch_alter_table("aluno") as b:
        for coluna in COLUNAS:
            b.add_column(sa.Column(coluna, sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("aluno") as b:
        for coluna in reversed(COLUNAS):
            b.drop_column(coluna)
