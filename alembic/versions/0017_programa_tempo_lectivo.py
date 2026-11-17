"""Unidade do programa ligada a um tempo lectivo registado (1.º, 2.º… tempo).

Revision ID: 0017
Revises: 0016
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('programa_unidade', recreate="auto") as b:
        b.add_column(sa.Column('tempo_lectivo_id', sa.BigInteger(), nullable=True))
        b.create_foreign_key(op.f('fk_programa_unidade_tempo_lectivo_id_tempo_lectivo'), 'tempo_lectivo',
                             ['tempo_lectivo_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('programa_unidade', recreate="auto") as b:
        b.drop_constraint(op.f('fk_programa_unidade_tempo_lectivo_id_tempo_lectivo'), type_='foreignkey')
        b.drop_column('tempo_lectivo_id')
