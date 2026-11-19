"""Ficha da disciplina por classe (programa oficial): regime, tempos teóricos e práticos, precedência, objectivos
gerais e bibliografia.

Revision ID: 0019
Revises: 0018
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ficha_disciplina',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('classe_id', sa.BigInteger(), nullable=False),
    sa.Column('disciplina_id', sa.BigInteger(), nullable=False),
    sa.Column('regime', sa.Text(), server_default='Anual', nullable=False),
    sa.Column('tempos_teoricos', sa.SmallInteger(), nullable=True),
    sa.Column('tempos_praticos', sa.SmallInteger(), nullable=True),
    sa.Column('precedencia', sa.Text(), nullable=True),
    sa.Column('objectivos', sa.Text(), nullable=True),
    sa.Column('bibliografia', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['classe_id'], ['classe.id'], name=op.f('fk_ficha_disciplina_classe_id_classe')),
    sa.ForeignKeyConstraint(['disciplina_id'], ['disciplina.id'], name=op.f('fk_ficha_disciplina_disciplina_id_disciplina')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_ficha_disciplina_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ficha_disciplina')),
    sa.UniqueConstraint('classe_id', 'disciplina_id', name=op.f('uq_ficha_disciplina_classe_id'))
    )
    with op.batch_alter_table('ficha_disciplina', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ficha_disciplina_estabelecimento_id'), ['estabelecimento_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ficha_disciplina')
