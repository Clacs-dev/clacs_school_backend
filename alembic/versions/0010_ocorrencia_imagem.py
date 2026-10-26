"""Fotografias nas ocorrências.

Revision ID: 0010
Revises: 0009
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ocorrencia_imagem',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('ocorrencia_id', sa.BigInteger(), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('nome', sa.Text(), nullable=True),
    sa.Column('criada_em', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['ocorrencia_id'], ['ocorrencia.id'], name=op.f('fk_ocorrencia_imagem_ocorrencia_id_ocorrencia'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ocorrencia_imagem'))
    )
    op.create_index(op.f('ix_ocorrencia_imagem_ocorrencia_id'), 'ocorrencia_imagem', ['ocorrencia_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ocorrencia_imagem_ocorrencia_id'), table_name='ocorrencia_imagem')
    op.drop_table('ocorrencia_imagem')
