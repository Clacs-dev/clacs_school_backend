"""Segredo TOTP para a autenticação em dois passos.

Revision ID: 0002
Revises: 0001
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("utilizador") as b:
        b.add_column(sa.Column("doisfa_segredo", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("utilizador") as b:
        b.drop_column("doisfa_segredo")
