"""Painel da Direcção: a escola de relance (alunos, aproveitamento, faltas, disciplina e pedidos), além das finanças.

Tudo no ano lectivo activo. A média de cada aluno é a média ponderada (pelo peso de cada tipo de avaliação) de
todas as notas lançadas na turma; a média da turma e a da escola são a média dessas médias.
"""
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.common import ano_activo
from app.models import (
    Aluno, Assiduidade, Avaliacao, AvaliacaoProfessor, Matricula, Nota, Ocorrencia, Pedido, TipoAvaliacao,
    TipoOcorrencia, Turma,
)
from app.services.pedagogico import escala

DIAS_FALTAS = 30  # janela das faltas


def _media(valores: list[float]) -> float | None:
    return round(sum(valores) / len(valores), 1) if valores else None


def resumo(db: Session, est: int, hoje: date, taxa_por_turma: dict[int, float]) -> tuple[dict, list[dict]]:
    ano = ano_activo(db, est, obrigatorio=False)
    esc = escala(db, est)
    desde = hoje - timedelta(days=DIAS_FALTAS)

    turmas = list(db.scalars(select(Turma).where(Turma.estabelecimento_id == est, Turma.ano_id == (ano.id if ano else -1))
                             .order_by(Turma.designacao)))
    ids = [t.id for t in turmas]
    # alunos com matrícula activa, por turma
    por_turma: dict[int, set[int]] = defaultdict(set)
    for tid, aid in db.execute(select(Matricula.turma_id, Matricula.aluno_id).where(
            Matricula.turma_id.in_(ids), Matricula.estado == "activa")):
        por_turma[tid].add(aid)
    activos = set().union(*por_turma.values()) if por_turma else set()

    # média ponderada de cada aluno em cada turma
    medias: dict[int, list[float]] = defaultdict(list)
    for tid, _aid, soma, pesos in db.execute(
            select(Avaliacao.turma_id, Nota.aluno_id, func.sum(Nota.valor * TipoAvaliacao.peso), func.sum(TipoAvaliacao.peso))
            .join(Avaliacao, Avaliacao.id == Nota.avaliacao_id).join(TipoAvaliacao, TipoAvaliacao.id == Avaliacao.tipo_id)
            .where(Avaliacao.turma_id.in_(ids), Nota.valor.is_not(None))
            .group_by(Avaliacao.turma_id, Nota.aluno_id)):
        if pesos:
            medias[tid].append(float(soma) / float(pesos))

    faltas = dict(db.execute(select(Assiduidade.turma_id, func.count()).where(
        Assiduidade.turma_id.in_(ids), Assiduidade.data >= desde,
        Assiduidade.estado.in_(("falta_justificada", "falta_injustificada"))).group_by(Assiduidade.turma_id)).tuples().all())
    injustificadas = db.scalar(select(func.count()).select_from(Assiduidade).where(
        Assiduidade.turma_id.in_(ids), Assiduidade.data >= desde, Assiduidade.estado == "falta_injustificada")) or 0

    # ocorrências por fechar, contadas na turma actual do aluno
    turma_do_aluno = {aid: tid for tid, alunos in por_turma.items() for aid in alunos}
    abertas: dict[int, int] = defaultdict(int)
    graves = 0
    for aid, gravidade in db.execute(select(Ocorrencia.aluno_id, TipoOcorrencia.gravidade)
                                     .join(TipoOcorrencia, TipoOcorrencia.id == Ocorrencia.tipo_id)
                                     .join(Aluno, Aluno.id == Ocorrencia.aluno_id)
                                     .where(Aluno.estabelecimento_id == est, Ocorrencia.estado != "fechada",
                                            TipoOcorrencia.gravidade != "positiva")):
        if aid in turma_do_aluno:
            abertas[turma_do_aluno[aid]] += 1
        graves += gravidade == "grave"

    linhas = []
    for t in turmas:
        m = medias.get(t.id, [])
        linhas.append({
            "turma_id": t.id, "turma": t.designacao, "alunos": len(por_turma.get(t.id, ())), "capacidade": t.capacidade,
            "media": _media(m), "negativas": sum(v < esc["nota_minima"] for v in m),
            "faltas": faltas.get(t.id, 0), "ocorrencias_abertas": abertas.get(t.id, 0),
            "taxa_cobranca": taxa_por_turma.get(t.id),
        })

    todas = [v for m in medias.values() for v in m]
    nee = db.scalar(select(func.count()).select_from(Aluno).where(
        Aluno.id.in_(activos), func.coalesce(func.trim(Aluno.necessidades_especiais), "") != "")) if activos else 0
    aval = db.scalar(select(func.avg(AvaliacaoProfessor.nota)).where(AvaliacaoProfessor.estabelecimento_id == est))
    escola = {
        "alunos": len(activos), "alunos_nee": nee or 0, "turmas": len(turmas),
        "lugares": sum(t.capacidade or 0 for t in turmas),
        "media_geral": _media(todas), "nota_maxima": esc["nota_maxima"], "nota_minima": esc["nota_minima"],
        "alunos_com_negativa": sum(v < esc["nota_minima"] for v in todas),
        "faltas_injustificadas": injustificadas, "dias_faltas": DIAS_FALTAS,
        "ocorrencias_abertas": sum(abertas.values()), "ocorrencias_graves": graves,
        "pedidos_pendentes": db.scalar(select(func.count()).select_from(Pedido).where(
            Pedido.estabelecimento_id == est, Pedido.estado == "pendente")) or 0,
        "avaliacao_professores": round(float(aval), 1) if aval is not None else None,
    }
    return escola, linhas
