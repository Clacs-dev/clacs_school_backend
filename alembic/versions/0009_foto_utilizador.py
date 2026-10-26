"""Foto do perfil do utilizador ("O meu perfil").

Revision ID: 0009
Revises: 0008
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("utilizador") as b:
        b.add_column(sa.Column("foto_url", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("utilizador") as b:
        b.drop_column("foto_url")
