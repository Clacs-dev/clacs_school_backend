"""Programa das disciplinas: unidades temáticas (objectivos, conteúdos, trimestre e tempos previstos) por classe.

Revision ID: 0015
Revises: 0014
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('programa_unidade',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('classe_id', sa.BigInteger(), nullable=False),
    sa.Column('disciplina_id', sa.BigInteger(), nullable=False),
    sa.Column('ordem', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('titulo', sa.Text(), nullable=False),
    sa.Column('objectivos', sa.Text(), nullable=True),
    sa.Column('conteudos', sa.Text(), nullable=True),
    sa.Column('trimestre', sa.SmallInteger(), nullable=True),
    sa.Column('tempos_previstos', sa.SmallInteger(), nullable=True),
    sa.CheckConstraint('trimestre BETWEEN 1 AND 3', name=op.f('ck_programa_unidade_trimestre')),
    sa.ForeignKeyConstraint(['classe_id'], ['classe.id'], name=op.f('fk_programa_unidade_classe_id_classe')),
    sa.ForeignKeyConstraint(['disciplina_id'], ['disciplina.id'], name=op.f('fk_programa_unidade_disciplina_id_disciplina')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_programa_unidade_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_programa_unidade'))
    )
    with op.batch_alter_table('programa_unidade', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_programa_unidade_estabelecimento_id'), ['estabelecimento_id'], unique=False)
        batch_op.create_index('ix_programa_unidade_classe_disciplina', ['classe_id', 'disciplina_id'], unique=False)


def downgrade() -> None:
    op.drop_table('programa_unidade')
