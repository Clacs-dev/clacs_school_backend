"""Pedidos do encarregado: criados no portal, decididos pela escola. Aprovar executa o efeito do pedido.

vaga                → cria o aluno, liga-o ao encarregado e matricula-o (turma escolhida ou a 1.ª da classe com vaga)
matricula           → matricula o educando no ano activo
transferencia       → aluno passa a "transferido" e a matrícula activa é anulada
declaracao          → a declaração de matrícula fica disponível em PDF no portal
justificacao_falta  → as faltas injustificadas desse dia passam a justificadas, com o motivo
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import ano_activo, as_dict, numero_processo
from app.models import (
    Alerta, Aluno, AlunoEncarregado, Assiduidade, Classe, Encarregado, Matricula, Mensagem, Pedido, Turma,
)
from app.schemas import PedidoIn
from app.services import turmas as ocupacao_turmas

NOMES = {"vaga": "vaga", "matricula": "matrícula", "transferencia": "transferência", "declaracao": "declaração",
         "justificacao_falta": "justificação de falta"}


def _matricula_activa(db: Session, est: int, aluno_id: int) -> Matricula | None:
    ano = ano_activo(db, est, obrigatorio=False)
    return ano and db.scalar(select(Matricula).where(Matricula.aluno_id == aluno_id, Matricula.ano_id == ano.id,
                                                     Matricula.estado == "activa"))


def criar(db: Session, ee: Encarregado, dados: PedidoIn) -> Pedido:
    if dados.aluno_id is not None:
        educandos = set(db.scalars(select(AlunoEncarregado.aluno_id).where(AlunoEncarregado.encarregado_id == ee.id)))
        if dados.aluno_id not in educandos:
            raise HTTPException(404, "Educando não encontrado")
    if dados.classe_id is not None and db.get(Classe, dados.classe_id) is None:
        raise HTTPException(422, f"A classe {dados.classe_id} não existe")
    if dados.tipo != "vaga" and db.scalar(select(Pedido.id).where(
            Pedido.encarregado_id == ee.id, Pedido.aluno_id == dados.aluno_id, Pedido.tipo == dados.tipo,
            Pedido.estado == "pendente")):
        raise HTTPException(409, f"Já há um pedido de {NOMES[dados.tipo]} pendente para este educando")
    p = Pedido(estabelecimento_id=ee.estabelecimento_id, tipo=dados.tipo, encarregado_id=ee.id,
               aluno_id=dados.aluno_id, dados=dados.model_dump(mode="json", exclude={"tipo", "aluno_id"}, exclude_none=True))
    db.add(p)
    db.flush()
    quem = db.get(Aluno, dados.aluno_id).nome_completo if dados.aluno_id else dados.nome_aluno
    db.add(Alerta(estabelecimento_id=ee.estabelecimento_id, tipo="pedido_encarregado", aluno_id=dados.aluno_id,
                  gravidade="baixa", dados={"pedido_id": p.id, "tipo": dados.tipo},
                  mensagem=f"{ee.nome_completo} pediu {NOMES[dados.tipo]} ({quem})."))
    db.commit()
    return p


def _turma(db: Session, est: int, ano_id: int, turma_id: int | None, classe_id: int) -> Turma:
    if turma_id is None:
        return ocupacao_turmas.escolher(db, est, ano_id, classe_id)
    t = db.get(Turma, turma_id)
    if t is None or t.estabelecimento_id != est or t.ano_id != ano_id:
        raise HTTPException(422, f"A turma {turma_id} não é do ano lectivo activo")
    ocupacao_turmas.garantir_vaga(db, t)
    return t


def _executar(db: Session, p: Pedido, turma_id: int | None, utilizador_id: int) -> dict:
    est, d = p.estabelecimento_id, p.dados
    if p.tipo in ("vaga", "matricula"):
        ano = ano_activo(db, est)
        if p.tipo == "matricula" and _matricula_activa(db, est, p.aluno_id):
            raise HTTPException(409, "O aluno já está matriculado no ano activo")
        turma = _turma(db, est, ano.id, turma_id, d["classe_id"])
        if p.tipo == "vaga":
            a = Aluno(estabelecimento_id=est, numero_processo=numero_processo(db, est), nome_completo=d["nome_aluno"],
                      data_nascimento=date.fromisoformat(d["data_nascimento"]) if d.get("data_nascimento") else None,
                      sexo=d.get("sexo"))
            db.add(a)
            db.flush()
            db.add(AlunoEncarregado(aluno_id=a.id, encarregado_id=p.encarregado_id, parentesco=d.get("parentesco", "mae"),
                                    principal=True, responsavel_financeiro=True))
            p.aluno_id = a.id
        m = Matricula(estabelecimento_id=est, aluno_id=p.aluno_id, ano_id=ano.id, turma_id=turma.id,
                      tipo="nova" if p.tipo == "vaga" else "renovacao", registado_por=utilizador_id,
                      observacoes=f"Pedido {p.id} do portal")
        db.add(m)
        db.flush()
        return {"aluno_id": p.aluno_id, "matricula_id": m.id, "turma_id": turma.id, "turma": turma.designacao}
    if p.tipo == "transferencia":
        a = db.get(Aluno, p.aluno_id)
        a.estado = "transferido"
        m = _matricula_activa(db, est, p.aluno_id)
        if m:
            m.estado = "anulada"
        return {"aluno_id": a.id, "matricula_anulada": m.id if m else None, "escola_destino": d["escola_destino"]}
    if p.tipo == "declaracao":
        m = _matricula_activa(db, est, p.aluno_id)
        if m is None:
            raise HTTPException(409, "O aluno não tem matrícula activa: não é possível passar a declaração")
        return {"matricula_id": m.id, "documento": f"/api/portal/pedidos/{p.id}/declaracao.pdf"}
    # justificacao_falta
    dia = date.fromisoformat(d["data"])
    faltas = db.scalars(select(Assiduidade).where(Assiduidade.aluno_id == p.aluno_id, Assiduidade.data == dia,
                                                  Assiduidade.estado == "falta_injustificada")).all()
    for f in faltas:
        f.estado, f.justificacao = "falta_justificada", d["motivo"]
    return {"faltas_justificadas": len(faltas)}


def decidir(db: Session, p: Pedido, aprovar: bool, resposta: str | None, turma_id: int | None, utilizador_id: int) -> Pedido:
    if p.estado != "pendente":
        raise HTTPException(409, f"O pedido já está {p.estado}")
    if not aprovar and not (resposta or "").strip():
        raise HTTPException(422, "Indique o motivo da rejeição (resposta)")
    if aprovar:
        p.resultado = _executar(db, p, turma_id, utilizador_id)
    p.estado, p.resposta = ("aprovado" if aprovar else "rejeitado"), resposta
    p.decidido_por, p.decidido_em = utilizador_id, datetime.now(timezone.utc)
    for a in db.scalars(select(Alerta).where(Alerta.estabelecimento_id == p.estabelecimento_id,
                                             Alerta.tipo == "pedido_encarregado", Alerta.estado.in_(("novo", "visto")))):
        if (a.dados or {}).get("pedido_id") == p.id:
            a.estado, a.tratado_por = "tratado", utilizador_id
    texto = f"O seu pedido de {NOMES[p.tipo]} foi {'aprovado' if aprovar else 'recusado'}."
    if aprovar and p.tipo in ("vaga", "matricula"):
        texto += f" Turma: {p.resultado['turma']}."
    if aprovar and p.tipo == "declaracao":
        texto += " Pode descarregar a declaração no portal."
    if resposta:
        texto += f" {resposta}"
    for canal in ("app", "whatsapp"):
        db.add(Mensagem(estabelecimento_id=p.estabelecimento_id, encarregado_id=p.encarregado_id, aluno_id=p.aluno_id,
                        canal=canal, assunto="Pedido", corpo=texto))
    db.commit()
    return p


def com_nomes(db: Session, p: Pedido) -> dict:
    aluno = db.get(Aluno, p.aluno_id) if p.aluno_id else None
    return {**as_dict(p), "aluno": aluno.nome_completo if aluno else p.dados.get("nome_aluno"),
            "encarregado": db.get(Encarregado, p.encarregado_id).nome_completo}
