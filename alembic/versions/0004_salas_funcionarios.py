"""Sala com tipo, piso, localização, observações e activo; funcionário com ano de contratação.

Revision ID: 0004
Revises: 0003
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TIPOS = "'sala_aula','laboratorio','informatica','biblioteca','ginasio','auditorio','refeitorio','outro'"


def upgrade() -> None:
    with op.batch_alter_table("sala") as b:
        b.add_column(sa.Column("tipo", sa.Text(), server_default="sala_aula", nullable=False))
        b.add_column(sa.Column("piso", sa.Text(), nullable=True))
        b.add_column(sa.Column("localizacao", sa.Text(), nullable=True))
        b.add_column(sa.Column("observacoes", sa.Text(), nullable=True))
        b.add_column(sa.Column("activo", sa.Boolean(), server_default=sa.true(), nullable=False))
        b.create_check_constraint(op.f("ck_sala_tipo"), f"tipo IN ({TIPOS})")
    with op.batch_alter_table("funcionario") as b:
        b.add_column(sa.Column("ano_contratacao", sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("funcionario") as b:
        b.drop_column("ano_contratacao")
    with op.batch_alter_table("sala") as b:
        b.drop_constraint(op.f("ck_sala_tipo"), type_="check")
        for coluna in ("activo", "observacoes", "localizacao", "piso", "tipo"):
            b.drop_column(coluna)
