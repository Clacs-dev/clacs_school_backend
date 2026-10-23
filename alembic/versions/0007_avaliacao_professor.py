"""Avaliação dos professores pelos encarregados (1 a 5, com comentário), uma por educando, professor e trimestre.

Revision ID: 0007
Revises: 0006
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _fk(coluna: str, tabela: str) -> sa.Column:
    return sa.Column(coluna, sa.BigInteger(), sa.ForeignKey(
        f"{tabela}.id", name=op.f(f"fk_avaliacao_professor_{coluna}_{tabela}")), nullable=False)


def upgrade() -> None:
    op.create_table(
        "avaliacao_professor",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), sa.Identity(always=False), nullable=False),
        _fk("estabelecimento_id", "estabelecimento"), _fk("encarregado_id", "encarregado"),
        _fk("funcionario_id", "funcionario"), _fk("aluno_id", "aluno"), _fk("periodo_id", "periodo_lectivo"),
        sa.Column("nota", sa.SmallInteger(), nullable=False),
        sa.Column("comentario", sa.Text(), nullable=True),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("nota BETWEEN 1 AND 5", name=op.f("ck_avaliacao_professor_nota")),
        sa.UniqueConstraint("encarregado_id", "funcionario_id", "aluno_id", "periodo_id",
                            name=op.f("uq_avaliacao_professor_encarregado_id")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_avaliacao_professor")),
    )


def downgrade() -> None:
    op.drop_table("avaliacao_professor")
