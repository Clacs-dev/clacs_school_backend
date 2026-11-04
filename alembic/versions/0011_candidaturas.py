"""Candidaturas (pré-inscrição pelo Google Forms até à matrícula e boas-vindas), coordenador de cada nível e o alerta
"candidatura".

Revision ID: 0011
Revises: 0010
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
ALERTAS = ("'risco_incumprimento','queda_desempenho','padrao_faltas','divergencia_primavera','comportamento',"
           "'comprovativo_pagamento','pedido_encarregado'")
ESTADOS = ("'submetida','reuniao_marcada','reuniao_realizada','validada','autorizada','vaga_comunicada',"
           "'documentos_entregues','matriculada','concluida','rejeitada','cancelada'")


def _tipos_alerta(lista: str) -> None:
    with op.batch_alter_table("alerta", recreate="auto") as b:
        b.drop_constraint(op.f("ck_alerta_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_alerta_tipo"), f"tipo IN ({lista})")


def _fk(coluna: str, tabela: str, origem: str = "candidatura") -> sa.ForeignKey:
    return sa.ForeignKey(f"{tabela}.id", name=op.f(f"fk_{origem}_{coluna}_{tabela}"))


def upgrade() -> None:
    with op.batch_alter_table("nivel_ensino") as b:
        b.add_column(sa.Column("coordenador_id", sa.BigInteger(), nullable=True))
        b.create_foreign_key(op.f("fk_nivel_ensino_coordenador_id_utilizador"), "utilizador", ["coordenador_id"], ["id"])

    op.create_table(
        "candidatura",
        sa.Column("id", ID, sa.Identity(always=False), nullable=False),
        sa.Column("estabelecimento_id", sa.BigInteger(), _fk("estabelecimento_id", "estabelecimento"), nullable=False),
        sa.Column("origem", sa.Text(), server_default="secretaria", nullable=False),
        sa.Column("id_externo", sa.Text(), nullable=True),
        sa.Column("estado", sa.Text(), server_default="submetida", nullable=False),
        sa.Column("ano_id", sa.BigInteger(), _fk("ano_id", "ano_lectivo"), nullable=True),
        sa.Column("classe_id", sa.BigInteger(), _fk("classe_id", "classe"), nullable=True),
        sa.Column("nivel_id", sa.BigInteger(), _fk("nivel_id", "nivel_ensino"), nullable=True),
        sa.Column("nome_aluno", sa.Text(), nullable=False),
        sa.Column("data_nascimento", sa.Date(), nullable=True),
        sa.Column("sexo", sa.String(1), nullable=True),
        sa.Column("escola_anterior", sa.Text(), nullable=True),
        sa.Column("nome_encarregado", sa.Text(), nullable=False),
        sa.Column("parentesco", sa.Text(), server_default="mae", nullable=False),
        sa.Column("telefone", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("observacoes", sa.Text(), nullable=True),
        sa.Column("respostas", JSON, nullable=True),
        sa.Column("reuniao_em", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reuniao_local", sa.Text(), nullable=True),
        sa.Column("avaliacao", sa.Text(), nullable=True),
        sa.Column("parecer", sa.Text(), nullable=True),
        sa.Column("token", sa.Text(), nullable=True),
        sa.Column("dados_matricula", JSON, nullable=True),
        sa.Column("correccao", sa.Text(), nullable=True),
        sa.Column("turma_id", sa.BigInteger(), _fk("turma_id", "turma"), nullable=True),
        sa.Column("aluno_id", sa.BigInteger(), _fk("aluno_id", "aluno"), nullable=True),
        sa.Column("encarregado_id", sa.BigInteger(), _fk("encarregado_id", "encarregado"), nullable=True),
        sa.Column("matricula_id", sa.BigInteger(), _fk("matricula_id", "matricula"), nullable=True),
        sa.Column("integracao", JSON, nullable=True),
        sa.Column("historico", JSON, nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("actualizado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(f"estado IN ({ESTADOS})", name=op.f("ck_candidatura_estado")),
        sa.CheckConstraint("origem IN ('google_forms','secretaria','importacao')", name=op.f("ck_candidatura_origem")),
        sa.CheckConstraint("sexo IN ('F','M')", name=op.f("ck_candidatura_sexo")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidatura")),
        sa.UniqueConstraint("estabelecimento_id", "id_externo", name=op.f("uq_candidatura_estabelecimento_id")),
        sa.UniqueConstraint("token", name=op.f("uq_candidatura_token")),
    )
    op.create_index(op.f("ix_candidatura_estabelecimento_id"), "candidatura", ["estabelecimento_id"])
    op.create_index("ix_candidatura_estado", "candidatura", ["estabelecimento_id", "estado"])
    op.create_table(
        "candidatura_documento",
        sa.Column("id", ID, sa.Identity(always=False), nullable=False),
        sa.Column("candidatura_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=True),
        sa.Column("ficheiro_url", sa.Text(), nullable=False),
        sa.Column("criado_em", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["candidatura_id"], ["candidatura.id"], ondelete="CASCADE",
                                name=op.f("fk_candidatura_documento_candidatura_id_candidatura")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_candidatura_documento")),
    )
    op.create_index(op.f("ix_candidatura_documento_candidatura_id"), "candidatura_documento", ["candidatura_id"])
    _tipos_alerta(ALERTAS + ",'candidatura'")


def downgrade() -> None:
    op.execute("DELETE FROM alerta WHERE tipo = 'candidatura'")
    _tipos_alerta(ALERTAS)
    op.drop_index(op.f("ix_candidatura_documento_candidatura_id"), table_name="candidatura_documento")
    op.drop_table("candidatura_documento")
    op.drop_index("ix_candidatura_estado", table_name="candidatura")
    op.drop_index(op.f("ix_candidatura_estabelecimento_id"), table_name="candidatura")
    op.drop_table("candidatura")
    with op.batch_alter_table("nivel_ensino") as b:
        b.drop_constraint(op.f("fk_nivel_ensino_coordenador_id_utilizador"), type_="foreignkey")
        b.drop_column("coordenador_id")
