"""Modelos das respostas da API (o que cada endpoint devolve), para o /api/docs e para gerar tipos no frontend.

Os modelos das tabelas são gerados a partir das colunas SQLAlchemy (app/models.py), por isso não divergem do
esquema. Os compostos acrescentam os campos calculados que cada endpoint junta.

Nos testes (CLACS_TESTES=1) os modelos recusam campos que não estejam aqui declarados: um campo novo numa resposta
obriga a documentá-lo. Em produção, campos a mais são simplesmente omitidos.
"""
import os
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model
from sqlalchemy import JSON, Boolean, Date, DateTime, Integer, Numeric, String, Text, Time

from app import models as m

ESTRITO = os.environ.get("CLACS_TESTES") == "1"


class Saida(BaseModel):
    model_config = ConfigDict(extra="forbid" if ESTRITO else "ignore", populate_by_name=True)


# ---------------------------------------------------------------- modelos gerados das tabelas
LISTAS_DE_TEXTO = {("regra_automacao", "canais"), ("relatorio_agendado", "destinatarios")}


def _tipo(tabela: str, col) -> Any:
    if (tabela, col.key) in LISTAS_DE_TEXTO:
        return list[str]
    t = col.type
    for classe, py in ((Boolean, bool), (DateTime, datetime), (Date, date), (Time, time), (Numeric, float),
                       (Integer, int), (String, str), (Text, str)):
        if isinstance(t, classe):
            return py
    if isinstance(t, JSON):
        return Any
    try:
        return t.python_type
    except NotImplementedError:
        return Any


def _de_tabela(model, nome: str, excluir: tuple[str, ...] = ()) -> type[Saida]:
    """Modelo com uma propriedade por coluna. Colunas que podem ser nulas, ou que a base preenche sozinha
    (valores por omissão, datas de criação, colunas calculadas), ficam opcionais."""
    campos = {}
    for col in model.__table__.columns:
        if col.key in excluir:
            continue
        tipo = _tipo(model.__tablename__, col)
        preenchida_pela_base = col.server_default is not None or col.computed is not None or col.onupdate is not None
        if col.nullable or preenchida_pela_base:
            campos[col.key] = (tipo | None, None)
        else:
            campos[col.key] = (tipo, ...)
    return create_model(nome, __base__=Saida, **campos)


Estabelecimento = _de_tabela(m.Estabelecimento, "Estabelecimento")
Parametro = _de_tabela(m.Parametro, "Parametro")
AnoLectivo = _de_tabela(m.AnoLectivo, "AnoLectivo")
PeriodoLectivo = _de_tabela(m.PeriodoLectivo, "PeriodoLectivo")
NivelEnsino = _de_tabela(m.NivelEnsino, "NivelEnsino")
Classe = _de_tabela(m.Classe, "Classe")
ClasseDisciplina = _de_tabela(m.ClasseDisciplina, "ClasseDisciplina")
Sala = _de_tabela(m.Sala, "Sala")
Disciplina = _de_tabela(m.Disciplina, "Disciplina")
Funcionario = _de_tabela(m.Funcionario, "Funcionario")
Turma = _de_tabela(m.Turma, "Turma")
AtribuicaoDocente = _de_tabela(m.AtribuicaoDocente, "AtribuicaoDocente")
Aluno = _de_tabela(m.Aluno, "Aluno")
Encarregado = _de_tabela(m.Encarregado, "Encarregado")
AlunoEncarregado = _de_tabela(m.AlunoEncarregado, "AlunoEncarregado")
DocumentoAluno = _de_tabela(m.DocumentoAluno, "DocumentoAluno")
Matricula = _de_tabela(m.Matricula, "Matricula")
MatriculaHistorico = _de_tabela(m.MatriculaHistorico, "MatriculaHistorico")
Perfil = _de_tabela(m.Perfil, "Perfil")
AuditoriaLog = _de_tabela(m.AuditoriaLog, "AuditoriaLog")
Servico = _de_tabela(m.Servico, "Servico")
TabelaPreco = _de_tabela(m.TabelaPreco, "TabelaPreco")
PlanoPagamento = _de_tabela(m.PlanoPagamento, "PlanoPagamento")
MultaRegra = _de_tabela(m.MultaRegra, "MultaRegra")
Desconto = _de_tabela(m.Desconto, "Desconto")
MatriculaDesconto = _de_tabela(m.MatriculaDesconto, "MatriculaDesconto")
MetodoPagamento = _de_tabela(m.MetodoPagamento, "MetodoPagamento")
DocumentoFiscal = _de_tabela(m.DocumentoFiscal, "DocumentoFiscal")
Cobranca = _de_tabela(m.Cobranca, "Cobranca")
Pagamento = _de_tabela(m.Pagamento, "Pagamento")
AdesaoServico = _de_tabela(m.AdesaoServico, "AdesaoServico")
RefeicaoTipo = _de_tabela(m.RefeicaoTipo, "RefeicaoTipo")
Ementa = _de_tabela(m.Ementa, "Ementa")
RefeicaoRegisto = _de_tabela(m.RefeicaoRegisto, "RefeicaoRegisto")
IntegracaoConfig = _de_tabela(m.IntegracaoConfig, "IntegracaoConfig")
IntegracaoSyncLog = _de_tabela(m.IntegracaoSyncLog, "IntegracaoSyncLog")
IntegracaoDivergencia = _de_tabela(m.IntegracaoDivergencia, "IntegracaoDivergencia")
ModeloMensagem = _de_tabela(m.ModeloMensagem, "ModeloMensagem")
RegraAutomacao = _de_tabela(m.RegraAutomacao, "RegraAutomacao")
Comunicado = _de_tabela(m.Comunicado, "Comunicado")
Mensagem = _de_tabela(m.Mensagem, "Mensagem")
PreferenciaContacto = _de_tabela(m.PreferenciaContacto, "PreferenciaContacto")
TipoAvaliacao = _de_tabela(m.TipoAvaliacao, "TipoAvaliacao")
Avaliacao = _de_tabela(m.Avaliacao, "Avaliacao")
ResultadoFinal = _de_tabela(m.ResultadoFinal, "ResultadoFinal")
Assiduidade = _de_tabela(m.Assiduidade, "Assiduidade")
TipoOcorrencia = _de_tabela(m.TipoOcorrencia, "TipoOcorrencia")
Ocorrencia = _de_tabela(m.Ocorrencia, "Ocorrencia")
RegistoDiario = _de_tabela(m.RegistoDiario, "RegistoDiario")
Alerta = _de_tabela(m.Alerta, "Alerta")
Pedido = _de_tabela(m.Pedido, "Pedido")


class PedidoComNomes(Pedido):
    aluno: str | None = Field(description="Educando (na vaga, o nome pedido)")
    encarregado: str
Indicador = _de_tabela(m.Indicador, "Indicador")
RelatorioAgendado = _de_tabela(m.RelatorioAgendado, "RelatorioAgendado")
RelatorioExecucao = _de_tabela(m.RelatorioExecucao, "RelatorioExecucao")
ImportacaoLote = _de_tabela(m.ImportacaoLote, "ImportacaoLote")
ImportacaoErro = _de_tabela(m.ImportacaoErro, "ImportacaoErro")
UtilizadorBase = _de_tabela(m.Utilizador, "UtilizadorBase", excluir=("senha_hash", "doisfa_segredo"))


# ---------------------------------------------------------------- respostas simples
class Ok(Saida):
    estado: str = Field(examples=["ok"])


class Mensagens(Saida):
    mensagens: int


class Lancadas(Saida):
    lancadas: int


class Resultados(Saida):
    resultados: int


class Registos(Saida):
    registos: int


class Novos(Saida):
    novos: int


# ---------------------------------------------------------------- autenticação
class Token(Saida):
    access_token: str
    token_type: Literal["bearer"]


class Utilizador(UtilizadorBase):
    perfis: list[str] = Field(description="Códigos dos perfis neste estabelecimento, ex. ['admin']")


class EstabelecimentoCurto(Saida):
    id: int
    nome: str


class Eu(Utilizador):
    estabelecimento: EstabelecimentoCurto
    permissoes: list[str] = Field(description="'modulo:accao', ex. 'propinas:ver'. O perfil admin tem acesso a tudo.")


class DoisPassos(Saida):
    segredo: str = Field(description="Segredo TOTP em base32 (para escrever à mão na aplicação)")
    uri: str = Field(description="otpauth://… para mostrar como código QR")


class ContaPortal(Saida):
    utilizador_id: int
    email: str


# ---------------------------------------------------------------- académico
class ClasseComNivel(Classe):
    nivel: str


class DisciplinaDoPlano(Disciplina):
    carga_horaria: int | None


class DisciplinaDoAluno(DisciplinaDoPlano):
    professor_id: int | None
    professor: str | None = Field(description="Professor da disciplina na turma do aluno")


class TurmaComOcupacao(Turma):
    classe: str
    sala: str | None
    director_turma: str | None
    alunos: int = Field(description="Matrículas activas")
    vagas: int
    cheia: bool


class AtribuicaoComNomes(AtribuicaoDocente):
    disciplina: str
    professor: str


class TotaisFecho(Saida):
    aprovado: int
    reprovado: int
    excluido_faltas: int
    sem_avaliacao: int


class LinhaFecho(Saida):
    matricula_id: int
    aluno: str
    turma: str
    media_final: float | None
    faltas: int
    situacao: Literal["aprovado", "reprovado", "excluido_faltas"] | None


class ResultadoFecho(Saida):
    ano: str
    nota_minima: float
    faltas_maximas: int | None
    simulado: bool
    totais: TotaisFecho
    linhas: list[LinhaFecho]


class ResultadoComNomes(ResultadoFinal):
    aluno: str
    turma: str


class Ignorada(Saida):
    aluno: str
    motivo: str


class ResultadoTransicao(Saida):
    criadas: int
    ignoradas: list[Ignorada]


# ---------------------------------------------------------------- cadastro
class MediaTrimestre(Saida):
    periodo_id: int
    periodo: str
    media: float | None = Field(description="Média ponderada das notas do período (null = sem notas)")


class AlunoLista(Aluno):
    turma_id: int | None
    turma: str | None
    encarregado: str | None = Field(description="Encarregado principal")
    telefone: str | None
    ano_lectivo: str | None
    necessidade_especial: bool = Field(description="true = mostrar o aluno destacado (tem necessidades especiais)")
    avaliacoes: list[MediaTrimestre] = Field(description="Média de cada trimestre do ano activo")


class EncarregadoDoAluno(Encarregado):
    encarregado_id: int
    parentesco: Literal["mae", "pai", "tutor", "outro"]
    principal: bool | None
    responsavel_financeiro: bool | None
    autorizado_recolha: bool | None


class MatriculaComNomes(Matricula):
    turma: str
    ano_lectivo: str


class AlunoDetalhe(Aluno):
    necessidade_especial: bool
    encarregados: list[EncarregadoDoAluno]
    matriculas: list[MatriculaComNomes]
    documentos: list[DocumentoAluno]
    disciplinas: list["DisciplinaDoAluno"] = Field(description="Plano curricular da classe da turma actual")


class Educando(Saida):
    id: int
    nome: str


class EncarregadoLista(Encarregado):
    parentesco: str | None
    educandos: list[Educando] = Field(description="Ao expandir a linha do encarregado")
    num_educandos: int
    total_pago: float = Field(description="Kz já pagos (cobranças de que é o responsável financeiro)")
    total_em_divida: float = Field(description="Kz em aberto")
    cobrancas_em_atraso: int


class EducandoDetalhe(Educando):
    parentesco: str
    responsavel_financeiro: bool | None


class EncarregadoDetalhe(Encarregado):
    educandos: list[EducandoDetalhe]
    preferencias: dict[str, bool] = Field(description="Canal → activo, ex. {'whatsapp': true}")
    em_divida: float


class MatriculaLista(Matricula):
    aluno: str
    turma: str
    ano_lectivo: str


class MatriculaRegistada(Matricula):
    turma: str
    ano_lectivo: str
    mudanca_de_turma: bool = Field(description="true quando o aluno já estava matriculado e mudou de turma")


class DescontoDaMatricula(MatriculaDesconto):
    desconto: Desconto


class TurmaCurta(Saida):
    id: int
    designacao: str


class EncarregadoContacto(Saida):
    id: int
    nome: str
    telefone: str | None
    whatsapp: str | None
    email: str | None
    parentesco: Literal["mae", "pai", "tutor", "outro"]
    principal: bool | None
    responsavel_financeiro: bool | None


class ServicoAderido(Saida):
    id: int = Field(description="Id da adesão")
    servico: str
    modalidade: str
    estado: Literal["activa", "suspensa", "cancelada"] | None


class Financeiro360(Saida):
    cobrancas: list["LinhaCobranca"]
    em_divida: float
    em_atraso: float
    servicos: list[ServicoAderido]


class Pedagogico360(Saida):
    media: float | None
    faltas: int
    comportamento: Literal["bom", "regular", "atencao"] | None
    ocorrencias_abertas: int


class Aluno360(Saida):
    aluno: Aluno
    turma: TurmaCurta | None
    matricula_id: int | None
    encarregados: list[EncarregadoContacto]
    financeiro: Financeiro360
    pedagogico: Pedagogico360


# ---------------------------------------------------------------- financeiro
class LinhaCobranca(Saida):
    cobranca_id: int
    aluno_id: int
    aluno: str
    turma_id: int
    turma: str
    encarregado_id: int | None
    encarregado: str | None
    telefone: str | None
    servico_id: int
    servico: str
    categoria: str
    competencia: date = Field(description="1.º dia do mês cobrado")
    vencimento: date
    descricao: str | None
    valor_base: float
    valor_desconto: float
    valor_multa: float
    valor_total: float
    valor_pago: float
    valor_em_aberto: float
    estado: Literal["pendente", "parcial", "paga", "anulada"]
    dias_atraso: int


class ResumoCobrancas(Saida):
    registos: int
    facturado: float
    recebido: float
    em_aberto: float
    em_atraso: float
    alunos_em_incumprimento: int
    taxa_cobranca: float = Field(description="Percentagem 0–100")


class ListaCobrancas(Saida):
    resumo: ResumoCobrancas
    linhas: list[LinhaCobranca]


class Devedor(Saida):
    aluno_id: int
    aluno: str
    turma: str
    encarregado_id: int | None
    encarregado: str | None
    telefone: str | None
    cobrancas_em_atraso: int
    total_em_divida: float
    maior_atraso: int = Field(description="Dias")


class TurmaVagas(Saida):
    id: int
    designacao: str
    periodo_dia: str | None
    alunos: int
    capacidade: int
    vagas: int
    cheia: bool


class SugestaoTurma(Saida):
    classe_id: int
    turma_pedida: TurmaVagas | None = Field(description="Só quando se pediu uma turma")
    sugerida: TurmaVagas | None = Field(description="A turma onde matricular; null = nenhuma com vaga")
    mensagem: str


class ResultadoGeracao(Saida):
    competencia: date
    criadas: int
    ja_existentes: int
    servicos_sem_preco: list[str]
    fora_do_plano: bool
    motivo: str | None = Field(None, description="Só quando fora_do_plano é true")
    com_desconto_irmaos: int | None = Field(None, description="Matrículas com desconto de irmãos aplicado")
    multas_aplicadas: int | None = Field(None, description="Cobranças em atraso que receberam ou actualizaram a multa")
    meses: list["MesGerado"] | None = Field(None, description="Só com todo_o_ano: cobranças criadas em cada mês")


class MesGerado(Saida):
    competencia: date
    criadas: int


class PlanoDoAno(Saida):
    prestacoes: int
    mes_inicio: int
    dia_vencimento: int
    meses: list[int] = Field(description="Meses (1–12) com prestação, por ordem")


class PrecoDaClasse(Saida):
    classe_id: int
    classe: str
    valor: float | None = Field(description="null = sem preço: o mapa não pode ser gerado")


class MultaDaPropina(Saida):
    tipo: str
    valor: float = Field(description="Percentagem (tipo percentual) ou Kz (tipo fixo)")
    dias_tolerancia: int
    maximo: float | None


class PropinaConfigurada(Saida):
    servico_id: int
    nome: str
    precos: list[PrecoDaClasse] = Field(description="Uma linha por classe com alunos matriculados no ano activo")
    multa: MultaDaPropina | None


class ConfiguracaoCobranca(Saida):
    ano: str | None
    propinas: list[PropinaConfigurada]
    dia_vencimento: int
    plano: PlanoDoAno | None
    alertas_dias: list[int] = Field(description="Dias antes do vencimento com lembrete por WhatsApp (0 = no dia)")
    pendentes: list[str] = Field(description="O que falta configurar antes de gerar")
    pronto: bool


class MultasAplicadas(Saida):
    cobrancas_actualizadas: int


class LembretesEnviados(Saida):
    mensagens: int
    encarregados: int


class ServicoComPreco(Servico):
    preco: float | None = Field(description="Preço geral (todas as classes) no ano activo")


class PrecoComNomes(TabelaPreco):
    servico: str
    classe: str = Field(description="Designação da classe, ou 'Todas'")


class Alocacao(Saida):
    cobranca_id: int
    valor: float


class PagamentoComAlocacoes(Pagamento):
    alocacoes: list[Alocacao]


class PagamentoLista(PagamentoComAlocacoes):
    encarregado: str | None
    metodo: str


# ---------------------------------------------------------------- refeitório
class TotaisRefeitorio(Saida):
    alimentacao: int
    so_utilizacao: int
    nenhum: int


class LinhaRefeitorio(Saida):
    adesao_id: int
    aluno_id: int
    aluno: str
    turma: str
    servico_id: int
    servico: str
    categoria: Literal["alimentacao", "refeitorio"]
    modalidade: str
    valor: float | None = Field(description="Valor da última mensalidade")
    estado: Literal["pendente", "parcial", "paga", "anulada"] | None
    dias_atraso: int
    cobranca_id: int | None


class ResumoRefeitorio(Saida):
    totais: TotaisRefeitorio
    linhas: list[LinhaRefeitorio]


class AdesaoComNomes(AdesaoServico):
    servico: str
    aluno_id: int


class EmentaComRefeicao(Ementa):
    refeicao: str


class RefeicoesRegistadas(Saida):
    registados: int
    sem_adesao: list[int] = Field(description="Alunos sem adesão ao refeitório (refeição avulsa)")
    refeicoes_cobradas: int


class RegistoComNomes(RefeicaoRegisto):
    aluno: str
    refeicao: str


# ---------------------------------------------------------------- comunicação
class MensagemComEncarregado(Mensagem):
    encarregado: str


class ComunicadoEnviado(Comunicado):
    destinatarios: int


class Despacho(Saida):
    enviadas: int
    falhadas: int


# ---------------------------------------------------------------- pedagógico
class AvaliacaoComNomes(Avaliacao):
    disciplina: str
    tipo: str


class NotaDoAluno(Saida):
    aluno_id: int
    aluno: str
    valor: float | None
    observacao: str | None


class LinhaPauta(Saida):
    matricula_id: int
    aluno_id: int
    aluno: str
    medias: dict[str, float | None] = Field(description="Disciplina → média do período")
    media_geral: float | None


class LinhaPautaEducando(Saida):
    disciplina_id: int
    disciplina: str
    medias: list[float | None] = Field(description="Uma por trimestre, pela ordem de periodos")
    media_final: float | None
    situacao: Literal["positiva", "negativa"] | None


class PautaEducando(Saida):
    aluno_id: int
    aluno: str
    turma: str | None
    periodos: list[str]
    linhas: list[LinhaPautaEducando]
    medias_gerais: list[float | None] = Field(description="Média geral de cada trimestre")
    nota_maxima: float
    nota_minima: float


class Pauta(Saida):
    disciplinas: list[str]
    linhas: list[LinhaPauta]
    nota_maxima: float = Field(description="Escala das notas (10 ou 20)")
    nota_minima: float = Field(description="Média a partir da qual é positiva")


class AssiduidadeComAluno(Assiduidade):
    aluno: str


class OcorrenciaComNomes(Ocorrencia):
    aluno: str
    tipo: str
    gravidade: Literal["positiva", "leve", "moderada", "grave"]


# ---------------------------------------------------------------- painel, alertas, relatórios
class Kpis(Saida):
    recebido_mes: float
    em_atraso: float
    alunos_em_incumprimento: int
    alunos: int
    taxa_cobranca: float
    facturado: float
    recebido: float


class IndicadorComValor(Saida):
    id: int
    codigo: str
    nome: str
    formato: Literal["kz", "pct", "num"]
    valor: float
    destino: str | None = Field(description="Ecrã do frontend com o detalhe")
    perfil_minimo: str
    global_: bool = Field(alias="global", description="true se for um indicador de todos os estabelecimentos")


class ResumoTurma(Saida):
    turma_id: int
    turma: str
    alunos: int
    facturado: float
    recebido: float
    em_atraso: float
    taxa_cobranca: float


class AlertaCalculado(Saida):
    tipo: Literal["risco_incumprimento", "queda_desempenho", "padrao_faltas", "divergencia_primavera", "comportamento"]
    gravidade: Literal["baixa", "media", "alta"]
    aluno_id: int | None
    aluno: str | None
    turma: str | None
    mensagem: str
    dados: dict[str, Any] | None


class Painel(Saida):
    data: date
    ano_lectivo: str | None
    kpis: Kpis
    indicadores: list[IndicadorComValor]
    por_turma: list[ResumoTurma]
    alertas: list[AlertaCalculado]


class IndicadorDoCatalogo(Saida):
    codigo: str
    nome: str
    formato: Literal["kz", "pct", "num"]
    fonte: str


class AlertaComAluno(Alerta):
    aluno: str | None


class RespostaAssistente(Saida):
    resposta: str
    fonte: Literal["ia", "regras"]


class RelatorioCriado(RelatorioAgendado):
    proxima_execucao: datetime | None


class TarefaAgendada(Saida):
    id: str
    nome: str
    proxima: datetime | None


class EstadoAgendador(Saida):
    activo: bool
    tarefas: list[TarefaAgendada]


class AuditoriaComUtilizador(AuditoriaLog):
    utilizador: str | None


# ---------------------------------------------------------------- Primavera
class EstadoPrimavera(Saida):
    config: IntegracaoConfig
    ultima_sincronizacao: IntegracaoSyncLog | None
    documentos_hoje: int
    divergencias_abertas: int
    simulado: bool = Field(description="true enquanto a integração não estiver activa")


# ---------------------------------------------------------------- importação e ficheiros
class LoteImportado(ImportacaoLote):
    erros: int


class Foto(Saida):
    foto_url: str


class Comprovativo(Saida):
    comprovativo_url: str


# ---------------------------------------------------------------- portal do encarregado
class EncarregadoPortal(Saida):
    id: int
    nome_completo: str
    telefone: str | None
    whatsapp: str | None
    email: str | None
    nif: str | None


class EducandoResumo(Saida):
    id: int
    nome: str
    numero_processo: str
    em_divida: float
    em_atraso: float
    turma: str | None


class PortalEu(Saida):
    encarregado: EncarregadoPortal
    educandos: list[EducandoResumo]
    preferencias: dict[str, bool]


class AlunoPortal(Saida):
    id: int
    nome: str
    numero_processo: str
    foto_url: str | None


class EducandoPortal(Saida):
    aluno: AlunoPortal
    turma: str | None
    cobrancas: list[LinhaCobranca]
    em_divida: float
    media: float | None
    faltas: int
    comportamento: Literal["bom", "regular", "atencao"] | None


class NotaPortal(Saida):
    data: date
    periodo: str
    disciplina: str
    tipo: str
    valor: float | None
    observacao: str | None


class CobrancasPortal(Saida):
    linhas: list[LinhaCobranca]
    em_divida: float


class ReferenciaGerada(Saida):
    pagamento_id: int
    entidade: str
    referencia: str = Field(description="9 dígitos, para pagar no Multicaixa")
    valor: float
    expira: date


class ComprovativoEnviado(Saida):
    pagamento_id: int
    estado: str
    valor: float
    mensagem: str


class PagamentoPortal(Saida):
    id: int
    data: date
    valor: float
    metodo: str
    estado: Literal["pendente", "confirmado", "rejeitado"]
    referencia: str | None


class MensagemPortal(Saida):
    id: int
    canal: Literal["whatsapp", "sms", "email", "app"]
    assunto: str | None
    corpo: str
    estado: str
    data: datetime | None


class PreferenciaPortal(Saida):
    canal: str
    activo: bool


class DispositivoRegistado(Saida):
    registado: bool


class WebhookResultado(Saida):
    estado: Literal["confirmado", "ja_confirmado"]
    pagamento_id: int | None = None


# ---------------------------------------------------------------- respostas que são ficheiros
def ficheiro(tipo: str, descricao: str) -> dict:
    """Para `responses=` de endpoints que devolvem um ficheiro em vez de JSON."""
    return {200: {"content": {tipo: {"schema": {"type": "string", "format": "binary"}}}, "description": descricao}}


PDF = ficheiro("application/pdf", "Documento PDF")
EXCEL = {200: {"content": {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {"schema": {"type": "string", "format": "binary"}},
    "text/csv": {"schema": {"type": "string", "format": "binary"}}}, "description": "Ficheiro Excel (xlsx) ou CSV"}}
QUALQUER_FICHEIRO = ficheiro("application/octet-stream", "O ficheiro (PDF, imagem, Excel ou CSV)")


Financeiro360.model_rebuild()
Aluno360.model_rebuild()
ResultadoGeracao.model_rebuild()
AlunoDetalhe.model_rebuild()
