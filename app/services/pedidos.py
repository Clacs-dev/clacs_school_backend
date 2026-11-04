"""Pedidos do encarregado: criados no portal, tratados pela escola. Aprovar executa o efeito do pedido.

vaga                → cria o aluno, liga-o ao encarregado e matricula-o (turma escolhida ou a 1.ª da classe com vaga)
matricula           → renovação: matricula o educando no ano activo e aplica a ficha confirmada pela família
transferencia       → aluno passa a "transferido" e a matrícula activa é anulada
declaracao          → a declaração (modelo escolhido) fica disponível em PDF; concluir = entregue
justificacao_falta  → as faltas injustificadas desse dia passam a justificadas, com o motivo
saida_antecipada    → autorização de saída (sem efeito no sistema além da resposta)
informacao, financeiro, saude, outro → respondidos e concluídos (não há aprovação)

Percurso: recebido (pendente) → em análise ↔ aguarda informação da família → aprovado/rejeitado → concluído.
Cada passo fica no histórico e a família é avisada (app + WhatsApp).
"""
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.common import ano_activo, as_dict, numero_processo
from app.models import (
    Alerta, Aluno, AlunoEncarregado, Assiduidade, Classe, Encarregado, Matricula, Mensagem, Pedido, Turma, Utilizador,
)
from app.schemas import PedidoIn
from app.services import calendario, secretaria
from app.services import turmas as ocupacao_turmas

NOMES = {"vaga": "vaga", "matricula": "matrícula", "transferencia": "transferência", "declaracao": "declaração",
         "justificacao_falta": "justificação de falta", "saida_antecipada": "saída antecipada",
         "informacao": "informação", "financeiro": "assunto financeiro", "saude": "comunicação de saúde",
         "outro": "pedido"}
NOMES_ESTADO = {"pendente": "recebido", "em_analise": "em análise", "aguarda_informacao": "a aguardar informação",
                "aprovado": "aprovado", "rejeitado": "recusado", "concluido": "concluído", "cancelado": "cancelado"}
SERVICO_DO_TIPO = {"justificacao_falta": "coordenacao", "saida_antecipada": "coordenacao", "financeiro": "tesouraria",
                   "saude": "saude"}
INFORMATIVOS = ("informacao", "financeiro", "saude", "outro")   # respondem-se e concluem-se; não há aprovação
ABERTOS = ("pendente", "em_analise", "aguarda_informacao")


def _matricula_activa(db: Session, est: int, aluno_id: int) -> Matricula | None:
    ano = ano_activo(db, est, obrigatorio=False)
    return ano and db.scalar(select(Matricula).where(Matricula.aluno_id == aluno_id, Matricula.ano_id == ano.id,
                                                     Matricula.estado == "activa"))


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _historico(p: Pedido, estado: str, por: str | None, autor: str, nota: str | None = None) -> None:
    p.historico = [*(p.historico or []), {"em": _agora().isoformat(timespec="seconds"), "estado": estado, "por": por,
                                          "autor": autor, "nota": nota}]


def _avisar(db: Session, p: Pedido, texto: str) -> None:
    for canal in ("app", "whatsapp"):
        db.add(Mensagem(estabelecimento_id=p.estabelecimento_id, encarregado_id=p.encarregado_id, aluno_id=p.aluno_id,
                        canal=canal, assunto="Pedido", corpo=texto))


def _alertas(db: Session, p: Pedido) -> list[Alerta]:
    return [a for a in db.scalars(select(Alerta).where(Alerta.estabelecimento_id == p.estabelecimento_id,
                                                       Alerta.tipo == "pedido_encarregado"))
            if (a.dados or {}).get("pedido_id") == p.id]


def _alertar(db: Session, p: Pedido, mensagem: str) -> None:
    db.add(Alerta(estabelecimento_id=p.estabelecimento_id, tipo="pedido_encarregado", aluno_id=p.aluno_id,
                  gravidade="baixa", dados={"pedido_id": p.id, "tipo": p.tipo, "servico": p.servico},
                  mensagem=mensagem))


def _fechar_alertas(db: Session, p: Pedido, utilizador_id: int | None) -> None:
    for a in _alertas(db, p):
        if a.estado in ("novo", "visto"):
            a.estado, a.tratado_por = "tratado", utilizador_id


def criar(db: Session, ee: Encarregado, dados: PedidoIn) -> Pedido:
    if dados.aluno_id is not None:
        educandos = set(db.scalars(select(AlunoEncarregado.aluno_id).where(AlunoEncarregado.encarregado_id == ee.id)))
        if dados.aluno_id not in educandos:
            raise HTTPException(404, "Educando não encontrado")
    if dados.classe_id is not None and db.get(Classe, dados.classe_id) is None:
        raise HTTPException(422, f"A classe {dados.classe_id} não existe")
    if dados.tipo == "declaracao":
        secretaria.modelo(db, ee.estabelecimento_id, dados.modelo or "matricula")   # 404 se o modelo não existir
    if dados.tipo not in ("vaga", *INFORMATIVOS) and db.scalar(select(Pedido.id).where(
            Pedido.encarregado_id == ee.id, Pedido.aluno_id == dados.aluno_id, Pedido.tipo == dados.tipo,
            Pedido.estado.in_(ABERTOS))):
        raise HTTPException(409, f"Já há um pedido de {NOMES[dados.tipo]} em curso para este educando")
    p = Pedido(estabelecimento_id=ee.estabelecimento_id, tipo=dados.tipo, encarregado_id=ee.id,
               aluno_id=dados.aluno_id, servico=SERVICO_DO_TIPO.get(dados.tipo, "secretaria"),
               dados=dados.model_dump(mode="json", exclude={"tipo", "aluno_id"}, exclude_none=True))
    _historico(p, "pendente", ee.nome_completo, "familia", "Pedido enviado pelo portal")
    db.add(p)
    db.flush()
    quem = db.get(Aluno, dados.aluno_id).nome_completo if dados.aluno_id else dados.nome_aluno
    _alertar(db, p, f"{ee.nome_completo} pediu {NOMES[dados.tipo]} ({quem or dados.assunto}).")
    db.commit()
    return p


# ---------------------------------------------------------------- tratamento pela escola
def analisar(db: Session, p: Pedido, por: Utilizador) -> None:
    if p.estado != "pendente":
        raise HTTPException(409, f"O pedido está {NOMES_ESTADO[p.estado]}")
    p.estado = "em_analise"
    _historico(p, "em_analise", por.nome, "escola")
    _avisar(db, p, f"O seu pedido de {NOMES[p.tipo]} está em análise.")


def pedir_informacao(db: Session, p: Pedido, por: Utilizador, mensagem: str) -> None:
    if p.estado not in ("pendente", "em_analise"):
        raise HTTPException(409, f"O pedido está {NOMES_ESTADO[p.estado]}")
    p.estado = "aguarda_informacao"
    _historico(p, "aguarda_informacao", por.nome, "escola", mensagem)
    _fechar_alertas(db, p, por.id)
    _avisar(db, p, f"A escola precisa de mais informação sobre o seu pedido de {NOMES[p.tipo]}: {mensagem} "
                   "Responda no portal, em Pedidos à escola.")


def responder(db: Session, p: Pedido, ee: Encarregado, mensagem: str) -> None:
    """A família responde ao pedido de informação: o pedido volta à análise e a escola é avisada."""
    if p.estado != "aguarda_informacao":
        raise HTTPException(409, "A escola não pediu informação sobre este pedido")
    p.estado = "em_analise"
    _historico(p, "em_analise", ee.nome_completo, "familia", mensagem)
    _alertar(db, p, f"{ee.nome_completo} respondeu ao pedido de {NOMES[p.tipo]}: {mensagem}")


def encaminhar(db: Session, p: Pedido, por: Utilizador, servico: str, nota: str | None) -> None:
    if p.estado not in ABERTOS:
        raise HTTPException(409, f"O pedido está {NOMES_ESTADO[p.estado]}")
    anterior, p.servico = p.servico, servico
    _historico(p, p.estado, por.nome, "escola", f"Encaminhado de {anterior} para {servico}" + (f": {nota}" if nota else ""))
    for a in _alertas(db, p):
        if a.estado in ("novo", "visto"):
            a.dados = {**(a.dados or {}), "servico": servico}


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
        feito = {"aluno_id": p.aluno_id, "matricula_id": m.id, "turma_id": turma.id, "turma": turma.designacao}
        if p.tipo == "matricula" and d.get("ficha"):
            feito["ficha_actualizada"] = secretaria.aplicar_ficha(db, db.get(Aluno, p.aluno_id), d["ficha"])
        return feito
    if p.tipo == "transferencia":
        a = db.get(Aluno, p.aluno_id)
        a.estado = "transferido"
        m = _matricula_activa(db, est, p.aluno_id)
        if m:
            m.estado = "anulada"
        return {"aluno_id": a.id, "matricula_anulada": m.id if m else None, "escola_destino": d["escola_destino"]}
    if p.tipo == "declaracao":
        secretaria.matricula_para_declaracao(db, db.get(Aluno, p.aluno_id))
        return {"modelo": d.get("modelo") or "matricula", "documento": f"/api/portal/pedidos/{p.id}/declaracao.pdf"}
    if p.tipo == "saida_antecipada":
        return {"data": d["data"], "hora": d["hora"], "quem_recolhe": d.get("quem_recolhe")}
    # justificacao_falta: o registo original fica guardado em estado_original
    dia = date.fromisoformat(d["data"])
    faltas = db.scalars(select(Assiduidade).where(Assiduidade.aluno_id == p.aluno_id, Assiduidade.data == dia,
                                                  Assiduidade.estado.in_(("falta_injustificada", "atraso")))).all()
    for f in faltas:
        f.estado_original = f.estado_original or f.estado
        f.estado, f.justificacao, f.pedido_id = "falta_justificada", d["motivo"], p.id
        f.justificada_por, f.justificada_em = utilizador_id, _agora()
    return {"faltas_justificadas": len(faltas)}


def decidir(db: Session, p: Pedido, aprovar: bool, resposta: str | None, turma_id: int | None, utilizador_id: int) -> Pedido:
    if p.estado not in ABERTOS:
        raise HTTPException(409, f"O pedido está {NOMES_ESTADO[p.estado]}")
    if aprovar and p.tipo in INFORMATIVOS:
        raise HTTPException(409, "Este pedido não se aprova: responda e conclua (POST .../concluir)")
    if not aprovar and not (resposta or "").strip():
        raise HTTPException(422, "Indique o motivo da rejeição (resposta)")
    if aprovar:
        p.resultado = _executar(db, p, turma_id, utilizador_id)
    p.estado, p.resposta = ("aprovado" if aprovar else "rejeitado"), resposta
    p.decidido_por, p.decidido_em = utilizador_id, _agora()
    _historico(p, p.estado, db.get(Utilizador, utilizador_id).nome, "escola", resposta)
    _fechar_alertas(db, p, utilizador_id)
    texto = f"O seu pedido de {NOMES[p.tipo]} foi {'aprovado' if aprovar else 'recusado'}."
    if aprovar and p.tipo in ("vaga", "matricula"):
        texto += f" Turma: {p.resultado['turma']}."
    if aprovar and p.tipo == "declaracao":
        texto += " A declaração está emitida: pode descarregá-la no portal ou levantá-la na secretaria."
    if resposta:
        texto += f" {resposta}"
    _avisar(db, p, texto)
    db.commit()
    return p


def concluir(db: Session, p: Pedido, por: Utilizador, resposta: str | None) -> None:
    """Pedidos informativos: responder e concluir. Aprovados: fechar (ex.: declaração entregue, saída feita)."""
    if p.tipo in INFORMATIVOS:
        if p.estado not in ABERTOS:
            raise HTTPException(409, f"O pedido está {NOMES_ESTADO[p.estado]}")
        if not (resposta or "").strip():
            raise HTTPException(422, "Escreva a resposta à família")
    elif p.estado != "aprovado":
        raise HTTPException(409, "Só um pedido aprovado se conclui")
    p.estado, p.resposta = "concluido", resposta or p.resposta
    p.decidido_por, p.decidido_em = por.id, p.decidido_em or _agora()
    nota = "Declaração entregue" if p.tipo == "declaracao" else resposta
    _historico(p, "concluido", por.nome, "escola", nota)
    _fechar_alertas(db, p, por.id)
    _avisar(db, p, f"O seu pedido de {NOMES[p.tipo]} foi concluído." + (f" {resposta}" if resposta else ""))


def com_nomes(db: Session, p: Pedido) -> dict:
    aluno = db.get(Aluno, p.aluno_id) if p.aluno_id else None
    prazo = calendario.somar_dias_uteis(db, p.estabelecimento_id, p.criado_em.date(),
                                        calendario.prazo_pedidos(db, p.estabelecimento_id))
    return {**as_dict(p), "aluno": aluno.nome_completo if aluno else p.dados.get("nome_aluno"),
            "encarregado": db.get(Encarregado, p.encarregado_id).nome_completo, "estado_nome": NOMES_ESTADO[p.estado],
            "prazo": prazo, "fora_do_prazo": p.estado in ABERTOS and date.today() > prazo}
