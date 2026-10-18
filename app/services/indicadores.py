"""Indicadores (KPIs) do painel. A tabela `indicador` escolhe quais aparecem, com que nome e para que perfil;
o cálculo de cada código está aqui."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import ano_activo, primeiro_dia, soma_meses
from app.models import (
    Aluno, Assiduidade, Indicador, IntegracaoDivergencia, Matricula, Mensagem, Ocorrencia, Pagamento,
)
from app.services import financeiro as fin


@dataclass
class Definicao:
    nome: str
    formato: str            # kz | pct | num
    fonte: str              # vista ou tabela de origem (vista_origem)
    destino: str | None     # ecrã do frontend para o detalhe
    calcular: Callable[[Session, int], float]


def _recebido_mes(db: Session, est: int) -> float:
    mes = primeiro_dia(date.today())
    v = db.scalar(select(func.coalesce(func.sum(Pagamento.valor), 0)).where(
        Pagamento.estabelecimento_id == est, Pagamento.estado == "confirmado",
        Pagamento.data_pagamento >= mes, Pagamento.data_pagamento < soma_meses(mes, 1)))
    return float(v)


def _resumo(db: Session, est: int) -> dict:
    return fin.resumo(fin.cobrancas_detalhe(db, est, ate_competencia=primeiro_dia(date.today())))


def _matriculados(db: Session, est: int) -> float:
    ano = ano_activo(db, est, obrigatorio=False)
    return db.scalar(select(func.count()).select_from(Matricula).where(
        Matricula.estabelecimento_id == est, Matricula.ano_id == (ano.id if ano else -1), Matricula.estado == "activa")) or 0


def _divida_refeitorio(db: Session, est: int) -> float:
    return sum(x["valor_em_aberto"] for x in fin.cobrancas_detalhe(db, est, estado="atraso")
               if x["categoria"] in ("alimentacao", "refeitorio"))


def _faltas_30(db: Session, est: int) -> float:
    return db.scalar(select(func.count()).select_from(Assiduidade).join(Aluno, Aluno.id == Assiduidade.aluno_id).where(
        Aluno.estabelecimento_id == est, Assiduidade.estado == "falta_injustificada",
        Assiduidade.data >= date.today() - timedelta(days=30))) or 0


def _ocorrencias(db: Session, est: int) -> float:
    return db.scalar(select(func.count()).select_from(Ocorrencia).join(Aluno, Aluno.id == Ocorrencia.aluno_id).where(
        Aluno.estabelecimento_id == est, Ocorrencia.estado != "fechada")) or 0


def _divergencias(db: Session, est: int) -> float:
    return db.scalar(select(func.count()).select_from(IntegracaoDivergencia).where(
        IntegracaoDivergencia.estabelecimento_id == est, IntegracaoDivergencia.estado == "aberta")) or 0


def _mensagens_falhadas(db: Session, est: int) -> float:
    return db.scalar(select(func.count()).select_from(Mensagem).where(
        Mensagem.estabelecimento_id == est, Mensagem.estado == "falhada")) or 0


CATALOGO: dict[str, Definicao] = {
    "recebido_mes": Definicao("Recebido este mês", "kz", "pagamento", "/propinas?estado=pago", _recebido_mes),
    "em_atraso": Definicao("Em atraso", "kz", "vw_cobranca_detalhe", "/propinas?estado=atraso",
                           lambda db, est: _resumo(db, est)["em_atraso"]),
    "alunos_incumprimento": Definicao("Alunos em incumprimento", "num", "vw_devedores", "/propinas?estado=atraso",
                                      lambda db, est: _resumo(db, est)["alunos_em_incumprimento"]),
    "taxa_cobranca": Definicao("Taxa de cobrança", "pct", "vw_resumo_turma", "/propinas",
                               lambda db, est: _resumo(db, est)["taxa_cobranca"]),
    "alunos_matriculados": Definicao("Alunos matriculados", "num", "matricula", "/cadastro/alunos", _matriculados),
    "divida_refeitorio": Definicao("Refeitório em atraso", "kz", "vw_cobranca_detalhe", "/refeitorio", _divida_refeitorio),
    "faltas_30_dias": Definicao("Faltas injustificadas (30 dias)", "num", "assiduidade", None, _faltas_30),
    "ocorrencias_abertas": Definicao("Ocorrências abertas", "num", "ocorrencia", None, _ocorrencias),
    "divergencias_primavera": Definicao("Divergências Primavera", "num", "integracao_divergencia", "/primavera",
                                        _divergencias),
    "mensagens_falhadas": Definicao("Mensagens falhadas", "num", "mensagem", "/comunicacao", _mensagens_falhadas),
}
PADRAO = ("recebido_mes", "em_atraso", "alunos_incumprimento", "taxa_cobranca")
PERFIS_TOPO = {"admin", "direccao"}


def garantir_padrao(db: Session, est: int) -> None:
    """Cria os 4 indicadores do protótipo para o estabelecimento, se ainda não tiver nenhum."""
    if db.scalar(select(Indicador.id).where(Indicador.estabelecimento_id == est).limit(1)):
        return
    for codigo in PADRAO:
        d = CATALOGO[codigo]
        db.add(Indicador(estabelecimento_id=est, codigo=codigo, nome=d.nome, vista_origem=d.fonte, perfil_minimo="direccao"))
    db.flush()


def visiveis(db: Session, est: int, perfis: set[str]) -> list[dict]:
    """Indicadores do estabelecimento (e os globais) que o utilizador pode ver, já calculados."""
    linhas = db.scalars(select(Indicador).where((Indicador.estabelecimento_id == est) | Indicador.estabelecimento_id.is_(None))
                        .order_by(Indicador.id)).all()
    res = []
    for ind in linhas:
        d = CATALOGO.get(ind.codigo)
        if not d or not (perfis & PERFIS_TOPO or ind.perfil_minimo in perfis):
            continue
        res.append({"id": ind.id, "codigo": ind.codigo, "nome": ind.nome, "formato": d.formato,
                    "valor": round(float(d.calcular(db, est)), 1), "destino": d.destino,
                    "perfil_minimo": ind.perfil_minimo, "global": ind.estabelecimento_id is None})
    return res
