"""Candidaturas feitas pela família no formulário público do site (origem "site").

Revision ID: 0014
Revises: 0013
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _origens(lista: str) -> None:
    with op.batch_alter_table("candidatura", recreate="auto") as b:
        b.drop_constraint(op.f("ck_candidatura_origem"), type_="check")
        b.create_check_constraint(op.f("ck_candidatura_origem"), f"origem IN ({lista})")


def upgrade() -> None:
    _origens("'google_forms','secretaria','importacao','site'")


def downgrade() -> None:
    op.execute("UPDATE candidatura SET origem = 'secretaria' WHERE origem = 'site'")
    _origens("'google_forms','secretaria','importacao'")
