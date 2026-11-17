"""Tempos lectivos da escola (1.º, 2.º… tempo do dia) com a duração em minutos.

Revision ID: 0016
Revises: 0015
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('tempo_lectivo',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('ordem', sa.SmallInteger(), nullable=False),
    sa.Column('designacao', sa.Text(), nullable=False),
    sa.Column('duracao_minutos', sa.SmallInteger(), server_default='45', nullable=False),
    sa.CheckConstraint('duracao_minutos BETWEEN 5 AND 240', name=op.f('ck_tempo_lectivo_duracao')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_tempo_lectivo_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tempo_lectivo'))
    )
    with op.batch_alter_table('tempo_lectivo', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_tempo_lectivo_estabelecimento_id'), ['estabelecimento_id'], unique=False)


def downgrade() -> None:
    op.drop_table('tempo_lectivo')
