"""Secretaria: estados e encaminhamento dos pedidos (e novos tipos), validação dos documentos dos alunos,
documentos exigidos por classe, modelos de declaração e justificação de faltas sem apagar o registo original.

Revision ID: 0012
Revises: 0011
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
TIPOS_ANTES = "'vaga','matricula','transferencia','declaracao','justificacao_falta'"
TIPOS = TIPOS_ANTES + ",'saida_antecipada','informacao','financeiro','outro'"
ESTADOS_ANTES = "'pendente','aprovado','rejeitado','cancelado'"
ESTADOS = "'pendente','em_analise','aguarda_informacao','aprovado','rejeitado','concluido','cancelado'"

# Valores iniciais de cada escola (copiados aqui: a migração não depende do código da aplicação).
DOCUMENTOS = [("cedula_bi_aluno", "Cédula ou BI do aluno", True), ("fotografia", "Fotografia tipo passe", True),
              ("bi_encarregado", "BI do encarregado de educação", True), ("boletim_vacinas", "Boletim de vacinas", True),
              ("declaracao_escola_anterior", "Declaração ou certificado da escola anterior", False)]
MODELOS = [
    ("matricula", "Declaração de matrícula",
     "Para os devidos efeitos, declara-se que <b>{nome}</b>, nascido(a) a {data_nascimento}, com o processo n.º "
     "{processo}, está matriculado(a) neste estabelecimento de ensino na <b>{classe}</b>, turma <b>{turma}</b>, no ano "
     "lectivo <b>{ano}</b>."),
    ("frequencia", "Declaração de frequência",
     "Para os devidos efeitos, declara-se que <b>{nome}</b>, nascido(a) a {data_nascimento}, com o processo n.º "
     "{processo}, frequenta com regularidade a <b>{classe}</b>, turma <b>{turma}</b>, no ano lectivo <b>{ano}</b>, "
     "sendo seu encarregado de educação {encarregado}."),
]


def _pedido(tipos: str, estados: str) -> None:
    with op.batch_alter_table("pedido", recreate="auto") as b:
        b.drop_constraint(op.f("ck_pedido_tipo"), type_="check")
        b.drop_constraint(op.f("ck_pedido_estado"), type_="check")
        b.create_check_constraint(op.f("ck_pedido_tipo"), f"tipo IN ({tipos})")
        b.create_check_constraint(op.f("ck_pedido_estado"), f"estado IN ({estados})")


def upgrade() -> None:
    with op.batch_alter_table("pedido", recreate="auto") as b:
        b.add_column(sa.Column("servico", sa.Text(), server_default="secretaria", nullable=False))
        b.add_column(sa.Column("historico", JSON, nullable=True))
        b.create_check_constraint(op.f("ck_pedido_servico"),
                                  "servico IN ('secretaria','coordenacao','tesouraria','direccao','saude')")
    _pedido(TIPOS, ESTADOS)
    op.execute("UPDATE pedido SET servico = 'coordenacao' WHERE tipo = 'justificacao_falta'")

    with op.batch_alter_table("documento_aluno", recreate="auto") as b:
        b.add_column(sa.Column("estado", sa.Text(), server_default="recebido", nullable=False))
        b.add_column(sa.Column("observacao", sa.Text(), nullable=True))
        b.add_column(sa.Column("validado_por", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("validado_em", sa.DateTime(timezone=True), nullable=True))
        b.create_foreign_key(op.f("fk_documento_aluno_validado_por_utilizador"), "utilizador", ["validado_por"], ["id"])
        b.create_check_constraint(op.f("ck_documento_aluno_estado"), "estado IN ('recebido','aceite','rejeitado')")
    # Os documentos que já existiam foram entregues e guardados pela secretaria: ficam aceites.
    op.execute("UPDATE documento_aluno SET estado = 'aceite'")

    with op.batch_alter_table("assiduidade", recreate="auto") as b:
        b.add_column(sa.Column("estado_original", sa.Text(), nullable=True))
        b.add_column(sa.Column("pedido_id", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("justificada_por", sa.BigInteger(), nullable=True))
        b.add_column(sa.Column("justificada_em", sa.DateTime(timezone=True), nullable=True))
        b.create_foreign_key(op.f("fk_assiduidade_pedido_id_pedido"), "pedido", ["pedido_id"], ["id"])
        b.create_foreign_key(op.f("fk_assiduidade_justificada_por_utilizador"), "utilizador", ["justificada_por"], ["id"])

    op.create_table(
        "documento_exigido",
        sa.Column("id", ID, sa.Identity(always=False), nullable=False),
        sa.Column("estabelecimento_id", sa.BigInteger(), nullable=False),
        sa.Column("tipo", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("classe_id", sa.BigInteger(), nullable=True),
        sa.Column("obrigatorio", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["estabelecimento_id"], ["estabelecimento.id"],
                                name=op.f("fk_documento_exigido_estabelecimento_id_estabelecimento")),
        sa.ForeignKeyConstraint(["classe_id"], ["classe.id"], name=op.f("fk_documento_exigido_classe_id_classe")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documento_exigido")),
        sa.UniqueConstraint("estabelecimento_id", "tipo", "classe_id", name=op.f("uq_documento_exigido_estabelecimento_id")),
    )
    op.create_index(op.f("ix_documento_exigido_estabelecimento_id"), "documento_exigido", ["estabelecimento_id"])
    op.create_table(
        "modelo_declaracao",
        sa.Column("id", ID, sa.Identity(always=False), nullable=False),
        sa.Column("estabelecimento_id", sa.BigInteger(), nullable=False),
        sa.Column("codigo", sa.Text(), nullable=False),
        sa.Column("nome", sa.Text(), nullable=False),
        sa.Column("texto", sa.Text(), nullable=False),
        sa.Column("activo", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.ForeignKeyConstraint(["estabelecimento_id"], ["estabelecimento.id"],
                                name=op.f("fk_modelo_declaracao_estabelecimento_id_estabelecimento")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_modelo_declaracao")),
        sa.UniqueConstraint("estabelecimento_id", "codigo", name=op.f("uq_modelo_declaracao_estabelecimento_id")),
    )
    op.create_index(op.f("ix_modelo_declaracao_estabelecimento_id"), "modelo_declaracao", ["estabelecimento_id"])

    conn = op.get_bind()
    exigido = sa.table("documento_exigido", sa.column("estabelecimento_id"), sa.column("tipo"), sa.column("nome"),
                       sa.column("obrigatorio"))
    modelo = sa.table("modelo_declaracao", sa.column("estabelecimento_id"), sa.column("codigo"), sa.column("nome"),
                      sa.column("texto"))
    for (est,) in conn.execute(sa.text("SELECT id FROM estabelecimento")):
        op.bulk_insert(exigido, [{"estabelecimento_id": est, "tipo": t, "nome": n, "obrigatorio": o} for t, n, o in DOCUMENTOS])
        op.bulk_insert(modelo, [{"estabelecimento_id": est, "codigo": c, "nome": n, "texto": t} for c, n, t in MODELOS])


def downgrade() -> None:
    op.drop_index(op.f("ix_modelo_declaracao_estabelecimento_id"), table_name="modelo_declaracao")
    op.drop_table("modelo_declaracao")
    op.drop_index(op.f("ix_documento_exigido_estabelecimento_id"), table_name="documento_exigido")
    op.drop_table("documento_exigido")
    with op.batch_alter_table("assiduidade", recreate="auto") as b:
        b.drop_constraint(op.f("fk_assiduidade_justificada_por_utilizador"), type_="foreignkey")
        b.drop_constraint(op.f("fk_assiduidade_pedido_id_pedido"), type_="foreignkey")
        for coluna in ("justificada_em", "justificada_por", "pedido_id", "estado_original"):
            b.drop_column(coluna)
    with op.batch_alter_table("documento_aluno", recreate="auto") as b:
        b.drop_constraint(op.f("ck_documento_aluno_estado"), type_="check")
        b.drop_constraint(op.f("fk_documento_aluno_validado_por_utilizador"), type_="foreignkey")
        b.drop_column("validado_em")
        b.drop_column("validado_por")
        b.drop_column("observacao")
        b.drop_column("estado")
    op.execute(f"DELETE FROM pedido WHERE tipo NOT IN ({TIPOS_ANTES})")
    op.execute("UPDATE pedido SET estado = 'pendente' WHERE estado IN ('em_analise','aguarda_informacao')")
    op.execute("UPDATE pedido SET estado = 'aprovado' WHERE estado = 'concluido'")
    _pedido(TIPOS_ANTES, ESTADOS_ANTES)
    with op.batch_alter_table("pedido", recreate="auto") as b:
        b.drop_constraint(op.f("ck_pedido_servico"), type_="check")
        b.drop_column("historico")
        b.drop_column("servico")
