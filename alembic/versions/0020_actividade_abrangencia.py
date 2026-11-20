"""Plano de actividades: objectivo de cada actividade e a quem se destina (1.º ciclo, 2.º ciclo ou toda a escola).

Revision ID: 0020
Revises: 0019
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.add_column(sa.Column('objectivo', sa.Text(), nullable=True))
        b.add_column(sa.Column('abrangencia', sa.Text(), server_default='escola', nullable=False))
        b.create_check_constraint(op.f("ck_evento_calendario_abrangencia"),
                                  "abrangencia IN ('primeiro_ciclo','segundo_ciclo','escola')")


def downgrade() -> None:
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.drop_constraint(op.f("ck_evento_calendario_abrangencia"), type_="check")
        b.drop_column('abrangencia')
        b.drop_column('objectivo')
