"""Alertas inteligentes: risco de incumprimento, queda de desempenho, faltas, comportamento, Primavera."""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import ano_activo
from app.models import (
    Alerta, Aluno, Assiduidade, Avaliacao, IntegracaoDivergencia, Matricula, Nota, Ocorrencia,
    RegistoDiario, TipoOcorrencia, Turma,
)
from app.services.financeiro import cobrancas_detalhe


def _turmas(db: Session, est: int) -> dict[int, str]:
    ano = ano_activo(db, est, obrigatorio=False)
    if not ano:
        return {}
    return dict(db.execute(select(Matricula.aluno_id, Turma.designacao).join(Turma, Turma.id == Matricula.turma_id)
                           .where(Matricula.ano_id == ano.id, Matricula.estado == "activa")).tuples().all())


def medias_por_avaliacao(db: Session, aluno_id: int) -> list[tuple[date, float]]:
    """Média do aluno em cada data de avaliação, por ordem cronológica."""
    rows = db.execute(select(Avaliacao.data, func.avg(Nota.valor))
                      .join(Nota, Nota.avaliacao_id == Avaliacao.id)
                      .where(Nota.aluno_id == aluno_id, Nota.valor.is_not(None))
                      .group_by(Avaliacao.data).order_by(Avaliacao.data)).all()
    return [(d, float(m)) for d, m in rows]


def calcular_alertas(db: Session, est: int, hoje: date | None = None) -> list[dict]:
    hoje = hoje or date.today()
    turma = _turmas(db, est)
    nomes = dict(db.execute(select(Aluno.id, Aluno.nome_completo).where(Aluno.estabelecimento_id == est)).tuples().all())
    alertas: list[dict] = []

    # Risco de incumprimento: 2 ou mais meses diferentes em atraso.
    meses = defaultdict(set)
    for x in cobrancas_detalhe(db, est, estado="atraso", hoje=hoje):
        meses[x["aluno_id"]].add(x["competencia"])
    for aid, ms in meses.items():
        if len(ms) >= 2:
            alertas.append({"tipo": "risco_incumprimento", "gravidade": "alta" if len(ms) >= 3 else "media",
                            "aluno_id": aid, "aluno": nomes.get(aid), "turma": turma.get(aid),
                            "mensagem": f"{nomes.get(aid)} ({turma.get(aid)}) tem {len(ms)} meses com pagamentos em atraso.",
                            "dados": {"meses_em_atraso": len(ms)}})

    # Queda de desempenho: média da última avaliação 2+ valores abaixo da anterior.
    for aid in turma:
        medias = medias_por_avaliacao(db, aid)
        if len(medias) >= 2:
            queda = medias[-2][1] - medias[-1][1]
            if queda >= 2:
                alertas.append({"tipo": "queda_desempenho", "gravidade": "media", "aluno_id": aid,
                                "aluno": nomes.get(aid), "turma": turma.get(aid),
                                "mensagem": f"{nomes.get(aid)} desceu {queda:.0f} valores na média.",
                                "dados": {"anterior": round(medias[-2][1], 1), "actual": round(medias[-1][1], 1)}})

    # Padrão de faltas: 5 ou mais faltas injustificadas nos últimos 30 dias.
    for aid, n in db.execute(select(Assiduidade.aluno_id, func.count())
                             .join(Aluno, Aluno.id == Assiduidade.aluno_id)
                             .where(Aluno.estabelecimento_id == est,
                                    Assiduidade.estado == "falta_injustificada",
                                    Assiduidade.data >= hoje - timedelta(days=30))
                             .group_by(Assiduidade.aluno_id)):
        if n >= 5:
            alertas.append({"tipo": "padrao_faltas", "gravidade": "media", "aluno_id": aid,
                            "aluno": nomes.get(aid), "turma": turma.get(aid),
                            "mensagem": f"{nomes.get(aid)} tem {n} faltas injustificadas nos últimos 30 dias.",
                            "dados": {"faltas": n}})

    # Comportamento: ocorrência grave em aberto.
    for aid, desc in db.execute(select(Ocorrencia.aluno_id, TipoOcorrencia.designacao)
                                .join(TipoOcorrencia, TipoOcorrencia.id == Ocorrencia.tipo_id)
                                .where(TipoOcorrencia.estabelecimento_id == est, TipoOcorrencia.gravidade == "grave",
                                       Ocorrencia.estado != "fechada")):
        alertas.append({"tipo": "comportamento", "gravidade": "alta", "aluno_id": aid, "aluno": nomes.get(aid),
                        "turma": turma.get(aid), "mensagem": f"{nomes.get(aid)}: ocorrência grave em aberto ({desc}).",
                        "dados": None})

    n = db.scalar(select(func.count()).select_from(IntegracaoDivergencia)
                  .where(IntegracaoDivergencia.estabelecimento_id == est, IntegracaoDivergencia.estado == "aberta"))
    if n:
        alertas.append({"tipo": "divergencia_primavera", "gravidade": "media", "aluno_id": None, "aluno": None,
                        "turma": None, "mensagem": f"{n} divergência(s) por resolver.", "dados": {"abertas": n}})
    return alertas


def persistir_alertas(db: Session, est: int, hoje: date | None = None) -> int:
    """Grava os alertas calculados que ainda não existam em aberto (mesmo tipo e aluno)."""
    abertos = set(db.execute(select(Alerta.tipo, Alerta.aluno_id).where(
        Alerta.estabelecimento_id == est, Alerta.estado.in_(("novo", "visto")))).tuples())
    novos = 0
    for a in calcular_alertas(db, est, hoje):
        if (a["tipo"], a["aluno_id"]) in abertos:
            continue
        db.add(Alerta(estabelecimento_id=est, tipo=a["tipo"], aluno_id=a["aluno_id"], gravidade=a["gravidade"],
                      mensagem=a["mensagem"], dados=a["dados"]))
        novos += 1
    db.commit()
    return novos


def comportamento_actual(db: Session, aluno_id: int) -> str | None:
    return db.scalar(select(RegistoDiario.comportamento).where(RegistoDiario.aluno_id == aluno_id,
                                                               RegistoDiario.comportamento.is_not(None))
                     .order_by(RegistoDiario.data.desc(), RegistoDiario.id.desc()).limit(1))
