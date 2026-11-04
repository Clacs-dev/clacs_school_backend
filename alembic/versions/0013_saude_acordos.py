"""Saúde escolar (ocorrências e medicação), acordos de pagamento, multas por escalões com memória de cálculo e
isenção, orçamento e despesas, calendário, horários, substituições e aulas (assiduidade por tempo lectivo).

Revision ID: 0013
Revises: 0012
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
ALERTAS = ("'risco_incumprimento','queda_desempenho','padrao_faltas','divergencia_primavera','comportamento',"
           "'comprovativo_pagamento','pedido_encarregado','candidatura'")
TIPOS = ("'vaga','matricula','transferencia','declaracao','justificacao_falta','saida_antecipada','informacao',"
         "'financeiro','outro'")
# Criadas por esta migração, pela ordem inversa (para o downgrade).
TABELAS = ['acordo_cobranca', 'substituicao', 'aula', 'horario', 'ocorrencia_saude_nota', 'medicacao_administracao', 'acordo_prestacao', 'orcamento_linha', 'ocorrencia_saude', 'medicacao_instrucao', 'despesa', 'acordo_pagamento', 'orcamento', 'evento_calendario', 'acordo_modelo']


ACCOES = "'insert','update','delete','login','export'"


def _checks(alertas: str, tipos: str, accoes: str) -> None:
    with op.batch_alter_table("auditoria_log", recreate="auto") as b:
        b.drop_constraint(op.f("ck_auditoria_log_accao"), type_="check")
        b.create_check_constraint(op.f("ck_auditoria_log_accao"), f"accao IN ({accoes})")
    with op.batch_alter_table("alerta", recreate="auto") as b:
        b.drop_constraint(op.f("ck_alerta_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_alerta_tipo"), f"tipo IN ({alertas})")
    with op.batch_alter_table("pedido", recreate="auto") as b:
        b.drop_constraint(op.f("ck_pedido_tipo"), type_="check")
        b.create_check_constraint(op.f("ck_pedido_tipo"), f"tipo IN ({tipos})")


def upgrade() -> None:
    op.create_table('acordo_modelo',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('nome', sa.Text(), nullable=False),
    sa.Column('num_prestacoes', sa.SmallInteger(), nullable=False),
    sa.Column('intervalo_meses', sa.SmallInteger(), server_default='1', nullable=False),
    sa.Column('entrada_percentual', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('suspende_multas', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('activo', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_acordo_modelo_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_acordo_modelo'))
    )
    with op.batch_alter_table('acordo_modelo', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_acordo_modelo_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('evento_calendario',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('tipo', sa.Text(), nullable=False),
    sa.Column('titulo', sa.Text(), nullable=False),
    sa.Column('data_inicio', sa.Date(), nullable=False),
    sa.Column('data_fim', sa.Date(), nullable=True),
    sa.Column('descricao', sa.Text(), nullable=True),
    sa.Column('publico', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.CheckConstraint("tipo IN ('feriado','interrupcao','evento','reuniao','prova')", name=op.f('ck_evento_calendario_tipo')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_evento_calendario_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evento_calendario'))
    )
    with op.batch_alter_table('evento_calendario', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_evento_calendario_data_inicio'), ['data_inicio'], unique=False)
        batch_op.create_index(batch_op.f('ix_evento_calendario_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('orcamento',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('ano_id', sa.BigInteger(), nullable=False),
    sa.Column('designacao', sa.Text(), nullable=False),
    sa.Column('estado', sa.Text(), server_default='rascunho', nullable=False),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("estado IN ('rascunho','aprovado','fechado')", name=op.f('ck_orcamento_estado')),
    sa.ForeignKeyConstraint(['ano_id'], ['ano_lectivo.id'], name=op.f('fk_orcamento_ano_id_ano_lectivo')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_orcamento_estabelecimento_id_estabelecimento')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orcamento')),
    sa.UniqueConstraint('estabelecimento_id', 'ano_id', 'designacao', name=op.f('uq_orcamento_estabelecimento_id'))
    )
    with op.batch_alter_table('orcamento', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orcamento_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('acordo_pagamento',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('encarregado_id', sa.BigInteger(), nullable=False),
    sa.Column('aluno_id', sa.BigInteger(), nullable=True),
    sa.Column('modelo_id', sa.BigInteger(), nullable=True),
    sa.Column('motivo', sa.Text(), nullable=False),
    sa.Column('valor_total', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('pago_inicial', sa.Numeric(precision=14, scale=2), server_default='0', nullable=False),
    sa.Column('suspende_multas', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('estado', sa.Text(), server_default='proposto', nullable=False),
    sa.Column('criado_por', sa.BigInteger(), nullable=True),
    sa.Column('aprovado_por', sa.BigInteger(), nullable=True),
    sa.Column('aprovado_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('historico', JSON, nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("estado IN ('proposto','activo','cumprido','cancelado')", name=op.f('ck_acordo_pagamento_estado')),
    sa.ForeignKeyConstraint(['aluno_id'], ['aluno.id'], name=op.f('fk_acordo_pagamento_aluno_id_aluno')),
    sa.ForeignKeyConstraint(['aprovado_por'], ['utilizador.id'], name=op.f('fk_acordo_pagamento_aprovado_por_utilizador')),
    sa.ForeignKeyConstraint(['criado_por'], ['utilizador.id'], name=op.f('fk_acordo_pagamento_criado_por_utilizador')),
    sa.ForeignKeyConstraint(['encarregado_id'], ['encarregado.id'], name=op.f('fk_acordo_pagamento_encarregado_id_encarregado')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_acordo_pagamento_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['modelo_id'], ['acordo_modelo.id'], name=op.f('fk_acordo_pagamento_modelo_id_acordo_modelo')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_acordo_pagamento'))
    )
    with op.batch_alter_table('acordo_pagamento', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_acordo_pagamento_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('despesa',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('categoria', sa.Text(), nullable=False),
    sa.Column('descricao', sa.Text(), nullable=False),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('fornecedor', sa.Text(), nullable=True),
    sa.Column('recorrente', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('registada_por', sa.BigInteger(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_despesa_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['registada_por'], ['utilizador.id'], name=op.f('fk_despesa_registada_por_utilizador')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_despesa'))
    )
    with op.batch_alter_table('despesa', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_despesa_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('medicacao_instrucao',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('aluno_id', sa.BigInteger(), nullable=False),
    sa.Column('medicamento', sa.Text(), nullable=False),
    sa.Column('dose', sa.Text(), nullable=False),
    sa.Column('horario', sa.Text(), nullable=False),
    sa.Column('data_inicio', sa.Date(), nullable=False),
    sa.Column('data_fim', sa.Date(), nullable=True),
    sa.Column('instrucoes', sa.Text(), nullable=True),
    sa.Column('receita_url', sa.Text(), nullable=True),
    sa.Column('encarregado_id', sa.BigInteger(), nullable=True),
    sa.Column('confirmada_pela_familia', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('estado', sa.Text(), server_default='pendente', nullable=False),
    sa.Column('motivo', sa.Text(), nullable=True),
    sa.Column('validada_por', sa.BigInteger(), nullable=True),
    sa.Column('validada_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("estado IN ('pendente','autorizada','recusada','terminada')", name=op.f('ck_medicacao_instrucao_estado')),
    sa.ForeignKeyConstraint(['aluno_id'], ['aluno.id'], name=op.f('fk_medicacao_instrucao_aluno_id_aluno')),
    sa.ForeignKeyConstraint(['encarregado_id'], ['encarregado.id'], name=op.f('fk_medicacao_instrucao_encarregado_id_encarregado')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_medicacao_instrucao_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['validada_por'], ['utilizador.id'], name=op.f('fk_medicacao_instrucao_validada_por_utilizador')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_medicacao_instrucao'))
    )
    with op.batch_alter_table('medicacao_instrucao', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_medicacao_instrucao_aluno_id'), ['aluno_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_medicacao_instrucao_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('ocorrencia_saude',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('aluno_id', sa.BigInteger(), nullable=False),
    sa.Column('data_hora', sa.DateTime(timezone=True), nullable=False),
    sa.Column('local', sa.Text(), nullable=True),
    sa.Column('descricao', sa.Text(), nullable=False),
    sa.Column('sinais', sa.Text(), nullable=True),
    sa.Column('medidas', sa.Text(), nullable=True),
    sa.Column('encaminhamento', sa.Text(), nullable=True),
    sa.Column('desfecho', sa.Text(), nullable=True),
    sa.Column('partilhada', sa.Boolean(), server_default=sa.false(), nullable=False),
    sa.Column('estado', sa.Text(), server_default='aberta', nullable=False),
    sa.Column('registada_por', sa.BigInteger(), nullable=True),
    sa.Column('encerrada_por', sa.BigInteger(), nullable=True),
    sa.Column('encerrada_em', sa.DateTime(timezone=True), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("desfecho IN ('ficou_na_escola','recolhido','encaminhado')", name=op.f('ck_ocorrencia_saude_desfecho')),
    sa.CheckConstraint("estado IN ('aberta','encerrada')", name=op.f('ck_ocorrencia_saude_estado')),
    sa.ForeignKeyConstraint(['aluno_id'], ['aluno.id'], name=op.f('fk_ocorrencia_saude_aluno_id_aluno')),
    sa.ForeignKeyConstraint(['encerrada_por'], ['utilizador.id'], name=op.f('fk_ocorrencia_saude_encerrada_por_utilizador')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_ocorrencia_saude_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['registada_por'], ['utilizador.id'], name=op.f('fk_ocorrencia_saude_registada_por_utilizador')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ocorrencia_saude'))
    )
    with op.batch_alter_table('ocorrencia_saude', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ocorrencia_saude_aluno_id'), ['aluno_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_ocorrencia_saude_estabelecimento_id'), ['estabelecimento_id'], unique=False)

    op.create_table('orcamento_linha',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('orcamento_id', sa.BigInteger(), nullable=False),
    sa.Column('tipo', sa.Text(), nullable=False),
    sa.Column('categoria', sa.Text(), nullable=False),
    sa.Column('mes', sa.SmallInteger(), nullable=True),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.CheckConstraint("tipo IN ('receita','despesa')", name=op.f('ck_orcamento_linha_tipo')),
    sa.CheckConstraint('mes BETWEEN 1 AND 12', name=op.f('ck_orcamento_linha_mes')),
    sa.ForeignKeyConstraint(['orcamento_id'], ['orcamento.id'], name=op.f('fk_orcamento_linha_orcamento_id_orcamento'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_orcamento_linha'))
    )
    with op.batch_alter_table('orcamento_linha', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_orcamento_linha_orcamento_id'), ['orcamento_id'], unique=False)

    op.create_table('acordo_prestacao',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('acordo_id', sa.BigInteger(), nullable=False),
    sa.Column('numero', sa.SmallInteger(), nullable=False),
    sa.Column('valor', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('vencimento', sa.Date(), nullable=False),
    sa.ForeignKeyConstraint(['acordo_id'], ['acordo_pagamento.id'], name=op.f('fk_acordo_prestacao_acordo_id_acordo_pagamento'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_acordo_prestacao'))
    )
    with op.batch_alter_table('acordo_prestacao', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_acordo_prestacao_acordo_id'), ['acordo_id'], unique=False)

    op.create_table('medicacao_administracao',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('instrucao_id', sa.BigInteger(), nullable=False),
    sa.Column('administrada_em', sa.DateTime(timezone=True), nullable=False),
    sa.Column('dose', sa.Text(), nullable=False),
    sa.Column('administrada', sa.Boolean(), server_default=sa.true(), nullable=False),
    sa.Column('observacao', sa.Text(), nullable=True),
    sa.Column('por', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['instrucao_id'], ['medicacao_instrucao.id'], name=op.f('fk_medicacao_administracao_instrucao_id_medicacao_instrucao'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['por'], ['utilizador.id'], name=op.f('fk_medicacao_administracao_por_utilizador')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_medicacao_administracao'))
    )
    with op.batch_alter_table('medicacao_administracao', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_medicacao_administracao_instrucao_id'), ['instrucao_id'], unique=False)

    op.create_table('ocorrencia_saude_nota',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('ocorrencia_id', sa.BigInteger(), nullable=False),
    sa.Column('tipo', sa.Text(), nullable=False),
    sa.Column('texto', sa.Text(), nullable=False),
    sa.Column('canal', sa.Text(), nullable=True),
    sa.Column('contactado', sa.Text(), nullable=True),
    sa.Column('orientacao', sa.Text(), nullable=True),
    sa.Column('por', sa.BigInteger(), nullable=True),
    sa.Column('em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("tipo IN ('nota','contacto_familia','alteracao')", name=op.f('ck_ocorrencia_saude_nota_tipo')),
    sa.ForeignKeyConstraint(['ocorrencia_id'], ['ocorrencia_saude.id'], name=op.f('fk_ocorrencia_saude_nota_ocorrencia_id_ocorrencia_saude'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['por'], ['utilizador.id'], name=op.f('fk_ocorrencia_saude_nota_por_utilizador')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ocorrencia_saude_nota'))
    )
    with op.batch_alter_table('ocorrencia_saude_nota', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_ocorrencia_saude_nota_ocorrencia_id'), ['ocorrencia_id'], unique=False)

    op.create_table('horario',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('estabelecimento_id', sa.BigInteger(), nullable=False),
    sa.Column('turma_id', sa.BigInteger(), nullable=False),
    sa.Column('disciplina_id', sa.BigInteger(), nullable=False),
    sa.Column('funcionario_id', sa.BigInteger(), nullable=True),
    sa.Column('sala_id', sa.BigInteger(), nullable=True),
    sa.Column('dia_semana', sa.SmallInteger(), nullable=False),
    sa.Column('tempo', sa.SmallInteger(), nullable=False),
    sa.Column('hora_inicio', sa.Time(), nullable=False),
    sa.Column('hora_fim', sa.Time(), nullable=False),
    sa.Column('valido_desde', sa.Date(), nullable=False),
    sa.Column('valido_ate', sa.Date(), nullable=True),
    sa.Column('criado_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('dia_semana BETWEEN 1 AND 6', name=op.f('ck_horario_dia_semana')),
    sa.CheckConstraint('hora_fim > hora_inicio', name=op.f('ck_horario_horas')),
    sa.ForeignKeyConstraint(['disciplina_id'], ['disciplina.id'], name=op.f('fk_horario_disciplina_id_disciplina')),
    sa.ForeignKeyConstraint(['estabelecimento_id'], ['estabelecimento.id'], name=op.f('fk_horario_estabelecimento_id_estabelecimento')),
    sa.ForeignKeyConstraint(['funcionario_id'], ['funcionario.id'], name=op.f('fk_horario_funcionario_id_funcionario')),
    sa.ForeignKeyConstraint(['sala_id'], ['sala.id'], name=op.f('fk_horario_sala_id_sala')),
    sa.ForeignKeyConstraint(['turma_id'], ['turma.id'], name=op.f('fk_horario_turma_id_turma')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_horario'))
    )
    with op.batch_alter_table('horario', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_horario_estabelecimento_id'), ['estabelecimento_id'], unique=False)
        batch_op.create_index('ix_horario_turma', ['turma_id', 'dia_semana'], unique=False)

    op.create_table('aula',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('horario_id', sa.BigInteger(), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('funcionario_id', sa.BigInteger(), nullable=True),
    sa.Column('estado', sa.Text(), server_default='dada', nullable=False),
    sa.Column('atraso_minutos', sa.SmallInteger(), server_default='0', nullable=False),
    sa.Column('sumario', sa.Text(), nullable=True),
    sa.Column('registada_por', sa.BigInteger(), nullable=True),
    sa.Column('registada_em', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint("estado IN ('dada','falta_professor')", name=op.f('ck_aula_estado')),
    sa.ForeignKeyConstraint(['funcionario_id'], ['funcionario.id'], name=op.f('fk_aula_funcionario_id_funcionario')),
    sa.ForeignKeyConstraint(['horario_id'], ['horario.id'], name=op.f('fk_aula_horario_id_horario'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['registada_por'], ['utilizador.id'], name=op.f('fk_aula_registada_por_utilizador')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_aula')),
    sa.UniqueConstraint('horario_id', 'data', name=op.f('uq_aula_horario_id'))
    )
    with op.batch_alter_table('aula', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_aula_data'), ['data'], unique=False)

    op.create_table('substituicao',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), sa.Identity(always=False), nullable=False),
    sa.Column('horario_id', sa.BigInteger(), nullable=False),
    sa.Column('data', sa.Date(), nullable=False),
    sa.Column('substituto_id', sa.BigInteger(), nullable=False),
    sa.Column('motivo', sa.Text(), nullable=True),
    sa.Column('registada_por', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['horario_id'], ['horario.id'], name=op.f('fk_substituicao_horario_id_horario'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['registada_por'], ['utilizador.id'], name=op.f('fk_substituicao_registada_por_utilizador')),
    sa.ForeignKeyConstraint(['substituto_id'], ['funcionario.id'], name=op.f('fk_substituicao_substituto_id_funcionario')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_substituicao')),
    sa.UniqueConstraint('horario_id', 'data', name=op.f('uq_substituicao_horario_id'))
    )
    op.create_table('acordo_cobranca',
    sa.Column('acordo_id', sa.BigInteger(), nullable=False),
    sa.Column('cobranca_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['acordo_id'], ['acordo_pagamento.id'], name=op.f('fk_acordo_cobranca_acordo_id_acordo_pagamento'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['cobranca_id'], ['cobranca.id'], name=op.f('fk_acordo_cobranca_cobranca_id_cobranca')),
    sa.PrimaryKeyConstraint('acordo_id', 'cobranca_id', name=op.f('pk_acordo_cobranca'))
    )

    with op.batch_alter_table("assiduidade", recreate="auto") as b:
        b.add_column(sa.Column("horario_id", sa.BigInteger(), nullable=True))
        b.create_foreign_key(op.f("fk_assiduidade_horario_id_horario"), "horario", ["horario_id"], ["id"])
        b.drop_constraint("uq_assiduidade_aluno_id", type_="unique")
        b.create_unique_constraint("uq_assiduidade_aluno_id", ["aluno_id", "turma_id", "disciplina_id", "data", "horario_id"])
    # Colunas novas sem recriar a tabela (cobranca tem uma coluna calculada, valor_total).
    op.add_column("cobranca", sa.Column("multa_calculo", JSON, nullable=True))
    op.add_column("cobranca", sa.Column("multa_isenta", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("cobranca", sa.Column("multa_isencao_motivo", sa.Text(), nullable=True))
    op.add_column("cobranca", sa.Column("multa_isencao_por", sa.BigInteger(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":  # no SQLite, a chave estrangeira obrigaria a recriar a tabela
        op.create_foreign_key(op.f("fk_cobranca_multa_isencao_por_utilizador"), "cobranca", "utilizador",
                              ["multa_isencao_por"], ["id"])
    op.add_column("multa_regra", sa.Column("escaloes", JSON, nullable=True))
    _checks(ALERTAS + ",'saude'", TIPOS.replace("'outro'", "'saude','outro'"), ACCOES + ",'view','notify'")


def downgrade() -> None:
    op.execute("DELETE FROM alerta WHERE tipo = 'saude'")
    op.execute("DELETE FROM pedido WHERE tipo = 'saude'")
    op.execute("DELETE FROM auditoria_log WHERE accao IN ('view','notify')")
    _checks(ALERTAS, TIPOS, ACCOES)
    with op.batch_alter_table("multa_regra", recreate="auto") as b:
        b.drop_column("escaloes")
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint(op.f("fk_cobranca_multa_isencao_por_utilizador"), "cobranca", type_="foreignkey")
    for coluna in ("multa_isencao_por", "multa_isencao_motivo", "multa_isenta", "multa_calculo"):
        op.drop_column("cobranca", coluna)
    op.execute("DELETE FROM assiduidade WHERE horario_id IS NOT NULL")
    with op.batch_alter_table("assiduidade", recreate="auto") as b:
        b.drop_constraint("uq_assiduidade_aluno_id", type_="unique")
        b.create_unique_constraint("uq_assiduidade_aluno_id", ["aluno_id", "turma_id", "disciplina_id", "data"])
        b.drop_constraint(op.f("fk_assiduidade_horario_id_horario"), type_="foreignkey")
        b.drop_column("horario_id")
    for tabela in TABELAS:
        op.drop_table(tabela)
