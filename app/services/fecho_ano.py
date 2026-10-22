"""Fecho do ano lectivo (resultado final) e passagem dos alunos para o ano seguinte."""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import AdesaoServico, Aluno, AnoLectivo, Matricula, Parametro, ResultadoFinal, Turma
from app.services.pedagogico import escala, media_geral, total_faltas


def _param(db: Session, est: int, chave: str, omissao):
    p = db.get(Parametro, (est, chave))
    return p.valor if p is not None else omissao


def fechar_ano(db: Session, est: int, ano: AnoLectivo, simular: bool = False) -> dict:
    """Calcula a média final e a situação de cada matrícula activa. Com simular=True não grava nada."""
    nota_minima = escala(db, est)["nota_minima"]
    faltas_max = _param(db, est, "faltas_maximas", None)
    linhas, contagem = [], {"aprovado": 0, "reprovado": 0, "excluido_faltas": 0, "sem_avaliacao": 0}
    mats = db.execute(select(Matricula, Aluno.nome_completo, Turma.designacao)
                      .join(Aluno, Aluno.id == Matricula.aluno_id).join(Turma, Turma.id == Matricula.turma_id)
                      .where(Matricula.ano_id == ano.id, Matricula.estado == "activa")
                      .order_by(Turma.designacao, Aluno.nome_completo)).all()
    agora = datetime.now(timezone.utc)
    for m, nome, turma in mats:
        media = media_geral(db, m)
        faltas = total_faltas(db, m.aluno_id, m.turma_id)
        if faltas_max is not None and faltas > int(faltas_max):
            situacao = "excluido_faltas"
        elif media is None:
            situacao = None
        else:
            situacao = "aprovado" if media >= nota_minima else "reprovado"
        contagem[situacao or "sem_avaliacao"] += 1
        linhas.append({"matricula_id": m.id, "aluno": nome, "turma": turma, "media_final": media,
                       "faltas": faltas, "situacao": situacao})
        if simular or situacao is None:
            continue
        r = db.get(ResultadoFinal, m.id) or ResultadoFinal(matricula_id=m.id)
        r.media_final = Decimal(str(media)) if media is not None else None
        r.situacao, r.fechada_em = situacao, agora
        db.add(r)
        m.estado = "concluida"
    if not simular:
        if contagem["sem_avaliacao"]:
            db.rollback()
            raise HTTPException(409, f"{contagem['sem_avaliacao']} matrícula(s) sem notas. Lance as notas ou "
                                     "veja a lista com simular=true antes de fechar.")
        ano.estado = "encerrado"
        db.commit()
    return {"ano": ano.designacao, "nota_minima": nota_minima, "faltas_maximas": faltas_max, "simulado": simular,
            "totais": contagem, "linhas": linhas}


def transitar(db: Session, est: int, de: AnoLectivo, para: AnoLectivo, mapa: dict[int, int],
              repetentes: dict[int, int], copiar_servicos: bool, registado_por: int | None) -> dict:
    """Cria as matrículas do novo ano: aprovados → turma de `mapa`, reprovados → turma de `repetentes`."""
    destino = {t.id: t for t in db.scalars(select(Turma).where(Turma.ano_id == para.id, Turma.estabelecimento_id == est))}
    for t_id in list(mapa.values()) + list(repetentes.values()):
        if t_id not in destino:
            raise HTTPException(422, f"A turma {t_id} não pertence ao ano {para.designacao}")
    ocupacao = dict(db.execute(select(Matricula.turma_id, func.count()).where(
        Matricula.ano_id == para.id, Matricula.estado == "activa").group_by(Matricula.turma_id)).tuples().all())
    ja = set(db.scalars(select(Matricula.aluno_id).where(Matricula.ano_id == para.id)))
    criadas, ignoradas = 0, []
    rows = db.execute(select(Matricula, ResultadoFinal.situacao, Aluno.nome_completo)
                      .join(ResultadoFinal, ResultadoFinal.matricula_id == Matricula.id)
                      .join(Aluno, Aluno.id == Matricula.aluno_id)
                      .where(Matricula.ano_id == de.id, Aluno.estado == "activo")).all()
    for m, situacao, nome in rows:
        alvo = mapa.get(m.turma_id) if situacao in ("aprovado", "transita") else repetentes.get(m.turma_id)
        if m.aluno_id in ja:
            ignoradas.append({"aluno": nome, "motivo": "já matriculado no novo ano"})
            continue
        if alvo is None:
            ignoradas.append({"aluno": nome, "motivo": f"sem turma de destino ({situacao})"})
            continue
        if ocupacao.get(alvo, 0) >= destino[alvo].capacidade:
            ignoradas.append({"aluno": nome, "motivo": f"turma {destino[alvo].designacao} cheia"})
            continue
        nova = Matricula(estabelecimento_id=est, aluno_id=m.aluno_id, ano_id=para.id, turma_id=alvo,
                         tipo="renovacao", registado_por=registado_por,
                         observacoes="repetente" if situacao != "aprovado" else None)
        db.add(nova)
        db.flush()
        ocupacao[alvo] = ocupacao.get(alvo, 0) + 1
        criadas += 1
        if copiar_servicos:
            for ad in db.scalars(select(AdesaoServico).where(AdesaoServico.matricula_id == m.id,
                                                             AdesaoServico.estado == "activa")):
                db.add(AdesaoServico(matricula_id=nova.id, servico_id=ad.servico_id, modalidade=ad.modalidade,
                                     data_inicio=para.data_inicio))
    db.commit()
    return {"criadas": criadas, "ignoradas": ignoradas}
