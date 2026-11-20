"""Plano de actividades: a actividade destina-se a um nível de ensino registado (ou a toda a escola), em vez das
opções fixas «1.º ciclo / 2.º ciclo / toda a escola».

Revision ID: 0022
Revises: 0021
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _nivel(niveis: list, segundo: bool):
    """O nível registado que corresponde ao «1.º ciclo» ou ao «2.º ciclo» (pelo nome); None se não houver."""
    for nid, nome in niveis:
        n = (nome or "").lower().replace("º", "").replace(".", "")
        if segundo and ("ii ciclo" in n or "2 ciclo" in n or "segundo ciclo" in n):
            return nid
        if not segundo and ("ii ciclo" not in n) and ("i ciclo" in n or "1 ciclo" in n or "primeiro ciclo" in n):
            return nid
    return None


def upgrade() -> None:
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.add_column(sa.Column('nivel_id', sa.BigInteger(), nullable=True))
        b.create_foreign_key(op.f('fk_evento_calendario_nivel_id_nivel_ensino'), 'nivel_ensino', ['nivel_id'], ['id'])
    con = op.get_bind()
    for est, abr in con.execute(sa.text("SELECT DISTINCT estabelecimento_id, abrangencia FROM evento_calendario "
                                        "WHERE abrangencia IN ('primeiro_ciclo','segundo_ciclo')")).all():
        niveis = con.execute(sa.text("SELECT id, nome FROM nivel_ensino WHERE estabelecimento_id = :e ORDER BY ordem, id"),
                             {"e": est}).all()
        nid = _nivel(niveis, abr == "segundo_ciclo")
        if nid:
            con.execute(sa.text("UPDATE evento_calendario SET nivel_id = :n WHERE estabelecimento_id = :e AND abrangencia = :a"),
                        {"n": nid, "e": est, "a": abr})
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.drop_constraint(op.f("ck_evento_calendario_abrangencia"), type_="check")
        b.drop_column('abrangencia')


def downgrade() -> None:
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.add_column(sa.Column('abrangencia', sa.Text(), server_default='escola', nullable=False))
        b.create_check_constraint(op.f("ck_evento_calendario_abrangencia"),
                                  "abrangencia IN ('primeiro_ciclo','segundo_ciclo','escola')")
    with op.batch_alter_table('evento_calendario', recreate="auto") as b:
        b.drop_constraint(op.f('fk_evento_calendario_nivel_id_nivel_ensino'), type_='foreignkey')
        b.drop_column('nivel_id')
