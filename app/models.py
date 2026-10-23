"""Modelos SQLAlchemy do esquema CLACS Escola.

Segue o documento "clacs escola schema" do cliente, secção a secção. Os tipos
específicos do PostgreSQL (JSONB, TEXT[], INET) têm variante genérica para que
o mesmo código corra em SQLite (desenvolvimento/testes) e PostgreSQL (Render).
Vistas, triggers e o índice GIN de texto são criados só em PostgreSQL, na
migração Alembic.
"""
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    JSON, BigInteger, Boolean, CheckConstraint, Computed, Date, DateTime, ForeignKey,
    Identity, Index, Integer, MetaData, Numeric, SmallInteger, String, Text, Time,
    UniqueConstraint, func, text, true, false,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

BigPK = BigInteger().with_variant(Integer, "sqlite")
JSONType = JSON().with_variant(JSONB(), "postgresql")
TextArray = JSON().with_variant(ARRAY(Text), "postgresql")
InetType = String(45).with_variant(INET(), "postgresql")
Money = Numeric(14, 2)

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def pk():
    return mapped_column(BigPK, Identity(), primary_key=True)


def fk(target: str, nullable: bool = False, ondelete: str | None = None, **kw):
    return mapped_column(BigInteger, ForeignKey(target, ondelete=ondelete), nullable=nullable, **kw)


def estab_fk(nullable: bool = False):
    return fk("estabelecimento.id", nullable=nullable, index=True)


def criado_em():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def actualizado_em():
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


def ck_in(col: str, *values: str, name: str | None = None) -> CheckConstraint:
    lista = ",".join(f"'{v}'" for v in values)
    return CheckConstraint(f"{col} IN ({lista})", name=name or col)


# =====================================================================
# 0. NÚCLEO: ESTABELECIMENTOS E PARÂMETROS
# =====================================================================
class Estabelecimento(Base):
    __tablename__ = "estabelecimento"
    id: Mapped[int] = pk()
    nome: Mapped[str] = mapped_column(Text)
    nif: Mapped[str | None] = mapped_column(Text)
    morada: Mapped[str | None] = mapped_column(Text)
    telefone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    logotipo_url: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())
    criado_em: Mapped[datetime] = criado_em()
    actualizado_em: Mapped[datetime] = actualizado_em()


class Parametro(Base):
    __tablename__ = "parametro"
    estabelecimento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("estabelecimento.id"), primary_key=True)
    chave: Mapped[str] = mapped_column(Text, primary_key=True)
    valor: Mapped[dict | list | int | str] = mapped_column(JSONType)
    descricao: Mapped[str | None] = mapped_column(Text)


# =====================================================================
# 1. ACADÉMICO
# =====================================================================
class AnoLectivo(Base):
    __tablename__ = "ano_lectivo"
    __table_args__ = (
        UniqueConstraint("estabelecimento_id", "designacao"),
        ck_in("estado", "planeado", "activo", "encerrado"),
        CheckConstraint("data_fim > data_inicio", name="datas"),
        Index("uq_ano_activo", "estabelecimento_id", unique=True,
              postgresql_where=text("estado = 'activo'"), sqlite_where=text("estado = 'activo'")),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    data_inicio: Mapped[date] = mapped_column(Date)
    data_fim: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(Text, server_default="planeado")


class PeriodoLectivo(Base):
    __tablename__ = "periodo_lectivo"
    __table_args__ = (UniqueConstraint("ano_id", "ordem"),)
    id: Mapped[int] = pk()
    ano_id: Mapped[int] = fk("ano_lectivo.id", ondelete="CASCADE")
    designacao: Mapped[str] = mapped_column(Text)
    ordem: Mapped[int] = mapped_column(SmallInteger)
    data_inicio: Mapped[date] = mapped_column(Date)
    data_fim: Mapped[date] = mapped_column(Date)


class NivelEnsino(Base):
    __tablename__ = "nivel_ensino"
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome: Mapped[str] = mapped_column(Text)
    ordem: Mapped[int] = mapped_column(SmallInteger, server_default="0")


class Classe(Base):
    __tablename__ = "classe"
    id: Mapped[int] = pk()
    nivel_id: Mapped[int] = fk("nivel_ensino.id")
    designacao: Mapped[str] = mapped_column(Text)
    ordem: Mapped[int] = mapped_column(SmallInteger)


TIPOS_SALA = ("sala_aula", "laboratorio", "informatica", "biblioteca", "ginasio", "auditorio", "refeitorio", "outro")


class Sala(Base):
    __tablename__ = "sala"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "designacao"), ck_in("tipo", *TIPOS_SALA))
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    capacidade: Mapped[int | None] = mapped_column(SmallInteger)
    tipo: Mapped[str] = mapped_column(Text, server_default="sala_aula")
    piso: Mapped[str | None] = mapped_column(Text)          # "R/C", "1.º andar"
    localizacao: Mapped[str | None] = mapped_column(Text)   # posição: bloco, ala, junto a…
    observacoes: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class Disciplina(Base):
    __tablename__ = "disciplina"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "nome"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome: Mapped[str] = mapped_column(Text)
    sigla: Mapped[str | None] = mapped_column(Text)


class ClasseDisciplina(Base):
    __tablename__ = "classe_disciplina"
    classe_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("classe.id"), primary_key=True)
    disciplina_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("disciplina.id"), primary_key=True)
    carga_horaria: Mapped[int | None] = mapped_column(SmallInteger)


class Funcionario(Base):
    __tablename__ = "funcionario"
    __table_args__ = (ck_in("cargo", "professor", "coordenador", "director", "secretaria", "financeiro", "outro"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome_completo: Mapped[str] = mapped_column(Text)
    cargo: Mapped[str] = mapped_column(Text)
    nif: Mapped[str | None] = mapped_column(Text)
    telefone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    ano_contratacao: Mapped[int | None] = mapped_column(SmallInteger)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())
    criado_em: Mapped[datetime] = criado_em()


class Turma(Base):
    __tablename__ = "turma"
    __table_args__ = (
        UniqueConstraint("estabelecimento_id", "ano_id", "designacao"),
        ck_in("periodo_dia", "manha", "tarde", "noite"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    ano_id: Mapped[int] = fk("ano_lectivo.id")
    classe_id: Mapped[int] = fk("classe.id")
    designacao: Mapped[str] = mapped_column(Text)
    sala_id: Mapped[int | None] = fk("sala.id", nullable=True)
    periodo_dia: Mapped[str] = mapped_column(Text, server_default="manha")
    director_turma_id: Mapped[int | None] = fk("funcionario.id", nullable=True)
    capacidade: Mapped[int] = mapped_column(SmallInteger, server_default="30")


class AtribuicaoDocente(Base):
    __tablename__ = "atribuicao_docente"
    __table_args__ = (UniqueConstraint("turma_id", "disciplina_id"),)
    id: Mapped[int] = pk()
    turma_id: Mapped[int] = fk("turma.id", ondelete="CASCADE")
    disciplina_id: Mapped[int] = fk("disciplina.id")
    funcionario_id: Mapped[int] = fk("funcionario.id")


# =====================================================================
# 2. CADASTRO
# =====================================================================
class Aluno(Base):
    __tablename__ = "aluno"
    __table_args__ = (
        UniqueConstraint("estabelecimento_id", "numero_processo"),
        CheckConstraint("sexo IN ('F','M')", name="sexo"),
        ck_in("estado", "activo", "transferido", "desistente", "concluido"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    numero_processo: Mapped[str] = mapped_column(Text)
    nome_completo: Mapped[str] = mapped_column(Text)
    data_nascimento: Mapped[date | None] = mapped_column(Date)
    sexo: Mapped[str | None] = mapped_column(String(1))
    nacionalidade: Mapped[str | None] = mapped_column(Text, server_default="Angolana")
    bi_numero: Mapped[str | None] = mapped_column(Text)
    morada: Mapped[str | None] = mapped_column(Text)
    alergias: Mapped[str | None] = mapped_column(Text)
    necessidades_especiais: Mapped[str | None] = mapped_column(Text)
    # saúde escolar
    grupo_sanguineo: Mapped[str | None] = mapped_column(Text)
    condicoes_saude: Mapped[str | None] = mapped_column(Text)      # doenças crónicas, ex.: asma
    medicacao: Mapped[str | None] = mapped_column(Text)
    contacto_emergencia: Mapped[str | None] = mapped_column(Text)
    telefone_emergencia: Mapped[str | None] = mapped_column(Text)
    escola_anterior: Mapped[str | None] = mapped_column(Text)
    foto_url: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text, server_default="activo")
    criado_em: Mapped[datetime] = criado_em()
    actualizado_em: Mapped[datetime] = actualizado_em()


class Encarregado(Base):
    __tablename__ = "encarregado"
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome_completo: Mapped[str] = mapped_column(Text)
    nif: Mapped[str | None] = mapped_column(Text)
    bi_numero: Mapped[str | None] = mapped_column(Text)
    telefone: Mapped[str | None] = mapped_column(Text)
    whatsapp: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    profissao: Mapped[str | None] = mapped_column(Text)
    morada: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())
    criado_em: Mapped[datetime] = criado_em()
    actualizado_em: Mapped[datetime] = actualizado_em()


class AlunoEncarregado(Base):
    __tablename__ = "aluno_encarregado"
    __table_args__ = (
        ck_in("parentesco", "mae", "pai", "tutor", "outro"),
        Index("uq_ee_financeiro", "aluno_id", unique=True,
              postgresql_where=text("responsavel_financeiro"), sqlite_where=text("responsavel_financeiro")),
    )
    aluno_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("aluno.id", ondelete="CASCADE"), primary_key=True)
    encarregado_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("encarregado.id", ondelete="CASCADE"), primary_key=True)
    parentesco: Mapped[str] = mapped_column(Text)
    principal: Mapped[bool] = mapped_column(Boolean, server_default=false())
    responsavel_financeiro: Mapped[bool] = mapped_column(Boolean, server_default=false())
    autorizado_recolha: Mapped[bool] = mapped_column(Boolean, server_default=true())


class DocumentoAluno(Base):
    __tablename__ = "documento_aluno"
    id: Mapped[int] = pk()
    aluno_id: Mapped[int] = fk("aluno.id", ondelete="CASCADE")
    tipo: Mapped[str] = mapped_column(Text)
    ficheiro_url: Mapped[str] = mapped_column(Text)
    validade: Mapped[date | None] = mapped_column(Date)
    criado_em: Mapped[datetime] = criado_em()


class Matricula(Base):
    __tablename__ = "matricula"
    __table_args__ = (
        UniqueConstraint("aluno_id", "ano_id"),
        ck_in("tipo", "nova", "renovacao", "transferencia"),
        ck_in("estado", "activa", "anulada", "concluida"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    aluno_id: Mapped[int] = fk("aluno.id")
    ano_id: Mapped[int] = fk("ano_lectivo.id")
    turma_id: Mapped[int] = fk("turma.id", index=True)
    tipo: Mapped[str] = mapped_column(Text)
    data_matricula: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    estado: Mapped[str] = mapped_column(Text, server_default="activa")
    observacoes: Mapped[str | None] = mapped_column(Text)
    registado_por: Mapped[int | None] = fk("utilizador.id", nullable=True)


class MatriculaHistorico(Base):
    __tablename__ = "matricula_historico"
    id: Mapped[int] = pk()
    matricula_id: Mapped[int] = fk("matricula.id", ondelete="CASCADE")
    turma_anterior_id: Mapped[int | None] = fk("turma.id", nullable=True)
    turma_nova_id: Mapped[int] = fk("turma.id")
    motivo: Mapped[str | None] = mapped_column(Text)
    data_alteracao: Mapped[datetime] = criado_em()


# =====================================================================
# 3. SEGURANÇA
# =====================================================================
class Utilizador(Base):
    __tablename__ = "utilizador"
    __table_args__ = (
        CheckConstraint("NOT (funcionario_id IS NOT NULL AND encarregado_id IS NOT NULL)", name="tipo_conta"),
    )
    id: Mapped[int] = pk()
    nome: Mapped[str] = mapped_column(Text)
    email: Mapped[str] = mapped_column(Text, unique=True)
    telefone: Mapped[str | None] = mapped_column(Text)
    senha_hash: Mapped[str] = mapped_column(Text)
    funcionario_id: Mapped[int | None] = fk("funcionario.id", nullable=True)
    encarregado_id: Mapped[int | None] = fk("encarregado.id", nullable=True)
    doisfa_activo: Mapped[bool] = mapped_column(Boolean, server_default=false())
    doisfa_segredo: Mapped[str | None] = mapped_column(Text)  # TOTP (migração 0002)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())
    ultimo_login: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = criado_em()


class Perfil(Base):
    __tablename__ = "perfil"
    id: Mapped[int] = pk()
    codigo: Mapped[str] = mapped_column(Text, unique=True)
    nome: Mapped[str] = mapped_column(Text)


class Permissao(Base):
    __tablename__ = "permissao"
    __table_args__ = (UniqueConstraint("modulo", "accao"),)
    id: Mapped[int] = pk()
    modulo: Mapped[str] = mapped_column(Text)
    accao: Mapped[str] = mapped_column(Text)


class PerfilPermissao(Base):
    __tablename__ = "perfil_permissao"
    perfil_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("perfil.id", ondelete="CASCADE"), primary_key=True)
    permissao_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("permissao.id", ondelete="CASCADE"), primary_key=True)


class UtilizadorPerfil(Base):
    __tablename__ = "utilizador_perfil"
    utilizador_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("utilizador.id", ondelete="CASCADE"), primary_key=True)
    perfil_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("perfil.id"), primary_key=True)
    estabelecimento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("estabelecimento.id"), primary_key=True)


class AuditoriaLog(Base):
    __tablename__ = "auditoria_log"
    __table_args__ = (
        ck_in("accao", "insert", "update", "delete", "login", "export"),
        Index("ix_auditoria_tabela", "tabela", "registo_id"),
        Index("ix_auditoria_data", text("criado_em DESC")),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int | None] = fk("estabelecimento.id", nullable=True)
    utilizador_id: Mapped[int | None] = fk("utilizador.id", nullable=True)
    tabela: Mapped[str] = mapped_column(Text)
    registo_id: Mapped[int | None] = mapped_column(BigInteger)
    accao: Mapped[str] = mapped_column(Text)
    dados_antes: Mapped[dict | None] = mapped_column(JSONType)
    dados_depois: Mapped[dict | None] = mapped_column(JSONType)
    ip: Mapped[str | None] = mapped_column(InetType)
    criado_em: Mapped[datetime] = criado_em()


# =====================================================================
# 4. PROPINAS, COBRANÇAS E PAGAMENTOS
# =====================================================================
CATEGORIAS = ("propina", "alimentacao", "refeitorio", "transporte", "actividade", "fardamento", "matricula", "outro")


class Servico(Base):
    __tablename__ = "servico"
    __table_args__ = (
        UniqueConstraint("estabelecimento_id", "codigo"),
        ck_in("categoria", *CATEGORIAS),
        ck_in("periodicidade", "mensal", "trimestral", "anual", "unica"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    codigo: Mapped[str] = mapped_column(Text)
    nome: Mapped[str] = mapped_column(Text)
    categoria: Mapped[str] = mapped_column(Text)
    periodicidade: Mapped[str] = mapped_column(Text, server_default="mensal")
    artigo_primavera: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class TabelaPreco(Base):
    __tablename__ = "tabela_preco"
    __table_args__ = (
        UniqueConstraint("servico_id", "ano_id", "classe_id"),
        CheckConstraint("valor >= 0", name="valor"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    servico_id: Mapped[int] = fk("servico.id")
    ano_id: Mapped[int] = fk("ano_lectivo.id")
    classe_id: Mapped[int | None] = fk("classe.id", nullable=True)
    valor: Mapped[Decimal] = mapped_column(Money)


class PlanoPagamento(Base):
    __tablename__ = "plano_pagamento"
    __table_args__ = (
        CheckConstraint("mes_inicio BETWEEN 1 AND 12", name="mes_inicio"),
        CheckConstraint("dia_vencimento BETWEEN 1 AND 31", name="dia_vencimento"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    num_prestacoes: Mapped[int] = mapped_column(SmallInteger)
    mes_inicio: Mapped[int] = mapped_column(SmallInteger)
    dia_vencimento: Mapped[int] = mapped_column(SmallInteger)


class MultaRegra(Base):
    __tablename__ = "multa_regra"
    __table_args__ = (ck_in("tipo", "percentual", "fixo"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    servico_id: Mapped[int | None] = fk("servico.id", nullable=True)
    dias_tolerancia: Mapped[int] = mapped_column(SmallInteger, server_default="0")
    tipo: Mapped[str] = mapped_column(Text)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    maximo: Mapped[Decimal | None] = mapped_column(Money)


class Desconto(Base):
    __tablename__ = "desconto"
    __table_args__ = (
        ck_in("origem", "manual", "irmaos", "bolsa", "funcionario", "pontualidade"),
        ck_in("tipo", "percentual", "fixo"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    origem: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    valor: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    servico_id: Mapped[int | None] = fk("servico.id", nullable=True)


class MatriculaDesconto(Base):
    __tablename__ = "matricula_desconto"
    id: Mapped[int] = pk()
    matricula_id: Mapped[int] = fk("matricula.id", ondelete="CASCADE")
    desconto_id: Mapped[int] = fk("desconto.id")
    valido_ate: Mapped[date | None] = mapped_column(Date)
    aprovado_por: Mapped[int | None] = fk("utilizador.id", nullable=True)


class MetodoPagamento(Base):
    __tablename__ = "metodo_pagamento"
    __table_args__ = (ck_in("tipo", "numerario", "transferencia", "multicaixa", "tpa", "deposito", "referencia"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class DocumentoFiscal(Base):
    """Espelho dos documentos emitidos no Primavera (a escola não emite documentos legais)."""
    __tablename__ = "documento_fiscal"
    __table_args__ = (
        UniqueConstraint("estabelecimento_id", "tipo", "serie", "numero"),
        ck_in("tipo", "fatura", "fatura_recibo", "recibo", "nota_credito"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    tipo: Mapped[str] = mapped_column(Text)
    serie: Mapped[str | None] = mapped_column(Text)
    numero: Mapped[str] = mapped_column(Text)
    data_emissao: Mapped[date] = mapped_column(Date)
    encarregado_id: Mapped[int | None] = fk("encarregado.id", nullable=True)
    nif_cliente: Mapped[str | None] = mapped_column(Text)
    total: Mapped[Decimal] = mapped_column(Money)
    id_primavera: Mapped[str | None] = mapped_column(Text)
    estado_agt: Mapped[str | None] = mapped_column(Text)
    pdf_url: Mapped[str | None] = mapped_column(Text)


class Cobranca(Base):
    __tablename__ = "cobranca"
    __table_args__ = (
        UniqueConstraint("matricula_id", "servico_id", "competencia"),
        ck_in("estado", "pendente", "parcial", "paga", "anulada"),
        Index("ix_cobranca_estado", "estabelecimento_id", "estado", "vencimento"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("estabelecimento.id"))
    matricula_id: Mapped[int] = fk("matricula.id")
    aluno_id: Mapped[int] = fk("aluno.id", index=True)
    encarregado_id: Mapped[int | None] = fk("encarregado.id", nullable=True)
    servico_id: Mapped[int] = fk("servico.id")
    competencia: Mapped[date] = mapped_column(Date)
    descricao: Mapped[str | None] = mapped_column(Text)
    valor_base: Mapped[Decimal] = mapped_column(Money)
    valor_desconto: Mapped[Decimal] = mapped_column(Money, server_default="0")
    valor_multa: Mapped[Decimal] = mapped_column(Money, server_default="0")
    valor_total: Mapped[Decimal] = mapped_column(
        Money, Computed("valor_base - valor_desconto + valor_multa", persisted=True))
    vencimento: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(Text, server_default="pendente")
    documento_fiscal_id: Mapped[int | None] = fk("documento_fiscal.id", nullable=True)
    criado_em: Mapped[datetime] = criado_em()
    actualizado_em: Mapped[datetime] = actualizado_em()


class Pagamento(Base):
    __tablename__ = "pagamento"
    __table_args__ = (
        CheckConstraint("valor > 0", name="valor"),
        ck_in("estado", "pendente", "confirmado", "rejeitado"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    encarregado_id: Mapped[int | None] = fk("encarregado.id", nullable=True)
    metodo_id: Mapped[int] = fk("metodo_pagamento.id")
    valor: Mapped[Decimal] = mapped_column(Money)
    data_pagamento: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    referencia: Mapped[str | None] = mapped_column(Text)
    comprovativo_url: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text, server_default="confirmado")
    documento_fiscal_id: Mapped[int | None] = fk("documento_fiscal.id", nullable=True)
    registado_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    criado_em: Mapped[datetime] = criado_em()


class PagamentoAlocacao(Base):
    __tablename__ = "pagamento_alocacao"
    __table_args__ = (CheckConstraint("valor > 0", name="valor"),)
    pagamento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("pagamento.id", ondelete="CASCADE"), primary_key=True)
    cobranca_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("cobranca.id"), primary_key=True)
    valor: Mapped[Decimal] = mapped_column(Money)


# =====================================================================
# 5. REFEITÓRIO E SERVIÇOS ADICIONAIS
# =====================================================================
class AdesaoServico(Base):
    __tablename__ = "adesao_servico"
    __table_args__ = (
        UniqueConstraint("matricula_id", "servico_id", "data_inicio"),
        ck_in("modalidade", "padrao", "so_utilizacao", "alimentacao_completa", "avulso"),
        ck_in("estado", "activa", "suspensa", "cancelada"),
    )
    id: Mapped[int] = pk()
    matricula_id: Mapped[int] = fk("matricula.id", ondelete="CASCADE")
    servico_id: Mapped[int] = fk("servico.id")
    modalidade: Mapped[str] = mapped_column(Text, server_default="padrao")
    data_inicio: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    data_fim: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(Text, server_default="activa")


class RefeicaoTipo(Base):
    __tablename__ = "refeicao_tipo"
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    hora_inicio: Mapped[time | None] = mapped_column(Time)


class Ementa(Base):
    __tablename__ = "ementa"
    __table_args__ = (UniqueConstraint("refeicao_tipo_id", "data"),)
    id: Mapped[int] = pk()
    refeicao_tipo_id: Mapped[int] = fk("refeicao_tipo.id")
    data: Mapped[date] = mapped_column(Date)
    descricao: Mapped[str] = mapped_column(Text)


class RefeicaoRegisto(Base):
    __tablename__ = "refeicao_registo"
    __table_args__ = (UniqueConstraint("aluno_id", "refeicao_tipo_id", "data"),)
    id: Mapped[int] = pk()
    aluno_id: Mapped[int] = fk("aluno.id")
    refeicao_tipo_id: Mapped[int] = fk("refeicao_tipo.id")
    data: Mapped[date] = mapped_column(Date)
    adesao_id: Mapped[int | None] = fk("adesao_servico.id", nullable=True)
    valor_avulso: Mapped[Decimal | None] = mapped_column(Money)
    cobranca_id: Mapped[int | None] = fk("cobranca.id", nullable=True)
    registado_por: Mapped[int | None] = fk("utilizador.id", nullable=True)


# =====================================================================
# 6. INTEGRAÇÃO COM O PRIMAVERA
# =====================================================================
class IntegracaoConfig(Base):
    __tablename__ = "integracao_config"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "sistema"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    sistema: Mapped[str] = mapped_column(Text, server_default="primavera")
    versao: Mapped[str | None] = mapped_column(Text)
    url_base: Mapped[str | None] = mapped_column(Text)
    credenciais_ref: Mapped[str | None] = mapped_column(Text)  # referência ao cofre, nunca a senha
    activo: Mapped[bool] = mapped_column(Boolean, server_default=false())


class IntegracaoMapeamento(Base):
    __tablename__ = "integracao_mapeamento"
    __table_args__ = (
        UniqueConstraint("estabelecimento_id", "entidade", "id_local", name="uq_integracao_mapeamento_local"),
        UniqueConstraint("estabelecimento_id", "entidade", "id_externo", name="uq_integracao_mapeamento_externo"),
        ck_in("entidade", "cliente", "artigo", "documento", "pagamento"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    entidade: Mapped[str] = mapped_column(Text)
    id_local: Mapped[int] = mapped_column(BigInteger)
    id_externo: Mapped[str] = mapped_column(Text)
    hash_dados: Mapped[str | None] = mapped_column(Text)
    ultima_sync: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegracaoSyncLog(Base):
    __tablename__ = "integracao_sync_log"
    __table_args__ = (
        ck_in("direccao", "envio", "recepcao"),
        ck_in("estado", "em_curso", "ok", "parcial", "erro"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    entidade: Mapped[str] = mapped_column(Text)
    direccao: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text)
    total_registos: Mapped[int] = mapped_column(Integer, server_default="0")
    total_erros: Mapped[int] = mapped_column(Integer, server_default="0")
    detalhe: Mapped[dict | None] = mapped_column(JSONType)
    iniciado_em: Mapped[datetime] = criado_em()
    terminado_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegracaoDivergencia(Base):
    __tablename__ = "integracao_divergencia"
    __table_args__ = (
        ck_in("estado", "aberta", "resolvida", "ignorada"),
        Index("ix_divergencia_aberta", "estabelecimento_id",
              postgresql_where=text("estado = 'aberta'"), sqlite_where=text("estado = 'aberta'")),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("estabelecimento.id"))
    entidade: Mapped[str] = mapped_column(Text)
    id_local: Mapped[int | None] = mapped_column(BigInteger)
    id_externo: Mapped[str | None] = mapped_column(Text)
    campo: Mapped[str | None] = mapped_column(Text)
    valor_local: Mapped[str | None] = mapped_column(Text)
    valor_externo: Mapped[str | None] = mapped_column(Text)
    descricao: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text, server_default="aberta")
    resolvida_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    resolvida_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detectada_em: Mapped[datetime] = criado_em()


# =====================================================================
# 7. COMUNICAÇÃO E MOTOR DE REGRAS
# =====================================================================
CANAIS = ("whatsapp", "sms", "email", "app")


class ModeloMensagem(Base):
    __tablename__ = "modelo_mensagem"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "codigo", "canal"), ck_in("canal", *CANAIS))
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    codigo: Mapped[str] = mapped_column(Text)
    canal: Mapped[str] = mapped_column(Text)
    assunto: Mapped[str | None] = mapped_column(Text)
    corpo: Mapped[str] = mapped_column(Text)
    variaveis: Mapped[list | None] = mapped_column(JSONType)


class RegraAutomacao(Base):
    __tablename__ = "regra_automacao"
    __table_args__ = (
        ck_in("evento", "antes_vencimento", "apos_vencimento", "divida_acima_dias",
              "ocorrencia", "nota_baixa", "faltas_excessivas"),
        ck_in("destino", "encarregado", "direccao"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome: Mapped[str] = mapped_column(Text)
    evento: Mapped[str] = mapped_column(Text)
    dias_offset: Mapped[int | None] = mapped_column(Integer)
    condicao: Mapped[dict | None] = mapped_column(JSONType)
    canais: Mapped[list[str]] = mapped_column(TextArray)
    modelo_id: Mapped[int | None] = fk("modelo_mensagem.id", nullable=True)
    destino: Mapped[str] = mapped_column(Text, server_default="encarregado")
    activa: Mapped[bool] = mapped_column(Boolean, server_default=true())
    criada_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    criada_em: Mapped[datetime] = criado_em()


class Comunicado(Base):
    __tablename__ = "comunicado"
    __table_args__ = (ck_in("alvo", "todos", "classe", "turma"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    titulo: Mapped[str] = mapped_column(Text)
    corpo: Mapped[str] = mapped_column(Text)
    autor_id: Mapped[int | None] = fk("utilizador.id", nullable=True)
    alvo: Mapped[str] = mapped_column(Text, server_default="todos")
    criado_em: Mapped[datetime] = criado_em()


class ComunicadoAlvo(Base):
    __tablename__ = "comunicado_alvo"
    # O esquema original não define chave primária; o ORM precisa de uma.
    id: Mapped[int] = pk()
    comunicado_id: Mapped[int] = fk("comunicado.id", ondelete="CASCADE")
    turma_id: Mapped[int | None] = fk("turma.id", nullable=True)
    classe_id: Mapped[int | None] = fk("classe.id", nullable=True)


class Mensagem(Base):
    """Fila e histórico de envios."""
    __tablename__ = "mensagem"
    __table_args__ = (
        ck_in("canal", *CANAIS),
        ck_in("estado", "agendada", "enviada", "entregue", "lida", "falhada"),
        Index("ix_mensagem_fila", "estado", "agendada_para",
              postgresql_where=text("estado = 'agendada'"), sqlite_where=text("estado = 'agendada'")),
        # Evita enviar a mesma regra duas vezes para a mesma cobrança e canal
        Index("uq_mensagem_regra", "regra_id", "cobranca_id", "canal", unique=True,
              postgresql_where=text("regra_id IS NOT NULL"), sqlite_where=text("regra_id IS NOT NULL")),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    regra_id: Mapped[int | None] = fk("regra_automacao.id", nullable=True)
    comunicado_id: Mapped[int | None] = fk("comunicado.id", nullable=True)
    encarregado_id: Mapped[int] = fk("encarregado.id")
    aluno_id: Mapped[int | None] = fk("aluno.id", nullable=True)
    cobranca_id: Mapped[int | None] = fk("cobranca.id", nullable=True)
    canal: Mapped[str] = mapped_column(Text)
    assunto: Mapped[str | None] = mapped_column(Text)
    corpo: Mapped[str] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text, server_default="agendada")
    agendada_para: Mapped[datetime] = criado_em()
    enviada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    id_externo: Mapped[str | None] = mapped_column(Text)
    erro: Mapped[str | None] = mapped_column(Text)


class PreferenciaContacto(Base):
    __tablename__ = "preferencia_contacto"
    __table_args__ = (ck_in("canal", *CANAIS),)
    encarregado_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("encarregado.id", ondelete="CASCADE"), primary_key=True)
    canal: Mapped[str] = mapped_column(Text, primary_key=True)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class DispositivoPush(Base):
    __tablename__ = "dispositivo_push"
    __table_args__ = (ck_in("plataforma", "android", "ios", "web"),)
    id: Mapped[int] = pk()
    utilizador_id: Mapped[int] = fk("utilizador.id", ondelete="CASCADE")
    token: Mapped[str] = mapped_column(Text, unique=True)
    plataforma: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = criado_em()


# =====================================================================
# 8. PEDAGÓGICO
# =====================================================================
class TipoAvaliacao(Base):
    __tablename__ = "tipo_avaliacao"
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    peso: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default="1")


class Avaliacao(Base):
    __tablename__ = "avaliacao"
    id: Mapped[int] = pk()
    turma_id: Mapped[int] = fk("turma.id")
    disciplina_id: Mapped[int] = fk("disciplina.id")
    periodo_id: Mapped[int] = fk("periodo_lectivo.id")
    tipo_id: Mapped[int] = fk("tipo_avaliacao.id")
    data: Mapped[date] = mapped_column(Date)
    descricao: Mapped[str | None] = mapped_column(Text)


class Nota(Base):
    __tablename__ = "nota"
    __table_args__ = (
        UniqueConstraint("avaliacao_id", "aluno_id"),
        CheckConstraint("valor BETWEEN 0 AND 20", name="valor"),
    )
    id: Mapped[int] = pk()
    avaliacao_id: Mapped[int] = fk("avaliacao.id", ondelete="CASCADE")
    aluno_id: Mapped[int] = fk("aluno.id")
    valor: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    observacao: Mapped[str | None] = mapped_column(Text)
    lancada_por: Mapped[int | None] = fk("utilizador.id", nullable=True)


class ResultadoPeriodo(Base):
    __tablename__ = "resultado_periodo"
    __table_args__ = (UniqueConstraint("matricula_id", "disciplina_id", "periodo_id"),)
    id: Mapped[int] = pk()
    matricula_id: Mapped[int] = fk("matricula.id")
    disciplina_id: Mapped[int] = fk("disciplina.id")
    periodo_id: Mapped[int] = fk("periodo_lectivo.id")
    media: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))


class ResultadoFinal(Base):
    __tablename__ = "resultado_final"
    __table_args__ = (ck_in("situacao", "aprovado", "reprovado", "recurso", "transita", "excluido_faltas"),)
    matricula_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("matricula.id"), primary_key=True)
    media_final: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    situacao: Mapped[str | None] = mapped_column(Text)
    fechada_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Assiduidade(Base):
    __tablename__ = "assiduidade"
    __table_args__ = (
        UniqueConstraint("aluno_id", "turma_id", "disciplina_id", "data"),
        ck_in("estado", "presente", "falta_justificada", "falta_injustificada", "atraso"),
    )
    id: Mapped[int] = pk()
    aluno_id: Mapped[int] = fk("aluno.id")
    turma_id: Mapped[int] = fk("turma.id")
    disciplina_id: Mapped[int | None] = fk("disciplina.id", nullable=True)
    data: Mapped[date] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(Text)
    justificacao: Mapped[str | None] = mapped_column(Text)
    registada_por: Mapped[int | None] = fk("utilizador.id", nullable=True)


class TipoOcorrencia(Base):
    __tablename__ = "tipo_ocorrencia"
    __table_args__ = (ck_in("gravidade", "positiva", "leve", "moderada", "grave"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    designacao: Mapped[str] = mapped_column(Text)
    gravidade: Mapped[str] = mapped_column(Text)


class Ocorrencia(Base):
    __tablename__ = "ocorrencia"
    __table_args__ = (ck_in("estado", "aberta", "em_acompanhamento", "fechada"),)
    id: Mapped[int] = pk()
    aluno_id: Mapped[int] = fk("aluno.id")
    tipo_id: Mapped[int] = fk("tipo_ocorrencia.id")
    data: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    descricao: Mapped[str] = mapped_column(Text)
    accao_tomada: Mapped[str | None] = mapped_column(Text)
    notificar_ee: Mapped[bool] = mapped_column(Boolean, server_default=false())
    estado: Mapped[str] = mapped_column(Text, server_default="aberta")
    registada_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    criada_em: Mapped[datetime] = criado_em()


class RegistoDiario(Base):
    __tablename__ = "registo_diario"
    __table_args__ = (ck_in("comportamento", "bom", "regular", "atencao"),)
    id: Mapped[int] = pk()
    turma_id: Mapped[int] = fk("turma.id")
    aluno_id: Mapped[int | None] = fk("aluno.id", nullable=True)  # NULL = registo da turma
    data: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    comportamento: Mapped[str | None] = mapped_column(Text)
    nota_texto: Mapped[str | None] = mapped_column(Text)
    autor_id: Mapped[int | None] = fk("utilizador.id", nullable=True)


# =====================================================================
# 9. ALERTAS, KPIs, RELATÓRIOS E IA
# =====================================================================
# =====================================================================
# TRANSPORTE ESCOLAR
# =====================================================================
class Viatura(Base):
    __tablename__ = "viatura"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "matricula"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    matricula: Mapped[str] = mapped_column(Text)          # matrícula da viatura, ex.: LD-12-34-AB
    descricao: Mapped[str | None] = mapped_column(Text)   # marca e modelo
    lugares: Mapped[int] = mapped_column(SmallInteger)
    motorista: Mapped[str | None] = mapped_column(Text)
    telefone_motorista: Mapped[str | None] = mapped_column(Text)
    observacoes: Mapped[str | None] = mapped_column(Text)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class RotaTransporte(Base):
    __tablename__ = "rota_transporte"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "nome"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome: Mapped[str] = mapped_column(Text)
    descricao: Mapped[str | None] = mapped_column(Text)
    viatura_id: Mapped[int | None] = fk("viatura.id", nullable=True)
    servico_id: Mapped[int | None] = fk("servico.id", nullable=True)   # serviço cobrado a quem se inscreve
    hora_ida: Mapped[time | None] = mapped_column(Time)
    hora_volta: Mapped[time | None] = mapped_column(Time)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class ParagemTransporte(Base):
    __tablename__ = "paragem_transporte"
    id: Mapped[int] = pk()
    rota_id: Mapped[int] = fk("rota_transporte.id", ondelete="CASCADE")
    ordem: Mapped[int] = mapped_column(SmallInteger)
    nome: Mapped[str] = mapped_column(Text)
    referencia: Mapped[str | None] = mapped_column(Text)
    hora_ida: Mapped[time | None] = mapped_column(Time)
    hora_volta: Mapped[time | None] = mapped_column(Time)


class AlunoTransporte(Base):
    """Inscrição de um aluno numa rota; com o serviço da rota, cria a adesão que o põe nas cobranças."""
    __tablename__ = "aluno_transporte"
    __table_args__ = (
        ck_in("sentido", "ida", "volta", "ida_volta"),
        ck_in("estado", "activa", "suspensa", "cancelada"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    aluno_id: Mapped[int] = fk("aluno.id")
    rota_id: Mapped[int] = fk("rota_transporte.id")
    paragem_id: Mapped[int | None] = fk("paragem_transporte.id", nullable=True, ondelete="SET NULL")
    sentido: Mapped[str] = mapped_column(Text, server_default="ida_volta")
    data_inicio: Mapped[date] = mapped_column(Date, server_default=func.current_date())
    data_fim: Mapped[date | None] = mapped_column(Date)
    estado: Mapped[str] = mapped_column(Text, server_default="activa")
    adesao_id: Mapped[int | None] = fk("adesao_servico.id", nullable=True, ondelete="SET NULL")
    criado_em: Mapped[datetime] = criado_em()


class AvaliacaoProfessor(Base):
    """Avaliação de um professor por um encarregado (1 a 5), uma por educando, professor e trimestre."""
    __tablename__ = "avaliacao_professor"
    __table_args__ = (
        UniqueConstraint("encarregado_id", "funcionario_id", "aluno_id", "periodo_id"),
        CheckConstraint("nota BETWEEN 1 AND 5", name="nota"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    encarregado_id: Mapped[int] = fk("encarregado.id")
    funcionario_id: Mapped[int] = fk("funcionario.id")
    aluno_id: Mapped[int] = fk("aluno.id")
    periodo_id: Mapped[int] = fk("periodo_lectivo.id")
    nota: Mapped[int] = mapped_column(SmallInteger)
    comentario: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = criado_em()


TIPOS_PEDIDO = ("vaga", "matricula", "transferencia", "declaracao", "justificacao_falta")


class Pedido(Base):
    """Pedido do encarregado pelo portal, decidido pela escola (aprovar executa o efeito: matrícula, PDF…)."""
    __tablename__ = "pedido"
    __table_args__ = (
        ck_in("tipo", *TIPOS_PEDIDO),
        ck_in("estado", "pendente", "aprovado", "rejeitado", "cancelado"),
        Index("ix_pedido_estado", "estabelecimento_id", "estado"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    tipo: Mapped[str] = mapped_column(Text)
    encarregado_id: Mapped[int] = fk("encarregado.id")
    aluno_id: Mapped[int | None] = fk("aluno.id", nullable=True)   # na vaga, preenchido ao aprovar
    dados: Mapped[dict] = mapped_column(JSONType)
    estado: Mapped[str] = mapped_column(Text, server_default="pendente")
    resposta: Mapped[str | None] = mapped_column(Text)
    resultado: Mapped[dict | None] = mapped_column(JSONType)      # o que a aprovação fez (matrícula, turma…)
    decidido_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    decidido_em: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = criado_em()


class Alerta(Base):
    __tablename__ = "alerta"
    __table_args__ = (
        ck_in("tipo", "risco_incumprimento", "queda_desempenho", "padrao_faltas",
              "divergencia_primavera", "comportamento", "comprovativo_pagamento", "pedido_encarregado"),
        ck_in("gravidade", "baixa", "media", "alta"),
        ck_in("estado", "novo", "visto", "tratado", "descartado"),
        Index("ix_alerta_aberto", "estabelecimento_id", "tipo",
              postgresql_where=text("estado IN ('novo','visto')"), sqlite_where=text("estado IN ('novo','visto')")),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("estabelecimento.id"))
    tipo: Mapped[str] = mapped_column(Text)
    aluno_id: Mapped[int | None] = fk("aluno.id", nullable=True)
    gravidade: Mapped[str] = mapped_column(Text, server_default="media")
    mensagem: Mapped[str] = mapped_column(Text)
    dados: Mapped[dict | None] = mapped_column(JSONType)
    estado: Mapped[str] = mapped_column(Text, server_default="novo")
    tratado_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    criado_em: Mapped[datetime] = criado_em()


class Indicador(Base):
    __tablename__ = "indicador"
    __table_args__ = (UniqueConstraint("estabelecimento_id", "codigo"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int | None] = fk("estabelecimento.id", nullable=True)
    codigo: Mapped[str] = mapped_column(Text)
    nome: Mapped[str] = mapped_column(Text)
    vista_origem: Mapped[str] = mapped_column(Text)
    perfil_minimo: Mapped[str] = mapped_column(Text, server_default="direccao")


class RelatorioAgendado(Base):
    __tablename__ = "relatorio_agendado"
    __table_args__ = (ck_in("formato", "pdf", "xlsx", "csv"),)
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    nome: Mapped[str] = mapped_column(Text)
    tipo: Mapped[str] = mapped_column(Text)
    filtros: Mapped[dict | None] = mapped_column(JSONType)
    formato: Mapped[str] = mapped_column(Text, server_default="pdf")
    agenda_cron: Mapped[str] = mapped_column(Text)
    destinatarios: Mapped[list[str]] = mapped_column(TextArray)
    activo: Mapped[bool] = mapped_column(Boolean, server_default=true())


class RelatorioExecucao(Base):
    __tablename__ = "relatorio_execucao"
    __table_args__ = (ck_in("estado", "ok", "erro"),)
    id: Mapped[int] = pk()
    relatorio_id: Mapped[int] = fk("relatorio_agendado.id", ondelete="CASCADE")
    estado: Mapped[str] = mapped_column(Text)
    ficheiro_url: Mapped[str | None] = mapped_column(Text)
    executado_em: Mapped[datetime] = criado_em()


class IaConsultaLog(Base):
    __tablename__ = "ia_consulta_log"
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    utilizador_id: Mapped[int] = fk("utilizador.id")
    pergunta: Mapped[str] = mapped_column(Text)
    consulta_gerada: Mapped[str | None] = mapped_column(Text)
    resposta: Mapped[str | None] = mapped_column(Text)
    criado_em: Mapped[datetime] = criado_em()


# =====================================================================
# 10. IMPORTAÇÃO DE DADOS DO SOFTWARE ACTUAL
# =====================================================================
class ImportacaoLote(Base):
    __tablename__ = "importacao_lote"
    __table_args__ = (
        ck_in("entidade", "aluno", "encarregado", "turma", "cobranca", "pagamento"),
        ck_in("estado", "em_curso", "concluido", "erro"),
    )
    id: Mapped[int] = pk()
    estabelecimento_id: Mapped[int] = estab_fk()
    origem: Mapped[str] = mapped_column(Text)
    entidade: Mapped[str] = mapped_column(Text)
    ficheiro: Mapped[str | None] = mapped_column(Text)
    estado: Mapped[str] = mapped_column(Text, server_default="em_curso")
    total_linhas: Mapped[int] = mapped_column(Integer, server_default="0")
    linhas_ok: Mapped[int] = mapped_column(Integer, server_default="0")
    criado_por: Mapped[int | None] = fk("utilizador.id", nullable=True)
    criado_em: Mapped[datetime] = criado_em()


class ImportacaoErro(Base):
    __tablename__ = "importacao_erro"
    id: Mapped[int] = pk()
    lote_id: Mapped[int] = fk("importacao_lote.id", ondelete="CASCADE")
    linha: Mapped[int] = mapped_column(Integer)
    mensagem: Mapped[str] = mapped_column(Text)
    dados: Mapped[dict | None] = mapped_column(JSONType)
