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


class PrecoClasseIn(Entrada):
    classe_id: int
    valor: Decimal = Field(ge=0)


class GerarIn(Entrada):
    """Mapa de cobrança do mês. Os campos de configuração são opcionais quando já estão gravados
    (veja GET /api/cobrancas/configuracao); sem eles configurados, não gera."""
    competencia: date
    servico_id: int | None = Field(None, description="A propina; por omissão, a única propina mensal activa")
    valor: Decimal | None = Field(None, ge=0, description="Preço da propina, igual para todas as classes")
    precos: list[PrecoClasseIn] | None = Field(None, description="Preço por classe (sobrepõe valor nessas classes)")
    multa_percentual: Decimal | None = Field(None, gt=0, le=100, description="Multa sobre a propina em atraso, em %")
    dias_tolerancia: int | None = Field(None, ge=0, le=90, description="Dias depois do vencimento sem multa")
    multa_maximo: Decimal | None = Field(None, gt=0, description="Tecto da multa em Kz (opcional)")


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
    """Estado de um alerta: visto (aberto mas lido), tratado ou descartado; novo volta a abri-lo."""
    estado: Literal["novo", "visto", "tratado", "descartado"]


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


# ---------------------------------------------------------------- exemplos para o /api/docs
# Um pedido de exemplo por esquema (o "Example Value" do Swagger). O teste test_exemplos_de_pedido garante
# que cada exemplo passa na validação do próprio esquema.
EXEMPLOS: dict[type[Entrada], dict] = {
    LoginIn: {"email": "secretaria@clacs.ao", "senha": "senha-forte-123"},
    UtilizadorIn: {"nome": "Marta Kiala", "email": "marta.kiala@clacs.ao", "senha": "Mudar-2026!",
                   "telefone": "+244923456789", "perfis": ["secretaria"]},
    UtilizadorUpd: {"telefone": "+244923000111", "perfis": ["secretaria", "financeiro"], "activo": True},
    RecuperarIn: {"email": "marta.kiala@clacs.ao"},
    RedefinirIn: {"token": "token-recebido-por-email", "senha_nova": "Nova-senha-2026"},
    CodigoIn: {"codigo": "482913"},
    SenhaIn: {"senha_actual": "Mudar-2026!", "senha_nova": "Nova-senha-2026"},
    EstabelecimentoUpd: {"nome": "Colégio Cota Lionel", "nif": "5417098765", "morada": "Rua da Missão 12, Luanda",
                         "telefone": "+244222123456", "email": "geral@clacs.ao"},
    ParametroIn: {"valor": 3, "descricao": "Número máximo de faltas injustificadas antes de alerta"},
    AnoIn: {"designacao": "2027/2028", "data_inicio": "2027-09-01", "data_fim": "2028-07-15",
            "criar_trimestres": True},
    PeriodoIn: {"designacao": "1.º Trimestre", "ordem": 1, "data_inicio": "2026-09-01", "data_fim": "2026-12-15"},
    NivelIn: {"nome": "Ensino Primário", "ordem": 2},
    ClasseIn: {"nivel_id": 2, "designacao": "3.ª Classe", "ordem": 3},
    SalaIn: {"designacao": "Sala 12", "capacidade": 32},
    DisciplinaIn: {"nome": "Educação Musical", "sigla": "EM"},
    ClasseDisciplinaIn: {"disciplina_id": 8, "carga_horaria": 2},
    FuncionarioIn: {"nome_completo": "João Manuel Sebastião", "cargo": "professor", "telefone": "+244912345678",
                    "email": "joao.sebastiao@clacs.ao"},
    FuncionarioUpd: {"cargo": "coordenador", "telefone": "+244912000222"},
    TurmaIn: {"designacao": "3.ª A", "classe_id": 3, "sala_id": 1, "periodo_dia": "manha",
              "director_turma_id": 1, "capacidade": 30},
    TurmaUpd: {"sala_id": 2, "capacidade": 32},
    AtribuicaoIn: {"disciplina_id": 1, "funcionario_id": 1},
    AlunoIn: {"nome_completo": "Ana Paula Domingos", "data_nascimento": "2018-03-14", "sexo": "F",
              "nacionalidade": "Angolana", "bi_numero": "006123456LA041", "morada": "Talatona, Luanda",
              "alergias": "Amendoim", "encarregado_id": 1, "parentesco": "mae", "turma_id": 1},
    AlunoUpd: {"morada": "Kilamba, Bloco Q, Luanda", "estado": "activo"},
    EncarregadoIn: {"nome_completo": "Isabel Domingos", "nif": "004567891LA033", "telefone": "+244923111222",
                    "whatsapp": "+244923111222", "email": "isabel.domingos@gmail.com", "profissao": "Enfermeira",
                    "morada": "Talatona, Luanda"},
    EncarregadoUpd: {"whatsapp": "+244934555666", "email": "isabel.d@hotmail.com"},
    LigacaoIn: {"encarregado_id": 2, "parentesco": "pai", "principal": False, "responsavel_financeiro": True,
                "autorizado_recolha": True},
    DocumentoIn: {"tipo": "certidao_nascimento", "ficheiro_url": "/api/ficheiros/alunos/1/certidao.pdf"},
    MatriculaIn: {"aluno_id": 1, "turma_id": 1, "tipo": "renovacao", "observacoes": "Transita da 2.ª A"},
    MudancaTurmaIn: {"turma_id": 2, "motivo": "Pedido do encarregado (horário da tarde)"},
    ServicoIn: {"codigo": "TRANSP", "nome": "Transporte escolar", "categoria": "transporte",
                "periodicidade": "mensal", "artigo_primavera": "SRV-TRANSP"},
    PrecoIn: {"servico_id": 1, "valor": 45000, "classe_id": 3},
    PlanoIn: {"designacao": "10 prestações (Set a Jun)", "num_prestacoes": 10, "mes_inicio": 9,
              "dia_vencimento": 10},
    MultaIn: {"servico_id": 1, "dias_tolerancia": 15, "tipo": "percentual", "valor": 10, "maximo": 9000},
    DescontoIn: {"designacao": "Desconto de irmãos", "origem": "irmaos", "tipo": "percentual", "valor": 10,
                 "servico_id": 1},
    MatriculaDescontoIn: {"desconto_id": 1, "valido_ate": "2027-07-15"},
    MetodoIn: {"nome": "Multicaixa Express", "tipo": "multicaixa"},
    PrecoClasseIn: {"classe_id": 4, "valor": 50000},
    GerarIn: {"competencia": "2026-11-01", "valor": 45000, "precos": [{"classe_id": 4, "valor": 50000}],
              "multa_percentual": 10, "dias_tolerancia": 15},
    CobrancaAvulsaIn: {"aluno_id": 1, "servico_id": 5, "competencia": "2026-10-01", "valor_base": 15000,
                       "vencimento": "2026-10-20", "descricao": "Fardamento (2 camisolas)"},
    AlocacaoIn: {"cobranca_id": 36, "valor": 18000},
    PagamentoIn: {"metodo_id": 2, "valor": 18000, "encarregado_id": 1, "cobranca_ids": [36],
                  "data_pagamento": "2026-10-08", "referencia": "TRF-0045821", "estado": "confirmado"},
    LembreteIn: {"canais": ["whatsapp", "sms"], "turma_id": 1, "categoria": "propina"},
    AdesaoIn: {"aluno_id": 1, "servico_id": 2, "modalidade": "padrao", "data_inicio": "2026-10-01"},
    AdesaoUpd: {"estado": "suspensa", "data_fim": "2026-12-31"},
    RefeicaoTipoIn: {"designacao": "Almoço", "hora_inicio": "12:30:00"},
    EmentaIn: {"refeicao_tipo_id": 1, "data": "2026-10-20", "descricao": "Muamba de galinha com funge e salada"},
    RefeicaoRegistoIn: {"refeicao_tipo_id": 1, "data": "2026-10-20", "aluno_ids": [1, 2, 5, 8]},
    ModeloIn: {"codigo": "propina_atraso", "canal": "whatsapp",
               "corpo": "Olá {encarregado}, a propina de {aluno} ({mes}) de {valor} Kz está em atraso.",
               "variaveis": ["encarregado", "aluno", "mes", "valor"]},
    RegraIn: {"nome": "Lembrete 3 dias antes do vencimento", "evento": "antes_vencimento", "dias_offset": 3,
              "canais": ["whatsapp"], "modelo_id": 1, "destino": "encarregado", "activa": True},
    RegraUpd: {"dias_offset": 5, "activa": False},
    MensagemAlunoIn: {"assunto": "Reunião de pais", "corpo": "A reunião de pais é na sexta-feira às 17h.",
                      "canais": ["whatsapp", "email"]},
    ComunicadoIn: {"titulo": "Feriado de 11 de Novembro", "corpo": "Não haverá aulas no dia 11 de Novembro.",
                   "alvo": "turma", "turma_ids": [1, 2], "canais": ["app", "whatsapp"]},
    PreferenciaIn: {"canal": "sms", "activo": False},
    TipoAvaliacaoIn: {"designacao": "Prova trimestral", "peso": 2},
    AvaliacaoIn: {"turma_id": 1, "disciplina_id": 1, "periodo_id": 1, "tipo_id": 1, "data": "2026-11-12",
                  "descricao": "Prova do 1.º trimestre"},
    NotaItem: {"aluno_id": 6, "valor": 14.5},
    NotasIn: {"notas": [{"aluno_id": 6, "valor": 14.5}, {"aluno_id": 11, "valor": 9},
                        {"aluno_id": 16, "valor": None, "observacao": "Faltou à prova"}]},
    PresencaItem: {"aluno_id": 6, "estado": "presente"},
    AssiduidadeIn: {"turma_id": 1, "data": "2026-10-20",
                    "registos": [{"aluno_id": 6, "estado": "presente"}, {"aluno_id": 11, "estado": "atraso"},
                                 {"aluno_id": 16, "estado": "falta_justificada", "justificacao": "Consulta médica"}]},
    TipoOcorrenciaIn: {"designacao": "Comportamento exemplar", "gravidade": "positiva"},
    OcorrenciaIn: {"aluno_id": 1, "tipo_id": 2, "data": "2026-10-20",
                   "descricao": "Conflito com um colega no intervalo.", "accao_tomada": "Conversa com a coordenação",
                   "notificar_ee": True},
    OcorrenciaUpd: {"estado": "fechada", "accao_tomada": "Reunião com o encarregado realizada"},
    RegistoDiarioIn: {"turma_id": 1, "aluno_id": 6, "data": "2026-10-20", "comportamento": "bom",
                      "nota_texto": "Participou muito bem na aula de leitura."},
    EstadoIn: {"estado": "tratado"},
    PerguntaIn: {"pergunta": "Quantos alunos da 3.ª A têm propinas em atraso?"},
    IntegracaoIn: {"versao": "V10", "url_base": "https://primavera.clacs.ao/api", "credenciais_ref": "PRIMAVERA",
                   "activo": True},
    RelatorioIn: {"nome": "Devedores (segunda-feira)", "tipo": "devedores", "filtros": {"dias_min": 30},
                  "formato": "xlsx", "agenda_cron": "0 8 * * 1", "destinatarios": ["direccao@clacs.ao"]},
}

def com_exemplo(exemplo: dict):
    """Decorador para esquemas de entrada definidos nos routers: regista o pedido de exemplo."""
    def aplicar(esquema: type[Entrada]) -> type[Entrada]:
        EXEMPLOS[esquema] = exemplo
        esquema.model_config["json_schema_extra"] = {"examples": [exemplo]}
        esquema.model_rebuild(force=True)
        return esquema
    return aplicar


for _esquema, _exemplo in list(EXEMPLOS.items()):
    com_exemplo(_exemplo)(_esquema)
