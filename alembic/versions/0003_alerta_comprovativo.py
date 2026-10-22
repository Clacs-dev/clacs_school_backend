"""Alerta do tipo comprovativo_pagamento: a escola é avisada quando um encarregado envia um comprovativo.

Revision ID: 0003
Revises: 0002
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ANTES = "'risco_incumprimento','queda_desempenho','padrao_faltas','divergencia_primavera','comportamento'"
DEPOIS = ANTES + ",'comprovativo_pagamento'"


def _tipos(lista: str) -> None:
    with op.batch_alter_table("alerta", recreate="auto") as b:
        b.drop_constraint(op.f("ck_alerta_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_alerta_tipo"), f"tipo IN ({lista})")


def upgrade() -> None:
    _tipos(DEPOIS)


def downgrade() -> None:
    op.execute("DELETE FROM alerta WHERE tipo = 'comprovativo_pagamento'")
    _tipos(ANTES)
