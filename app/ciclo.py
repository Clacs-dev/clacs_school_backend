"""Ciclo académico em que se trabalha (multi-tenant por ano lectivo).

Como a empresa no Primavera ou no CLACS GeoModel: o utilizador escolhe o ciclo no topo da aplicação e o frontend
envia-o em cada pedido no cabeçalho ``X-Ano-Lectivo``. Enquanto houver um ciclo escolhido, todas as consultas
ORM são filtradas para esse ciclo, sem cada rota ter de o fazer:

- pelo ano: matrículas, turmas, candidaturas e orçamentos;
- pela matrícula ou pela turma: cobranças, adesões, resultados, avaliações, faltas, horários, registo diário;
- pelas datas do ciclo: pagamentos, ocorrências, pedidos, mensagens, comunicados, despesas, eventos, refeições,
  transporte, saúde, alertas e documentos fiscais.

Os dados de base (alunos, encarregados, funcionários, classes, disciplinas, salas, serviços, utilizadores…) são
partilhados entre ciclos e não são filtrados. Sem cabeçalho (portal das famílias, agendador, testes) nada muda.
Um ciclo encerrado fica só de consulta, excepto a cobrança das suas dívidas (ver ``app.main``).
"""
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from functools import wraps

from sqlalchemy import and_, event, func, or_, select
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app import models as m


@dataclass(frozen=True)
class Ciclo:
    id: int
    estabelecimento_id: int
    designacao: str
    inicio: date
    fim: date
    estado: str

    @property
    def inicio_dt(self) -> datetime:
        return datetime.combine(self.inicio, time.min)

    @property
    def fim_dt(self) -> datetime:
        """Primeiro instante depois do último dia do ciclo (para colunas com hora)."""
        return datetime.combine(self.fim + timedelta(days=1), time.min)


CICLO: ContextVar[Ciclo | None] = ContextVar("ciclo", default=None)


def ciclo_actual() -> Ciclo | None:
    return CICLO.get()


@contextmanager
def todos_os_ciclos() -> Iterator[None]:
    """Desliga o filtro do ciclo (rotas que trabalham explicitamente com outro ano: fecho, transição…)."""
    marca = CICLO.set(None)
    try:
        yield
    finally:
        CICLO.reset(marca)


def livre_de_ciclo(funcao):
    """Decorador para rotas que trabalham com um ano indicado no pedido, que pode não ser o ciclo escolhido."""
    @wraps(funcao)
    def rota(*args, **kwargs):
        with todos_os_ciclos():
            return funcao(*args, **kwargs)
    return rota


def carregar(db: Session, ano_id: int) -> Ciclo | None:
    ano = db.get(m.AnoLectivo, ano_id)
    if ano is None:
        return None
    return Ciclo(ano.id, ano.estabelecimento_id, ano.designacao, ano.data_inicio, ano.data_fim, ano.estado)


def _opcoes(c: Ciclo) -> list:
    aid, ini, fim, ini_dt, fim_dt = c.id, c.inicio, c.fim, c.inicio_dt, c.fim_dt
    mats = select(m.Matricula.id).where(m.Matricula.ano_id == aid).scalar_subquery()
    turmas = select(m.Turma.id).where(m.Turma.ano_id == aid).scalar_subquery()
    periodos = select(m.PeriodoLectivo.id).where(m.PeriodoLectivo.ano_id == aid).scalar_subquery()

    # Expressões directas (não lambdas): os valores do ciclo e as subconsultas entram como parâmetros normais.
    def crit(entidade, expr):
        return with_loader_criteria(entidade, expr, include_aliases=True)

    return [
        # pelo ano
        crit(m.Matricula, m.Matricula.ano_id == aid),
        crit(m.Turma, m.Turma.ano_id == aid),
        crit(m.Orcamento, m.Orcamento.ano_id == aid),
        crit(m.Candidatura, or_(m.Candidatura.ano_id == aid, m.Candidatura.ano_id.is_(None))),
        # pela matrícula
        crit(m.Cobranca, m.Cobranca.matricula_id.in_(mats)),
        crit(m.AdesaoServico, m.AdesaoServico.matricula_id.in_(mats)),
        crit(m.ResultadoFinal, m.ResultadoFinal.matricula_id.in_(mats)),
        crit(m.ResultadoPeriodo, m.ResultadoPeriodo.matricula_id.in_(mats)),
        crit(m.MatriculaDesconto, m.MatriculaDesconto.matricula_id.in_(mats)),
        # pela turma
        crit(m.Avaliacao, m.Avaliacao.turma_id.in_(turmas)),
        crit(m.Horario, m.Horario.turma_id.in_(turmas)),
        crit(m.RegistoDiario, m.RegistoDiario.turma_id.in_(turmas)),
        crit(m.AtribuicaoDocente, m.AtribuicaoDocente.turma_id.in_(turmas)),
        crit(m.AvaliacaoProfessor, m.AvaliacaoProfessor.periodo_id.in_(periodos)),
        # pelas datas do ciclo
        crit(m.Assiduidade, m.Assiduidade.data.between(ini, fim)),
        crit(m.Aula, m.Aula.data.between(ini, fim)),
        crit(m.Substituicao, m.Substituicao.data.between(ini, fim)),
        # Pagamentos com data no ciclo e também os que saldam cobranças do ciclo (ex.: dívida de um ciclo
        # encerrado paga mais tarde, que fica a constar nos dois).
        crit(m.Pagamento, or_(m.Pagamento.data_pagamento.between(ini, fim), m.Pagamento.id.in_(
            select(m.PagamentoAlocacao.pagamento_id).join(m.Cobranca, m.Cobranca.id == m.PagamentoAlocacao.cobranca_id)
            .where(m.Cobranca.matricula_id.in_(mats))))),
        crit(m.Ocorrencia, m.Ocorrencia.data.between(ini, fim)),
        crit(m.OcorrenciaSaude, and_(m.OcorrenciaSaude.data_hora >= ini_dt, m.OcorrenciaSaude.data_hora < fim_dt)),
        crit(m.Pedido, and_(m.Pedido.criado_em >= ini_dt, m.Pedido.criado_em < fim_dt)),
        crit(m.Comunicado, and_(m.Comunicado.criado_em >= ini_dt, m.Comunicado.criado_em < fim_dt)),
        crit(m.Mensagem, or_(
            and_(func.coalesce(m.Mensagem.enviada_em, m.Mensagem.agendada_para) >= ini_dt, func.coalesce(m.Mensagem.enviada_em, m.Mensagem.agendada_para) < fim_dt),
            and_(m.Mensagem.enviada_em.is_(None), m.Mensagem.agendada_para.is_(None)))),
        crit(m.Despesa, m.Despesa.data.between(ini, fim)),
        crit(m.EventoCalendario, and_(m.EventoCalendario.data_inicio <= fim, func.coalesce(m.EventoCalendario.data_fim, m.EventoCalendario.data_inicio) >= ini)),
        crit(m.RefeicaoRegisto, m.RefeicaoRegisto.data.between(ini, fim)),
        crit(m.Ementa, m.Ementa.data.between(ini, fim)),
        crit(m.AlunoTransporte, and_(m.AlunoTransporte.data_inicio <= fim, or_(m.AlunoTransporte.data_fim.is_(None), m.AlunoTransporte.data_fim >= ini))),
        crit(m.MedicacaoInstrucao, and_(m.MedicacaoInstrucao.data_inicio <= fim, or_(m.MedicacaoInstrucao.data_fim.is_(None), m.MedicacaoInstrucao.data_fim >= ini))),
        crit(m.AcordoPagamento, and_(m.AcordoPagamento.criado_em >= ini_dt, m.AcordoPagamento.criado_em < fim_dt)),
        crit(m.Alerta, and_(m.Alerta.criado_em >= ini_dt, m.Alerta.criado_em < fim_dt)),
        crit(m.DocumentoFiscal, m.DocumentoFiscal.data_emissao.between(ini, fim)),
    ]


@event.listens_for(Session, "do_orm_execute")
def _filtrar_pelo_ciclo(estado: ORMExecuteState) -> None:
    c = CICLO.get()
    if c is None or not estado.is_select or estado.is_column_load or estado.is_relationship_load:
        return
    if estado.execution_options.get("todos_os_ciclos"):
        return
    estado.statement = estado.statement.options(*_opcoes(c))
