"""Tempos lectivos com as horas de cada um (registados aos pares: 1.º e 2.º, 3.º e 4.º, 5.º e 6.º tempo).

Revision ID: 0021
Revises: 0020
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('tempo_lectivo', recreate="auto") as b:
        b.add_column(sa.Column('hora_inicio', sa.Time(), nullable=True))
        b.add_column(sa.Column('hora_fim', sa.Time(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('tempo_lectivo', recreate="auto") as b:
        b.drop_column('hora_fim')
        b.drop_column('hora_inicio')
