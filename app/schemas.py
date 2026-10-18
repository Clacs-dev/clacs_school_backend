"""Esquemas Pydantic de entrada. As respostas são dicionários com as colunas das tabelas."""
from datetime import date, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Entrada(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def vazio_para_none(cls, v):
        return None if v == "" else v


Canal = Literal["whatsapp", "sms", "email", "app"]


# ---------------------------------------------------------------- auth / utilizadores
class LoginIn(Entrada):
    email: str
    senha: str


class UtilizadorIn(Entrada):
    nome: str = Field(min_length=2)
    email: str
    senha: str = Field(min_length=8)
    telefone: str | None = None
    perfis: list[str] = ["secretaria"]
    funcionario_id: int | None = None
    encarregado_id: int | None = None


class UtilizadorUpd(Entrada):
    nome: str | None = None
    telefone: str | None = None
    activo: bool | None = None
    perfis: list[str] | None = None
    senha: str | None = Field(default=None, min_length=8)


class RecuperarIn(Entrada):
    email: str


class RedefinirIn(Entrada):
    token: str
    senha_nova: str = Field(min_length=8)


class CodigoIn(Entrada):
    codigo: str = Field(min_length=6, max_length=8)


class SenhaIn(Entrada):
    senha_actual: str
    senha_nova: str = Field(min_length=8)


# ---------------------------------------------------------------- académico
class EstabelecimentoUpd(Entrada):
    nome: str | None = None
    nif: str | None = None
    morada: str | None = None
    telefone: str | None = None
    email: str | None = None
    logotipo_url: str | None = None


class ParametroIn(Entrada):
    valor: dict | list | int | float | str | bool
    descricao: str | None = None


class AnoIn(Entrada):
    designacao: str = Field(min_length=4)
    data_inicio: date
    data_fim: date
    criar_trimestres: bool = True


class PeriodoIn(Entrada):
    designacao: str
    ordem: int
    data_inicio: date
    data_fim: date


class NivelIn(Entrada):
    nome: str
    ordem: int = 0


class ClasseIn(Entrada):
    nivel_id: int
    designacao: str
    ordem: int


class SalaIn(Entrada):
    designacao: str
    capacidade: int | None = None


class DisciplinaIn(Entrada):
    nome: str
    sigla: str | None = None


class ClasseDisciplinaIn(Entrada):
    disciplina_id: int
    carga_horaria: int | None = None


Cargo = Literal["professor", "coordenador", "director", "secretaria", "financeiro", "outro"]


class FuncionarioIn(Entrada):
    nome_completo: str
    cargo: Cargo
    nif: str | None = None
    telefone: str | None = None
    email: str | None = None
    activo: bool = True


class FuncionarioUpd(Entrada):
    nome_completo: str | None = None
    cargo: Cargo | None = None
    nif: str | None = None
    telefone: str | None = None
    email: str | None = None
    activo: bool | None = None


class TurmaIn(Entrada):
    designacao: str
    classe_id: int
    ano_id: int | None = None  # por omissão, o ano activo
    sala_id: int | None = None
    periodo_dia: Literal["manha", "tarde", "noite"] = "manha"
    director_turma_id: int | None = None
    capacidade: int = Field(30, ge=1)


class TurmaUpd(Entrada):
    designacao: str | None = None
    sala_id: int | None = None
    periodo_dia: Literal["manha", "tarde", "noite"] | None = None
    director_turma_id: int | None = None
    capacidade: int | None = Field(None, ge=1)


class AtribuicaoIn(Entrada):
    disciplina_id: int
    funcionario_id: int


# ---------------------------------------------------------------- cadastro
Parentesco = Literal["mae", "pai", "tutor", "outro"]


class AlunoBase(Entrada):
    nome_completo: str | None = None
    numero_processo: str | None = None
    data_nascimento: date | None = None
    sexo: Literal["F", "M"] | None = None
    nacionalidade: str | None = None
    bi_numero: str | None = None
    morada: str | None = None
    alergias: str | None = None
    necessidades_especiais: str | None = None
    escola_anterior: str | None = None
    foto_url: str | None = None


class AlunoIn(AlunoBase):
    nome_completo: str = Field(min_length=2)
    # Atalhos do ecrã "Novo aluno": liga o encarregado e matricula no ano activo.
    encarregado_id: int | None = None
    parentesco: Parentesco = "mae"
    turma_id: int | None = None


class AlunoUpd(AlunoBase):
    estado: Literal["activo", "transferido", "desistente", "concluido"] | None = None


class EncarregadoBase(Entrada):
    nome_completo: str | None = None
    nif: str | None = None
    bi_numero: str | None = None
    telefone: str | None = None
    whatsapp: str | None = None
    email: str | None = None
    profissao: str | None = None
    morada: str | None = None


class EncarregadoIn(EncarregadoBase):
    nome_completo: str = Field(min_length=2)


class EncarregadoUpd(EncarregadoBase):
    activo: bool | None = None


class LigacaoIn(Entrada):
    encarregado_id: int
    parentesco: Parentesco
    principal: bool = False
    responsavel_financeiro: bool = False
    autorizado_recolha: bool = True


class DocumentoIn(Entrada):
    tipo: str
    ficheiro_url: str
    validade: date | None = None


class MatriculaIn(Entrada):
    aluno_id: int
    turma_id: int
    tipo: Literal["nova", "renovacao", "transferencia"] = "renovacao"
    observacoes: str | None = None


class MudancaTurmaIn(Entrada):
    turma_id: int
    motivo: str | None = None


# ---------------------------------------------------------------- financeiro
class ServicoIn(Entrada):
    codigo: str
    nome: str
    categoria: Literal["propina", "alimentacao", "refeitorio", "transporte", "actividade", "fardamento",
                       "matricula", "outro"]
    periodicidade: Literal["mensal", "trimestral", "anual", "unica"] = "mensal"
    artigo_primavera: str | None = None
    activo: bool = True


class PrecoIn(Entrada):
    servico_id: int
    valor: Decimal = Field(ge=0)
    classe_id: int | None = None
    ano_id: int | None = None


class PlanoIn(Entrada):
    designacao: str
    num_prestacoes: int = Field(ge=1)
    mes_inicio: int = Field(ge=1, le=12)
    dia_vencimento: int = Field(ge=1, le=31)


class MultaIn(Entrada):
    servico_id: int | None = None
    dias_tolerancia: int = 0
    tipo: Literal["percentual", "fixo"]
    valor: Decimal
    maximo: Decimal | None = None


class DescontoIn(Entrada):
    designacao: str
    origem: Literal["manual", "irmaos", "bolsa", "funcionario", "pontualidade"]
    tipo: Literal["percentual", "fixo"]
    valor: Decimal
    servico_id: int | None = None


class MatriculaDescontoIn(Entrada):
    desconto_id: int
    valido_ate: date | None = None


class MetodoIn(Entrada):
    nome: str
    tipo: Literal["numerario", "transferencia", "multicaixa", "tpa", "deposito", "referencia"]


class GerarIn(Entrada):
    competencia: date


class CobrancaAvulsaIn(Entrada):
    aluno_id: int
    servico_id: int
    competencia: date
    valor_base: Decimal | None = None  # por omissão, o preço da tabela
    vencimento: date | None = None
    descricao: str | None = None


class AlocacaoIn(Entrada):
    cobranca_id: int
    valor: Decimal = Field(gt=0)


class PagamentoIn(Entrada):
    metodo_id: int
    valor: Decimal = Field(gt=0)
    encarregado_id: int | None = None
    cobranca_ids: list[int] | None = None
    alocacoes: list[AlocacaoIn] | None = None
    data_pagamento: date | None = None
    referencia: str | None = None
    comprovativo_url: str | None = None
    estado: Literal["pendente", "confirmado"] = "confirmado"


class LembreteIn(Entrada):
    canais: list[Canal] = ["whatsapp"]
    turma_id: int | None = None
    servico_id: int | None = None
    categoria: str | None = None
    q: str | None = None
    cobranca_ids: list[int] | None = None


# ---------------------------------------------------------------- refeitório
class AdesaoIn(Entrada):
    aluno_id: int
    servico_id: int
    modalidade: Literal["padrao", "so_utilizacao", "alimentacao_completa", "avulso"] = "padrao"
    data_inicio: date | None = None


class AdesaoUpd(Entrada):
    modalidade: Literal["padrao", "so_utilizacao", "alimentacao_completa", "avulso"] | None = None
    estado: Literal["activa", "suspensa", "cancelada"] | None = None
    data_fim: date | None = None


class RefeicaoTipoIn(Entrada):
    designacao: str
    hora_inicio: time | None = None


class EmentaIn(Entrada):
    refeicao_tipo_id: int
    data: date
    descricao: str


class RefeicaoRegistoIn(Entrada):
    refeicao_tipo_id: int
    data: date | None = None
    aluno_ids: list[int]
    valor_avulso: Decimal | None = None


# ---------------------------------------------------------------- comunicação
class ModeloIn(Entrada):
    codigo: str
    canal: Canal
    assunto: str | None = None
    corpo: str
    variaveis: list[str] | None = None


class RegraIn(Entrada):
    nome: str
    evento: Literal["antes_vencimento", "apos_vencimento", "divida_acima_dias", "ocorrencia", "nota_baixa",
                    "faltas_excessivas"] = "apos_vencimento"
    dias_offset: int | None = None
    condicao: dict | None = None
    canais: list[Canal] = Field(min_length=1)
    modelo_id: int | None = None
    destino: Literal["encarregado", "direccao"] = "encarregado"
    activa: bool = True


class RegraUpd(Entrada):
    nome: str | None = None
    dias_offset: int | None = None
    condicao: dict | None = None
    canais: list[Canal] | None = None
    modelo_id: int | None = None
    activa: bool | None = None


class MensagemAlunoIn(Entrada):
    corpo: str = Field(min_length=1)
    canais: list[Canal] = ["whatsapp"]
    assunto: str | None = None


class ComunicadoIn(Entrada):
    titulo: str
    corpo: str
    alvo: Literal["todos", "classe", "turma"] = "todos"
    turma_ids: list[int] = []
    classe_ids: list[int] = []
    canais: list[Canal] = ["app"]


class PreferenciaIn(Entrada):
    canal: Canal
    activo: bool


# ---------------------------------------------------------------- pedagógico
class TipoAvaliacaoIn(Entrada):
    designacao: str
    peso: Decimal = Decimal(1)


class AvaliacaoIn(Entrada):
    turma_id: int
    disciplina_id: int
    periodo_id: int
    tipo_id: int
    data: date
    descricao: str | None = None


class NotaItem(Entrada):
    aluno_id: int
    valor: Decimal | None = Field(None, ge=0, le=20)
    observacao: str | None = None


class NotasIn(Entrada):
    notas: list[NotaItem]


class PresencaItem(Entrada):
    aluno_id: int
    estado: Literal["presente", "falta_justificada", "falta_injustificada", "atraso"]
    justificacao: str | None = None


class AssiduidadeIn(Entrada):
    turma_id: int
    data: date
    disciplina_id: int | None = None
    registos: list[PresencaItem]


class TipoOcorrenciaIn(Entrada):
    designacao: str
    gravidade: Literal["positiva", "leve", "moderada", "grave"]


class OcorrenciaIn(Entrada):
    aluno_id: int
    tipo_id: int
    data: date | None = None
    descricao: str
    accao_tomada: str | None = None
    notificar_ee: bool = False


class OcorrenciaUpd(Entrada):
    accao_tomada: str | None = None
    estado: Literal["aberta", "em_acompanhamento", "fechada"] | None = None


class RegistoDiarioIn(Entrada):
    turma_id: int
    aluno_id: int | None = None
    data: date | None = None
    comportamento: Literal["bom", "regular", "atencao"] | None = None
    nota_texto: str | None = None


# ---------------------------------------------------------------- outros
class EstadoIn(Entrada):
    estado: str


class PerguntaIn(Entrada):
    pergunta: str = Field(min_length=2, max_length=500)


class IntegracaoIn(Entrada):
    versao: str | None = None
    url_base: str | None = None
    credenciais_ref: str | None = None
    activo: bool | None = None


class RelatorioIn(Entrada):
    nome: str
    tipo: Literal["cobranca_turma", "devedores", "refeitorio", "cobrancas"]
    filtros: dict | None = None
    formato: Literal["pdf", "xlsx", "csv"] = "xlsx"
    agenda_cron: str
    destinatarios: list[str]
    activo: bool = True
