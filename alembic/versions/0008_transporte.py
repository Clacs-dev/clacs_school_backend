"""Transporte escolar: viaturas, rotas com paragens e inscrição dos alunos (ligada à cobrança do serviço).

Revision ID: 0008
Revises: 0007
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('viatura',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('matricula', sa.Text(), nullable=False),
    sa.Column('descricao', sa.Text(), nullable=True),
    sa.Column('lugares', sa.SmallInteger(), nullable=False),
    sa.Column('motorista', sa.Text(), nullable=True),
    sa.Column('telefone_motorista', sa.Text(), nullable=True),
    sa.Column('observacoes', sa.Text(), nullable=True),
    sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_viatura_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_viatura')),
    sa.UniqueConstraint('estabelecimento_id', 'matricula', name=op.f('uq_viatura_estabelecimento_id'))
    )
    op.create_table('rota_transporte',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('nome', sa.Text(), nullable=False),
    sa.Column('descricao', sa.Text(), nullable=True),
    sa.Column('viatura_id', sa.BigInteger(), nullable=True),
    sa.Column('servico_id', sa.BigInteger(), nullable=True),
    sa.Column('hora_ida', sa.Time(), nullable=True),
    sa.Column('hora_volta', sa.Time(), nullable=True),
    sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_rota_transporte_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['servico_id'], ['servico.id'], name=op.f('fk_rota_transporte_servico_id_servico')),
    sa.ForeignKeyConstraint(['viatura_id'], ['viatura.id'], name=op.f('fk_rota_transporte_viatura_id_viatura')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rota_transporte')),
    sa.UniqueConstraint('estabelecimento_id', 'nome', name=op.f('uq_rota_transporte_estabelecimento_id'))
    )
    op.create_table('paragem_transporte',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('rota_id', sa.BigInteger(), nullable=False),
    sa.Column('ordem', sa.SmallInteger(), nullable=False),
    sa.Column('nome', sa.Text(), nullable=False),
    sa.Column('referencia', sa.Text(), nullable=True),
    sa.Column('hora_ida', sa.Time(), nullable=True),
    sa.Column('hora_volta', sa.Time(), nullable=True),
    sa.ForeignKeyConstraint(['rota_id'], ['rota_transporte.id'], name=op.f('fk_paragem_transporte_rota_id_rota_transporte'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_paragem_transporte'))
    )
    op.create_table('aluno_transporte',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('aluno_id', sa.BigInteger(), nullable=False),
    sa.Column('rota_id', sa.BigInteger(), nullable=False),
    sa.Column('paragem_id', sa.BigInteger(), nullable=True),
    sa.Column('sentido', sa.Text(), server_default='ida_volta', nullable=False),
    sa.Column('data_inicio', sa.Date(), server_default=sa.func.current_date(), nullable=False),
    sa.Column('data_fim', sa.Date(), nullable=True),
    sa.Column('estado', sa.Text(), server_default='activa', nullable=False),
    sa.Column('adesao_id', sa.BigInteger(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("estado IN ('activa','suspensa','cancelada')", name=op.f('ck_aluno_transporte_estado')),
    sa.CheckConstraint("sentido IN ('ida','volta','ida_volta')", name=op.f('ck_aluno_transporte_sentido')),
    sa.ForeignKeyConstraint(['adesao_id'], ['adesao_servico.id'], name=op.f('fk_aluno_transporte_adesao_id_adesao_servico'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['aluno_id'], ['aluno.id'], name=op.f('fk_aluno_transporte_aluno_id_aluno')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_aluno_transporte_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['paragem_id'], ['paragem_transporte.id'], name=op.f('fk_aluno_transporte_paragem_id_paragem_transporte'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['rota_id'], ['rota_transporte.id'], name=op.f('fk_aluno_transporte_rota_id_rota_transporte')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_aluno_transporte'))
    )


def downgrade() -> None:
    for tabela in ("aluno_transporte", "paragem_transporte", "rota_transporte", "viatura"):
        op.drop_table(tabela)
