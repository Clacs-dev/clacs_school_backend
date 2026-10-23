"""Pedidos do encarregado (vaga, matrícula, transferência, declaração, justificação de falta) e o seu alerta.

Revision ID: 0006
Revises: 0005
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ALERTAS = ("'risco_incumprimento','queda_desempenho','padrao_faltas','divergencia_primavera','comportamento',"
           "'comprovativo_pagamento'")


def _tipos_alerta(lista: str) -> None:
    with op.batch_alter_table("alerta", recreate="auto") as b:
        b.drop_constraint(op.f("ck_alerta_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_alerta_tipo"), f"tipo IN ({lista})")


def upgrade() -> None:
    op.create_table(
        "pedido",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=False), nullable=False),
        sa.Column("estabelecimento_id", sa.BigInteger(), sa.ForeignKey("estabelecimento.id", name=op.f("fk_pedido_estabelecimento_id_estabelecimento")), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("encarregado_id", sa.BigInteger(), sa.ForeignKey("encarregado.id", name=op.f("fk_pedido_encarregado_id_encarregado")), nullable=False),
        sa.Column("aluno_id", sa.BigInteger(), sa.ForeignKey("aluno.id", name=op.f("fk_pedido_aluno_id_aluno")), nullable=True),
        sa.Column("dados", JSON, nullable=False),
        sa.Column("estado", sa.Text(), server_default="pendente", nullable=False),
        sa.Column("resposta", sa.Text(), nullable=True),
        sa.Column("resultado", JSON, nullable=True),
        sa.Column("decidido_por", sa.BigInteger(), sa.ForeignKey("utilizador.id", name=op.f("fk_pedido_decidido_por_utilizador")), nullable=True),
        sa.Column("decidido_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("tipo IN ('vaga','matricula','transferencia','declaracao','justificacao_falta')",
                           name=op.f("ck_pedido_tipo")),
        sa.CheckConstraint("estado IN ('pendente','aprovado','rejeitado','cancelado')", name=op.f("ck_pedido_estado")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pedido")),
    )
    op.create_index("ix_pedido_estado", "pedido", ["estabelecimento_id", "estado"])
    _tipos_alerta(ALERTAS + ",'pedido_encarregado'")


def downgrade() -> None:
    op.execute("DELETE FROM alerta WHERE tipo = 'pedido_encarregado'")
    _tipos_alerta(ALERTAS)
    op.drop_index("ix_pedido_estado", table_name="pedido")
    op.drop_table("pedido")
